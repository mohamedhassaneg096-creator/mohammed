"""Media duration probing via ffprobe (transcription is external import-only in AutoCut v1.2+)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def probe_media_duration(media_path: Path) -> float:
    """Return media duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(media_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media_path}: {res.stderr.strip()}")
    data = json.loads(res.stdout or "{}")
    dur = float((data.get("format") or {}).get("duration") or 0.0)
    return dur
