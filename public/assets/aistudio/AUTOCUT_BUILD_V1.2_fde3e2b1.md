# AutoCut — build v1.2 (single, self-contained prompt)

Current installed version is 1.1 (it already has the GitHub updater, problem finder, waveform player, motion studio, export). Build v1.2 on top of it. This file REPLACES AUTOCUT_UPDATE_4.md (do not use that one; everything from it is included here). Do not break anything that works. Keep the current layout, components and overall look — this is a refinement, not a redesign. Arabic RTL UI, English code/comments. Windows, CPU only, 100% local.

## 0. Ground rules for this build
- It will be delivered to my installed copy through the in-app GitHub updater: bump `VERSION` to 1.2.0 and add a `CHANGELOG.md` entry (Arabic or English, plain words).
- `requirements.txt` will LOSE packages (see section 1). The updater must handle removed packages without failing and must NOT uninstall anything from my `.venv`.
- The updater must never touch `projects/`, `settings.json`, `logs/`, `backups/`, `.venv/`, `models/`, `staging/` (hard-coded protection).
- Nothing heavy starts by itself (no transcription/render/download without my click). All progress numbers must be real (Update 2 rules stay).
- Run all existing tests plus the new ones. At the end report: what changed, test results, new VERSION, and known limits.

## 1. Transcription is EXTERNAL ONLY (import-only)
I transcribe in another app (Vibe, which uses whisper.cpp) and give AutoCut the transcript file. AutoCut must no longer transcribe anything.
- Remove from UI/settings: model choice, local model folder, external command, model download, faster-whisper options, speed/accuracy mode, and the stall/progress features that only made sense for in-app transcription.
- Remove `faster-whisper`, `ctranslate2`, `onnxruntime`, `av`, `tokenizers`, `huggingface_hub` (and anything only they need) from `requirements.txt` and `install.bat`. Nothing about Whisper is downloaded or installed.
- Keep: matching, cuts review, waveform/audio player, scenes, motion, export, project management, diagnostics, updater, desktop shortcut. Stepper step 2 becomes "التفريغ (استيراد)".
- The imported transcript is stored in the project (`transcript.json`, normalized) so re-matching is instant.

## 2. The "التفريغ" screen and parsers
- Drag & drop / pick a transcript file, or paste SRT/VTT text. Auto-detect the format and show a badge: `JSON — توقيت كل كلمة ✓` or `SRT — توقيت الجمل ⚠`.
- Precision indicator, explicit: word-level = high precision; sentence-level only = lower precision with the hint "لدقة أعلى: في Vibe شغّل Word timestamps وصدّر JSON".
- Blocking validation with clear Arabic errors: no timestamps at all (my first Vibe JSON export was like that: only `{"transcript":[{"text":"..."}]}`), unreadable file, times not increasing, times far beyond the audio duration (> audio length + 2 s).
- Preview: scrollable list with times; clicking a line plays the audio from there. Stats: coverage, lines/words, audio duration vs last timestamp.
- Suspicious-lines detector (non-destructive): (a) consecutive identical lines, (b) lines after the audio end or inside long silence, (c) lines in a different script than the rest (e.g., Arabic lines at the end of an English transcript — Whisper hallucination), (d) lines with identical start and end. Each flagged line has "تجاهل هذا السطر". Ignored lines are excluded from matching but stay in the file.
- `transcript_import.py` normalizes everything to words `{text, start, end, estimated}` plus the original segments. Support, tolerantly (never hard-code one schema):
  1. Vibe JSON / whisper.cpp JSON: arrays under keys like `transcript`, `transcription`, `segments`, `result`; timing as `start`/`end`, `from`/`to`, `t0`/`t1`, `timestamps:{from,to}`, `offsets:{from,to}` (ms); word data under `words`, `tokens` or `items`. whisper.cpp tokens are sub-word pieces: merge by leading space, skip specials (`[_BEG_]`, `[_TT_*]`, `<|...|>`, `[BLANK_AUDIO]`).
  2. OpenAI whisper / faster-whisper JSON (`segments[].words[]` with `word`,`start`,`end`).
  3. SRT and VTT (missing header, no blank line between cues, `,` or `.` millisecond separator, optional hours; inline word tags like `<00:00:01.200>` if present).
  4. Generic JSON: list of objects with a text field and start/end fields.
