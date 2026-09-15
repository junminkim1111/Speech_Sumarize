#!/bin/bash
# Finder 에서 더블클릭하면 GUI 가 뜬다.

# 바탕화면 등에 심볼릭 링크를 두고 실행하면 $0 이 링크 위치를 가리킨다.
# 링크를 따라가 실제 스크립트가 있는 폴더로 이동한다.
SRC="$0"
while [ -L "$SRC" ]; do
    DIR="$(cd -P "$(dirname "$SRC")" && pwd)"
    SRC="$(readlink "$SRC")"
    case "$SRC" in
        /*) ;;
        *) SRC="$DIR/$SRC" ;;
    esac
done
cd "$(cd -P "$(dirname "$SRC")" && pwd)" || exit 1

# 필요한 패키지가 갖춰진 python3 을 찾는다.
# 특정 설치 경로를 박아두면 다른 맥에서 안 돌아간다.
NEED='import tkinter, google.genai'
for PY in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
          /opt/anaconda3/bin/python3 "$HOME/miniforge3/bin/python3"; do
    command -v "$PY" >/dev/null 2>&1 || continue
    if "$PY" -c "$NEED" >/dev/null 2>&1; then
        exec "$PY" src/stt_gui.py
    fi
    [ -z "$FALLBACK" ] && "$PY" -c "import tkinter" >/dev/null 2>&1 && FALLBACK="$PY"
done

if [ -n "$FALLBACK" ]; then
    echo "필요한 패키지가 설치되어 있지 않습니다. 아래를 실행하세요:"
    echo "  $FALLBACK -m pip install groq google-genai mlx-whisper tkinterdnd2"
else
    echo "python3 을 찾지 못했습니다."
fi
echo
echo "창을 닫으려면 아무 키나 누르세요."
read -r -n 1
