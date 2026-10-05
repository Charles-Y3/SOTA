"""Custom word list for Live Translate: names, products and jargon the speech
engine mishears and the translator would otherwise mangle.

An entry has up to three parts, all plain text:

  word         the correct spelling ("Acme Dynamics", "Dr. Okafor", "kanban")
  heard        other spellings the speech engine tends to produce for it
               ("Acne Dynamics, Acme Dinamics") - optional
  translation  what it must become in the translation ("看板"); left empty the
               word is kept exactly as written - optional

Two jobs, both applied per finished sentence:

  correct(text)  fixes the recognized text: every "heard" spelling becomes the
                 word, and a close spelling of a longer Latin word (one or two
                 letters off) is snapped to it as well.
  hints(text)    the entries that occur in a sentence, as (word, translation)
                 pairs; the translator is told to use exactly these.

SenseVoice has no hot-word feature, so correcting its text afterwards is the
only lever there; for the translator, giving the model a reference
translation for a term is what its own prompt format is designed for.
"""

import re

_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def _is_latin(s):
    return bool(s) and all(c.isascii() for c in s)


def _clean_entry(raw):
    word = str(raw.get("word", "")).strip()
    if not word:
        return None
    heard = raw.get("heard", [])
    if isinstance(heard, str):
        heard = re.split(r"[,;，；、]", heard)
    heard = [h.strip() for h in heard if h and h.strip() and h.strip().lower() != word.lower()]
    return {"word": word, "heard": heard, "translation": str(raw.get("translation", "")).strip()}


def clean_entries(entries):
    """Validates a list of dicts (as saved in the settings file); drops empty
    rows and duplicate words."""
    out, seen = [], set()
    for raw in entries or []:
        if not isinstance(raw, dict):
            continue
        entry = _clean_entry(raw)
        if entry and entry["word"].lower() not in seen:
            seen.add(entry["word"].lower())
            out.append(entry)
    return out


def parse_text(text):
    """'word | heard1, heard2 | translation' per line (tabs also work as the
    separator); lines starting with # are comments."""
    entries = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"\||\t", line)]
        parts += [""] * (3 - len(parts))
        entries.append({"word": parts[0], "heard": parts[1], "translation": parts[2]})
    return clean_entries(entries)


def to_text(entries):
    lines = ["# word | often heard as (comma separated) | translate as"]
    for e in clean_entries(entries):
        lines.append(f"{e['word']} | {', '.join(e['heard'])} | {e['translation']}")
    return "\n".join(lines) + "\n"


def edit_distance(a, b, limit):
    """Levenshtein distance, giving up (returning limit + 1) beyond `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _boundary_pattern(term):
    if _is_latin(term):
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(re.escape(term))


def _fuzzy_limit(word):
    n = len(word)
    if n < 6:
        return 0           # short words are too easily real, different words
    return 1 if n <= 8 else 2


class WordList:
    """Thread-safe by replacement: set_entries() builds a complete new
    compiled state and swaps it in with one assignment, so the translator
    thread never sees a half-updated list."""

    def __init__(self, entries=()):
        self._state = ((), (), ())
        self.set_entries(entries)

    def set_entries(self, entries):
        entries = clean_entries(entries)
        alias_rules = []
        for e in entries:
            for heard in sorted(e["heard"], key=len, reverse=True):
                alias_rules.append((_boundary_pattern(heard), e["word"]))
        fuzzy = [e["word"] for e in entries if _is_latin(e["word"]) and _fuzzy_limit(e["word"])]
        presence = [(e, _boundary_pattern(e["word"])) for e in entries]
        self._state = (tuple(entries), tuple(alias_rules), (tuple(fuzzy), tuple(presence)))

    @property
    def entries(self):
        return list(self._state[0])

    def __len__(self):
        return len(self._state[0])

    # ------------------------------------------------------------------

    def correct(self, text):
        entries, alias_rules, (fuzzy, _presence) = self._state
        if not text or not entries:
            return text
        for pattern, word in alias_rules:
            text = pattern.sub(word, text)
        if fuzzy:
            text = self._snap_close_spellings(text, fuzzy)
        return text

    @staticmethod
    def _snap_close_spellings(text, words):
        known = {w.lower() for w in words}
        tokens = list(_LATIN_WORD.finditer(text))
        replacements = []                       # (start, end, new)
        used = set()
        for word in words:
            n_parts = len(word.split())
            limit = _fuzzy_limit(word.replace(" ", ""))
            for i in range(len(tokens) - n_parts + 1):
                window = tokens[i:i + n_parts]
                if any(t in used for t in range(i, i + n_parts)):
                    continue
                if any(text[window[k].end():window[k + 1].start()].strip()
                       for k in range(n_parts - 1)):
                    continue                     # words must be neighbours (spaces only)
                heard = text[window[0].start():window[-1].end()]
                if heard.lower() == word.lower() or heard.lower() in known:
                    continue
                if heard[0].lower() != word[0].lower():
                    continue
                if edit_distance(heard.lower(), word.lower(), limit) <= limit:
                    replacements.append((window[0].start(), window[-1].end(), word))
                    used.update(range(i, i + n_parts))
        for start, end, new in sorted(replacements, reverse=True):
            text = text[:start] + new + text[end:]
        return text

    def hints(self, text):
        """[(word, translation_or_same_word)] for the entries present in text."""
        _entries, _aliases, (_fuzzy, presence) = self._state
        if not text:
            return []
        return [(e["word"], e["translation"] or e["word"])
                for e, pattern in presence if pattern.search(text)]
