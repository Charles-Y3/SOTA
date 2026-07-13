"""Live microphone dictation via SenseVoice.

Ports the commit-on-pause design from LOMA1's services/voice_input.py to a
native mic input (sounddevice) instead of browser-recorded WebM blobs: only
the tail of audio since the last commit is ever re-decoded, so per-tick cost
stays bounded no matter how long the recording runs — a naive whole-buffer
re-decode gets slower every tick as the session grows. SenseVoice has no
streaming mode and no per-word timestamps (unlike whisper), so "has speech
paused" is decided with a plain trailing-RMS silence check instead of
slicing on a word boundary — backed by a hard time cap (MAX_UNCOMMITTED_S)
that commits regardless of the silence check, since a real mic's noise
floor can sit above that threshold indefinitely and never trigger it.

That cap is purely about bounding how much uncommitted audio can pile up —
it does NOT mean every commit becomes its own paragraph. A cap-forced
commit happens mid-flow, with no pause having actually occurred, so it's
glued onto the current paragraph instead of starting a new one; only a
commit that genuinely ends on silence starts fresh. See _commit_text() and
_paragraph_pending for the mechanics. This keeps a long uninterrupted
sentence reading as one paragraph on screen (and in the saved transcript)
even though it's still being committed — and its tail cleared — every
MAX_UNCOMMITTED_S seconds underneath.

The raw recording is streamed to its WAV file on disk as the session runs
(the worker flushes captured chunks every tick), not accumulated in RAM and
written once at the end. That keeps memory flat no matter how long the
session goes — the previous keep-it-all-in-RAM design needed a low-memory
watchdog, MemoryError handling around one giant final concatenation, and
still lost the entire recording on a crash or power loss. Now the only
audio held in RAM is the uncommitted tail (bounded by MAX_UNCOMMITTED_S)
plus at most one tick's worth of not-yet-flushed chunks, and a crash leaves
a playable WAV of everything captured so far (wave.writeframes patches the
header on every flush).

The Live Transcription tab only supports SenseVoice's 5 languages
(transcriber.SENSEVOICE_LANGUAGES) — there is no whisper fallback here, by
design (see app.py's Live tab hint text).

This module is UI-agnostic: it talks to the app only through the `events`
queue passed into LiveTranscriber, the same convention transcriber.py and
llm.py already use:

    ("live_status", key, detail)              -> status line
    ("live_text", committed, preview, lang)   -> full committed text + live tail
    ("live_saved", audio_path, txt_path)      -> auto-save landed (either may
                                                  be None if that half failed)
    ("live_stopped",)                         -> recording has fully wound down

The raw recording (WAV) and its transcript (.docx/.txt) are saved to
different folders — settings.live_recordings_folder() and
settings.transcriptions_folder() respectively — so every transcript, whether
from a recorded file or a live session, lives in one place, while the
Live tab's actual audio recordings get their own.
"""

import datetime
import os
import threading
import wave

import numpy as np

import docx_export
import settings
import timestamps
import transcriber

SAMPLE_RATE = transcriber.SENSEVOICE_SAMPLE_RATE
STEP_S = 1.0            # how often the worker re-decodes the uncommitted tail
TRAIL_S = 1.0           # trailing window checked for "has speech paused"
SILENCE_RMS = 0.012     # same threshold LOMA1 tuned for this pause check
MIN_TAIL_S = 0.4        # skip a tick if there's barely any new audio yet
# Hard cap on how long the uncommitted tail is allowed to grow, independent
# of the silence check above. SILENCE_RMS was tuned against LOMA1's browser
# mic pipeline; a real mic's noise floor (room tone, fan hiss, AGC) can sit
# above it indefinitely, so relying on silence alone risks a tail that never
# commits and keeps growing for the whole session. This is a safety net, not
# the primary trigger — it only fires if a natural pause never got detected.
MAX_UNCOMMITTED_S = 20.0


def _rms(audio):
    if len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype("float64") ** 2)))


def _is_trailing_silence(audio, seconds, threshold):
    trail_samples = int(seconds * SAMPLE_RATE)
    trail = audio[-trail_samples:] if len(audio) >= trail_samples else audio
    return len(trail) == 0 or _rms(trail) < threshold


def list_input_devices():
    """Names of the machine's microphone-capable devices, for the Live
    tab's device picker. Restricted to the host API of the default input
    device — on Windows, PortAudio otherwise reports every physical mic
    three or four times over (MME, DirectSound, WASAPI, WDM-KS variants of
    the same hardware), which makes the picker useless. Deduplicated by
    name; returns [] if enumeration fails (the picker then just offers
    "System default")."""
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
    """Maps a stored device name back to a PortAudio device index, or None
    (= system default) if the name is empty or no longer present — device
    indices aren't stable across reboots or unplugs, so the *name* is what
    gets persisted, and a vanished device silently falls back to the
    default rather than failing the whole session."""
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


