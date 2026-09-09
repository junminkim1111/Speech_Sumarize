#!/usr/bin/env python3
"""Gemini 로 오디오를 받아쓴다.

Groq 가 막힌 네트워크(예: 학교 와이파이)에서 쓰는 대체 엔진.
받아쓰기 전용 모델(gemini-*-transcribe)이 있으면 그쪽의 전용 설정을 쓴다.
전용 설정은 프롬프트보다 훨씬 정확하다 — 특히 custom_vocabulary 를 주면
영어 전문 용어가 한글로 음차되지 않는다.

단일 화자 녹음만 다루므로 타임스탬프와 화자 분리는 지원하지 않는다.
(그 둘은 custom_vocabulary 와 함께 못 쓰기 때문에 호출이 한 번 더 필요했다)
"""

import mimetypes
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize import load_gemini_key  # noqa: E402
from transcribe import to_audio  # noqa: E402

MODEL_PREFERENCE = [
    "gemini-3.5-transcribe",   # 받아쓰기 전용. 전용 설정을 지원한다
    "gemini-flash-latest",     # 아래는 프롬프트 방식 대체재
    "gemini-3.8-flash",
    "gemini-3.6-flash",
]

INLINE_LIMIT = 15 * 1024 * 1024      # 이보다 크면 Files API 로 업로드
BCP47 = {"ko": "ko-KR", "en": "en-US", "ja": "ja-JP", "zh": "cmn-Hans-CN"}

MIME = {
    ".mp3": "audio/mp3", ".wav": "audio/wav", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".aiff": "audio/aiff",
    ".aac": "audio/aac", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
}

_resolved_model = None


def _mime(path: Path):
    return MIME.get(path.suffix.lower()) or \
        mimetypes.guess_type(str(path))[0] or "audio/ogg"


def _vocab(hint):
    """'RLPD, offline RL' 같은 힌트를 어휘 목록으로 쪼갠다."""
    if not hint:
        return []
    parts = re.split(r"[,\n;]+", hint)
    out = []
    for p in parts:
        p = p.strip(" .·")
        # 문장형 힌트는 어휘가 아니므로 제외
        if p and len(p) <= 60 and len(p.split()) <= 6:
            out.append(p)
    return out[:500]


def _prompt(lang, hint):
    """전용 모델이 아닌 일반 flash 모델용 지시문."""
    name = {"ko": "한국어", "en": "영어", "ja": "일본어", "zh": "중국어"}.get(lang, lang)
    p = (f"이 오디오를 {name}로 받아써 주세요.\n"
         "- 들리는 말을 그대로 옮깁니다. 요약하지 마세요.\n"
         f"- 주 언어는 {name}이지만 섞여 나오는 영어 용어는 알파벳 표기 그대로 씁니다.\n"
         "- 문장이 끝나면 문장부호를 넣고 줄을 바꿉니다.\n"
         "- 받아쓴 내용만 출력하세요.\n")
    if hint:
        p += f"\n나오는 용어와 고유명사: {hint}\n"
    return p


def _extract(resp):
    """전용 모델은 audio_transcription 파트로, 일반 모델은 text 로 돌려준다."""
    texts = []
    try:
        parts = resp.candidates[0].content.parts or []
    except Exception:
        parts = []
    for pt in parts:
        tr = getattr(pt, "audio_transcription", None)
        if tr is not None:
            if getattr(tr, "text", None):
                texts.append(tr.text)
            continue
        if getattr(pt, "thought", None):
            continue
        if getattr(pt, "text", None):
            texts.append(pt.text)
    text = "\n".join(t.strip() for t in texts if t.strip()).strip()
    if not text:
        text = (getattr(resp, "text", None) or "").strip()
    return text




def candidate_models(client, preferred=None):
    if preferred:
        return [preferred]
    try:
        avail = set()
        for m in client.models.list():
            acts = getattr(m, "supported_actions", None) or []
            if acts and "generateContent" not in acts:
                continue
            avail.add(m.name.replace("models/", ""))
    except Exception:
        avail = set()
    cands = [c for c in MODEL_PREFERENCE if not avail or c in avail]
    return cands or ["gemini-flash-latest"]


