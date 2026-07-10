"""User settings, app folders, and logging for SOTA."""

import json
import os
import sys
import traceback

APP_VERSION = "1.1.0"

APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SOTA"
)
MODELS_DIR = os.path.join(APP_DIR, "models")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
LOG_FILE = os.path.join(APP_DIR, "sota.log")


def app_root():
    """Folder the app lives in: next to SOTA.exe when packaged, or the
    source folder when run with `python app.py`."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DEFAULT_OUTPUT_FOLDER = os.path.join(app_root(), "output")

DEFAULTS = {
    "quality": "balanced",
    "transcribe_language": "auto",
    "timestamps": False,
    "ui_language": "en",
    "llm_mode": "summarize",
    "llm_target": "zh-hant",
    "llm_quality": "balanced",
    "editor_font_size": 14,
    "llm_source_font_size": 13,
    "llm_output_font_size": 13,
    "llm_panel_split": 0.5,
}


def load():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    except Exception:
        return dict(DEFAULTS)


def save(values):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({k: values[k] for k in DEFAULTS}, f, indent=2)
    except Exception:
        pass  # settings are a convenience; never crash over them


def log(message):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def log_exception(prefix):
    log(prefix + "\n" + traceback.format_exc())
