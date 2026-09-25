"""Mail folders survive a restart: shown from disk at once, refreshed with a delta."""
import json
import os
import threading
import time
import unittest

from harness import FakeBackend, make_acct, webapp


def _box(a, cid="14", n=3, gen=7):
    box = webapp._mail_box(a, cid)
    box.update(filter=5, fields=("from", "subject"), label="Входящие", gen=gen, complete=True, ts=time.time())
    box["by_id"] = {f"{cid}:{i}": {"item_id": f"{cid}:{i}", "subject": f"letter {i}", "is_read": i > 0,
                                  "received": f"2026-09-2{i} 10:00"} for i in range(n)}
    return box


class _Store:
    def __init__(self, cols):
        self.cols = cols

    def get(self, key, default=None):
        return self.cols if key == "collections" else default


class _Client:
    def __init__(self, cols):
        self.store = _Store(cols)


class MailCache(unittest.TestCase):
    def setUp(self):
        self.a = make_acct()
        self.a.cfg.update(username="corp\\u_test", url="https://mail.test/eas", state_name="state-cachetest.json")
        self.cols = {"14": {"sync_key": "K-100", "generation": 7}}
        self.a.backend.client = _Client(self.cols)
        webapp._mail_cache_path(self.a).unlink(missing_ok=True)
        self.state = webapp.bridge.DATA_DIR / "state-cachetest.json"

    def tearDown(self):
        webapp._mail_cache_path(self.a).unlink(missing_ok=True)
        self.state.unlink(missing_ok=True)

    def _state(self, cols):
        self.state.write_text(json.dumps({"collections": cols}))

    def _reload(self):
        b = make_acct()
        b.cfg.update(self.a.cfg)
        return b, webapp._mail_cache_load(b)

    def test_round_trip_keeps_letters_and_sync_generation(self):
        _box(self.a)
        webapp._mail_box(self.a, "99")  # never listed: nothing to save
        self.assertTrue(webapp._mail_cache_save(self.a))
        path = webapp._mail_cache_path(self.a)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, "letters are readable by this user only")
        self._state(self.cols)
        b, n = self._reload()
        self.assertEqual(n, 1)
        box = b.mail_boxes["14"]
        self.assertEqual(box["gen"], 7, "the next refresh continues this SyncKey (delta)")
        self.assertEqual((box["filter"], box["fields"]), (5, ("from", "subject")))
        self.assertEqual(len(box["by_id"]), 3)
        self.assertNotIn("99", b.mail_boxes)

    def test_sync_key_moved_on_after_the_save_means_one_full_listing(self):
        _box(self.a)
        webapp._mail_cache_save(self.a)
        self._state({"14": {"sync_key": "K-101", "generation": 7}})  # a delta ran after the save
        b, _ = self._reload()
        box = b.mail_boxes["14"]
        self.assertIsNone(box["gen"], "no delta from a key the cache is behind")
        self.assertEqual(len(box["by_id"]), 3, "letters still shown while it re-lists")
        self.assertNotIn("sync_key", box)
        self._state({})  # state file lost
        c, _ = self._reload()
        self.assertIsNone(c.mail_boxes["14"]["gen"])

    def test_another_login_or_server_does_not_see_the_letters(self):
        _box(self.a)
        webapp._mail_cache_save(self.a)
        other = make_acct()
        other.cfg.update(username="corp\\u_other", url="https://mail.test/eas")
        self.assertEqual(webapp._mail_cache_load(other), 0)
        self.assertEqual(other.mail_boxes, {})

    def test_cache_only_list_answers_at_once_even_when_the_mailbox_is_busy(self):
        _box(self.a)
        release, held = threading.Event(), threading.Event()

        def hold():
            with self.a.backend.lock:
                held.set()
                release.wait(5)
        t = threading.Thread(target=hold)
        t.start()
        held.wait(2)
        try:
            t0 = time.time()
            r = webapp.call(self.a, "mail", {"action": "list", "folder": "14", "filter": 5,
                                             "fields": ["subject", "from"], "cache_only": True})
            self.assertLess(time.time() - t0, 0.2)
        finally:
            release.set()
            t.join()
        self.assertTrue(r["from_cache"])
        self.assertEqual([m["subject"] for m in r["items"]], ["letter 2", "letter 1", "letter 0"])

    def test_cache_only_misses_when_period_or_fields_differ(self):
        _box(self.a)
        r = webapp.call(self.a, "mail", {"action": "list", "folder": "14", "filter": 0,
                                         "fields": ["subject", "from"], "cache_only": True})
        self.assertTrue(r["miss"])
        self.assertEqual(r["items"], [])

    def test_fingerprint_changes_when_a_letter_is_read(self):
        box = _box(self.a)
        before = webapp._mail_cache_sig(self.a)
        box["by_id"]["14:0"]["is_read"] = True
        self.assertNotEqual(before, webapp._mail_cache_sig(self.a))

    def test_busy_mailbox_skips_the_save_instead_of_waiting(self):
        _box(self.a)
        held, release = threading.Event(), threading.Event()

        def hold():
            with self.a.backend.lock:
                held.set()
                release.wait(5)
        t = threading.Thread(target=hold)
        t.start()
        held.wait(2)
        try:
            t0 = time.time()
            self.assertFalse(webapp._mail_cache_save(self.a))
            self.assertLess(time.time() - t0, 3)
        finally:
            release.set()
            t.join()


if __name__ == "__main__":
    unittest.main()
