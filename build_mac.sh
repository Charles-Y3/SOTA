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
  --collect-all llama_cpp \
  --collect-all opencc \
  --collect-all funasr \
  --collect-all torch \
  --collect-all torchaudio \
  --hidden-import onnxruntime \
  app.py

echo "Bundling README.md with the app..."
rm -rf dist/SOTA-release
mkdir -p dist/SOTA-release
cp -R dist/SOTA.app dist/SOTA-release/
cp README.md dist/SOTA-release/

echo
echo "============================================================"
echo " Build complete: dist/SOTA-release/ (SOTA.app + README.md)"
echo " Share it by zipping that folder (use 'ditto' to preserve"
echo " the app bundle's permissions, e.g.:"
echo "   ditto -c -k --keepParent dist/SOTA-release SOTA-macOS.zip"
echo "============================================================"
