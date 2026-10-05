"""Live subtitles for the Live Translate tab: text only (no background panel)
at the bottom of a chosen screen (a projector, a second monitor), rolling
like broadcast captions.

What the viewer sees is chosen with LAYOUTS (and, separately, with "show
original": an audience that cannot read the source language sees translations
only):

    "none"    no lines at all: nothing is drawn on the subtitle screen
    "two"     the CURRENT sentence only: its original (live, as it is being
              spoken) and its translation underneath                    (2 lines)
    "three"   + the PREVIOUS sentence's translation, dimmer             (3 lines)
    "four"    + the PREVIOUS sentence, original and translation         (4 lines)
    "scroll"  a SCROLL of the last three translations (the newest white,
              older ones dimmer) and, separately underneath, a small LIVE
              STRIP with the original words as they are spoken right now

When a new sentence starts the picture ROLLS: older lines slide up, the new
one slides in. Nothing blinks off: lines stay on screen until something pushes
them out; after a long silence they roll away one by one.

Parts, so the timing rules and the drawing can be tested without a real screen:

  SubtitleController  pure logic: which sentences are on screen, when they go.
  SubtitleRenderer    draws a controller view onto any Tk canvas (outlined text,
                      rolling animation, in-place updates while a translation
                      streams in).
  SubtitleWindow      the transparent, borderless, always-on-top window around a
                      renderer. On Windows the background is a colour key (those
                      pixels are see-through and clickable), on macOS it is a
                      transparent window; elsewhere the window is hidden while
                      there is nothing to show.
  LayoutPreview       a window that runs every layout side by side with the same
                      demo speech, so the viewer can pick one by eye.
"""

import collections
import ctypes
import re
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

MIN_HOLD_S = 2.5               # reading time of a finished pair: at least...
MAX_HOLD_S = 8.0               # ...at most (counted from when its translation arrived)
HOLD_BASE_S = 1.2
HOLD_PER_CHAR_S = 0.06
TRIM_STEP_S = 2.0              # after hold + 1 step only 2 sentences stay, after 2 steps only 1...
CLEAR_AFTER_S = 8.0            # ...and after hold + this much silence the last one rolls away too
PENDING_HOLD_S = 6.0           # an untranslated sentence counts as read after this
PREVIEW_STALE_S = 6.0          # live words nobody refreshed any more are dropped
KEEP_SENTENCES = 4             # the controller remembers this many sentences (the scroll needs up to 4 lines)

SCALE_MIN, SCALE_MAX = 0.5, 2.0            # overall subtitle size (x the default)
ORIG_RATIO_MIN, ORIG_RATIO_MAX = 0.4, 1.0  # original line size relative to the translation
DEFAULT_SCALE, DEFAULT_ORIG_RATIO = 1.0, 0.66
_BASE_FRAC = 0.056                         # translation size at scale 1.0, of screen height

ROLL_MS, ROLL_STEPS = 240, 10

LAYOUTS = ("none", "two", "three", "four", "scroll")     # everything the renderer can draw
CHOICES = ("three", "four", "scroll")                    # what the viewer is offered
DEFAULT_LAYOUT = "three"

SCROLL_LINES_CHOICES = (2, 3, 4)       # lines of translation the scroll layout shows (counted in screen lines)
DEFAULT_SCROLL_LINES = 2


def normalize_layout(value):
    return value if value in LAYOUTS else DEFAULT_LAYOUT


def normalize_choice(value):
    """A layout the viewer may choose (the dropdown's)."""
    return value if value in CHOICES else DEFAULT_LAYOUT


