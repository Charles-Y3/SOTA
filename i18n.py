"""All user-facing text for SOTA, in English and Traditional Chinese."""

UI_LANGUAGES = ["en", "zh"]

STRINGS = {
    "en": {
        # Display name only (v2 rename) — internal identifiers (repo name,
        # app-data folder, update-checker's GitHub lookup) all stay "SOTA".
        "app_title": "Smart Offline Transcription & Audio",
        "tab_transcribe": "Transcribe",
        "tab_edit": "Edit & Export",
        "quality_label": "Quality",
        "quality_fast": "Fast",
        "quality_balanced": "Balanced",
        "quality_accurate": "Accurate",
        "language_label": "Language",
        "sensevoice_label": "SenseVoice: more accurate for English, Mandarin,"
                             " Cantonese, Japanese, Korean",
        "drop_title": "Drag & drop audio files here",
        "drop_sub": "or click to browse  •  mp3, wav, m4a, flac, ogg, video files…",
        "browse_dialog_title": "Choose audio files",
        "transcribe_button": "Transcribe All",
        "transcribing_button": "Transcribing…",
        "cancel_button": "Cancel",
        "cancelling_button": "Cancelling…",
        "clear_button": "Clear list",
        "open_output_folder": "Open output folder",
        "status_waiting": "Waiting",
        "status_transcribing": "Transcribing… {pct}%",
        "status_done": "Done ✓",
        "status_done_lang": "Done ✓ ({lang})",
        "status_done_no_speech": "Done — no speech detected",
        "status_cancelled": "Cancelled",
        "status_failed_model": "Failed — model unavailable",
        "status_failed_not_found": "Failed — file not found",
        "status_failed_write": "Failed — could not save the file",
        "status_failed_error": "Failed — {error}",
        "line_downloading": "Downloading {quality} model (~{size_mb} MB, {pct}%)"
                             " — first run only, internet required…",
        "line_loading": "Loading {quality} model…",
        "line_sensevoice_downloading": "Downloading SenseVoice model (~{size_mb} MB,"
                                        " {pct}%) — first run only, internet required…",
        "line_sensevoice_loading": "Loading SenseVoice model… this can take up to a minute.",
        "line_download_failed": "Could not download the model. Connect to the"
                                 " internet (needed once per quality level) and try again.",
        "line_load_failed": "The speech model could not be loaded. See sota.log for details.",
        "line_transcribing": "Transcribing…",
        "line_cancelled": "Cancelled.",
        "line_finished": "Finished.",
        "line_crashed": "Something went wrong. See sota.log for details.",
        "please_wait_batch": "Please wait for the current batch to finish.",
        "cancelling_status": "Cancelling — finishing the current step…",
        "no_audio_found": "No audio files found there.",
        "double_click_tip": "Tip: double-click a finished file to open it in the Edit & Export tab.",
        "add_files_first_title": "SOTA",
        "add_files_first_message": "Add some audio files first — drag & drop them into the window.",
        "confirm_quit_title": "SOTA",
        "confirm_quit_message": "Transcription is still running. Stop and quit?",
        "error_dialog_title": "SOTA",
        "error_dialog_message": "Something went wrong:\n{error}\n\nDetails: {log}",
        # --- system capability checks (before a model download)
        "capability_low_ram_title": "SOTA",
        "capability_low_ram_message":
            "This PC has about {ram} GB of RAM. The {quality} model typically"
            " needs about {required} GB to run comfortably, so it may run"
            " slowly or fail.\n\nWe recommend {recommended} instead."
            " Continue with {quality} anyway?",
        "capability_low_ram_message_min":
            "This PC has about {ram} GB of RAM, less than the ~{required} GB"
            " the {quality} model typically needs — even the lightest option"
            " may run slowly or fail. Continue anyway?",
        "capability_low_disk_title": "SOTA",
        "capability_low_disk_message":
            "Not enough free disk space to download this model. It needs"
            " about {required} GB, but only {free} GB is free in:\n{folder}"
            "\n\nFree up some space and try again.",
        "ram_caption": "Detected {ram} GB RAM — {recommended} recommended for this PC",
        "ram_caption_close_apps": "Detected {ram} GB RAM — {recommended} recommended;"
                                   " close other apps first for the best experience",
        "model_info_title": "About the speech models",
        "model_info_body":
            "SOTA can transcribe using two different engines.\n\n"
            "Whisper handles everything by default and works with almost"
            " any language. The Fast / Balanced / Accurate choice above"
            " controls how careful (and how slow) it is.\n\n"
            "SenseVoice is a second engine that's extra accurate"
            " specifically for English, Mandarin, Cantonese, Japanese, and"
            " Korean. Turn on the checkbox above to use it automatically"
            " for those languages — everything else still goes to Whisper.\n\n"
            "Both run entirely on this computer. Nothing is ever uploaded.",
        # --- Edit & Export tab
        "edit_file_label": "File",
        "edit_open_button": "Open a file…",
        "edit_no_file": "No file selected. Transcribe something, or open an audio file.",
        "edit_pick_dialog": "Open an audio file to edit its transcript",
        "player_play": "▶  Play",
        "player_pause": "⏸  Pause",
        "player_stop": "⏹  Stop",
        "player_speed": "Speed",
        "player_preparing": "Preparing {speed}× audio… {pct}%",
        "editor_hint": "Edit the transcription below — click a timestamp to"
                       " hear that part of the recording — then save your copy.",
        "punct_toggle": "Punctuation",
        "timestamps_toggle": "Timestamps",
        "save_button": "Save copy",
        "saved_docx": "Saved Word document: {path}",
        "saved_txt": "Saved text file: {path}",
        "saved_locked_fallback": "The file is open elsewhere (e.g. in Word)"
                                  " and couldn't be overwritten — saved as a"
                                  " new copy instead: {path}",
        "save_failed": "Could not save. See sota.log for details.",
        "save_failed_folder_title": "SOTA",
        "save_failed_folder_message":
            "Could not save to the output folder — it may not be writable."
            "\n\nWould you like to choose a different output folder now?",
        "save_failed_folder_message_mac":
            "Could not save to the output folder — it may not be writable."
            "\n\nOn a Mac, this often happens when the app is still running"
            " from a temporary, read-only location — common if SOTA.app"
            " wasn't moved out of Downloads (or a mounted disk image)"
            " before its first launch. Moving it into Applications (or any"
            " regular folder) and relaunching fixes this permanently."
            "\n\nWould you like to choose a different output folder now as"
            " a quicker fix?",
        "nothing_to_save": "Nothing to save yet — open or select a file first.",
        "audio_load_failed": "Could not open the audio for this file.",
        "no_transcript_found": "Loaded audio, but no transcript was found — you can type one.",
        # --- Live Transcription tab
        "tab_live": "Live Transcription",
        "live_filename_label": "Filename (optional):",
        "live_filename_placeholder": "e.g. Team meeting — leave blank for an automatic name",
        "live_filename_invalid_chars": 'That name can\'t include: < > : " / \\ | ? *',
        "live_filename_trailing_dot_space": "That name can't end with a space or a period.",
        "live_filename_reserved": "That name is reserved by Windows and can't be"
                                  " used — try adding another word to it.",
        "live_filename_too_long": "That name is too long — please shorten it.",
        "live_hint": "This tab uses the SenseVoice engine, which understands"
                     " English, Mandarin, Cantonese, Japanese, and Korean.",
        "live_text_hint": "Transcription is shown below as you speak.",
        "live_start_button": "Start Recording",
        "live_stop_button": "Stop",
        "live_draft_button": "Save draft",
        "live_status_draft_saved": "Draft saved — {path}. Recording continues…",
        "live_status_draft_empty": "No new finished paragraph to save yet —"
                                    " keep going and try again.",
        "edit_new_live_button": "Add new live text",
        "live_placeholder": "Press Start Recording and speak — the transcript"
                             " will appear here as you talk.",
        "live_status_recording": "Recording…",
        "live_status_finalizing": "Finishing up…",
        "live_status_stopping_load": "Stopping — waiting for the SenseVoice"
                                      " download/load already in progress to"
                                      " finish (can't be interrupted mid-way);"
                                      " no recording will start.",
        "live_status_saved": "Saved — {path}",
        "live_status_idle": "Still recording — no speech for a while."
                             " Switch tabs freely; it keeps going until you"
                             " click Stop (or after a very long silence).",
        "live_status_idle_cleared": "Recording…",
        "live_status_saved_idle_stop": "No speech for {minutes} min, so the"
                                        " session stopped itself and saved —"
                                        " {path}.",
        "live_status_no_speech": "No speech detected.",
        "live_status_save_failed": "Could not save the recording — the"
                                    " output folder isn't writable.",
        "live_status_mic_failed": "Could not access the microphone. Check that"
                                   " no other application is using it and that"
                                   " SOTA has microphone permission.",
        "live_status_engine_failed": "SenseVoice could not be loaded. See"
                                      " sota.log for details.",
        "live_status_downloading": "Downloading SenseVoice model (~{size_mb} MB,"
                                    " {pct}%) — first run only, internet required…",
        "live_status_loading": "Loading SenseVoice model… this can take up to a minute.",
        "live_mic_label": "Microphone",
        "live_level_label": "Level",
        "mic_default": "System default",
        # --- AI Summary & Translate tab
        "tab_llm": "AI Summary & Translate",
        "llm_mode_label": "Mode",
        "llm_mode_summarize": "Summarize",
        "llm_mode_translate": "Translate",
        "llm_mode_both": "Both",
        "llm_translate_to": "Translate to",
        "llm_generate": "Generate",
        "llm_left_title": "Transcription",
        "llm_right_title": "AI output — editable when finished",
        "llm_pick_dialog": "Open a transcript or audio file",
        "llm_downloading": "Downloading AI model (~{size} GB, {pct}%) — first run only, internet required…",
        "llm_loading": "Loading AI model…",
        "llm_generating_status": "Generating…",
        "llm_generating_part": "Generating… part {part} of {total}",
        "llm_done": "Finished — you can edit the output and save a copy.",
        "llm_cancelled": "Cancelled.",
        "llm_download_failed": "Could not download the AI model. Connect to the"
                                " internet (needed once per quality level) and try again.",
        "llm_failed": "Something went wrong. See sota.log for details.",
        "llm_no_file": "No transcript loaded — pick a file above, or transcribe something first.",
        "confirm_quit_generating": "AI generation is still running. Stop and quit?",
        "llm_suffix_summary": "summary",
        "llm_suffix_translated": "translated to {lang}",
        "llm_suffix_summary_in": "summary in {lang}",
        # --- Settings tab
        "tab_settings": "Settings",
        "settings_section_prefs": "Preferences",
        "settings_chinese_traditional": "Save Chinese transcripts as Traditional Chinese (繁體)",
        "settings_output_folder": "Output folder",
        "settings_output_change": "Change…",
        "settings_output_reset": "Use default",
        "settings_output_pick_dialog": "Choose where transcripts and recordings are saved",
        "settings_output_not_writable": "Cannot write to that folder — please pick another.",
        "settings_section_models": "Models & storage",
        "settings_models_hint": "Models download automatically the first time they're"
                                 " needed. Pre-download them here while on a good"
                                 " connection, or delete them to free disk space.",
        "settings_model_whisper": "Speech model — {quality} ({size})",
        "settings_model_sensevoice": "SenseVoice engine (incl. voice-activity model)",
        "settings_model_llm": "AI model — {quality}",
        "settings_model_nsnet2": "AI noise reduction model (NSNet2)",
        "settings_model_downloaded": "Downloaded — {size}",
        "settings_model_not_downloaded": "Not downloaded (~{size})",
        "settings_model_download": "Download",
        "settings_model_delete": "Delete",
        "settings_model_downloading": "Downloading… {pct}%",
        "settings_model_loading": "Finishing up…",
        "settings_model_dl_failed": "Download failed — check your internet connection.",
        "settings_model_delete_failed": "Could not delete — restart SOTA and try again.",
        "settings_model_delete_confirm_title": "SOTA",
        "settings_model_delete_confirm": "Delete “{name}”? It will be"
                                          " downloaded again the next time it's needed.",
        "settings_model_busy": "Please wait for the current task to finish first.",
        "settings_total_usage": "Total space used by models: {size}",
        "settings_section_maintenance": "Maintenance",
        "settings_version": "Version {version}",
        "settings_check_updates": "Check for updates",
        "settings_update_checking": "Checking…",
        "settings_update_latest": "You're up to date.",
        "settings_update_available": "Version {version} is available.",
        "settings_update_open": "Open download page",
        "settings_update_failed": "Could not check for updates — are you online?",
        "settings_open_log": "Open log file",
        "settings_open_models": "Open models folder",
        "settings_reset": "Reset all settings",
        "settings_reset_confirm_title": "SOTA",
        "settings_reset_confirm": "Reset all settings to their defaults? Downloaded"
                                   " models and saved transcripts are not affected.",
        "settings_section_safeguards": "Recording Safeguards",
        "settings_safeguard_hint": "Escalating checks for a recording or transcription"
                                   " left running unattended. Warn/Alert only show a"
                                   " dismissible notice and never interrupt anything;"
                                   " Auto-stop actually stops the recording.",
        "settings_safeguard_col_warn": "Warn",
        "settings_safeguard_col_alert": "Alert",
        "settings_safeguard_col_stop": "Auto-stop",
        "settings_safeguard_row_duration": "Recording duration (hours)",
        "settings_safeguard_row_disk": "Free disk space (minutes left)",
        "settings_safeguard_row_ram": "Free system RAM (GB)",
        "safeguard_warn_duration": "A recording has been running for {hours}h"
                                   " (warning threshold: {threshold}h). Still expected?",
        "safeguard_alert_duration": "A recording has been running for {hours}h"
                                    " (alert threshold: {threshold}h) — check it's still needed.",
        "safeguard_stop_duration": "A recording ran past {threshold}h and was"
                                   " automatically stopped and saved.",
        "safeguard_warn_disk": "Low disk space: {free} GB free. The current"
                              " recording/transcription may not have much room left.",
        "safeguard_alert_disk": "Disk space is very low: {free} GB free —"
                                " a recording could stop unexpectedly soon.",
        "safeguard_stop_disk": "Disk space ran critically low ({free} GB free) —"
                               " the recording was automatically stopped and saved"
                               " to avoid a failed write.",
        "safeguard_warn_ram": "Free memory is low ({free} GB) — SOTA or other"
                             " apps may start slowing down.",
        "safeguard_alert_ram": "Free memory is very low ({free} GB) — close some"
                              " other apps if possible.",
        "safeguard_stop_ram": "Free memory ran critically low ({free} GB) — any"
                             " active recording was automatically stopped and"
                             " saved to avoid a crash.",
        # --- Audio Studio (v2)
        "tab_group_audio_studio": "Audio Studio",
        "tab_group_transcription_studio": "Transcription Studio",
        "tab_group_settings": "Settings",
        "tab_audio_record": "Record",
        "tab_audio_edit": "Edit",
        "audio_filetypes": "Audio files",
        "aenh_preview": "Preview",
        "aenh_revert": "Revert",
        "aenh_apply": "Apply",
        # -- Record subtab
        "arec_mic_label": "Microphone",
        "arec_input_card_label": "Input",
        "arec_format_card_label": "Format",
        "arec_rate_label": "Sample rate",
        "arec_channels_label": "Channels",
        "arec_bitdepth_label": "Bit depth",
        "arec_format_label": "Format",
        "arec_level_label": "Level",
        "arec_gain_label": "Input gain",
        "arec_test_mic": "Test Mic",
        "arec_test_mic_stop": "Stop Test",
        "arec_device_using": "Using: {device}",
        "arec_clip_warning": "⚠ Clipping — lower the input gain",
        "arec_filename_label": "Filename (optional):",
        "arec_start_button": "Start Recording",
        "arec_pause": "Pause",
        "arec_resume": "Resume",
        "arec_stop_button": "Stop",
        "arec_mark_button": "Mark",
        "arec_tip_mark": "Drops a labeled marker at this point in the"
                        " recording without stopping — e.g. one per"
                        " speaker turn. Carried over when you send the"
                        " recording to Edit, where each marker's span can"
                        " be renamed and saved as its own file.",
        "arec_partial_save_button": "Partial Save",
        "arec_tip_partial_save": "Exports the audio between two markers as"
                                 " its own file, right now — while this"
                                 " recording keeps going uninterrupted."
                                 " Needs at least 2 markers dropped first.",
        "arec_partial_save_title": "Partial Save",
        "arec_partial_save_hint": "Pick two markers — everything between"
                                  " them is saved as its own file, opened"
                                  " in Edit right after. The main recording"
                                  " is not paused or interrupted.",
        "arec_partial_save_from": "From:",
        "arec_partial_save_to": "To:",
        "arec_partial_save_same_marker": "\"From\" and \"To\" can't be the same marker.",
        "arec_partial_save_bad_range": "\"To\" must come after \"From\" — pick a different pair.",
        "arec_partial_save_saving": "Saving segment…",
        "arec_partial_save_saved": "Saved {path} — opening in Edit…",
        "arec_partial_save_failed": "Partial save failed — see sota.log for details.",
        "arec_edit_button": "Open in Edit",
        "arec_space_label": "This recording so far: {used}    ·    Free space on this drive: {free}",
        "arec_status_recording": "Recording…",
        "arec_status_paused": "Paused",
        "arec_status_saved": "Saved — {path}",
        "arec_status_saved_clipped": "Saved — {path} (clipping detected, consider lowering input gain)",
        "arec_status_failed": "Recording failed — see sota.log for details.",
        "arec_status_low_disk": "Not enough free disk space for at least {minutes} minutes"
                                " of recording at these settings.",
        "arec_status_converting_mp3": "Converting to MP3…",
        # -- Edit subtab
        "aedit_group_clipboard": "Clipboard",
        "aedit_group_structure": "Structure",
        "aedit_group_history": "History & Search",
        "aedit_dirty": "● Unsaved changes",
        "aedit_selection_info": "Selection: {start}–{end} ({dur}s)",
        "aedit_no_selection": "No selection",
        "aedit_view_waveform": "Waveform",
        "aedit_view_spectrogram": "Spectrogram",
        "aedit_tip_view_toggle": "Waveform shows amplitude over time;"
                                 " Spectrogram shows frequency content —"
                                 " useful for finding exactly where a hum,"
                                 " hiss, or specific sound sits.",
        "aedit_spectrogram_caption": "Vertical axis: frequency, 0 Hz at"
                                    " bottom to {nyquist} kHz (this"
                                    " recording's max) at top. Brighter/"
                                    " warmer color = louder at that"
                                    " frequency and moment in time.",
        "aedit_tip_timeline_scroll": "Scroll here to zoom in/out in time,"
                                    " centered on the cursor — same as"
                                    " scrolling on the waveform itself.",
        "aedit_tip_vaxis_scroll": "Scroll here to zoom the waveform's"
                                 " vertical scale — stretches quiet audio"
                                 " taller to see its shape more clearly."
                                 " Double-click to reset.",
        "aedit_marker_rename": "Rename…",
        "aedit_marker_delete": "Delete",
        "aedit_file_status": "{name} · {duration}",
        "aedit_open_dialog": "Open an audio file to edit",
        "aedit_open_button": "Open a file…",
        "aedit_shortcuts_button": "⌨ Keyboard Shortcuts",
        "aedit_shortcuts_title": "Keyboard Shortcuts",
        "aedit_shortcuts_hint": "Audio Studio's Edit tab only — these don't do"
                               " anything while a text field elsewhere has focus.",
        "aedit_shortcut_play": "Play / Pause",
        "aedit_shortcut_undo": "Undo",
        "aedit_shortcut_redo": "Redo",
        "aedit_shortcut_cut": "Cut the current selection",
        "aedit_shortcut_copy": "Copy the current selection",
        "aedit_shortcut_paste": "Paste at the selection start, or the playhead",
        "aedit_shortcut_delete": "Delete (cut without copying) the current selection",
        "aedit_shortcut_zoom_out": "Zoom out",
        "aedit_shortcut_zoom_in": "Zoom in",
        "aedit_shortcut_find": "Find similar… (needs a selection)",
        "aedit_shortcut_home": "Jump to the start",
        "aedit_shortcut_end": "Jump to the end",
        "aedit_shortcut_save": "Export as WAV",
        "aedit_no_file": "No audio file open.",
        "aedit_zoom_fit": "Fit",
        "aedit_cut_button": "Cut",
        "aedit_copy_button": "Copy",
        "aedit_paste_button": "Paste",
        "aedit_trim_button": "Trim",
        "aedit_split_button": "Split",
        "aedit_silence_button": "Insert silence",
        "aedit_undo_button": "Undo",
        "aedit_redo_button": "Redo",
        "aedit_find_button": "Find similar…",
        "aedit_marker_button": "Add marker",
        "aedit_tip_marker": "Adds a labeled marker at the current playhead"
                            " position. Click a marker's flag to jump to"
                            " it; right-click to delete it.",
        "aedit_marker_dialog_title": "Add marker",
        "aedit_marker_dialog_prompt": "Label for this marker:",
        "aedit_tip_trim": "Keeps only the selected region and discards the"
                          " rest. Needs a selection — drag on the waveform first.",
        "aedit_tip_split": "Splits the clip in two at the playhead (click"
                           " the waveform to place it). The part after the"
                           " split is saved as a new file; the part before"
                           " stays open here.",
        "aedit_tip_silence": "Inserts {seconds:.0f}s of silence at the"
                             " playhead, or at the start of the selection"
                             " if one is active.",
        "aedit_tip_find": "Finds other places in the recording that sound"
                          " like the current selection, so you can review"
                          " and remove every occurrence. Needs a selection"
                          " — drag on the waveform first.",
        "aedit_detect_silence_button": "Detect no-speech",
        "aedit_tip_detect_silence": "Finds every stretch of at least"
                                   " {min_gap:.1f}s where no speech was"
                                   " detected — not just true silence, a"
                                   " clap or loud noise counts too — and"
                                   " lists them in the No speech panel"
                                   " below to review. Skips brief natural"
                                   " pauses shorter than that. Doesn't"
                                   " need a selection, it scans the whole"
                                   " clip.",
        "aedit_status_removed_matches": "Removed {count} segment(s).",
        "aedit_save_wav_button": "Export WAV",
        "aedit_save_mp3_button": "Export MP3",
        "aedit_revert_button": "Revert to original",
        "aedit_status_loading": "Loading…",
        "aedit_status_loaded": "Loaded.",
        "aedit_status_load_failed": "Could not open that file — see sota.log for details.",
        "aedit_status_need_selection": "Select a region on the waveform first (click and drag).",
        "aedit_status_effect_applied": "Effect applied.",
        "aedit_status_effect_failed": "Something went wrong — see sota.log for details.",
        "aedit_status_reverted": "Reverted to the original recording.",
        "aedit_status_split": "Split — the second half was saved as {path}.",
        "aedit_status_exporting": "Exporting…",
        "aedit_status_exported": "Exported — {path}",
        "aedit_status_export_failed": "Export failed — see sota.log for details.",
        "aedit_previewing": "Previewing — not applied",
        # -- Enhance subtab
        "aenh_toggle": "Enhance",
        "aenh_preset_button": "✨ Auto Enhance",
        "aenh_preset_tip": "Runs a fixed tone/loudness pass in one step —"
                          " High-pass filter, Compressor, then Normalize"
                          " — on the selected region, or the whole clip"
                          " if nothing's selected, same as every slider"
                          " below. Deliberately doesn't touch noise"
                          " reduction, noise gate, clicks/pops, or"
                          " pauses — those are either heavier processing"
                          " that isn't safe to apply blind, or one-off"
                          " repairs at a specific spot. The three sliders"
                          " it does touch update afterward to show what"
                          " was actually applied. For a sequence of your"
                          " own choosing, see Configurations beside this"
                          " button.",
        "aenh_no_clip": "Open a file in the Edit subtab first.",
        "aenh_selection_hint": "Applies to the selected region on the waveform"
                                " (drag to select) — or the whole clip if"
                                " nothing's selected.",
        "aenh_configs_button": "⚙ Configurations…",
        "aenh_configs_button_tip": "Manage your own saved sequences —"
                                  " create, run, edit, rename, or delete."
                                  " Auto Enhance beside this always runs"
                                  " its own fixed sequence; this is where"
                                  " a sequence of your own choosing lives"
                                  " instead.",
        "aenh_configs_manager_title": "Configurations",
        "aenh_configs_manager_intro": "Your own saved sequences of Enhance"
                                     " steps — run one, open it to change"
                                     " which steps and order it uses, or"
                                     " create a new one from scratch.",
        "aenh_configs_none": "No saved configurations yet.",
        "aenh_config_run": "Run",
        "aenh_config_open": "Open",
        "aenh_config_rename": "Rename",
        "aenh_config_delete_button": "Delete",
        "aenh_config_new_button": "+ New configuration",
        "aenh_config_close_button": "Close",
        "aenh_config_name_taken": "A configuration with that name already exists.",
        "aenh_config_builder_title": "Configuration",
        "aenh_config_name_label": "Name:",
        "aenh_config_name_required": "Enter a name for this configuration.",
        "aenh_config_bad_value": "{step}'s value must be a number.",
        "aenh_config_no_steps": "Check at least one step to include.",
        "aenh_config_pause_tip": "Always runs last, and can't be"
                               " reordered — it's the only step here that"
                               " changes the recording's length rather"
                               " than just reshaping the sound in place,"
                               " so every other (same-length) step has to"
                               " run before it.",
        "aenh_config_save_button": "Save",
        "aenh_config_cancel_button": "Cancel",
        "aenh_heading_tone": "Loudness & tone",
        "aenh_heading_noise": "Noise & frequency",
        "aenh_noise_guide": "• High/Low-pass filter — blocks everything"
                           " above/below a cutoff; suited to steady"
                           " hum/rumble in one frequency range"
                           " (frequency domain)\n"
                           "• Noise gate — mutes stretches that are"
                           " quiet, full stop; suited to noise only"
                           " between words (time domain)\n"
                           "• Noise reduction (below) — suited to noise"
                           " mixed into the speech itself (time and"
                           " frequency domains)",
        "aenh_heading_repair": "Repairs & timing",
        "aenh_amplify": "Amplify",
        "aenh_amplify_tip": "Multiplies the volume by a fixed amount —"
                           " a blunt overall boost/cut. Doesn't adapt to"
                           " how loud different parts already are, unlike"
                           " Normalize or Loudness below.",
        "aenh_normalize": "Normalize",
        "aenh_normalize_tip": "Scales the single loudest sample to sit"
                             " at this level — controls headroom/clipping,"
                             " not how loud the audio sounds overall. For"
                             " perceived loudness (e.g. a podcast target),"
                             " use Loudness (LUFS) below instead.",
        "aenh_loudness": "Loudness (LUFS)",
        "aenh_loudness_tip": "Scales overall perceived loudness to this"
                            " LUFS target — the standard podcasts/streaming"
                            " use. Unlike Normalize above, this accounts for"
                            " how loud the audio actually sounds, not just"
                            " its single loudest peak.",
        "aenh_highpass": "High-pass filter",
        "aenh_highpass_tip": "Cuts low-frequency rumble below this"
                            " frequency — mic handling noise, desk bumps,"
                            " AC/fan hum.",
        "aenh_lowpass": "Low-pass filter",
        "aenh_lowpass_tip": "Cuts high-frequency hiss/noise above this"
                           " frequency.",
        "aenh_eq": "EQ (1 kHz band)",
        "aenh_eq_tip": "Boosts or cuts frequencies around 1 kHz — the"
                      " range that carries most vocal presence/clarity.",
        "aenh_compress": "Compressor",
        "aenh_compress_tip": "Turns down volume once it crosses this"
                            " threshold, narrowing the gap between the"
                            " loudest and quietest parts — makes speech"
                            " sound more consistently loud rather than"
                            " changing the overall level like Amplify.",
        "aenh_denoise": "Noise reduction",
        "aenh_denoise_tip": "Removes background noise (hiss, hum, fan,"
                           " traffic) from the selection using an AI model"
                           " — reaches noise happening even *under* speech."
                           " Different from Noise gate below, which only"
                           " mutes the quiet gaps *between* speech.",
        "aenh_denoise_engine": "Engine used: {engine}",
        "aenh_busy_title": "Applying Auto Enhance…",
        "aenh_busy_title_denoise": "Reducing noise…",
        "aenh_busy_title_silences": "Detecting no-speech…",
        "aenh_profile_engine": "noise profile",
        "aenh_profile_none": "No noise profile captured — using blind denoising above.",
        "aenh_profile_active": "Noise profile: {seconds}s captured — denoise above will use it.",
        "aenh_get_profile_button": "Get noise profile",
        "aenh_profile_tip": "Tunes noise reduction to this recording's own"
                           " specific noise instead of guessing — select a"
                           " noise-only moment (no speech) on the waveform"
                           " first, then click this.",
        "aenh_clear_profile_button": "Clear profile",
        # -- "Repairs & timing" group (was the separate Clean subtab)
        "aclean_pause": "Shorten pauses longer than",
        "aclean_pause_tip": "Shortens any silent gap longer than this many"
                           " seconds down to a brief {keep}s breath,"
                           " instead of cutting it away completely —"
                           " speech segments themselves are never touched.",
        "aclean_gate": "Noise gate",
        "aclean_gate_tip": "Mutes stretches whose volume falls below this"
                          " level — good for hiss/hum in the gaps between"
                          " speech. Different from Noise reduction above,"
                          " which also reaches noise happening under"
                          " speech, not just between it.",
        "aclean_clicks": "Remove clicks/pops",
        "aclean_clicks_tip": "Detects short, abrupt spikes (a mic bump,"
                            " a pop) that jump far outside the normal"
                            " sample-to-sample variation, and smooths them"
                            " out — a spike-detection heuristic, not full"
                            " denoising, so it won't touch steady"
                            " background hiss or hum.",
        # -- Find similar segments panel
        "afind_toggle": "Matches",
        "afind_hint": "Select a region on the waveform, then click"
                      " “Find similar…” to search the whole recording for"
                      " other places that sound like it.",
        "afind_none_found": "No similar segments found.",
        "afind_found": "{count} similar segment(s) found — review and choose which to remove.",
        "afind_none_at_filter": "{count} segment(s) found, but none at this filter level"
                              " — try a lower percentage.",
        "afind_filter_tip": "Shows only matches at or above this score —"
                           " the search itself already only keeps ≥80%"
                           " matches, so this just narrows which of those"
                           " are worth reviewing, without re-searching.",
        "afind_play": "Play",
        "afind_jump": "Jump",
        "afind_select_all": "Select all",
        "afind_select_none": "Select none",
        "afind_delete_button": "Delete selected",
        "afind_searching_title": "Finding similar segments…",
        "afind_searching_status": "Searching… {pct}% done, {count} found so far",
        "afind_cancel_search": "Cancel",
        "afind_cancelling": "Cancelling…",
        # -- No-speech panel (was labeled "Silence" — renamed since a span
        # here just means VAD didn't detect speech, which a loud clap or
        # cough also triggers despite not being remotely quiet)
        "asilence_toggle": "No speech",
        "asilence_hint": "Click “Detect no-speech” above the waveform to"
                        " scan the whole recording for stretches with no"
                        " detected speech.",
        "asilence_none_found": "No no-speech stretches found.",
        "asilence_found": "{count} stretch(es) with no speech found,"
                         " {seconds}s total — review and choose which to"
                         " remove.",
        "asilence_none_at_filter": "{count} stretch(es) found, but none at this"
                                   " filter level — try a shorter minimum.",
        "asilence_filter_tip": "Shows only no-speech stretches at or above this"
                              " length — detection itself already only keeps"
                              " ≥1.5s stretches, so this just narrows which of"
                              " those are worth reviewing, without re-scanning.",
        # -- Markers panel
        "amark_toggle": "Markers",
        "amark_none": "No markers yet — use Add marker above.",
        "amark_found": "{count} marker(s).",
        "amark_save_button": "Save",
        "amark_tip_save": "Saves the audio from this marker up to the next"
                          " one (or the end) as its own WAV file in the"
                          " exports folder.",
        "amark_save_selected_button": "Save Selected as Files",
        "amark_tip_save_selected": "Saves the span after each checked"
                                   " marker (up to the next marker, or the"
                                   " end) as its own WAV file — e.g. one"
                                   " file per speaker turn.",
        "amark_status_saving": "Saving segment(s)…",
        "amark_status_saved": "Saved {path}",
        "amark_status_saved_multi": "Saved {count} file(s) to the exports folder.",
        "amark_status_save_failed": "Save failed — see sota.log for details.",
        # -- AI panel
        "aai_toggle": "AI",
        "aai_hint": "Click Analyze Audio to check this recording for common problems.",
        "aai_analyze_button": "🩺 Analyze Audio",
        "aai_quick_analyze_button": "⚡ Quick Analysis",
        "aai_tip_full_analyze": "Full analysis: noise, clipping, volume"
                                " consistency, long pauses, AND filler"
                                " words/repeated phrases. The last part"
                                " needs transcribing the whole recording"
                                " with Whisper, so for a long recording"
                                " this can take several minutes to tens"
                                " of minutes (cancel anytime).",
        "aai_tip_quick_analyze": "Quick analysis: noise, clipping, volume"
                                 " consistency, and long pauses only —"
                                 " skips filler word/repeated-phrase"
                                 " detection (which needs transcribing the"
                                 " recording), so it finishes in well under"
                                 " a minute even for a very long recording.",
        "aai_health": "Recording Health: {pct}%",
        "aai_problem_noise": "⚠ Background noise detected (noise floor ~{db} dB)",
        "aai_problem_clipping": "⚠ Clipping detected ({count} samples) — consider re-recording with lower input gain",
        "aai_problem_loudness": "⚠ Inconsistent volume (variance {db} dB)",
        "aai_problem_words_unavailable": "Filler words & repeated phrases: download the Whisper"
                                        " \"base\" model in Settings to enable",
        "aai_fix_denoise": "Noise reduction (NSNet2)",
        "aai_fix_denoise_unavailable": "Noise reduction — download the NSNet2 model in Settings to enable",
        "aai_fix_loudness": "Loudness normalization",
        "aai_section_pauses": "Long pauses ({count}) — review each before removing",
        "aai_section_pauses_none_at_filter": "{count} long pause(s) found, but none at"
                                             " this filter level — try a shorter minimum.",
        "aai_pause_filter_tip": "Shows only long pauses at or above this length —"
                               " detection itself already only keeps ≥1.5s pauses,"
                               " so this just narrows which of those are worth"
                               " reviewing, without re-running detection.",
        "aai_section_fillers": "Filler words ({count}) — review each before removing",
        "aai_section_repetitions": "Repeated words/phrases ({count}) — review each before removing",
        "aai_row_pause": "{time}   ({dur}s)",
        "aai_row_filler": "\"{word}\"   {time}",
        "aai_row_repetition": "\"{word}\"   {time}",
        "aai_preset_label": "AI Preset:",
        "aai_preset_apply": "Apply Preset",
        "aai_filler_words_button": "Filler Words…",
        "aai_filler_words_title": "Filler Words",
        "aai_filler_words_intro": "Words or phrases Remove Filler Words looks for"
                                 " (English only) — add or remove entries below."
                                 " Takes effect on the next Analyze Audio.",
        "aai_filler_words_none": "No filler words configured — Remove Filler Words won't flag anything.",
        "aai_filler_words_add_placeholder": "Add a word or phrase…",
        "aai_filler_words_add_button": "Add",
        "aai_filler_words_close_button": "Close",
        "aai_preset_podcast": "Podcast",
        "aai_preset_lecture": "Lecture",
        "aai_preset_meeting": "Meeting",
        "aai_preset_interview": "Interview",
        "aai_preset_youtube": "YouTube Voice",
        "aai_preset_audiobook": "Audiobook",
        "aai_preset_phone": "Phone Recording",
        "aai_apply_button": "AI Enhance",
        "aai_analyzing_title": "Analyzing recording",
        "aai_analyzing_status_initial": "Analyzing a {minutes}-minute recording…"
                                        " estimating time remaining…",
        "aai_analyzing_status_initial_quick": "Quick-analyzing a {minutes}-minute"
                                              " recording (noise/clipping/volume/"
                                              " pauses only)…",
        "aai_analyzing_status": "Analyzing… {pct}% — {eta}",
        "aai_analyzing_eta_estimating": "estimating time remaining…",
        "aai_analyzing_eta_minutes": "about {minutes} min remaining",
        "aai_analyzing_eta_seconds": "about {seconds}s remaining",
        "aai_cancel_analyze": "Cancel",
        "aai_status_cancelled": "Analysis cancelled — showing partial results.",
        "aai_problem_cancelled_partial": "Analysis was cancelled partway through —"
                                         " filler words/repetitions above only cover"
                                         " audio up to where it stopped.",
        "aai_problem_quick_mode": "Quick Analysis — filler words and repeated"
                                 " phrases were skipped. Run the full Analyze"
                                 " Audio to check for those too.",
        "aai_busy_applying": "Applying AI fixes…",
        "aai_status_applied": "Applied {count} fix(es).",
        "aai_status_preset_applied": "Preset applied.",
        "arec_tip_timeline_scroll": "Scroll here to zoom in/out in time,"
                                   " centered on the cursor. Double-click"
                                   " to go back to auto-follow (the whole"
                                   " recording so far).",
        "arec_tip_vaxis_scroll": "Scroll here to zoom the waveform's"
                                " vertical scale — stretches quiet audio"
                                " taller to see its shape more clearly."
                                " Double-click to reset.",
        "aai_status_failed": "AI processing failed — see sota.log for details.",
        "aai_status_nsnet2_needed": "AI Presets need the NSNet2 model — download it in Settings first.",
    },
    "zh": {
        "app_title": "智慧離線轉錄與音訊",
        "tab_transcribe": "轉錄",
        "tab_edit": "編輯與匯出",
        "quality_label": "品質",
        "quality_fast": "快速",
        "quality_balanced": "平衡",
        "quality_accurate": "精確",
        "language_label": "語言",
        "sensevoice_label": "使用 SenseVoice — 英文、中文、粵語、日文、韓文更準確",
        "drop_title": "將音訊檔案拖放到這裡",
        "drop_sub": "或點擊瀏覽 • mp3、wav、m4a、flac、ogg、影片檔…",
        "browse_dialog_title": "選擇音訊檔案",
        "transcribe_button": "開始轉錄全部",
        "transcribing_button": "轉錄中…",
        "cancel_button": "取消",
        "cancelling_button": "取消中…",
        "clear_button": "清除清單",
        "open_output_folder": "開啟輸出資料夾",
        "status_waiting": "等待中",
        "status_transcribing": "轉錄中… {pct}%",
        "status_done": "完成 ✓",
        "status_done_lang": "完成 ✓（{lang}）",
        "status_done_no_speech": "完成 — 未偵測到語音",
        "status_cancelled": "已取消",
        "status_failed_model": "失敗 — 模型無法使用",
        "status_failed_not_found": "失敗 — 找不到檔案",
        "status_failed_write": "失敗 — 無法儲存檔案",
        "status_failed_error": "失敗 — {error}",
        "line_downloading": "正在下載{quality}模型（約 {size_mb} MB，{pct}%）"
                             "— 僅限第一次執行，需要網路連線…",
        "line_loading": "正在載入{quality}模型…",
        "line_sensevoice_downloading": "正在下載 SenseVoice 模型（約 {size_mb} MB，"
                                        "{pct}%）— 僅限第一次執行，需要網路連線…",
        "line_sensevoice_loading": "正在載入 SenseVoice 模型…可能需要一分鐘左右。",
        "line_download_failed": "無法下載模型。請連接網路（每個品質等級僅需一次）後再試一次。",
        "line_load_failed": "無法載入語音模型。詳情請見 sota.log。",
        "line_transcribing": "轉錄中…",
        "line_cancelled": "已取消。",
        "line_finished": "完成。",
        "line_crashed": "發生錯誤。詳情請見 sota.log。",
        "please_wait_batch": "請等待目前批次完成。",
        "cancelling_status": "取消中 — 正在完成目前步驟…",
        "no_audio_found": "找不到音訊檔案。",
        "double_click_tip": "提示：雙擊已完成的檔案，即可在「編輯與匯出」分頁中開啟編輯。",
        "add_files_first_title": "SOTA",
        "add_files_first_message": "請先新增音訊檔案 — 將檔案拖放到視窗中即可。",
        "confirm_quit_title": "SOTA",
        "confirm_quit_message": "轉錄仍在進行中。要停止並離開嗎？",
        "error_dialog_title": "SOTA",
        "error_dialog_message": "發生錯誤：\n{error}\n\n詳情：{log}",
        # --- system capability checks (before a model download)
        "capability_low_ram_title": "SOTA",
        "capability_low_ram_message":
            "這台電腦大約有 {ram} GB 記憶體。{quality}模型通常需要約 {required} GB"
            " 才能順暢執行，可能會執行緩慢或失敗。\n\n建議改用{recommended}。"
            "仍要繼續使用{quality}嗎？",
        "capability_low_ram_message_min":
            "這台電腦大約有 {ram} GB 記憶體，低於{quality}模型所需的約"
            " {required} GB — 即使是最輕量的選項也可能執行緩慢或失敗。"
            "仍要繼續嗎？",
        "capability_low_disk_title": "SOTA",
        "capability_low_disk_message":
            "磁碟空間不足，無法下載此模型。需要約 {required} GB，但下列位置"
            "僅剩 {free} GB 可用：\n{folder}\n\n請騰出空間後再試一次。",
        "ram_caption": "偵測到 {ram} GB 記憶體 — 建議此電腦使用{recommended}",
        "ram_caption_close_apps": "偵測到 {ram} GB 記憶體 — 建議使用{recommended}；"
                                   "為求最佳體驗，請先關閉其他應用程式",
        "model_info_title": "關於語音模型",
        "model_info_body":
            "SOTA 可以使用兩種不同的引擎進行轉錄。\n\n"
            "Whisper 預設處理所有內容，幾乎支援所有語言。上方的"
            "「快速／平衡／精確」選項會決定它的仔細程度（也就是速度）。\n\n"
            "SenseVoice 是第二種引擎，專門針對英文、中文、粵語、日文與"
            "韓文特別準確。勾選上方的選項即可自動對這些語言使用"
            "SenseVoice — 其他語言仍會交給 Whisper 處理。\n\n"
            "兩者都完全在這台電腦上執行，不會上傳任何內容。",
        # --- Edit & Export tab
        "edit_file_label": "檔案",
        "edit_open_button": "開啟檔案…",
        "edit_no_file": "尚未選擇檔案。請先轉錄，或開啟一個音訊檔案。",
        "edit_pick_dialog": "開啟音訊檔案以編輯其轉錄稿",
        "player_play": "▶  播放",
        "player_pause": "⏸  暫停",
        "player_stop": "⏹  停止",
        "player_speed": "速度",
        "player_preparing": "正在準備 {speed}× 音訊… {pct}%",
        "editor_hint": "在下方編輯轉錄稿 — 點擊時間戳記可跳至錄音對應片段 — 然後儲存您的副本。",
        "punct_toggle": "標點符號",
        "timestamps_toggle": "時間戳記",
        "save_button": "儲存副本",
        "saved_docx": "已儲存 Word 文件：{path}",
        "saved_txt": "已儲存文字檔：{path}",
        "saved_locked_fallback": "檔案已在其他程式中開啟（例如 Word），"
                                  "無法覆寫 — 已改儲存為新檔案：{path}",
        "save_failed": "無法儲存。詳情請見 sota.log。",
        "save_failed_folder_title": "SOTA",
        "save_failed_folder_message":
            "無法儲存到輸出資料夾 — 該資料夾可能無法寫入。"
            "\n\n是否要立即選擇其他輸出資料夾？",
        "save_failed_folder_message_mac":
            "無法儲存到輸出資料夾 — 該資料夾可能無法寫入。"
            "\n\n在 Mac 上，這通常是因為應用程式仍在暫存的唯讀位置執行 —"
            "常見於 SOTA.app 在第一次啟動前未從「下載」資料夾（或已掛載的"
            "磁碟映像檔）移出。將它移到「應用程式」資料夾（或任何一般資料夾）"
            "後重新啟動即可徹底解決。"
            "\n\n要不要先選擇其他輸出資料夾作為快速解決方案？",
        "nothing_to_save": "尚無可儲存的內容 — 請先開啟或選擇檔案。",
        "audio_load_failed": "無法開啟此檔案的音訊。",
        "no_transcript_found": "已載入音訊，但找不到轉錄稿 — 您可以自行輸入。",
        # --- Live Transcription tab
        "tab_live": "即時轉錄",
        "live_filename_label": "檔名（選填）：",
        "live_filename_placeholder": "例如：團隊會議 — 留空則自動命名",
        "live_filename_invalid_chars": "名稱不可包含：< > : \" / \\ | ? *",
        "live_filename_trailing_dot_space": "名稱結尾不可為空格或句號。",
        "live_filename_reserved": "此名稱為 Windows 系統保留字，無法使用 —"
                                  "請嘗試加上其他文字。",
        "live_filename_too_long": "名稱過長，請縮短。",
        "live_hint": "此分頁使用 SenseVoice 引擎，支援英文、中文、粵語、日文與韓文。",
        "live_text_hint": "轉錄文字會顯示於下方。",
        "live_start_button": "開始錄音",
        "live_stop_button": "停止",
        "live_draft_button": "儲存草稿",
        "live_status_draft_saved": "已儲存草稿 — {path}，錄音持續中…",
        "live_status_draft_empty": "尚無新的完整段落可儲存 — 請繼續錄音後再試。",
        "edit_new_live_button": "加入新的即時內容",
        "live_placeholder": "按下「開始錄音」後開始說話 — 文字會即時顯示於此。",
        "live_status_recording": "錄音中…",
        "live_status_finalizing": "正在完成…",
        "live_status_stopping_load": "正在停止 — 等待進行中的 SenseVoice"
                                      "下載／載入完成（無法中途中斷）；"
                                      "不會開始錄音。",
        "live_status_saved": "已儲存 — {path}",
        "live_status_idle": "仍在錄音中 — 已有一段時間沒有偵測到語音。"
                             "可自由切換分頁；錄音會持續進行，直到您按下"
                             "「停止」（或靜音時間過長後自動停止）。",
        "live_status_idle_cleared": "錄音中…",
        "live_status_saved_idle_stop": "已 {minutes} 分鐘沒有語音，"
                                        "工作階段已自動停止並儲存 — {path}。",
        "live_status_no_speech": "未偵測到語音。",
        "live_status_save_failed": "無法儲存錄音 — 輸出資料夾無法寫入。",
        "live_status_mic_failed": "無法存取麥克風。請確認沒有其他程式正在使用"
                                   "麥克風，且 SOTA 已取得麥克風權限。",
        "live_status_engine_failed": "無法載入 SenseVoice。詳情請見 sota.log。",
        "live_status_downloading": "正在下載 SenseVoice 模型（約 {size_mb} MB，"
                                    "{pct}%）— 僅限第一次執行，需要網路連線…",
        "live_status_loading": "正在載入 SenseVoice 模型…可能需要一分鐘左右。",
        "live_mic_label": "麥克風",
        "live_level_label": "音量",
        "mic_default": "系統預設",
        # --- AI Summary & Translate tab
        "tab_llm": "AI 摘要與翻譯",
        "llm_mode_label": "模式",
        "llm_mode_summarize": "摘要",
        "llm_mode_translate": "翻譯",
        "llm_mode_both": "兩者",
        "llm_translate_to": "翻譯成",
        "llm_generate": "開始生成",
        "llm_left_title": "轉錄稿",
        "llm_right_title": "AI 輸出 — 完成後可編輯",
        "llm_pick_dialog": "開啟轉錄稿或音訊檔案",
        "llm_downloading": "正在下載 AI 模型（約 {size} GB，{pct}%）— 僅限第一次執行，需要網路連線…",
        "llm_loading": "正在載入 AI 模型…",
        "llm_generating_status": "生成中…",
        "llm_generating_part": "生成中… 第 {part}/{total} 部分",
        "llm_done": "完成 — 您可以編輯輸出並儲存副本。",
        "llm_cancelled": "已取消。",
        "llm_download_failed": "無法下載 AI 模型。請連接網路（每個品質等級僅需一次）後再試一次。",
        "llm_failed": "發生錯誤。詳情請見 sota.log。",
        "llm_no_file": "尚未載入轉錄稿 — 請在上方選擇檔案，或先進行轉錄。",
        "confirm_quit_generating": "AI 生成仍在進行中。要停止並離開嗎？",
        "llm_suffix_summary": "摘要",
        "llm_suffix_translated": "翻譯成{lang}",
        "llm_suffix_summary_in": "{lang}摘要",
        # --- Settings tab
        "tab_settings": "設定",
        "settings_section_prefs": "偏好設定",
        "settings_chinese_traditional": "中文轉錄稿以繁體中文儲存",
        "settings_output_folder": "輸出資料夾",
        "settings_output_change": "變更…",
        "settings_output_reset": "還原預設",
        "settings_output_pick_dialog": "選擇轉錄稿與錄音的儲存位置",
        "settings_output_not_writable": "無法寫入該資料夾 — 請另選一個。",
        "settings_section_models": "模型與儲存空間",
        "settings_models_hint": "模型會在第一次需要時自動下載。您可以趁網路狀況"
                                 "良好時先在此下載，或刪除以釋放磁碟空間。",
        "settings_model_whisper": "語音模型 — {quality}（{size}）",
        "settings_model_sensevoice": "SenseVoice 引擎（含語音活動模型）",
        "settings_model_llm": "AI 模型 — {quality}",
        "settings_model_nsnet2": "AI 降噪模型（NSNet2）",
        "settings_model_downloaded": "已下載 — {size}",
        "settings_model_not_downloaded": "未下載（約 {size}）",
        "settings_model_download": "下載",
        "settings_model_delete": "刪除",
        "settings_model_downloading": "下載中… {pct}%",
        "settings_model_loading": "即將完成…",
        "settings_model_dl_failed": "下載失敗 — 請檢查網路連線。",
        "settings_model_delete_failed": "無法刪除 — 請重新啟動 SOTA 後再試一次。",
        "settings_model_delete_confirm_title": "SOTA",
        "settings_model_delete_confirm": "要刪除「{name}」嗎？下次需要時會重新下載。",
        "settings_model_busy": "請先等待目前工作完成。",
        "settings_total_usage": "模型佔用空間共計：{size}",
        "settings_section_maintenance": "維護",
        "settings_version": "版本 {version}",
        "settings_check_updates": "檢查更新",
        "settings_update_checking": "檢查中…",
        "settings_update_latest": "已是最新版本。",
        "settings_update_available": "有新版本 {version}。",
        "settings_update_open": "開啟下載頁面",
        "settings_update_failed": "無法檢查更新 — 請確認網路連線。",
        "settings_open_log": "開啟記錄檔",
        "settings_open_models": "開啟模型資料夾",
        "settings_reset": "重設所有設定",
        "settings_reset_confirm_title": "SOTA",
        "settings_reset_confirm": "要將所有設定重設為預設值嗎？已下載的模型與"
                                   "已儲存的轉錄稿不受影響。",
        "settings_section_safeguards": "錄音安全防護",
        "settings_safeguard_hint": "針對錄音或轉錄無人看管、持續執行的情況所做的分級檢查。"
                                   "「警告」與「提醒」只會顯示一則可關閉的通知，"
                                   "絕不會中斷任何進行中的作業；「自動停止」則會真的停止錄音。",
        "settings_safeguard_col_warn": "警告",
        "settings_safeguard_col_alert": "提醒",
        "settings_safeguard_col_stop": "自動停止",
        "settings_safeguard_row_duration": "錄音時長（小時）",
        "settings_safeguard_row_disk": "剩餘可用磁碟空間（分鐘）",
        "settings_safeguard_row_ram": "剩餘系統記憶體（GB）",
        "safeguard_warn_duration": "已錄音 {hours} 小時（警告門檻：{threshold} 小時）。"
                                   "確定還需要繼續錄嗎？",
        "safeguard_alert_duration": "已錄音 {hours} 小時（提醒門檻：{threshold} 小時）"
                                    "——請確認是否仍需要這段錄音。",
        "safeguard_stop_duration": "錄音已超過 {threshold} 小時，已自動停止並存檔。",
        "safeguard_warn_disk": "磁碟空間不足：剩餘 {free} GB。目前的錄音／轉錄"
                              "可能沒有太多空間了。",
        "safeguard_alert_disk": "磁碟空間非常不足：剩餘 {free} GB"
                                "——錄音可能很快就會意外中止。",
        "safeguard_stop_disk": "磁碟空間已嚴重不足（剩餘 {free} GB）"
                               "——已自動停止並存檔錄音，以避免寫入失敗。",
        "safeguard_warn_ram": "可用記憶體偏低（{free} GB）"
                             "——SOTA 或其他應用程式可能開始變慢。",
        "safeguard_alert_ram": "可用記憶體非常不足（{free} GB）"
                              "——如果可以，請關閉其他應用程式。",
        "safeguard_stop_ram": "可用記憶體已嚴重不足（{free} GB）"
                             "——目前進行中的錄音已自動停止並存檔，以避免當機。",
        # --- Audio Studio (v2)
        "tab_group_audio_studio": "音訊工作室",
        "tab_group_transcription_studio": "轉錄工作室",
        "tab_group_settings": "設定",
        "tab_audio_record": "錄音",
        "tab_audio_edit": "編輯",
        "audio_filetypes": "音訊檔案",
        "aenh_preview": "預覽",
        "aenh_revert": "還原",
        "aenh_apply": "套用",
        # -- Record subtab
        "arec_mic_label": "麥克風",
        "arec_input_card_label": "輸入來源",
        "arec_format_card_label": "格式",
        "arec_rate_label": "取樣率",
        "arec_channels_label": "聲道數",
        "arec_bitdepth_label": "位元深度",
        "arec_format_label": "格式",
        "arec_level_label": "音量",
        "arec_gain_label": "輸入增益",
        "arec_test_mic": "測試麥克風",
        "arec_test_mic_stop": "停止測試",
        "arec_device_using": "使用中：{device}",
        "arec_clip_warning": "⚠ 削波 — 請降低輸入增益",
        "arec_filename_label": "檔名（選填）：",
        "arec_start_button": "開始錄音",
        "arec_pause": "暫停",
        "arec_resume": "繼續",
        "arec_stop_button": "停止",
        "arec_mark_button": "標記",
        "arec_tip_mark": "在錄音目前位置加上一個帶標籤的標記，且不會中斷錄音"
                        "（例如每次換人說話時標記一次）。傳送錄音至「編輯」時"
                        "會一併帶過去，之後可為每個標記區段重新命名並個別存檔。",
        "arec_partial_save_button": "部分存檔",
        "arec_tip_partial_save": "將兩個標記之間的音訊立即另存為一個檔案"
                                 "——同時本次錄音會持續進行、不受影響。"
                                 "需要先加入至少 2 個標記。",
        "arec_partial_save_title": "部分存檔",
        "arec_partial_save_hint": "選擇兩個標記——兩者之間的所有音訊將另存為"
                                  "一個檔案，儲存後立即在「編輯」中開啟。"
                                  "主錄音不會暫停或中斷。",
        "arec_partial_save_from": "從：",
        "arec_partial_save_to": "到：",
        "arec_partial_save_same_marker": "「從」與「到」不能是同一個標記。",
        "arec_partial_save_bad_range": "「到」必須晚於「從」——請選擇另一組標記。",
        "arec_partial_save_saving": "正在儲存區段…",
        "arec_partial_save_saved": "已儲存 {path} — 正在於編輯中開啟…",
        "arec_partial_save_failed": "部分存檔失敗 — 詳情請見 sota.log。",
        "arec_edit_button": "在編輯中開啟",
        "arec_space_label": "此錄音目前大小：{used}    ·    此磁碟機可用空間：{free}",
        "arec_status_recording": "錄音中…",
        "arec_status_paused": "已暫停",
        "arec_status_saved": "已儲存 — {path}",
        "arec_status_saved_clipped": "已儲存 — {path}（偵測到削波，建議降低輸入增益）",
        "arec_status_failed": "錄音失敗 — 詳情請見 sota.log。",
        "arec_status_low_disk": "磁碟空間不足，無法以目前設定錄製至少 {minutes} 分鐘。",
        "arec_status_converting_mp3": "正在轉換為 MP3…",
        # -- Edit subtab
        "aedit_group_clipboard": "剪貼簿",
        "aedit_group_structure": "結構",
        "aedit_group_history": "歷史紀錄與搜尋",
        "aedit_dirty": "● 尚未儲存的變更",
        "aedit_selection_info": "選取範圍：{start}–{end}（{dur} 秒）",
        "aedit_no_selection": "尚未選取範圍",
        "aedit_view_waveform": "波形圖",
        "aedit_view_spectrogram": "頻譜圖",
        "aedit_tip_view_toggle": "波形圖顯示隨時間變化的音量；"
                                 "頻譜圖顯示頻率內容 — 方便找出"
                                 "嗡嗡聲、嘶聲或特定聲音的確切位置。",
        "aedit_spectrogram_caption": "縱軸：頻率，下方為 0 Hz，上方為"
                                    "此錄音的最高頻率 {nyquist} kHz。"
                                    "顏色越亮／越暖，代表該頻率、"
                                    "該時刻的音量越大。",
        "aedit_tip_timeline_scroll": "在此捲動可依游標位置放大／縮小時間軸"
                                    "— 效果與在波形圖上捲動相同。",
        "aedit_tip_vaxis_scroll": "在此捲動可縮放波形的垂直比例 — "
                                 "將較安靜的聲音拉高，方便看清其形狀。"
                                 "雙擊可重設。",
        "aedit_marker_rename": "重新命名…",
        "aedit_marker_delete": "刪除",
        "aedit_file_status": "{name} · {duration}",
        "aedit_open_dialog": "開啟音訊檔案以進行編輯",
        "aedit_open_button": "開啟檔案…",
        "aedit_shortcuts_button": "⌨ 鍵盤快捷鍵",
        "aedit_shortcuts_title": "鍵盤快捷鍵",
        "aedit_shortcuts_hint": "僅適用於「音訊工作室」的編輯分頁"
                               "——當其他文字欄位取得焦點時不會作用。",
        "aedit_shortcut_play": "播放／暫停",
        "aedit_shortcut_undo": "復原",
        "aedit_shortcut_redo": "重做",
        "aedit_shortcut_cut": "剪下目前選取範圍",
        "aedit_shortcut_copy": "複製目前選取範圍",
        "aedit_shortcut_paste": "貼到選取範圍起點，或播放位置",
        "aedit_shortcut_delete": "刪除（剪下但不複製）目前選取範圍",
        "aedit_shortcut_zoom_out": "縮小",
        "aedit_shortcut_zoom_in": "放大",
        "aedit_shortcut_find": "尋找相似片段…（需要先選取範圍）",
        "aedit_shortcut_home": "跳至開頭",
        "aedit_shortcut_end": "跳至結尾",
        "aedit_shortcut_save": "匯出為 WAV",
        "aedit_no_file": "尚未開啟音訊檔案。",
        "aedit_zoom_fit": "符合視窗",
        "aedit_cut_button": "剪下",
        "aedit_copy_button": "複製",
        "aedit_paste_button": "貼上",
        "aedit_trim_button": "修剪",
        "aedit_split_button": "分割",
        "aedit_silence_button": "插入靜音",
        "aedit_undo_button": "復原",
        "aedit_redo_button": "取消復原",
        "aedit_find_button": "尋找相似片段…",
        "aedit_marker_button": "新增標記",
        "aedit_tip_marker": "在目前播放位置新增一個帶標籤的標記。"
                            "點擊標記旗幟可跳至該處；按右鍵可刪除。",
        "aedit_marker_dialog_title": "新增標記",
        "aedit_marker_dialog_prompt": "此標記的標籤：",
        "aedit_tip_trim": "僅保留選取的範圍，其餘捨棄。需要先在波形圖上拖曳選取範圍。",
        "aedit_tip_split": "在播放位置（點擊波形圖以設定）將錄音分割成兩段："
                           "後半段會儲存為新檔案，前半段則繼續留在此處編輯。",
        "aedit_tip_silence": "在播放位置（若有選取範圍則為選取範圍的起點）"
                             "插入 {seconds:.0f} 秒的靜音。",
        "aedit_tip_find": "在整段錄音中尋找與目前選取範圍聽起來相似的片段，"
                          "方便您逐一檢視並移除。需要先在波形圖上拖曳選取範圍。",
        "aedit_detect_silence_button": "偵測無語音片段",
        "aedit_tip_detect_silence": "找出整段錄音中所有長度達 {min_gap:.1f} 秒"
                                   "以上、未偵測到語音的片段 — 不只是真正的"
                                   "靜音，拍手聲或大聲的噪音也會被視為"
                                   "「無語音」— 並列在下方的「無語音」分頁中"
                                   "供您檢視。較短的自然停頓不會列入。"
                                   "不需要先選取範圍，會掃描整段錄音。",
        "aedit_status_removed_matches": "已移除 {count} 個片段。",
        "aedit_save_wav_button": "匯出 WAV",
        "aedit_save_mp3_button": "匯出 MP3",
        "aedit_revert_button": "還原為原始檔",
        "aedit_status_loading": "載入中…",
        "aedit_status_loaded": "已載入。",
        "aedit_status_load_failed": "無法開啟該檔案 — 詳情請見 sota.log。",
        "aedit_status_need_selection": "請先在波形圖上拖曳選取一段範圍。",
        "aedit_status_effect_applied": "已套用效果。",
        "aedit_status_effect_failed": "發生錯誤 — 詳情請見 sota.log。",
        "aedit_status_reverted": "已還原為原始錄音。",
        "aedit_status_split": "已分割 — 後半段已儲存為 {path}。",
        "aedit_status_exporting": "匯出中…",
        "aedit_status_exported": "已匯出 — {path}",
        "aedit_status_export_failed": "匯出失敗 — 詳情請見 sota.log。",
        "aedit_previewing": "預覽中 — 尚未套用",
        # -- Enhance subtab
        "aenh_toggle": "強化",
        "aenh_preset_button": "✨ 一鍵優化",
        "aenh_preset_tip": "一次執行固定的音量／音色流程 — 高通濾波、"
                          "壓縮器，最後是正規化 — 套用於選取的範圍，"
                          "若未選取範圍則套用於整段錄音，與下方每個"
                          "滑桿的行為相同。特意不包含降噪、雜訊閘、"
                          "爆音／喀嚓聲移除或停頓處理 — 這些若非不適合"
                          "盲目套用的較強處理，就是針對特定位置的"
                          "個別修復。套用後，它實際用到的三個滑桿會"
                          "更新顯示實際套用的數值。若想使用自己選擇的"
                          "流程，請見旁邊的「設定組合」。",
        "aenh_no_clip": "請先在「編輯」分頁中開啟檔案。",
        "aenh_selection_hint": "套用於波形圖上選取的範圍（拖曳以選取）；"
                                "若未選取任何範圍，則套用於整段錄音。",
        "aenh_configs_button": "⚙ 設定組合…",
        "aenh_configs_button_tip": "管理您自己儲存的流程 — 建立、執行、"
                                  "編輯、重新命名或刪除。旁邊的「一鍵"
                                  "優化」永遠執行其固定的流程；您自己"
                                  "選擇的流程則在這裡管理。",
        "aenh_configs_manager_title": "設定組合",
        "aenh_configs_manager_intro": "您自己儲存的強化步驟流程 — 可執行、"
                                     "開啟以修改所使用的步驟與順序，"
                                     "或從頭建立新的流程。",
        "aenh_configs_none": "尚無已儲存的設定組合。",
        "aenh_config_run": "執行",
        "aenh_config_open": "開啟",
        "aenh_config_rename": "重新命名",
        "aenh_config_delete_button": "刪除",
        "aenh_config_new_button": "+ 新增設定組合",
        "aenh_config_close_button": "關閉",
        "aenh_config_name_taken": "已存在同名的設定組合。",
        "aenh_config_builder_title": "設定組合",
        "aenh_config_name_label": "名稱：",
        "aenh_config_name_required": "請輸入此設定組合的名稱。",
        "aenh_config_bad_value": "「{step}」的數值必須是數字。",
        "aenh_config_no_steps": "請至少勾選一個步驟。",
        "aenh_config_pause_tip": "永遠排在最後，且無法調整順序 — 這是"
                               "唯一會改變錄音「長度」的步驟，而非僅"
                               "重塑聲音本身，因此其他（不改變長度的）"
                               "步驟都必須先於它執行。",
        "aenh_config_save_button": "儲存",
        "aenh_config_cancel_button": "取消",
        "aenh_heading_tone": "音量與音色",
        "aenh_heading_noise": "噪音與頻率",
        "aenh_noise_guide": "• 高通／低通濾波器 — 封鎖截止頻率以上／以下的"
                           "所有頻率；適合單一頻段的持續嗡嗡聲／隆隆聲"
                           "（頻率域）\n"
                           "• 雜訊閘 — 直接將偏安靜的段落靜音；適合"
                           "只出現在字句之間的噪音（時間域）\n"
                           "• 降噪（下方）— 適合混雜在語音本身之中的噪音"
                           "（時間與頻率域）",
        "aenh_heading_repair": "修復與時間軸",
        "aenh_amplify": "增益",
        "aenh_amplify_tip": "以固定倍率調整音量 — 是概略的整體增減，"
                           "不會因錄音各段原本音量不同而調整，"
                           "與下方的「正規化」或「音量標準化」不同。",
        "aenh_normalize": "正規化",
        "aenh_normalize_tip": "將整段中最大聲的取樣點調整到此音量 — "
                             "控制的是動態餘裕／避免削波，並非整體聽感"
                             "音量。若要調整聽感音量（如 Podcast 標準），"
                             "請改用下方的「音量標準化（LUFS）」。",
        "aenh_loudness": "音量標準化（LUFS）",
        "aenh_loudness_tip": "將整體聽感音量調整到此 LUFS 目標值 — "
                            "Podcast／串流平台常用的標準。與上方的"
                            "「正規化」不同，這會考量實際聽起來的音量，"
                            "而非只看最大峰值。",
        "aenh_highpass": "高通濾波器",
        "aenh_highpass_tip": "切除此頻率以下的低頻隆隆聲 — 例如麥克風"
                            "碰撞聲、桌面震動、空調／風扇噪音。",
        "aenh_lowpass": "低通濾波器",
        "aenh_lowpass_tip": "切除此頻率以上的高頻嘶聲／雜訊。",
        "aenh_eq": "等化器（1 kHz 頻段）",
        "aenh_eq_tip": "增強或衰減 1 kHz 附近的頻率 — 這是人聲清晰度"
                      "與存在感最主要的頻段。",
        "aenh_compress": "壓縮器",
        "aenh_compress_tip": "音量超過此閾值時自動調低，縮小最大聲與"
                            "最小聲之間的差距 — 讓語音音量更一致，"
                            "與整體調高／調低音量的「增益」不同。",
        "aenh_denoise": "降噪",
        "aenh_denoise_tip": "使用 AI 模型移除選取範圍的背景噪音（嘶聲、"
                           "電流聲、風扇、交通噪音）— 連語音「底下」的"
                           "噪音也能處理。與下方的「雜訊閘」不同，"
                           "雜訊閘只會靜音語音之間的空隙。",
        "aenh_denoise_engine": "使用引擎：{engine}",
        "aenh_busy_title": "正在套用一鍵優化…",
        "aenh_busy_title_denoise": "正在降噪…",
        "aenh_busy_title_silences": "正在偵測無語音片段…",
        "aenh_profile_engine": "雜訊樣本",
        "aenh_profile_none": "尚未擷取雜訊樣本 — 上方降噪將使用一般模式。",
        "aenh_profile_active": "雜訊樣本：已擷取 {seconds} 秒 — 上方降噪將使用此樣本。",
        "aenh_get_profile_button": "擷取雜訊樣本",
        "aenh_profile_tip": "讓降噪針對這段錄音「特有」的噪音調整，"
                           "而非用通用猜測 — 請先在波形圖上選取一段"
                           "只有噪音、沒有語音的範圍，再點擊此按鈕。",
        "aenh_clear_profile_button": "清除樣本",
        # -- 「修復與時間軸」群組（原本是獨立的「清理」分頁）
        "aclean_pause": "縮短超過此長度的停頓",
        "aclean_pause_tip": "將長度超過此秒數的靜音停頓，縮短為 {keep} 秒"
                           "的短暫停頓，而非整段剪掉 — 語音段落本身"
                           "完全不會被更動。",
        "aclean_gate": "雜訊閘",
        "aclean_gate_tip": "將音量低於此水準的段落靜音 — 適合處理語音"
                          "之間空隙中的嘶聲／電流聲。與上方的「降噪」"
                          "不同，降噪連語音底下的噪音也能處理，"
                          "不只是語音之間的空隙。",
        "aclean_clicks": "移除爆音／喀嚓聲",
        "aclean_clicks_tip": "偵測明顯超出正常取樣變化範圍的短暫尖峰"
                            "（如麥克風碰撞聲、爆音），並將其平滑處理 — "
                            "屬於尖峰偵測的簡易演算法，並非完整降噪，"
                            "不會處理持續性的背景嘶聲或電流聲。",
        # -- Find similar segments panel
        "afind_toggle": "相似片段",
        "afind_hint": "先在波形圖上選取一段範圍，再點擊「尋找相似片段…」"
                      "即可在整段錄音中搜尋聽起來相似的其他片段。",
        "afind_none_found": "找不到相似的片段。",
        "afind_found": "找到 {count} 個相似片段 — 請檢視並選擇要移除的項目。",
        "afind_none_at_filter": "找到 {count} 個片段，但在此篩選條件下沒有符合的"
                              " — 請嘗試較低的百分比。",
        "afind_filter_tip": "只顯示達到此分數以上的相似片段 — 搜尋本身已"
                           "只保留 80% 以上的結果，這裡只是進一步篩選要"
                           "檢視哪些，不會重新搜尋。",
        "afind_play": "播放",
        "afind_jump": "跳至",
        "afind_select_all": "全選",
        "afind_select_none": "全不選",
        "afind_delete_button": "刪除已勾選",
        "afind_searching_title": "正在尋找相似片段…",
        "afind_searching_status": "搜尋中… 已完成 {pct}%，目前找到 {count} 個",
        "afind_cancel_search": "取消",
        "afind_cancelling": "正在取消…",
        # -- No-speech panel（原稱「靜音」— 已更名，因為只要 VAD 未偵測到
        # 語音就會被列入，拍手聲或咳嗽聲等明顯不安靜的聲音也會被列入）
        "asilence_toggle": "無語音",
        "asilence_hint": "點擊波形圖上方的「偵測無語音片段」，即可掃描整段"
                        "錄音找出未偵測到語音的片段。",
        "asilence_none_found": "找不到無語音的片段。",
        "asilence_found": "找到 {count} 個無語音片段，共 {seconds} 秒 — "
                         "請檢視並選擇要移除的項目。",
        "asilence_none_at_filter": "找到 {count} 個片段，但在此篩選條件下沒有符合的"
                                   "——請嘗試較短的最短長度。",
        "asilence_filter_tip": "只顯示長度達到此門檻以上的無語音片段"
                              "——偵測本身已只保留 ≥1.5 秒的片段，"
                              "此篩選只是進一步縮小要檢視的範圍，不會重新掃描。",
        # -- Markers panel
        "amark_toggle": "標記",
        "amark_none": "尚無標記 — 可使用上方的「新增標記」。",
        "amark_found": "共 {count} 個標記。",
        "amark_save_button": "存檔",
        "amark_tip_save": "將此標記到下一個標記（或結尾）之間的音訊，"
                          "另存為一個 WAV 檔至匯出資料夾。",
        "amark_save_selected_button": "將已勾選的分別存檔",
        "amark_tip_save_selected": "為每個已勾選的標記，將其到下一個標記"
                                   "（或結尾）之間的音訊分別存成一個 WAV 檔"
                                   "——例如每位發言者一個檔案。",
        "amark_status_saving": "正在儲存區段…",
        "amark_status_saved": "已儲存 {path}",
        "amark_status_saved_multi": "已儲存 {count} 個檔案至匯出資料夾。",
        "amark_status_save_failed": "儲存失敗 — 詳情請見 sota.log。",
        # -- AI panel
        "aai_toggle": "AI",
        "aai_hint": "點擊「分析音訊」以檢查這段錄音的常見問題。",
        "aai_analyze_button": "🩺 分析音訊",
        "aai_quick_analyze_button": "⚡ 快速分析",
        "aai_tip_full_analyze": "完整分析：噪音、削波、音量一致性、長停頓，"
                                "以及贅字／重複詞句。最後一項需要用 Whisper"
                                "轉錄整段錄音，因此錄音較長時可能需要數分鐘"
                                "到數十分鐘（可隨時取消）。",
        "aai_tip_quick_analyze": "快速分析：僅檢查噪音、削波、音量一致性與"
                                 "長停頓——略過贅字／重複詞句偵測（需要轉錄"
                                 "錄音），因此即使錄音很長，也能在一分鐘內"
                                 "完成。",
        "aai_health": "錄音健康度：{pct}%",
        "aai_problem_noise": "⚠ 偵測到背景噪音（噪音基準約 {db} dB）",
        "aai_problem_clipping": "⚠ 偵測到削波（{count} 個樣本）— 建議降低輸入增益後重新錄製",
        "aai_problem_loudness": "⚠ 音量不穩定（變異量 {db} dB）",
        "aai_problem_words_unavailable": "贅字與重複詞句 — 請至設定頁下載 Whisper「base」模型以啟用",
        "aai_fix_denoise": "降噪（NSNet2）",
        "aai_fix_denoise_unavailable": "降噪 — 請至設定頁下載 NSNet2 模型以啟用",
        "aai_fix_loudness": "音量標準化",
        "aai_section_pauses": "長停頓（{count}）— 移除前請逐一確認",
        "aai_section_pauses_none_at_filter": "找到 {count} 個長停頓，但在此篩選條件下"
                                             "沒有符合的 — 請嘗試較短的最短長度。",
        "aai_pause_filter_tip": "只顯示長度達到此門檻以上的長停頓"
                               "——偵測本身已只保留 ≥1.5 秒的停頓，"
                               "此篩選只是進一步縮小要檢視的範圍，不會重新偵測。",
        "aai_section_fillers": "贅字（{count}）— 移除前請逐一確認",
        "aai_section_repetitions": "重複字詞／片語（{count}）— 移除前請逐一確認",
        "aai_row_pause": "{time}   （{dur}秒）",
        "aai_row_filler": "「{word}」   {time}",
        "aai_row_repetition": "「{word}」   {time}",
        "aai_preset_label": "AI 預設：",
        "aai_preset_apply": "套用預設",
        "aai_filler_words_button": "贅字清單…",
        "aai_filler_words_title": "贅字清單",
        "aai_filler_words_intro": "「移除贅字」比對的字詞或片語（僅限英文）— "
                                 "可在下方新增或移除。下次「分析音訊」時生效。",
        "aai_filler_words_none": "尚未設定任何贅字 — 「移除贅字」將不會標記任何內容。",
        "aai_filler_words_add_placeholder": "新增字詞或片語…",
        "aai_filler_words_add_button": "新增",
        "aai_filler_words_close_button": "關閉",
        "aai_preset_podcast": "Podcast",
        "aai_preset_lecture": "課程講座",
        "aai_preset_meeting": "會議",
        "aai_preset_interview": "訪談",
        "aai_preset_youtube": "YouTube 人聲",
        "aai_preset_audiobook": "有聲書",
        "aai_preset_phone": "電話錄音",
        "aai_apply_button": "AI 強化",
        "aai_analyzing_title": "正在分析錄音",
        "aai_analyzing_status_initial": "正在分析一段 {minutes} 分鐘的錄音…"
                                        "正在估算剩餘時間…",
        "aai_analyzing_status_initial_quick": "正在快速分析一段 {minutes} 分鐘的錄音"
                                              "（僅噪音／削波／音量／停頓）…",
        "aai_analyzing_status": "分析中… {pct}% — {eta}",
        "aai_analyzing_eta_estimating": "正在估算剩餘時間…",
        "aai_analyzing_eta_minutes": "剩餘約 {minutes} 分鐘",
        "aai_analyzing_eta_seconds": "剩餘約 {seconds} 秒",
        "aai_cancel_analyze": "取消",
        "aai_status_cancelled": "分析已取消 — 顯示部分結果。",
        "aai_problem_cancelled_partial": "分析在中途被取消 — 以上的贅字／重複詞句"
                                         "僅涵蓋到取消當下的音訊。",
        "aai_problem_quick_mode": "快速分析 — 已略過贅字與重複詞句偵測。"
                                 "如需檢查這些項目，請執行完整的「分析音訊」。",
        "aai_busy_applying": "正在套用 AI 修正…",
        "aai_status_applied": "已套用 {count} 項修正。",
        "aai_status_preset_applied": "已套用預設。",
        "arec_tip_timeline_scroll": "在此捲動可依游標位置放大／縮小時間軸。"
                                   "雙擊可回到自動跟隨（顯示目前為止的整段錄音）。",
        "arec_tip_vaxis_scroll": "在此捲動可縮放波形的垂直比例 — "
                                "讓較安靜的聲音也能看清波形。雙擊可重設。",
        "aai_status_failed": "AI 處理失敗 — 詳情請見 sota.log。",
        "aai_status_nsnet2_needed": "AI 預設需要 NSNet2 模型 — 請先至設定頁下載。",
    },
}

