"""SOTA — Smart Offline Transcription Application.

Four tabs:
  • Transcribe — drop audio files in, click Transcribe All, get a transcript
    each (.docx when Word is installed, .txt otherwise).
  • Live Transcription — dictate from the microphone via SenseVoice
    (English/Mandarin/Cantonese/Japanese/Korean only); auto-saves on Stop.
  • Edit & Export — replay a file (adjustable speed), fix the transcript,
    save a copy.
  • AI Summary & Translate — run a local LLM over a transcript to summarize
    and/or translate it, streaming into an editable panel.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

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
        self.grid_rowconfigure(1, weight=1)

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
        for i in range(len(self.TAB_KEYS)):
            btn = ctk.CTkButton(
                self.tab_bar, text="", height=32, corner_radius=6,
                border_spacing=0, command=lambda idx=i: self._show_tab(idx),
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

        # --- content area holds both tab frames in the same cell
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.transcribe_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.live_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.edit_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.llm_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.settings_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        # Only the Transcribe tab is gridded up front. All four occupy the
        # same cell, and Tk stacks later-gridded widgets on top — gridding
        # all four here (as before) put the AI tab (gridded last) on top
        # of the stack from the very first paint, until _show_tab(0) later
        # sorted it out. That's a no-op on a fast dev machine, but left a
        # real window on a slower one (e.g. a packaged .exe) where the
        # wrong tab could actually be what gets painted first. _show_tab()
        # grids whichever tab is active, so the other three never need to be
        # gridded here at all.
        self.transcribe_frame.grid(row=0, column=0, sticky="nsew")

        self._build_transcribe_tab(self.transcribe_frame)
        self._build_live_tab(self.live_frame)
        self._build_edit_tab(self.edit_frame)
        self._build_llm_tab(self.llm_frame)
        self._build_settings_tab(self.settings_frame)

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

        self.sensevoice_var = ctk.BooleanVar(value=False)
        self.sensevoice_box = ctk.CTkCheckBox(
            options, text="", variable=self.sensevoice_var, command=self._on_pref_change,
        )
        # Its own row, not crammed onto the quality/language row — the full
        # language-name label this checkbox needs is too long to share a
        # row with the pickers at the window's default (or minimum) width.
        # The English text also has an explicit line break (i18n.py) so it
        # never relies on the window being wide enough to avoid overflow.
        self.sensevoice_box.grid(
            row=1, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 12))

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

        self.live_hint_label = ctk.CTkLabel(
            options, text="", anchor="w", text_color=self.MUTED_TEXT, wraplength=640,
            justify="left")
        self.live_hint_label.grid(row=1, column=0, columnspan=7, sticky="ew",
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
        bottom.grid_columnconfigure(2, weight=1)

        self.live_toggle_button = ctk.CTkButton(
            bottom, text="", height=36,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._toggle_live_recording)
        self.live_toggle_button.grid(row=0, column=0, padx=(0, 8))

        self.live_open_folder_button = ctk.CTkButton(
            bottom, text="", height=36, width=150, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT, border_width=1,
            command=lambda: self._open_output_folder(settings.live_recordings_folder()))
        self.live_open_folder_button.grid(row=0, column=1, padx=(0, 10))

        self.live_status_line = ctk.CTkLabel(bottom, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.live_status_line.grid(row=0, column=2, sticky="ew")

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
        saverow.grid_columnconfigure(2, weight=1)
        self.save_button = ctk.CTkButton(
            saverow, text="", height=36, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._save_edit)
        self.save_button.grid(row=0, column=0, padx=(0, 8))
        self.edit_open_folder_button = ctk.CTkButton(
            saverow, text="", height=36, width=150, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._open_output_folder)
        self.edit_open_folder_button.grid(row=0, column=1, padx=(0, 10))
        self.edit_status_line = ctk.CTkLabel(saverow, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.edit_status_line.grid(row=0, column=2, sticky="ew")

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
        if self.current_tab == 4 and not self.model_downloads:
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
            return i18n.t(self.ui_lang, "settings_model_whisper", quality=quality)
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
            self._show_tab(4)
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

    TAB_KEYS = ["tab_transcribe", "tab_live", "tab_edit", "tab_llm", "tab_settings"]
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
        """Pans the tab strip so the currently selected tab's button is
        fully in view — otherwise a language switch reflowing tab widths
        (or programmatic tab changes) could leave the active tab scrolled
        out of sight with no visual indication it's still selected."""
        if not (0 <= self.current_tab < len(self.tab_buttons)):
            return
        self.tab_canvas.update_idletasks()
        content_w = max(1, self.tab_bar.winfo_reqwidth())
        viewport_w = max(1, self.tab_canvas.winfo_width())
        if content_w <= viewport_w:
            return
        btn = self.tab_buttons[self.current_tab]
        btn_x0, btn_x1 = btn.winfo_x(), btn.winfo_x() + btn.winfo_width()
        cur_x0 = self.tab_canvas.canvasx(0)
        if btn_x0 < cur_x0:
            self.tab_canvas.xview_moveto(btn_x0 / content_w)
        elif btn_x1 > cur_x0 + viewport_w:
            self.tab_canvas.xview_moveto(max(0, btn_x1 - viewport_w) / content_w)

    def _show_tab(self, index):
        if self.current_tab == 1 and index != 1 and self.live_running:
            # Navigating away from the Live tab mid-recording — stop it
            # automatically rather than leaving it recording invisibly in
            # the background. Same call the Stop button makes: still
            # auto-saves whatever's been captured so far.
            self._stop_live_recording()
        self.current_tab = index
        frames = [self.transcribe_frame, self.live_frame, self.edit_frame,
                  self.llm_frame, self.settings_frame]
        for i, frame in enumerate(frames):
            if i == index:
                # Explicit args, not a bare grid() — the live/edit/AI frames
                # may never have been gridded before (see _build_ui), so
                # there's no remembered geometry for a bare call to restore.
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        if index == 1:
            self._refresh_mic_menu()
            self._maybe_preload_sensevoice()
        elif index == 2:
            self._maybe_autoload_edit()
        elif index == 3:
            self._maybe_autoload_llm()
        elif index == 4:
            self._refresh_settings_tab()
        self._style_tab_buttons()
        self._ensure_active_tab_visible()

    def _style_tab_buttons(self):
        for i, btn in enumerate(self.tab_buttons):
            selected = i == self.current_tab
            btn.configure(
                fg_color=self.TAB_SELECTED_BG if selected else self.TAB_UNSELECTED_BG,
                hover_color=self.TAB_SELECTED_BG if selected else self.TAB_UNSELECTED_HOVER,
                text_color=("gray10", "gray95") if selected else ("gray35", "gray65"),
                font=ctk.CTkFont(size=13, weight="bold" if selected else "normal"),
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

        # tabs — all five share one width, sized to fit the widest label
        # (measured in the bold variant, which the selected tab uses, so
        # selecting a tab never changes any width). A single fixed constant
        # stopped fitting once the fifth tab arrived, so it's computed from
        # the current language's own longest label instead.
        tab_font = ctk.CTkFont(size=13, weight="bold")
        texts = [t(key) for key in self.TAB_KEYS]
        uniform_width = max(64, max(tab_font.measure(text) for text in texts) + 34)
        for btn, text in zip(self.tab_buttons, texts):
            btn.configure(text=text, width=uniform_width)
        self._style_tab_buttons()
        # Tab widths just changed (language switch) — re-check whether the
        # strip still fits its viewport and keep the active tab in view.
        self._update_tab_scroll_state()
        self._ensure_active_tab_visible()

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
        self.live_hint_label.configure(text=t("live_hint"))
        self.live_text_hint_label.configure(text=t("live_text_hint"))
        self.live_open_folder_button.configure(text=t("open_output_folder"))
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
        if self.current_tab != 0:
            return
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        self._add_paths(paths)

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
        self._show_tab(2)

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
                if self.current_tab == 2:
                    self._maybe_autoload_edit()
                elif self.current_tab == 3:
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
                elif key == "save_failed":
                    self._offer_output_folder_fix()
        elif kind == "live_text":
            _, committed, preview, _lang = event
            self._set_live_text(committed, preview)
        elif kind == "live_saved":
            _, audio_path, txt_path = event
            self._register_live_transcript(audio_path, txt_path)
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
        # First-ever session downloads the engine (~900 MB) — the tab's
        # preload deliberately doesn't (see SenseVoicePreloader), so this
        # is the one place the Live tab can trigger it. Gate on disk room.
        if not self._confirm_sensevoice_download():
            return
        code = self.prefs["live_language"]
        language = "" if code == "auto" else code
        self.live_session_started = True
        self.live_running = True
        self._write_live_text("")
        self.live_language_menu.configure(state="disabled")
        self.live_mic_menu.configure(state="disabled")
        self._render_live_toggle_button()
        # No status set here — the worker's own events ("loading",
        # "downloading", "mic_failed", or "recording") are the only source
        # of truth for what's actually happening. Claiming "Recording…"
        # immediately would be a lie whenever the model still needs to
        # load (which can take real time even when nothing needs
        # downloading) or the mic fails to open.
        self.live_worker = live_transcription.LiveTranscriber(
            language, self.events,
            device_name=self.prefs.get("live_mic_device", ""),
            traditional_chinese=bool(self.prefs.get("chinese_traditional")))
        self.live_worker.start()

    def _stop_live_recording(self):
        if self.live_worker:
            self.live_worker.stop()
        self.live_toggle_button.configure(state="disabled")
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
            self._register_edit_file(audio_path, txt_path)
        elif txt_path:
            stem = os.path.splitext(os.path.basename(txt_path))[0]
            self.edit_files.append(
                {"label": self._unique_label(stem), "audio": None, "txt": txt_path})
            self._refresh_edit_menu()
        else:
            return
        self._refresh_llm_menu()
        if self.current_tab == 2:
            self._maybe_autoload_edit()
        elif self.current_tab == 3:
            self._maybe_autoload_llm()

    def _on_live_stopped(self):
        self.live_running = False
        self.live_worker = None
        state = "normal" if self.sensevoice_available else "disabled"
        self.live_language_menu.configure(state=state)
        self.live_mic_menu.configure(state=state)
        self.live_level_bar.set(0)
        self._render_live_toggle_button()

    # ============================================================= edit tab

    def _register_edit_file(self, audio_path, txt_path):
        label = os.path.basename(audio_path)
        for entry in self.edit_files:
            if entry["audio"] == audio_path:
                entry["txt"] = txt_path
                break
        else:
            self.edit_files.append({"label": label, "audio": audio_path, "txt": txt_path})
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
        if self.current_tab == 1 and self.live_running and self.live_worker:
            # Rough perceptual scaling: speech RMS sits around 0.02–0.2, so
            # ×12 maps quiet speech near 1/4 bar and normal speech near full.
            self.live_level_bar.set(min(1.0, self.live_worker.level * 12))
        if self.current_tab == 2 and self.player.loaded_path is not None:
            if self.player.is_playing:
                self.position_slider.set(self.player.get_fraction())
                self.time_label.configure(
                    text=f"{_fmt_time(self.player.get_time())} / {_fmt_time(self.player.duration)}")
            elif self.play_button.cget("text") == i18n.t(self.ui_lang, "player_pause"):
                # Playback ended on its own.
                self._render_play_button()
        self.after(TICK_MS, self._tick_player)

    def _save_edit(self):
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
        try:
            path, kind = docx_export.save_transcript(
                text, settings.transcriptions_folder(), stem)
            if has_times:
                timestamps.save_sidecar(path, times)
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
