"""AI-assisted analysis and editing for Audio Studio's Edit tab.

Deliberately independent of Transcription Studio's AI stack —
transcriber.get_vad_model()'s shared cache and TranscriberWorker's
WhisperModel are both tied to the user's Transcribe-tab engine/quality
settings, and audio_clean.py already couples to the former. This module
owns its own model lifecycle instead (its own module-level caches, a
fixed whisper size never read from Transcribe-tab settings), so a change
to those settings can never affect Audio Studio's AI features and vice
versa. It's fine to reuse transcriber's local-path resolution helpers
(read-only lookups, to avoid a duplicate download of files SenseVoice/
Whisper already have on disk) — just never its cached model instances.

Speaker count is intentionally not reported anywhere here — that needs a
diarization model, which is out of scope. Echo detection is also
intentionally absent — no reliable low-cost detector exists among this
app's current dependencies; the Edit tab surfaces this as "not checked in
this version" rather than guessing or silently omitting the line.
"""

import hashlib
import os
from math import gcd

import numpy as np

import audio_profiles
import settings
import transcriber

AI_EDIT_SAMPLE_RATE = 16000  # what both the VAD model and whisper "base" expect
AI_EDIT_WHISPER_SIZE = "base"

# Each entry may be one word ("um") or a multi-word phrase ("you know",
# "sort of") — filler_word_ranges below matches the longest configured
# phrase first at each position, so a 2-word entry like "you know" isn't
# missed just because "you" alone happens to also be a match target
# elsewhere. English-only for now (matches the detector's word-level
# transcription, which is English-only itself). This is only the
# fallback if prefs somehow has no list at all (shouldn't normally
# happen — settings.DEFAULTS carries the same list) — the list a user
# actually edits via the AI panel's "Filler Words…" button lives in
# settings (key "ai_filler_words"), read fresh by current_filler_words()
# on every analysis rather than cached, so an edit takes effect on the
# very next Analyze without needing to reload anything.
DEFAULT_FILLER_WORDS = ["um", "uh", "erm", "hmm", "uhh", "umm", "mm", "ah", "aah", "er",
                       "you know", "i mean", "sort of", "kind of"]


def current_filler_words():
    words = settings.load().get("ai_filler_words") or DEFAULT_FILLER_WORDS
    return {w.strip().lower() for w in words if w.strip()}

DEFAULT_LONG_PAUSE_S = 1.5      # matches audio_clean.remove_long_pauses's/detect_silences's own default —
                                # shorter than this is a normal breath/sentence pause, not a problem to flag
REPETITION_MAX_GAP_S = 1.2      # how long a pause between the two halves of a repeat still counts as "immediate"
REPETITION_MAX_PHRASE_WORDS = 4  # longest repeated phrase considered, e.g. "you know you know" = 2

_whisper_cache = {}
_vad_cache = {}


def _resample(buffer, orig_sr, target_sr):
    if orig_sr == target_sr or buffer.size == 0:
        return buffer.astype(np.float32)
    from scipy.signal import resample_poly

    g = gcd(orig_sr, target_sr)
    up, down = target_sr // g, orig_sr // g
    return resample_poly(buffer, up, down).astype(np.float32)


# -- speech/pause detection -------------------------------------------------
# Own VAD instance (own cache, own AutoModel object) — never
# transcriber.get_vad_model()'s shared one. Reuses SenseVoice's
# already-downloaded model files via transcriber.sensevoice_local_paths()
# (a read-only path lookup, not a shared model) when present; falls back to
# a plain energy-threshold detector — coarser, but dependency-free — when
# SenseVoice/VAD has never been downloaded, so analysis still works.

def _get_vad_model():
    if "model" in _vad_cache:
        return _vad_cache["model"]
    model = None
    paths = transcriber.sensevoice_local_paths()
    if paths:
        try:
            from funasr import AutoModel

            model = AutoModel(model=paths[1], device="cpu", disable_update=True)
        except Exception:
            model = None
    _vad_cache["model"] = model
    return model


