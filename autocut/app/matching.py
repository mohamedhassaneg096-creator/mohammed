"""Text parsing, Arabic/English normalization, and monotonic fuzzy segment matching."""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from rapidfuzz import fuzz as _rf_fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    _rf_fuzz = None
    HAS_RAPIDFUZZ = False

# Arabic diacritics, harakat, Quranic marks, and tatweel
_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")

_DIGIT_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC_CHAR_TRANS = str.maketrans({
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ى": "ي",
    "ة": "ه",
    "ؤ": "و",
    "ئ": "ي",
})

# Matches: "1 text", "1. text", "1- text", "1) text", "[1] text", "1: text", "١. text"
_SEGMENT_LINE_RE = re.compile(
    r"^\s*\[?\s*([0-9٠-٩۰-۹]+)\s*\]?\s*(?:[.\-:)\]]\s*|\s+)(.*)$"
)


def normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANS)


def normalize_text(text: str) -> str:
    """Normalize Arabic and Latin text for robust fuzzy transcript matching."""
    if not text:
        return ""
    s = normalize_digits(text)
    s = _ARABIC_DIACRITICS_RE.sub("", s)
    s = s.translate(_ARABIC_CHAR_TRANS)
    s = s.lower()
    s = _PUNCTUATION_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def normalize_words(text: str) -> List[str]:
    norm = normalize_text(text)
    return norm.split() if norm else []


def parse_numbered_segments(raw_text: str) -> Dict[str, Any]:
    """
    Parse numbered plain-text lines tolerantly.
    Returns {segments: [{id, text}], warnings: [str]}.
    """
    segments: List[Dict[str, Any]] = []
    warnings: List[str] = []
    seen_ids: set[int] = set()
    duplicates: List[int] = []

    for line_no, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        m = _SEGMENT_LINE_RE.match(line)
        if m:
            num_str = normalize_digits(m.group(1))
            seg_id = int(num_str)
            body = m.group(2).strip()
            if seg_id in seen_ids:
                duplicates.append(seg_id)
            seen_ids.add(seg_id)
            segments.append({"id": seg_id, "text": body, "line": line_no})
        else:
            if segments:
                prev_text = segments[-1]["text"]
                segments[-1]["text"] = f"{prev_text} {line}".strip()
            else:
                warnings.append(f"السطر {line_no} لا يبدأ برقم وتم تجاهله: «{line[:40]}»")

    if not segments:
        warnings.append("لم يتم العثور على أي مقاطع مرقمة.")
        return {"segments": [], "warnings": warnings}

    # Check ascending order, duplicates, and gaps
    if duplicates:
        dup_str = "، ".join(str(d) for d in sorted(set(duplicates)))
        warnings.append(f"أرقام مقاطع مكررة: {dup_str}")

    non_ascending = []
    gaps = []
    for idx in range(1, len(segments)):
        prev_id = segments[idx - 1]["id"]
        curr_id = segments[idx]["id"]
        if curr_id <= prev_id:
            non_ascending.append(f"{prev_id} → {curr_id}")
        elif curr_id > prev_id + 1:
            missing_range = list(range(prev_id + 1, curr_id))
            gaps.extend(missing_range)

    if segments[0]["id"] > 1:
        gaps = list(range(1, segments[0]["id"])) + gaps

    if non_ascending:
        warnings.append("الترقيم غير تصاعدي عند: " + "، ".join(non_ascending))
    if gaps:
        preview = "، ".join(str(g) for g in gaps[:12])
        suffix = "..." if len(gaps) > 12 else ""
        warnings.append(f"فجوات في الترقيم (أرقام مفقودة): {preview}{suffix}")

    empty_text_ids = [s["id"] for s in segments if not s["text"].strip()]
    if empty_text_ids:
        warnings.append("مقاطع بدون نص: " + "، ".join(str(i) for i in empty_text_ids[:10]))

    return {"segments": segments, "warnings": warnings}


def _similarity(a: str, b: str) -> float:
    """Return 0..100 similarity score between two normalized strings."""
    if not a or not b:
        return 0.0
    if HAS_RAPIDFUZZ:
        # Combine ratio & token_sort/partial resilience for slight OCR/whisper word splits
        r1 = float(_rf_fuzz.ratio(a, b))
        r2 = float(_rf_fuzz.partial_ratio(a, b)) if len(a) >= 8 and len(b) >= 8 else r1
        return 0.7 * r1 + 0.3 * r2
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


