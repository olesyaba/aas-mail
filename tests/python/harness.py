"""Import webapp against a throwaway config/data dir and a free port.

Must be imported before webapp: bridge/webapp read EAS_BRIDGE_CONFIG,
EAS_BRIDGE_DATA_DIR and EAS_MAIL_PORT at import time, and the real
~/.config/eas-bridge (config, prefs, runtime_token) must never be touched.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="aas-mail-tests-"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


os.environ["EAS_BRIDGE_CONFIG"] = str(TMP / "config.json")
os.environ["EAS_BRIDGE_DATA_DIR"] = str(TMP)
os.environ["EAS_MAIL_PORT"] = str(_free_port())
sys.path.insert(0, str(ROOT))

import webapp  # noqa: E402

webapp.PREFS_PATH = TMP / "prefs.json"
assert webapp.bridge.DATA_DIR == TMP, "tests must not use the real data dir"

# Keychain stand-in: tests never shell out to /usr/bin/security.
KEYCHAIN: dict[str, str] = {}
webapp._keychain_get = lambda aid, service="eas-bridge": KEYCHAIN.get(aid)
webapp._keychain_set = lambda aid, pw, service="eas-bridge": KEYCHAIN.__setitem__(aid, pw)


class FakeBackend:
    """Just enough of bridge.EasBackend for webapp code paths."""

    def __init__(self, mime: dict[str, bytes] | None = None):
        self.lock = threading.RLock()
        self.client = None
        self.mime = mime or {}
        self.sent: list[bytes] = []
        self.fetch_hook = None

    def _pace(self):
        pass

    def ensure_identity(self):
        pass

    def fetch_mime(self, item_id: str) -> bytes:
        if self.fetch_hook:
            self.fetch_hook()
        if item_id not in self.mime:
            raise KeyError(item_id)
        return self.mime[item_id]

    def send(self, mime: bytes):
        self.sent.append(mime)


def make_acct(aid: str = "main", backend: FakeBackend | None = None, email: str = "me@bank.test"):
    a = webapp.Acct(aid, "Alfa-Bank" if aid == "main" else "Alfa-Seller",
                    {"email": email}, aid != "main")
    a.backend = backend or FakeBackend()
    return a
