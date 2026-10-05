"""Live Translate: microphone speech -> SenseVoice transcript -> Hy-MT2-1.8B
translation, sentence by sentence, with as little delay as the machine allows.

Design in one paragraph. The mic is captured exactly like the Live
Transcription tab (same helpers, same SenseVoice commit-on-pause idea), but
instead of committing whole paragraphs the worker hands out *sentences* as
soon as they are finished (SentenceStream), and a second thread translates
them one by one with Hy-MT2-1.8B. SenseVoice is cheap (~0.05x real time) and
the translation model is the bottleneck, so the two run on separate threads
and the CPU is split between them by a one-off self-benchmark (calibrate())
instead of any number tuned for one laptop. Three profiles trade delay for
accuracy (PROFILES): latency / balanced / accuracy.

UI-agnostic: talks to the app only through the `events` queue, like
live_transcription.py and llm.py:

    ("lt_status", key, detail)               -> status line
    ("lt_segment", id, source, translation, state)
                                             state: "pending" | "streaming" | "final"
    ("lt_preview", source_fragment, partial_translation)
                                             the sentence being spoken: its words so far,
                                             and (Latency mode) the translation of the
                                             clauses already finished
    ("lt_metrics", {"mt_ms", "e2e_ms", "queue"})
    ("lt_calibrated", calibration_dict)      -> app persists it in prefs
    ("lt_stopped",)                          -> worker has fully wound down

Nothing is recorded or saved to disk (v0 of the tab, by request).
"""

import collections
import gc
import os
import queue
import re
import threading
import time

import numpy as np

import i18n
import live_transcription as lt
import llm
import settings
import transcriber

SAMPLE_RATE = lt.SAMPLE_RATE
SILENCE_RMS = lt.SILENCE_RMS
N_CTX = 2048                 # short sentences + a little context; 8192 would waste RAM
MAX_NEW_TOKENS_FLOOR = 48
CALIBRATION_VERSION = 2
SLOW_MT_MS = 3500            # per-sentence translate time above which we warn
LAG_DEGRADE_QUEUE = 3        # this many untranslated sentences -> drop context/drafts
HISTORY_KEEP = 6

PROFILE_KEYS = ["latency", "accuracy"]
PROFILE_ALIASES = {"balanced": "accuracy"}      # the old middle mode became "accuracy"
PROFILES = {
    # step_s: ASR tick; pause_s: silence that ends a sentence group;
    # max_uncommitted_s: hard cap on re-decoded audio (ASR cost grows with
    # the square of tail length); stable_ticks: how many consecutive ticks a
    # finished sentence must read identically before it is handed on;
    # context_pairs: previous (source, translation) pairs shown to the
    # translator; chunks: translate each clause of the sentence while it is
    # still being spoken, so most of the translation exists when it ends.
    # confirm_after_s: a sentence that already has this much newer speech
    # after it is trusted without waiting for another identical decode.
    "latency": dict(step_s=0.5, pause_s=0.5, max_uncommitted_s=12.0,
                    stable_ticks=1, context_pairs=0, chunks=True, confirm_after_s=0.0),
    "accuracy": dict(step_s=0.75, pause_s=0.8, max_uncommitted_s=15.0,
                     stable_ticks=2, context_pairs=1, chunks=False, confirm_after_s=1.5),
}
MIN_TAIL_S = 0.4
MIN_PIN_TAIL_S = 2.0         # language detection on <2 s clips is unreliable

# Speech detection is relative to the room, not a fixed number: a quiet voice
# or a low mic gain (RMS ~0.01) sits below any threshold that is safe for a
# noisy room, and the old fixed 0.012 turned such speakers into "silence"
# whose audio was then thrown away.
FRAME_SAMPLES = 480                    # 30 ms
MIN_SPEECH_THR, MAX_SPEECH_THR = 0.003, 0.03
SPEECH_OVER_FLOOR = 3.0
MIN_SPEECH_FRAMES = 3
FLOOR_HISTORY_S = 12.0
FLOOR_RISE_PER_TICK = 1.02             # the noise-floor estimate may only creep upward
TARGET_SPEECH_RMS = 0.08               # quiet voices are amplified toward this before decoding
MAX_GAIN = 12.0

# Chunk translation (Latency mode): a clause is translated as soon as it is
# finished and stable, not when the whole sentence is.
CHUNK_MIN_UNITS = 4          # a chunk is at least this many words (a CJK character counts half)
CHUNK_FORCE_UNITS = 14       # an unpunctuated run this long is split anyway...
CHUNK_FORCE_AT = 8           # ...after this many words
CLAUSE_END = ",;:\uff0c\uff1b\uff1a\u3001"
SENTENCE_END_STRIP = " \u3002.!?\uff01\uff1f\u2026"

# A detected source language that already is the chosen target needs no
# translation (SenseVoice code -> LLM_TARGET_LANGUAGES keys it satisfies).
SAME_LANGUAGE = {"en": {"en"}, "ja": {"ja"}, "ko": {"ko"},
                 "zh": {"zh-hans", "zh-hant"}}


# ---------------------------------------------------------------- sentences

_CLOSERS = "」』”’)）\"'"
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "st", "no",
                  "e.g", "i.e", "u.s", "a.m", "p.m"}


def _is_cjk(ch):
    o = ord(ch)
    return (0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF
            or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF)


def _norm(s):
    return re.sub(r"[\W_]+", "", s.lower())


def is_meaningful(s):
    """Filters the ghost fragments SenseVoice sometimes emits on noise."""
    n = _norm(s)
    return len(n) >= 2 or (len(n) == 1 and _is_cjk(n))


def join_text(a, b):
    """Joins two text pieces: no space across CJK boundaries."""
    if not a:
        return b
    if not b:
        return a
    if _is_cjk(a[-1]) or _is_cjk(b[0]):
        return a + b
    return a + " " + b


