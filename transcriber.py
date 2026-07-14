"""Background transcription worker built on faster-whisper.

The worker runs in its own thread and never touches the UI. It reports
everything through a queue of events, using translation-neutral keys so the
UI can render them in whichever language the user has selected:

    ("line", key, detail)             -> bottom status line
    ("job", index, key, detail, pct)  -> per-file status (pct may be None)
    ("saved", index, output_path)     -> a transcript file was written
    ("finished",)                     -> the whole batch is over
"""

import contextlib
import os
import re
import threading

import settings
from progress import make_progress_tqdm_class

# Canonical quality keys -> Whisper model sizes. Display names for these
# keys live in i18n.py.
QUALITY_MODELS = {
    "fast": "base",
    "balanced": "small",
    # A pruned-decoder large-v3: near-large-v3 transcription accuracy (the
    # decoder pruning mainly costs X->English *translation* quality, a mode
    # this app never uses — Whisper always runs in transcribe mode here,
    # with translation handled separately by the local LLM) at a fraction
    # of large-v3's CPU cost — the only "large-class" Whisper model that's
    # actually practical without a GPU.
    "accurate": "large-v3-turbo",
}

# huggingface_hub repo id for each size. Not a simple f"Systran/faster-
# whisper-{size}" pattern: Systran never published a CT2 conversion of
# large-v3-turbo, so that model's weights live under a different
# maintainer's repo. Getting this wrong means a silent 404 on first
# download, so every lookup (download, and the on-disk folder-name check)
# goes through this map rather than re-deriving the repo id inline.
WHISPER_MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
# Measured directly (HEAD on each repo's model.bin + the small config/
# tokenizer files) rather than assumed, since download size scales with
# parameter count, not with the short "model size" label.
MODEL_DOWNLOAD_MB = {"base": 145, "small": 480, "large-v3-turbo": 1550}


def whisper_repo_dirname(size):
    """The huggingface_hub cache folder name for `size`'s snapshot —
    derived from WHISPER_MODEL_REPOS rather than assumed, for the same
    reason the repo id itself isn't derived from a pattern."""
    return "models--" + WHISPER_MODEL_REPOS[size].replace("/", "--")


# Rough total-RAM guidance (generous, accounting for OS + app overhead —
# not just the model file) so we can warn before a download if the whole
# system looks too tight to run comfortably.
QUALITY_RAM_GB = {"fast": 1.5, "balanced": 2.5, "accurate": 4.0}


def recommended_quality(ram_gb):
    """Highest quality tier likely to run comfortably with `ram_gb` of
    total RAM. Defaults to "balanced" if RAM couldn't be determined."""
    if ram_gb is None:
        return "balanced"
    best = "fast"
    for key in ("fast", "balanced", "accurate"):
        if ram_gb >= QUALITY_RAM_GB[key]:
            best = key
    return best

# Matches faster_whisper.utils.download_model — we replicate its snapshot
# download ourselves (instead of letting WhisperModel do it) so we can pass
# our own tqdm_class and report real progress.
_WHISPER_ALLOW_PATTERNS = [
    "config.json", "preprocessor_config.json", "model.bin",
    "tokenizer.json", "vocabulary.*",
]

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".oga", ".opus", ".wma",
    ".aac", ".aiff", ".aif", ".amr", ".mpga",
    # video containers: the audio track is extracted automatically
    ".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".mpeg", ".mpg", ".3gp",
}


def is_supported(path):
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


# --------------------------------------------------------------------------- #
# Traditional Chinese conversion (OpenCC).
# SenseVoice always emits Mandarin as Simplified Chinese, and whisper's zh
# output is unpredictable (either script, sometimes mixed within one file) —
# for the Traditional-Chinese users this app targets, that's a constant
# irritation. s2t is safe to run over text that's already Traditional (those
# characters map to themselves), so no "is it Simplified?" pre-check is
# needed. It MUST stay gated on the transcript language being Chinese,
# though: Japanese shares codepoints with Simplified Chinese (e.g. 国, 学)
# that s2t would "convert", mangling correct Japanese into Chinese forms.
# --------------------------------------------------------------------------- #
CHINESE_LANGS = {"zh", "yue"}

_OPENCC_CACHE = {}


def to_traditional(text):
    """Converts Simplified Chinese in `text` to Traditional. Returns the
    text unchanged if opencc isn't available or fails — a transcript in the
    wrong script is a much smaller problem than losing it."""
    if not text:
        return text
    try:
        converter = _OPENCC_CACHE.get("s2t")
        if converter is None:
            from opencc import OpenCC

            converter = _OPENCC_CACHE["s2t"] = OpenCC("s2t")
        return converter.convert(text)
    except Exception:
        settings.log_exception("OpenCC conversion failed:")
        return text


def apply_chinese_conversion(text, language, enabled):
    """to_traditional(), applied only when the user wants it and the
    transcript is actually Chinese (see CHINESE_LANGS note above for why
    the language gate is load-bearing, not cosmetic)."""
    if enabled and language in CHINESE_LANGS:
        return to_traditional(text)
    return text


# --------------------------------------------------------------------------- #
# SenseVoice engine (Transcribe tab toggle + Live Transcription tab).
# SenseVoiceSmall (FunASR) covers exactly these 5 languages, natively, in one
# model — everything else stays on whisper. It's non-streaming (whole-buffer
# decode) and has no per-word timestamps, so it always returns one blob of
# text for whatever audio it's given rather than timestamped segments.
# --------------------------------------------------------------------------- #
SENSEVOICE_LANGUAGES = {"en", "zh", "yue", "ja", "ko"}
SENSEVOICE_SAMPLE_RATE = 16000
SENSEVOICE_DOWNLOAD_MB = 900  # approximate combined size shown in the UI
                              # (SenseVoiceSmall ~880 MB + fsmn-vad ~2 MB)

