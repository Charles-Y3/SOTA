"""Standalone audio-only recorder for Audio Studio's Record subtab.

Deliberately independent of live_transcription.LiveTranscriber — Record
captures audio only (no transcription running alongside it), and per an
explicit product decision the two recorders share no code, so neither can
regress the other by way of a "shared" refactor. The approach below
(device enumeration by name, chunk-list buffering rather than growing one
numpy array on every mic callback, incremental WAV writes so the header
patches itself as it goes) mirrors LiveTranscriber's reasoning, but is a
fresh, self-contained implementation, not a shared one.
"""

import datetime
import os
import threading

import numpy as np

import settings
import transcriber  # only for unique_path — no transcription code used

DEFAULT_SAMPLE_RATE = 44100
SAMPLE_RATE_OPTIONS = [16000, 22050, 44100, 48000]
CHANNEL_OPTIONS = [1, 2]
BIT_DEPTH_OPTIONS = [16, 24]
DEFAULT_BIT_DEPTH = 16
CLIP_THRESHOLD = 0.98
MIC_TEST_SECONDS = 5.0


def _rms(audio):
    if len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype("float64") ** 2)))


def extract_wav_segment(src_path, start_s, end_s, dest_path):
    """Copies [start_s, end_s) of src_path's audio into a brand-new WAV
    file at dest_path, same channels/sample width/rate — a raw frame-
    range copy, no decode or resample involved (unlike audio_clip's own
    load path, which always decodes to mono float32 at CLIP_SAMPLE_RATE).

    Works directly on a WAV a recorder thread is STILL actively
    appending to (Partial Save, in app.py) — safe because it only ever
    reads up to `end_s`, and by construction that's always audio from
    strictly before "now" (both endpoints come from markers already
    dropped in the past), so it's already been through a flush by the
    time this runs; a plain read-mode file handle doesn't conflict with
    the writer thread's separate handle. `end_s` is clamped to whatever
    the file's own header currently declares — itself patched on every
    flush (see AudioRecorder's class docstring) — so this can never try
    to read past what's actually been written."""
    import wave

    with wave.open(src_path, "rb") as src:
        framerate = src.getframerate()
        channels = src.getnchannels()
        sampwidth = src.getsampwidth()
        total_frames = src.getnframes()
        start_frame = max(0, min(total_frames, int(round(start_s * framerate))))
        end_frame = max(0, min(total_frames, int(round(end_s * framerate))))
        if end_frame <= start_frame:
            raise ValueError("Empty or out-of-range segment")
        src.setpos(start_frame)
        frames = src.readframes(end_frame - start_frame)

    folder = os.path.dirname(dest_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with wave.open(dest_path, "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(sampwidth)
        dst.setframerate(framerate)
        dst.writeframes(frames)


def _pack_pcm24(audio):
    """Packs a float32 array (range ~[-1, 1]) as little-endian signed
    24-bit PCM bytes. numpy has no native int24 dtype, so scale into the
    int32 range, then drop the (always-zero after this scaling) top byte
    of each little-endian int32 word."""
    clipped = np.clip(audio, -1.0, 1.0)
    as_int32 = (clipped * 8388607.0).astype("<i4")
    as_bytes = as_int32.tobytes()
    # Each int32 word is 4 bytes little-endian; keep the low 3, drop the top.
    view = np.frombuffer(as_bytes, dtype=np.uint8).reshape(-1, 4)
    return view[:, :3].tobytes()


def list_input_devices():
    """Names of the machine's microphone-capable devices. Restricted to
    the default input device's host API — on Windows, PortAudio otherwise
    reports every physical mic three or four times over (MME, DirectSound,
    WASAPI, WDM-KS variants of the same hardware)."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception:
        return []
    hostapi = None
    try:
        hostapi = sd.query_devices(kind="input")["hostapi"]
    except Exception:
        pass
    names, seen = [], set()
    for dev in devices:
        try:
            if dev["max_input_channels"] <= 0:
                continue
            if hostapi is not None and dev["hostapi"] != hostapi:
                continue
            name = dev["name"]
        except Exception:
            continue
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _resolve_device(device_name):
    """Maps a stored device name back to a PortAudio index, or None
    (system default) if empty or no longer present."""
    if not device_name:
        return None
    try:
        import sounddevice as sd

        for index, dev in enumerate(sd.query_devices()):
            if dev["name"] == device_name and dev["max_input_channels"] > 0:
                return index
    except Exception:
        pass
    return None


def resolved_device_label(device_name):
    """Human-readable name of the mic that will actually be used —
    `device_name` itself when it still exists, otherwise the system's
    current default input device (covers both the empty "use default"
    selection and a previously-selected device that's since disappeared)."""
    if device_name and _resolve_device(device_name) is not None:
        return device_name
    try:
        import sounddevice as sd

        return sd.query_devices(kind="input")["name"]
    except Exception:
        return device_name or ""


class AudioRecorder(threading.Thread):
    """Records raw audio from the microphone to a WAV file: pause/resume
    mid-session, an incremental level meter, and auto-save-as-you-go (the
    WAV header is patched on every flush, so the file stays playable even
    if the process dies mid-recording). Call start() to begin, stop() to
    finish — ("record_stopped", path, seconds) is emitted once the file is
    closed; ("record_started", path) fires as soon as it's created."""

    TICK_S = 0.15
    PEAK_WINDOW_S = 0.05  # one waveform column per 50ms of audio
    RECENT_AUDIO_S = 30.0  # how far back the fine-grained waveform buffer reaches — see recent_snapshot()

    def __init__(self, events, device_name="", sample_rate=DEFAULT_SAMPLE_RATE,
                channels=1, custom_stem=None, gain=1.0, bit_depth=DEFAULT_BIT_DEPTH):
        super().__init__(daemon=True)
        self.events = events
        self.device_name = device_name or ""
        self.sample_rate = sample_rate
        self.channels = max(1, min(2, channels))
        self.custom_stem = custom_stem or None
        self.gain = gain
        self.bit_depth = bit_depth if bit_depth in BIT_DEPTH_OPTIONS else DEFAULT_BIT_DEPTH
        self.stop_event = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()
        self._pending_disk = []
        self._recorded_samples = 0
        self.level = 0.0
        self._clipped = False
        self._wav = None
        self._audio_path = None
        self._stream = None
        # Live waveform for the Record subtab: one (min, max) pair per
        # PEAK_WINDOW_S of audio, appended as it arrives — cheap enough to
        # keep for an arbitrarily long recording (a few hundred bytes per
        # minute), unlike keeping the raw samples themselves in memory.
        self._peak_mins = []
        self._peak_maxes = []
        self._peak_remainder = np.zeros(0, dtype=np.float32)
        self._peak_window_samples = max(1, int(sample_rate * self.PEAK_WINDOW_S))
        # A SEPARATE, deliberately bounded rolling buffer of raw samples
        # covering only the last RECENT_AUDIO_S seconds — the peak cache
        # above is coarse (50ms buckets) by design to stay cheap over an
        # arbitrarily long recording, but that coarseness is exactly what
        # makes the live waveform look blocky when the visible window is
        # short (early in a recording, or zoomed in). This buffer trades a
        # small, capped amount of memory (~5MB at 44.1kHz float32 for 30s)
        # for pixel-accurate rendering of whatever's recent, the same way
        # _pending_disk buffers chunks for the WAV writer — a list of
        # chunks, trimmed from the front once the total exceeds the cap,
        # not a from-scratch ring-buffer implementation.
        self._recent_chunks = []
        self._recent_len = 0
        self._recent_cap_samples = max(1, int(sample_rate * self.RECENT_AUDIO_S))

    def stop(self):
        self.stop_event.set()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    @property
    def is_paused(self):
        return self._paused.is_set()

    @property
    def clipped(self):
        """True if any sample has hit CLIP_THRESHOLD since the last
        reset_clip_flag() call (sticky, so a brief overload isn't missed
        between UI ticks)."""
        with self._lock:
            return self._clipped

    def reset_clip_flag(self):
        with self._lock:
            self._clipped = False

    @property
    def elapsed_seconds(self):
        with self._lock:
            samples = self._recorded_samples
        return samples / self.sample_rate if self.sample_rate else 0.0

    @property
    def audio_path(self):
        """The WAV file this recorder is writing to, or None before
        _open_wav has run. Assigned once early in run() (before
        "record_started" is even emitted) and never reassigned after, so
        reading it from the UI thread needs no lock — used by Partial
        Save to read back already-flushed audio while recording keeps
        going, without touching this recorder object at all."""
        return self._audio_path

    def _emit(self, *event):
        self.events.put(event)

    def _on_audio(self, indata, _frames, _time_info, _status):
        if self._paused.is_set():
            return
        chunk = indata.copy()
        if self.gain != 1.0:
            chunk *= self.gain
        mono = chunk[:, 0]
        is_clipped = bool(np.any(np.abs(mono) >= CLIP_THRESHOLD))
        with self._lock:
            self._pending_disk.append(chunk)
            self._recorded_samples += len(chunk)
            self._accumulate_peaks(mono)
            self._accumulate_recent(mono)
            if is_clipped:
                self._clipped = True
        self.level = _rms(mono)

    def _accumulate_recent(self, mono_chunk):
        """Called under self._lock, right alongside _accumulate_peaks —
        same input, different purpose (see RECENT_AUDIO_S's comment in
        __init__). Appends the new chunk, then drops whole chunks off the
        front until the total is back under the cap; dropping by whole
        chunks (not trimming mid-chunk) keeps this O(1) amortized instead
        of slicing a numpy array on every callback."""
        self._recent_chunks.append(mono_chunk.copy())
        self._recent_len += len(mono_chunk)
        while len(self._recent_chunks) > 1 and self._recent_len - len(self._recent_chunks[0]) >= self._recent_cap_samples:
            self._recent_len -= len(self._recent_chunks.pop(0))

    def _accumulate_peaks(self, mono_chunk):
        """Called under self._lock. Folds `mono_chunk` into fixed-size
        peak windows, carrying over any partial window (`_peak_remainder`)
        to the next call so window boundaries don't depend on how the
        audio backend happens to size its callbacks."""
        data = (np.concatenate([self._peak_remainder, mono_chunk])
                if self._peak_remainder.size else mono_chunk)
        window = self._peak_window_samples
        n_full = len(data) // window
        for i in range(n_full):
            piece = data[i * window:(i + 1) * window]
            self._peak_mins.append(float(piece.min()))
            self._peak_maxes.append(float(piece.max()))
        self._peak_remainder = data[n_full * window:].copy()

    def peaks_snapshot(self):
        """Returns (mins, maxes) — plain lists, safe to read from the UI
        thread without touching the lock's internals further."""
        with self._lock:
            return list(self._peak_mins), list(self._peak_maxes)

    def recent_snapshot(self):
        """Returns (buffer, start_offset_s): a concatenated float32 array
        of up to the last RECENT_AUDIO_S seconds of raw mono samples, and
        the absolute recording-timeline offset (seconds since the
        recording started) its first sample corresponds to — a caller
        comparing a visible (start_s, end_s) window against this offset
        can tell whether that window falls entirely within the fine-
        grained buffer, and if so, compute pixel-accurate peaks directly
        from it (e.g. via audio_clip.peaks_from_buffer) instead of the
        coarser PEAK_WINDOW_S cache peaks_snapshot() returns."""
        with self._lock:
            chunks = list(self._recent_chunks)
            recent_len = self._recent_len
            total_samples = self._recorded_samples
        buffer = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        start_offset_s = (total_samples - recent_len) / self.sample_rate if self.sample_rate else 0.0
        return buffer, start_offset_s

    def _open_wav(self):
        import wave

        if self.custom_stem:
            stem = self.custom_stem
        else:
            # Fullwidth colon, same reasoning as the Live tab's auto name:
            # a literal ":" silently truncates a Windows filename (NTFS
            # alternate-data-stream syntax) rather than erroring.
            now = datetime.datetime.now()
            stem = "Recording " + now.strftime("%Y-%m-%d %H") + "：" + now.strftime("%M")
        try:
            folder = settings.audio_recordings_folder()
            os.makedirs(folder, exist_ok=True)
            path = transcriber.unique_path(os.path.join(folder, stem + ".wav"))
            wav = wave.open(path, "wb")
            wav.setnchannels(self.channels)
            wav.setsampwidth(3 if self.bit_depth == 24 else 2)
            wav.setframerate(self.sample_rate)
            self._wav, self._audio_path = wav, path
        except Exception:
            settings.log_exception("Could not create the Audio Studio recording file:")
            self._wav, self._audio_path = None, None

    def _flush_audio(self):
        with self._lock:
            chunks, self._pending_disk = self._pending_disk, []
        if not chunks or self._wav is None:
            return
        try:
            audio = np.concatenate(chunks)
            if self.bit_depth == 24:
                self._wav.writeframes(_pack_pcm24(audio))
            else:
                pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
                self._wav.writeframes(pcm16.tobytes())
        except Exception:
            settings.log_exception("Audio Studio recording write failed:")

    def _close_wav(self):
        wav, self._wav = self._wav, None
        if wav is not None:
            try:
                wav.close()
            except Exception:
                settings.log_exception("Audio Studio recording close failed:")

    def run(self):
        import sounddevice as sd

        self._open_wav()
        self._emit("record_started", self._audio_path)
        try:
            device = _resolve_device(self.device_name)
            self._stream = sd.InputStream(
                samplerate=self.sample_rate, channels=self.channels,
                dtype="float32", device=device, callback=self._on_audio)
            self._stream.start()
            while not self.stop_event.is_set():
                self.stop_event.wait(self.TICK_S)
                self._flush_audio()
        except Exception:
            settings.log_exception("Audio Studio recording failed:")
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
            self._flush_audio()
            seconds = self.elapsed_seconds
            self._close_wav()
            self._emit("record_stopped", self._audio_path, seconds)


class MicTester(threading.Thread):
    """A short, disk-free "Test Mic" run: opens the same input stream an
    AudioRecorder would, exposes the same level/clipped readout, and
    auto-stops itself after MIC_TEST_SECONDS — nothing is written to disk.
    Deliberately not a subclass of AudioRecorder (no WAV writer, no
    pause/resume, no pending-disk buffer to manage) so it can't drag any
    of that unrelated bookkeeping in by accident."""

    def __init__(self, device_name="", sample_rate=DEFAULT_SAMPLE_RATE,
                channels=1, gain=1.0, duration=MIC_TEST_SECONDS):
        super().__init__(daemon=True)
        self.device_name = device_name or ""
        self.sample_rate = sample_rate
        self.channels = max(1, min(2, channels))
        self.gain = gain
        self.duration = duration
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self.level = 0.0
        self._clipped = False
        self._stream = None

    def stop(self):
        self.stop_event.set()

    @property
    def clipped(self):
        with self._lock:
            return self._clipped

    def _on_audio(self, indata, _frames, _time_info, _status):
        mono = indata[:, 0] * self.gain
        if np.any(np.abs(mono) >= CLIP_THRESHOLD):
            with self._lock:
                self._clipped = True
        self.level = _rms(mono)

    def run(self):
        import sounddevice as sd

        try:
            device = _resolve_device(self.device_name)
            self._stream = sd.InputStream(
                samplerate=self.sample_rate, channels=self.channels,
                dtype="float32", device=device, callback=self._on_audio)
            self._stream.start()
            self.stop_event.wait(self.duration)
        except Exception:
            settings.log_exception("Audio Studio mic test failed:")
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
