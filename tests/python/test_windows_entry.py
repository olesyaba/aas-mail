"""windows_entry: Keychain → Credential Manager, -win.zip self-update, fcntl stand-in (Windows only)."""
from __future__ import annotations

import errno
import os
import sys
import unittest
from pathlib import Path

from harness import TMP, webapp

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows"))
import windows_entry  # noqa: E402
from test_android_entry import FakeSecrets  # noqa: E402


class InstallTest(unittest.TestCase):
    def setUp(self):
        names = ("_keychain_get", "_keychain_set", "update_check", "update_install", "_app_bundle", "ASSET_SUFFIX")
        self.saved = {n: getattr(webapp, n) for n in names}
        self.sec = FakeSecrets()

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(webapp, n, v)

    def test_passwords_go_through_secrets(self):
        windows_entry.install(webapp, self.sec)
        webapp._keychain_set("seller", "pw1")
        self.assertEqual(self.sec.d, {("seller", "eas-bridge"): "pw1"})
        self.assertEqual(webapp._keychain_get("seller"), "pw1")
        self.assertIsNone(webapp._keychain_get("main"))

    def test_updates_from_the_win_zip(self):
        windows_entry.install(webapp, self.sec)
        self.assertEqual(webapp.ASSET_SUFFIX, "-win.zip")

    def test_checkout_does_not_replace_itself(self):
        # A checkout has no python\pythonw.exe beside windows\ — only the unpacked zip updates.
        windows_entry.install(webapp, self.sec)
        self.assertIsNone(webapp._app_bundle())
        webapp.update_check = lambda force=False: {"available": True, "can_install": False, "reason": "из исходников"}
        out = webapp.update_install()
        self.assertFalse(out["ok"])
        self.assertEqual(out["message"], "из исходников")


@unittest.skipUnless(os.name == "nt", "msvcrt: Windows only")
class FcntlShimTest(unittest.TestCase):
    def test_second_lock_fails_until_unlocked(self):
        f = windows_entry.fcntl_shim()
        path = TMP / "state.json.lock"
        with open(path, "a+") as a, open(path, "a+") as b:
            f.flock(a.fileno(), f.LOCK_EX | f.LOCK_NB)
            with self.assertRaises(OSError) as cm:
                f.flock(b.fileno(), f.LOCK_EX | f.LOCK_NB)
            self.assertEqual(cm.exception.errno, errno.EAGAIN)  # what state._acquire retries on
            f.flock(a.fileno(), f.LOCK_UN)
            f.flock(b.fileno(), f.LOCK_EX | f.LOCK_NB)
            f.flock(b.fileno(), f.LOCK_UN)

