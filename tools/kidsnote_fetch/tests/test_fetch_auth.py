"""fetch.py's login block: fails fast and clearly, before any Notion work."""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    import fetch
except ImportError:  # pragma: no cover - requests not installed
    fetch = None

import kidsnote_auth as ka  # noqa: E402
from fake_http import FakeServer  # noqa: E402
from fake_kidsnote import SESSIONID, FakeKidsnote  # noqa: E402

USER, PASSWORD = "parent01", "Secret!23"
NOTION_MISSING = "NOTION_TOKEN / NOTION_DATABASE_ID missing"


@unittest.skipIf(fetch is None, "fetch.py dependencies (requests) not installed")
class FetchAuthTest(unittest.TestCase):
    def setUp(self):
        self.kn = FakeKidsnote({USER: PASSWORD})
        server = FakeServer(self.kn)
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.empty_env_file = Path(tmp.name, ".env")
        self.empty_env_file.write_text("", encoding="utf-8")
        for patcher in (mock.patch.object(ka, "KIDSNOTE_BASE", server.url),
                        mock.patch.object(ka, "_sleep", lambda s: None),
                        # the Notion side is out of scope; reaching it proves login passed
                        mock.patch.dict(sys.modules, {"notion_mirror": types.SimpleNamespace(NotionMirror=object)})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_fetch(self, **env):
        with mock.patch.dict(os.environ, {}):
            for key in list(os.environ):
                if key.startswith(("KIDSNOTE_", "NOTION_")):
                    del os.environ[key]
            os.environ.update(env)
            with self.assertLogs(level="INFO") as logs, self.assertRaises(SystemExit) as cm:
                fetch.main(["--publish-to-notion", "--no-local-save", "--env-file", str(self.empty_env_file)])
        return str(cm.exception.code), "\n".join(logs.output)

    def test_no_credentials(self):
        with mock.patch.dict(os.environ, {}):
            for key in list(os.environ):
                if key.startswith(("KIDSNOTE_", "NOTION_")):
                    del os.environ[key]
            with self.assertRaises(SystemExit) as cm:
                fetch.main(["--publish-to-notion", "--no-local-save", "--env-file", str(self.empty_env_file)])
        self.assertIn("Kidsnote credentials missing", str(cm.exception.code))

    def test_fresh_login_passes_before_notion_setup(self):
        code, logs = self.run_fetch(KIDSNOTE_USERNAME=USER, KIDSNOTE_PASSWORD=PASSWORD)
        self.assertIn(NOTION_MISSING, code)
        self.assertIn("Kidsnote session ready (fresh login)", logs)

    def test_ci_path_uses_session_from_login_step(self):
        self.kn.valid_sessions.add(SESSIONID)
        code, logs = self.run_fetch(KIDSNOTE_SESSION_COOKIE=SESSIONID)
        self.assertIn(NOTION_MISSING, code)
        self.assertIn("Kidsnote session ready (KIDSNOTE_SESSION_COOKIE)", logs)

    def test_wrong_password_stops_before_notion(self):
        with mock.patch.dict(os.environ, {}):
            for key in list(os.environ):
                if key.startswith(("KIDSNOTE_", "NOTION_")):
                    del os.environ[key]
            os.environ.update(KIDSNOTE_USERNAME=USER, KIDSNOTE_PASSWORD="wrong")
            with self.assertRaises(SystemExit) as cm:
                fetch.main(["--publish-to-notion", "--no-local-save", "--env-file", str(self.empty_env_file)])
        self.assertEqual(str(cm.exception.code),
                         "Kidsnote login failed (invalid_credentials): HTTP 400 on /api/web/login/")

    def test_wrong_password_with_valid_cookie_warns_and_continues(self):
        self.kn.valid_sessions.add(SESSIONID)
        code, logs = self.run_fetch(KIDSNOTE_USERNAME=USER, KIDSNOTE_PASSWORD="wrong",
                                    KIDSNOTE_SESSION_COOKIE=SESSIONID)
        self.assertIn(NOTION_MISSING, code)
        self.assertIn("login failed (invalid_credentials", logs)
        self.assertIn("Kidsnote session ready (KIDSNOTE_SESSION_COOKIE)", logs)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(fetch is None, "fetch.py dependencies (requests) not installed")
class ChildRosterTest(unittest.TestCase):
    """--list-children-json works without --backup-root (Drive workflow step 1)."""

    def test_roster_written_without_backup_root(self):
        kn = FakeKidsnote({USER: PASSWORD})
        kn.children = [{"id": 11, "name": "테스트아이"}, {"id": 22, "name": "테스트아이"}]
        with FakeServer(kn) as server, tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp, ".env")
            env_file.write_text("", encoding="utf-8")
            out = Path(tmp, "children.json")
            with mock.patch.object(ka, "KIDSNOTE_BASE", server.url), \
                    mock.patch.object(ka, "_sleep", lambda s: None), \
                    mock.patch.object(fetch, "_list_children", lambda sess: kn.children), \
                    mock.patch.object(fetch, "_list_reports", lambda sess, cid: [
                        {"date_written": "2021-03-02"}, {"date_written": "2019-04-01"}]), \
                    mock.patch.dict(os.environ, {"KIDSNOTE_USERNAME": USER,
                                                 "KIDSNOTE_PASSWORD": PASSWORD}):
                rc = fetch.main(["--auth-mode", "session-cookie-env",
                                 "--list-children-json", str(out),
                                 "--env-file", str(env_file)])
            self.assertEqual(rc, 0)
            import json
            roster = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual([r["id"] for r in roster], [11, 22])
            self.assertEqual(roster[0]["folder"], "2019-2021_11")
            for r in roster:
                self.assertNotIn("테스트아이", r["folder"])
