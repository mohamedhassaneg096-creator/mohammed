# Project: "AutoCut" — local semi-automatic video assembly tool (Windows)

You are building a desktop-style tool that runs 100% locally on my Windows 10/11 laptop. It runs in the browser on localhost (a small Python server + one web page). It must be FREE: no paid APIs, no cloud services, no internet needed after install. I will use it repeatedly, so it must be stable, resumable and fast. Do not ask me questions during the build; where something is unclear, use the defaults written here and note the choice in README.md.

## 0. Hard constraints
- Machine: Windows, CPU only (NO NVIDIA GPU). Everything must work on CPU.
- Language: Python 3.11+. Server: FastAPI (or Flask) + vanilla HTML/JS frontend (no heavy frontend build step, no Node required).
- Video engine: FFmpeg (install with `winget install Gyan.FFmpeg` if missing; the app must check `ffmpeg -version` at startup and show a clear Arabic message if missing).
- Transcription: `faster-whisper` on CPU, `compute_type="int8"`, word_timestamps=True. Default model "medium" (better for Arabic), selectable in settings: small / medium / large-v3. Language option: auto / ar / en. Audio is Arabic or English (sometimes mixed).
- The whole UI text must be in ARABIC (RTL layout). Code, comments, logs and README in English.
- Provide `install.bat` (creates venv, installs requirements, checks FFmpeg) and `run.bat` (starts server and opens http://127.0.0.1:8000 in the browser). One double-click each.
- Never upload anything anywhere. Bind the server to 127.0.0.1 only.

## 1. What the tool does (overview)
Input: one narration audio file + numbered text lines (1..N) + a folder of numbered scenes (images and/or videos).
Output: one horizontal (16:9, 1920x1080, 30 fps, configurable) MP4 where scene k appears exactly during narration segment k, with optional smooth camera motion on images that I choose by hand.

The tool does NOT clean, edit or remove anything from the audio. The audio stays whole and untouched. Only the visuals are assembled.

## 2. Inputs (screen 1)
1. Audio file: file picker (mp3/wav/m4a/ogg/mp4). Copy it into the project folder.
2. Numbered text: a big textarea where I PASTE plain text. Format, one segment per line:
   `1 first few words of segment one`
   `2 first few words of segment two`
   ...
   Each line = a number + the opening words (a few words up to a sentence) of the narration where that segment begins. Parse tolerantly: accept `1 text`, `1. text`, `1- text`, `1) text`, `[1] text`, `1: text`, and Arabic-Indic digits (١٢٣). A line without a leading number continues the previous segment's text. Numbers must be ascending; warn if there are gaps/duplicates.
3. Scenes folder: path text field + browse button. Detect files by the FIRST integer in the filename (`7.png`, `07.jpg`, `scene_7.mp4`, `7 - city.webp` all mean scene 7). Supported: png, jpg, jpeg, webp, mp4, mov, mkv, webm. If one number has both an image and a video, show an error naming the number. List missing numbers (segment exists but no scene) and extra numbers (scene exists but no segment) as warnings before continuing.
4. Settings (with defaults): resolution 1920x1080, fps 30, Whisper model medium, language auto, max video speed change 2.0x (see 5), default motion strength 6%.

## 3. Stage A — Transcribe, cut points, numbering
(Reminder: the design rules in section 11 apply to every screen from Phase 1, not only at the end.)
1. Run faster-whisper on the audio with word timestamps. Show a progress bar and elapsed time (long audio is normal: ~9 min audio ≈ 4-5 min). Run it in a background thread/process; the UI polls progress. Save the full transcript (words + times) to `project/transcript.json` and REUSE it if the audio file hash did not change (so I never wait twice).
2. Find the start time of every segment:
   - Normalize BOTH my text and the transcript words before comparing: strip Arabic diacritics and tatweel, normalize alef forms (أإآ→ا), ى→ي, ة→ه, remove punctuation, lowercase Latin, normalize Arabic-Indic digits.
   - For segment k, take its opening words (first ~6-10 words) and fuzzy-match them (use `rapidfuzz`) against a sliding window of transcript words. The search for segment k+1 must start AFTER the matched start of segment k (monotonic order, never go backwards), and should only search a reasonable forward window first, then widen if no good match.
   - Segment start time = start time of the first matched word. Segment 1 starts at 0.0. Segment k ends where segment k+1 starts. The last segment ends at the audio end.
   - Cut ALL segments first (compute the full cut list), and only then number them. (Numbering = the segment ids 1..N exactly as I gave them.)
3. Review screen (very important): a table with one row per segment: number, my text, the matched transcript text, start time, end time, duration, and a confidence score. Highlight low-confidence rows in red/orange. For each row: a play button that plays the audio from 1 second before the start, and the start time is EDITABLE (type a time, or nudge ±0.1s with buttons). Editing a start time updates the previous segment's end. A button "حفظ ومتابعة" continues. Save all edits to `project/cuts.json`.
   Also let me paste/override quickly: if a segment could not be matched, mark it "غير متطابق" and let me set the time by playing the audio and clicking "ابدأ هنا" at the current playhead.

## 4. Stage B — Place scenes on the timeline
- Scene k goes on segment k, from its start to its end.
- NO GAPS, NO BLACK FRAMES, EVER. The timeline is continuous: scene k+1 starts on the exact frame where scene k ends, and a scene's end IS the next segment's start (the first spoken word of the next segment). Scene 1 starts at 0.0 and the last scene ends exactly at the audio end. Add an automated assertion in code (`timeline.py`) that verifies `end_frame[k] == start_frame[k+1]` for all k and that total frames == audio duration * fps; fail loudly if not.
- IMAGE: shown for the whole segment duration.
- VIDEO (muted; I never want the scene video's own audio): compute `speed = video_duration / segment_duration`.
  - NEVER CUT OR TRIM a video, and never freeze/loop it. The whole video must always play from its first to its last frame, stretched or compressed to fit the segment exactly: if the video is shorter than the segment → slow it down (setpts=PTS/speed with speed<1); if longer → speed it up (speed>1). There is NO maximum speed limit that changes the result.
  - If the needed speed is extreme (slower than 0.4x or faster than 3x), do NOT change the result; only show an informational badge on that scene ("سرعة عالية/بطيئة: 0.3x") in the UI and in the final report so I can decide.
  - Optional setting "سلاسة البطيء" (default OFF): for slow-down below 0.7x, use frame blending (`minterpolate=mi_mode=blend`, cheap) or `mi_mode=mci` (high quality, slow on CPU) to reduce choppiness. Default OFF to keep export fast.
  - Frame-accurate duration: compute frame boundaries from cumulative rounding (`frame_start = round(start*fps)`, `frame_end = round(end*fps)`), so there is NO drift and the last scene ends exactly with the audio. Force the rendered clip to have exactly `frame_end - frame_start` frames.
- Normalize every scene to the target resolution: scale to fit with the background filled by the same image blurred (or black, selectable) so no stretching happens. Images/videos with a different aspect ratio must still look good.
- Show a simple preview grid of all scenes with their numbers and durations; I can click a scene to see it.

## 5. Stage C — Motion screen (the key feature)
Only IMAGES get motion. Videos are skipped. Four tabs, shown in this order: **Zoom In**, **Zoom Out**, **Left**, **Right**.

Flow:
INTERACTION MODEL = ONE CLICK PER IMAGE (no rectangles, no dragging). The point I click is the focus point of the motion.
1. Tab "Zoom In": the tab opens on the FIRST image, shown big (filling most of the screen), with a counter "صورة 1 من 60". I click anywhere on the image (any part). Instantly a BLUE DOT appears at that spot (with a soft pulse animation) — that is the point the camera will zoom toward. I can click again elsewhere to move the dot; clicking the dot itself (or pressing Delete/Backspace) removes it. Then I move to the next/previous image with the ARROW KEYS (← →) or by mouse (big arrow buttons on both sides of the image, or clicking a thumbnail in the strip below). No "confirm" button needed: a dot on an image = that image is assigned to this tab's motion, automatically saved. Images where I put NO dot are simply left unassigned.
2. When I reach the end (or press the "التالي" button / tab at any time), I go to tab "Zoom Out". It shows ONLY the images that did NOT get a dot in Zoom In (assigned images disappear from the list, one by one as I assign them). Same one-click blue-dot flow. Zoom Out starts tight on the clicked point and pulls back to the full frame.
3. Then tab "Left" and tab "Right", each showing ONLY images that still have no motion assigned. Same one-click flow. For Left/Right the clicked point sets the vertical focus (the height at which the camera travels) and the camera, slightly zoomed in by the motion strength, pans horizontally across the image (Left = camera moves toward the left, Right = toward the right). If I click nothing on an image in Left/Right, that image simply stays still (no motion).
4. An image that already got a motion in an earlier tab NEVER appears again in later tabs. Show per-tab counters (assigned / remaining), and a thumbnail strip of ALL images with a small icon for the motion each has (or none) and a highlight on the current image. I can click any thumbnail to re-open that image and change/clear its motion even if it belongs to an earlier tab. A final button "تنفيذ الحركات والتصدير" starts the export. I may skip any tab entirely.
   Motion strength (how much it zooms): a global slider with default 15% (range 5-40%) shown on the screen and applied to all, with an optional per-image override.
5. Per-image extra control (optional but cheap): motion strength slider and "ease" on/off (default on).
6. Save the motion data to `project/motions.json` continuously so closing the browser loses nothing.

SMOOTHNESS REQUIREMENT (critical — no camera shake/jitter): FFmpeg `zoompan` on a normal-size image jitters because it rounds the crop to whole pixels. Avoid that. Implement motion in ONE of these ways (choose the one that tests smooth, prefer A):
- A) Pre-upscale the source image to a very large canvas first (e.g. width 8000 px using Lanczos), then run `zoompan` with expressions on that big canvas and a final `scale` to the output size, `zoompan` using `d=<frames>` and `s=<out size>`, `fps=<fps>`.
- B) Render frames in Python with OpenCV: for each frame compute the crop rectangle in FLOAT coordinates (sub-pixel) from an eased progress value, use `cv2.warpAffine`/`cv2.resize` with `INTER_LANCZOS4` (or INTER_CUBIC) so there is no pixel snapping, and pipe raw frames into an FFmpeg process via stdin.
Use ease-in-out (smoothstep) on the progress so motion starts and ends gently. The motion spans the whole segment duration. The point I clicked is the focus (zoom in: full frame → zoomed-in window centered on the point, clamped so the window never leaves the image; zoom out: that window → full frame; left/right: slightly zoomed window panning horizontally at the point's height). Interpolate the crop's center AND size smoothly. Test it on a sample and make sure there is no visible stutter on slow movements.

## 6. Export
- For each segment, render a clip with IDENTICAL encoding parameters (same codec, resolution, fps, pixel format) so they can be concatenated without re-encoding: H.264 (libx264), `-preset veryfast`, `-crf 18`, `-pix_fmt yuv420p`, `-r <fps>`, no audio.
- Render clips in parallel using a process pool (use about half the CPU cores so the laptop stays usable), with a progress bar and an estimated remaining time. Cache each rendered clip by a hash of (scene file, duration, motion params, settings) so that if I change one image's motion and export again, ONLY that clip is re-rendered.
- Concatenate with the concat demuxer (`-c copy`), then mux the ORIGINAL audio file (AAC 192k) on top, `-shortest` off; the video length must equal the audio length (verify with ffprobe and show both numbers).
- Output: `project/output/final.mp4`, plus a button to open the output folder.
- Make the tool robust: unicode/Arabic file paths and spaces in paths on Windows must work (always pass arguments as lists, never build shell strings).

## 7. Project handling
- Each run is a "project" folder (name chosen at start). All state (settings, text, cuts, motions, transcript) is saved to JSON so I can close everything and reopen the project later and continue exactly where I stopped. A project list on the home screen.
- A "تقرير" panel at the end: total segments, scenes missing, speed-clamped videos, low-confidence cuts, total duration, export time.

## 8. Code structure
```
autocut/
  install.bat
  run.bat
  requirements.txt
  README.md            (how to install/run, how to change settings, troubleshooting)
  app/
    server.py          (FastAPI routes + static files)
    transcribe.py      (faster-whisper wrapper + cache)
    matching.py        (text normalization + fuzzy matching of segments)
    scenes.py          (scene discovery by number, ffprobe info)
    timeline.py        (frame-accurate boundaries, speed math)
    motion.py          (zoom/pan renderers, smooth easing)
    render.py          (per-clip render, parallel, cache, concat, mux)
    project.py         (project JSON save/load)
  web/
    index.html, app.js, style.css   (Arabic RTL UI, canvas rectangle drawing)
  tests/
    test_matching.py, test_timeline.py, make_sample.py
```
Keep functions small and each module independently testable.

## 9. Build in phases. Test each phase before the next. Do not stop between phases unless a test fails and you cannot fix it.
- Phase 1: install/run scripts, project handling, Inputs screen, transcription with cache, matching, cuts review screen. Test `matching.py` with a fake transcript JSON (no audio needed) including Arabic text with diacritics, typos and out-of-order traps.
- Phase 2: scene discovery, timeline math, placing images/videos, export WITHOUT motion to `final.mp4`. Test with generated sample images (solid colors with big numbers drawn on them) and sample videos made by FFmpeg (`testsrc`) and a generated sine-wave audio; verify the output duration equals the audio duration and each scene appears at the right time (extract a frame at the middle of each segment and check the number drawn on it).
- Phase 3: motion screen + motion renderer + export with motion. Verify smoothness: render a zoom-in on a high-detail test image and check frame-to-frame crop movement is monotonic and eased (log the crop rectangle per frame).
- Phase 4: caching of clips, parallel rendering, report panel, README polish.

## 10. Acceptance checklist (run it yourself and report results at the end)
- [ ] `install.bat` then `run.bat` works from a clean state.
- [ ] 150 numbered segments paste and parse correctly, including Arabic digits and odd separators.
- [ ] Matching never goes backwards in time; low-confidence rows are flagged.
- [ ] Editing a start time updates neighbors and persists after restart.
- [ ] Final video length == audio length (±1 frame) and scene k is on screen during segment k.
- [ ] Video scenes are stretched/compressed to fit their segment exactly, never cut, never frozen, muted; extreme speeds only show an info badge.
- [ ] Zero gaps/black frames: the timeline assertion passes (end of scene k == start of scene k+1).
- [ ] Every screen passes the design self-review in section 11 (screenshots saved).
- [ ] Motion screen: one click puts a blue dot, arrow keys/mouse move between images, images assigned in an earlier tab never show in later tabs; assignments persist; clicking a thumbnail edits it.
- [ ] One-button automatic run with the live timeline view; after the result, manual fixes (times, scene replacement, motion) re-render only the changed clips.
- [ ] Zoom/pan is smooth (no jitter) and eased.
- [ ] Re-export after changing one image's motion re-renders only that clip.
- [ ] Works with Arabic filenames and paths with spaces.

At the very end give me: (1) the exact steps to install and run, (2) anything that needs my action (e.g., FFmpeg install), (3) known limits.

## 10b. AUTOMATIC RUN WITH A LIVE VIEW, THEN MANUAL FIXES
I do not want to drive the tool step by step. After I give the inputs, I press ONE button "ابدأ التشغيل التلقائي" and the tool runs the whole pipeline by itself while I WATCH it happen live: transcription → cuts → scene placement → (it pauses only at the motion screen, where I do my clicks) → export. When I finish the motion clicks and press export, it runs on its own to the final video.
- LIVE VIEW: a horizontal timeline at the top of the screen that fills in as work progresses: while transcribing, the recognized words appear in real time; when cutting, the segment blocks appear one by one with their numbers; when placing scenes, each block shows its scene thumbnail as it is attached; when exporting, each block turns from "waiting" to "rendering" (with a small progress bar) to "done" (check mark). A live log line below says in Arabic what it is doing now ("جاري تركيب المشهد 7...").
- NO MANUAL STEP IS REQUIRED to get a full result. If everything matched, I just press the button, do the motion clicks, and get the video.
- MANUAL CONTROL AFTER THE RESULT (a "مراجعة وتعديل" screen): when the first result is ready I can fix small mistakes by hand, without redoing everything. It shows the full timeline with all scenes and a player for the result. Clicking any segment lets me: change its start/end time (with ± nudge), replace its scene file (pick another image/video), change or remove its motion (re-open the blue-dot screen for that image), and change its video speed behavior. Then a button "تحديث" re-renders ONLY the changed clips (use the clip cache) and rebuilds the final video quickly. Everything I did automatically stays as it was; manual edits only override what I touched.
- Manual control is an ADDITION: the tool must be fully usable without it.

## 11. PROFESSIONAL UI / DESIGN (mandatory — this must look like a polished commercial product, not a prototype)
The interface quality is as important as the functionality. Before writing any UI code, define a small design system (CSS variables) and follow it everywhere. If a `frontend-design` skill or similar design guidance is available to you, use it.

**Look & feel**
- Dark, calm "video editor" theme (like DaVinci/Premiere/CapCut): background `#0F1115`, surface cards `#171A21`, raised surfaces `#1E222B`, borders `#2A2F3A`, text `#E8EAF0`, muted text `#9AA3B2`. One accent color (indigo/blue `#5B8CFF`), plus semantic colors: success `#34D399`, warning `#FBBF24`, error `#F87171`. Contrast must meet WCAG AA. Optional light theme toggle.
- Spacing on an 8px grid, border radius 10-14px, soft shadows, subtle 150-200ms transitions on hover/focus/press. No harsh borders, no default browser-looking buttons/inputs/scrollbars (style them all, including the scrollbar and file inputs).
- Typography: a modern Arabic UI font, e.g. "IBM Plex Sans Arabic" or "Cairo" or "Tajawal". The app must work OFFLINE, so download the font files (woff2) into `web/fonts/` during `install.bat` and load them with `@font-face`; fall back to `Segoe UI, Tahoma` if missing. Clear type scale (title 24-28, section 18, body 15-16, small 13). Numbers displayed with tabular figures. Timecodes in a monospace font.
- Icons: inline SVG set (Lucide-style, stroke icons), no CDN. Consistent size and stroke.
- Layout: right-to-left everywhere (`dir="rtl"`), logical CSS properties (`margin-inline`, etc.). Designed for 1366x768 laptops and up; fluid up to 4K; nothing cut off, no horizontal scrolling.

**Structure**
- Top bar with app name/logo (simple wordmark), current project name, save status ("تم الحفظ ✓"), and a settings button.
- A horizontal stepper showing the stages: 1 المدخلات → 2 التفريغ والقص → 3 المشاهد → 4 الحركات → 5 التصدير. Done steps get a check mark, the current one is highlighted, steps are clickable to go back (never lose data).
- One clear primary action per screen (large accent button), secondary actions as quiet buttons. Destructive actions need a confirm dialog.
- Home screen = project cards (name, date, progress, thumbnail) + "مشروع جديد" button.

**States & feedback (this is what makes it feel professional)**
- Every long task has a real progress bar with percentage, elapsed time and ETA, plus a Cancel button. Never a frozen screen.
- Skeleton loaders / spinners while loading, friendly empty states with an icon and a hint, toast notifications for success/errors (auto-dismiss), inline validation messages next to the field.
- All errors in plain Arabic with a suggested fix ("FFmpeg غير مثبت — شغّل install.bat"). Never show raw stack traces to the user; write them to `logs/app.log` and offer a "فتح ملف السجل" button.
- Drag & drop for the audio file and the scenes folder in addition to the pickers.
- Keyboard shortcuts on the motion screen (Enter/→ next, ← previous, S skip, Z undo, 1-4 switch tabs) with a small visible hint bar.

**Cuts review screen**: a clean data table with sticky header, zebra-free subtle row separators, confidence shown as a small colored pill (green/orange/red), inline-editable time cells with ± nudge buttons, row hover highlight, and a mini waveform or audio playhead bar if cheap to do.

**Motion screen**: large image on a centered stage with a dark backdrop; clicking places a clear BLUE DOT (accent color, white ring, soft pulse) with a faint crosshair/target ring showing the zoom focus; big round previous/next arrow buttons on both sides; the dot is draggable. Show the tab bar as a segmented control with counters (e.g. "Zoom In 12/60"). Below the stage, a thumbnail strip of all images with motion icons and the current one highlighted. Add a "معاينة" button that renders a fast low-res (e.g. 480p, 2-3 s) preview of the chosen motion and plays it in place, so I can check smoothness before export.

**Export screen**: summary cards (segments, duration, warnings), overall progress with per-clip mini progress, final success state with the output path, "فتح الفيديو" and "فتح المجلد" buttons, and the report.

**Self-review (required)**: when the UI is built, open it in a browser, take a screenshot of EVERY screen (home, inputs, cuts review, scenes, motion tabs, export, an error state, an empty state), critique them against this section, and fix everything that looks unpolished (alignment, spacing, contrast, overflow, RTL mistakes, cut-off Arabic text, default-looking controls). Repeat until each screen looks professional. Include the screenshots in a `docs/screenshots/` folder.

## 12. Future (do NOT build now, but keep code ready for it)
- Vertical 9:16 export (Reels) as a settings option.
- An optional "suggest zoom areas automatically" button using a free vision model later.
