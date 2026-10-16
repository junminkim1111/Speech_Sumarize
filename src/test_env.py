#!/usr/bin/env python3
"""load_env() 파서 검사. python3 src/test_env.py"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcribe as T

SAMPLE = """# 주석
GEMINI_API_KEY=abc123

GROQ_API_KEY = "with spaces and quotes"
QUOTED='single'
NOT_A_LINE
"""


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / ".env"
        env.write_text(SAMPLE, encoding="utf-8")
        T.ENV_FILE = env
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ["GROQ_API_KEY"] = "already-set"   # 환경변수가 우선

        T.load_env()
        assert os.environ["GEMINI_API_KEY"] == "abc123"
        assert os.environ["GROQ_API_KEY"] == "already-set", "환경변수를 덮어썼다"
        assert os.environ["QUOTED"] == "single"
        assert "NOT_A_LINE" not in os.environ
        assert "# 주석" not in os.environ

        os.environ.pop("GROQ_API_KEY")
        assert T.load_key("GROQ_API_KEY", "안내") == "with spaces and quotes"

        T.ENV_FILE = Path(d) / "없는파일"
        os.environ.pop("GEMINI_API_KEY")
        try:
            T.load_key("GEMINI_API_KEY", "안내")
        except RuntimeError:
            pass
        else:
            raise AssertionError("키가 없는데 예외가 안 났다")
    print("test_env: 통과")


if __name__ == "__main__":
    main()
