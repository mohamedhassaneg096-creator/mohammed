"""Local stdlib HTTP bridge for Playwright UI self-review across all 3 themes in v1.3.0."""

from __future__ import annotations

import json
import mimetypes
import shutil
import sys
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.matching import match_segments_to_transcript, parse_numbered_segments, update_cut_start_time
from app.project import (
    LOG_FILE,
    create_project,
    delete_project,
    file_sha256,
    get_project_dir,
    list_projects,
    load_cuts,
    load_motions,
    load_project,
    load_transcript,
    save_cuts,
    save_motions,
    save_project,
    save_transcript,
)
from app.render import export_single_scene_with_audio, export_timeline_video, slice_audio_into_clips
from app.scenes import scan_scenes_folder
from app.settings_store import (
    build_problem_report,
    get_app_version,
    get_public_global_settings,
    save_global_settings,
)
from app.timeline import build_timeline
from app.transcript_import import (
    TranscriptImportError,
    apply_ignored_segments,
    parse_and_normalize_transcript,
)
from app.workspace_helpers import create_demo_project
from tests.make_sample import make_sine_audio, make_solid_scene_image, make_testsrc_video

WEB_DIR = ROOT_DIR / "web"
SIMULATED_MISSING_FFMPEG = {"flag": False}
SIMULATED_UPDATE_SETTINGS = {"file": None}


def seed_demo_project(projects_root: Path) -> str:
    """Create a realistic Arabic project with imported transcript, suspicious line, sliced audio clips, scenes, motions, and final.mp4."""
    proj = create_project("وثائقي الحضارة الأندلسية", projects_root=projects_root)
    pid = proj["id"]
    pdir = get_project_dir(pid, projects_root)

    scenes_dir = pdir / "demo_scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = make_sine_audio(pdir / "audio" / "التعليق_الصوتي.wav", duration_s=8.0)
    make_solid_scene_image(scenes_dir / "1.png", 1)
    make_solid_scene_image(scenes_dir / "2 - قرطبة.png", 2)
    make_testsrc_video(scenes_dir / "scene_3.mp4", duration_s=0.6)
    make_solid_scene_image(scenes_dir / "4.png", 4)
    make_solid_scene_image(scenes_dir / "5.png", 5)

    raw_text = (
        "1. في قلب الأندلس بدأت قصة حضارة مضيئة\n"
        "2. كانت قرطبة مركزاً للعلم والعمارة والفنون\n"
        "3. وتدفقت المياه في حدائق قصر الحمراء\n"
        "4. وازدهرت المكتبات والمدارس في كل مدينة\n"
        "5. وبقي أثر تلك الحقبة خالداً حتى يومنا هذا"
    )
    parsed = parse_numbered_segments(raw_text)
    scan = scan_scenes_folder(pid, scenes_dir, segment_ids=[s["id"] for s in parsed["segments"]], projects_root=projects_root)

    srt_text = (
        "1\n00:00:00,000 --> 00:00:01,500\nفي قلب الأندلس بدأت قصة حضارة مضيئة\n\n"
        "2\n00:00:01,550 --> 00:00:03,400\nكانت قرطبة مركزاً للعلم والعمارة والفنون\n\n"
        "3\n00:00:03,450 --> 00:00:05,300\nوتدفقت المياه في حدائق قصر الحمراء الرائعة\n\n"
        "4\n00:00:05,350 --> 00:00:06,700\nوازدهرت المكتبات والمدارس في كل مدينة\n\n"
        "5\n00:00:06,750 --> 00:00:07,600\nوبقي أثر تلك الحقبة خالداً حتى يومنا هذا\n\n"
        "6\n00:00:07,650 --> 00:00:07,950\nThanks for watching subscribe now\n"
    )
    transcript = parse_and_normalize_transcript(srt_text, audio_duration=8.0, filename="andalus_vibe.srt")
    save_transcript(pid, transcript, projects_root=projects_root)

    cuts = match_segments_to_transcript(parsed["segments"], transcript, audio_duration=8.0)
    cuts["segments"][2]["estimated"] = True
    cuts["segments"][2]["badge"] = "تقدير"
    cuts["segments"][3]["confidence"] = 58.0
    save_cuts(pid, cuts, projects_root=projects_root)

    settings = {**proj["settings"], "resolution": "640x360", "width": 640, "height": 360, "fps": 24}
    audio_clips = slice_audio_into_clips(pid, audio_path=audio_path, cuts=cuts, settings=settings, projects_root=projects_root)

    motions = {
        "global_strength": 15,
        "assignments": {
            "1": {"type": "zoom_in", "x": 0.38, "y": 0.42, "strength": 16, "ease": True},
        },
    }
    save_motions(pid, motions, projects_root=projects_root)

    tl = build_timeline(cuts, scan["scenes"], fps=24)
    report = export_timeline_video(
        project_id=pid,
        audio_path=audio_path,
        timeline=tl,
        settings=settings,
        motions_data=motions,
        projects_root=projects_root,
    )
    save_project(
        pid,
        {
            "audio_path": str(audio_path),
            "audio_filename": audio_path.name,
            "audio_hash": file_sha256(audio_path),
            "audio_duration": 8.0,
            "audio_clips": audio_clips,
            "transcript_filename": transcript["filename"],
            "transcript_precision": transcript["precision"],
            "scenes_dir": str(scenes_dir),
            "raw_text": raw_text,
            "scenes_scan": scan,
            "settings": settings,
            "export_report": report,
            "stage": "inputs",
        },
        projects_root=projects_root,
    )
    return pid


