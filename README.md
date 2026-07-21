# SOTA — Smart Offline Transcription Application

**Version 1.2.2**

Drop in audio files, click **Transcribe All**, and get a transcript for each
file — saved as `.docx` if Microsoft Word is installed, otherwise `.txt`. Or
dictate straight from your microphone in the **Live Transcription** tab.
Everything runs locally on your computer — after the one-time model
download(s), no internet is needed and no audio ever leaves your machine.

## The five tabs

1. **Transcribe** — drop audio files in, get a transcript for each.
2. **Live Transcription** — dictate from the microphone; auto-saves when you stop.
3. **Edit & Export** — replay a file, fix the transcript, save a copy;
   click a paragraph's timestamp to jump the playback there.
4. **AI Summary & Translate** — summarize and/or translate a transcript with a local AI model.
5. **Settings** — manage downloaded models, pick the output folder, check for updates.

## Transcribe tab

1. Drag & drop audio files into the window (or click the drop zone to browse).
   Supported: mp3, wav, m4a, flac, ogg, opus, wma, aac and common video files
   (the audio track is used).
2. Pick your options — all remembered for next time:
   - **Quality**: Fast (Whisper base) / Balanced (Whisper small) / Accurate
     (Whisper large-v3-turbo) — higher = better text, slower.
   - **Language**: Auto-detect works well; set it manually if needed.
   - **SenseVoice**: a second, more accurate engine for English, Mandarin,
     Cantonese, Japanese, and Korean — check the box to use it whenever the
     picked (or auto-detected) language is one of those five. Everything
     else still uses the Fast/Balanced/Accurate model above. The **ⓘ**
     button next to it explains the Whisper/SenseVoice split in plain
     language.
   - Transcripts always save into `output\Transcriptions` next to the app.
3. Click **Transcribe All**. Each file shows its own progress and any file
   that fails won't stop the rest.

The first time you use a quality level (or turn on SenseVoice), the app
downloads the corresponding speech model — Fast ~145 MB, Balanced ~480 MB,
Accurate ~1.5 GB, SenseVoice + its voice-activity model ~900 MB combined.
That needs internet once; afterwards the app is fully offline. Punctuation
(commas, periods, question marks) is added automatically.

Mandarin and Cantonese transcripts are saved in **Traditional Chinese
(繁體中文)** by default — speech engines otherwise tend to produce
Simplified. This applies to both the Transcribe and Live Transcription tabs
and can be turned off in the Settings tab. Other languages (including
Japanese) are never touched.

### Interface language

A toggle in the top-right corner switches the whole app between **English**
and **Traditional Chinese (繁體中文)**. Your choice is remembered.

## Live Transcription tab

Dictate straight from your microphone instead of recording a file first:

1. Pick a language — **Auto-detect** or one of English / Mandarin /
   Cantonese / Japanese / Korean. This tab only supports those five (the
   ones the SenseVoice engine covers); use the Transcribe tab for anything
   else. Next to the language picker, a **Microphone** dropdown lets you
   choose which input device to record from (refreshed every time you open
   the tab, so a newly plugged-in mic shows up right away); your choice is
   remembered. An optional **Name** field lets you give the session a
   filename up front (e.g. "Team meeting") instead of the automatic
   "Live *date time*" name — leave it blank for the automatic name, which
   gets filled into the field once recording starts either way. The name
   locks once you start (matching the language/microphone choice) and the
   field clears for the next session; a name with characters Windows
   doesn't allow in filenames is flagged immediately, before recording
   starts.
2. Click **Start Recording** and speak. Text appears as you talk — SOTA only
   re-processes the part of the recording since your last natural pause, so
   it stays responsive no matter how long the session runs. A small level
   meter next to the buttons shows that the chosen mic is actually hearing
   you — if it stays empty while you talk, the wrong (or a muted) device is
   selected.
3. Click **Stop**. The session is auto-saved: the transcript goes to
   `output\Transcriptions` (right alongside recorded-file transcripts), and
   the raw audio recording is saved as a `.wav` in
   `output\Live Recordings`. The recording is also added to the **Edit &
   Export** tab's file list automatically, so you can play it back and
   correct the text right away.

