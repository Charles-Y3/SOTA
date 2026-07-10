# SOTA — Smart Offline Transcription Application

**Version 1.1.0**

Drop in audio files, click **Transcribe All**, and get a transcript for each
file — saved as `.docx` if Microsoft Word is installed, otherwise `.txt`.
Everything runs locally on your computer — after the one-time model download,
no internet is needed and no audio ever leaves your machine.

## Using the app

1. Drag & drop audio files into the window (or click the drop zone to browse).
   Supported: mp3, wav, m4a, flac, ogg, opus, wma, aac and common video files
   (the audio track is used).
2. Pick your options — all remembered for next time:
   - **Quality**: Fast / Balanced / Accurate (higher = better text, slower).
   - **Language**: Auto-detect works well; set it manually if needed.
   - **Timestamps**: adds `[MM:SS]` markers to each line.
   - Transcripts always save into the `output` folder next to the app
     (e.g. `SOTA\output\`).
3. Click **Transcribe All**. Each file shows its own progress and any file
   that fails won't stop the rest.

The first time you use a quality level, the app downloads its speech model
(Fast ~75 MB, Balanced ~145 MB, Accurate ~480 MB). That needs internet once;
afterwards the app is fully offline. Before a download, SOTA checks your
PC's free RAM and disk space and lets you know if a heavier quality level
is unlikely to run well on this machine, suggesting a better-suited option.

Punctuation (commas, periods, question marks) is added automatically.

### Interface language

A toggle in the top-right corner switches the whole app between **English**
and **Traditional Chinese (繁體中文)**. Your choice is remembered.

## Edit & Export tab

After transcribing, open the **Edit & Export** tab to proof-read against the
audio:

1. Pick a file from the dropdown (or **Open a file…** to load any audio; its
   transcript is found automatically if one exists). The file dialog opens
   in the `output` folder by default, since that's usually where the file
   you want is.
2. **Play / Pause / Stop**, scrub with the slider, and change **Speed**
   (0.5×–2×) — uses WSOLA time-stretching (searches for the best-aligned
   splice point instead of blindly gluing fixed-size chunks together), so
   both pitch and intelligibility stay natural even at 0.5×.
3. Edit the text — use the **A- / A+** buttons above the editor to resize
   the text — then **Save copy**. If Microsoft Word is installed the copy is
   saved as a `.docx`; otherwise as a `.txt`. Copies are saved in the
   `output` folder as `<name> (edited).docx/.txt`, leaving the original intact.
   Reopening the file later (including in the AI Summary tab) picks up this
   edited copy rather than the original transcript.
4. Need punctuation that's awkward to type directly (， 。 「」 《》 etc.)?
   Click **Punctuation** next to the font-size buttons to reveal a row of
   full-width Chinese punctuation marks above the editor — click one to
   insert it at the cursor. Toggle it off again when you don't need it.

## AI Summary & Translate tab

Runs a local AI model (no account, no API key, nothing to configure) over a
transcript — fully offline after a one-time model download:

1. Pick a transcribed file from the dropdown, or **Open a file…** (accepts
   `.txt`/`.docx` transcripts directly, or an audio file whose transcript
   exists in the output folder).
2. Choose the mode: **Summarize**, **Translate** (pick a target language from
   ~20 options), or **Both** (a summary written in the target language).
3. Pick a quality: Fast / Balanced / Accurate — the first use of each level
   downloads its AI model (~1.8 / 2.5 / 4.7 GB, one time).
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

Like the speech models, the AI model checks your PC's RAM before a first
download and lets you know if a lighter quality level would suit this
machine better.

## Building the app

Every build — local or via GitHub Actions — bundles `README.md` alongside
the app (in the same folder as `SOTA.exe` on Windows, next to `SOTA.app` on
macOS), so it always ships with the release.

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
- **Releases**: pushing a tag like `v1.1.0` also publishes a GitHub Release
  with both zips attached:
  ```
  git tag v1.1.0
  git push origin v1.1.0
  ```

The macOS build is unsigned (no Apple Developer account), so macOS
Gatekeeper will block the first launch with "unidentified developer."
Right-click (or Control-click) `SOTA.app` → **Open** → **Open** once to
allow it; after that it opens normally.

## Running from source

```
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## Troubleshooting

- Settings and models live in `%LOCALAPPDATA%\SOTA`.
- If something goes wrong, check `%LOCALAPPDATA%\SOTA\sota.log`.
- Deleting `%LOCALAPPDATA%\SOTA` fully resets the app.

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
- **[Qwen3](https://github.com/QwenLM/Qwen3)** (Alibaba Cloud / the Qwen
  team) — the local models used for summarizing and translating, run via
  **[llama.cpp](https://github.com/ggml-org/llama.cpp)** /
  **[llama-cpp-python](https://github.com/abetlen/llama-cpp-python)**.
- **[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)** — the
  UI toolkit.
- **[tkinterdnd2](https://github.com/pmgagne/tkinterdnd2)** — drag-and-drop
  support.
- **[sounddevice](https://github.com/spatialaudio/python-sounddevice)** /
  PortAudio — audio playback.
- **[PyAV](https://github.com/PyAV-Org/PyAV)** — audio decoding.
- **[python-docx](https://github.com/python-openxml/python-docx)** — `.docx`
  export.
- **[Hugging Face Hub](https://github.com/huggingface/huggingface_hub)** —
  fetching models directly from their original publishers.
- **[NumPy](https://numpy.org/)** — the WSOLA time-stretching used by the
  variable-speed player.
- **[PyInstaller](https://github.com/pyinstaller/pyinstaller)** — packaging
  the Windows and macOS builds.

Speech and language models are downloaded straight from their original
publishers on Hugging Face and cached locally on your machine — SOTA
doesn't modify, rehost, or redistribute them.
