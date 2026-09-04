"""User-savable Enhance pipelines ("configurations") for Audio Studio.

Auto Enhance's built-in sequence (audio_clean.auto_enhance) is one fixed
pipeline; this lets a user build and save their own — an ordered subset
of the same steps Enhance's own sliders expose, run in one click. The
built-in sequence is always available too, as the reserved "Default"
entry (see DEFAULT_PROFILE_NAME) — selecting it (or "Revert to default")
runs audio_clean.auto_enhance itself, not a copy of its steps here, so
the two can never drift apart.

A profile is {"name": str, "steps": [{"key": str, "value": float|None}, ...]}
— "steps" is deliberately a plain ordered list (not a dict) since order
is the entire point (the user picks which steps run and in what rank).
"""

import json
import os

import audio_clean
import audio_denoise
import audio_dsp
import settings

PROFILES_PATH = os.path.join(settings.audio_studio_folder(), "enhance_profiles.json")

# Reserved id for the built-in sequence — never stored in the profiles
# file, never deletable, always present in a picker alongside whatever
# the user has saved. Not user-facing text (the UI translates it).
DEFAULT_PROFILE_NAME = "__default__"

# step key -> apply_fn(buffer, sample_rate, value) -> buffer. "denoise"
# and "pause" are handled specially in run_profile (denoise also reports
# which engine ran; pause is the only step that changes the buffer's
# length, so it always runs last regardless of its rank in the saved
# list — every other step is a same-length transform, so reordering
# those relative to each other never needs remapping, but a pause
# anywhere except last would).
STEP_CATALOG = {
    "gate": lambda buf, sr, v: audio_dsp.noise_gate(buf, sr, threshold_db=v),
    "high_pass": lambda buf, sr, v: audio_dsp.high_pass(buf, sr, v),
    "low_pass": lambda buf, sr, v: audio_dsp.low_pass(buf, sr, v),
    "compress": lambda buf, sr, v: audio_dsp.compressor(buf, sr, threshold_db=v),
    "amplify": lambda buf, sr, v: audio_dsp.amplify(buf, v),
    "normalize": lambda buf, sr, v: audio_dsp.normalize(buf, v),
    "loudness": lambda buf, sr, v: audio_dsp.normalize_lufs(buf, sr, v),
    "eq": lambda buf, sr, v: audio_dsp.eq_band(buf, sr, 1000.0, v),
    "clicks": lambda buf, sr, v: audio_clean.remove_clicks(buf, sr, threshold=v),
}

# Steps with no numeric value at all — "denoise" uses its own tuned
# defaults (like the manual Noise reduction row does), same as the
# Default sequence, since a user building a custom configuration is
# making a deliberate choice, not relying on Auto Enhance's blind-safety
# gentling (see audio_clean.auto_enhance's own denoise call for why that
# one is gentler).
STEP_KEYS_NO_VALUE = {"denoise"}


def load_profiles():
    """[{"name": ..., "steps": [...]}, ...] — user-saved configurations
    only; the built-in Default isn't stored here, see DEFAULT_PROFILE_NAME."""
    if not os.path.isfile(PROFILES_PATH):
        return []
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        settings.log_exception("Audio Studio: failed to load Enhance configurations:")
        return []


def save_profiles(profiles):
    os.makedirs(os.path.dirname(PROFILES_PATH), exist_ok=True)
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2)


def find_profile(profiles, name):
    return next((p for p in profiles if p["name"] == name), None)


def run_profile(buffer, sample_rate, profile, return_ranges=False):
    """Runs a saved configuration's steps in their saved order (except
    "pause", forced last — see STEP_CATALOG's comment) and returns
    (result_buffer, removed_ranges, engine) — engine is the denoise
    engine that ran, or None if the profile has no "denoise" step;
    removed_ranges is [] unless a "pause" step was included.
    return_ranges=False collapses this to just result_buffer, matching
    every other Enhance apply path (see app.py's _apply_audio_effect)."""
    result = buffer
    pause_value = None
    engine = None
    for step in profile["steps"]:
        key, value = step["key"], step.get("value")
        if key == "pause":
            pause_value = value
        elif key == "denoise":
            result, engine = audio_denoise.denoise(result, sample_rate)
        else:
            result = STEP_CATALOG[key](result, sample_rate, value)
    removed_ranges = []
    if pause_value is not None:
        result, removed_ranges = audio_clean.remove_long_pauses(
            result, sample_rate, max_pause_s=pause_value, return_ranges=True)
    return (result, removed_ranges, engine) if return_ranges else result
