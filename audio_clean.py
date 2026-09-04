"""Enhance subtab's "Repairs & timing" operations for Audio Studio:
long-pause shortening, no-speech detection, section markers, and click/
pop removal — plus auto_enhance, the built-in Auto Enhance tone/loudness
pass. Silence/section detection reuses the fsmn-vad model already
bundled for SenseVoice (transcriber.get_vad_model) rather than needing
any new ML — see get_vad_model's own docstring for why that model (not
SenseVoice's own bundled VAD) is the one that exposes segment boundaries
at all. Plain-DSP effects (gain, filters, gate, compressor) live in
audio_dsp.py; ML noise reduction lives in audio_denoise.py and isn't
used here — see auto_enhance's own docstring for why.
"""

from math import gcd

import numpy as np

import audio_dsp
import transcriber

VAD_SAMPLE_RATE = transcriber.SENSEVOICE_SAMPLE_RATE  # 16000 — fsmn-vad's own rate


def _resample(buffer, orig_sr, target_sr):
    if orig_sr == target_sr or buffer.size == 0:
        return buffer.astype(np.float32)
    from scipy.signal import resample_poly

    g = gcd(orig_sr, target_sr)
    up, down = target_sr // g, orig_sr // g
    return resample_poly(buffer, up, down).astype(np.float32)


def _speech_segments_s(buffer, sample_rate):
    """[(start_s, end_s), ...] speech spans from fsmn-vad, run on a copy
    of `buffer` resampled to the model's own 16kHz rate — detection only;
    the original buffer/sample_rate is untouched and is what every
    second-based span here gets applied back onto."""
    vad = transcriber.get_vad_model()
    audio16k = _resample(buffer, sample_rate, VAD_SAMPLE_RATE)
    res = vad.generate(input=audio16k, cache={})
    segments_ms = res[0].get("value", []) if res else []
    return [(s / 1000.0, e / 1000.0) for s, e in segments_ms]


DEFAULT_SECTION_GAP_S = 3.0  # shorter than this is a normal sentence/breath pause, not a section break


def speech_sections(buffer, sample_rate, min_gap_s=DEFAULT_SECTION_GAP_S):
    """Returns [start_s, ...] — one time per "section": the first
    detected speech segment's start, and the start of every later
    segment that follows a gap of at least min_gap_s since speech last
    stopped. Used by app.py's "Detect sections" marker feature.

    This reuses the same VAD speech spans detect_silences/
    remove_long_pauses already compute, but merges across any gap
    shorter than min_gap_s first — VAD's own end-silence threshold is
    only ~800ms, so treating every one of its raw segment boundaries as
    a "section" produced a new marker roughly every sentence (found via
    real feedback: markers spaced ~1s apart, one per breath, not one per
    actual topic/section break)."""
    segments = _speech_segments_s(buffer, sample_rate)
    if not segments:
        return []
    starts = [segments[0][0]]
    prev_end = segments[0][1]
    for start, end in segments[1:]:
        if start - prev_end >= min_gap_s:
            starts.append(start)
        prev_end = max(prev_end, end)
    return starts


DEFAULT_MIN_SILENCE_S = 1.5  # shorter gaps are normal pauses in speaking, not worth flagging for removal


def detect_silences(buffer, sample_rate, min_duration_s=DEFAULT_MIN_SILENCE_S):
    """Returns [(start_s, end_s), ...] spans with no detected speech — the
    gaps between VAD-detected speech segments, plus any lead-in before the
    first one and tail after the last. Spans shorter than min_duration_s
    are dropped: VAD's own end-silence threshold is only ~800ms, so
    without a floor here this reported every natural breath/sentence
    pause as flagged for removal — technically a gap with no speech in
    it, but not something anyone should bulk-delete (a 0.5s gap is a
    normal pause in speaking, not a mistake).

    Despite the function's name, a span here just means "VAD didn't
    classify this as speech" — a loud clap, cough, or held noise also
    lands here even though it's not remotely quiet; the user-facing
    label for this feature is "No speech detected", not "Silence", for
    exactly that reason."""
    speech = _speech_segments_s(buffer, sample_rate)
    duration = len(buffer) / sample_rate
    spans = []
    prev_end = 0.0
    for start, end in speech:
        if start > prev_end:
            spans.append((prev_end, start))
        prev_end = max(prev_end, end)
    if duration > prev_end:
        spans.append((prev_end, duration))
    return [(s, e) for s, e in spans if e - s >= min_duration_s]


DEFAULT_PAUSE_KEEP_S = 0.3  # shared with app.py's tooltip text, so they can't drift apart


