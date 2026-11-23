"""External transcript importer & normalizer (Vibe JSON, whisper.cpp JSON, OpenAI Whisper JSON, SRT, VTT, generic JSON)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .matching import normalize_text

_HMS_RE = re.compile(
    r"^\s*(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\s*$"
)
_CUE_ARROW_RE = re.compile(
    r"^\s*(\d{1,3}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?\s*-->\s*(\d{1,3}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
)
_INLINE_TAG_TS_RE = re.compile(r"<((?:\d{1,3}:)?\d{1,2}:\d{2}[.,]\d{1,3})>")
_HTML_OR_VTT_TAG_RE = re.compile(r"</?[^>]+>|\{\\[^}]+\}")
_SPECIAL_TOKEN_RE = re.compile(
    r"^(?:\[_(?:BEG_|EOT_|SOT_|TT_\d+|LANG[^\]]*)\]|<\|[^|>]+\|>|\[BLANK_AUDIO\]|\[SILENCE\])$",
    re.IGNORECASE,
)
_ARABIC_CHARS_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_LATIN_CHARS_RE = re.compile(r"[A-Za-z]")


class TranscriptImportError(ValueError):
    """Arabic user-facing error raised on invalid or blocked transcript import."""

    def __init__(self, message: str, hint: str = "", code: str = "import_error", preview_lines: Optional[List[str]] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
        self.preview_lines = preview_lines or []


class TranscriptNoTimestampsError(TranscriptImportError):
    def __init__(self) -> None:
        super().__init__(
            "ملف التفريغ لا يحتوي على أي توقيتات زمنية (Timestamps).",
            hint="في برنامج Vibe: افتح More Options ← فعّل Word timestamps ← صدّر بصيغة JSON (أو استخدم SRT كبديل).",
            code="no_timestamps",
        )


class TranscriptUnknownFormatError(TranscriptImportError):
    def __init__(self, preview_lines: List[str]) -> None:
        super().__init__(
            "الصيغة غير معروفة.",
            hint="الصيغ المدعومة: Vibe JSON / whisper.cpp JSON / OpenAI Whisper JSON / SRT / VTT.",
            code="unknown_format",
            preview_lines=preview_lines[:30],
        )


def parse_timestamp_value(val: Any) -> Optional[float]:
    """Parse HMS string or numeric timestamp (raw, unit conversion happens later)."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        m = _HMS_RE.match(s)
        if m:
            hrs = int(m.group(1) or 0)
            mins = int(m.group(2))
            secs = int(m.group(3))
            ms_str = (m.group(4) or "0").ljust(3, "0")[:3]
            return hrs * 3600.0 + mins * 60.0 + secs + int(ms_str) / 1000.0
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _is_hms_string(val: Any) -> bool:
    return isinstance(val, str) and bool(_HMS_RE.match(val.strip()))


def _extract_pair_timestamps(obj: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], str]:
    """
    Extract (start, end, unit_hint) from a segment or token dict.
    unit_hint is one of: 'ms', 'hms', 'num', ''.
    """
    if not isinstance(obj, dict):
        return None, None, ""

    # 1. Prefer whisper.cpp 'offsets' (always milliseconds)
    offsets = obj.get("offsets")
    if isinstance(offsets, dict) and ("from" in offsets or "to" in offsets or "start" in offsets or "end" in offsets):
        s = parse_timestamp_value(offsets.get("from", offsets.get("start")))
        e = parse_timestamp_value(offsets.get("to", offsets.get("end")))
        if s is not None or e is not None:
            return s, e if e is not None else s, "ms"

    # 2. Nested 'timestamps': {from, to} or {start, end}
    ts_obj = obj.get("timestamps")
    if isinstance(ts_obj, dict):
        raw_s = ts_obj.get("from", ts_obj.get("start"))
        raw_e = ts_obj.get("to", ts_obj.get("end"))
        s = parse_timestamp_value(raw_s)
        e = parse_timestamp_value(raw_e)
        if s is not None or e is not None:
            hint = "hms" if (_is_hms_string(raw_s) or _is_hms_string(raw_e)) else "num"
            return s, e if e is not None else s, hint

    # 3. Flat keys
    key_pairs = [
        ("start", "end"),
        ("from", "to"),
        ("start_time", "end_time"),
        ("begin", "stop"),
        ("t0", "t1"),
    ]
    for k_s, k_e in key_pairs:
        if k_s in obj or k_e in obj:
            raw_s = obj.get(k_s)
            raw_e = obj.get(k_e)
            s = parse_timestamp_value(raw_s)
            e = parse_timestamp_value(raw_e)
            if s is not None or e is not None:
                hint = "hms" if (_is_hms_string(raw_s) or _is_hms_string(raw_e)) else "num"
                return s, e if e is not None else s, hint

    return None, None, ""


