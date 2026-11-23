"""Separate helper process for safe Windows file swapping, backups, requirements diff, migrations, self-test, and rollback."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .migrations_runner import run_all_migrations
from .project import BASE_DIR, _atomic_write_json, _read_json, logger
from .settings_store import get_app_version
from .updater import is_protected_relpath, load_manifest

MAX_BACKUPS = 5
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def parse_requirements(req_text: str) -> Dict[str, str]:
    """Parse requirements.txt into {normalized_pkg_name: full_requirement_line}."""
    parsed: Dict[str, str] = {}
    for raw in (req_text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _REQ_NAME_RE.match(line)
        key = m.group(1).lower().replace("_", "-") if m else line.lower()
        parsed[key] = line
    return parsed


def diff_requirements(old_text: str, new_text: str) -> List[str]:
    """Return ONLY packages that are newly added or changed in requirements.txt."""
    old_map = parse_requirements(old_text)
    new_map = parse_requirements(new_text)
    changed: List[str] = []
    for pkg, spec in new_map.items():
        if pkg not in old_map or old_map[pkg] != spec:
            changed.append(spec)
    return changed


def default_pip_installer(packages: List[str]) -> Tuple[bool, str]:
    """Install changed packages via current interpreter's pip."""
    for pkg in packages:
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            return False, pkg
    return True, ""


def default_self_test(base_dir: Path) -> Tuple[bool, str]:
    """
    Quick self-test after swapping files:
    1) Import core AutoCut modules
    2) Verify ffmpeg/ffprobe
    3) Tiny FFmpeg color clip render
    4) Verify server /api/health handler (if FastAPI installed)
    """
    bdir_str = str(base_dir.resolve())
    if bdir_str not in sys.path:
        sys.path.insert(0, bdir_str)

    try:
        for mod_name in (
            "app.project",
            "app.matching",
            "app.scenes",
            "app.timeline",
            "app.motion",
            "app.render",
            "app.transcribe",
            "app.transcript_import",
            "app.workspace_helpers",
            "app.settings_store",
            "app.updater",
        ):
            mod = importlib.import_module(mod_name)
            importlib.reload(mod)
    except Exception as exc:
        return False, f"فشل استيراد وحدات البرنامج بعد التحديث: {exc}"

    try:
        ff = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        fp = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True)
        if ff.returncode != 0 or fp.returncode != 0:
            return False, "فشل التحقق من FFmpeg/FFprobe أثناء الاختبار الذاتي."
    except FileNotFoundError:
        return False, "أداة FFmpeg غير موجودة في النظام."

    # Tiny 4-frame render test
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out_mp4 = Path(tmp) / "selftest.mp4"
            res = subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=128x72:r=24",
                    "-frames:v", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_mp4),
                ],
                capture_output=True, text=True,
            )
            if res.returncode != 0 or not out_mp4.exists() or out_mp4.stat().st_size == 0:
                return False, "فشل اختبار تصيير الفيديو السريع بعد التحديث."
    except Exception as exc:
        return False, f"فشل اختبار التصيير الذاتي: {exc}"

    # Check server health function when FastAPI is installed
    try:
        srv = importlib.import_module("app.server")
        importlib.reload(srv)
        health = srv.api_health()
        if not health.get("ffmpeg_ok"):
            return False, "فشل فحص /api/health بعد التحديث."
    except ImportError:
        # Allowed in minimal test environments without FastAPI
        pass
    except Exception as exc:
        return False, f"فشل اختبار خادم الويب (/api/health): {exc}"

    return True, ""


