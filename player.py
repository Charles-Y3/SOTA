"""Simple, dependency-light audio player with variable-speed playback.

Audio is decoded to a mono float32 array (via faster-whisper's PyAV helper,
so no external ffmpeg is needed). Speed changes use an overlap-add time
stretch so the pitch stays natural — 1.0x is bit-exact passthrough.

The player is UI-agnostic: it talks to the GUI only through the optional
`ready_callback` (fired when a speed's audio finishes preparing) so the app
can marshal that back onto the Tk thread.
"""

import threading

import numpy as np

PLAYBACK_RATE = 22050  # Hz — plenty for reviewing speech, keeps files small.
SPEED_OPTIONS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


def time_stretch(x, rate):
    """Change tempo by `rate` while preserving pitch (overlap-add)."""
    if abs(rate - 1.0) < 1e-3 or x.size == 0:
        return x.astype(np.float32, copy=True)

    frame = 1024
    syn_hop = frame // 4                       # 256
    ana_hop = max(1, int(round(syn_hop * rate)))
    win = np.hanning(frame).astype(np.float32)

    n_frames = 1 + max(0, (len(x) - frame) // ana_hop)
    out_len = frame + syn_hop * (n_frames - 1)
    out = np.zeros(out_len, dtype=np.float32)
    norm = np.zeros(out_len, dtype=np.float32)

    for i in range(n_frames):
        a = i * ana_hop
        seg = x[a:a + frame]
        if len(seg) < frame:
            seg = np.pad(seg, (0, frame - len(seg)))
        s = i * syn_hop
        out[s:s + frame] += seg * win
        norm[s:s + frame] += win

    norm[norm < 1e-6] = 1.0
    out /= norm
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 1.0:
        out /= peak
    return out


class Player:
    def __init__(self, ready_callback=None):
        self._sd = None  # sounddevice, imported lazily
        self._base = np.zeros(0, dtype=np.float32)
        self.duration = 0.0
        self.loaded_path = None

        self._lock = threading.Lock()
        self._buf = np.zeros(0, dtype=np.float32)  # stretched for current speed
        self._pos = 0                               # sample index into _buf
        self._speed = 1.0
        self._cache = {1.0: None}
        self._stream = None
        self._prepare_token = 0
        self.ready_callback = ready_callback

    # -- loading ------------------------------------------------------------

    def load(self, path):
        """Decode `path`. Returns True on success, False on failure."""
        from faster_whisper.audio import decode_audio  # reuse bundled PyAV

        self.stop()
        samples = decode_audio(path, sampling_rate=PLAYBACK_RATE)
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        with self._lock:
            self._base = samples
            self.duration = len(samples) / PLAYBACK_RATE
            self.loaded_path = path
            self._cache = {1.0: samples}
            self._speed = 1.0
            self._buf = samples
            self._pos = 0
        return True

    # -- transport ----------------------------------------------------------

    def _ensure_sd(self):
        if self._sd is None:
            import sounddevice as sd
            self._sd = sd
        return self._sd

    def play(self):
        if self._base.size == 0:
            return
        sd = self._ensure_sd()
        with self._lock:
            if self._pos >= len(self._buf):
                self._pos = 0
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=PLAYBACK_RATE,
                channels=1,
                dtype="float32",
                callback=self._callback,
                finished_callback=self._on_stream_finished,
            )
            self._stream.start()

    def _callback(self, outdata, frames, time_info, status):
        with self._lock:
            buf, pos = self._buf, self._pos
            end = min(pos + frames, len(buf))
            n = end - pos
            if n > 0:
                outdata[:n, 0] = buf[pos:end]
                self._pos = end
            if n < frames:
                outdata[n:, 0] = 0.0
                raise self._sd.CallbackStop()

    def _on_stream_finished(self):
        # Runs on the audio thread after CallbackStop/stop; drop the stream.
        self._stream = None

    def pause(self):
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def stop(self):
        self.pause()
        with self._lock:
            self._pos = 0

    @property
    def is_playing(self):
        return self._stream is not None and self._stream.active

    # -- position -----------------------------------------------------------

    def get_fraction(self):
        with self._lock:
            if len(self._buf) == 0:
                return 0.0
            return min(1.0, self._pos / len(self._buf))

    def get_time(self):
        return self.get_fraction() * self.duration

    def seek_fraction(self, frac):
        frac = max(0.0, min(1.0, frac))
        with self._lock:
            self._pos = int(frac * len(self._buf))

    # -- speed --------------------------------------------------------------

    def set_speed(self, speed):
        """Switch playback speed, preserving the current position. Heavy
        stretching runs on a worker thread; `ready_callback` fires when done."""
        if self._base.size == 0:
            self._speed = speed
            return
        frac = self.get_fraction()
        cached = self._cache.get(speed)
        if cached is not None:
            self._apply_speed_buffer(speed, cached, frac)
            return

        self._prepare_token += 1
        token = self._prepare_token
        base = self._base

        def work():
            stretched = time_stretch(base, speed)
            if token != self._prepare_token:
                return  # a newer speed change superseded this one
            self._cache[speed] = stretched
            self._apply_speed_buffer(speed, stretched, frac)
            if self.ready_callback:
                self.ready_callback()

        threading.Thread(target=work, daemon=True).start()

    def _apply_speed_buffer(self, speed, buf, frac):
        with self._lock:
            self._speed = speed
            self._buf = buf
            self._pos = int(frac * len(buf))
