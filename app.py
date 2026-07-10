"""SOTA — Smart Offline Transcription Application.

Three tabs:
  • Transcribe — drop audio files in, click Transcribe All, get a transcript
    each (.docx when Word is installed, .txt otherwise).
  • Edit & Export — replay a file (adjustable speed), fix the transcript,
    save a copy.
  • AI Summary & Translate — run a local LLM over a transcript to summarize
    and/or translate it, streaming into an editable panel.
"""

import os
import queue
import sys
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

import docx_export
import i18n
import llm
import settings
import sysinfo
import transcriber
from llm import LLMWorker
from player import SPEED_OPTIONS, Player
from transcriber import Job, TranscriberWorker, is_supported

POLL_MS = 100
TICK_MS = 150


def _fmt_time(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _speed_label(speed):
    return (f"{speed:g}×")


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        self.geometry("820x680")
        self.minsize(680, 560)

        self.prefs = settings.load()
        for key in ("editor_font_size", "llm_source_font_size", "llm_output_font_size"):
            self.prefs[key] = self._clamp_font_size(self.prefs.get(key, 14))
        self.sys_ram_gb = sysinfo.total_ram_gb()
        self.ui_lang = self.prefs["ui_language"] if self.prefs["ui_language"] in i18n.UI_LANGUAGES else "en"
        self.rows = []
        self.worker = None
        self.events = queue.Queue()
        self.running = False
        self.status_key = None
        self.status_detail = None
        self.current_tab = 0

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

        # AI tab state
        self.llm_worker = None
        self.llm_running = False
        self.llm_current = None
        self.llm_status_key = None
        self.llm_status_detail = None

        self._build_ui()
        self._apply_prefs()
        self._retranslate()
        self._show_tab(0)

        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll_events)
        self.after(TICK_MS, self._tick_player)

    # ================================================================== UI

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- top bar: tabs (left) + UI language toggle (right)
        topbar = ctk.CTkFrame(self, fg_color="transparent")
        topbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        topbar.grid_columnconfigure(1, weight=1)

        # Flat rectangular tabs (not the pill-shaped segmented-button look):
        # the selected tab's background matches the content panel below it,
        # so it visually merges into it, like tabs in a regular desktop app.
        self.tab_bar = ctk.CTkFrame(topbar, fg_color="transparent")
        self.tab_bar.grid(row=0, column=0, sticky="w")
        self.tab_buttons = []
        for i in range(len(self.TAB_KEYS)):
            btn = ctk.CTkButton(
                self.tab_bar, text="", height=32, corner_radius=6,
                border_spacing=0, command=lambda idx=i: self._show_tab(idx),
            )
            btn.grid(row=0, column=i, sticky="ew", padx=1)
            self.tab_bar.grid_columnconfigure(i, weight=0, uniform="tabbar")
            self.tab_buttons.append(btn)

        self.ui_lang_button = ctk.CTkSegmentedButton(
            topbar, values=["EN", "繁中"], command=self._on_ui_lang_change,
        )
        self.ui_lang_button.grid(row=0, column=2, sticky="e")
        self._equalize_segments(self.ui_lang_button, 52)

        # --- content area holds both tab frames in the same cell
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.transcribe_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.edit_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.llm_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        # Only the Transcribe tab is gridded up front. All three occupy the
        # same cell, and Tk stacks later-gridded widgets on top — gridding
        # all three here (as before) put the AI tab (gridded last) on top
        # of the stack from the very first paint, until _show_tab(0) later
        # sorted it out. That's a no-op on a fast dev machine, but left a
        # real window on a slower one (e.g. a packaged .exe) where the
        # wrong tab could actually be what gets painted first. _show_tab()
        # grids whichever tab is active, so the other two never need to be
        # gridded here at all.
        self.transcribe_frame.grid(row=0, column=0, sticky="nsew")

        self._build_transcribe_tab(self.transcribe_frame)
        self._build_edit_tab(self.edit_frame)
        self._build_llm_tab(self.llm_frame)

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

        self.timestamps_var = ctk.BooleanVar(value=False)
        self.timestamps_box = ctk.CTkCheckBox(
            options, text="", variable=self.timestamps_var, command=self._on_pref_change,
        )
        self.timestamps_box.grid(row=0, column=4, padx=(0, 8), pady=10)

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

        # bottom bar
        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=12, pady=(6, 2))
        bottom.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(bottom)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))

        self.transcribe_button = ctk.CTkButton(
            bottom, text="", height=36, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_transcription)
        self.transcribe_button.grid(row=1, column=0, sticky="ew", padx=(0, 8))

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
        self.open_folder_button.grid(row=1, column=3)

        self.status_line = ctk.CTkLabel(parent, text="", anchor="w", text_color=self.MUTED_TEXT)
        self.status_line.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 8))

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
        self.punct_toggle_button = ctk.CTkButton(
            hint_row, text="", width=90, height=24, font=ctk.CTkFont(size=12),
            fg_color="transparent", text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._toggle_punct_pad)
        self.punct_toggle_button.grid(row=0, column=2, sticky="e")

        # punctuation pad — hidden until toggled on; sits above the editor
        # so inserts land wherever the cursor already is.
        self.punct_pad = ctk.CTkFrame(parent, fg_color=("gray90", "gray17"))
        self.punct_pad.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 4))
        self._build_punct_pad(self.punct_pad)

        # editor
        self.editor = ctk.CTkTextbox(parent, wrap="word", font=self.editor_font)
        self.editor.grid(row=4, column=0, sticky="nsew", padx=12, pady=6)

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

        # action row
        actions = ctk.CTkFrame(parent)
        actions.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        actions.grid_columnconfigure(6, weight=1)

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

        # Sits in the stretchy column right after the quality picker it's
        # advising on, with the generate button pinned past it on the right.
        self.llm_ram_caption = ctk.CTkLabel(
            actions, text="", anchor="w", text_color=self.MUTED_TEXT,
            font=ctk.CTkFont(size=11))
        self.llm_ram_caption.grid(row=0, column=6, sticky="w", padx=(0, 8), pady=10)

        self.llm_generate_button = ctk.CTkButton(
            actions, text="", width=130, height=32,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._generate_or_cancel)
        self.llm_generate_button.grid(row=0, column=7, sticky="e",
                                      padx=(0, 12), pady=10)

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

        # save row
        saverow = ctk.CTkFrame(parent, fg_color="transparent")
        saverow.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 4))
        saverow.grid_columnconfigure(2, weight=1)
        self.llm_save_button = ctk.CTkButton(
            saverow, text="", height=36, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._save_llm_output)
        self.llm_save_button.grid(row=0, column=0, padx=(0, 8))
        self.llm_open_folder_button = ctk.CTkButton(
            saverow, text="", height=36, width=150, fg_color="transparent",
            text_color=self.OUTLINE_BUTTON_TEXT,
            border_width=1, command=self._open_output_folder)
        self.llm_open_folder_button.grid(row=0, column=1, padx=(0, 10))
        self.llm_status_line = ctk.CTkLabel(saverow, text="", anchor="w",
                                            text_color=self.MUTED_TEXT)
        self.llm_status_line.grid(row=0, column=2, sticky="ew")

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

    def _set_punct_pad_visible(self, visible):
        self.punct_pad_visible = visible
        if visible:
            self.punct_pad.grid()
        else:
            self.punct_pad.grid_remove()
        self.punct_toggle_button.configure(
            fg_color=("gray75", "gray30") if visible else "transparent")

    TAB_KEYS = ["tab_transcribe", "tab_edit", "tab_llm"]
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

    def _show_tab(self, index):
        self.current_tab = index
        frames = [self.transcribe_frame, self.edit_frame, self.llm_frame]
        for i, frame in enumerate(frames):
            if i == index:
                # Explicit args, not a bare grid() — the edit/AI frames may
                # never have been gridded before (see _build_ui), so there's
                # no remembered geometry for a bare call to restore.
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        if index == 1:
            self._maybe_autoload_edit()
        elif index == 2:
            self._maybe_autoload_llm()
        self._style_tab_buttons()

    def _style_tab_buttons(self):
        for i, btn in enumerate(self.tab_buttons):
            selected = i == self.current_tab
            btn.configure(
                fg_color=self.TAB_SELECTED_BG if selected else self.TAB_UNSELECTED_BG,
                hover_color=self.TAB_SELECTED_BG if selected else self.TAB_UNSELECTED_HOVER,
                text_color=("gray10", "gray95") if selected else ("gray35", "gray65"),
                font=ctk.CTkFont(size=13, weight="bold" if selected else "normal"),
            )

    def _maybe_autoload_edit(self):
        """When the Edit tab has nothing open yet, load the most recent file
        (its audio and, if present, its transcript) so it's ready to review."""
        if self.edit_current is None and self.edit_files:
            self._load_edit_entry(self.edit_files[-1])

    def _maybe_autoload_llm(self):
        if self.llm_current is None and self.edit_files:
            self._load_llm_entry(self.edit_files[-1])

    def _apply_prefs(self):
        if self.prefs["quality"] not in i18n.QUALITY_KEYS:
            self.prefs["quality"] = "balanced"
        valid_codes = {code for code, _, _ in i18n.TRANSCRIBE_LANGUAGES}
        if self.prefs["transcribe_language"] not in valid_codes:
            self.prefs["transcribe_language"] = "auto"
        self.timestamps_var.set(bool(self.prefs["timestamps"]))
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

        # tabs
        for i, key in enumerate(self.TAB_KEYS):
            self.tab_buttons[i].configure(text=t(key), width=165)
        self._style_tab_buttons()

        # transcribe tab
        self.quality_label.configure(text=t("quality_label"))
        self.language_label.configure(text=t("language_label"))
        self.timestamps_box.configure(text=t("timestamps_label"))
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

        # edit tab
        self.edit_file_label.configure(text=t("edit_file_label"))
        self.edit_open_button.configure(text=t("edit_open_button"))
        self.speed_caption.configure(text=t("player_speed"))
        self.save_button.configure(text=t("save_button"))
        self.edit_open_folder_button.configure(text=t("open_output_folder"))
        self.editor_hint_label.configure(text=t("editor_hint"))
        self.punct_toggle_button.configure(text=t("punct_toggle"))
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

    def _update_ram_caption(self):
        if self.sys_ram_gb is None:
            self.llm_ram_caption.configure(text="")
            return
        recommended = llm.recommended_quality(self.sys_ram_gb)
        self.llm_ram_caption.configure(text=i18n.t(
            self.ui_lang, "ram_caption",
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
        self._show_tab(1)

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
        self.prefs["timestamps"] = bool(self.timestamps_var.get())
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
        for row in self.rows:
            row["output"] = None
            row["status_key"], row["status_detail"] = "waiting", {}
            self._render_row_status(row)
        code = self.prefs["transcribe_language"]
        jobs = [Job(i, row["path"]) for i, row in enumerate(self.rows)]
        self.worker = TranscriberWorker(
            jobs=jobs, quality=self.prefs["quality"],
            language_code=None if code == "auto" else code,
            timestamps=self.prefs["timestamps"],
            output_folder=settings.DEFAULT_OUTPUT_FOLDER, events=self.events)
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
        self.timestamps_box.configure(state=state)
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
                if self.current_tab == 1:
                    self._maybe_autoload_edit()
                elif self.current_tab == 2:
                    self._maybe_autoload_llm()
        elif kind == "finished":
            self._set_running(False)
            self.worker = None
            done = sum(1 for r in self.rows if r["status_key"].startswith("done"))
            self.progress_bar.set(1 if done == len(self.rows) and done
                                  else self.progress_bar.get())
        elif kind == "speed_ready":
            if self.edit_status_key == "player_preparing":
                self._set_edit_status(None, "")
        elif kind == "speed_progress":
            _, speed, frac = event
            self._set_edit_status(
                "player_preparing", {"speed": _speed_label(speed), "pct": int(frac * 100)})
        elif kind == "audio_loaded":
            self._on_audio_loaded(event[1], event[2])
        elif kind == "llm_status":
            _, key, detail = event
            self.llm_status_key, self.llm_status_detail = key, detail
            self._render_llm_status()
        elif kind == "llm_reset":
            self._clear_llm_output()
        elif kind == "llm_finished":
            ok = event[1] if len(event) > 1 else False
            self._set_llm_running(False)
            self.llm_worker = None
            self.llm_output.configure(state="normal")
            if ok:
                self._save_llm_output()  # always save a copy once generation succeeds

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

    def _open_output_folder(self):
        folder = settings.DEFAULT_OUTPUT_FOLDER
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
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

    def _set_status(self, text):
        self.status_key = None
        self.status_line.configure(text=text)

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
        """Start 'Open file' dialogs in the output folder — transcripts and
        exported copies all end up there, so it's usually where the file
        the user wants to reopen already is."""
        folder = settings.DEFAULT_OUTPUT_FOLDER
        return folder if os.path.isdir(folder) else None

    def _find_transcript_for(self, audio_path):
        stem = os.path.splitext(os.path.basename(audio_path))[0]
        for folder in (settings.DEFAULT_OUTPUT_FOLDER, os.path.dirname(audio_path)):
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
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)

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
        if self.current_tab == 1 and self.player.loaded_path is not None:
            if self.player.is_playing:
                self.position_slider.set(self.player.get_fraction())
                self.time_label.configure(
                    text=f"{_fmt_time(self.player.get_time())} / {_fmt_time(self.player.duration)}")
            elif self.play_button.cget("text") == i18n.t(self.ui_lang, "player_pause"):
                # Playback ended on its own.
                self._render_play_button()
        self.after(TICK_MS, self._tick_player)

    def _save_edit(self):
        text = self.editor.get("1.0", "end").rstrip("\n")
        if not text.strip():
            self._set_edit_status("nothing_to_save", {})
            return
        if self.edit_current:
            stem = os.path.splitext(self.edit_current["label"])[0] + " (edited)"
        else:
            stem = "transcript (edited)"
        try:
            path, kind = docx_export.save_transcript(
                text, settings.DEFAULT_OUTPUT_FOLDER, stem)
            self._set_edit_status("saved_docx" if kind == "docx" else "saved_txt",
                                  {"path": path})
            if self.edit_current:
                # Point this entry at the edited copy so anything that loads
                # it next — the AI tab's autoload, reopening it here later —
                # picks up the user's edits instead of the original text.
                self.edit_current["txt"] = path
                if self.llm_current is self.edit_current and not self.llm_running:
                    self._load_llm_entry(self.llm_current)
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
                text = docx_export.read_transcript(entry["txt"])
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
            if not self._confirm_model_download(
                quality, llm.QUALITY_RAM_GB[quality],
                llm.QUALITY_LLM[quality]["size_gb"],
                llm.recommended_quality(self.sys_ram_gb),
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
                text, settings.DEFAULT_OUTPUT_FOLDER, f"{base} ({suffix})")
            self._set_llm_status(
                None, i18n.t(self.ui_lang,
                             "saved_docx" if kind == "docx" else "saved_txt",
                             path=path))
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
        try:
            self.player.stop()
        except Exception:
            pass
        settings.save(self.prefs)
        self.destroy()


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
