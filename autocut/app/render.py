"""Throttled two-stage pipeline: Stage 1 immediate audio slicing + Stage 2 per-scene clip rendering, pause/resume, caching, and final muxing."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .motion import build_background_fit_filter, render_image_motion_clip
from .project import _atomic_write_json, file_sha256, get_project_dir, logger
from .transcribe import probe_media_duration

RESOURCE_PROFILES: Dict[str, Dict[str, Any]] = {
    "light": {
        "id": "light",
        "label": "خفيف (الافتراضي — يحافظ على الحرارة والرامات)",
        "workers": 1,
        "ffmpeg_threads": 2,
        "rest_delay": 0.35,
    },
    "medium": {
        "id": "medium",
        "label": "متوسط (توازن بين السرعة والاستهلاك)",
        "workers": 2,
        "ffmpeg_threads": 3,
        "rest_delay": 0.12,
    },
    "fast": {
        "id": "fast",
        "label": "سريع (أقصى استغلال للمعالج)",
        "workers": 0,  # resolved dynamically as half cores
        "ffmpeg_threads": 0,
        "rest_delay": 0.0,
    },
}


def get_resource_profile(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return throttled worker/thread/delay parameters based on settings['resource_mode']."""
    mode = str((settings or {}).get("resource_mode") or "light").lower()
    if mode not in RESOURCE_PROFILES:
        mode = "light"
    prof = dict(RESOURCE_PROFILES[mode])
    if prof["workers"] <= 0:
        prof["workers"] = max(1, (os.cpu_count() or 2) // 2)
    return prof


def _wait_if_paused(
    pause_event: Optional[threading.Event],
    cancel_event: Optional[threading.Event],
) -> None:
    """Block cleanly while paused without burning CPU; abort immediately if cancelled."""
    while pause_event is not None and pause_event.is_set():
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("تم إلغاء العملية.")
        time.sleep(0.15)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("تم إلغاء العملية.")


def probe_video_stats(video_path: Path) -> Dict[str, Any]:
    """Return duration and frame count of a video clip via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-count_frames",
        "-show_entries", "stream=nb_read_frames,nb_frames,width,height,r_frame_rate,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return {"duration": 0.0, "frames": 0}
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    s = streams[0] if streams else {}
    dur = float((data.get("format") or {}).get("duration") or s.get("duration") or 0.0)
    frames = int(s.get("nb_read_frames") or s.get("nb_frames") or 0)
    return {
        "duration": round(dur, 4),
        "frames": frames,
        "width": int(s.get("width") or 0),
        "height": int(s.get("height") or 0),
    }


def slice_audio_into_clips(
    project_id: str,
    audio_path: Path,
    cuts: Dict[str, Any],
    settings: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
    projects_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Stage 1: Immediately slice original narration audio into lightweight per-scene audio clips
    chunk-by-chunk with resource throttling and Pause/Resume support.
    """
    t0 = time.monotonic()
    pdir = get_project_dir(project_id, projects_root)
    clips_dir = pdir / "audio_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    segments = cuts.get("segments") or []
    if not segments:
        return []

    prof = get_resource_profile(settings)
    thread_args = ["-threads", str(prof["ffmpeg_threads"])] if prof["ffmpeg_threads"] > 0 else []
    audio_hash = file_sha256(audio_path)[:12]
    total = len(segments)
    clip_states: Dict[int, str] = {int(seg["id"]): "waiting" for seg in segments}
    results: List[Dict[str, Any]] = []

    def emit(msg: str, completed: int, pct: Optional[float] = None) -> None:
        if not progress_callback:
            return
        elapsed = time.monotonic() - t0
        cur_pct = pct if pct is not None else (completed / max(1, total)) * 100.0
        eta = None
        if 0 < completed < total:
            eta = round((elapsed / completed) * (total - completed), 1)
        progress_callback({
            "phase": "slicing_audio",
            "stage_num": 1,
            "paused": bool(pause_event and pause_event.is_set()),
            "progress": round(cur_pct, 1),
            "elapsed": round(elapsed, 1),
            "eta": eta,
            "completed": completed,
            "total": total,
            "message": msg,
            "clip_states": dict(clip_states),
            "audio_clips": list(results),
        })

    emit("المرحلة 1: بدء قص الصوت فوراً إلى مقاطع مرقمة...", 0, 1.0)

    for idx, seg in enumerate(segments):
        _wait_if_paused(pause_event, cancel_event)
        sid = int(seg["id"])
        start = round(max(0.0, float(seg["start"])), 3)
        end = round(max(start + 0.05, float(seg["end"])), 3)
        dur = round(max(0.05, end - start), 3)
        clip_states[sid] = "rendering"
        emit(f"قص مقطع الصوت #{sid} ({idx + 1}/{total})...", idx)

        key = hashlib.sha256(f"{audio_hash}:{sid}:{start}:{dur}".encode("utf-8")).hexdigest()[:12]
        out_name = f"clip_{sid}_{key}.m4a"
        out_path = clips_dir / out_name

        # Remove stale slices for same segment id
        for old in clips_dir.glob(f"clip_{sid}_*.m4a"):
            if old.name != out_name:
                old.unlink(missing_ok=True)

        cached = out_path.exists() and out_path.stat().st_size > 256
        if not cached:
            tmp_out = clips_dir / f"tmp_{sid}_{key}.m4a"
            cmd = [
                "ffmpeg", "-y",
                *thread_args,
                "-ss", f"{start:.3f}",
                "-t", f"{dur:.3f}",
                "-i", str(audio_path),
                "-vn",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(tmp_out),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"فشل قص المقطع الصوتي #{sid}: {res.stderr[-400:]}")
            os.replace(tmp_out, out_path)
            if prof["rest_delay"] > 0 and idx < total - 1:
                time.sleep(min(0.25, prof["rest_delay"] * 0.5))

        clip_states[sid] = "done"
        results.append({
            "segment_id": sid,
            "start": start,
            "end": end,
            "duration": dur,
            "text": seg.get("text", ""),
            "filename": out_name,
            "path": str(out_path.resolve()),
            "url": f"/api/projects/{project_id}/audio-clips/{sid}",
            "size_bytes": out_path.stat().st_size,
            "cached": cached,
        })
        emit(f"تم قص مقطع الصوت #{sid} ({idx + 1}/{total})", idx + 1)

    _atomic_write_json(clips_dir / "manifest.json", {"audio_hash": audio_hash, "clips": results})
    emit("اكتملت المرحلة 1: جاهز لتركيب المشاهد!", total, 100.0)
    return results


def compute_clip_cache_key(
    item: Dict[str, Any],
    settings: Dict[str, Any],
    motion: Optional[Dict[str, Any]],
    global_strength: float,
) -> str:
    """Hash all parameters affecting a single clip so unchanged clips reuse cache."""
    eff_motion = None
    if item["scene_type"] == "image" and motion and motion.get("type"):
        eff_motion = {
            "type": motion["type"],
            "x": round(float(motion.get("x", 0.5)), 4),
            "y": round(float(motion.get("y", 0.5)), 4),
            "strength": float(motion.get("strength") if motion.get("strength") is not None else global_strength),
            "ease": bool(motion.get("ease", True)),
            "duration": round(float(motion.get("duration") or settings.get("default_motion_duration", 3.5)), 2),
        }

    slowmo_mode = item.get("slowmo_smooth") or settings.get("slowmo_smooth", "off")
    payload = {
        "v": 2,
        "segment_id": item["segment_id"] if item["scene_type"] == "missing" else 0,
        "scene_type": item["scene_type"],
        "scene_sha256": item.get("scene_sha256") or "",
        "frame_count": item["frame_count"],
        "speed": item.get("speed", 1.0) if item["scene_type"] == "video" else 1.0,
        "width": int(settings.get("width", 1920)),
        "height": int(settings.get("height", 1080)),
        "fps": int(settings.get("fps", 30)),
        "background_fill": settings.get("background_fill", "blurred"),
        "slowmo_smooth": slowmo_mode if item["scene_type"] == "video" else "off",
        "motion": eff_motion,
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _render_missing_scene_clip(
    segment_id: int,
    out_clip: Path,
    frame_count: int,
    width: int,
    height: int,
    fps: int,
    ffmpeg_threads: Optional[int] = None,
) -> None:
    """Render a clean dark slate placeholder if user exported despite missing-scene warning."""
    out_clip.parent.mkdir(parents=True, exist_ok=True)
    thread_args = ["-threads", str(ffmpeg_threads)] if ffmpeg_threads and ffmpeg_threads > 0 else []
    cmd = [
        "ffmpeg", "-y",
        *thread_args,
        "-f", "lavfi",
        "-i", f"color=c=#171A21:s={width}x{height}:r={fps}",
        "-vf", f"drawbox=x=iw*0.1:y=ih*0.1:w=iw*0.8:h=ih*0.8:color=#2A2F3A:t=3,format=yuv420p",
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
        raise RuntimeError(f"Failed rendering placeholder for missing scene {segment_id}: {res.stderr[-400:]}")


def _render_video_scene_clip(
    src_video: Path,
    out_clip: Path,
    frame_count: int,
    speed: float,
    width: int,
    height: int,
    fps: int,
    background_fill: str = "blurred",
    slowmo_smooth: str = "off",
    ffmpeg_threads: Optional[int] = None,
) -> None:
    """
    Stretch or compress the ENTIRE video from first to last frame to match exact frame_count.
    Muted (-an), never trimmed, never looped.
    """
    out_clip.parent.mkdir(parents=True, exist_ok=True)
    fit_filter = build_background_fit_filter(width, height, background_fill)
    safe_speed = max(0.05, float(speed))
    thread_args = ["-threads", str(ffmpeg_threads)] if ffmpeg_threads and ffmpeg_threads > 0 else []

    pts_filter = f"setpts=(PTS-STARTPTS)/{safe_speed:.8f}"
    filters = [pts_filter]

    if slowmo_smooth in ("blend", "mci") and safe_speed < 0.7:
        filters.append(f"minterpolate=fps={fps}:mi_mode={slowmo_smooth}")
    else:
        filters.append(f"fps={fps}")

    filters.append(fit_filter)
    filters.append("tpad=stop_mode=clone:stop_duration=0.25")
    filters.append("format=yuv420p")

    vf = ",".join(filters)
    cmd = [
        "ffmpeg", "-y",
        *thread_args,
        "-i", str(src_video),
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
        raise RuntimeError(f"FFmpeg video scene error ({src_video.name}): {res.stderr[-500:]}")


def render_single_clip(
    item: Dict[str, Any],
    cache_dir: Path,
    settings: Dict[str, Any],
    motion: Optional[Dict[str, Any]],
    global_strength: float,
    ffmpeg_threads: Optional[int] = None,
) -> Dict[str, Any]:
    """Render (or reuse cached) clip for one timeline item."""
    cache_key = compute_clip_cache_key(item, settings, motion, global_strength)
    clip_path = cache_dir / f"clip_{cache_key}.mp4"
    tmp_path = cache_dir / f"tmp_{item['segment_id']}_{cache_key}.mp4"

    if clip_path.exists() and clip_path.stat().st_size > 1000:
        return {
            "segment_id": item["segment_id"],
            "clip_path": clip_path,
            "cache_key": cache_key,
            "cached": True,
        }

    width = int(settings.get("width", 1920))
    height = int(settings.get("height", 1080))
    fps = int(settings.get("fps", 30))
    bg = settings.get("background_fill", "blurred")
    fc = int(item["frame_count"])

    if item["scene_type"] == "missing" or not item.get("scene_path"):
        _render_missing_scene_clip(item["segment_id"], tmp_path, fc, width, height, fps, ffmpeg_threads=ffmpeg_threads)
    elif item["scene_type"] == "video":
        slowmo = item.get("slowmo_smooth") or settings.get("slowmo_smooth", "off")
        _render_video_scene_clip(
            src_video=Path(item["scene_path"]),
            out_clip=tmp_path,
            frame_count=fc,
            speed=float(item.get("speed", 1.0)),
            width=width,
            height=height,
            fps=fps,
            background_fill=bg,
            slowmo_smooth=slowmo,
            ffmpeg_threads=ffmpeg_threads,
        )
    else:
        render_image_motion_clip(
            src_image=Path(item["scene_path"]),
            out_clip=tmp_path,
            frame_count=fc,
            width=width,
            height=height,
            fps=fps,
            background_fill=bg,
            motion=motion,
            global_strength=global_strength,
            ffmpeg_threads=ffmpeg_threads,
        )

    os.replace(tmp_path, clip_path)
    return {
        "segment_id": item["segment_id"],
        "clip_path": clip_path,
        "cache_key": cache_key,
        "cached": False,
    }


def export_single_scene_with_audio(
    project_id: str,
    item: Dict[str, Any],
    audio_path: Path,
    settings: Dict[str, Any],
    motion: Optional[Dict[str, Any]] = None,
    global_strength: float = 15.0,
    projects_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Export one scene independently with its sliced audio clip (output/scene_<id>.mp4)."""
    pdir = get_project_dir(project_id, projects_root)
    cache_dir = pdir / "cache"
    out_dir = pdir / "output"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    prof = get_resource_profile(settings)
    threads = prof["ffmpeg_threads"] or None
    clip_info = render_single_clip(item, cache_dir, settings, motion, global_strength, ffmpeg_threads=threads)
    sid = int(item["segment_id"])
    scene_mp4 = out_dir / f"scene_{sid}.mp4"
    start = max(0.0, float(item.get("start", 0.0)))
    dur = max(0.05, float(item.get("duration", 1.0)))

    cmd = [
        "ffmpeg", "-y",
        *(["-threads", str(threads)] if threads else []),
        "-i", str(clip_info["clip_path"]),
        "-ss", f"{start:.3f}",
        "-t", f"{dur:.3f}",
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(scene_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"فشل تصدير المشهد #{sid}: {res.stderr[-400:]}")
    return {
        "ok": True,
        "segment_id": sid,
        "output_path": str(scene_mp4.resolve()),
        "url": f"/api/projects/{project_id}/output/scene_{sid}.mp4",
        "cached": clip_info["cached"],
    }


def export_timeline_video(
    project_id: str,
    audio_path: Path,
    timeline: Dict[str, Any],
    settings: Dict[str, Any],
    motions_data: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
    projects_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Stage 2: Render timeline clips with resource throttling (light/medium/fast),
    Pause/Resume + Cancel support, concat with -c copy, and mux untouched original audio.
    """
    t0 = time.monotonic()
    pdir = get_project_dir(project_id, projects_root)
    cache_dir = pdir / "cache"
    out_dir = pdir / "output"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    prof = get_resource_profile(settings)
    items = timeline["items"]
    motions_data = motions_data or {"global_strength": 15, "assignments": {}}
    assignments = motions_data.get("assignments") or {}
    global_strength = float(motions_data.get("global_strength", settings.get("default_motion_strength", 15)))

    clip_states: Dict[int, str] = {int(it["segment_id"]): "waiting" for it in items}
    clip_results: Dict[int, Dict[str, Any]] = {}
    lock = threading.Lock()
    total_clips = len(items)
    completed_count = 0
    cached_count = 0
    rendered_count = 0

    def emit(message: str, phase: str = "rendering", pct_override: Optional[float] = None) -> None:
        if not progress_callback:
            return
        elapsed = time.monotonic() - t0
        pct = pct_override if pct_override is not None else (completed_count / max(1, total_clips)) * 90.0
        eta = None
        if completed_count > 0 and completed_count < total_clips and pct_override is None:
            per_clip = elapsed / completed_count
            eta = round(per_clip * (total_clips - completed_count), 1)
        progress_callback({
            "phase": phase,
            "stage_num": 2,
            "resource_mode": prof["id"],
            "paused": bool(pause_event and pause_event.is_set()),
            "progress": round(pct, 1),
            "elapsed": round(elapsed, 1),
            "eta": eta,
            "completed": completed_count,
            "total": total_clips,
            "cached_count": cached_count,
            "rendered_count": rendered_count,
            "message": message,
            "clip_states": dict(clip_states),
        })

    emit(f"المرحلة 2: تصيير المشاهد ({prof['id']})...", phase="rendering", pct_override=1.0)

    workers = prof["workers"]
    threads = prof["ffmpeg_threads"] or None
    rest_delay = float(prof["rest_delay"])

    def _worker(it: Dict[str, Any]) -> Dict[str, Any]:
        _wait_if_paused(pause_event, cancel_event)
        sid = int(it["segment_id"])
        with lock:
            clip_states[sid] = "rendering"
        emit(f"جاري تركيب وتصيير المشهد #{sid}...")
        motion = assignments.get(str(sid)) if it["scene_type"] == "image" else None
        res = render_single_clip(it, cache_dir, settings, motion, global_strength, ffmpeg_threads=threads)
        if not res["cached"] and rest_delay > 0:
            time.sleep(rest_delay)
        return res

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, it): it for it in items}
        for fut in as_completed(futures):
            _wait_if_paused(pause_event, cancel_event)
            res = fut.result()
            sid = int(res["segment_id"])
            with lock:
                clip_results[sid] = res
                clip_states[sid] = "done"
                completed_count += 1
                if res["cached"]:
                    cached_count += 1
                else:
                    rendered_count += 1
            emit(f"اكتمل المشهد #{sid} ({completed_count}/{total_clips})")

    _wait_if_paused(pause_event, cancel_event)
    emit("جاري دمج جميع المقاطع بدون إعادة ترميز...", phase="concat", pct_override=92.0)
    concat_txt = out_dir / "concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for it in items:
            sid = int(it["segment_id"])
            cpath = clip_results[sid]["clip_path"].resolve()
            escaped = str(cpath).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    concat_mp4 = out_dir / "video_only.mp4"
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_txt),
        "-c", "copy",
        str(concat_mp4),
    ]
    res_concat = subprocess.run(concat_cmd, capture_output=True, text=True)
    if res_concat.returncode != 0:
        raise RuntimeError(f"فشل دمج المقاطع: {res_concat.stderr[-500:]}")

    _wait_if_paused(pause_event, cancel_event)
    emit("جاري إضافة الصوت الأصلي وتأكيد التطابق الزمني...", phase="mux", pct_override=96.0)
    final_mp4 = out_dir / "final.mp4"
    mux_cmd = [
        "ffmpeg", "-y",
        *(["-threads", str(threads)] if threads else []),
        "-i", str(concat_mp4),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(final_mp4),
    ]
    res_mux = subprocess.run(mux_cmd, capture_output=True, text=True)
    if res_mux.returncode != 0:
        raise RuntimeError(f"فشل دمج الصوت مع الفيديو: {res_mux.stderr[-500:]}")

    try:
        concat_mp4.unlink(missing_ok=True)
    except Exception:
        pass

    final_stats = probe_video_stats(final_mp4)
    audio_dur = round(probe_media_duration(audio_path), 3)
    video_dur = round(final_stats["duration"], 3)
    export_time = round(time.monotonic() - t0, 2)

    report = {
        "output_path": str(final_mp4.resolve()),
        "output_dir": str(out_dir.resolve()),
        "resource_mode": prof["id"],
        "total_segments": len(items),
        "missing_scenes": timeline.get("missing_scenes", []),
        "extreme_speed_videos": timeline.get("extreme_speed_videos", []),
        "low_confidence_cuts": [
            {"segment_id": it["segment_id"], "confidence": it["confidence"]}
            for it in items if float(it.get("confidence", 100.0)) < 68.0
        ],
        "audio_duration": audio_dur,
        "video_duration": video_dur,
        "duration_diff": round(abs(video_dur - audio_dur), 3),
        "total_frames": final_stats.get("frames", timeline["total_frames"]),
        "expected_frames": timeline["total_frames"],
        "cached_clips": cached_count,
        "rendered_clips": rendered_count,
        "export_time_seconds": export_time,
    }
    emit("تم التصدير بنجاح!", phase="done", pct_override=100.0)
    return report