# Downloaded from Hugging Face first — the same CDN whisper's models use,
# dramatically faster than ModelScope outside mainland China — with funasr's
# own ModelScope flow kept as an automatic fallback (which is also what
# mainland users land on, where Hugging Face is blocked). Both repos belong
# to the models' original publishers: FunAudioLLM is the SenseVoice team,
# and funasr/fsmn-vad is the FunASR team's own mirror (the exact mapping
# funasr's hub="hf" mode uses in name_maps_hf).
SENSEVOICE_HF_REPOS = ("FunAudioLLM/SenseVoiceSmall", "funasr/fsmn-vad")

# READMEs, demo audio/images, demo.py, and requirements.txt (only consumed
# under trust_remote_code, which is deliberately off) — ~15 MB of weight the
# model never loads.
_SENSEVOICE_HF_IGNORE = ["*.md", "example/*", "examples/*", "image/*",
                         "fig/*", "demo.py", "requirements.txt"]

_SENSEVOICE_CACHE = {}
_SENSEVOICE_TAG_RE = re.compile(r"<\|[^|]*\|>")
_SENSEVOICE_LOCK = threading.Lock()
_SENSEVOICE_PROGRESS_LISTENERS = []
_SENSEVOICE_LISTENERS_LOCK = threading.Lock()


def _broadcast_sensevoice_progress(downloaded, total):
    """The single on_progress actually passed to the download — fans out
    to every caller currently waiting on get_sensevoice_model(), not just
    whichever one happens to be the lock owner doing the real work (see
    get_sensevoice_model's docstring for why that distinction matters)."""
    with _SENSEVOICE_LISTENERS_LOCK:
        listeners = list(_SENSEVOICE_PROGRESS_LISTENERS)
    for fn in listeners:
        try:
            fn(downloaded, total)
        except Exception:
            pass


def sensevoice_is_available():
    import importlib.util
    try:
        return importlib.util.find_spec("funasr") is not None
    except Exception:
        return False


def sensevoice_model_loaded():
    """True once get_sensevoice_model() has succeeded in this process —
    lets a caller show a "loading/downloading" status only the first time."""
    return _SENSEVOICE_CACHE.get("model") is not None


def sensevoice_load_in_progress():
    """True while some caller currently holds _SENSEVOICE_LOCK — resolving
    the local copy, downloading, or constructing. Lets a second caller (a
    different tab) recognize "already being fetched elsewhere" and skip
    straight to get_sensevoice_model() (which blocks on the same lock and
    reuses the result) instead of re-running its own confirm-before-
    download gate, which would otherwise show a redundant dialog for work
    that's already underway."""
    return _SENSEVOICE_LOCK.locked()


def unload_sensevoice_model():
    """Drops the in-memory SenseVoiceSmall + fsmn-vad instances. Without
    this, deleting the on-disk files via the Settings tab would leave a
    model already loaded into memory (from an earlier use, in the same
    running process) fully usable — silently — because sensevoice_model_
    loaded() only reflects what's in RAM, not what's on disk. Called right
    after a Settings-tab delete so every tab's "is it downloaded" checks
    agree with the Settings list from that point on, instead of only after
    an app restart clears the cache naturally."""
    _SENSEVOICE_CACHE.pop("model", None)
    _VAD_CACHE.pop("model", None)


# Legacy on-disk folder names modelscope used under MODELS_DIR before the
# switch to Hugging Face downloads (see settings.py's MODELSCOPE_CACHE note
# for how they end up there). Still recognized so installs that downloaded
# SenseVoice through ModelScope keep working without a re-download.
_SENSEVOICE_MS_DIRNAMES = (
    "iic--SenseVoiceSmall",
    "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch",
)


def _hf_cache_dirname(repo):
    """huggingface_hub's cache folder name for a repo under cache_dir."""
    return "models--" + repo.replace("/", "--")


def sensevoice_model_dirs():
    """Every folder the SenseVoice pair can occupy under MODELS_DIR — the
    Hugging Face cache layout (current downloads) plus the legacy ModelScope
    layout (pre-HF installs). The Settings tab's model manager sums/deletes
    whichever of these exist; the pair is treated as one unit since the
    models are only ever useful together."""
    names = ([_hf_cache_dirname(repo) for repo in SENSEVOICE_HF_REPOS]
             + list(_SENSEVOICE_MS_DIRNAMES))
    return [os.path.join(settings.MODELS_DIR, name) for name in names]


def _dir_with_model_files(root):
    """The folder under `root` holding a complete funasr model — config.yaml
    and model.pt together (model.pt is the payload; both hubs only
    materialize it in the final location once its transfer finished, so its
    presence means the copy is usable) — or None. Walks because the layouts
    nest differently: HF keeps files under snapshots/<revision>/, and
    modelscope's own layout has varied across versions (flat, or
    snapshots/<revision>/)."""
    if not os.path.isdir(root):
        return None
    for dirpath, _dirs, files in os.walk(root):
        if "config.yaml" in files and "model.pt" in files:
            return dirpath
    return None


def sensevoice_local_paths():
    """(sensevoice_dir, vad_dir) of complete on-disk copies — HF layout
    preferred, legacy ModelScope layout accepted — or None if either half
    is missing. Handing these directory paths to AutoModel makes the load
    fully local: funasr skips its hub/download code entirely for a `model`
    that is an existing path, so warm starts are instant and guaranteed
    offline (no version-check call, no timeout on a machine with no
    network)."""
    dirs = sensevoice_model_dirs()  # [hf_sv, hf_vad, legacy_sv, legacy_vad]
    sv = _dir_with_model_files(dirs[0]) or _dir_with_model_files(dirs[2])
    vad = _dir_with_model_files(dirs[1]) or _dir_with_model_files(dirs[3])
    if sv and vad:
        return sv, vad
    return None