def split_sentences(text):
    """-> (complete_sentences, trailing_fragment). A sentence is complete when
    it ends with . ! ? (followed by whitespace/end) or 。！？ (anywhere)."""
    sentences, start, i, n = [], 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "。！？":
            j = i + 1
            while j < n and (text[j] in "。！？" or text[j] in _CLOSERS):
                j += 1
            sentences.append(text[start:j].strip())
            start = i = j
            continue
        if ch in ".!?":
            j = i + 1
            while j < n and (text[j] in ".!?" or text[j] in _CLOSERS):
                j += 1
            boundary = j >= n or text[j].isspace()
            if boundary and ch == ".":
                # "Dr. Smith" is not a sentence end ("3.5" never reaches here:
                # a digit follows the dot, not whitespace). Single letters are
                # deliberately NOT treated as initials: "Plan B." ends sentences
                # far more often in speech than "J. Smith" appears.
                word = re.search(r"([A-Za-z][A-Za-z.]*)$", text[start:i])
                if word and word.group(1).lower() in _ABBREVIATIONS:
                    boundary = False
            if boundary:
                sentences.append(text[start:j].strip())
                start = j
            i = j
            continue
        i += 1
    return [s for s in sentences if s], text[start:].strip()


_TERMINATORS = set(".!?。！？")
CUT_MARGIN_S = 0.12      # keep this much clear of the next word's onset
WORD_S, CJK_CHAR_S = 0.35, 0.2   # typical spoken length, to turn onsets into word ends


def _is_terminator_token(word):
    return bool(word) and all(c in _TERMINATORS or c in _CLOSERS for c in word) \
        and any(c in _TERMINATORS for c in word)


def _is_punct_token(word):
    return not any(c.isalnum() for c in word)


def _join_tokens(tokens):
    text = ""
    for word, _ in tokens:
        if _is_punct_token(word):
            text += word
        else:
            text = join_text(text, word)
    return text


def _cut_point(prev_end, next_onset):
    """Middle of the silence between two words, kept clear of both."""
    return max(prev_end + 0.05, min((prev_end + next_onset) / 2, next_onset - CUT_MARGIN_S))


def segment(text, tokens, gap_s):
    """Splits one SenseVoice decode into finished units plus an unfinished
    fragment. Returns (units, fragment) where units is a list of
    (text, cut_seconds_or_None, closed_by_pause).

    With token timestamps a unit ends at sentence punctuation OR at a silence
    of at least gap_s between two words - the decoder often glues sentences
    together with commas when the speaker clearly paused, and the pause is
    the more reliable signal. cut_seconds is where the audio can be cut
    (middle of the silence after the unit); None means unknown. Without
    usable tokens (missing, or they do not spell the same text) this falls
    back to splitting the text on punctuation alone."""
    if not tokens or _norm("".join(w for w, _ in tokens)) != _norm(text):
        sentences, frag = split_sentences(text)
        return [(sent, None, False) for sent in sentences], frag

    units, cur = [], []
    prev_end = None
    i, n = 0, len(tokens)
    while i < n:
        word, onset = tokens[i]
        if cur and prev_end is not None and not _is_punct_token(word) \
                and onset - prev_end >= gap_s:
            units.append((_join_tokens(cur), _cut_point(prev_end, onset), True))
            cur = []
        cur.append((word, onset))
        if _is_punct_token(word):
            prev_end = onset
        else:
            prev_end = onset + (CJK_CHAR_S if _is_cjk(word[0]) else WORD_S)
        if _is_terminator_token(word) and not (
                word == "." and i > 0 and tokens[i - 1][0].lower() in _ABBREVIATIONS):
            j = i
            while j + 1 < n and _is_terminator_token(tokens[j + 1][0]):
                j += 1
                cur.append(tokens[j])
            last_onset = tokens[j][1]
            cut = _cut_point(last_onset, tokens[j + 1][1]) if j + 1 < n else None
            units.append((_join_tokens(cur), cut, False))
            cur, prev_end = [], None
            i = j
        i += 1
    return units, _join_tokens(cur) if cur else ""


class SentenceStream:
    """Turns the repeatedly re-decoded text of the uncommitted audio tail into
    a stream of finished sentences, each handed out exactly once.

    update(text, tokens) is called every tick with the full decode of the
    tail. A unit (see segment()) that is followed by more text is released
    once it has read identically for `stable_ticks` consecutive ticks
    (SenseVoice can still revise recent words); one closed by a real pause
    needs a single sighting, the silence is the evidence. The last unit of a
    decode waits for the commit, because the decoder puts a full stop on
    whatever it has heard so far, even mid-sentence. With final=True (a
    pause, the audio cap, or Stop) everything left, including the
    unfinished fragment, is released.

    Returns (released, preview, cut_s). When token timestamps are usable,
    cut_s is the point (seconds into the tail) up to which the audio is now
    consumed and can be dropped, so the next decode starts right after the
    last released unit and never re-reads audio that was already translated.
    Without usable timestamps cut_s is None and released units are skipped
    by index instead."""

    def __init__(self, stable_ticks=2, gap_s=0.8, confirm_after_s=None):
        self.stable_ticks = max(1, stable_ticks)
        self.gap_s = gap_s
        self.confirm_after_s = confirm_after_s
        self.reset()

    def reset(self):
        self.emitted = 0
        self._prev = []
        self._seen = []

    def _needed(self, unit, tail_s):
        """Sightings required before `unit` may be released."""
        _text, cut, by_pause = unit
        if by_pause:
            return 1        # the silence after it is the evidence
        if (self.confirm_after_s is not None and cut is not None
                and tail_s - cut >= self.confirm_after_s):
            return 1        # plenty of newer speech already follows it
        return self.stable_ticks

    def update(self, text, tokens=None, final=False, tail_s=0.0):
        units, frag = segment(text, tokens, self.gap_s)
        complete = [u[0] for u in units]
        seen = []
        for i, sent in enumerate(complete):
            if i < len(self._prev) and _norm(self._prev[i]) == _norm(sent):
                seen.append(self._seen[i] + 1)
            else:
                seen.append(1)
        self._prev, self._seen = complete, seen
        all_parts = complete + ([frag] if frag else [])
        if final:
            out = all_parts[self.emitted:]
            self.reset()
            return out, "", None
        releasable = len(units) if frag else max(0, len(units) - 1)
        i = self.emitted
        out = []
        while i < releasable and self._seen[i] >= self._needed(units[i], tail_s):
            out.append(complete[i])
            i += 1
        cut = units[i - 1][1] if out else None
        preview = ""
        for part in all_parts[i:]:
            preview = join_text(preview, part)
        if cut is not None:
            self.reset()        # the tail restarts after the cut
        else:
            self.emitted = i
        return out, preview, cut


