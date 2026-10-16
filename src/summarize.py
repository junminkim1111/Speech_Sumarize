#!/usr/bin/env python3
"""받아쓴 대본을 Gemini 로 요약한다.

단독 실행:
    python summarize.py 회의.txt
    python summarize.py ./대본폴더 --extra "할 일만 뽑아줘"
"""

import argparse
import re
import sys
import time
from pathlib import Path

import prompts as P

# 선호 순서. 앞엣것부터 실제로 되는지 시도한다.
# 목록에 있어도 "신규 사용자에게는 제공 안 됨"으로 404 가 나는 모델이 있어서,
# 이름 조회만으로는 부족하고 실제 호출이 성공해야 확정한다.
MODEL_PREFERENCE = [
    "gemini-flash-latest",   # 항상 최신 flash 를 가리키는 별칭
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]

_resolved_model = None


def load_gemini_key():
    from transcribe import load_key
    return load_key("GEMINI_API_KEY",
                    "aistudio.google.com 에서 발급받아 .env 에 "
                    "GEMINI_API_KEY=... 로 적으세요.")


def _version_key(name):
    """gemini-3.8-flash -> 3.8 처럼 버전 숫자를 뽑아 최신순 정렬에 쓴다."""
    import re
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else -1.0


def candidate_models(client, preferred=None):
    """시도해 볼 모델을 순서대로 돌려준다."""
    if preferred:
        return [preferred]

    try:
        available = []
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            available.append(m.name.replace("models/", ""))
    except Exception:
        available = []

    cands = [c for c in MODEL_PREFERENCE if not available or c in available]

    # 선호 목록이 다 막혔을 때를 대비해, 남은 flash 계열을 최신순으로 덧붙인다.
    extra = [n for n in available
             if "flash" in n
             and not any(bad in n for bad in ("image", "tts", "lite", "omni", "transcribe"))
             and n not in cands]
    cands += sorted(extra, key=_version_key, reverse=True)
    return cands or ["gemini-flash-latest"]


def resolve_model(client, preferred=None):
    """쓸 수 있는 모델 이름 하나. (실제 호출 성공 여부까지는 보지 않는다)"""
    global _resolved_model
    if preferred:
        return preferred
    if _resolved_model:
        return _resolved_model
    _resolved_model = candidate_models(client)[0]
    return _resolved_model


def summarize(text, extra="", model=None, hint="", api_key=None,
              fmt="md", log=print):
    """대본 문자열을 요약해 마크다운으로 돌려준다.

    extra 는 사용자가 GUI 에서 적어 넣는 추가 지시문이다. 기본 지시 뒤에 붙는다.
    """
    from google import genai
    from google.genai import types

    if not text.strip():
        raise ValueError("대본이 비어 있습니다.")

    global _resolved_model
    client = genai.Client(api_key=api_key or load_gemini_key())
    if _resolved_model and not model:
        cands = [_resolved_model] + [c for c in candidate_models(client)
                                     if c != _resolved_model]
    else:
        cands = candidate_models(client, model)
    log(f"요약 생성 중… (대본 {len(text):,}자)")

    prompt = P.SUMMARY_TASK + P.SUMMARY_RULES
    if fmt == "txt":
        prompt += P.SUMMARY_PLAIN
    if extra and extra.strip():
        prompt += P.SUMMARY_EXTRA.format(extra=extra.strip())
    if hint.strip():
        prompt += P.SUMMARY_HINT.format(hint=hint.strip())
    prompt += P.SUMMARY_BODY.format(text=text)

    last_err = None
    for cand in cands:
        resp = None
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=cand,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.3),
                )
                break
            except Exception as e:
                msg = str(e)
                last_err = e
                # 이 계정에 없는 모델이면 재시도해도 소용없다
                if "404" in msg or "NOT_FOUND" in msg or "not available" in msg:
                    log(f"  {cand} 사용 불가 → 다음 모델")
                    break
                # 과부하/한도는 잠깐 기다렸다 다시
                if any(k in msg for k in ("503", "UNAVAILABLE", "429",
                                          "RESOURCE_EXHAUSTED", "high demand",
                                          "overloaded")):
                    if attempt < 2:
                        wait = 3 * (attempt + 1)
                        log(f"  {cand} 혼잡 → {wait}초 후 재시도 ({attempt + 2}/3)")
                        time.sleep(wait)
                        continue
                    log(f"  {cand} 계속 혼잡 → 다음 모델")
                    break
                raise
        if resp is None:
            continue
        out = (resp.text or "").strip()
        if not out:
            raise RuntimeError(
                "요약이 비어 있습니다. 대본이 너무 짧거나 응답이 차단됐을 수 있습니다.")
        _resolved_model = cand
        log(f"  모델: {cand}")
        return out

    raise RuntimeError(f"쓸 수 있는 모델을 찾지 못했습니다. 마지막 오류: {last_err}")


def main():
    p = argparse.ArgumentParser(description="Gemini 로 대본 요약")
    p.add_argument("inputs", nargs="+", help=".txt 파일 또는 폴더")
    p.add_argument("--extra", default="",
                   help="요약에 덧붙일 추가 지시문")
    p.add_argument("--format", default="md", choices=["md", "txt"],
                   help="저장 형식: md(마크다운) 또는 txt(평문)")
    p.add_argument("--model", default=None, help="모델 지정 (기본: 자동 선택)")
    p.add_argument("--hint", default="", help="자주 나오는 용어/고유명사")
    p.add_argument("--out", default=None, help="저장 폴더 (기본: 원본 옆)")
    p.add_argument("--list-models", action="store_true", help="쓸 수 있는 모델 목록만 출력")
    args = p.parse_args()

    if args.list_models:
        from google import genai
        client = genai.Client(api_key=load_gemini_key())
        for m in client.models.list():
            acts = getattr(m, "supported_actions", None) or []
            if not acts or "generateContent" in acts:
                print(" ", m.name.replace("models/", ""))
        return

    files = []
    for item in args.inputs:
        path = Path(item).expanduser()
        if path.is_dir():
            files += sorted(f for f in path.glob("*.txt")
                            if not f.stem.endswith("_요약"))
        elif path.is_file():
            files.append(path)

    if not files:
        sys.exit("요약할 .txt 파일이 없습니다.")

    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        try:
            md = summarize(f.read_text(encoding="utf-8"), args.extra,
                           args.model, args.hint, fmt=args.format,
                           log=lambda m: print(f"  {m}"))
            dest = Path(args.out).expanduser() if args.out else f.parent
            dest.mkdir(parents=True, exist_ok=True)
            out = dest / (f.stem + "_요약." + args.format)
            out.write_text(md, encoding="utf-8")
            print(f"  -> {out}")
        except Exception as e:
            print(f"  실패: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
