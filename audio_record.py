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


def _rms(audio):
    if len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype("float64") ** 2)))


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


class AudioRecorder(threading.Thread):
    """Records raw audio from the microphone to a WAV file: pause/resume
    mid-session, an incremental level meter, and auto-save-as-you-go (the
    WAV header is patched on every flush, so the file stays playable even
    if the process dies mid-recording). Call start() to begin, stop() to
    finish — ("record_stopped", path, seconds) is emitted once the file is
    closed; ("record_started", path) fires as soon as it's created."""

    TICK_S = 0.15
    PEAK_WINDOW_S = 0.05  # one waveform column per 50ms of audio

    def __init__(self, events, device_name="", sample_rate=DEFAULT_SAMPLE_RATE,
                channels=1, custom_stem=None):
        super().__init__(daemon=True)
        self.events = events
        self.device_name = device_name or ""
        self.sample_rate = sample_rate
        self.channels = max(1, min(2, channels))
        self.custom_stem = custom_stem or None
        self.stop_event = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()
        self._pending_disk = []
        self._recorded_samples = 0
        self.level = 0.0
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
    def elapsed_seconds(self):
        with self._lock:
            samples = self._recorded_samples
        return samples / self.sample_rate if self.sample_rate else 0.0

    def _emit(self, *event):
        self.events.put(event)

    def _on_audio(self, indata, _frames, _time_info, _status):
        if self._paused.is_set():
            return
        chunk = indata.copy()
        mono = chunk[:, 0]
        with self._lock:
            self._pending_disk.append(chunk)
            self._recorded_samples += len(chunk)
            self._accumulate_peaks(mono)
        self.level = _rms(mono)

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
            wav.setsampwidth(2)
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