def decode(audio, language):
    """SenseVoice decode of `audio` with token timestamps. Same cleanup as
    transcriber.sensevoice_transcribe; returns (text, detected_language,
    tokens) where tokens is [(word, onset_seconds)] or None if the timestamp
    output is missing or does not line up."""
    model = transcriber.get_sensevoice_model()
    res = model.generate(
        input=audio, cache={}, language=(language or "auto"), use_itn=True,
        batch_size_s=60, merge_vad=True, merge_length_s=15, output_timestamp=True)
    item = res[0] if res else {}
    raw = item.get("text", "") or ""
    text = transcriber._SENSEVOICE_TAG_RE.sub("", raw).strip()
    m = re.match(r"<\|(\w+)\|>", raw)
    detected = m.group(1) if m and m.group(1) in transcriber.SENSEVOICE_LANGUAGES else ""
    words, stamps = item.get("words"), item.get("timestamp")
    tokens = None
    if words and stamps and len(words) == len(stamps):
        try:
            tokens = [(w, st[0] / 1000.0) for w, st in zip(words, stamps)]
        except (TypeError, IndexError):
            tokens = None
    return text, detected, tokens


# ------------------------------------------------------- level / language

def frame_rms(audio):
    """RMS of consecutive 30 ms frames."""
    n = len(audio) // FRAME_SAMPLES
    if n == 0:
        return np.array([lt._rms(audio)], dtype="float64") if len(audio) else np.zeros(0)
    x = audio[:n * FRAME_SAMPLES].astype("float64").reshape(n, FRAME_SAMPLES)
    return np.sqrt(np.mean(x * x, axis=1))


def speech_threshold(floor):
    return min(MAX_SPEECH_THR, max(MIN_SPEECH_THR, SPEECH_OVER_FLOOR * floor))


def has_speech(frames, thr):
    return int(np.count_nonzero(frames > thr)) >= MIN_SPEECH_FRAMES


def trailing_silent(frames, seconds, thr):
    """True when the last `seconds` of audio are quiet (a stray click or two
    does not count as speech)."""
    n = int(seconds * SAMPLE_RATE / FRAME_SAMPLES)
    if n <= 0 or len(frames) < n:
        return False
    return float(np.mean(frames[-n:] > thr)) < 0.08


def speech_gain(frames, thr):
    """Gain that brings quiet speech up to TARGET_SPEECH_RMS (never reduces,
    never beyond MAX_GAIN): SenseVoice and its VAD recognize a whisper-quiet
    mic far worse than the same words at a normal level."""
    voiced = frames[frames > thr]
    if len(voiced) < MIN_SPEECH_FRAMES:
        return 1.0
    rms = float(np.sqrt(np.mean(voiced ** 2)))
    if rms >= TARGET_SPEECH_RMS * 0.7:
        return 1.0
    return float(min(MAX_GAIN, TARGET_SPEECH_RMS / max(rms, 1e-4)))


class LanguageVote:
    """Decides which language to hand SenseVoice when the user chose Auto.
    One mis-detection on a short clip must not lock the whole session onto
    the wrong language (the old behaviour), so a language is only pinned after
    two of the last five detections agree, and only replaced when another one
    wins three of five. A language the user picked is never overridden."""

    def __init__(self, fixed=""):
        self.fixed = fixed or ""
        self.current = self.fixed
        self._votes = collections.deque(maxlen=5)

    def add(self, detected, tail_s, text):
        if self.fixed or not detected or tail_s < MIN_PIN_TAIL_S or not is_meaningful(text):
            return
        self._votes.append(detected)
        lang, n = collections.Counter(self._votes).most_common(1)[0]
        if lang == self.current:
            return
        if (not self.current and n >= 2) or n >= 3:
            self.current = lang


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_CJK_TARGETS = {"zh-hant", "zh-hans", "ja", "ko"}


def looks_like_target(text, target_key):
    """Cheap check that `text` really is already in the target language, so
    that a mis-detected source language cannot make the app skip a needed
    translation and show foreign text as if it were the translation."""
    kana, hangul = bool(_KANA_RE.search(text)), bool(_HANGUL_RE.search(text))
    han = bool(_CJK_RE.search(text))
    if target_key in ("zh-hant", "zh-hans"):
        return han and not kana and not hangul
    if target_key == "ja":
        return kana
    if target_key == "ko":
        return hangul
    if target_key == "en":
        letters = sum(c.isalpha() for c in text)
        ascii_letters = sum(c.isascii() and c.isalpha() for c in text)
        return letters > 0 and ascii_letters / letters > 0.9
    return False


def translation_ok(source, out, target_key):
    """Sanity gate for one translation: rejects empty output, the source
    echoed back, runaway repetition, absurd length and the wrong script."""
    out = (out or "").strip()
    if not out:
        return False
    if len(out) > 6 * len(source) + 30:
        return False
    if re.search(r"(.{3,}?)\1{4,}", out):
        return False
    if _norm(out) == _norm(source) and len(source) > 12:
        return False
    cjk = len(_CJK_RE.findall(out)) + len(_KANA_RE.findall(out)) + len(_HANGUL_RE.findall(out))
    ratio = cjk / max(1, len(out))
    if target_key in _CJK_TARGETS:
        return ratio >= 0.2
    return ratio < 0.3