class LiveTranscriber(threading.Thread):
    """Records from the chosen microphone and runs SenseVoice in a
    commit-on-pause loop. Call start() to begin, stop() to end the session —
    the final (possibly still-uncommitted) tail is transcribed and the whole
    transcript is auto-saved before ("live_stopped",) is emitted."""

    def __init__(self, language, events, device_name="", traditional_chinese=False):
        super().__init__(daemon=True)
        self.language = language or ""  # "" = auto; else one of SENSEVOICE_LANGUAGES
        self.events = events
        self.device_name = device_name or ""
        self.traditional_chinese = traditional_chinese
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        # Audio is buffered as lists of small chunks, never one array grown
        # with np.concatenate on every callback — a numpy array has no
        # amortized growth, so repeatedly concatenating onto an ever-growing
        # array makes every mic callback more expensive than the last; on
        # the real-time audio thread that eventually starves the stream.
        # _pending_disk holds only what hasn't been flushed to the WAV file
        # yet (cleared every tick by _flush_audio), _tail_chunks holds only
        # what's been recorded since the last commit (cleared on every
        # commit, so it never exceeds MAX_UNCOMMITTED_S worth of audio) and
        # is what _step() re-decodes every tick. Neither ever holds the
        # whole session.
        self._pending_disk = []
        self._tail_chunks = []
        self._recorded_samples = 0
        # Committed paragraphs as (start_seconds, text). The start offset
        # comes for free: it's simply where in the recording the committed
        # tail began, which the sample counters already know. These become
        # the transcript's clickable paragraph timestamps (see
        # timestamps.py), making live sessions the most precisely-
        # timestamped transcripts of all — no model or VAD support needed.
        #
        # Not every commit starts a new paragraph, though: MAX_UNCOMMITTED_S
        # can force a commit mid-flow, with no pause having actually
        # happened — the speaker is still talking. Treating that as a new
        # paragraph read as if one sentence had fractured into two
        # unrelated ones. _paragraph_pending tracks whether the *previous*
        # commit ended on a real pause (True) or was cap-forced (False);
        # a cap-forced previous commit means this new tail is a direct,
        # gap-free continuation of the same speech, so it's glued onto the
        # last paragraph instead of starting a fresh one. Starts True so
        # the very first commit always begins a paragraph.
        self._parts = []
        self._paragraph_pending = True
        self._tail_start_samples = 0
        self._pinned_lang = self.language
        # Latest mic chunk's RMS — read (without a lock; it's one float
        # assigned atomically under the GIL) by the UI's level meter.
        self.level = 0.0
        self._wav = None
        self._audio_path = None
        self._stem = None

    def stop(self):
        self.stop_event.set()

    # -- internals ------------------------------------------------------

    def _emit(self, *event):
        self.events.put(event)

    def _on_audio(self, indata, _frames, _time_info, _status):
        chunk = indata[:, 0].copy()
        with self._lock:
            self._pending_disk.append(chunk)
            self._tail_chunks.append(chunk)
            self._recorded_samples += len(chunk)
        self.level = _rms(chunk)

    def _tail(self):
        with self._lock:
            tail_chunks = list(self._tail_chunks)
        return np.concatenate(tail_chunks) if tail_chunks \
            else np.zeros(0, dtype=np.float32)

    def _commit_tail(self):
        with self._lock:
            self._tail_chunks = []
            # The next tail starts here — its eventual commit's timestamp.
            self._tail_start_samples = self._recorded_samples

    def _committed_text(self):
        return "\n\n".join(text for _start, text in self._parts)

    def _language_for_call(self):
        return self.language or self._pinned_lang or "auto"

    def _convert(self, text, detected):
        return transcriber.apply_chinese_conversion(
            text, detected or self._pinned_lang or self.language,
            self.traditional_chinese)

    # -- recording file ---------------------------------------------------

    def _open_wav(self):
        """Creates the session's WAV file up front so captured audio can be
        flushed to it as the session runs. A failure here (disk full,
        folder unwritable) is logged and leaves the session running without
        an audio file — the transcript, the part that matters most, is
        unaffected."""
        self._stem = "Live " + datetime.datetime.now().strftime("%Y-%m-%d %H%M%S")
        try:
            folder = settings.live_recordings_folder()
            os.makedirs(folder, exist_ok=True)
            path = transcriber.unique_path(
                os.path.join(folder, self._stem + ".wav"))
            wav = wave.open(path, "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            self._wav, self._audio_path = wav, path
        except Exception:
            settings.log_exception("Could not create the live recording file:")
            self._wav, self._audio_path = None, None

    def _flush_audio(self):
        """Appends everything captured since the last flush to the WAV file.
        Runs on the worker thread once per tick — ~32 KB/s of sequential
        writes, thousands of times below what any disk can sustain, and
        never on the real-time audio callback. wave.writeframes patches the
        RIFF header as it goes, so the file on disk is playable up to the
        last flush even if the process dies. A write failure abandons the
        audio file (logged, file deleted) but never the session."""
        with self._lock:
            chunks, self._pending_disk = self._pending_disk, []
        if not chunks or self._wav is None:
            return
        try:
            audio = np.concatenate(chunks)
            pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
            self._wav.writeframes(pcm16.tobytes())
        except Exception:
            settings.log_exception(
                "Live recording write failed — transcript continues, audio does not:")
            self._close_wav(delete=True)

    def _close_wav(self, delete=False):
        wav, self._wav = self._wav, None
        if wav is not None:
            try:
                wav.close()
            except Exception:
                settings.log_exception("Live recording close failed:")
        if delete and self._audio_path:
            try:
                os.remove(self._audio_path)
            except Exception:
                pass
            self._audio_path = None

    def _finalize_audio(self):
        """Final flush + close. Discards recordings too short to be worth
        keeping. Returns the saved path, or None."""
        self._flush_audio()
        too_short = (self._recorded_samples / SAMPLE_RATE) < 0.5
        self._close_wav(delete=too_short)
        return self._audio_path

    # -- session ----------------------------------------------------------

    def run(self):
        try:
            self._run()
        except Exception:
            settings.log_exception("Live transcription crashed:")
            self._emit("live_status", "engine_failed", {})
            self._emit("live_stopped")

    def _run(self):
        if not transcriber.sensevoice_is_available():
            self._emit("live_status", "engine_failed", {})
            self._emit("live_stopped")
            return

        import sounddevice as sd

        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                device=_resolve_device(self.device_name),
                callback=self._on_audio,
            )
            stream.start()
        except Exception:
            settings.log_exception("Microphone open failed:")
            self._emit("live_status", "mic_failed", {})
            self._emit("live_stopped")
            return

        self._open_wav()

        try:
            if not transcriber.sensevoice_model_loaded():
                self._emit("live_status", "loading", {})

                def on_pct(pct):
                    self._emit("live_status", "downloading",
                               {"size_mb": transcriber.SENSEVOICE_DOWNLOAD_MB, "pct": pct})
                    if pct >= 100:
                        # Byte transfer is done, but constructing the model
                        # (loading weights into memory) is a separate,
                        # unmeasurable step that still takes real time —
                        # switch back to "loading" so 100% doesn't just sit
                        # there looking stuck for the next 10-30s.
                        self._emit("live_status", "loading", {})

                transcriber.get_sensevoice_model(transcriber.monotonic_pct_reporter(on_pct))
        except Exception:
            settings.log_exception("SenseVoice load failed:")
            self._emit("live_status", "engine_failed", {})
            self._close_stream(stream)
            # The engine never got to transcribe anything, but the mic was
            # already capturing (and streaming to disk) before it failed —
            # keep that audio rather than silently losing it, without
            # touching the "engine_failed" status message above (a
            # misleading "no speech detected" would otherwise overwrite it).
            audio_path = self._finalize_audio()
            if audio_path:
                self._emit("live_saved", audio_path, None)
            self._emit("live_stopped")
            return

        self._emit("live_status", "recording", {})
        try:
            while not self.stop_event.wait(STEP_S):
                self._flush_audio()
                self._step()
        finally:
            self._close_stream(stream)
            self._finish()

    @staticmethod
    def _close_stream(stream):
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    def _step(self):
        tail = self._tail()
        tail_dur = len(tail) / SAMPLE_RATE
        if tail_dur < MIN_TAIL_S:
            return
        # Cheap skip on audio that's silence throughout — avoids a wasted
        # model call (and any residual hallucination risk on pure silence)
        # on a tick where nothing new has actually been said yet.
        if _rms(tail) < SILENCE_RMS:
            return

        try:
            text, detected = transcriber.sensevoice_transcribe(tail, self._language_for_call())
        except Exception:
            # Don't let one bad tick kill the whole session — the tail
            # isn't cleared, so this same audio just gets retried next
            # tick instead of being silently dropped.
            settings.log_exception("Live transcription tick failed, retrying next tick:")
            return
        if detected and not self.language:
            self._pinned_lang = detected
        text = self._convert(text, detected)

        natural_pause = tail_dur >= TRAIL_S and _is_trailing_silence(tail, TRAIL_S, SILENCE_RMS)
        # Commit on a detected pause OR once the tail's grown past the hard
        # cap regardless — see MAX_UNCOMMITTED_S above for why the cap
        # can't be skipped in favor of the silence check alone.
        should_commit = text and (natural_pause or tail_dur >= MAX_UNCOMMITTED_S)
        if should_commit:
            self._commit_text(text.strip(), natural_pause)
            self._commit_tail()
            self._emit("live_text", self._committed_text(), "", self._pinned_lang)
        elif text:
            self._emit("live_text", self._committed_text(), text, self._pinned_lang)

    def _commit_text(self, text, ended_on_pause):
        """Adds `text` to the transcript — as a new paragraph if the
        previous commit ended on a real pause (or this is the first
        content ever), or glued onto the last paragraph if the previous
        commit was cap-forced mid-flow (see _paragraph_pending's comment
        in __init__). `ended_on_pause` becomes the new _paragraph_pending:
        it's this commit's own trailing-silence status that decides
        whether the *next* commit continues it or starts fresh."""
        if self._paragraph_pending or not self._parts:
            self._parts.append((self._tail_start_samples / SAMPLE_RATE, text))
        else:
            last_start, last_text = self._parts[-1]
            self._parts[-1] = (
                last_start,
                transcriber.join_pieces(last_text, text, self._pinned_lang))
        self._paragraph_pending = ended_on_pause

    def _finish(self):
        tail = self._tail()
        if len(tail) / SAMPLE_RATE >= 0.3 and _rms(tail) >= SILENCE_RMS:
            try:
                text, detected = transcriber.sensevoice_transcribe(
                    tail, self._language_for_call())
                if detected and not self.language:
                    self._pinned_lang = detected
                text = self._convert(text, detected)
                if text:
                    # Nothing follows this commit (the session is ending),
                    # so the ended_on_pause value passed here is never
                    # read back — only whether it continues the previous
                    # paragraph (via the existing _paragraph_pending)
                    # matters.
                    self._commit_text(text.strip(), True)
            except Exception:
                settings.log_exception("Live transcription final pass failed:")
        self._emit("live_text", self._committed_text(), "", self._pinned_lang)
        self._save_and_finish()

    def _save_and_finish(self):
        text = self._committed_text().strip()

        # Audio first, but a failure there never prevents the transcript
        # save below — the transcript is what actually matters most and is
        # orders of magnitude smaller.
        audio_path = self._finalize_audio()

        txt_path = None
        write_failed = False
        if text:
            try:
                txt_path, _kind = docx_export.save_transcript(
                    text + "\n", settings.transcriptions_folder(), self._stem)
                timestamps.save_sidecar(
                    txt_path, [start for start, _text in self._parts])
            except (PermissionError, OSError):
                # Distinct from "nothing was said" below — the output
                # folder itself is the problem (classic macOS symptom: see
                # README's unsigned-build section), not the session. Text
                # was produced and then lost, which the app can offer to
                # help fix, so this must not collapse into "no_speech".
                settings.log_exception("Live transcript auto-save failed (write error):")
                write_failed = True
            except Exception:
                settings.log_exception("Live transcript auto-save failed:")

        if txt_path:
            self._emit("live_status", "saved", {"path": txt_path})
        elif write_failed:
            self._emit("live_status", "save_failed", {})
        else:
            # No text either because nothing was said, or because ASR
            # produced nothing usable — the audio (if any) is still saved.
            self._emit("live_status", "no_speech", {})

        if audio_path or txt_path:
            self._emit("live_saved", audio_path, txt_path)
        self._emit("live_stopped")


