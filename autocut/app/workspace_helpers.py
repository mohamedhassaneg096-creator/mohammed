"""Workspace helpers for companion file detection, quick-folder import, and self-contained demo project creation."""

from __future__ import annotations

import math
import re
import shutil
import struct
import wave
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    Image = None
    ImageDraw = None
    HAS_PIL = False

from .matching import match_segments_to_transcript, parse_numbered_segments
from .project import (
    create_project,
    file_sha256,
    get_project_dir,
    load_project,
    logger,
    save_cuts,
    save_motions,
    save_project,
    save_transcript,
)
from .scenes import SUPPORTED_EXTS, scan_scenes_folder
from .transcribe import probe_media_duration
from .transcript_import import TranscriptImportError, parse_and_normalize_transcript

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac", ".wma"}
TRANSCRIPT_EXTS = {".json", ".srt", ".vtt"}
SCRIPT_EXTS = {".txt"}


def find_companion_files(audio_path: Path) -> Dict[str, Any]:
    """
    Look in audio_path.parent for same-stem transcript (.json/.srt/.vtt) and numbered script (.txt or script.txt).
    Returns dict with optional `transcript_path`, `transcript_name`, `script_path`, `script_name`.
    """
    out: Dict[str, Any] = {
        "transcript_path": None,
        "transcript_name": None,
        "script_path": None,
        "script_name": None,
    }
    if not audio_path or not audio_path.parent.exists():
        return out

    parent = audio_path.parent
    stem = audio_path.stem.lower()

    # Transcript candidates: prefer exact stem match in order json > srt > vtt
    for ext in (".json", ".srt", ".vtt"):
        cand = parent / f"{audio_path.stem}{ext}"
        if cand.exists() and cand.is_file():
            out["transcript_path"] = str(cand.resolve())
            out["transcript_name"] = cand.name
            break
    if not out["transcript_path"]:
        for p in sorted(parent.iterdir()):
            if p.is_file() and p.suffix.lower() in TRANSCRIPT_EXTS and p.stem.lower() == stem:
                out["transcript_path"] = str(p.resolve())
                out["transcript_name"] = p.name
                break

    # Numbered script candidates: <stem>.txt or script.txt
    for cname in (f"{audio_path.stem}.txt", "script.txt", "النص.txt"):
        cand = parent / cname
        if cand.exists() and cand.is_file():
            out["script_path"] = str(cand.resolve())
            out["script_name"] = cand.name
            break

    return out


def _probe_wav_fallback(audio_path: Path) -> float:
    try:
        return probe_media_duration(audio_path)
    except Exception:
        if audio_path.suffix.lower() == ".wav":
            try:
                with wave.open(str(audio_path), "rb") as wf:
                    return wf.getnframes() / float(wf.getframerate() or 16000)
            except Exception:
                pass
        raise


