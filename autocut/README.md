# AutoCut — Local Semi-Automatic Video Assembly Tool (Windows CPU)

AutoCut assembles a horizontal 16:9 MP4 video from one untouched narration audio track, numbered text cues (`1..N`), and a folder of numbered images and/or videos — running **100% locally and offline on Windows CPU** with zero paid APIs or cloud uploads.

## Quick Start (Windows 10/11)

1. Double-click **`install.bat`**:
   - Creates `.venv` (Python 3.11+ required).
   - Installs FastAPI, Uvicorn, `faster-whisper`, `rapidfuzz`, OpenCV, and Pillow.
   - Verifies FFmpeg (attempts `winget install Gyan.FFmpeg` if missing).
   - Downloads offline Arabic font files (`IBM Plex Sans Arabic`) into `web/fonts/`.
2. Double-click **`run.bat`**:
   - Starts the local server bound strictly to `http://127.0.0.1:8000` and opens your browser.

## Workflow

1. **Inputs (`المدخلات`)**:
   - Select or drag-and-drop your narration audio (`mp3`, `wav`, `m4a`, `ogg`, `mp4`).
   - Paste your numbered script (`1 ...`, `2 ...`; accepts `.`, `-`, `)`, `:`, `[1]`, and Arabic-Indic digits `١٢٣`).
   - Enter or browse your numbered scenes folder (`7.png`, `07.jpg`, `scene_7.mp4`, `7 - city.webp`).
   - Click **"ابدأ التشغيل التلقائي"** (or step through manually).
2. **Transcribe & Cut (`التفريغ والقص`)**:
   - Runs `faster-whisper` on CPU (`compute_type="int8"`, `word_timestamps=True`) and caches `project/transcript.json` by audio SHA-256 hash so re-runs are instant.
   - Matches each segment opening monotonically (never backwards in time) and lets you audition (`-1s`), nudge (`±0.1s`), or stamp (`ابدأ هنا`) any cut point.
3. **Scenes (`المشاهد`)**:
   - Builds a gapless, frame-accurate timeline (`end_frame[k] == start_frame[k+1]`).
   - Video scenes are muted and stretched/compressed in full from first to last frame (`speed = video_duration / segment_duration`) — never trimmed or looped. Extreme speeds (`<0.4x` or `>3.0x`) display an informational badge.
4. **Motion (`الحركات`)**:
   - Click once on each image to place the blue focus dot across 4 ordered tabs: **Zoom In → Zoom Out → Left → Right**.
   - Assigned images are automatically excluded from subsequent tabs.
   - Uses smoothstep easing on an 8000px Lanczos pre-upscaled canvas to eliminate `zoompan` pixel-snapping jitter.
   - Includes a fast 480p **"معاينة الحركة"** preview button before full export.
5. **Export & Post-Export Review (`التصدير` & `مراجعة وتعديل`)**:
   - Renders clips in parallel (half CPU cores) with identical H.264 parameters (`libx264`, `-preset veryfast`, `-crf 18`, `yuv420p`) and SHA-256 clip caching.
   - Concatenates with `-c copy` and muxes the original audio (AAC 192k).
   - Post-export manual review lets you adjust times, replace scenes, or edit motions and re-export **only changed clips**.

## Design Defaults & Choices

- **Default Motion Strength**: `15%` (range `5%–40%`) per Section 5 specification (note: Section 2 mentioned 6% as an initial note; `15%` provides clear visual motion out of the box and is adjustable globally or per image).
- **Default Settings**: `1920x1080`, `30 fps`, Whisper model `medium`, language `auto`, background fill `blurred`, slow-mo smoothing `off`.
- **Logs**: Technical errors and stack traces are written to `logs/app.log` and never shown raw in the UI.

## Troubleshooting

- **"FFmpeg غير مثبت"**: Run `winget install Gyan.FFmpeg` in PowerShell, restart your terminal/PC, and run `run.bat`.
- **Slow first transcription**: `faster-whisper` downloads the selected model (`small`/`medium`/`large-v3`) once into the local HuggingFace cache; subsequent runs and cached audio hashes are offline and instant.
- **Check logs**: Click **"السجل"** in the top bar or open `autocut/logs/app.log`.

---

## Quick Updates via GitHub / التحديثات السريعة عبر GitHub

### الإعداد السريع (بالعربية)
1. **مرة واحدة فقط (التثبيت الأولي)**: إذا كان لديك إصدار أقدم قبل نظام التحديث، نزّل حزمة المشروع الجديدة وفكّها في مجلد البرنامج (أو شغّل `run.bat` بعد نسخ الملفات). بعد ذلك لن تحتاج لتنزيل الملف الكامل مجدداً.
2. **ربط AI Studio بمستودع GitHub**: ادفع التعديلات من بيئة التطوير إلى مستودعك على GitHub (عام أو خاص).
3. **إعداد مصدر التحديث داخل AutoCut**:
   - افتح البرنامج واذهب إلى **التحديثات ← المصدر**.
   - أدخل اسم المستودع بصيغة `owner/repo` والفرع (الافتراضي `main`) واترك المجلد الفرعي `auto` (يكتشف الجذر أو `autocut/` تلقائياً).
   - **للمستودع الخاص فقط**: أنشئ رمز **Fine-grained Personal Access Token** من GitHub محدداً المستودع المعني فقط بصلاحية **`Contents: Read-only`** ثم الصقه في خانة الرمز (يُحفظ في `settings.json` محلياً ولا يظهر في السجلات أو تقارير المشكلة).
   - اضغط **اختبار الاتصال** ثم **حفظ إعدادات المصدر**.
4. **تنفيذ التحديث**:
   - اضغط **فحص التحديثات** لعرض التغييرات وحجم التنزيل الحقيقي بالميجابايت (يُنزَّل فقط ما تغيّر بمقارنة بصمة Git SHA-1).
   - اضغط **تحديث الآن**. يتم أخذ نسخة احتياطية تلقائية (لآخر 5 إصدارات)، وتطبيق التحديث مع اختبار ذاتي، وإعادة التشغيل تلقائياً مع الحفاظ الكامل على `projects/` و`settings.json` و`models/`.
   - يمكنك التراجع في أي وقت عبر زر **التراجع للإصدار السابق**، أو التحديث بدون إنترنت عبر تبويب **تحديث من ملف** (`.zip` أو مجلد محلي).

### Setup Guide (English)
1. **One-time initial install**: Copy/extract this version into your AutoCut folder once; future updates download only changed files as delta patches.
2. **Sync AI Studio → GitHub**: Push updates from AI Studio to your GitHub repository (public or private).
3. **Configure Source (`التحديثات → المصدر`)**:
   - Enter `owner/repo`, branch (`main`), and leave subfolder as `auto` (auto-detects repo root or `autocut/`).
   - *(Private repos only)* Create a GitHub **Fine-grained Personal Access Token** scoped to that single repo with **`Contents: Read-only`**, paste it into the token field, and click **اختبار الاتصال** then **حفظ إعدادات المصدر**.
4. **Apply & Rollback**:
   - Click **فحص التحديثات** to inspect changed file count, exact download MB, and `CHANGELOG.md` notes, then click **تحديث الآن**.
   - Automatic backups (last 5 versions), self-tests, and auto-rollback protect `projects/`, `settings.json`, `models/`, `.venv/`, and `logs/`.
   - Offline fallback is always available under **التحديثات → تحديث من ملف** (`.zip` or folder).

