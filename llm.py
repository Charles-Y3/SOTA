"""Local LLM worker for summarize/translate, built on llama.cpp
(llama-cpp-python). Runs fully offline after a one-time model download.

The model family is fixed (Qwen3 instruct GGUF builds from the official,
ungated Hugging Face repos) and hidden behind the app's Fast/Balanced/
Accurate quality words. Thinking mode is disabled via Qwen's "/no_think"
soft switch, and any think blocks are stripped from the stream defensively.

Same event-queue pattern as transcriber.py:

    ("llm_status", key, detail)  -> status line under the panels
    ("llm_reset",)               -> clear the output panel
    ("llm_token", text)          -> append streamed text to the output panel
    ("llm_finished", ok)         -> generation over (ok=False on error/cancel)
"""

import gc
import os
import re
import threading

import settings
from progress import make_progress_tqdm_class

QUALITY_LLM = {
    "fast": {
        "repo": "Qwen/Qwen3-1.7B-GGUF",
        "file": "Qwen3-1.7B-Q8_0.gguf",
        "size_gb": 1.8,
    },
    "balanced": {
        "repo": "Qwen/Qwen3-4B-GGUF",
        "file": "Qwen3-4B-Q4_K_M.gguf",
        "size_gb": 2.5,
    },
    "accurate": {
        "repo": "Qwen/Qwen3-8B-GGUF",
        "file": "Qwen3-8B-Q4_K_M.gguf",
        "size_gb": 4.7,
    },
}

# Rough total-RAM guidance for running each tier's GGUF comfortably via
# llama.cpp (model file + KV cache + interpreter overhead) — these models
# are GB-sized, so unlike the Whisper ones this is where RAM actually
# becomes a real constraint.
QUALITY_RAM_GB = {"fast": 3.0, "balanced": 4.0, "accurate": 7.0}


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


N_CTX = 8192
SINGLE_PASS_BUDGET = 4500     # max input tokens for a one-shot summary
MAP_CHUNK_TOKENS = 3000       # per-part input size for long summaries
TRANSLATE_CHUNK_TOKENS = 1100  # per-part input size for translation

