"""Scene discovery by first integer in filename, conflict checks, ffprobe info, and thumbnails."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .matching import normalize_digits
from .project import file_sha256, get_project_dir, logger

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

_FIRST_INT_RE = re.compile(r"([0-9٠-٩۰-۹]+)")


def extract_scene_number(filename: str) -> Optional[int]:
    """Extract the FIRST integer in a filename (supports Arabic-Indic digits)."""
    stem = Path(filename).stem
    m = _FIRST_INT_RE.search(stem)
    if not m:
        return None
    norm = normalize_digits(m.group(1))
    return int(norm)


def probe_scene_media(path: Path, is_video: bool) -> Dict[str, Any]:
    """Inspect image or video dimensions and duration via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"تعذر قراءة بيانات الملف {path.name}: {res.stderr.strip()}")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    stream = streams[0] if streams else {}
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    duration = 0.0
    if is_video:
        raw_dur = stream.get("duration") or (data.get("format") or {}).get("duration") or 0.0
        duration = float(raw_dur)
    return {"width": width, "height": height, "duration": round(duration, 3)}


def generate_scene_thumbnail(src_path: Path, is_video: bool, dest_jpg: Path, force: bool = False) -> bool:
    """Create a 320px-wide JPEG thumbnail for UI grids and timeline blocks."""
    if dest_jpg.exists() and not force:
        return True
    dest_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y"]
    if is_video:
        cmd += ["-ss", "0.2"]
    cmd += [
        "-i", str(src_path),
        "-vframes", "1",
        "-vf", "scale=320:-2",
        "-q:v", "4",
        str(dest_jpg),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 and is_video:
        # Retry at 0.0s for very short videos
        cmd_retry = [
            "ffmpeg", "-y", "-i", str(src_path),
            "-vframes", "1", "-vf", "scale=320:-2", "-q:v", "4", str(dest_jpg),
        ]
        res = subprocess.run(cmd_retry, capture_output=True, text=True)
    return res.returncode == 0 and dest_jpg.exists()


def scan_scenes_folder(
    project_id: str,
    scenes_dir: str | Path,
    segment_ids: Optional[List[int]] = None,
    generate_thumbs: bool = True,
    projects_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Scan scenes_dir for supported images/videos mapped by first integer.
    Detects conflicts (same number has both image & video, or multiple files).
    Computes missing_numbers and extra_numbers relative to segment_ids.
    """
    folder = Path(scenes_dir)
    if not str(scenes_dir).strip() or not folder.exists() or not folder.is_dir():
        return {
            "ok": False,
            "error": f"مجلد المشاهد غير موجود: {scenes_dir}",
            "scenes": {},
            "conflicts": [],
            "missing_numbers": list(segment_ids or []),
            "extra_numbers": [],
            "unnumbered_files": [],
        }

    by_number: Dict[int, List[Dict[str, Any]]] = {}
    unnumbered_files: List[str] = []

    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            continue
        num = extract_scene_number(entry.name)
        if num is None:
            unnumbered_files.append(entry.name)
            continue
        media_type = "video" if ext in VIDEO_EXTS else "image"
        by_number.setdefault(num, []).append({
            "number": num,
            "path": str(entry.resolve()),
            "filename": entry.name,
            "ext": ext,
            "type": media_type,
        })

    conflicts: List[Dict[str, Any]] = []
    scenes: Dict[str, Dict[str, Any]] = {}
    pdir = get_project_dir(project_id, projects_root)
    thumbs_dir = pdir / "thumbnails"

    for num, entries in sorted(by_number.items()):
        if len(entries) > 1:
            types = {e["type"] for e in entries}
            names = [e["filename"] for e in entries]
            if "image" in types and "video" in types:
                msg = f"المشهد رقم {num} يحتوي على صورة وفيديو معاً ({' ، '.join(names)})"
            else:
                msg = f"المشهد رقم {num} مكرر في أكثر من ملف ({' ، '.join(names)})"
            conflicts.append({"number": num, "files": names, "message": msg})
            continue

        item = entries[0]
        src_path = Path(item["path"])
        is_video = item["type"] == "video"
        try:
            info = probe_scene_media(src_path, is_video)
        except Exception as exc:
            logger.warning("ffprobe failed for %s: %s", src_path, exc)
            info = {"width": 0, "height": 0, "duration": 0.0}

        thumb_name = f"scene_{num}.jpg"
        thumb_path = thumbs_dir / thumb_name
        thumb_url = None
        if generate_thumbs:
            ok_thumb = generate_scene_thumbnail(src_path, is_video, thumb_path)
            if ok_thumb:
                thumb_url = f"/api/projects/{project_id}/thumbnails/{thumb_name}"

        scenes[str(num)] = {
            **item,
            "width": info["width"],
            "height": info["height"],
            "duration": info["duration"],
            "sha256": file_sha256(src_path),
            "thumbnail_url": thumb_url,
        }

    seg_set = set(int(i) for i in (segment_ids or []))
    scene_set = {int(k) for k in scenes.keys()} | {c["number"] for c in conflicts}
    missing_numbers = sorted(seg_set - scene_set)
    extra_numbers = sorted(scene_set - seg_set) if seg_set else []

    if conflicts:
        conflict_msgs = " | ".join(c["message"] for c in conflicts)
        return {
            "ok": False,
            "error": conflict_msgs,
            "scenes": scenes,
            "conflicts": conflicts,
            "missing_numbers": missing_numbers,
            "extra_numbers": extra_numbers,
            "unnumbered_files": unnumbered_files,
        }

    return {
        "ok": True,
        "error": None,
        "scenes": scenes,
        "conflicts": [],
        "missing_numbers": missing_numbers,
        "extra_numbers": extra_numbers,
        "unnumbered_files": unnumbered_files,
    }