def _detect_numeric_scale(raw_nums: List[float], unit_hints: set[str], audio_duration: float) -> Tuple[float, str]:
    """
    Decide multiplier to convert raw timestamps to seconds.
    Returns (scale, unit_label).
    """
    if "ms" in unit_hints:
        return 0.001, "milliseconds"
    if not raw_nums:
        if "hms" in unit_hints:
            return 1.0, "hms"
        return 1.0, "seconds"
    if "hms" in unit_hints and "num" not in unit_hints:
        return 1.0, "hms"

    max_v = max(raw_nums)
    min_v = min(raw_nums)
    all_int_like = all(abs(v - round(v)) < 1e-6 for v in raw_nums)

    if audio_duration and audio_duration > 0:
        if max_v > audio_duration * 2.5 + 2.0:
            # Check if dividing by 1000 yields plausible timestamps within audio duration
            if (max_v / 1000.0) <= audio_duration * 1.5 + 5.0:
                return 0.001, "milliseconds"
    else:
        if all_int_like and max_v >= 1000.0 and (max_v - min_v) >= 500.0:
            return 0.001, "milliseconds"

    return 1.0, "seconds"


def _distribute_words_proportionally(seg_text: str, start: float, end: float, seg_index: int) -> List[Dict[str, Any]]:
    """Distribute segment duration across words proportionally to character count (estimated=True)."""
    raw_words = [w for w in seg_text.strip().split() if w]
    if not raw_words:
        return []
    span = max(0.0, end - start)
    weights = [max(1, len(re.sub(r"[^\w\u0600-\u06FF]", "", w)) or len(w)) for w in raw_words]
    total_w = float(sum(weights)) or float(len(raw_words))

    words_out: List[Dict[str, Any]] = []
    cur_t = start
    for idx, (w_txt, weight) in enumerate(zip(raw_words, weights)):
        dur = span * (weight / total_w)
        w_start = round(cur_t, 3)
        w_end = round(end if idx == len(raw_words) - 1 else cur_t + dur, 3)
        words_out.append({
            "text": w_txt,
            "start": w_start,
            "end": max(w_start, w_end),
            "estimated": True,
            "segment_index": seg_index,
            "word_index_in_segment": idx,
            "ignored": False,
        })
        cur_t += dur
    return words_out


def _merge_subword_tokens(raw_tokens: List[Dict[str, Any]], scale: float, seg_index: int, is_bpe: bool) -> List[Dict[str, Any]]:
    """
    Convert segment token/word objects into normalized word entries.
    When is_bpe is True (whisper.cpp `tokens`), merges sub-word pieces by leading whitespace
    and skips specials ([_BEG_], [_TT_*], <|...|>, [BLANK_AUDIO]).
    """
    merged: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None

    for tok in raw_tokens:
        if not isinstance(tok, dict):
            continue
        raw_txt = str(tok.get("text") if tok.get("text") is not None else tok.get("word") if tok.get("word") is not None else tok.get("token") or "")
        stripped = raw_txt.strip()
        if not stripped or _SPECIAL_TOKEN_RE.match(stripped):
            continue

        s, e, hint = _extract_pair_timestamps(tok)
        if s is None:
            continue
        tok_scale = 0.001 if hint == "ms" else (1.0 if hint == "hms" else scale)
        ts = round(float(s) * tok_scale, 3)
        te = round(float(e if e is not None else s) * tok_scale, 3)
        te = max(ts, te)

        starts_with_space = len(raw_txt) > 0 and raw_txt[0].isspace()
        if not is_bpe:
            # Explicit word-level entries
            for sub_idx, piece in enumerate(stripped.split()):
                merged.append({
                    "text": piece,
                    "start": ts,
                    "end": te,
                    "estimated": False,
                    "segment_index": seg_index,
                    "word_index_in_segment": len(merged),
                    "ignored": False,
                })
            continue

        # BPE sub-word merging: leading whitespace (or first word) starts a new word;
        # continuation sub-words without leading whitespace append to current word.
        if cur is None or starts_with_space:
            if cur is not None:
                merged.append(cur)
            cur = {
                "text": stripped,
                "start": ts,
                "end": te,
                "estimated": False,
                "segment_index": seg_index,
                "word_index_in_segment": len(merged),
                "ignored": False,
            }
        else:
            cur["text"] = cur["text"] + stripped
            cur["end"] = max(cur["end"], te)

    if cur is not None:
        merged.append(cur)
    return merged