def _best_window_match(
    query_words: List[str],
    transcript_norm_words: List[str],
    search_start: int,
    search_end: int,
) -> Tuple[Optional[int], int, float]:
    """
    Slide a window over transcript_norm_words[search_start:search_end]
    to find the best alignment for query_words.
    Returns (best_start_idx, best_len, best_score).
    """
    n_q = len(query_words)
    n_t = len(transcript_norm_words)
    if n_q == 0 or search_start >= n_t or search_start >= search_end:
        return None, 0, 0.0

    query_str = " ".join(query_words)
    first_q = query_words[0]
    best_score = -1.0
    best_idx: Optional[int] = None
    best_len = n_q

    # Allow slight length variance (+/- 1 word) because Whisper sometimes splits/merges words
    len_candidates = sorted({max(1, n_q - 1), n_q, min(n_q + 1, n_t)})

    limit = min(search_end, n_t)
    for pos in range(search_start, limit):
        # Quick first-word bonus check to break ties cleanly
        w_pos = transcript_norm_words[pos]
        anchor_sim = _similarity(first_q, w_pos) if len(first_q) >= 2 else 50.0

        for wlen in len_candidates:
            if pos + wlen > n_t:
                continue
            cand_str = " ".join(transcript_norm_words[pos : pos + wlen])
            sim = _similarity(query_str, cand_str)
            # Slight bonus when the opening word aligns closely, and slight preference for earlier hits
            score = min(100.0, sim * 0.9 + anchor_sim * 0.1)
            if score > best_score + 0.4:  # bias toward earliest match on near-ties
                best_score = score
                best_idx = pos
                best_len = wlen

    if best_idx is None:
        return None, 0, 0.0
    return best_idx, best_len, round(best_score, 1)


