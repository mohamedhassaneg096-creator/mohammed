"""GitHub delta patch updater & local ZIP/folder fallback updater for AutoCut."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .project import BASE_DIR, _atomic_write_json, _read_json, logger
from .settings_store import get_app_version, load_global_settings, redact_secrets

# Hard-coded protection: CANNOT be overridden by update_manifest.json or remote repos.
HARD_PROTECTED_NAMES = {
    "projects",
    "settings.json",
    "models",
    "logs",
    "backups",
    ".venv",
    "staging",
}

DEFAULT_MANIFEST: Dict[str, Any] = {
    "managed_paths": [
        "app/",
        "web/",
        "migrations/",
        "VERSION",
        "CHANGELOG.md",
        "requirements.txt",
        "install.bat",
        "run.bat",
        "launcher.py",
        "create_shortcut.bat",
        "README.md",
        "update_manifest.json",
    ],
    "ignore_patterns": [
        "src/",
        "public/",
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "vite.config.*",
        "node_modules/",
        ".git/",
        ".github/",
        "docs/",
        "tests/",
        "__pycache__/",
        "*.pyc",
        "*.pyo",
    ],
}


class UpdaterError(RuntimeError):
    """User-facing Arabic error raised by updater operations."""


class HashMismatchError(UpdaterError):
    """Raised when a downloaded blob SHA-1 does not match the remote tree SHA."""


def git_blob_sha1(data: bytes) -> str:
    """Compute exact Git blob SHA-1: sha1('blob <len>\\0' + data)."""
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def parse_semver(version_str: str) -> Tuple[int, ...]:
    cleaned = (version_str or "").strip().lstrip("vV")
    core = cleaned.split("-", 1)[0].split("+", 1)[0]
    parts: List[int] = []
    for p in core.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(remote_ver: str, local_ver: str) -> bool:
    return parse_semver(remote_ver) > parse_semver(local_ver)


def load_manifest(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    mfile = (base_dir or BASE_DIR) / "update_manifest.json"
    data = _read_json(mfile)
    if not isinstance(data, dict):
        return dict(DEFAULT_MANIFEST)
    return {
        "managed_paths": list(data.get("managed_paths") or DEFAULT_MANIFEST["managed_paths"]),
        "ignore_patterns": list(data.get("ignore_patterns") or DEFAULT_MANIFEST["ignore_patterns"]),
    }


def is_protected_relpath(relpath: str) -> bool:
    norm = relpath.replace("\\", "/").strip("/")
    if not norm or norm.startswith("..") or norm.startswith("/"):
        return True
    parts = norm.split("/")
    if ".." in parts:
        return True
    if parts[0] in HARD_PROTECTED_NAMES:
        return True
    if norm.lower().endswith(".lnk"):
        return True
    return False


def _matches_pattern(relpath: str, pattern: str) -> bool:
    norm = relpath.replace("\\", "/").lstrip("/")
    pat = pattern.replace("\\", "/").lstrip("/")
    if pat.endswith("/"):
        return norm.startswith(pat) or norm == pat.rstrip("/")
    if "/" not in pat:
        parts = norm.split("/")
        return any(fnmatch.fnmatch(part, pat) for part in parts)
    return fnmatch.fnmatch(norm, pat)


def is_managed_relpath(relpath: str, manifest: Dict[str, Any]) -> bool:
    norm = relpath.replace("\\", "/").lstrip("/")
    if is_protected_relpath(norm):
        return False
    for ign in manifest.get("ignore_patterns") or []:
        if _matches_pattern(norm, ign):
            return False
    for mp in manifest.get("managed_paths") or []:
        if _matches_pattern(norm, mp):
            return True
    return False


def extract_changelog_summary(changelog_text: str, max_lines: int = 12) -> List[str]:
    """Extract bullet lines for the latest release section in CHANGELOG.md."""
    lines: List[str] = []
    in_first_release = False
    for raw in (changelog_text or "").splitlines():
        s = raw.strip()
        if s.startswith("## "):
            if in_first_release:
                break
            in_first_release = True
            lines.append(s.lstrip("# ").strip())
            continue
        if in_first_release and s:
            lines.append(s.lstrip("- ").strip())
            if len(lines) >= max_lines:
                break
    return lines


class GitHubClient:
    """Lightweight stdlib GitHub REST client with strict secret redaction & rate-limit handling."""

    def __init__(self, repo: str, branch: str = "main", token: str = "", api_base: str = "https://api.github.com"):
        self.repo = (repo or "").strip().strip("/")
        self.branch = (branch or "main").strip() or "main"
        self.token = (token or "").strip()
        self.api_base = api_base.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "AutoCut-Updater/1.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def request_json(self, path: str, retries: int = 2) -> Any:
        url = f"{self.api_base}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                body_txt = ""
                try:
                    body_txt = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                rem = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
                if exc.code == 404:
                    raise UpdaterError("المستودع أو الفرع غير موجود (تأكد من owner/repo واسم الفرع، أو أضف رمز وصول للمستودع الخاص).")
                if exc.code == 401:
                    raise UpdaterError("رمز الوصول (Token) غير صالح أو منتهي الصلاحية.")
                if exc.code in (403, 429):
                    if rem == "0" or "rate limit" in body_txt.lower():
                        raise UpdaterError("تم تجاوز حد طلبات GitHub مؤقتاً. انتظر قليلاً أو أضف رمز وصول للقراءة.")
                    raise UpdaterError("لا توجد صلاحية لقراءة هذا المستودع (تأكد من صلاحية Contents: read-only).")
                if exc.code >= 500 and attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    last_exc = exc
                    continue
                raise UpdaterError(f"تعذر جلب البيانات من GitHub (رمز HTTP {exc.code}).")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < retries:
                    time.sleep(0.4 * (attempt + 1))
                    last_exc = exc
                    continue
                safe_msg = redact_secrets(str(exc), [self.token])
                logger.warning("GitHub network error: %s", safe_msg)
                raise UpdaterError("تعذر الاتصال بالإنترنت أو بخادم GitHub.")
        raise UpdaterError(f"فشل الاتصال بخادم GitHub: {redact_secrets(str(last_exc), [self.token])}")

    def get_branch_info(self) -> Dict[str, Any]:
        if "/" not in self.repo:
            raise UpdaterError("صيغة المستودع غير صحيحة. استخدم الصيغة: owner/repo")
        data = self.request_json(f"/repos/{self.repo}/branches/{urllib.parse.quote(self.branch)}")
        commit = data.get("commit") or {}
        c_info = commit.get("commit") or {}
        committer = c_info.get("committer") or c_info.get("author") or {}
        return {
            "commit_sha": commit.get("sha") or self.branch,
            "commit_date": committer.get("date") or "",
            "commit_message": (c_info.get("message") or "").splitlines()[0] if c_info.get("message") else "",
        }

    def get_tree(self, ref_sha: str) -> List[Dict[str, Any]]:
        data = self.request_json(f"/repos/{self.repo}/git/trees/{urllib.parse.quote(ref_sha)}?recursive=1")
        return [e for e in (data.get("tree") or []) if e.get("type") == "blob"]

    def get_blob_bytes(self, blob_sha: str) -> bytes:
        data = self.request_json(f"/repos/{self.repo}/git/blobs/{urllib.parse.quote(blob_sha)}")
        enc = data.get("encoding", "base64")
        content = data.get("content", "")
        if enc == "base64":
            return base64.b64decode(content)
        return content.encode("utf-8")

    def get_recent_commits(self, per_page: int = 8) -> List[str]:
        try:
            items = self.request_json(
                f"/repos/{self.repo}/commits?sha={urllib.parse.quote(self.branch)}&per_page={per_page}",
                retries=0,
            )
            msgs: List[str] = []
            for it in items or []:
                msg = ((it.get("commit") or {}).get("message") or "").splitlines()[0].strip()
                if msg:
                    msgs.append(msg)
            return msgs
        except Exception:
            return []


def detect_subfolder(blobs: List[Dict[str, Any]], configured_subfolder: str = "auto") -> str:
    """Detect subfolder containing AutoCut (VERSION + app/server.py)."""
    paths = {b["path"] for b in blobs}
    cfg = (configured_subfolder or "auto").strip().strip("/")
    if cfg and cfg != "auto":
        return cfg

    if "VERSION" in paths and "app/server.py" in paths:
        return ""
    if "autocut/VERSION" in paths and "autocut/app/server.py" in paths:
        return "autocut"

    for p in sorted(paths):
        if p.endswith("/app/server.py"):
            prefix = p[: -len("/app/server.py")]
            if f"{prefix}/VERSION" in paths:
                return prefix
    raise UpdaterError("لم يتم العثور على مجلد AutoCut (ملفات VERSION و app/server.py) داخل المستودع.")


def scan_local_managed_files(base_dir: Path, manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return {relpath: {sha, size}} for all local managed files."""
    result: Dict[str, Dict[str, Any]] = {}
    for p in sorted(base_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base_dir).as_posix()
        if not is_managed_relpath(rel, manifest):
            continue
        raw = p.read_bytes()
        result[rel] = {
            "sha": git_blob_sha1(raw),
            "size": len(raw),
        }
    return result


