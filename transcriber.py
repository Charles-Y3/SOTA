"""Background transcription worker built on faster-whisper.

The worker runs in its own thread and never touches the UI. It reports
everything through a queue of events, using translation-neutral keys so the
UI can render them in whichever language the user has selected:

    ("line", key, detail)             -> bottom status line
    ("job", index, key, detail, pct)  -> per-file status (pct may be None)
    ("saved", index, output_path)     -> a transcript file was written
    ("finished",)                     -> the whole batch is over
"""

import os
import threading

import settings

# Canonical quality keys -> Whisper model sizes (kept small so downloads
# stay reasonable and CPU transcription stays usable). Display names for
# these keys live in i18n.py.
QUALITY_MODELS = {
    "fast": "tiny",
    "balanced": "base",
    "accurate": "small",
}
MODEL_DOWNLOAD_MB = {"tiny": 75, "base": 145, "small": 480}

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".oga", ".opus", ".wma",
    ".aac", ".aiff", ".aif", ".amr", ".mpga",
    # video containers: the audio track is extracted automatically
    ".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".mpeg", ".mpg", ".3gp",
}


def is_supported(path):
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


def model_is_downloaded(size):
    snapshots = os.path.join(
        settings.MODELS_DIR, f"models--Systran--faster-whisper-{size}", "snapshots"
    )
    if not os.path.isdir(snapshots):
        return False
    for snap in os.listdir(snapshots):
        if os.path.isfile(os.path.join(snapshots, snap, "model.bin")):
            return True
    return False


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for i in range(2, 1000):
        candidate = f"{base} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return f"{base} ({os.getpid()}){ext}"


def _format_timestamp(seconds):
    s = int(max(0, seconds))
    hours, rest = divmod(s, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"[{hours}:{minutes:02d}:{secs:02d}]"
    return f"[{minutes:02d}:{secs:02d}]"


def build_text(segments, timestamps):
    if timestamps:
        lines = [
            f"{_format_timestamp(seg.start)} {seg.text.strip()}"
            for seg in segments
            if seg.text.strip()
        ]
        return "\n".join(lines) + ("\n" if lines else "")

    # Group segments into paragraphs on pauses longer than 2 seconds.
    paragraphs, current, prev_end = [], [], None
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if current and prev_end is not None and seg.start - prev_end > 2.0:
            paragraphs.append(" ".join(current))
            current = []
        current.append(text)
        prev_end = seg.end
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")


def _short_error(exc):
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if not message:
        message = type(exc).__name__
    if len(message) > 90:
        message = message[:90] + "…"
    return message


class Job:
    def __init__(self, index, path):
        self.index = index
        self.path = path


class TranscriberWorker(threading.Thread):
    """Transcribes a list of jobs sequentially; results go to `events`."""

    def __init__(self, jobs, quality, language_code, timestamps, output_folder, events):
        super().__init__(daemon=True)
        self.jobs = jobs
        self.quality = quality  # "fast" | "balanced" | "accurate"
        self.language_code = language_code  # None (auto) or an ISO code
        self.timestamps = timestamps
        self.output_folder = output_folder
        self.events = events
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    # -- helpers ------------------------------------------------------------

    def _emit(self, *event):
        self.events.put(event)

    def _load_model(self):
        size = QUALITY_MODELS[self.quality]
        first_run = not model_is_downloaded(size)
        if first_run:
            self._emit(
                "line", "downloading",
                {"quality": self.quality, "size_mb": MODEL_DOWNLOAD_MB[size]},
            )
        else:
            self._emit("line", "loading", {"quality": self.quality})

        os.makedirs(settings.MODELS_DIR, exist_ok=True)
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from faster_whisper import WhisperModel  # deferred: heavy import

        try:
            return WhisperModel(
                size,
                device="cpu",
                compute_type="int8",
                cpu_threads=max(1, (os.cpu_count() or 4) - 1),
                download_root=settings.MODELS_DIR,
            )
        except Exception:
            settings.log_exception("Model load failed:")
            self._emit("line", "download_failed" if first_run else "load_failed", {})
            for job in self.jobs:
                self._emit("job", job.index, "failed_model", {}, None)
            return None

    # -- main loop ----------------------------------------------------------

    def run(self):
        try:
            self._run()
        except Exception:
            settings.log_exception("Worker crashed:")
            self._emit("line", "crashed", {})
        finally:
            self._emit("finished")

    def _run(self):
        model = self._load_model()
        if model is None:
            return

        self._emit("line", "transcribing", {})
        for job in self.jobs:
            if self.cancel_event.is_set():
                self._emit("job", job.index, "cancelled", {}, None)
                continue
            self._transcribe_one(model, job)

        self._emit("line", "cancelled" if self.cancel_event.is_set() else "finished", {})

    def _transcribe_one(self, model, job):
        self._emit("job", job.index, "transcribing", {"pct": 0}, 0)
        try:
            if not os.path.isfile(job.path):
                self._emit("job", job.index, "failed_not_found", {}, None)
                return

            segments_iter, info = model.transcribe(
                job.path,
                language=self.language_code,
                beam_size=5,
                condition_on_previous_text=False,
            )
            duration = info.duration or 0.0

            segments = []
            for seg in segments_iter:
                if self.cancel_event.is_set():
                    self._emit("job", job.index, "cancelled", {}, None)
                    return
                segments.append(seg)
                if duration > 0:
                    pct = int(min(99, seg.end / duration * 100))
                    self._emit("job", job.index, "transcribing", {"pct": pct}, pct)

            text = build_text(segments, self.timestamps)
            stem = os.path.splitext(os.path.basename(job.path))[0]
            folder = self.output_folder or os.path.dirname(job.path)
            try:
                import docx_export  # lazy: avoids an import cycle

                output_path, _kind = docx_export.save_transcript(text, folder, stem)
            except (PermissionError, OSError):
                settings.log_exception(f"Write failed in {folder}:")
                self._emit("job", job.index, "failed_write", {}, None)
                return

            self._emit("saved", job.index, output_path)
            if not text.strip():
                self._emit("job", job.index, "done_no_speech", {}, 100)
            elif self.language_code is None and info.language:
                self._emit("job", job.index, "done_lang", {"code": info.language}, 100)
            else:
                self._emit("job", job.index, "done", {}, 100)

        except Exception as exc:
            settings.log_exception(f"Transcription failed for {job.path}:")
            self._emit(
                "job", job.index, "failed_error", {"error": _short_error(exc)}, None
            )