# (canonical quality key, model size) — display names come from STRINGS above.
QUALITY_KEYS = ["fast", "balanced", "accurate"]

# (canonical language code key, English name, Traditional Chinese name).
# code key is "auto" or an ISO 639-1 code understood by faster-whisper.
TRANSCRIBE_LANGUAGES = [
    ("auto", "Auto-detect", "自動偵測"),
    ("en", "English", "英文"),
    ("zh", "Chinese", "中文"),
    ("yue", "Cantonese", "粵語"),
    ("ms", "Malay", "馬來文"),
    ("id", "Indonesian", "印尼文"),
    ("es", "Spanish", "西班牙文"),
    ("fr", "French", "法文"),
    ("de", "German", "德文"),
    ("ja", "Japanese", "日文"),
    ("ko", "Korean", "韓文"),
    ("hi", "Hindi", "印地文"),
    ("ta", "Tamil", "坦米爾文"),
    ("ar", "Arabic", "阿拉伯文"),
    ("pt", "Portuguese", "葡萄牙文"),
    ("ru", "Russian", "俄文"),
    ("it", "Italian", "義大利文"),
    ("nl", "Dutch", "荷蘭文"),
    ("th", "Thai", "泰文"),
    ("vi", "Vietnamese", "越南文"),
    ("tr", "Turkish", "土耳其文"),
]
_LANG_INDEX = {code: (en, zh) for code, en, zh in TRANSCRIBE_LANGUAGES}

