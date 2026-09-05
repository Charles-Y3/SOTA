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


def _pcm24_bytes_to_int16(raw):
    """Downconverts interleaved little-endian signed 24-bit PCM bytes to
    16-bit — lameenc (and MP3 itself) has no 24-bit input path, and the
    precision difference is inaudible once lossy compression runs on top
    anyway. Inverse of audio_record._pack_pcm24's encode, using the same
    8388607.0 scale for a clean round trip."""
    as_bytes = np.frombuffer(raw, dtype=np.uint8)
    n = len(as_bytes) // 3
    triplets = as_bytes[:n * 3].reshape(n, 3)
    sign_byte = np.where(triplets[:, 2] >= 0x80, 0xFF, 0x00).astype(np.uint8)
    padded = np.concatenate([triplets, sign_byte.reshape(-1, 1)], axis=1)
    as_int32 = padded.view("<i4").reshape(-1)
    floats = as_int32.astype(np.float32) / 8388607.0
    return _to_pcm16(floats)


def export_wav_as_mp3(wav_path, mp3_path, bitrate_kbps=192, quality=2):
    """Converts a finished WAV file to MP3 in place, preserving its
    channel count and sample rate — used by Audio Studio's Record tab
    when MP3 is chosen as the recording format. Capture itself always
    writes WAV first (see audio_record.py's own docstring on why: only
    WAV supports the incremental, crash-safe writer), so this only ever
    runs once, on the finished file, right after Stop."""
    import lameenc

    with wave.open(wav_path, "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if sampwidth == 2:
        pcm16_bytes = raw
    elif sampwidth == 3:
        pcm16_bytes = _pcm24_bytes_to_int16(raw).tobytes()
    else:
        raise ValueError(f"Unsupported WAV sample width for MP3 export: {sampwidth}")

    folder = os.path.dirname(mp3_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate_kbps)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(quality)
    data = encoder.encode(pcm16_bytes)
    data += encoder.flush()
    with open(mp3_path, "wb") as f:
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