def _energy_speech_segments(audio, sample_rate, frame_ms=30, threshold_rms=0.015):
    """No-model fallback: flags any frame_ms window whose RMS clears
    threshold_rms as speech, merging adjacent speech frames into spans.
    Coarser than VAD (no phoneme awareness — a loud non-speech sound
    counts as speech) but needs no downloaded model at all."""
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    n = len(audio) // frame
    if n == 0:
        return []
    frames = audio[:n * frame].reshape(n, frame)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    is_speech = rms >= threshold_rms
    segments, start = [], None
    for i, speaking in enumerate(is_speech):
        t = i * frame / sample_rate
        if speaking and start is None:
            start = t
        elif not speaking and start is not None:
            segments.append((start, t))
            start = None
    if start is not None:
        segments.append((start, len(audio) / sample_rate))
    return segments


def _speech_segments_s(buffer, sample_rate):
    """[(start_s, end_s), ...] speech spans in the ORIGINAL buffer's
    coordinates, detected on a resampled 16kHz copy."""
    audio16k = _resample(buffer, sample_rate, AI_EDIT_SAMPLE_RATE)
    vad = _get_vad_model()
    if vad is not None:
        res = vad.generate(input=audio16k, cache={})
        segments_ms = res[0].get("value", []) if res else []
        return [(s / 1000.0, e / 1000.0) for s, e in segments_ms]
    return _energy_speech_segments(audio16k, AI_EDIT_SAMPLE_RATE)


def long_pause_ranges(buffer, sample_rate, min_pause_s=DEFAULT_LONG_PAUSE_S):
    """[(start_s, end_s), ...] gaps between detected speech (plus any
    lead-in/tail) at least min_pause_s long."""
    speech = _speech_segments_s(buffer, sample_rate)
    duration = len(buffer) / sample_rate if sample_rate else 0.0
    spans, prev_end = [], 0.0
    for start, end in speech:
        if start - prev_end >= min_pause_s:
            spans.append((prev_end, start))
        prev_end = max(prev_end, end)
    if duration - prev_end >= min_pause_s:
        spans.append((prev_end, duration))
    return spans


# -- word-level transcription (own whisper instance) -------------------------

def _get_whisper_model():
    if "model" in _whisper_cache:
        return _whisper_cache["model"]
    model = None
    try:
        from faster_whisper import WhisperModel  # deferred: heavy import

        local_dir = transcriber.whisper_local_model_dir(AI_EDIT_WHISPER_SIZE)
        model = WhisperModel(
            local_dir or AI_EDIT_WHISPER_SIZE, device="cpu", compute_type="int8",
            cpu_threads=max(1, (os.cpu_count() or 4) - 1))
    except Exception:
        settings.log_exception("Audio Studio AI: failed to load the word-timing model:")
        model = None
    _whisper_cache["model"] = model
    return model


def transcribe_words(buffer, sample_rate):
    """[{"word": str, "start": float, "end": float}, ...] word-level
    timestamps from this module's own whisper "base" instance — never
    TranscriberWorker's model, so the Transcribe tab's engine/quality
    choice has no bearing here. Returns [] if the model can't be loaded
    (e.g. fully offline with no cached copy of "base" yet)."""
    model = _get_whisper_model()
    if model is None:
        return []
    audio16k = _resample(buffer, sample_rate, AI_EDIT_SAMPLE_RATE)
    segments, _info = model.transcribe(audio16k, word_timestamps=True, vad_filter=False)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            word = (w.word or "").strip()
            if word:
                words.append({"word": word, "start": w.start, "end": w.end})
    return words


def _normalize_word(word):
    return word.strip(" .,!?…").lower()