def _locate_json_entries(data: Any) -> Optional[List[Dict[str, Any]]]:
    """Locate segment list inside arbitrary Vibe / whisper.cpp / OpenAI / generic JSON structures."""
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data):
            return data
        return [] if data == [] else None

    if not isinstance(data, dict):
        return None

    candidate_keys = ("transcript", "transcription", "segments", "result", "chunks", "items", "utterances", "entries", "cues")
    for k in candidate_keys:
        val = data.get(k)
        if isinstance(val, list):
            if val == [] or all(isinstance(x, dict) for x in val):
                return val
        elif isinstance(val, dict):
            nested = _locate_json_entries(val)
            if nested is not None:
                return nested

    # Top-level words-only object {"words": [...]}
    words_val = data.get("words")
    if isinstance(words_val, list) and all(isinstance(x, dict) for x in words_val):
        return words_val

    for subkey in ("data", "output"):
        sub = data.get(subkey)
        if isinstance(sub, dict):
            nested = _locate_json_entries(sub)
            if nested is not None:
                return nested

    return None


def _parse_json_transcript(text: str, audio_duration: float) -> Tuple[List[Dict[str, Any]], str, str, str]:
    """
    Parse JSON transcript into raw segments with resolved unit scaling.
    Returns (segments_raw, format_tag, unit_detected, precision).
    """
    raw_lines = text.splitlines()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TranscriptUnknownFormatError(raw_lines[:30]) from exc

    entries = _locate_json_entries(data)
    if entries is None:
        raise TranscriptUnknownFormatError(raw_lines[:30])
    if not entries:
        raise TranscriptImportError("ملف التفريغ JSON فارغ ولا يحتوي على مقاطع.")

    # Verify entries have a text field
    has_text_key = any(
        any(k in e for k in ("text", "word", "token", "sentence", "content", "line"))
        for e in entries
    )
    if not has_text_key:
        raise TranscriptUnknownFormatError(raw_lines[:30])

    # Collect timestamps and unit hints across segments & child words/tokens
    raw_nums: List[float] = []
    unit_hints: set[str] = set()
    any_timestamp_found = False

    def _collect_ts(obj: Dict[str, Any]) -> None:
        nonlocal any_timestamp_found
        s, e, h = _extract_pair_timestamps(obj)
        if s is not None:
            any_timestamp_found = True
            if h == "num":
                raw_nums.append(s)
                if e is not None:
                    raw_nums.append(e)
            if h:
                unit_hints.add(h)

    for entry in entries:
        _collect_ts(entry)
        for child_k in ("words", "tokens", "items"):
            children = entry.get(child_k)
            if isinstance(children, list):
                for ch in children:
                    if isinstance(ch, dict):
                        _collect_ts(ch)

    if not any_timestamp_found:
        raise TranscriptNoTimestampsError()

    scale, unit_detected = _detect_numeric_scale(raw_nums, unit_hints, audio_duration)

    segments_out: List[Dict[str, Any]] = []
    has_real_words = False

    # Check if top-level entries themselves are single words (e.g. {"words": [{"word": ...}]})
    looks_word_list = (
        isinstance(data, dict)
        and "words" in data
        and entries is data.get("words")
        and all("word" in e or "text" in e for e in entries)
    )
    if looks_word_list:
        words_all = _merge_subword_tokens(entries, scale, seg_index=1, is_bpe=False)
        if words_all:
            full_text = " ".join(w["text"] for w in words_all)
            segments_out.append({
                "index": 1,
                "start": words_all[0]["start"],
                "end": words_all[-1]["end"],
                "text": full_text,
                "estimated": False,
                "words": words_all,
            })
            return segments_out, "json", unit_detected, "word"

    for idx, entry in enumerate(entries, start=1):
        seg_text = str(
            entry.get("text")
            if entry.get("text") is not None
            else entry.get("sentence")
            if entry.get("sentence") is not None
            else entry.get("content")
            if entry.get("content") is not None
            else entry.get("line")
            if entry.get("line") is not None
            else entry.get("word") or ""
        ).strip()

        s, e, h = _extract_pair_timestamps(entry)
        seg_scale = 0.001 if h == "ms" else (1.0 if h == "hms" else scale)

        child_words: List[Dict[str, Any]] = []
        child_list = None
        child_key = ""
        for ck in ("words", "tokens", "items"):
            if isinstance(entry.get(ck), list) and entry[ck]:
                child_list = entry[ck]
                child_key = ck
                break

        if child_list:
            is_bpe = (child_key == "tokens") or any(
                isinstance(ch, dict) and isinstance(ch.get("text"), str) and ch["text"].startswith((" ", "\t"))
                for ch in child_list
            ) and (child_key != "words" or len(child_list) > max(1, len(seg_text.split())))
            child_words = _merge_subword_tokens(child_list, scale, seg_index=idx, is_bpe=is_bpe)
            if child_words:
                has_real_words = True
                if not seg_text:
                    seg_text = " ".join(w["text"] for w in child_words)

        if s is not None:
            seg_start = round(float(s) * seg_scale, 3)
            seg_end = round(float(e if e is not None else s) * seg_scale, 3)
        elif child_words:
            seg_start = child_words[0]["start"]
            seg_end = child_words[-1]["end"]
        else:
            continue

        if not seg_text and not child_words:
            continue

        if not child_words:
            child_words = _distribute_words_proportionally(seg_text, seg_start, seg_end, seg_index=idx)
            seg_estimated = True
        else:
            seg_estimated = False

        segments_out.append({
            "index": idx,
            "start": seg_start,
            "end": seg_end,
            "text": seg_text,
            "estimated": seg_estimated,
            "words": child_words,
        })

    if not segments_out:
        raise TranscriptNoTimestampsError()

    precision = "word" if has_real_words else "sentence"
    return segments_out, "json", unit_detected, precision


