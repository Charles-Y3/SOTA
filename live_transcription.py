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

# Long-silence handling for sessions left running while the user works in
# another tab (see Save Draft). Deliberately two-tier rather than one quick
# auto-stop: a real dictation pause (thinking, a phone call, checking notes)
# can easily run past a minute or two, and a *silent* stop risks the worse
# failure — talking into a session that quietly isn't listening anymore,
# possibly unnoticed for a while. IDLE_NUDGE_S only asks the UI to note
# "still idle" (visible from any tab, nothing is cut); only past
# IDLE_AUTO_STOP_S — comfortably outside any normal pause, "walked away and
# forgot" territory — does the session actually end itself, same as if
# Stop had been clicked (everything up to that point is already on disk).
IDLE_NUDGE_S = 600.0       # 10 min
IDLE_AUTO_STOP_S = 3000.0  # 50 min

# Floor between automatic draft saves (see _step()'s trigger below) — not a
# data-loss risk either way (the final save always picks up whatever a
# debounced tick skipped), purely a cap on write frequency. python-docx has
# no true incremental append; append_transcript re-reads and re-serializes
# the whole file every call, so a burst of short, closely-spaced paragraphs
# (a natural-pause commit can in principle fire every STEP_S) would
# otherwise mean a docx rewrite that often.
AUTO_SAVE_MIN_INTERVAL_S = 5.0


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

    def __init__(self, language, events, device_name="", traditional_chinese=False,
                 custom_stem=None):
        super().__init__(daemon=True)
        self.language = language or ""  # "" = auto; else one of SENSEVOICE_LANGUAGES
        self.events = events
        self.device_name = device_name or ""
        self.traditional_chinese = traditional_chinese
        # Already validated (illegal characters, Windows-reserved names,
        # length) by the caller before this thread is even started — see
        # app.py's _validate_live_filename. None/empty means "use the
        # automatic timestamp name" (_open_wav).
        self.custom_stem = custom_stem or None
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
        # Draft ("partial save") state. The UI's Save-draft button sets the
        # event; the worker services it between ticks, so all file writes
        # happen on this thread — no locking of _parts needed (it's only
        # ever touched from here). _draft_path is the transcript file once
        # the first draft save created it; every later save (and the final
        # one at session end) appends to it rather than writing a new file.
        self._partial_save_requested = threading.Event()
        self._draft_path = None
        self._draft_saved_count = 0  # how many of _parts are already on disk
        # Ready to fire the moment the first paragraph completes — see the
        # auto-save trigger in _step().
        self._ticks_since_auto_save = AUTO_SAVE_MIN_INTERVAL_S

        # Long-silence tracking (see IDLE_NUDGE_S/IDLE_AUTO_STOP_S). Counted
        # in accumulated tick-seconds, not wall-clock time, so it's exact
        # regardless of how long a tick itself takes to run.
        self._idle_s = 0.0
        self._idle_notified = False
        self._auto_stopped_idle = False
        # Latest mic chunk's RMS — read (without a lock; it's one float
        # assigned atomically under the GIL) by the UI's level meter.
        self.level = 0.0
        self._wav = None
        self._audio_path = None
        self._stem = None

    def stop(self):
        self.stop_event.set()

    def request_partial_save(self):
        """Manual trigger for the same save _step() already fires on its own
        whenever a paragraph completes (see the natural_pause branch there)
        — this button just forces it right now rather than waiting for the
        next one, useful mid-debounce-window or right before switching
        tabs. Thread-safe: just sets a flag; the worker thread does the
        actual writing."""
        self._partial_save_requested.set()

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
        if self.custom_stem:
            self._stem = self.custom_stem
        else:
            # Fullwidth colon (U+FF1A "："), not the ASCII ":" — a real
            # colon in a Windows filename doesn't error, it silently
            # truncates: NTFS reads "06:08.txt" as alternate-data-stream
            # syntax (file "06" with a hidden stream named "08.txt"), so
            # the visible file loses both its minute and its extension
            # with no warning. The fullwidth colon is a different Unicode
            # codepoint that reads almost identically and behaves as an
            # ordinary character on disk. Minute precision only — two
            # sessions starting the same minute already get "(2)" etc.
            # from unique_path below, same as any other name clash.
            now = datetime.datetime.now()
            self._stem = "Live " + now.strftime("%Y-%m-%d %H") + "：" + now.strftime("%M")
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

        if self.stop_event.is_set():
            # Stop was clicked while the load above was still running (a
            # slow first-time download, or funasr falling back to
            # ModelScope after Hugging Face failed — both can take minutes
            # and have no cooperative cancellation point inside them, so
            # stop_event couldn't interrupt it mid-call). Without this
            # check, the session would barrel into "recording" anyway,
            # silently overriding a Stop the user already asked for. The
            # model load itself still isn't wasted — it's cached for next
            # time — only this session's recording is abandoned.
            self._close_stream(stream)
            audio_path = self._finalize_audio()
            if audio_path:
                self._emit("live_saved", audio_path, None)
            self._emit("live_stopped")
            return

        # "stem" lets the UI display the automatic name it ended up using
        # when the filename field was left blank — harmless extra key when
        # a custom name was given instead; live_status_recording's template
        # ("Recording…") doesn't reference it, and str.format ignores
        # unused kwargs.
        self._emit("live_status", "recording", {"stem": self._stem})
        try:
            while not self.stop_event.wait(STEP_S):
                self._flush_audio()
                self._ticks_since_auto_save += STEP_S
                self._step()
                if self._partial_save_requested.is_set():
                    self._partial_save_requested.clear()
                    self._partial_save()
                if self._check_idle():
                    break
        finally:
            self._close_stream(stream)
            self._finish()

    def _check_idle(self):
        """Updates idle-nudge state after each tick; returns True once the
        session should end itself (crossed IDLE_AUTO_STOP_S)."""
        if self._idle_s >= IDLE_NUDGE_S:
            if not self._idle_notified:
                self._idle_notified = True
                self._emit("live_status", "idle", {})
        elif self._idle_notified:
            self._idle_notified = False
            self._emit("live_status", "idle_cleared", {})
        if self._idle_s >= IDLE_AUTO_STOP_S:
            self._auto_stopped_idle = True
            return True
        return False

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
            # Not a silence signal — this fires constantly right after every
            # commit clears the tail, active session or not. Idle tracking
            # (_idle_s) is untouched here on purpose; only the branch below,
            # a real tail that's actually silent, counts toward it.
            return
        # Cheap skip on audio that's silence throughout — avoids a wasted
        # model call (and any residual hallucination risk on pure silence)
        # on a tick where nothing new has actually been said yet.
        if _rms(tail) < SILENCE_RMS:
            self._idle_s += STEP_S
            return
        self._idle_s = 0.0

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
            if natural_pause and self._ticks_since_auto_save >= AUTO_SAVE_MIN_INTERVAL_S:
                # The paragraph just committed is now frozen (see
                # _frozen_count) — surface it in the Edit tab as soon as
                # it's ready instead of only after a manual Save Draft
                # click. Same request/flag the button sets; _run()'s loop
                # services it on this same tick, right after _step()
                # returns.
                self._ticks_since_auto_save = 0.0
                self._partial_save_requested.set()
        elif text:
            self._emit("live_text", self._committed_text(), text, self._pinned_lang)

    def _frozen_count(self):
        """How many leading paragraphs of _parts can no longer change.
        Committed paragraphs are append-only with one exception: the most
        recent one can still grow via continuation-commits (a cap-forced
        commit glues onto it — see _commit_text). So the last paragraph is
        only 'frozen' once _paragraph_pending says the next commit will
        start a NEW paragraph; otherwise it's excluded so a draft save
        never writes text that a later glue would silently extend."""
        if self._paragraph_pending:
            return len(self._parts)
        return max(0, len(self._parts) - 1)

    def _partial_save(self):
        """Writes every frozen-but-unsaved paragraph to the session's
        transcript file — creating it on the first draft save, appending on
        later ones — and tells the UI, so the file shows up in the Edit tab
        (and grows in place) while recording continues. Failure never kills
        the session: the paragraphs stay in _parts and the next save (or
        the final one at stop) simply retries them."""
        upto = self._frozen_count()
        new = self._parts[self._draft_saved_count:upto]
        if not new:
            self._emit("live_status", "draft_empty", {})
            return
        try:
            if self._draft_path is None:
                block = "\n\n".join(text for _start, text in new)
                self._draft_path, _kind = docx_export.save_transcript(
                    block + "\n", settings.transcriptions_folder(), self._stem)
            else:
                docx_export.append_transcript(
                    self._draft_path, [text for _start, text in new])
            timestamps.save_sidecar(
                self._draft_path,
                [start for start, _text in self._parts[:upto]])
        except Exception:
            settings.log_exception("Live draft save failed:")
            self._emit("live_status", "save_failed", {})
            return
        self._draft_saved_count = upto
        # Register the (still-growing) transcript + audio with the Edit tab
        # right away, then hand it the appended paragraphs so an already-
        # open editor can offer to pull them in instead of going stale.
        self._emit("live_saved", self._audio_path, self._draft_path)
        self._emit("live_draft", self._draft_path, list(new))
        self._emit("live_status", "draft_saved", {"path": self._draft_path})

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
        if self._draft_path is not None:
            # Draft saves already created this session's transcript file —
            # the final save appends everything not yet on disk (at this
            # point every paragraph is final, including the previously
            # still-mutable last one and _finish()'s tail commit) instead
            # of writing a second file next to the draft.
            txt_path = self._draft_path
            remainder = self._parts[self._draft_saved_count:]
            try:
                if remainder:
                    docx_export.append_transcript(
                        txt_path, [t for _start, t in remainder])
                    timestamps.save_sidecar(
                        txt_path, [start for start, _text in self._parts])
                    self._draft_saved_count = len(self._parts)
                    self._emit("live_draft", txt_path, list(remainder))
            except (PermissionError, OSError):
                settings.log_exception("Live transcript final append failed (write error):")
                write_failed = True
            except Exception:
                settings.log_exception("Live transcript final append failed:")
        elif text:
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

        # write_failed is checked first: with a draft file, txt_path is set
        # even when the final append just failed — reporting "saved" there
        # would hide that the session's newest paragraphs never landed.
        if write_failed:
            self._emit("live_status", "save_failed", {})
        elif txt_path and self._auto_stopped_idle:
            # Distinct from plain "saved" so returning to the tab later
            # explains why the session isn't running anymore, instead of it
            # just quietly not being there.
            self._emit("live_status", "saved_idle_stop",
                       {"path": txt_path, "minutes": int(IDLE_AUTO_STOP_S // 60)})
        elif txt_path:
            self._emit("live_status", "saved", {"path": txt_path})
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
    honestly say "loading" up front rather than inviting the user to start
    recording before the engine is actually ready.

    Load-from-disk only, never a download: merely opening this tab must not
    kick off a ~900 MB fetch the user didn't ask for — it fires even with
    the SenseVoice checkbox off, and it competes for bandwidth with any
    Transcribe-tab model download running at the same time. The download
    belongs to an explicit Start Recording (LiveTranscriber, after app.py's
    disk-space gate).

    get_sensevoice_model() is single-flight (see transcriber.py): if the
    user hits Start Recording before this finishes, LiveTranscriber's own
    load call just blocks on the same lock and reuses whatever this thread
    already loaded, rather than duplicating the work.

    Reports through the same ("live_status", key, detail) events as
    LiveTranscriber, plus a "ready" key (not otherwise used) meaning the
    model is loaded (or intentionally left undownloaded) and idle — app.py
    treats that as "show the normal placeholder", not as a message to
    display.
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
        if not transcriber.sensevoice_is_downloaded():
            self._emit("live_status", "ready", {})
            return
        try:
            self._emit("live_status", "loading", {})
            transcriber.get_sensevoice_model()
            self._emit("live_status", "ready", {})
        except Exception:
            settings.log_exception("SenseVoice preload failed:")
            self._emit("live_status", "engine_failed", {})
