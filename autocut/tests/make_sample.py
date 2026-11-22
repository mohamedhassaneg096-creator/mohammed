"""Generate sample audio, numbered images, and testsrc videos via FFmpeg (supports Arabic/space paths)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Tuple

# Distinct signature RGB colors per scene index so extracted frames can be verified programmatically
SCENE_COLORS: Dict[int, Tuple[int, int, int]] = {
    1: (220, 40, 40),    # Red
    2: (40, 190, 60),    # Green
    3: (40, 100, 230),   # Blue
    4: (230, 190, 30),   # Yellow
    5: (190, 50, 210),   # Magenta
}


def make_sine_audio(out_wav: Path, duration_s: float = 8.0) -> Path:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration_s}",
        "-ar", "44100",
        "-ac", "2",
        str(out_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed generating sample audio: {res.stderr}")
    return out_wav


def make_solid_scene_image(out_png: Path, scene_num: int, width: int = 1280, height: int = 720) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    r, g, b = SCENE_COLORS.get(scene_num, ((scene_num * 53) % 200 + 30, (scene_num * 97) % 200 + 30, (scene_num * 151) % 200 + 30))
    hex_col = f"0x{r:02X}{g:02X}{b:02X}"
    # Add high-detail inner grid/bars so motion & smoothness tests also have rich edges
    vf = (
        f"drawbox=x=40:y=40:w={width-80}:h={height-80}:color=white@0.22:t=4,"
        f"drawbox=x=120:y=120:w={80 + scene_num * 40}:h=120:color=white@0.35:t=fill"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={hex_col}:s={width}x{height}",
        "-vf", vf,
        "-frames:v", "1",
        str(out_png),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed generating sample image: {res.stderr}")
    return out_png


def make_testsrc_video(out_mp4: Path, duration_s: float = 1.5, width: int = 640, height: int = 360) -> Path:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration_s}:size={width}x{height}:rate=30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(out_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed generating testsrc video: {res.stderr}")
    return out_mp4
