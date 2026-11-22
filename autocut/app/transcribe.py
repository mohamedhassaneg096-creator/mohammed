"""CPU transcription via faster-whisper with word timestamps and SHA-256 caching."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .project import file_sha256, load_transcript, logger, save_transcript


def probe_media_duration(media_path: Path) -> float:
    """Return media duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(media_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media_path}: {res.stderr.strip()}")
    data = json.loads(res.stdout or "{}")
    dur = float((data.get("format") or {}).get("duration") or 0.0)
    return dur


def transcribe_audio(
    project_id: str,
    audio_path: Path,
    model_name: str = "medium",
    language: str = "auto",
    force: bool = False,
    progress_callback: Optional[Callable[[float, float, Optional[float], str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    projects_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Transcribe audio on CPU using faster-whisper (int8, word_timestamps=True).
    Reuses cached project/transcript.json if audio hash, model, and language match.
    """
    audio_hash = file_sha256(audio_path)
    duration = probe_media_duration(audio_path)

    if not force:
        cached = load_transcript(project_id, projects_root)
        if (
            cached
            and cached.get("audio_hash") == audio_hash
            and cached.get("model") == model_name
            and cached.get("language_setting") == language
            and cached.get("words") is not None
        ):
            logger.info("Reusing cached transcript for project %s", project_id)
            if progress_callback:
                preview_words = " ".join(w["word"] for w in cached["words"][:18])
                progress_callback(100.0, 0.0, 0.0, preview_words)
            return cached

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "مكتبة faster-whisper غير مثبتة. يرجى تشغيل install.bat لتثبيت المتطلبات."
        ) from exc

    t0 = time.monotonic()
    if progress_callback:
        progress_callback(2.0, 0.0, None, "جاري تحميل نموذج التفريغ على المعالج...")

    logger.info("Loading faster-whisper model=%s compute_type=int8 on CPU", model_name)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    lang_arg = None if (not language or language == "auto") else language
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=lang_arg,
        word_timestamps=True,
        vad_filter=True,
    )

    total_duration = float(getattr(info, "duration", 0.0) or duration or 1.0)
    words_out: List[Dict[str, Any]] = []
    segments_out: List[Dict[str, Any]] = []

    for seg in segments_iter:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("تم إلغاء عملية التفريغ بناءً على طلب المستخدم.")

        seg_text = (seg.text or "").strip()
        segments_out.append({
            "start": round(float(seg.start), 3),
            "end": round(float(seg.end), 3),
            "text": seg_text,
        })

        if getattr(seg, "words", None):
            for w in seg.words:
                w_str = (w.word or "").strip()
                if not w_str:
                    continue
                words_out.append({
                    "word": w_str,
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "probability": round(float(getattr(w, "probability", 1.0) or 1.0), 3),
                })
        elif seg_text:
            # Fallback if a segment yields no word list
            split_words = seg_text.split()
            span = max(0.05, float(seg.end) - float(seg.start))
            step = span / max(1, len(split_words))
            for idx, sw in enumerate(split_words):
                ws = float(seg.start) + idx * step
                we = min(float(seg.end), ws + step)
                words_out.append({
                    "word": sw,
                    "start": round(ws, 3),
                    "end": round(we, 3),
                    "probability": 0.85,
                })

        elapsed = time.monotonic() - t0
        pct = min(98.0, max(5.0, (float(seg.end) / total_duration) * 100.0))
        eta = (elapsed * (100.0 - pct) / pct) if pct > 5.0 else None
        recent_preview = " ".join(w["word"] for w in words_out[-16:])
        if progress_callback:
            progress_callback(round(pct, 1), round(elapsed, 1), round(eta, 1) if eta is not None else None, recent_preview)

    transcript = {
        "audio_hash": audio_hash,
        "model": model_name,
        "language_setting": language,
        "detected_language": getattr(info, "language", language),
        "duration": round(duration or total_duration, 3),
        "words": words_out,
        "segments": segments_out,
    }
    save_transcript(project_id, transcript, projects_root)

    elapsed_total = time.monotonic() - t0
    if progress_callback:
        progress_callback(100.0, round(elapsed_total, 1), 0.0, " ".join(w["word"] for w in words_out[-16:]))

    return transcript