def remove_long_pauses(buffer, sample_rate, max_pause_s=1.5, keep_s=DEFAULT_PAUSE_KEEP_S,
                       return_ranges=False):
    """Shortens every silent gap longer than max_pause_s down to keep_s
    (a short, natural-sounding breath rather than a hard cut), preserving
    every detected speech segment untouched.

    return_ranges=True also returns the list of [start_s, end_s) spans
    (in the ORIGINAL, pre-shortening buffer's coordinates) that were
    shortened away — a caller tracking markers needs exactly this to keep
    them in sync (see AudioClip.apply_removing_ranges); callers that
    don't care about markers can ignore the flag entirely."""
    speech = _speech_segments_s(buffer, sample_rate)
    if not speech:
        result = buffer.copy()
        return (result, []) if return_ranges else result
    duration = len(buffer) / sample_rate
    keep_samples = int(keep_s * sample_rate)
    pieces = []
    removed_ranges = []
    prev_end = 0.0
    for i, (start, end) in enumerate(speech):
        gap = start - prev_end
        if gap > max_pause_s:
            pieces.append(np.zeros(keep_samples, dtype=np.float32))
            removed_ranges.append((prev_end + keep_s, start))
        else:
            pieces.append(buffer[int(prev_end * sample_rate):int(start * sample_rate)])
        pieces.append(buffer[int(start * sample_rate):int(end * sample_rate)])
        prev_end = end
    trailing = duration - prev_end
    if trailing > max_pause_s:
        pieces.append(np.zeros(keep_samples, dtype=np.float32))
        removed_ranges.append((prev_end + keep_s, duration))
    else:
        pieces.append(buffer[int(prev_end * sample_rate):])
    result = np.concatenate(pieces).astype(np.float32)
    return (result, removed_ranges) if return_ranges else result


def remove_clicks(buffer, sample_rate, threshold=6.0, window_ms=2.0):
    """Detects short amplitude spikes that jump far outside their local
    neighborhood's typical sample-to-sample variation (the signature of a
    click/pop) and replaces a small window around each with a linear
    interpolation across the gap. `threshold` (in local standard
    deviations of the sample-to-sample difference) is set conservatively
    so ordinary speech transients — plosives, sibilants — aren't mistaken
    for clicks; this is a plain spike-detection heuristic, not a learned
    model, so it only ever catches genuinely abrupt discontinuities."""
    if buffer.size < 3:
        return buffer.copy()
    diff = np.diff(buffer.astype(np.float64))
    local_std = np.std(diff) + 1e-9
    spike_idx = np.where(np.abs(diff) > threshold * local_std)[0]
    if spike_idx.size == 0:
        return buffer.copy()
    out = buffer.copy()
    window = max(1, int(sample_rate * window_ms / 1000.0))
    for idx in spike_idx:
        lo = max(0, idx - window)
        hi = min(len(out), idx + window + 1)
        if hi - lo < 2:
            continue
        out[lo:hi] = np.linspace(out[lo], out[hi - 1], hi - lo).astype(np.float32)
    return out


# Auto Enhance's own tuned settings — kept as named constants (not just
# each step's own function default) so the pipeline stays correct and
# self-documenting even if an individual function's default ever
# changes for its own manual-slider reasons. app.py reads these back to
# move each slider to match after Auto Enhance runs, so the panel
# reflects what actually happened rather than sitting at whatever it
# was before.
#
# This went through two more elaborate versions before landing back
# here — one added a noise gate + LUFS loudness (to fix a real clipping
# bug in blind loudness boosting), a follow-up added ML noise reduction
# ahead of that (to stop the loudness boost amplifying noise it hadn't
# removed) — and both, in real listening tests, made recordings sound
# worse than this simpler chain despite fixing the specific bugs they
# targeted. This one can't reintroduce that clipping bug either: peak
# normalize can't clip by construction (it explicitly targets a level
# below 0dBFS), unlike LUFS normalize's unbounded gain-to-target.
AUTO_ENHANCE_HIGH_PASS_HZ = 100.0
AUTO_ENHANCE_COMPRESS_DB = -20.0
AUTO_ENHANCE_COMPRESS_RATIO = 3.0
AUTO_ENHANCE_NORMALIZE_DB = -3.0


def auto_enhance(buffer, sample_rate):
    """One-click tone/loudness pass: cut low-end rumble, even out the
    dynamics, then normalize to a safe peak. Deliberately does NOT
    include noise reduction, noise gate, click removal, or pause-
    shortening — those are either heavier processing that isn't safe to
    apply blind/unsupervised, or one-off repairs at a specific spot
    rather than a rule to apply throughout. All of them are still
    available as their own Enhance rows, and as steps in a saved custom
    configuration (see audio_profiles.py)."""
    cleaned = audio_dsp.high_pass(buffer, sample_rate, AUTO_ENHANCE_HIGH_PASS_HZ)
    cleaned = audio_dsp.compressor(cleaned, sample_rate, threshold_db=AUTO_ENHANCE_COMPRESS_DB,
                                   ratio=AUTO_ENHANCE_COMPRESS_RATIO)
    cleaned = audio_dsp.normalize(cleaned, AUTO_ENHANCE_NORMALIZE_DB)
    return cleaned
