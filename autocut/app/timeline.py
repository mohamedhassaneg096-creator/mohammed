"""Frame-accurate timeline construction, invariant verification, and video speed calculations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class TimelineInvariantError(AssertionError):
    """Raised if timeline frame boundaries contain any gap, overlap, or audio duration mismatch."""


def build_timeline(
    cuts: Dict[str, Any],
    scenes_map: Dict[str, Dict[str, Any]],
    fps: int = 30,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a gapless, frame-accurate timeline using cumulative rounding:
      frame_start[k] = round(start[k] * fps)
      frame_end[k]   = frame_start[k+1]
      frame_end[-1]  = round(audio_duration * fps)
    Videos NEVER trim/loop/freeze: speed = video_duration / segment_duration.
    """
    segments = cuts.get("segments") or []
    audio_duration = float(cuts.get("audio_duration") or 0.0)
    overrides = overrides or {}

    if not segments:
        raise TimelineInvariantError("لا توجد مقاطع لبناء الخط الزمني.")
    if fps <= 0:
        raise TimelineInvariantError(f"معدل الإطارات غير صالح: {fps}")

    total_frames = max(len(segments), int(round(audio_duration * fps)))

    # Compute cumulative start frames cleanly while ensuring >= 1 frame per segment
    raw_starts = [0]
    for i in range(1, len(segments)):
        f_start = int(round(float(segments[i]["start"]) * fps))
        min_f = raw_starts[-1] + 1
        max_f = total_frames - (len(segments) - i)
        f_start = max(min_f, min(max_f, f_start))
        raw_starts.append(f_start)

    items: List[Dict[str, Any]] = []
    missing_scenes: List[int] = []
    extreme_speed_videos: List[Dict[str, Any]] = []

    for idx, seg in enumerate(segments):
        seg_id = int(seg["id"])
        f_start = raw_starts[idx]
        f_end = raw_starts[idx + 1] if idx + 1 < len(segments) else total_frames
        f_count = f_end - f_start
        seg_dur = f_count / float(fps)

        seg_override = overrides.get(str(seg_id)) or {}
        scene = seg_override.get("scene") or scenes_map.get(str(seg_id))

        speed = 1.0
        speed_badge = None
        extreme_speed = False
        scene_type = "missing"
        scene_path = None
        scene_filename = None
        thumbnail_url = None
        video_duration = 0.0

        if scene:
            scene_type = scene.get("type", "image")
            scene_path = scene.get("path")
            scene_filename = scene.get("filename")
            thumbnail_url = scene.get("thumbnail_url")

            if scene_type == "video":
                video_duration = float(scene.get("duration") or 0.0)
                if video_duration > 0 and seg_dur > 0:
                    speed = round(video_duration / seg_dur, 4)
                    if speed < 0.4:
                        extreme_speed = True
                        speed_badge = f"سرعة بطيئة جداً: {speed:.2f}x"
                    elif speed > 3.0:
                        extreme_speed = True
                        speed_badge = f"سرعة عالية جداً: {speed:.2f}x"
                    if extreme_speed:
                        extreme_speed_videos.append({
                            "segment_id": seg_id,
                            "filename": scene_filename,
                            "speed": speed,
                            "badge": speed_badge,
                            "video_duration": round(video_duration, 2),
                            "segment_duration": round(seg_dur, 2),
                        })
        else:
            missing_scenes.append(seg_id)

        items.append({
            "segment_id": seg_id,
            "text": seg.get("text", ""),
            "matched_text": seg.get("matched_text", ""),
            "confidence": seg.get("confidence", 100.0),
            "start": round(f_start / float(fps), 4),
            "end": round(f_end / float(fps), 4),
            "duration": round(seg_dur, 4),
            "frame_start": f_start,
            "frame_end": f_end,
            "frame_count": f_count,
            "scene_type": scene_type,
            "scene_path": scene_path,
            "scene_filename": scene_filename,
            "scene_sha256": scene.get("sha256") if scene else None,
            "thumbnail_url": thumbnail_url,
            "video_duration": video_duration,
            "speed": speed,
            "extreme_speed": extreme_speed,
            "speed_badge": speed_badge,
            "slowmo_smooth": seg_override.get("slowmo_smooth"),
        })

    verify_timeline_invariants(items, total_frames / float(fps), fps)

    return {
        "fps": fps,
        "audio_duration": round(audio_duration, 4),
        "total_frames": total_frames,
        "video_duration": round(total_frames / float(fps), 4),
        "items": items,
        "missing_scenes": missing_scenes,
        "extreme_speed_videos": extreme_speed_videos,
    }


def verify_timeline_invariants(items: List[Dict[str, Any]], expected_duration: float, fps: int) -> None:
    """
    Automated assertion (Section 4):
    - end_frame[k] == start_frame[k+1] for all k
    - start_frame[0] == 0
    - sum(frame_count) == total_frames == round(expected_duration * fps)
    Fails loudly with TimelineInvariantError if violated.
    """
    if not items:
        raise TimelineInvariantError("Timeline invariant failed: empty items list.")

    expected_total_frames = int(round(expected_duration * fps))
    if items[0]["frame_start"] != 0:
        raise TimelineInvariantError(
            f"Timeline invariant failed: first frame_start is {items[0]['frame_start']}, expected 0."
        )

    accumulated = 0
    for k, item in enumerate(items):
        fc = item["frame_end"] - item["frame_start"]
        if fc <= 0 or fc != item["frame_count"]:
            raise TimelineInvariantError(
                f"Timeline invariant failed at segment {item['segment_id']}: invalid frame_count={item['frame_count']}."
            )
        accumulated += fc
        if k + 1 < len(items):
            next_start = items[k + 1]["frame_start"]
            if item["frame_end"] != next_start:
                raise TimelineInvariantError(
                    f"Timeline invariant failed between segment {item['segment_id']} and {items[k + 1]['segment_id']}: "
                    f"end_frame[{k}]={item['frame_end']} != start_frame[{k+1}]={next_start}."
                )

    if items[-1]["frame_end"] != expected_total_frames or accumulated != expected_total_frames:
        raise TimelineInvariantError(
            f"Timeline invariant failed: total frames {accumulated} (last end={items[-1]['frame_end']}) "
            f"!= expected {expected_total_frames}."
        )
