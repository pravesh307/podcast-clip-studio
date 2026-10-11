#!/bin/bash
cd "$(dirname "$0")" || exit 1
PYTHON=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Install Python 3.11 or 3.12 and run this script again."; read -r; exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "Please install FFmpeg first: brew install ffmpeg"; read -r; exit 1
fi
if [ ! -d ".venv" ]; then "$PYTHON" -m venv .venv || exit 1; fi
source .venv/bin/activate
python -m pip install -r requirements.txt || exit 1
printf '\nOpen your browser at http://127.0.0.1:8765\n'
python app.py