TIMESTAMP_RE = re.compile(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*", re.MULTILINE)
SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")

# One loaded model at a time (they are GB-sized); swapped when quality changes.
_cache = {"quality": None, "llama": None}
_cache_lock = threading.Lock()


def llm_model_is_downloaded(quality):
    spec = QUALITY_LLM[quality]
    repo_dir = "models--" + spec["repo"].replace("/", "--")
    snapshots = os.path.join(settings.MODELS_DIR, repo_dir, "snapshots")
    if not os.path.isdir(snapshots):
        return False
    for snap in os.listdir(snapshots):
        if os.path.isfile(os.path.join(snapshots, snap, spec["file"])):
            return True
    return False


def strip_timestamps(text):
    return TIMESTAMP_RE.sub("", text)


class ThinkFilter:
    """Streaming filter that removes <think>…</think> blocks."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.pending = ""
        self.in_think = False

    def feed(self, chunk):
        self.pending += chunk
        out = []
        while True:
            if self.in_think:
                i = self.pending.find(self.CLOSE)
                if i < 0:
                    # Drop think content, keeping only a possible partial tag.
                    self.pending = self.pending[-(len(self.CLOSE) - 1):]
                    break
                self.pending = self.pending[i + len(self.CLOSE):]
                self.in_think = False
            else:
                i = self.pending.find(self.OPEN)
                if i < 0:
                    # Emit everything except a possible partial open tag.
                    keep = 0
                    for k in range(min(len(self.OPEN) - 1, len(self.pending)), 0, -1):
                        if self.OPEN.startswith(self.pending[-k:]):
                            keep = k
                            break
                    cut = len(self.pending) - keep
                    out.append(self.pending[:cut])
                    self.pending = self.pending[cut:]
                    break
                out.append(self.pending[:i])
                self.pending = self.pending[i + len(self.OPEN):]
                self.in_think = True
        return "".join(out)

    def close(self):
        if self.in_think:
            self.pending = ""
            return ""
        out, self.pending = self.pending, ""
        return out


class LLMWorker(threading.Thread):
    """Summarizes and/or translates `text`; results go to `events`.

    mode: "summarize" | "translate" | "both"
    target_prompt_name: language name for the prompt (e.g. "Traditional
        Chinese"); ignored when mode == "summarize".
    """

    def __init__(self, text, mode, target_prompt_name, quality, events):
        super().__init__(daemon=True)
        self.text = text
        self.mode = mode
        self.target = target_prompt_name
        self.quality = quality
        self.events = events
        self.cancel_event = threading.Event()
        self._emitted_any = False

    def cancel(self):
        self.cancel_event.set()

    # -- plumbing -----------------------------------------------------------

    def _emit(self, *event):
        self.events.put(event)

    def _emit_text(self, chunk):
        if not self._emitted_any:
            chunk = chunk.lstrip()
            if not chunk:
                return
            self._emitted_any = True
        self._emit("llm_token", chunk)

    # -- model --------------------------------------------------------------

    def _load_model(self):
        spec = QUALITY_LLM[self.quality]
        first_run = not llm_model_is_downloaded(self.quality)
        os.makedirs(settings.MODELS_DIR, exist_ok=True)
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

        tqdm_class = None
        if first_run:
            self._emit("llm_status", "llm_downloading", {"size": spec["size_gb"], "pct": 0})
            last_pct = {"value": -1}

            def on_progress(downloaded, total):
                pct = int(downloaded / total * 100)
                if pct != last_pct["value"]:
                    last_pct["value"] = pct
                    self._emit("llm_status", "llm_downloading",
                               {"size": spec["size_gb"], "pct": pct})

            tqdm_class = make_progress_tqdm_class(on_progress)
        else:
            self._emit("llm_status", "llm_loading", {})

        try:
            from huggingface_hub import hf_hub_download

            kwargs = {"cache_dir": settings.MODELS_DIR}
            if tqdm_class is not None:
                kwargs["tqdm_class"] = tqdm_class
            path = hf_hub_download(spec["repo"], spec["file"], **kwargs)
        except Exception:
            settings.log_exception("LLM model download failed:")
            self._emit("llm_status", "llm_download_failed", {})
            return None

        try:
            with _cache_lock:
                if _cache["quality"] == self.quality and _cache["llama"] is not None:
                    return _cache["llama"]
                _cache["llama"] = None
                gc.collect()
                from llama_cpp import Llama  # deferred: heavy import

                llama = Llama(
                    model_path=path,
                    n_ctx=N_CTX,
                    n_threads=max(1, (os.cpu_count() or 4) - 1),
                    n_gpu_layers=0,
                    verbose=False,
                )
                _cache["quality"], _cache["llama"] = self.quality, llama
                return llama
        except Exception:
            settings.log_exception("LLM model load failed:")
            self._emit("llm_status", "llm_failed", {})
            return None

    # -- text utilities -------------------------------------------------------

    @staticmethod
    def _count_tokens(llama, text):
        try:
            return len(llama.tokenize(text.encode("utf-8"), add_bos=False))
        except Exception:
            return max(1, len(text) // 3)

    def _chunks(self, llama, text, limit):
        """Split text into pieces of at most `limit` tokens, preferring
        paragraph then sentence boundaries."""
        pieces = []
        for para in text.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            if self._count_tokens(llama, para) <= limit:
                pieces.append(para)
                continue
            for sent in SENTENCE_RE.split(para):
                sent = sent.strip()
                if not sent:
                    continue
                if self._count_tokens(llama, sent) <= limit:
                    pieces.append(sent)
                else:  # pathological run-on: hard split by characters
                    step = max(1, limit * 3)
                    pieces.extend(
                        sent[i:i + step] for i in range(0, len(sent), step)
                    )

        chunks, current, current_tokens = [], [], 0
        for piece in pieces:
            n = self._count_tokens(llama, piece)
            if current and current_tokens + n > limit:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            current.append(piece)
            current_tokens += n
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    # -- generation -----------------------------------------------------------

    def _generate(self, llama, system, user, max_tokens, stream_out, temperature):
        """Run one chat completion. Returns the text, or None if cancelled."""
        think = ThinkFilter()
        collected = []
        stream = llama.create_chat_completion(
            messages=[
                {"role": "system", "content": system + " /no_think"},
                {"role": "user", "content": user},
            ],
            stream=True,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=0.9,
        )
        for part in stream:
            if self.cancel_event.is_set():
                return None
            delta = part["choices"][0].get("delta", {})
            piece = delta.get("content")
            if not piece:
                continue
            visible = think.feed(piece)
            if visible:
                collected.append(visible)
                if stream_out:
                    self._emit_text(visible)
        tail = think.close()
        if tail:
            collected.append(tail)
            if stream_out:
                self._emit_text(tail)
        return "".join(collected).strip()

    def _lang_directive(self):
        """An emphatic, standalone opening instruction. Smaller models
        reliably follow a short, first, imperative sentence about language
        far more often than the same instruction buried mid-paragraph among
        several other constraints — a plain sentence tucked at the end (the
        original approach) was frequently ignored entirely in testing."""
        if self.target:
            return (
                f"You must write your entire response in {self.target}. Do"
                " not use English or any other language, even if the source"
                " text is in a different language."
            )
        return "Write your response in the same language as the transcript."

    def _lang_reminder(self):
        """A short closing repeat of the same constraint — repetition at
        both ends measurably improves compliance for small models."""
        if self.target:
            return f" Remember: write the entire response in {self.target}, not English."
        return ""

    def _translate(self, llama, text):
        system = (
            f"{self._lang_directive()} Translate the user's text into"
            f" {self.target}, preserving the paragraph structure. Output"
            f" only the translation — no notes, no explanations."
            f"{self._lang_reminder()}"
        )
        chunks = self._chunks(llama, text, TRANSLATE_CHUNK_TOKENS)
        total = len(chunks)
        for i, chunk in enumerate(chunks, 1):
            if total > 1:
                self._emit("llm_status", "llm_generating_part",
                           {"part": i, "total": total})
            else:
                self._emit("llm_status", "llm_generating_status", {})
            if i > 1:
                self._emit_text("\n\n")
            n = self._count_tokens(llama, chunk)
            out = self._generate(
                llama, system, chunk,
                max_tokens=min(2 * n + 256, N_CTX - n - 512),
                stream_out=True, temperature=0.2,
            )
            if out is None:
                return False
        return True

    def _summarize(self, llama, text, stream=True):
        """Returns the summary text (always in the transcript's own
        language), or None on failure/cancellation.

        Deliberately has no language-switching logic at all: testing showed
        that asking a small model to summarize AND switch output language
        in one instruction reliably causes it to silently drop the language
        instruction, producing a full English summary even when Chinese was
        requested. "Both" mode instead calls this for a plain summary, then
        runs the well-tested single-purpose _translate() on the result —
        each step only has to do one thing, which small models handle
        reliably (verified: the combined approach produced 0% target-
        language output; the two-step approach reliably produced it).
        """
        final_system = (
            "Write a condensed summary of the user's transcript. Follow the"
            " same order and structure as the original — condense each part"
            " in sequence rather than reorganizing the content into"
            " categories. Write in flowing prose paragraphs; do not use"
            " bullet points or headings. Output only the summary."
        )
        if self._count_tokens(llama, text) <= SINGLE_PASS_BUDGET:
            self._emit("llm_status", "llm_generating_status", {})
            return self._generate(llama, final_system, text,
                                  max_tokens=1400, stream_out=stream,
                                  temperature=0.7)

        # Long transcript: condense each part in prose (preserving order),
        # then merge (repeating the condensing if notes are still too long).
        notes_system = (
            "The user's text is one consecutive part of a longer transcript."
            " Condense it into flowing prose, preserving the order and"
            " structure of the content. Use the same language as the text."
            " Do not use bullet points or headings. Output only the"
            " condensed text."
        )
        chunks = self._chunks(llama, text, MAP_CHUNK_TOKENS)
        notes = []
        for i, chunk in enumerate(chunks, 1):
            self._emit("llm_status", "llm_generating_part",
                       {"part": i, "total": len(chunks) + 1})
            out = self._generate(llama, notes_system, chunk,
                                 max_tokens=500, stream_out=False,
                                 temperature=0.7)
            if out is None:
                return None
            notes.append(out)

        merged = "\n\n".join(notes)
        for _ in range(3):
            if self._count_tokens(llama, merged) <= SINGLE_PASS_BUDGET:
                break
            condensed = []
            for chunk in self._chunks(llama, merged, MAP_CHUNK_TOKENS):
                out = self._generate(llama, notes_system, chunk,
                                     max_tokens=400, stream_out=False,
                                     temperature=0.7)
                if out is None:
                    return None
                condensed.append(out)
            merged = "\n\n".join(condensed)

        merge_system = (
            "The user's text is condensed notes taken from consecutive parts"
            " of one long transcript, in order. Combine them into one"
            " coherent summary in flowing prose paragraphs, preserving the"
            " original order and structure. Do not reorganize into"
            " categories, and do not use bullet points or headings. Output"
            " only the summary."
        )
        self._emit("llm_status", "llm_generating_part",
                   {"part": len(chunks) + 1, "total": len(chunks) + 1})
        return self._generate(llama, merge_system, merged,
                              max_tokens=1400, stream_out=stream,
                              temperature=0.7)

    # -- main -----------------------------------------------------------------

    def run(self):
        ok = False
        try:
            ok = self._run()
        except Exception:
            settings.log_exception("LLM worker crashed:")
            self._emit("llm_status", "llm_failed", {})
        finally:
            self._emit("llm_finished", ok)

    def _run(self):
        text = strip_timestamps(self.text).strip()
        if not text:
            self._emit("llm_status", "llm_no_file", {})
            return False

        llama = self._load_model()
        if llama is None:
            return False
        if self.cancel_event.is_set():
            self._emit("llm_status", "llm_cancelled", {})
            return False

        self._emit("llm_reset")
        if self.mode == "translate":
            ok = self._translate(llama, text)
        elif self.mode == "both":
            # Two focused single-purpose passes instead of one compound
            # instruction -- see _summarize()'s docstring for why.
            summary = self._summarize(llama, text, stream=False)
            ok = summary is not None and self._translate(llama, summary)
        else:  # "summarize"
            ok = self._summarize(llama, text) is not None

        if not ok:
            self._emit("llm_status", "llm_cancelled", {})
            return False
        self._emit("llm_status", "llm_done", {})
        return True
