"""End-to-end boot: run webapp.py as the app does and talk to it like the UI and tray.

No credentials are configured, so the server stays in first-run (needs_setup)
mode and never contacts Exchange.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BootTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="aas-mail-boot-"))
        cls.port = _free_port()
        env = {**os.environ, "EAS_MAIL_PORT": str(cls.port), "EAS_BRIDGE_DATA_DIR": str(cls.tmp),
               "EAS_BRIDGE_CONFIG": str(cls.tmp / "config.json"), "EAS_MAIL_LOG": "WARNING"}
        cls.proc = subprocess.Popen([sys.executable, "webapp.py"], cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 20
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError("webapp.py exited:\n" + cls.proc.stdout.read().decode())
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("webapp.py did not start listening")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.proc.stdout.close()

    def _req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", **(headers or {})})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_token_published_for_tray_and_private(self):
        tok = self.tmp / "runtime_token"
        self.assertTrue(tok.exists())
        self.assertEqual(os.stat(tok).st_mode & 0o777, 0o600)

    def test_ui_page_carries_the_same_token(self):
        status, page = self._req("GET", "/")
        self.assertEqual(status, 200)
        token = (self.tmp / "runtime_token").read_text()
        self.assertIn(f'content="{token}"'.encode(), page)

    def test_first_run_api_with_tray_token(self):
        token = (self.tmp / "runtime_token").read_text()
        status, data = self._req("POST", "/api/about", b"{}", {"X-Tok": token, "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        j = json.loads(data)
        self.assertTrue(j["needs_setup"])
        status, data = self._req("POST", "/api/accounts", b"{}", {"X-Tok": token})
        self.assertEqual([a["id"] for a in json.loads(data)["accounts"]], ["main"])
        cfg = json.loads((self.tmp / "config.json").read_text())
        self.assertEqual((cfg["username"], "password" in cfg), ("", False))


if __name__ == "__main__":
    unittest.main()
