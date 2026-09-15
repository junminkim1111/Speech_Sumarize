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

DEFAULT_PROMPT = """다음은 녹음을 받아쓴 발표자료 대본입니다. 읽기 좋은 정리본으로 만들어 주세요.
마크다운으로 작성하되, 형식은 내용에 맞게 정하세요. 개념과 흐름이 드러나게 하면 됩니다.
맨 위에 한두 문장짜리 요약을 두고, 그 아래에 본문을 정리하세요.
"""

COMMON_RULES = """
규칙:
- 한국어로 작성하되, 대본에 영어로 등장한 전문 용어는 영어 표기를 그대로 살립니다.
- 영어 전문 용어는 절대 변형하지 마세요. 번역하거나 한글로 음차하지 말고,
  대본에 적힌 영어 철자 그대로 씁니다.
- 대본에 한글로 음차되어 적힌 영어 용어는 원래의 영어 표기로 되돌려 씁니다.
  예: "리인포스먼트 러닝" → reinforcement learning, "커버리지" → coverage,
  "베이스라인" → baseline, "어블레이션" → ablation, "시드" → seed.
  음차인지 확실하지 않으면 대본 표기를 그대로 둡니다.
- 분량은 공백 포함 1000자에서 2000자 사이로 맞춥니다.
- 대본에 없는 내용을 지어내지 마세요.
- 받아쓰기 오류로 보이는 단어는 문맥에 맞게 보정해서 이해하세요.
- 서론이나 "요약해 드리겠습니다" 같은 인사말 없이 바로 본문부터 시작하세요.
- 문장 끝을 ~입니다, ~합니다 대신 ~임, ~함 처럼 축약해서 끝내세요
"""

PLAIN_RULES = """
- 마크다운 문법을 쓰지 마세요. #, *, **, `, | 표, 링크 표기를 넣지 않습니다.
- 제목은 그냥 한 줄로 쓰고 아래에 빈 줄을 둡니다.
- 목록은 문장 앞에 "- " 만 붙입니다.
- 표가 필요한 내용은 "항목: 값" 형태의 줄로 풀어 씁니다.
"""


def strip_markdown(text):
    """평문(.txt)으로 낼 때 남아 있는 마크다운 표기를 걷어낸다."""
    out = []
    for line in text.splitlines():
        if re.fullmatch(r"\s*[-*_]{3,}\s*", line):      # 구분선
            continue
        line = re.sub(r"^\s*#{1,6}\s*", "", line)        # 제목
        line = re.sub(r"^(\s*)[*+]\s+", r"\1- ", line)   # 불릿 통일
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)     # 굵게
        line = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)          # 코드
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)  # 링크
        if re.fullmatch(r"\s*\|[\s:|-]+\|\s*", line):    # 표 구분줄
            continue
        if line.strip().startswith("|"):                 # 표 본문
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = " · ".join(c for c in cells if c)
        out.append(line.rstrip())
    body = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


_resolved_model = None


def load_gemini_key():
    import os
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key.strip()
    keyfile = Path.home() / ".gemini_key"
    if keyfile.exists():
        return keyfile.read_text().strip()
    raise RuntimeError(
        "Gemini API 키가 없습니다.\n"
        "aistudio.google.com 에서 발급받아 ~/.gemini_key 에 저장하거나\n"
        "GEMINI_API_KEY 환경변수로 설정하세요."
    )


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

    prompt = DEFAULT_PROMPT + COMMON_RULES
    if fmt == "txt":
        prompt += PLAIN_RULES
    if extra and extra.strip():
        prompt += f"\n추가 요청:\n{extra.strip()}\n"
    if hint.strip():
        prompt += f"\n참고 — 이 녹음에 자주 나오는 용어와 고유명사: {hint.strip()}\n"
    prompt += f"\n---- 대본 시작 ----\n{text}\n---- 대본 끝 ----"

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
