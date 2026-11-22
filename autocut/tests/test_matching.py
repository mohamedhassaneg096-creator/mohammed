"""Phase 1 tests: tolerant numbered parsing, normalization, and monotonic fuzzy matching."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.matching import (
    match_segments_to_transcript,
    normalize_text,
    parse_numbered_segments,
    update_cut_start_time,
)


class TestMatching(unittest.TestCase):
    def test_normalization_arabic_and_latin(self):
        raw = "أَهْلاً بِكُمْ فِي العَالَمِ ١٢٣! Hello—World"
        self.assertEqual(normalize_text(raw), "اهلا بكم في العالم 123 hello world")
        self.assertEqual(normalize_text("إِلَى مَدِينَةٍ كُبْرَى"), "الي مدينه كبري")

    def test_parse_150_segments_with_arabic_digits_and_odd_separators(self):
        lines = []
        seps = [" ", ". ", "- ", ") ", ": ", " - "]
        arabic_digits = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
        for i in range(1, 151):
            num_txt = str(i).translate(arabic_digits) if i % 3 == 0 else str(i)
            sep = seps[i % len(seps)]
            if i % 7 == 0:
                lines.append(f"[{num_txt}] بداية المشهد رقم {i}")
                lines.append(f"وتكملة السطر للمشهد {i}")
            else:
                lines.append(f"{num_txt}{sep}بداية المشهد رقم {i}")

        parsed = parse_numbered_segments("\n".join(lines))
        self.assertEqual(len(parsed["segments"]), 150)
        self.assertEqual(parsed["warnings"], [])
        self.assertIn("وتكملة السطر", parsed["segments"][6]["text"])

    def test_parse_detects_gaps_and_duplicates(self):
        raw = "1 اول\n2 ثاني\n4 رابع\n4 مكرر"
        parsed = parse_numbered_segments(raw)
        self.assertEqual(len(parsed["segments"]), 4)
        joined = " | ".join(parsed["warnings"])
        self.assertIn("مكررة", joined)
        self.assertIn("فجوات", joined)

    def test_monotonic_matching_with_diacritics_typos_and_out_of_order_trap(self):
        # Fake transcript with diacritics, minor typos, and an out-of-order repeated phrase trap
        # Segment 2 phrase ("في هذه المدينة القديمة") also appears much later at t=26s!
        # Segment 3 phrase ("ثم انطلقت القافلة نحو الشمال") appears at t=11s.
        # Segment 4 phrase ("وهنا ظهر الحصن الكبير") appears at t=20s.
        transcript_pairs = [
            ("بِسْمِ", 0.0, 0.4), ("اللَّهِ", 0.4, 0.8), ("نَبْدَأُ", 0.8, 1.3), ("القِصَّةَ", 1.3, 1.9),
            ("التَّارِيخِيَّةَ", 1.9, 2.5), ("اليَوْمَ", 2.5, 3.2),
            # Seg 2 at t=4.5s (with slight typo المدينة -> المدينه, تاء مربوطة)
            ("فِي", 4.5, 4.8), ("هَذِهِ", 4.8, 5.2), ("المَدِينَةِ", 5.2, 5.8), ("القَدِيمَةِ", 5.8, 6.5),
            ("كَانَ", 6.5, 6.9), ("النَّاسُ", 6.9, 7.5), ("يَعِيشُونَ", 7.5, 8.2), ("بِسَلَامٍ", 8.2, 9.0),
            # Seg 3 at t=11.0s
            ("ثُمَّ", 11.0, 11.4), ("انْطَلَقَتِ", 11.4, 12.1), ("القَافِلَةُ", 12.1, 12.9), ("نَحْوَ", 12.9, 13.4),
            ("الشَّمَالِ", 13.4, 14.2), ("البَعِيدِ", 14.2, 15.0),
            # Seg 4 at t=20.0s
            ("وَهُنَا", 20.0, 20.5), ("ظَهَرَ", 20.5, 21.0), ("الحِصْنُ", 21.0, 21.7), ("الكَبِيرُ", 21.7, 22.5),
            # Trap at t=26.0s repeating Seg 2's phrase!
            ("فِي", 26.0, 26.3), ("هَذِهِ", 26.3, 26.7), ("المَدِينَةِ", 26.7, 27.3), ("القَدِيمَةِ", 27.3, 28.0),
            # Seg 5 at t=30.0s
            ("وَأَخِيرًا", 30.0, 30.6), ("عَادَ", 30.6, 31.1), ("المُسَافِرُ", 31.1, 31.9), ("إِلَى", 31.9, 32.3),
            ("بَيْتِهِ", 32.3, 33.0),
        ]
        transcript = {
            "duration": 35.0,
            "words": [
                {"word": w, "start": s, "end": e, "probability": 0.95}
                for w, s, e in transcript_pairs
            ],
        }
        segments = [
            {"id": 1, "text": "بسم الله نبدا القصه التاريخيه"},
            {"id": 2, "text": "في هذه المدينه القديمه كان الناس"},
            {"id": 3, "text": "ثم انطلقت القافله نحو الشمال"},  # typo test: القافله vs القافلة
            {"id": 4, "text": "وهنا ظهر الحصن الكبير"},
            {"id": 5, "text": "واخيرا عاد المسافر الي بيته"},
        ]

        res = match_segments_to_transcript(segments, transcript, audio_duration=35.0)
        cuts = res["segments"]
        self.assertEqual(len(cuts), 5)
        self.assertEqual(cuts[0]["start"], 0.0)
        # Seg 2 must match at ~4.5s, NOT jump to the trap at 26.0s!
        self.assertAlmostEqual(cuts[1]["start"], 4.5, delta=0.2)
        self.assertAlmostEqual(cuts[2]["start"], 11.0, delta=0.2)
        self.assertAlmostEqual(cuts[3]["start"], 20.0, delta=0.2)
        self.assertAlmostEqual(cuts[4]["start"], 30.0, delta=0.2)
        self.assertEqual(cuts[-1]["end"], 35.0)

        # Strictly monotonic and contiguous
        for i in range(len(cuts) - 1):
            self.assertLess(cuts[i]["start"], cuts[i + 1]["start"])
            self.assertEqual(cuts[i]["end"], cuts[i + 1]["start"])

        # Editing start time updates previous segment's end
        update_cut_start_time(res, 3, 12.2)
        self.assertEqual(res["segments"][2]["start"], 12.2)
        self.assertEqual(res["segments"][1]["end"], 12.2)
        self.assertAlmostEqual(res["segments"][1]["duration"], 12.2 - 4.5, delta=0.01)


if __name__ == "__main__":
    unittest.main()
