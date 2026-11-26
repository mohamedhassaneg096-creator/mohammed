"""Project persistence, settings, logging, and directory management."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = BASE_DIR / "projects"
LOGS_DIR = BASE_DIR / "logs"
LOG_FILE = LOGS_DIR / "app.log"

DEFAULT_SETTINGS: Dict[str, Any] = {
    "resolution": "1920x1080",
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "max_video_speed_change": 2.0,
    "default_motion_strength": 15,  # Section 5 specifies 15% default (5-40% range)
    "default_motion_duration": 3.5, # Default live/preview motion duration (3-4s)
    "background_fill": "blurred",    # "blurred" | "black"
    "slowmo_smooth": "off",          # "off" | "blend" | "mci"
    "resource_mode": "light",        # "light" (default) | "medium" | "fast"
    "auto_slice_audio": True,        # Stage 1 immediate audio slicing
    "theme": "blue_night",           # "blue_night" | "calm_forest" | "daylight"
    # Future-ready fields (Section 12)
    "aspect_preset": "16:9",
}


def setup_logging() -> logging.Logger:
    """Configure file + stream logger writing detailed errors to logs/app.log."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("autocut")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


logger = setup_logging()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.exception("Failed reading JSON %s: %s", path, exc)
        return default


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _safe_slug(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name.strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:48] or "project"


def get_project_dir(project_id: str, projects_root: Optional[Path] = None) -> Path:
    root = projects_root or PROJECTS_DIR
    # Prevent path traversal
    safe_id = Path(project_id).name
    if not safe_id or safe_id in (".", ".."):
        raise ValueError("Invalid project ID")
    return root / safe_id


def parse_resolution(res_str: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d{3,4})\s*[xX×]\s*(\d{3,4})\s*$", str(res_str or "1920x1080"))
    if not m:
        return 1920, 1080
    return int(m.group(1)), int(m.group(2))


def create_project(name: str, projects_root: Optional[Path] = None) -> Dict[str, Any]:
    root = projects_root or PROJECTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    pid = f"{_safe_slug(name)}_{uuid.uuid4().hex[:6]}"
    pdir = root / pid
    for sub in ("audio", "audio_clips", "cache", "thumbnails", "output", "previews"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)

    now = _utc_now_iso()
    project_meta: Dict[str, Any] = {
        "id": pid,
        "name": name.strip() or "مشروع جديد",
        "created_at": now,
        "updated_at": now,
        "stage": "inputs",
        "audio_path": None,
        "audio_filename": None,
        "audio_hash": None,
        "audio_duration": 0.0,
        "audio_clips": [],
        "transcript_filename": None,
        "transcript_precision": None,
        "scenes_dir": "",
        "raw_text": "",
        "settings": dict(DEFAULT_SETTINGS),
        "scenes_scan": None,
        "export_report": None,
        "overrides": {},
    }
    _atomic_write_json(pdir / "project.json", project_meta)
    return project_meta


def load_project(project_id: str, projects_root: Optional[Path] = None) -> Dict[str, Any]:
    pdir = get_project_dir(project_id, projects_root)
    meta = _read_json(pdir / "project.json")
    if meta is None:
        raise FileNotFoundError(f"Project not found: {project_id}")
    # Ensure defaults for new keys
    settings = dict(DEFAULT_SETTINGS)
    settings.update(meta.get("settings") or {})
    w, h = parse_resolution(settings.get("resolution", "1920x1080"))
    settings["width"] = w
    settings["height"] = h
    meta["settings"] = settings
    return meta


def save_project(project_id: str, updates: Dict[str, Any], projects_root: Optional[Path] = None) -> Dict[str, Any]:
    pdir = get_project_dir(project_id, projects_root)
    meta = load_project(project_id, projects_root)
    for k, v in updates.items():
        if k == "settings" and isinstance(v, dict):
            merged = dict(meta.get("settings") or DEFAULT_SETTINGS)
            merged.update(v)
            w, h = parse_resolution(merged.get("resolution", "1920x1080"))
            merged["width"] = w
            merged["height"] = h
            meta["settings"] = merged
        else:
            meta[k] = v
    meta["updated_at"] = _utc_now_iso()
    _atomic_write_json(pdir / "project.json", meta)
    return meta


def delete_project(project_id: str, projects_root: Optional[Path] = None) -> None:
    pdir = get_project_dir(project_id, projects_root)
    if pdir.exists():
        shutil.rmtree(pdir)


def list_projects(projects_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = projects_root or PROJECTS_DIR
    if not root.exists():
        return []
    items: List[Dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = _read_json(child / "project.json")
        if not meta:
            continue
        cuts = load_cuts(child.name, root)
        motions = load_motions(child.name, root)
        out_video = child / "output" / "final.mp4"
        thumb_url = None
        thumbs_dir = child / "thumbnails"
        if thumbs_dir.exists():
            first_thumb = next((p for p in sorted(thumbs_dir.iterdir()) if p.suffix.lower() in (".jpg", ".png", ".webp")), None)
            if first_thumb:
                thumb_url = f"/api/projects/{child.name}/thumbnails/{first_thumb.name}"

        items.append({
            "id": meta["id"],
            "name": meta["name"],
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "stage": meta.get("stage", "inputs"),
            "audio_filename": meta.get("audio_filename"),
            "audio_duration": meta.get("audio_duration", 0.0),
            "transcript_filename": meta.get("transcript_filename"),
            "segment_count": len(cuts.get("segments", [])) if cuts else 0,
            "motion_count": len(motions.get("assignments", {})) if motions else 0,
            "has_output": out_video.exists(),
            "thumbnail_url": thumb_url,
        })
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return items


def save_transcript(project_id: str, transcript: Dict[str, Any], projects_root: Optional[Path] = None) -> None:
    pdir = get_project_dir(project_id, projects_root)
    _atomic_write_json(pdir / "transcript.json", transcript)


def load_transcript(project_id: str, projects_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    pdir = get_project_dir(project_id, projects_root)
    return _read_json(pdir / "transcript.json")


def save_cuts(project_id: str, cuts: Dict[str, Any], projects_root: Optional[Path] = None) -> None:
    pdir = get_project_dir(project_id, projects_root)
    _atomic_write_json(pdir / "cuts.json", cuts)


def load_cuts(project_id: str, projects_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    pdir = get_project_dir(project_id, projects_root)
    return _read_json(pdir / "cuts.json")


def save_motions(project_id: str, motions: Dict[str, Any], projects_root: Optional[Path] = None) -> None:
    pdir = get_project_dir(project_id, projects_root)
    _atomic_write_json(pdir / "motions.json", motions)


def load_motions(project_id: str, projects_root: Optional[Path] = None) -> Dict[str, Any]:
    pdir = get_project_dir(project_id, projects_root)
    data = _read_json(pdir / "motions.json")
    if not data:
        return {"global_strength": 15, "assignments": {}}
    return data