def sensevoice_is_downloaded():
    return sensevoice_local_paths() is not None


def _make_sensevoice_progress_callback_class(on_progress, base_cls):
    """Builds a modelscope_hub ProgressCallback subclass that aggregates
    bytes across every concurrently-downloading file and reports the
    running total/downloaded pair — the same aggregation progress.py's
    make_progress_tqdm_class does for whisper's tqdm-based hook, adapted to
    modelscope_hub's own (non-tqdm) progress_callbacks interface."""
    state = {}
    lock = threading.Lock()

    def _emit():
        with lock:
            downloaded = sum(n for n, _ in state.values())
            total = sum(t for _, t in state.values())
        if total:
            on_progress(downloaded, total)

    class _Callback(base_cls):
        def __init__(self, filename, file_size):
            super().__init__(filename, file_size)
            with lock:
                state[id(self)] = (0, file_size or 0)
            _emit()

        def update(self, size):
            with lock:
                downloaded, total = state.get(id(self), (0, self.file_size or 0))
                state[id(self)] = (downloaded + size, total)
            _emit()

        def end(self):
            with lock:
                _downloaded, total = state.get(id(self), (0, 0))
                if total:
                    state[id(self)] = (total, total)
            _emit()

    return _Callback


@contextlib.contextmanager
def _sensevoice_download_progress(on_progress):
    """Best-effort byte-level download progress for funasr's model fetch.

    Unlike huggingface_hub (used for whisper models, which accepts a
    tqdm_class override directly), funasr's default hub (ModelScope) has no
    such hook on the path it actually calls: modelscope.hub.snapshot_download
    -> modelscope_hub.compat.snapshot_download -> HubApi.download_repo all
    the way down to DownloadManager.download_repo, which *does* accept a
    progress_callbacks=[ProgressCallback subclass, ...] list and forwards it
    to real per-chunk cb.update(nbytes) calls — but nothing in that chain
    ever passes one in, so it's silently never used. This patches
    DownloadManager.download_repo for the duration of this call to inject
    our own ProgressCallback subclass as the default when the caller didn't
    supply one. If modelscope_hub's internals have moved (class renamed,
    method signature changed), this just silently does nothing — a missing
    progress readout is a much smaller problem than crashing the download.

    The module's own console tqdm bars (a per-file counter, plus byte bars
    on the single-stream path) are also swapped for a silent no-report
    class while patched: in a --windowed build sys.stderr is None and tqdm
    writing to it crashes the download (the same failure progress.py's
    _NullFile exists to prevent). Silent on purpose — real byte progress
    comes from the injected callback; a second reporter with a different
    denominator (those bars never see the parallel-path files) would fight
    it.
    """
    if on_progress is None:
        yield
        return
    try:
        import modelscope_hub._download as _msd
    except Exception:
        yield
        return
    original_download_repo = getattr(_msd.DownloadManager, "download_repo", None)
    progress_callback_base = getattr(_msd, "ProgressCallback", None)
    if original_download_repo is None or progress_callback_base is None:
        yield
        return
    callback_cls = _make_sensevoice_progress_callback_class(on_progress, progress_callback_base)

    def _patched_download_repo(self, *args, **kwargs):
        kwargs.setdefault("progress_callbacks", [callback_cls])
        return original_download_repo(self, *args, **kwargs)

    original_tqdm = getattr(_msd, "tqdm", None)
    _msd.DownloadManager.download_repo = _patched_download_repo
    if original_tqdm is not None:
        _msd.tqdm = make_progress_tqdm_class(lambda _downloaded, _total: None)
    try:
        yield
    finally:
        _msd.DownloadManager.download_repo = original_download_repo
        if original_tqdm is not None:
            _msd.tqdm = original_tqdm


def monotonic_pct_reporter(on_pct):
    """Wraps an on_pct(whole_number_percent) callback so it only ever fires
    on a new, non-decreasing whole-number percentage.

    SenseVoice's download spans ~28 files across two models (SenseVoiceSmall
    + its fsmn-vad front-end) fetched with 4-way parallelism — the "total"
    isn't known upfront, so it grows as new files start downloading, which
    can make the raw aggregate fraction dip backwards (e.g. 100% -> 5%) each
    time a new file's bytes are added to the denominator. A visible progress
    readout that jumps backward reads as broken; clamping to non-decreasing
    keeps it looking like real progress even though the underlying estimate
    isn't monotonic. Returns an on_progress(downloaded, total) callable
    suitable for get_sensevoice_model()."""
    state = {"value": -1}

    def on_progress(downloaded, total):
        if not total:
            return
        # min(100, …): a resumed/retried fetch re-counts already-downloaded
        # bytes against the same running total, so the raw ratio can
        # overshoot 1.0 near the end.
        pct = max(state["value"], min(100, int(downloaded / total * 100)))
        if pct != state["value"]:
            state["value"] = pct
            on_pct(pct)

    return on_progress


def _download_sensevoice_hf(on_progress):
    """Fetches both SenseVoice repos from Hugging Face into MODELS_DIR —
    the same cache layout and tqdm-class byte-progress hook whisper's
    downloads use. One make_progress_tqdm_class instance is shared across
    both repos so listeners see a single growing denominator rather than
    two disjoint 0-100%% runs. Returns (sensevoice_dir, vad_dir)."""
    import huggingface_hub

    os.makedirs(settings.MODELS_DIR, exist_ok=True)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    tqdm_class = make_progress_tqdm_class(on_progress)
    return tuple(
        huggingface_hub.snapshot_download(
            repo, cache_dir=settings.MODELS_DIR,
            ignore_patterns=_SENSEVOICE_HF_IGNORE, tqdm_class=tqdm_class)
        for repo in SENSEVOICE_HF_REPOS)


