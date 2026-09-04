"""Shared Audio Studio clip model: a mutable audio buffer with a linear
undo/redo stack, a protected copy of the original recording, and a
waveform-peak helper for drawing without rendering every sample.

Editing is destructive by design (an explicit product decision, not a
default): every operation replaces `self.buffer` and pushes the previous
buffer onto the undo stack, rather than keeping a non-destructive chain of
effects. The *source* file is never touched — on first load a verbatim
copy is made into `originals_dir` before any edit can run, so "Revert to
Original" always works even after the working copy has been saved over.
"""

import os
import shutil
import wave

import numpy as np

# Editing works at a higher rate than the 22050Hz `player.py` uses for
# quick playback preview — that rate was chosen for small live-session
# files and fast WSOLA stretching, not editing fidelity.
CLIP_SAMPLE_RATE = 44100


def decode_to_buffer(path, sample_rate=CLIP_SAMPLE_RATE):
    """Decodes any audio/video file `player.py` can already open (via the
    PyAV decoder bundled with faster-whisper — no ffmpeg needed) to a mono
    float32 numpy array at `sample_rate`."""
    from faster_whisper.audio import decode_audio

    samples = decode_audio(path, sampling_rate=sample_rate)
    return np.ascontiguousarray(samples, dtype=np.float32)