During a long session you don't have to wait for Stop: switching to another
tab no longer stops the recording — it keeps dictating in the background
(a colored dot on the **Live Transcription** tab shows a session is still
running), so you can work in **Edit & Export** at the same time. Every
finished paragraph is saved to the transcript file automatically as soon as
it's ready, without interrupting the recording; a **Save draft** button is
also there if you want to force a save right now. Each save appends only
the new paragraphs to the same file — and if you have it open in the
editor when that happens, an **Add new live text** button appears there;
clicking it appends the new
paragraphs to the end of what you're editing and refreshes the audio
player to match, keeping your current playback position (saving also
pulls them in automatically, so an edited copy is never missing text — or
audio — the session already produced). If the room goes quiet for a long
stretch, the session keeps
going (a real pause is common and never cuts you off) — it only stops
itself after roughly 50 minutes of continuous silence, which by then has
already been auto-saved.

The first time you use this tab, the SenseVoice model needs to load into
memory — this takes real time (tens of seconds) even if it's already
downloaded, and longer the very first time it needs to download. SOTA shows
what's happening (downloading vs. loading, with a progress percentage while
downloading) both in the status line and directly in the text area, so it's
clear the app is working rather than stuck.

The recording is written to its `.wav` file continuously while you speak
(not held in memory until you press Stop), so sessions can run for hours
without eating RAM — and even if the app or the PC dies mid-session,
everything captured up to that moment is already on disk and playable.

## Edit & Export tab

After transcribing (recorded or live), open the **Edit & Export** tab to
proof-read against the audio:

1. Pick a file from the dropdown (or **Open a file…** to load any audio; its
   transcript is found automatically if one exists). The file dialog opens
   in the `output\Transcriptions` folder by default, since that's usually
   where the file you want is.
2. **Play / Pause / Stop**, scrub with the slider, and change **Speed**
   (0.5×–2×) — uses WSOLA time-stretching (searches for the best-aligned
   splice point instead of blindly gluing fixed-size chunks together), so
   both pitch and intelligibility stay natural even at 0.5×.
   Each paragraph starts with a blue **[mm:ss] timestamp — click it and
   playback jumps straight to that moment**, so finding the spot you want
   to re-listen to takes one click instead of scrubbing. The
   **Timestamps** button above the editor hides/shows the markers; when
   hidden, saved copies contain clean text only (the times are kept in a
   small `.times.json` file next to the transcript either way, so nothing
   is lost by toggling). Timestamps exist for transcripts made from
   v1.2.0 onward — older transcripts simply show none.
3. Edit the text — use the **A- / A+** buttons above the editor to resize
   the text — then **Save copy**. If Microsoft Word is installed the copy is
   saved as a `.docx`; otherwise as a `.txt`. Copies are saved in
   `output\Transcriptions` as `<name> (edited).docx/.txt`, leaving the
   original intact. Reopening the file later (including in the AI Summary
   tab) picks up this edited copy rather than the original transcript.
4. Need punctuation that's awkward to type directly (， 。 「」 《》 etc.)?
   Click **Punctuation** next to the font-size buttons to reveal a row of
   full-width Chinese punctuation marks above the editor — click one to
   insert it at the cursor. Toggle it off again when you don't need it.

## AI Summary & Translate tab

Runs a local AI model (no account, no API key, nothing to configure) over a
transcript — fully offline after a one-time model download:

1. Pick a transcribed file from the dropdown, or **Open a file…** (accepts
   `.txt`/`.docx` transcripts directly, or an audio file whose transcript
   exists in `output\Transcriptions`).
2. Choose the mode: **Summarize**, **Translate** (pick a target language from
   ~20 options), or **Both** (a summary written in the target language).