def normalize_scroll_lines(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SCROLL_LINES
    return n if n in SCROLL_LINES_CHOICES else DEFAULT_SCROLL_LINES


_WRAP_TOKEN = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]|[^\s\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+|\s+")
_NO_LINE_START = "\uff0c\u3002\u3001\uff01\uff1f\uff1b\uff1a\uff09\u300d\u300f\u201d\u2019,.!?;:)]}"


def tail_fit(text, measure, max_px):
    """The most recent words of `text` that fit one line (older words drop off
    the front, like a ticker). No ellipsis: it only ran into the outline."""
    text = (text or "").strip()
    if not text or measure(text) <= max_px:
        return text
    tokens = _WRAP_TOKEN.findall(text)
    i = 0
    while i < len(tokens) - 1 and measure("".join(tokens[i:]).strip()) > max_px:
        i += 1
    return "".join(tokens[i:]).strip()


def wrap_lines(text, measure, max_px):
    """Breaks `text` into screen lines no wider than max_px (measure(str) -> px).
    Words are kept whole, Chinese/Japanese/Korean break between any two
    characters, and closing punctuation never starts a line. Done here, not by
    Tk, so the scroll layout knows exactly how many lines a sentence takes."""
    lines, cur, space = [], "", False
    for tok in _WRAP_TOKEN.findall(text or ""):
        if tok.isspace():
            space = bool(cur)
            continue
        trial = cur + (" " if space and cur else "") + tok
        space = False
        if not cur or measure(trial) <= max_px or tok[0] in _NO_LINE_START:
            cur = trial
        else:
            lines.append(cur)
            cur = tok
    if cur:
        lines.append(cur)
    return lines


def clamp(value, low, high, default):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


class SubtitleController:
    """Decides which sentences are on screen. view() returns None, or

        {"current":  (original, translation),          # the newest sentence
         "previous": (original, translation) or None,  # the one before it
         "history":  [{"seq", "src", "tr"}, ...],      # up to 3, oldest first
         "seq": int, "prev_seq": int or None}

    `seq` identifies a sentence, so a window can tell "the same sentence got
    more translation" from "a new sentence started: roll". The translation is
    shown as it grows (it streams in as the model writes it)."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.reset()

    def reset(self):
        self._items = []           # oldest first, at most KEEP_SENTENCES
        self._seq = 0

    @staticmethod
    def hold_s(translation, original=""):
        n = max(len(translation or ""), len(original or "") * 0.8)
        return max(MIN_HOLD_S, min(MAX_HOLD_S, HOLD_BASE_S + HOLD_PER_CHAR_S * n))

    def _new_item(self, **fields):
        self._seq += 1
        item = {"seq": self._seq, "seg": None, "live": False, "src": "", "tr": "",
                "final_at": None, "touched": self._clock()}
        item.update(fields)
        self._items.append(item)
        del self._items[:-KEEP_SENTENCES]
        return item

    def on_preview(self, text, translation=""):
        """The sentence being spoken right now (not finished yet), '' when
        none, and the translation of its clauses that are already finished."""
        text = (text or "").strip()
        if not text:
            return
        now = self._clock()
        newest = self._items[-1] if self._items else None
        if newest is not None and newest["live"]:
            newest["src"], newest["touched"] = text, now
            newest["tr"] = translation or ""
        else:
            self._new_item(live=True, src=text, tr=translation or "")

    def on_segment(self, seg_id, source, translation, state):
        now = self._clock()
        item = next((i for i in self._items if i["seg"] == seg_id), None)
        if item is None:
            newest = self._items[-1] if self._items else None
            if newest is not None and newest["live"]:
                item = newest                       # the live words became this sentence
                item["live"], item["seg"] = False, seg_id
            else:
                item = self._new_item(seg=seg_id)
        item["src"], item["tr"] = source, translation
        if state == "failed" and not translation:
            item["tr"] = "⚠"
        if state in ("final", "failed") and item["final_at"] is None:
            item["final_at"] = now
        if state == "pending":
            item["touched"] = now

    def view(self):
        now = self._clock()
        # live words nobody refreshed any more: gone
        self._items = [i for i in self._items
                       if not (i["live"] and now - i["touched"] >= PREVIEW_STALE_S)]
        if not self._items:
            return None
        newest = self._items[-1]
        if not newest["live"]:
            if newest["final_at"] is not None:
                idle = now - newest["final_at"]
                hold = self.hold_s(newest["tr"], newest["src"])
            else:
                idle, hold = now - newest["touched"], PENDING_HOLD_S
            if idle >= hold + CLEAR_AFTER_S:
                self._items.clear()
                return None
            # after a long silence the older lines roll away one by one
            keep = (KEEP_SENTENCES if idle < hold + TRIM_STEP_S
                    else 2 if idle < hold + 2 * TRIM_STEP_S else 1)
            if len(self._items) > keep:
                del self._items[:-keep]
        newest = self._items[-1]
        previous = self._items[-2] if len(self._items) > 1 else None
        pair = lambda i: (i["src"], i["tr"])        # noqa: E731
        return {"current": pair(newest), "seq": newest["seq"],
                "previous": pair(previous) if previous else None,
                "prev_seq": previous["seq"] if previous else None,
                "history": [{"seq": i["seq"], "src": i["src"], "tr": i["tr"]} for i in self._items]}


# ------------------------------------------------------------------ screens

def list_monitors(root=None):
    """[(x, y, width, height, is_primary), ...] in pixels. Windows enumerates
    every attached screen; elsewhere only the main screen is known to Tk."""
    if sys.platform == "win32":
        try:
            rects = []

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

            proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.POINTER(RECT), ctypes.c_void_p)

            def callback(hmon, _hdc, _rect, _data):
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
                r = info.rcMonitor
                rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top,
                              bool(info.dwFlags & 1)))
                return 1

            ctypes.windll.user32.EnumDisplayMonitors(None, None, proto(callback), 0)
            if rects:
                rects.sort(key=lambda m: (not m[4], m[0], m[1]))     # primary first
                return rects
        except Exception:
            pass
    if root is not None:
        return [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight(), True)]
    return [(0, 0, 1920, 1080, True)]


def monitor_label(index, monitor, main_word):
    _x, _y, w, h, primary = monitor
    return f"{index + 1}: {w}×{h}" + (f" ({main_word})" if primary else "")


# ----------------------------------------------------------------- renderer

class SubtitleRenderer:
    """Draws SubtitleController views onto a Tk canvas whose size stands for the
    bottom part of a screen of size screen_w x screen_h."""

    OUTLINE = "#000000"
    ORIGINAL_FG, TRANSLATION_FG = "#f3dc8a", "#ffffff"            # soft gold / white
    PREV_ORIGINAL_FG, PREV_TRANSLATION_FG = "#a8996a", "#c4c4c4"  # the same, dimmed
    OLDEST_TRANSLATION_FG = "#8c8c8c"                             # scroll layout: the oldest line
    FIT_STEPS = (1.0, 0.88, 0.76, 0.66, 0.56, 0.48, 0.40, 0.33, 0.27)

    def __init__(self, canvas, screen_w, screen_h, scale=DEFAULT_SCALE,
                 orig_ratio=DEFAULT_ORIG_RATIO, show_original=True, layout=DEFAULT_LAYOUT,
                 scroll_lines=DEFAULT_SCROLL_LINES):
        self.canvas = canvas
        self.mon = (0, 0, int(screen_w), int(screen_h))
        self.bar_h = int(screen_h * 0.62)
        self.scale = clamp(scale, SCALE_MIN, SCALE_MAX, DEFAULT_SCALE)
        self.orig_ratio = clamp(orig_ratio, ORIG_RATIO_MIN, ORIG_RATIO_MAX, DEFAULT_ORIG_RATIO)
        self.show_original = bool(show_original)
        self.layout = normalize_layout(layout)
        self.scroll_lines = normalize_scroll_lines(scroll_lines)
        self._view = None            # what is on screen (after the layout filter)
        self._raw_view = None        # what the controller reported (to recognise a new sentence)
        self._settings_key = None
        self._visible = False
        self._roll_job = None
        self._roll_state = None
        self._fit = 1.0
        # Fonts are created once per (weight, size) and NEVER reconfigured:
        # changing a named font that is in use makes Tk re-measure everything
        # that uses it and, as measured, set off a layout pass over the whole
        # application window (every control, the dropdowns above all, flickered
        # for each new subtitle).
        self._font_cache = {}

    # -- settings ---------------------------------------------------------------

    def _changed(self):
        self._settings_key = None          # forces a plain redraw, no animation

    def set_screen(self, width, height):
        self.mon = (0, 0, int(width), int(height))
        self.bar_h = int(height * 0.62)
        self._changed()

    def set_scale(self, scale):
        self.scale = clamp(scale, SCALE_MIN, SCALE_MAX, DEFAULT_SCALE)
        self._changed()

    def set_orig_ratio(self, ratio):
        self.orig_ratio = clamp(ratio, ORIG_RATIO_MIN, ORIG_RATIO_MAX, DEFAULT_ORIG_RATIO)
        self._changed()

    def set_show_original(self, flag):
        self.show_original = bool(flag)
        self._changed()

    def set_layout(self, layout):
        self.layout = normalize_layout(layout)
        self._changed()

    def set_scroll_lines(self, n):
        self.scroll_lines = normalize_scroll_lines(n)
        self._changed()

    # -- fonts / geometry ---------------------------------------------------------

    @staticmethod
    def _family():
        return "Segoe UI" if sys.platform == "win32" else "Helvetica"

    def _font(self, bold, px):
        key = (bool(bold), int(px))
        font = self._font_cache.get(key)
        if font is None:
            font = self._font_cache[key] = tkfont.Font(
                family=self._family(), size=-key[1], weight="bold" if bold else "normal")
        return font

    def _max_h(self):
        return self.bar_h - int(self.mon[3] * 0.02)

    def _bottom(self):
        return self.bar_h - int(self.mon[3] * 0.025)

    def _block_gap(self):
        return max(6, int(self.mon[3] * 0.018))

    def _gap(self):
        return max(2, int(self.mon[3] * 0.008))

    def _sizes(self, fit):
        h = self.mon[3]
        base = max(12, int(h * _BASE_FRAC * self.scale * fit))
        return base, max(9, int(base * self.orig_ratio))

    def _after(self, ms, fn):
        return self.canvas.after(ms, fn)

    # -- drawing primitives -------------------------------------------------------

    def _line(self, text, font, px, fill, kind, blk, cursor, stage="new", extra=()):
        """One wrapped text, bottom edge at y=cursor, with a black outline (the
        same text drawn shifted around it). Returns the y of its top edge, less
        one gap. Items are tagged with the stage, block, kind and `extra`."""
        c, w = self.canvas, self.mon[2]
        wrap, cx = int(w * 0.90), w // 2
        o = max(2, int(px / 16))
        for dx in (-o, 0, o):
            for dy in (-o, 0, o):
                if dx or dy:
                    c.create_text(cx + dx, cursor + dy, text=text, font=font, fill=self.OUTLINE,
                                  width=wrap, anchor="s", justify="center",
                                  tags=(stage, blk, "ol", "ol_" + kind) + tuple(extra))
        item = c.create_text(cx, cursor, text=text, font=font, fill=fill, width=wrap,
                             anchor="s", justify="center", tags=(stage, blk, kind) + tuple(extra))
        box = c.bbox(item)
        return (box[1] if box else cursor) - self._gap()

    def _block(self, original, translation, fit, bottom, dim, blk, reserve):
        """Draws one pair bottom-up so that it ends at y=bottom; returns the y
        of its top edge."""
        base, orig_px = self._sizes(fit)
        f_tr, f_orig = self._font(True, base), self._font(False, orig_px)
        cursor = bottom
        if not self.show_original:
            original = ""
        if translation:
            cursor = self._line(translation, f_tr, base,
                                self.PREV_TRANSLATION_FG if dim else self.TRANSLATION_FG,
                                "tr", blk, cursor)
        elif original and reserve:
            cursor -= f_tr.metrics("linespace") + self._gap()     # keep the translation's place free
        if original:
            cursor = self._line(original, f_orig, orig_px,
                                self.PREV_ORIGINAL_FG if dim else self.ORIGINAL_FG,
                                "orig", blk, cursor)
        return cursor

    # -- scroll layout pieces -----------------------------------------------------
    #
    # Top to bottom: the scroll of translations (as many SCREEN LINES as the viewer
    # chose, the newest sentence white, older ones dimmer), a small gap, and the
    # live strip with the original words. The strip stays at the bottom and is
    # ALWAYS ONE LINE (the most recent words that fit, like a ticker), so the
    # translations sit right on top of it and never move when it changes.

    def _wrap_px(self):
        return int(self.mon[2] * 0.90)

    def _scroll_geometry(self, fit):
        """-> (stack_bottom_y, strip_top_y, translation_pitch, original_pitch)."""
        base, orig_px = self._sizes(fit)
        f_tr, f_orig = self._font(True, base), self._font(False, orig_px)
        tr_ls, orig_ls = f_tr.metrics("linespace"), f_orig.metrics("linespace")
        bottom = self._bottom()
        if not self.show_original:
            return bottom, bottom, tr_ls, orig_ls
        strip_top = bottom - orig_ls
        return strip_top - max(2, int(tr_ls * 0.25)), strip_top, tr_ls, orig_ls

    def _scroll_visual_lines(self, view, fit):
        """[(text, rank)] oldest first: the last `scroll_lines` screen lines of
        the translations; rank 0 = the newest sentence, 1 = the one before..."""
        base, _orig_px = self._sizes(fit)
        font = self._font(True, base)
        sentences = [h["tr"] for h in view["history"] if h["tr"]]
        out = []
        for i, text in enumerate(sentences):
            rank = len(sentences) - 1 - i
            out.extend((ln, rank) for ln in wrap_lines(text, font.measure, self._wrap_px()))
        return out[-self.scroll_lines:]

    def _strip_lines(self, view, fit):
        if not self.show_original or not view["history"]:
            return []
        _base, orig_px = self._sizes(fit)
        font = self._font(False, orig_px)
        text = tail_fit(view["history"][-1]["src"], font.measure, self._wrap_px())
        return [text] if text else []

    def _draw_strip(self, view, fit, stage="new"):
        """The live strip: the original words as they are spoken right now."""
        if not self.show_original:
            return
        _stack_bottom, strip_top, _tr, orig_ls = self._scroll_geometry(fit)
        _base, orig_px = self._sizes(fit)
        font = self._font(False, orig_px)
        for k, text in enumerate(self._strip_lines(view, fit)):
            self._line(text, font, orig_px, self.ORIGINAL_FG, "orig", "strip",
                       strip_top + (k + 1) * orig_ls, stage, extra=("sp%d" % k,))

    def _stack_fill(self, rank):
        return (self.TRANSLATION_FG, self.PREV_TRANSLATION_FG, self.OLDEST_TRANSLATION_FG)[min(rank, 2)]

    def _draw_stack(self, view, fit, stage="new"):
        """The translation scroll, newest line at the bottom; returns the y of
        its top edge."""
        base, _orig_px = self._sizes(fit)
        font = self._font(True, base)
        stack_bottom, _strip_top, tr_ls, _orig_ls = self._scroll_geometry(fit)
        lines = self._scroll_visual_lines(view, fit)
        for idx, (text, rank) in enumerate(lines):
            y = stack_bottom - (len(lines) - 1 - idx) * tr_ls
            self._line(text, font, base, self._stack_fill(rank), "tr", "stack", y, stage,
                       extra=("sl%d" % idx,))
        return stack_bottom - len(lines) * tr_ls

    def _layout_scroll(self, view, fit):
        self._draw_strip(view, fit)
        top = self._draw_stack(view, fit)
        return self._bottom() - top

    # -- whole-picture drawing ----------------------------------------------------

    def _layout(self, view, fit):
        """Draws the whole picture; returns the height it needs."""
        if self.layout == "scroll":
            return self._layout_scroll(view, fit)
        bottom = self._bottom()
        cur_o, cur_t = view["current"]
        top = self._block(cur_o, cur_t, fit, bottom, False, "blk_cur", True)
        if view["previous"]:
            prev_o, prev_t = view["previous"]
            if self.layout != "four":
                prev_o = ""                              # 3 lines: only the previous translation
            if prev_o and self.show_original or prev_t:
                top = self._block(prev_o, prev_t, fit, top - self._block_gap(), True,
                                  "blk_prev", False)
        return bottom - top

    def _redraw(self, view):
        # Draw the new text first and only then remove the old, so there is no
        # blank frame between two states.
        for idx, fit in enumerate(self.FIT_STEPS):
            used = self._layout(view, fit)
            if used <= self._max_h() or idx == len(self.FIT_STEPS) - 1:
                break
            self.canvas.delete("new")
        self._fit = fit
        self.canvas.delete("cur")
        self.canvas.addtag_withtag("cur", "new")
        self.canvas.dtag("new")

    # -- render -------------------------------------------------------------------

    def render(self, view):
        """view: what SubtitleController.view() returns, or None to clear.
        Redraws only when the content or a setting actually changed; a new
        sentence rolls the old ones up. Returns True while something is shown."""
        raw = view
        if view is not None and self.layout == "two":
            view = dict(view, previous=None, prev_seq=None)
        settings_key = (self.show_original, self.layout, self.scale, self.orig_ratio, self.mon,
                        self.scroll_lines)
        if view == self._view and settings_key == self._settings_key:
            return self._visible
        old, old_raw, old_key = self._view, self._raw_view, self._settings_key
        self._view, self._raw_view, self._settings_key = view, raw, settings_key
        self._finish_roll()                          # never leave an animation half-way
        if self.layout == "none":
            self.canvas.delete("cur")
            self._visible = False
            return False
        if view is None or not (view["current"][0] or view["current"][1]
                                or any(h["tr"] for h in view["history"])):
            self.canvas.delete("cur")
            self._visible = False
            return False
        self._visible = True
        if self.layout == "scroll":
            if old is not None and old_key == settings_key and self._scroll_update(old, view):
                return True
            self._redraw(view)
            return True
        if (old is not None and old_key == settings_key and self._stream_update(old, view)):
            return True
        rolling = (old_raw is not None and old_key == settings_key
                   and raw.get("prev_seq") is not None and raw.get("prev_seq") == old_raw.get("seq")
                   and raw.get("seq") != old_raw.get("seq") and old_raw["current"][0])
        if rolling:
            self._roll(view)
        else:
            self._redraw(view)
        return True

    # -- in-place update while a translation streams in -----------------------------

    def _stream_update(self, old, view):
        """The same sentence, the same words, only its translation grew (the
        model is still writing it): change the text of the lines already on
        screen instead of redrawing, so nothing flashes or jumps. Returns False
        when a redraw is needed (e.g. the translation now needs another line)."""
        if (old is None or view.get("seq") != old.get("seq") or view.get("prev_seq") != old.get("prev_seq")
                or view["previous"] != old["previous"] or view["current"][0] != old["current"][0]
                or not old["current"][1] or not view["current"][1]):
            return False
        c = self.canvas
        main = c.find_withtag("blk_cur&&tr")
        outline = c.find_withtag("blk_cur&&ol_tr")
        orig = c.find_withtag("blk_cur&&orig")
        if not main:
            return False
        text = view["current"][1]
        for item in main + outline:
            c.itemconfigure(item, text=text)
        box_t = c.bbox(main[0])
        box_o = c.bbox(orig[0]) if orig else None
        if box_t is None or box_t[1] < 0 or (box_o is not None and box_t[1] < box_o[3] - 1):
            return False                       # it wrapped onto another line: lay everything out again
        return True

    # -- the roll (two / three / four) ----------------------------------------------

    def _roll(self, view):
        """A new sentence started: the old previous slides up and away, the old
        current slides up into the previous slot (dimming), and the new current
        slides in from below."""
        c = self.canvas
        c.delete("dying")
        c.addtag_withtag("dying", "blk_prev")          # the old previous: leaves upward
        c.dtag("blk_prev")
        if self.layout == "two":
            c.addtag_withtag("dying", "blk_cur")        # nothing is kept: the old current leaves too
            c.dtag("blk_cur")
        else:
            if self.layout == "three":                  # only its translation stays on as "previous"
                c.addtag_withtag("dying", "blk_cur&&(orig||ol_orig)")
                c.dtag("blk_cur&&(orig||ol_orig)", "blk_cur")
            for item in c.find_withtag("blk_cur"):      # the old current: becomes the previous
                tags = c.gettags(item)
                if "tr" in tags:
                    c.itemconfigure(item, fill=self.PREV_TRANSLATION_FG)
                elif "orig" in tags:
                    c.itemconfigure(item, fill=self.PREV_ORIGINAL_FG)
            c.addtag_withtag("blk_prev", "blk_cur")
            c.dtag("blk_cur")
        cur_o, cur_t = view["current"]
        top = self._block(cur_o, cur_t, self._fit, self._bottom(), False, "blk_cur", True)
        new_h = self._bottom() - top
        c.addtag_withtag("rolling_in", "new")
        c.dtag("new")
        rise = new_h + self._block_gap()                # how far the older lines have to move up
        slide_in = rise + max(4, int(self.mon[3] * 0.03))
        c.move("rolling_in", 0, slide_in)               # starts below its slot (clipped by the window edge)
        self._roll_state = {"kind": "roll", "step": 0, "rise": rise, "in": slide_in, "moved": 0.0,
                            "moved_in": 0.0, "view": view}
        self._roll_step()

    # -- the scroll layout: updates -------------------------------------------------

    def _scroll_update(self, old, view):
        """Updates the scroll layout as cheaply as the change allows. Returns
        False when a plain redraw is needed."""
        fit = self._fit
        o, n = self._scroll_visual_lines(old, fit), self._scroll_visual_lines(view, fit)
        strip_changed = self._strip_lines(old, fit) != self._strip_lines(view, fit)
        ot, nt = [t for t, _ in o], [t for t, _ in n]
        if o == n:                                       # only the live words moved on
            if strip_changed:
                self._redraw_strip(view)
            return True
        if o and len(o) == len(n) and [r for _, r in o] == [r for _, r in n]:
            self._scroll_inplace(ot, nt)                  # the newest lines are still being written
            if strip_changed:
                self._redraw_strip(view)
            return True
        if not o:
            return False
        # lines were added at the bottom (and the oldest scrolled off the top)
        for k in range(1, len(n) + 1):
            kept = len(n) - k                              # old lines that stay on screen
            if kept <= len(o) and kept >= 0 and ot[len(o) - kept:] == nt[:kept] and (
                    kept == len(o) or len(o) == self.scroll_lines):
                if kept == 0:
                    return False                           # nothing in common: no point sliding
                if strip_changed:
                    self._redraw_strip(view)
                self._scroll_slide(o, n, k, kept)
                return True
        return False

    def _scroll_inplace(self, old_texts, new_texts):
        c = self.canvas
        for idx, (a, b) in enumerate(zip(old_texts, new_texts)):
            if a != b:
                for item in c.find_withtag("stack&&sl%d&&(tr||ol_tr)" % idx):
                    c.itemconfigure(item, text=b)

    def _redraw_strip(self, view):
        self._draw_strip(view, self._fit, "newstrip")
        self.canvas.delete("strip&&cur")
        self.canvas.addtag_withtag("cur", "newstrip")
        self.canvas.dtag("newstrip")

    def _scroll_slide(self, old_lines, new_lines, k, kept):
        """k new lines appear at the bottom of the scroll: the lines that stay
        slide up by k line heights (and take the colour they will have), the
        ones that scroll off slide out; at the end the picture is redrawn clean."""
        c = self.canvas
        drop = len(old_lines) - kept
        for j in range(drop, len(old_lines)):               # old line j is new line j - drop
            fill = self._stack_fill(new_lines[j - drop][1])
            for item in c.find_withtag("stack&&cur&&sl%d&&tr" % j):
                c.itemconfigure(item, fill=fill)
        pitch = self._scroll_geometry(self._fit)[2]
        self._roll_state = {"kind": "scroll", "step": 0, "rise": k * pitch, "moved": 0.0,
                            "view": self._view}
        self._roll_step()

    # -- animation driver -------------------------------------------------------------

    def _roll_step(self):
        st = self._roll_state
        if st is None:
            return
        st["step"] += 1
        t = st["step"] / ROLL_STEPS
        ease = 1 - (1 - t) ** 3
        up = st["rise"] * ease - st["moved"]
        st["moved"] += up
        c = self.canvas
        if st["kind"] == "scroll":
            c.move("stack&&cur", 0, -up)
        else:
            down_in = st["in"] * ease - st["moved_in"]
            st["moved_in"] += down_in
            c.move("blk_prev", 0, -up)
            c.move("dying", 0, -up)
            c.move("rolling_in", 0, -down_in)
        if st["step"] >= ROLL_STEPS:
            self._finish_roll()
        else:
            self._roll_job = self._after(max(1, ROLL_MS // ROLL_STEPS), self._roll_step)

    def _finish_roll(self):
        """Ends a running animation at once: its final picture is replaced by a
        clean, exactly laid-out one (same position, so nothing visibly moves)."""
        if self._roll_job is not None:
            try:
                self.canvas.after_cancel(self._roll_job)
            except tk.TclError:
                pass
            self._roll_job = None
        st, self._roll_state = self._roll_state, None
        if st is None:
            return
        c = self.canvas
        c.delete("dying")
        c.addtag_withtag("rolling_in_done", "rolling_in")
        self._redraw(st["view"])
        c.delete("rolling_in_done")

    def destroy(self):
        self._finish_roll()


# ------------------------------------------------------------------- window

class SubtitleWindow:
    """The subtitle renderer in a transparent, borderless, always-on-top window
    along the bottom of one screen."""

    KEY = "#010101"            # colour key: pixels of exactly this colour are see-through

    def __init__(self, master, monitor, scale=DEFAULT_SCALE, orig_ratio=DEFAULT_ORIG_RATIO,
                 show_original=True, layout=DEFAULT_LAYOUT, scroll_lines=DEFAULT_SCROLL_LINES):
        self.master = master
        self._mode = ("windows" if sys.platform == "win32"
                      else "mac" if sys.platform == "darwin" else "plain")
        self._mapped = False
        self.top = tk.Toplevel(master)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        bg = self.KEY
        try:
            if self._mode == "windows":
                self.top.configure(bg=self.KEY)
                self.top.attributes("-transparentcolor", self.KEY)
            elif self._mode == "mac":
                bg = "systemTransparent"
                self.top.configure(bg=bg)
                self.top.attributes("-transparent", True)
            else:
                self.top.configure(bg=self.KEY)
        except tk.TclError:
            self._mode = "plain"
        self.canvas = tk.Canvas(self.top, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        x, y, w, h, _primary = monitor
        self.renderer = SubtitleRenderer(self.canvas, w, h, scale, orig_ratio, show_original, layout,
                                         scroll_lines)
        self.set_monitor(monitor)

    # the renderer's numbers, for callers and tests
    mon = property(lambda self: (self._pos[0], self._pos[1], self.renderer.mon[2], self.renderer.mon[3]))
    bar_h = property(lambda self: self.renderer.bar_h)
    layout = property(lambda self: self.renderer.layout)
    scroll_lines = property(lambda self: self.renderer.scroll_lines)
    show_original = property(lambda self: self.renderer.show_original)
    scale = property(lambda self: self.renderer.scale)
    orig_ratio = property(lambda self: self.renderer.orig_ratio)

    def set_monitor(self, monitor):
        x, y, w, h, _primary = monitor
        self._pos = (x, y)
        self.renderer.set_screen(w, h)
        # Tall enough for the biggest size with the biggest layout; the window
        # is see-through everywhere there is no text.
        self.top.geometry(f"{w}x{self.renderer.bar_h}{x:+d}{y + h - self.renderer.bar_h:+d}")

    def set_scale(self, scale):
        self.renderer.set_scale(scale)

    def set_orig_ratio(self, ratio):
        self.renderer.set_orig_ratio(ratio)

    def set_show_original(self, flag):
        self.renderer.set_show_original(flag)

    def set_layout(self, layout):
        self.renderer.set_layout(layout)

    def set_scroll_lines(self, n):
        self.renderer.set_scroll_lines(n)

    def render(self, view):
        visible = self.renderer.render(view)
        if visible and not self._mapped:
            # Mapped and raised ONCE; re-raising a topmost window per update
            # makes Windows re-order and repaint other windows.
            self.top.deiconify()
            self.top.attributes("-topmost", True)
            self.top.lift()
            self._mapped = True
        elif not visible and self._mapped and self._mode == "plain":
            self.top.withdraw()
            self._mapped = False

    def destroy(self):
        self.renderer.destroy()
        try:
            self.top.destroy()
        except tk.TclError:
            pass


# ------------------------------------------------------------- layout preview

DEMO_SENTENCES = [
    ("Good morning, everyone.", "大家早上好。"),
    ("Thank you for joining the call today.", "謝謝你今天參加會議。"),
    ("Our quarterly results were better than expected, mostly because of strong sales in Asia.",
     "我們的季度業績超過了預期，主要是因為亞洲地區的銷售強勁。"),
    ("However, we still need to reduce costs before the end of the year.",
     "不過，我們仍需在年底前降低成本。"),
    ("Thank you very much.", "非常謝謝。"),
]


def build_demo_events(sentences=DEMO_SENTENCES, words_per_s=3.1, lag_s=1.4):
    """A scripted speech for the preview: [(time, kind, payload)] and its length.
    Words appear one by one; each sentence is committed at its end; its
    translation arrives lag_s later and streams in; the next sentence starts
    while the last translation is still arriving (the situation that makes
    the previous lines useful); a quiet stretch at the end shows the roll-off."""
    events, t = [], 1.0
    for i, (src, tr) in enumerate(sentences, 1):
        words = src.split()
        for k in range(1, len(words) + 1):
            events.append((t + k / words_per_s, "preview", " ".join(words[:k])))
        t_end = t + len(words) / words_per_s
        events.append((t_end + 0.35, "preview", ""))
        events.append((t_end + 0.35, "segment", (i, src, "", "pending")))
        steps = max(4, len(tr) // 2)
        for s in range(1, steps + 1):
            part = tr[:max(1, round(len(tr) * s / steps))]
            events.append((t_end + lag_s + 0.06 * s, "segment",
                           (i, src, part, "streaming" if s < steps else "final")))
        t = t_end + 0.9
    events.sort(key=lambda e: e[0])
    return events, events[-1][0] + 14.0


class LayoutPreview:
    """A window with every layout (with and without the original) running the
    same demo speech side by side, so the viewer can choose by eye.

    strings: title, intro, use, in_use, with_original, translation_only and one
    entry per layout name. on_use(layout, show_original) is called when a panel's
    button is pressed."""

    BG, PANEL_BG, SLIDE_BG = "#1c1c1e", "#262629", "#2f4a63"

    def __init__(self, master, strings, layout, show_original, on_use, panel_width=450,
                 scroll_lines=DEFAULT_SCROLL_LINES):
        self.strings, self.on_use = strings, on_use
        self.current = (normalize_choice(layout), bool(show_original))
        self.top = tk.Toplevel(master)
        self.top.title(strings["title"])
        self.top.configure(bg=self.BG)
        cols = 3 if master.winfo_screenwidth() >= 1500 else 2
        tk.Label(self.top, text=strings["intro"], bg=self.BG, fg="#d8d8d8", justify="left",
                 anchor="w", wraplength=panel_width * cols + 30, font=("Segoe UI", 10)
                 ).grid(row=0, column=0, columnspan=cols, sticky="ew", padx=12, pady=(10, 4))
        self._panels = []
        sw, sh = panel_width, int(panel_width * 9 / 16)          # a 16:9 screen, scaled down
        combos = [(l, o) for l in CHOICES for o in ((True,) if l == "none" else (True, False))]
        for n, (layout_name, orig) in enumerate(combos):
            frame = tk.Frame(self.top, bg=self.PANEL_BG, padx=6, pady=4)
            frame.grid(row=1 + n // cols, column=n % cols, padx=6, pady=5, sticky="n")
            title = tk.Label(frame, bg=self.PANEL_BG, fg="#e8e8e8", anchor="w", font=("Segoe UI", 10, "bold"))
            title.grid(row=0, column=0, sticky="w")
            button = tk.Button(frame, text=strings["use"], relief="flat", bg="#3a6ea5", fg="white",
                               activebackground="#4a80ba", activeforeground="white", padx=10,
                               command=lambda l=layout_name, o=orig: self._use(l, o))
            button.grid(row=0, column=1, sticky="e")
            frame.grid_columnconfigure(0, weight=1)
            renderer_h = int(sh * 0.62)
            canvas = tk.Canvas(frame, width=sw, height=renderer_h, bg=self.SLIDE_BG,
                               highlightthickness=0, bd=0)
            canvas.grid(row=1, column=0, columnspan=2, pady=(4, 0))
            clock = [0.0]
            ctrl = SubtitleController(lambda c=clock: c[0])
            renderer = SubtitleRenderer(canvas, sw, sh, 1.3, DEFAULT_ORIG_RATIO, orig, layout_name,
                                        scroll_lines)
            self._panels.append({"layout": layout_name, "orig": orig, "title": title, "button": button,
                                 "frame": frame, "canvas": canvas, "clock": clock, "ctrl": ctrl,
                                 "renderer": renderer})
        self._events, self._total = build_demo_events()
        self._t0, self._next, self._job = time.monotonic(), 0, None
        self._mark_current()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self._tick()

    def exists(self):
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def _label(self, layout, orig):
        s = self.strings
        if layout == "none":
            return s[layout]
        return f"{s[layout]}  ·  {s['with_original'] if orig else s['translation_only']}"

    def _mark_current(self):
        for p in self._panels:
            here = ((p["layout"], p["orig"]) == self.current
                    or (p["layout"] == "none" and self.current[0] == "none"))
            p["title"].configure(text=self._label(p["layout"], p["orig"]) + (f"   ✓ {self.strings['in_use']}" if here else ""),
                                 fg="#8fd694" if here else "#e8e8e8")
            p["frame"].configure(highlightthickness=2 if here else 0, highlightbackground="#8fd694",
                                 highlightcolor="#8fd694")

    def _use(self, layout, orig):
        self.current = (layout, orig)
        self._mark_current()
        self.on_use(layout, orig)

    def _tick(self):
        if not self.exists():
            return
        now = time.monotonic() - self._t0
        if now > self._total:                                      # start the demo again
            self._t0, self._next = time.monotonic(), 0
            for p in self._panels:
                p["ctrl"].reset()
                p["renderer"].render(None)
            now = 0.0
        while self._next < len(self._events) and self._events[self._next][0] <= now:
            _t, kind, payload = self._events[self._next]
            self._next += 1
            for p in self._panels:
                p["clock"][0] = _t
                if kind == "preview":
                    p["ctrl"].on_preview(payload)
                else:
                    p["ctrl"].on_segment(*payload)
        for p in self._panels:
            p["clock"][0] = now
            p["renderer"].render(p["ctrl"].view())
        self._job = self.top.after(100, self._tick)

    def close(self):
        if self._job is not None:
            try:
                self.top.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        for p in self._panels:
            p["renderer"].destroy()
        try:
            self.top.destroy()
        except tk.TclError:
            pass
