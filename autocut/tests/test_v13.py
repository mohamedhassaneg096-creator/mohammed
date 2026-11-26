"""Tests for AutoCut v1.3.0: immediate audio slicing, 2-stage pipeline, resource profiles, pause/resume, snap_zoom, single-scene export, and PWA assets."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.motion import VALID_DIRECTIONS, compute_crop_trajectory, render_image_motion_clip
from app.project import BASE_DIR, DEFAULT_SETTINGS, create_project, get_project_dir, save_cuts
from app.render import (
    export_single_scene_with_audio,
    export_timeline_video,
    get_resource_profile,
    probe_video_stats,
    slice_audio_into_clips,
)
from app.scenes import scan_scenes_folder
from app.timeline import build_timeline
from app.transcribe import probe_media_duration
from tests.make_sample import make_sine_audio, make_solid_scene_image


class V13Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_obj.name)
        self.proj_root = self.tmp / "projects"
        self.proj_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_obj.cleanup()

    def _seed_sample_project(self):
        proj = create_project("اختبار v1.3", projects_root=self.proj_root)
        pid = proj["id"]
        pdir = get_project_dir(pid, self.proj_root)
        audio_path = make_sine_audio(pdir / "audio" / "voice.wav", duration_s=4.2)
        scenes_dir = pdir / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        img1 = make_solid_scene_image(scenes_dir / "1.png", 1)
        img2 = make_solid_scene_image(scenes_dir / "2.png", 2)
        img3 = make_solid_scene_image(scenes_dir / "3.png", 3)

        cuts = {
            "audio_duration": 4.2,
            "segments": [
                {"id": 1, "text": "مقطع أول", "start": 0.0, "end": 1.4, "confidence": 95.0, "matched": True},
                {"id": 2, "text": "مقطع ثاني", "start": 1.4, "end": 2.8, "confidence": 92.0, "matched": True},
                {"id": 3, "text": "مقطع ثالث", "start": 2.8, "end": 4.2, "confidence": 94.0, "matched": True},
            ],
        }
        save_cuts(pid, cuts, projects_root=self.proj_root)
        scan = scan_scenes_folder(pid, scenes_dir, segment_ids=[1, 2, 3], generate_thumbs=False, projects_root=self.proj_root)
        return pid, audio_path, cuts, scan, [img1, img2, img3]

    def test_resource_profiles_default_light_and_modes(self) -> None:
        self.assertEqual(DEFAULT_SETTINGS["resource_mode"], "light")
        self.assertEqual(DEFAULT_SETTINGS["default_motion_duration"], 3.5)
        self.assertTrue(DEFAULT_SETTINGS["auto_slice_audio"])

        light = get_resource_profile({"resource_mode": "light"})
        medium = get_resource_profile({"resource_mode": "medium"})
        fast = get_resource_profile({"resource_mode": "fast"})

        self.assertEqual(light["workers"], 1)
        self.assertEqual(light["ffmpeg_threads"], 2)
        self.assertGreater(light["rest_delay"], 0.0)
        self.assertGreaterEqual(medium["workers"], 2)
        self.assertGreaterEqual(fast["workers"], 1)

    def test_stage1_immediate_audio_slicing_and_cache(self) -> None:
        pid, audio_path, cuts, _scan, _imgs = self._seed_sample_project()
        events = []
        clips = slice_audio_into_clips(
            pid,
            audio_path=audio_path,
            cuts=cuts,
            settings={"resource_mode": "light"},
            progress_callback=lambda e: events.append(e),
            projects_root=self.proj_root,
        )
        self.assertEqual(len(clips), 3)
        self.assertTrue(any(e.get("phase") == "slicing_audio" and e.get("stage_num") == 1 for e in events))

        for c in clips:
            cpath = Path(c["path"])
            self.assertTrue(cpath.exists())
            dur = probe_media_duration(cpath)
            self.assertAlmostEqual(dur, 1.4, delta=0.25)

        # Second call reuses existing slices immediately via cache
        clips2 = slice_audio_into_clips(
            pid,
            audio_path=audio_path,
            cuts=cuts,
            settings={"resource_mode": "light"},
            projects_root=self.proj_root,
        )
        self.assertTrue(all(c["cached"] for c in clips2))

    def test_pause_resume_and_cancel_events_in_slicing(self) -> None:
        pid, audio_path, cuts, _scan, _imgs = self._seed_sample_project()
        pause_ev = threading.Event()
        cancel_ev = threading.Event()
        pause_ev.set()

        result_holder = {}

        def _worker():
            try:
                res = slice_audio_into_clips(
                    pid,
                    audio_path=audio_path,
                    cuts=cuts,
                    settings={"resource_mode": "fast"},
                    pause_event=pause_ev,
                    cancel_event=cancel_ev,
                    projects_root=self.proj_root,
                )
                result_holder["clips"] = res
            except Exception as exc:  # noqa: BLE001
                result_holder["err"] = exc

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        time.sleep(0.25)
        self.assertTrue(t.is_alive())
        self.assertNotIn("clips", result_holder)  # genuinely paused

        pause_ev.clear()
        t.join(timeout=15.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(len(result_holder.get("clips", [])), 3)

        # Test cancel while paused
        pause_ev.set()
        cancel_ev.set()
        with self.assertRaises(InterruptedError):
            slice_audio_into_clips(
                pid,
                audio_path=audio_path,
                cuts=cuts,
                settings={"resource_mode": "fast"},
                pause_event=pause_ev,
                cancel_event=cancel_ev,
                projects_root=self.proj_root,
            )

    def test_snap_zoom_trajectory_and_ffmpeg_render(self) -> None:
        self.assertIn("snap_zoom", VALID_DIRECTIONS)
        traj = compute_crop_trajectory("snap_zoom", focus_x=0.35, focus_y=0.45, strength_pct=18, frame_count=48, ease=True, fps=24)
        self.assertEqual(len(traj), 48)
        # Zoom increases sharply early (fast punch-in within first 30% of frames)
        self.assertGreater(traj[12]["zoom"], 1.15)
        self.assertGreaterEqual(traj[-1]["zoom"], traj[12]["zoom"])

        img = make_solid_scene_image(self.tmp / "snap_test.png", 9)
        out_clip = self.tmp / "snap_clip.mp4"
        render_image_motion_clip(
            src_image=img,
            out_clip=out_clip,
            frame_count=36,
            width=640,
            height=360,
            fps=24,
            motion={"type": "snap_zoom", "x": 0.35, "y": 0.45, "strength": 18, "ease": True},
            ffmpeg_threads=2,
        )
        self.assertTrue(out_clip.exists())
        stats = probe_video_stats(out_clip)
        self.assertEqual(stats["width"], 640)
        self.assertEqual(stats["height"], 360)
        self.assertAlmostEqual(stats["duration"], 1.5, delta=0.15)

    def test_stage2_independent_single_scene_export_and_full_export(self) -> None:
        pid, audio_path, cuts, scan, _imgs = self._seed_sample_project()
        tl = build_timeline(cuts, scan["scenes"], fps=24)
        settings = {**DEFAULT_SETTINGS, "width": 640, "height": 360, "fps": 24, "resource_mode": "light"}

        # Export just scene #2 independently
        single_res = export_single_scene_with_audio(
            project_id=pid,
            item=tl["items"][1],
            audio_path=audio_path,
            settings=settings,
            motion={"type": "snap_zoom", "x": 0.5, "y": 0.5, "strength": 15, "ease": True},
            projects_root=self.proj_root,
        )
        self.assertTrue(single_res["ok"])
        scene2_mp4 = Path(single_res["output_path"])
        self.assertTrue(scene2_mp4.exists())
        s2_stats = probe_video_stats(scene2_mp4)
        self.assertAlmostEqual(s2_stats["duration"], 1.4, delta=0.25)

        # Full export reuses cached scene #2 and reports light resource mode
        full_res = export_timeline_video(
            project_id=pid,
            audio_path=audio_path,
            timeline=tl,
            settings=settings,
            motions_data={"global_strength": 15, "assignments": {"2": {"type": "snap_zoom", "x": 0.5, "y": 0.5, "strength": 15, "ease": True}}},
            projects_root=self.proj_root,
        )
        self.assertEqual(full_res["resource_mode"], "light")
        self.assertGreaterEqual(full_res["cached_clips"], 1)
        self.assertTrue(Path(full_res["output_path"]).exists())

    def test_pwa_assets_and_v13_server_routes(self) -> None:
        manifest_path = BASE_DIR / "web" / "manifest.json"
        sw_path = BASE_DIR / "web" / "sw.js"
        self.assertTrue(manifest_path.exists())
        self.assertTrue(sw_path.exists())

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertLessEqual(len(manifest["short_name"]), 12)

        for icon_name in ("icon.svg", "pwa-192x192.png", "pwa-512x512.png", "pwa-maskable-512x512.png", "apple-touch-icon.png"):
            self.assertTrue((BASE_DIR / "web" / "icons" / icon_name).exists(), f"Missing icon {icon_name}")

        server_py = (BASE_DIR / "app" / "server.py").read_text(encoding="utf-8")
        for route in ("/slice-audio", "/audio-clips/{seg_id}", "/export-scene/{seg_id}", "/pause", "/resume", "/manifest.json", "/sw.js"):
            self.assertIn(route, server_py)

        # Confirm Whisper remains excluded
        settings_ex = (BASE_DIR / "settings.example.json").read_text(encoding="utf-8").lower()
        self.assertNotIn("whisper", settings_ex)


if __name__ == "__main__":
    unittest.main(verbosity=2)