def _strip_cue_markup(text: str) -> str:
    cleaned = _HTML_OR_VTT_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_srt_vtt_transcript(text: str) -> Tuple[List[Dict[str, Any]], str, str, str]:
    """Tolerantly parse SRT or VTT (with/without header, missing blank lines, comma/dot ms, inline word tags)."""
    lines = text.splitlines()
    is_vtt = bool(lines and lines[0].strip().upper().startswith("WEBVTT"))
    has_dot_ts = False

    cues: List[Tuple[float, float, List[str]]] = []
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None
    cur_lines: List[str] = []

    i = 0
    while i < len(lines):
        raw_line = lines[i].strip()
        if "-->" in raw_line and _CUE_ARROW_RE.match(raw_line):
            # Flush previous cue
            if cur_start is not None and cur_lines:
                cues.append((cur_start, cur_end if cur_end is not None else cur_start, cur_lines))
            parts = raw_line.split("-->", 1)
            left_ts = parts[0].strip().split()[0]
            right_ts = parts[1].strip().split()[0]
            if "." in left_ts or "." in right_ts:
                has_dot_ts = True
            cur_start = parse_timestamp_value(left_ts)
            cur_end = parse_timestamp_value(right_ts)
            cur_lines = []
            i += 1
            continue

        if cur_start is None:
            # Skip WEBVTT header / metadata / cue index before first cue
            i += 1
            continue

        if raw_line == "":
            if cur_lines:
                cues.append((cur_start, cur_end if cur_end is not None else cur_start, cur_lines))
                cur_start = None
                cur_end = None
                cur_lines = []
            i += 1
            continue

        # Check if this line is just a numeric cue index right before another timestamp cue (handles missing blank lines!)
        if raw_line.isdigit() and i + 1 < len(lines) and "-->" in lines[i + 1] and _CUE_ARROW_RE.match(lines[i + 1].strip()):
            if cur_lines:
                cues.append((cur_start, cur_end if cur_end is not None else cur_start, cur_lines))
            cur_start = None
            cur_end = None
            cur_lines = []
            i += 1
            continue

        # Skip VTT NOTE blocks
        if raw_line.startswith("NOTE"):
            i += 1
            continue

        cur_lines.append(lines[i].strip())
        i += 1

    if cur_start is not None and cur_lines:
        cues.append((cur_start, cur_end if cur_end is not None else cur_start, cur_lines))

    if not cues:
        raise TranscriptUnknownFormatError(lines[:30])

    segments_out: List[Dict[str, Any]] = []
    any_inline_words = False

    for idx, (s_val, e_val, cue_lines) in enumerate(cues, start=1):
        raw_body = " ".join(cue_lines).strip()
        # Check for inline word timestamps like <00:00:01.200>word
        inline_matches = list(_INLINE_TAG_TS_RE.finditer(raw_body))
        words_list: List[Dict[str, Any]] = []
        clean_body = _strip_cue_markup(raw_body)
        if not clean_body:
            continue

        if inline_matches:
            any_inline_words = True
            # Split by inline timestamp markers
            cursor = 0
            cur_w_ts = s_val
            chunks: List[Tuple[float, str]] = []
            for m in inline_matches:
                before = _strip_cue_markup(raw_body[cursor:m.start()])
                if before:
                    chunks.append((cur_w_ts, before))
                ts_next = parse_timestamp_value(m.group(1))
                if ts_next is not None:
                    cur_w_ts = ts_next
                cursor = m.end()
            tail = _strip_cue_markup(raw_body[cursor:])
            if tail:
                chunks.append((cur_w_ts, tail))

            for c_idx, (c_ts, c_txt) in enumerate(chunks):
                next_ts = chunks[c_idx + 1][0] if c_idx + 1 < len(chunks) else e_val
                sub_words = c_txt.split()
                span = max(0.02, next_ts - c_ts)
                step = span / max(1, len(sub_words))
                for sw_idx, sw in enumerate(sub_words):
                    ws = round(c_ts + sw_idx * step, 3)
                    we = round(max(ws, c_ts + (sw_idx + 1) * step), 3)
                    words_list.append({
                        "text": sw,
                        "start": ws,
                        "end": we,
                        "estimated": False,
                        "segment_index": idx,
                        "word_index_in_segment": len(words_list),
                        "ignored": False,
                    })
        else:
            words_list = _distribute_words_proportionally(clean_body, s_val, e_val, seg_index=idx)

        segments_out.append({
            "index": idx,
            "start": round(s_val, 3),
            "end": round(e_val, 3),
            "text": clean_body,
            "estimated": not bool(inline_matches),
            "words": words_list,
        })

    if not segments_out:
        raise TranscriptUnknownFormatError(lines[:30])

    fmt = "vtt" if (is_vtt or has_dot_ts and not any(l.strip().isdigit() for l in lines[:4])) else "srt"
    if is_vtt:
        fmt = "vtt"
    precision = "word" if any_inline_words else "sentence"
    return segments_out, fmt, "hms", precision


