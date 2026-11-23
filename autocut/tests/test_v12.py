"""Tests for AutoCut v1.2.0: external transcript import, parsers, quick folder, demo project, WCAG AA themes, hygiene, updater."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.matching import match_segments_to_transcript, parse_numbered_segments
from app.project import BASE_DIR, DEFAULT_SETTINGS, load_project, save_cuts
from app.render import export_timeline_video
from app.scenes import probe_scene_media
from app.timeline import build_timeline
from app.transcript_import import (
    TranscriptImportError,
    TranscriptNoTimestampsError,
    TranscriptUnknownFormatError,
    apply_ignored_segments,
    parse_and_normalize_transcript,
)
from app.updater import plan_github_update, stage_github_update
from app.updater_helper import apply_staged_update, diff_requirements
from app.workspace_helpers import apply_quick_folder, create_demo_project, find_companion_files
from tests.test_updater import MockGitHubFixture


VIBE_SRT_FIXTURE = """1
00:00:00,000 --> 00:00:04,640
"In the year 874, a few shiploads of farmers stepped onto

2
00:00:04,640 --> 00:00:12,080
an island of ice and volcanic rock. Behind them lay green valleys, tall forests, and the graves of

32
00:02:55,200 --> 00:03:00,400
but it was warm enough to stay alive. Clue number three was on their backs: wool.

48
00:04:25,200 --> 00:04:29,680
In fact, medieval Icelandic law spent careful pages deciding who owned one.

49
00:04:30,240 --> 00:04:34,480
In fact, medieval Icelandic law spent careful pages deciding who owned one.

101
00:09:43,600 --> 00:09:45,600
أن تفيد المترجم للغاية.

