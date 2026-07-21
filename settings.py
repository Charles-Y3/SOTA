"""User settings, app folders, and logging for SOTA."""

import json
import os
import sys
import traceback

APP_VERSION = "1.2.2"


def _default_app_dir():
    """Per-user folder for settings + log: %LOCALAPPDATA%\\SOTA on Windows,
    ~/Library/Application Support/SOTA on macOS (the platform's standard
    location — the old LOCALAPPDATA-fallback landed on a bare ~/SOTA folder
    in the home directory there, which reads as clutter on a Mac)."""
    if os.name == "nt":
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SOTA")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/SOTA")
    return os.path.expanduser("~/.config/SOTA")


APP_DIR = _default_app_dir()
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
LOG_FILE = os.path.join(APP_DIR, "sota.log")


def app_root():
    """Folder the app lives in: next to SOTA.exe when packaged, or the
    source folder when run with `python app.py`.

    On a packaged macOS build, sys.executable lives deep inside the bundle
    (SOTA.app/Contents/MacOS/SOTA) — "next to the app" must mean next to
    SOTA.app itself, not inside it: writing models/ and output/ into the
    bundle breaks its code signature and fails outright when the app sits
    somewhere read-only. Climb out to the folder that contains the .app."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if sys.platform == "darwin":
            probe = exe_dir
            while probe and probe != os.path.dirname(probe):
                if probe.endswith(".app"):
                    return os.path.dirname(probe)
                probe = os.path.dirname(probe)
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


# Every downloaded model — whisper, SenseVoice + its VAD front-end, and the
# local LLM — lives in one folder next to the app, alongside output/, so the
# whole thing stays a single self-contained, movable folder rather than
# splitting bulky downloads off into %LOCALAPPDATA%.
MODELS_DIR = os.path.join(app_root(), "models")
# funasr's downloader (modelscope) doesn't take a per-call cache location
# the way huggingface_hub does — it only reads this env var, which needs to
# be set before funasr is ever imported. Pointing it at app_root() (not
# MODELS_DIR itself) is deliberate: modelscope always nests its own
# "models" subfolder under whatever this points to, so this makes that
# land at exactly MODELS_DIR, next to whisper's and the LLM's own
# models--... folders instead of a separate nested copy.
os.environ.setdefault("MODELSCOPE_CACHE", app_root())

DEFAULT_OUTPUT_FOLDER = os.path.join(app_root(), "output")

# The output base is configurable (Settings tab) but defaults to the
# self-contained output/ folder next to the app. It's module-level mutable
# state set once at startup (and again whenever the user changes it) so the
# worker modules can keep asking settings for "the" output location without
# each of them having to be handed a folder explicitly.
_output_base = DEFAULT_OUTPUT_FOLDER


def set_output_base(path):
    """Points transcript/recording output at `path` (falls back to the
    default output/ folder next to the app when empty/None)."""
    global _output_base
    _output_base = path if path and os.path.isabs(path) else DEFAULT_OUTPUT_FOLDER


def output_base():
    return _output_base


# Recorded-file transcripts (Transcribe/Edit/AI tabs) and live-dictation
# recordings land in separate subfolders — they're different kinds of output
# and mixing them made the folder confusing to browse once both were in use.
def transcriptions_folder():
    return os.path.join(_output_base, "Transcriptions")


def live_recordings_folder():
    return os.path.join(_output_base, "Live Recordings")


DEFAULTS = {
    "quality": "balanced",
    "transcribe_language": "auto",
    "sensevoice_preferred": True,
    "ui_language": "en",
    "llm_mode": "summarize",
    "llm_target": "zh-hant",
    "llm_quality": "balanced",
    "editor_font_size": 14,
    "llm_source_font_size": 13,
    "llm_output_font_size": 13,
    "llm_panel_split": 0.5,
    "live_language": "auto",
    "live_text_font_size": 14,
    # Convert Mandarin/Cantonese transcripts to Traditional Chinese (OpenCC).
    "chinese_traditional": True,
    # Timestamp-marker visibility is deliberately NOT here — it's a
    # session-only toggle (see app.py's self.timestamps_visible), always
    # starting off, same as the punctuation pad.
    # "" = system default microphone; otherwise a device name from
    # live_transcription.list_input_devices().
    "live_mic_device": "",
    # "" = DEFAULT_OUTPUT_FOLDER (output/ next to the app).
    "output_folder": "",
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


def _migrate_old_models_dir():
    """Copies model folders left over from a version that stored downloads
    under %LOCALAPPDATA%\\SOTA\\models — without this, upgrading would
    silently lose track of (and re-download) everything already fetched
    there.

    Copies per-model-folder rather than moving the whole tree in one
    shutil.move() call, and never deletes the old copy: a single multi-GB
    move across drives (LOCALAPPDATA and the app folder aren't guaranteed
    to be on the same volume) can fail partway through — e.g. on a lock
    file transiently held elsewhere — leaving neither location complete.
    Copying one already-self-contained model folder at a time means a
    failure on one doesn't touch the others, and skipping any folder
    that already exists at the destination makes this safe to run on
    every launch instead of needing a fragile "did we already migrate"
    flag. The old copy is left in place for the user to delete by hand
    once they've confirmed everything works from the new location.
    """
    old_dir = os.path.join(APP_DIR, "models")
    if old_dir == MODELS_DIR or not os.path.isdir(old_dir):
        return
    import shutil

    for name in os.listdir(old_dir):
        src = os.path.join(old_dir, name)
        dst = os.path.join(MODELS_DIR, name)
        if not os.path.isdir(src) or os.path.exists(dst):
            continue
        try:
            shutil.copytree(src, dst)
        except Exception:
            log_exception(f"Model folder migration failed for {name}:")


_migrate_old_models_dir()