def _classify_script(text: str) -> str:
    ar = len(_ARABIC_CHARS_RE.findall(text))
    la = len(_LATIN_CHARS_RE.findall(text))
    if ar == 0 and la == 0:
        return "other"
    if ar > la * 2 and ar >= 2:
        return "arabic"
    if la > ar * 2 and la >= 2:
        return "latin"
    return "mixed"


def _detect_suspicious_segments(segments: List[Dict[str, Any]], audio_duration: float) -> None:
    """Annotate suspicious segments non-destructively (a: duplicates, b: after audio / long silence, c: different script, d: zero duration)."""
    total_ar = sum(len(_ARABIC_CHARS_RE.findall(s["text"])) for s in segments)
    total_la = sum(len(_LATIN_CHARS_RE.findall(s["text"])) for s in segments)
    dominant = "other"
    if total_ar + total_la > 0:
        if total_ar >= (total_ar + total_la) * 0.65:
            dominant = "arabic"
        elif total_la >= (total_ar + total_la) * 0.65:
            dominant = "latin"

    reasons_map: List[Tuple[List[str], List[str]]] = [([], []) for _ in segments]

    for i, seg in enumerate(segments):
        r_codes, r_labels = reasons_map[i]

        # (d) Identical start and end
        if abs(float(seg["end"]) - float(seg["start"])) <= 0.001:
            r_codes.append("zero_duration")
            r_labels.append("بداية ونهاية متطابقتان")

        # (b) After audio end or after long silence
        if audio_duration and audio_duration > 0:
            if float(seg["start"]) >= audio_duration - 0.01 or float(seg["end"]) > audio_duration + 0.15:
                r_codes.append("after_audio")
                r_labels.append("بعد نهاية الصوت")
        if i > 0:
            gap = float(seg["start"]) - float(segments[i - 1]["end"])
            dur = float(seg["end"]) - float(seg["start"])
            if gap >= 4.0 and dur <= 2.2:
                r_codes.append("long_silence")
                r_labels.append("بعد صمت طويل")

        # (c) Different script from dominant transcript script
        if dominant in ("arabic", "latin"):
            sc = _classify_script(seg["text"])
            if sc in ("arabic", "latin") and sc != dominant:
                r_codes.append("different_script")
                r_labels.append("لغة مختلفة (شائبة محتملة)")

        # (a) Consecutive identical lines (flag both lines so duplicates stand out cleanly)
        if i > 0:
            n_cur = normalize_text(seg["text"])
            n_prev = normalize_text(segments[i - 1]["text"])
            if n_cur and n_cur == n_prev:
                if "duplicate" not in reasons_map[i - 1][0]:
                    reasons_map[i - 1][0].append("duplicate")
                    reasons_map[i - 1][1].append("تكرار مطابق")
                if "duplicate" not in r_codes:
                    r_codes.append("duplicate")
                    r_labels.append("تكرار مطابق")

    for i, seg in enumerate(segments):
        seg["suspicious_reasons"] = reasons_map[i][0]
        seg["suspicious_labels"] = reasons_map[i][1]
        seg["ignored"] = bool(seg.get("ignored", False))


