#!/usr/bin/env bash
# Builds SOTA.app on macOS. Run from the project root:
#   chmod +x build_mac.sh && ./build_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate

echo "Installing dependencies (first time can take a few minutes)..."
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt pyinstaller

echo "Building SOTA.app ..."
pyinstaller --noconfirm --clean --windowed --name SOTA \
  --collect-all customtkinter \
  --collect-all tkinterdnd2 \
  --collect-all sounddevice \
  --collect-all av \
  --collect-all docx \
  --collect-data faster_whisper \
  --collect-all ctranslate2 \
  --hidden-import onnxruntime \
  app.py

echo
echo "============================================================"
echo " Build complete: dist/SOTA.app"
echo " Share the app by zipping dist/SOTA.app (use 'ditto' to"
echo " preserve permissions, e.g.:"
echo "   ditto -c -k --keepParent dist/SOTA.app SOTA-macOS.zip"
echo "============================================================"