def _prune_old_backups(backups_dir: Path, keep: int = MAX_BACKUPS) -> None:
    if not backups_dir.exists():
        return
    dirs = [d for d in backups_dir.iterdir() if d.is_dir() and (d / "backup_meta.json").exists()]
    dirs.sort(key=lambda d: ((_read_json(d / "backup_meta.json") or {}).get("created_at", ""), d.stat().st_mtime_ns), reverse=True)
    for old in dirs[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def list_backups(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    bdir = base_dir or BASE_DIR
    backups_dir = bdir / "backups"
    if not backups_dir.exists():
        return []
    items: List[Dict[str, Any]] = []
    for d in backups_dir.iterdir():
        meta_path = d / "backup_meta.json"
        meta = _read_json(meta_path)
        if d.is_dir() and isinstance(meta, dict):
            items.append({
                "id": d.name,
                "old_version": meta.get("old_version", d.name),
                "target_version": meta.get("target_version", ""),
                "created_at": meta.get("created_at", ""),
                "file_count": len(meta.get("backed_up_files") or []),
                "_mtime_ns": meta_path.stat().st_mtime_ns if meta_path.exists() else 0,
            })
    items.sort(key=lambda x: (x.get("created_at", ""), x.get("_mtime_ns", 0)), reverse=True)
    for it in items:
        it.pop("_mtime_ns", None)
    return items


def rollback_to_backup(base_dir: Path, backup_id: str) -> Dict[str, Any]:
    """Restore backed-up bytes and remove newly added managed files from the rolled-back update."""
    backups_dir = base_dir / "backups"
    bpath = backups_dir / Path(backup_id).name
    meta = _read_json(bpath / "backup_meta.json")
    if not isinstance(meta, dict):
        raise FileNotFoundError(f"النسخة الاحتياطية غير موجودة: {backup_id}")

    files_root = bpath / "files"
    for rel in meta.get("added_files") or []:
        if is_protected_relpath(rel):
            continue
        target = base_dir / rel
        if target.exists() and target.is_file():
            target.unlink()

    for rel in meta.get("backed_up_files") or []:
        if is_protected_relpath(rel):
            continue
        src = files_root / rel
        if src.exists() and src.is_file():
            dest = base_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    restored_ver = get_app_version(base_dir)
    return {"ok": True, "restored_version": restored_ver, "backup_id": backup_id}


def apply_staged_update(
    base_dir: Path,
    stage_root: Path,
    pip_installer: Optional[Callable[[List[str]], Tuple[bool, str]]] = None,
    self_test_runner: Optional[Callable[[Path], Tuple[bool, str]]] = None,
) -> Dict[str, Any]:
    """
    Execute safe update apply sequence:
    1. Read staging/<ver>/plan.json
    2. Backup replaced & deleted files into backups/<old_ver>_<ts>/ (keep last 5)
    3. Swap staged files & delete removed managed files (never touching protected paths)
    4. Diff requirements.txt and install ONLY changed packages
    5. Run idempotent migrations/ on projects/
    6. Run self-test; on failure rollback automatically
    """
    plan = _read_json(stage_root / "plan.json")
    if not isinstance(plan, dict):
        raise RuntimeError("ملف خطة التحديث غير موجود في staging.")

    old_ver = plan.get("old_version") or get_app_version(base_dir)
    new_ver = plan.get("new_version") or old_ver
    staged_files_dir = stage_root / "files"

    # Snapshot old requirements before swap
    old_req_file = base_dir / "requirements.txt"
    old_req_text = old_req_file.read_text(encoding="utf-8", errors="replace") if old_req_file.exists() else ""

    # 2. Backup all files to be replaced or deleted
    now_utc = datetime.now(timezone.utc)
    ts = now_utc.strftime("%Y%m%d_%H%M%S_%f")
    backup_id = f"{old_ver}_{ts}"
    backup_dir = base_dir / "backups" / backup_id
    backup_files_dir = backup_dir / "files"
    backup_files_dir.mkdir(parents=True, exist_ok=True)

    backed_up_files: List[str] = []
    added_files: List[str] = []

    to_download = plan.get("to_download") or []
    to_delete = plan.get("to_delete") or []

    for item in to_download:
        rel = item["relpath"]
        if is_protected_relpath(rel):
            continue
        cur = base_dir / rel
        if cur.exists() and cur.is_file():
            bdest = backup_files_dir / rel
            bdest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cur, bdest)
            backed_up_files.append(rel)
        else:
            added_files.append(rel)

    for rel in to_delete:
        if is_protected_relpath(rel):
            continue
        cur = base_dir / rel
        if cur.exists() and cur.is_file():
            bdest = backup_files_dir / rel
            bdest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cur, bdest)
            backed_up_files.append(rel)

    _atomic_write_json(backup_dir / "backup_meta.json", {
        "old_version": old_ver,
        "target_version": new_ver,
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "backed_up_files": backed_up_files,
        "added_files": added_files,
    })
    _prune_old_backups(base_dir / "backups", MAX_BACKUPS)

    # 3. Swap staged files & remove deleted managed files
    for item in to_download:
        rel = item["relpath"]
        if is_protected_relpath(rel):
            continue
        src_file = staged_files_dir / rel
        if not src_file.exists():
            rollback_to_backup(base_dir, backup_id)
            return {
                "ok": False,
                "rolled_back": True,
                "version": old_ver,
                "error": f"الملف المرحّل مفقود: {rel}. تم التراجع للإصدار {old_ver}.",
            }
        dest = base_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)

    manifest = load_manifest(base_dir)
    for rel in to_delete:
        if is_protected_relpath(rel):
            continue
        # Double-check managed status before deleting
        from .updater import is_managed_relpath
        if not is_managed_relpath(rel, manifest):
            continue
        target = base_dir / rel
        if target.exists() and target.is_file():
            target.unlink()

    # 4. Requirements diff & selective pip install
    new_req_text = old_req_file.read_text(encoding="utf-8", errors="replace") if old_req_file.exists() else ""
    req_changes = diff_requirements(old_req_text, new_req_text)
    if req_changes:
        installer = pip_installer or default_pip_installer
        pip_ok, failed_pkg = installer(req_changes)
        if not pip_ok:
            rollback_to_backup(base_dir, backup_id)
            return {
                "ok": False,
                "rolled_back": True,
                "version": old_ver,
                "failed_package": failed_pkg,
                "error": f"فشل تثبيت الحزمة ({failed_pkg}) عبر pip (تأكد من الاتصال بالإنترنت). تم التراجع تلقائياً للإصدار {old_ver}.",
            }

    # 5. Idempotent migrations on existing projects & settings
    try:
        mig_res = run_all_migrations(base_dir=base_dir)
    except Exception as exc:
        rollback_to_backup(base_dir, backup_id)
        return {
            "ok": False,
            "rolled_back": True,
            "version": old_ver,
            "error": f"فشل ترقية بيانات المشاريع: {exc}. تم التراجع للإصدار {old_ver}.",
        }

    # 6. Self-test with automatic rollback on failure
    tester = self_test_runner or default_self_test
    test_ok, test_err = tester(base_dir)
    if not test_ok:
        rollback_to_backup(base_dir, backup_id)
        return {
            "ok": False,
            "rolled_back": True,
            "version": old_ver,
            "error": f"{test_err} تم التراجع تلقائياً للإصدار {old_ver}.",
        }

    # Cleanup staged files after success
    shutil.rmtree(stage_root, ignore_errors=True)
    result = {
        "ok": True,
        "rolled_back": False,
        "old_version": old_ver,
        "new_version": get_app_version(base_dir),
        "changelog": plan.get("changelog") or [],
        "backup_id": backup_id,
        "pip_installed": req_changes,
        "migrations": mig_res,
    }
    _atomic_write_json(base_dir / "staging" / "last_update_result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoCut external swap helper")
    parser.add_argument("--action", choices=["apply", "rollback"], required=True)
    parser.add_argument("--base-dir", default=str(BASE_DIR))
    parser.add_argument("--stage-dir", default="")
    parser.add_argument("--backup-id", default="")
    parser.add_argument("--wait-pid", type=int, default=0)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    bdir = Path(args.base_dir).resolve()
    if args.wait_pid > 0:
        # Wait briefly for main engine process to exit and release file locks on Windows
        for _ in range(40):
            try:
                os.kill(args.wait_pid, 0)
                time.sleep(0.15)
            except OSError:
                break

    if args.action == "apply":
        res = apply_staged_update(bdir, Path(args.stage_dir).resolve())
    else:
        res = rollback_to_backup(bdir, args.backup_id)
        _atomic_write_json(bdir / "staging" / "last_update_result.json", {
            "ok": True,
            "rolled_back": False,
            "new_version": res["restored_version"],
            "changelog": [f"تم التراجع إلى النسخة الاحتياطية {args.backup_id}"],
        })

    if not res.get("ok"):
        _atomic_write_json(bdir / "staging" / "last_update_result.json", res)

    pending_file = bdir / "staging" / ".pending_swap.json"
    if pending_file.exists():
        pending_file.unlink(missing_ok=True)

    if args.restart:
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=str(bdir),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