def _recompute_transcript_words_and_stats(transcript: Dict[str, Any]) -> Dict[str, Any]:
    segments = transcript.get("segments") or []
    audio_duration = float(transcript.get("audio_duration") or 0.0)
    words_flat: List[Dict[str, Any]] = []
    suspicious_count = 0
    ignored_count = 0
    covered_sec = 0.0

    for seg in segments:
        is_ign = bool(seg.get("ignored", False))
        if seg.get("suspicious_reasons"):
            suspicious_count += 1
        if is_ign:
            ignored_count += 1
        else:
            covered_sec += max(0.0, float(seg["end"]) - float(seg["start"]))
        for w in seg.get("words") or []:
            w["ignored"] = is_ign
            words_flat.append({
                "text": w["text"],
                "start": w["start"],
                "end": w["end"],
                "estimated": bool(w.get("estimated", False)),
                "segment_index": int(w.get("segment_index", seg["index"])),
                "word_index_in_segment": int(w.get("word_index_in_segment", 0)),
                "ignored": is_ign,
            })

    last_ts = max((float(s["end"]) for s in segments), default=0.0)
    eff_dur = audio_duration if audio_duration > 0 else max(last_ts, 0.0)
    coverage = min(100.0, round((covered_sec / eff_dur) * 100.0, 1)) if eff_dur > 0 else 0.0

    transcript["words"] = words_flat
    transcript["last_timestamp"] = round(last_ts, 3)
    transcript["coverage_pct"] = coverage
    transcript["stats"] = {
        "segment_count": len(segments),
        "word_count": sum(1 for w in words_flat if not w.get("ignored")),
        "total_words": len(words_flat),
        "suspicious_count": suspicious_count,
        "ignored_count": ignored_count,
        "last_timestamp": round(last_ts, 3),
        "audio_duration": round(audio_duration, 3),
        "coverage_pct": coverage,
    }
    return transcript


def apply_ignored_segments(transcript: Dict[str, Any], ignored_indices: Iterable[int]) -> Dict[str, Any]:
    """Update ignored flag on segments by 1-based index and refresh word list/stats without deleting lines."""
    ign_set = {int(i) for i in ignored_indices}
    for seg in transcript.get("segments") or []:
        seg["ignored"] = int(seg["index"]) in ign_set
    return _recompute_transcript_words_and_stats(transcript)


