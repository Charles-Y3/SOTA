# SOTA — Smart Offline Transcription Application

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
afterwards the app is fully offline.

Punctuation (commas, periods, question marks) is added automatically.

### Interface language

A toggle in the top-right corner switches the whole app between **English**
and **Traditional Chinese (繁體中文)**. Your choice is remembered.

## Edit & Export tab

After transcribing, open the **Edit & Export** tab to proof-read against the
audio:

1. Pick a file from the dropdown (or **Open a file…** to load any audio; its
   transcript is found automatically if one exists).
2. **Play / Pause / Stop**, scrub with the slider, and change **Speed**
   (0.5×–2×) — the pitch stays natural, so faster playback is still clear.
3. Edit the text, then **Save copy**. If Microsoft Word is installed the copy
   is saved as a `.docx`; otherwise as a `.txt`. Copies are saved in the
   `output` folder as `<name> (edited).docx/.txt`, leaving the original intact.

## Building the app

### Locally

- **Windows**: run `build.bat` (needs Python 3.11–3.13). Produces
  `dist\SOTA\SOTA.exe`. Zip the `dist\SOTA` folder to share it.
- **macOS**: run `./build_mac.sh` (needs Python 3.11+; run with
  `bash build_mac.sh` if it's not marked executable). Produces
  `dist/SOTA.app`. Zip it with
  `ditto -c -k --keepParent dist/SOTA.app SOTA-macOS.zip` before sharing —
  plain zip loses the app bundle's permissions.

### With GitHub Actions (no Mac required)

This repo's [.github/workflows/build.yml](.github/workflows/build.yml)
builds both platforms in the cloud — useful since PyInstaller can only
build for the OS it runs on, so a Windows machine alone can't produce a
macOS `.app`.

- **On demand**: go to the repo's **Actions** tab → **Build SOTA** →
  **Run workflow**. When it finishes, download `SOTA-windows` and
  `SOTA-macOS` from the run's **Artifacts** section.
- **Automatically**: every push to `main` builds both platforms.
- **Releases**: pushing a tag like `v1.0.0` also publishes a GitHub Release
  with both zips attached:
  ```
  git tag v1.0.0
  git push origin v1.0.0
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
