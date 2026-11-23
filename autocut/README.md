# AutoCut — Local Semi-Automatic Video Assembly Tool (v1.2.0 · Windows CPU)

AutoCut assembles a horizontal 16:9 MP4 video from one untouched narration audio track, an **externally imported transcript** (JSON / SRT / VTT from **Vibe** or whisper.cpp), numbered text cues (`1..N`), and a folder of numbered images and/or videos — running **100% locally and offline on Windows CPU** with zero paid APIs or in-app transcription overhead.

## Quick Start (Windows 10/11)

1. Double-click **`install.bat`**:
   - Creates `.venv` (Python 3.11+ required).
   - Installs FastAPI, Uvicorn, `rapidfuzz`, OpenCV, and Pillow (no heavy Whisper/CTranslate2 packages).
   - Verifies FFmpeg (attempts `winget install Gyan.FFmpeg` if missing).
   - Downloads offline Arabic font files (`IBM Plex Sans Arabic`) into `web/fonts/`.
2. Double-click **`run.bat`**:
   - Starts the local server bound strictly to `http://127.0.0.1:8000` and opens your browser.

## Recommended Workflow with Vibe (External Transcription)

1. Transcribe your narration audio externally in **Vibe**:
   - Open **More Options** → enable **Word timestamps** → export as **JSON** (for word-level precision ✓).
   - **SRT / VTT** is also supported tolerantly as a fallback (sentence-level cues are distributed proportionally across words, and mid-line cuts are marked **`تقدير`** so you can fine-tune them on the waveform).
2. **New Project (`مشروع جديد`)**:
   - Use **مشروع سريع من فولدر** (Quick Folder) to drop a folder containing audio, transcript (`.json`/`.srt`/`.vtt`), `script.txt`, and `scenes/` to auto-populate all 4 cards at once, OR click **مشروع تجريبي** to generate a ready-to-run Arabic sample in seconds.
   - Or pick your audio track directly — AutoCut automatically detects matching-name transcript and `.txt` script files in the same folder.
3. **Transcript Import & Cuts (`التفريغ (استيراد)`)**:
   - Drag & drop or paste JSON / SRT / VTT text (`Vibe JSON`, `whisper.cpp JSON` tokens/ms offsets, OpenAI `segments[].words[]`, tolerant SRT/VTT).
   - Review precision indicators, click any line to seek and play the audio, and use one-click **تجاهل هذا السطر** on flagged suspicious lines (consecutive duplicates, trailing wrong-script lines, post-audio lines, zero-duration cues) non-destructively.
   - Normalized output is saved in `project/transcript.json` for instant re-matching.
4. **Scenes (`المشاهد`)**:
   - Builds a gapless, frame-accurate timeline (`end_frame[k] == start_frame[k+1]`).
   - Video scenes are muted and stretched/compressed in full from first to last frame (`speed = video_duration / segment_duration`) — never trimmed or looped.
5. **Motion (`الحركات`)**:
   - Click once on each image to place the always-blue (`#4C8DFF`) focus dot across 4 ordered tabs: **Zoom In → Zoom Out → Left → Right**.
   - Switch anytime between 3 accessible themes (**ليل أزرق** default, **غابة هادية**, **نهار**) from the top bar or Settings.
6. **Export & Review (`التصدير` & `مراجعة وتعديل`)**:
   - Renders clips in parallel (half CPU cores) with H.264 + SHA-256 clip caching and muxes untouched original audio (AAC 192k).
   - Re-export changed clips only after manual edits.

## Configuration & Hygiene Defaults

- **Default Settings**: `1920x1080`, `30 fps`, `15%` motion strength, `blurred` background fill, `slowmo_smoothing: "off"`, `theme: "blue_night"`.
- **Example Settings**: Copy `settings.example.json` if inspecting defaults; never commit `settings.json` (excluded in `.gitignore`).
- **Logs**: Technical traces are written to `logs/app.log` and never shown raw in the UI.

## Quick Updates via GitHub / التحديثات السريعة عبر GitHub

- Open **التحديثات ← المصدر**, enter `owner/repo` (plus a repo-scoped read-only Personal Access Token if private), and click **فحص التحديثات** then **تحديث الآن**.
- Removed packages in `requirements.txt` (such as migrating v1.1 → v1.2) are safely skipped without failing or uninstalling `.venv/` packages.
- Automatic backups (last 5 versions), self-tests, and auto-rollback protect `projects/`, `settings.json`, `models/`, `.venv/`, `backups/`, `staging/`, and `logs/`.
- Offline `.zip` or folder update is always available under **التحديثات ← تحديث من ملف**.