102
00:09:45,600 --> 00:09:47,600
أن تفيد المترجم للغاية.
"""


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rel_luminance(rgb: tuple[int, int, int]) -> float:
    def ch(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = ch(rgb[0]), ch(rgb[1]), ch(rgb[2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hex1: str, hex2: str) -> float:
    l1 = _rel_luminance(_hex_to_rgb(hex1))
    l2 = _rel_luminance(_hex_to_rgb(hex2))
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


class V12Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_obj.name)

    def tearDown(self) -> None:
        self.tmp_obj.cleanup()

    def test_vibe_srt_fixture_estimation_duplicates_script_and_leading_quote(self) -> None:
        tr = parse_and_normalize_transcript(
            VIBE_SRT_FIXTURE,
            audio_duration=590.0,
            filename="iceland_vibe.srt",
        )
        self.assertEqual(tr["format"], "srt")
        self.assertEqual(tr["precision"], "sentence")
        self.assertIn("SRT — توقيت الجمل", tr["format_label"])
        self.assertIn("Word timestamps", tr["precision_hint"])

        segs = tr["segments"]
        self.assertEqual(len(segs), 7)

        # Entry 1 keeps its leading quote without breaking matching
        self.assertTrue(segs[0]["text"].startswith('"In the year 874'))
        self.assertEqual(segs[0]["words"][0]["text"], '"In')

        # Entries 48/49 (indices 3, 4 in list) flagged as duplicate
        self.assertIn("duplicate", segs[3]["suspicious_reasons"])
        self.assertIn("duplicate", segs[4]["suspicious_reasons"])

        # Entries 101/102 (indices 5, 6 in list) flagged as different_script trailing junk
        self.assertIn("different_script", segs[5]["suspicious_reasons"])
        self.assertIn("different_script", segs[6]["suspicious_reasons"])

        # Ignore duplicates/junk non-destructively (indices 5, 6, 7 in 1-based segment index)
        updated = apply_ignored_segments(tr, [5, 6, 7])
        self.assertEqual(len(updated["segments"]), 7)  # Kept in file
        self.assertTrue(updated["segments"][4]["ignored"])
        self.assertTrue(updated["segments"][5]["ignored"])
        self.assertTrue(updated["segments"][6]["ignored"])
        self.assertEqual(updated["stats"]["ignored_count"], 3)

        # Match numbered text: line 1 starts with "In the year 874", line 2 starts mid-line at "Clue number three..."
        script = (
            "1. In the year 874, a few shiploads of farmers stepped onto an island of ice.\n"
            "2. Clue number three was on their backs: wool.\n"
            "3. In fact, medieval Icelandic law spent careful pages deciding who owned one.\n"
        )
        parsed = parse_numbered_segments(script)
        cuts = match_segments_to_transcript(parsed["segments"], updated, audio_duration=590.0)
        c_segs = cuts["segments"]
        self.assertEqual(len(c_segs), 3)
        self.assertTrue(c_segs[0]["matched"])
        self.assertEqual(c_segs[0]["start"], 0.0)

        # Line 2 matches INSIDE entry 32 (00:02:55,200 = 175.2s .. 00:03:00,400 = 180.4s) with "تقدير" badge
        self.assertTrue(c_segs[1]["matched"])
        self.assertGreater(c_segs[1]["start"], 175.2)
        self.assertLess(c_segs[1]["start"], 180.4)
        self.assertTrue(c_segs[1]["estimated"])
        self.assertEqual(c_segs[1]["badge"], "تقدير")

        # Line 3 matches entry 48 (265.2s), and NOT ignored lines 49/101/102
        self.assertTrue(c_segs[2]["matched"])
        self.assertAlmostEqual(c_segs[2]["start"], 265.2, delta=0.1)

    def test_no_timestamps_and_validation_rejections(self) -> None:
        # JSON without any timestamps
        no_ts = json.dumps({"transcript": [{"text": "Hello world"}, {"text": "Second line"}]})
        with self.assertRaises(TranscriptNoTimestampsError) as ctx:
            parse_and_normalize_transcript(no_ts, audio_duration=10.0, filename="vibe.json")
        self.assertEqual(ctx.exception.code, "no_timestamps")
        self.assertIn("Word timestamps", ctx.exception.hint)

        # Unknown JSON shape -> first 30 lines preview + "الصيغة غير معروفة"
        unknown = json.dumps({"foo": {"bar": 123, "baz": [1, 2, 3]}}, indent=2)
        with self.assertRaises(TranscriptUnknownFormatError) as ctx2:
            parse_and_normalize_transcript(unknown, audio_duration=10.0, filename="weird.json")
        self.assertIn("الصيغة غير معروفة", ctx2.exception.message)
        self.assertLessEqual(len(ctx2.exception.preview_lines), 30)
        self.assertGreater(len(ctx2.exception.preview_lines), 0)

        # Non-increasing timestamps rejected
        bad_order = "1\n00:00:05,000 --> 00:00:07,000\nFirst\n\n2\n00:00:01,000 --> 00:00:02,000\nSecond\n"
        with self.assertRaises(TranscriptImportError):
            parse_and_normalize_transcript(bad_order, audio_duration=15.0)

        # Exceeds audio_duration + 2s rejected
        too_long = "1\n00:00:00,000 --> 00:00:25,000\nToo long line\n"
        with self.assertRaises(TranscriptImportError):
            parse_and_normalize_transcript(too_long, audio_duration=10.0)

        # Unreadable binary rejected
        with self.assertRaises(TranscriptImportError):
            parse_and_normalize_transcript(b"\x00\x01\xff\xfePNG", audio_duration=10.0, filename="bad.json")

    def test_cross_format_equivalence_word_normalization(self) -> None:
        expected = [
            ("Hello", 0.0, 0.5),
            ("world", 0.5, 1.0),
            ("again", 1.0, 1.6),
        ]

        # 1. whisper.cpp JSON with offsets (ms) & BPE sub-word tokens + special tokens
        wcpp = {
            "transcription": [
                {
                    "offsets": {"from": 0, "to": 1600},
                    "text": " Hello world again",
                    "tokens": [
                        {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}},
                        {"text": " Hel", "offsets": {"from": 0, "to": 250}},
                        {"text": "lo", "offsets": {"from": 250, "to": 500}},
                        {"text": " world", "offsets": {"from": 500, "to": 1000}},
                        {"text": " again", "offsets": {"from": 1000, "to": 1600}},
                        {"text": "[_TT_80]", "offsets": {"from": 1600, "to": 1600}},
                        {"text": "<|endoftext|>", "offsets": {"from": 1600, "to": 1600}},
                    ],
                }
            ]
        }

        # 2. OpenAI Whisper JSON segments[].words[]
        openai_json = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.6,
                    "text": "Hello world again",
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.5},
                        {"word": "world", "start": 0.5, "end": 1.0},
                        {"word": "again", "start": 1.0, "end": 1.6},
                    ],
                }
            ]
        }

        # 3. Flat word-level JSON in seconds
        flat_json = {
            "words": [
                {"text": "Hello", "start": 0.0, "end": 0.5},
                {"text": "world", "start": 0.5, "end": 1.0},
                {"text": "again", "start": 1.0, "end": 1.6},
            ]
        }

        # 4. VTT without header + inline <time> word tags
        vtt_no_header = (
            "00:00.000 --> 00:01.600\n"
            "Hello<00:00.500> world<00:01.000> again\n"
        )

        # 5. SRT with '.' millisecond separator & no blank lines
        srt_dot = (
            "1\n00:00:00.000 --> 00:00:00.500\nHello\n"
            "2\n00:00:00.500 --> 00:00:01.000\nworld\n"
            "3\n00:00:01.000 --> 00:00:01.600\nagain\n"
        )

        # Normalize with UTF-8 BOM prefix on one input too
        raw_inputs = [
            ("\ufeff" + json.dumps(wcpp), "wcpp.json"),
            (json.dumps(openai_json), "openai.json"),
            (json.dumps(flat_json), "flat.json"),
            (vtt_no_header, "inline.vtt"),
        ]
        for raw, fn in raw_inputs:
            res = parse_and_normalize_transcript(raw, audio_duration=3.0, filename=fn)
            self.assertEqual(res["precision"], "word", msg=f"Expected word precision for {fn}")
            got = [(w["text"], round(w["start"], 2), round(w["end"], 2)) for w in res["words"]]
            self.assertEqual(got, expected, msg=f"Word normalization mismatch for {fn}")

        # SRT cue-level dot-ms check (3 distinct segments map to exact same start/end)
        res_srt = parse_and_normalize_transcript(srt_dot, audio_duration=3.0, filename="dot.srt")
        self.assertEqual(
            [(w["text"], round(w["start"], 2), round(w["end"], 2)) for w in res_srt["words"]],
            expected,
        )

    def test_suspicious_different_script_zero_dur_and_long_silence(self) -> None:
        data = {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "في قلب الأندلس بدأت حضارة عظيمة"},
                {"start": 2.0, "end": 2.0, "text": "قرطبة"},  # zero_duration
                {"start": 8.5, "end": 9.2, "text": "اشتركوا في القناة"},  # long_silence (>=4s gap & <=2.2s dur)
                {"start": 9.5, "end": 10.5, "text": "Thanks for watching"},  # different_script
            ]
        }
        res = parse_and_normalize_transcript(json.dumps(data, ensure_ascii=False), audio_duration=11.0, filename="ar.json")
        segs = res["segments"]
        self.assertIn("zero_duration", segs[1]["suspicious_reasons"])
        self.assertIn("long_silence", segs[2]["suspicious_reasons"])
        self.assertIn("different_script", segs[3]["suspicious_reasons"])

    def test_quick_folder_companion_and_demo_pipeline(self) -> None:
        # 1. Build a quick folder containing audio, transcript.json, script.txt, scenes/
        qdir = self.tmp / "quick_pack"
        (qdir / "scenes").mkdir(parents=True)
        # Generate small WAV + numbered PNGs using demo helpers via workspace folder
        from app.workspace_helpers import _write_demo_png, _write_demo_wav

        audio_file = _write_demo_wav(qdir / "story.wav", duration_s=4.0)
        (qdir / "story.json").write_text(
            json.dumps({
                "words": [
                    {"word": "First", "start": 0.0, "end": 0.8},
                    {"word": "scene", "start": 0.8, "end": 1.8},
                    {"word": "Second", "start": 1.8, "end": 2.8},
                    {"word": "scene", "start": 2.8, "end": 4.0},
                ]
            }),
            encoding="utf-8",
        )
        (qdir / "script.txt").write_text("1. First scene\n2. Second scene\n", encoding="utf-8")
        _write_demo_png(qdir / "scenes" / "1.png", 1, (30, 50, 80), "S1")
        _write_demo_png(qdir / "scenes" / "2.png", 2, (40, 80, 70), "S2")

        # Companion detection check
        comp = find_companion_files(audio_file)
        self.assertEqual(comp["transcript_name"], "story.json")
        self.assertEqual(comp["script_name"], "script.txt")

        # Quick folder fill check
        proj_root = self.tmp / "projects"
        demo_meta = create_demo_project(projects_root=proj_root)
        pid = demo_meta["id"]
        self.assertEqual(demo_meta["audio_filename"], "andalus_demo.wav")
        self.assertEqual(demo_meta["transcript_filename"], "andalus_demo.json")
        self.assertEqual(len(demo_meta["scenes_scan"]["scenes"]), 5)

        # Full pipeline on demo project: timeline + export + ffprobe verification
        cuts_file = proj_root / pid / "cuts.json"
        cuts = json.loads(cuts_file.read_text(encoding="utf-8"))
        tl = build_timeline(cuts, demo_meta["scenes_scan"]["scenes"], fps=24)
        save_cuts(pid, cuts, projects_root=proj_root)
        # Render fast 640x360 preview profile
        fast_settings = dict(demo_meta["settings"], width=640, height=360, fps=24)
        exp_res = export_timeline_video(
            pid,
            audio_path=Path(demo_meta["audio_path"]),
            timeline=tl,
            settings=fast_settings,
            motions_data={"global_strength": 15, "assignments": {"1": {"type": "zoom_in", "x": 0.4, "y": 0.4, "strength": 15, "ease": True}}},
            projects_root=proj_root,
        )
        out_mp4 = Path(exp_res["output_path"])
        self.assertTrue(out_mp4.exists())
        info = probe_scene_media(out_mp4, is_video=True)
        self.assertAlmostEqual(info["duration"], 8.0, delta=0.35)

        # Quick folder on fresh project
        from app.project import create_project
        p2 = create_project("مجلد سريع", projects_root=proj_root)
        qres = apply_quick_folder(p2["id"], folder_path=qdir, projects_root=proj_root)
        self.assertTrue(qres["ok"])
        self.assertEqual(qres["summary"]["audio"]["filename"], "story.wav")
        self.assertEqual(qres["summary"]["transcript"]["filename"], "story.json")
        self.assertEqual(qres["summary"]["script"]["segment_count"], 2)
        self.assertEqual(qres["summary"]["scenes"]["count"], 2)

    def test_theme_wcag_aa_contrast_and_always_blue_dot(self) -> None:
        css_text = (BASE_DIR / "web" / "style.css").read_text(encoding="utf-8")
        self.assertEqual(DEFAULT_SETTINGS["theme"], "blue_night")

        for theme_name in ("blue_night", "calm_forest", "daylight"):
            pattern = rf'\[data-theme="{theme_name}"\]\s*\{{(.*?)\}}'
            m = re.search(pattern, css_text, re.DOTALL)
            self.assertIsNotNone(m, f"Missing CSS theme block for {theme_name}")
            block = m.group(1)
            vars_map = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;", block))

            # Blue focus dot invariant across all 3 themes
            self.assertEqual(vars_map.get("--dot", "").upper(), "#4C8DFF", f"--dot changed in {theme_name}")

            # WCAG AA normal text contrast >= 4.5:1
            pairs = [
                ("--text", "--bg", 4.5),
                ("--text", "--surface", 4.5),
                ("--text", "--surface-2", 4.5),
                ("--text-2", "--surface", 4.5),
                ("--on-accent", "--accent-btn", 4.5),
                ("--success-text", "--surface", 4.5),
                ("--warning-text", "--surface", 4.5),
                ("--danger-text", "--surface", 4.5),
            ]
            for fg_k, bg_k, min_ratio in pairs:
                fg = vars_map[fg_k]
                bg = vars_map[bg_k]
                ratio = _contrast_ratio(fg, bg)
                self.assertGreaterEqual(
                    ratio,
                    min_ratio,
                    f"Contrast {theme_name} {fg_k}({fg}) on {bg_k}({bg}) = {ratio:.2f} < {min_ratio}",
                )

    def test_repo_hygiene_gitignore_requirements_and_v11_to_v12_update(self) -> None:
        req_text = (BASE_DIR / "requirements.txt").read_text(encoding="utf-8").lower()
        for banned in ("faster-whisper", "ctranslate2", "onnxruntime", "huggingface_hub", "tokenizers"):
            self.assertNotIn(banned, req_text, f"{banned} must not exist in requirements.txt")

        install_bat = (BASE_DIR / "install.bat").read_text(encoding="utf-8").lower()
        self.assertNotIn("whisper", install_bat)

        self.assertEqual((BASE_DIR / "VERSION").read_text(encoding="utf-8").strip(), "1.2.0")
        self.assertTrue((BASE_DIR / "settings.example.json").exists())

        gitignore = (BASE_DIR.parent / ".gitignore").read_text(encoding="utf-8")
        for entry in ("projects/", "logs/", "backups/", "staging/", "models/", ".venv/", "settings.json", ".env", "*.pyc", "__pycache__/", "*.mp4", "*.wav"):
            self.assertIn(entry, gitignore)

        # Simulate updating v1.1 -> v1.2 where faster-whisper was removed from requirements.txt
        old_req = "fastapi==0.115.6\nfaster-whisper>=1.0.3\nrapidfuzz==3.10.1\n"
        new_req = "fastapi==0.115.6\nrapidfuzz==3.10.1\n"
        self.assertEqual(diff_requirements(old_req, new_req), [])  # Removed packages are never installed or failed on!

        v11_dir = self.tmp / "v11_copy"
        (v11_dir / "app").mkdir(parents=True)
        (v11_dir / "web").mkdir(parents=True)
        (v11_dir / "projects" / "existing_p1").mkdir(parents=True)
        (v11_dir / ".venv").mkdir(parents=True)
        (v11_dir / "models").mkdir(parents=True)
        (v11_dir / "VERSION").write_text("1.1.0\n", encoding="utf-8")
        (v11_dir / "requirements.txt").write_text(old_req, encoding="utf-8")
        (v11_dir / "app" / "server.py").write_text("# v1.1 server\n", encoding="utf-8")
        (v11_dir / "web" / "index.html").write_text("<html>v1.1</html>\n", encoding="utf-8")
        (v11_dir / "update_manifest.json").write_text(
            (BASE_DIR / "update_manifest.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (v11_dir / "projects" / "existing_p1" / "project.json").write_text('{"id":"existing_p1","name":"Keep"}', encoding="utf-8")
        (v11_dir / ".venv" / "marker.txt").write_text("keep-venv", encoding="utf-8")
        (v11_dir / "models" / "keep.bin").write_text("keep-models", encoding="utf-8")
        settings_f = v11_dir / "settings.json"
        settings_f.write_text(json.dumps({"update_source": {"repo": "owner/autocut", "branch": "main", "subfolder": ""}}), encoding="utf-8")

        remote = {
            "VERSION": b"1.2.0\n",
            "CHANGELOG.md": b"## 1.2.0\n- External transcript import\n",
            "requirements.txt": new_req.encode("utf-8"),
            "app/server.py": b"# v1.2 server\n",
            "web/index.html": b"<html>v1.2</html>\n",
            "update_manifest.json": (BASE_DIR / "update_manifest.json").read_bytes(),
        }
        mock = MockGitHubFixture(remote)
        pip_calls = []
        try:
            plan = plan_github_update(v11_dir, settings_f, api_base=mock.base_url)
            stage_root = stage_github_update(plan, v11_dir, settings_f, api_base=mock.base_url)
            res = apply_staged_update(
                v11_dir,
                stage_root,
                pip_installer=lambda pkgs: (pip_calls.append(pkgs) or True, ""),
                self_test_runner=lambda _b: (True, ""),
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["new_version"], "1.2.0")
            self.assertEqual(pip_calls, [])  # No pip uninstall/fail on removed faster-whisper
            self.assertEqual((v11_dir / ".venv" / "marker.txt").read_text(encoding="utf-8"), "keep-venv")
            self.assertEqual((v11_dir / "models" / "keep.bin").read_text(encoding="utf-8"), "keep-models")
            self.assertTrue((v11_dir / "projects" / "existing_p1" / "project.json").exists())
            self.assertTrue(settings_f.exists())
        finally:
            mock.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
