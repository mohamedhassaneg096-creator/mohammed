"""Idempotent migration runner executing all scripts in migrations/ on projects/ and settings.json."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .project import BASE_DIR, DEFAULT_SETTINGS, PROJECTS_DIR, _atomic_write_json, _read_json, logger
from .settings_store import DEFAULT_GLOBAL_SETTINGS, SETTINGS_FILE


def run_all_migrations(
    base_dir: Optional[Path] = None,
    projects_dir: Optional[Path] = None,
    settings_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run all migration modules in migrations/ sorted by filename. Safe and idempotent."""
    bdir = base_dir or BASE_DIR
    pdir = projects_dir or (bdir / "projects") if base_dir else PROJECTS_DIR
    sfile = settings_file or (bdir / "settings.json") if base_dir else SETTINGS_FILE
    mig_dir = bdir / "migrations"

    # 1. Upgrade global settings.json defaults without overwriting existing user values
    if sfile.exists():
        gs = _read_json(sfile) or {}
        gs_changed = False
        for section, defaults in DEFAULT_GLOBAL_SETTINGS.items():
            if section not in gs or not isinstance(gs[section], dict):
                gs[section] = dict(defaults)
                gs_changed = True
            else:
                for k, v in defaults.items():
                    if k not in gs[section]:
                        gs[section][k] = v
                        gs_changed = True
        if gs_changed:
            _atomic_write_json(sfile, gs)

    # 2. Load migration scripts dynamically (works even after updates add new migration files)
    executed: List[str] = []
    upgraded_projects = 0

    if not mig_dir.exists():
        return {"executed": executed, "upgraded_projects": 0}

    mig_files = sorted(
        p for p in mig_dir.iterdir()
        if p.is_file() and p.suffix == ".py" and not p.name.startswith("__")
    )

    modules = []
    for mf in mig_files:
        spec = importlib.util.spec_from_file_location(f"autocut_mig_{mf.stem}", mf)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            modules.append((mf.name, mod))
            executed.append(mf.name)

    if pdir.exists():
        for proj_folder in sorted(pdir.iterdir()):
            if not proj_folder.is_dir():
                continue
            pjson = proj_folder / "project.json"
            if not pjson.exists():
                continue
            meta = _read_json(pjson)
            if not isinstance(meta, dict):
                continue
            modified = False
            for mname, mod in modules:
                fn = getattr(mod, "upgrade_project", None)
                if callable(fn):
                    try:
                        if fn(meta, DEFAULT_SETTINGS):
                            modified = True
                    except Exception as exc:
                        logger.warning("Migration %s skipped on %s: %s", mname, proj_folder.name, exc)
            if modified:
                _atomic_write_json(pjson, meta)
                upgraded_projects += 1

    return {"executed": executed, "upgraded_projects": upgraded_projects}
