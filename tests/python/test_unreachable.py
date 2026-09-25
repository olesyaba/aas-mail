"""Exchange stops answering: the app must not queue every call behind a 90 s timeout.
Fail fast after the first failure, answer from cache while a mailbox is busy."""
import threading
import time
import unittest

from harness import FakeBackend, make_acct, webapp  # noqa: F401 — harness sets up paths
from outlook_activesync_mcp.errors import NotAuthenticated


class _Client:
    """Stands in for EasClient: `command` is wrapped like the real one."""

    def __init__(self, fail=True):
        self.calls, self.fail = 0, fail

    def raw(self, cmd, node, *, policy_key=None, allow_empty=False):
        self.calls += 1
        if self.fail:
            raise NotAuthenticated("EAS endpoint недоступен (Connection reset by peer); проверь EAS_URL и сеть")
        return "tree"


class FailFast(unittest.TestCase):
    def setUp(self):
        self.c = _Client()
        self.cmd = webapp._fail_fast_command(_Client.raw)

    def test_after_a_failure_calls_do_not_reach_the_server(self):
        with self.assertRaises(NotAuthenticated) as first:
            self.cmd(self.c, "Sync", None)
        self.assertEqual(first.exception.code, "unreachable")
        t0 = time.time()
        for _ in range(5):
            with self.assertRaises(NotAuthenticated):
                self.cmd(self.c, "Sync", None)
        self.assertEqual(self.c.calls, 1, "queued calls fail at once, without a network round")
        self.assertLess(time.time() - t0, 0.5)

    def test_pause_grows_then_resets_when_the_server_is_back(self):
        st = webapp._down_state(self.c)
        pauses = []
        for _ in range(5):
            st["until"] = 0  # the pause is over: this call is the probe
            with self.assertRaises(NotAuthenticated):
                self.cmd(self.c, "Sync", None)
            pauses.append(round(st["until"] - time.time()))
        self.assertEqual(pauses, [10, 20, 40, 60, 60])
        st["until"], self.c.fail = 0, False
        self.assertEqual(self.cmd(self.c, "Sync", None), "tree")
        self.assertEqual((st["n"], st["until"]), (0, 0.0))

    def test_other_errors_do_not_start_a_pause(self):
        def bad(self, cmd, node, **kw):
            raise ValueError("bad request")
        cmd = webapp._fail_fast_command(bad)
        with self.assertRaises(ValueError):
            cmd(self.c, "Sync", None)
        self.assertEqual(webapp._down_state(self.c)["until"], 0.0)

    def test_unreachable_is_treated_as_backoff_and_worded_for_people(self):
        e = NotAuthenticated("EAS endpoint недоступен (Read timed out)")
        self.assertTrue(webapp._is_unreachable(e))
        self.assertTrue(webapp._is_backoff(e), "keep the cached folder instead of a full dump")
        a = make_acct()
        self.assertIn("не отвечает", webapp._friendly_error(a, "mail", "list", e))
        self.assertFalse(webapp._is_unreachable(NotAuthenticated("нет логина")))


class BusyMailbox(unittest.TestCase):
    def setUp(self):
        self.a = make_acct()
        self.wait = webapp.LOCK_WAIT_S
        webapp.LOCK_WAIT_S = 0.2
        holder_ready, self.release = threading.Event(), threading.Event()

        def hold():  # a slow Exchange call holds the mailbox
            with self.a.backend.lock:
                holder_ready.set()
                self.release.wait(5)
        self.t = threading.Thread(target=hold)
        self.t.start()
        holder_ready.wait(2)

    def tearDown(self):
        self.release.set()
        self.t.join()
        webapp.LOCK_WAIT_S = self.wait

    def test_list_answers_from_cache_without_waiting(self):
        box = webapp._mail_box(self.a, "14")
        box["by_id"] = {"14:1": {"item_id": "14:1", "subject": "saved", "received": "2026-09-25 10:00"}}
        t0 = time.time()
        r = webapp.call(self.a, "mail", {"action": "list", "folder": "14"})
        self.assertLess(time.time() - t0, 1.5)
        self.assertTrue(r["ok"])
        self.assertTrue(r["stale"])
        self.assertEqual([m["subject"] for m in r["items"]], ["saved"])

    def test_no_cache_means_a_clear_error_not_a_hang(self):
        r = webapp.call(self.a, "mail", {"action": "list", "folder": "99"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "unreachable")
        self.assertIn("Alfa-Bank", r["message"])


if __name__ == "__main__":
    unittest.main()