def _construct_sensevoice(model_dir, vad_dir):
    """Builds the AutoModel from local directory paths, never hub IDs —
    funasr skips its hub/download code entirely for an existing path, so
    this is instant-warm and fully offline.

    Natively registered SenseVoiceSmall class (funasr/models/sense_voice/
    model.py) — deliberately NOT passing trust_remote_code=True, which
    would execute the repo's own model.py instead of the library's vetted
    implementation. The modelscope download patch stays wrapped around
    construction as a safety net: if some sub-config unexpectedly triggers
    a hub fetch anyway, it reports progress and keeps the console-tqdm
    silent instead of stalling invisibly (or crashing a windowed build)."""
    from funasr import AutoModel

    with _sensevoice_download_progress(_broadcast_sensevoice_progress):
        return AutoModel(
            model=model_dir,
            vad_model=vad_dir, vad_kwargs={"max_single_segment_time": 30000},
            device="cpu", disable_update=True,
        )


def get_sensevoice_model(on_progress=None):
    """Loads SenseVoiceSmall once per process and caches it — shared by
    the Transcribe tab's batch worker and the Live Transcription tab.

    Load order: an already-complete local copy (HF or legacy-ModelScope
    layout) is used directly via its path — instant, zero network. Missing
    models download from Hugging Face first (fast CDN worldwide except
    mainland China), and anything that fails — download or a local copy
    that won't construct — falls back to funasr's own ModelScope flow,
    which both serves mainland users and self-repairs a broken local copy
    by re-fetching missing/changed files.

    Loaded with an fsmn-vad front-end (FunASR's own documented pairing for
    SenseVoice), not the bare model. Measured directly: without it, a 10-
    minute clip took ~175-193s (self-attention over the whole un-chunked
    sequence scales quadratically with length) — with it, ~22s for the same
    clip, same completeness, because VAD splits the audio into bounded
    segments first. The bare model isn't just slower on long audio, it's a
    real crash/hang risk on genuinely long recordings (an hour-long meeting
    would be an attention matrix over the entire sequence at once) — this
    is why FunASR's own SenseVoice examples always pair it with a VAD model
    rather than feeding raw long audio directly to the encoder.

    on_progress(downloaded_bytes, total_bytes), if given, fires while any
    part of the model is actually being downloaded (SenseVoiceSmall plus
    its fsmn-vad front-end) — it's never called at all if everything was
    already cached on disk, so a caller can safely treat "no calls" as
    "this was just a fast local load", same distinction whisper's own
    downloading-vs-loading status already makes.

    Thread-safe / single-flight: the Live tab preloads this in the
    background as soon as its tab is opened, while Start Recording (or a
    batch job) can also trigger a load — without the lock, two concurrent
    callers would both kick off a full download/construction. The second
    caller here just blocks until the first finishes, then gets the same
    cached instance, instead of duplicating the work.

    That single-flight lock has a catch: only the caller that actually
    acquires it and does the work gets to pass its on_progress into the
    download — a second caller blocked on the lock would otherwise sit
    there with its own progress callback never firing at all, even though
    real work is happening on its behalf (this shipped as a bug: opening
    the Live tab starts a background preload, and starting a Transcribe-tab
    job with SenseVoice checked while that's still in flight would show
    "loading" frozen the whole time — downloading for real, just reporting
    to nobody). Every caller with a real on_progress registers itself in
    _SENSEVOICE_PROGRESS_LISTENERS *before* attempting the lock, and
    whichever caller ends up doing the actual download broadcasts to all
    of them via _broadcast_sensevoice_progress, not just its own callback.
    """
    model = _SENSEVOICE_CACHE.get("model")
    if model is not None:
        return model
    if on_progress is not None:
        with _SENSEVOICE_LISTENERS_LOCK:
            _SENSEVOICE_PROGRESS_LISTENERS.append(on_progress)
    try:
        with _SENSEVOICE_LOCK:
            model = _SENSEVOICE_CACHE.get("model")
            if model is None:
                paths = sensevoice_local_paths()
                if paths is None:
                    try:
                        paths = _download_sensevoice_hf(
                            _broadcast_sensevoice_progress)
                    except Exception:
                        settings.log_exception(
                            "SenseVoice HF download failed, trying ModelScope:")
                if paths is not None:
                    try:
                        model = _construct_sensevoice(*paths)
                    except Exception:
                        settings.log_exception(
                            "SenseVoice local copy failed to load,"
                            " re-downloading via ModelScope:")
                if model is None:
                    # Last resort AND the mainland-China path (HF is blocked
                    # there): funasr's own ModelScope flow. Also the self-
                    # repair route — modelscope revalidates an existing local
                    # copy and re-fetches whatever is missing or changed.
                    from funasr import AutoModel

                    with _sensevoice_download_progress(_broadcast_sensevoice_progress):
                        model = AutoModel(
                            model="iic/SenseVoiceSmall",
                            vad_model="fsmn-vad",
                            vad_kwargs={"max_single_segment_time": 30000},
                            device="cpu", disable_update=True,
                        )
                _SENSEVOICE_CACHE["model"] = model
            return model
    finally:
        if on_progress is not None:
            with _SENSEVOICE_LISTENERS_LOCK:
                try:
                    _SENSEVOICE_PROGRESS_LISTENERS.remove(on_progress)
                except ValueError:
                    pass


