"""Smart Offline Transcription & Audio (internal codename: SOTA).

Three top-level tab groups:
  • Audio Studio — Record / Edit / Clean: capture and edit audio on its
    own, independent of transcription. Enhance (amplify/EQ/compressor/
    noise reduction) isn't a separate subtab — it's a collapsible panel
    inside Edit, so it always acts on the waveform already on screen.
  • Transcription Studio — Transcribe / Live / Edit / AI: drop audio
    files in for a batch transcript, dictate live via SenseVoice, replay
    a file and fix its transcript, or run a local LLM over a transcript
    to summarize/translate it.
  • Settings — models, output folder, updates.
"""

import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk
from PIL import ImageTk
import numpy as np
from tkinterdnd2 import DND_FILES, TkinterDnD

import audio_clean
import audio_clip
import audio_denoise
import audio_dsp
import audio_export
import audio_find
import audio_profiles
import audio_spectrogram
import audio_record
import docx_export
import i18n
import live_transcription
import llm
import settings
import sysinfo
import timestamps
import transcriber
from llm import LLMWorker
from player import SPEED_OPTIONS, Player
from transcriber import Job, TranscriberWorker, is_supported

POLL_MS = 100
TICK_MS = 150


def _fmt_time(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _open_path(path):
    """Opens a file or folder in the system's file manager / default app.
    os.startfile only exists on Windows — on macOS every 'Open output
    folder' button silently did nothing before this."""
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# Illegal in a Windows filename — importantly including ":", which doesn't
# error, it silently truncates (NTFS reads "name:rest" as alternate-data-
# stream syntax, discarding "rest" with no warning — verified directly
# while building the auto-generated live-session name). Validating up
# front is the only safe option; letting a bad name reach the filesystem
# and hoping for a loud error is not, since some of these fail silently.
_WINDOWS_ILLEGAL_FILENAME_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
_WINDOWS_RESERVED_FILENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_LIVE_FILENAME_MAX_LEN = 100


def _validate_live_filename(name):
    """Returns None if `name` (already stripped of surrounding whitespace)
    is safe to use as a live session's filename stem, or an i18n key
    naming what's wrong. An empty name is valid — it means "use the
    automatic name" — so this only rejects genuinely unusable text, not a
    blank field."""
    if not name:
        return None
    if len(name) > _LIVE_FILENAME_MAX_LEN:
        return "live_filename_too_long"
    if _WINDOWS_ILLEGAL_FILENAME_CHARS & set(name):
        return "live_filename_invalid_chars"
    if name != name.rstrip(". "):
        # Windows silently drops trailing dots/spaces from a filename —
        # same "looks fine, quietly isn't" failure mode as a bare colon.
        return "live_filename_trailing_dot_space"
    if name.upper().split(".", 1)[0] in _WINDOWS_RESERVED_FILENAMES:
        return "live_filename_reserved"
    return None


def _fmt_size(num_bytes):
    """Human-readable size for the Settings tab's model list."""
    gb = num_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{num_bytes / (1024 ** 2):.0f} MB"


def _folder_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _version_tuple(version):
    parts = []
    for piece in str(version).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


UPDATE_API_URL = "https://api.github.com/repos/Charles-Y3/SOTA/releases/latest"
RELEASES_PAGE_URL = "https://github.com/Charles-Y3/SOTA/releases/latest"


def _speed_label(speed):
    return (f"{speed:g}×")


class _Tooltip:
    """Minimal hover tooltip: a borderless Toplevel with one label, shown
    near the cursor on <Enter> and destroyed on <Leave>. CustomTkinter has
    no built-in tooltip widget, so this is deliberately tiny rather than
    pulling in a dependency for it. `text_getter` is called fresh on every
    show — pass a callable (not a plain string) so the tooltip always
    reflects the current UI language/values instead of whatever they were
    when the widget was built."""

    def __init__(self, widget, text_getter):
        self.widget = widget
        self.text_getter = text_getter
        self.tip = None
        # A CTkButton's outer frame is fully covered by its own internal
        # canvas (where the rounded rect + text are actually drawn), so
        # <Enter>/<Leave> for a real mouse never reach the frame itself —
        # Tk delivers them to whichever widget the pointer is actually
        # over, which is that inner canvas. Binding there instead (same
        # "reach into the real widget" approach already used elsewhere in
        # this app, e.g. the Edit tab's `editor._textbox`) is what makes
        # this actually fire; binding on `widget` alone silently never
        # does for a CTkButton, only for a plain Tk widget like a Canvas.
        target = getattr(widget, "_canvas", widget)
        target.bind("<Enter>", self._show, add="+")
        target.bind("<Leave>", self._hide, add="+")
        target.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event=None):
        text = self.text_getter() if callable(self.text_getter) else self.text_getter
        if not text:
            return
        self._hide()
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        try:
            self.tip.wm_attributes("-topmost", True)
        except Exception:
            pass
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.tip, text=text, justify="left", background="#333333", foreground="white",
            relief="solid", borderwidth=1, wraplength=320, padx=6, pady=3,
        ).pack()

    def _hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        # A concrete starting size (also the floor for un-maximizing) plus
        # minsize; the actual maximize call is deferred (see
        # _maximize_on_startup's docstring for why it can't just run here).
        # minsize's width is 720, not 680: measured empirically as the
        # smallest width the Edit tab's player-controls row (two buttons +
        # time label + slider + speed picker) needs without clipping.
        self.geometry("820x680")
        self.minsize(720, 560)

        self.prefs = settings.load()
        settings.set_output_base(self.prefs.get("output_folder"))
        for key in ("editor_font_size", "llm_source_font_size", "llm_output_font_size",
                    "live_text_font_size"):
            self.prefs[key] = self._clamp_font_size(self.prefs.get(key, 14))
        self.sys_ram_gb = sysinfo.total_ram_gb()
        self.ui_lang = self.prefs["ui_language"] if self.prefs["ui_language"] in i18n.UI_LANGUAGES else "en"
        self.sensevoice_available = transcriber.sensevoice_is_available()
        self.rows = []
        self.worker = None
        self.events = queue.Queue()
        self.running = False
        self.status_key = None
        self.status_detail = None
        self.current_tab = 0
        self.current_group = 0
        self._group_last_leaf = {}   # group index -> last leaf shown there

        # Audio Studio state — Record/Edit/Enhance/Clean all operate on
        # one clip at a time, so this lives at the App level rather than
        # per-subtab. A dedicated Player instance (not the Transcription
        # Studio Edit tab's self.player below) so the two can never
        # interfere with each other's playback.
        self.audio_recorder = None
        self.audio_recording = False
        self.audio_record_status_key = None
        self.audio_record_status_detail = None
        self._last_recording_path = None
        self.audio_player = Player(
            ready_callback=lambda: self.events.put(("audio_speed_ready",)),
            progress_callback=lambda speed, frac: None,
        )
        self.audio_clip = None            # audio_clip.AudioClip currently open, or None
        self.audio_clip_path = None       # source path it was opened from
        self.audio_selection = None       # (start_s, end_s) or None
        self._find_cancel_event = None    # threading.Event for the in-progress Find Similar search, or None
        self._find_progress_dialog = None
        self._find_min_score = 0.80        # Matches panel's display filter — persists across searches, not reset per-search
        self._busy_dialog = None          # generic "please wait" modal (see _show_busy_dialog) — None when not showing
        self._busy_bar = None
        self.aenh_configs = audio_profiles.load_profiles()  # user-saved Enhance configurations (see audio_profiles.py)
        self.audio_clipboard = None       # numpy buffer from the last Copy/Cut
        self.audio_zoom = (0.0, 0.0)      # visible (start_s, end_s) window on the waveform
        self.audio_vzoom = 1.0            # amplitude scale for the waveform display — 1.0 is unscaled
        self.audio_status_key = None
        self.audio_status_detail = None
        # Set only while an Enhance/Clean Preview is playing: the full
        # clip buffer with the previewed effect spliced into the selected
        # (or whole-clip) region, purely for waveform display — never
        # written back to audio_clip until Apply is clicked.
        self.audio_preview = None
        self.audio_noise_profile = None  # captured noise-only snippet for Enhance's noise-profile denoise
        self.wave_view_mode = "waveform"  # or "spectrogram" — see _on_wave_view_change
        self._spectrogram_photo = None    # keeps the PhotoImage alive; Tk drops it otherwise
        self._wave_content_cache_key = None  # see _redraw_waveform's caching
        self._preview_range_samples = (0, 0)  # composite-space sample range the amber tint covers
        self._preview_offset_s = 0.0  # clip-time offset of an isolated selection preview's own t=0
        self._play_until = None       # clip-time stop point for a selection-limited Play
        self._playhead_item = None    # canvas line item id — moved, not recreated, on every tick
        self.active_effects_panel = None  # "enhance" | "clean" | None — mutually exclusive
        self._tooltips = []  # keeps _Tooltip instances alive (see _add_tooltip)

        # Live Transcription tab state
        self.live_worker = None
        self.live_running = False
        self.live_session_started = False
        self.live_preload_started = False
        self.live_status_key = None
        self.live_status_detail = None

        # Edit tab state
        self.player = Player(
            ready_callback=lambda: self.events.put(("speed_ready",)),
            progress_callback=lambda speed, frac: self.events.put(
                ("speed_progress", speed, frac)),
        )
        self.edit_files = []       # [{label, audio, txt}] (shared with AI tab)
        self.edit_current = None   # current {label, audio, txt}
        self.edit_status_key = None
        self.edit_status_detail = None
        self._edit_loaded_text = None  # editor content as of the last load — lets
                                        # auto-refresh detect unsaved typing and back off
        # Live-draft paragraphs saved to a file the editor currently has
        # open — the on-disk copy already contains them (the worker writes
        # before announcing), but the open buffer doesn't. Held here until
        # the user clicks the "add new live text" notice (or saves, which
        # pulls them in first) — never spliced into the buffer unasked.
        self.live_pending_appends = []   # [(start_seconds_or_None, text)]
        self.live_pending_path = None    # transcript path the above belong to
        # Recording badge on the Live Transcription tab button — the only
        # reminder a session is still going once you've switched to another
        # tab (see _style_tab_buttons). Color-only, no text change, so it
        # can never affect the tab strip's layout/width.
        self.live_tab_idle = False

        # AI tab state
        self.llm_worker = None
        self.llm_running = False
        self.llm_current = None
        self.llm_status_key = None
        self.llm_status_detail = None

        # Settings tab state
        self.model_rows = {}        # row key -> {"spec", "status", "button"}
        self.model_downloads = set()  # row keys with a download in flight
        self.update_checking = False
        self.update_status_key = None
        self.update_status_detail = None

        # Shown at most once per session — see _offer_output_folder_fix.
        self._save_failure_hint_shown = False

        self._build_ui()
        self._apply_prefs()
        self._retranslate()
        self._show_tab(0)

        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)

        # Audio Studio Edit-subtab keyboard shortcuts — all gated the same
        # way by _guarded_shortcut (only act on that tab, never while a
        # text field elsewhere has focus). Ctrl+Z/X/C/V etc. still work
        # normally in a CTkEntry (the Record tab's filename field, say)
        # since the guard returns None instead of "break" there, leaving
        # Tk's own default binding for that widget untouched.
        for _seq, _handler in [
            ("<space>", self._toggle_audio_edit_play),
            ("<Control-z>", self._audio_edit_undo),
            ("<Control-y>", self._audio_edit_redo),
            ("<Control-x>", self._audio_edit_cut),
            ("<Control-c>", self._audio_edit_copy),
            ("<Control-v>", self._audio_edit_paste),
            ("<Delete>", self._audio_edit_delete),
            ("<BackSpace>", self._audio_edit_delete),
            ("<bracketleft>", lambda: self._zoom_audio_edit(2.0)),
            ("<bracketright>", lambda: self._zoom_audio_edit(0.5)),
            ("<Control-f>", self._run_find_similar),
            ("<Home>", lambda: self._seek_audio_edit_to(0.0)),
            ("<End>", lambda: self._seek_audio_edit_to(
                self.audio_clip.duration if self.audio_clip else 0.0)),
            ("<Control-s>", lambda: self._export_audio_clip("wav")),
        ]:
            self.bind_all(_seq, self._guarded_shortcut(_handler))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll_events)
        self.after(TICK_MS, self._tick_player)
        self.after(0, self._maximize_on_startup)

    def _maximize_on_startup(self):
        """'zoomed' is a real Tk window state on Windows (and most Linux
        window managers) but isn't reliably supported on macOS across Tk
        builds — falling back to sizing the window to the full screen
        keeps the same "maximized" outcome there instead of silently
        leaving the small fallback geometry from __init__ in place.

        Scheduled via after(0, ...) from __init__ rather than called
        directly: on Windows, CustomTkinter's own CTk.__init__ (which runs
        as our super().__init__() before any of our code) synchronously
        withdraws the window to set the dark-mode titlebar color, and its
        restore-afterward logic silently drops the window state because
        the window didn't exist yet when it captured what to restore to.
        A state('zoomed') called from our __init__ therefore lands on a
        window that's about to be quietly left un-maximized. Deferring to
        the next idle tick runs this after that whole dance (and the rest
        of __init__) has settled, so it's the last thing to touch window
        state before the window is actually shown."""
        try:
            self.state("zoomed")
        except Exception:
            self.update_idletasks()
            self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")

    # ================================================================== UI

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)  # row 1 is the subtab strip, fixed height

        # --- top bar: tabs (left, scrollable) + UI language toggle (right)
        topbar = ctk.CTkFrame(self, fg_color="transparent")
        topbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        # Column 1 (the tab viewport) is the only one that stretches — the
        # arrow buttons and language toggle stay fixed-width on either side.
        topbar.grid_columnconfigure(1, weight=1)

        # Five tabs at their natural (uniform) width don't fit every real
        # screen even maximized — e.g. English measures ~1215px for the tab
        # row alone, which overflows common 1280/1366px-wide laptop
        # displays. Rather than shrink or wrap the tabs, the strip scrolls:
        # it lives inside a plain tkinter.Canvas viewport (CTk has no
        # scrollable-row widget) whose width tracks the available space:
        # when the tabs fit, it's just a static row; when they don't, "<"/
        # ">" arrows appear to pan it, the same pattern Word's ribbon uses
        # for overflowing tabs.
        self.tab_scroll_left = ctk.CTkButton(
            topbar, text="<", width=22, corner_radius=6,
            command=lambda: self._scroll_tabs(-1))
        self.tab_scroll_left.grid(row=0, column=0, padx=(0, 3))
        self.tab_scroll_left.grid_remove()  # shown only when the tabs overflow

        # Height is set after the tab buttons exist below, from their own
        # actual rendered height — not a guessed constant. CTkButton's
        # height=32 constructor argument is a request, not the final
        # rendered size (padding/border push it a few px taller, more at
        # some DPI scales); a canvas fixed to the requested-but-not-actual
        # height clips the bottom of every embedded tab.
        self.tab_canvas = tk.Canvas(topbar, highlightthickness=0, bd=0)
        self.tab_canvas.grid(row=0, column=1, sticky="ew")
        self._sync_tab_canvas_bg()

        # Flat rectangular tabs (not the pill-shaped segmented-button look):
        # the selected tab's background matches the content panel below it,
        # so it visually merges into it, like tabs in a regular desktop app.
        self.tab_bar = ctk.CTkFrame(self.tab_canvas, fg_color="transparent")
        self._tab_bar_window = self.tab_canvas.create_window(
            (0, 0), window=self.tab_bar, anchor="nw")
        self.tab_buttons = []
        for i in range(len(self.GROUP_KEYS)):
            btn = ctk.CTkButton(
                self.tab_bar, text="", height=32, corner_radius=6,
                border_spacing=0, command=lambda idx=i: self._show_group(idx),
            )
            # No uniform width group: each tab is sized to its own label in
            # _retranslate() instead — narrower in aggregate than forcing
            # every tab to match the widest one, which helps but doesn't
            # alone solve the overflow (hence the scrolling viewport above).
            btn.grid(row=0, column=i, sticky="ew", padx=1)
            self.tab_bar.grid_columnconfigure(i, weight=0)
            self.tab_buttons.append(btn)

        self.tab_scroll_right = ctk.CTkButton(
            topbar, text=">", width=22, corner_radius=6,
            command=lambda: self._scroll_tabs(1))
        self.tab_scroll_right.grid(row=0, column=2, padx=(3, 8))
        self.tab_scroll_right.grid_remove()

        # Now that a tab button exists, measure its real rendered height
        # and size the canvas (and the flanking arrow buttons, so they
        # don't look short next to full-height tabs) to match exactly.
        self.tab_bar.update_idletasks()
        tab_h = self.tab_buttons[0].winfo_reqheight()
        self.tab_canvas.configure(height=tab_h)
        self.tab_scroll_left.configure(height=tab_h)
        self.tab_scroll_right.configure(height=tab_h)

        self.ui_lang_button = ctk.CTkSegmentedButton(
            topbar, values=["EN", "繁中"], command=self._on_ui_lang_change,
        )
        self.ui_lang_button.grid(row=0, column=3, sticky="e")
        self._equalize_segments(self.ui_lang_button, 52)

        self.tab_canvas.bind("<Configure>", self._on_tab_canvas_resize)
        self.tab_bar.bind("<Configure>", self._on_tab_bar_resize)

        # --- second row: the active group's own subtab strip (Audio
        # Studio and Transcription Studio each have one; AI and Settings
        # don't, so nothing shows for those). Reuses the generic
        # scrollable-strip helper built for the AI tab's own overflowing
        # action row rather than a second bespoke Canvas — one wrapper
        # frame per group-with-subtabs, stacked in the same grid cell and
        # shown/hidden by _show_tab, the same "only the active one is
        # gridded" pattern self.content uses below for the leaf frames.
        self.subtab_bar_row = ctk.CTkFrame(self, fg_color="transparent")
        self.subtab_bar_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(6, 0))
        self.subtab_bar_row.grid_columnconfigure(0, weight=1)
        self.subtab_strips = {}  # group index -> (content, canvas, left_btn, right_btn, [buttons])
        for group_index, leaves in enumerate(self.GROUP_LEAVES):
            if len(leaves) < 2:
                continue  # no subtabs for this group — it has one leaf
            content, canvas, left_btn, right_btn = self._make_scroll_strip(
                self.subtab_bar_row, row=0, column=0, fg_color="transparent")
            buttons = []
            for slot, leaf in enumerate(leaves):
                btn = ctk.CTkButton(
                    content, text="", height=28, corner_radius=6,
                    border_spacing=0, fg_color="transparent",
                    command=lambda lf=leaf: self._show_tab(lf))
                btn.grid(row=0, column=slot, sticky="ew", padx=(0 if slot == 0 else 6, 0))
                buttons.append(btn)
            self._finalize_scroll_strip(canvas, content, left_btn, right_btn)
            self.subtab_strips[group_index] = (content, canvas, left_btn, right_btn, buttons)

        # --- content area holds every leaf tab's frame in the same cell
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.audio_record_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.audio_edit_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.transcribe_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.live_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.edit_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.llm_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.settings_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        # Only the first leaf is gridded up front. Every leaf occupies the
        # same cell, and Tk stacks later-gridded widgets on top — gridding
        # them all here would put the last one built on top of the stack
        # from the very first paint, until _show_tab(0) later sorted it
        # out. That's a no-op on a fast dev machine, but left a real
        # window on a slower one (e.g. a packaged .exe) where the wrong
        # tab could actually be what gets painted first. _show_tab() grids
        # whichever leaf is active, so the rest never need to be gridded
        # here at all.
        self.audio_record_frame.grid(row=0, column=0, sticky="nsew")

        self._build_audio_record_tab(self.audio_record_frame)
        self._build_audio_edit_tab(self.audio_edit_frame)
        self._build_transcribe_tab(self.transcribe_frame)
        self._build_live_tab(self.live_frame)
        self._build_edit_tab(self.edit_frame)
        self._build_llm_tab(self.llm_frame)
        self._build_settings_tab(self.settings_frame)

    def _add_tooltip(self, widget, text_getter):
        self._tooltips.append(_Tooltip(widget, text_getter))

    def _add_tooltip_to_segmented(self, segmented_button, text_getter):
        """A CTkSegmentedButton's individual segments are their own
        button widgets sitting on top of the container's own canvas
        (same _buttons_dict this app already reaches into for
        _equalize_segments) — a tooltip on the container alone would
        never fire since the segments cover it completely."""
        self._add_tooltip(segmented_button, text_getter)
        for btn in segmented_button._buttons_dict.values():
            self._add_tooltip(btn, text_getter)

    # ================================================== audio studio: record

    def _build_audio_record_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)  # the live waveform canvas

        options = ctk.CTkFrame(parent)
        options.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 6))
        options.grid_columnconfigure(7, weight=1)

        self.arec_mic_label = ctk.CTkLabel(options, text="")
        self.arec_mic_label.grid(row=0, column=0, padx=(12, 6), pady=(10, 4))
        self.arec_mic_menu = ctk.CTkOptionMenu(
            options, width=220, dynamic_resizing=False, command=self._on_arec_pref_change)
        self.arec_mic_menu.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=(10, 4))

        self.arec_rate_label = ctk.CTkLabel(options, text="")
        self.arec_rate_label.grid(row=0, column=2, padx=(0, 6), pady=(10, 4))
        self.arec_rate_menu = ctk.CTkOptionMenu(
            options, width=90, values=[str(r) for r in audio_record.SAMPLE_RATE_OPTIONS])
        self.arec_rate_menu.set(str(audio_record.DEFAULT_SAMPLE_RATE))
        self.arec_rate_menu.grid(row=0, column=3, padx=(0, 16), pady=(10, 4))

        self.arec_channels_label = ctk.CTkLabel(options, text="")
        self.arec_channels_label.grid(row=0, column=4, padx=(0, 6), pady=(10, 4))
        self.arec_channels_menu = ctk.CTkOptionMenu(options, width=64, values=["1", "2"])
        self.arec_channels_menu.set("1")
        self.arec_channels_menu.grid(row=0, column=5, padx=(0, 16), pady=(10, 4))

        self.arec_level_label = ctk.CTkLabel(options, text="")
        self.arec_level_label.grid(row=0, column=6, padx=(0, 6), pady=(10, 4))
        self.arec_level_bar = ctk.CTkProgressBar(options, width=90)
        self.arec_level_bar.set(0)
        self.arec_level_bar.grid(row=0, column=7, sticky="w", padx=(0, 12), pady=(10, 4))

        self.arec_filename_label = ctk.CTkLabel(options, text="")
        self.arec_filename_label.grid(row=1, column=0, padx=(12, 6), pady=(4, 10))
        self.arec_filename_entry = ctk.CTkEntry(options, width=440)
        self.arec_filename_entry.grid(row=1, column=1, columnspan=5, sticky="w",
                                      padx=(0, 16), pady=(4, 10))

        self.arec_timer_label = ctk.CTkLabel(
            parent, text="00:00", font=ctk.CTkFont(size=32, weight="bold"))
        self.arec_timer_label.grid(row=1, column=0, pady=(24, 8))

        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.grid(row=2, column=0, pady=(0, 12))
        self.arec_start_button = ctk.CTkButton(
            controls, text="", height=40, width=150,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._toggle_audio_record)
        self.arec_start_button.grid(row=0, column=0, padx=(0, 8))
        self.arec_pause_button = ctk.CTkButton(
            controls, text="", height=40, width=110, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            state="disabled", command=self._toggle_audio_record_pause)
        self.arec_pause_button.grid(row=0, column=1, padx=(0, 8))
        self.arec_stop_button = ctk.CTkButton(
            controls, text="", height=40, width=110, fg_color="#8a3535",
            hover_color="#a04040", state="disabled", command=self._stop_audio_record)
        self.arec_stop_button.grid(row=0, column=2)

        # Live waveform — grows as the recording proceeds, drawn from
        # AudioRecorder.peaks_snapshot() (updated by _tick_player while
        # this subtab is on screen and a recording is in progress).
        self.arec_wave_canvas = tk.Canvas(parent, height=140, highlightthickness=0, bd=0)
        self.arec_wave_canvas.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 6))
        self.arec_wave_canvas.bind("<Configure>", lambda _e: self._redraw_record_waveform())

        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="sew", padx=12, pady=(0, 8))
        bottom.grid_columnconfigure(2, weight=1)
        self.arec_edit_button = ctk.CTkButton(
            bottom, text="", height=32, state="disabled",
            command=self._send_last_recording_to_edit)
        self.arec_edit_button.grid(row=0, column=0, padx=(0, 10))
        self.arec_open_folder_button = ctk.CTkButton(
            bottom, text="", height=32, width=150, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=lambda: self._open_output_folder(settings.audio_recordings_folder()))
        self.arec_open_folder_button.grid(row=0, column=1, padx=(0, 10))
        self.arec_status_line = ctk.CTkLabel(bottom, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.arec_status_line.grid(row=0, column=2, sticky="ew")

    def _refresh_audio_record_mic_menu(self):
        names = audio_record.list_input_devices()
        default_label = i18n.t(self.ui_lang, "mic_default")
        current = getattr(self, "_arec_selected_mic", "")
        self.arec_mic_menu.configure(values=[default_label] + names)
        self.arec_mic_menu.set(current if current in names else default_label)

    def _on_arec_pref_change(self, _value=None):
        default_label = i18n.t(self.ui_lang, "mic_default")
        mic_value = self.arec_mic_menu.get()
        self._arec_selected_mic = "" if mic_value == default_label else mic_value

    def _toggle_audio_record(self):
        if not self.audio_recording:
            self._start_audio_record()

    def _start_audio_record(self):
        self._on_arec_pref_change()
        custom = self.arec_filename_entry.get().strip()
        error_key = _validate_live_filename(custom)
        if error_key:
            self._set_audio_record_status(error_key, {})
            return
        self.audio_recorder = audio_record.AudioRecorder(
            self.events, device_name=self._arec_selected_mic,
            sample_rate=int(self.arec_rate_menu.get()),
            channels=int(self.arec_channels_menu.get()),
            custom_stem=custom or None)
        self.audio_recording = True
        self.arec_start_button.configure(state="disabled")
        self.arec_pause_button.configure(state="normal", text=i18n.t(self.ui_lang, "arec_pause"))
        self.arec_stop_button.configure(state="normal")
        for w in (self.arec_mic_menu, self.arec_rate_menu, self.arec_channels_menu,
                  self.arec_filename_entry):
            w.configure(state="disabled")
        self._set_audio_record_status("arec_status_recording", {})
        self.audio_recorder.start()

    def _toggle_audio_record_pause(self):
        if not self.audio_recorder:
            return
        if self.audio_recorder.is_paused:
            self.audio_recorder.resume()
            self.arec_pause_button.configure(text=i18n.t(self.ui_lang, "arec_pause"))
            self._set_audio_record_status("arec_status_recording", {})
        else:
            self.audio_recorder.pause()
            self.arec_pause_button.configure(text=i18n.t(self.ui_lang, "arec_resume"))
            self._set_audio_record_status("arec_status_paused", {})

    def _stop_audio_record(self):
        if self.audio_recorder:
            self.audio_recorder.stop()
        self.arec_stop_button.configure(state="disabled")
        self.arec_pause_button.configure(state="disabled")

    def _on_audio_record_event(self, kind, *rest):
        if kind == "record_stopped":
            path, _seconds = rest
            self.audio_recording = False
            self.audio_recorder = None
            self.arec_start_button.configure(state="normal")
            for w in (self.arec_mic_menu, self.arec_rate_menu, self.arec_channels_menu,
                      self.arec_filename_entry):
                w.configure(state="normal")
            self.arec_filename_entry.delete(0, "end")
            if path:
                self._last_recording_path = path
                self.arec_edit_button.configure(state="normal")
                self._set_audio_record_status("arec_status_saved", {"path": os.path.basename(path)})
            else:
                self._set_audio_record_status("arec_status_failed", {})
        # "record_started" needs no UI update beyond what _start_audio_record
        # already did — it exists so the worker thread has an event to
        # confirm the file was actually created before anything else reads
        # self._last_recording_path.

    def _set_audio_record_status(self, key, detail):
        self.audio_record_status_key, self.audio_record_status_detail = key, detail
        self.arec_status_line.configure(text=i18n.t(self.ui_lang, key, **detail))

    def _send_last_recording_to_edit(self):
        path = self._last_recording_path
        if not path or not os.path.isfile(path):
            return
        self._open_audio_clip(path)
        self._show_tab(self.LEAF_AUDIO_EDIT)

    def _redraw_record_waveform(self):
        """Draws the whole recording so far, scaled to fit the canvas
        width — unlike the Edit subtab's waveform, there's no zoom/scroll
        here, just "the shape of everything captured up to now"."""
        canvas = self.arec_wave_canvas
        canvas.delete("all")
        canvas.configure(bg=self._resolve_root_bg())
        if self.audio_recorder is None:
            return
        mins, maxes = self.audio_recorder.peaks_snapshot()
        if not mins:
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        mid = height / 2
        n = len(mins)
        color = "#e05a5a" if not self.audio_recorder.is_paused else "#d99a30"
        for x in range(width):
            # Right-aligned: the most recent audio always sits at the
            # right edge, same as a live level meter or scrolling EKG —
            # so the canvas doesn't need its own zoom/scroll controls for
            # the one thing anyone actually wants from it (is it hearing
            # something right now).
            src_i = n - width + x
            if src_i < 0:
                continue
            y0 = mid - maxes[src_i] * mid
            y1 = mid - mins[src_i] * mid
            canvas.create_line(x, y0, x, y1 + 1, fill=color)

    # ==================================================== audio studio: edit

    def _build_button_group(self, parent, col, caption_key, buttons):
        """One card of edit-action buttons (e.g. Clipboard: Cut/Copy/
        Paste) — grouped visually via the card border, named via a
        hover tooltip on the card itself rather than a permanent caption
        label underneath (which cost a whole extra row of height for
        text that's rarely the first thing anyone needs)."""
        card = ctk.CTkFrame(parent)
        card.grid(row=0, column=col, sticky="w", padx=(0, 8))
        self._add_tooltip(card, lambda k=caption_key: i18n.t(self.ui_lang, k))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, padx=8, pady=6)
        for i, (attr, cmd, tip_key) in enumerate(buttons):
            btn = ctk.CTkButton(
                row, text="", width=100, height=30, fg_color="transparent",
                text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=cmd)
            btn.grid(row=0, column=i, padx=(0 if i == 0 else 6, 0))
            setattr(self, attr, btn)
            if tip_key == "aedit_tip_silence":
                self._add_tooltip(btn, lambda k=tip_key: i18n.t(
                    self.ui_lang, k, seconds=self.INSERT_SILENCE_S))
            elif tip_key == "aedit_tip_detect_silence":
                self._add_tooltip(btn, lambda k=tip_key: i18n.t(
                    self.ui_lang, k, min_gap=audio_clean.DEFAULT_MIN_SILENCE_S))
            elif tip_key:
                self._add_tooltip(btn, lambda k=tip_key: i18n.t(self.ui_lang, k))
        return card

    def _build_audio_edit_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)  # the wave area (contains the waveform canvas)

        picker = ctk.CTkFrame(parent)
        picker.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 6))
        picker.grid_columnconfigure(2, weight=1)
        self.aedit_open_button = ctk.CTkButton(
            picker, text="", width=130, command=self._open_audio_clip_dialog)
        self.aedit_open_button.grid(row=0, column=0, padx=(10, 8), pady=10)
        self.aedit_file_label = ctk.CTkLabel(picker, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.aedit_file_label.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=10)
        # Separate from aedit_file_label (rather than one colored string)
        # so "unsaved changes" can actually be red — a CTkLabel can't mix
        # two text colors in one string — while still sharing the same
        # row instead of costing its own strip of height.
        self.aedit_dirty_label = ctk.CTkLabel(picker, text="", anchor="w", text_color="#e05a5a")
        self.aedit_dirty_label.grid(row=0, column=2, sticky="w", pady=10)

        transport = ctk.CTkFrame(parent)
        transport.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        self.aedit_play_button = ctk.CTkButton(
            transport, text="", width=90, command=self._toggle_audio_edit_play)
        self.aedit_play_button.grid(row=0, column=0, padx=(10, 6), pady=8)
        self.aedit_stop_button = ctk.CTkButton(
            transport, text="", width=90, fg_color="transparent", border_width=1,
            text_color=self.OUTLINE_BUTTON_TEXT, command=self._stop_audio_edit_play)
        self.aedit_stop_button.grid(row=0, column=1, padx=(0, 12), pady=8)
        self.aedit_time_label = ctk.CTkLabel(transport, text="00:00 / 00:00", width=110)
        self.aedit_time_label.grid(row=0, column=2, padx=(0, 8), pady=8)
        self.aedit_speed_menu = ctk.CTkOptionMenu(
            transport, width=80, values=[_speed_label(s) for s in SPEED_OPTIONS],
            command=self._on_audio_edit_speed_change)
        self.aedit_speed_menu.set(_speed_label(1.0))
        self.aedit_speed_menu.grid(row=0, column=3, padx=(0, 16), pady=8)
        self.aedit_zoom_out_button = ctk.CTkButton(
            transport, text="−", width=30, command=lambda: self._zoom_audio_edit(2.0))
        self.aedit_zoom_out_button.grid(row=0, column=4, padx=(0, 2), pady=8)
        self.aedit_zoom_fit_button = ctk.CTkButton(
            transport, text="", width=60, command=self._zoom_audio_edit_fit)
        self.aedit_zoom_fit_button.grid(row=0, column=5, padx=2, pady=8)
        self.aedit_zoom_in_button = ctk.CTkButton(
            transport, text="+", width=30, command=lambda: self._zoom_audio_edit(0.5))
        self.aedit_zoom_in_button.grid(row=0, column=6, padx=(2, 10), pady=8)
        self.aedit_view_toggle = ctk.CTkSegmentedButton(
            transport, values=["Waveform", "Spectrogram"], command=self._on_wave_view_change)
        self.aedit_view_toggle.grid(row=0, column=7, padx=(0, 10), pady=8)
        # Not tooltip-bound here — configure(values=...) in retranslate
        # (called once right after this, during __init__) destroys and
        # recreates these segments regardless, which would silently
        # orphan a binding made now. See the retranslate call site.
        transport.grid_columnconfigure(8, weight=1)
        self.aedit_selection_label = ctk.CTkLabel(
            transport, text="", anchor="e", text_color=self.MUTED_TEXT)
        self.aedit_selection_label.grid(row=0, column=8, sticky="e", padx=(0, 12), pady=8)
        # Only shown in Spectrogram mode (see _update_spectrogram_caption)
        # — the toggle's own hover tooltip explains the two view modes,
        # but a tooltip is easy to miss entirely; someone who's never used
        # a spectrogram before needs the color/axis meaning spelled out
        # somewhere they'll actually see it without hovering anything.
        self.aedit_spectrogram_caption = ctk.CTkLabel(
            transport, text="", anchor="w", text_color=self.MUTED_TEXT, font=ctk.CTkFont(size=11))
        self.aedit_spectrogram_caption.grid(
            row=1, column=0, columnspan=9, sticky="w", padx=12, pady=(0, 6))
        self.aedit_spectrogram_caption.grid_remove()

        # Edit actions sit directly above the waveform they act on, right
        # after the transport/zoom controls — grouped with the thing
        # they're about to change, not tucked away below the scrollbar.
        # Split into three labeled clusters (Clipboard / Structure /
        # History & Search) each in their own card, rather than one flat
        # unbroken row of nine near-identical buttons — same idea as
        # Audacity keeping its Edit and Selection toolbars visually
        # distinct instead of merging them into one long strip.
        editrow = ctk.CTkFrame(parent, fg_color="transparent")
        editrow.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
        self._build_button_group(editrow, 0, "aedit_group_clipboard", [
            ("aedit_cut_button", self._audio_edit_cut, None),
            ("aedit_copy_button", self._audio_edit_copy, None),
            ("aedit_paste_button", self._audio_edit_paste, None),
        ])
        self._build_button_group(editrow, 1, "aedit_group_structure", [
            ("aedit_trim_button", self._audio_edit_trim, "aedit_tip_trim"),
            ("aedit_split_button", self._audio_edit_split, "aedit_tip_split"),
            ("aedit_silence_button", self._audio_edit_insert_silence, "aedit_tip_silence"),
        ])
        self._build_button_group(editrow, 2, "aedit_group_history", [
            ("aedit_undo_button", self._audio_edit_undo, None),
            ("aedit_redo_button", self._audio_edit_redo, None),
            ("aedit_find_button", self._run_find_similar, "aedit_tip_find"),
            ("aedit_detect_silence_button", self._detect_silences, "aedit_tip_detect_silence"),
            ("aedit_marker_button", self._add_marker_at_playhead, "aedit_tip_marker"),
        ])

        # Wave area: a dB axis to the left, timeline ruler + waveform +
        # horizontal scrollbar to the right — bundled in their own 2-
        # column grid so the axis lines up with the waveform's amplitude
        # regardless of window size.
        wave_area = ctk.CTkFrame(parent, fg_color="transparent")
        wave_area.grid(row=3, column=0, sticky="nsew", padx=12, pady=(6, 0))
        wave_area.grid_columnconfigure(1, weight=1)
        wave_area.grid_rowconfigure(2, weight=1)

        # No separate track-header strip — the picker row's file label
        # above already names the open file (and Audio Studio only ever
        # has one open at a time), so a second label naming the same
        # thing here would just be a repeated row of height for nothing.
        DB_AXIS_WIDTH = 34
        ctk.CTkFrame(wave_area, width=DB_AXIS_WIDTH, height=20, fg_color="transparent").grid(
            row=0, column=0)  # spacer so column 0 lines up under the timeline row too
        self.atimeline_canvas = tk.Canvas(wave_area, height=20, highlightthickness=0, bd=0,
                                          bg=self._resolve_root_bg())
        self.atimeline_canvas.grid(row=0, column=1, sticky="ew")
        # Same cursor-anchored time-zoom as scrolling the waveform itself
        # — reuses that handler directly (it only reads event.x/.delta and
        # self.awave_canvas's width, which this canvas always matches).
        self.atimeline_canvas.bind("<MouseWheel>", self._on_wave_scroll)
        self._add_tooltip(self.atimeline_canvas, lambda: i18n.t(self.ui_lang, "aedit_tip_timeline_scroll"))

        # Marker lane — small flag glyphs at each marker's time, its own
        # thin canvas between the ruler and the waveform so markers stay
        # visible without competing for space inside the waveform canvas
        # itself. Left-click seeks there; double-click renames; right-
        # click opens a Rename/Delete menu (both actions were previously
        # only reachable one way each, and not discoverable at all).
        ctk.CTkFrame(wave_area, width=DB_AXIS_WIDTH, height=18, fg_color="transparent").grid(row=1, column=0)
        self.amarker_canvas = tk.Canvas(wave_area, height=18, highlightthickness=0, bd=0,
                                        bg=self._resolve_root_bg())
        self.amarker_canvas.grid(row=1, column=1, sticky="ew")
        self.amarker_canvas.bind("<Configure>", lambda _e: self._redraw_waveform())
        self.amarker_canvas.bind("<Button-1>", self._on_marker_lane_click)
        self.amarker_canvas.bind("<Double-Button-1>", self._on_marker_lane_double_click)
        self.amarker_canvas.bind("<Button-3>", self._on_marker_lane_right_click)
        # Trackpad "secondary click" without a configured two-finger tap
        # sends Control+left-click instead of a real Button-3 on macOS —
        # a second binding, not a replacement, since a real right-click
        # (or a trackpad with two-finger-tap enabled) already sends
        # Button-3 correctly and should keep working as-is.
        self.amarker_canvas.bind("<Control-Button-1>", self._on_marker_lane_right_click)

        self.adb_axis_canvas = tk.Canvas(
            wave_area, width=DB_AXIS_WIDTH, height=220, highlightthickness=0, bd=0,
            bg=self._resolve_root_bg())
        self.adb_axis_canvas.grid(row=2, column=0, sticky="ns")
        # Scrolling the amplitude axis zooms vertically instead of in
        # time — stretches quiet audio taller to see its shape more
        # clearly (waveform mode only; there's no equivalent frequency-
        # range zoom for the spectrogram's axis yet).
        self.adb_axis_canvas.bind("<MouseWheel>", self._on_db_axis_scroll)
        self.adb_axis_canvas.bind("<Double-Button-1>", lambda _e: self._reset_vertical_zoom())
        self._add_tooltip(self.adb_axis_canvas, lambda: i18n.t(self.ui_lang, "aedit_tip_vaxis_scroll"))

        self.awave_canvas = tk.Canvas(wave_area, height=220, highlightthickness=0, bd=0,
                                      bg=self._resolve_root_bg())
        self.awave_canvas.grid(row=2, column=1, sticky="nsew")
        self.awave_canvas.bind("<Configure>", lambda _e: self._redraw_waveform())
        self.awave_canvas.bind("<ButtonPress-1>", self._on_wave_press)
        self.awave_canvas.bind("<B1-Motion>", self._on_wave_drag)
        self.awave_canvas.bind("<ButtonRelease-1>", self._on_wave_release)
        self.awave_canvas.bind("<MouseWheel>", self._on_wave_scroll)
        self.awave_canvas.bind("<Button-3>", self._on_wave_right_click)
        self.awave_canvas.bind("<Control-Button-1>", self._on_wave_right_click)  # see the marker-lane comment above

        # Horizontal scrollbar for panning a zoomed-in waveform — standard
        # Tk scrollbar protocol: its thumb position/size is set from
        # _redraw_waveform (via _update_wave_scrollbar) to always reflect
        # the current zoom window, and dragging/clicking it calls back
        # into _on_wave_hscroll to move that window.
        self.awave_scrollbar = ctk.CTkScrollbar(
            wave_area, orientation="horizontal", command=self._on_wave_hscroll)
        self.awave_scrollbar.grid(row=3, column=1, sticky="ew", pady=(2, 0))
        self.awave_scrollbar.set(0, 1)

        # Enhance, Clean, and Find (similar-segment search) — collapsed by
        # default, mutually exclusive (opening one closes the others),
        # same show/hide-a-panel pattern as the Edit & Export tab's
        # punctuation pad, sitting side by side rather than each being
        # its own tab: these all act on THIS waveform, so seeing them
        # stay right next to it beats a separate tab that hides what's
        # actually changing.
        panel_toggle_row = ctk.CTkFrame(parent, fg_color="transparent")
        panel_toggle_row.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 2))
        for col, which, attr in ((0, "enhance", "aenh"), (1, "find", "afind"),
                                 (2, "silence", "asilence"), (3, "markers", "amark")):
            btn = ctk.CTkButton(
                panel_toggle_row, text="", width=140, height=26, font=ctk.CTkFont(size=12),
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT,
                border_width=1, command=lambda w=which: self._toggle_effects_panel(w))
            btn.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0))
            # A thin accent strip under the active tab — the fg_color-only
            # difference between selected/unselected wasn't distinctive
            # enough to tell at a glance which panel (if any) is open.
            accent = ctk.CTkFrame(panel_toggle_row, height=3, fg_color="transparent")
            accent.grid(row=1, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0), pady=(2, 0))
            setattr(self, f"{attr}_toggle_button", btn)
            setattr(self, f"{attr}_toggle_accent", accent)

        # Enhance is the only sliders-panel now — Clean was merged into it
        # (see _build_enhance_panel's "Repairs & timing" group) once it
        # became clear no problem/tool actually sorted cleanly into two
        # tabs: "too quiet to hear" is as much a "problem" as a click or a
        # long pause, so the old Enhance/Clean boundary never had a real
        # rule behind it, just historical grouping-by-vibe. One tab, one
        # Auto Fix button, three grouped-by-mechanism sections below it.
        self.aenh_panel = ctk.CTkScrollableFrame(parent, fg_color=("gray90", "gray17"), height=220)
        self.aenh_panel.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.aenh_panel.grid_columnconfigure(0, weight=1)
        self._build_enhance_panel(self.aenh_panel)
        self.aenh_panel.grid_remove()

        # Matches/No speech/Markers are plain fixed-height frames, not
        # scrollable ones (unlike Enhance/Clean above, whose sliders
        # reliably fill or exceed 220px) — a CTkScrollableFrame's inner
        # frame only ever sizes itself to its actual content, so with few
        # or no rows the leftover space below was just empty canvas that
        # row weights couldn't reach, leaving the bottom Select all/none/
        # Delete row floating at an inconsistent height in each of the
        # three instead of anchored to the panel's bottom edge (found via
        # a real side-by-side comparison of all three when empty). Row 1
        # (each build method's rows_frame) is given the vertical weight
        # so it — not the button row — absorbs any leftover space.
        self.afind_panel = ctk.CTkFrame(parent, fg_color=("gray90", "gray17"), height=220)
        self.afind_panel.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.afind_panel.grid_columnconfigure(0, weight=1)
        self.afind_panel.grid_rowconfigure(1, weight=1)
        self.afind_panel.grid_propagate(False)
        self._build_find_panel(self.afind_panel)
        self.afind_panel.grid_remove()

        self.asilence_panel = ctk.CTkFrame(parent, fg_color=("gray90", "gray17"), height=220)
        self.asilence_panel.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.asilence_panel.grid_columnconfigure(0, weight=1)
        self.asilence_panel.grid_rowconfigure(1, weight=1)
        self.asilence_panel.grid_propagate(False)
        self._build_silence_panel(self.asilence_panel)
        self.asilence_panel.grid_remove()

        self.amark_panel = ctk.CTkFrame(parent, fg_color=("gray90", "gray17"), height=220)
        self.amark_panel.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.amark_panel.grid_columnconfigure(0, weight=1)
        self.amark_panel.grid_rowconfigure(1, weight=1)
        self.amark_panel.grid_propagate(False)
        self._build_markers_panel(self.amark_panel)
        self.amark_panel.grid_remove()

        self._render_effects_toggle_buttons()

        saverow = ctk.CTkFrame(parent, fg_color="transparent")
        saverow.grid(row=6, column=0, sticky="ew", padx=12, pady=(2, 8))
        saverow.grid_columnconfigure(4, weight=1)
        self.aedit_save_wav_button = ctk.CTkButton(
            saverow, text="", height=34, command=lambda: self._export_audio_clip("wav"))
        self.aedit_save_wav_button.grid(row=0, column=0, padx=(0, 8))
        self.aedit_save_mp3_button = ctk.CTkButton(
            saverow, text="", height=34, command=lambda: self._export_audio_clip("mp3"))
        self.aedit_save_mp3_button.grid(row=0, column=1, padx=(0, 8))
        self.aedit_revert_button = ctk.CTkButton(
            saverow, text="", height=34, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=self._audio_edit_revert)
        self.aedit_revert_button.grid(row=0, column=2, padx=(0, 10))
        self.aedit_open_folder_button = ctk.CTkButton(
            saverow, text="", height=34, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=lambda: self._open_output_folder(settings.audio_exports_folder()))
        self.aedit_open_folder_button.grid(row=0, column=3, padx=(0, 10))
        self.aedit_status_line = ctk.CTkLabel(saverow, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.aedit_status_line.grid(row=0, column=4, sticky="ew")

    # which -> (panel widget, toggle button, i18n key) — Enhance/Matches/
    # No speech/Markers are mutually exclusive collapsible panels inside
    # the Edit subtab; opening one closes whichever other was open.
    EFFECT_PANEL_ACCENT = "#3B8ED0"  # same blue as the waveform/selection — ties the tab to what it edits

    def _effect_panel_registry(self):
        return {
            "enhance": (self.aenh_panel, self.aenh_toggle_button, self.aenh_toggle_accent, "aenh_toggle"),
            "find": (self.afind_panel, self.afind_toggle_button, self.afind_toggle_accent, "afind_toggle"),
            "silence": (self.asilence_panel, self.asilence_toggle_button, self.asilence_toggle_accent,
                       "asilence_toggle"),
            "markers": (self.amark_panel, self.amark_toggle_button, self.amark_toggle_accent, "amark_toggle"),
        }

    def _toggle_effects_panel(self, which):
        self.active_effects_panel = None if self.active_effects_panel == which else which
        self._show_active_effects_panel()

    # which -> the rows-list frame whose scrollbar needs re-checking once
    # its panel actually gets real screen geometry (see
    # _update_rows_scrollbar) — Enhance/Clean have no such list.
    _ROWS_FRAME_ATTR = {"find": "afind_rows_frame", "silence": "asilence_rows_frame",
                        "markers": "amark_rows_frame"}

    def _show_active_effects_panel(self):
        for name, (panel, _button, _accent, _key) in self._effect_panel_registry().items():
            if name == self.active_effects_panel:
                panel.grid()
            else:
                panel.grid_remove()
        self._render_effects_toggle_buttons()
        # A render that happened while this panel was still hidden
        # (grid_remove'd) had no real height to measure yet, so the
        # scrollbar decision made then could be stale — redo it now that
        # the panel is actually visible and sized.
        rows_attr = self._ROWS_FRAME_ATTR.get(self.active_effects_panel)
        if rows_attr is not None:
            self._update_rows_scrollbar(getattr(self, rows_attr))

    def _render_effects_toggle_buttons(self):
        for which, (_panel, button, accent, key) in self._effect_panel_registry().items():
            active = self.active_effects_panel == which
            arrow = "▾" if active else "▸"
            button.configure(
                text=f"{arrow} {i18n.t(self.ui_lang, key)}",
                fg_color=("gray75", "gray30") if active else "transparent")
            accent.configure(fg_color=self.EFFECT_PANEL_ACCENT if active else "transparent")

    # -- clip loading -------------------------------------------------------

    def _open_audio_clip_dialog(self):
        path = filedialog.askopenfilename(
            title=i18n.t(self.ui_lang, "aedit_open_dialog"),
            filetypes=[(i18n.t(self.ui_lang, "audio_filetypes"),
                       "*.wav *.mp3 *.m4a *.flac *.ogg *.aac")])
        if path:
            self._open_audio_clip(path)

    def _open_audio_clip(self, path):
        """Decoding runs on a background thread behind the modal "please
        wait" dialog — a several-minute MP3 can take a noticeable moment
        to decode/resample, which used to just freeze the window (or, in
        an earlier version, disable one button and change a status label
        that was easy to miss) with no clear feedback that it was still
        working."""
        def work():
            return audio_clip.AudioClip.load(path, settings.audio_originals_folder())

        def done(clip, error):
            if error is not None or clip is None:
                self._set_audio_edit_status("aedit_status_load_failed", {})
                return
            self.audio_clip = clip
            self.audio_clip_path = path
            self.audio_selection = None
            self.audio_clipboard = None
            self.audio_preview = None
            self.audio_noise_profile = None  # a profile from a previous file means nothing for this one
            self._update_noise_profile_label()
            self._clear_find_results()  # matches/silence spans were sample positions in the OLD clip
            self._clear_silence_results()
            self.audio_player.load_buffer(clip.buffer, clip.sample_rate)
            self._zoom_audio_edit_fit()
            self._refresh_audio_studio_ui()
            self._set_audio_edit_status("aedit_status_loaded", {})

        self._run_busy("aedit_status_loading", work, done)

    def _refresh_audio_studio_ui(self):
        has_clip = self.audio_clip is not None
        self._set_audio_edit_controls_enabled(has_clip)
        self._set_audio_enhance_controls_enabled(has_clip)
        self._render_markers_panel()
        self._redraw_waveform()

    def _set_audio_edit_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for attr in ("aedit_play_button", "aedit_stop_button", "aedit_speed_menu",
                     "aedit_zoom_out_button", "aedit_zoom_fit_button", "aedit_zoom_in_button",
                     "aedit_cut_button", "aedit_copy_button", "aedit_trim_button",
                     "aedit_split_button", "aedit_silence_button", "aedit_find_button",
                     "aedit_detect_silence_button",
                     "aedit_marker_button", "amark_sections_button",
                     "aedit_save_wav_button", "aedit_save_mp3_button", "aedit_revert_button"):
            getattr(self, attr).configure(state=state)
        can_paste = enabled and self.audio_clipboard is not None
        self.aedit_paste_button.configure(state="normal" if can_paste else "disabled")
        can_undo = enabled and self.audio_clip is not None and self.audio_clip.can_undo
        can_redo = enabled and self.audio_clip is not None and self.audio_clip.can_redo
        self.aedit_undo_button.configure(state="normal" if can_undo else "disabled")
        self.aedit_redo_button.configure(state="normal" if can_redo else "disabled")

    def _set_audio_edit_status(self, key, detail=None):
        self.audio_status_key, self.audio_status_detail = key, detail or {}
        self.aedit_status_line.configure(text=i18n.t(self.ui_lang, key, **(detail or {})))

    # -- transport ------------------------------------------------------

    def _guarded_shortcut(self, handler):
        """Wraps a no-arg `handler` so it only fires while Audio Studio's
        Edit subtab is showing, and never while focus is actually inside
        a text field elsewhere in the app (so e.g. Ctrl+C in the Record
        tab's filename field still copies text, not audio) — every
        binding in AUDIO_EDIT_SHORTCUTS shares this one gate, bound
        globally via bind_all since Tk has no per-tab keyboard scope of
        its own."""
        def wrapped(event):
            if self.current_tab != self.LEAF_AUDIO_EDIT:
                return None
            if isinstance(event.widget, (tk.Entry, tk.Text)):
                return None
            handler()
            return "break"
        return wrapped

    def _audio_edit_delete(self):
        """Delete/Backspace: removes the selection without touching the
        clipboard — distinct from Cut, which is the same removal but also
        overwrites whatever was last copied."""
        if not self.audio_clip or not self.audio_selection:
            self._set_audio_edit_status("aedit_status_need_selection")
            return
        start, end = self.audio_selection
        self.audio_clip.cut(start, end)
        self.audio_selection = None
        self._after_audio_edit()

    def _toggle_audio_edit_play(self):
        if self.audio_clip is None:
            return
        if self.audio_player.is_playing:
            self.audio_player.pause()
            self._play_until = None
        else:
            if self.audio_selection and self.audio_preview is None:
                # A selection is active (and this isn't already an
                # isolated Preview, which sets its own range) — play only
                # that region, same "only play the selected part" idea as
                # Enhance/Clean Preview.
                start, end = self.audio_selection
                self._seek_audio_edit_to(start)
                self._play_until = end
            else:
                self._play_until = None
            self.audio_player.play()
        self._render_audio_edit_play_button()

    def _stop_audio_edit_play(self):
        self.audio_player.stop()
        self._play_until = None
        self._clear_audio_preview()
        self._render_audio_edit_play_button()
        self._redraw_waveform()

    def _clear_audio_preview(self):
        """Drops a pending (unapplied) Enhance/Clean preview and restores
        the real clip to the player — called whenever the preview stops
        being the relevant thing on screen (playback ends, a new
        selection starts, an edit commits, a new clip loads)."""
        if self.audio_preview is None:
            return
        self.audio_preview = None
        self._preview_offset_s = 0.0
        if self.audio_clip is not None:
            self.audio_player.load_buffer(self.audio_clip.buffer, self.audio_clip.sample_rate)

    def _render_audio_edit_play_button(self):
        self.aedit_play_button.configure(
            text=i18n.t(self.ui_lang, "player_pause" if self.audio_player.is_playing else "player_play"))

    def _on_audio_edit_speed_change(self, label):
        self.audio_player.set_speed(float(label.rstrip("×")))

    # -- zoom / waveform / selection --------------------------------------

    def _zoom_audio_edit_fit(self):
        self.audio_zoom = (0.0, self.audio_clip.duration if self.audio_clip else 0.0)
        self._redraw_waveform()

    def _zoom_audio_edit(self, factor):
        """The +/- zoom buttons center on whatever point the user last
        clicked/selected on the waveform (same idea as the scroll-wheel
        zoom in _on_wave_scroll, just centered on that point instead of
        keeping it at its current on-screen position, since a button
        click has no cursor position of its own to anchor to). A plain
        click (no drag) doesn't leave a real selection behind — it just
        moves the playhead there — so the playhead position is the
        anchor in that case; falls back to the current view's center
        only when nothing has ever been clicked/sought."""
        if self.audio_clip is None:
            return
        start, end = self.audio_zoom
        if self.audio_selection is not None:
            sel_start, sel_end = self.audio_selection
            center = (sel_start + sel_end) / 2
        else:
            center = self.audio_player.get_time() or (start + end) / 2
        span = max(0.05, (end - start) * factor)
        full = self.audio_clip.duration
        span = min(span, full) if full else span
        new_start = max(0.0, min(full - span, center - span / 2)) if full else 0.0
        self.audio_zoom = (new_start, new_start + span)
        self._redraw_waveform()

    def _update_wave_scrollbar(self):
        """Sets the horizontal scrollbar's thumb to match the current
        zoom window — a full-width thumb (0..1) when zoomed all the way
        out, matching the standard Tk scrollbar protocol."""
        full = self.audio_clip.duration if self.audio_clip else 0.0
        if full <= 0:
            self.awave_scrollbar.set(0, 1)
            return
        start, end = self.audio_zoom
        self.awave_scrollbar.set(start / full, min(1.0, end / full))

    def _on_wave_hscroll(self, *args):
        """Scrollbar drag/click callback (standard Tk protocol: either
        ("moveto", fraction) or ("scroll", amount, "units"/"pages"))."""
        if self.audio_clip is None or self.audio_clip.duration <= 0:
            return
        full = self.audio_clip.duration
        start, end = self.audio_zoom
        span = end - start
        if args[0] == "moveto":
            new_start = float(args[1]) * full
        elif args[0] == "scroll":
            amount = float(args[1])
            step = span * (0.9 if args[2] == "pages" else 0.1)
            new_start = start + amount * step
        else:
            return
        new_start = max(0.0, min(full - span, new_start)) if full > span else 0.0
        self.audio_zoom = (new_start, new_start + span)
        self._redraw_waveform()

    # "Nice" timeline tick intervals (seconds) — the ruler picks the
    # smallest one that still keeps labels roughly TIMELINE_TARGET_PX
    # apart, so they never overlap regardless of zoom level.
    TIMELINE_TARGET_PX = 80
    TIMELINE_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]

    def _update_selection_label(self):
        if self.audio_clip is not None and self.audio_selection:
            s, e = self.audio_selection
            self.aedit_selection_label.configure(text=i18n.t(
                self.ui_lang, "aedit_selection_info",
                start=_fmt_time(s), end=_fmt_time(e), dur=f"{e - s:.1f}"))
        else:
            self.aedit_selection_label.configure(
                text=i18n.t(self.ui_lang, "aedit_no_selection") if self.audio_clip is not None else "")

    def _update_dirty_indicator(self):
        """Sets the picker row's file label to name+duration, and the
        adjacent (red) label to "unsaved changes" when dirty — sharing
        that row rather than each costing its own strip of height."""
        if self.audio_clip is None:
            self.aedit_file_label.configure(text=i18n.t(self.ui_lang, "aedit_no_file"))
            self.aedit_dirty_label.configure(text="")
            return
        name = os.path.basename(self.audio_clip_path or "")
        self.aedit_file_label.configure(text=i18n.t(
            self.ui_lang, "aedit_file_status", name=name,
            duration=_fmt_time(self.audio_clip.duration)))
        self.aedit_dirty_label.configure(
            text=i18n.t(self.ui_lang, "aedit_dirty") if self.audio_clip.dirty else "")

    def _redraw_waveform(self):
        """Redraws the waveform/spectrogram canvas. The expensive part —
        the actual waveform lines, or the spectrogram image — is cached
        (self._wave_content_cache_key) and only rebuilt when what it
        depends on (zoom window, canvas size, preview state, or the
        buffer itself) actually changed. A plain selection drag — by far
        the most frequent caller, firing on every mouse-move — just moves
        the lightweight selection/match overlay instead. Without this,
        every drag event recomputed the full spectrogram (multiple
        seconds for a zoomed-out clip) or rebuilt hundreds of waveform
        line items, which is what made clicking the canvas visibly blank
        out. Tick updates during playback go through _update_playhead_line
        instead, which only moves one line item — untouched by either
        path here."""
        self._update_selection_label()
        self._update_dirty_indicator()
        self._update_spectrogram_caption()

        canvas = self.awave_canvas
        if self.audio_clip is None or self.audio_clip.buffer.size == 0:
            root_bg = self._resolve_root_bg()
            canvas.delete("all")
            canvas.configure(bg=root_bg)
            self._playhead_item = None
            self._wave_content_cache_key = None
            self.awave_scrollbar.set(0, 1)
            self.atimeline_canvas.delete("all")
            self.atimeline_canvas.configure(bg=root_bg)
            self.adb_axis_canvas.delete("all")
            self.adb_axis_canvas.configure(bg=root_bg)
            self.amarker_canvas.delete("all")
            self.amarker_canvas.configure(bg=root_bg)
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        start, end = self.audio_zoom
        self._update_wave_scrollbar()
        self._redraw_timeline()
        self._redraw_db_axis(height)
        self._redraw_marker_lane()
        if end <= start:
            canvas.delete("all")
            self._wave_content_cache_key = None
            return

        previewing = self.audio_preview is not None
        display_buffer = self.audio_preview if previewing else self.audio_clip.buffer
        cache_key = (self.wave_view_mode, start, end, width, height, id(display_buffer),
                    self._preview_range_samples if previewing else None, self.audio_vzoom)
        if cache_key != self._wave_content_cache_key:
            canvas.delete("all")
            self._playhead_item = None
            canvas.configure(bg=self._resolve_root_bg())
            if self.wave_view_mode == "spectrogram":
                self._draw_spectrogram_content(canvas, width, height, start, end)
            else:
                self._draw_waveform_content(canvas, width, height, start, end, display_buffer, previewing)
            self._wave_content_cache_key = cache_key
        else:
            canvas.delete("overlay")

        self._draw_selection_overlay(canvas, width, height, start, end)
        self._update_playhead_line()

    def _draw_waveform_content(self, canvas, width, height, start, end, display_buffer, previewing):
        # While an Enhance/Clean effect is being previewed (live, as a
        # slider moves — see _update_effect_preview), only the region it
        # actually changed renders from the not-yet-applied composite,
        # tinted amber; everything outside that keeps showing the real
        # clip in blue — so the tint always matches exactly what Apply
        # would change, not the whole recording.
        mid = height / 2
        vscale = mid * self.audio_vzoom  # scrolling the dB axis (see _on_db_axis_scroll) adjusts this
        sr = self.audio_clip.sample_rate
        mins, maxes = audio_clip.peaks_from_buffer(display_buffer, sr, start, end, width)
        if previewing:
            p0, p1 = self._preview_range_samples
            i0_view = max(0, int(round(start * sr)))
            span_samples = max(1, int(round((end - start) * sr)))
            for x in range(width):
                col_sample = i0_view + int((x + 0.5) / width * span_samples)
                color = "#d99a30" if p0 <= col_sample < p1 else "#3B8ED0"
                y0 = mid - float(maxes[x]) * vscale
                y1 = mid - float(mins[x]) * vscale
                canvas.create_line(x, y0, x, y1 + 1, fill=color, tags="content")
            canvas.create_text(8, 8, anchor="nw", fill="#d99a30",
                               text=i18n.t(self.ui_lang, "aedit_previewing"), tags="content")
        else:
            for x in range(width):
                y0 = mid - float(maxes[x]) * vscale
                y1 = mid - float(mins[x]) * vscale
                canvas.create_line(x, y0, x, y1 + 1, fill="#3B8ED0", tags="content")

    def _draw_spectrogram_content(self, canvas, width, height, start, end):
        image = audio_spectrogram.compute_image(
            self.audio_clip.buffer, self.audio_clip.sample_rate, start, end, width, height)
        self._spectrogram_photo = ImageTk.PhotoImage(image)  # keep a reference — Tk drops an unreferenced one
        canvas.create_image(0, 0, anchor="nw", image=self._spectrogram_photo, tags="content")

    def _draw_selection_overlay(self, canvas, width, height, start, end):
        # The selection itself stays visibly highlighted regardless of
        # whether a preview is active — adjusting a slider shouldn't make
        # the region you're working on harder to see.
        if self.audio_selection:
            sel_start, sel_end = self.audio_selection
            x0 = (max(start, sel_start) - start) / (end - start) * width
            x1 = (min(end, sel_end) - start) / (end - start) * width
            if x1 > x0:
                canvas.create_rectangle(x0, 0, x1, height, fill="#3B8ED0",
                                        stipple="gray25", outline="", tags="overlay")

        # Every currently-checked Find Similar match, in green — visible
        # regardless of which panel is open, so you can see at a glance
        # what's about to be removed while scrolling/zooming around.
        for (m_start, m_end, _score), var in zip(self._find_visible_matches, self._find_vars):
            if not var.get():
                continue
            x0 = (max(start, m_start) - start) / (end - start) * width
            x1 = (min(end, m_end) - start) / (end - start) * width
            if x1 > x0:
                canvas.create_rectangle(x0, 0, x1, height, fill="#4caf50",
                                        stipple="gray25", outline="", tags="overlay")

        # Every currently-checked detected-silence span, in purple — same
        # always-visible treatment as matches above, in a different color
        # so the two review lists stay visually distinct on the waveform.
        for (s_start, s_end), var in zip(self._silence_spans, self._silence_vars):
            if not var.get():
                continue
            x0 = (max(start, s_start) - start) / (end - start) * width
            x1 = (min(end, s_end) - start) / (end - start) * width
            if x1 > x0:
                canvas.create_rectangle(x0, 0, x1, height, fill="#9575CD",
                                        stipple="gray25", outline="", tags="overlay")

    def _redraw_timeline(self):
        """Ruler above the waveform showing mm:ss tick marks — spaced by
        a 'nice' interval (1/2/5 x 10^n seconds) chosen so labels stay
        legible at any zoom level instead of overlapping."""
        canvas = self.atimeline_canvas
        canvas.delete("all")
        canvas.configure(bg=self._resolve_root_bg())
        start, end = self.audio_zoom
        span = end - start
        if span <= 0:
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        text_color = self._apply_appearance_mode(self.MUTED_TEXT)
        target_interval = span * self.TIMELINE_TARGET_PX / width
        interval = self.TIMELINE_STEPS[-1]
        for step in self.TIMELINE_STEPS:
            if step >= target_interval:
                interval = step
                break
        t = math.ceil(start / interval) * interval
        while t <= end + 1e-9:
            x = (t - start) / span * width
            canvas.create_line(x, height - 6, x, height, fill=text_color)
            canvas.create_text(x + 3, 1, anchor="nw", text=_fmt_time(t),
                               fill=text_color, font=("Segoe UI", 9))
            t += interval

    # dBFS levels the vertical axis marks, mirrored above/below the
    # silence (center) line — 0dB is full scale (±1.0 in the raw buffer).
    # Extends well past what's ever visible at vzoom=1 (only 0..-24 fits)
    # since vertically zooming in (see _on_db_axis_scroll) is specifically
    # for seeing quiet detail more clearly, which means deeper negative
    # levels come into view — _redraw_db_axis skips whichever of these
    # land off-canvas rather than needing a different list per zoom level.
    DB_AXIS_LEVELS = (0, -6, -12, -18, -24, -30, -36, -42, -48, -54, -60)

    def _redraw_db_axis(self, height):
        """Narrow vertical ruler to the left of the waveform. In waveform
        mode it marks amplitude in dBFS (mirrored above/below the center
        silence line, since the waveform itself is bipolar); in
        spectrogram mode there's no amplitude axis to show (color is
        amplitude there) so this instead marks frequency, 0Hz at the
        bottom to Nyquist at the top like any other spectrogram."""
        if self.wave_view_mode == "spectrogram":
            self._redraw_frequency_axis(height)
            return
        canvas = self.adb_axis_canvas
        canvas.delete("all")
        canvas.configure(bg=self._resolve_root_bg())
        width = max(1, canvas.winfo_width())
        mid = height / 2
        vscale = mid * self.audio_vzoom  # same scale _draw_waveform_content uses, so the axis always matches
        text_color = self._apply_appearance_mode(self.MUTED_TEXT)
        canvas.create_line(width - 1, 0, width - 1, height, fill=text_color)
        for db in self.DB_AXIS_LEVELS:
            amp = 10 ** (db / 20.0)
            y_top, y_bottom = mid - amp * vscale, mid + amp * vscale
            if y_top < 0 or y_bottom > height:
                continue  # zoomed in past this level, or zoomed out enough it'd crowd the one above/below it
            canvas.create_line(width - 6, y_top, width, y_top, fill=text_color)
            canvas.create_line(width - 6, y_bottom, width, y_bottom, fill=text_color)
            canvas.create_text(width - 8, y_top, anchor="e", text=str(db),
                               fill=text_color, font=("Segoe UI", 8))
        canvas.create_line(width - 6, mid, width, mid, fill=text_color)
        canvas.create_text(width - 8, mid, anchor="e", text="-∞",
                           fill=text_color, font=("Segoe UI", 8))

    # Frequency gridlines for the spectrogram axis, in Hz — 0 is always
    # added at the bottom regardless of Nyquist.
    FREQUENCY_AXIS_LEVELS_HZ = [1000, 2000, 4000, 8000, 16000]

    def _redraw_frequency_axis(self, height):
        canvas = self.adb_axis_canvas
        canvas.delete("all")
        canvas.configure(bg=self._resolve_root_bg())
        width = max(1, canvas.winfo_width())
        text_color = self._apply_appearance_mode(self.MUTED_TEXT)
        nyquist = (self.audio_clip.sample_rate / 2.0) if self.audio_clip else 22050.0
        canvas.create_line(width - 1, 0, width - 1, height, fill=text_color)
        canvas.create_text(width - 8, height - 2, anchor="se", text="0",
                           fill=text_color, font=("Segoe UI", 8))
        for hz in self.FREQUENCY_AXIS_LEVELS_HZ:
            if hz >= nyquist:
                break
            y = height - (hz / nyquist) * height
            canvas.create_line(width - 6, y, width, y, fill=text_color)
            label = f"{hz // 1000}k" if hz >= 1000 else str(hz)
            canvas.create_text(width - 8, y, anchor="e", text=label,
                               fill=text_color, font=("Segoe UI", 8))

    def _update_spectrogram_caption(self):
        if self.wave_view_mode != "spectrogram" or self.audio_clip is None:
            self.aedit_spectrogram_caption.grid_remove()
            return
        nyquist_khz = self.audio_clip.sample_rate / 2000.0
        self.aedit_spectrogram_caption.configure(
            text=i18n.t(self.ui_lang, "aedit_spectrogram_caption", nyquist=f"{nyquist_khz:.1f}"))
        self.aedit_spectrogram_caption.grid()

    def _on_wave_view_change(self, value):
        self.wave_view_mode = "spectrogram" if value == i18n.t(self.ui_lang, "aedit_view_spectrogram") \
            else "waveform"
        self._redraw_waveform()

    def _update_playhead_line(self):
        """Moves (or creates, or hides) just the playhead line — cheap
        enough to call every tick during playback without the flicker a
        full _redraw_waveform caused. While an isolated-selection Preview
        is playing, the player's own get_time() starts back at 0 for just
        that snippet, so _preview_offset_s maps it back onto the full
        clip's timeline for display."""
        canvas = self.awave_canvas
        if self.audio_clip is None or self.audio_player.loaded_path is None:
            return
        start, end = self.audio_zoom
        if end <= start:
            return
        offset = self._preview_offset_s if self.audio_preview is not None else 0.0
        playhead_t = offset + self.audio_player.get_time()
        height = max(1, canvas.winfo_height())
        if start <= playhead_t <= end:
            x = (playhead_t - start) / (end - start) * max(1, canvas.winfo_width())
            if self._playhead_item is None:
                self._playhead_item = canvas.create_line(
                    x, 0, x, height, fill="#e05a5a", width=2, tags="playhead")
            else:
                canvas.coords(self._playhead_item, x, 0, x, height)
                canvas.itemconfigure(self._playhead_item, state="normal")
        elif self._playhead_item is not None:
            canvas.itemconfigure(self._playhead_item, state="hidden")

    def _on_wave_scroll(self, event):
        """Mouse-wheel zoom, anchored at the cursor rather than the view
        center — the point under the pointer stays put as you zoom, same
        convention as a map or image viewer."""
        if self.audio_clip is None:
            return
        anchor = self._wave_x_to_seconds(event.x)
        factor = 0.8 if event.delta > 0 else 1.25
        self._zoom_audio_edit_at(factor, anchor)

    def _zoom_audio_edit_at(self, factor, anchor_time):
        if self.audio_clip is None:
            return
        start, end = self.audio_zoom
        full = self.audio_clip.duration
        span = max(0.05, (end - start) * factor)
        span = min(span, full) if full else span
        rel = (anchor_time - start) / (end - start) if end > start else 0.5
        new_start = anchor_time - rel * span
        new_start = max(0.0, min(full - span, new_start)) if full else 0.0
        self.audio_zoom = (new_start, new_start + span)
        self._redraw_waveform()

    VZOOM_MIN, VZOOM_MAX = 1.0, 20.0

    def _on_db_axis_scroll(self, event):
        """Scrolling the amplitude axis stretches/shrinks the waveform's
        vertical scale — same scroll-to-zoom convention as the time axis,
        just the other dimension. Only meaningful in waveform mode; the
        spectrogram's axis is frequency, which this doesn't touch."""
        if self.audio_clip is None or self.wave_view_mode != "waveform":
            return
        factor = 1.25 if event.delta > 0 else 0.8
        self.audio_vzoom = min(self.VZOOM_MAX, max(self.VZOOM_MIN, self.audio_vzoom * factor))
        self._redraw_waveform()

    def _reset_vertical_zoom(self):
        self.audio_vzoom = 1.0
        self._redraw_waveform()

    def _wave_x_to_seconds(self, x):
        width = max(1, self.awave_canvas.winfo_width())
        start, end = self.audio_zoom
        return start + max(0.0, min(1.0, x / width)) * (end - start)

    def _seek_audio_edit_to(self, seconds):
        """Moves the playhead to `seconds` — Play then resumes from there,
        and Insert Silence (with nothing selected) lands there too."""
        if self.audio_clip is None or self.audio_clip.duration <= 0:
            return
        frac = max(0.0, min(1.0, seconds / self.audio_clip.duration))
        self.audio_player.seek_fraction(frac)
        self.aedit_time_label.configure(
            text=f"{_fmt_time(self.audio_player.get_time())} / "
                 f"{_fmt_time(self.audio_player.duration)}")

    def _on_wave_press(self, event):
        if self.audio_clip is None:
            return
        self._play_until = None
        self._clear_audio_preview()
        self._wave_drag_start = self._wave_x_to_seconds(event.x)
        self.audio_selection = (self._wave_drag_start, self._wave_drag_start)
        self._redraw_waveform()

    def _on_wave_drag(self, event):
        if self.audio_clip is None or not hasattr(self, "_wave_drag_start"):
            return
        current = self._wave_x_to_seconds(event.x)
        self.audio_selection = tuple(sorted((self._wave_drag_start, current)))
        self._redraw_waveform()

    def _on_wave_release(self, _event):
        if self.audio_clip is None:
            return
        if self.audio_selection and abs(self.audio_selection[1] - self.audio_selection[0]) < 0.05:
            # A plain click, not a drag — moves the playhead there (shown
            # as the vertical marker) instead of leaving a zero-width
            # "selection" nothing downstream can act on.
            click_time = self._wave_drag_start
            self.audio_selection = None
            self._seek_audio_edit_to(click_time)
        self._redraw_waveform()
        self._set_audio_edit_controls_enabled(True)

    def _on_wave_right_click(self, _event):
        """Right-click resets the waveform's clicked state — clears any
        selection and moves the playhead (the red line) back to the
        clip's start, undoing whatever the last left-click/drag left
        behind without requiring the file to be reopened."""
        if self.audio_clip is None:
            return
        self.audio_selection = None
        self._seek_audio_edit_to(0.0)
        self._redraw_waveform()

    # -- editing operations -----------------------------------------------

    def _after_audio_edit(self):
        self.audio_preview = None
        self._preview_offset_s = 0.0
        self._play_until = None
        # Any edit (cut/trim/apply/insert/etc.) shifts sample positions
        # throughout the clip, so a match/silence list from before is no
        # longer trustworthy — cleared here rather than in each
        # individual edit method, since every one of them already funnels
        # through this.
        self._clear_find_results()
        self._clear_silence_results()
        # load_buffer() always resets playback to 0 (it's a fresh buffer
        # as far as the player's concerned) — restoring the pre-edit time
        # here keeps the playhead where the user actually was (e.g. right
        # after clicking a point and inserting silence there) instead of
        # jumping back to the start on every single edit.
        playhead_t = self.audio_player.get_time()
        self.audio_player.load_buffer(self.audio_clip.buffer, self.audio_clip.sample_rate)
        self._seek_audio_edit_to(playhead_t)
        self._refresh_audio_studio_ui()

    def _audio_edit_cut(self):
        if not self.audio_clip or not self.audio_selection:
            self._set_audio_edit_status("aedit_status_need_selection")
            return
        start, end = self.audio_selection
        self.audio_clipboard = self.audio_clip.cut(start, end)
        self.audio_selection = None
        self._after_audio_edit()

    def _audio_edit_copy(self):
        if not self.audio_clip or not self.audio_selection:
            self._set_audio_edit_status("aedit_status_need_selection")
            return
        start, end = self.audio_selection
        self.audio_clipboard = self.audio_clip.copy_region(start, end)
        self._set_audio_edit_controls_enabled(True)

    def _audio_edit_paste(self):
        if not self.audio_clip or self.audio_clipboard is None:
            return
        at = self.audio_selection[0] if self.audio_selection else self.audio_clip.duration
        self.audio_clip.paste(at, self.audio_clipboard)
        self._after_audio_edit()

    def _audio_edit_trim(self):
        if not self.audio_clip or not self.audio_selection:
            self._set_audio_edit_status("aedit_status_need_selection")
            return
        start, end = self.audio_selection
        self.audio_clip.trim(start, end)
        self.audio_selection = None
        self._after_audio_edit()
        self._zoom_audio_edit_fit()

    def _audio_edit_split(self):
        """Splits at the playhead (a single point — click the waveform to
        place it, same as anywhere else in Edit) into two files: the half
        before the split point stays open as the current clip, the half
        after is written out alongside it. Split is point-based rather
        than selection-based on purpose — Audio Studio only ever edits
        one clip in memory at a time, so it produces two files rather
        than two open clips, and a single point is all "cut this
        recording into two files here" needs (unlike Trim, which keeps a
        selected range and needs both a start and an end)."""
        if not self.audio_clip:
            return
        at = self.audio_player.get_time()
        before, after = self.audio_clip.split(at)
        src = self.audio_clip_path or "clip.wav"
        base, ext = os.path.splitext(os.path.basename(src))
        folder = settings.audio_exports_folder()
        after_path = transcriber.unique_path(
            os.path.join(folder, f"{base} (split){ext or '.wav'}"))
        audio_clip.write_wav(after_path, after, self.audio_clip.sample_rate)
        # Markers past the split point went with `after`, which isn't
        # tracked by this clip anymore — drop them; the rest are already
        # at the right time (nothing before the split point moved).
        markers_before = [dict(m) for m in self.audio_clip.markers]
        self.audio_clip.markers = [m for m in self.audio_clip.markers if m["time"] < at]
        self.audio_clip.apply(before, markers_before=markers_before)
        self._after_audio_edit()
        self._set_audio_edit_status("aedit_status_split", {"path": os.path.basename(after_path)})

    INSERT_SILENCE_S = 1.0  # kept as one constant so the button's tooltip can't drift from the actual amount

    def _audio_edit_insert_silence(self):
        if not self.audio_clip:
            return
        at = self.audio_selection[0] if self.audio_selection else self.audio_player.get_time()
        self.audio_clip.insert_silence(at, self.INSERT_SILENCE_S)
        self._after_audio_edit()

    def _audio_edit_undo(self):
        if self.audio_clip and self.audio_clip.undo():
            self._after_audio_edit()

    def _audio_edit_redo(self):
        if self.audio_clip and self.audio_clip.redo():
            self._after_audio_edit()

    def _audio_edit_revert(self):
        if self.audio_clip and self.audio_clip.revert_to_original():
            self._after_audio_edit()
            self._zoom_audio_edit_fit()
            self._set_audio_edit_status("aedit_status_reverted")

    def _export_audio_clip(self, fmt):
        if self.audio_clip is None:
            return
        base = os.path.splitext(os.path.basename(self.audio_clip_path or "clip"))[0]
        folder = settings.audio_exports_folder()
        path = transcriber.unique_path(os.path.join(folder, f"{base}.{fmt}"))
        buffer, sample_rate = self.audio_clip.buffer, self.audio_clip.sample_rate

        def work():
            audio_export.export_audio(buffer, sample_rate, path, fmt=fmt)
            return path

        def done(_result, error):
            if error is not None:  # already logged by _run_busy itself
                self._set_audio_edit_status("aedit_status_export_failed")
                return
            self.audio_clip.mark_exported()
            self._update_dirty_indicator()
            self._set_audio_edit_status("aedit_status_exported", {"path": os.path.basename(path)})

        self._run_busy("aedit_status_exporting", work, done)

    # -- shared Enhance/Clean effect-row builder ---------------------------

    def _build_effect_row(self, parent, row, label_key, slider_from, slider_to,
                          slider_default, on_change, on_apply, fmt="{:.0f}"):
        """One Enhance/Clean effect row: label, a slider, a live value
        readout, a Revert button, and an Apply button. Moving the slider
        itself updates the waveform's amber preview overlay live
        (on_change fires on every drag tick, same as the value readout)
        — no separate Preview step. The preview then stays up (even
        through Play/Stop) until you explicitly choose Revert (discard
        it, back to the real clip) or Apply (commit it, pushing an undo
        entry). Every simple single-parameter effect in both subtabs
        shares this instead of each hand-building a near-identical row."""
        frame = ctk.CTkFrame(parent)
        frame.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
        frame.grid_columnconfigure(1, weight=1)
        label = ctk.CTkLabel(frame, text="", anchor="w", width=150)
        label.grid(row=0, column=0, padx=(10, 8), pady=10, sticky="w")
        value_label = ctk.CTkLabel(frame, text="", width=64, anchor="e")

        def _on_move(v):
            value_label.configure(text=fmt.format(float(v)))
            on_change(v)

        var = tk.DoubleVar(value=slider_default)
        slider = ctk.CTkSlider(frame, from_=slider_from, to=slider_to,
                               variable=var, command=_on_move)
        value_label.configure(text=fmt.format(float(slider_default)))  # label only — on_change needs a loaded clip
        slider.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=10)
        value_label.grid(row=0, column=2, padx=(0, 10), pady=10)

        def _revert():
            # Also resets the slider back to its starting value — leaving
            # it wherever it was dragged to but with the preview
            # discarded would make the slider lie about what's actually
            # showing on the waveform.
            var.set(slider_default)
            value_label.configure(text=fmt.format(float(slider_default)))
            self._revert_effect_preview()

        revert_btn = ctk.CTkButton(
            frame, text="", width=80, height=26, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=_revert)
        revert_btn.grid(row=0, column=3, padx=(0, 6), pady=10)
        apply_btn = ctk.CTkButton(
            frame, text="", width=90, height=26, command=lambda: on_apply(var.get()))
        apply_btn.grid(row=0, column=4, padx=(0, 10), pady=10)
        return {"frame": frame, "label": label, "label_key": label_key, "slider": slider, "var": var,
                "value_label": value_label, "revert_btn": revert_btn, "apply_btn": apply_btn,
                "fmt": fmt}

    def _sync_effect_row_display(self, row, value):
        """Moves a slider (and its value readout) to `value` without
        triggering on_change/preview — used right after a one-click
        preset applies its own hard-coded settings, so the slider shows
        what was actually just applied instead of silently sitting at
        its old default as if nothing happened."""
        row["var"].set(value)
        row["value_label"].configure(text=row["fmt"].format(float(value)))

    # -- generic "please wait" modal for any button-triggered action that -----
    # -- can take a noticeable moment (file decode, denoise, VAD, export) -----
    #
    # Distinct from Find Similar's own progress dialog (_show_find_progress_
    # dialog): that one supports real percentage progress and Cancel because
    # the search is naturally chunkable; none of these are, so this is just
    # an indeterminate spinner with no cancel — still modal (grab_set), for
    # the same reason Find Similar's is: leaving the rest of the app
    # clickable while something is quietly running in the background is what
    # trained people to think the app had frozen and keep clicking other
    # buttons, which is worse than a plain "busy" state.

    def _show_busy_dialog(self, title_key):
        dlg = ctk.CTkToplevel(self)
        dlg.title(i18n.t(self.ui_lang, title_key))
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)  # no cancel — not safely interruptible mid-flight
        ctk.CTkLabel(dlg, text=i18n.t(self.ui_lang, title_key)).pack(padx=30, pady=(24, 14))
        bar = ctk.CTkProgressBar(dlg, width=260, mode="indeterminate")
        bar.pack(padx=30, pady=(0, 24))
        bar.start()
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.grab_set()  # modal — blocks the rest of the app while this is up, see note above
        self._busy_dialog = dlg
        self._busy_bar = bar

    def _close_busy_dialog(self):
        if self._busy_dialog is not None:
            self._busy_bar.stop()
            self._busy_dialog.grab_release()
            self._busy_dialog.destroy()
        self._busy_dialog = None
        self._busy_bar = None

    def _run_busy(self, title_key, work_fn, on_done):
        """Runs work_fn() (no args — the caller closes over whatever
        snapshot of state it needs, captured *before* this is called, so
        the background thread never races a live self.* read against
        whatever the main thread does next) on a background thread behind
        the modal above. on_done(result, error) runs on the main thread
        once work_fn returns or raises — exactly one of result/error is
        not None."""
        self._show_busy_dialog(title_key)

        def task():
            try:
                result = work_fn()
                self.events.put(("busy_done", on_done, result, None))
            except Exception as exc:
                settings.log_exception(f"Audio Studio: background task failed ({title_key}):")
                self.events.put(("busy_done", on_done, None, exc))

        threading.Thread(target=task, daemon=True).start()

    def _selection_bounds(self):
        """Sample-index bounds an Enhance/Clean effect should act on: the
        current waveform selection if there is one, else the whole clip."""
        if self.audio_selection:
            return self.audio_clip.sample_bounds(*self.audio_selection)
        return 0, len(self.audio_clip.buffer)

    def _selected_buffer(self):
        i0, i1 = self._selection_bounds()
        return self.audio_clip.buffer[i0:i1]

    def _update_effect_preview(self, result_buffer, autoplay=False):
        """Shows `result_buffer` (the effect applied to the current
        selection, or the whole clip) spliced into a full-length preview
        copy — without touching audio_clip — on the waveform (amber).
        Called on every slider drag tick (see _build_effect_row's
        on_change), so moving a slider is itself the preview — there's no
        separate Preview button/step to click first; Play (or an
        already-running playback, which restarts on the new value) is
        what actually lets you hear it. `autoplay` is for Denoise's own
        explicit Preview button, which has no slider to drive this live.

        Playback itself only covers what actually changed: with a
        selection active, `result_buffer` alone (just that processed
        snippet) is what's loaded, starting over at t=0 —
        _preview_offset_s records where that snippet sits in the full
        clip's timeline so _update_playhead_line/the time label can still
        show a real position instead of a confusing "00:00 of 00:03" on a
        5-minute recording. With no selection, the effect covers the
        whole clip anyway, so the full composite is what's loaded."""
        if self.audio_clip is None:
            return
        result_buffer, _removed_ranges = self._split_effect_result(result_buffer)
        i0, i1 = self._selection_bounds()
        composite = np.concatenate(
            [self.audio_clip.buffer[:i0], result_buffer, self.audio_clip.buffer[i1:]])
        self.audio_preview = composite  # waveform display always shows the full composite
        # Composite-space sample range the amber tint should cover — the
        # middle segment above, wherever it actually landed. Using the
        # composite's own coordinates (not the original selection's)
        # keeps this correct even for a length-changing effect (e.g.
        # Clean's pause removal), where result_buffer isn't the same
        # length as buffer[i0:i1] was.
        self._preview_range_samples = (i0, i0 + len(result_buffer))
        was_playing = self.audio_player.is_playing
        if self.audio_selection:
            self._preview_offset_s = self.audio_selection[0]
            self._play_until = self._preview_offset_s + (len(result_buffer) / self.audio_clip.sample_rate)
            self.audio_player.load_buffer(result_buffer, self.audio_clip.sample_rate)
        else:
            self._preview_offset_s = 0.0
            self._play_until = None
            self.audio_player.load_buffer(composite, self.audio_clip.sample_rate)
        if autoplay or was_playing:
            self.audio_player.play()
        self._redraw_waveform()

    @staticmethod
    def _split_effect_result(result):
        """An effect_fn (see Enhance/Clean's specs) normally returns just
        a buffer; a length-changing one that needs markers kept in sync
        (Clean's pause-shortening, via return_ranges=True) returns
        (buffer, removed_ranges_s) instead — this normalizes either shape
        to (buffer, removed_ranges_or_None)."""
        if isinstance(result, tuple):
            return result
        return result, None

    def _apply_audio_effect(self, result):
        """Commits the effect result (a buffer, or a (buffer,
        removed_ranges) pair — see _split_effect_result) by splicing it
        into audio_clip at the same bounds Preview used, so what you
        heard is what you get. removed_ranges, if present, keeps markers
        in sync with whatever the effect shortened away."""
        if self.audio_clip is None:
            return
        result_buffer, removed_ranges = self._split_effect_result(result)
        i0, i1 = self._selection_bounds()
        full = np.concatenate(
            [self.audio_clip.buffer[:i0], result_buffer, self.audio_clip.buffer[i1:]])
        if removed_ranges:
            offset = i0 / self.audio_clip.sample_rate
            self.audio_clip.apply_removing_ranges(
                full, [(offset + s, offset + e) for s, e in removed_ranges])
        else:
            self.audio_clip.apply(full)
        self._after_audio_edit()
        self._set_audio_edit_status("aedit_status_effect_applied")

    def _apply_auto_enhance(self):
        """Runs the fixed Auto Enhance sequence (audio_clean.auto_enhance)
        on the current selection — or the whole clip if nothing's
        selected, same as every other Enhance row — via the normal
        _apply_audio_effect splice, so this behaves exactly like any
        other row's Apply. Moves every slider it touched to match what
        was applied — leaving them at their old values would make the
        panel lie about what the waveform now reflects. For a custom
        sequence of your own choosing, see the Configurations button
        beside this one."""
        if self.audio_clip is None:
            return
        buffer, sample_rate = self._selected_buffer(), self.audio_clip.sample_rate

        def work():
            return audio_clean.auto_enhance(buffer, sample_rate)

        def done(result, error):
            if error is not None:  # already logged by _run_busy itself
                self._set_audio_edit_status("aedit_status_effect_failed")
                return
            self._apply_audio_effect(result)
            self._sync_effect_row_display(self.aenh_rows["high_pass"], audio_clean.AUTO_ENHANCE_HIGH_PASS_HZ)
            self._sync_effect_row_display(self.aenh_rows["compress"], audio_clean.AUTO_ENHANCE_COMPRESS_DB)
            self._sync_effect_row_display(self.aenh_rows["normalize"], audio_clean.AUTO_ENHANCE_NORMALIZE_DB)

        self._run_busy("aenh_busy_title", work, done)

    def _run_configuration(self, profile):
        """Runs a saved custom configuration (see audio_profiles.py) the
        same way _apply_auto_enhance runs the built-in one — on the
        current selection, or the whole clip if nothing's selected, via
        the normal _apply_audio_effect splice."""
        if self.audio_clip is None:
            return
        buffer, sample_rate = self._selected_buffer(), self.audio_clip.sample_rate

        def work():
            return audio_profiles.run_profile(buffer, sample_rate, profile, return_ranges=True)

        def done(result, error):
            if error is not None:  # already logged by _run_busy itself
                self._set_audio_edit_status("aedit_status_effect_failed")
                return
            result_buffer, removed_ranges, engine = result
            self._apply_audio_effect((result_buffer, removed_ranges) if removed_ranges else result_buffer)
            if engine is not None:
                self.aenh_denoise_engine_label.configure(
                    text=i18n.t(self.ui_lang, "aenh_denoise_engine", engine=engine))
            for step in profile["steps"]:
                if step["key"] in self.aenh_rows:
                    self._sync_effect_row_display(self.aenh_rows[step["key"]], step["value"])

        self._run_busy("aenh_busy_title", work, done)

    # -- Enhance configurations: user-saved step sequences, managed from ----
    # -- the "Configurations…" button beside Auto Enhance -------------------

    # (step key, i18n label key) — every value-bearing key here matches a
    # key in self.aenh_rows, so the builder dialog can prefill each
    # value entry from that slider's current position. "pause" is
    # deliberately last and handled separately wherever this is used —
    # see _open_config_builder_dialog's own comment on why.
    CONFIG_STEP_ORDER = (
        ("denoise", "aenh_denoise"),
        ("gate", "aclean_gate"),
        ("high_pass", "aenh_highpass"),
        ("low_pass", "aenh_lowpass"),
        ("compress", "aenh_compress"),
        ("amplify", "aenh_amplify"),
        ("normalize", "aenh_normalize"),
        ("loudness", "aenh_loudness"),
        ("eq", "aenh_eq"),
        ("clicks", "aclean_clicks"),
        ("pause", "aclean_pause"),
    )

    def _open_configs_manager_dialog(self):
        """The single entry point for everything to do with saved
        configurations: create, run, open for editing, rename, delete —
        a list of what's saved plus a "+ New" button, rather than a
        permanent row of controls sitting in the main panel for a
        feature most sessions won't touch."""
        if self.audio_clip is None:
            return
        t = lambda k, **kw: i18n.t(self.ui_lang, k, **kw)  # noqa: E731
        dlg = ctk.CTkToplevel(self)
        dlg.title(t("aenh_configs_manager_title"))
        dlg.resizable(False, False)
        dlg.transient(self)

        def close():
            dlg.grab_release()
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", close)

        ctk.CTkLabel(dlg, text=t("aenh_configs_manager_intro"), text_color=self.MUTED_TEXT,
                    wraplength=420, justify="left").grid(
            row=0, column=0, sticky="w", padx=16, pady=(16, 8))

        list_frame = ctk.CTkScrollableFrame(dlg, width=460, height=220, fg_color=("gray90", "gray17"))
        list_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        list_frame.grid_columnconfigure(0, weight=1)

        def render_list():
            for w in list_frame.winfo_children():
                w.destroy()
            if not self.aenh_configs:
                ctk.CTkLabel(list_frame, text=t("aenh_configs_none"), text_color=self.MUTED_TEXT).grid(
                    row=0, column=0, sticky="w", padx=8, pady=8)
                return
            for i, cfg in enumerate(self.aenh_configs):
                row = ctk.CTkFrame(list_frame, fg_color=("gray95", "gray24"))
                row.grid(row=i, column=0, sticky="ew", pady=2)
                row.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(row, text=cfg["name"], anchor="w").grid(
                    row=0, column=0, sticky="w", padx=(8, 8), pady=6)
                ctk.CTkButton(row, text=t("aenh_config_run"), width=56, height=24,
                             command=lambda c=cfg: run_config(c)).grid(row=0, column=1, padx=(0, 4), pady=6)
                ctk.CTkButton(row, text=t("aenh_config_open"), width=56, height=24, fg_color="transparent",
                             text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                             command=lambda c=cfg: open_for_edit(c)).grid(row=0, column=2, padx=(0, 4), pady=6)
                ctk.CTkButton(row, text=t("aenh_config_rename"), width=70, height=24, fg_color="transparent",
                             text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                             command=lambda c=cfg: rename_config(c)).grid(row=0, column=3, padx=(0, 4), pady=6)
                ctk.CTkButton(row, text=t("aenh_config_delete_button"), width=56, height=24,
                             fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                             command=lambda c=cfg: delete_config(c)).grid(row=0, column=4, padx=(0, 8), pady=6)

        def run_config(cfg):
            close()
            self._run_configuration(cfg)

        def open_for_edit(cfg):
            close()
            self._open_config_builder_dialog(existing=cfg)

        def rename_config(cfg):
            new_name = simpledialog.askstring(
                t("aenh_config_rename"), t("aenh_config_name_label"),
                initialvalue=cfg["name"], parent=dlg)
            if not new_name or not new_name.strip():
                return
            new_name = new_name.strip()
            if new_name == cfg["name"]:
                return
            if any(c["name"] == new_name for c in self.aenh_configs):
                messagebox.showerror(t("aenh_configs_manager_title"), t("aenh_config_name_taken"), parent=dlg)
                return
            cfg["name"] = new_name
            audio_profiles.save_profiles(self.aenh_configs)
            render_list()

        def delete_config(cfg):
            self.aenh_configs = [c for c in self.aenh_configs if c is not cfg]
            audio_profiles.save_profiles(self.aenh_configs)
            render_list()

        render_list()

        bottom_row = ctk.CTkFrame(dlg, fg_color="transparent")
        bottom_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        bottom_row.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(bottom_row, text=t("aenh_config_new_button"),
                     command=lambda: (close(), self._open_config_builder_dialog())).grid(row=0, column=0)
        ctk.CTkButton(bottom_row, text=t("aenh_config_close_button"), fg_color="transparent",
                     text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=close).grid(row=0, column=2)

        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.grab_set()

    def _open_config_builder_dialog(self, existing=None):
        """A modal for building (or, with existing given, editing a copy
        of) a named configuration: check which steps to include, set
        each one's value (prefilled from that slider's current position,
        or from `existing` if editing), and rank their order with ▲/▼ —
        order matters, it's the sequence this configuration runs in.

        "Shorten pauses" always sits last, below a separator, with no
        ▲/▼ of its own: it's the only step that changes the recording's
        *length* rather than just reshaping the sound in place, so it
        has to run after every other step regardless of where it'd
        otherwise rank — reordering the same-length steps around each
        other is always safe, but a pause anywhere except last would
        need remapping every step that comes after it."""
        if self.audio_clip is None:
            return
        t = lambda k, **kw: i18n.t(self.ui_lang, k, **kw)  # noqa: E731
        dlg = ctk.CTkToplevel(self)
        dlg.title(t("aenh_config_builder_title"))
        dlg.resizable(False, False)
        dlg.transient(self)

        def close():
            dlg.grab_release()
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", close)

        name_row = ctk.CTkFrame(dlg, fg_color="transparent")
        name_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        ctk.CTkLabel(name_row, text=t("aenh_config_name_label")).grid(row=0, column=0, padx=(0, 8))
        name_entry = ctk.CTkEntry(name_row, width=220)
        if existing is not None:
            name_entry.insert(0, existing["name"])
        name_entry.grid(row=0, column=1)

        rows_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        rows_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        builder_rows = []  # [{"key", "var", "value_entry", "frame"}, ...] — order here IS the rank (pause excluded)

        def regrid():
            for i, entry in enumerate(builder_rows):
                entry["frame"].grid(row=i, column=0, sticky="ew", pady=2)

        def move_entry(entry, delta):
            # Looked up by identity, not a captured index — the buttons
            # below are created once and must keep working after this
            # list gets reordered out from under their original position.
            idx = builder_rows.index(entry)
            j = idx + delta
            if 0 <= j < len(builder_rows):
                builder_rows[idx], builder_rows[j] = builder_rows[j], builder_rows[idx]
                regrid()

        existing_keys = [s["key"] for s in existing["steps"]] if existing else []
        existing_values = {s["key"]: s["value"] for s in existing["steps"]} if existing else {}
        # Editing an existing configuration shows its own steps first, in
        # their saved order (so the rank you already picked is exactly
        # what you see) and every other available step after, unchecked.
        ordered_keys = existing_keys + [k for k, _ in self.CONFIG_STEP_ORDER
                                        if k not in existing_keys and k != "pause"]
        label_lookup = dict(self.CONFIG_STEP_ORDER)

        # Column widths are fixed across every row (a value-less step like
        # "Noise reduction" still reserves the value column as blank
        # space) specifically so the ▲/▼ buttons land in the same
        # position on every row, checked or not, value or not.
        VALUE_COLUMN_WIDTH = 80

        for key in ordered_keys:
            row_frame = ctk.CTkFrame(rows_frame, fg_color=("gray95", "gray24"))
            var = ctk.BooleanVar(value=key in existing_keys)
            ctk.CTkCheckBox(row_frame, text=t(label_lookup[key]), variable=var, width=170).grid(
                row=0, column=0, padx=(8, 8), pady=6, sticky="w")
            value_entry = None
            if key in audio_profiles.STEP_KEYS_NO_VALUE:
                ctk.CTkFrame(row_frame, width=VALUE_COLUMN_WIDTH, height=1, fg_color="transparent").grid(
                    row=0, column=1, padx=(0, 8), pady=6)
            else:
                value_entry = ctk.CTkEntry(row_frame, width=VALUE_COLUMN_WIDTH)
                default_value = existing_values.get(
                    key, self.aenh_rows[key]["var"].get() if key in self.aenh_rows else 0.0)
                value_entry.insert(0, f"{default_value:g}")
                value_entry.grid(row=0, column=1, padx=(0, 8), pady=6)
            entry = {"key": key, "var": var, "value_entry": value_entry, "frame": row_frame}
            ctk.CTkButton(row_frame, text="▲", width=28, height=24,
                         command=lambda e=entry: move_entry(e, -1)).grid(row=0, column=2, padx=(0, 2), pady=6)
            ctk.CTkButton(row_frame, text="▼", width=28, height=24,
                         command=lambda e=entry: move_entry(e, 1)).grid(row=0, column=3, pady=6)
            builder_rows.append(entry)
        regrid()

        # "Shorten pauses" — always last, own row below a separator, no
        # rank buttons at all (see this method's docstring for why).
        next_row = len(builder_rows)
        ctk.CTkFrame(rows_frame, height=2, fg_color=("gray70", "gray35")).grid(
            row=next_row, column=0, sticky="ew", pady=(10, 8))
        pause_frame = ctk.CTkFrame(rows_frame, fg_color=("gray95", "gray24"))
        pause_frame.grid(row=next_row + 1, column=0, sticky="ew", pady=2)
        pause_var = ctk.BooleanVar(value="pause" in existing_keys)
        ctk.CTkCheckBox(pause_frame, text=t("aclean_pause"), variable=pause_var, width=170).grid(
            row=0, column=0, padx=(8, 8), pady=6, sticky="w")
        pause_value_entry = ctk.CTkEntry(pause_frame, width=VALUE_COLUMN_WIDTH)
        pause_default = existing_values.get(
            "pause", self.aenh_rows["pause"]["var"].get() if "pause" in self.aenh_rows else 1.5)
        pause_value_entry.insert(0, f"{pause_default:g}")
        pause_value_entry.grid(row=0, column=1, padx=(0, 8), pady=6)
        self._add_tooltip(pause_frame, lambda: t("aenh_config_pause_tip"))

        button_row = ctk.CTkFrame(dlg, fg_color="transparent")
        button_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        button_row.grid_columnconfigure(0, weight=1)
        status_label = ctk.CTkLabel(button_row, text="", text_color="#e05a5a", anchor="w")
        status_label.grid(row=0, column=0, sticky="w")

        def on_save():
            name = name_entry.get().strip()
            if not name:
                status_label.configure(text=t("aenh_config_name_required"))
                return
            if any(c["name"] == name and (existing is None or c is not existing) for c in self.aenh_configs):
                status_label.configure(text=t("aenh_config_name_taken"))
                return
            steps = []
            for entry in builder_rows:
                if not entry["var"].get():
                    continue
                value = None
                if entry["value_entry"] is not None:
                    try:
                        value = float(entry["value_entry"].get())
                    except ValueError:
                        status_label.configure(
                            text=t("aenh_config_bad_value", step=t(label_lookup[entry["key"]])))
                        return
                steps.append({"key": entry["key"], "value": value})
            if pause_var.get():
                try:
                    steps.append({"key": "pause", "value": float(pause_value_entry.get())})
                except ValueError:
                    status_label.configure(text=t("aenh_config_bad_value", step=t("aclean_pause")))
                    return
            if not steps:
                status_label.configure(text=t("aenh_config_no_steps"))
                return
            if existing is not None:
                self.aenh_configs = [c for c in self.aenh_configs if c is not existing]
            self.aenh_configs.append({"name": name, "steps": steps})
            audio_profiles.save_profiles(self.aenh_configs)
            close()

        ctk.CTkButton(button_row, text=t("aenh_config_save_button"), command=on_save).grid(
            row=0, column=1, padx=(8, 6))
        ctk.CTkButton(button_row, text=t("aenh_config_cancel_button"), fg_color="transparent",
                     text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=close).grid(
            row=0, column=2)

        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.grab_set()

    def _revert_effect_preview(self):
        """Discards the current Enhance/Clean preview without applying it
        — the waveform drops back to the real (unmodified) clip. Distinct
        from "Revert to original" in the save row below, which discards
        every already-*applied* edit back to the pristine recording;
        this only ever discards a not-yet-applied preview."""
        self._play_until = None
        self._clear_audio_preview()
        self._redraw_waveform()

    # ========================================= audio studio: enhance panel
    #
    # Built into the Edit subtab (a collapsible panel, see
    # _build_audio_edit_tab/_toggle_enhance_panel) rather than a separate
    # tab: these effects act on the waveform Edit is already showing, so
    # switching tabs to reach them would hide the very thing they're
    # changing. `parent` here is that panel's own CTkScrollableFrame.

    # Enhance's rows fall into a few genuinely different kinds of tool
    # (see the Loudness/tone vs. Noise/frequency vs. Repairs/timing
    # split) — each group collapsible under its own heading, so a long,
    # three-group panel stays scannable without scrolling past sliders
    # you're not touching right now. Group name doubles as the second
    # half of its i18n heading key ("tone" -> "aenh_heading_tone").
    ENH_GROUPS = ("tone", "noise", "repair")

    def _build_group_heading(self, parent, row, group):
        """A clickable subheading collapsing/expanding every widget later
        registered under `group` via self._enh_group_widgets[group]."""
        btn = ctk.CTkButton(
            parent, text="", anchor="w", fg_color="transparent",
            hover_color=("gray85", "gray25"), text_color=self.OUTLINE_BUTTON_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda g=group: self._toggle_enhance_group(g))
        btn.grid(row=row, column=0, sticky="ew", padx=6, pady=(14, 2))
        self._enh_group_headings[group] = btn
        return btn

    def _toggle_enhance_group(self, group):
        self._enh_group_collapsed[group] = not self._enh_group_collapsed[group]
        self._apply_enhance_group_visibility(group)

    def _apply_enhance_group_visibility(self, group):
        """Shows/hides every widget in the group per its collapsed state,
        and refreshes the heading's arrow+text — called both right after
        toggling and from _retranslate_audio_studio_tabs (language switch
        needs the heading text redrawn too; re-applying the same
        collapsed state to already-correct widgets is harmless)."""
        collapsed = self._enh_group_collapsed[group]
        for w in self._enh_group_widgets[group]:
            w.grid_remove() if collapsed else w.grid()
        arrow = "▸" if collapsed else "▾"
        text = i18n.t(self.ui_lang, f"aenh_heading_{group}")
        self._enh_group_headings[group].configure(text=f"{arrow} {text}")

    def _build_enhance_panel(self, parent):
        # Hint (left) and the one-click preset (right) share a row rather
        # than each getting their own — with several sliders below
        # already, every extra row here is vertical space the waveform
        # above loses.
        hint_row = ctk.CTkFrame(parent, fg_color="transparent")
        hint_row.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 10))
        hint_row.grid_columnconfigure(0, weight=1)
        # Doubles as the "open a file first" message and, once a clip is
        # loaded, the reminder that effects act on the current selection
        # (or the whole clip if nothing's selected) — see
        # _set_audio_enhance_controls_enabled.
        self.aenh_no_clip_label = ctk.CTkLabel(
            hint_row, text="", anchor="w", text_color=self.MUTED_TEXT, wraplength=700, justify="left")
        self.aenh_no_clip_label.grid(row=0, column=0, sticky="w")
        # One-click "make this whole recording good" pipeline (see
        # audio_clean.auto_enhance) — the Descript-style shortcut for
        # anyone who'd rather not reason about individual sliders. Every
        # slider below stays exactly as granular as before for when the
        # one-click result isn't quite right — a tooltip spells out what
        # it actually does and in what order, since the label alone
        # ("Auto Enhance") doesn't say.
        self.aenh_preset_button = ctk.CTkButton(
            hint_row, text="", height=28, command=self._apply_auto_enhance)
        self.aenh_preset_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._add_tooltip(self.aenh_preset_button, lambda: i18n.t(self.ui_lang, "aenh_preset_tip"))
        # A saved sequence of your own, alongside the fixed Auto Enhance
        # one — everything about that (create/run/open/rename/delete)
        # lives behind this one button instead of a permanent row of
        # controls most sessions will never touch (see
        # _open_configs_manager_dialog).
        self.aenh_configs_button = ctk.CTkButton(
            hint_row, text="", height=28, width=140, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=self._open_configs_manager_dialog)
        self.aenh_configs_button.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._add_tooltip(self.aenh_configs_button, lambda: i18n.t(self.ui_lang, "aenh_configs_button_tip"))

        # Rows split into two groups, each under its own subheading —
        # otherwise Amplify/EQ/Compressor and High-pass/Noise gate/Noise
        # reduction all read as one undifferentiated pile of "audio
        # knobs," which is exactly what made it hard to tell which one to
        # reach for. "Loudness & tone" never changes *what* noise is
        # present, only how the existing signal sounds; "Noise &
        # frequency" is specifically about reducing unwanted sound, each
        # row a different mechanism (see each one's own tooltip) — a
        # one-line pointer at the top of that group says which situation
        # each is actually for.
        #
        # (key, label_key, slider_from, slider_to, default, fmt, effect_fn)
        # Every effect_fn takes the SELECTED buffer (self._selected_buffer()
        # — the current waveform selection, or the whole clip if nothing's
        # selected), not self.audio_clip.buffer directly, so Preview/Apply
        # only ever touch what's actually highlighted.
        tone_specs = [
            ("amplify", "aenh_amplify", -24, 24, 0, "{:+.0f} dB", "aenh_amplify_tip",
             lambda g: audio_dsp.amplify(self._selected_buffer(), g)),
            ("normalize", "aenh_normalize", -12, 0, -3, "{:.0f} dB", "aenh_normalize_tip",
             lambda g: audio_dsp.normalize(self._selected_buffer(), g)),
            ("loudness", "aenh_loudness", -30, -6, -16, "{:.0f} LUFS", "aenh_loudness_tip",
             lambda t: audio_dsp.normalize_lufs(self._selected_buffer(), self.audio_clip.sample_rate, t)),
            ("eq", "aenh_eq", -12, 12, 0, "{:+.0f} dB", "aenh_eq_tip",
             lambda g: audio_dsp.eq_band(self._selected_buffer(), self.audio_clip.sample_rate, 1000.0, g)),
            ("compress", "aenh_compress", -40, 0, -20, "{:.0f} dB", "aenh_compress_tip",
             lambda t: audio_dsp.compressor(self._selected_buffer(), self.audio_clip.sample_rate,
                                            threshold_db=t)),
        ]
        noise_specs = [
            ("high_pass", "aenh_highpass", 20, 2000, 100, "{:.0f} Hz", "aenh_highpass_tip",
             lambda c: audio_dsp.high_pass(self._selected_buffer(), self.audio_clip.sample_rate, c)),
            ("low_pass", "aenh_lowpass", 2000, 20000, 8000, "{:.0f} Hz", "aenh_lowpass_tip",
             lambda c: audio_dsp.low_pass(self._selected_buffer(), self.audio_clip.sample_rate, c)),
            ("gate", "aclean_gate", -60, -10, -40, "{:.0f} dB", "aclean_gate_tip",
             lambda t: audio_dsp.noise_gate(self._selected_buffer(), self.audio_clip.sample_rate,
                                            threshold_db=t)),
        ]
        self.aenh_rows = {}
        self._enh_group_headings = {}
        self._enh_group_widgets = {g: [] for g in self.ENH_GROUPS}
        self._enh_group_collapsed = {g: False for g in self.ENH_GROUPS}
        row = 1  # row 0 = hint_row (built above)
        self._build_group_heading(parent, row, "tone")
        row += 1
        for key, label_key, lo, hi, default, fmt, tip_key, fn in tone_specs:
            self.aenh_rows[key] = self._build_effect_row(
                parent, row, label_key, lo, hi, default, fmt=fmt,
                on_change=lambda v, fn=fn: self._update_effect_preview(fn(v)),
                on_apply=lambda v, fn=fn: self._apply_audio_effect(fn(v)))
            self._add_tooltip(self.aenh_rows[key]["label"], lambda k=tip_key: i18n.t(self.ui_lang, k))
            self._enh_group_widgets["tone"].append(self.aenh_rows[key]["frame"])
            row += 1

        self._build_group_heading(parent, row, "noise")
        row += 1
        self.aenh_noise_guide_label = ctk.CTkLabel(
            parent, text="", anchor="w", text_color=self.MUTED_TEXT, wraplength=900, justify="left")
        self.aenh_noise_guide_label.grid(row=row, column=0, sticky="w", padx=10, pady=(0, 6))
        self._enh_group_widgets["noise"].append(self.aenh_noise_guide_label)
        row += 1
        for key, label_key, lo, hi, default, fmt, tip_key, fn in noise_specs:
            self.aenh_rows[key] = self._build_effect_row(
                parent, row, label_key, lo, hi, default, fmt=fmt,
                on_change=lambda v, fn=fn: self._update_effect_preview(fn(v)),
                on_apply=lambda v, fn=fn: self._apply_audio_effect(fn(v)))
            self._add_tooltip(self.aenh_rows[key]["label"], lambda k=tip_key: i18n.t(self.ui_lang, k))
            self._enh_group_widgets["noise"].append(self.aenh_rows[key]["frame"])
            row += 1

        # Noise reduction — no slider (the model has its own tuned
        # defaults), so it keeps its own explicit Preview button (unlike
        # the rows above, dragging isn't how you'd trigger this) — plus
        # Apply, and a note on which engine actually ran, since
        # DeepFilterNet and its noisereduce fallback differ noticeably in
        # quality.
        denoise_frame = ctk.CTkFrame(parent)
        denoise_frame.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
        denoise_frame.grid_columnconfigure(1, weight=1)
        self.aenh_denoise_label = ctk.CTkLabel(denoise_frame, text="", anchor="w", width=150)
        self.aenh_denoise_label.grid(row=0, column=0, padx=(10, 8), pady=10, sticky="w")
        self._add_tooltip(self.aenh_denoise_label, lambda: i18n.t(self.ui_lang, "aenh_denoise_tip"))
        self.aenh_denoise_engine_label = ctk.CTkLabel(
            denoise_frame, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.aenh_denoise_engine_label.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.aenh_denoise_preview_button = ctk.CTkButton(
            denoise_frame, text="", width=80, height=26, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=self._preview_denoise)
        self.aenh_denoise_preview_button.grid(row=0, column=3, padx=(0, 6), pady=10)
        self.aenh_denoise_revert_button = ctk.CTkButton(
            denoise_frame, text="", width=80, height=26, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=self._revert_effect_preview)
        self.aenh_denoise_revert_button.grid(row=0, column=4, padx=(0, 6), pady=10)
        self.aenh_denoise_apply_button = ctk.CTkButton(
            denoise_frame, text="", width=80, height=26, command=self._apply_denoise)
        self.aenh_denoise_apply_button.grid(row=0, column=5, padx=(0, 10), pady=10)
        self._enh_group_widgets["noise"].append(denoise_frame)
        row += 1

        # Noise profile — the classic Audacity "select noise, Get Noise
        # Profile, Apply" workflow: tunes denoising to this recording's
        # own specific noise, which often beats the blind model-based
        # denoise above on unusual/non-speech noise (a particular fridge
        # hum, a fan). Optional — with no profile captured, the buttons
        # above keep using blind DeepFilterNet/noisereduce as before.
        profile_frame = ctk.CTkFrame(parent, fg_color="transparent")
        profile_frame.grid(row=row, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.aenh_profile_label = ctk.CTkLabel(profile_frame, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.aenh_profile_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.aenh_get_profile_button = ctk.CTkButton(
            profile_frame, text="", width=190, height=26, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=self._capture_noise_profile)
        self.aenh_get_profile_button.grid(row=0, column=1, padx=(0, 6))
        self._add_tooltip(self.aenh_get_profile_button, lambda: i18n.t(self.ui_lang, "aenh_profile_tip"))
        self.aenh_clear_profile_button = ctk.CTkButton(
            profile_frame, text="", width=90, height=26, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=self._clear_noise_profile)
        self.aenh_clear_profile_button.grid(row=0, column=2)
        self._enh_group_widgets["noise"].append(profile_frame)
        row += 1

        # Third group: one-off repairs at a specific spot (a click *here*,
        # a pause *there*) rather than a rule applied uniformly throughout
        # — the one distinction among all these tools that actually holds
        # up (unlike "fixes a problem", which every row above could just
        # as easily claim: too quiet is a problem, hiss is a problem,
        # everything here is "fixing a problem" from the listener's
        # side — that's not a dividing line, it's just true of the whole
        # panel).
        self._build_group_heading(parent, row, "repair")
        row += 1
        repair_specs = [
            ("pause", "aclean_pause", 0.5, 5.0, 1.5, "{:.1f}s", "aclean_pause_tip",
             lambda mp: audio_clean.remove_long_pauses(
                 self._selected_buffer(), self.audio_clip.sample_rate, max_pause_s=mp,
                 return_ranges=True)),
            ("clicks", "aclean_clicks", 2, 12, 6, "{:.0f}σ", "aclean_clicks_tip",
             lambda th: audio_clean.remove_clicks(
                 self._selected_buffer(), self.audio_clip.sample_rate, threshold=th)),
        ]
        for key, label_key, lo, hi, default, fmt, tip_key, fn in repair_specs:
            self.aenh_rows[key] = self._build_effect_row(
                parent, row, label_key, lo, hi, default, fmt=fmt,
                on_change=lambda v, fn=fn: self._update_effect_preview(fn(v)),
                on_apply=lambda v, fn=fn: self._apply_audio_effect(fn(v)))
            if key == "pause":
                self._add_tooltip(self.aenh_rows[key]["label"], lambda k=tip_key: i18n.t(
                    self.ui_lang, k, keep=f"{audio_clean.DEFAULT_PAUSE_KEEP_S:g}"))
            else:
                self._add_tooltip(self.aenh_rows[key]["label"], lambda k=tip_key: i18n.t(self.ui_lang, k))
            self._enh_group_widgets["repair"].append(self.aenh_rows[key]["frame"])
            row += 1

    def _capture_noise_profile(self):
        if self.audio_clip is None or not self.audio_selection:
            self._set_audio_edit_status("aedit_status_need_selection")
            return
        self.audio_noise_profile = self._selected_buffer().copy()
        self._update_noise_profile_label()
        self.aenh_clear_profile_button.configure(state="normal")

    def _clear_noise_profile(self):
        self.audio_noise_profile = None
        self._update_noise_profile_label()
        self.aenh_clear_profile_button.configure(state="disabled")

    def _update_noise_profile_label(self):
        if self.audio_noise_profile is not None and self.audio_clip is not None:
            seconds = len(self.audio_noise_profile) / self.audio_clip.sample_rate
            text = i18n.t(self.ui_lang, "aenh_profile_active", seconds=f"{seconds:.1f}")
        else:
            text = i18n.t(self.ui_lang, "aenh_profile_none")
        self.aenh_profile_label.configure(text=text)

    def _set_audio_enhance_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.aenh_preset_button.configure(state=state)
        self.aenh_no_clip_label.configure(text=i18n.t(
            self.ui_lang, "aenh_selection_hint" if enabled else "aenh_no_clip"))
        self.aenh_configs_button.configure(state=state)
        for row in self.aenh_rows.values():
            row["slider"].configure(state=state)
            row["revert_btn"].configure(state=state)
            row["apply_btn"].configure(state=state)
        self.aenh_denoise_preview_button.configure(state=state)
        self.aenh_denoise_revert_button.configure(state=state)
        self.aenh_denoise_apply_button.configure(state=state)
        self.aenh_get_profile_button.configure(state=state)
        self.aenh_clear_profile_button.configure(
            state="normal" if enabled and self.audio_noise_profile is not None else "disabled")

    def _run_denoise_async(self, on_result):
        """Computes Enhance's Noise reduction row on a background thread
        behind the busy modal — DeepFilterNet especially can take a real
        moment — then calls on_result(result_buffer) on the main thread.
        Preview and Apply only differ in what they do with that result,
        so they both just supply a different on_result."""
        if self.audio_clip is None:
            return
        buffer = self._selected_buffer()
        sample_rate = self.audio_clip.sample_rate
        profile = self.audio_noise_profile

        def work():
            if profile is not None:
                return audio_denoise.denoise_with_profile(buffer, sample_rate, profile), "profile"
            return audio_denoise.denoise(buffer, sample_rate)

        def done(payload, error):
            if error is not None:  # already logged by _run_busy itself
                self._set_audio_edit_status("aedit_status_effect_failed")
                return
            result, engine = payload
            # "profile" is a plain sentinel from work() above, not yet
            # translated — i18n.t only happens here, on the main thread.
            engine_label = i18n.t(self.ui_lang, "aenh_profile_engine") if engine == "profile" else engine
            self.aenh_denoise_engine_label.configure(
                text=i18n.t(self.ui_lang, "aenh_denoise_engine", engine=engine_label))
            on_result(result)

        self._run_busy("aenh_busy_title_denoise", work, done)

    def _preview_denoise(self):
        self._run_denoise_async(lambda result: self._update_effect_preview(result, autoplay=True))

    def _apply_denoise(self):
        self._run_denoise_async(self._apply_audio_effect)

    def _detect_sections(self):
        """Auto-labeled markers ("Section 1", "Section 2", …) — one at
        the start of the recording's speech, and one after every real
        break (a pause of at least audio_clean.DEFAULT_SECTION_GAP_S)
        since speech last stopped. Reuses the same VAD detection
        audio_clean.detect_silences/remove_long_pauses already run, not a
        second pass — see audio_clean.speech_sections for why it merges
        across short pauses instead of marking every one. Replaces only
        the previous auto set; user-added markers are untouched (see
        AudioClip.replace_auto_markers). Runs on a background thread
        behind the busy modal — VAD inference on a long recording is
        real work, not instant."""
        if self.audio_clip is None:
            return
        buffer, sample_rate = self.audio_clip.buffer, self.audio_clip.sample_rate

        def work():
            return audio_clean.speech_sections(buffer, sample_rate)

        def done(starts, error):
            if error is not None:  # already logged by _run_busy itself
                self._set_audio_edit_status("aedit_status_sections_failed")
                return
            labels = [(start, i18n.t(self.ui_lang, "aedit_section_label", n=i + 1))
                     for i, start in enumerate(starts)]
            self.audio_clip.replace_auto_markers(labels)
            self._redraw_waveform()
            self._render_markers_panel()
            self._set_audio_edit_status("aedit_status_sections_found", {"count": len(labels)})

        self._run_busy("aenh_busy_title_sections", work, done)

    # -- user-added markers -------------------------------------------------

    def _add_marker_at_playhead(self):
        if self.audio_clip is None:
            return
        label = simpledialog.askstring(
            i18n.t(self.ui_lang, "aedit_marker_dialog_title"),
            i18n.t(self.ui_lang, "aedit_marker_dialog_prompt"),
            parent=self)
        if not label:
            return
        self.audio_clip.add_marker(self.audio_player.get_time(), label, auto=False)
        self._redraw_waveform()
        self._render_markers_panel()

    def _marker_lane_x_to_seconds(self, x):
        width = max(1, self.amarker_canvas.winfo_width())
        start, end = self.audio_zoom
        return start + max(0.0, min(1.0, x / width)) * (end - start)

    def _marker_near_x(self, x, tolerance_px=10):
        """Returns the index into audio_clip.markers whose flag is within
        tolerance_px of `x`, or None — used by every marker-lane
        interaction so they all agree on which marker was hit. Widened
        from the original 6px: the flags themselves are only ~7px wide,
        which made them easy to miss by a pixel or two in practice."""
        if self.audio_clip is None or not self.audio_clip.markers:
            return None
        width = max(1, self.amarker_canvas.winfo_width())
        start, end = self.audio_zoom
        if end <= start:
            return None
        best_index, best_dist = None, tolerance_px + 1
        for i, m in enumerate(self.audio_clip.markers):
            mx = (m["time"] - start) / (end - start) * width
            dist = abs(mx - x)
            if dist < best_dist:
                best_index, best_dist = i, dist
        return best_index

    def _on_marker_lane_click(self, event):
        index = self._marker_near_x(event.x)
        if index is None:
            return
        self._seek_audio_edit_to(self.audio_clip.markers[index]["time"])
        self._redraw_waveform()

    def _on_marker_lane_double_click(self, event):
        index = self._marker_near_x(event.x)
        if index is not None:
            self._rename_marker(index)

    def _on_marker_lane_right_click(self, event):
        """Right-clicking a marker used to delete it immediately, with no
        way to rename one at all — neither was discoverable. A small
        context menu makes both an explicit, visible choice instead."""
        index = self._marker_near_x(event.x)
        if index is None:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=i18n.t(self.ui_lang, "aedit_marker_rename"),
                         command=lambda: self._rename_marker(index))
        menu.add_command(label=i18n.t(self.ui_lang, "aedit_marker_delete"),
                         command=lambda: self._delete_marker(index))
        menu.tk_popup(event.x_root, event.y_root)

    def _rename_marker(self, index):
        if self.audio_clip is None or not (0 <= index < len(self.audio_clip.markers)):
            return
        current = self.audio_clip.markers[index]["label"]
        new_label = simpledialog.askstring(
            i18n.t(self.ui_lang, "aedit_marker_dialog_title"),
            i18n.t(self.ui_lang, "aedit_marker_dialog_prompt"),
            initialvalue=current, parent=self)
        if new_label:
            self.audio_clip.markers[index]["label"] = new_label
            self._redraw_waveform()
            self._render_markers_panel()

    def _delete_marker(self, index):
        if self.audio_clip is None:
            return
        self.audio_clip.remove_marker_at(index)
        self._redraw_waveform()
        self._render_markers_panel()

    def _redraw_marker_lane(self):
        canvas = self.amarker_canvas
        canvas.delete("all")
        canvas.configure(bg=self._resolve_root_bg())
        if self.audio_clip is None or not self.audio_clip.markers:
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        start, end = self.audio_zoom
        if end <= start:
            return
        color = self.EFFECT_PANEL_ACCENT
        for m in self.audio_clip.markers:
            if not (start <= m["time"] <= end):
                continue
            x = (m["time"] - start) / (end - start) * width
            canvas.create_polygon(x, 1, x + 7, 4, x, 8, fill=color, outline=color)
            canvas.create_line(x, 0, x, height, fill=color)
            canvas.create_text(x + 9, height / 2, anchor="w", text=m["label"],
                               fill=color, font=("Segoe UI", 8))

    # ===================================================== audio studio: find
    # similar segments — a third collapsible panel beside Enhance/Clean
    # (see _toggle_effects_panel). Select a snippet (e.g. one time a word
    # or phrase is said), search for every other place it recurs, review
    # each match (jump to it, listen), then remove whichever are checked
    # in a single batch.

    # Matches panel's display-filter presets — value -> minimum score to
    # show. The search itself already only returns >=80% (see
    # audio_find.DEFAULT_THRESHOLD), so "≥80%" here shows everything
    # found; the tighter presets just narrow which of those already-
    # found matches are worth reviewing, without re-running the search.
    FIND_FILTER_VALUES = {"≥80%": 0.80, "≥85%": 0.85, "≥90%": 0.90, "≥95%": 0.95}

    def _build_find_panel(self, parent):
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 8))
        top.grid_columnconfigure(0, weight=1)
        self.afind_hint_label = ctk.CTkLabel(
            top, text="", anchor="w", text_color=self.MUTED_TEXT, wraplength=700, justify="left")
        self.afind_hint_label.grid(row=0, column=0, sticky="w")
        self.afind_filter_menu = ctk.CTkOptionMenu(
            top, width=100, values=list(self.FIND_FILTER_VALUES.keys()),
            command=self._on_find_filter_change)
        self.afind_filter_menu.set("≥80%")
        self.afind_filter_menu.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._add_tooltip(self.afind_filter_menu, lambda: i18n.t(self.ui_lang, "afind_filter_tip"))

        self.afind_rows_frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self.afind_rows_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))
        self.afind_rows_frame.grid_columnconfigure(0, weight=1)

        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.afind_select_all_button = ctk.CTkButton(
            bottom, text="", width=90, height=30, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, state="disabled",
            command=lambda: self._set_all_match_checks(True))
        self.afind_select_all_button.grid(row=0, column=0, padx=(0, 6))
        self.afind_select_none_button = ctk.CTkButton(
            bottom, text="", width=90, height=30, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, state="disabled",
            command=lambda: self._set_all_match_checks(False))
        self.afind_select_none_button.grid(row=0, column=1, padx=(0, 14))
        self.afind_delete_button = ctk.CTkButton(
            bottom, text="", height=30, state="disabled", command=self._delete_checked_matches)
        self.afind_delete_button.grid(row=0, column=2, padx=(0, 8))

        # _find_matches: [(start_s, end_s, score), ...] from the last search.
        # _find_vars: one BooleanVar per match, parallel to _find_matches.
        self._clear_find_results()

    def _run_find_similar(self):
        """Runs the search on a background thread behind a modal "please
        wait" dialog: even after the find_similar_segments perf fix a
        long recording can take a noticeable moment, and the previous
        synchronous call froze the whole window for that whole moment
        with no way to tell it was still working, let alone stop it."""
        if self.audio_clip is None:
            return
        if not self.audio_selection:
            self._set_audio_edit_status("aedit_status_need_selection")
            return
        start, end = self.audio_selection
        buffer = self.audio_clip.buffer
        sample_rate = self.audio_clip.sample_rate
        cancel_event = threading.Event()
        self._find_cancel_event = cancel_event

        def progress_cb(frac, results_so_far):
            self.events.put(("find_similar", "progress", (frac, len(results_so_far))))

        def cancel_cb():
            return cancel_event.is_set()

        def work():
            try:
                results = audio_find.find_similar_segments(
                    buffer, sample_rate, start, end,
                    progress_cb=progress_cb, cancel_cb=cancel_cb)
                status = "cancelled" if cancel_event.is_set() else "done"
                self.events.put(("find_similar", status, results))
            except Exception:
                settings.log_exception("Audio Studio: find-similar failed:")
                self.events.put(("find_similar", "error", []))

        self._show_find_progress_dialog()
        threading.Thread(target=work, daemon=True).start()

    def _show_find_progress_dialog(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title(i18n.t(self.ui_lang, "afind_searching_title"))
        dlg.resizable(False, False)
        dlg.transient(self)
        # No title-bar close — Cancel (below) is the only way out, so a
        # stray Alt-F4/Escape can't leave the search running with the
        # rest of the app silently un-grabbed.
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)
        self._find_progress_label = ctk.CTkLabel(
            dlg, text=i18n.t(self.ui_lang, "afind_searching_status", pct=0, count=0))
        self._find_progress_label.pack(padx=24, pady=(22, 10))
        self._find_progress_bar = ctk.CTkProgressBar(dlg, width=300)
        self._find_progress_bar.set(0)
        self._find_progress_bar.pack(padx=24, pady=(0, 16))
        self._find_progress_cancel_btn = ctk.CTkButton(
            dlg, text=i18n.t(self.ui_lang, "afind_cancel_search"),
            command=self._cancel_find_similar)
        self._find_progress_cancel_btn.pack(pady=(0, 18))
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        # Modal: grabs all input so the rest of the app can't be clicked
        # while the search is running, per the user's explicit ask.
        dlg.grab_set()
        self._find_progress_dialog = dlg

    def _update_find_progress_dialog(self, frac, count):
        if self._find_progress_dialog is None:
            return
        self._find_progress_bar.set(frac)
        self._find_progress_label.configure(
            text=i18n.t(self.ui_lang, "afind_searching_status", pct=int(frac * 100), count=count))

    def _cancel_find_similar(self):
        if self._find_cancel_event is not None:
            self._find_cancel_event.set()
        if self._find_progress_cancel_btn is not None:
            self._find_progress_cancel_btn.configure(
                state="disabled", text=i18n.t(self.ui_lang, "afind_cancelling"))

    def _close_find_progress_dialog(self):
        if self._find_progress_dialog is not None:
            self._find_progress_dialog.grab_release()
            self._find_progress_dialog.destroy()
        self._find_progress_dialog = None
        self._find_progress_label = None
        self._find_progress_bar = None
        self._find_progress_cancel_btn = None
        self._find_cancel_event = None

    def _on_find_similar_event(self, status, payload):
        if status == "progress":
            frac, count = payload
            self._update_find_progress_dialog(frac, count)
            return
        self._close_find_progress_dialog()
        self._find_matches = payload  # whatever was found so far, for "cancelled" and "error" too
        self._find_has_searched = True
        self.active_effects_panel = "find"
        self._show_active_effects_panel()
        self._render_find_results()
        # The Matches panel becoming visible for the very first time
        # resizes the waveform canvas (it now shares vertical space with
        # the panel below it) — Tk only recomputes that geometry on its
        # next idle pass, so without forcing it here _redraw_waveform
        # below would read the canvas's still-stale (taller, pre-panel)
        # size just this once and draw at the wrong scale, correcting
        # itself only on the next unrelated redraw (found via a real
        # first-search-looks-wrong-then-fixes-itself report).
        self.awave_canvas.update_idletasks()
        self._redraw_waveform()  # without this, the green overlay kept showing the PREVIOUS search's matches

    def _on_find_filter_change(self, value):
        self._find_min_score = self.FIND_FILTER_VALUES.get(value, 0.80)
        self._render_find_results()
        self._redraw_waveform()  # checked-match overlay needs to drop whatever just got filtered out

    def _render_find_results(self):
        for w in self.afind_rows_frame.winfo_children():
            w.destroy()
        self._find_vars = []
        self._find_visible_matches = [m for m in self._find_matches if m[2] >= self._find_min_score]
        if not self._find_matches:
            key = "afind_none_found" if self._find_has_searched else "afind_hint"
            self.afind_hint_label.configure(text=i18n.t(self.ui_lang, key))
            self.afind_delete_button.configure(state="disabled")
            self.afind_select_all_button.configure(state="disabled")
            self.afind_select_none_button.configure(state="disabled")
            self._update_rows_scrollbar(self.afind_rows_frame)
            return
        if not self._find_visible_matches:
            self.afind_hint_label.configure(text=i18n.t(
                self.ui_lang, "afind_none_at_filter", count=len(self._find_matches)))
            self.afind_delete_button.configure(state="disabled")
            self.afind_select_all_button.configure(state="disabled")
            self.afind_select_none_button.configure(state="disabled")
            self._update_rows_scrollbar(self.afind_rows_frame)
            return
        self.afind_hint_label.configure(
            text=i18n.t(self.ui_lang, "afind_found", count=len(self._find_visible_matches)))
        for i, (start, end, score) in enumerate(self._find_visible_matches):
            var = ctk.BooleanVar(value=True)
            self._find_vars.append(var)
            row = ctk.CTkFrame(self.afind_rows_frame, fg_color=("gray95", "gray24"))
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkCheckBox(row, text="", variable=var, width=20,
                           command=self._redraw_waveform).grid(
                row=0, column=0, padx=(8, 6), pady=6)
            ctk.CTkLabel(
                row, anchor="w",
                text=f"{_fmt_time(start)} – {_fmt_time(end)}   ({score * 100:.0f}%)",
            ).grid(row=0, column=1, sticky="ew", pady=6)
            play_btn = ctk.CTkButton(
                row, text=i18n.t(self.ui_lang, "afind_play"), width=60, height=24,
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                command=lambda s=start, e=end: self._play_match(s, e))
            play_btn.grid(row=0, column=2, padx=(6, 0), pady=6)
            jump_btn = ctk.CTkButton(
                row, text=i18n.t(self.ui_lang, "afind_jump"), width=70, height=24,
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                command=lambda s=start, e=end: self._jump_to_match(s, e))
            jump_btn.grid(row=0, column=3, padx=(6, 8), pady=6)
        self.afind_delete_button.configure(state="normal")
        self.afind_select_all_button.configure(state="normal")
        self.afind_select_none_button.configure(state="normal")
        self._update_rows_scrollbar(self.afind_rows_frame)

    def _update_rows_scrollbar(self, frame):
        """Hides a Matches/No speech/Markers rows panel's scrollbar when
        everything already fits, showing it only once content actually
        overflows the fixed-height area — CTkScrollableFrame has no such
        behavior built in (its scrollbar is always gridded, a thin full-
        length thumb even with one row), so this reads the canvas's
        actual content height against its visible height after every
        render and toggles it by hand."""
        frame.update_idletasks()
        canvas = frame._parent_canvas
        bbox = canvas.bbox("all")
        content_h = (bbox[3] - bbox[1]) if bbox else 0
        if content_h > canvas.winfo_height():
            frame._scrollbar.grid()
        else:
            frame._scrollbar.grid_remove()

    def _pan_into_view(self, t):
        """Shifts the zoom window just enough to bring time `t` into view,
        keeping the current zoom *width* unchanged — Jump should move the
        view, not change how zoomed-in it is."""
        if self.audio_clip is None:
            return
        start, end = self.audio_zoom
        width = end - start
        if start <= t <= end:
            return
        full = self.audio_clip.duration
        new_start = max(0.0, min(full - width, t - width / 2))
        self.audio_zoom = (new_start, min(full, new_start + width))

    def _jump_to_match(self, start, end):
        """Selects and seeks to a match without zooming in — Play (beside
        this button in the row) is the one that actually starts audio."""
        if self.audio_clip is None:
            return
        self.audio_selection = (start, end)
        self._pan_into_view(start)
        self._seek_audio_edit_to(start)
        self._redraw_waveform()

    def _play_match(self, start, end):
        """Jumps to the match (same as _jump_to_match) and immediately
        plays just that span — unlike the transport Play button, this
        always starts playback from the beginning of the match rather
        than toggling pause if something else was already playing."""
        if self.audio_clip is None:
            return
        self.audio_player.pause()
        self._jump_to_match(start, end)
        self._play_until = end
        self.audio_player.play()
        self._render_audio_edit_play_button()

    def _set_all_match_checks(self, checked):
        for var in self._find_vars:
            var.set(checked)
        self._redraw_waveform()

    def _delete_checked_matches(self):
        if self.audio_clip is None:
            return
        ranges = [m[:2] for m, v in zip(self._find_visible_matches, self._find_vars) if v.get()]
        if not ranges:
            return
        self.audio_clip.remove_ranges(ranges)
        self._after_audio_edit()  # also clears _find_matches — the positions just shifted
        self._zoom_audio_edit_fit()
        self._set_audio_edit_status("aedit_status_removed_matches", {"count": len(ranges)})

    # ==================================================== audio studio: markers
    # panel — a fourth collapsible panel beside Enhance/Clean/Matches,
    # listing every marker (both auto-detected sections and user-added
    # ones) with the same review-then-act shape as Find Similar: check
    # the ones you want, Play/Jump/Rename any one, or delete a batch.

    def _build_markers_panel(self, parent):
        # "Detect sections" lives here (not in Clean, where it used to
        # be) because what it actually does is add markers — it just
        # happens to reuse Clean's own VAD detection to decide where.
        # Same row as the hint label (far right of it), not its own row —
        # the hint text already has the height, so a whole extra row just
        # for one button would be pure wasted space.
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 8))
        top.grid_columnconfigure(0, weight=1)
        self.amark_hint_label = ctk.CTkLabel(
            top, text="", anchor="w", text_color=self.MUTED_TEXT, wraplength=700, justify="left")
        self.amark_hint_label.grid(row=0, column=0, sticky="w")
        self.amark_sections_button = ctk.CTkButton(
            top, text="", width=160, height=28, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, command=self._detect_sections)
        self.amark_sections_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._add_tooltip(self.amark_sections_button, lambda: i18n.t(
            self.ui_lang, "aclean_sections_tip", min_gap=audio_clean.DEFAULT_SECTION_GAP_S))

        self.amark_rows_frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self.amark_rows_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))
        self.amark_rows_frame.grid_columnconfigure(0, weight=1)

        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.amark_select_all_button = ctk.CTkButton(
            bottom, text="", width=90, height=30, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, state="disabled",
            command=lambda: self._set_all_marker_checks(True))
        self.amark_select_all_button.grid(row=0, column=0, padx=(0, 6))
        self.amark_select_none_button = ctk.CTkButton(
            bottom, text="", width=90, height=30, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, state="disabled",
            command=lambda: self._set_all_marker_checks(False))
        self.amark_select_none_button.grid(row=0, column=1, padx=(0, 14))
        self.amark_delete_button = ctk.CTkButton(
            bottom, text="", height=30, state="disabled", command=self._delete_checked_markers)
        self.amark_delete_button.grid(row=0, column=2, padx=(0, 8))

        self._marker_vars = []  # one BooleanVar per audio_clip.markers entry, parallel to it
        self._render_markers_panel()

    def _render_markers_panel(self):
        for w in self.amark_rows_frame.winfo_children():
            w.destroy()
        self._marker_vars = []
        markers = self.audio_clip.markers if self.audio_clip is not None else []
        if not markers:
            self.amark_hint_label.configure(text=i18n.t(self.ui_lang, "amark_none"))
            for btn in (self.amark_select_all_button, self.amark_select_none_button,
                       self.amark_delete_button):
                btn.configure(state="disabled")
            self._update_rows_scrollbar(self.amark_rows_frame)
            return
        self.amark_hint_label.configure(text=i18n.t(self.ui_lang, "amark_found", count=len(markers)))
        for i, m in enumerate(markers):
            var = ctk.BooleanVar(value=False)
            self._marker_vars.append(var)
            row = ctk.CTkFrame(self.amark_rows_frame, fg_color=("gray95", "gray24"))
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkCheckBox(row, text="", variable=var, width=20).grid(
                row=0, column=0, padx=(8, 6), pady=6)
            ctk.CTkLabel(row, anchor="w", text=f"{_fmt_time(m['time'])}   {m['label']}").grid(
                row=0, column=1, sticky="ew", pady=6)
            ctk.CTkButton(
                row, text=i18n.t(self.ui_lang, "afind_play"), width=56, height=24,
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                command=lambda idx=i: self._play_marker(idx),
            ).grid(row=0, column=2, padx=(6, 0), pady=6)
            ctk.CTkButton(
                row, text=i18n.t(self.ui_lang, "afind_jump"), width=56, height=24,
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                command=lambda idx=i: self._jump_to_marker(idx),
            ).grid(row=0, column=3, padx=(6, 0), pady=6)
            ctk.CTkButton(
                row, text=i18n.t(self.ui_lang, "aedit_marker_rename"), width=70, height=24,
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                command=lambda idx=i: self._rename_marker(idx),
            ).grid(row=0, column=4, padx=(6, 8), pady=6)
        for btn in (self.amark_select_all_button, self.amark_select_none_button, self.amark_delete_button):
            btn.configure(state="normal")
        self._update_rows_scrollbar(self.amark_rows_frame)

    def _set_all_marker_checks(self, checked):
        for var in self._marker_vars:
            var.set(checked)

    def _delete_checked_markers(self):
        if self.audio_clip is None:
            return
        indices = [i for i, v in enumerate(self._marker_vars) if v.get()]
        for i in sorted(indices, reverse=True):  # delete from the end so earlier indices stay valid
            self.audio_clip.remove_marker_at(i)
        self._render_markers_panel()
        self._redraw_waveform()

    def _jump_to_marker(self, index):
        if self.audio_clip is None or not (0 <= index < len(self.audio_clip.markers)):
            return
        t = self.audio_clip.markers[index]["time"]
        self.audio_selection = None
        self._pan_into_view(t)
        self._seek_audio_edit_to(t)
        self._redraw_waveform()

    def _play_marker(self, index):
        """Plays onward from the marker — markers are points, not
        ranges, so unlike _play_match there's no natural end to stop at."""
        if self.audio_clip is None or not (0 <= index < len(self.audio_clip.markers)):
            return
        self.audio_player.pause()
        self._jump_to_marker(index)
        self._play_until = None
        self.audio_player.play()
        self._render_audio_edit_play_button()

    def _clear_find_results(self):
        self._find_matches = []
        self._find_visible_matches = []
        self._find_vars = []
        self._find_has_searched = False
        if hasattr(self, "afind_hint_label"):  # not yet built during initial clip-less state
            self._render_find_results()

    # ==================================================== audio studio: no-speech
    # panel (internal name still "silence" — see audio_clean.detect_silences
    # for why the user-facing label is "No speech" instead) — beside
    # Matches, listing every stretch with no detected speech with the same
    # review-then-act shape: check the ones to remove, or Play/Jump any
    # one. "Detect no-speech" in the toolbar (beside Find similar) is what
    # populates this list — it used to just print a one-line count/
    # duration summary in the Clean subtab, which told you a number but
    # gave nothing to actually act on.

    def _build_silence_panel(self, parent):
        self.asilence_hint_label = ctk.CTkLabel(
            parent, text="", anchor="w", text_color=self.MUTED_TEXT, wraplength=900, justify="left")
        self.asilence_hint_label.grid(row=0, column=0, sticky="w", padx=8, pady=(4, 8))

        self.asilence_rows_frame = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self.asilence_rows_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))
        self.asilence_rows_frame.grid_columnconfigure(0, weight=1)

        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.asilence_select_all_button = ctk.CTkButton(
            bottom, text="", width=90, height=30, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, state="disabled",
            command=lambda: self._set_all_silence_checks(True))
        self.asilence_select_all_button.grid(row=0, column=0, padx=(0, 6))
        self.asilence_select_none_button = ctk.CTkButton(
            bottom, text="", width=90, height=30, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1, state="disabled",
            command=lambda: self._set_all_silence_checks(False))
        self.asilence_select_none_button.grid(row=0, column=1, padx=(0, 14))
        self.asilence_delete_button = ctk.CTkButton(
            bottom, text="", height=30, state="disabled", command=self._delete_checked_silences)
        self.asilence_delete_button.grid(row=0, column=2, padx=(0, 8))

        # _silence_spans: [(start_s, end_s), ...] from the last detection.
        # _silence_vars: one BooleanVar per span, parallel to it.
        self._clear_silence_results()

    def _detect_silences(self):
        """Runs VAD-based silence detection and shows the results in the
        Silence panel — every gap between detected speech, reviewable and
        deletable one by one or in bulk, the same shape as Find Similar's
        Matches panel. Runs on a background thread behind the busy modal,
        same reasoning as Detect sections — VAD over a whole recording
        isn't instant."""
        if self.audio_clip is None:
            return
        buffer, sample_rate = self.audio_clip.buffer, self.audio_clip.sample_rate

        def work():
            return audio_clean.detect_silences(buffer, sample_rate)

        def done(spans, error):
            self._silence_spans = [] if error is not None else spans  # error already logged by _run_busy
            self._silence_has_searched = True
            self.active_effects_panel = "silence"
            self._show_active_effects_panel()
            self._render_silence_results()
            self.awave_canvas.update_idletasks()  # see the matching comment in _on_find_similar_event
            self._redraw_waveform()

        self._run_busy("aenh_busy_title_silences", work, done)

    def _render_silence_results(self):
        for w in self.asilence_rows_frame.winfo_children():
            w.destroy()
        self._silence_vars = []
        if not self._silence_spans:
            key = "asilence_none_found" if self._silence_has_searched else "asilence_hint"
            self.asilence_hint_label.configure(text=i18n.t(self.ui_lang, key))
            self.asilence_delete_button.configure(state="disabled")
            self.asilence_select_all_button.configure(state="disabled")
            self.asilence_select_none_button.configure(state="disabled")
            self._update_rows_scrollbar(self.asilence_rows_frame)
            return
        total = sum(e - s for s, e in self._silence_spans)
        self.asilence_hint_label.configure(text=i18n.t(
            self.ui_lang, "asilence_found", count=len(self._silence_spans), seconds=f"{total:.1f}"))
        for i, (start, end) in enumerate(self._silence_spans):
            var = ctk.BooleanVar(value=False)
            self._silence_vars.append(var)
            row = ctk.CTkFrame(self.asilence_rows_frame, fg_color=("gray95", "gray24"))
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkCheckBox(row, text="", variable=var, width=20,
                           command=self._redraw_waveform).grid(
                row=0, column=0, padx=(8, 6), pady=6)
            ctk.CTkLabel(
                row, anchor="w",
                text=f"{_fmt_time(start)} – {_fmt_time(end)}   ({end - start:.1f}s)",
            ).grid(row=0, column=1, sticky="ew", pady=6)
            play_btn = ctk.CTkButton(
                row, text=i18n.t(self.ui_lang, "afind_play"), width=60, height=24,
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                command=lambda s=start, e=end: self._play_match(s, e))
            play_btn.grid(row=0, column=2, padx=(6, 0), pady=6)
            jump_btn = ctk.CTkButton(
                row, text=i18n.t(self.ui_lang, "afind_jump"), width=70, height=24,
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
                command=lambda s=start, e=end: self._jump_to_match(s, e))
            jump_btn.grid(row=0, column=3, padx=(6, 8), pady=6)
        self.asilence_delete_button.configure(state="normal")
        self.asilence_select_all_button.configure(state="normal")
        self.asilence_select_none_button.configure(state="normal")
        self._update_rows_scrollbar(self.asilence_rows_frame)

    def _set_all_silence_checks(self, checked):
        for var in self._silence_vars:
            var.set(checked)
        self._redraw_waveform()

    def _delete_checked_silences(self):
        if self.audio_clip is None:
            return
        ranges = [s for s, v in zip(self._silence_spans, self._silence_vars) if v.get()]
        if not ranges:
            return
        self.audio_clip.remove_ranges(ranges)
        self._after_audio_edit()  # also clears _silence_spans — the positions just shifted
        self._zoom_audio_edit_fit()
        self._set_audio_edit_status("aedit_status_removed_matches", {"count": len(ranges)})

    def _clear_silence_results(self):
        self._silence_spans = []
        self._silence_vars = []
        self._silence_has_searched = False
        if hasattr(self, "asilence_hint_label"):  # not yet built during initial clip-less state
            self._render_silence_results()

    # -- Audio Studio: translation -----------------------------------------

    def _retranslate_audio_studio_tabs(self):
        t = lambda key, **kw: i18n.t(self.ui_lang, key, **kw)  # noqa: E731

        self.arec_mic_label.configure(text=t("arec_mic_label"))
        self.arec_rate_label.configure(text=t("arec_rate_label"))
        self.arec_channels_label.configure(text=t("arec_channels_label"))
        self.arec_level_label.configure(text=t("arec_level_label"))
        self.arec_filename_label.configure(text=t("arec_filename_label"))
        self.arec_filename_entry.configure(placeholder_text=t("live_filename_placeholder"))
        self.arec_start_button.configure(text=t("arec_start_button"))
        self.arec_pause_button.configure(text=t(
            "arec_resume" if (self.audio_recorder and self.audio_recorder.is_paused) else "arec_pause"))
        self.arec_stop_button.configure(text=t("arec_stop_button"))
        self.arec_edit_button.configure(text=t("arec_edit_button"))
        self.arec_open_folder_button.configure(text=t("open_output_folder"))
        self._refresh_audio_record_mic_menu()
        if self.audio_record_status_key:
            self.arec_status_line.configure(
                text=t(self.audio_record_status_key, **(self.audio_record_status_detail or {})))

        self.aedit_open_button.configure(text=t("aedit_open_button"))
        if self.audio_clip is None:
            self.aedit_file_label.configure(text=t("aedit_no_file"))
        self._render_audio_edit_play_button()
        self.aedit_stop_button.configure(text=t("player_stop"))
        self.aedit_zoom_fit_button.configure(text=t("aedit_zoom_fit"))
        self.aedit_cut_button.configure(text=t("aedit_cut_button"))
        self.aedit_copy_button.configure(text=t("aedit_copy_button"))
        self.aedit_paste_button.configure(text=t("aedit_paste_button"))
        self.aedit_trim_button.configure(text=t("aedit_trim_button"))
        self.aedit_split_button.configure(text=t("aedit_split_button"))
        self.aedit_silence_button.configure(text=t("aedit_silence_button"))
        self.aedit_undo_button.configure(text=t("aedit_undo_button"))
        self.aedit_redo_button.configure(text=t("aedit_redo_button"))
        self.aedit_find_button.configure(text=t("aedit_find_button"))
        self.aedit_detect_silence_button.configure(text=t("aedit_detect_silence_button"))
        self.aedit_marker_button.configure(text=t("aedit_marker_button"))
        self.aedit_save_wav_button.configure(text=t("aedit_save_wav_button"))
        self.aedit_save_mp3_button.configure(text=t("aedit_save_mp3_button"))
        self.aedit_revert_button.configure(text=t("aedit_revert_button"))
        self.aedit_open_folder_button.configure(text=t("open_output_folder"))
        self._render_effects_toggle_buttons()
        view_values = [t("aedit_view_waveform"), t("aedit_view_spectrogram")]
        self.aedit_view_toggle.configure(values=view_values)
        self.aedit_view_toggle.set(
            view_values[1] if self.wave_view_mode == "spectrogram" else view_values[0])
        # CTkSegmentedButton.configure(values=...) destroys and recreates
        # its internal segment buttons every time — even when the values
        # are unchanged — which silently orphaned any tooltip bound
        # before the first call (i.e. always, since _retranslate() runs
        # once during __init__ right after the tooltip was attached).
        # Re-bind after every reconfigure rather than only once at build.
        self._add_tooltip_to_segmented(
            self.aedit_view_toggle, lambda: i18n.t(self.ui_lang, "aedit_tip_view_toggle"))
        self._update_selection_label()
        self._update_dirty_indicator()
        if self.audio_status_key:
            self.aedit_status_line.configure(
                text=t(self.audio_status_key, **(self.audio_status_detail or {})))

        self.aenh_preset_button.configure(text=t("aenh_preset_button"))
        self.aenh_no_clip_label.configure(text=t(
            "aenh_selection_hint" if self.audio_clip else "aenh_no_clip"))
        self.aenh_configs_button.configure(text=t("aenh_configs_button"))
        self.aenh_noise_guide_label.configure(text=t("aenh_noise_guide"))
        for group in self.ENH_GROUPS:
            self._apply_enhance_group_visibility(group)  # also redraws the heading's text in the new language
        for row in self.aenh_rows.values():
            row["label"].configure(text=t(row["label_key"]))
            row["revert_btn"].configure(text=t("aenh_revert"))
            row["apply_btn"].configure(text=t("aenh_apply"))
        self.aenh_denoise_label.configure(text=t("aenh_denoise"))
        self.aenh_denoise_preview_button.configure(text=t("aenh_preview"))
        self.aenh_denoise_revert_button.configure(text=t("aenh_revert"))
        self.aenh_denoise_apply_button.configure(text=t("aenh_apply"))
        self.aenh_get_profile_button.configure(text=t("aenh_get_profile_button"))
        self.aenh_clear_profile_button.configure(text=t("aenh_clear_profile_button"))
        self._update_noise_profile_label()

        self.amark_sections_button.configure(text=t("aclean_sections_button"))

        self.afind_select_all_button.configure(text=t("afind_select_all"))
        self.afind_select_none_button.configure(text=t("afind_select_none"))
        self.afind_delete_button.configure(text=t("afind_delete_button"))
        self._render_find_results()  # re-renders the hint text and any match rows/Jump buttons

        self.asilence_select_all_button.configure(text=t("afind_select_all"))
        self.asilence_select_none_button.configure(text=t("afind_select_none"))
        self.asilence_delete_button.configure(text=t("afind_delete_button"))
        self._render_silence_results()  # re-renders the hint text and any silence rows/buttons

        self.amark_select_all_button.configure(text=t("afind_select_all"))
        self.amark_select_none_button.configure(text=t("afind_select_none"))
        self.amark_delete_button.configure(text=t("afind_delete_button"))
        self._render_markers_panel()  # re-renders the hint text and any marker rows/buttons

        has_clip = self.audio_clip is not None
        self._set_audio_edit_controls_enabled(has_clip)
        self._set_audio_enhance_controls_enabled(has_clip)
        self._redraw_waveform()  # refreshes the spectrogram caption's language too

    # ------------------------------------------------------ transcribe tab

    def _build_transcribe_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        options = ctk.CTkFrame(parent)
        options.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 6))

        self.quality_label = ctk.CTkLabel(options, text="")
        self.quality_label.grid(row=0, column=0, padx=(12, 6), pady=10)
        self.quality_button = ctk.CTkSegmentedButton(options, command=self._on_pref_change)
        self.quality_button.grid(row=0, column=1, padx=(0, 16), pady=10)

        self.language_label = ctk.CTkLabel(options, text="")
        self.language_label.grid(row=0, column=2, padx=(0, 6), pady=10)
        self.language_menu = ctk.CTkOptionMenu(
            options, width=130, command=self._on_pref_change,
        )
        self.language_menu.grid(row=0, column=3, padx=(0, 16), pady=10)

        # Checkbox + info icon share one sub-frame (rather than the icon
        # sitting in its own column of `options`) so the icon lands right
        # next to the checkbox's own text — a column further along in
        # `options` starts wherever the widest OTHER row's content in that
        # column ends, not wherever this checkbox's text happens to end,
        # which put the icon far off to the right of "Korean" in practice.
        sv_row = ctk.CTkFrame(options, fg_color="transparent")
        sv_row.grid(row=1, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 12))

        self.sensevoice_var = ctk.BooleanVar(value=False)
        # The full language-name label this checkbox needs is too long to
        # share a row with the pickers at the window's default (or
        # minimum) width — hence its own row. The English text also has an
        # explicit line break (i18n.py) so it never relies on the window
        # being wide enough to avoid overflow.
        self.sensevoice_box = ctk.CTkCheckBox(
            sv_row, text="", variable=self.sensevoice_var, command=self._on_pref_change,
        )
        self.sensevoice_box.grid(row=0, column=0, sticky="w")

        # Plain-language explainer for the Whisper/SenseVoice choice this
        # row makes — a messagebox rather than a new dialog widget, same
        # pattern already used everywhere else in the app.
        self.model_info_button = ctk.CTkButton(
            sv_row, text="ⓘ", width=26, height=26, corner_radius=13,
            fg_color="transparent", text_color=self.MUTED_TEXT,
            hover_color=("gray80", "gray25"), command=self._show_model_info)
        self.model_info_button.grid(row=0, column=1, sticky="w", padx=(10, 0))

        # drop zone
        self.drop_zone = ctk.CTkFrame(parent, height=92, border_width=2, corner_radius=10)
        self.drop_zone.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        self.drop_zone.grid_propagate(False)
        self.drop_zone.grid_columnconfigure(0, weight=1)
        self.drop_zone.grid_rowconfigure((0, 1), weight=1)
        self.drop_title = ctk.CTkLabel(
            self.drop_zone, text="", font=ctk.CTkFont(size=15, weight="bold"))
        self.drop_title.grid(row=0, column=0, sticky="s")
        self.drop_sub = ctk.CTkLabel(self.drop_zone, text="", text_color=self.MUTED_TEXT)
        self.drop_sub.grid(row=1, column=0, sticky="n")
        for widget in (self.drop_zone, self.drop_title, self.drop_sub):
            widget.bind("<Button-1>", lambda _e: self._browse_files())
            widget.configure(cursor="hand2")

        # file list (its header shows the double-click tip)
        self.file_list = ctk.CTkScrollableFrame(
            parent, label_text=i18n.t(self.ui_lang, "double_click_tip"))
        self.file_list.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)

        # bottom bar — buttons + trailing status line share one row, same
        # pattern as the Live/Edit/AI tabs' bottom rows, instead of the
        # status line sitting on its own line below everything.
        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=12, pady=(6, 4))
        bottom.grid_columnconfigure(4, weight=1)

        self.progress_bar = ctk.CTkProgressBar(bottom)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8))

        self.transcribe_button = ctk.CTkButton(
            bottom, text="", height=36, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_transcription)
        self.transcribe_button.grid(row=1, column=0, padx=(0, 8))

        self.cancel_button = ctk.CTkButton(
            bottom, text="", height=36, fg_color="#8a3535",
            hover_color="#a04040", command=self._cancel_transcription)
        self.cancel_button.grid(row=1, column=1, padx=(0, 8))
        self.cancel_button.grid_remove()

        self.clear_button = ctk.CTkButton(
            bottom, text="", height=36, width=90, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._clear_list)
        self.clear_button.grid(row=1, column=2, padx=(0, 8))

        self.open_folder_button = ctk.CTkButton(
            bottom, text="", height=36, width=150, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._open_output_folder)
        self.open_folder_button.grid(row=1, column=3, padx=(0, 10))

        self.status_line = ctk.CTkLabel(bottom, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.status_line.grid(row=1, column=4, sticky="ew")

    # ------------------------------------------------------------- live tab

    def _build_live_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        options = ctk.CTkFrame(parent)
        options.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 6))
        options.grid_columnconfigure(6, weight=1)

        self.live_language_label = ctk.CTkLabel(options, text="")
        self.live_language_label.grid(row=0, column=0, padx=(12, 6), pady=(10, 4))
        self.live_language_menu = ctk.CTkOptionMenu(
            options, width=130, command=self._on_live_pref_change,
        )
        self.live_language_menu.grid(row=0, column=1, padx=(0, 16), pady=(10, 4))

        self.live_mic_label = ctk.CTkLabel(options, text="")
        self.live_mic_label.grid(row=0, column=2, padx=(0, 6), pady=(10, 4))
        # Persisted by device *name* (indices shuffle when devices come and
        # go); values are refreshed every time the tab is shown so a mic
        # plugged in mid-session appears without a restart.
        self.live_mic_menu = ctk.CTkOptionMenu(
            options, width=230, command=self._on_live_pref_change,
            dynamic_resizing=False,
        )
        self.live_mic_menu.grid(row=0, column=3, sticky="w", padx=(0, 16), pady=(10, 4))

        # Input level meter, grouped right next to the device it reports
        # on — live feedback that the chosen mic is actually hearing
        # something (the most common "nothing transcribes" cause is the
        # wrong/muted device, which otherwise just looks like silence).
        # Fed from the worker's per-chunk RMS by _tick_player.
        self.live_level_label = ctk.CTkLabel(options, text="")
        self.live_level_label.grid(row=0, column=4, padx=(0, 6), pady=(10, 4))
        self.live_level_bar = ctk.CTkProgressBar(options, width=90)
        self.live_level_bar.set(0)
        self.live_level_bar.grid(row=0, column=5, sticky="w", padx=(0, 12), pady=(10, 4))

        self.live_filename_label = ctk.CTkLabel(options, text="")
        self.live_filename_label.grid(row=1, column=0, padx=(12, 6), pady=(4, 4))
        self.live_filename_entry = ctk.CTkEntry(options, width=440)
        self.live_filename_entry.grid(
            row=1, column=1, columnspan=3, sticky="w", padx=(0, 16), pady=(4, 4))
        # Cleared any stale validation message the moment the user starts
        # fixing what they typed — the message itself only (re)appears on
        # the next Start Recording click, not on every keystroke.
        self.live_filename_entry.bind(
            "<KeyRelease>", lambda _e: self._hide_live_filename_error())
        self.live_filename_error_label = ctk.CTkLabel(
            options, text="", anchor="w", text_color="#e57373")
        self.live_filename_error_label.grid(
            row=2, column=0, columnspan=7, sticky="ew", padx=12, pady=(0, 2))
        self.live_filename_error_label.grid_remove()

        self.live_hint_label = ctk.CTkLabel(
            options, text="", anchor="w", text_color=self.MUTED_TEXT, wraplength=640,
            justify="left", height=18)
        self.live_hint_label.grid(row=3, column=0, columnspan=7, sticky="ew",
                                  padx=12, pady=(0, 10))

        # hint (left) + font size row (right) — same layout as the Edit
        # tab's hint_row, sitting directly above the text area it resizes.
        font_row = ctk.CTkFrame(parent, fg_color="transparent")
        font_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 2))
        font_row.grid_columnconfigure(0, weight=1)
        self.live_text_hint_label = ctk.CTkLabel(
            font_row, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.live_text_hint_label.grid(row=0, column=0, sticky="w")
        self.live_text_font = ctk.CTkFont(size=self.prefs["live_text_font_size"])
        self._build_font_size_row(
            font_row, self.live_text_font, "live_text_font_size"
        ).grid(row=0, column=1, sticky="e")

        self.live_text = ctk.CTkTextbox(parent, wrap="word", font=self.live_text_font)
        self.live_text.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        self.live_text.configure(state="disabled")

        # Same layout as the Edit tab's save row: buttons left-aligned,
        # status text following immediately after on the same row.
        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 4))
        bottom.grid_columnconfigure(3, weight=1)

        self.live_toggle_button = ctk.CTkButton(
            bottom, text="", height=36,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._toggle_live_recording)
        self.live_toggle_button.grid(row=0, column=0, padx=(0, 8))

        # Saves everything transcribed so far without stopping — so a long
        # session's early minutes can be edited while dictation continues.
        # Only meaningful mid-recording; enabled/disabled with the session.
        self.live_draft_button = ctk.CTkButton(
            bottom, text="", height=36, width=130, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            state="disabled", command=self._save_live_draft)
        self.live_draft_button.grid(row=0, column=1, padx=(0, 8))

        self.live_open_folder_button = ctk.CTkButton(
            bottom, text="", height=36, width=150, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=lambda: self._open_output_folder(settings.live_recordings_folder()))
        self.live_open_folder_button.grid(row=0, column=2, padx=(0, 10))

        self.live_status_line = ctk.CTkLabel(bottom, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.live_status_line.grid(row=0, column=3, sticky="ew")

    # ------------------------------------------------------------ edit tab

    def _build_edit_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(4, weight=1)

        # file picker row
        picker = ctk.CTkFrame(parent)
        picker.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 6))
        picker.grid_columnconfigure(1, weight=1)
        self.edit_file_label = ctk.CTkLabel(picker, text="")
        self.edit_file_label.grid(row=0, column=0, padx=(12, 6), pady=10)
        self.edit_file_menu = ctk.CTkOptionMenu(
            picker, values=[""], command=self._on_edit_file_selected)
        self.edit_file_menu.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=10)
        self.edit_open_button = ctk.CTkButton(
            picker, text="", width=120, command=self._open_file_for_edit)
        self.edit_open_button.grid(row=0, column=2, padx=(0, 10), pady=10)

        # player controls row
        controls = ctk.CTkFrame(parent)
        controls.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        controls.grid_columnconfigure(3, weight=1)

        self.play_button = ctk.CTkButton(controls, text="", width=96,
                                         command=self._toggle_play)
        self.play_button.grid(row=0, column=0, padx=(12, 6), pady=10)
        self.stop_button = ctk.CTkButton(
            controls, text="", width=96, fg_color="transparent", border_width=1,
            text_color=self.OUTLINE_BUTTON_TEXT, command=self._stop_play)
        self.stop_button.grid(row=0, column=1, padx=(0, 12), pady=10)

        self.time_label = ctk.CTkLabel(controls, text="00:00 / 00:00", width=110)
        self.time_label.grid(row=0, column=2, padx=(0, 8), pady=10)

        self.position_slider = ctk.CTkSlider(
            controls, from_=0, to=1, command=self._on_seek)
        self.position_slider.set(0)
        self.position_slider.grid(row=0, column=3, sticky="ew", padx=8, pady=10)

        self.speed_caption = ctk.CTkLabel(controls, text="")
        self.speed_caption.grid(row=0, column=4, padx=(8, 4), pady=10)
        self.speed_menu = ctk.CTkOptionMenu(
            controls, width=80, values=[_speed_label(s) for s in SPEED_OPTIONS],
            command=self._on_speed_change)
        self.speed_menu.set(_speed_label(1.0))
        self.speed_menu.grid(row=0, column=5, padx=(0, 12), pady=10)

        # editor hint (+ font size controls and the punctuation pad toggle,
        # right-aligned on the same row)
        hint_row = ctk.CTkFrame(parent, fg_color="transparent")
        hint_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 2))
        hint_row.grid_columnconfigure(0, weight=1)
        self.editor_hint_label = ctk.CTkLabel(hint_row, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.editor_hint_label.grid(row=0, column=0, sticky="w")
        self.editor_font = ctk.CTkFont(size=self.prefs["editor_font_size"])
        self._build_font_size_row(
            hint_row, self.editor_font, "editor_font_size"
        ).grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.ts_toggle_button = ctk.CTkButton(
            hint_row, text="", width=90, height=24, font=ctk.CTkFont(size=12),
            fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._toggle_timestamps)
        self.ts_toggle_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
        self.punct_toggle_button = ctk.CTkButton(
            hint_row, text="", width=90, height=24, font=ctk.CTkFont(size=12),
            fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._toggle_punct_pad)
        self.punct_toggle_button.grid(row=0, column=3, sticky="e")

        # punctuation pad — hidden until toggled on; sits above the editor
        # so inserts land wherever the cursor already is.
        self.punct_pad = ctk.CTkFrame(parent, fg_color=("gray90", "gray17"))
        self.punct_pad.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 4))
        self._build_punct_pad(self.punct_pad)

        # editor
        self.editor = ctk.CTkTextbox(parent, wrap="word", font=self.editor_font)
        self.editor.grid(row=4, column=0, sticky="nsew", padx=12, pady=6)

        # Clickable [mm:ss] paragraph markers: styled + bound through Tk
        # text tags on the underlying Text widget, so they keep their
        # styling and click behavior while moving naturally with the text
        # as the user edits around them.
        tb = self.editor._textbox
        tb.tag_configure(self.TS_TAG, foreground="#3B8ED0")
        tb.tag_bind(self.TS_TAG, "<Button-1>", self._on_timestamp_click)
        tb.tag_bind(self.TS_TAG, "<Enter>",
                    lambda _e: tb.configure(cursor="hand2"))
        tb.tag_bind(self.TS_TAG, "<Leave>",
                    lambda _e: tb.configure(cursor="xterm"))
        self.edit_times_available = False
        # Session-only, like punct_pad_visible below — not a saved
        # preference, so markers never surprise you by appearing (or a
        # saved copy never surprises you by including them) just because
        # a previous session happened to leave the toggle on. Rendered
        # immediately (not left to fire only once a file loads) so the
        # tag's elide state is deterministically "hidden" from the moment
        # the tab is built.
        self.timestamps_visible = False
        self._render_ts_toggle()

        # A session-only convenience, not a saved preference — always
        # starts hidden so it never surprises you on the next launch.
        self._set_punct_pad_visible(False)

        # save row
        saverow = ctk.CTkFrame(parent, fg_color="transparent")
        saverow.grid(row=5, column=0, sticky="ew", padx=12, pady=(2, 4))
        saverow.grid_columnconfigure(3, weight=1)
        self.save_button = ctk.CTkButton(
            saverow, text="", height=36, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._save_edit)
        self.save_button.grid(row=0, column=0, padx=(0, 8))
        self.edit_open_folder_button = ctk.CTkButton(
            saverow, text="", height=36, width=150, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._open_output_folder)
        self.edit_open_folder_button.grid(row=0, column=1, padx=(0, 10))
        # "New live text arrived" notice — appears only while the open file
        # is a live session's draft that has since grown on disk; clicking
        # appends the new paragraphs to the end of the buffer. A notice
        # instead of an automatic splice on purpose: text must never appear
        # in the editor mid-keystroke without the user asking for it.
        self.edit_new_live_button = ctk.CTkButton(
            saverow, text="", height=36,
            command=self._pull_in_live_content)
        self.edit_new_live_button.grid(row=0, column=2, padx=(0, 10))
        self.edit_new_live_button.grid_remove()
        self.edit_status_line = ctk.CTkLabel(saverow, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.edit_status_line.grid(row=0, column=3, sticky="ew")

        self._set_player_enabled(False)

    # -------------------------------------------------------------- AI tab

    def _build_llm_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        # file picker row
        picker = ctk.CTkFrame(parent)
        picker.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 6))
        picker.grid_columnconfigure(1, weight=1)
        self.llm_file_label = ctk.CTkLabel(picker, text="")
        self.llm_file_label.grid(row=0, column=0, padx=(12, 6), pady=10)
        self.llm_file_menu = ctk.CTkOptionMenu(
            picker, values=[""], command=self._on_llm_file_selected)
        self.llm_file_menu.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=10)
        self.llm_open_button = ctk.CTkButton(
            picker, text="", width=120, command=self._open_file_for_llm)
        self.llm_open_button.grid(row=0, column=2, padx=(0, 10), pady=10)

        # action row — Mode, Target, Quality, and the RAM caption all
        # together on one line, as originally designed. That's wider than
        # some windows can show even maximized, so — same fix as the tab
        # bar — it's a scrollable strip: it just looks like a normal card
        # row when everything fits, and grows "<"/">" arrows to pan across
        # it when it doesn't.
        actions, self.llm_actions_canvas, self.llm_actions_left, \
            self.llm_actions_right = self._make_scroll_strip(
                parent, row=1, column=0, padx=12, pady=6)

        self.llm_mode_label = ctk.CTkLabel(actions, text="")
        self.llm_mode_label.grid(row=0, column=0, padx=(12, 6), pady=10)
        self.llm_mode_button = ctk.CTkSegmentedButton(
            actions, command=self._on_llm_pref_change)
        self.llm_mode_button.grid(row=0, column=1, padx=(0, 14), pady=10)

        self.llm_target_label = ctk.CTkLabel(actions, text="")
        self.llm_target_label.grid(row=0, column=2, padx=(0, 6), pady=10)
        self.llm_target_menu = ctk.CTkOptionMenu(
            actions, width=150, command=self._on_llm_pref_change)
        self.llm_target_menu.grid(row=0, column=3, padx=(0, 14), pady=10)

        self.llm_quality_label = ctk.CTkLabel(actions, text="")
        self.llm_quality_label.grid(row=0, column=4, padx=(0, 6), pady=10)
        self.llm_quality_button = ctk.CTkSegmentedButton(
            actions, command=self._on_llm_pref_change)
        self.llm_quality_button.grid(row=0, column=5, padx=(0, 12), pady=10)

        # Sits right after the quality picker it's advising on. Kept a
        # wraplength as a safety net so a long message (the "close other
        # apps first" variant especially) wraps instead of stretching the
        # row indefinitely.
        self.llm_ram_caption = ctk.CTkLabel(
            actions, text="", anchor="w", justify="left", text_color=self.MUTED_TEXT,
            font=ctk.CTkFont(size=11), wraplength=360)
        self.llm_ram_caption.grid(row=0, column=6, sticky="w", padx=(0, 12), pady=10)

        self._finalize_scroll_strip(self.llm_actions_canvas, actions,
                                    self.llm_actions_left, self.llm_actions_right)

        # panels, with a draggable sash between them. Each side's header
        # (title + font-size buttons) lives directly above its own textbox,
        # inside the same container and the same grid column as that
        # textbox — so their widths are literally the same number, not two
        # independently-computed ones we have to keep in sync. A long label
        # like "AI output — editable when finished" can still set a floor
        # on how narrow that side gets, but it can no longer drift out of
        # step with the panel below it, and its own buttons can't vanish.
        self.llm_panels = panels = ctk.CTkFrame(parent, fg_color="transparent")
        panels.grid(row=2, column=0, sticky="nsew", padx=12, pady=(2, 6))
        panels.grid_rowconfigure(0, weight=1)
        panels.grid_columnconfigure(1, weight=0)  # sash: fixed width

        left_side = ctk.CTkFrame(panels, fg_color="transparent")
        left_side.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left_side.grid_columnconfigure(0, weight=1)
        left_side.grid_rowconfigure(1, weight=1)

        self.llm_source_font = ctk.CTkFont(size=self.prefs["llm_source_font_size"])
        left_header = ctk.CTkFrame(left_side, fg_color="transparent")
        left_header.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        left_header.grid_columnconfigure(0, weight=1)
        self.llm_left_title = ctk.CTkLabel(left_header, text="", anchor="w",
                                           text_color=self.MUTED_TEXT)
        self.llm_left_title.grid(row=0, column=0, sticky="w")
        self._build_font_size_row(
            left_header, self.llm_source_font, "llm_source_font_size"
        ).grid(row=0, column=1, sticky="e")

        self.llm_source = ctk.CTkTextbox(left_side, wrap="word",
                                         font=self.llm_source_font)
        self.llm_source.grid(row=1, column=0, sticky="nsew")
        self.llm_source.configure(state="disabled")

        self.llm_sash = ctk.CTkFrame(
            panels, width=6, fg_color=("gray75", "gray25"), corner_radius=3,
            cursor="sb_h_double_arrow")
        self.llm_sash.grid(row=0, column=1, sticky="ns")
        self.llm_sash.grid_propagate(False)
        for w in (self.llm_sash,):
            w.bind("<ButtonPress-1>", self._on_llm_sash_press)
            w.bind("<B1-Motion>", self._on_llm_sash_drag)
            w.bind("<ButtonRelease-1>", self._on_llm_sash_release)

        right_side = ctk.CTkFrame(panels, fg_color="transparent")
        right_side.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        right_side.grid_columnconfigure(0, weight=1)
        right_side.grid_rowconfigure(1, weight=1)

        self.llm_output_font = ctk.CTkFont(size=self.prefs["llm_output_font_size"])
        right_header = ctk.CTkFrame(right_side, fg_color="transparent")
        right_header.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        right_header.grid_columnconfigure(0, weight=1)
        self.llm_right_title = ctk.CTkLabel(right_header, text="", anchor="w",
                                            text_color=self.MUTED_TEXT)
        self.llm_right_title.grid(row=0, column=0, sticky="w")
        self._build_font_size_row(
            right_header, self.llm_output_font, "llm_output_font_size"
        ).grid(row=0, column=1, sticky="e")

        self.llm_output = ctk.CTkTextbox(right_side, wrap="word",
                                         font=self.llm_output_font)
        self.llm_output.grid(row=1, column=0, sticky="nsew")

        self.llm_split = self._clamp_split(self.prefs.get("llm_panel_split", 0.5))
        self._apply_llm_split()

        # save row — Generate leads, same as the primary action button in
        # every other tab (Transcribe All, Start Recording, Save copy in
        # Edit), followed by Save copy / Open folder / status.
        saverow = ctk.CTkFrame(parent, fg_color="transparent")
        saverow.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 4))
        saverow.grid_columnconfigure(3, weight=1)
        self.llm_generate_button = ctk.CTkButton(
            saverow, text="", height=36, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._generate_or_cancel)
        self.llm_generate_button.grid(row=0, column=0, padx=(0, 8))
        self.llm_save_button = ctk.CTkButton(
            saverow, text="", height=36, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._save_llm_output)
        self.llm_save_button.grid(row=0, column=1, padx=(0, 8))
        self.llm_open_folder_button = ctk.CTkButton(
            saverow, text="", height=36, width=150, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._open_output_folder)
        self.llm_open_folder_button.grid(row=0, column=2, padx=(0, 10))
        self.llm_status_line = ctk.CTkLabel(saverow, text="", anchor="w",
                                            text_color=self.MUTED_TEXT)
        self.llm_status_line.grid(row=0, column=3, sticky="ew")

    # ------------------------------------------------------- settings tab

    def _build_settings_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        scroll.grid_columnconfigure(0, weight=1)

        section_font = ctk.CTkFont(size=14, weight="bold")

        # --- Preferences
        prefs_card = ctk.CTkFrame(scroll)
        prefs_card.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 8))
        prefs_card.grid_columnconfigure(1, weight=1)
        self.settings_prefs_title = ctk.CTkLabel(
            prefs_card, text="", font=section_font, anchor="w")
        self.settings_prefs_title.grid(
            row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 6))

        self.chinese_trad_var = ctk.BooleanVar(value=True)
        self.chinese_trad_box = ctk.CTkCheckBox(
            prefs_card, text="", variable=self.chinese_trad_var,
            command=self._on_settings_pref_change)
        self.chinese_trad_box.grid(
            row=1, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 12))

        self.output_folder_title = ctk.CTkLabel(prefs_card, text="", anchor="w")
        self.output_folder_title.grid(row=2, column=0, sticky="w",
                                      padx=(12, 10), pady=(0, 12))
        self.output_folder_value = ctk.CTkLabel(
            prefs_card, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.output_folder_value.grid(row=2, column=1, sticky="ew",
                                      padx=(0, 10), pady=(0, 12))
        self.output_change_button = ctk.CTkButton(
            prefs_card, text="", width=90, height=28, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=self._change_output_folder)
        self.output_change_button.grid(row=2, column=2, padx=(0, 6), pady=(0, 12))
        self.output_reset_button = ctk.CTkButton(
            prefs_card, text="", width=90, height=28, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=self._reset_output_folder)
        self.output_reset_button.grid(row=2, column=3, padx=(0, 12), pady=(0, 12))

        # --- Models & storage
        models_card = ctk.CTkFrame(scroll)
        models_card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 8))
        models_card.grid_columnconfigure(0, weight=1)
        self.settings_models_title = ctk.CTkLabel(
            models_card, text="", font=section_font, anchor="w")
        self.settings_models_title.grid(row=0, column=0, sticky="w",
                                        padx=12, pady=(10, 2))
        self.settings_models_hint = ctk.CTkLabel(
            models_card, text="", anchor="w", justify="left",
            text_color=self.MUTED_TEXT, wraplength=640)
        self.settings_models_hint.grid(row=1, column=0, sticky="ew",
                                       padx=12, pady=(0, 6))
        self.model_rows_frame = ctk.CTkFrame(models_card, fg_color="transparent")
        self.model_rows_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.model_rows_frame.grid_columnconfigure(0, weight=1)
        self.settings_total_label = ctk.CTkLabel(
            models_card, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.settings_total_label.grid(row=3, column=0, sticky="w",
                                       padx=12, pady=(0, 10))

        # --- Maintenance
        maint_card = ctk.CTkFrame(scroll)
        maint_card.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 8))
        maint_card.grid_columnconfigure(0, weight=1)
        self.settings_maint_title = ctk.CTkLabel(
            maint_card, text="", font=section_font, anchor="w")
        self.settings_maint_title.grid(row=0, column=0, sticky="w",
                                       padx=12, pady=(10, 6))

        update_row = ctk.CTkFrame(maint_card, fg_color="transparent")
        update_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        update_row.grid_columnconfigure(3, weight=1)
        self.settings_version_label = ctk.CTkLabel(update_row, text="", anchor="w")
        self.settings_version_label.grid(row=0, column=0, padx=(0, 14))
        self.update_button = ctk.CTkButton(
            update_row, text="", width=150, height=28,
            command=self._check_updates)
        self.update_button.grid(row=0, column=1, padx=(0, 8))
        self.update_open_button = ctk.CTkButton(
            update_row, text="", width=150, height=28, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=lambda: webbrowser.open(RELEASES_PAGE_URL))
        self.update_open_button.grid(row=0, column=2, padx=(0, 8))
        self.update_open_button.grid_remove()
        self.update_status_label = ctk.CTkLabel(
            update_row, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.update_status_label.grid(row=0, column=3, sticky="ew")

        tools_row = ctk.CTkFrame(maint_card, fg_color="transparent")
        tools_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.open_log_button = ctk.CTkButton(
            tools_row, text="", width=150, height=28, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=self._open_log_file)
        self.open_log_button.grid(row=0, column=0, padx=(0, 8))
        self.open_models_button = ctk.CTkButton(
            tools_row, text="", width=150, height=28, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=lambda: self._open_output_folder(settings.MODELS_DIR))
        self.open_models_button.grid(row=0, column=1, padx=(0, 8))
        self.reset_settings_button = ctk.CTkButton(
            tools_row, text="", width=150, height=28, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=self._reset_all_settings)
        self.reset_settings_button.grid(row=0, column=2)

    def _retranslate_settings_tab(self):
        t = lambda key, **kw: i18n.t(self.ui_lang, key, **kw)  # noqa: E731
        self.settings_prefs_title.configure(text=t("settings_section_prefs"))
        self.chinese_trad_box.configure(text=t("settings_chinese_traditional"))
        self.output_folder_title.configure(text=t("settings_output_folder"))
        self.output_folder_value.configure(text=settings.output_base())
        self.output_change_button.configure(text=t("settings_output_change"))
        self.output_reset_button.configure(text=t("settings_output_reset"))
        self.settings_models_title.configure(text=t("settings_section_models"))
        self.settings_models_hint.configure(text=t("settings_models_hint"))
        self.settings_maint_title.configure(text=t("settings_section_maintenance"))
        self.settings_version_label.configure(
            text=t("settings_version", version=settings.APP_VERSION))
        self.update_button.configure(text=t("settings_check_updates"))
        self.update_open_button.configure(text=t("settings_update_open"))
        self.open_log_button.configure(text=t("settings_open_log"))
        self.open_models_button.configure(text=t("settings_open_models"))
        self.reset_settings_button.configure(text=t("settings_reset"))
        self._render_update_status()
        self._refresh_model_rows()

    def _refresh_settings_tab(self):
        self.output_folder_value.configure(text=settings.output_base())
        self._refresh_model_rows()

    def _refresh_models_if_visible(self):
        """The Settings tab's model list is only built on tab entry — a
        model that finishes downloading in the background (batch worker,
        live session) while the user is sitting on the tab would keep
        reading "Not downloaded" until they switched away and back. Called
        from the events that mark those completions. Skipped mid-Settings-
        download for the same reason _refresh_model_rows itself bails: a
        rebuild would destroy the row label that download's progress is
        being written to."""
        if self.current_tab == self.LEAF_SETTINGS and not self.model_downloads:
            self._refresh_model_rows()

    # -- model manager ----------------------------------------------------

    def _model_registry(self):
        """One spec per manageable model (whisper tiers, SenseVoice pair,
        LLM tiers): where it lives on disk, how big its download is, and
        how to tell whether it's already present."""
        specs = []
        for q in i18n.QUALITY_KEYS:
            size = transcriber.QUALITY_MODELS[q]
            specs.append({
                "key": f"whisper_{q}", "kind": "whisper", "quality": q,
                "folders": [os.path.join(
                    settings.MODELS_DIR, transcriber.whisper_repo_dirname(size))],
                "download_bytes": transcriber.MODEL_DOWNLOAD_MB[size] * 1024 ** 2,
                "is_downloaded": lambda s=size: transcriber.model_is_downloaded(s),
            })
        if self.sensevoice_available:
            specs.append({
                "key": "sensevoice", "kind": "sensevoice", "quality": None,
                "folders": transcriber.sensevoice_model_dirs(),
                "download_bytes": transcriber.SENSEVOICE_DOWNLOAD_MB * 1024 ** 2,
                "is_downloaded": transcriber.sensevoice_is_downloaded,
            })
        for q in i18n.QUALITY_KEYS:
            spec = llm.QUALITY_LLM[q]
            specs.append({
                "key": f"llm_{q}", "kind": "llm", "quality": q,
                "folders": [os.path.join(
                    settings.MODELS_DIR,
                    "models--" + spec["repo"].replace("/", "--"))],
                "download_bytes": int(spec["size_gb"] * 1024 ** 3),
                "is_downloaded": lambda qq=q: llm.llm_model_is_downloaded(qq),
            })
        return specs

    def _model_display_name(self, spec):
        quality = i18n.quality_display(spec["quality"], self.ui_lang) \
            if spec["quality"] else ""
        if spec["kind"] == "whisper":
            size = "whisper-" + transcriber.QUALITY_MODELS[spec["quality"]]
            return i18n.t(self.ui_lang, "settings_model_whisper", quality=quality, size=size)
        if spec["kind"] == "llm":
            return i18n.t(self.ui_lang, "settings_model_llm", quality=quality)
        return i18n.t(self.ui_lang, "settings_model_sensevoice")

    def _refresh_model_rows(self):
        # Rebuilding mid-download would destroy the label its progress is
        # being written to; the refresh happens again from the "done"/
        # "failed" event instead.
        if self.model_downloads:
            return
        t = lambda key, **kw: i18n.t(self.ui_lang, key, **kw)  # noqa: E731
        for child in self.model_rows_frame.winfo_children():
            child.destroy()
        self.model_rows = {}
        for r, spec in enumerate(self._model_registry()):
            row = ctk.CTkFrame(self.model_rows_frame, fg_color=("gray95", "gray24"))
            row.grid(row=r, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(0, weight=1)
            name = ctk.CTkLabel(row, text=self._model_display_name(spec), anchor="w")
            name.grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=8)
            downloaded = spec["is_downloaded"]()
            if downloaded:
                on_disk = sum(_folder_size(f) for f in spec["folders"]
                              if os.path.isdir(f))
                status_text = t("settings_model_downloaded", size=_fmt_size(on_disk))
            else:
                status_text = t("settings_model_not_downloaded",
                                size=_fmt_size(spec["download_bytes"]))
            status = ctk.CTkLabel(row, text=status_text, anchor="e",
                                  text_color=self.MUTED_TEXT)
            status.grid(row=0, column=1, padx=(0, 10), pady=8)
            if downloaded:
                button = ctk.CTkButton(
                    row, text=t("settings_model_delete"), width=88, height=26,
                    fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT,
                    border_width=1,
                    command=lambda s=spec: self._delete_model(s))
            else:
                button = ctk.CTkButton(
                    row, text=t("settings_model_download"), width=88, height=26,
                    command=lambda s=spec: self._download_model(s))
            button.grid(row=0, column=2, padx=(0, 10), pady=6)
            self.model_rows[spec["key"]] = {"spec": spec, "status": status,
                                            "button": button}
        total = _folder_size(settings.MODELS_DIR) \
            if os.path.isdir(settings.MODELS_DIR) else 0
        self.settings_total_label.configure(
            text=t("settings_total_usage", size=_fmt_size(total)))

    def _models_busy(self):
        return self.running or self.live_running or self.llm_running

    def _download_model(self, spec):
        key = spec["key"]
        row = self.model_rows.get(key)
        # One download at a time — the progress plumbing (and the
        # download-hook patching the SenseVoice path does) isn't built for
        # two at once, and neither is a typical home connection.
        if self._models_busy() or self.model_downloads:
            if row:
                row["status"].configure(
                    text=i18n.t(self.ui_lang, "settings_model_busy"))
            return
        self.model_downloads.add(key)
        if row:
            row["button"].configure(state="disabled")
            row["status"].configure(
                text=i18n.t(self.ui_lang, "settings_model_downloading", pct=0))

        def emit_pct(pct):
            self.events.put(("model_dl", key, "downloading", {"pct": pct}))

        def work():
            try:
                os.makedirs(settings.MODELS_DIR, exist_ok=True)
                os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
                from progress import make_progress_tqdm_class

                reporter = make_progress_tqdm_class(
                    transcriber.monotonic_pct_reporter(emit_pct))
                if spec["kind"] == "whisper":
                    import huggingface_hub

                    size = transcriber.QUALITY_MODELS[spec["quality"]]
                    huggingface_hub.snapshot_download(
                        transcriber.WHISPER_MODEL_REPOS[size],
                        cache_dir=settings.MODELS_DIR,
                        allow_patterns=transcriber._WHISPER_ALLOW_PATTERNS,
                        tqdm_class=reporter)
                elif spec["kind"] == "llm":
                    from huggingface_hub import hf_hub_download

                    llm_spec = llm.QUALITY_LLM[spec["quality"]]
                    hf_hub_download(llm_spec["repo"], llm_spec["file"],
                                    cache_dir=settings.MODELS_DIR,
                                    tqdm_class=reporter)
                else:
                    # SenseVoice: same loader Start Recording and batch
                    # jobs use (HF download with ModelScope fallback) — it
                    # loads the model into memory as well, which doubles as
                    # warming it up for this session.
                    def on_pct(pct):
                        emit_pct(pct)
                        if pct >= 100:
                            self.events.put(("model_dl", key, "loading", {}))

                    transcriber.get_sensevoice_model(
                        transcriber.monotonic_pct_reporter(on_pct))
                self.events.put(("model_dl", key, "done", {}))
            except Exception:
                settings.log_exception(f"Model download failed ({key}):")
                self.events.put(("model_dl", key, "failed", {}))

        threading.Thread(target=work, daemon=True).start()

    def _on_model_dl_event(self, key, status, detail):
        row = self.model_rows.get(key)
        t = lambda k, **kw: i18n.t(self.ui_lang, k, **kw)  # noqa: E731
        if status == "downloading":
            if row:
                row["status"].configure(
                    text=t("settings_model_downloading", pct=detail.get("pct", 0)))
            return
        if status == "loading":
            if row:
                row["status"].configure(text=t("settings_model_loading"))
            return
        self.model_downloads.discard(key)
        if status == "failed":
            if row:
                row["status"].configure(text=t("settings_model_dl_failed"))
                row["button"].configure(state="normal")
            return
        self._refresh_model_rows()

    def _delete_model(self, spec):
        row = self.model_rows.get(spec["key"])
        if self._models_busy() or self.model_downloads:
            if row:
                row["status"].configure(
                    text=i18n.t(self.ui_lang, "settings_model_busy"))
            return
        if not messagebox.askyesno(
            i18n.t(self.ui_lang, "settings_model_delete_confirm_title"),
            i18n.t(self.ui_lang, "settings_model_delete_confirm",
                   name=self._model_display_name(spec))):
            return
        if spec["kind"] == "llm":
            # A loaded .gguf is memory-mapped (= locked on Windows);
            # dropping the cache first is what makes the delete succeed.
            llm.unload_cached_model()
        elif spec["kind"] == "sensevoice":
            # Without this, a model already loaded into memory this session
            # (Transcribe tab, Live tab, or this very Settings download)
            # keeps working from RAM after its files are gone — every tab's
            # "is it downloaded" check would then disagree with what
            # Settings just did, until the app restarts and the cache
            # clears naturally.
            transcriber.unload_sensevoice_model()
        ok = True
        for folder in spec["folders"]:
            if os.path.isdir(folder):
                try:
                    shutil.rmtree(folder)
                except Exception:
                    settings.log_exception(f"Model delete failed: {folder}")
                    ok = False
        self._refresh_model_rows()
        if not ok:
            row = self.model_rows.get(spec["key"])
            if row:
                row["status"].configure(
                    text=i18n.t(self.ui_lang, "settings_model_delete_failed"))

    # -- preferences / maintenance -----------------------------------------

    def _on_settings_pref_change(self):
        self.prefs["chinese_traditional"] = bool(self.chinese_trad_var.get())
        settings.save(self.prefs)

    def _offer_output_folder_fix(self):
        """Shown at most once per session, right after a save fails with a
        permission/OS-level error — the classic symptom of the output
        folder not actually being writable. Most common on an unsigned
        macOS build launched straight from Downloads or a mounted disk
        image (see the README's "Running the unsigned macOS build"
        section): Gatekeeper silently runs the app from a hidden,
        read-only copy in that case, so every save fails the same way no
        matter which tab triggered it. Offers the one fix this app can
        actually help with directly — jumping to Settings and opening the
        output-folder picker immediately — rather than leaving the user to
        find that on their own from a bare error message."""
        if self._save_failure_hint_shown:
            return
        self._save_failure_hint_shown = True
        key = ("save_failed_folder_message_mac" if sys.platform == "darwin"
              else "save_failed_folder_message")
        if messagebox.askyesno(
            i18n.t(self.ui_lang, "save_failed_folder_title"),
            i18n.t(self.ui_lang, key),
        ):
            self._show_tab(self.LEAF_SETTINGS)
            self._change_output_folder()

    def _change_output_folder(self):
        folder = filedialog.askdirectory(
            title=i18n.t(self.ui_lang, "settings_output_pick_dialog"),
            initialdir=settings.output_base())
        if not folder:
            return
        folder = os.path.abspath(folder)
        try:
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".sota-write-test")
            with open(probe, "w"):
                pass
            os.remove(probe)
        except Exception:
            messagebox.showerror(
                "SOTA", i18n.t(self.ui_lang, "settings_output_not_writable"))
            return
        self.prefs["output_folder"] = folder
        settings.set_output_base(folder)
        settings.save(self.prefs)
        self.output_folder_value.configure(text=settings.output_base())

    def _reset_output_folder(self):
        self.prefs["output_folder"] = ""
        settings.set_output_base("")
        settings.save(self.prefs)
        self.output_folder_value.configure(text=settings.output_base())

    def _open_log_file(self):
        try:
            if os.path.isfile(settings.LOG_FILE):
                _open_path(settings.LOG_FILE)
            else:
                os.makedirs(settings.APP_DIR, exist_ok=True)
                _open_path(settings.APP_DIR)
        except Exception:
            settings.log_exception("Open log file failed:")

    def _check_updates(self):
        if self.update_checking:
            return
        self.update_checking = True
        self.update_open_button.grid_remove()
        self._set_update_status("settings_update_checking", {})

        def work():
            tag = None
            try:
                import json
                import ssl
                import urllib.request

                # certifi's CA bundle, not ssl's defaults: in the packaged
                # macOS build, ssl's default verify paths point at build-
                # machine locations that don't exist on the user's Mac, so
                # every HTTPS request fails certificate verification.
                # certifi ships inside the bundle (requests depends on it),
                # so it always resolves; fall back to defaults if not.
                try:
                    import certifi
                    context = ssl.create_default_context(cafile=certifi.where())
                except Exception:
                    context = None

                req = urllib.request.Request(UPDATE_API_URL, headers={
                    "User-Agent": "SOTA",
                    "Accept": "application/vnd.github+json",
                })
                with urllib.request.urlopen(req, timeout=10, context=context) as resp:
                    tag = (json.load(resp).get("tag_name") or "").strip()
            except Exception:
                settings.log_exception("Update check failed:")
            self.events.put(("update_check", tag))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_check_result(self, tag):
        self.update_checking = False
        if not tag:
            self._set_update_status("settings_update_failed", {})
        elif _version_tuple(tag) > _version_tuple(settings.APP_VERSION):
            self._set_update_status("settings_update_available",
                                    {"version": tag.lstrip("vV")})
            self.update_open_button.grid()
        else:
            self._set_update_status("settings_update_latest", {})

    def _set_update_status(self, key, detail):
        self.update_status_key = key
        self.update_status_detail = detail if isinstance(detail, dict) else {}
        self._render_update_status()

    def _render_update_status(self):
        if self.update_status_key is None:
            self.update_status_label.configure(text="")
            return
        self.update_status_label.configure(
            text=i18n.t(self.ui_lang, self.update_status_key,
                        **(self.update_status_detail or {})))

    def _reset_all_settings(self):
        if not messagebox.askyesno(
            i18n.t(self.ui_lang, "settings_reset_confirm_title"),
            i18n.t(self.ui_lang, "settings_reset_confirm")):
            return
        self.prefs = dict(settings.DEFAULTS)
        settings.set_output_base("")
        settings.save(self.prefs)
        self.ui_lang = self.prefs["ui_language"]
        self.editor_font.configure(size=self.prefs["editor_font_size"])
        self.llm_source_font.configure(size=self.prefs["llm_source_font_size"])
        self.llm_output_font.configure(size=self.prefs["llm_output_font_size"])
        self.live_text_font.configure(size=self.prefs["live_text_font_size"])
        self.llm_split = self._clamp_split(self.prefs["llm_panel_split"])
        self._apply_llm_split()
        self._apply_prefs()
        self._retranslate()
        self._refresh_settings_tab()

    # --------------------------------------------- AI tab: resizable panels

    SPLIT_MIN, SPLIT_MAX = 0.15, 0.85

    @classmethod
    def _clamp_split(cls, split):
        try:
            split = float(split)
        except (TypeError, ValueError):
            split = 0.5
        return max(cls.SPLIT_MIN, min(cls.SPLIT_MAX, split))

    def _apply_llm_split(self):
        """Give the two panel containers (each holding its own header +
        textbox stacked together) column weights matching the current
        split ratio — the header and textbox on each side always share
        that same column, so they can never end up different widths."""
        left = max(1, round(self.llm_split * 1000))
        right = max(1, 1000 - left)
        self.llm_panels.grid_columnconfigure(0, weight=left)
        self.llm_panels.grid_columnconfigure(2, weight=right)

    def _on_llm_sash_press(self, event):
        self._sash_drag_start_x = event.x_root
        self._sash_drag_start_split = self.llm_split

    def _on_llm_sash_drag(self, event):
        total_width = self.llm_panels.winfo_width()
        if total_width <= 1:
            return
        dx = event.x_root - self._sash_drag_start_x
        self.llm_split = self._clamp_split(
            self._sash_drag_start_split + dx / total_width)
        self._apply_llm_split()

    def _on_llm_sash_release(self, _event):
        self.prefs["llm_panel_split"] = self.llm_split
        settings.save(self.prefs)

    # ------------------------------------------------------------- tabs

    @staticmethod
    def _equalize_segments(seg, width):
        """Give every segment the same fixed width so the control keeps its
        size when the labels change (e.g. switching UI language)."""
        for btn in seg._buttons_dict.values():
            btn.configure(width=width)

    FONT_SIZE_MIN = 10
    FONT_SIZE_MAX = 28
    FONT_SIZE_STEP = 2

    @classmethod
    def _clamp_font_size(cls, size):
        return max(cls.FONT_SIZE_MIN, min(cls.FONT_SIZE_MAX, int(size)))

    def _build_font_size_row(self, parent, ctk_font, pref_key):
        """A small right-aligned 'A-' / 'A+' pair that resizes `ctk_font`
        live (every widget using that CTkFont instance updates automatically)
        and remembers the size in settings under `pref_key`."""
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        def _step(delta):
            size = self._clamp_font_size(self.prefs[pref_key] + delta)
            self.prefs[pref_key] = size
            ctk_font.configure(size=size)
            settings.save(self.prefs)

        minus = ctk.CTkButton(
            frame, text="A-", width=30, height=24, font=ctk.CTkFont(size=12),
            fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=lambda: _step(-self.FONT_SIZE_STEP))
        minus.grid(row=0, column=0, padx=(0, 4))
        plus = ctk.CTkButton(
            frame, text="A+", width=30, height=24, font=ctk.CTkFont(size=12),
            fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=lambda: _step(self.FONT_SIZE_STEP))
        plus.grid(row=0, column=1)
        return frame

    # --------------------------------------------- edit tab: punctuation pad

    # Full-width CJK punctuation, grouped the way a Chinese IME's own
    # punctuation pad usually does — plain marks, then paired brackets/quotes.
    PUNCT_CHARS = [
        "，", "。", "、", "；", "：", "？", "！", "～", "—", "…", "‧", "·",
        "（", "）", "【", "】", "「", "」", "『", "』", "《", "》", "〈", "〉",
    ]

    def _build_punct_pad(self, frame):
        # All marks in a single row rather than wrapping to a second one.
        for col in range(len(self.PUNCT_CHARS)):
            frame.grid_columnconfigure(col, weight=1)
        for col, ch in enumerate(self.PUNCT_CHARS):
            btn = ctk.CTkButton(
                frame, text=ch, width=28, height=26, font=ctk.CTkFont(size=13),
                fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT,
                border_width=1, command=lambda c=ch: self._insert_punct(c))
            btn.grid(row=0, column=col, padx=2, pady=3, sticky="ew")

    def _insert_punct(self, char):
        self.editor.insert("insert", char)
        self.editor.focus_set()

    def _toggle_punct_pad(self):
        self._set_punct_pad_visible(not self.punct_pad_visible)

    # ------------------------------------------- edit tab: timestamps

    TS_TAG = "tstamp"

    def _apply_timestamp_markup(self):
        """Finds every [mm:ss] marker currently in the editor and tags it —
        the shared TS_TAG for styling/clicks/hiding, plus a per-marker
        "ts:<seconds>" tag that carries the seek target. Python string
        offsets convert directly to Tk "1.0+Nc" indices because the widget
        holds exactly the text that was inserted. Runs on load; markers
        typed by hand afterwards aren't clickable until reload, but Save
        still honors them (it re-parses by regex, not by tag)."""
        self._clean_orphaned_markers()
        tb = self.editor._textbox
        text = self.editor.get("1.0", "end-1c")
        found = False
        for m in timestamps.MARKER_RE.finditer(text):
            found = True
            start, end = f"1.0+{m.start()}c", f"1.0+{m.end()}c"
            tb.tag_add(self.TS_TAG, start, end)
            tb.tag_add(f"ts:{timestamps.marker_seconds(m)}", start, end)
        self.edit_times_available = found
        self._render_ts_toggle()

    def _clean_orphaned_markers(self):
        """Removes a [mm:ss] marker that's immediately followed by another
        one, with nothing in between — the trace left by deleting an
        entire paragraph while markers were toggled hidden. A hidden
        marker is elided to zero width, so nothing in the editor marks
        where it starts or lets a mouse/keyboard gesture select over it;
        "delete this whole line" aimed at the now-visible text leaves the
        marker itself behind, jammed against whatever paragraph follows.
        Left alone, it's not just a display glitch: Save's own parsing
        (timestamps.parse_marked_text) already collapses a run like this
        to the last marker, but the editor buffer itself would otherwise
        keep showing the orphan once markers are revealed, unlike what
        actually gets saved.

        Finds these the same way parse_marked_text does — repeatedly
        matching (and slicing off) a leading marker — rather than
        MARKER_RE.finditer over the whole text: finditer can only find a
        SECOND glued-together marker if MARKER_RE's ^ anchor sees it as a
        line start, which it never is here (nothing precedes it but the
        first marker, no newline) — finditer would silently miss exactly
        the case this exists to catch. Deletes straight from the Tk widget
        (not a buffer rewrite) so this can run on every re-tag without
        disturbing the user's cursor, scroll position, or undo stack
        elsewhere in the document — only the identified junk text moves.
        """
        tb = self.editor._textbox
        text = self.editor.get("1.0", "end-1c")
        to_delete = []  # (abs_start, abs_end) in `text`'s own offsets
        offset = 0
        for para in text.split("\n\n"):
            consumed = 0
            spans = []  # (start, end) within this paragraph
            remaining = para
            while True:
                m = timestamps.MARKER_RE.match(remaining)
                if not m:
                    break
                spans.append((consumed, consumed + m.end()))
                consumed += m.end()
                remaining = remaining[m.end():]
            for start, end in spans[:-1]:  # every marker but the last is an orphan
                to_delete.append((offset + start, offset + end))
            offset += len(para) + 2  # +2 for the "\n\n" text.split() consumed
        for start, end in reversed(to_delete):  # back to front: earlier offsets stay valid
            tb.delete(f"1.0+{start}c", f"1.0+{end}c")

    def _on_timestamp_click(self, event):
        tb = self.editor._textbox
        index = tb.index(f"@{event.x},{event.y}")
        for tag in tb.tag_names(index):
            if tag.startswith("ts:"):
                self._seek_to_seconds(float(tag[3:]))
                return "break"  # don't also move the insert cursor into the marker
        return None

    def _seek_to_seconds(self, seconds):
        if self.player.loaded_path is None or not self.player.duration:
            return
        frac = max(0.0, min(1.0, seconds / self.player.duration))
        self.player.seek_fraction(frac)
        self.position_slider.set(frac)
        self.time_label.configure(
            text=f"{_fmt_time(self.player.get_time())} / {_fmt_time(self.player.duration)}")

    def _toggle_timestamps(self):
        self.timestamps_visible = not self.timestamps_visible
        if self.timestamps_visible:
            # Revealing is the moment an orphaned marker (see
            # _clean_orphaned_markers) would otherwise become visible —
            # clean it up first so turning markers on never shows stale
            # junk left over from an earlier paragraph deletion.
            self._clean_orphaned_markers()
        self._render_ts_toggle()

    def _render_ts_toggle(self):
        """Show/hide is Tk's elide on the shared tag: hidden markers stay
        in the widget's text (so Save can still parse them and write the
        sidecar) — they just don't render or take up space."""
        show = self.timestamps_visible
        self.editor._textbox.tag_configure(self.TS_TAG, elide=not show)
        self.ts_toggle_button.configure(
            state="normal" if self.edit_times_available else "disabled",
            fg_color=("gray75", "gray30")
            if (show and self.edit_times_available) else "transparent")

    def _set_punct_pad_visible(self, visible):
        self.punct_pad_visible = visible
        if visible:
            self.punct_pad.grid()
        else:
            self.punct_pad.grid_remove()
        self.punct_toggle_button.configure(
            fg_color=("gray75", "gray30") if visible else "transparent")

    # Two-level tab structure: an outer strip of 4 groups, two of which
    # (Audio Studio, Transcription Studio) have their own inner strip of
    # subtabs. `self.current_tab` is always a *leaf* index into LEAF_KEYS
    # — every existing per-feature check (Live tab tick, Edit tab
    # autoload, etc.) keys off one specific leaf, never a group, so
    # keeping one flat index is far less invasive than threading a
    # (group, leaf) pair through all of them.
    # Enhance isn't its own leaf — it lives inside the Edit leaf's own
    # collapsible panel (see _build_enhance_panel, _toggle_effects_panel)
    # so the waveform it acts on is always the one on screen, not a tab
    # switch away. AI moved from being its own top-level group to
    # Transcription Studio's 4th subtab, alongside Transcribe/Live/Edit.
    LEAF_AUDIO_RECORD, LEAF_AUDIO_EDIT, \
        LEAF_TRANSCRIBE, LEAF_LIVE, LEAF_EDIT, LEAF_AI, LEAF_SETTINGS = range(7)
    LEAF_KEYS = [
        "tab_audio_record", "tab_audio_edit",
        "tab_transcribe", "tab_live", "tab_edit", "tab_llm",
        "tab_settings",
    ]
    GROUP_KEYS = [
        "tab_group_audio_studio", "tab_group_transcription_studio",
        "tab_group_settings",
    ]
    # Leaves (indices into LEAF_KEYS), in on-screen order, belonging to
    # each group. A group with only one leaf gets no inner strip at all —
    # clicking its outer button shows that leaf directly.
    GROUP_LEAVES = [
        [LEAF_AUDIO_RECORD, LEAF_AUDIO_EDIT],
        [LEAF_TRANSCRIBE, LEAF_LIVE, LEAF_EDIT, LEAF_AI],
        [LEAF_SETTINGS],
    ]
    GROUP_TRANSCRIPTION_STUDIO = 1  # index into GROUP_KEYS/GROUP_LEAVES — for the Live-badge tint

    # Selected tab matches the content panel's background (so it visually
    # merges into it); unselected tabs use the app's normal frame color.
    TAB_SELECTED_BG = ("gray92", "gray14")
    TAB_UNSELECTED_BG = ("gray86", "gray17")
    TAB_UNSELECTED_HOVER = ("gray80", "gray23")

    # CTkButton's built-in default text_color is a single pale color meant
    # to sit on the button's own filled, colored background — on an
    # outline-style button (fg_color="transparent") that text then sits
    # directly on the window background instead, and is nearly invisible in
    # light mode (pale-on-near-white). Any transparent-background button
    # needs this explicit override.
    OUTLINE_BUTTON_TEXT = ("gray10", "gray90")
    # Secondary/hint text (status lines, captions) — readable on both.
    MUTED_TEXT = ("gray35", "gray65")

    # -------------------------------------------- generic scrollable strip
    #
    # A reusable version of the tab bar's Word-ribbon-style horizontal
    # scroll (canvas viewport + "<"/">" arrows that appear only on
    # overflow) for any OTHER row of controls that might not fit a narrow
    # window — e.g. the AI tab's Mode/Target/Quality/RAM-caption row. The
    # tab bar itself predates this and keeps its own bespoke
    # implementation (_sync_tab_canvas_bg / _update_tab_scroll_state /
    # _scroll_tabs below) rather than being retrofitted onto it, so an
    # already-verified piece of navigation can't be destabilized by a
    # refactor it didn't need.

    def _resolve_root_bg(self):
        try:
            return self._apply_appearance_mode(self.cget("fg_color"))
        except Exception:
            return self.cget("fg_color")

    def _make_scroll_strip(self, parent, row, column=0, columnspan=1,
                           sticky="ew", padx=0, pady=0, fg_color=None):
        """Builds '<' arrow / canvas viewport / '>' arrow, gridded into
        `parent` at (row, column). Returns (content, canvas, left_btn,
        right_btn) — build your row's actual widgets as children of
        `content`, then call _finalize_scroll_strip(canvas, content,
        left_btn, right_btn) once they're all in place to lock in the
        right height and wire up auto-resize.

        fg_color is the canvas's background (it has no CTk theming of its
        own, so needs one set explicitly to blend in) — defaults to
        CTkFrame's own card color, matching a plain ctk.CTkFrame() card;
        pass "transparent" to match the window's own background instead."""
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=row, column=column, columnspan=columnspan,
                     sticky=sticky, padx=padx, pady=pady)
        wrapper.grid_columnconfigure(1, weight=1)

        if fg_color is None:
            fg_color = ctk.ThemeManager.theme["CTkFrame"]["fg_color"]
        resolved_bg = (self._resolve_root_bg() if fg_color == "transparent"
                      else self._apply_appearance_mode(fg_color))

        left_btn = ctk.CTkButton(wrapper, text="<", width=22, corner_radius=6)
        left_btn.grid(row=0, column=0, padx=(0, 3))
        left_btn.grid_remove()

        canvas = tk.Canvas(wrapper, highlightthickness=0, bd=0, bg=resolved_bg)
        canvas.grid(row=0, column=1, sticky="ew")

        content = ctk.CTkFrame(canvas, fg_color=fg_color)
        canvas.create_window((0, 0), window=content, anchor="nw")

        right_btn = ctk.CTkButton(wrapper, text=">", width=22, corner_radius=6)
        right_btn.grid(row=0, column=2, padx=(3, 0))
        right_btn.grid_remove()

        left_btn.configure(command=lambda: self._scroll_strip(canvas, content, -1))
        right_btn.configure(command=lambda: self._scroll_strip(canvas, content, 1))
        canvas.bind("<Configure>", lambda _e: self._update_strip_overflow(
            canvas, content, left_btn, right_btn))
        content.bind("<Configure>", lambda _e: self._on_strip_content_resize(
            canvas, content, left_btn, right_btn))

        return content, canvas, left_btn, right_btn

    def _finalize_scroll_strip(self, canvas, content, left_btn, right_btn):
        """Call once content's widgets are all built — measures the real
        rendered height (a CTkButton's actual height isn't its constructor
        height= argument; guessing a fixed canvas height clips content,
        exactly the tab-bar bug this generic version avoids by
        construction) and locks the canvas + arrows to match."""
        content.update_idletasks()
        h = content.winfo_reqheight()
        canvas.configure(height=h)
        left_btn.configure(height=h)
        right_btn.configure(height=h)
        self._update_strip_overflow(canvas, content, left_btn, right_btn)

    def _on_strip_content_resize(self, canvas, content, left_btn, right_btn):
        canvas.configure(scrollregion=canvas.bbox("all"))
        self._update_strip_overflow(canvas, content, left_btn, right_btn)

    def _update_strip_overflow(self, canvas, content, left_btn, right_btn):
        canvas.update_idletasks()
        content_w = content.winfo_reqwidth()
        viewport_w = canvas.winfo_width()
        if content_w > viewport_w + 1:
            left_btn.grid()
            right_btn.grid()
            max_x = max(0, content_w - viewport_w)
            if canvas.canvasx(0) > max_x:
                canvas.xview_moveto(max_x / content_w if content_w else 0)
        else:
            left_btn.grid_remove()
            right_btn.grid_remove()
            canvas.xview_moveto(0)

    def _scroll_strip(self, canvas, content, direction, step=140):
        canvas.update_idletasks()
        content_w = max(1, content.winfo_reqwidth())
        viewport_w = max(1, canvas.winfo_width())
        new_x = max(0, min(canvas.canvasx(0) + direction * step,
                           content_w - viewport_w))
        canvas.xview_moveto(new_x / content_w)

    # ---------------------------------------------------------- tab bar

    def _sync_tab_canvas_bg(self):
        """The raw tkinter.Canvas hosting the tab strip has no CTk theming
        of its own — resolve the root window's (light, dark) fg_color
        tuple to a concrete color for the current appearance mode, so the
        canvas doesn't show through as a mismatched flat square behind the
        tabs. Called once at build time; the app has no live light/dark
        toggle after launch, so this never needs to re-run."""
        try:
            color = self._apply_appearance_mode(self.cget("fg_color"))
        except Exception:
            color = self.cget("fg_color")
        self.tab_canvas.configure(bg=color)

    def _on_tab_canvas_resize(self, _event):
        self._update_tab_scroll_state()

    def _on_tab_bar_resize(self, _event):
        self.tab_canvas.configure(scrollregion=self.tab_canvas.bbox("all"))
        self._update_tab_scroll_state()

    def _update_tab_scroll_state(self):
        """Shows/hides the </> arrows depending on whether the tab strip's
        natural width exceeds the viewport it scrolls inside — called after
        any resize (window resize, or tab widths changing on a language
        switch) so the arrows track reality instead of a one-time guess
        made at startup."""
        self.tab_canvas.update_idletasks()
        content_w = self.tab_bar.winfo_reqwidth()
        viewport_w = self.tab_canvas.winfo_width()
        overflow = content_w > viewport_w + 1
        if overflow:
            self.tab_scroll_left.grid()
            self.tab_scroll_right.grid()
            # Clamp the current scroll offset in case the viewport just
            # grew (or the content just shrank) enough that the old
            # position now runs past the end.
            max_x = max(0, content_w - viewport_w)
            if self.tab_canvas.canvasx(0) > max_x:
                self.tab_canvas.xview_moveto(max_x / content_w if content_w else 0)
        else:
            self.tab_scroll_left.grid_remove()
            self.tab_scroll_right.grid_remove()
            self.tab_canvas.xview_moveto(0)

    def _scroll_tabs(self, direction):
        """Pans the tab strip left/right by roughly one tab's width —
        discrete step navigation (like Word's ribbon overflow arrows),
        not free-form drag-scrolling."""
        self.tab_canvas.update_idletasks()
        content_w = max(1, self.tab_bar.winfo_reqwidth())
        viewport_w = max(1, self.tab_canvas.winfo_width())
        step = (self.tab_buttons[0].winfo_width() + 2) if self.tab_buttons else 100
        new_x = max(0, min(self.tab_canvas.canvasx(0) + direction * step,
                           content_w - viewport_w))
        self.tab_canvas.xview_moveto(new_x / content_w)

    def _ensure_active_tab_visible(self):
        """Pans the tab strip so the currently selected group's button is
        fully in view — otherwise a language switch reflowing tab widths
        (or programmatic tab changes) could leave the active tab scrolled
        out of sight with no visual indication it's still selected."""
        if not (0 <= self.current_group < len(self.tab_buttons)):
            return
        self.tab_canvas.update_idletasks()
        content_w = max(1, self.tab_bar.winfo_reqwidth())
        viewport_w = max(1, self.tab_canvas.winfo_width())
        if content_w <= viewport_w:
            return
        btn = self.tab_buttons[self.current_group]
        btn_x0, btn_x1 = btn.winfo_x(), btn.winfo_x() + btn.winfo_width()
        cur_x0 = self.tab_canvas.canvasx(0)
        if btn_x0 < cur_x0:
            self.tab_canvas.xview_moveto(btn_x0 / content_w)
        elif btn_x1 > cur_x0 + viewport_w:
            self.tab_canvas.xview_moveto(max(0, btn_x1 - viewport_w) / content_w)

    def _leaf_group(self, leaf):
        for group_index, leaves in enumerate(self.GROUP_LEAVES):
            if leaf in leaves:
                return group_index
        return 0

    def _leaf_frames(self):
        return [self.audio_record_frame, self.audio_edit_frame,
                self.transcribe_frame, self.live_frame, self.edit_frame, self.llm_frame,
                self.settings_frame]

    def _show_group(self, group_index):
        """Outer tab bar click: shows whichever leaf was last active in
        that group (or its first leaf, the first time)."""
        leaves = self.GROUP_LEAVES[group_index]
        self._show_tab(self._group_last_leaf.get(group_index, leaves[0]))

    def _show_tab(self, index):
        # A live session deliberately keeps running when you switch away —
        # that's the point of Save Draft: dictate, then work the early
        # minutes in Transcription Studio's Edit subtab while the rest
        # keeps coming in. The worker thread and its mic stream are
        # already fully independent of which tab is on screen;
        # _style_tab_buttons' recording badge is the reminder it's still
        # going. Audio Studio's recorder is the same story.
        self.current_tab = index
        self.current_group = self._leaf_group(index)
        self._group_last_leaf[self.current_group] = index
        for group_index, strip in self.subtab_strips.items():
            wrapper = strip[1].master  # the scroll-strip's own wrapper frame
            if group_index == self.current_group:
                wrapper.grid()
            else:
                wrapper.grid_remove()
        for i, frame in enumerate(self._leaf_frames()):
            if i == index:
                # Explicit args, not a bare grid() — most leaf frames may
                # never have been gridded before (see _build_ui), so
                # there's no remembered geometry for a bare call to restore.
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        if index == self.LEAF_LIVE:
            self._refresh_mic_menu()
            self._maybe_preload_sensevoice()
        elif index == self.LEAF_EDIT:
            self._maybe_autoload_edit()
        elif index == self.LEAF_AI:
            self._maybe_autoload_llm()
        elif index == self.LEAF_SETTINGS:
            self._refresh_settings_tab()
        elif index == self.LEAF_AUDIO_RECORD:
            self._refresh_audio_record_mic_menu()
        self._style_tab_buttons()
        self._ensure_active_tab_visible()

    # Recording-badge tints for the Live Transcription subtab (and, while
    # Transcription Studio isn't the active group, its outer tab button
    # too) — a color change only, never text, so the badge can't affect
    # any tab's width or force a layout recompute when a session
    # starts/stops/goes idle.
    LIVE_TAB_RECORDING_COLOR = "#e05a5a"
    LIVE_TAB_IDLE_COLOR = "#d99a30"

    def _style_tab_buttons(self):
        live_color = (self.LIVE_TAB_IDLE_COLOR if self.live_tab_idle
                     else self.LIVE_TAB_RECORDING_COLOR)
        for i, btn in enumerate(self.tab_buttons):
            selected = i == self.current_group
            text_color = ("gray10", "gray95") if selected else ("gray35", "gray65")
            # Only tinted while its own group isn't the one on screen —
            # looking straight at the Live subtab already shows the real
            # status line/text area, a much richer signal than a tinted
            # label, so the normal selected styling wins there instead of
            # fighting it for attention.
            if (i == self.GROUP_TRANSCRIPTION_STUDIO and self.live_running
                    and self.current_tab != self.LEAF_LIVE):
                text_color = live_color
            btn.configure(
                fg_color=self.TAB_SELECTED_BG if selected else self.TAB_UNSELECTED_BG,
                hover_color=self.TAB_SELECTED_BG if selected else self.TAB_UNSELECTED_HOVER,
                text_color=text_color,
                font=ctk.CTkFont(size=13, weight="bold" if selected else "normal"),
            )
        for group_index, (_content, _canvas, _left, _right, buttons) in self.subtab_strips.items():
            leaves = self.GROUP_LEAVES[group_index]
            group_active = group_index == self.current_group
            for leaf, btn in zip(leaves, buttons):
                selected = group_active and leaf == self.current_tab
                text_color = ("gray10", "gray95") if selected else self.MUTED_TEXT
                if leaf == self.LEAF_LIVE and self.live_running and not selected:
                    text_color = live_color
                btn.configure(
                    fg_color=self.TAB_SELECTED_BG if selected else "transparent",
                    text_color=text_color,
                    font=ctk.CTkFont(size=12, weight="bold" if selected else "normal"),
                )

    def _edit_has_unsaved_changes(self):
        if self._edit_loaded_text is None:
            return False
        return self.editor.get("1.0", "end-1c") != self._edit_loaded_text

    def _maybe_autoload_edit(self):
        """Keeps the Edit tab pointed at the most recently finished
        transcript — a fresh batch job, a live session, or simply nothing
        open yet — so it's always ready to review without the user having
        to reselect it from the dropdown. Backs off if there's nothing new,
        or if the currently-open transcript has unsaved edits: switching
        out from under those would silently discard them."""
        if not self.edit_files:
            return
        latest = self.edit_files[-1]
        if self.edit_current is latest:
            return
        if self.edit_current is not None and self._edit_has_unsaved_changes():
            return
        self._load_edit_entry(latest)

    def _maybe_autoload_llm(self):
        """Same idea as _maybe_autoload_edit, for the AI tab's source
        picker. Backs off while a generation is running — switching the
        source out from under an in-progress (or just-finished, still on
        screen) generation would be confusing, even though it wouldn't
        actually destroy the AI output itself."""
        if not self.edit_files or self.llm_running:
            return
        latest = self.edit_files[-1]
        if self.llm_current is not latest:
            self._load_llm_entry(latest)

    def _apply_prefs(self):
        if self.prefs["quality"] not in i18n.QUALITY_KEYS:
            self.prefs["quality"] = "balanced"
        valid_codes = {code for code, _, _ in i18n.TRANSCRIBE_LANGUAGES}
        if self.prefs["transcribe_language"] not in valid_codes:
            self.prefs["transcribe_language"] = "auto"
        if not self.sensevoice_available:
            self.prefs["sensevoice_preferred"] = False
        self.sensevoice_var.set(bool(self.prefs["sensevoice_preferred"]))
        self.sensevoice_box.configure(state="normal" if self.sensevoice_available else "disabled")
        if self.prefs["live_language"] not in i18n.LIVE_LANGUAGE_CODES:
            self.prefs["live_language"] = "auto"
        self.live_toggle_button.configure(state="normal" if self.sensevoice_available else "disabled")
        self.live_language_menu.configure(state="normal" if self.sensevoice_available else "disabled")
        self.live_mic_menu.configure(state="normal" if self.sensevoice_available else "disabled")
        self.live_filename_entry.configure(state="normal" if self.sensevoice_available else "disabled")
        self.chinese_trad_var.set(bool(self.prefs.get("chinese_traditional")))
        self.ui_lang_button.set("EN" if self.ui_lang == "en" else "繁中")
        if self.prefs["llm_mode"] not in i18n.LLM_MODES:
            self.prefs["llm_mode"] = "summarize"
        valid_targets = {key for key, _, _, _ in i18n.LLM_TARGET_LANGUAGES}
        if self.prefs["llm_target"] not in valid_targets:
            self.prefs["llm_target"] = "zh-hant"
        if self.prefs["llm_quality"] not in i18n.QUALITY_KEYS:
            self.prefs["llm_quality"] = "balanced"

    # --------------------------------------------------------- translation

    def _retranslate(self):
        t = lambda key, **kw: i18n.t(self.ui_lang, key, **kw)  # noqa: E731
        self.title(f"{t('app_title')} v{settings.APP_VERSION}")

        # outer group tabs — all four share one width, sized to fit the
        # widest label (measured in the bold variant, which the selected
        # tab uses, so selecting one never changes any width). Computed
        # from the current language's own longest label rather than a
        # fixed constant, same reasoning as the old five-tab strip.
        tab_font = ctk.CTkFont(size=13, weight="bold")
        group_texts = [t(key) for key in self.GROUP_KEYS]
        uniform_width = max(64, max(tab_font.measure(text) for text in group_texts) + 34)
        for btn, text in zip(self.tab_buttons, group_texts):
            btn.configure(text=text, width=uniform_width)
        # inner subtab strips — each button sized to its own label instead
        # of a shared uniform width, same approach _make_scroll_strip's
        # other user (the AI tab's action row) already takes.
        sub_font = ctk.CTkFont(size=12)
        for group_index, (content, canvas, left_btn, right_btn, buttons) in self.subtab_strips.items():
            for leaf, btn in zip(self.GROUP_LEAVES[group_index], buttons):
                label = t(self.LEAF_KEYS[leaf])
                btn.configure(text=label, width=max(70, sub_font.measure(label) + 28))
            self._finalize_scroll_strip(canvas, content, left_btn, right_btn)
        self._style_tab_buttons()
        # Tab widths just changed (language switch) — re-check whether the
        # strip still fits its viewport and keep the active tab in view.
        self._update_tab_scroll_state()
        self._ensure_active_tab_visible()
        self._retranslate_audio_studio_tabs()

        # transcribe tab
        self.quality_label.configure(text=t("quality_label"))
        self.language_label.configure(text=t("language_label"))
        self.sensevoice_box.configure(text=t("sensevoice_label"))
        self.drop_title.configure(text=t("drop_title"))
        self.drop_sub.configure(text=t("drop_sub"))
        self.clear_button.configure(text=t("clear_button"))
        self.open_folder_button.configure(text=t("open_output_folder"))
        self.file_list.configure(label_text=t("double_click_tip"))
        self.quality_button.configure(values=i18n.quality_options(self.ui_lang))
        self.quality_button.set(i18n.quality_display(self.prefs["quality"], self.ui_lang))
        self._equalize_segments(self.quality_button, 88)
        self.language_menu.configure(values=i18n.language_options(self.ui_lang))
        self.language_menu.set(
            i18n.language_display(self.prefs["transcribe_language"], self.ui_lang))
        if self.running:
            self.transcribe_button.configure(text=t("transcribing_button"))
            self.cancel_button.configure(
                text=t("cancelling_button") if self.cancel_button.cget("state") == "disabled"
                else t("cancel_button"))
        else:
            self.transcribe_button.configure(text=t("transcribe_button"))
            self.cancel_button.configure(text=t("cancel_button"))
        for row in self.rows:
            self._render_row_status(row)
        self._render_status_line()

        # live transcription tab
        self.live_language_label.configure(text=t("language_label"))
        self.live_mic_label.configure(text=t("live_mic_label"))
        self.live_level_label.configure(text=t("live_level_label"))
        self.live_filename_label.configure(text=t("live_filename_label"))
        self.live_filename_entry.configure(placeholder_text=t("live_filename_placeholder"))
        self.live_hint_label.configure(text=t("live_hint"))
        self.live_text_hint_label.configure(text=t("live_text_hint"))
        self.live_open_folder_button.configure(text=t("open_output_folder"))
        self.live_draft_button.configure(text=t("live_draft_button"))
        self.live_language_menu.configure(values=i18n.live_language_options(self.ui_lang))
        self.live_language_menu.set(
            i18n.language_display(self.prefs["live_language"], self.ui_lang))
        self._refresh_mic_menu()
        if not self.live_session_started:
            self._show_live_placeholder()
        self._render_live_toggle_button()
        if self.live_status_key is None and not self.sensevoice_available:
            self._set_live_status("live_status_engine_failed", {})
        else:
            self._render_live_status()

        # edit tab
        self.edit_file_label.configure(text=t("edit_file_label"))
        self.edit_open_button.configure(text=t("edit_open_button"))
        self.speed_caption.configure(text=t("player_speed"))
        self.save_button.configure(text=t("save_button"))
        self.edit_open_folder_button.configure(text=t("open_output_folder"))
        self.edit_new_live_button.configure(text=t("edit_new_live_button"))
        self.editor_hint_label.configure(text=t("editor_hint"))
        self.punct_toggle_button.configure(text=t("punct_toggle"))
        self.ts_toggle_button.configure(text=t("timestamps_toggle"))
        self._render_play_button()
        self._refresh_edit_menu()
        self._render_edit_status()

        # AI tab
        self.llm_file_label.configure(text=t("edit_file_label"))
        self.llm_open_button.configure(text=t("edit_open_button"))
        self.llm_mode_label.configure(text=t("llm_mode_label"))
        self.llm_target_label.configure(text=t("llm_translate_to"))
        self.llm_quality_label.configure(text=t("quality_label"))
        self.llm_left_title.configure(text=t("llm_left_title"))
        self.llm_right_title.configure(text=t("llm_right_title"))
        self.llm_save_button.configure(text=t("save_button"))
        self.llm_open_folder_button.configure(text=t("open_output_folder"))
        self.llm_mode_button.configure(values=i18n.llm_mode_options(self.ui_lang))
        self.llm_mode_button.set(
            i18n.llm_mode_display(self.prefs["llm_mode"], self.ui_lang))
        self._equalize_segments(self.llm_mode_button, 80)
        self.llm_target_menu.configure(values=i18n.llm_target_options(self.ui_lang))
        self.llm_target_menu.set(
            i18n.llm_target_display(self.prefs["llm_target"], self.ui_lang))
        self.llm_quality_button.configure(values=i18n.quality_options(self.ui_lang))
        self.llm_quality_button.set(
            i18n.quality_display(self.prefs["llm_quality"], self.ui_lang))
        self._equalize_segments(self.llm_quality_button, 88)
        self.llm_generate_button.configure(
            text=t("cancel_button") if self.llm_running else t("llm_generate"))
        self._refresh_llm_menu()
        self._render_llm_status()
        self._update_llm_target_state()
        self._update_ram_caption()
        # Mode/Target/Quality label widths and the RAM caption text just
        # changed (language switch) — re-check whether the actions row
        # still fits its viewport.
        self._update_strip_overflow(self.llm_actions_canvas, self.llm_mode_button.master,
                                    self.llm_actions_left, self.llm_actions_right)

        # settings tab
        self._retranslate_settings_tab()

    def _update_ram_caption(self):
        if self.sys_ram_gb is None:
            self.llm_ram_caption.configure(text="")
            return
        recommended, close_apps_hint = llm.recommended_quality(
            self.sys_ram_gb, sysinfo.free_ram_gb(),
            sysinfo.free_disk_gb(settings.MODELS_DIR))
        key = "ram_caption_close_apps" if close_apps_hint else "ram_caption"
        self.llm_ram_caption.configure(text=i18n.t(
            self.ui_lang, key,
            ram=f"{self.sys_ram_gb:.0f}",
            recommended=i18n.quality_display(recommended, self.ui_lang)))

    def _on_ui_lang_change(self, value):
        self.ui_lang = "en" if value == "EN" else "zh"
        self.prefs["ui_language"] = self.ui_lang
        settings.save(self.prefs)
        self._retranslate()

    # ======================================================= transcribe tab

    def _on_drop(self, event):
        if self.current_tab != self.LEAF_TRANSCRIBE:
            return
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        self._add_paths(paths)

    def _show_model_info(self):
        messagebox.showinfo(
            i18n.t(self.ui_lang, "model_info_title"),
            i18n.t(self.ui_lang, "model_info_body"))

    def _browse_files(self):
        if self.running:
            return
        paths = filedialog.askopenfilenames(
            title=i18n.t(self.ui_lang, "browse_dialog_title"),
            filetypes=[
                ("Audio & video files",
                 "*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.wma *.aac *.aiff"
                 " *.mp4 *.mkv *.mov *.avi *.webm"),
                ("All files", "*.*"),
            ])
        if paths:
            self._add_paths(paths)

    def _add_paths(self, paths):
        if self.running:
            self._set_status(i18n.t(self.ui_lang, "please_wait_batch"))
            return
        candidates = []
        for path in paths:
            if os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    candidates.extend(os.path.join(root, f) for f in files)
            else:
                candidates.append(path)

        existing = {row["path"] for row in self.rows}
        added = skipped = duplicates = 0
        for path in candidates:
            path = os.path.abspath(path)
            if not is_supported(path):
                skipped += 1
                continue
            if path in existing:
                duplicates += 1
                continue
            existing.add(path)
            self._add_row(path)
            added += 1

        parts = []
        if added:
            parts.append(i18n.added_files_text(added, self.ui_lang))
        if duplicates:
            parts.append(i18n.duplicates_text(duplicates, self.ui_lang))
        if skipped:
            parts.append(i18n.skipped_text(skipped, self.ui_lang))
        self._set_status(
            ". ".join(parts) + "." if parts else i18n.t(self.ui_lang, "no_audio_found"))

    def _add_row(self, path):
        # The scrollable list itself is theme gray86/gray17 — in light mode
        # that's the exact same "gray86" this used to use for rows, so rows
        # were invisible against the list background. Rows need a shade
        # that's clearly different from the container's in BOTH modes.
        frame = ctk.CTkFrame(self.file_list, fg_color=("gray95", "gray24"))
        frame.pack(fill="x", pady=3, padx=2)
        frame.grid_columnconfigure(0, weight=1)
        name_label = ctk.CTkLabel(frame, text=os.path.basename(path), anchor="w")
        name_label.grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=8)
        status_label = ctk.CTkLabel(
            frame, text="", anchor="e", text_color=self.MUTED_TEXT, wraplength=300)
        status_label.grid(row=0, column=1, sticky="e", padx=(0, 8), pady=8)
        row = {
            "path": path, "frame": frame, "name_label": name_label,
            "status_label": status_label, "output": None,
            "status_key": "waiting", "status_detail": {},
        }
        remove_btn = ctk.CTkButton(
            frame, text="✕", width=28, height=24, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT,
            hover_color=("gray75", "gray30"), command=lambda r=row: self._remove_row(r))
        remove_btn.grid(row=0, column=2, padx=(0, 8), pady=8)
        row["remove_btn"] = remove_btn
        # Double-click a finished row to open it in the Edit & Export tab.
        for w in (frame, name_label):
            w.bind("<Double-Button-1>", lambda _e, r=row: self._open_row_in_editor(r))
        self.rows.append(row)
        self._render_row_status(row)

    def _open_row_in_editor(self, row):
        if not row.get("output"):
            return
        self._register_edit_file(row["path"], row["output"])
        entry = next(e for e in self.edit_files if e["audio"] == row["path"])
        self._load_edit_entry(entry)
        self._show_tab(self.LEAF_EDIT)

    def _remove_row(self, row):
        if self.running:
            return
        row["frame"].destroy()
        self.rows.remove(row)

    def _clear_list(self):
        if self.running:
            return
        for row in self.rows:
            row["frame"].destroy()
        self.rows.clear()
        self.progress_bar.set(0)
        self._set_status("")

    def _on_pref_change(self, _value=None):
        self.prefs["quality"] = i18n.quality_key_for_display(
            self.quality_button.get(), self.ui_lang)
        self.prefs["transcribe_language"] = i18n.language_key_for_display(
            self.language_menu.get(), self.ui_lang)
        self.prefs["sensevoice_preferred"] = bool(self.sensevoice_var.get())
        settings.save(self.prefs)

    def _start_transcription(self):
        if self.running:
            return
        if not self.rows:
            messagebox.showinfo(
                i18n.t(self.ui_lang, "add_files_first_title"),
                i18n.t(self.ui_lang, "add_files_first_message"))
            return
        self._on_pref_change()
        quality = self.prefs["quality"]
        model_size = transcriber.QUALITY_MODELS[quality]
        if not transcriber.model_is_downloaded(model_size):
            if not self._confirm_model_download(
                quality, transcriber.QUALITY_RAM_GB[quality],
                transcriber.MODEL_DOWNLOAD_MB[model_size] / 1024,
                transcriber.recommended_quality(self.sys_ram_gb),
            ):
                return
        # With the SenseVoice box checked, jobs in its 5 languages will pull
        # its ~900 MB engine mid-batch — run the same disk gate up front
        # rather than letting that download start as a surprise.
        if self.prefs["sensevoice_preferred"] and not self._confirm_sensevoice_download():
            return
        for row in self.rows:
            row["output"] = None
            row["status_key"], row["status_detail"] = "waiting", {}
            self._render_row_status(row)
        code = self.prefs["transcribe_language"]
        jobs = [Job(i, row["path"]) for i, row in enumerate(self.rows)]
        self.worker = TranscriberWorker(
            jobs=jobs, quality=self.prefs["quality"],
            language_code=None if code == "auto" else code,
            sensevoice_preferred=self.prefs["sensevoice_preferred"],
            output_folder=settings.transcriptions_folder(), events=self.events,
            traditional_chinese=bool(self.prefs.get("chinese_traditional")))
        self._set_running(True)
        self.progress_bar.set(0)
        self.worker.start()

    def _cancel_transcription(self):
        if self.worker:
            self.worker.cancel()
            self.cancel_button.configure(
                state="disabled", text=i18n.t(self.ui_lang, "cancelling_button"))
            self._set_status(i18n.t(self.ui_lang, "cancelling_status"))

    def _set_running(self, running):
        self.running = running
        state = "disabled" if running else "normal"
        self.quality_button.configure(state=state)
        self.language_menu.configure(state=state)
        self.sensevoice_box.configure(
            state="disabled" if (running or not self.sensevoice_available) else "normal")
        self.clear_button.configure(state=state)
        for row in self.rows:
            row["remove_btn"].configure(state=state)
        if running:
            self.transcribe_button.configure(
                state="disabled", text=i18n.t(self.ui_lang, "transcribing_button"))
            self.cancel_button.configure(
                state="normal", text=i18n.t(self.ui_lang, "cancel_button"))
            self.cancel_button.grid()
        else:
            self.transcribe_button.configure(
                state="normal", text=i18n.t(self.ui_lang, "transcribe_button"))
            self.cancel_button.grid_remove()

    # ------------------------------------------------------ event polling

    def _poll_events(self):
        tokens = []  # batch streamed LLM text into one insert per poll
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "llm_token":
                    tokens.append(event[1])
                    continue
                if tokens:
                    self._append_llm_output("".join(tokens))
                    tokens = []
                self._handle_event(event)
        except queue.Empty:
            pass
        if tokens:
            self._append_llm_output("".join(tokens))
        self.after(POLL_MS, self._poll_events)

    def _handle_event(self, event):
        kind = event[0]
        if kind == "line":
            _, key, detail = event
            self.status_key, self.status_detail = key, detail
            self._render_status_line()
            # "loading" follows a whisper download completing; "transcribing"
            # follows a SenseVoice one (see _try_sensevoice) — moments the
            # Settings model list may have just gone stale.
            if key in ("loading", "transcribing"):
                self._refresh_models_if_visible()
        elif kind == "job":
            _, index, key, detail, pct = event
            if index >= len(self.rows):
                return
            row = self.rows[index]
            row["status_key"], row["status_detail"] = key, detail
            self._render_row_status(row)
            self._update_overall_progress(index, pct)
        elif kind == "saved":
            _, index, output_path = event
            if index < len(self.rows):
                self.rows[index]["output"] = output_path
                self._register_edit_file(self.rows[index]["path"], output_path)
                if self.current_tab == self.LEAF_EDIT:
                    self._maybe_autoload_edit()
                elif self.current_tab == self.LEAF_AI:
                    self._maybe_autoload_llm()
        elif kind == "finished":
            self._set_running(False)
            self.worker = None
            self._refresh_models_if_visible()
            done = sum(1 for r in self.rows if r["status_key"].startswith("done"))
            self.progress_bar.set(1 if done == len(self.rows) and done
                                  else self.progress_bar.get())
            if any(r["status_key"] == "failed_write" for r in self.rows):
                self._offer_output_folder_fix()
        elif kind == "speed_ready":
            if self.edit_status_key == "player_preparing":
                self._set_edit_status(None, "")
        elif kind == "speed_progress":
            _, speed, frac = event
            self._set_edit_status(
                "player_preparing", {"speed": _speed_label(speed), "pct": int(frac * 100)})
        elif kind == "audio_loaded":
            self._on_audio_loaded(event[1], event[2])
        elif kind == "audio_reloaded":
            self._on_audio_reloaded(event[1])
        elif kind == "find_similar":
            self._on_find_similar_event(event[1], event[2])
        elif kind == "busy_done":
            _, on_done, result, error = event
            self._close_busy_dialog()
            on_done(result, error)
        elif kind == "live_status":
            _, key, detail = event
            if key == "ready":
                # Preload finished (or found the model already loaded) —
                # nothing to report; revert to the normal idle placeholder,
                # but only if a real recording session hasn't since taken
                # over the status line/text area.
                if not self.live_running:
                    self._set_live_status(None, "")
                    self._show_live_placeholder()
                self._refresh_models_if_visible()
            else:
                text_key = f"live_status_{key}"
                self._set_live_status(text_key, detail)
                if key == "recording":  # a first-session download just finished
                    self._refresh_models_if_visible()
                # A first-time (or first-this-session) model load can take
                # real time even with nothing to download — surface it in
                # the big text area too, not just the small status line, so
                # it's not mistaken for the app being frozen.
                if key in ("loading", "downloading"):
                    self._write_live_text(i18n.t(self.ui_lang, text_key, **(detail or {})))
                elif key == "recording":
                    self._write_live_text("")
                    # The field was left blank (auto-name requested) — now
                    # that the worker has settled on one, show it. Still
                    # disabled/unmodifiable; this is display-only, matching
                    # what a custom name already does for the rest of the
                    # session.
                    stem = (detail or {}).get("stem")
                    if stem and not self.live_filename_entry.get().strip():
                        self.live_filename_entry.configure(state="normal")
                        self.live_filename_entry.delete(0, "end")
                        self.live_filename_entry.insert(0, stem)
                        self.live_filename_entry.configure(state="disabled")
                elif key == "save_failed":
                    self._offer_output_folder_fix()
                elif key in ("idle", "idle_cleared"):
                    # The only reminder a long-silent session is still going
                    # once you've switched away from this tab — see
                    # _style_tab_buttons. Purely a color change on the tab
                    # button, so this never touches layout/width.
                    self.live_tab_idle = (key == "idle")
                    self._style_tab_buttons()
        elif kind == "live_text":
            _, committed, preview, _lang = event
            self._set_live_text(committed, preview)
        elif kind == "live_saved":
            _, audio_path, txt_path = event
            self._register_live_transcript(audio_path, txt_path)
        elif kind == "live_draft":
            # Paragraphs a draft (or the final) save just appended to the
            # session's transcript file. Only matters when the editor has
            # that very session open — matched on live_draft_path (stable
            # for the session), not txt (repoints to an edited copy once
            # the user saves one — see _register_edit_file — so comparing
            # against txt would stop matching after the very first save).
            # Accumulated + surfaced as a click-to-add notice.
            _, path, parts = event
            if self.edit_current and self.edit_current.get("live_draft_path") == path:
                self.live_pending_path = path
                self.live_pending_appends.extend(parts)
                self.edit_new_live_button.grid()
        elif kind == "live_stopped":
            self._on_live_stopped()
        elif kind == "llm_status":
            _, key, detail = event
            self.llm_status_key, self.llm_status_detail = key, detail
            self._render_llm_status()
            # A first-time model download/load can take a while — surface
            # it in the big output panel too, not just the small status
            # line, so it's obvious the app is working rather than stuck.
            # "llm_reset" (emitted right before real generation starts)
            # already clears the output panel, so this disappears on its
            # own the moment there's something real to show instead.
            if key in ("llm_downloading", "llm_loading"):
                self._write_llm_output_notice(i18n.t(self.ui_lang, key, **(detail or {})))
            if key == "llm_loading":  # follows a first-run LLM download
                self._refresh_models_if_visible()
        elif kind == "llm_reset":
            self._clear_llm_output()
        elif kind == "llm_finished":
            ok = event[1] if len(event) > 1 else False
            self._set_llm_running(False)
            self.llm_worker = None
            self.llm_output.configure(state="normal")
            if ok:
                self._save_llm_output()  # always save a copy once generation succeeds
        elif kind == "model_dl":
            self._on_model_dl_event(event[1], event[2], event[3])
        elif kind == "update_check":
            self._on_update_check_result(event[1])
        elif kind in ("record_started", "record_stopped"):
            self._on_audio_record_event(kind, *event[1:])

    def _render_row_status(self, row):
        text = i18n.job_status_text(self.ui_lang, row["status_key"], row["status_detail"])
        color = self.MUTED_TEXT
        if row["status_key"].startswith("done"):
            color = "#4caf50"
        elif row["status_key"].startswith("failed"):
            color = "#e57373"
        elif row["status_key"] == "transcribing":
            color = ("gray10", "gray90")
        row["status_label"].configure(text=text, text_color=color)

    def _render_status_line(self):
        if self.status_key is None:
            return
        detail = dict(self.status_detail or {})
        if "quality" in detail:
            detail["quality"] = i18n.quality_display(detail["quality"], self.ui_lang)
        self.status_line.configure(
            text=i18n.t(self.ui_lang, f"line_{self.status_key}", **detail))

    def _update_overall_progress(self, index, pct):
        total = len(self.rows)
        if not total:
            return
        finished = sum(1 for r in self.rows
                       if r["status_key"] not in ("waiting", "transcribing"))
        current = (pct or 0) / 100 if pct is not None else 0
        self.progress_bar.set(min(1.0, (finished + current) / total))

    def _open_output_folder(self, folder=None):
        folder = folder or settings.transcriptions_folder()
        try:
            os.makedirs(folder, exist_ok=True)
            _open_path(folder)
        except Exception:
            settings.log_exception(f"Open output folder failed: {folder}")

    def _confirm_model_download(self, quality, required_ram_gb, download_gb,
                                 recommended_key):
        """Runs once, right before a model that isn't downloaded yet would
        be fetched and loaded. Blocks (with a dialog) only when the PC looks
        genuinely too tight for it — never nags for a model already sized
        fine for this hardware. Returns True to proceed, False to back out."""
        free_gb = sysinfo.free_disk_gb(settings.MODELS_DIR)
        needed_disk = download_gb * 1.2 + 0.2
        if free_gb is not None and free_gb < needed_disk:
            messagebox.showerror(
                i18n.t(self.ui_lang, "capability_low_disk_title"),
                i18n.t(self.ui_lang, "capability_low_disk_message",
                       required=f"{needed_disk:.1f}", free=f"{free_gb:.1f}",
                       folder=settings.MODELS_DIR))
            return False

        if self.sys_ram_gb is not None and self.sys_ram_gb < required_ram_gb:
            ram = f"{self.sys_ram_gb:.1f}"
            required = f"{required_ram_gb:.1f}"
            quality_name = i18n.quality_display(quality, self.ui_lang)
            if quality == recommended_key:
                return messagebox.askyesno(
                    i18n.t(self.ui_lang, "capability_low_ram_title"),
                    i18n.t(self.ui_lang, "capability_low_ram_message_min",
                           ram=ram, required=required, quality=quality_name))
            return messagebox.askyesno(
                i18n.t(self.ui_lang, "capability_low_ram_title"),
                i18n.t(self.ui_lang, "capability_low_ram_message",
                       ram=ram, required=required, quality=quality_name,
                       recommended=i18n.quality_display(recommended_key, self.ui_lang)))
        return True

    def _confirm_sensevoice_download(self):
        """Same pre-download gate as whisper's, for the first SenseVoice
        fetch (~900 MB): silently passes on a machine with room, blocks
        with a dialog when disk is genuinely too tight. required_ram_gb=0
        skips _confirm_model_download's RAM branch (and the whisper-quality
        wording that goes with it) — SenseVoice runs comfortably anywhere
        whisper does; only disk can realistically block it.

        Also passes silently if another tab is already mid-fetch (single-
        flight in get_sensevoice_model means this caller will just block on
        that download and reuse its result, never start a second one) —
        otherwise the second tab would re-ask "download now?" for work
        that's already running."""
        if transcriber.sensevoice_is_downloaded() or transcriber.sensevoice_load_in_progress():
            return True
        quality = self.prefs["quality"]
        return self._confirm_model_download(
            quality, 0, transcriber.SENSEVOICE_DOWNLOAD_MB / 1024, quality)

    def _set_status(self, text):
        self.status_key = None
        self.status_line.configure(text=text)

    # ============================================================ live tab

    def _on_live_pref_change(self, _value=None):
        self.prefs["live_language"] = i18n.live_language_key_for_display(
            self.live_language_menu.get(), self.ui_lang)
        mic = self.live_mic_menu.get()
        self.prefs["live_mic_device"] = \
            "" if mic == i18n.t(self.ui_lang, "mic_default") else mic
        settings.save(self.prefs)

    def _refresh_mic_menu(self):
        """Re-enumerates input devices (cheap) and reselects the remembered
        one; a device that has vanished falls back to the default entry
        without erasing the preference — plugging it back in restores it."""
        default_label = i18n.t(self.ui_lang, "mic_default")
        devices = live_transcription.list_input_devices()
        self.live_mic_menu.configure(values=[default_label] + devices)
        preferred = self.prefs.get("live_mic_device", "")
        self.live_mic_menu.set(preferred if preferred in devices else default_label)

    def _maybe_preload_sensevoice(self):
        """First time the Live tab is opened, warm up SenseVoice in the
        background — load-from-disk only; a model that was never downloaded
        stays that way until an explicit Start Recording (see
        SenseVoicePreloader for why). Once per session — LiveTranscriber's
        own load is single-flight against this (see transcriber.py), so
        it's safe even if the user hits Start before this finishes."""
        if self.live_preload_started or not self.sensevoice_available:
            return
        self.live_preload_started = True
        live_transcription.SenseVoicePreloader(self.events).start()

    def _toggle_live_recording(self):
        if self.live_running:
            self._stop_live_recording()
        else:
            self._start_live_recording()

    def _start_live_recording(self):
        if self.live_running or not self.sensevoice_available:
            return
        custom_name = self.live_filename_entry.get().strip()
        error_key = _validate_live_filename(custom_name)
        if error_key:
            self._show_live_filename_error(error_key)
            return
        self._hide_live_filename_error()
        # First-ever session downloads the engine (~900 MB) — the tab's
        # preload deliberately doesn't (see SenseVoicePreloader), so this
        # is the one place the Live tab can trigger it. Gate on disk room.
        if not self._confirm_sensevoice_download():
            return
        code = self.prefs["live_language"]
        language = "" if code == "auto" else code
        self.live_session_started = True
        self.live_running = True
        self.live_tab_idle = False
        self._write_live_text("")
        self.live_language_menu.configure(state="disabled")
        self.live_mic_menu.configure(state="disabled")
        # Locked for the life of the session (matches every other per-
        # session choice — device, language): if left blank, the entry
        # still gets filled in, once the worker settles on one, with
        # whatever automatic name it ended up using (see _handle_event's
        # "recording" case) — never editable, just shown.
        self.live_filename_entry.configure(state="disabled")
        self._render_live_toggle_button()
        self._style_tab_buttons()
        # No status set here — the worker's own events ("loading",
        # "downloading", "mic_failed", or "recording") are the only source
        # of truth for what's actually happening. Claiming "Recording…"
        # immediately would be a lie whenever the model still needs to
        # load (which can take real time even when nothing needs
        # downloading) or the mic fails to open.
        self.live_worker = live_transcription.LiveTranscriber(
            language, self.events,
            device_name=self.prefs.get("live_mic_device", ""),
            traditional_chinese=bool(self.prefs.get("chinese_traditional")),
            custom_stem=custom_name or None)
        self.live_worker.start()
        self.live_draft_button.configure(state="normal")

    def _show_live_filename_error(self, error_key):
        self.live_filename_error_label.configure(text=i18n.t(self.ui_lang, error_key))
        self.live_filename_error_label.grid()

    def _hide_live_filename_error(self):
        self.live_filename_error_label.grid_remove()

    def _save_live_draft(self):
        if self.live_running and self.live_worker:
            self.live_worker.request_partial_save()

    def _pull_in_live_content(self):
        """Appends the pending live-draft paragraphs to the end of the open
        editor buffer (with their clickable [mm:ss] markers), leaving the
        user's cursor, scroll position, and undo history alone. The file on
        disk already contains this text — this only brings the buffer up to
        date — so the loaded-text baseline advances too: pulling in isn't a
        user edit and must not, by itself, read as unsaved changes."""
        parts, self.live_pending_appends = self.live_pending_appends, []
        path, self.live_pending_path = self.live_pending_path, None
        self.edit_new_live_button.grid_remove()
        if not parts or not self.edit_current or self.edit_current.get("live_draft_path") != path:
            return
        add = "\n\n".join(
            f"{timestamps.format_marker(s)} {t}" if s is not None else t
            for s, t in parts)
        current = self.editor.get("1.0", "end-1c")
        joiner = "\n\n" if current.strip() else ""
        self.editor.insert("end", joiner + add)
        self._apply_timestamp_markup()
        if self._edit_loaded_text is not None:
            self._edit_loaded_text = self._edit_loaded_text + joiner + add
        # The recording's WAV keeps growing in the background too — without
        # this, the player stays frozen at whatever length was on disk when
        # this entry was first opened, so a marker in the text we just
        # pulled in could point past audio the player doesn't know exists
        # yet. Only when the player currently has THIS entry's own audio
        # loaded (not some unrelated file the user has since opened).
        audio = self.edit_current.get("audio")
        if audio and self.player.loaded_path == audio:
            self._reload_live_audio(audio)

    def _reload_live_audio(self, audio):
        def work():
            ok = False
            try:
                ok = self.player.reload(audio)
            except Exception:
                settings.log_exception(f"Live audio reload failed: {audio}")
            self.events.put(("audio_reloaded", ok))
        threading.Thread(target=work, daemon=True).start()

    def _on_audio_reloaded(self, ok):
        if not ok:
            return  # best-effort refresh; the previous, shorter audio stays usable
        self.time_label.configure(
            text=f"{_fmt_time(self.player.get_time())} / {_fmt_time(self.player.duration)}")
        self.position_slider.set(self.player.get_fraction())
        self._render_play_button()

    def _stop_live_recording(self):
        if self.live_worker:
            self.live_worker.stop()
        self.live_toggle_button.configure(state="disabled")
        self.live_draft_button.configure(state="disabled")
        # Distinguish "wrapping up a real session" from "still stuck on the
        # model download/load step" — the latter can run for minutes (or a
        # ModelScope fallback after Hugging Face fails) with no way to
        # interrupt it mid-call, and plain "Finishing up…" reads as stuck
        # when the big text area still shows the old "Loading SenseVoice
        # model…" message underneath it with nothing having changed.
        if self.live_status_key in ("live_status_loading", "live_status_downloading"):
            self._set_live_status("live_status_stopping_load", {})
            self._write_live_text(i18n.t(self.ui_lang, "live_status_stopping_load"))
        else:
            self._set_live_status("live_status_finalizing", {})

    def _render_live_toggle_button(self):
        if self.live_running:
            self.live_toggle_button.configure(
                text=i18n.t(self.ui_lang, "live_stop_button"),
                state="normal", fg_color="#8a3535", hover_color="#a04040")
        else:
            theme = ctk.ThemeManager.theme["CTkButton"]
            self.live_toggle_button.configure(
                text=i18n.t(self.ui_lang, "live_start_button"),
                state="normal" if self.sensevoice_available else "disabled",
                fg_color=theme["fg_color"], hover_color=theme["hover_color"])

    def _write_live_text(self, text):
        self.live_text.configure(state="normal")
        self.live_text.delete("1.0", "end")
        self.live_text.insert("1.0", text)
        self.live_text.see("end")
        self.live_text.configure(state="disabled")

    def _show_live_placeholder(self):
        self._write_live_text(i18n.t(self.ui_lang, "live_placeholder"))

    def _set_live_text(self, committed, preview):
        # Committed chunks are paragraphs now (one per pause-commit, each
        # carrying a timestamp in the saved sidecar); the still-changing
        # preview renders as a trailing paragraph-in-progress.
        full = committed
        if preview:
            full = f"{committed}\n\n{preview}" if committed else preview
        self._write_live_text(full)

    def _set_live_status(self, key, detail):
        self.live_status_key = key
        self.live_status_detail = detail if isinstance(detail, dict) else {}
        self._render_live_status()

    def _render_live_status(self):
        if self.live_status_key is None:
            self.live_status_line.configure(text="")
            return
        self.live_status_line.configure(
            text=i18n.t(self.ui_lang, self.live_status_key, **(self.live_status_detail or {})))

    def _register_live_transcript(self, audio_path, txt_path):
        # Reuses the same entry shape recorded-file transcripts use — when
        # both halves saved, this is literally the same call _handle_event's
        # "saved" case makes for a batch job, so the live recording plays
        # back in the Edit tab exactly like any other file.
        if audio_path:
            self._register_edit_file(audio_path, txt_path, is_live=True)
        elif txt_path:
            # Draft saves re-announce the same transcript as it grows —
            # update the existing entry instead of adding a duplicate row.
            # Matched on live_draft_path (stable), not txt — the entry's
            # txt gets repointed to an edited copy once the user saves one
            # (see _register_edit_file/_save_edit), but this raw draft path
            # never changes for the life of the session.
            for entry in self.edit_files:
                if entry.get("live_draft_path") == txt_path:
                    self._refresh_edit_menu()
                    return
            stem = os.path.splitext(os.path.basename(txt_path))[0]
            self.edit_files.append(
                {"label": self._unique_label(stem), "audio": None, "txt": txt_path,
                 "live_draft_path": txt_path})
            self._refresh_edit_menu()
        else:
            return
        self._refresh_llm_menu()
        if self.current_tab == self.LEAF_EDIT:
            self._maybe_autoload_edit()
        elif self.current_tab == self.LEAF_AI:
            self._maybe_autoload_llm()

    def _on_live_stopped(self):
        self.live_running = False
        self.live_tab_idle = False
        self.live_worker = None
        state = "normal" if self.sensevoice_available else "disabled"
        self.live_language_menu.configure(state=state)
        self.live_mic_menu.configure(state=state)
        self.live_level_bar.set(0)
        self.live_draft_button.configure(state="disabled")
        # Cleared rather than left showing the just-finished session's name
        # — reusing it for the next session (intentionally or by not
        # noticing) would still work fine (collision handling just appends
        # "(2)"), but an unclearing field risks looking like "still the
        # same session" rather than "ready for a new one."
        self.live_filename_entry.configure(state=state)
        self.live_filename_entry.delete(0, "end")
        self._hide_live_filename_error()
        self._render_live_toggle_button()
        self._style_tab_buttons()

    # ============================================================= edit tab

    def _register_edit_file(self, audio_path, txt_path, is_live=False):
        label = os.path.basename(audio_path)
        for entry in self.edit_files:
            if entry["audio"] == audio_path:
                if is_live:
                    # live_draft_path is the stable raw-draft identity used
                    # to match incoming content (see _handle_event's
                    # live_draft branch) — always the same path for this
                    # session, safe to re-set every call. txt is what a
                    # reload of this entry would show, though: once the
                    # user has an edited copy, a later live_saved firing
                    # again (new paragraphs landed) must NOT silently
                    # revert it back to the raw, unedited draft — that new
                    # content is surfaced through the pending-append
                    # notice instead, not by clobbering the pointer.
                    entry["live_draft_path"] = txt_path
                    if not entry.get("edited_path"):
                        entry["txt"] = txt_path
                else:
                    entry["txt"] = txt_path
                break
        else:
            entry = {"label": label, "audio": audio_path, "txt": txt_path}
            if is_live:
                entry["live_draft_path"] = txt_path
            self.edit_files.append(entry)
        self._refresh_edit_menu()

    def _unique_label(self, base):
        labels = {e["label"] for e in self.edit_files}
        if base not in labels:
            return base
        i = 2
        while f"{base} ({i})" in labels:
            i += 1
        return f"{base} ({i})"

    def _refresh_edit_menu(self):
        if not self.edit_files:
            self.edit_file_menu.configure(values=[i18n.t(self.ui_lang, "edit_no_file")])
            self.edit_file_menu.set(i18n.t(self.ui_lang, "edit_no_file"))
            return
        values = [e["label"] for e in self.edit_files]
        self.edit_file_menu.configure(values=values)
        if self.edit_current and self.edit_current in self.edit_files:
            self.edit_file_menu.set(self.edit_current["label"])
        else:
            self.edit_file_menu.set(values[-1])

    def _on_edit_file_selected(self, label):
        entry = next((e for e in self.edit_files if e["label"] == label), None)
        if entry:
            self._load_edit_entry(entry)

    def _open_file_for_edit(self):
        path = filedialog.askopenfilename(
            title=i18n.t(self.ui_lang, "edit_pick_dialog"),
            initialdir=self._open_dialog_initialdir(),
            filetypes=[
                ("Audio & video files",
                 "*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.wma *.aac *.aiff"
                 " *.mp4 *.mkv *.mov *.avi *.webm"),
                ("All files", "*.*"),
            ])
        if not path:
            return
        path = os.path.abspath(path)
        txt = self._find_transcript_for(path)
        entry = next((e for e in self.edit_files if e["audio"] == path), None)
        if entry is None:
            entry = {"label": self._unique_label(os.path.basename(path)),
                     "audio": path, "txt": txt}
            self.edit_files.append(entry)
        else:
            entry["txt"] = txt
        self._refresh_edit_menu()
        self._load_edit_entry(entry)

    def _open_dialog_initialdir(self):
        """Start 'Open file' dialogs in the Transcriptions folder — transcripts
        and exported copies all end up there, so it's usually where the file
        the user wants to reopen already is."""
        folder = settings.transcriptions_folder()
        return folder if os.path.isdir(folder) else None

    def _find_transcript_for(self, audio_path):
        stem = os.path.splitext(os.path.basename(audio_path))[0]
        for folder in (settings.transcriptions_folder(), os.path.dirname(audio_path)):
            for ext in (".docx", ".txt"):
                candidate = os.path.join(folder, stem + ext)
                if os.path.isfile(candidate):
                    return candidate
        return None

    def _load_edit_entry(self, entry):
        self.edit_current = entry
        self._stop_play()
        self.speed_menu.set(_speed_label(1.0))
        # A fresh load reads the file from disk, which already includes any
        # live-draft paragraphs waiting in the pending buffer — keeping
        # them would append the same text twice on the next pull-in.
        self.live_pending_appends = []
        self.live_pending_path = None
        self.edit_new_live_button.grid_remove()

        # load transcript text (immediately)
        text, found = "", True
        if entry["txt"] and os.path.isfile(entry["txt"]):
            try:
                text = docx_export.read_transcript(entry["txt"])
            except Exception:
                settings.log_exception(f"Read transcript failed: {entry['txt']}")
                text = ""
        else:
            found = False
        # Timestamps: a file saved with markers already carries them in its
        # text; otherwise the transcript's sidecar (if any) supplies the
        # paragraph times and the markers get inserted here for display.
        if text and not timestamps.has_markers(text):
            times = timestamps.load_sidecar(entry["txt"]) if entry["txt"] else None
            if times:
                text = timestamps.insert_markers(text, times)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self._apply_timestamp_markup()
        self._edit_loaded_text = self.editor.get("1.0", "end-1c")

        # load audio on a worker thread (decoding can take a moment)
        self._set_player_enabled(False)
        self.time_label.configure(text="00:00 / 00:00")
        self.position_slider.set(0)
        self.edit_file_menu.set(entry["label"])
        audio = entry["audio"]
        if not audio:  # transcript-only entry (opened in the AI tab)
            self._set_edit_status(None, "")
            return
        self._set_edit_status(None, "…")

        def work():
            ok = True
            try:
                self.player.load(audio)
            except Exception:
                settings.log_exception(f"Audio load failed: {audio}")
                ok = False
            self.events.put(("audio_loaded", ok, found))

        threading.Thread(target=work, daemon=True).start()

    def _on_audio_loaded(self, ok, transcript_found):
        if not ok:
            self._set_player_enabled(False)
            self._set_edit_status("audio_load_failed", {})
            return
        self._set_player_enabled(True)
        self.time_label.configure(text=f"00:00 / {_fmt_time(self.player.duration)}")
        self.position_slider.set(0)
        self._render_play_button()
        if not transcript_found:
            self._set_edit_status("no_transcript_found", {})
        else:
            self._set_edit_status(None, "")

    def _set_player_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for w in (self.play_button, self.stop_button, self.position_slider,
                  self.speed_menu, self.save_button):
            w.configure(state=state)
        # Save stays usable even without audio if there is text to save.
        self.save_button.configure(state="normal")

    def _toggle_play(self):
        if self.player.loaded_path is None:
            return
        try:
            if self.player.is_playing:
                self.player.pause()
            else:
                self.player.play()
        except Exception:
            settings.log_exception("Playback failed:")
            self._set_edit_status("audio_load_failed", {})
        self._render_play_button()

    def _stop_play(self):
        try:
            self.player.stop()
        except Exception:
            pass
        self.position_slider.set(0)
        self.time_label.configure(text=f"00:00 / {_fmt_time(self.player.duration)}")
        self._render_play_button()

    def _render_play_button(self):
        key = "player_pause" if self.player.is_playing else "player_play"
        self.play_button.configure(text=i18n.t(self.ui_lang, key))
        self.stop_button.configure(text=i18n.t(self.ui_lang, "player_stop"))

    def _on_seek(self, value):
        if self.player.loaded_path is not None:
            self.player.seek_fraction(float(value))
            self.time_label.configure(
                text=f"{_fmt_time(self.player.get_time())} / {_fmt_time(self.player.duration)}")

    def _on_speed_change(self, label):
        speed = float(label.rstrip("×"))
        self.player.set_speed(speed)
        if speed != 1.0 and self.player.loaded_path is not None:
            # If the buffer must be prepared, show a brief note.
            if self.player._cache.get(speed) is None:
                self._set_edit_status(
                    "player_preparing", {"speed": _speed_label(speed), "pct": 0})

    def _tick_player(self):
        if self.current_tab == self.LEAF_LIVE and self.live_running and self.live_worker:
            # Rough perceptual scaling: speech RMS sits around 0.02–0.2, so
            # ×12 maps quiet speech near 1/4 bar and normal speech near full.
            self.live_level_bar.set(min(1.0, self.live_worker.level * 12))
        if self.current_tab == self.LEAF_EDIT and self.player.loaded_path is not None:
            if self.player.is_playing:
                self.position_slider.set(self.player.get_fraction())
                self.time_label.configure(
                    text=f"{_fmt_time(self.player.get_time())} / {_fmt_time(self.player.duration)}")
            elif self.play_button.cget("text") == i18n.t(self.ui_lang, "player_pause"):
                # Playback ended on its own.
                self._render_play_button()
        if self.current_tab == self.LEAF_AUDIO_RECORD and self.audio_recorder is not None:
            self.arec_timer_label.configure(
                text=_fmt_time(self.audio_recorder.elapsed_seconds))
            self.arec_level_bar.set(min(1.0, self.audio_recorder.level * 12))
            self._redraw_record_waveform()
        if self.current_tab == self.LEAF_AUDIO_EDIT and self.audio_clip is not None:
            if self.audio_player.is_playing and self._play_until is not None \
                    and (self._preview_offset_s + self.audio_player.get_time()) >= self._play_until:
                # Reached the end of a selection-limited Play/Preview —
                # stop exactly there instead of continuing into whatever
                # comes after the selection.
                self.audio_player.pause()
                self._play_until = None
                self._clear_audio_preview()
                self._render_audio_edit_play_button()
                self._redraw_waveform()
            elif self.audio_player.is_playing:
                offset = self._preview_offset_s if self.audio_preview is not None else 0.0
                self.aedit_time_label.configure(
                    text=f"{_fmt_time(offset + self.audio_player.get_time())} / "
                         f"{_fmt_time(self.audio_clip.duration)}")
                self._update_playhead_line()
            elif self.aedit_play_button.cget("text") == i18n.t(self.ui_lang, "player_pause"):
                # Playback ended on its own. A Preview deliberately stays
                # up (amber tint, still loaded) after this — reaching the
                # end of a preview is "you finished listening," not "you
                # decided to discard it"; Player.play() already rewinds
                # to the start on the next click, so this can just be
                # replayed. Apply/Revert (or starting a new selection)
                # are the only things that end a Preview now.
                self._play_until = None
                self._render_audio_edit_play_button()
        self.after(TICK_MS, self._tick_player)

    def _save_edit(self):
        # Saving with live-draft text still pending would produce an edited
        # copy that's missing paragraphs the session already transcribed —
        # pull them into the buffer first, so what's saved is complete.
        if (self.live_pending_appends and self.edit_current
                and self.edit_current.get("live_draft_path") == self.live_pending_path):
            self._pull_in_live_content()
        raw = self.editor.get("1.0", "end").rstrip("\n")
        if not raw.strip():
            self._set_edit_status("nothing_to_save", {})
            return
        # Toggle ON: the saved copy keeps the visible [mm:ss] markers.
        # Toggle OFF: they're stripped from the document. Either way the
        # sidecar written below preserves the times, so reopening the copy
        # (or flipping the toggle later) loses nothing.
        clean, times = timestamps.parse_marked_text(raw)
        has_times = any(t is not None for t in times)
        text = raw if (has_times and self.timestamps_visible) else clean
        if self.edit_current:
            stem = os.path.splitext(self.edit_current["label"])[0] + " (edited)"
        else:
            stem = "transcript (edited)"
        is_live = bool(self.edit_current and self.edit_current.get("live_draft_path"))
        try:
            fallback_used = False
            if is_live:
                # A live session's edited copy is one evolving file, kept
                # in place across every save (with a single-backup safety
                # net) rather than a fresh numbered file each time — unlike
                # a regular transcript, there's no separate stable
                # "original" this needs to protect: the draft itself is the
                # working document, so version-per-save mostly adds
                # clutter without a matching safety benefit. Path is picked
                # once and reused; word_available() isn't re-checked on
                # later saves so a session can't straddle two file kinds.
                if not self.edit_current.get("edited_path"):
                    self.edit_current["edited_path"] = docx_export.edited_copy_path(
                        settings.transcriptions_folder(), stem)
                path = self.edit_current["edited_path"]
                try:
                    docx_export.save_transcript_at(text, path)
                    kind = "docx" if path.lower().endswith(".docx") else "txt"
                except (PermissionError, OSError):
                    # Overwriting in place failed — most likely the file is
                    # currently open elsewhere (Word holds a .docx locked
                    # while it's open). Falling straight to save_failed
                    # would lose this edit until the user notices, closes
                    # Word, and retries by hand — instead, save as a new
                    # file (the same numbered-naming rule a regular save
                    # uses) and adopt it as the session's edited copy going
                    # forward: the next save just overwrites THIS one in
                    # place, no different from any other session.
                    settings.log_exception(
                        "Live edited copy is locked (open elsewhere?),"
                        " saving a new copy instead:")
                    path, kind = docx_export.save_transcript(
                        text, settings.transcriptions_folder(), stem)
                    self.edit_current["edited_path"] = path
                    fallback_used = True
            else:
                path, kind = docx_export.save_transcript(
                    text, settings.transcriptions_folder(), stem)
            if has_times:
                timestamps.save_sidecar(path, times)
            if fallback_used:
                self._set_edit_status("saved_locked_fallback", {"path": path})
            else:
                self._set_edit_status("saved_docx" if kind == "docx" else "saved_txt",
                                      {"path": path})
            if self.edit_current:
                # Point this entry at the edited copy so anything that loads
                # it next — the AI tab's autoload, reopening it here later —
                # picks up the user's edits instead of the original text.
                self.edit_current["txt"] = path
                if self.llm_current is self.edit_current and not self.llm_running:
                    self._load_llm_entry(self.llm_current)
        except (PermissionError, OSError):
            settings.log_exception("Save edit failed:")
            self._set_edit_status("save_failed", {})
            self._offer_output_folder_fix()
        except Exception:
            settings.log_exception("Save edit failed:")
            self._set_edit_status("save_failed", {})

    def _set_edit_status(self, key, detail):
        self.edit_status_key = key
        self.edit_status_detail = detail if isinstance(detail, dict) else {}
        if key is None:
            self.edit_status_line.configure(text=detail if isinstance(detail, str) else "")
        else:
            self._render_edit_status()

    def _render_edit_status(self):
        if self.edit_status_key is None:
            return
        self.edit_status_line.configure(
            text=i18n.t(self.ui_lang, self.edit_status_key, **(self.edit_status_detail or {})))

    # ============================================================== AI tab

    def _on_llm_pref_change(self, _value=None):
        self.prefs["llm_mode"] = i18n.llm_mode_key_for_display(
            self.llm_mode_button.get(), self.ui_lang)
        self.prefs["llm_target"] = i18n.llm_target_key_for_display(
            self.llm_target_menu.get(), self.ui_lang)
        self.prefs["llm_quality"] = i18n.quality_key_for_display(
            self.llm_quality_button.get(), self.ui_lang)
        settings.save(self.prefs)
        self._update_llm_target_state()

    def _update_llm_target_state(self):
        state = "disabled" if (
            self.llm_running or self.prefs["llm_mode"] == "summarize"
        ) else "normal"
        self.llm_target_menu.configure(state=state)

    def _refresh_llm_menu(self):
        if not self.edit_files:
            self.llm_file_menu.configure(values=[i18n.t(self.ui_lang, "edit_no_file")])
            self.llm_file_menu.set(i18n.t(self.ui_lang, "edit_no_file"))
            return
        values = [e["label"] for e in self.edit_files]
        self.llm_file_menu.configure(values=values)
        if self.llm_current and self.llm_current in self.edit_files:
            self.llm_file_menu.set(self.llm_current["label"])
        else:
            self.llm_file_menu.set(values[-1])

    def _on_llm_file_selected(self, label):
        if self.llm_running:
            self._refresh_llm_menu()
            return
        entry = next((e for e in self.edit_files if e["label"] == label), None)
        if entry:
            self._load_llm_entry(entry)

    def _open_file_for_llm(self):
        if self.llm_running:
            return
        path = filedialog.askopenfilename(
            title=i18n.t(self.ui_lang, "llm_pick_dialog"),
            initialdir=self._open_dialog_initialdir(),
            filetypes=[
                ("Transcripts", "*.txt *.docx"),
                ("Audio & video files",
                 "*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.wma *.aac *.aiff"
                 " *.mp4 *.mkv *.mov *.avi *.webm"),
                ("All files", "*.*"),
            ])
        if not path:
            return
        path = os.path.abspath(path)
        if path.lower().endswith((".txt", ".docx")):
            entry = next((e for e in self.edit_files if e["txt"] == path), None)
            if entry is None:
                entry = {"label": self._unique_label(os.path.basename(path)),
                         "audio": None, "txt": path}
                self.edit_files.append(entry)
        else:
            txt = self._find_transcript_for(path)
            entry = next((e for e in self.edit_files if e["audio"] == path), None)
            if entry is None:
                entry = {"label": self._unique_label(os.path.basename(path)),
                         "audio": path, "txt": txt}
                self.edit_files.append(entry)
            else:
                entry["txt"] = entry["txt"] or txt
        self._refresh_edit_menu()
        self._load_llm_entry(entry)

    def _load_llm_entry(self, entry):
        self.llm_current = entry
        text = ""
        if entry["txt"] and os.path.isfile(entry["txt"]):
            try:
                # Always the clean transcript here, regardless of whether
                # the saved file has [mm:ss] markers baked in (Timestamps
                # toggle was on when it was saved) — markers are an Edit-
                # tab navigation aid, not something a summary/translation
                # should ever see or display.
                text = timestamps.strip_markers(docx_export.read_transcript(entry["txt"]))
            except Exception:
                settings.log_exception(f"Read transcript failed: {entry['txt']}")
        self.llm_source.configure(state="normal")
        self.llm_source.delete("1.0", "end")
        self.llm_source.insert("1.0", text)
        self.llm_source.configure(state="disabled")
        self._refresh_llm_menu()
        if not text.strip():
            self._set_llm_status("llm_no_file", {})
        else:
            self._set_llm_status(None, "")

    def _generate_or_cancel(self):
        if self.llm_running:
            if self.llm_worker:
                self.llm_worker.cancel()
                self.llm_generate_button.configure(
                    state="disabled", text=i18n.t(self.ui_lang, "cancelling_button"))
            return
        text = self.llm_source.get("1.0", "end").strip()
        if not text:
            self._set_llm_status("llm_no_file", {})
            return
        self._on_llm_pref_change()
        quality = self.prefs["llm_quality"]
        if not llm.llm_model_is_downloaded(quality):
            recommended_key, _close_apps_hint = llm.recommended_quality(
                self.sys_ram_gb, sysinfo.free_ram_gb(),
                sysinfo.free_disk_gb(settings.MODELS_DIR))
            if not self._confirm_model_download(
                quality, llm.QUALITY_RAM_GB[quality],
                llm.QUALITY_LLM[quality]["size_gb"],
                recommended_key,
            ):
                return
        mode = self.prefs["llm_mode"]
        target = None
        if mode in ("translate", "both"):
            target = i18n.llm_target_prompt_name(self.prefs["llm_target"])
        self.llm_worker = LLMWorker(
            text=text, mode=mode, target_prompt_name=target,
            quality=self.prefs["llm_quality"], events=self.events)
        self._set_llm_running(True)
        self._clear_llm_output()
        self.llm_worker.start()

    def _set_llm_running(self, running):
        self.llm_running = running
        state = "disabled" if running else "normal"
        self.llm_mode_button.configure(state=state)
        self.llm_quality_button.configure(state=state)
        self.llm_file_menu.configure(state=state)
        self.llm_open_button.configure(state=state)
        self.llm_save_button.configure(state=state)
        self._update_llm_target_state()
        if running:
            self.llm_generate_button.configure(
                state="normal", text=i18n.t(self.ui_lang, "cancel_button"),
                fg_color="#8a3535", hover_color="#a04040")
        else:
            theme = ctk.ThemeManager.theme["CTkButton"]
            self.llm_generate_button.configure(
                state="normal", text=i18n.t(self.ui_lang, "llm_generate"),
                fg_color=theme["fg_color"], hover_color=theme["hover_color"])

    def _clear_llm_output(self):
        self.llm_output.configure(state="normal")
        self.llm_output.delete("1.0", "end")
        if self.llm_running:
            self.llm_output.configure(state="disabled")

    def _append_llm_output(self, text):
        self.llm_output.configure(state="normal")
        self.llm_output.insert("end", text)
        self.llm_output.see("end")
        if self.llm_running:
            self.llm_output.configure(state="disabled")

    def _write_llm_output_notice(self, text):
        self.llm_output.configure(state="normal")
        self.llm_output.delete("1.0", "end")
        self.llm_output.insert("1.0", text)
        if self.llm_running:
            self.llm_output.configure(state="disabled")

    def _save_llm_output(self):
        text = self.llm_output.get("1.0", "end").rstrip("\n")
        if not text.strip():
            self._set_llm_status(None, i18n.t(self.ui_lang, "nothing_to_save"))
            return
        base = os.path.splitext(self.llm_current["label"])[0] if self.llm_current \
            else "transcript"
        suffix = i18n.llm_output_suffix(
            self.ui_lang, self.prefs["llm_mode"], self.prefs["llm_target"])
        try:
            path, kind = docx_export.save_transcript(
                text, settings.transcriptions_folder(), f"{base} ({suffix})")
            self._set_llm_status(
                None, i18n.t(self.ui_lang,
                             "saved_docx" if kind == "docx" else "saved_txt",
                             path=path))
        except (PermissionError, OSError):
            settings.log_exception("Save AI output failed:")
            self._set_llm_status(None, i18n.t(self.ui_lang, "save_failed"))
            self._offer_output_folder_fix()
        except Exception:
            settings.log_exception("Save AI output failed:")
            self._set_llm_status(None, i18n.t(self.ui_lang, "save_failed"))

    def _set_llm_status(self, key, detail):
        self.llm_status_key = key
        self.llm_status_detail = detail if isinstance(detail, dict) else {}
        if key is None:
            self.llm_status_line.configure(
                text=detail if isinstance(detail, str) else "")
        else:
            self._render_llm_status()

    def _render_llm_status(self):
        if self.llm_status_key is None:
            return
        self.llm_status_line.configure(
            text=i18n.t(self.ui_lang, self.llm_status_key,
                        **(self.llm_status_detail or {})))

    # ------------------------------------------------------------- misc

    def _on_close(self):
        if self.running and self.worker:
            if not messagebox.askyesno(
                i18n.t(self.ui_lang, "confirm_quit_title"),
                i18n.t(self.ui_lang, "confirm_quit_message")):
                return
            self.worker.cancel()
        if self.llm_running and self.llm_worker:
            if not messagebox.askyesno(
                i18n.t(self.ui_lang, "confirm_quit_title"),
                i18n.t(self.ui_lang, "confirm_quit_generating")):
                return
            self.llm_worker.cancel()
        if self.live_running and self.live_worker:
            # No confirmation dialog: stopping (unlike Cancel elsewhere)
            # still auto-saves what's been said, so quitting mid-recording
            # loses nothing — just briefly wait for that save to land before
            # the process actually exits (the worker thread is a daemon and
            # would otherwise be killed mid-write).
            self.live_worker.stop()
            self.live_worker.join(timeout=5.0)
        try:
            self.player.stop()
        except Exception:
            pass
        settings.save(self.prefs)
        self.destroy()
        # Hard-exit instead of letting the interpreter shut down normally.
        # A model download in flight (whisper via huggingface_hub, or
        # SenseVoice via modelscope_hub) runs on a concurrent.futures.
        # ThreadPoolExecutor, and the stdlib registers an atexit hook
        # (concurrent.futures.thread._python_exit) the first time any
        # ThreadPoolExecutor is created in the process — it unconditionally
        # .join()s every worker thread that pool has ever spun up, with no
        # timeout, before the interpreter is allowed to exit. Closing the
        # app mid-download would otherwise block on that in-flight network
        # request (observed: a lingering python.exe after closing mid-
        # transcription) rather than actually quitting. os._exit() skips
        # atexit entirely and tears the process down immediately; anything
        # that must survive the close (prefs, the live recording) is
        # already saved above.
        os._exit(0)


def _excepthook(exc_type, exc, tb):
    import traceback
    settings.log("Unhandled error:\n" + "".join(
        traceback.format_exception(exc_type, exc, tb)))
    try:
        prefs = settings.load()
        ui_lang = prefs["ui_language"] if prefs["ui_language"] in i18n.UI_LANGUAGES else "en"
        messagebox.showerror(
            i18n.t(ui_lang, "error_dialog_title"),
            i18n.t(ui_lang, "error_dialog_message", error=exc, log=settings.LOG_FILE))
    except Exception:
        pass


def main():
    sys.excepthook = _excepthook
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = App()
    if "--selftest" in sys.argv:
        app.after(1500, app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
