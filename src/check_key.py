#!/usr/bin/env python3
"""저장된 API 키들이 유효한지 확인한다. 키 값 자체는 절대 출력하지 않는다."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def inspect(name, key, prefix, expect_len):
    src = f"환경변수 {name}" if os.environ.get(name) else f"~/.{name.split('_')[0].lower()}_key 파일"
    print(f"  출처    : {src}")
    print(f"  길이    : {len(key)}자" + (f" (보통 {'/'.join(map(str, expect_len))}자)" if expect_len else ""))
    print(f"  미리보기: {key[:6]}{'*' * 10}{key[-4:]}")
    warn = []
    prefixes = prefix if isinstance(prefix, tuple) else (prefix,)
    if not key.startswith(prefixes):
        warn.append(f"보통 {' 또는 '.join(prefixes)} 로 시작한다. 다른 서비스 키일 수 있다.")
    if any(c in key for c in ' \t\n"\''):
        warn.append("키 안에 공백이나 따옴표가 있다. 붙여넣기 사고 가능성.")
    if len(key) % 2 == 0 and key[:len(key)//2] == key[len(key)//2:]:
        warn.append("키가 두 번 붙여넣어졌다! 앞 절반만 남겨야 한다.")
    elif expect_len and len(key) not in expect_len:
        warn.append(f"길이가 예상({'/'.join(map(str, expect_len))}자)과 다르다.")
    for w in warn:
        print(f"  ⚠️  {w}")
    return not warn


print("=== Groq (받아쓰기) ===")
try:
    from transcribe import load_api_key
    gk = load_api_key()
    inspect("GROQ_API_KEY", gk, "gsk_", (56,))
    print("  확인 중…")
    from groq import Groq
    ids = sorted(m.id for m in Groq(api_key=gk).models.list().data)
    w = [m for m in ids if "whisper" in m]
    print(f"  ✅ 정상 — whisper 모델: {', '.join(w) if w else '없음(!)'}")
except Exception as e:
    print(f"  ❌ {type(e).__name__}: {str(e)[:200]}")

print("\n=== Gemini (요약) ===")
try:
    from summarize import load_gemini_key, resolve_model, MODEL_PREFERENCE
    kk = load_gemini_key()
    inspect("GEMINI_API_KEY", kk, ("AIza", "AQ."), (39, 53))
    print("  확인 중…")
    from google import genai
    client = genai.Client(api_key=kk)
    names = []
    for m in client.models.list():
        acts = getattr(m, "supported_actions", None) or []
        if not acts or "generateContent" in acts:
            names.append(m.name.replace("models/", ""))
    picked = resolve_model(client)
    print(f"  ✅ 정상 — 사용 가능 모델 {len(names)}개")
    print(f"  선택된 모델: {picked}")
    prefer = [n for n in MODEL_PREFERENCE if n in names]
    print(f"  선호 목록 중 실제 존재: {', '.join(prefer) if prefer else '없음 (flash 계열로 대체)'}")
except Exception as e:
    print(f"  ❌ {type(e).__name__}: {str(e)[:300]}")
    sys.exit(1)
