"""FastAPI server for AutoCut (127.0.0.1 only, offline Arabic RTL web UI + local pipeline APIs)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .matching import match_segments_to_transcript, parse_numbered_segments, update_cut_start_time
from .motion import render_image_motion_clip
import sys
import time
from .project import (
    BASE_DIR,
    LOG_FILE,
    _atomic_write_json,
    _read_json,
    create_project,
    delete_project,
    file_sha256,
    get_project_dir,
    list_projects,
    load_cuts,
    load_motions,
    load_project,
    load_transcript,
    logger,
    save_cuts,
    save_motions,
    save_project,
)
from .render import export_timeline_video
from .scenes import SUPPORTED_EXTS, scan_scenes_folder
from .settings_store import (
    build_problem_report,
    get_app_version,
    get_public_global_settings,
    load_global_settings,
    redact_secrets,
    save_global_settings,
)
from .timeline import TimelineInvariantError, build_timeline
from .transcribe import probe_media_duration, transcribe_audio
from .updater import (
    UpdaterError,
    check_github_version_only,
    plan_github_update,
    stage_github_update,
    stage_local_update,
    test_github_connection,
)
from .updater_helper import list_backups

WEB_DIR = BASE_DIR / "web"

app = FastAPI(title="AutoCut Local Server", docs_url=None, redoc_url=None)

# Background job registry
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_EVENTS: Dict[str, threading.Event] = {}
_JOBS_LOCK = threading.Lock()


def check_binaries() -> Dict[str, Any]:
    ffmpeg_ok = False
    ffprobe_ok = False
    ffmpeg_ver = ""
    try:
        res = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        if res.returncode == 0:
            ffmpeg_ok = True
            ffmpeg_ver = (res.stdout.splitlines()[0] if res.stdout else "").strip()
    except FileNotFoundError:
        pass
    try:
        res = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True)
        ffprobe_ok = res.returncode == 0
    except FileNotFoundError:
        pass

    try:
        import faster_whisper  # noqa: F401
        whisper_ok = True
    except ImportError:
        whisper_ok = False

    try:
        import rapidfuzz  # noqa: F401
        rapidfuzz_ok = True
    except ImportError:
        rapidfuzz_ok = False

    return {
        "ok": ffmpeg_ok and ffprobe_ok,
        "version": get_app_version(),
        "ffmpeg_ok": ffmpeg_ok,
        "ffprobe_ok": ffprobe_ok,
        "ffmpeg_version": ffmpeg_ver,
        "faster_whisper_ok": whisper_ok,
        "rapidfuzz_ok": rapidfuzz_ok,
        "log_path": str(LOG_FILE),
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    safe_err = redact_secrets(str(exc))
    logger.error("Unhandled error on %s %s: %s", request.method, request.url.path, safe_err)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "حدث خطأ داخلي غير متوقع أثناء تنفيذ العملية.",
            "hint": "راجع ملف السجل (logs/app.log) لمعرفة التفاصيل التقنية.",
        },
    )


def _update_job(job_id: str, **updates: Any) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(updates)


@app.get("/api/health")
def api_health() -> Dict[str, Any]:
    data = check_binaries()
    last_res = _read_json(BASE_DIR / "staging" / "last_update_result.json")
    data["last_update_result"] = last_res
    return data


@app.get("/api/projects")
def api_list_projects() -> Dict[str, Any]:
    return {"ok": True, "projects": list_projects()}


@app.post("/api/projects")
async def api_create_project(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = str(payload.get("name") or "").strip() or "مشروع جديد"
    meta = create_project(name)
    return {"ok": True, "project": meta}


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: str) -> Dict[str, Any]:
    try:
        meta = load_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="المشروع غير موجود.")
    cuts = load_cuts(project_id)
    motions = load_motions(project_id)
    transcript = load_transcript(project_id)
    timeline = None
    if cuts and meta.get("scenes_scan"):
        try:
            scenes_map = (meta["scenes_scan"] or {}).get("scenes") or {}
            timeline = build_timeline(
                cuts,
                scenes_map,
                fps=int(meta["settings"].get("fps", 30)),
                overrides=meta.get("overrides") or {},
            )
        except TimelineInvariantError as exc:
            logger.warning("Timeline build note on load: %s", exc)

    pdir = get_project_dir(project_id)
    has_output = (pdir / "output" / "final.mp4").exists()
    return {
        "ok": True,
        "project": meta,
        "cuts": cuts,
        "timeline": timeline,
        "motions": motions,
        "has_transcript": transcript is not None,
        "has_output": has_output,
    }


@app.patch("/api/projects/{project_id}")
async def api_update_project(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"name", "raw_text", "scenes_dir", "settings", "stage", "overrides"}
    updates = {k: v for k, v in payload.items() if k in allowed}
    try:
        meta = save_project(project_id, updates)
        return {"ok": True, "project": meta}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="المشروع غير موجود.")


@app.delete("/api/projects/{project_id}")
def api_delete_project(project_id: str) -> Dict[str, Any]:
    delete_project(project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/audio")
async def api_upload_audio(project_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        meta = load_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="المشروع غير موجود.")

    pdir = get_project_dir(project_id)
    audio_dir = pdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    # Clean previous audio files in project/audio
    for old in audio_dir.iterdir():
        if old.is_file():
            old.unlink()

    filename = Path(file.filename or "narration.mp3").name
    dest = audio_dir / filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        duration = probe_media_duration(dest)
    except Exception as exc:
        logger.exception("Audio ffprobe failed: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "تعذر قراءة ملف الصوت. تأكد من أن الملف سليم وأن FFmpeg مثبت.",
                "hint": "FFmpeg غير مثبت أو الملف تالف — شغّل install.bat.",
            },
        )

    ahash = file_sha256(dest)
    meta = save_project(project_id, {
        "audio_path": str(dest.resolve()),
        "audio_filename": filename,
        "audio_hash": ahash,
        "audio_duration": round(duration, 3),
    })
    return {"ok": True, "project": meta}


@app.get("/api/projects/{project_id}/audio")
def api_stream_audio(project_id: str):
    meta = load_project(project_id)
    ap = meta.get("audio_path")
    if not ap or not Path(ap).exists():
        raise HTTPException(status_code=404, detail="ملف الصوت غير موجود.")
    return FileResponse(ap, filename=meta.get("audio_filename") or "audio")


@app.post("/api/projects/{project_id}/parse-text")
async def api_parse_text(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_text = str(payload.get("raw_text") or "")
    save_project(project_id, {"raw_text": raw_text})
    parsed = parse_numbered_segments(raw_text)
    return {"ok": True, **parsed}


@app.post("/api/projects/{project_id}/scan-scenes")
async def api_scan_scenes(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = load_project(project_id)
    scenes_dir = str(payload.get("scenes_dir") or meta.get("scenes_dir") or "").strip()
    raw_text = str(payload.get("raw_text") if payload.get("raw_text") is not None else meta.get("raw_text") or "")
    parsed = parse_numbered_segments(raw_text)
    seg_ids = [s["id"] for s in parsed["segments"]]

    scan = scan_scenes_folder(project_id, scenes_dir, segment_ids=seg_ids, generate_thumbs=True)
    save_project(project_id, {"scenes_dir": scenes_dir, "raw_text": raw_text, "scenes_scan": scan})
    return scan


@app.post("/api/projects/{project_id}/upload-scenes")
async def api_upload_scenes(project_id: str, files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    meta = load_project(project_id)
    pdir = get_project_dir(project_id)
    up_dir = pdir / "uploaded_scenes"
    if up_dir.exists():
        shutil.rmtree(up_dir)
    up_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    for uf in files:
        fname = Path(uf.filename or "").name
        if not fname or Path(fname).suffix.lower() not in SUPPORTED_EXTS:
            continue
        dest = up_dir / fname
        with open(dest, "wb") as f:
            shutil.copyfileobj(uf.file, f)
        saved_count += 1

    if saved_count == 0:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "لم يتم العثور على صور أو مقاطع فيديو مدعومة في الملفات المحددة."},
        )

    parsed = parse_numbered_segments(meta.get("raw_text") or "")
    seg_ids = [s["id"] for s in parsed["segments"]]
    scan = scan_scenes_folder(project_id, up_dir, segment_ids=seg_ids, generate_thumbs=True)
    save_project(project_id, {"scenes_dir": str(up_dir.resolve()), "scenes_scan": scan})
    return {**scan, "scenes_dir": str(up_dir.resolve())}


@app.post("/api/projects/{project_id}/replace-scene/{seg_id}")
async def api_replace_scene(project_id: str, seg_id: int, file: UploadFile = File(...)) -> Dict[str, Any]:
    """Replace a single segment's scene file during post-export manual review (§10b)."""
    meta = load_project(project_id)
    pdir = get_project_dir(project_id)
    rep_dir = pdir / "replaced_scenes"
    rep_dir.mkdir(parents=True, exist_ok=True)
    orig_name = Path(file.filename or f"scene_{seg_id}.png").name
    ext = Path(orig_name).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(status_code=400, detail="صيغة الملف غير مدعومة.")

    dest = rep_dir / f"{seg_id}_{uuid.uuid4().hex[:6]}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    from .scenes import VIDEO_EXTS, generate_scene_thumbnail, probe_scene_media
    is_video = ext in VIDEO_EXTS
    info = probe_scene_media(dest, is_video)
    thumb_name = f"scene_{seg_id}_custom_{uuid.uuid4().hex[:4]}.jpg"
    generate_scene_thumbnail(dest, is_video, pdir / "thumbnails" / thumb_name, force=True)

    overrides = dict(meta.get("overrides") or {})
    seg_ov = dict(overrides.get(str(seg_id)) or {})
    seg_ov["scene"] = {
        "number": seg_id,
        "path": str(dest.resolve()),
        "filename": orig_name,
        "ext": ext,
        "type": "video" if is_video else "image",
        "width": info["width"],
        "height": info["height"],
        "duration": info["duration"],
        "sha256": file_sha256(dest),
        "thumbnail_url": f"/api/projects/{project_id}/thumbnails/{thumb_name}",
    }
    overrides[str(seg_id)] = seg_ov
    meta = save_project(project_id, {"overrides": overrides})

    cuts = load_cuts(project_id)
    scenes_map = (meta.get("scenes_scan") or {}).get("scenes") or {}
    timeline = build_timeline(cuts, scenes_map, fps=int(meta["settings"].get("fps", 30)), overrides=overrides)
    return {"ok": True, "project": meta, "timeline": timeline}