# Live Transcription tab only ever runs SenseVoice, so its language picker is
# a subset of TRANSCRIBE_LANGUAGES restricted to what that engine covers.
LIVE_LANGUAGE_CODES = ["auto", "en", "zh", "yue", "ja", "ko"]


def live_language_options(ui_lang):
    return [language_display(code, ui_lang) for code in LIVE_LANGUAGE_CODES]


def live_language_key_for_display(display, ui_lang):
    for code in LIVE_LANGUAGE_CODES:
        if language_display(code, ui_lang) == display:
            return code
    return "auto"


def t(ui_lang, key, **kwargs):
    template = STRINGS.get(ui_lang, STRINGS["en"]).get(key) or STRINGS["en"][key]
    return template.format(**kwargs) if kwargs else template


def quality_display(quality_key, ui_lang):
    return t(ui_lang, f"quality_{quality_key}")


def quality_options(ui_lang):
    return [quality_display(k, ui_lang) for k in QUALITY_KEYS]


def quality_key_for_display(display, ui_lang):
    for key in QUALITY_KEYS:
        if quality_display(key, ui_lang) == display:
            return key
    return "balanced"


def language_display(code_key, ui_lang):
    names = _LANG_INDEX.get(code_key)
    if names:
        return names[0] if ui_lang == "en" else names[1]
    return code_key


