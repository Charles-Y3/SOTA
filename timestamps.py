"""Paragraph timestamps for transcripts: sidecar storage + [mm:ss] markers.

Every transcript with timing information gets a small JSON sidecar next to
it (``<stem>.times.json``) holding one start-time per paragraph. The Edit
tab renders those as clickable ``[mm:ss]`` markers at each paragraph's
start; clicking one seeks the player. The markers are plain text inside the
editor (so they survive normal editing and travel with their paragraphs),
which is why parsing/stripping them robustly lives here rather than in the
UI.

A transcript saved with the Timestamps toggle ON also keeps the markers in
the saved file itself — so the sidecar is a convenience, not a hard
dependency: a marked-up file that lost its sidecar still round-trips.
"""

import json
import os
import re

# [mm:ss] or [h:mm:ss], only at a paragraph/line start, optionally eating
# one following space so hiding/stripping a marker never leaves a stray
# leading gap. Hours are one group deeper, not a wider minutes field —
# [75:30] is not a marker this app ever writes ([1:15:30] is).
MARKER_RE = re.compile(r"^\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\] ?", re.MULTILINE)

_SIDECAR_SUFFIX = ".times.json"


def format_marker(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"[{h}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def marker_seconds(match):
    """Seconds encoded by a MARKER_RE match."""
    h = int(match.group(1)) if match.group(1) else 0
    return h * 3600 + int(match.group(2)) * 60 + int(match.group(3))


def sidecar_path(transcript_path):
    return os.path.splitext(transcript_path)[0] + _SIDECAR_SUFFIX


def save_sidecar(transcript_path, times):
    """Writes per-paragraph start times next to `transcript_path`.
    Best-effort: timestamps are a convenience layer, so a failed sidecar
    write must never take the transcript save down with it."""
    if not times or all(t is None for t in times):
        return
    try:
        with open(sidecar_path(transcript_path), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "paragraph_times": list(times)}, f)
    except Exception:
        import settings

        settings.log_exception(f"Timestamp sidecar write failed for {transcript_path}:")


def load_sidecar(transcript_path):
    """Per-paragraph times for `transcript_path`, or None if there's no
    (readable) sidecar."""
    try:
        with open(sidecar_path(transcript_path), encoding="utf-8") as f:
            data = json.load(f)
        times = data.get("paragraph_times")
        if isinstance(times, list) and times:
            return [t if isinstance(t, (int, float)) else None for t in times]
    except Exception:
        pass
    return None


def insert_markers(text, times):
    """Prefixes each paragraph of `text` with its marker from `times`
    (parallel list; None entries get no marker)."""
    paragraphs = text.split("\n\n")
    out = []
    for i, para in enumerate(paragraphs):
        t = times[i] if i < len(times) else None
        if t is not None and para.strip():
            out.append(f"{format_marker(t)} {para}")
        else:
            out.append(para)
    return "\n\n".join(out)


def parse_marked_text(text):
    """Splits marked-up editor text back into (clean_text, times) where
    times has one entry (seconds or None) per paragraph of clean_text.

    Tolerant by design: the markers live inside an editable text box, so a
    user may have deleted some, added paragraphs without any, or mangled
    one into plain text (a mangled marker simply stops matching and stays
    as ordinary words). One more thing markers survive: deleting an entire
    paragraph while markers are toggled hidden. A hidden marker is elided
    to zero width, so it can't be clicked or dragged over — nothing marks
    where it starts, meaning a "delete this whole line" gesture aimed at
    the now-visible text naturally leaves it behind, jammed directly
    against whatever paragraph follows (no blank-line gap, since that
    belonged to the deleted paragraph too). Consuming every leading
    marker in a loop instead of just one, keeping only the last one's
    time, cleans that up: the surviving marker is the one that actually
    matches where the remaining text starts; earlier ones point at
    content that's gone."""
    paragraphs = text.split("\n\n")
    clean, times = [], []
    for para in paragraphs:
        seconds = None
        while True:
            m = MARKER_RE.match(para)
            if not m:
                break
            seconds = marker_seconds(m)
            para = para[m.end():]
        times.append(seconds)
        clean.append(para)
    return "\n\n".join(clean), times


def strip_markers(text):
    return MARKER_RE.sub("", text)


def has_markers(text):
    return MARKER_RE.search(text) is not None