def match_segments_to_transcript(
    segments: List[Dict[str, Any]],
    transcript: Dict[str, Any],
    audio_duration: Optional[float] = None,
    opening_word_count: int = 8,
    local_window_words: int = 85,
    good_score_threshold: float = 68.0,
    min_match_threshold: float = 50.0,
) -> Dict[str, Any]:
    """
    Match numbered segments monotonically against transcript words.
    1) Compute all cut points first in strictly non-decreasing order.
    2) Assign segment numbers 1..N after cuts are computed.
    """
    words: List[Dict[str, Any]] = transcript.get("words") or []
    if audio_duration is None or audio_duration <= 0:
        if words:
            audio_duration = float(words[-1].get("end") or words[-1].get("start") or 0.0)
        else:
            audio_duration = float(transcript.get("duration") or 0.0)

    if not segments:
        return {"audio_duration": audio_duration, "segments": []}

    norm_t_words = [normalize_text(str(w.get("word", ""))) for w in words]
    # Filter empty normalized tokens while keeping original index mapping
    indexed_tokens: List[Tuple[int, str]] = [
        (i, tok) for i, tok in enumerate(norm_t_words) if tok
    ]
    tok_list = [tok for _, tok in indexed_tokens]
    n_tok = len(tok_list)

    raw_cuts: List[Dict[str, Any]] = []
    cursor_tok = 0  # Strictly advancing token cursor

    for seg_pos, seg in enumerate(segments):
        q_words = normalize_words(seg.get("text", ""))[:opening_word_count]

        if seg_pos == 0:
            # Segment 1 always starts at 0.0 (Section 3)
            matched_text = ""
            conf = 100.0 if n_tok > 0 else 0.0
            matched = True
            if n_tok > 0 and q_words:
                idx, wlen, score = _best_window_match(q_words, tok_list, 0, min(n_tok, 25))
                if idx is not None and score >= min_match_threshold:
                    orig_start = indexed_tokens[idx][0]
                    orig_end = indexed_tokens[min(n_tok - 1, idx + wlen - 1)][0]
                    matched_text = " ".join(str(words[i].get("word", "")).strip() for i in range(orig_start, orig_end + 1))
                    conf = score
                    cursor_tok = idx + 1
                else:
                    orig_end = min(len(words) - 1, max(0, len(q_words) - 1))
                    matched_text = " ".join(str(words[i].get("word", "")).strip() for i in range(0, orig_end + 1))
                    conf = score if idx is not None else 60.0
                    cursor_tok = 1
            raw_cuts.append({
                "start": 0.0,
                "matched_text": matched_text,
                "confidence": round(conf, 1),
                "matched": matched,
                "token_index": 0,
            })
            continue

        if not q_words or cursor_tok >= n_tok:
            prev_start = raw_cuts[-1]["start"]
            fallback_start = min(audio_duration, prev_start + 0.5)
            raw_cuts.append({
                "start": round(fallback_start, 3),
                "matched_text": "",
                "confidence": 0.0,
                "matched": False,
                "token_index": cursor_tok,
            })
            continue

        # 1. Search local forward window first (prevents jumping to repeated phrases much later)
        local_end = min(n_tok, cursor_tok + local_window_words)
        best_idx, best_len, best_score = _best_window_match(q_words, tok_list, cursor_tok, local_end)

        # 2. Widen forward if local window wasn't confident enough
        if best_idx is None or best_score < good_score_threshold:
            wide_idx, wide_len, wide_score = _best_window_match(q_words, tok_list, cursor_tok, n_tok)
            if wide_idx is not None and wide_score > best_score + 6.0:
                best_idx, best_len, best_score = wide_idx, wide_len, wide_score

        if best_idx is not None and best_score >= min_match_threshold:
            orig_start_idx = indexed_tokens[best_idx][0]
            orig_end_idx = indexed_tokens[min(n_tok - 1, best_idx + best_len - 1)][0]
            start_time = float(words[orig_start_idx]["start"])

            # Guarantee strict monotonic non-decreasing time
            prev_start = raw_cuts[-1]["start"]
            if start_time <= prev_start:
                start_time = min(audio_duration, prev_start + 0.05)

            matched_text = " ".join(
                str(words[i].get("word", "")).strip()
                for i in range(orig_start_idx, orig_end_idx + 1)
            )
            cursor_tok = best_idx + 1  # Strictly advance after matched start
            raw_cuts.append({
                "start": round(start_time, 3),
                "matched_text": matched_text,
                "confidence": round(best_score, 1),
                "matched": True,
                "token_index": best_idx,
            })
        else:
            # Unmatched ("غير متطابق"): place safely after previous start without skipping ahead
            prev_start = raw_cuts[-1]["start"]
            remaining_segs = max(1, len(segments) - seg_pos)
            remaining_audio = max(0.2, audio_duration - prev_start)
            est_step = min(2.0, remaining_audio / (remaining_segs + 1))
            fallback_start = round(min(audio_duration - 0.1 * remaining_segs, prev_start + max(0.2, est_step)), 3)
            raw_cuts.append({
                "start": max(prev_start + 0.05, fallback_start),
                "matched_text": "",
                "confidence": round(max(0.0, best_score), 1),
                "matched": False,
                "token_index": cursor_tok,
            })

    # Compute contiguous end times across all cuts, THEN assign segment numbers
    if raw_cuts:
        raw_cuts[0]["start"] = 0.0

    # Ensure strictly increasing start times clamped within [0, audio_duration)
    n_cuts = len(raw_cuts)
    for idx in range(1, n_cuts):
        min_allowed = raw_cuts[idx - 1]["start"] + 0.04
        max_allowed = max(min_allowed, audio_duration - 0.04 * (n_cuts - idx))
        if raw_cuts[idx]["start"] < min_allowed:
            raw_cuts[idx]["start"] = round(min(min_allowed, max_allowed), 3)
        elif raw_cuts[idx]["start"] > max_allowed:
            raw_cuts[idx]["start"] = round(max_allowed, 3)

    numbered_segments: List[Dict[str, Any]] = []
    for idx, cut in enumerate(raw_cuts):
        end_time = raw_cuts[idx + 1]["start"] if idx + 1 < n_cuts else audio_duration
        end_time = round(max(cut["start"] + 0.04, end_time), 3)
        start_time = round(cut["start"], 3)
        seg = segments[idx]
        numbered_segments.append({
            "id": int(seg["id"]),
            "text": seg["text"],
            "matched_text": cut["matched_text"],
            "start": start_time,
            "end": end_time,
            "duration": round(end_time - start_time, 3),
            "confidence": cut["confidence"],
            "matched": cut["matched"],
            "edited": False,
        })

    return {
        "audio_duration": round(float(audio_duration), 3),
        "segments": numbered_segments,
    }


def update_cut_start_time(
    cuts: Dict[str, Any],
    segment_id: int,
    new_start: float,
) -> Dict[str, Any]:
    """
    Update a segment's start time and automatically adjust the previous segment's end time.
    Clamps within valid neighbor bounds.
    """
    segs = cuts.get("segments") or []
    audio_duration = float(cuts.get("audio_duration") or 0.0)
    idx = next((i for i, s in enumerate(segs) if int(s["id"]) == int(segment_id)), None)
    if idx is None:
        raise KeyError(f"Segment {segment_id} not found")

    if idx == 0:
        # Segment 1 always starts at 0.0
        clamped = 0.0
    else:
        min_t = float(segs[idx - 1]["start"]) + 0.05
        max_t = (float(segs[idx + 1]["start"]) - 0.05) if idx + 1 < len(segs) else max(min_t, audio_duration - 0.05)
        clamped = round(max(min_t, min(max_t, float(new_start))), 3)

    segs[idx]["start"] = clamped
    segs[idx]["duration"] = round(float(segs[idx]["end"]) - clamped, 3)
    segs[idx]["edited"] = True
    segs[idx]["matched"] = True

    if idx > 0:
        segs[idx - 1]["end"] = clamped
        segs[idx - 1]["duration"] = round(clamped - float(segs[idx - 1]["start"]), 3)

    return cuts