@app.post("/api/projects/{project_id}/transcribe-and-match")
async def api_start_transcribe_match(project_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    meta = load_project(project_id)
    audio_path = meta.get("audio_path")
    if not audio_path or not Path(audio_path).exists():
        return JSONResponse(status_code=400, content={"ok": False, "error": "يرجى اختيار ملف الصوت أولاً."})

    raw_text = payload.get("raw_text") if payload.get("raw_text") is not None else meta.get("raw_text", "")
    parsed = parse_numbered_segments(raw_text)
    if not parsed["segments"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "يرجى لصق النص المرقم (1..N) أولاً."})

    scenes_dir = payload.get("scenes_dir") if payload.get("scenes_dir") is not None else meta.get("scenes_dir", "")
    save_project(project_id, {"raw_text": raw_text, "scenes_dir": scenes_dir})

    job_id = uuid.uuid4().hex[:10]
    cancel_ev = threading.Event()
    with _JOBS_LOCK:
        _JOB_EVENTS[job_id] = cancel_ev
        _JOBS[job_id] = {
            "id": job_id,
            "type": "transcribe_match",
            "status": "running",
            "progress": 0.0,
            "elapsed": 0.0,
            "eta": None,
            "message": "جاري بدء التفريغ الصوتي...",
            "words_preview": "",
            "cuts": None,
            "timeline": None,
            "scenes_scan": None,
            "error": None,
        }

    force_retranscribe = bool(payload.get("force", False))
    auto_mode = bool(payload.get("auto_mode", False))

    def _run() -> None:
        try:
            settings = meta["settings"]
            def _on_prog(pct: float, elapsed: float, eta: Optional[float], words_preview: str) -> None:
                _update_job(
                    job_id,
                    progress=round(pct * 0.75, 1),
                    elapsed=elapsed,
                    eta=eta,
                    words_preview=words_preview,
                    message="جاري تفريغ الصوت واستخراج توقيت الكلمات...",
                )

            transcript = transcribe_audio(
                project_id=project_id,
                audio_path=Path(audio_path),
                model_name=settings.get("whisper_model", "medium"),
                language=settings.get("language", "auto"),
                force=force_retranscribe,
                progress_callback=_on_prog,
                cancel_event=cancel_ev,
            )

            _update_job(job_id, progress=82.0, message="جاري مطابقة المقاطع المرقمة وحساب نقاط القص...")
            cuts = match_segments_to_transcript(
                segments=parsed["segments"],
                transcript=transcript,
                audio_duration=float(meta.get("audio_duration") or transcript.get("duration") or 0.0),
            )
            save_cuts(project_id, cuts)

            _update_job(job_id, progress=90.0, cuts=cuts, message="جاري فحص المشاهد وتركيبها على الخط الزمني...")
            seg_ids = [s["id"] for s in parsed["segments"]]
            scan = meta.get("scenes_scan")
            if scenes_dir:
                scan = scan_scenes_folder(project_id, scenes_dir, segment_ids=seg_ids, generate_thumbs=True)
                save_project(project_id, {"scenes_scan": scan})

            timeline = None
            if scan and scan.get("scenes"):
                timeline = build_timeline(
                    cuts,
                    scan["scenes"],
                    fps=int(settings.get("fps", 30)),
                    overrides=meta.get("overrides") or {},
                )

            next_stage = "motion" if auto_mode else "cuts"
            save_project(project_id, {"stage": next_stage})
            _update_job(
                job_id,
                status="done",
                progress=100.0,
                cuts=cuts,
                scenes_scan=scan,
                timeline=timeline,
                message="اكتمل التفريغ والقص ومطابقة المشاهد!",
            )
        except InterruptedError as exc:
            _update_job(job_id, status="cancelled", error=str(exc), message="تم إلغاء العملية.")
        except Exception as exc:
            logger.exception("Transcribe/match job failed: %s", exc)
            _update_job(
                job_id,
                status="error",
                error=str(exc) if "faster-whisper" in str(exc) else "تعذر إتمام التفريغ الصوتي. راجع ملف السجل.",
                message="حدث خطأ أثناء التفريغ.",
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "job_id": job_id}


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str) -> Dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="المهمة غير موجودة.")
        return {"ok": True, "job": dict(job)}


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str) -> Dict[str, Any]:
    with _JOBS_LOCK:
        ev = _JOB_EVENTS.get(job_id)
        if ev:
            ev.set()
    return {"ok": True}