def sensevoice_transcribe(audio, language="auto"):
    """Runs SenseVoice over `audio` (16kHz mono float32 numpy array).
    Returns (clean_text, detected_language) — detected_language is one of
    SENSEVOICE_LANGUAGES, or "" if the tag couldn't be parsed.

    Deliberately does NOT use funasr's rich_transcription_postprocess: raw
    output looks like "<|en|><|SAD|><|Speech|><|withitn|>hello there", and
    that helper renders the emotion tag as an emoji glyph — fine for a chat
    UI, wrong for dictation. We just strip every <|...|> tag and keep the
    plain (already ITN-normalized) text.
    """
    model = get_sensevoice_model()
    res = model.generate(
        input=audio, cache={}, language=(language or "auto"),
        use_itn=True, batch_size_s=60, merge_vad=True, merge_length_s=15,
    )
    raw = (res[0].get("text", "") if res else "") or ""
    text = _SENSEVOICE_TAG_RE.sub("", raw).strip()
    m = re.match(r"<\|(\w+)\|>", raw)
    detected = m.group(1) if m and m.group(1) in SENSEVOICE_LANGUAGES else ""
    return text, detected


_VAD_CACHE = {}
_VAD_LOCK = threading.Lock()

# Paragraph grouping. PARAGRAPH_GAP_S is shared by both engines: a real
# silence gap longer than this always starts a new paragraph. Beyond that,
# whisper (build_text() further down) and batch SenseVoice
# (sensevoice_transcribe_timed) diverge on purpose:
#
# Whisper's own decode already breaks its output into short, linguistically
# -informed segments, so PARAGRAPH_MAX_SPAN_S there just chooses among
# boundaries the model already picked — that reliably lands near a natural
# seam even short of a full pause, so a plain duration cap looks fine.
#
# SenseVoice's timestamps instead come from fsmn-vad, which has no concept
# of language at all — telling it "never emit a segment longer than N
# seconds" makes it chop continuous speech at the wall clock, mid-sentence,
# with no awareness of where a sentence actually ends. That produced the
# exact symptom this was built to avoid: the back half of one sentence
# reading as an unrelated new paragraph. SENSEVOICE_SOFT_TARGET_S /
# SENSEVOICE_HARD_MAX_S below replace that hard cap with a target-plus-seam
# rule (see sensevoice_transcribe_timed) that only breaks at a duration
# boundary as an absolute last resort.
PARAGRAPH_GAP_S = 2.0
PARAGRAPH_MAX_SPAN_S = 20.0        # whisper only — see note above
SENSEVOICE_SOFT_TARGET_S = 20.0    # aim for roughly this long...
SENSEVOICE_HARD_MAX_S = 32.0       # ...but never exceed this without a seam

# Sentence-final punctuation SenseVoice's own ITN may place at a true
# sentence boundary — covers all 5 SenseVoice languages (full-width for
# zh/yue/ja, half-width also common for ko and always for en). Used to
# prefer breaking a paragraph at the end of a sentence once the soft
# target is reached, rather than wherever the clock happens to be.
_SENTENCE_END_CHARS = "。！？.!?"


def join_pieces(prefix, addition, lang):
    """Glues two independently-decoded pieces of the same paragraph back
    together — no separator for CJK languages (SenseVoice's own decode
    never inserts spaces between adjacent CJK words, so adding one here
    would look wrong), a single space otherwise. Shared by batch
    SenseVoice's per-segment paragraph assembly and the Live tab's
    continuation-commit joining (live_transcription.py) — both are
    stitching separately-decoded pieces of one spoken thought back
    together and need to do it the same way."""
    addition = (addition or "").strip()
    if not addition:
        return prefix
    if not prefix:
        return addition
    sep = "" if (lang or "") in ("zh", "yue", "ja", "ko") else " "
    return f"{prefix}{sep}{addition}"


def get_vad_model():
    """Loads FunASR's fsmn-vad standalone, once per process. Its model
    files are the same ones already downloaded as SenseVoice's front-end,
    so this never triggers a new download — it exists because AutoModel's
    bundled-VAD pipeline (get_sensevoice_model) never exposes the segment
    times it computes internally (verified: merge_vad=False still returns
    a single {key, text} result), and paragraph timestamps need them.

    max_single_segment_time is set to SENSEVOICE_SOFT_TARGET_S here, at
    construction — it's a constructor-only parameter of FunASR's VAD model
    (read once into self.vad_opts in __init__; confirmed by reading the
    source, since passing it to generate() instead is silently accepted
    and does nothing).

    Deliberately NOT set to SENSEVOICE_HARD_MAX_S: found on real audio
    that VAD's own natural silence detection sometimes produces one
    genuinely long segment on its own (no cap involved — the speaker just
    never paused long enough for VAD's ~800ms threshold to trigger), and
    sensevoice_transcribe_timed's pass 2 can only check/break *between*
    whatever segments VAD hands it. With the cap set to HARD_MAX itself,
    one such oversized segment could jump a paragraph's span from just
    under the cap straight past it in a single step — e.g. observed
    32s + 28s = 60s, a 28-second overshoot past a 32s ceiling that was
    supposed to be absolute. Capping at the smaller SOFT_TARGET instead
    keeps every piece pass 2 sees small enough that it always gets a
    chance to enforce SENSEVOICE_HARD_MAX_S close to where it's actually
    set, at the cost of a little more decode overhead — a fine trade for
    the batch tab, where latency was never the constraint.

    This still isn't the primary segmentation mechanism — VAD's own
    silence detection produces far shorter natural segments than either
    number in the vast majority of real speech, which is exactly what
    pass 2 needs to make sentence-aware grouping decisions. This cap only
    matters when a stretch of speech has no detectable pause at all for
    SENSEVOICE_SOFT_TARGET_S straight."""
    model = _VAD_CACHE.get("model")
    if model is not None:
        return model
    with _VAD_LOCK:
        model = _VAD_CACHE.get("model")
        if model is None:
            from funasr import AutoModel

            # Resolved local directory when available — which is always, in
            # practice: this only runs after get_sensevoice_model succeeded,
            # so the files are on disk. A path keeps funasr's hub code (and
            # its network calls) entirely out of the way; the "fsmn-vad"
            # hub alias remains only as a repair fallback.
            paths = sensevoice_local_paths()
            model = AutoModel(
                model=paths[1] if paths else "fsmn-vad",
                device="cpu", disable_update=True,
                max_single_segment_time=int(SENSEVOICE_SOFT_TARGET_S * 1000),
            )
            _VAD_CACHE["model"] = model
        return model