class SenseVoicePreloader(threading.Thread):
    """Warms up the SenseVoice model as soon as the Live tab is opened,
    instead of waiting for Start Recording — so the idle placeholder can
    honestly say "downloading"/"loading" up front rather than inviting the
    user to start recording before the engine is actually ready.

    get_sensevoice_model() is single-flight (see transcriber.py): if the
    user hits Start Recording before this finishes, LiveTranscriber's own
    load call just blocks on the same lock and reuses whatever this thread
    already downloaded/loaded, rather than duplicating the work.

    Reports through the same ("live_status", key, detail) events as
    LiveTranscriber, plus a "ready" key (not otherwise used) meaning the
    model is loaded and idle — app.py treats that as "show the normal
    placeholder", not as a message to display.
    """

    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events

    def _emit(self, *event):
        self.events.put(event)

    def run(self):
        if not transcriber.sensevoice_is_available():
            self._emit("live_status", "engine_failed", {})
            return
        if transcriber.sensevoice_model_loaded():
            self._emit("live_status", "ready", {})
            return
        try:
            self._emit("live_status", "loading", {})

            def on_pct(pct):
                self._emit("live_status", "downloading",
                           {"size_mb": transcriber.SENSEVOICE_DOWNLOAD_MB, "pct": pct})
                if pct >= 100:
                    self._emit("live_status", "loading", {})

            transcriber.get_sensevoice_model(transcriber.monotonic_pct_reporter(on_pct))
            self._emit("live_status", "ready", {})
        except Exception:
            settings.log_exception("SenseVoice preload failed:")
            self._emit("live_status", "engine_failed", {})
