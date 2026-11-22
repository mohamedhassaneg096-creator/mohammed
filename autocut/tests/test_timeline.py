"""Timeline math & invariant tests (Phase 2)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.timeline import TimelineInvariantError, build_timeline, verify_timeline_invariants


class TestTimeline(unittest.TestCase):
    def test_gapless_cumulative_rounding_and_speed_badges(self):
        cuts = {
            "audio_duration": 10.0,
            "segments": [
                {"id": 1, "text": "one", "start": 0.0, "end": 2.333, "duration": 2.333, "confidence": 95},
                {"id": 2, "text": "two", "start": 2.333, "end": 5.667, "duration": 3.334, "confidence": 90},
                {"id": 3, "text": "three", "start": 5.667, "end": 10.0, "duration": 4.333, "confidence": 85},
            ],
        }
        scenes = {
            "1": {"number": 1, "type": "image", "path": "/tmp/1.png", "filename": "1.png", "duration": 0.0},
            # Video is 1.0s for a ~3.33s segment -> speed = 0.30x (<0.4x -> extreme badge)
            "2": {"number": 2, "type": "video", "path": "/tmp/2.mp4", "filename": "2.mp4", "duration": 1.0},
            # Video is 15.0s for a ~4.33s segment -> speed = 3.46x (>3.0x -> extreme badge)
            "3": {"number": 3, "type": "video", "path": "/tmp/3.mp4", "filename": "3.mp4", "duration": 15.0},
        }
        tl = build_timeline(cuts, scenes, fps=30)
        self.assertEqual(tl["total_frames"], 300)
        items = tl["items"]
        self.assertEqual(items[0]["frame_start"], 0)
        self.assertEqual(items[-1]["frame_end"], 300)
        for i in range(len(items) - 1):
            self.assertEqual(items[i]["frame_end"], items[i + 1]["frame_start"])

        self.assertEqual(len(tl["extreme_speed_videos"]), 2)
        self.assertTrue(items[1]["extreme_speed"])
        self.assertLess(items[1]["speed"], 0.4)
        self.assertTrue(items[2]["extreme_speed"])
        self.assertGreater(items[2]["speed"], 3.0)

    def test_invariant_fails_loudly_on_gap(self):
        bad_items = [
            {"segment_id": 1, "frame_start": 0, "frame_end": 59, "frame_count": 59},
            {"segment_id": 2, "frame_start": 60, "frame_end": 120, "frame_count": 60},
        ]
        with self.assertRaises(TimelineInvariantError):
            verify_timeline_invariants(bad_items, 4.0, 30)


if __name__ == "__main__":
    unittest.main()
