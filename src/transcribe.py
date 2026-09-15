#!/usr/bin/env python3
"""Groq Whisper 로 오디오/비디오 파일을 한국어 텍스트로 받아쓴다.

사용법:
    python transcribe.py 녹음.m4a
    python transcribe.py ./recordings          # 폴더 전체
    python transcribe.py *.mp3 --srt           # 자막 파일도 같이
    python transcribe.py 회의.wav --prompt "RLPD, LIBERO, offline RL"

API 키는 환경변수 GROQ_API_KEY 또는 ~/.groq_key 파일에서 읽는다.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from groq import Groq

# Groq 업로드 상한은 25MB. 변환 오차를 감안해 여유를 둔다.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".webm",
              ".mp4", ".mov", ".mkv", ".aac", ".wma", ".amr"}


def load_api_key():
    key = os.environ.get("GROQ_API_KEY")
    if key:
        return key.strip()
    keyfile = Path.home() / ".groq_key"
    if keyfile.exists():
        return keyfile.read_text().strip()
    raise RuntimeError(
        "GROQ_API_KEY 가 없습니다.\n"
        "export GROQ_API_KEY=... 를 ~/.zshrc 에 추가하거나\n"
        "키를 ~/.groq_key 파일에 저장하세요."
    )


def have_ffmpeg():
    return subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0


# 컨테이너가 영상이면 API 가 영상으로 오해한다. 오디오만 뽑아내야 한다.
PLAIN_AUDIO = {".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".aiff"}


def needs_convert(src: Path, size_limit=None) -> bool:
    if src.suffix.lower() not in PLAIN_AUDIO:
        return True
    return size_limit is not None and src.stat().st_size > size_limit


def to_audio(src: Path, workdir: Path, size_limit=None, log=print) -> Path:
    """필요할 때만 16kHz mono opus 로 바꾼다. 아니면 원본 그대로."""
    if not needs_convert(src, size_limit):
        return src
    if not have_ffmpeg():
        raise RuntimeError(
            f"{src.name} 은(는) 변환이 필요한데 ffmpeg 가 없습니다: brew install ffmpeg")
    why = ("영상 컨테이너에서 오디오 추출"
           if src.suffix.lower() not in PLAIN_AUDIO else "용량 축소")
    log(f"{why} 중… ({src.stat().st_size / 1e6:.1f}MB)")
    return compress(src, workdir)


def compress(src: Path, workdir: Path) -> Path:
    """16kHz mono opus 로 줄인다. Whisper 입력은 어차피 16kHz라 손해가 없다."""
    dst = workdir / (src.stem + ".ogg")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ar", "16000", "-ac", "1",
         "-c:a", "libopus", "-b:a", "24k", str(dst)],
        check=True, capture_output=True,
    )
    return dst


def split(src: Path, workdir: Path, seconds: int) -> list[Path]:
    """긴 파일을 seconds 단위로 자른다."""
    pattern = workdir / (src.stem + "_part%03d.ogg")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-f", "segment",
         "-segment_time", str(seconds), "-ar", "16000", "-ac", "1",
         "-c:a", "libopus", "-b:a", "24k", str(pattern)],
        check=True, capture_output=True,
    )
    return sorted(workdir.glob(src.stem + "_part*.ogg"))


def prepare(src: Path, workdir: Path, log=print) -> list[Path]:
    """업로드 가능한 크기의 조각들로 만든다."""
    small = to_audio(src, workdir, size_limit=MAX_UPLOAD_BYTES, log=log)
    if small.stat().st_size <= MAX_UPLOAD_BYTES:
        return [small]

    # 24kbps opus 기준 25MB ≈ 2.3시간. 안전하게 100분씩 자른다.
    print("  아직 크다. 100분 단위로 분할...")
    return split(small, workdir, 100 * 60)


def srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def transcribe_one(client, path: Path, args, offset: float = 0.0):
    with open(path, "rb") as f:
        r = client.audio.transcriptions.create(
            file=f,
            model=args.model,
            language=args.lang,
            prompt=args.prompt,
            response_format="verbose_json",
            temperature=0.0,
        )
    def get(seg, field):
        # SDK 버전에 따라 segment 가 dict 이기도 하고 객체이기도 하다.
        return seg[field] if isinstance(seg, dict) else getattr(seg, field)

    segs = [
        {"start": get(s, "start") + offset,
         "end": get(s, "end") + offset,
         "text": get(s, "text")}
        for s in (getattr(r, "segments", None) or [])
    ]
    return r.text, segs, (getattr(r, "duration", None) or 0.0)


def main():
    p = argparse.ArgumentParser(description="Groq Whisper 받아쓰기")
    p.add_argument("inputs", nargs="+", help="오디오 파일 또는 폴더")
    p.add_argument("--lang", default="ko",
                   help="언어 코드 (기본 ko). 한국어에 영어가 섞여도 ko로 두는 게 정확하다")
    p.add_argument("--model", default="whisper-large-v3",
                   help="whisper-large-v3 (기본) 또는 whisper-large-v3-turbo")
    p.add_argument("--prompt", default="한국어 녹음입니다. 영어 기술 용어가 섞여 있습니다.",
                   help="자주 나오는 고유명사/전문용어를 적어두면 표기 정확도가 오른다")
    p.add_argument("--out", default=None, help="결과를 저장할 폴더 (기본: 원본 옆)")
    p.add_argument("--srt", action="store_true", help="타임스탬프 자막(.srt)도 저장")
    p.add_argument("--summarize", action="store_true",
                   help="받아쓰기 후 Gemini 로 요약도 생성")
    p.add_argument("--extra", default="",
                   help="요약에 덧붙일 추가 지시문 (--summarize 와 함께)")
    p.add_argument("--format", default="md", choices=["md", "txt"],
                   help="요약 저장 형식 (기본 md)")
    args = p.parse_args()

    try:
        client = Groq(api_key=load_api_key())
    except RuntimeError as e:
        sys.exit(str(e))

    # 입력 펼치기
    files = []
    for item in args.inputs:
        path = Path(item).expanduser()
        if path.is_dir():
            files += sorted(f for f in path.iterdir()
                            if f.suffix.lower() in AUDIO_EXTS)
        elif path.is_file():
            files.append(path)
        else:
            print(f"건너뜀 (없는 경로): {item}")

    if not files:
        sys.exit("처리할 오디오 파일이 없다.")

    total_audio = 0.0
    for i, src in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {src.name}")
        outdir = Path(args.out).expanduser() if args.out else src.parent
        outdir.mkdir(parents=True, exist_ok=True)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                chunks = prepare(src, Path(tmp), log=lambda m: print(f"  {m}"))
                texts, all_segs, offset = [], [], 0.0
                for n, chunk in enumerate(chunks, 1):
                    if len(chunks) > 1:
                        print(f"  조각 {n}/{len(chunks)}...")
                    text, segs, dur = transcribe_one(client, chunk, args, offset)
                    texts.append(text.strip())
                    all_segs += segs
                    offset += dur
                total_audio += offset

            body = "\n".join(t for t in texts if t)
            txt_path = outdir / (src.stem + ".txt")
            txt_path.write_text(body, encoding="utf-8")
            print(f"  -> {txt_path}  ({len(body):,}자)")

            if args.summarize and body.strip():
                try:
                    import summarize as S
                    md = S.summarize(body, extra=args.extra, hint=args.prompt,
                                      fmt=args.format,
                                      log=lambda m: print(f"  {m}"))
                    mp = outdir / (src.stem + "_요약." + args.format)
                    mp.write_text(md, encoding="utf-8")
                    print(f"  -> {mp}")
                except Exception as e:
                    print(f"  요약 실패: {e}", file=sys.stderr)

            if args.srt and all_segs:
                srt = "\n".join(
                    f"{k}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text'].strip()}\n"
                    for k, s in enumerate(all_segs, 1)
                )
                srt_path = outdir / (src.stem + ".srt")
                srt_path.write_text(srt, encoding="utf-8")
                print(f"  -> {srt_path}")

        except Exception as e:
            print(f"  실패: {type(e).__name__}: {e}", file=sys.stderr)

    if total_audio:
        print(f"\n총 {total_audio / 60:.1f}분 처리. "
              f"무료 한도는 하루 480분(8시간).")


if __name__ == "__main__":
    main()
