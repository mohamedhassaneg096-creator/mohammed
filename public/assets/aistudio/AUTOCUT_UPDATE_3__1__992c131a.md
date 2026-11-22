# AutoCut — Update 3: one-click "patch-style" updates from GitHub (extends Update 2, section 4)

Apply on top of Updates 1 and 2. Where this conflicts with section 4 of Update 2, THIS file wins; the local ZIP/folder update from Update 2 stays as an offline fallback. Do not break anything that works. At the end give me: what changed, the exact steps to set it up once, and known limits.

## Goal
I want updates to behave like a game patch: when a new version of AutoCut exists, I press one button inside the app and it downloads ONLY the files that changed (a few KB/MB of code), replaces only those files, keeps everything of mine (projects, settings, models, logs, `.venv`, installed packages), tests itself, and restarts — no re-installing, no copying folders by hand, no re-downloading the big libraries. Source of truth = a GitHub repository that AI Studio syncs my project to (AI Studio can sync/push the project to a GitHub repo).

## 1. Update source settings (التحديثات → المصدر)
- Fields: repository (`owner/repo`), branch (default `main`), subfolder inside the repo that contains AutoCut (default: the repo root or `autocut/` — auto-detect by looking for `app/server.py` and `VERSION`), optional access token (for a PRIVATE repo; use a fine-grained read-only token, "Contents: read-only"). Store the token only in the local `settings.json`, mask it in the UI, never write it to logs, never include it in the problem-report text.
- A "اختبار الاتصال" button that checks the repo/branch/token and shows what it found (latest version number, last commit date) or a clear Arabic error (repo not found, bad token, no internet, rate limit).
- Fallback source: the local ZIP/folder update from Update 2 remains available as "تحديث من ملف".

## 2. Checking for updates
- A "فحص التحديثات" button, and an optional startup check (setting, ON by default) that only compares the remote `VERSION` file with the local one and shows a small badge "تحديث متاح — 1.4.0" in the top bar. It NEVER downloads or applies anything by itself. If offline, it silently does nothing.
- Versioning rule: the repo has a `VERSION` file (semantic version). **From now on, every time you (the AI Studio agent) change AutoCut code in response to a request, bump `VERSION` and add a short entry to `CHANGELOG.md`** (Arabic or English, what changed in plain words). The updater only offers a new version when `VERSION` in the repo is higher than the local one, so half-finished intermediate commits are not offered.

## 3. Delta download (only what changed)
- Compare local files with the remote ones by content hash, not by dates: use the GitHub API tree (`GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1`) which gives a blob SHA per file; compute the same Git blob SHA-1 locally (`sha1("blob " + size + "\0" + bytes)`) for each managed local file. Files with equal SHA are NOT downloaded.
- Download only new/changed files (`GET /repos/{owner}/{repo}/git/blobs/{sha}` or the contents/raw endpoint), verify each downloaded file's SHA against the tree before using it.
- Managed (updatable) paths: `app/`, `web/`, `VERSION`, `CHANGELOG.md`, `requirements.txt`, `install.bat`, `run.bat`, `launcher*`, `create_shortcut*`, `README.md`, `migrations/`. Define them in an `update_manifest.json` inside the app (editable by updates) together with ignore patterns: the repo may also contain files from AI Studio's own preview app (`src/`, `package.json`, `vite.config.*`, `node_modules/`, etc.) — those must be ignored and never downloaded.
- Files that were removed in the new version are removed locally, but ONLY inside managed paths.
- **Never touch** (hard-coded protection, cannot be overridden by an update): `projects/`, `settings.json`, `models/`, `logs/`, `backups/`, `.venv/`, `staging/`, the desktop shortcut, and anything outside the app folder.
- Before downloading, show a summary: "الإصدار الحالي 1.3.0 → الجديد 1.4.0", the list of changes from `CHANGELOG.md` (and the commit messages between the two versions if cheap to fetch), the number of files to change, and the REAL download size in MB (sum of the blob sizes from the tree). A button "تحديث الآن" and a "إلغاء".
- Real progress rules from Update 2 apply: percent = bytes downloaded / bytes total, with MB and speed shown; no fake progress.
- Handle GitHub rate limits and network drops: resume or retry the missing files only; if the whole thing cannot complete, change nothing.

## 4. Safe apply, self-test, rollback
1. Download everything into `staging/<new-version>/` first. Verify every file hash.
2. Back up every local file that will be replaced or deleted into `backups/<old-version>/` (keep last 5).
3. Stop the engine and swap the files from a separate helper process (the updater must not overwrite files it is currently running). Windows locks files in use, so do the swap after the engine exits, then restart through the supervisor.
4. If `requirements.txt` changed, run `pip install` ONLY for the packages that changed (compare old vs new requirements), with real pip progress; if pip fails (e.g., offline), roll back and keep the old version working, telling me which package failed.
5. Run the project-data migrations from `migrations/` (idempotent; they upgrade the JSON of existing projects; never delete user data).
6. Run a quick self-test (import all modules, ffmpeg/ffprobe check, tiny render, `/api/health`). On failure: automatic rollback from the backup and an Arabic explanation.
7. Reload the page automatically and show "تم التحديث إلى 1.4.0" with the list of changes.
- A "التراجع للإصدار السابق" button restores a chosen backup in one click.
- New settings introduced by a new version are added with defaults; values I already set are never overwritten.

## 5. Setup instructions to include in README (Arabic + English)
1. In AI Studio, connect the project to a GitHub repository (new PRIVATE repo is fine) and let it sync.
2. Open AutoCut → التحديثات → المصدر, enter `owner/repo`; if the repo is private, create a fine-grained read-only token for that repo only and paste it.
3. Press "فحص التحديثات". From then on, updating = open the app, see the badge, press "تحديث الآن".
Also document that the first installation of the updater itself is done manually once (copy the folder), and every update after that goes through the app.

## 6. Tests and acceptance
Add tests using a fake local "remote" (mock the GitHub API with a local fixture): (a) delta detection downloads only changed/new files and not unchanged ones; (b) hash mismatch on a downloaded file aborts the update with nothing changed; (c) removed files are deleted only inside managed paths; (d) protected folders are never modified even if the remote contains files with those names; (e) backup is created and rollback restores the exact old bytes; (f) requirements diff installs only changed packages (mock pip); (g) failed self-test triggers automatic rollback; (h) token never appears in logs or in the copied problem report; (i) rate-limit / offline handling leaves the install untouched.

Acceptance checklist:
- [ ] With the repo connected, a new `VERSION` shows a badge; pressing "تحديث الآن" downloads only the changed files (size shown in MB before starting) and applies them.
- [ ] My `projects`, `settings.json`, `models`, `logs`, `.venv` and desktop shortcut are untouched after an update.
- [ ] A failed update (bad hash, failed pip, failed self-test) leaves the old version working; manual rollback works.
- [ ] The local ZIP/folder update still works as a fallback.
- [ ] Progress shown during the update is real (bytes), and nothing is downloaded or applied without my click.
