"""iPad entry: same contract as android_entry, Keychain via Security.framework (ctypes)."""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

from harness import webapp

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ios" / "python"))
import ios_entry  # noqa: E402
import ios_keychain  # noqa: E402


class IosEntryTest(unittest.TestCase):
    def test_install_swaps_keychain_and_update(self):
        store = {}

        class Secrets:
            get = staticmethod(lambda a, s: store.get((a, s)))
            set = staticmethod(lambda a, s, p: store.__setitem__((a, s), p))

        saved = (webapp._keychain_get, webapp._keychain_set, webapp.update_check, webapp.WEB)
        try:
            webapp.update_check = lambda force=False: {"available": True, "can_install": True}
            ios_entry.install(webapp, Path("/tmp/web"), Secrets)
            webapp._keychain_set("main", "pw")
            self.assertEqual(webapp._keychain_get("main"), "pw")
            u = webapp.update_check()
            self.assertFalse(u["can_install"])
            self.assertIn("Xcode", u["reason"])
            self.assertEqual(webapp.WEB, Path("/tmp/web"))
        finally:
            webapp._keychain_get, webapp._keychain_set, webapp.update_check, webapp.WEB = saved


@unittest.skipUnless(sys.platform == "darwin", "Security.framework")
class IosKeychainTest(unittest.TestCase):
    def test_round_trip_update_and_delete(self):
        svc = "aas-test-" + uuid.uuid4().hex
        try:
            self.assertIsNone(ios_keychain.get("main", svc))
            ios_keychain.set("main", svc, "первый")
            ios_keychain.set("main", svc, "второй")
            self.assertEqual(ios_keychain.get("main", svc), "второй")
        finally:
            ios_keychain.delete("main", svc)
        self.assertIsNone(ios_keychain.get("main", svc))


if __name__ == "__main__":
    unittest.main()
