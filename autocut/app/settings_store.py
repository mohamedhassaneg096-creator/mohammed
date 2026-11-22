"""Global settings.json store, secret masking, log redaction, and safe problem-report generation."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .project import BASE_DIR, DEFAULT_SETTINGS, LOG_FILE, _atomic_write_json, _read_json

SETTINGS_FILE = BASE_DIR / "settings.json"

DEFAULT_GLOBAL_SETTINGS: Dict[str, Any] = {
    "update_source": {
        "repo": "",
        "branch": "main",
        "subfolder": "auto",
        "token": "",
        "check_on_startup": True,
    },
    "defaults": dict(DEFAULT_SETTINGS),
}

_TOKEN_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"gho_[A-Za-z0-9_]{10,}"),
    re.compile(r"(Authorization:\s*(?:Bearer|token)\s+)[^\s]+", re.IGNORECASE),
]


def get_app_version(base_dir: Optional[Path] = None) -> str:
    vfile = (base_dir or BASE_DIR) / "VERSION"
    if not vfile.exists():
        return "1.0.0"
    return vfile.read_text(encoding="utf-8").strip().lstrip("vV") or "1.0.0"


def load_global_settings(settings_file: Optional[Path] = None) -> Dict[str, Any]:
    path = settings_file or SETTINGS_FILE
    raw = _read_json(path) or {}
    merged_source = dict(DEFAULT_GLOBAL_SETTINGS["update_source"])
    merged_source.update(raw.get("update_source") or {})
    merged_defaults = dict(DEFAULT_GLOBAL_SETTINGS["defaults"])
    merged_defaults.update(raw.get("defaults") or {})
    return {
        "update_source": merged_source,
        "defaults": merged_defaults,
    }


def save_global_settings(updates: Dict[str, Any], settings_file: Optional[Path] = None) -> Dict[str, Any]:
    path = settings_file or SETTINGS_FILE
    current = load_global_settings(path)
    if "update_source" in updates and isinstance(updates["update_source"], dict):
        src = dict(current["update_source"])
        for k, v in updates["update_source"].items():
            if k == "token":
                # Preserve existing token when UI sends masked placeholder or empty preserve flag
                if v == "__KEEP__":
                    continue
                src["token"] = str(v or "").strip()
            elif k == "check_on_startup":
                src[k] = bool(v)
            else:
                src[k] = str(v or "").strip()
        current["update_source"] = src
    if "defaults" in updates and isinstance(updates["defaults"], dict):
        d = dict(current["defaults"])
        d.update(updates["defaults"])
        current["defaults"] = d
    _atomic_write_json(path, current)
    return current


def mask_token(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return ""
    if len(t) <= 8:
        return "••••••••"
    return "••••••••" + t[-4:]


def get_public_global_settings(settings_file: Optional[Path] = None) -> Dict[str, Any]:
    """Return global settings safe for UI/API responses (token is masked, never exposed in plaintext)."""
    data = load_global_settings(settings_file)
    src = dict(data["update_source"])
    raw_tok = src.pop("token", "")
    src["has_token"] = bool(raw_tok)
    src["token_masked"] = mask_token(raw_tok)
    return {
        "update_source": src,
        "defaults": data["defaults"],
    }


def redact_secrets(text: str, extra_secrets: Optional[List[str]] = None, settings_file: Optional[Path] = None) -> str:
    """Strip GitHub PATs / configured token from any log snippet or problem report."""
    out = str(text or "")
    secrets_to_scrub: List[str] = list(extra_secrets or [])
    try:
        tok = load_global_settings(settings_file)["update_source"].get("token", "")
        if tok:
            secrets_to_scrub.append(tok)
    except Exception:
        pass

    for s in secrets_to_scrub:
        if s and len(s) >= 4:
            out = out.replace(s, "***REDACTED***")

    for pat in _TOKEN_PATTERNS:
        if pat.groups >= 1:
            out = pat.sub(r"\1***REDACTED***", out)
        else:
            out = pat.sub("***REDACTED***", out)
    return out


def build_problem_report(
    health: Dict[str, Any],
    base_dir: Optional[Path] = None,
    settings_file: Optional[Path] = None,
    log_file: Optional[Path] = None,
) -> str:
    """Generate a diagnostic problem report with zero secrets or raw tokens."""
    bdir = base_dir or BASE_DIR
    lfile = log_file or LOG_FILE
    pub = get_public_global_settings(settings_file)
    src = pub["update_source"]
    lines = [
        "=== AutoCut Problem Report ===",
        f"App Version: {get_app_version(bdir)}",
        f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {sys.version.split()[0]}",
        f"FFmpeg OK: {health.get('ffmpeg_ok')} ({health.get('ffmpeg_version', '')})",
        f"faster-whisper OK: {health.get('faster_whisper_ok')}",
        f"rapidfuzz OK: {health.get('rapidfuzz_ok')}",
        f"Update Repo: {src.get('repo') or '(not configured)'} [branch={src.get('branch')}, subfolder={src.get('subfolder')}, token_configured={src.get('has_token')}]",
        "",
        "--- Recent Logs (redacted) ---",
    ]
    if lfile.exists():
        raw_log = lfile.read_text(encoding="utf-8", errors="replace").splitlines()[-45:]
        lines.extend(raw_log)
    else:
        lines.append("(no log entries)")

    report = "\n".join(lines)
    return redact_secrets(report, settings_file=settings_file)
