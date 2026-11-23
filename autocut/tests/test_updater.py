"""Comprehensive Update 3 unit & integration tests (mocked GitHub API, SHA-1 delta, rollback, pip diff, migrations, secret redaction, rate-limit/resume, local zip/folder fallback)."""

from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.migrations_runner import run_all_migrations
from app.settings_store import (
    build_problem_report,
    get_app_version,
    get_public_global_settings,
    redact_secrets,
    save_global_settings,
)
from app.updater import (
    HashMismatchError,
    UpdaterError,
    check_github_version_only,
    git_blob_sha1,
    is_managed_relpath,
    is_protected_relpath,
    load_manifest,
    plan_github_update,
    stage_github_update,
    stage_local_update,
    test_github_connection,
)
from app.updater_helper import apply_staged_update, diff_requirements, list_backups, rollback_to_backup


class MockGitHubFixture:
    """In-process mocked GitHub REST API server for testing AutoCut delta updater."""

    def __init__(self, files: Dict[str, bytes], branch: str = "main", commit_sha: str = "commit_abc123"):
        self.files = dict(files)
        self.branch = branch
        self.commit_sha = commit_sha
        self.blob_requests: List[str] = []
        self.corrupt_blob_sha: str = ""
        self.force_http_status: int = 0
        self.fail_after_blob_calls: int = 0
        self.commits: List[str] = ["release 1.2.0", "fix arabic timeline"]
        self._blobs_by_sha: Dict[str, bytes] = {}
        self._rebuild_blobs()

        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _send_json(self, code: int, obj: Any, extra_headers: Dict[str, str] | None = None):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                if extra_headers:
                    for k, v in extra_headers.items():
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if fixture.force_http_status:
                    headers = {"X-RateLimit-Remaining": "0"} if fixture.force_http_status in (403, 429) else {}
                    self._send_json(
                        fixture.force_http_status,
                        {"message": "API rate limit exceeded"} if fixture.force_http_status in (403, 429) else {"message": "error"},
                        headers,
                    )
                    return

                if self.path.startswith("/repos/") and "/branches/" in self.path:
                    self._send_json(200, {
                        "name": fixture.branch,
                        "commit": {
                            "sha": fixture.commit_sha,
                            "commit": {
                                "message": fixture.commits[0],
                                "committer": {"date": "2026-11-22T12:00:00Z"},
                            },
                        },
                    })
                    return

                if "/git/trees/" in self.path:
                    tree = []
                    for rel, content in sorted(fixture.files.items()):
                        tree.append({
                            "path": rel,
                            "mode": "100644",
                            "type": "blob",
                            "sha": git_blob_sha1(content),
                            "size": len(content),
                        })
                    self._send_json(200, {"sha": fixture.commit_sha, "tree": tree})
                    return

                if "/git/blobs/" in self.path:
                    sha = self.path.split("/git/blobs/", 1)[1].split("?", 1)[0]
                    fixture.blob_requests.append(sha)
                    if fixture.fail_after_blob_calls and len(fixture.blob_requests) >= fixture.fail_after_blob_calls:
                        # Drop TCP connection abruptly to simulate network failure mid-download
                        self.connection.close()
                        return
                    data = fixture._blobs_by_sha.get(sha)
                    if data is None:
                        self._send_json(404, {"message": "Not Found"})
                        return
                    if fixture.corrupt_blob_sha == sha:
                        data = b"CORRUPTED_BYTES_" + data
                    b64 = base64.b64encode(data).decode("ascii")
                    self._send_json(200, {"sha": sha, "encoding": "base64", "content": b64})
                    return

                if "/commits" in self.path:
                    self._send_json(200, [{"commit": {"message": m}} for m in fixture.commits])
                    return

                self._send_json(404, {"message": "Unknown mock endpoint"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _rebuild_blobs(self) -> None:
        self._blobs_by_sha = {git_blob_sha1(content): content for content in self.files.values()}

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _create_sample_install(base_dir: Path, version: str = "1.0.0") -> None:
    """Populate a realistic miniature AutoCut install directory for updater tests."""
    (base_dir / "app").mkdir(parents=True, exist_ok=True)
    (base_dir / "web").mkdir(parents=True, exist_ok=True)
    (base_dir / "migrations").mkdir(parents=True, exist_ok=True)
    (base_dir / "projects" / "p1").mkdir(parents=True, exist_ok=True)
    (base_dir / "models").mkdir(parents=True, exist_ok=True)
    (base_dir / "logs").mkdir(parents=True, exist_ok=True)
    (base_dir / ".venv").mkdir(parents=True, exist_ok=True)

    (base_dir / "VERSION").write_text(version + "\n", encoding="utf-8")
    (base_dir / "CHANGELOG.md").write_text(f"## {version}\n- initial\n", encoding="utf-8")
    (base_dir / "requirements.txt").write_text("fastapi==0.115.6\nrapidfuzz==3.10.1\n", encoding="utf-8")
    (base_dir / "app" / "server.py").write_text("# server v1\n", encoding="utf-8")
    (base_dir / "app" / "matching.py").write_text("# matching v1\n", encoding="utf-8")
    (base_dir / "app" / "old_obsolete.py").write_text("# will be deleted\n", encoding="utf-8")
    (base_dir / "web" / "index.html").write_text("<html>v1</html>\n", encoding="utf-8")
    (base_dir / "migrations" / "001_project_schema.py").write_text(
        "def upgrade_project(meta, defaults):\n"
        "    s = meta.setdefault('settings', {})\n"
        "    changed = False\n"
        "    for k, v in defaults.items():\n"
        "        if k not in s:\n"
        "            s[k] = v\n"
        "            changed = True\n"
        "    return changed\n",
        encoding="utf-8",
    )
    shutil.copy2(ROOT_DIR / "update_manifest.json", base_dir / "update_manifest.json")

    # Protected user assets that must NEVER be touched
    (base_dir / "projects" / "p1" / "project.json").write_text(
        json.dumps({"id": "p1", "name": "My Documentary", "settings": {"fps": 60}}),
        encoding="utf-8",
    )
    (base_dir / "models" / "whisper-medium.bin").write_bytes(b"MODEL_DATA_DO_NOT_DELETE")
    (base_dir / ".venv" / "marker.txt").write_text("venv-keep", encoding="utf-8")
    (base_dir / "logs" / "app.log").write_text("log line\n", encoding="utf-8")
    (base_dir / "AutoCut.lnk").write_bytes(b"WINDOWS_SHORTCUT")


class UpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="autocut_upd_test_"))
        self.base_dir = self.tmp / "install"
        _create_sample_install(self.base_dir, version="1.0.0")
        self.settings_file = self.base_dir / "settings.json"
        save_global_settings(
            {
                "update_source": {
                    "repo": "owner/autocut",
                    "branch": "main",
                    "subfolder": "auto",
                    "token": "github_pat_super_secret_token_987654321",
                    "check_on_startup": True,
                }
            },
            settings_file=self.settings_file,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _remote_files_from_local(self, prefix: str = "autocut/") -> Dict[str, bytes]:
        remote: Dict[str, bytes] = {}
        manifest = load_manifest(self.base_dir)
        for p in self.base_dir.rglob("*"):
            if p.is_file():
                rel = p.relative_to(self.base_dir).as_posix()
                if is_managed_relpath(rel, manifest):
                    remote[prefix + rel] = p.read_bytes()
        return remote

    def test_a_delta_detection_downloads_only_changed_files(self) -> None:
        remote = self._remote_files_from_local("autocut/")
        # Change ONLY VERSION + CHANGELOG.md + app/matching.py (keep others byte-identical!)
        new_version = b"1.1.0\n"
        new_changelog = b"## 1.1.0\n- Faster matching\n"
        new_matching = b"# matching v2\ndef faster(): return True\n"
        remote["autocut/VERSION"] = new_version
        remote["autocut/CHANGELOG.md"] = new_changelog
        remote["autocut/app/matching.py"] = new_matching
        # Also include repo-side AI Studio files that MUST be ignored
        remote["src/App.tsx"] = b"export default function App() {}"
        remote["package.json"] = b'{"name": "preview"}'

        mock = MockGitHubFixture(remote)
        try:
            conn = test_github_connection("owner/autocut", "main", "auto", "tok", api_base=mock.base_url)
            self.assertEqual(conn["subfolder"], "autocut")
            self.assertEqual(conn["remote_version"], "1.1.0")

            chk = check_github_version_only(self.base_dir, self.settings_file, api_base=mock.base_url)
            self.assertTrue(chk["update_available"])

            mock.blob_requests.clear()
            plan = plan_github_update(self.base_dir, self.settings_file, api_base=mock.base_url)
            dl_paths = sorted(i["relpath"] for i in plan["to_download"])
            self.assertEqual(dl_paths, ["CHANGELOG.md", "VERSION", "app/matching.py"])
            self.assertGreaterEqual(plan["unchanged_count"], 5)
            expected_bytes = len(new_version) + len(new_changelog) + len(new_matching)
            self.assertEqual(plan["total_bytes"], expected_bytes)

            # Staging downloads ONLY the 3 changed blobs (NOT unchanged server.py or web/index.html)
            mock.blob_requests.clear()
            stage_root = stage_github_update(plan, self.base_dir, self.settings_file, api_base=mock.base_url)
            self.assertEqual(len(mock.blob_requests), 3)
            self.assertEqual((stage_root / "files" / "app" / "matching.py").read_bytes(), new_matching)
            # Live install is still unchanged before apply
            self.assertEqual((self.base_dir / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")
        finally:
            mock.close()

    def test_b_hash_mismatch_fails_loudly_without_touching_live_files(self) -> None:
        remote = self._remote_files_from_local("")
        remote["VERSION"] = b"1.1.0\n"
        corrupt_sha = git_blob_sha1(remote["app/matching.py"])
        remote["app/matching.py"] = b"# modified v2\n"
        corrupt_sha = git_blob_sha1(remote["app/matching.py"])

        mock = MockGitHubFixture(remote)
        mock.corrupt_blob_sha = corrupt_sha
        try:
            plan = plan_github_update(self.base_dir, self.settings_file, api_base=mock.base_url)
            with self.assertRaises(HashMismatchError):
                stage_github_update(plan, self.base_dir, self.settings_file, api_base=mock.base_url)
            # Live files must remain untouched at 1.0.0
            self.assertEqual(get_app_version(self.base_dir), "1.0.0")
            self.assertEqual((self.base_dir / "app" / "matching.py").read_text(encoding="utf-8"), "# matching v1\n")
        finally:
            mock.close()

    def test_c_d_deleted_managed_files_and_hard_protected_paths(self) -> None:
        remote = self._remote_files_from_local("")
        remote["VERSION"] = b"1.1.0\n"
        # Remove app/old_obsolete.py from remote
        remote.pop("app/old_obsolete.py", None)
        # Tamper manifest locally AND put protected files in remote tree to try to overwrite/delete them
        tampered = load_manifest(self.base_dir)
        tampered["managed_paths"].extend(["projects/", "settings.json", "models/", "*.lnk"])
        (self.base_dir / "update_manifest.json").write_text(json.dumps(tampered), encoding="utf-8")
        remote["projects/p1/project.json"] = b'{"hacked": true}'
        remote["settings.json"] = b'{"wiped": true}'
        remote["models/whisper-medium.bin"] = b"OVERWRITTEN"
        remote["AutoCut.lnk"] = b"BAD_SHORTCUT"

        self.assertTrue(is_protected_relpath("projects/p1/project.json"))
        self.assertTrue(is_protected_relpath("settings.json"))
        self.assertTrue(is_protected_relpath("models/whisper-medium.bin"))
        self.assertTrue(is_protected_relpath("AutoCut.lnk"))
        self.assertFalse(is_managed_relpath("projects/p1/project.json", tampered))

        mock = MockGitHubFixture(remote)
        try:
            plan = plan_github_update(self.base_dir, self.settings_file, api_base=mock.base_url)
            self.assertEqual(plan["to_delete"], ["app/old_obsolete.py"])
            for item in plan["to_download"]:
                self.assertFalse(is_protected_relpath(item["relpath"]))

            stage_root = stage_github_update(plan, self.base_dir, self.settings_file, api_base=mock.base_url)
            res = apply_staged_update(
                self.base_dir,
                stage_root,
                pip_installer=lambda pkgs: (True, ""),
                self_test_runner=lambda bdir: (True, ""),
            )
            self.assertTrue(res["ok"])
            self.assertFalse((self.base_dir / "app" / "old_obsolete.py").exists())
            # Hard-protected user data untouched!
            self.assertEqual((self.base_dir / "models" / "whisper-medium.bin").read_bytes(), b"MODEL_DATA_DO_NOT_DELETE")
            self.assertEqual((self.base_dir / "AutoCut.lnk").read_bytes(), b"WINDOWS_SHORTCUT")
            self.assertIn("github_pat_super_secret_token_987654321", self.settings_file.read_text(encoding="utf-8"))
        finally:
            mock.close()

    def test_e_backup_limit_and_rollback(self) -> None:
        # Run 6 sequential updates and verify backup count caps at 5 and rollback restores bytes & deletes added files
        for idx in range(1, 7):
            new_ver = f"1.{idx}.0"
            remote = self._remote_files_from_local("")
            remote["VERSION"] = (new_ver + "\n").encode("utf-8")
            remote["app/matching.py"] = f"# matching v{idx}\n".encode("utf-8")
            if idx == 6:
                remote["app/brand_new_module.py"] = b"NEW_FILE = True\n"
            mock = MockGitHubFixture(remote)
            try:
                plan = plan_github_update(self.base_dir, self.settings_file, api_base=mock.base_url)
                stage_root = stage_github_update(plan, self.base_dir, self.settings_file, api_base=mock.base_url)
                res = apply_staged_update(
                    self.base_dir,
                    stage_root,
                    pip_installer=lambda pkgs: (True, ""),
                    self_test_runner=lambda bdir: (True, ""),
                )
                self.assertTrue(res["ok"])
            finally:
                mock.close()

        backups = list_backups(self.base_dir)
        self.assertEqual(len(backups), 5)
        self.assertEqual(get_app_version(self.base_dir), "1.6.0")
        self.assertTrue((self.base_dir / "app" / "brand_new_module.py").exists())

        # Roll back latest backup (from 1.6.0 -> 1.5.0)
        latest_backup_id = backups[0]["id"]
        rb = rollback_to_backup(self.base_dir, latest_backup_id)
        self.assertTrue(rb["ok"])
        self.assertEqual(get_app_version(self.base_dir), "1.5.0")
        self.assertFalse((self.base_dir / "app" / "brand_new_module.py").exists())
        self.assertEqual((self.base_dir / "app" / "matching.py").read_text(encoding="utf-8"), "# matching v5\n")

    def test_f_pip_diff_only_changed_and_rollback_on_failure(self) -> None:
        old_req = "fastapi==0.115.6\nrapidfuzz==3.10.1\n# comment\n"
        new_req = "fastapi==0.115.6\nrapidfuzz==3.11.0\nhttpx==0.28.1\n"
        self.assertEqual(diff_requirements(old_req, new_req), ["rapidfuzz==3.11.0", "httpx==0.28.1"])

        remote = self._remote_files_from_local("")
        remote["VERSION"] = b"1.1.0\n"
        remote["requirements.txt"] = new_req.encode("utf-8")
        mock = MockGitHubFixture(remote)
        installed_calls: List[List[str]] = []
        try:
            plan = plan_github_update(self.base_dir, self.settings_file, api_base=mock.base_url)
            stage_root = stage_github_update(plan, self.base_dir, self.settings_file, api_base=mock.base_url)

            def failing_pip(pkgs: List[str]):
                installed_calls.append(pkgs)
                return False, "httpx==0.28.1"

            res = apply_staged_update(
                self.base_dir,
                stage_root,
                pip_installer=failing_pip,
                self_test_runner=lambda bdir: (True, ""),
            )
            self.assertFalse(res["ok"])
            self.assertTrue(res["rolled_back"])
            self.assertEqual(installed_calls, [["rapidfuzz==3.11.0", "httpx==0.28.1"]])
            self.assertIn("httpx==0.28.1", res["error"])
            # Version and requirements rolled back cleanly
            self.assertEqual(get_app_version(self.base_dir), "1.0.0")
            self.assertIn("rapidfuzz==3.10.1", (self.base_dir / "requirements.txt").read_text(encoding="utf-8"))
        finally:
            mock.close()

    def test_g_failed_selftest_rollback_and_idempotent_migrations(self) -> None:
        remote = self._remote_files_from_local("")
        remote["VERSION"] = b"1.2.0\n"
        remote["app/server.py"] = b"# broken server v1.2\n"
        mock = MockGitHubFixture(remote)
        try:
            plan = plan_github_update(self.base_dir, self.settings_file, api_base=mock.base_url)
            stage_root = stage_github_update(plan, self.base_dir, self.settings_file, api_base=mock.base_url)
            res = apply_staged_update(
                self.base_dir,
                stage_root,
                pip_installer=lambda pkgs: (True, ""),
                self_test_runner=lambda bdir: (False, "فشل فحص /api/health"),
            )
            self.assertFalse(res["ok"])
            self.assertTrue(res["rolled_back"])
            self.assertEqual(get_app_version(self.base_dir), "1.0.0")
            self.assertEqual((self.base_dir / "app" / "server.py").read_text(encoding="utf-8"), "# server v1\n")
        finally:
            mock.close()

        # Verify migrations add missing defaults WITHOUT overwriting existing user fps=60
        (self.base_dir / "projects" / "p2").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "projects" / "p2" / "project.json").write_text(
            json.dumps({"id": "p2", "name": "Old Project", "settings": {"fps": 60}}),
            encoding="utf-8",
        )
        m1 = run_all_migrations(base_dir=self.base_dir)
        self.assertEqual(m1["upgraded_projects"], 1)
        pjson = json.loads((self.base_dir / "projects" / "p2" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(pjson["settings"]["fps"], 60)
        self.assertIn("background_fill", pjson["settings"])
        self.assertIn("theme", pjson["settings"])
        # Idempotent second run changes 0 projects
        m2 = run_all_migrations(base_dir=self.base_dir)
        self.assertEqual(m2["upgraded_projects"], 0)

    def test_h_token_never_leaks_in_ui_logs_or_problem_report(self) -> None:
        secret = "github_pat_super_secret_token_987654321"
        pub = get_public_global_settings(self.settings_file)
        self.assertNotIn("token", pub["update_source"])
        self.assertTrue(pub["update_source"]["has_token"])
        self.assertNotIn(secret, json.dumps(pub))

        # Write fake sensitive trace into log file
        log_file = self.base_dir / "logs" / "app.log"
        log_file.write_text(
            f"ERROR Authorization: Bearer {secret}\nFailed url with ghp_abcdef1234567890\n",
            encoding="utf-8",
        )
        redacted = redact_secrets(log_file.read_text(encoding="utf-8"), settings_file=self.settings_file)
        self.assertNotIn(secret, redacted)
        self.assertNotIn("ghp_abcdef1234567890", redacted)

        report = build_problem_report(
            {"ffmpeg_ok": True, "ffmpeg_version": "4.4"},
            base_dir=self.base_dir,
            settings_file=self.settings_file,
            log_file=log_file,
        )
        self.assertNotIn(secret, report)
        self.assertNotIn("ghp_abcdef1234567890", report)
        self.assertIn("***REDACTED***", report)

    def test_i_rate_limit_network_drop_and_resume(self) -> None:
        remote = self._remote_files_from_local("")
        remote["VERSION"] = b"1.3.0\n"
        remote["app/matching.py"] = b"# v1.3 matching\n"
        remote["web/index.html"] = b"<html>v1.3</html>\n"
        mock = MockGitHubFixture(remote)
        try:
            # 1. Rate-limit HTTP 429 leaves everything untouched
            mock.force_http_status = 429
            with self.assertRaises(UpdaterError) as ctx:
                plan_github_update(self.base_dir, self.settings_file, api_base=mock.base_url)
            self.assertIn("حد طلبات GitHub", str(ctx.exception))
            self.assertEqual(get_app_version(self.base_dir), "1.0.0")

            # 2. Network drop after 1 blob download: staging retains downloaded file; live untouched
            mock.force_http_status = 0
            plan = plan_github_update(self.base_dir, self.settings_file, api_base=mock.base_url)
            mock.blob_requests.clear()
            mock.fail_after_blob_calls = 2  # fails on 2nd blob
            with self.assertRaises(UpdaterError):
                stage_github_update(plan, self.base_dir, self.settings_file, api_base=mock.base_url)
            self.assertEqual(get_app_version(self.base_dir), "1.0.0")

            # 3. Retry after recovery -> skips already-verified 1st file and fetches only remaining 2!
            mock.fail_after_blob_calls = 0
            mock.blob_requests.clear()
            stage_root = stage_github_update(plan, self.base_dir, self.settings_file, api_base=mock.base_url)
            self.assertEqual(len(mock.blob_requests), 2)
            self.assertTrue((stage_root / "plan.json").exists())
        finally:
            mock.close()

    def test_local_zip_and_folder_fallback(self) -> None:
        # Build a local ZIP update package containing 1.4.0
        pkg_dir = self.tmp / "pkg_1_4_0"
        (pkg_dir / "autocut" / "app").mkdir(parents=True, exist_ok=True)
        (pkg_dir / "autocut" / "VERSION").write_text("1.4.0\n", encoding="utf-8")
        (pkg_dir / "autocut" / "CHANGELOG.md").write_text("## 1.4.0\n- Local ZIP update\n", encoding="utf-8")
        (pkg_dir / "autocut" / "app" / "server.py").write_text("# server v1\n", encoding="utf-8")
        (pkg_dir / "autocut" / "app" / "matching.py").write_text("# v1.4 local\n", encoding="utf-8")

        zip_path = self.tmp / "update_1.4.0.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in pkg_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(pkg_dir))

        staged = stage_local_update(zip_path, base_dir=self.base_dir)
        res = apply_staged_update(
            self.base_dir,
            Path(staged["stage_root"]),
            pip_installer=lambda pkgs: (True, ""),
            self_test_runner=lambda bdir: (True, ""),
        )
        self.assertTrue(res["ok"])
        self.assertEqual(get_app_version(self.base_dir), "1.4.0")
        self.assertEqual((self.base_dir / "app" / "matching.py").read_text(encoding="utf-8"), "# v1.4 local\n")

        # Also test local folder fallback directly (1.4.0 -> 1.5.0)
        folder_pkg = self.tmp / "folder_1_5_0"
        (folder_pkg / "app").mkdir(parents=True, exist_ok=True)
        (folder_pkg / "VERSION").write_text("1.5.0\n", encoding="utf-8")
        (folder_pkg / "app" / "server.py").write_text("# server v1.5\n", encoding="utf-8")
        staged_dir = stage_local_update(folder_pkg, base_dir=self.base_dir)
        res2 = apply_staged_update(
            self.base_dir,
            Path(staged_dir["stage_root"]),
            pip_installer=lambda pkgs: (True, ""),
            self_test_runner=lambda bdir: (True, ""),
        )
        self.assertTrue(res2["ok"])
        self.assertEqual(get_app_version(self.base_dir), "1.5.0")
        self.assertEqual((self.base_dir / "app" / "server.py").read_text(encoding="utf-8"), "# server v1.5\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