def cap_split(tokens, start_s=0.0):
    """For a long run with no sentence end and no real pause: the best place
    to split it is the biggest silence between two words in the later part,
    not the very end of the audio (which may be the middle of a word).
    Returns (left_text, cut_seconds, left_token_count) or None."""
    idx = [i for i, (w, on) in enumerate(tokens) if on >= start_s]
    if len(idx) < 4:
        return None
    best, best_gap = None, 0.0
    first, last = idx[0], idx[-1]
    for pos in range(len(idx) - 1):
        i, j = idx[pos], idx[pos + 1]
        w, on = tokens[i]
        nxt_w, nxt_on = tokens[j]
        if _is_punct_token(nxt_w):
            continue
        end = on if _is_punct_token(w) else on + (CJK_CHAR_S if _is_cjk(w[0]) else WORD_S)
        gap = nxt_on - end
        in_later_part = (i - first) >= 0.3 * (last - first)
        if gap >= 0.12 and in_later_part and gap > best_gap:
            best, best_gap = (i, end, nxt_on), gap
    if best is None:
        return None
    i, end, nxt_on = best
    left = tokens[idx[0]:i + 1]
    return _join_tokens(left), _cut_point(end, nxt_on), len(left)


# ------------------------------------------------------- chunked translation

def _units(text):
    """Size of a text in 'words': a CJK character counts half a word."""
    latin = "".join(" " if _is_cjk(ch) else ch for ch in text)
    return len(latin.split()) + sum(1 for ch in text if _is_cjk(ch)) / 2.0


def plan_chunks(text):
    """Splits the sentence being spoken into finished clauses and the part
    still being spoken. Returns (chunks, rest). A clause ends at , ; : (or the
    full-width forms); clauses shorter than CHUNK_MIN_UNITS words are merged
    with the next one (a lone "Well," is not worth a translation), and a long
    run without any punctuation is split after CHUNK_FORCE_AT words."""
    chunks, start, last_cut = [], 0, 0
    for i, ch in enumerate(text):
        if ch in CLAUSE_END:
            piece = text[start:i + 1]
            if _units(piece) >= CHUNK_MIN_UNITS:
                chunks.append(piece.strip())
                start = last_cut = i + 1
    rest = text[last_cut:].strip()
    while " " in rest and _units(rest) >= CHUNK_FORCE_UNITS:
        words = rest.split(" ")
        chunk = " ".join(words[:CHUNK_FORCE_AT])
        chunks.append(chunk)
        rest = " ".join(words[CHUNK_FORCE_AT:]).strip()
    return [c for c in chunks if c], rest


class ChunkTracker:
    """Decides WHEN a clause of the sentence being spoken is ready to be
    translated: it must have read identically on two consecutive ticks (the
    recognizer still revises recent words) and all earlier clauses must have
    been handed out already. update() returns ([(index, text)], stale); stale
    means an earlier clause changed after it was handed out, so everything
    translated for this sentence so far is worthless."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._emitted = []
        self._prev = []

    def update(self, preview):
        chunks, _rest = plan_chunks(preview)
        norm = [_norm(c) for c in chunks]
        stale = False
        for i, done in enumerate(self._emitted):
            if i >= len(norm) or norm[i] != done:
                stale = True
                break
        if stale:
            self._emitted = []
        new = []
        for i in range(len(self._emitted), len(chunks)):
            if i < len(self._prev) and self._prev[i] == norm[i]:
                self._emitted.append(norm[i])
                new.append((i, chunks[i]))
            else:
                break                               # keep the order: wait for this one
        self._prev = norm
        return new, stale


def prefix_split(source, prefix_sources):
    """The final sentence `source` should start with the clauses that were
    translated early. Returns what is left after them (possibly ''), or None
    when the recognizer changed that part too much for the early translation
    to be trusted."""
    pre = _norm("".join(prefix_sources))
    if not pre:
        return source
    count, pos = 0, len(source)
    for idx, ch in enumerate(source):
        if re.match(r"[^\W_]", ch):
            count += 1
            if count >= len(pre):
                pos = idx + 1
                break
    limit = max(2, len(pre) // 12)
    if edit_distance_text(_norm(source[:pos]), pre, limit) > limit:
        return None
    tail = source[pos:].lstrip(" " + CLAUSE_END)
    return tail if re.search(r"[^\W_]", tail) else ""      # only a full stop left: nothing to translate


def edit_distance_text(a, b, limit):
    from wordlist import edit_distance
    return edit_distance(a, b, limit)


def join_translations(a, b, target_key):
    """Joins the translation of the earlier clauses and of the rest. The model
    ends every clause like a sentence ("..."), so the stop is dropped and the
    parts are joined with the target language's own comma."""
    if not a:
        return b
    if not b:
        return a
    a = a.rstrip(SENTENCE_END_STRIP)
    comma = {"zh-hant": "\uff0c", "zh-hans": "\uff0c", "ja": "\u3001"}.get(target_key)
    if a.endswith(tuple(CLAUSE_END + ",")):
        return a + ("" if comma else " ") + b.lstrip()
    return a + (comma if comma else ", ") + b.lstrip()


# ------------------------------------------------------------ translation

def _build_prompt(text, target, context, glossary=()):
    """glossary: [(term, required_translation)] - written in Hy-MT's own
    terminology format (a Chinese header, which the model was trained on and
    which kept every listed term intact in testing, including names that must
    stay untranslated)."""
    head = ""
    if glossary:
        terms = "\n".join(f"{term} \u7ffb\u8bd1\u6210 {translation}" for term, translation in glossary)
        head = f"\u53c2\u8003\u4e0b\u9762\u7684\u7ffb\u8bd1\uff1a\n{terms}\n\n"
    if not context:
        return (head + f"Translate the following text into {target}. Note that you "
                "should only output the translated result without any "
                f"additional explanation:\n\n{text}")
    ctx = "\n".join(src for src, _ in context)
    return (head + f"{ctx}\n\nThe lines above are earlier speech, given only as context "
            f"(do not translate them). Translate the following text into {target}. "
            "Note that you should only output the translated result without any "
            f"additional explanation:\n\n{text}")