def parse_and_normalize_transcript(
    raw_input: str | bytes,
    audio_duration: float = 0.0,
    filename: str = "",
) -> Dict[str, Any]:
    """
    Parse, validate, and normalize an external transcript (JSON / SRT / VTT).
    Produces normalized `{text, start, end, estimated}` word records, original segments,
    precision indicators, and non-destructive suspicious-line flags.
    """
    if isinstance(raw_input, bytes):
        if b"\x00" in raw_input:
            raise TranscriptImportError("الملف المحدد ليس ملفاً نصياً صالحاً (يحتوي على بيانات ثنائية).")
        try:
            text = raw_input.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise TranscriptImportError("تعذر قراءة الملف بترميز UTF-8. تأكد من حفظ الملف بصيغة UTF-8.") from exc
    else:
        text = str(raw_input or "").lstrip("\ufeff")

    stripped = text.strip()
    if not stripped:
        raise TranscriptImportError("ملف التفريغ فارغ.")

    if stripped.startswith("{") or stripped.startswith("[") or filename.lower().endswith(".json"):
        segments, fmt, unit_detected, precision = _parse_json_transcript(stripped, float(audio_duration or 0.0))
    else:
        segments, fmt, unit_detected, precision = _parse_srt_vtt_transcript(stripped)

    # Blocking validation: increasing times & end >= start & not far past audio_duration
    prev_start = -1.0
    for seg in segments:
        s = float(seg["start"])
        e = float(seg["end"])
        if e + 0.05 < s:
            raise TranscriptImportError(
                f"توقيت النهاية ({e:.2f}ث) أصغر من توقيت البداية ({s:.2f}ث) في السطر #{seg['index']}.",
                hint="راجع توقيتات الملف قبل الاستيراد.",
            )
        if e < s:
            seg["end"] = s
        if prev_start >= 0 and s + 0.5 < prev_start:
            raise TranscriptImportError(
                f"التوقيتات غير متسلسلة تصاعدياً عند السطر #{seg['index']} ({s:.2f}ث قبل {prev_start:.2f}ث).",
                hint="تأكد من ترتيب السطور زمنياً في ملف التفريغ.",
            )
        if prev_start >= 0 and s < prev_start:
            seg["start"] = prev_start
            if seg["end"] < seg["start"]:
                seg["end"] = seg["start"]
        prev_start = float(seg["start"])

    last_ts = max(float(s["end"]) for s in segments)
    if audio_duration and audio_duration > 0 and last_ts > float(audio_duration) + 2.0:
        raise TranscriptImportError(
            f"توقيتات التفريغ ({last_ts:.1f}ث) تتجاوز مدة الصوت ({float(audio_duration):.1f}ث) بأكثر من ثانيتين.",
            hint="تأكد من اختيار ملف التفريغ المطابق لهذا الملف الصوتي.",
        )

    _detect_suspicious_segments(segments, float(audio_duration or 0.0))

    fmt_upper = fmt.upper()
    if precision == "word":
        format_label = f"{fmt_upper} — توقيت كل كلمة ✓"
        precision_label = "دقة عالية (توقيت كل كلمة)"
        precision_hint = ""
    else:
        format_label = f"{fmt_upper} — توقيت الجمل ⚠"
        precision_label = "دقة متوسطة (توقيت الجمل فقط)"
        precision_hint = "لدقة أعلى: في Vibe شغّل Word timestamps وصدّر JSON"

    warnings: List[str] = []
    if precision_hint:
        warnings.append(precision_hint)

    transcript: Dict[str, Any] = {
        "source": "imported",
        "filename": filename or f"transcript.{fmt}",
        "format": fmt,
        "format_label": format_label,
        "precision": precision,
        "precision_label": precision_label,
        "precision_hint": precision_hint,
        "unit_detected": unit_detected,
        "duration": round(float(audio_duration or last_ts), 3),
        "audio_duration": round(float(audio_duration or 0.0), 3),
        "segments": segments,
        "words": [],
        "warnings": warnings,
    }
    return _recompute_transcript_words_and_stats(transcript)
