@echo off
setlocal
cd /d "%~dp0"

rem ---- Pick a Python with good wheel support for the ML dependencies ----
set "PYCMD="
for %%V in (3.12 3.11 3.13) do (
    if not defined PYCMD (
        py -%%V --version >nul 2>nul && set "PYCMD=py -%%V"
    )
)
if not defined PYCMD (
    python --version >nul 2>nul && set "PYCMD=python"
)
if not defined PYCMD (
    echo Python 3.11/3.12/3.13 not found. Install it from python.org and re-run.
    pause
    exit /b 1
)
echo Using: %PYCMD%

rem ---- Virtual environment ----
if not exist .venv (
    echo Creating virtual environment...
    %PYCMD% -m venv .venv || (echo Failed to create the virtual environment.& pause & exit /b 1)
)
call .venv\Scripts\activate.bat

echo Installing dependencies (first time can take a few minutes)...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt pyinstaller || (echo Dependency install failed.& pause & exit /b 1)

echo Building SOTA.exe ...
pyinstaller --noconfirm --clean --windowed --name SOTA ^
  --collect-all customtkinter ^
  --collect-all tkinterdnd2 ^
  --collect-all sounddevice ^
  --collect-all av ^
  --collect-all docx ^
  --collect-data faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all llama_cpp ^
  --hidden-import onnxruntime ^
  app.py || (echo Build failed.& pause & exit /b 1)

echo Bundling README.md with the app...
copy /Y README.md dist\SOTA\README.md >nul

echo.
echo ============================================================
echo  Build complete:  dist\SOTA\SOTA.exe  (+ README.md alongside it)
echo  Share the app by zipping the whole dist\SOTA folder.
echo ============================================================
pause
