"""User settings, app folders, and logging for SOTA."""

import json
import os
import sys
import traceback

APP_VERSION = "2.1.0"


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
    source folder when run with `python app.py`. Windows-only helper — see
    the MODELS_DIR comment below for why macOS can't use this."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


if sys.platform == "darwin":
    # Every downloaded model — whisper, SenseVoice + its VAD front-end, and
    # the local LLM — plus output/ would ideally live in one folder next to
    # the app, same as Windows below. But a freshly downloaded, not-yet-moved
    # .app runs under Gatekeeper's "App Translocation" from a read-only
    # synthetic path (/private/var/.../AppTranslocation/<uuid>/d/), and even
    # after being dragged into /Applications — the conventional next step —
    # writing a sibling folder there needs admin rights a normal user
    # doesn't have. Either way "next to the app" isn't reliably writable on
    # macOS, so use the standard per-user Application Support folder
    # instead — writable regardless of where the .app itself is running
    # from, and the platform-conventional place for this kind of data.
    MODELS_DIR = os.path.join(APP_DIR, "models")
    os.environ.setdefault("MODELSCOPE_CACHE", APP_DIR)
    DEFAULT_OUTPUT_FOLDER = os.path.join(APP_DIR, "output")
else:
    # Every downloaded model — whisper, SenseVoice + its VAD front-end, and
    # the local LLM — lives in one folder next to the app, alongside
    # output/, so the whole thing stays a single self-contained, movable
    # folder rather than splitting bulky downloads off into %LOCALAPPDATA%.
    MODELS_DIR = os.path.join(app_root(), "models")
    # funasr's downloader (modelscope) doesn't take a per-call cache location
    # the way huggingface_hub does — it only reads this env var, which needs
    # to be set before funasr is ever imported. Pointing it at app_root()
    # (not MODELS_DIR itself) is deliberate: modelscope always nests its own
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


# Named to match Transcription Studio's own tab-group name (parallel to
# audio_studio_folder()'s "Audio Studio" below) rather than "Transcriptions"
# — this is that whole studio's output folder, transcripts included, not
# a narrower "just transcripts" folder that happens to also hold audio.
def transcriptions_folder():
    return os.path.join(_output_base, "Transcription Studio")


def live_recordings_folder():
    """Live Transcription's own raw audio — nested under
    transcriptions_folder() (not a sibling of it) since it's always the
    audio behind one of the transcripts living right alongside it, not a
    separate kind of output in its own right. Named "Transcription
    Audio" rather than "Live Recordings" for the same reason: from
    inside the Transcription Studio folder, "Live Recordings" reads as
    an unrelated category, while "Transcription Audio" reads as exactly
    what it is — the audio for these transcripts."""
    return os.path.join(transcriptions_folder(), "Transcription Audio")


# Audio Studio's own recordings/originals live under one "Audio Studio"
# subfolder, parallel to Transcription Studio above — a different kind of
# output again (raw/edited audio, not transcripts).
def audio_studio_folder():
    return os.path.join(_output_base, "Audio Studio")


def audio_originals_folder():
    """Where the Record tab saves every recording, AND where Audio
    Studio's Edit subtab keeps its protected "never overwritten" copies
    (see audio_clip.AudioClip.load) — deliberately the SAME folder, not
    two. A freshly recorded file sent straight to Edit ("Open in Edit")
    used to get copied a second time into a separate Originals folder
    purely because Edit always makes a protected copy on first open; now
    that a recording already lands in this exact folder, AudioClip.load's
    own dedup check (_same_file) recognizes the destination as the same
    file and skips the copy entirely — no more duplicate storage for the
    common record-then-edit path. Anything opened into Edit from
    somewhere else on disk still gets its own protected copy here, same
    as before."""
    return os.path.join(audio_studio_folder(), "Originals")


def audio_recordings_folder():
    """Same folder as audio_originals_folder() — kept as its own name
    only because "the Record tab's own output folder" and "Edit's
    protected-copy folder" are different concepts that happen to share
    one location now; callers keep asking for whichever concept applies
    to them."""
    return audio_originals_folder()


def audio_exports_folder():
    return os.path.join(audio_studio_folder(), "Exports")


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
    # Audio Studio's AI panel: words/phrases Remove Filler Words looks
    # for (audio_ai_edit.py) — an entry may be a single word ("um") or a
    # multi-word phrase ("you know"). User-editable via the AI panel's
    # "Filler Words…" button — kept in prefs, not hardcoded, since what
    # counts as a filler is genuinely dialect/speaker-dependent (English-
    # only for now, same limitation the detector itself has).
    "ai_filler_words": ["um", "uh", "erm", "hmm", "uhh", "umm", "mm", "ah", "aah", "er",
                        "you know", "i mean", "sort of", "kind of"],
    # Recording/transcription safeguards (app.py's _check_recording_safeguards) —
    # three escalating tiers (warn/alert/stop) across three dimensions
    # (how long a recording's been running, free disk space, free RAM).
    # "Stop" is the only tier that actually acts (auto-stops the
    # recording); warn/alert only ever show a dismissible notice, never
    # interrupt anything — see app.py's _show_safeguard_notice. All nine
    # are user-editable in Settings.
    "safeguard_warn_hours": 2.0,
    "safeguard_alert_hours": 4.0,
    "safeguard_stop_hours": 8.0,
    "safeguard_warn_disk_minutes": 10.0,
    "safeguard_alert_disk_minutes": 2.0,
    "safeguard_stop_disk_minutes": 0.5,
    "safeguard_warn_ram_gb": 1.0,
    "safeguard_alert_ram_gb": 0.4,
    "safeguard_stop_ram_gb": 0.15,
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
