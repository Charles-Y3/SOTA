"""Spectrogram rendering for Audio Studio's Edit subtab — a frequency-
content view alongside the (amplitude-only) waveform, the way Audacity/
Audition/Ocenaudio all offer one, useful for finding exactly where a hum,
hiss, or specific syllable actually sits when the waveform alone can't
show it.
"""

import numpy as np
from PIL import Image
from scipy.signal import stft

# Hand-rolled "heat" colormap (dark -> blue -> magenta -> orange -> pale
# yellow) via a handful of control points and linear interpolation —
# avoids adding matplotlib as a dependency just for one colormap.
_LUT_STOPS = np.array([
    [0, 0, 0],
    [30, 20, 90],
    [110, 40, 150],
    [200, 80, 90],
    [250, 180, 40],
    [255, 255, 220],
], dtype=np.float32)


def _build_lut():
    n = 256
    stops = np.linspace(0, n - 1, len(_LUT_STOPS))
    lut = np.zeros((n, 3), dtype=np.uint8)
    for ch in range(3):
        lut[:, ch] = np.interp(np.arange(n), stops, _LUT_STOPS[:, ch]).astype(np.uint8)
    return lut


_LUT = _build_lut()
DB_FLOOR = -80.0  # anything this far below the loudest bin renders as black


def compute_image(buffer, sample_rate, start_s, end_s, width_px, height_px,
                  nperseg=1024, noverlap_ratio=0.75):
    """Renders a color spectrogram of buffer[start_s:end_s] as a
    width_px x height_px PIL.Image. Bounded compute time regardless of
    the clip's total length: only the visible zoom window is sliced out
    before running the STFT, the same principle
    audio_clip.peaks_from_buffer already uses for the waveform."""
    i0 = max(0, min(len(buffer), int(round(start_s * sample_rate))))
    i1 = max(0, min(len(buffer), int(round(end_s * sample_rate))))
    width_px, height_px = max(1, int(width_px)), max(1, int(height_px))
    region = buffer[i0:i1]
    if region.size < nperseg:
        return Image.new("RGB", (width_px, height_px), (0, 0, 0))
    noverlap = int(nperseg * noverlap_ratio)
    _freqs, _times, zxx = stft(region, fs=sample_rate, nperseg=nperseg, noverlap=noverlap)
    magnitude = np.abs(zxx)
    db = 20 * np.log10(np.maximum(magnitude, 1e-6))
    db = np.clip(db, DB_FLOOR, 0)
    normalized = ((db - DB_FLOOR) / -DB_FLOOR * 255).astype(np.uint8)
    # STFT's row 0 is 0Hz; a spectrogram image conventionally reads
    # top=high frequency, bottom=low, so flip vertically.
    normalized = normalized[::-1, :]
    rgb = _LUT[normalized]  # (freq_bins, time_bins, 3)
    return Image.fromarray(rgb, mode="RGB").resize((width_px, height_px), Image.BILINEAR)