def language_options(ui_lang):
    return [language_display(code, ui_lang) for code, _, _ in TRANSCRIBE_LANGUAGES]


def language_key_for_display(display, ui_lang):
    for code, en, zh in TRANSCRIBE_LANGUAGES:
        if (en if ui_lang == "en" else zh) == display:
            return code
    return "auto"


def detected_language_name(code, ui_lang):
    names = _LANG_INDEX.get(code)
    if names:
        return names[0] if ui_lang == "en" else names[1]
    return code.upper() if code else "?"


def added_files_text(n, ui_lang):
    if ui_lang == "zh":
        return f"已新增 {n} 個檔案"
    return f"Added {n} file{'s' if n != 1 else ''}"


def duplicates_text(n, ui_lang):
    if ui_lang == "zh":
        return f"{n} 個已在清單中"
    return f"{n} already in the list"


def skipped_text(n, ui_lang):
    if ui_lang == "zh":
        return f"{n} 個不支援的檔案已略過"
    return f"{n} unsupported file{'s' if n != 1 else ''} skipped"


def job_status_text(ui_lang, key, detail):
    detail = detail or {}
    if key == "transcribing":
        return t(ui_lang, "status_transcribing", pct=detail.get("pct", 0))
    if key == "done_lang":
        return t(ui_lang, "status_done_lang",
                  lang=detected_language_name(detail.get("code", ""), ui_lang))
    if key == "failed_error":
        return t(ui_lang, "status_failed_error", error=detail.get("error", ""))
    return t(ui_lang, f"status_{key}")