def sensevoice_transcribe_timed(audio, language="auto", on_progress=None):
    """SenseVoice transcription with per-paragraph start times.

    Two passes. First, fsmn-vad segments the whole clip on real silence
    (its own default ~800ms end-silence threshold, not a fixed duration
    cap), and each segment is decoded independently. This is not a
    quality tradeoff versus the simple merged path (sensevoice_transcribe
    with merge_vad=True): that path already decodes per raw VAD segment
    internally and only differs in how the text gets glued back together
    — spot-checked against it on real audio, same text quality either
    way. Decoding independently here just gives visibility into each
    segment's own text and timing, which the second pass needs.

    Second, the decoded (start, end, text) segments are assembled into
    paragraphs: a gap over PARAGRAPH_GAP_S is always a real pause and
    always breaks; short of that, a paragraph keeps absorbing segments
    past SENSEVOICE_SOFT_TARGET_S only until one ends in sentence-final
    punctuation (see _SENTENCE_END_CHARS) — that's treated as a natural
    seam and breaks there, so a paragraph boundary lands at the end of a
    sentence rather than wherever a fixed-duration clock happens to be;
    if no such seam turns up before SENSEVOICE_HARD_MAX_S, a break is
    forced anyway, as an absolute ceiling. This replaces the old design's
    failure mode: a hard wall-clock cut landing mid-sentence, so the back
    half of one sentence read as an unrelated new paragraph.

    Decoding per-segment (rather than one merge_vad=True call) is also
    what makes real progress reporting possible at all: SenseVoice has no
    per-token/per-chunk callback of its own, so without visibility into
    each segment as it's decoded, a caller has nothing to report progress
    from between "started" and "done" (the original bug this was built
    to fix — SenseVoice jobs in the Transcribe tab sat at 0% and jumped
    straight to done).

    on_progress(pct), if given, is called after each segment is decoded —
    pct is 0-99, the fraction of the audio's total duration covered so
    far by time position (not by segment count, so it stays accurate
    even when some segments decode to no text and get dropped). Same
    convention _transcribe_whisper's per-segment pct already uses (100 is
    left for the caller to report once saving is actually done).

    Returns (paragraphs, detected) where paragraphs is [(start_seconds,
    text), ...] with empty decodes dropped, and detected is the first
    non-empty segment's language tag ("" if none parsed).
    """
    vad = get_vad_model()
    res = vad.generate(input=audio, cache={})
    segments_ms = res[0].get("value", []) if res else []
    if not segments_ms:
        return [], ""

    duration = len(audio) / SENSEVOICE_SAMPLE_RATE

    # -- pass 1: decode every VAD segment independently --------------------
    decoded, detected = [], ""  # decoded: [(start_s, end_s, text), ...]
    for start_ms, end_ms in segments_ms:
        piece = audio[int(start_ms / 1000 * SENSEVOICE_SAMPLE_RATE):
                      int(end_ms / 1000 * SENSEVOICE_SAMPLE_RATE)]
        text, piece_lang = sensevoice_transcribe(piece, language)
        text = text.strip()
        if text:
            if not detected and piece_lang:
                detected = piece_lang
            decoded.append((start_ms / 1000.0, end_ms / 1000.0, text))
        if on_progress and duration > 0:
            on_progress(int(min(99, end_ms / 1000.0 / duration * 100)))

    # -- pass 2: assemble into sentence-aware paragraphs --------------------
    # Below SENSEVOICE_SOFT_TARGET_S, nothing but a real pause ever breaks
    # a paragraph — no seam-hunting yet. Once a fold-in pushes the span
    # past that target, the paragraph keeps absorbing segments until one
    # of them ends in sentence-final punctuation (checked on that segment
    # itself, not on whatever text happened to precede it — a segment
    # that both crosses the target *and* completes a sentence is exactly
    # the natural place to stop), and breaks right there. If none ever
    # does before SENSEVOICE_HARD_MAX_S, a break is forced anyway, as an
    # absolute ceiling — the only path left that can land mid-sentence,
    # and only after ~32s of speech with no detectable sentence end at
    # all. This is what actually fixes the old failure mode: a fixed
    # 20-second wall-clock cut landing mid-sentence regardless of context,
    # making the back half of one sentence read as an unrelated new
    # paragraph.
    effective_lang = detected or language
    paragraphs = []
    cur_start = cur_end = None
    cur_text = ""
    for start_s, end_s, text in decoded:
        if cur_start is None:
            cur_start, cur_end, cur_text = start_s, end_s, text
            continue

        if start_s - cur_end > PARAGRAPH_GAP_S:
            # A real pause always breaks, regardless of span or seams.
            paragraphs.append((cur_start, cur_text))
            cur_start, cur_end, cur_text = start_s, end_s, text
            continue

        cur_text = join_pieces(cur_text, text, effective_lang)
        cur_end = end_s
        span = cur_end - cur_start
        at_seam = text.rstrip()[-1:] in _SENTENCE_END_CHARS
        if span > SENSEVOICE_HARD_MAX_S or (span > SENSEVOICE_SOFT_TARGET_S and at_seam):
            paragraphs.append((cur_start, cur_text))
            cur_start = cur_end = None
            cur_text = ""
    if cur_start is not None:
        paragraphs.append((cur_start, cur_text))

    return paragraphs, detected


