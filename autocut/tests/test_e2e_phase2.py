"""Phase 2 & 4 E2E tests: scene discovery, Arabic/space paths, video speed stretching, cache reuse, and frame verification."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.project import create_project, save_motions
from app.render import export_timeline_video, probe_video_stats
from app.scenes import scan_scenes_folder
from app.timeline import build_timeline
from tests.make_sample import (
    SCENE_COLORS,
    make_sine_audio,
    make_solid_scene_image,
    make_testsrc_video,
)


def sample_center_rgb(video_path: Path, timestamp_s: float) -> tuple[int, int, int]:
    """Extract 1 frame at timestamp_s as 3x3 PPM and read the top-left corner pixel (away from center box)."""
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", f"{timestamp_s:.3f}",
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", "crop=8:8:4:4",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-",
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or len(res.stdout) < 3:
        raise RuntimeError(f"Failed sampling frame at {timestamp_s}s: {res.stderr.decode('utf-8', 'replace')}")
    r, g, b = res.stdout[0], res.stdout[1], res.stdout[2]
    return r, g, b


class TestE2EPhase2And4(unittest.TestCase):
    def test_full_export_arabic_paths_video_speed_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects_root = root / "projects"
            scenes_dir = root / "مشاهد المشروع 1"
            scenes_dir.mkdir(parents=True, exist_ok=True)

            # Create project & 8.0s sine audio with Arabic filename + spaces
            proj = create_project("تجربة التصدير", projects_root=projects_root)
            pid = proj["id"]
            audio_path = make_sine_audio(root / "صوت التجربة النهائي.wav", duration_s=8.0)

            # Scenes: 1 (image), 2 (image with Arabic filename), 3 (short video 1.0s stretched to 2.0s), 4 (image)
            make_solid_scene_image(scenes_dir / "1.png", 1)
            make_solid_scene_image(scenes_dir / "02 - مشهد عربي.png", 2)
            make_testsrc_video(scenes_dir / "scene_3.mp4", duration_s=1.0)
            make_solid_scene_image(scenes_dir / "4.png", 4)

            scan = scan_scenes_folder(pid, scenes_dir, segment_ids=[1, 2, 3, 4], projects_root=projects_root)
            self.assertTrue(scan["ok"], f"Scan failed: {scan.get('error')}")
            self.assertEqual(scan["missing_numbers"], [])

            cuts = {
                "audio_duration": 8.0,
                "segments": [
                    {"id": 1, "text": "one", "start": 0.0, "end": 2.0, "duration": 2.0, "confidence": 96},
                    {"id": 2, "text": "two", "start": 2.0, "end": 4.0, "duration": 2.0, "confidence": 92},
                    {"id": 3, "text": "three", "start": 4.0, "end": 6.0, "duration": 2.0, "confidence": 88},
                    {"id": 4, "text": "four", "start": 6.0, "end": 8.0, "duration": 2.0, "confidence": 95},
                ],
            }
            settings = {"width": 640, "height": 360, "fps": 30, "background_fill": "blurred", "slowmo_smooth": "off"}
            tl = build_timeline(cuts, scan["scenes"], fps=30)

            # 1st Export (without motion)
            report1 = export_timeline_video(
                project_id=pid,
                audio_path=audio_path,
                timeline=tl,
                settings=settings,
                motions_data={"global_strength": 15, "assignments": {}},
                projects_root=projects_root,
            )
            self.assertEqual(report1["rendered_clips"], 4)
            self.assertEqual(report1["cached_clips"], 0)
            self.assertAlmostEqual(report1["video_duration"], report1["audio_duration"], delta=0.05)

            final_mp4 = Path(report1["output_path"])
            self.assertTrue(final_mp4.exists())
            stats = probe_video_stats(final_mp4)
            self.assertEqual(stats["frames"], 240)

            # Verify scene 1, 2, 4 appear at their midpoints (1.0s, 3.0s, 7.0s)
            for seg_num, mid_t in [(1, 1.0), (2, 3.0), (4, 7.0)]:
                r, g, b = sample_center_rgb(final_mp4, mid_t)
                er, eg, eb = SCENE_COLORS[seg_num]
                self.assertLess(abs(r - er) + abs(g - eg) + abs(b - eb), 45, f"Scene {seg_num} color mismatch at {mid_t}s: {(r,g,b)} vs {(er,eg,eb)}")

            # 2nd Export after changing ONLY image 2's motion -> MUST re-render ONLY 1 clip and reuse 3 cached clips!
            motions = {
                "global_strength": 15,
                "assignments": {
                    "2": {"type": "zoom_in", "x": 0.4, "y": 0.5, "strength": 15, "ease": True}
                },
            }
            save_motions(pid, motions, projects_root=projects_root)
            report2 = export_timeline_video(
                project_id=pid,
                audio_path=audio_path,
                timeline=tl,
                settings=settings,
                motions_data=motions,
                projects_root=projects_root,
            )
            self.assertEqual(report2["cached_clips"], 3)
            self.assertEqual(report2["rendered_clips"], 1)
            self.assertAlmostEqual(report2["video_duration"], 8.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