class MTEngine:
    """Hy-MT2-1.8B through llama.cpp, owned by one worker for one session."""

    def __init__(self, events=None):
        self.events = events
        self.llama = None
        self.threads = None

    def _emit(self, *event):
        if self.events is not None:
            self.events.put(event)

    def load(self, threads):
        spec = llm.TRANSLATE_LLM["fast"]
        os.makedirs(settings.MODELS_DIR, exist_ok=True)
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        tqdm_class = None
        if not llm.llm_model_is_downloaded(spec):
            from progress import make_progress_tqdm_class
            last = {"pct": -1}

            def on_progress(done, total):
                pct = int(done / total * 100)
                if pct != last["pct"]:
                    last["pct"] = pct
                    self._emit("lt_status", "downloading_mt",
                               {"size": spec["size_gb"], "pct": pct})

            self._emit("lt_status", "downloading_mt", {"size": spec["size_gb"], "pct": 0})
            tqdm_class = make_progress_tqdm_class(on_progress)
        from huggingface_hub import hf_hub_download
        kwargs = {"cache_dir": settings.MODELS_DIR}
        if tqdm_class is not None:
            kwargs["tqdm_class"] = tqdm_class
        path = hf_hub_download(spec["repo"], spec["file"], **kwargs)
        self._emit("lt_status", "loading_mt", {})
        from llama_cpp import Llama
        self.threads = max(1, threads)
        self.llama = Llama(model_path=path, n_ctx=N_CTX, n_threads=self.threads,
                           n_threads_batch=self.threads, n_gpu_layers=0, verbose=False)

    def set_threads(self, n):
        """Changes the thread count of the already-loaded model."""
        n = max(1, int(n))
        try:
            import llama_cpp
            llama_cpp.llama_set_n_threads(self.llama._ctx.ctx, n, n)
            self.threads = n
            return True
        except Exception:
            settings.log_exception("llama_set_n_threads failed:")
            return False

    def close(self):
        self.llama = None
        gc.collect()

    def translate(self, text, target, context=(), abort=None, on_partial=None,
                  temperature=0.0, repeat_penalty=1.05, glossary=()):
        """Translation of one short text (greedy by default). Returns the
        text, or None when `abort()` turned true mid-way."""
        llama = self.llama
        n_in = len(llama.tokenize(text.encode("utf-8"), add_bos=False))
        user = _build_prompt(text, target, context, glossary)
        stream = llama.create_chat_completion(
            messages=[{"role": "user", "content": user}], stream=True,
            max_tokens=max(MAX_NEW_TOKENS_FLOOR, 2 * n_in + 32),
            temperature=temperature, repeat_penalty=repeat_penalty)
        out, last_emit = [], 0.0
        try:
            for part in stream:
                if abort is not None and abort():
                    return None
                piece = part["choices"][0].get("delta", {}).get("content")
                if not piece:
                    continue
                out.append(piece)
                now = time.perf_counter()
                if on_partial is not None and now - last_emit >= 0.08:
                    last_emit = now
                    on_partial("".join(out))
        finally:
            stream.close()      # an abandoned llama.cpp generator must not linger
        return "".join(out).strip()