def detect_language_fast(model, audio):
    """Cheap language ID using whisper's encoder plus a single language-token
    classification over ~30s of audio — much cheaper than a full transcribe
    pass. Used to decide, for "auto" + SenseVoice-preferred, whether a file
    should be routed to SenseVoice at all. Returns an ISO code, or "" if
    detection wasn't confident or failed."""
    try:
        language, probability, _all = model.detect_language(
            audio, vad_filter=True, language_detection_segments=1)
        return language if probability >= 0.5 else ""
    except Exception:
        return ""


def whisper_local_model_dir(size):
    """Snapshot folder of a fully-downloaded whisper model under
    MODELS_DIR, or None. model.bin is the payload — huggingface_hub only
    materializes it in a snapshot once its transfer completed, so its
    presence means the copy is usable. Passing this directory (rather than
    the model alias) to WhisperModel skips faster_whisper's hub lookup
    entirely: instant, guaranteed-offline warm starts with no network
    attempt to time out on."""
    snapshots = os.path.join(
        settings.MODELS_DIR, whisper_repo_dirname(size), "snapshots"
    )
    if not os.path.isdir(snapshots):
        return None
    for snap in sorted(os.listdir(snapshots)):
        candidate = os.path.join(snapshots, snap)
        if os.path.isfile(os.path.join(candidate, "model.bin")):
            return candidate
    return None


def model_is_downloaded(size):
    return whisper_local_model_dir(size) is not None


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for i in range(2, 1000):
        candidate = f"{base} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return f"{base} ({os.getpid()}){ext}"


