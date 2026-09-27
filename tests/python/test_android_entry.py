"""android_entry.install: Keychain → Android Secrets, web dir, no self-update on Android."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from harness import TMP, webapp

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "android/app/src/main/python"))
import android_entry  # noqa: E402


class FakeSecrets:
    def __init__(self):
        self.d = {}

    def get(self, account, service):
        return self.d.get((account, service))

    def set(self, account, service, password):
        self.d[(account, service)] = password


class InstallTest(unittest.TestCase):
    def setUp(self):
        names = ("WEB", "_keychain_get", "_keychain_set", "update_check")
        self.saved = {n: getattr(webapp, n) for n in names}
        self.sec = FakeSecrets()

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(webapp, n, v)

    def test_passwords_go_through_secrets(self):
        android_entry.install(webapp, TMP / "web", self.sec)
        webapp._keychain_set("seller", "pw1")
        self.assertEqual(self.sec.d, {("seller", "eas-bridge"): "pw1"})
        self.assertEqual(webapp._keychain_get("seller"), "pw1")
        self.assertIsNone(webapp._keychain_get("main"))

    def test_plaintext_password_migrates_into_secrets(self):
        android_entry.install(webapp, TMP / "web", self.sec)
        self.assertEqual(webapp.get_password("main", "old-pw", service="test-svc"), "old-pw")
        self.assertEqual(self.sec.d, {("main", "test-svc"): "old-pw"})

    def test_web_dir(self):
        android_entry.install(webapp, TMP / "web", self.sec)
        self.assertEqual(webapp.WEB, TMP / "web")

    def test_update_never_installs(self):
        fake = {"ok": True, "available": True, "can_install": True, "reason": "", "url": "x.zip"}
        webapp.update_check = lambda force=False: dict(fake)
        android_entry.install(webapp, TMP / "web", self.sec)
        out = webapp.update_check()
        self.assertFalse(out["can_install"])
        self.assertIn("APK", out["reason"])
        self.assertTrue(out["page"].endswith("/releases/latest"))
