#!/usr/bin/env python3
"""로컬(Apple Silicon)에서 Whisper 로 받아쓴다.

MLX 로 돌아가므로 M 시리즈 Mac 의 GPU 를 쓴다.
API 키도 인터넷도 필요 없고 토큰도 들지 않는다.
대신 첫 실행 때 모델을 내려받고(1~2GB), 클라우드보다 느리다.
"""

import sys
import time
from pathlib import Path

# 큰 것부터. 없으면 자동으로 내려받는다.
MODELS = {
    "turbo": "mlx-community/whisper-large-v3-turbo",   # 기본. 빠르고 정확
    "large": "mlx-community/whisper-large-v3-mlx",     # 가장 정확, 느림
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",        # 가볍지만 한국어 약함
}
DEFAULT = "turbo"

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".webm",
              ".mp4", ".mov", ".mkv", ".aac", ".amr"}


def available():
    """이 기기에서 로컬 엔진을 쓸 수 있는지."""
    try:
        import mlx.core  # noqa: F401
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


def model_path(size):
    return MODELS.get(size, MODELS[DEFAULT])


def is_downloaded(size):
    """모델이 이미 받아져 있는지 (허깅페이스 캐시 확인)."""
    repo = model_path(size).replace("/", "--")
    cache = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo}"
    return cache.exists() and any(cache.rglob("*.safetensors"))


def transcribe(path, lang="ko", hint="", size=DEFAULT, log=print):
    """오디오 한 개를 받아써 텍스트로 돌려준다."""
    if not available():
        raise RuntimeError(
            "로컬 엔진을 쓰려면 mlx-whisper 가 필요합니다:\n"
            "  pip install mlx-whisper\n"
            "(Apple Silicon Mac 에서만 동작합니다)")

    import mlx_whisper

    repo = model_path(size)
    if not is_downloaded(size):
        log(f"모델을 처음 내려받습니다 ({size}). 1~2GB, 몇 분 걸립니다…")

    # Whisper 의 initial_prompt 는 표기 힌트로 쓰인다.
    # 영어 용어가 한글로 음차되는 걸 조금이나마 줄여준다.
    prompt = None
    if hint and hint.strip():
        prompt = f"다음 용어가 나옵니다: {hint.strip()}"

    t0 = time.time()
    log(f"받아쓰는 중… (로컬 {size}, 진행 표시 없음)")
    result = mlx_whisper.transcribe(
        str(path),
        path_or_hf_repo=repo,
        language=lang,
        initial_prompt=prompt,
        temperature=0.0,
        verbose=None,
    )
    text = (result.get("text") or "").strip()
    dur = result.get("segments") or []
    audio_len = dur[-1]["end"] if dur else 0.0
    elapsed = time.time() - t0
    speed = (audio_len / elapsed) if elapsed > 0 and audio_len else 0
    log(f"완료 — {elapsed:.0f}초 소요"
        + (f" (녹음 {audio_len / 60:.1f}분, 실시간의 {speed:.1f}배)" if speed else ""))
    if not text:
        raise RuntimeError("받아쓴 내용이 비어 있습니다.")
    return text


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="로컬 Whisper 받아쓰기")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--lang", default="ko")
    ap.add_argument("--hint", default="")
    ap.add_argument("--size", default=DEFAULT, choices=list(MODELS))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    files = []
    for it in a.inputs:
        p = Path(it).expanduser()
        files += (sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXTS)
                  if p.is_dir() else [p])

    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        try:
            txt = transcribe(f, a.lang, a.hint, a.size,
                             log=lambda m: print(f"  {m}"))
            dest = Path(a.out).expanduser() if a.out else f.parent
            dest.mkdir(parents=True, exist_ok=True)
            op = dest / (f.stem + ".txt")
            op.write_text(txt, encoding="utf-8")
            print(f"  -> {op} ({len(txt):,}자)")
        except Exception as e:
            print(f"  실패: {e}", file=sys.stderr)
