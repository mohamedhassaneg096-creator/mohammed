"""Migration 001: Ensure project.json and settings.json include newly introduced default keys without overwriting user values."""

from __future__ import annotations

from typing import Any, Dict

MIGRATION_ID = "001_project_schema"


def upgrade_project(meta: Dict[str, Any], default_settings: Dict[str, Any]) -> bool:
    """Upgrade a single project dict in-place. Returns True if modified."""
    changed = False
    if int(meta.get("schema_version") or 0) < 1:
        meta["schema_version"] = 1
        changed = True

    settings = meta.get("settings")
    if not isinstance(settings, dict):
        meta["settings"] = dict(default_settings)
        return True

    for key, val in default_settings.items():
        if key not in settings:
            settings[key] = val
            changed = True

    if "overrides" not in meta:
        meta["overrides"] = {}
        changed = True

    return changed
