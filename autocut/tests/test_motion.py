"""Phase 3 tests: smoothstep crop trajectory monotonicity, clamping, and FFmpeg motion render."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.motion import compute_crop_trajectory, render_image_motion_clip
from app.render import probe_video_stats
from tests.make_sample import make_solid_scene_image


class TestMotion(unittest.TestCase):
    def test_crop_trajectory_monotonic_eased_and_clamped(self):
        # Corner focus (0.95, 0.95) must clamp so crop window never leaves [0, 1]
        traj = compute_crop_trajectory("zoom_in", focus_x=0.95, focus_y=0.95, strength_pct=20.0, frame_count=60, ease=True)
        self.assertEqual(len(traj), 60)
        self.assertAlmostEqual(traj[0]["w"], 1.0, places=4)
        self.assertAlmostEqual(traj[-1]["w"], 0.8, places=4)

        for i, step in enumerate(traj):
            self.assertGreaterEqual(step["x"], -1e-6)
            self.assertGreaterEqual(step["y"], -1e-6)
            self.assertLessEqual(step["x"] + step["w"], 1.0 + 1e-6)
            self.assertLessEqual(step["y"] + step["h"], 1.0 + 1e-6)
            if i > 0:
                # Width strictly non-increasing for zoom_in
                self.assertLessEqual(step["w"], traj[i - 1]["w"] + 1e-9)

        # Verify smoothstep easing: middle slope is faster than start/end slopes
        dw_start = abs(traj[1]["w"] - traj[0]["w"])
        dw_mid = abs(traj[30]["w"] - traj[29]["w"])
        dw_end = abs(traj[-1]["w"] - traj[-2]["w"])
        self.assertGreater(dw_mid, dw_start * 3.0)
        self.assertGreater(dw_mid, dw_end * 3.0)

    def test_render_zoom_in_and_pan_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            img = make_solid_scene_image(tdir / "صورة عالية.png", 1, width=1280, height=720)
            for direction in ("zoom_in", "zoom_out", "left", "right"):
                out_mp4 = tdir / f"motion_{direction}.mp4"
                render_image_motion_clip(
                    src_image=img,
                    out_clip=out_mp4,
                    frame_count=30,
                    width=640,
                    height=360,
                    fps=30,
                    motion={"type": direction, "x": 0.72, "y": 0.35, "strength": 18, "ease": True},
                )
                stats = probe_video_stats(out_mp4)
                self.assertEqual(stats["frames"], 30, f"{direction} frame count mismatch")
                self.assertAlmostEqual(stats["duration"], 1.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
