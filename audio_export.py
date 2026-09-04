"""Audio file export for Audio Studio: WAV (stdlib `wave`, no extra
dependency) and MP3 (via lameenc, a pure-Python-installable binding
around the LAME encoder — avoids bundling/shelling out to an ffmpeg
binary, keeping the PyInstaller build unchanged)."""

import os
import wave

import numpy as np


def _to_pcm16(buffer):
    return (np.clip(buffer, -1.0, 1.0) * 32767.0).astype(np.int16)


def export_wav(buffer, sample_rate, path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    pcm16 = _to_pcm16(buffer)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def export_mp3(buffer, sample_rate, path, bitrate_kbps=192, quality=2):
    import lameenc

    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate_kbps)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(quality)  # 2 = highest quality, 7 = fastest
    pcm16 = _to_pcm16(buffer)
    data = encoder.encode(pcm16.tobytes())
    data += encoder.flush()
    with open(path, "wb") as f:
        f.write(data)


def export_audio(buffer, sample_rate, path, fmt=None):
    """fmt: "wav" or "mp3"; inferred from `path`'s extension if omitted."""
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".") or "wav").lower()
    if fmt == "mp3":
        export_mp3(buffer, sample_rate, path)
    elif fmt == "wav":
        export_wav(buffer, sample_rate, path)
    else:
        raise ValueError(f"Unsupported export format: {fmt!r}")