def calibrate(mt, cores=None):
    """One-off self-benchmark: how many llama.cpp threads does THIS machine
    actually need? Times one fixed sentence at a descending ladder of thread
    counts and picks the smallest count within ~12% of the fastest (past that
    point extra threads only steal CPU from SenseVoice). SenseVoice gets the
    remaining cores. Works the same on 2 cores or 32 - nothing is hardcoded to
    one laptop. Takes a few seconds; the app caches the result."""
    cores = cores or os.cpu_count() or 4
    ladder = sorted({cores, max(1, round(cores * 0.75)), max(1, cores // 2),
                     max(1, cores // 3), max(1, cores // 4)}, reverse=True)
    sample = "Thank you all for coming, we will start the meeting in a few minutes."
    mt.set_threads(ladder[0])
    mt.translate("Hello.", "Chinese")  # warm-up: page the weights in
    timings = {}
    for th in ladder:
        mt.set_threads(th)
        runs = []
        for _ in range(2):  # best of two: one run is too noisy to compare 12% gaps
            t0 = time.perf_counter()
            mt.translate(sample, "Chinese")
            runs.append((time.perf_counter() - t0) * 1000)
        timings[th] = min(runs)
    best = min(timings.values())
    chosen = min((th for th, ms in timings.items() if ms <= best * 1.12))
    # One timing per rung is noisy; never starve the translator below a third
    # of the cores (it also has to keep up while SenseVoice is busy).
    chosen = max(chosen, min(cores, max(2, cores // 3)))
    torch_threads = max(1, min(cores - chosen, 8))
    return {"version": CALIBRATION_VERSION, "cores": cores,
            "llama_threads": chosen, "torch_threads": torch_threads,
            "mt_ms": round(timings[chosen]), "timings": {str(k): round(v) for k, v in timings.items()}}


def calibration_is_valid(cal):
    return (isinstance(cal, dict) and cal.get("version") == CALIBRATION_VERSION
            and cal.get("cores") == (os.cpu_count() or 4)
            and cal.get("llama_threads") and cal.get("torch_threads"))


# ------------------------------------------------------------------ worker

class LiveTranslator(threading.Thread):
    """Mic -> SenseVoice -> sentences -> Hy-MT2-1.8B. start() to begin,
    stop() to end; ("lt_stopped",) is emitted when everything has wound
    down (pending sentences are still translated first)."""

    def __init__(self, events, source_language="", target_key="en", profile="latency",
                 device_name="", traditional_chinese=False, calibration=None, wordlist=None):
        super().__init__(daemon=True)
        self.wordlist = wordlist
        self.events = events
        self.language = source_language or ""
        self.target_key = target_key
        self.target_name = i18n.llm_target_prompt_name(target_key)
        profile = PROFILE_ALIASES.get(profile, profile)
        self.profile_key = profile if profile in PROFILES else "latency"
        self.profile = PROFILES[self.profile_key]
        self.device_name = device_name or ""
        self.traditional_chinese = traditional_chinese
        self.calibration = calibration if calibration_is_valid(calibration) else None
        self.stop_event = threading.Event()
        self.level = 0.0

        self._lock = threading.Lock()
        self._tail_chunks = []
        self._recorded_samples = 0
        self._lang = LanguageVote(self.language)
        self._hist = collections.deque()      # recent raw audio, for the noise floor
        self._hist_samples = 0
        self._floor = None
        self._stream = SentenceStream(self.profile["stable_ticks"], self.profile["pause_s"],
                                      self.profile["confirm_after_s"])
        self._translator = None
        self._stats = {"sentences": 0, "retries": 0, "failed": 0, "restarts": 0}
        self._idle_s = 0.0
        self._torch_prev = None

        self._mt = MTEngine(events)
        self._q = queue.Queue()
        self._history = collections.deque(maxlen=HISTORY_KEEP)
        self._next_id = 1
        self._draft_src = ""
        self._chunker = ChunkTracker()
        self._cstate = self._new_cstate()
        self._pending = 0           # sentences handed to the translator, not finished
        self._pending_lock = threading.Lock()

    # -- public ------------------------------------------------------------

    def stop(self):
        self.stop_event.set()

    @property
    def elapsed_seconds(self):
        with self._lock:
            return self._recorded_samples / SAMPLE_RATE

    # -- internals ---------------------------------------------------------

    def _emit(self, *event):
        self.events.put(event)

    def _fix_target(self, text):
        """Hy-MT2 sometimes answers in Simplified Chinese when asked for
        Traditional (seen in testing); OpenCC makes the choice reliable."""
        if self.target_key == "zh-hant" and text:
            return transcriber.to_traditional(text)
        return text

    def _on_audio(self, indata, _frames, _time_info, _status):
        chunk = indata[:, 0].copy()
        with self._lock:
            self._tail_chunks.append(chunk)
            self._recorded_samples += len(chunk)
            self._hist.append(chunk)
            self._hist_samples += len(chunk)
            while self._hist_samples - len(self._hist[0]) >= FLOOR_HISTORY_S * SAMPLE_RATE:
                self._hist_samples -= len(self._hist.popleft())
        self.level = lt._rms(chunk)

    def _tail(self):
        with self._lock:
            chunks = list(self._tail_chunks)
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def _consume_tail(self, n_samples):
        """Drops the first n_samples of the tail (the audio just decoded and
        handed on) but keeps everything the mic delivered while that decode
        was running - clearing the whole tail instead would silently lose
        up to a decode's worth of speech at every mid-speech cut."""
        with self._lock:
            if not self._tail_chunks:
                return
            audio = np.concatenate(self._tail_chunks)
            rest = audio[max(0, int(n_samples)):]
            self._tail_chunks = [rest] if len(rest) else []

    def _language_for_call(self):
        return self._lang.current or "auto"

    def _speech_threshold(self):
        """Speech/silence level for THIS room: a few times the quietest
        recent audio. The estimate may fall quickly but only creep upward,
        so a speaker who starts talking at once does not raise it."""
        with self._lock:
            chunks = list(self._hist)
        if not chunks:
            return MIN_SPEECH_THR
        frames = frame_rms(np.concatenate(chunks))
        if len(frames) == 0:
            return MIN_SPEECH_THR
        candidate = float(np.percentile(frames, 10))
        if self._floor is None:
            self._floor = candidate
        else:
            self._floor = min(candidate, self._floor * FLOOR_RISE_PER_TICK + 1e-5)
        return speech_threshold(self._floor)

    def run(self):
        try:
            self._run()
        except Exception:
            settings.log_exception("Live translate crashed:")
            self._emit("lt_status", "failed", {})
        finally:
            try:
                import torch
                if self._torch_prev:
                    torch.set_num_threads(self._torch_prev)
            except Exception:
                pass
            self._mt.close()
            self._emit("lt_stopped")

    def _prepare(self):
        """Loads both models, calibrates if needed, warms up. Returns the
        torch thread count to restore afterwards, or None if aborted."""
        import torch
        if not transcriber.sensevoice_is_available():
            self._emit("lt_status", "engine_failed", {})
            return None
        if not transcriber.sensevoice_model_loaded():
            self._emit("lt_status", "loading_asr", {})

            def on_pct(pct):
                self._emit("lt_status", "downloading_asr",
                           {"size_mb": transcriber.SENSEVOICE_DOWNLOAD_MB, "pct": pct})
                if pct >= 100:
                    self._emit("lt_status", "loading_asr", {})

            transcriber.get_sensevoice_model(transcriber.monotonic_pct_reporter(on_pct))
        if self.stop_event.is_set():
            return None

        cores = os.cpu_count() or 4
        self._mt.load(self.calibration["llama_threads"] if self.calibration else max(1, cores // 2))
        if self.stop_event.is_set():
            return None
        if self.calibration is None:
            self._emit("lt_status", "calibrating", {})
            self.calibration = calibrate(self._mt, cores)
            self._emit("lt_calibrated", self.calibration)
            settings.log(f"Live translate calibration: {self.calibration}")
        else:
            self._mt.set_threads(self.calibration["llama_threads"])
        previous = self._torch_prev = torch.get_num_threads()
        torch.set_num_threads(self.calibration["torch_threads"])
        self._mt.translate("Hello.", self.target_name)           # warm the prompt path
        transcriber.sensevoice_transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), "auto")
        if self.calibration["mt_ms"] > SLOW_MT_MS:
            self._emit("lt_status", "slow", {"ms": self.calibration["mt_ms"]})
        return previous

    def _run(self):
        import sounddevice as sd
        self._emit("lt_status", "preparing", {})
        restore = self._prepare()
        if restore is None or self.stop_event.is_set():
            return restore
        try:
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    device=lt._resolve_device(self.device_name),
                                    callback=self._on_audio)
            stream.start()
        except Exception:
            settings.log_exception("Microphone open failed:")
            self._emit("lt_status", "mic_failed", {})
            return restore

        self._start_translator()
        self._emit("lt_status", "listening", {})
        try:
            step = self.profile["step_s"]
            next_t = time.monotonic()
            while True:
                # Fixed-rate ticks: waiting a full step AFTER each decode
                # would stretch every tick by the decode time.
                next_t += step
                if self.stop_event.wait(max(0.0, next_t - time.monotonic())):
                    break
                self._step()
                self._watchdog()
                if next_t < time.monotonic() - step:
                    next_t = time.monotonic()      # fell behind: do not burst
                if self._idle_s >= lt.IDLE_AUTO_STOP_S:
                    self._emit("lt_status", "idle_stop", {"minutes": int(lt.IDLE_AUTO_STOP_S // 60)})
                    break
            # Stop (or idle stop): flush what was said last, then let the
            # translator finish the sentences already handed over.
            self._emit("lt_status", "finishing", {})
            self._step(final=True)
        finally:
            lt.LiveTranscriber._close_stream(stream)
            self._q.put(None)
            if self._translator is not None:
                self._translator.join(timeout=60)
            settings.log(f"Live translate session ended: {self._stats}")
        return restore

    # -- recognition tick --------------------------------------------------

    def _step(self, final=False):
        tail = self._tail()
        tail_dur = len(tail) / SAMPLE_RATE
        if tail_dur < MIN_TAIL_S:
            return
        thr = self._speech_threshold()
        frames = frame_rms(tail)
        if not has_speech(frames, thr):
            # Silence throughout: nothing to decode. Clearing it once it is
            # longer than a pause keeps leading silence from diluting the
            # speech that starts next.
            self._idle_s += self.profile["step_s"]
            if final or tail_dur >= self.profile["pause_s"]:
                self._flush_silence(len(tail))
            return
        self._idle_s = 0.0

        gain = speech_gain(frames, thr)
        audio = np.clip(tail * gain, -1.0, 1.0) if gain > 1.0 else tail
        try:
            text, detected, tokens = decode(audio, self._language_for_call())
        except Exception:
            settings.log_exception("Live translate tick failed, retrying next tick:")
            if tail_dur >= self.profile["max_uncommitted_s"]:
                self._consume_tail(len(tail))
                self._stream.reset()
            return
        self._lang.add(detected, tail_dur, text)
        lang = detected or self._lang.current or self.language
        text = transcriber.apply_chinese_conversion(text, lang, self.traditional_chinese)

        natural_pause = tail_dur >= self.profile["pause_s"] and trailing_silent(
            frames, self.profile["pause_s"], thr)
        capped = tail_dur >= self.profile["max_uncommitted_s"]
        commit = final or natural_pause
        ready, preview, cut_s = self._stream.update(text, tokens, final=commit, tail_s=tail_dur)
        for sentence in ready:
            if is_meaningful(sentence):
                self._submit(sentence, lang)
        if commit:
            self._consume_tail(len(tail))
            self._draft_src = ""
            self._reset_chunks()
            self._emit("lt_preview", "", "")
            return
        if cut_s is not None:
            self._consume_tail(int(cut_s * SAMPLE_RATE))
            tail_dur -= cut_s
            tokens = [(w, on - cut_s) for w, on in (tokens or []) if on >= cut_s]
        if capped and tail_dur >= self.profile["max_uncommitted_s"]:
            # Long run with no sentence end and no real pause. Split at the
            # biggest silence between two words rather than at the very end
            # of the audio, which may be in the middle of a word.
            split = cap_split(tokens) if tokens else None
            if split is not None:
                left_text, cut, _n = split
                if is_meaningful(left_text):
                    self._submit(left_text, lang)
                self._consume_tail(int(cut * SAMPLE_RATE))
                self._stream.reset()
                preview = ""
            else:
                for sentence in self._stream.update(text, tokens, final=True)[0]:
                    if is_meaningful(sentence):
                        self._submit(sentence, lang)
                self._consume_tail(len(tail))
                preview = ""
        if self.wordlist is not None and preview:
            preview = self.wordlist.correct(preview)
        self._draft_src = preview
        self._schedule_chunks(preview, lang)
        self._emit("lt_preview", preview, self._chunk_acc(self._cstate))

    def _flush_silence(self, n_samples):
        ready, _, _ = self._stream.update("", final=True)
        # `ready` is empty unless an earlier partial decode left text behind.
        for sentence in ready:
            if is_meaningful(sentence):
                self._submit(sentence, self._lang.current)
        self._consume_tail(n_samples)
        self._draft_src = ""
        self._reset_chunks()
        self._emit("lt_preview", "", "")

    # -- chunks: translating a sentence while it is still being spoken ------------

    @staticmethod
    def _new_cstate():
        return {"src": [], "tr": [], "dead": False}

    def _reset_chunks(self):
        self._cstate["dead"] = True
        self._cstate = self._new_cstate()
        self._chunker.reset()

    def _chunk_acc(self, state):
        """Translation of the leading clauses that are already done."""
        acc = ""
        for tr in state["tr"]:
            if not tr:
                break
            acc = join_translations(acc, tr, self.target_key)
        return acc

    def _schedule_chunks(self, preview, lang):
        if not self.profile["chunks"] or not preview:
            return
        if self._q.qsize() >= LAG_DEGRADE_QUEUE:
            return                                   # already behind: no extra work
        if (self.target_key in SAME_LANGUAGE.get(lang or "", set())
                and looks_like_target(preview, self.target_key)):
            return                                   # nothing to translate
        new, stale = self._chunker.update(preview)
        if stale:
            self._cstate["dead"] = True
            self._cstate = self._new_cstate()
        for idx, text in new:
            self._cstate["src"].append(text)
            self._cstate["tr"].append(None)
            self._q.put(("chunk", self._cstate, idx, text))

    def _submit(self, source, lang):
        if self.wordlist is not None:
            source = self.wordlist.correct(source)
        seg_id = self._next_id
        self._next_id += 1
        t_emit = time.perf_counter()
        # Skip translating only when the detected language AND the text itself
        # are already the target - a mis-detected language must never make the
        # app show foreign text as if it were the translation.
        same = (self.target_key in SAME_LANGUAGE.get(lang or "", set())
                and looks_like_target(source, self.target_key))
        self._stats["sentences"] += 1
        # The clauses translated early belong to the FIRST sentence released
        # now; hand them over (the translator checks they still match).
        state = None
        if self._cstate["src"]:
            state = self._cstate
            self._cstate = self._new_cstate()
            self._chunker.reset()
        if same:
            if state is not None:
                state["dead"] = True
            self._emit("lt_segment", seg_id, source, source, "final")
            return
        with self._pending_lock:
            self._pending += 1
        self._emit("lt_segment", seg_id, source,
                   self._chunk_acc(state) if state else "", "pending")
        self._q.put(("sentence", seg_id, source, t_emit, state))

    # -- translation thread -------------------------------------------------

    def _start_translator(self):
        self._translator = threading.Thread(target=self._translate_loop, daemon=True)
        self._translator.start()

    def _watchdog(self):
        """A dead translator thread would leave sentences stuck as "..." with
        nothing ever following. Restart it (the queue survives)."""
        if (self._translator is not None and not self._translator.is_alive()
                and not self.stop_event.is_set()):
            self._stats["restarts"] += 1
            settings.log("Live translate: translator thread died, restarting it.")
            self._start_translator()

    def _translate_loop(self):
        try:
            while True:
                try:
                    item = self._q.get(timeout=0.15)
                except queue.Empty:
                    continue
                if item is None:
                    return
                if item[0] == "chunk":
                    try:
                        self._translate_chunk(item)
                    except Exception:
                        settings.log_exception("Live translate: translating a clause failed:")
                        item[1]["tr"][item[2]] = ""
                    continue
                try:
                    self._translate_sentence(item)
                except Exception:
                    settings.log_exception("Live translate: translating a sentence failed:")
                    self._stats["failed"] += 1
                    self._emit("lt_segment", item[1], item[2], "", "failed")
                finally:
                    with self._pending_lock:
                        self._pending -= 1
        except BaseException:
            settings.log_exception("Live translate: translator thread crashed:")
            raise

    def _translate_chunk(self, item):
        """One finished clause of the sentence still being spoken. The result
        is stored in the sentence's chunk state and shown as the live
        translation; if the sentence changes meanwhile it is simply dropped."""
        _kind, state, idx, text = item
        if state["dead"]:
            return
        # No earlier-clause context here: on a CPU every extra prompt token costs
        # time (measured: a clause with one clause of context took twice as
        # long), and speed is the whole point of Latency mode.
        glossary = self.wordlist.hints(text) if self.wordlist is not None else []
        out = self._mt.translate(
            text, self.target_name, [], glossary=glossary,
            abort=lambda: state["dead"] or self.stop_event.is_set())
        if out is None:
            return
        out = self._fix_target(out)
        state["tr"][idx] = out if translation_ok(text, out, self.target_key) else ""
        if state is self._cstate:
            self._emit("lt_preview", self._draft_src, self._chunk_acc(state))

    def _translate_sentence(self, item):
        _kind, seg_id, source, t_emit, state = item
        pairs, tail = [], None
        if state is not None:
            for s_i, t_i in zip(state["src"], state["tr"]):
                if not t_i:
                    break
                pairs.append((s_i, t_i))
            while pairs:                            # trust only what still matches the final text
                tail = prefix_split(source, [s_i for s_i, _ in pairs])
                if tail is not None:
                    break
                pairs.pop()
        if not pairs or tail is None:
            self._translate_whole(seg_id, source, t_emit)
            return
        t0 = time.perf_counter()
        acc = self._chunk_acc({"tr": [t_i for _, t_i in pairs]})
        if not tail.strip():
            final = acc                              # the sentence ended with a clause: nothing left to do
        else:
            glossary = self.wordlist.hints(tail) if self.wordlist is not None else []
            part = self._fix_target(self._mt.translate(
                tail, self.target_name, [], glossary=glossary,
                on_partial=lambda txt, i=seg_id, s=source, a=acc:
                    self._emit("lt_segment", i, s,
                               join_translations(a, self._fix_target(txt), self.target_key),
                               "streaming")) or "")
            if not translation_ok(tail, part, self.target_key):
                self._translate_whole(seg_id, source, t_emit)    # safest: translate it all in one piece
                return
            final = join_translations(acc, part, self.target_key)
        self._finish_sentence(seg_id, source, final, t0, t_emit)

    def _finish_sentence(self, seg_id, source, out, t0, t_emit):
        done = time.perf_counter()
        self._history.append((source, out))
        self._emit("lt_segment", seg_id, source, out, "final")
        self._emit("lt_metrics", {"mt_ms": round((done - t0) * 1000),
                                  "e2e_ms": round((done - t_emit) * 1000),
                                  "queue": self._q.qsize()})

    def _translate_whole(self, seg_id, source, t_emit):
        backlog = self._q.qsize()
        n_ctx_pairs = 0 if backlog >= LAG_DEGRADE_QUEUE else self.profile["context_pairs"]
        context = list(self._history)[-n_ctx_pairs:] if n_ctx_pairs else []
        t0 = time.perf_counter()
        glossary = self.wordlist.hints(source) if self.wordlist is not None else []
        out = self._fix_target(self._mt.translate(
            source, self.target_name, context, glossary=glossary,
            on_partial=lambda txt, i=seg_id, s=source:
                self._emit("lt_segment", i, s, self._fix_target(txt), "streaming")) or "")
        if not translation_ok(source, out, self.target_key):
            # One more try with the settings least likely to repeat the same
            # failure: no context, a little randomness, no repetition penalty.
            self._stats["retries"] += 1
            out = self._fix_target(self._mt.translate(
                source, self.target_name, [], temperature=0.3, repeat_penalty=1.0,
                glossary=glossary) or "")
            if not translation_ok(source, out, self.target_key):
                self._stats["failed"] += 1
                settings.log(f"Live translate: no usable translation for {source!r} -> {out!r}")
                self._emit("lt_segment", seg_id, source, "", "failed")
                return
        self._finish_sentence(seg_id, source, out, t0, t_emit)
