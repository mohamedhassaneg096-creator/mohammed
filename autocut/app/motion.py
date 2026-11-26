"""Smooth eased camera motion (Zoom In, Zoom Out, Left, Right, Snap Zoom) for image scenes."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .project import logger

VALID_DIRECTIONS = ("zoom_in", "zoom_out", "left", "right", "snap_zoom")


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    u = 1.0 - t
    return 1.0 - u * u * u


def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


def _snap_curve(p: float, snap_ratio: float) -> float:
    """Fast punch-in snap curve: reaches ~92% zoom inside snap_ratio, then gently creeps to 1.0."""
    sr = clamp(snap_ratio, 0.12, 0.65)
    if p <= sr:
        return 0.92 * ease_out_cubic(p / sr)
    tail = (p - sr) / max(1e-6, 1.0 - sr)
    return 0.92 + 0.08 * smoothstep(tail)


def compute_crop_trajectory(
    direction: str,
    focus_x: float,
    focus_y: float,
    strength_pct: float,
    frame_count: int,
    ease: bool = True,
    fps: int = 30,
) -> List[Dict[str, float]]:
    """
    Compute normalized [0..1] float crop rectangles (x, y, w, h, cx, cy, zoom)
    for every frame. Used both for OpenCV sub-pixel rendering and trajectory verification tests.
    """
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"Unsupported motion direction: {direction}")

    strength = clamp(strength_pct / 100.0, 0.03, 0.45)
    # snap_zoom ("حركة خاطفة") punches deeper (~1.4x strength) for a snappy impact
    if direction == "snap_zoom":
        w_zoomed = clamp(1.0 - min(0.48, strength * 1.45), 0.48, 0.92)
    else:
        # Target window size relative to full frame: 15% strength -> 0.85 window (1.176x zoom)
        w_zoomed = clamp(1.0 - strength, 0.55, 0.96)

    fx = clamp(focus_x, 0.0, 1.0)
    fy = clamp(focus_y, 0.0, 1.0)

    n = max(1, int(frame_count))
    denom = max(1, n - 1)
    snap_ratio = clamp((max(4.0, fps * 0.55)) / denom, 0.14, 0.50)
    traj: List[Dict[str, float]] = []

    for i in range(n):
        p = (i / float(denom)) if n > 1 else 0.0
        if direction == "snap_zoom":
            e = _snap_curve(p, snap_ratio)
            half_w = w_zoomed * 0.5
            cx_t = clamp(fx, half_w, 1.0 - half_w)
            cy_t = clamp(fy, half_w, 1.0 - half_w)
            w = 1.0 + (w_zoomed - 1.0) * e
            cx = 0.5 + (cx_t - 0.5) * e
            cy = 0.5 + (cy_t - 0.5) * e
        else:
            e = smoothstep(p) if ease else p
            if direction == "zoom_in":
                half_w = w_zoomed * 0.5
                cx_t = clamp(fx, half_w, 1.0 - half_w)
                cy_t = clamp(fy, half_w, 1.0 - half_w)
                w = 1.0 + (w_zoomed - 1.0) * e
                cx = 0.5 + (cx_t - 0.5) * e
                cy = 0.5 + (cy_t - 0.5) * e
            elif direction == "zoom_out":
                half_w = w_zoomed * 0.5
                cx_t = clamp(fx, half_w, 1.0 - half_w)
                cy_t = clamp(fy, half_w, 1.0 - half_w)
                w = w_zoomed + (1.0 - w_zoomed) * e
                cx = cx_t + (0.5 - cx_t) * e
                cy = cy_t + (0.5 - cy_t) * e
            elif direction == "left":
                # Camera pans toward the left (center moves from right edge toward left edge)
                w = w_zoomed
                half_w = w * 0.5
                cx_start = 1.0 - half_w
                cx_end = half_w
                cx = cx_start + (cx_end - cx_start) * e
                cy = clamp(fy, half_w, 1.0 - half_w)
            else:  # right
                w = w_zoomed
                half_w = w * 0.5
                cx_start = half_w
                cx_end = 1.0 - half_w
                cx = cx_start + (cx_end - cx_start) * e
                cy = clamp(fy, half_w, 1.0 - half_w)

        h = w
        x = clamp(cx - w * 0.5, 0.0, 1.0 - w)
        y = clamp(cy - h * 0.5, 0.0, 1.0 - h)
        traj.append({
            "frame": i,
            "progress": p,
            "eased": e,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "cx": cx,
            "cy": cy,
            "zoom": 1.0 / w,
        })

    return traj


def build_background_fit_filter(width: int, height: int, background_fill: str = "blurred") -> str:
    """
    Build an FFmpeg filtergraph that fits input [0:v] into width x height
    without stretching, filling borders with blurred copy or solid black.
    """
    if background_fill == "black":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1"
        )
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase:flags=bilinear,"
        f"crop={width}:{height},boxblur=24:4,eq=brightness=-0.08[bg_blur];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fg_fit];"
        f"[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def _zoompan_expr(
    direction: str,
    focus_x: float,
    focus_y: float,
    strength_pct: float,
    frame_count: int,
    ease: bool,
    out_w: int,
    out_h: int,
    fps: int,
) -> str:
    """
    Build FFmpeg zoompan filter string using smoothstep (or rapid snap_zoom) easing on an 8000px-wide canvas.
    Pre-upscaling to 8000px wide reduces zoompan integer crop rounding to sub-0.25px in 1080p!
    """
    strength = clamp(strength_pct / 100.0, 0.03, 0.45)
    if direction == "snap_zoom":
        w_zoomed = clamp(1.0 - min(0.48, strength * 1.45), 0.48, 0.92)
    else:
        w_zoomed = clamp(1.0 - strength, 0.55, 0.96)

    z_max = 1.0 / w_zoomed
    half_w = w_zoomed * 0.5
    fx = clamp(focus_x, 0.0, 1.0)
    fy = clamp(focus_y, 0.0, 1.0)

    denom = max(1, frame_count - 1)
    p_expr = f"(on/{denom})"

    if direction == "snap_zoom":
        # Fast snap phase over first ~0.55s (clamped ratio), then slight hold drift
        snap_ratio = clamp((max(4.0, fps * 0.55)) / denom, 0.14, 0.50)
        sr_s = f"{snap_ratio:.6f}"
        # t1 = 1 - p/sr -> ease_out = 1 - t1^3
        t1 = f"(1-({p_expr}/{sr_s}))"
        snap_e = f"(0.92*(1-{t1}*{t1}*{t1}))"
        tail_e = f"(0.92+0.08*(({p_expr}-{sr_s})/(1-{sr_s})))"
        e_expr = f"if(lte({p_expr},{sr_s}),{snap_e},{tail_e})"
        cx_t = clamp(fx, half_w, 1.0 - half_w)
        cy_t = clamp(fy, half_w, 1.0 - half_w)
        w_e = f"(1+({w_zoomed:.6f}-1)*{e_expr})"
        z_expr = f"(1/{w_e})"
        cx_e = f"(0.5+({cx_t:.6f}-0.5)*{e_expr})"
        cy_e = f"(0.5+({cy_t:.6f}-0.5)*{e_expr})"
        x_expr = f"iw*({cx_e}-{w_e}/2)"
        y_expr = f"ih*({cy_e}-{w_e}/2)"
    elif direction == "zoom_in":
        e_expr = f"({p_expr}*{p_expr}*(3-2*{p_expr}))" if ease else p_expr
        cx_t = clamp(fx, half_w, 1.0 - half_w)
        cy_t = clamp(fy, half_w, 1.0 - half_w)
        w_e = f"(1+({w_zoomed:.6f}-1)*{e_expr})"
        z_expr = f"(1/{w_e})"
        cx_e = f"(0.5+({cx_t:.6f}-0.5)*{e_expr})"
        cy_e = f"(0.5+({cy_t:.6f}-0.5)*{e_expr})"
        x_expr = f"iw*({cx_e}-{w_e}/2)"
        y_expr = f"ih*({cy_e}-{w_e}/2)"
    elif direction == "zoom_out":
        e_expr = f"({p_expr}*{p_expr}*(3-2*{p_expr}))" if ease else p_expr
        cx_t = clamp(fx, half_w, 1.0 - half_w)
        cy_t = clamp(fy, half_w, 1.0 - half_w)
        w_e = f"({w_zoomed:.6f}+(1-{w_zoomed:.6f})*{e_expr})"
        z_expr = f"(1/{w_e})"
        cx_e = f"({cx_t:.6f}+(0.5-{cx_t:.6f})*{e_expr})"
        cy_e = f"({cy_t:.6f}+(0.5-{cy_t:.6f})*{e_expr})"
        x_expr = f"iw*({cx_e}-{w_e}/2)"
        y_expr = f"ih*({cy_e}-{w_e}/2)"
    elif direction == "left":
        e_expr = f"({p_expr}*{p_expr}*(3-2*{p_expr}))" if ease else p_expr
        cy_c = clamp(fy, half_w, 1.0 - half_w)
        cx_start = 1.0 - half_w
        cx_end = half_w
        z_expr = f"{z_max:.6f}"
        cx_e = f"({cx_start:.6f}+({cx_end:.6f}-{cx_start:.6f})*{e_expr})"
        x_expr = f"iw*({cx_e}-{half_w:.6f})"
        y_expr = f"ih*{cy_c - half_w:.6f}"
    else:  # right
        e_expr = f"({p_expr}*{p_expr}*(3-2*{p_expr}))" if ease else p_expr
        cy_c = clamp(fy, half_w, 1.0 - half_w)
        cx_start = half_w
        cx_end = 1.0 - half_w
        z_expr = f"{z_max:.6f}"
        cx_e = f"({cx_start:.6f}+({cx_end:.6f}-{cx_start:.6f})*{e_expr})"
        x_expr = f"iw*({cx_e}-{half_w:.6f})"
        y_expr = f"ih*{cy_c - half_w:.6f}"

    return (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={frame_count}:s={out_w}x{out_h}:fps={fps}"
    )


def render_image_motion_clip(
    src_image: Path,
    out_clip: Path,
    frame_count: int,
    width: int,
    height: int,
    fps: int,
    background_fill: str = "blurred",
    motion: Optional[Dict[str, Any]] = None,
    global_strength: float = 15.0,
    ffmpeg_threads: Optional[int] = None,
) -> None:
    """
    Render a single image segment (still or smooth/snap zoom/pan) to H.264 MP4 with exact frame_count.
    Supports optional ffmpeg_threads cap to prevent CPU overheating on long 45-60 min projects.
    """
    out_clip.parent.mkdir(parents=True, exist_ok=True)
    fit_filter = build_background_fit_filter(width, height, background_fill)
    thread_args = ["-threads", str(ffmpeg_threads)] if ffmpeg_threads and ffmpeg_threads > 0 else []

    if not motion or not motion.get("type"):
        vf = f"{fit_filter},fps={fps},format=yuv420p"
        cmd = [
            "ffmpeg", "-y",
            *thread_args,
            "-loop", "1",
            "-i", str(src_image),
            "-vf", vf,
            "-frames:v", str(frame_count),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-an",
            str(out_clip),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg error rendering image {src_image.name}: {res.stderr[-600:]}")
        return

    direction = motion["type"]
    if direction not in VALID_DIRECTIONS:
        direction = "zoom_in"
    fx = float(motion.get("x", 0.5))
    fy = float(motion.get("y", 0.5))
    strength = float(motion.get("strength") if motion.get("strength") is not None else global_strength)
    ease = bool(motion.get("ease", True))

    upscale_w = 8000
    upscale_h = int(round(upscale_w * (height / float(width))))
    if upscale_h % 2 != 0:
        upscale_h += 1

    zp = _zoompan_expr(
        direction=direction,
        focus_x=fx,
        focus_y=fy,
        strength_pct=strength,
        frame_count=frame_count,
        ease=ease,
        out_w=width,
        out_h=height,
        fps=fps,
    )

    vf = (
        f"{fit_filter},"
        f"scale={upscale_w}:{upscale_h}:flags=lanczos,"
        f"{zp},"
        f"scale={width}:{height}:flags=lanczos,"
        f"format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        *thread_args,
        "-i", str(src_image),
        "-vf", vf,
        "-frames:v", str(frame_count),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-an",
        str(out_clip),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error("FFmpeg zoompan failed: %s", res.stderr[-1000:])
        raise RuntimeError(f"فشل تصيير حركة الصورة {src_image.name}: {res.stderr[-400:]}")


def render_image_motion_opencv(
    src_image: Path,
    out_clip: Path,
    frame_count: int,
    width: int,
    height: int,
    fps: int,
    background_fill: str = "blurred",
    motion: Dict[str, Any] = None,
    global_strength: float = 15.0,
    ffmpeg_threads: Optional[int] = None,
) -> None:
    """
    Method B: Sub-pixel float crop via OpenCV warpAffine + INTER_LANCZOS4 piped to FFmpeg stdin.
    """
    import cv2
    import numpy as np

    base_png = out_clip.with_suffix(".base.png")
    base_w, base_h = width * 2, height * 2
    fit_filter = build_background_fit_filter(base_w, base_h, background_fill)
    prep = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src_image), "-vf", fit_filter, "-frames:v", "1", str(base_png)],
        capture_output=True, text=True,
    )
    if prep.returncode != 0:
        raise RuntimeError(f"Failed preparing base frame: {prep.stderr[-400:]}")

    img = cv2.imread(str(base_png), cv2.IMREAD_COLOR)
    try:
        base_png.unlink(missing_ok=True)
    except Exception:
        pass
    if img is None:
        raise RuntimeError(f"Could not read prepared image for {src_image.name}")

    ih, iw = img.shape[:2]
    direction = motion["type"]
    traj = compute_crop_trajectory(
        direction=direction,
        focus_x=float(motion.get("x", 0.5)),
        focus_y=float(motion.get("y", 0.5)),
        strength_pct=float(motion.get("strength") if motion.get("strength") is not None else global_strength),
        frame_count=frame_count,
        ease=bool(motion.get("ease", True)),
        fps=fps,
    )

    thread_args = ["-threads", str(ffmpeg_threads)] if ffmpeg_threads and ffmpeg_threads > 0 else []
    out_clip.parent.mkdir(parents=True, exist_ok=True)
    ff_cmd = [
        "ffmpeg", "-y",
        *thread_args,
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_clip),
    ]
    proc = subprocess.Popen(ff_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    for step in traj:
        src_x = step["x"] * iw
        src_y = step["y"] * ih
        src_w = step["w"] * iw
        src_h = step["h"] * ih
        sx = width / src_w
        sy = height / src_h
        M = np.float32([[sx, 0.0, -src_x * sx], [0.0, sy, -src_y * sy]])
        frame = cv2.warpAffine(img, M, (width, height), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    stderr_out = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"OpenCV pipe FFmpeg failed: {stderr_out[-400:]}")