def transcribe(path, lang="ko", hint="", model=None, api_key=None, log=print):
    """오디오 한 개를 받아써 텍스트로 돌려준다."""
    global _resolved_model
    from google import genai
    from google.genai import types

    path = Path(path)
    client = genai.Client(api_key=api_key or load_gemini_key())

    # 영상 컨테이너(.mov/.mkv/.webm)를 그대로 보내면 Gemini 가 영상으로 보고
    # "0 Frames found" 로 거부한다. 큰 파일도 미리 줄여 업로드를 줄인다.
    tmpdir = tempfile.TemporaryDirectory()
    try:
        path = to_audio(path, Path(tmpdir.name), size_limit=INLINE_LIMIT, log=log)
    except Exception:
        tmpdir.cleanup()
        raise
    size = path.stat().st_size

    uploaded = None
    if size > INLINE_LIMIT:
        log(f"업로드 중… ({size / 1e6:.1f}MB)")
        uploaded = client.files.upload(file=str(path))
        for _ in range(150):
            info = client.files.get(name=uploaded.name)
            state = str(getattr(info, "state", ""))
            if "ACTIVE" in state:
                break
            if "FAILED" in state:
                raise RuntimeError("업로드한 파일을 Gemini 가 처리하지 못했습니다.")
            time.sleep(2)
        audio = uploaded
    else:
        audio = types.Part.from_bytes(data=path.read_bytes(),
                                      mime_type=_mime(path))

    if _resolved_model and not model:
        cands = [_resolved_model] + [c for c in candidate_models(client)
                                     if c != _resolved_model]
    else:
        cands = candidate_models(client, model)

    vocab = _vocab(hint)

    def call(cand, acfg=None):
        """모델 한 번 호출. 실패 사유는 그대로 올려보낸다."""
        if acfg is not None:
            cfg = types.GenerateContentConfig(audio_transcription_config=acfg)
            contents = [audio]
        else:
            cfg = types.GenerateContentConfig(temperature=0.0)
            contents = [audio, _prompt(lang, hint)]
        return client.models.generate_content(
            model=cand, contents=contents, config=cfg)

    def attempt(cand, acfg):
        """혼잡하면 재시도. 못 쓰는 조합/모델이면 None."""
        for i in range(3):
            try:
                return call(cand, acfg)
            except Exception as e:
                msg = str(e)
                if any(k in msg for k in ("503", "UNAVAILABLE", "429",
                                          "RESOURCE_EXHAUSTED", "high demand",
                                          "overloaded")):
                    if i < 2:
                        wait = 4 * (i + 1)
                        log(f"{cand} 혼잡/한도 → {wait}초 후 재시도 ({i + 2}/3)")
                        time.sleep(wait)
                        continue
                    log(f"{cand} 한도 초과 → 다음 모델")
                    return None
                if any(k in msg for k in ("404", "NOT_FOUND", "not available",
                                          "INVALID_ARGUMENT", "400")):
                    reason = msg.split("message':")[-1][:120].strip(" '\"}")
                    log(f"{cand} 사용 불가 → 다음 모델 ({reason})")
                    return None
                raise
        return None

    try:
        for cand in cands:
            dedicated = "transcribe" in cand

            if not dedicated:
                # 일반 flash 모델: 프롬프트 방식. 타임스탬프는 지원하지 않는다.
                resp = attempt(cand, None)
                if resp is None:
                    continue
                text = _extract(resp)
                if not text:
                    continue
                _resolved_model = cand
                log(f"모델: {cand} (프롬프트 방식)")
                return text

            # 전용 모델: 언어 힌트 + 커스텀 어휘로 한 번만 호출한다.
            base_cfg = dict(language_codes=[BCP47.get(lang, lang)])
            resp = attempt(cand, types.AudioTranscriptionConfig(
                **base_cfg, custom_vocabulary=vocab or None))
            if resp is None:
                continue
            text = _extract(resp)
            if not text:
                continue
            _resolved_model = cand
            note = f"모델: {cand}"
            if vocab:
                note += f" (전용 어휘 {len(vocab)}개)"
            log(note)
            return text

        raise RuntimeError("쓸 수 있는 모델이 없습니다.")
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
        tmpdir.cleanup()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Gemini 받아쓰기")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--lang", default="ko")
    ap.add_argument("--hint", default="", help="용어/고유명사 (쉼표 구분)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    files = []
    for it in a.inputs:
        p = Path(it).expanduser()
        files += (sorted(f for f in p.iterdir() if f.suffix.lower() in MIME)
                  if p.is_dir() else [p])

    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        try:
            txt = transcribe(f, a.lang, a.hint, a.model,
                             log=lambda m: print(f"  {m}"))
            dest = Path(a.out).expanduser() if a.out else f.parent
            dest.mkdir(parents=True, exist_ok=True)
            op = dest / (f.stem + ".txt")
            op.write_text(txt, encoding="utf-8")
            print(f"  -> {op} ({len(txt):,}자)")
        except Exception as e:
            print(f"  실패: {e}", file=sys.stderr)
