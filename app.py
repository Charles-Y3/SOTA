"""SOTA — Smart Offline Transcription Application.

Two tabs:
  • Transcribe — drop audio files in, click Transcribe All, get a .txt each.
  • Edit & Export — replay a file (adjustable speed), fix the transcript,
    save a copy as .docx (if Word is installed) or .txt.
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
import settings
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
        self.ui_lang = self.prefs["ui_language"] if self.prefs["ui_language"] in i18n.UI_LANGUAGES else "en"
        self.rows = []
        self.worker = None
        self.events = queue.Queue()
        self.running = False
        self.status_key = None
        self.status_detail = None
        self.current_tab = 0

        # Edit tab state
        self.player = Player(ready_callback=lambda: self.events.put(("speed_ready",)))
        self.edit_files = []       # [{label, audio, txt}]
        self.edit_current = None   # current {label, audio, txt}
        self.edit_status_key = None
        self.edit_status_detail = None

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
        topbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        topbar.grid_columnconfigure(1, weight=1)

        self.tab_button = ctk.CTkSegmentedButton(topbar, command=self._on_tab_change)
        self.tab_button.grid(row=0, column=0, sticky="w")

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
        for f in (self.transcribe_frame, self.edit_frame):
            f.grid(row=0, column=0, sticky="nsew")

        self._build_transcribe_tab(self.transcribe_frame)
        self._build_edit_tab(self.edit_frame)

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
        self.drop_sub = ctk.CTkLabel(self.drop_zone, text="", text_color="gray")
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
            border_width=1, command=self._clear_list)
        self.clear_button.grid(row=1, column=2, padx=(0, 8))

        self.open_folder_button = ctk.CTkButton(
            bottom, text="", height=36, width=150, fg_color="transparent",
            border_width=1, command=self._open_output_folder)
        self.open_folder_button.grid(row=1, column=3)

        self.status_line = ctk.CTkLabel(parent, text="", anchor="w", text_color="gray")
        self.status_line.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 8))

    # ------------------------------------------------------------ edit tab

    def _build_edit_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(3, weight=1)

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
            command=self._stop_play)
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

        # editor hint
        self.editor_hint_label = ctk.CTkLabel(parent, text="", anchor="w", text_color="gray")
        self.editor_hint_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 2))

        # editor
        self.editor = ctk.CTkTextbox(parent, wrap="word", font=ctk.CTkFont(size=14))
        self.editor.grid(row=3, column=0, sticky="nsew", padx=12, pady=6)

        # save row
        saverow = ctk.CTkFrame(parent, fg_color="transparent")
        saverow.grid(row=4, column=0, sticky="ew", padx=12, pady=(2, 4))
        saverow.grid_columnconfigure(2, weight=1)
        self.save_button = ctk.CTkButton(
            saverow, text="", height=36, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._save_edit)
        self.save_button.grid(row=0, column=0, padx=(0, 8))
        self.edit_open_folder_button = ctk.CTkButton(
            saverow, text="", height=36, width=150, fg_color="transparent",
            border_width=1, command=self._open_output_folder)
        self.edit_open_folder_button.grid(row=0, column=1, padx=(0, 10))
        self.edit_status_line = ctk.CTkLabel(saverow, text="", anchor="w", text_color="gray")
        self.edit_status_line.grid(row=0, column=2, sticky="ew")

        self._set_player_enabled(False)

    # ------------------------------------------------------------- tabs

    @staticmethod
    def _equalize_segments(seg, width):
        """Give every segment the same fixed width so the control keeps its
        size when the labels change (e.g. switching UI language)."""
        for btn in seg._buttons_dict.values():
            btn.configure(width=width)

    def _on_tab_change(self, value):
        labels = [i18n.t(self.ui_lang, "tab_transcribe"), i18n.t(self.ui_lang, "tab_edit")]
        self._show_tab(labels.index(value) if value in labels else 0)

    def _show_tab(self, index):
        self.current_tab = index
        if index == 0:
            self.edit_frame.grid_remove()
            self.transcribe_frame.grid()
        else:
            self.transcribe_frame.grid_remove()
            self.edit_frame.grid()
            self._maybe_autoload_edit()
        self.tab_button.set(
            i18n.t(self.ui_lang, "tab_transcribe" if index == 0 else "tab_edit")
        )

    def _maybe_autoload_edit(self):
        """When the Edit tab has nothing open yet, load the most recent file
        (its audio and, if present, its transcript) so it's ready to review."""
        if self.edit_current is None and self.edit_files:
            self._load_edit_entry(self.edit_files[-1])

    def _apply_prefs(self):
        if self.prefs["quality"] not in i18n.QUALITY_KEYS:
            self.prefs["quality"] = "balanced"
        valid_codes = {code for code, _, _ in i18n.TRANSCRIBE_LANGUAGES}
        if self.prefs["transcribe_language"] not in valid_codes:
            self.prefs["transcribe_language"] = "auto"
        self.timestamps_var.set(bool(self.prefs["timestamps"]))
        self.ui_lang_button.set("EN" if self.ui_lang == "en" else "繁中")

    # --------------------------------------------------------- translation

    def _retranslate(self):
        t = lambda key, **kw: i18n.t(self.ui_lang, key, **kw)  # noqa: E731
        self.title(t("app_title"))

        # tabs
        self.tab_button.configure(values=[t("tab_transcribe"), t("tab_edit")])
        self.tab_button.set(t("tab_transcribe" if self.current_tab == 0 else "tab_edit"))
        self._equalize_segments(self.tab_button, 112)

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
        self._render_play_button()
        self._refresh_edit_menu()
        self._render_edit_status()

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
        frame = ctk.CTkFrame(self.file_list, fg_color=("gray86", "gray20"))
        frame.pack(fill="x", pady=3, padx=2)
        frame.grid_columnconfigure(0, weight=1)
        name_label = ctk.CTkLabel(frame, text=os.path.basename(path), anchor="w")
        name_label.grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=8)
        status_label = ctk.CTkLabel(
            frame, text="", anchor="e", text_color="gray", wraplength=300)
        status_label.grid(row=0, column=1, sticky="e", padx=(0, 8), pady=8)
        row = {
            "path": path, "frame": frame, "name_label": name_label,
            "status_label": status_label, "output": None,
            "status_key": "waiting", "status_detail": {},
        }
        remove_btn = ctk.CTkButton(
            frame, text="✕", width=28, height=24, fg_color="transparent",
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
        self.open_folder_button.configure(state="disabled")
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
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
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
        elif kind == "finished":
            self._set_running(False)
            self.worker = None
            done = sum(1 for r in self.rows if r["status_key"].startswith("done"))
            self.progress_bar.set(1 if done == len(self.rows) and done
                                  else self.progress_bar.get())
        elif kind == "speed_ready":
            if self.edit_status_key == "player_preparing":
                self._set_edit_status(None, "")
        elif kind == "audio_loaded":
            self._on_audio_loaded(event[1], event[2])

    def _render_row_status(self, row):
        text = i18n.job_status_text(self.ui_lang, row["status_key"], row["status_detail"])
        color = "gray"
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
        self._set_edit_status(None, "…")
        audio = entry["audio"]

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
                self._set_edit_status("player_preparing", {"speed": _speed_label(speed)})

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

    # ------------------------------------------------------------- misc

    def _on_close(self):
        if self.running and self.worker:
            if not messagebox.askyesno(
                i18n.t(self.ui_lang, "confirm_quit_title"),
                i18n.t(self.ui_lang, "confirm_quit_message")):
                return
            self.worker.cancel()
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
