"""Simple, dependency-light audio player with variable-speed playback.

Audio is decoded to a mono float32 array (via faster-whisper's PyAV helper,
so no external ffmpeg is needed). Speed changes use WSOLA (Waveform
Similarity Overlap-Add) time-stretching so the pitch stays natural — 1.0x is
bit-exact passthrough.

The player is UI-agnostic: it talks to the GUI only through the optional
`ready_callback` (fired when a speed's audio finishes preparing) so the app
can marshal that back onto the Tk thread.
"""

import threading

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

PLAYBACK_RATE = 22050  # Hz — plenty for reviewing speech, keeps files small.
SPEED_OPTIONS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

_FRAME = 1024                     # ~46ms at 22050Hz
# A larger synthesis hop means fewer (and thus faster) iterations for the
# same audio, at some cost in splice quality. 384 was chosen by measuring
# actual splice-join correlation on real speech across several hop sizes:
# it keeps ~92% of the quality of the smoothest (but ~1.7x slower) setting,
# while both remain dramatically better than naive fixed-hop overlap-add
# (which measured ~0.008 splice correlation — see wsola_test.py).
_SYN_HOP = 384
_SEARCH = 128                    # +/- search range for the best alignment


def time_stretch(x, rate, frame=_FRAME, syn_hop=_SYN_HOP, search=_SEARCH,
                 progress_callback=None):
    """Change tempo by `rate` while preserving pitch, using WSOLA.

    Plain fixed-hop overlap-add (the naive approach) blindly takes evenly
    spaced frames and glues them together; any phase mismatch between
    consecutive frames causes the warbling/garbled "phasiness" that makes
    speech unintelligible at extreme rates. WSOLA fixes this by, at each
    step, searching a small window around the ideal next frame for the
    position whose waveform best matches (via normalized cross-correlation)
    the tail of the previously placed frame, so consecutive frames splice
    together with minimal phase discontinuity.

    progress_callback(fraction: float), if given, is called as the
    stretch progresses (throttled to whole-percent changes).
    """
    x = np.asarray(x, dtype=np.float32)
    n = len(x)
    if n == 0 or abs(rate - 1.0) < 1e-3:
        return x.copy()

    ana_hop = max(1, int(round(syn_hop * rate)))
    window = np.hanning(frame).astype(np.float32)
    overlap = frame - syn_hop

    # Pad so any candidate frame (including the search margin) can always be
    # sliced without bounds-checking every access.
    xp = np.pad(x, (search, frame + search), mode="constant")

    out_len = int(n / rate) + frame + syn_hop
    out = np.zeros(out_len, dtype=np.float32)
    norm = np.zeros(out_len, dtype=np.float32)

    syn_pos = 0
    # `ideal_ana` is a fixed grid that always advances by exactly `ana_hop`
    # per step, independent of what the search below finds. It only ever
    # picks *which frame* to use; it must never determine the next search
    # center, or a run of "the leftmost candidate matches best" (common in
    # silence/pauses) could leave the position stuck with zero net progress.
    ideal_ana = 0
    prev_tail = None  # last `overlap` raw (unwindowed) samples placed
    last_reported_pct = -1

    while ideal_ana < n:
        if progress_callback is not None:
            pct = int(ideal_ana / n * 100)
            if pct != last_reported_pct:
                last_reported_pct = pct
                progress_callback(pct / 100)

        lo = max(0, ideal_ana - search)
        hi = min(n - 1, ideal_ana + search)
        best = min(max(ideal_ana, lo), hi)

        if prev_tail is not None and overlap > 0 and hi > lo:
            width = hi - lo + 1
            base = search + lo  # index into xp corresponding to input pos `lo`
            candidates = sliding_window_view(xp[base:base + width + overlap - 1], overlap)
            dots = candidates @ prev_tail
            cand_norms = np.sqrt((candidates * candidates).sum(axis=1)) + 1e-8
            best = lo + int(np.argmax(dots / cand_norms))

        raw_frame = xp[search + best: search + best + frame]
        end = syn_pos + frame
        if end > len(out):
            grow = end - len(out) + syn_hop * 8
            out = np.concatenate([out, np.zeros(grow, dtype=np.float32)])
            norm = np.concatenate([norm, np.zeros(grow, dtype=np.float32)])
        out[syn_pos:end] += raw_frame * window
        norm[syn_pos:end] += window

        prev_tail = raw_frame[-overlap:] if overlap > 0 else None
        syn_pos += syn_hop
        ideal_ana += ana_hop

    if progress_callback is not None:
        progress_callback(1.0)

    final_len = min(len(out), syn_pos + frame)
    out = out[:final_len]
    norm = norm[:final_len]
    norm[norm < 1e-6] = 1.0
    out = out / norm
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 1.0:
        out = out / peak
    return out.astype(np.float32)


class Player:
    def __init__(self, ready_callback=None, progress_callback=None):
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
        # progress_callback(speed, fraction) fires while a new (uncached)
        # speed is being prepared in the background.
        self.progress_callback = progress_callback

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

    def reload(self, path=None):
        """Re-decodes `path` (defaults to the currently loaded file), for
        when it has grown since load() — a live session's WAV, still being
        appended to on disk. Unlike load(), does not reset playback to the
        start: old audio is never altered for a growing recording, only
        appended to, so the current position and speed are captured first
        and restored afterward. Position is restored in absolute time, not
        as a fraction — the fraction's meaning shifts as total duration
        grows, but the same moment in the recording is still the same
        moment. Always pauses across the swap (a non-1.0x speed needs its
        own background re-stretch — same async path set_speed always
        takes, with the same ready_callback/progress_callback — so there's
        no single instant to resume playback from mid-swap without racing
        it). Returns True on success, False if there's no path to reload.

        Best-effort like every other call in here that touches a live
        session's file while it's still being written: decode_audio reads
        the file at whatever moment this runs, which could in principle
        land mid-write of the WAV header (the writer patches the data-
        length field on every flush) — if that produces a read that fails
        to decode, the exception propagates to the caller, which already
        treats a failed reload as "the previous, shorter audio stays
        usable," not a crash. The next reload attempt starts fresh.
        """
        path = path or self.loaded_path
        if path is None:
            return False
        position_s = self.get_time()
        speed = self._speed
        self.pause()
        if not self.load(path):
            return False
        if self.duration > 0:
            self.seek_fraction(min(1.0, position_s / self.duration))
        if speed != 1.0:
            self.set_speed(speed)
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
        # Every call invalidates any in-flight background stretch from a
        # previous call, even one that resolves synchronously below (e.g.
        # switching back to an already-cached speed) -- otherwise a slow
        # speed-up still computing in the background can finish *after* the
        # user has switched back down and silently overwrite that choice.
        self._prepare_token += 1
        token = self._prepare_token

        cached = self._cache.get(speed)
        if cached is not None:
            self._apply_speed_buffer(speed, cached, frac)
            return

        base = self._base

        def on_progress(fraction):
            if token == self._prepare_token and self.progress_callback:
                self.progress_callback(speed, fraction)

        def work():
            stretched = time_stretch(
                base, speed,
                progress_callback=on_progress if self.progress_callback else None,
            )
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