- Unit detection: seconds vs milliseconds vs `HH:MM:SS,mmm` strings, with the audio duration as a sanity check.
- Sentence-level input: estimate word times by distributing each segment's duration across its words proportionally to character count, `estimated: true`. If my numbered text starts in the MIDDLE of a transcript line, use the estimated word time inside the line and flag the cut "تقدير" (warning color) so I can fix it on the waveform.
- Unknown JSON shape: show the first 30 lines, the message "الصيغة غير معروفة", and a "نسخ تقرير المشكلة" button; never crash. UTF-8 with/without BOM; Arabic and English.

## 3. New "مشروع جديد" screen (layout to build)
RTL, inside the existing app shell:
- Top row: project name field (left side in RTL: the start), and a "مشروع سريع من فولدر" drop zone (dashed border): drop a folder containing audio, transcript (.json/.srt/.vtt), `script.txt` (numbered text) and a `scenes/` subfolder; all four cards fill automatically.
- Four material cards in a 2x2 grid, each with a numbered circle, title, status badge and a pick/drop area:
  1. الصوت: file name, duration, a small waveform.
  2. التفريغ: file name, precision badge, words count, last timestamp, number of suspicious lines.
  3. النص المرقّم: paste area OR pick a `.txt`; segment count; warnings for gaps/duplicate numbers.
  4. المشاهد: folder path, counts (images / videos), a thumbnail strip, list of missing and extra numbers.
- Status badges: ready (success color), warning (warning color, e.g. "ينقص 3"), required (danger color).
- Below: one summary line of the current settings (resolution, fps, motion strength, background fill) with an "تعديل" button opening the full settings form.
- Primary button "ابدأ التشغيل التلقائي" and the note "مفيش حاجة بتشتغل قبل ما تدوس". If audio, transcript or text is missing the button stays visible and explains what is missing instead of failing silently.

## 4. Small extras
1. When I pick the audio, look in the same folder for a transcript with the same base name (.json/.srt/.vtt) and a `.txt` and offer them with one click.
2. Numbered text can be loaded from a `.txt` as well as pasted.
3. "مشروع تجريبي" button: generates a tiny sample (generated audio, numbered images, a transcript and numbered text) so the whole pipeline can be verified in seconds without my files.
4. A short Arabic guide on the transcript card: how to export from Vibe (More Options → enable Word timestamps → export JSON; SRT as a fallback).

## 5. Visual refinement: color themes (keep the look, make it friendlier)
The current dark-blue look is good. Keep it as the default but refine it into a calmer, more pleasant palette, and add a theme switcher (top bar or settings) with 3 presets saved in settings. Define every color as a CSS variable once and use only variables. Both text and controls must keep WCAG AA contrast.

Preset "ليل أزرق" (default, refined version of the current look):
`--bg #0F1218; --surface #161B23; --surface-2 #1C232D; --border #28313D; --text #E8EDF3; --text-2 #9AA7B6; --accent #6C9BFF; --accent-hover #86AEFF; --on-accent #0B1220; --success #57D39B; --warning #F4B860; --danger #F27B7B`

Preset "غابة هادية" (calm green-teal):
`--bg #0E1514; --surface #142120; --surface-2 #1A2B2A; --border #264038; --text #E6F1EE; --text-2 #8FB0A8; --accent #3FD0B0; --accent-hover #66DEC4; --on-accent #06201B; --success #7BD88F; --warning #F2C14E; --danger #F07C6C`

Preset "نهار" (light):
`--bg #F5F7FA; --surface #FFFFFF; --surface-2 #F0F3F7; --border #DDE3EA; --text #1B2430; --text-2 #5A6878; --accent #3B6FF0; --accent-hover #2F5FD8; --on-accent #FFFFFF; --success #1E9E6A; --warning #B7791F; --danger #D64545`