def build_text(segments):
    """Groups whisper segments into paragraphs on pauses longer than
    PARAGRAPH_GAP_S, or once a paragraph's span exceeds
    PARAGRAPH_MAX_SPAN_S (same rule sensevoice_transcribe_timed uses, so
    both engines paragraph the same way and get comparable timestamp
    density). Returns (text, times): one start-time (seconds, from the
    paragraph's first segment) per paragraph, for the Edit tab's clickable
    timestamps."""
    paragraphs, times, current, start, prev_end = [], [], [], None, None
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        gap_broke = current and prev_end is not None and seg.start - prev_end > PARAGRAPH_GAP_S
        span_broke = current and start is not None and seg.end - start > PARAGRAPH_MAX_SPAN_S
        if gap_broke or span_broke:
            paragraphs.append(" ".join(current))
            times.append(start)
            current, start = [], None
        if start is None:
            start = seg.start
        current.append(text)
        prev_end = seg.end
    if current:
        paragraphs.append(" ".join(current))
        times.append(start)
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else ""), times


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

    def __init__(self, jobs, quality, language_code, sensevoice_preferred,
                 output_folder, events, traditional_chinese=False):
        super().__init__(daemon=True)
        self.jobs = jobs
        self.quality = quality  # "fast" | "balanced" | "accurate"
        self.language_code = language_code  # None (auto) or an ISO code
        self.sensevoice_preferred = sensevoice_preferred
        self.output_folder = output_folder
        self.events = events
        self.traditional_chinese = traditional_chinese
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    # -- helpers ------------------------------------------------------------

    def _emit(self, *event):
        self.events.put(event)

    def _load_model(self):
        size = QUALITY_MODELS[self.quality]
        os.makedirs(settings.MODELS_DIR, exist_ok=True)
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

        local_dir = whisper_local_model_dir(size)
        if local_dir is not None:
            self._emit("line", "loading", {"quality": self.quality})
            try:
                return self._construct_whisper(local_dir)
            except Exception:
                # The on-disk copy exists but won't load (a file deleted or
                # corrupted since whisper_local_model_dir's cheap check) —
                # fall through to the download below, which re-fetches
                # whatever's missing and tries once more, instead of
                # failing the whole batch over a broken cache.
                settings.log_exception(
                    "Local whisper model failed to load, re-downloading:")

        self._emit("line", "downloading",
                   {"quality": self.quality, "size_mb": MODEL_DOWNLOAD_MB[size], "pct": 0})

        def on_pct(pct):
            # Displayed pct is capped at 99: the aggregate total only
            # includes files whose transfer has started, and a finished
            # file's bar snaps to its own total — so the raw ratio can
            # read 100% minutes before the download really completes
            # (observed in the wild: "100%" sitting there while
            # model.bin was still crawling in). Real completion is the
            # switch to "loading" below, after snapshot_download
            # actually returns.
            self._emit("line", "downloading", {
                "quality": self.quality, "size_mb": MODEL_DOWNLOAD_MB[size],
                "pct": min(pct, 99),
            })

        try:
            import huggingface_hub

            model_path = huggingface_hub.snapshot_download(
                WHISPER_MODEL_REPOS[size],
                cache_dir=settings.MODELS_DIR,
                allow_patterns=_WHISPER_ALLOW_PATTERNS,
                tqdm_class=make_progress_tqdm_class(
                    monotonic_pct_reporter(on_pct)),
            )
        except Exception:
            settings.log_exception("Model download failed:")
            self._emit("line", "download_failed", {})
            for job in self.jobs:
                self._emit("job", job.index, "failed_model", {}, None)
            return None
        self._emit("line", "loading", {"quality": self.quality})

        try:
            return self._construct_whisper(model_path)
        except Exception:
            settings.log_exception("Model load failed:")
            self._emit("line", "load_failed", {})
            for job in self.jobs:
                self._emit("job", job.index, "failed_model", {}, None)
            return None

    @staticmethod
    def _construct_whisper(model_dir):
        from faster_whisper import WhisperModel  # deferred: heavy import

        return WhisperModel(
            model_dir,
            device="cpu",
            compute_type="int8",
            cpu_threads=max(1, (os.cpu_count() or 4) - 1),
        )

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

            from faster_whisper.audio import decode_audio

            audio = decode_audio(job.path, sampling_rate=SENSEVOICE_SAMPLE_RATE)

            sv_language = self._resolve_sensevoice_language(model, audio)
            if sv_language and self._try_sensevoice(job, audio, sv_language):
                return

            self._transcribe_whisper(model, job, audio)

        except Exception as exc:
            settings.log_exception(f"Transcription failed for {job.path}:")
            self._emit(
                "job", job.index, "failed_error", {"error": _short_error(exc)}, None
            )

    def _resolve_sensevoice_language(self, model, audio):
        """Returns an ISO code to route this job to SenseVoice with, or ""
        to use whisper. An explicit language pick is checked directly
        against SENSEVOICE_LANGUAGES; "auto" runs a cheap upfront whisper
        language-ID pass first, since SenseVoice itself isn't safe to try
        blind on a language outside its known 5 (see detect_language_fast)."""
        if not (self.sensevoice_preferred and sensevoice_is_available()):
            return ""
        if self.language_code:
            return self.language_code if self.language_code in SENSEVOICE_LANGUAGES else ""
        detected = detect_language_fast(model, audio)
        return detected if detected in SENSEVOICE_LANGUAGES else ""

    def _try_sensevoice(self, job, audio, language):
        """Returns True once SenseVoice has produced and saved a result for
        this job; False to fall back to whisper (SenseVoice unavailable,
        model download failed, or a runtime error)."""
        showed_load_status = False
        try:
            if not sensevoice_model_loaded():
                showed_load_status = True
                self._emit("line", "sensevoice_loading", {})

                def on_pct(pct):
                    self._emit("line", "sensevoice_downloading",
                               {"size_mb": SENSEVOICE_DOWNLOAD_MB, "pct": pct})
                    if pct >= 100:
                        # Byte transfer is done, but constructing the model
                        # (loading weights into memory) is a separate,
                        # unmeasurable step that still takes real time —
                        # switch back to "loading" so 100% doesn't just sit
                        # there looking stuck for the next 10-30s.
                        self._emit("line", "sensevoice_loading", {})

                get_sensevoice_model(monotonic_pct_reporter(on_pct))  # populates the cache
                # Load is over — put the status line back on "Transcribing…"
                # (what _run set before this job), or it would keep reading
                # "Loading SenseVoice model…" for the rest of the batch.
                self._emit("line", "transcribing", {})

            def on_decode_pct(pct):
                self._emit("job", job.index, "transcribing", {"pct": pct}, pct)

            paragraphs, detected = sensevoice_transcribe_timed(
                audio, language, on_progress=on_decode_pct)
        except Exception:
            settings.log_exception("SenseVoice failed, falling back to whisper:")
            if showed_load_status:
                # Same stale-line problem on the failure path: whisper takes
                # over below, so don't leave "loading SenseVoice" up.
                self._emit("line", "transcribing", {})
            return False
        effective_lang = detected or language
        parts = [(start, apply_chinese_conversion(text, effective_lang,
                                                  self.traditional_chinese))
                 for start, text in paragraphs]
        text = "\n\n".join(t for _s, t in parts)
        times = [s for s, _t in parts]
        lang_for_status = detected if (self.language_code is None and detected) else None
        self._save_result(job, (text + "\n") if text else "", lang_for_status, times)
        return True

    def _transcribe_whisper(self, model, job, audio):
        segments_iter, info = model.transcribe(
            audio,
            language=self.language_code,
            beam_size=5,
            condition_on_previous_text=False,
            # When auto-detecting, re-run language detection for every
            # segment instead of committing the whole file to a single
            # guess from the first ~30s. Without this, a short ambiguous
            # opening (or genuinely mixed-language audio) can lock the
            # entire transcription into the wrong language, producing
            # text that is not just mistranslated but phonetically
            # nonsensical.
            multilingual=self.language_code is None,
        )
        duration = info.duration or (len(audio) / SENSEVOICE_SAMPLE_RATE)

        segments = []
        for seg in segments_iter:
            if self.cancel_event.is_set():
                self._emit("job", job.index, "cancelled", {}, None)
                return
            segments.append(seg)
            if duration > 0:
                pct = int(min(99, seg.end / duration * 100))
                self._emit("job", job.index, "transcribing", {"pct": pct}, pct)

        text, times = build_text(segments)
        text = apply_chinese_conversion(
            text, self.language_code or info.language, self.traditional_chinese)
        lang_for_status = info.language if (self.language_code is None and info.language) else None
        self._save_result(job, text, lang_for_status, times)

    def _save_result(self, job, text, lang_for_status, times=None):
        stem = os.path.splitext(os.path.basename(job.path))[0]
        folder = self.output_folder or os.path.dirname(job.path)
        try:
            import docx_export  # lazy: avoids an import cycle

            output_path, _kind = docx_export.save_transcript(text, folder, stem)
        except (PermissionError, OSError):
            settings.log_exception(f"Write failed in {folder}:")
            self._emit("job", job.index, "failed_write", {}, None)
            return

        if times and text.strip():
            import timestamps

            timestamps.save_sidecar(output_path, times)
        self._emit("saved", job.index, output_path)
        if not text.strip():
            self._emit("job", job.index, "done_no_speech", {}, 100)
        elif lang_for_status:
            self._emit("job", job.index, "done_lang", {"code": lang_for_status}, 100)
        else:
            self._emit("job", job.index, "done", {}, 100)
