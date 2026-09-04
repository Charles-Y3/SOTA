"""DSP effects for Audio Studio's Enhance subtab.

Every function takes a mono float32 numpy buffer (plus sample_rate where
the effect is frequency-dependent) and returns a new buffer — cheap
enough (aside from noise reduction, in audio_denoise.py) to re-run on a
short selection for live preview as a slider moves; the caller decides
preview-vs-apply, this module just computes.
"""

import numpy as np
from scipy import signal


def amplify(buffer, gain_db):
    gain = 10 ** (gain_db / 20.0)
    return np.clip(buffer * gain, -1.0, 1.0).astype(np.float32)


def normalize(buffer, target_db=-3.0):
    """Scales so the loudest sample sits at target_db (default -3dB,
    leaving a little headroom rather than hitting 0dB exactly). This is
    peak normalization — it controls headroom, not perceived loudness;
    see normalize_lufs below for an actual loudness-standard target."""
    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak < 1e-6:
        return buffer.copy()
    target = 10 ** (target_db / 20.0)
    return np.clip(buffer * (target / peak), -1.0, 1.0).astype(np.float32)


# ITU-R BS.1770 needs at least a 400ms gated block to measure; anything
# shorter can't be measured at all (pyloudnorm raises), so there's no
# meaningful loudness target for a selection that short.
LUFS_MIN_SAMPLES_S = 0.4

# A recording quiet/noisy enough to sit tens of dB under the target
# (found on a real one: -44 LUFS in, -16 LUFS target, needing +28dB) was
# getting boosted by however much the target demanded with no ceiling —
# amplifying its noise floor right along with it, and hard-clipping in
# the process (pyloudnorm's own "possible clipped samples" warning, and
# genuinely audible distortion — worse than the untouched original).
# Capping the boost keeps this from trying to rescue a badly-off
# recording by wrecking it instead.
MAX_LUFS_BOOST_DB = 12.0


def normalize_lufs(buffer, sample_rate, target_lufs=-16.0):
    """Loudness normalization to a target in LUFS (ITU-R BS.1770,
    integrated/gated loudness via pyloudnorm) — the standard real editors
    (Audition, Auphonic, Descript) use for "make this sound like a normal
    podcast/stream," unlike peak normalize() above which only controls
    headroom. Falls back to peak normalization for a selection too short
    to gate-measure, and leaves true silence untouched (its loudness is
    -inf; there's nothing to scale it to).

    The applied gain is capped two ways: never more than
    MAX_LUFS_BOOST_DB regardless of how far below target the recording
    is, and never enough to actually clip a sample — computed directly
    against this buffer's own peak rather than trusting pyloudnorm's own
    normalize() (which has neither guard) to not blow past 0dBFS."""
    if buffer.size < int(LUFS_MIN_SAMPLES_S * sample_rate):
        return normalize(buffer, -3.0)
    import pyloudnorm as pyln

    data = buffer.astype(np.float64)
    try:
        loudness = pyln.Meter(sample_rate).integrated_loudness(data)
    except Exception:
        return normalize(buffer, -3.0)
    if not np.isfinite(loudness):
        return buffer.copy()
    gain_db = min(target_lufs - loudness, MAX_LUFS_BOOST_DB)
    gain = 10 ** (gain_db / 20.0)
    peak = float(np.max(np.abs(data)))
    if peak > 1e-9:
        gain = min(gain, 0.999 / peak)  # a hard ceiling beats letting np.clip below turn it into distortion
    return np.clip(data * gain, -1.0, 1.0).astype(np.float32)


def _butter_filter(buffer, sample_rate, cutoff_hz, btype, order=4):
    if buffer.size == 0:
        return buffer.copy()
    nyquist = sample_rate / 2.0
    normal_cutoff = max(1e-4, min(0.999, cutoff_hz / nyquist))
    b, a = signal.butter(order, normal_cutoff, btype=btype)
    # filtfilt (zero-phase, forward+backward) rather than lfilter: a
    # single-pass IIR filter shifts transients in time, which is audible
    # as smearing on speech; filtfilt cancels that phase shift at the
    # cost of processing the whole buffer twice.
    return signal.filtfilt(b, a, buffer).astype(np.float32)