Rules:
- The motion-studio focus dot stays BLUE (`--dot #4C8DFF` with a white ring and soft pulse) in every theme.
- No pure black or pure white large areas; soft 12px card radius, 8px spacing grid, subtle 150-200 ms transitions, consistent outline icons, one primary button per screen, nothing flashy or neon.
- Status colors only mean status (ready/warning/error); the accent color is for primary actions, links and selection.
- Smooth theme switch without page reload; the app window and the desktop shortcut keep working.
- Re-capture screenshots of every changed screen in all 3 themes and review them yourself (alignment, spacing, contrast, RTL, cut-off Arabic text) before reporting.

## 6. Repo hygiene (the GitHub repo is now PUBLIC)
- Make sure `.gitignore` excludes: `projects/`, `logs/`, `backups/`, `staging/`, `models/`, `.venv/`, `settings.json`, `.env` (only `.env.example` may be committed), `*.pyc`, `__pycache__/`, and any exported videos/audio.
- Never put keys, tokens, personal paths or personal data in committed files; scan the repo for secrets before reporting and tell me what you found.
- `settings.json` must never be uploaded; a `settings.example.json` with defaults is fine.

## 7. Tests and acceptance
Use these REAL snippets from my Vibe SRT export as fixtures:
```
1
00:00:00,000 --> 00:00:04,640
"In the year 874, a few shiploads of farmers stepped onto

2
00:00:04,640 --> 00:00:12,080
an island of ice and volcanic rock. Behind them lay green valleys, tall forests, and the graves of

32
00:02:55,200 --> 00:03:00,400
but it was warm enough to stay alive. Clue number three was on their backs: wool.

48
00:04:25,200 --> 00:04:29,680
In fact, medieval Icelandic law spent careful pages deciding who owned one.

49
00:04:30,240 --> 00:04:34,480
In fact, medieval Icelandic law spent careful pages deciding who owned one.

101
00:09:43,600 --> 00:09:45,600
أن تفيد المترجم للغاية.

102
00:09:45,600 --> 00:09:47,600
أن تفيد المترجم للغاية.
```
- A numbered line starting at "Clue number three" matches INSIDE entry 32 with an estimated time and the "تقدير" flag; entries 48/49 flagged as duplicates; 101/102 flagged as different-script trailing junk; entry 1 keeps its leading quote without breaking matching.
- A JSON with only `{"transcript":[{"text":"..."}]}` is rejected with the Arabic "no timestamps" error and the Vibe hint.
- Synthetic fixtures: whisper.cpp JSON with `offsets` in ms and token-level data; OpenAI-style `segments[].words[]`; VTT without header; SRT with `.` milliseconds; word-level JSON in seconds. All normalize to the same word list for the same content.
- Quick folder import fills all four cards; the demo project runs the whole pipeline; theme switching keeps contrast ≥ AA (automated check of the variable pairs); `.gitignore` excludes the listed paths; updater test: applying v1.2 to a v1.1 copy with removed packages succeeds and leaves `projects/`, `settings.json`, `.venv/` untouched.
- Earlier matching/cuts/timeline/motion/export tests still pass using imported fixtures; remove only tests that covered the removed engine.

Acceptance checklist:
- [ ] Fresh install installs no Whisper packages and downloads nothing; the app never transcribes.
- [ ] I can import Vibe JSON/SRT (word- or sentence-level) and the app states the precision honestly; no-timestamp files are rejected with the fix.
- [ ] Suspicious lines are flagged and can be ignored in one click; mid-line starts get "تقدير".
- [ ] "مشروع جديد" screen matches section 3; quick folder and demo project work.
- [ ] 3 themes available, default refined blue, motion dot always blue, AA contrast, screenshots reviewed.
- [ ] Repo is clean: no secrets, no user data, `.gitignore` correct.
- [ ] VERSION 1.2.0, CHANGELOG updated, all tests pass.