def test_github_connection(
    repo: str,
    branch: str = "main",
    subfolder: str = "auto",
    token: str = "",
    api_base: str = "https://api.github.com",
) -> Dict[str, Any]:
    """Validate repo, branch, token, subfolder detection, and return remote VERSION + commit date."""
    client = GitHubClient(repo=repo, branch=branch, token=token, api_base=api_base)
    binfo = client.get_branch_info()
    blobs = client.get_tree(binfo["commit_sha"])
    detected_sub = detect_subfolder(blobs, subfolder)
    prefix = f"{detected_sub}/" if detected_sub else ""
    ver_blob = next((b for b in blobs if b["path"] == f"{prefix}VERSION"), None)
    remote_version = "1.0.0"
    if ver_blob:
        remote_version = client.get_blob_bytes(ver_blob["sha"]).decode("utf-8", errors="replace").strip().lstrip("vV")

    return {
        "ok": True,
        "repo": client.repo,
        "branch": client.branch,
        "subfolder": detected_sub or "(root)",
        "remote_version": remote_version,
        "commit_sha": binfo["commit_sha"],
        "commit_date": binfo["commit_date"],
    }


def check_github_version_only(
    base_dir: Optional[Path] = None,
    settings_file: Optional[Path] = None,
    api_base: str = "https://api.github.com",
) -> Dict[str, Any]:
    """Lightweight check comparing only remote VERSION with local VERSION (never downloads/applies)."""
    bdir = base_dir or BASE_DIR
    gs = load_global_settings(settings_file)
    src = gs["update_source"]
    repo = src.get("repo", "")
    local_ver = get_app_version(bdir)
    if not repo:
        return {"ok": True, "configured": False, "local_version": local_ver, "update_available": False}

    info = test_github_connection(
        repo=repo,
        branch=src.get("branch", "main"),
        subfolder=src.get("subfolder", "auto"),
        token=src.get("token", ""),
        api_base=api_base,
    )
    remote_ver = info["remote_version"]
    return {
        "ok": True,
        "configured": True,
        "local_version": local_ver,
        "remote_version": remote_ver,
        "update_available": is_newer_version(remote_ver, local_ver),
        "commit_date": info["commit_date"],
        "subfolder": info["subfolder"],
    }