@app.put("/api/projects/{project_id}/cuts")
async def api_save_cuts(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = load_project(project_id)
    cuts = payload.get("cuts")
    if not cuts:
        raise HTTPException(status_code=400, detail="بيانات القص غير صالحة.")
    save_cuts(project_id, cuts)
    if payload.get("stage"):
        meta = save_project(project_id, {"stage": payload["stage"]})

    timeline = None
    scenes_map = (meta.get("scenes_scan") or {}).get("scenes") or {}
    if scenes_map or cuts.get("segments"):
        timeline = build_timeline(
            cuts,
            scenes_map,
            fps=int(meta["settings"].get("fps", 30)),
            overrides=meta.get("overrides") or {},
        )
    return {"ok": True, "cuts": cuts, "timeline": timeline}


@app.post("/api/projects/{project_id}/cuts/{seg_id}/start")
async def api_update_cut_start(project_id: str, seg_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = load_project(project_id)
    cuts = load_cuts(project_id)
    if not cuts:
        raise HTTPException(status_code=404, detail="لم يتم إنشاء نقاط القص بعد.")
    new_start = float(payload["start"])
    cuts = update_cut_start_time(cuts, seg_id, new_start)
    save_cuts(project_id, cuts)

    scenes_map = (meta.get("scenes_scan") or {}).get("scenes") or {}
    timeline = build_timeline(
        cuts,
        scenes_map,
        fps=int(meta["settings"].get("fps", 30)),
        overrides=meta.get("overrides") or {},
    )
    return {"ok": True, "cuts": cuts, "timeline": timeline}


@app.put("/api/projects/{project_id}/motions")
async def api_save_motions(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    motions = {
        "global_strength": float(payload.get("global_strength", 15)),
        "assignments": payload.get("assignments") or {},
    }
    save_motions(project_id, motions)
    save_project(project_id, {})  # Update updated_at timestamp
    return {"ok": True, "motions": motions}


@app.post("/api/projects/{project_id}/motions/preview")
async def api_preview_motion(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Render a fast 480p 2.5s preview MP4 for the selected image motion."""
    meta = load_project(project_id)
    seg_id = int(payload["segment_id"])
    motion = payload.get("motion")
    global_strength = float(payload.get("global_strength", 15))

    overrides = meta.get("overrides") or {}
    scenes_map = (meta.get("scenes_scan") or {}).get("scenes") or {}
    scene = (overrides.get(str(seg_id)) or {}).get("scene") or scenes_map.get(str(seg_id))
    if not scene or scene.get("type") != "image":
        raise HTTPException(status_code=404, detail="الصورة غير موجودة للمعاينة.")

    pdir = get_project_dir(project_id)
    preview_name = f"preview_{seg_id}_{uuid.uuid4().hex[:6]}.mp4"
    out_mp4 = pdir / "previews" / preview_name
    # Remove old previews for this segment
    for old in (pdir / "previews").glob(f"preview_{seg_id}_*.mp4"):
        old.unlink(missing_ok=True)

    render_image_motion_clip(
        src_image=Path(scene["path"]),
        out_clip=out_mp4,
        frame_count=60,  # 2.5s at 24fps -> ultra-fast 480p preview
        width=854,
        height=480,
        fps=24,
        background_fill=meta["settings"].get("background_fill", "blurred"),
        motion=motion,
        global_strength=global_strength,
    )
    return {
        "ok": True,
        "preview_url": f"/api/projects/{project_id}/previews/{preview_name}",
    }


@app.post("/api/projects/{project_id}/export")
async def api_start_export(project_id: str) -> Dict[str, Any]:
    meta = load_project(project_id)
    cuts = load_cuts(project_id)
    if not cuts:
        return JSONResponse(status_code=400, content={"ok": False, "error": "يرجى إتمام مرحلة التفريغ والقص أولاً."})

    audio_path = meta.get("audio_path")
    if not audio_path or not Path(audio_path).exists():
        return JSONResponse(status_code=400, content={"ok": False, "error": "ملف الصوت غير موجود."})

    scenes_map = (meta.get("scenes_scan") or {}).get("scenes") or {}
    timeline = build_timeline(
        cuts,
        scenes_map,
        fps=int(meta["settings"].get("fps", 30)),
        overrides=meta.get("overrides") or {},
    )
    motions = load_motions(project_id)

    job_id = uuid.uuid4().hex[:10]
    cancel_ev = threading.Event()
    with _JOBS_LOCK:
        _JOB_EVENTS[job_id] = cancel_ev
        _JOBS[job_id] = {
            "id": job_id,
            "type": "export",
            "status": "running",
            "progress": 0.0,
            "elapsed": 0.0,
            "eta": None,
            "message": "جاري بدء التصدير...",
            "clip_states": {int(it["segment_id"]): "waiting" for it in timeline["items"]},
            "report": None,
            "error": None,
        }

    def _run_export() -> None:
        try:
            def _on_prog(info: Dict[str, Any]) -> None:
                _update_job(job_id, **info)

            report = export_timeline_video(
                project_id=project_id,
                audio_path=Path(audio_path),
                timeline=timeline,
                settings=meta["settings"],
                motions_data=motions,
                progress_callback=_on_prog,
                cancel_event=cancel_ev,
            )
            save_project(project_id, {"stage": "export", "export_report": report})
            _update_job(job_id, status="done", progress=100.0, report=report, message="اكتمل التصدير بنجاح!")
        except InterruptedError as exc:
            _update_job(job_id, status="cancelled", error=str(exc), message="تم إلغاء التصدير.")
        except Exception as exc:
            logger.exception("Export failed: %s", exc)
            _update_job(
                job_id,
                status="error",
                error="حدث خطأ أثناء تصيير الفيديو. تأكد من تثبيت FFmpeg وراجع ملف السجل.",
                message="فشل التصدير.",
            )

    threading.Thread(target=_run_export, daemon=True).start()
    return {"ok": True, "job_id": job_id}


@app.get("/api/projects/{project_id}/output/final.mp4")
def api_get_output_video(project_id: str):
    pdir = get_project_dir(project_id)
    out = pdir / "output" / "final.mp4"
    if not out.exists():
        raise HTTPException(status_code=404, detail="الفيديو النهائي غير موجود بعد.")
    return FileResponse(out, media_type="video/mp4")


@app.get("/api/projects/{project_id}/thumbnails/{filename}")
def api_get_thumbnail(project_id: str, filename: str):
    pdir = get_project_dir(project_id)
    safe_name = Path(filename).name
    fpath = pdir / "thumbnails" / safe_name
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="الصورة المصغرة غير موجودة.")
    return FileResponse(fpath)


@app.get("/api/projects/{project_id}/previews/{filename}")
def api_get_preview(project_id: str, filename: str):
    pdir = get_project_dir(project_id)
    safe_name = Path(filename).name
    fpath = pdir / "previews" / safe_name
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="المعاينة غير موجودة.")
    return FileResponse(fpath, media_type="video/mp4")


@app.get("/api/projects/{project_id}/scene-file/{seg_id}")
def api_get_scene_file(project_id: str, seg_id: int):
    meta = load_project(project_id)
    overrides = meta.get("overrides") or {}
    scenes_map = (meta.get("scenes_scan") or {}).get("scenes") or {}
    scene = (overrides.get(str(seg_id)) or {}).get("scene") or scenes_map.get(str(seg_id))
    if not scene or not scene.get("path") or not Path(scene["path"]).exists():
        raise HTTPException(status_code=404, detail="ملف المشهد غير موجود.")
    return FileResponse(scene["path"])


@app.post("/api/system/pick-folder")
def api_pick_folder() -> Dict[str, Any]:
    """Open native Windows folder picker dialog (PowerShell / tkinter) on localhost."""
    if platform.system() == "Windows":
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dlg.Description = 'اختر مجلد المشاهد المرقمة'; "
            "if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dlg.SelectedPath }"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True)
        path = res.stdout.strip()
        return {"ok": bool(path), "path": path}
    return {"ok": False, "path": "", "error": "منتقي المجلدات الأصلي متاح على نظام ويندوز، أو يمكنك سحب الملفات وإفلاتها هنا."}


@app.post("/api/system/open-path")
async def api_open_path(payload: Dict[str, Any]) -> Dict[str, Any]:
    target = str(payload.get("target") or "")
    project_id = payload.get("project_id")
    path_to_open: Optional[Path] = None

    if target == "log":
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not LOG_FILE.exists():
            LOG_FILE.write_text("", encoding="utf-8")
        path_to_open = LOG_FILE
    elif project_id:
        pdir = get_project_dir(str(project_id))
        if target == "output_folder":
            path_to_open = pdir / "output"
        elif target == "output_video":
            path_to_open = pdir / "output" / "final.mp4"

    if not path_to_open or not path_to_open.exists():
        return {"ok": False, "error": "المسار المطلوب غير موجود."}

    if platform.system() == "Windows":
        os.startfile(str(path_to_open))  # type: ignore[attr-defined]
        return {"ok": True}
    return {"ok": True, "path": str(path_to_open)}


@app.get("/api/system/problem-report")
def api_problem_report() -> Dict[str, Any]:
    return {"ok": True, "report": build_problem_report(check_binaries())}


# ---------- Update 3: Global Settings, GitHub Delta Patch Updater, Local Fallback & Rollback ----------

def _trigger_swap_and_restart(pending_payload: Dict[str, Any]) -> None:
    """Write pending swap marker and exit engine cleanly so helper/supervisor swaps unlocked files and restarts."""
    pending_file = BASE_DIR / "staging" / ".pending_swap.json"
    _atomic_write_json(pending_file, pending_payload)

    def _exit_soon() -> None:
        time.sleep(0.35)
        if os.environ.get("AUTOCUT_SUPERVISED") == "1":
            os._exit(42)
        else:
            cmd = [
                sys.executable, "-m", "app.updater_helper",
                "--action", pending_payload["action"],
                "--base-dir", str(BASE_DIR),
                "--wait-pid", str(os.getpid()),
                "--restart",
            ]
            if pending_payload["action"] == "apply":
                cmd += ["--stage-dir", str(pending_payload["stage_dir"])]
            elif pending_payload["action"] == "rollback":
                cmd += ["--backup-id", str(pending_payload["backup_id"])]
            subprocess.Popen(cmd, cwd=str(BASE_DIR))
            os._exit(0)

    threading.Thread(target=_exit_soon, daemon=True).start()


@app.get("/api/settings/global")
def api_get_global_settings() -> Dict[str, Any]:
    return {"ok": True, **get_public_global_settings(), "version": get_app_version()}


@app.put("/api/settings/global")
async def api_save_global_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    save_global_settings(payload)
    return {"ok": True, **get_public_global_settings()}


@app.post("/api/updates/test-connection")
async def api_updates_test_connection(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    saved = load_global_settings()["update_source"]
    repo = str(payload.get("repo") if payload.get("repo") is not None else saved.get("repo", "")).strip()
    branch = str(payload.get("branch") if payload.get("branch") is not None else saved.get("branch", "main")).strip() or "main"
    subfolder = str(payload.get("subfolder") if payload.get("subfolder") is not None else saved.get("subfolder", "auto")).strip() or "auto"
    raw_tok = payload.get("token")
    token = saved.get("token", "") if (raw_tok is None or raw_tok == "__KEEP__") else str(raw_tok).strip()

    try:
        res = test_github_connection(repo=repo, branch=branch, subfolder=subfolder, token=token)
        return res
    except UpdaterError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.get("/api/updates/check")
def api_updates_check() -> Dict[str, Any]:
    try:
        return check_github_version_only()
    except UpdaterError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.post("/api/updates/plan")
def api_updates_plan() -> Dict[str, Any]:
    try:
        return plan_github_update()
    except UpdaterError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.post("/api/updates/apply")
def api_updates_apply() -> Dict[str, Any]:
    try:
        plan = plan_github_update(include_commits=False)
    except UpdaterError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})

    if not plan.get("update_available") and plan.get("change_count", 0) == 0:
        return {"ok": True, "already_latest": True, "version": plan["local_version"]}

    job_id = uuid.uuid4().hex[:10]
    cancel_ev = threading.Event()
    with _JOBS_LOCK:
        _JOB_EVENTS[job_id] = cancel_ev
        _JOBS[job_id] = {
            "id": job_id,
            "type": "update",
            "status": "running",
            "progress": 0.0,
            "bytes_downloaded": 0,
            "total_bytes": plan["total_bytes"],
            "mb_downloaded": 0.0,
            "total_mb": plan["total_mb"],
            "speed_mb_s": 0.0,
            "message": "جاري بدء تنزيل ملفات التحديث...",
            "restarting": False,
            "error": None,
        }

    def _run_update() -> None:
        try:
            def _on_prog(info: Dict[str, Any]) -> None:
                _update_job(
                    job_id,
                    progress=info.get("percent", 0.0),
                    bytes_downloaded=info.get("bytes_downloaded", 0),
                    total_bytes=info.get("total_bytes", 0),
                    mb_downloaded=info.get("mb_downloaded", 0.0),
                    total_mb=info.get("total_mb", 0.0),
                    speed_mb_s=info.get("speed_mb_s", 0.0),
                    message=info.get("message", "جاري التنزيل..."),
                )

            stage_root = stage_github_update(plan, progress_callback=_on_prog, cancel_event=cancel_ev)
            _update_job(
                job_id,
                status="done",
                progress=100.0,
                restarting=True,
                message="اكتمل التنزيل والتحقق. جاري استبدال الملفات وإعادة تشغيل الخادم...",
            )
            _trigger_swap_and_restart({"action": "apply", "stage_dir": str(stage_root)})
        except InterruptedError as exc:
            _update_job(job_id, status="cancelled", error=str(exc), message="تم إلغاء التحديث.")
        except UpdaterError as exc:
            _update_job(job_id, status="error", error=str(exc), message="تعذر إكمال التحديث.")
        except Exception as exc:
            logger.error("Update apply error: %s", redact_secrets(str(exc)))
            _update_job(job_id, status="error", error="حدث خطأ أثناء تنزيل التحديث. لم يتم تغيير أي ملف.", message="فشل التحديث.")

    threading.Thread(target=_run_update, daemon=True).start()
    return {"ok": True, "job_id": job_id, "plan": plan}


@app.post("/api/updates/local-zip")
async def api_updates_local_zip(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Offline fallback (Update 2): stage and apply update from an uploaded .zip release."""
    tmp_zip = BASE_DIR / "staging" / f"upload_{uuid.uuid4().hex[:6]}.zip"
    tmp_zip.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp_zip, "wb") as f:
            shutil.copyfileobj(file.file, f)
        res = stage_local_update(tmp_zip)
        _trigger_swap_and_restart({"action": "apply", "stage_dir": res["stage_root"]})
        return {"ok": True, "restarting": True, "plan": res["plan"]}
    except UpdaterError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    finally:
        tmp_zip.unlink(missing_ok=True)


@app.post("/api/updates/local-folder")
async def api_updates_local_folder(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Offline fallback (Update 2): stage and apply update from a local folder path."""
    folder = str(payload.get("path") or "").strip()
    try:
        res = stage_local_update(Path(folder))
        _trigger_swap_and_restart({"action": "apply", "stage_dir": res["stage_root"]})
        return {"ok": True, "restarting": True, "plan": res["plan"]}
    except UpdaterError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.get("/api/updates/backups")
def api_updates_backups() -> Dict[str, Any]:
    return {"ok": True, "backups": list_backups()}


@app.post("/api/updates/rollback")
async def api_updates_rollback(payload: Dict[str, Any]) -> Dict[str, Any]:
    backup_id = str(payload.get("backup_id") or "").strip()
    if not backup_id:
        return JSONResponse(status_code=400, content={"ok": False, "error": "يرجى اختيار نسخة احتياطية للتراجع إليها."})
    bdir = BASE_DIR / "backups" / Path(backup_id).name
    if not (bdir / "backup_meta.json").exists():
        return JSONResponse(status_code=404, content={"ok": False, "error": "النسخة الاحتياطية غير موجودة."})
    _trigger_swap_and_restart({"action": "rollback", "backup_id": Path(backup_id).name})
    return {"ok": True, "restarting": True}


@app.post("/api/updates/ack-result")
def api_updates_ack_result() -> Dict[str, Any]:
    res_file = BASE_DIR / "staging" / "last_update_result.json"
    if res_file.exists():
        res_file.unlink(missing_ok=True)
    return {"ok": True}


# Serve static frontend from autocut/web/
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