def apply_quick_folder(
    project_id: str,
    folder_path: Optional[Path] = None,
    uploaded_files: Optional[List[Tuple[str, bytes]]] = None,
    projects_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Auto-detect and populate Audio, Transcript, Numbered Script (.txt), and Scenes
    from either a local folder path OR a list of (relative_path, file_bytes) uploads.
    """
    meta = load_project(project_id, projects_root=projects_root)
    pdir = get_project_dir(project_id, projects_root=projects_root)

    candidates: List[Tuple[str, Optional[Path], Optional[bytes]]] = []
    if folder_path is not None:
        fpath = Path(folder_path)
        if not fpath.exists() or not fpath.is_dir():
            return {"ok": False, "error": "المجلد المحدد غير موجود."}
        for p in sorted(fpath.rglob("*")):
            if p.is_file():
                rel = p.relative_to(fpath).as_posix()
                # Skip hidden / project internals
                if any(part.startswith(".") or part in ("output", "cache", "thumbnails", "previews") for part in Path(rel).parts):
                    continue
                candidates.append((rel, p, None))
    elif uploaded_files is not None:
        for rel, data in uploaded_files:
            norm_rel = rel.replace("\\", "/").lstrip("/")
            if norm_rel:
                candidates.append((norm_rel, None, data))
    else:
        return {"ok": False, "error": "لم يتم تحديد مجلد أو ملفات."}

    def _read_bytes(item: Tuple[str, Optional[Path], Optional[bytes]]) -> bytes:
        _, p, b = item
        if b is not None:
            return b
        assert p is not None
        return p.read_bytes()

    # 1. Pick audio (prefer root-level or shallowest audio file)
    audio_items = [c for c in candidates if Path(c[0]).suffix.lower() in AUDIO_EXTS]
    audio_items.sort(key=lambda c: (len(Path(c[0]).parts), c[0].lower()))

    # 2. Pick scenes: prefer files inside a 'scenes' folder, otherwise numbered image/video files outside audio
    scene_folder_items = [
        c for c in candidates
        if Path(c[0]).suffix.lower() in SUPPORTED_EXTS
        and any(part.lower() in ("scenes", "المشاهد", "visuals", "media") for part in Path(c[0]).parts[:-1])
    ]
    if not scene_folder_items:
        scene_folder_items = [
            c for c in candidates
            if Path(c[0]).suffix.lower() in SUPPORTED_EXTS
            and (not audio_items or c[0] != audio_items[0][0])
        ]

    # 3. Pick transcript (.json > .srt > .vtt), excluding metadata files like project.json/cuts.json
    excluded_json = {"project.json", "cuts.json", "motions.json", "settings.json", "update_manifest.json", "package.json"}
    transcript_items = [
        c for c in candidates
        if Path(c[0]).suffix.lower() in TRANSCRIPT_EXTS
        and Path(c[0]).name.lower() not in excluded_json
    ]
    ext_prio = {".json": 0, ".srt": 1, ".vtt": 2}
    audio_stem = Path(audio_items[0][0]).stem.lower() if audio_items else ""
    transcript_items.sort(
        key=lambda c: (
            0 if Path(c[0]).stem.lower() == audio_stem and audio_stem else 1,
            len(Path(c[0]).parts),
            ext_prio.get(Path(c[0]).suffix.lower(), 9),
            c[0].lower(),
        )
    )

    # 4. Pick script (.txt), preferring script.txt or audio stem
    script_items = [
        c for c in candidates
        if Path(c[0]).suffix.lower() in SCRIPT_EXTS
        and len(Path(c[0]).parts) <= 2
    ]
    script_items.sort(
        key=lambda c: (
            0 if Path(c[0]).name.lower() in ("script.txt", f"{audio_stem}.txt") else 1,
            len(Path(c[0]).parts),
            c[0].lower(),
        )
    )

    summary: Dict[str, Any] = {
        "audio": None,
        "transcript": None,
        "script": None,
        "scenes": None,
        "warnings": [],
    }

    # Apply audio
    audio_duration = float(meta.get("audio_duration") or 0.0)
    if audio_items:
        rel, src_p, src_b = audio_items[0]
        audio_dir = pdir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        for old in audio_dir.iterdir():
            if old.is_file():
                old.unlink()
        dest = audio_dir / Path(rel).name
        dest.write_bytes(_read_bytes((rel, src_p, src_b)))
        try:
            audio_duration = round(_probe_wav_fallback(dest), 3)
            meta = save_project(
                project_id,
                {
                    "audio_path": str(dest.resolve()),
                    "audio_filename": dest.name,
                    "audio_hash": file_sha256(dest),
                    "audio_duration": audio_duration,
                },
                projects_root=projects_root,
            )
            summary["audio"] = {"filename": dest.name, "duration": audio_duration}
        except Exception as exc:
            summary["warnings"].append(f"تعذر قراءة ملف الصوت ({dest.name}): {exc}")

    # Apply script
    raw_text = meta.get("raw_text") or ""
    if script_items:
        rel, src_p, src_b = script_items[0]
        try:
            raw_text = _read_bytes((rel, src_p, src_b)).decode("utf-8-sig")
            parsed_script = parse_numbered_segments(raw_text)
            meta = save_project(project_id, {"raw_text": raw_text}, projects_root=projects_root)
            summary["script"] = {
                "filename": Path(rel).name,
                "segment_count": len(parsed_script["segments"]),
                "warnings": parsed_script.get("warnings", []),
            }
        except Exception as exc:
            summary["warnings"].append(f"تعذر قراءة ملف النص ({Path(rel).name}): {exc}")

    # Apply transcript
    transcript_obj = None
    if transcript_items:
        rel, src_p, src_b = transcript_items[0]
        try:
            tr_bytes = _read_bytes((rel, src_p, src_b))
            transcript_obj = parse_and_normalize_transcript(
                tr_bytes,
                audio_duration=audio_duration,
                filename=Path(rel).name,
            )
            save_transcript(project_id, transcript_obj, projects_root=projects_root)
            meta = save_project(
                project_id,
                {
                    "transcript_filename": transcript_obj["filename"],
                    "transcript_precision": transcript_obj["precision"],
                },
                projects_root=projects_root,
            )
            summary["transcript"] = {
                "filename": transcript_obj["filename"],
                "format_label": transcript_obj["format_label"],
                "precision": transcript_obj["precision"],
                "word_count": transcript_obj["stats"]["word_count"],
                "suspicious_count": transcript_obj["stats"]["suspicious_count"],
            }
        except TranscriptImportError as exc:
            summary["warnings"].append(f"تفريغ ({Path(rel).name}): {exc.message}")
        except Exception as exc:
            summary["warnings"].append(f"تعذر استيراد التفريغ ({Path(rel).name}): {exc}")

    # Apply scenes
    if scene_folder_items:
        parsed_seg_ids = [s["id"] for s in parse_numbered_segments(raw_text)["segments"]]
        # If local folder has a dedicated scenes directory, scan it directly; otherwise copy into uploaded_scenes
        local_scene_dir: Optional[Path] = None
        if folder_path is not None and scene_folder_items[0][1] is not None:
            parents = {c[1].parent.resolve() for c in scene_folder_items if c[1] is not None}
            if len(parents) == 1:
                local_scene_dir = next(iter(parents))

        if local_scene_dir is None:
            up_dir = pdir / "uploaded_scenes"
            if up_dir.exists():
                shutil.rmtree(up_dir)
            up_dir.mkdir(parents=True, exist_ok=True)
            for rel, src_p, src_b in scene_folder_items:
                dest = up_dir / Path(rel).name
                dest.write_bytes(_read_bytes((rel, src_p, src_b)))
            target_scenes_dir = up_dir
        else:
            target_scenes_dir = local_scene_dir

        scan = scan_scenes_folder(
            project_id,
            target_scenes_dir,
            segment_ids=parsed_seg_ids,
            generate_thumbs=True,
            projects_root=projects_root,
        )
        meta = save_project(
            project_id,
            {"scenes_dir": str(target_scenes_dir.resolve()), "scenes_scan": scan},
            projects_root=projects_root,
        )
        summary["scenes"] = {
            "count": len(scan.get("scenes") or {}),
            "missing_numbers": scan.get("missing_numbers") or [],
            "extra_numbers": scan.get("extra_numbers") or [],
            "ok": scan.get("ok", False),
        }

    if not any([summary["audio"], summary["transcript"], summary["script"], summary["scenes"]]):
        return {
            "ok": False,
            "error": "لم يتم العثور على ملفات صوت أو تفريغ أو نص أو مشاهد مدعومة داخل المجلد.",
            "summary": summary,
        }

    meta = load_project(project_id, projects_root=projects_root)
    return {
        "ok": True,
        "project": meta,
        "transcript": transcript_obj,
        "summary": summary,
    }


def _write_demo_wav(out_wav: Path, duration_s: float = 8.0, sample_rate: int = 16000) -> Path:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_s * sample_rate)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            t = i / sample_rate
            # Gentle modulated tone so waveform shows realistic speech-like amplitude blocks
            env = 0.25 + 0.65 * abs(math.sin(2.0 * math.pi * 0.62 * t))
            val = int(14000 * env * math.sin(2.0 * math.pi * 330.0 * t))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(bytes(frames))
    return out_wav


def _write_demo_png(out_png: Path, num: int, bg_rgb: Tuple[int, int, int], label: str) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    w, h = 1280, 720
    if HAS_PIL and Image is not None and ImageDraw is not None:
        img = Image.new("RGB", (w, h), bg_rgb)
        draw = ImageDraw.Draw(img)
        draw.rectangle([48, 48, w - 48, h - 48], outline=(232, 237, 243), width=4)
        draw.rectangle([140, 140, 140 + 140 + num * 50, 320], fill=(255, 255, 255))
        draw.rectangle([w - 380, h - 220, w - 100, h - 90], outline=(108, 155, 255), width=6)
        draw.text((160, 205), f"SCENE #{num} - {label}", fill=(15, 18, 24))
        img.save(out_png, format="PNG")
        return out_png

    # Pure stdlib PNG generator fallback (crisp geometric scene card)
    r0, g0, b0 = bg_rgb
    bar_w = min(w - 200, 140 + num * 50)
    rows = bytearray()
    for y in range(h):
        rows.append(0)  # filter byte 0
        if 48 <= y <= 52 or (h - 52) <= y <= (h - 48):
            row = bytes([232, 237, 243]) * w
        elif 140 <= y <= 320:
            row = (
                bytes([r0, g0, b0]) * 140
                + bytes([255, 255, 255]) * bar_w
                + bytes([r0, g0, b0]) * (w - 140 - bar_w)
            )
        elif (h - 220) <= y <= (h - 90):
            row = (
                bytes([r0, g0, b0]) * (w - 380)
                + bytes([108, 155, 255]) * 280
                + bytes([r0, g0, b0]) * 100
            )
        else:
            row = bytes([r0, g0, b0]) * w
        rows.extend(row)

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(rows), level=1)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )
    out_png.write_bytes(png_bytes)
    return out_png


def create_demo_project(projects_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Create a self-contained synthetic Arabic demo project (WAV + numbered PNGs + script + word-level JSON)
    so users can test the entire workflow in one click without preparing any external files.
    """
    proj = create_project("مشروع تجريبي — الأندلس", projects_root=projects_root)
    pid = proj["id"]
    pdir = get_project_dir(pid, projects_root=projects_root)

    audio_path = _write_demo_wav(pdir / "audio" / "andalus_demo.wav", duration_s=8.0)

    scenes_dir = pdir / "demo_scenes"
    palette = [
        (36, 62, 107, "Andalus"),
        (28, 86, 78, "Cordoba"),
        (78, 52, 108, "Alhambra"),
        (110, 76, 38, "Libraries"),
        (42, 74, 96, "Legacy"),
    ]
    for idx, (r, g, b, lbl) in enumerate(palette, start=1):
        _write_demo_png(scenes_dir / f"{idx}.png", idx, (r, g, b), lbl)

    raw_text = (
        "1. في قلب الأندلس بدأت قصة حضارة مضيئة\n"
        "2. كانت قرطبة مركزاً للعلم والعمارة والفنون\n"
        "3. وتدفقت المياه في حدائق قصر الحمراء\n"
        "4. وازدهرت المكتبات والمدارس في كل مدينة\n"
        "5. وبقي أثر تلك الحقبة خالداً حتى يومنا هذا"
    )

    demo_words = [
        {"word": "في", "start": 0.0, "end": 0.35},
        {"word": "قلب", "start": 0.35, "end": 0.8},
        {"word": "الأندلس", "start": 0.8, "end": 1.35},
        {"word": "بدأت", "start": 1.35, "end": 1.6},
        {"word": "كانت", "start": 1.6, "end": 2.0},
        {"word": "قرطبة", "start": 2.0, "end": 2.5},
        {"word": "مركزاً", "start": 2.5, "end": 3.0},
        {"word": "للعلوم", "start": 3.0, "end": 3.45},
        {"word": "وتدفقت", "start": 3.45, "end": 4.0},
        {"word": "المياه", "start": 4.0, "end": 4.5},
        {"word": "في", "start": 4.5, "end": 4.8},
        {"word": "حدائق", "start": 4.8, "end": 5.3},
        {"word": "وازدهرت", "start": 5.3, "end": 5.85},
        {"word": "المكتبات", "start": 5.85, "end": 6.45},
        {"word": "والمدارس", "start": 6.45, "end": 6.9},
        {"word": "وبقي", "start": 6.9, "end": 7.3},
        {"word": "أثر", "start": 7.3, "end": 7.65},
        {"word": "تلك", "start": 7.65, "end": 8.0},
    ]
    import json
    raw_json_str = json.dumps({"words": demo_words}, ensure_ascii=False)
    transcript = parse_and_normalize_transcript(raw_json_str, audio_duration=8.0, filename="andalus_demo.json")
    save_transcript(pid, transcript, projects_root=projects_root)

    parsed = parse_numbered_segments(raw_text)
    scan = scan_scenes_folder(
        pid,
        scenes_dir,
        segment_ids=[s["id"] for s in parsed["segments"]],
        generate_thumbs=True,
        projects_root=projects_root,
    )
    cuts = match_segments_to_transcript(parsed["segments"], transcript, audio_duration=8.0)
    save_cuts(pid, cuts, projects_root=projects_root)
    save_motions(
        pid,
        {
            "global_strength": 15,
            "assignments": {
                "1": {"type": "zoom_in", "x": 0.42, "y": 0.45, "strength": 15, "ease": True},
            },
        },
        projects_root=projects_root,
    )

    meta = save_project(
        pid,
        {
            "audio_path": str(audio_path.resolve()),
            "audio_filename": audio_path.name,
            "audio_hash": file_sha256(audio_path),
            "audio_duration": 8.0,
            "transcript_filename": "andalus_demo.json",
            "transcript_precision": "word",
            "scenes_dir": str(scenes_dir.resolve()),
            "raw_text": raw_text,
            "scenes_scan": scan,
            "stage": "inputs",
        },
        projects_root=projects_root,
    )
    return meta