def plan_github_update(
    base_dir: Optional[Path] = None,
    settings_file: Optional[Path] = None,
    api_base: str = "https://api.github.com",
    include_commits: bool = True,
) -> Dict[str, Any]:
    """Compute exact GitHub delta plan (SHA-1 comparison) without modifying anything."""
    bdir = base_dir or BASE_DIR
    gs = load_global_settings(settings_file)
    src = gs["update_source"]
    repo = src.get("repo", "")
    if not repo:
        raise UpdaterError("لم يتم إعداد مستودع GitHub بعد. انتقل إلى التحديثات ← المصدر.")

    client = GitHubClient(
        repo=repo,
        branch=src.get("branch", "main"),
        token=src.get("token", ""),
        api_base=api_base,
    )
    binfo = client.get_branch_info()
    blobs = client.get_tree(binfo["commit_sha"])
    detected_sub = detect_subfolder(blobs, src.get("subfolder", "auto"))
    prefix = f"{detected_sub}/" if detected_sub else ""

    manifest = load_manifest(bdir)
    local_ver = get_app_version(bdir)

    ver_blob = next((b for b in blobs if b["path"] == f"{prefix}VERSION"), None)
    if not ver_blob:
        raise UpdaterError("ملف VERSION غير موجود في المستودع.")
    remote_ver = client.get_blob_bytes(ver_blob["sha"]).decode("utf-8", errors="replace").strip().lstrip("vV")

    changelog_blob = next((b for b in blobs if b["path"] == f"{prefix}CHANGELOG.md"), None)
    changelog_lines: List[str] = []
    if changelog_blob:
        cl_txt = client.get_blob_bytes(changelog_blob["sha"]).decode("utf-8", errors="replace")
        changelog_lines = extract_changelog_summary(cl_txt)

    local_files = scan_local_managed_files(bdir, manifest)
    remote_managed: Dict[str, Dict[str, Any]] = {}

    for b in blobs:
        full_path = b["path"]
        if prefix and not full_path.startswith(prefix):
            continue
        rel = full_path[len(prefix):] if prefix else full_path
        if not is_managed_relpath(rel, manifest):
            continue
        remote_managed[rel] = {
            "relpath": rel,
            "sha": b["sha"],
            "size": int(b.get("size") or 0),
        }

    to_download: List[Dict[str, Any]] = []
    unchanged: List[str] = []
    for rel, info in sorted(remote_managed.items()):
        loc = local_files.get(rel)
        if loc and loc["sha"] == info["sha"]:
            unchanged.append(rel)
        else:
            to_download.append(info)

    to_delete = sorted(rel for rel in local_files.keys() if rel not in remote_managed)
    total_bytes = sum(int(item["size"]) for item in to_download)
    commit_msgs = client.get_recent_commits(6) if include_commits else []

    return {
        "ok": True,
        "source": "github",
        "local_version": local_ver,
        "remote_version": remote_ver,
        "update_available": is_newer_version(remote_ver, local_ver),
        "commit_sha": binfo["commit_sha"],
        "commit_date": binfo["commit_date"],
        "subfolder": detected_sub,
        "changelog": changelog_lines,
        "commits": commit_msgs,
        "to_download": to_download,
        "to_delete": to_delete,
        "unchanged_count": len(unchanged),
        "change_count": len(to_download) + len(to_delete),
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024.0 * 1024.0), 3),
    }


