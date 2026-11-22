"""Supervisor launcher for AutoCut: runs FastAPI server and executes pending swaps cleanly between restarts."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RESTART_EXIT_CODE = 42


def _run_pending_swap() -> None:
    pending_file = BASE_DIR / "staging" / ".pending_swap.json"
    if not pending_file.exists():
        return
    try:
        info = json.loads(pending_file.read_text(encoding="utf-8"))
    except Exception:
        pending_file.unlink(missing_ok=True)
        return

    action = info.get("action", "apply")
    cmd = [sys.executable, "-m", "app.updater_helper", "--action", action, "--base-dir", str(BASE_DIR)]
    if action == "apply":
        cmd += ["--stage-dir", str(info.get("stage_dir", ""))]
    elif action == "rollback":
        cmd += ["--backup-id", str(info.get("backup_id", ""))]

    subprocess.run(cmd, cwd=str(BASE_DIR))


def main() -> int:
    # Execute any leftover pending swap before booting server
    _run_pending_swap()

    while True:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=str(BASE_DIR),
            env={**dict(__import__("os").environ), "AUTOCUT_SUPERVISED": "1"},
        )
        rc = proc.wait()
        pending_file = BASE_DIR / "staging" / ".pending_swap.json"
        if rc == RESTART_EXIT_CODE or pending_file.exists():
            _run_pending_swap()
            time.sleep(0.3)
            continue
        return rc


if __name__ == "__main__":
    raise SystemExit(main())