# --------------------------------------------------- AI summarize/translate

LLM_MODES = ["summarize", "translate", "both"]

# (canonical key, name used inside the LLM prompt, English display, 繁中 display)
LLM_TARGET_LANGUAGES = [
    ("zh-hant", "Traditional Chinese", "Traditional Chinese", "繁體中文"),
    ("zh-hans", "Simplified Chinese", "Simplified Chinese", "簡體中文"),
    ("en", "English", "English", "英文"),
    ("ms", "Malay", "Malay", "馬來文"),
    ("id", "Indonesian", "Indonesian", "印尼文"),
    ("ja", "Japanese", "Japanese", "日文"),
    ("ko", "Korean", "Korean", "韓文"),
    ("es", "Spanish", "Spanish", "西班牙文"),
    ("fr", "French", "French", "法文"),
    ("de", "German", "German", "德文"),
    ("pt", "Portuguese", "Portuguese", "葡萄牙文"),
    ("ru", "Russian", "Russian", "俄文"),
    ("it", "Italian", "Italian", "義大利文"),
    ("nl", "Dutch", "Dutch", "荷蘭文"),
    ("th", "Thai", "Thai", "泰文"),
    ("vi", "Vietnamese", "Vietnamese", "越南文"),
    ("tr", "Turkish", "Turkish", "土耳其文"),
    ("ar", "Arabic", "Arabic", "阿拉伯文"),
    ("hi", "Hindi", "Hindi", "印地文"),
    ("ta", "Tamil", "Tamil", "坦米爾文"),
]
_LLM_TARGET_INDEX = {key: (prompt, en, zh) for key, prompt, en, zh in LLM_TARGET_LANGUAGES}