def make_handler(projects_root: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send_json(self, code: int, obj: dict):
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_file(self, path: Path, content_type: str | None = None):
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            ct = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path == "/manifest.json":
                self._send_file(WEB_DIR / "manifest.json", "application/manifest+json")
                return
            if path == "/sw.js":
                self._send_file(WEB_DIR / "sw.js", "application/javascript")
                return
            if path in ("/icon.svg", "/pwa-192x192.png", "/pwa-512x512.png", "/pwa-maskable-512x512.png", "/apple-touch-icon.png"):
                self._send_file(WEB_DIR / "icons" / path.lstrip("/"))
                return

            if path == "/api/health":
                self._send_json(200, {
                    "ok": not SIMULATED_MISSING_FFMPEG["flag"],
                    "version": get_app_version(),
                    "ffmpeg_ok": not SIMULATED_MISSING_FFMPEG["flag"],
                    "ffprobe_ok": not SIMULATED_MISSING_FFMPEG["flag"],
                    "rapidfuzz_ok": True,
                    "transcription_mode": "external_import",
                    "log_path": str(LOG_FILE),
                    "last_update_result": None,
                })
                return

            if path == "/api/settings/global":
                sfile = SIMULATED_UPDATE_SETTINGS["file"]
                self._send_json(200, {"ok": True, **get_public_global_settings(sfile), "version": get_app_version()})
                return

            if path == "/api/system/problem-report":
                sfile = SIMULATED_UPDATE_SETTINGS["file"]
                rep = build_problem_report(
                    {"ffmpeg_ok": True, "ffmpeg_version": "ffmpeg 4.4", "rapidfuzz_ok": True},
                    settings_file=sfile,
                )
                self._send_json(200, {"ok": True, "report": rep})
                return

            if path == "/api/updates/check":
                self._send_json(200, {
                    "ok": True,
                    "configured": True,
                    "local_version": get_app_version(),
                    "remote_version": "1.3.1",
                    "update_available": True,
                    "commit_date": "2026-11-25T12:00:00Z",
                    "subfolder": "autocut",
                })
                return

            if path == "/api/updates/backups":
                self._send_json(200, {
                    "ok": True,
                    "backups": [
                        {
                            "id": "1.2.0_20261125_100000",
                            "old_version": "1.2.0",
                            "target_version": "1.3.0",
                            "created_at": "2026-11-25T10:00:00Z",
                            "file_count": 8,
                        }
                    ],
                })
                return

            if path == "/api/projects":
                self._send_json(200, {"ok": True, "projects": list_projects(projects_root)})
                return

            if path.startswith("/api/projects/"):
                parts = path[len("/api/projects/"):].split("/")
                pid = parts[0]
                if len(parts) == 1:
                    meta = load_project(pid, projects_root)
                    cuts = load_cuts(pid, projects_root)
                    motions = load_motions(pid, projects_root)
                    transcript = load_transcript(pid, projects_root)
                    scenes_map = (meta.get("scenes_scan") or {}).get("scenes") or {}
                    tl = build_timeline(cuts, scenes_map, fps=int(meta["settings"]["fps"]), overrides=meta.get("overrides") or {}) if cuts else None
                    has_out = (get_project_dir(pid, projects_root) / "output" / "final.mp4").exists()
                    self._send_json(200, {
                        "ok": True,
                        "project": meta,
                        "cuts": cuts,
                        "timeline": tl,
                        "motions": motions,
                        "transcript": transcript,
                        "audio_clips": meta.get("audio_clips") or [],
                        "has_transcript": transcript is not None,
                        "has_output": has_out,
                    })
                    return
                if parts[1] == "audio":
                    meta = load_project(pid, projects_root)
                    self._send_file(Path(meta["audio_path"]))
                    return
                if parts[1] == "audio-clips" and len(parts) == 3:
                    seg_id = int(parts[2])
                    matches = sorted((get_project_dir(pid, projects_root) / "audio_clips").glob(f"clip_{seg_id}_*.m4a"))
                    if matches:
                        self._send_file(matches[-1], "audio/mp4")
                    else:
                        self.send_error(404)
                    return
                if parts[1] == "thumbnails" and len(parts) == 3:
                    self._send_file(get_project_dir(pid, projects_root) / "thumbnails" / Path(parts[2]).name)
                    return
                if parts[1] == "scene-file" and len(parts) == 3:
                    meta = load_project(pid, projects_root)
                    sc = (meta.get("scenes_scan") or {}).get("scenes", {}).get(parts[2])
                    if sc:
                        self._send_file(Path(sc["path"]))
                    else:
                        self.send_error(404)
                    return
                if parts[1] == "output" and len(parts) == 3:
                    fname = Path(parts[2]).name
                    self._send_file(get_project_dir(pid, projects_root) / "output" / fname, "video/mp4")
                    return
                if parts[1] == "previews" and len(parts) == 3:
                    self._send_file(get_project_dir(pid, projects_root) / "previews" / Path(parts[2]).name, "video/mp4")
                    return

            rel = "index.html" if path == "/" else path.lstrip("/")
            fpath = WEB_DIR / rel
            if fpath.exists() and fpath.is_file():
                self._send_file(fpath)
            else:
                self._send_file(WEB_DIR / "index.html")

        def do_POST(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            body = self._read_json_body()

            if path == "/api/test/toggle-ffmpeg-error":
                SIMULATED_MISSING_FFMPEG["flag"] = bool(body.get("missing", False))
                self._send_json(200, {"ok": True})
                return

            if path.startswith("/api/jobs/") and (path.endswith("/pause") or path.endswith("/resume")):
                self._send_json(200, {"ok": True, "paused": path.endswith("/pause")})
                return

            if path == "/api/updates/test-connection":
                self._send_json(200, {
                    "ok": True,
                    "repo": body.get("repo") or "demo/autocut",
                    "branch": body.get("branch") or "main",
                    "subfolder": "autocut",
                    "remote_version": "1.3.1",
                    "commit_date": "2026-11-25T12:00:00Z",
                })
                return

            if path == "/api/updates/plan":
                self._send_json(200, {
                    "ok": True,
                    "source": "github",
                    "local_version": get_app_version(),
                    "remote_version": "1.3.1",
                    "update_available": True,
                    "commit_date": "2026-11-25T12:00:00Z",
                    "subfolder": "autocut",
                    "changelog": ["1.3.1", "تحسينات قص الصوت الفوري ومعاينة الحركة."],
                    "commits": ["feat: v1.3 pwa & 2-stage pipeline"],
                    "to_download": [{"relpath": "VERSION", "sha": "abc", "size": 6}],
                    "to_delete": [],
                    "unchanged_count": 26,
                    "change_count": 1,
                    "total_bytes": 6,
                    "total_mb": 0.001,
                })
                return

            if path == "/api/updates/ack-result":
                self._send_json(200, {"ok": True})
                return

            if path == "/api/projects/demo":
                meta = create_demo_project(projects_root=projects_root)
                self._send_json(200, {"ok": True, "project": meta})
                return

            if path == "/api/projects":
                meta = create_project(body.get("name", "مشروع جديد"), projects_root=projects_root)
                self._send_json(200, {"ok": True, "project": meta})
                return

            if path.startswith("/api/projects/"):
                parts = path[len("/api/projects/"):].split("/")
                pid = parts[0]
                if len(parts) >= 2 and parts[1] == "parse-text":
                    res = parse_numbered_segments(body.get("raw_text", ""))
                    self._send_json(200, {"ok": True, **res})
                    return
                if len(parts) >= 2 and parts[1] == "import-transcript":
                    meta = load_project(pid, projects_root)
                    try:
                        tr = parse_and_normalize_transcript(
                            str(body.get("text") or ""),
                            audio_duration=float(meta.get("audio_duration") or 0.0),
                            filename=str(body.get("filename") or "pasted.txt"),
                        )
                    except TranscriptImportError as exc:
                        self._send_json(400, {
                            "ok": False,
                            "error": exc.message,
                            "hint": exc.hint,
                            "code": exc.code,
                            "preview_lines": exc.preview_lines,
                        })
                        return
                    save_transcript(pid, tr, projects_root=projects_root)
                    meta = save_project(
                        pid,
                        {"transcript_filename": tr["filename"], "transcript_precision": tr["precision"]},
                        projects_root=projects_root,
                    )
                    self._send_json(200, {"ok": True, "transcript": tr, "project": meta})
                    return
                if len(parts) >= 3 and parts[1] == "transcript" and parts[2] == "ignored":
                    meta = load_project(pid, projects_root)
                    tr = apply_ignored_segments(load_transcript(pid, projects_root), body.get("ignored_indices") or [])
                    save_transcript(pid, tr, projects_root=projects_root)
                    cuts = match_segments_to_transcript(parse_numbered_segments(meta.get("raw_text") or "")["segments"], tr, audio_duration=float(meta.get("audio_duration") or 8.0))
                    save_cuts(pid, cuts, projects_root=projects_root)
                    tl = build_timeline(cuts, (meta.get("scenes_scan") or {}).get("scenes") or {}, fps=int(meta["settings"]["fps"]))
                    self._send_json(200, {"ok": True, "transcript": tr, "cuts": cuts, "timeline": tl})
                    return
                if len(parts) >= 2 and parts[1] == "match-cuts":
                    meta = load_project(pid, projects_root)
                    tr = load_transcript(pid, projects_root)
                    raw_text = body.get("raw_text") if body.get("raw_text") is not None else meta.get("raw_text", "")
                    parsed = parse_numbered_segments(raw_text)
                    cuts = match_segments_to_transcript(parsed["segments"], tr, audio_duration=float(meta.get("audio_duration") or 8.0))
                    save_cuts(pid, cuts, projects_root=projects_root)
                    clips = []
                    if meta.get("audio_path") and Path(meta["audio_path"]).exists():
                        clips = slice_audio_into_clips(pid, Path(meta["audio_path"]), cuts, settings=meta.get("settings") or {}, projects_root=projects_root)
                    tl = build_timeline(cuts, (meta.get("scenes_scan") or {}).get("scenes") or {}, fps=int(meta["settings"]["fps"]))
                    nxt = "motion" if body.get("auto_mode") else "cuts"
                    meta = save_project(pid, {"raw_text": raw_text, "stage": nxt, "audio_clips": clips}, projects_root=projects_root)
                    self._send_json(200, {"ok": True, "project": meta, "cuts": cuts, "timeline": tl, "scenes_scan": meta.get("scenes_scan"), "audio_clips": clips})
                    return
                if len(parts) >= 2 and parts[1] == "slice-audio":
                    meta = load_project(pid, projects_root)
                    cuts = load_cuts(pid, projects_root)
                    clips = slice_audio_into_clips(pid, Path(meta["audio_path"]), cuts, settings=meta.get("settings") or {}, projects_root=projects_root)
                    meta = save_project(pid, {"audio_clips": clips}, projects_root=projects_root)
                    self._send_json(200, {"ok": True, "audio_clips": clips, "project": meta})
                    return
                if len(parts) >= 3 and parts[1] == "export-scene":
                    seg_id = int(parts[2])
                    meta = load_project(pid, projects_root)
                    cuts = load_cuts(pid, projects_root)
                    tl = build_timeline(cuts, (meta.get("scenes_scan") or {}).get("scenes") or {}, fps=int(meta["settings"]["fps"]))
                    item = next(it for it in tl["items"] if int(it["segment_id"]) == seg_id)
                    motions = load_motions(pid, projects_root)
                    motion = (motions.get("assignments") or {}).get(str(seg_id))
                    res = export_single_scene_with_audio(
                        project_id=pid,
                        item=item,
                        audio_path=Path(meta["audio_path"]),
                        settings=meta["settings"],
                        motion=motion,
                        projects_root=projects_root,
                    )
                    self._send_json(200, res)
                    return
                if len(parts) >= 4 and parts[1] == "cuts" and parts[3] == "start":
                    seg_id = int(parts[2])
                    cuts = update_cut_start_time(load_cuts(pid, projects_root), seg_id, float(body["start"]))
                    save_cuts(pid, cuts, projects_root=projects_root)
                    meta = load_project(pid, projects_root)
                    tl = build_timeline(cuts, (meta.get("scenes_scan") or {}).get("scenes") or {}, fps=int(meta["settings"]["fps"]))
                    self._send_json(200, {"ok": True, "cuts": cuts, "timeline": tl})
                    return

        def do_PUT(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            body = self._read_json_body()
            if path == "/api/settings/global":
                sfile = SIMULATED_UPDATE_SETTINGS["file"]
                save_global_settings(body, settings_file=sfile)
                self._send_json(200, {"ok": True, **get_public_global_settings(sfile)})
                return
            if path.startswith("/api/projects/"):
                parts = path[len("/api/projects/"):].split("/")
                pid = parts[0]
                if len(parts) == 2 and parts[1] == "motions":
                    save_motions(pid, body, projects_root=projects_root)
                    self._send_json(200, {"ok": True, "motions": body})
                    return
                if len(parts) == 2 and parts[1] == "cuts":
                    save_cuts(pid, body["cuts"], projects_root=projects_root)
                    meta = load_project(pid, projects_root)
                    tl = build_timeline(body["cuts"], (meta.get("scenes_scan") or {}).get("scenes") or {}, fps=int(meta["settings"]["fps"]))
                    self._send_json(200, {"ok": True, "cuts": body["cuts"], "timeline": tl})
                    return

        def do_PATCH(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            body = self._read_json_body()
            if path.startswith("/api/projects/"):
                pid = path[len("/api/projects/"):].split("/")[0]
                meta = save_project(pid, body, projects_root=projects_root)
                self._send_json(200, {"ok": True, "project": meta})
                return

        def do_DELETE(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path.startswith("/api/projects/"):
                pid = path[len("/api/projects/"):].split("/")[0]
                delete_project(pid, projects_root=projects_root)
                self._send_json(200, {"ok": True})
                return

    return Handler


if __name__ == "__main__":
    tmp_dir = Path(tempfile.mkdtemp(prefix="autocut_ui_"))
    projects_root = tmp_dir / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    sfile = tmp_dir / "settings.json"
    save_global_settings(
        {"update_source": {"repo": "demo/autocut", "branch": "main", "subfolder": "auto", "token": "github_pat_demo1234567890", "check_on_startup": True}},
        settings_file=sfile,
    )
    SIMULATED_UPDATE_SETTINGS["file"] = sfile
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    seed_pid = seed_demo_project(projects_root)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(projects_root))
    print(f"READY port={port} seed_pid={seed_pid}", flush=True)
    try:
        server.serve_forever()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