def write_wav(path, buffer, sample_rate=CLIP_SAMPLE_RATE):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    pcm16 = (np.clip(buffer, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def peaks_from_buffer(buffer, sample_rate, start_s, end_s, pixel_count):
    """Standalone version of AudioClip.peaks — usable on any array, not
    just a clip's own buffer (e.g. a not-yet-applied preview composite in
    app.py). AudioClip.peaks below just delegates here."""
    i0 = max(0, min(len(buffer), int(round(start_s * sample_rate))))
    i1 = max(0, min(len(buffer), int(round(end_s * sample_rate))))
    if i1 < i0:
        i0, i1 = i1, i0
    region = buffer[i0:i1]
    pixel_count = max(1, int(pixel_count))
    if region.size == 0:
        z = np.zeros(pixel_count, dtype=np.float32)
        return z, z
    pad = (-len(region)) % pixel_count
    if pad:
        region = np.concatenate([region, np.zeros(pad, dtype=np.float32)])
    chunks = region.reshape(pixel_count, -1)
    return chunks.min(axis=1), chunks.max(axis=1)


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


class AudioClip:
    """In-memory mono audio buffer for Audio Studio's Edit/Enhance/Clean
    subtabs, plus the undo/redo stack all three build on."""

    UNDO_LIMIT = 50

    def __init__(self, sample_rate=CLIP_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.buffer = np.zeros(0, dtype=np.float32)
        self.original_path = None   # protected copy — never overwritten again
        self.source_path = None     # the file the user originally opened
        # [{"time": seconds, "label": str, "auto": bool}, ...], always
        # kept sorted by time. "auto" markers come from Detect Sections
        # and are wholesale replaced by the next detection run; user-
        # added ones (auto=False) never are. Undo/redo restore markers
        # alongside the buffer (see _undo/_redo below) since a structural
        # edit's marker positions are only meaningful paired with the
        # buffer state they were computed against.
        self.markers = []
        self._undo = []  # [(buffer, markers_snapshot), ...]
        self._redo = []
        # True whenever the buffer differs from what was last exported —
        # a simple flag (not an exact content comparison) so it behaves
        # like every other editor's "unsaved changes" indicator: any
        # mutation sets it, only a successful export (mark_exported)
        # clears it.
        self.dirty = False

    @classmethod
    def load(cls, path, originals_dir, sample_rate=CLIP_SAMPLE_RATE):
        """Decodes `path` and copies it verbatim into `originals_dir`
        before any edit can touch it. Re-opening the same source path
        again reuses its existing protected copy instead of duplicating
        it (checked by content identity, not just name, since two
        different files can share a name after Windows' own "(2)"
        de-duplication elsewhere in the app)."""
        clip = cls(sample_rate)
        clip.buffer = decode_to_buffer(path, sample_rate)
        clip.source_path = path
        os.makedirs(originals_dir, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(path))
        ext = ext or ".wav"
        dest = os.path.join(originals_dir, f"{stem}{ext}")
        n = 2
        while os.path.exists(dest) and not _same_file(dest, path):
            dest = os.path.join(originals_dir, f"{stem} ({n}){ext}")
            n += 1
        if not os.path.exists(dest):
            shutil.copy2(path, dest)
        clip.original_path = dest
        return clip

    def revert_to_original(self):
        """Discards every edit made so far, restoring the buffer exactly
        as it was when first loaded — including clearing markers, since
        the original recording never had any. Still undoable — this is
        itself just another buffer replacement on the same stack."""
        if not self.original_path or not os.path.isfile(self.original_path):
            return False
        before = self._markers_copy()
        self.markers = []
        self.apply(decode_to_buffer(self.original_path, self.sample_rate), markers_before=before)
        return True

    # -- undo/redo ----------------------------------------------------------

    def _markers_copy(self):
        return [dict(m) for m in self.markers]

    def apply(self, new_buffer, markers_before=None):
        """Replaces the buffer with `new_buffer`, pushing an undo entry so
        undo restores both the audio and the marker positions as they
        were beforehand. `markers_before` lets a structural caller that
        has already repositioned self.markers to match the new buffer
        (trim/cut/paste/insert_silence/remove_ranges — see each below)
        pass in what the markers were just before that repositioning;
        callers that don't touch markers at all (Enhance/Clean's point
        effects) omit it — the current, untouched markers are already
        the correct "before" state. Also clears any redo history (a
        fresh edit invalidates whatever redo chain existed, same as any
        conventional undo stack)."""
        before = markers_before if markers_before is not None else self._markers_copy()
        self._undo.append((self.buffer, before))
        if len(self._undo) > self.UNDO_LIMIT:
            self._undo.pop(0)
        self.buffer = np.ascontiguousarray(new_buffer, dtype=np.float32)
        self._redo.clear()
        self.dirty = True

    def undo(self):
        if not self._undo:
            return False
        self._redo.append((self.buffer, self._markers_copy()))
        self.buffer, self.markers = self._undo.pop()
        self.dirty = True
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append((self.buffer, self._markers_copy()))
        self.buffer, self.markers = self._redo.pop()
        self.dirty = True
        return True

    def mark_exported(self):
        """Clears the dirty flag — called once an Export actually
        succeeds, so the "unsaved changes" indicator reflects the file on
        disk, not just whether any edit was ever made this session."""
        self.dirty = False

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    @property
    def duration(self):
        return len(self.buffer) / self.sample_rate if self.sample_rate else 0.0

    # -- editing operations ---------------------------------------------
    # Each is a numpy slice/concatenate producing a brand-new buffer, then
    # handed to apply() — so every one of these is a single undo step.

    def _clamp_index(self, seconds):
        return max(0, min(len(self.buffer), int(round(seconds * self.sample_rate))))

    def _clamp_range(self, start_s, end_s):
        i0, i1 = self._clamp_index(start_s), self._clamp_index(end_s)
        return (i0, i1) if i0 <= i1 else (i1, i0)

    def sample_bounds(self, start_s, end_s):
        """Public version of _clamp_range — for callers (app.py) that need
        to translate a selection into sample indices without reaching
        into a private method, e.g. to splice a processed slice back in."""
        return self._clamp_range(start_s, end_s)

    def _shift_markers_after_removal(self, removed_ranges_s):
        """Call after deciding which [start_s, end_s) spans are about to
        be cut from the timeline (in current, pre-removal coordinates),
        before actually slicing self.buffer. Markers inside a removed
        span collapse to its start; markers after a span shift left by
        its duration — mirrors exactly what happens to the audio itself."""
        ranges = sorted(removed_ranges_s)
        new_markers = []
        for m in self.markers:
            t = m["time"]
            shift = 0.0
            collapsed_to = None
            for r0, r1 in ranges:
                if r1 <= t:
                    shift += (r1 - r0)
                elif r0 <= t < r1:
                    collapsed_to = max(0.0, r0 - shift)
                    break
            new_time = collapsed_to if collapsed_to is not None else max(0.0, t - shift)
            new_markers.append({**m, "time": new_time})
        self.markers = new_markers

    def _shift_markers_after_insertion(self, at_s, duration_s):
        """Call after deciding where/how much is about to be inserted,
        before actually splicing self.buffer — markers at or after the
        insertion point move forward by duration_s, same as the audio
        after that point does."""
        for m in self.markers:
            if m["time"] >= at_s:
                m["time"] += duration_s

    def trim(self, start_s, end_s):
        """Keeps only [start_s, end_s), discarding everything outside it."""
        i0, i1 = self._clamp_range(start_s, end_s)
        before = self._markers_copy()
        start_time, end_time = i0 / self.sample_rate, i1 / self.sample_rate
        self.markers = [
            {**m, "time": m["time"] - start_time}
            for m in self.markers if start_time <= m["time"] < end_time
        ]
        self.apply(self.buffer[i0:i1], markers_before=before)

    def cut(self, start_s, end_s):
        """Removes [start_s, end_s) and returns the removed audio, so the
        caller can hold it for a later Paste."""
        i0, i1 = self._clamp_range(start_s, end_s)
        removed = self.buffer[i0:i1].copy()
        before = self._markers_copy()
        self._shift_markers_after_removal([(i0 / self.sample_rate, i1 / self.sample_rate)])
        self.apply(np.concatenate([self.buffer[:i0], self.buffer[i1:]]), markers_before=before)
        return removed

    def copy_region(self, start_s, end_s):
        i0, i1 = self._clamp_range(start_s, end_s)
        return self.buffer[i0:i1].copy()

    def paste(self, at_s, clip_buffer):
        i = self._clamp_index(at_s)
        before = self._markers_copy()
        self._shift_markers_after_insertion(i / self.sample_rate, len(clip_buffer) / self.sample_rate)
        self.apply(np.concatenate([self.buffer[:i], clip_buffer, self.buffer[i:]]), markers_before=before)

    def split(self, at_s):
        """Returns (before, after) arrays split at at_s, without mutating
        this clip — whether that becomes two separate clips or one is a
        decision for the caller, not this model. Callers that then
        apply() one half back (Audio Studio's Split action does — see
        app.py) are responsible for their own marker bookkeeping, same
        reasoning: this method doesn't know which half the caller keeps."""
        i = self._clamp_index(at_s)
        return self.buffer[:i].copy(), self.buffer[i:].copy()

    def join(self, other_buffer):
        # Not currently wired to any UI action; markers are left as-is
        # (nothing before the join point moves) rather than guessing
        # what, if anything, the appended audio's own markers should be.
        self.apply(np.concatenate([self.buffer, other_buffer]))

    def insert_silence(self, at_s, duration_s):
        i = self._clamp_index(at_s)
        silence = np.zeros(max(0, int(duration_s * self.sample_rate)), dtype=np.float32)
        before = self._markers_copy()
        self._shift_markers_after_insertion(i / self.sample_rate, len(silence) / self.sample_rate)
        self.apply(np.concatenate([self.buffer[:i], silence, self.buffer[i:]]), markers_before=before)

    def apply_removing_ranges(self, new_buffer, removed_ranges_s):
        """Like apply(), but also shifts markers as if `removed_ranges_s`
        (spans in the CURRENT, pre-edit buffer's own coordinates) had
        just been cut out — for a caller that computed its own
        replacement buffer outside this class (Clean's pause-shortening
        effects, via audio_clean.remove_long_pauses's return_ranges=True)
        but still needs markers kept in sync with what actually moved."""
        before = self._markers_copy()
        self._shift_markers_after_removal(removed_ranges_s)
        self.apply(new_buffer, markers_before=before)

    def remove_ranges(self, ranges_s):
        """Removes every [start_s, end_s) span in `ranges_s` in one step
        (one undo entry) — e.g. every occurrence of a repeated phrase
        found via Find Similar Segments. Ranges may be given in any
        order; overlapping ones are merged first so nothing is double-
        removed."""
        if not ranges_s:
            return
        bounds = sorted(self._clamp_range(s, e) for s, e in ranges_s)
        merged = []
        for i0, i1 in bounds:
            if merged and i0 <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], i1))
            else:
                merged.append((i0, i1))
        pieces = []
        prev_end = 0
        for i0, i1 in merged:
            pieces.append(self.buffer[prev_end:i0])
            prev_end = i1
        pieces.append(self.buffer[prev_end:])
        self.apply_removing_ranges(
            np.concatenate(pieces),
            [(i0 / self.sample_rate, i1 / self.sample_rate) for i0, i1 in merged])

    # -- markers ----------------------------------------------------------
    # Adding/removing a marker is a lightweight annotation, not an audio
    # edit — deliberately NOT pushed onto the undo/redo stack (which is
    # scoped to buffer-changing operations); only the shifting above, tied
    # to an actual edit's apply(), goes through undo/redo.

    def add_marker(self, time_s, label, auto=False):
        time_s = max(0.0, min(self.duration, time_s))
        self.markers.append({"time": time_s, "label": label, "auto": auto})
        self.markers.sort(key=lambda m: m["time"])

    def remove_marker_at(self, index):
        if 0 <= index < len(self.markers):
            self.markers.pop(index)

    def replace_auto_markers(self, times_and_labels):
        """Wholesale-replaces every auto=True marker (from a Detect
        Sections run) while leaving user-added ones untouched — re-
        running detection is meant to refresh the automatic set, not
        pile up duplicates alongside it."""
        self.markers = [m for m in self.markers if not m.get("auto")] + [
            {"time": t, "label": label, "auto": True} for t, label in times_and_labels
        ]
        self.markers.sort(key=lambda m: m["time"])

    # -- waveform -------------------------------------------------------

    def peaks(self, start_s, end_s, pixel_count):
        """Returns (mins, maxes) float32 arrays of length `pixel_count`,
        one min/max amplitude pair per horizontal pixel across
        [start_s, end_s) — enough to draw a waveform without iterating
        every sample on every redraw/zoom/scroll."""
        return peaks_from_buffer(self.buffer, self.sample_rate, start_s, end_s, pixel_count)

    # -- export -----------------------------------------------------------

    def save_wav(self, path):
        write_wav(path, self.buffer, self.sample_rate)
