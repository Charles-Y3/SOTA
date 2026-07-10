"""All user-facing text for SOTA, in English and Traditional Chinese."""

UI_LANGUAGES = ["en", "zh"]

STRINGS = {
    "en": {
        "app_title": "SOTA — Smart Offline Transcription Application",
        "tab_transcribe": "Transcribe",
        "tab_edit": "Edit & Export",
        "quality_label": "Quality",
        "quality_fast": "Fast",
        "quality_balanced": "Balanced",
        "quality_accurate": "Accurate",
        "language_label": "Language",
        "timestamps_label": "Timestamps",
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
        "editor_hint": "Edit the transcription below, then save your copy.",
        "punct_toggle": "Punctuation",
        "save_button": "Save copy",
        "saved_docx": "Saved Word document: {path}",
        "saved_txt": "Saved text file: {path}",
        "save_failed": "Could not save. See sota.log for details.",
        "nothing_to_save": "Nothing to save yet — open or select a file first.",
        "audio_load_failed": "Could not open the audio for this file.",
        "no_transcript_found": "Loaded audio, but no transcript was found — you can type one.",
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
    },
    "zh": {
        "app_title": "SOTA — 智慧離線轉錄應用程式",
        "tab_transcribe": "轉錄",
        "tab_edit": "編輯與匯出",
        "quality_label": "品質",
        "quality_fast": "快速",
        "quality_balanced": "平衡",
        "quality_accurate": "精確",
        "language_label": "語言",
        "timestamps_label": "時間戳記",
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
        "editor_hint": "在下方編輯轉錄稿，然後儲存您的副本。",
        "punct_toggle": "標點符號",
        "save_button": "儲存副本",
        "saved_docx": "已儲存 Word 文件：{path}",
        "saved_txt": "已儲存文字檔：{path}",
        "save_failed": "無法儲存。詳情請見 sota.log。",
        "nothing_to_save": "尚無可儲存的內容 — 請先開啟或選擇檔案。",
        "audio_load_failed": "無法開啟此檔案的音訊。",
        "no_transcript_found": "已載入音訊，但找不到轉錄稿 — 您可以自行輸入。",
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