def high_pass(buffer, sample_rate, cutoff_hz=100.0):
    """Cuts low-frequency rumble (mic handling noise, HVAC hum) below
    cutoff_hz."""
    return _butter_filter(buffer, sample_rate, cutoff_hz, "highpass")


def low_pass(buffer, sample_rate, cutoff_hz=8000.0):
    """Cuts hiss/high-frequency noise above cutoff_hz."""
    return _butter_filter(buffer, sample_rate, cutoff_hz, "lowpass")


def eq_band(buffer, sample_rate, center_hz, gain_db, q=1.0):
    """A single peaking-EQ band (RBJ Audio EQ Cookbook biquad) — boosts or
    cuts a range of frequencies around center_hz by gain_db. Several calls
    with different centers chain together into a simple multi-band EQ."""
    if buffer.size == 0 or abs(gain_db) < 1e-3:
        return buffer.copy()
    a_coeff = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * center_hz / sample_rate
    alpha = np.sin(w0) / (2 * q)
    cos_w0 = np.cos(w0)
    b0 = 1 + alpha * a_coeff
    b1 = -2 * cos_w0
    b2 = 1 - alpha * a_coeff
    a0 = 1 + alpha / a_coeff
    a1 = -2 * cos_w0
    a2 = 1 - alpha / a_coeff
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1 / a0, a2 / a0])
    return signal.filtfilt(b, a, buffer).astype(np.float32)


def compressor(buffer, sample_rate, threshold_db=-20.0, ratio=4.0,
               attack_ms=5.0, release_ms=80.0, makeup_db=0.0):
    """A simple feed-forward compressor: reduces gain once the signal
    envelope crosses threshold_db, by `ratio`. Not broadcast-grade (attack
    and release are combined into a single envelope time constant rather
    than modeled separately, to keep the envelope follower vectorizable —
    a true per-sample attack/release loop doesn't scale to multi-minute
    clips in pure Python) but enough to tame dynamic range on a voice
    recording."""
    if buffer.size == 0:
        return buffer.copy()
    threshold = 10 ** (threshold_db / 20.0)
    time_constant_ms = (attack_ms + release_ms) / 2.0
    coeff = np.exp(-1.0 / (sample_rate * time_constant_ms / 1000.0))
    rectified = np.abs(buffer).astype(np.float64)
    envelope = signal.lfilter([1 - coeff], [1, -coeff], rectified)
    envelope = np.maximum(envelope, 1e-9)
    gain = np.ones_like(envelope)
    over = envelope > threshold
    gain[over] = (threshold + (envelope[over] - threshold) / ratio) / envelope[over]
    makeup = 10 ** (makeup_db / 20.0)
    return np.clip(buffer * gain * makeup, -1.0, 1.0).astype(np.float32)


def noise_gate(buffer, sample_rate, threshold_db=-40.0, window_ms=20.0, fade_ms=8.0):
    """Attenuates fixed windows whose RMS falls below threshold_db — good
    for hiss/hum between speech, not for noise present *under* speech
    (that needs the ML noise-reduction model in audio_denoise.py).

    Each window's on/off gate is smoothed across fade_ms rather than
    snapped straight to 0 — a hard per-window cutoff is itself an abrupt
    discontinuity, audible as a click at every single gate transition.
    Automatic cleanup runs click removal *before* this, so those new
    clicks went straight through unnoticed, making a recording run
    through "Automatic cleanup" sound choppier than the original despite
    each individual step being correct in isolation (found via a real
    recording that came out worse, not better)."""
    if buffer.size == 0:
        return buffer.copy()
    window = max(1, int(sample_rate * window_ms / 1000.0))
    threshold = 10 ** (threshold_db / 20.0)
    n_windows = int(np.ceil(len(buffer) / window))
    gate = np.ones(n_windows, dtype=np.float64)
    for i in range(n_windows):
        chunk = buffer[i * window:(i + 1) * window]
        rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))) if chunk.size else 0.0
        if rms < threshold:
            gate[i] = 0.0
    fade_windows = max(1, int(round(fade_ms / window_ms)))
    if n_windows > 1:
        kernel = np.ones(fade_windows * 2 + 1) / (fade_windows * 2 + 1)
        gate = np.convolve(gate, kernel, mode="same")
    gain = np.repeat(gate, window)[:len(buffer)].astype(np.float32)
    return buffer * gain