def stage_github_update(
    plan: Dict[str, Any],
    base_dir: Optional[Path] = None,
    settings_file: Optional[Path] = None,
    api_base: str = "https://api.github.com",
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """
    Download only changed/new blobs into staging/<new_version>/files/ with real byte progress,
    automatic resume for already-verified staged blobs, and strict SHA-1 verification.
    """
    bdir = base_dir or BASE_DIR
    gs = load_global_settings(settings_file)
    src = gs["update_source"]
    client = GitHubClient(
        repo=src.get("repo", ""),
        branch=src.get("branch", "main"),
        token=src.get("token", ""),
        api_base=api_base,
    )

    new_ver = plan["remote_version"]
    stage_root = bdir / "staging" / new_ver
    files_dir = stage_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    to_download = plan.get("to_download") or []
    total_bytes = max(1, int(plan.get("total_bytes") or 0))
    downloaded_bytes = 0
    t0 = time.monotonic()

    def _emit(current_file: str, pct_override: Optional[float] = None) -> None:
        if not progress_callback:
            return
        elapsed = max(0.05, time.monotonic() - t0)
        speed_mb = (downloaded_bytes / (1024.0 * 1024.0)) / elapsed
        pct = pct_override if pct_override is not None else min(100.0, (downloaded_bytes / float(total_bytes)) * 100.0)
        progress_callback({
            "phase": "downloading",
            "bytes_downloaded": downloaded_bytes,
            "total_bytes": int(plan.get("total_bytes") or 0),
            "mb_downloaded": round(downloaded_bytes / (1024.0 * 1024.0), 3),
            "total_mb": round(int(plan.get("total_bytes") or 0) / (1024.0 * 1024.0), 3),
            "speed_mb_s": round(speed_mb, 2),
            "percent": round(pct, 1),
            "current_file": current_file,
            "message": f"جاري تنزيل {current_file}...",
        })

    for item in to_download:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("تم إلغاء التنزيل.")
        rel = item["relpath"]
        expected_sha = item["sha"]
        if is_protected_relpath(rel):
            continue

        dest = files_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Resume support: reuse already-staged file if its Git blob SHA-1 already matches
        if dest.exists():
            existing_bytes = dest.read_bytes()
            if git_blob_sha1(existing_bytes) == expected_sha:
                downloaded_bytes += len(existing_bytes)
                _emit(rel)
                continue

        raw_bytes = client.get_blob_bytes(expected_sha)
        actual_sha = git_blob_sha1(raw_bytes)
        if actual_sha != expected_sha:
            raise HashMismatchError(
                f"فشل التحقق من سلامة الملف ({rel}): البصمة غير مطابقة. تم إلغاء التحديث دون أي تغيير."
            )

        tmp_dest = dest.with_suffix(dest.suffix + ".part")
        tmp_dest.write_bytes(raw_bytes)
        tmp_dest.replace(dest)

        downloaded_bytes += len(raw_bytes)
        _emit(rel)

    plan_meta = {
        "source": "github",
        "old_version": plan["local_version"],
        "new_version": new_ver,
        "to_download": to_download,
        "to_delete": plan.get("to_delete") or [],
        "changelog": plan.get("changelog") or [],
        "total_bytes": int(plan.get("total_bytes") or 0),
    }
    _atomic_write_json(stage_root / "plan.json", plan_meta)
    _emit("اكتمل التنزيل والتحقق", pct_override=100.0)
    return stage_root


def _detect_local_package_root(root_dir: Path) -> Path:
    if (root_dir / "VERSION").exists() and (root_dir / "app" / "server.py").exists():
        return root_dir
    if (root_dir / "autocut" / "VERSION").exists() and (root_dir / "autocut" / "app" / "server.py").exists():
        return root_dir / "autocut"
    for candidate in root_dir.iterdir():
        if candidate.is_dir() and (candidate / "VERSION").exists() and (candidate / "app" / "server.py").exists():
            return candidate
    raise UpdaterError("الحزمة المحلية لا تحتوي على ملفات AutoCut (VERSION و app/server.py).")


def stage_local_update(
    source_path: Path,
    base_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Offline fallback updater (Update 2 compatibility): stage an update from a local .zip file or folder.
    Computes SHA-1 delta against local managed files and stages into staging/<new_version>/.
    """
    bdir = base_dir or BASE_DIR
    src = Path(source_path)
    if not src.exists():
        raise UpdaterError("الملف أو المجلد المحدد غير موجود.")

    manifest = load_manifest(bdir)
    local_ver = get_app_version(bdir)
    local_files = scan_local_managed_files(bdir, manifest)

    extract_tmp: Optional[Path] = None
    try:
        if src.is_file() and src.suffix.lower() == ".zip":
            extract_tmp = bdir / "staging" / "_zip_extract_tmp"
            if extract_tmp.exists():
                shutil.rmtree(extract_tmp)
            extract_tmp.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(src, "r") as zf:
                zf.extractall(extract_tmp)
            pkg_root = _detect_local_package_root(extract_tmp)
        elif src.is_dir():
            pkg_root = _detect_local_package_root(src)
        else:
            raise UpdaterError("يرجى اختيار ملف .zip صالح أو مجلد.")

        new_ver = (pkg_root / "VERSION").read_text(encoding="utf-8", errors="replace").strip().lstrip("vV") or local_ver
        cl_file = pkg_root / "CHANGELOG.md"
        changelog_lines = extract_changelog_summary(cl_file.read_text(encoding="utf-8", errors="replace")) if cl_file.exists() else []

        remote_managed: Dict[str, Dict[str, Any]] = {}
        for fp in sorted(pkg_root.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(pkg_root).as_posix()
            if not is_managed_relpath(rel, manifest):
                continue
            raw = fp.read_bytes()
            remote_managed[rel] = {"relpath": rel, "sha": git_blob_sha1(raw), "size": len(raw), "src": fp}

        to_download: List[Dict[str, Any]] = []
        for rel, info in sorted(remote_managed.items()):
            loc = local_files.get(rel)
            if not loc or loc["sha"] != info["sha"]:
                to_download.append(info)
        to_delete = sorted(rel for rel in local_files.keys() if rel not in remote_managed)
        total_bytes = sum(int(i["size"]) for i in to_download)

        stage_root = bdir / "staging" / new_ver
        files_dir = stage_root / "files"
        if files_dir.exists():
            shutil.rmtree(files_dir)
        files_dir.mkdir(parents=True, exist_ok=True)

        copied_bytes = 0
        for item in to_download:
            dest = files_dir / item["relpath"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = item["src"].read_bytes()
            dest.write_bytes(raw)
            copied_bytes += len(raw)
            if progress_callback:
                pct = (copied_bytes / float(max(1, total_bytes))) * 100.0
                progress_callback({
                    "phase": "downloading",
                    "bytes_downloaded": copied_bytes,
                    "total_bytes": total_bytes,
                    "percent": round(pct, 1),
                    "current_file": item["relpath"],
                    "message": f"جاري تجهيز {item['relpath']}...",
                })

        plan_meta = {
            "source": "local",
            "old_version": local_ver,
            "new_version": new_ver,
            "to_download": [{"relpath": i["relpath"], "sha": i["sha"], "size": i["size"]} for i in to_download],
            "to_delete": to_delete,
            "changelog": changelog_lines,
            "total_bytes": total_bytes,
        }
        _atomic_write_json(stage_root / "plan.json", plan_meta)
        return {"ok": True, "stage_root": str(stage_root), "plan": plan_meta}
    finally:
        if extract_tmp and extract_tmp.exists():
            shutil.rmtree(extract_tmp, ignore_errors=True)