def filler_word_ranges(words, filler_words=None):
    """[{"start", "end", "word"}, ...] one entry per detected filler —
    single word ("um") or multi-word phrase ("you know"), matched against
    the user's configured list (see DEFAULT_FILLER_WORDS). At each
    position, the LONGEST configured phrase that matches wins, so a
    2-word entry like "you know" is flagged as one filler covering both
    words rather than (or in addition to) any shorter/overlapping match.
    `filler_words` lets a caller pin a specific set (e.g. analyze()
    reading it once per call rather than per-word); defaults to the
    user's current list."""
    filler_words = filler_words if filler_words is not None else current_filler_words()
    if not filler_words:
        return []
    max_len = max(len(f.split()) for f in filler_words)
    norm = [_normalize_word(w["word"]) for w in words]
    ranges = []
    n = len(words)
    i = 0
    while i < n:
        matched_len = 0
        for length in range(min(max_len, n - i), 0, -1):
            phrase = " ".join(norm[i:i + length])
            if phrase and phrase in filler_words:
                ranges.append({
                    "start": words[i]["start"], "end": words[i + length - 1]["end"],
                    "word": " ".join(w["word"] for w in words[i:i + length])})
                matched_len = length
                break
        i += matched_len if matched_len else 1
    return ranges


def repetition_ranges(words, max_gap_s=REPETITION_MAX_GAP_S,
                      max_phrase_words=REPETITION_MAX_PHRASE_WORDS):
    """[{"start", "end", "word"}, ...] one entry per IMMEDIATE stutter-
    style repeat — the same word, or the same short phrase (up to
    max_phrase_words), said again right away: "the the cat", "I think, I
    think", "you know you know". Only the repeated half is flagged, never
    the first occurrence, so removing it leaves exactly one copy.

    Deliberately NOT "this word appeared again somewhere in the last few
    seconds" (an earlier version worked that way) — ordinary sentences
    reuse common words (I, the, you, and, ...) constantly within any
    multi-second window, so that approach flagged completely normal
    speech as "repetition". Requiring the repeat to be IMMEDIATE (within
    max_gap_s of the first occurrence ending, i.e. no unrelated words in
    between) is what actually distinguishes a stutter/false-start from
    everyday word reuse.

    Checks the longest phrase length first at each position so "you know
    you know" is caught as one 2-word repeat, not reported twice over as
    two separate 1-word repeats ("you you", "know know")."""
    norm = [_normalize_word(w["word"]) for w in words]
    ranges = []
    n = len(words)
    i = 0
    while i < n:
        matched_len = 0
        max_len = min(max_phrase_words, (n - i) // 2)
        for length in range(max_len, 0, -1):
            phrase = norm[i:i + length]
            if not all(phrase) or phrase != norm[i + length:i + 2 * length]:
                continue
            gap = words[i + length]["start"] - words[i + length - 1]["end"]
            if gap > max_gap_s:
                continue
            text = " ".join(w["word"] for w in words[i + length:i + 2 * length])
            ranges.append({"start": words[i + length]["start"],
                           "end": words[i + 2 * length - 1]["end"], "word": text})
            matched_len = 2 * length
            break
        i += matched_len if matched_len else 1
    return ranges


# -- recording health -------------------------------------------------------

def estimate_noise_floor_db(buffer, sample_rate):
    """RMS of the quietest 10% of 50ms frames, in dBFS — a cheap proxy for
    background noise level (not a substitute for a real noise-profile
    analysis)."""
    frame = max(1, int(sample_rate * 0.05))
    n = len(buffer) // frame
    if n == 0:
        return -90.0
    frames = buffer[:n * frame].reshape(n, frame)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    quietest = np.sort(rms)[:max(1, n // 10)]
    floor_rms = float(np.mean(quietest))
    return float(20 * np.log10(max(floor_rms, 1e-9)))


def detect_clipping_incidents(buffer, threshold=0.98):
    """Count of samples at/above `threshold` — a proxy for how much (not
    just whether) the recording clipped."""
    return int(np.sum(np.abs(buffer) >= threshold))


def measure_loudness_variance_db(buffer, sample_rate, window_s=3.0):
    """Standard deviation, across window_s-long chunks, of each chunk's
    RMS level in dB — a proxy for "inconsistent volume" (moving toward/
    away from the mic, mixed near/far speakers)."""
    window = max(1, int(sample_rate * window_s))
    n = len(buffer) // window
    if n < 2:
        return 0.0
    chunks = buffer[:n * window].reshape(n, window)
    rms = np.sqrt(np.mean(chunks.astype(np.float64) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    return float(np.std(db))


def compute_recording_health(noise_floor_db, clip_incidents, loudness_variance_db,
                             pause_ratio, filler_ratio):
    """0-100 heuristic — a weighted-penalty formula, not a learned
    classifier: each factor subtracts up to its own capped penalty from a
    100 baseline. A first-pass design; the weights below need calibration
    against real known-good/known-bad recordings before being trusted as a
    hard verdict — treat the number as a rough indicator, not a grade."""
    score = 100.0
    score -= min(30.0, max(0.0, noise_floor_db + 50.0) * 1.2)   # baseline: a clean room sits near -50dB
    score -= min(30.0, clip_incidents / 50.0)
    score -= min(20.0, loudness_variance_db * 2.0)
    score -= min(20.0, (pause_ratio + filler_ratio) * 100.0)
    return int(max(0, min(100, round(score))))


class AnalysisReport:
    def __init__(self, health, noise_floor_db, clip_incidents, loudness_variance_db,
                 long_pauses, filler_words, repetitions, duration_s):
        self.health = health
        self.noise_floor_db = noise_floor_db
        self.clip_incidents = clip_incidents
        self.loudness_variance_db = loudness_variance_db
        self.long_pauses = long_pauses
        self.filler_words = filler_words
        self.repetitions = repetitions
        self.duration_s = duration_s


# Rough thresholds for "is this worth flagging as a problem" — separate
# from the health-score penalties above, which are continuous; these just
# decide which lines the Edit tab's AI panel shows at all.
NOISE_FLOOR_PROBLEM_DB = -40.0
LOUDNESS_VARIANCE_PROBLEM_DB = 4.0


def analyze(buffer, sample_rate):
    """Single entry point: one VAD pass + one word-transcription pass,
    every metric derived from those two."""
    duration = len(buffer) / sample_rate if sample_rate else 0.0
    noise_floor_db = estimate_noise_floor_db(buffer, sample_rate)
    clip_incidents = detect_clipping_incidents(buffer)
    loudness_variance_db = measure_loudness_variance_db(buffer, sample_rate)
    pauses = long_pause_ranges(buffer, sample_rate, min_pause_s=DEFAULT_LONG_PAUSE_S)
    pause_time = sum(e - s for s, e in pauses)
    pause_ratio = (pause_time / duration) if duration else 0.0
    words = transcribe_words(buffer, sample_rate)
    fillers = filler_word_ranges(words)
    filler_ratio = (len(fillers) / len(words)) if words else 0.0
    repetitions = repetition_ranges(words)
    health = compute_recording_health(
        noise_floor_db, clip_incidents, loudness_variance_db, pause_ratio, filler_ratio)
    return AnalysisReport(
        health=health, noise_floor_db=noise_floor_db, clip_incidents=clip_incidents,
        loudness_variance_db=loudness_variance_db, long_pauses=pauses,
        filler_words=fillers, repetitions=repetitions, duration_s=duration)


# -- NSNet2 (ML noise reduction, AI panel only) ------------------------
# The ONE ML-driven option in this module — deliberately kept out of
# audio_denoise.py entirely, so Enhance's manual "Noise reduction" row
# stays a plain, fully-deterministic tool (noisereduce only) and this is
# the only place in the app a model-based denoise result appears. Runs
# through onnxruntime, already a dependency of faster-whisper's own VAD
# filter — no new heavy runtime.
#
# Model: Microsoft's NSNet2 (DNS Challenge baseline, arXiv:2008.06412),
# 16kHz/20ms-window variant — chosen over the 48kHz variant specifically
# because the 16kHz inference path is pure numpy/scipy (no torch needed),
# matching this module's 16kHz-everywhere convention (same rate as VAD/
# whisper above) and keeping this feature's only new dependency to
# onnxruntime. Model weights are CC BY 4.0; the (short, from-scratch)
# inference code below is a from-scratch numpy port of Microsoft's own
# MIT-licensed reference implementation (microsoft/DNS-Challenge,
# NSNet2-baseline/{featurelib,enhance_onnx}.py) — verified bit-for-bit
# identical STFT/ISTFT output against that reference before landing here.
#
# The official repo removed the NSNet2-baseline directory from its master
# branch at some point after publishing it; the model file is still
# reachable via a specific historical commit, which is what's pinned
# below (not a branch, which could change or 404 later). Every download
# is verified against a sha256 computed independently from a manual
# download of that exact URL — if that check ever fails, treat it as the
# file having changed unexpectedly, not as a bug to route around.
NSNET2_MODEL_URL = ("https://raw.githubusercontent.com/microsoft/DNS-Challenge/"
                    "a052ad5a7714bcc6069b515666e837bf973099de/"
                    "NSNet2-baseline/nsnet2-20ms-baseline.onnx")
NSNET2_SHA256 = "88429b6253600be840ab816f46f466811d20078142fb12bff8cafe2b27bd4ca9"
NSNET2_DOWNLOAD_BYTES = 10752263
NSNET2_DIR_NAME = "nsnet2"
NSNET2_FILENAME = "nsnet2-20ms-baseline.onnx"

# NSNet2's own fixed operating parameters (from the reference cfg — do
# not change independently of each other, they're tied to the trained
# model's expected input shape: NFFT=320 -> 161 freq bins at 16kHz).
NSNET2_SAMPLE_RATE = 16000
NSNET2_NFFT = 320
NSNET2_WIN_SAMPLES = 320   # 20ms window at 16kHz
NSNET2_HOP_SAMPLES = 160   # 50% overlap
NSNET2_MIN_GAIN = 10 ** (-80 / 20)  # -80dB gain floor, matches reference cfg['mingain']

_nsnet2_cache = {}


def nsnet2_model_dir():
    """Public — the Settings model manager needs this to list/delete the
    folder without reaching into a private helper."""
    return os.path.join(settings.MODELS_DIR, NSNET2_DIR_NAME)


def _nsnet2_path():
    return os.path.join(nsnet2_model_dir(), NSNET2_FILENAME)


def nsnet2_is_downloaded():
    path = _nsnet2_path()
    return os.path.isfile(path) and os.path.getsize(path) == NSNET2_DOWNLOAD_BYTES


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_nsnet2_model(on_progress=None):
    """Downloads NSNet2's 16kHz ONNX model to settings.MODELS_DIR/nsnet2/,
    verifying its sha256 before accepting it. Downloads to a .part file
    first and only renames it into place after that check passes, so an
    interrupted/corrupted/tampered download can never be mistaken for a
    valid model by nsnet2_is_downloaded()'s later callers — same
    "materializes atomically on success only" principle as this app's
    other model downloads (see transcriber.whisper_local_model_dir's
    docstring). Raises on any failure — the caller (app.py's Settings
    model manager) is responsible for surfacing that to the user."""
    import urllib.request

    os.makedirs(nsnet2_model_dir(), exist_ok=True)
    dest = _nsnet2_path()
    tmp_path = dest + ".part"
    request = urllib.request.Request(
        NSNET2_MODEL_URL, headers={"User-Agent": "SOTA-AudioStudio"})
    downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, \
                open(tmp_path, "wb") as f:
            while True:
                chunk = response.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress and NSNET2_DOWNLOAD_BYTES:
                    on_progress(min(99, int(downloaded / NSNET2_DOWNLOAD_BYTES * 100)))
        if _sha256_of(tmp_path) != NSNET2_SHA256:
            raise ValueError("NSNet2 download failed integrity verification (sha256 mismatch)")
        os.replace(tmp_path, dest)
    finally:
        if os.path.isfile(tmp_path):
            os.remove(tmp_path)
    if on_progress:
        on_progress(100)


def unload_nsnet2_model():
    """Drops the cached onnxruntime session — called before deleting the
    model's files from Settings, same reasoning as
    transcriber.unload_sensevoice_model(): an open InferenceSession can
    hold the .onnx file locked on Windows, which would otherwise make the
    delete fail right after the user confirms it."""
    _nsnet2_cache.pop("session", None)


def _get_nsnet2_session():
    if "session" in _nsnet2_cache:
        return _nsnet2_cache["session"]
    if not nsnet2_is_downloaded():
        return None
    import onnxruntime as ort

    # CPUExecutionProvider explicitly, rather than onnxruntime's default
    # auto-selection — this app has no GPU-acceleration story anywhere
    # else (Whisper/SenseVoice/llama.cpp are all pinned CPU-only too), so
    # there's no reason to let onnxruntime probe for/attempt anything else.
    session = ort.InferenceSession(_nsnet2_path(), providers=["CPUExecutionProvider"])
    _nsnet2_cache["session"] = session
    return session


def _nsnet2_stft(x, win):
    """Mono short-time Fourier transform — a numpy port of NSNet2's own
    featurelib.stft (verified bit-identical against the original on
    synthetic input), trimmed to the mono-only case this module needs."""
    n_win = len(win)
    n_hop = NSNET2_HOP_SAMPLES
    n_frames = int(np.ceil((len(x) + n_win - n_hop) / n_hop))
    padded_len = n_frames * n_hop
    if padded_len > len(x):
        x = np.concatenate([x, np.zeros(padded_len - len(x), dtype=np.float32)])
    specsize = NSNET2_NFFT // 2 + 1
    spec = np.zeros((specsize, n_frames), dtype=np.complex128)
    frame_buf = np.zeros(n_win, dtype=np.float32)
    for i in range(n_frames):
        idx = i * n_hop
        frame_buf = np.concatenate([frame_buf[n_hop:], x[idx:idx + n_hop]])
        spec[:, i] = np.fft.rfft(frame_buf * win, NSNET2_NFFT)
    delay = n_win // n_hop - 1  # matches the reference's nodelay=True trim
    return spec[:, delay:]


def _nsnet2_istft(spec, win):
    """Inverse of _nsnet2_stft (overlap-add) — numpy port of featurelib.istft."""
    specsize, n_frames = spec.shape
    n_win = len(win)
    n_hop = NSNET2_HOP_SAMPLES
    out_len = n_hop * (n_frames - 1) + n_win
    out = np.zeros(out_len, dtype=np.float64)
    for i in range(n_frames):
        frame = np.fft.irfft(spec[:, i], NSNET2_NFFT)[:n_win] * win
        idx = i * n_hop
        out[idx:idx + n_win] += frame
    return out.astype(np.float32)


def _nsnet2_enhance_16k(session, audio16k):
    win = np.sqrt(np.hanning(NSNET2_WIN_SAMPLES)).astype(np.float32)
    spec = _nsnet2_stft(audio16k, win)
    log_power = np.log10(np.maximum(np.abs(spec) ** 2, 1e-12)).astype(np.float32)
    model_input = np.expand_dims(log_power.T, axis=0)  # [batch, frames, freq]
    input_name = session.get_inputs()[0].name
    gain = session.run(None, {input_name: model_input})[0][0]  # [frames, freq]
    gain = np.clip(gain.T, NSNET2_MIN_GAIN, 1.0)  # back to [freq, frames]
    return _nsnet2_istft(spec * gain, win)


def denoise_nsnet2(buffer, sample_rate):
    """Runs NSNet2 on `buffer`, resampling to/from its fixed 16kHz
    operating rate. The output is forced back to `buffer`'s exact
    original length (STFT/ISTFT framing and resampling can each shift a
    handful of samples off the input length) — callers (AI Enhance, AI
    Presets) rely on denoise never changing buffer length, since detected
    pause/filler/repetition ranges are computed against the pre-denoise
    buffer. Raises RuntimeError if the model hasn't been downloaded —
    callers should check nsnet2_is_downloaded() first and offer Settings
    instead of calling this blind."""
    session = _get_nsnet2_session()
    if session is None:
        raise RuntimeError("NSNet2 model not downloaded")
    original_len = len(buffer)
    audio16k = _resample(buffer, sample_rate, NSNET2_SAMPLE_RATE)
    enhanced16k = _nsnet2_enhance_16k(session, audio16k)
    enhanced16k = _fit_length(enhanced16k, len(audio16k))
    result = _resample(enhanced16k, NSNET2_SAMPLE_RATE, sample_rate)
    return _fit_length(result, original_len)


def _fit_length(array, target_len):
    if len(array) == target_len:
        return array
    if len(array) < target_len:
        return np.concatenate([array, np.zeros(target_len - len(array), dtype=np.float32)])
    return array[:target_len]


# -- AI presets ---------------------------------------------------------
# Fixed, curated DSP chains — not user-editable, so kept separate from
# audio_profiles.py's user-savable "configurations" — but executed through
# that module's existing run_profile/STEP_CATALOG engine, so this is only
# ever a table of step sequences, no new DSP code.

AI_PRESETS = {
    "podcast": {"steps": [
        {"key": "denoise"}, {"key": "high_pass", "value": 90},
        {"key": "compress", "value": -18}, {"key": "loudness", "value": -16},
    ]},
    "lecture": {"steps": [
        {"key": "denoise"}, {"key": "high_pass", "value": 100},
        {"key": "loudness", "value": -18}, {"key": "pause", "value": 1.5},
    ]},
    "meeting": {"steps": [
        {"key": "denoise"}, {"key": "gate", "value": -45}, {"key": "loudness", "value": -18},
    ]},
    "interview": {"steps": [
        {"key": "denoise"}, {"key": "compress", "value": -20}, {"key": "loudness", "value": -16},
    ]},
    "youtube": {"steps": [
        {"key": "denoise"}, {"key": "eq", "value": 2.0},
        {"key": "compress", "value": -18}, {"key": "loudness", "value": -14},
    ]},
    "audiobook": {"steps": [
        {"key": "denoise"}, {"key": "high_pass", "value": 80},
        {"key": "loudness", "value": -18}, {"key": "pause", "value": 2.0},
    ]},
    "phone": {"steps": [
        {"key": "denoise"}, {"key": "low_pass", "value": 3400},
        {"key": "high_pass", "value": 300}, {"key": "loudness", "value": -16},
    ]},
}

AI_PRESET_ORDER = ["podcast", "lecture", "meeting", "interview", "youtube", "audiobook", "phone"]


def apply_preset(buffer, sample_rate, preset_key, return_ranges=False):
    """Runs a preset's step chain — the same STEP_CATALOG plain-DSP steps
    audio_profiles.run_profile uses for everything except "denoise",
    which goes through this module's own NSNet2 (never
    audio_denoise.denoise's noisereduce-only dispatcher) — keeping the AI
    panel's noise reduction consistently ML-driven even for presets,
    matching the same separation AI Enhance's checklist observes. "pause"
    still delegates to audio_clean.remove_long_pauses (its VAD, tied to
    transcriber's shared cache) exactly as run_profile itself already
    did — unchanged here, since only the denoise engine choice was in
    scope for this split."""
    import audio_clean

    result = buffer
    pause_value = None
    for step in AI_PRESETS[preset_key]["steps"]:
        key, value = step["key"], step.get("value")
        if key == "pause":
            pause_value = value
        elif key == "denoise":
            result = denoise_nsnet2(result, sample_rate)
        else:
            result = audio_profiles.STEP_CATALOG[key](result, sample_rate, value)
    removed_ranges = []
    if pause_value is not None:
        result, removed_ranges = audio_clean.remove_long_pauses(
            result, sample_rate, max_pause_s=pause_value, return_ranges=True)
    return (result, removed_ranges, "nsnet2") if return_ranges else result