def llm_mode_display(mode_key, ui_lang):
    return t(ui_lang, f"llm_mode_{mode_key}")


def llm_mode_options(ui_lang):
    return [llm_mode_display(k, ui_lang) for k in LLM_MODES]


def llm_mode_key_for_display(display, ui_lang):
    for key in LLM_MODES:
        if llm_mode_display(key, ui_lang) == display:
            return key
    return "summarize"


def llm_target_display(key, ui_lang):
    entry = _LLM_TARGET_INDEX.get(key)
    if entry:
        return entry[1] if ui_lang == "en" else entry[2]
    return key


def llm_target_options(ui_lang):
    return [llm_target_display(key, ui_lang) for key, _, _, _ in LLM_TARGET_LANGUAGES]


def llm_target_key_for_display(display, ui_lang):
    for key, _prompt, en, zh in LLM_TARGET_LANGUAGES:
        if (en if ui_lang == "en" else zh) == display:
            return key
    return LLM_TARGET_LANGUAGES[0][0]


def llm_target_prompt_name(key):
    entry = _LLM_TARGET_INDEX.get(key)
    return entry[0] if entry else key


def llm_output_suffix(ui_lang, mode, target_key):
    """Filename suffix for the saved AI output, e.g. 'summary' or
    'translated Japanese'."""
    lang = llm_target_display(target_key, ui_lang)
    if mode == "summarize":
        return t(ui_lang, "llm_suffix_summary")
    if mode == "translate":
        return t(ui_lang, "llm_suffix_translated", lang=lang)
    return t(ui_lang, "llm_suffix_summary_in", lang=lang)