3. Pick a quality: Fast / Balanced / Accurate — the first use of each level
   downloads its AI model (~1.8 / 2.5 / 4.7 GB, one time). SOTA recommends a
   quality tier based on both your total and currently-free RAM (and checks
   there's enough free disk space to fit the download): **Accurate** if your
   free RAM already covers it, or your PC has at least 16 GB installed;
   **Balanced** for any PC with at least 8 GB (with a note to close other
   apps first if RAM is tight for it right now); otherwise **Fast**.
4. Click **Generate** and watch the output stream into the right panel. A
   copy is **saved automatically** the moment generation finishes (same
   `.docx`/`.txt` rule as everywhere else) — no extra click needed. You can
   still edit the text afterward and click **Save copy** again to save an
   updated version alongside the original.

Each panel (Transcription / AI output) has its own **A- / A+** font size
buttons, right-aligned on its title row, so you can size them independently.
Drag the divider between the two panels to resize them to your liking — the
split is remembered for next time.

Long transcripts are handled automatically (processed in parts); a Cancel
button stops generation at any point.

## Settings tab

Everything that isn't part of a day-to-day workflow lives here:

- **Preferences** — turn the Traditional Chinese conversion on or off, and
  change the **output folder** (where all transcripts and live recordings
  are saved). By default it's the `output` folder next to the app; pick any
  folder you like (e.g. inside Documents or a synced drive), or click
  **Use default** to go back.
- **Models & storage** — every model SOTA can use (three speech qualities,
  the SenseVoice engine, three AI qualities) listed with its size and
  whether it's downloaded. **Download** fetches one ahead of time — handy
  on a good connection before going offline — and **Delete** frees the disk
  space (it simply re-downloads the next time it's needed). The total space
  used by models is shown underneath.
- **Maintenance** — the app version with a **Check for updates** button
  (compares against the latest GitHub release; needs internet, does nothing
  otherwise), plus one-click buttons to open the log file and the models
  folder, and a **Reset all settings** button that restores every
  preference to its default without touching models or transcripts.

## Folder layout

Everything SOTA reads or writes lives in two folders next to the app itself
(`SOTA.exe` on Windows, next to `SOTA.app` on macOS, or the source folder
when run with `python app.py`) — the whole thing is self-contained and can
be moved or backed up as one unit. (The `output` half can be pointed
somewhere else entirely in the Settings tab; `models` always stays next to
the app.)

```
SOTA\
  models\             every downloaded model — whisper, SenseVoice + its
                       voice-activity model, and the local AI (LLM) models
  output\
    Transcriptions\    every transcript: recorded-file, live-dictation,
                       edited copies, and AI summaries/translations
    Live Recordings\   the raw audio (.wav) captured by the Live
                       Transcription tab
```

## Building the app

Every build — local or via GitHub Actions — bundles `README.md` alongside
the app (in the same folder as `SOTA.exe` on Windows, next to `SOTA.app` on
macOS), so it always ships with the release.

Note: since v1.2.0 the build bundles PyTorch and FunASR (for the SenseVoice
engine) alongside the existing dependencies, so both the build itself and
the resulting `dist\SOTA` folder are noticeably larger and slower to
produce than earlier versions.

### Locally

- **Windows**: run `build.bat` (needs Python 3.11–3.13). Produces
  `dist\SOTA\SOTA.exe` and `dist\SOTA\README.md`. Zip the `dist\SOTA` folder
  to share it.
- **macOS**: run `./build_mac.sh` (needs Python 3.11+; run with
  `bash build_mac.sh` if it's not marked executable). Produces
  `dist/SOTA-release/` containing `SOTA.app` and `README.md`. Zip it with
  `ditto -c -k --keepParent dist/SOTA-release SOTA-macOS.zip` before
  sharing — plain zip loses the app bundle's permissions.

### With GitHub Actions (no Mac required)

This repo's [.github/workflows/build.yml](.github/workflows/build.yml)
builds both platforms in the cloud — useful since PyInstaller can only
build for the OS it runs on, so a Windows machine alone can't produce a
macOS `.app`.

- **On demand**: go to the repo's **Actions** tab → **Build SOTA** →
  **Run workflow**. When it finishes, download `SOTA-windows` and
  `SOTA-macOS` from the run's **Artifacts** section.
- **Automatically**: every push to `main` builds both platforms.
- **Releases**: pushing a tag like `v1.2.0` also publishes a GitHub Release
  with both zips attached:
  ```
  git tag v1.2.0
  git push origin v1.2.0
  ```

### Running the unsigned macOS build

`SOTA.app` isn't code-signed or notarized (no Apple Developer account), so
macOS treats it as untrusted until you clear that manually:

1. **Move `SOTA.app` out of Downloads (or the mounted disk image) first** —
   drag it into `/Applications` or any regular folder before opening it.
   This step matters more than it looks: if you launch it straight from
   Downloads or a `.dmg` without moving it, macOS's Gatekeeper silently
   runs it from a hidden, **read-only** copy ("app translocation") instead
   of where you see it in Finder. Everything appears to work, but every
   save fails with "could not save the file," because the app is quietly
   trying to write next to a copy of itself it isn't allowed to write to.
2. Right-click (or Control-click) `SOTA.app` → **Open** → **Open** once to
   get past the "unidentified developer" warning; after that it opens
   normally with a plain double-click.
3. If it still won't open, or you're still seeing save failures after
   moving it, clear the quarantine flag directly from Terminal:
   ```
   xattr -cr /path/to/SOTA.app
   ```

If transcription still fails to save afterward, try pointing the output
folder somewhere you know is writable — **Settings → Output folder** — as
a quick way to rule out a permissions issue entirely.

## Running from source

```
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## Troubleshooting

- Downloaded models live in `models\` next to the app; transcripts and live
  recordings live in `output\` next to the app (see **Folder layout**
  above).
- Settings and the log file live in `%LOCALAPPDATA%\SOTA` on Windows and
  `~/Library/Application Support/SOTA` on macOS. If something goes wrong,
  check `sota.log` there (or use **Settings → Open log file**).
- Deleting that folder — or clicking **Settings → Reset all settings** —
  resets your preferences (quality, language, window sizing, etc.) without
  touching any downloaded models or saved transcripts.
- Upgrading from a version older than 1.2.0: any models already downloaded
  to the old `%LOCALAPPDATA%\SOTA\models` location are copied automatically
  into the new `models\` folder next to the app the first time you launch —
  nothing needs to be re-downloaded. The old copy is left in place; delete
  it by hand once you've confirmed everything works.

## License

This project is provided for **personal, non-commercial use only**. You're
welcome to use and modify it for your own purposes; redistribution, resale,
or commercial use is not permitted without the author's prior permission.

## Acknowledgements

SOTA is a thin desktop shell around other people's excellent open-source
work, run locally instead of through a cloud API:

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** (Systran) —
  CTranslate2-based speech recognition, running
  **[Whisper](https://github.com/openai/whisper)** models (OpenAI).
- **[FunASR](https://github.com/modelscope/FunASR)** (Alibaba/ModelScope) —
  runs **[SenseVoice](https://github.com/FunAudioLLM/SenseVoice)**, the
  more-accurate engine for English/Mandarin/Cantonese/Japanese/Korean, and
  its `fsmn-vad` voice-activity front-end.
- **[Qwen3](https://github.com/QwenLM/Qwen3)** (Alibaba Cloud / the Qwen
  team) — the local models used for summarizing and translating, run via
  **[llama.cpp](https://github.com/ggml-org/llama.cpp)** /
  **[llama-cpp-python](https://github.com/abetlen/llama-cpp-python)**.
- **[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)** — the
  UI toolkit.
- **[tkinterdnd2](https://github.com/pmgagne/tkinterdnd2)** — drag-and-drop
  support.
- **[sounddevice](https://github.com/spatialaudio/python-sounddevice)** /
  PortAudio — audio playback and microphone capture (Live Transcription).
- **[PyAV](https://github.com/PyAV-Org/PyAV)** — audio decoding.
- **[python-docx](https://github.com/python-openxml/python-docx)** — `.docx`
  export.
- **[OpenCC](https://github.com/BYVoid/OpenCC)** (via
  [opencc-python-reimplemented](https://github.com/yichen0831/opencc-python)) —
  Simplified → Traditional Chinese conversion for transcripts.
- **[Hugging Face Hub](https://github.com/huggingface/huggingface_hub)** /
  **[ModelScope](https://github.com/modelscope/modelscope)** — fetching
  models directly from their original publishers.
- **[PyTorch](https://github.com/pytorch/pytorch)** — runs the SenseVoice
  and voice-activity models (CPU only).
- **[NumPy](https://numpy.org/)** — the WSOLA time-stretching used by the
  variable-speed player.
- **[PyInstaller](https://github.com/pyinstaller/pyinstaller)** — packaging
  the Windows and macOS builds.

Speech and language models are downloaded straight from their original
publishers on Hugging Face / ModelScope and cached locally on your machine
— SOTA doesn't modify, rehost, or redistribute them.
