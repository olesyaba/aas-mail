"""Backend logic: prefs, caches, folder parsing, paging, calendar cache, MIME rendering."""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from unittest import mock

from harness import KEYCHAIN, TMP, FakeBackend, make_acct, webapp

from outlook_activesync_mcp.commands import calendar as cal_cmd
from outlook_activesync_mcp.commands import mail as mail_cmd
from outlook_activesync_mcp.commands import people as people_cmd
from outlook_activesync_mcp.wbxml import el


class PrefsTest(unittest.TestCase):
    def setUp(self):
        webapp.PREFS_PATH.unlink(missing_ok=True)

    def test_defaults_when_missing(self):
        self.assertEqual(webapp.load_prefs(), webapp.DEFAULT_PREFS)

    def test_update_validates_and_persists(self):
        out = webapp.update_prefs({
            "signature": "x" * 6000, "auto_sync": 3, "cal_view": "month", "threads": "yes",
            "unknown": 1, "favorite_folders": ["main:1", 5, "seller:" + "9" * 200],
            "links": [{"name": "ok", "url": "https://t.me/x"}, {"name": "bad", "url": "javascript:alert(1)"}, "junk"],
        })
        self.assertEqual(len(out["signature"]), 5000)
        self.assertEqual(out["auto_sync"], webapp.DEFAULT_PREFS["auto_sync"])  # 3 is not an allowed step
        self.assertEqual(out["cal_view"], "week")
        self.assertIs(out["threads"], True)  # wrong type ignored
        self.assertNotIn("unknown", out)
        self.assertEqual(out["favorite_folders"], ["main:1", ("seller:" + "9" * 200)[:80]])
        self.assertEqual(out["links"], [{"name": "ok", "url": "https://t.me/x"}])
        self.assertEqual(webapp.load_prefs(), out)
        self.assertEqual(os.stat(webapp.PREFS_PATH).st_mode & 0o777, 0o600)

    def test_corrupt_file_falls_back(self):
        webapp.PREFS_PATH.write_text("{not json")
        self.assertEqual(webapp.load_prefs(), webapp.DEFAULT_PREFS)


class CalendarExpandTest(unittest.TestCase):
    """Regression: the cached window is kept as `date`s; expanding masters with
    it raised «can't compare datetime.datetime to datetime.date» and the whole
    calendar (web + tray) came back empty."""

    def test_date_window_expands_single_and_recurring(self):
        from datetime import timezone
        start = datetime.now(timezone.utc).replace(microsecond=0)
        masters = {"1": {"subject": "one-off", "start": start, "end": start + timedelta(hours=1)}}
        items, _ = webapp._cal_expand(masters, "cal", date.today() - timedelta(days=1),
                                      date.today() + timedelta(days=2), webapp.CAL_FIELDS)
        self.assertEqual([e["subject"] for e in items], ["one-off"])


class CalendarOwnWritesTest(unittest.TestCase):
    """Regression: a deleted/declined event stayed on screen until a manual sync —
    ActiveSync never echoes the client's own changes to its delta refresh."""

    def _acct(self, items):
        a = make_acct()
        today = date.today()
        a.cal.update(items=items, ts=time.time(), loaded=True,
                     range=(today - timedelta(days=7), today + timedelta(days=60)))
        return a

    def _list(self, a):
        d = date.today()
        return webapp.cal_events(a, d.isoformat(), (d + timedelta(days=1)).isoformat())["items"]

    def test_cancel_decline_accept_create_show_immediately(self):
        now = datetime.combine(date.today(), datetime.min.time()).replace(hour=10)
        a = self._acct([dict(_ev(now, subject="gone"), item_id="1"), dict(_ev(now, subject="meet"), item_id="2"),
                        dict(_ev(now, subject="accept me", response_type="not_responded"), item_id="3")])
        with mock.patch.object(webapp, "cal_refresh_bg"):
            webapp._cal_after_write(a, {"action": "cancel", "item_id": "1"}, {"ok": True})
            webapp._cal_after_write(a, {"action": "respond", "item_id": "2", "response": "decline"}, {"ok": True})
            webapp._cal_after_write(a, {"action": "respond", "item_id": "3", "response": "accept"}, {"ok": True})
            webapp._cal_after_write(a, {"action": "create", "subject": "new", "start": now.strftime("%Y-%m-%dT11:00"),
                                        "end": now.strftime("%Y-%m-%dT11:30")},
                                    {"ok": True, "items": [{"item_id": "9"}]})
        got = {e["subject"]: e for e in self._list(a)}
        self.assertEqual(sorted(got), ["accept me", "new"])
        self.assertEqual(got["accept me"]["response_type"], "accepted")
        self.assertEqual(got["new"]["item_id"], "9")

    def test_full_sync_after_the_write_drops_the_overlay(self):
        a = self._acct([dict(_ev(datetime.now(), subject="x"), item_id="1")])
        webapp._cal_after_write(a, {"action": "cancel", "item_id": "1"}, {"ok": True})
        webapp._cal_overlay_prune(a.cal, time.time() + 1)  # a full sync that started later
        self.assertEqual(a.cal["hidden"], {})


class RespondErrorTest(unittest.TestCase):
    def test_meeting_response_status_is_explained(self):
        from outlook_activesync_mcp.errors import EasStatusError
        with mock.patch.object(cal_cmd, "handle", side_effect=EasStatusError("MeetingResponse", 2, "x")):
            r = webapp.call(make_acct(), "events", {"action": "respond", "item_id": "1:1", "response": "decline"})
        self.assertFalse(r["ok"])
        self.assertIn("отменили или перенесли", r["message"])


class ThrottleMessageTest(unittest.TestCase):
    def test_throttle_is_explained_in_plain_words(self):
        from outlook_activesync_mcp.errors import EasError
        err = EasError("x"); err.code = "throttled"
        with mock.patch.object(mail_cmd, "handle", side_effect=err):
            r = webapp.call(make_acct(), "mail", {"action": "get", "item_id": "1:1"})
        self.assertIn("временно ограничил", r["message"])


class SharedViewPrefsTest(unittest.TestCase):
    def setUp(self):
        webapp.PREFS_PATH.unlink(missing_ok=True)

    def test_mail_window_and_working_day(self):
        out = webapp.update_prefs({"mail_window": 3, "work_start": 10, "work_end": 19})
        self.assertEqual((out["mail_window"], out["work_start"], out["work_end"]), (3, 10, 19))
        out = webapp.update_prefs({"mail_window": 7, "work_start": 30})
        self.assertEqual((out["mail_window"], out["work_start"]), (3, 10), "invalid values ignored")
        out = webapp.update_prefs({"category_colors": {"статус": "#ff0000", "bad": "red", "x": 5}})
        self.assertEqual(out["category_colors"], {"статус": "#ff0000"})
        out = webapp.update_prefs({"reminder_minutes": 15})
        self.assertEqual(out["reminder_minutes"], 15)
        out = webapp.update_prefs({"reminder_minutes": 7})
        self.assertEqual(out["reminder_minutes"], 15, "only offered steps are accepted")
        out = webapp.update_prefs({"mail_sound": "short"})
        self.assertEqual(out["mail_sound"], "short")
        out = webapp.update_prefs({"mail_sound": "none"})
        self.assertEqual(out["mail_sound"], "none")
        out = webapp.update_prefs({"mail_sound": "boom"})
        self.assertEqual(out["mail_sound"], "none", "unknown sound is ignored")
        out = webapp.update_prefs({"work_start": 20, "work_end": 9})
        self.assertLess(out["work_start"], out["work_end"], "inverted day falls back to defaults")


class FreeSlotsTest(unittest.TestCase):
    def setUp(self):
        webapp.PREFS_PATH.unlink(missing_ok=True)
        webapp.update_prefs({"work_start": 10, "work_end": 18})

    def test_working_day_from_prefs_and_three_options(self):
        slots = [{"start": f"2026-09-24 {h:02d}:{m:02d}", "end": f"2026-09-24 {h + (m + 60) // 60 - 1 if m else h:02d}:{(m + 60) % 60:02d}",
                  "start_iso": "x", "end_iso": "y"} for h, m in [(10, 0), (11, 0), (17, 30), (12, 0), (13, 0)]]
        # 17:30 + 60 min ends 18:30 — past the working day
        slots[2]["end"] = "2026-09-24 18:30"
        seen = {}

        def fake(client, action, **kw):
            seen.update(kw)
            return {"ok": True, "action": action, "items": slots}

        with mock.patch.object(people_cmd, "handle", side_effect=fake):
            r = webapp.call(make_acct(), "people", {"action": "find_free_slots", "who": ["x@y"],
                                                    "duration_minutes": 60, "count": 3})
        self.assertEqual((seen["work_start"], seen["work_end"]), (10, 18))
        self.assertGreaterEqual(seen["count"], 30, "asks upstream for plenty before filtering")
        self.assertEqual([x["start"][-5:] for x in r["items"]], ["10:00", "11:00", "12:00"])


class PeopleCacheTest(unittest.TestCase):
    def setUp(self):
        webapp._people_find_cache.clear()

    def test_store_and_hit_case_insensitive(self):
        a = make_acct()
        webapp._people_find_store(a, {"query": "Ivan", "limit": 10}, {"ok": True, "items": [1]})
        self.assertEqual(webapp._people_find_cached(a, {"query": " ivan ", "limit": 10})["items"], [1])
        self.assertIsNone(webapp._people_find_cached(a, {"query": "ivan", "limit": 5}))
        self.assertIsNone(webapp._people_find_cached(make_acct("seller"), {"query": "ivan", "limit": 10}))

    def test_short_query_never_cached(self):
        a = make_acct()
        webapp._people_find_store(a, {"query": "i"}, {"ok": True})
        self.assertEqual(webapp._people_find_cache, {})

    def test_expired_entry_dropped(self):
        a = make_acct()
        webapp._people_find_store(a, {"query": "ivan"}, {"ok": True})
        with mock.patch.object(webapp.time, "time", return_value=time.time() + 1000):
            self.assertIsNone(webapp._people_find_cached(a, {"query": "ivan"}))
        self.assertEqual(webapp._people_find_cache, {})

    def test_growth_is_capped(self):
        a = make_acct()
        for i in range(260):
            webapp._people_find_store(a, {"query": f"q{i:03}"}, {"ok": True})
        self.assertLessEqual(len(webapp._people_find_cache), 201)

    def test_call_uses_cache_before_backend(self):
        a = make_acct()
        with mock.patch.object(people_cmd, "handle", return_value={"ok": True, "items": [{"address": "x@y"}]}) as h:
            r1 = webapp.call(a, "people", {"action": "find", "query": "petrov", "limit": 10})
            r2 = webapp.call(a, "people", {"action": "find", "query": "PETROV", "limit": 10})
        self.assertEqual(h.call_count, 1)
        self.assertEqual(r1["items"], r2["items"])


class CallDispatchTest(unittest.TestCase):
    def test_unknown_domain_or_missing_action(self):
        a = make_acct()
        self.assertEqual(webapp.call(a, "nope", {"action": "x"})["error"], "bad_request")
        self.assertEqual(webapp.call(a, "mail", {})["error"], "bad_request")

    def test_exception_becomes_error_envelope(self):
        a = make_acct()
        with mock.patch.object(cal_cmd, "handle", side_effect=RuntimeError("boom")):
            r = webapp.call(a, "events", {"action": "get", "item_id": "1:1"})
        self.assertEqual((r["ok"], r["error"], r["message"], r["items"]), (False, "RuntimeError", "boom", []))


class MailPagingTest(unittest.TestCase):
    def test_pulls_until_limit_and_passes_cursor(self):
        calls = []

        def fake(client, action, **kw):
            calls.append(kw)
            n = len(calls)
            return {"ok": True, "items": [{"item_id": f"{n}a"}, {"item_id": f"{n}b"}],
                    "has_more": True, "next_cursor": f"c{n}"}

        a = make_acct()
        with mock.patch.object(mail_cmd, "handle", side_effect=fake):
            r = webapp._mail_list_paged(a, a.backend, {"folder": "5", "limit": 5, "fields": ["subject"]})
        self.assertEqual(r["count"], 6)  # third page overshoots by one; the server decides page size
        self.assertEqual(calls[0], {"limit": 5, "folder": "5", "fields": ["subject"]})
        self.assertEqual(calls[1], {"limit": 3, "cursor": "c1"})
        self.assertEqual(calls[2], {"limit": 1, "cursor": "c2"})
        self.assertTrue(r["has_more"])
        self.assertEqual(r["next_cursor"], "c3")

    def test_slow_server_returns_what_arrived_within_budget(self):
        """Stalwart: 1–2 messages per round; don't make the UI wait for 40."""
        n = [0]

        def slow(client, action, **kw):
            n[0] += 1
            time.sleep(0.03)
            return {"ok": True, "items": [{"item_id": str(n[0])}], "has_more": True, "next_cursor": f"c{n[0]}"}

        a = make_acct()
        with mock.patch.object(mail_cmd, "handle", side_effect=slow), \
             mock.patch.object(webapp, "_MAIL_PAGE_BUDGET", 0.1):
            r = webapp._mail_fetch_pages(a.backend, {"limit": 40})
        self.assertLess(r["count"], 10)
        self.assertTrue(r["has_more"])
        self.assertTrue(r["next_cursor"])

    def test_stops_when_server_is_done(self):
        a = make_acct()
        with mock.patch.object(mail_cmd, "handle", return_value={"ok": True, "items": [{}], "has_more": False}):
            r = webapp._mail_list_paged(a, a.backend, {"limit": 40})
        self.assertEqual((r["count"], r["has_more"]), (1, False))

    def test_error_on_first_round_is_returned_later_rounds_keep_items(self):
        a = make_acct()
        err = {"ok": False, "error": "eas_error", "message": "x"}
        with mock.patch.object(mail_cmd, "handle", return_value=err):
            self.assertEqual(webapp._mail_list_paged(a, a.backend, {"limit": 5}), err)
        seq = iter([{"ok": True, "items": [{}], "has_more": True, "next_cursor": "c"}, err])
        with mock.patch.object(mail_cmd, "handle", side_effect=lambda *a, **k: next(seq)):
            r = webapp._mail_list_paged(a, a.backend, {"limit": 5})
        self.assertEqual((r["ok"], r["count"]), (True, 1))

    def test_list_updates_unread_badge_for_first_page_only(self):
        a = make_acct()
        page = {"ok": True, "items": [{"is_read": False}, {"is_read": True}, {"is_read": False}], "has_more": False}
        with mock.patch.object(mail_cmd, "handle", return_value=page):
            webapp.call(a, "mail", {"action": "list", "folder": "7", "limit": 40})
            self.assertEqual(webapp.unread_snapshot(a)["folders"], {"7": 2})
            webapp.call(a, "mail", {"action": "list", "folder": "7", "cursor": "c", "limit": 40})
        self.assertEqual(webapp.unread_snapshot(a)["total"], 2)

    def test_apply_tree_add_change_delete(self):
        from outlook_activesync_mcp.wbxml import el
        box = {"by_id": {}}
        tree = el("AirSync", "Sync",
                  el("AirSync", "Add",
                     el("AirSync", "ServerId", text="1"),
                     el("AirSync", "ApplicationData",
                        el("Email", "Subject", text="Hello"),
                        el("Email", "Read", text="0"))),
                  el("AirSync", "Change",
                     el("AirSync", "ServerId", text="1"),
                     el("AirSync", "ApplicationData",
                        el("Email", "Read", text="1"))),
                  el("AirSync", "Add",
                     el("AirSync", "ServerId", text="2"),
                     el("AirSync", "ApplicationData",
                        el("Email", "Subject", text="Gone"),
                        el("Email", "Read", text="0"))),
                  el("AirSync", "Delete",
                     el("AirSync", "ServerId", text="2")))
        webapp._mail_apply_tree(tree, "inbox", "Inbox",
                                ["subject", "from", "received", "is_read", "preview", "thread_topic"], box)
        from outlook_activesync_mcp.models import pack_item_id
        iid = pack_item_id("inbox", "1")
        self.assertEqual(set(box["by_id"]), {iid})
        self.assertTrue(box["by_id"][iid]["is_read"])
        self.assertEqual(box["by_id"][iid]["subject"], "Hello")


def _mail_add(sid: str, received: str, subject: str = "m"):
    return el("AirSync", "Add", el("AirSync", "ServerId", text=sid),
              el("AirSync", "ApplicationData",
                 el("Email", "Subject", text=subject),
                 el("Email", "DateReceived", text=received),
                 el("Email", "Read", text="0")))


class _DeltaClient:
    """Enough of EasClient for the cached-folder delta path."""

    def __init__(self, rounds):
        self.rounds = list(rounds)  # [(adds, more, gen)]
        self.store = _Store()

    def foldersync(self, force=False):
        return [{"id": "14", "name": "Inbox", "type": "2", "parent_id": "0"}]

    def sync_round(self, cid, *, generation=None, window=None, options_children=None,
                   command_children=None, get_changes=True):
        adds, more, gen = self.rounds.pop(0)
        return el("AirSync", "Sync", el("AirSync", "Collections", el("AirSync", "Collection",
                  el("AirSync", "Commands", *adds)))), more, gen


class MailDeltaCacheTest(unittest.TestCase):
    """Regression: newest mail vanished from Inbox. The first dump returns the
    newest page with MoreAvailable; the next refresh continued that key, got the
    older remainder as ≥40 Adds, and a 'looks like a re-dump' heuristic wiped
    the cache — leaving only old mail (newest shown: 5 days ago)."""

    def _run(self, delta_rounds):
        a = make_acct()
        a.backend.client = _DeltaClient(delta_rounds)
        newest = [{"item_id": f"14:n{i}", "received": f"2026-09-23 1{i}:00", "is_read": False} for i in range(3)]
        dump = {"ok": True, "items": newest, "has_more": True, "next_cursor": "c1"}
        with mock.patch.object(webapp, "_mail_fetch_pages", return_value=dump), \
             mock.patch.object(webapp, "_mail_store_gen", return_value=7):
            webapp._mail_list_paged(a, a.backend, {"folder": "14", "limit": 40, "filter": 5})
            return webapp._mail_list_paged(a, a.backend, {"folder": "14", "limit": 40, "filter": 5})

    def test_continuing_an_unfinished_dump_keeps_the_newest_mail(self):
        older = [_mail_add(f"o{i}", f"2026-08-{10 + i % 18:02d}T10:00:00.000Z") for i in range(45)]
        r = self._run([(older, False, 7)])
        ids = [m["item_id"] for m in r["items"]]
        self.assertEqual(ids[:3], ["14:n2", "14:n1", "14:n0"], "newest first, not wiped")
        self.assertEqual(len(ids), 48)

    def test_other_fields_never_reuse_the_ui_box(self):
        a = make_acct()
        a.backend.client = _DeltaClient([])
        dump = {"ok": True, "items": [{"item_id": "14:x", "received": "2026-09-23 10:00"}], "has_more": False}
        with mock.patch.object(webapp, "_mail_fetch_pages", return_value=dump) as f, \
             mock.patch.object(webapp, "_mail_store_gen", return_value=7):
            webapp._mail_list_paged(a, a.backend, {"folder": "14", "filter": 5, "fields": ["subject", "preview"]})
            webapp._mail_list_paged(a, a.backend, {"folder": "14", "filter": 5, "fields": ["subject"]})
        self.assertEqual(f.call_count, 2, "a different field set gets its own dump")

    def test_server_side_reprime_replaces_the_cache(self):
        fresh = [_mail_add("f1", "2026-09-23T15:00:00.000Z")]
        r = self._run([(fresh, False, 8)])  # generation changed → new listing
        self.assertEqual([m["subject"] for m in r["items"]], ["m"], "old cache dropped, fresh listing kept")


class FolderParsingTest(unittest.TestCase):
    @staticmethod
    def _row(tag, sid, name=None, typ=None, parent=None):
        kids = [el("FolderHierarchy", "ServerId", text=sid)]
        if name:
            kids.append(el("FolderHierarchy", "DisplayName", text=name))
        if typ:
            kids.append(el("FolderHierarchy", "Type", text=typ))
        if parent:
            kids.append(el("FolderHierarchy", "ParentId", text=parent))
        return el("FolderHierarchy", tag, *kids)

    def test_add_update_delete_and_key(self):
        tree = el("FolderHierarchy", "FolderSync",
                  el("FolderHierarchy", "SyncKey", text="7"),
                  el("FolderHierarchy", "Changes",
                     self._row("Add", "1", "Входящие", "2", "0"),
                     self._row("Update", "9", "Проекты", None, "1"),
                     self._row("Add", "5", "Старое", "12"),
                     self._row("Delete", "5")))
        rows, key = webapp._parse_folder_changes(tree)
        self.assertEqual(key, "7")
        self.assertEqual(set(rows), {"1", "9"})
        self.assertEqual(rows["9"], {"id": "9", "name": "Проекты", "type": "12", "parent_id": "1"})

    def test_empty_tree(self):
        rows, key = webapp._parse_folder_changes(el("FolderHierarchy", "FolderSync"))
        self.assertEqual((rows, key), ({}, "0"))

    def test_foldercreate_tokens_patch_is_idempotent(self):
        from outlook_activesync_mcp.wbxml import tables
        webapp._ensure_foldercreate_tokens()
        webapp._ensure_foldercreate_tokens()
        ns = tables.TAGS[7][0]
        self.assertEqual(tables.TOKENS[(ns, "FolderCreate")], (7, 0x13))


class _Store:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, k, default=None):
        return self.data.get(k, default)

    def transaction(self):
        store = self

        class _T:
            def __enter__(self):
                return store.data

            def __exit__(self, *a):
                return False
        return _T()


class _FolderClient:
    """Stalwart-like: user folders only arrive as Update on the follow-up round."""

    def __init__(self, store=None):
        self.store = store or _Store()
        self.commands = []

    def ensure_provisioned(self):
        pass

    def command(self, cmd, node, **kw):
        from outlook_activesync_mcp.wbxml import find, text_of
        key = text_of(find(node, "FolderHierarchy", "SyncKey"))
        self.commands.append((cmd, key))
        row = FolderParsingTest._row
        if key == "0":
            return el("FolderHierarchy", "FolderSync", el("FolderHierarchy", "SyncKey", text="1"),
                      el("FolderHierarchy", "Changes", row("Add", "inbox", "Inbox", "2", "0")))
        return el("FolderHierarchy", "FolderSync", el("FolderHierarchy", "SyncKey", text="2"),
                  el("FolderHierarchy", "Changes", row("Update", "i/f355f730", "Проекты", "12", "inbox")))


class DeepFolderSyncTest(unittest.TestCase):
    """Regression: 'папка 'i/f355f730' не найдена' — the client's own shallow
    FolderSync overwrote the deep tree, so opening a user folder failed."""

    def setUp(self):
        webapp._patch_deep_foldersync()
        from outlook_activesync_mcp.client import EasClient
        self.foldersync = EasClient.foldersync

    def test_client_foldersync_sees_user_folders(self):
        c = _FolderClient()
        ids = [f["id"] for f in self.foldersync(c, force=True)]
        self.assertEqual(sorted(ids), ["i/f355f730", "inbox"])
        self.assertEqual(c.store.data["folders"]["sync_key"], "2")

    def test_open_user_folder_resolves(self):
        c = _FolderClient()
        c.foldersync = lambda force=False: self.foldersync(c, force=force)
        self.assertEqual(mail_cmd.resolve_collection(c, "i/f355f730"), ("i/f355f730", "Проекты"))

    def test_shallow_cache_from_older_runs_is_replaced(self):
        from outlook_activesync_mcp.client import _now_epoch_iso
        c = _FolderClient(_Store({"folders": {"cached_at": _now_epoch_iso(),
                                              "tree": [{"id": "inbox", "name": "Inbox", "type": "2", "parent_id": "0"}]}}))
        self.assertIn("i/f355f730", [f["id"] for f in self.foldersync(c)])
        n = len(c.commands)
        self.foldersync(c)  # fresh deep cache → no network
        self.assertEqual(len(c.commands), n)


class FolderCreateValidationTest(unittest.TestCase):
    def test_rejects_empty_and_long_names_without_network(self):
        a = make_acct()
        a.backend = None  # any backend use would crash
        self.assertEqual(webapp.folder_create(a, "  ")["error"], "bad_request")
        self.assertEqual(webapp.folder_create(a, "x" * 65)["error"], "bad_request")


class UnreadTest(unittest.TestCase):
    def test_recount_and_snapshot(self):
        a = make_acct()
        webapp.recount_unread_from_items(a, "3", [{"is_read": False}, {}])
        webapp.recount_unread_from_items(a, "", [{"is_read": False}])
        self.assertEqual(webapp.unread_snapshot(a)["folders"], {"3": 2})
        self.assertEqual(webapp.refresh_unread(a, force=True)["total"], 2)


def _ev(start: datetime, minutes: int = 60, **kw) -> dict:
    """Server-shaped event: UTC start_iso + local 'YYYY-MM-DD HH:MM' end."""
    end = start + timedelta(minutes=minutes)
    return {"subject": kw.pop("subject", "e"), "start_iso": start.astimezone().isoformat(),
            "end": end.strftime("%Y-%m-%d %H:%M"), **kw}


class CalendarCacheTest(unittest.TestCase):
    def test_cold_cache_waits_for_first_load(self):
        """First request on an account with no cache must wait for the load,
        not answer 'calendar_unavailable' straight away."""
        today = datetime.combine(date.today(), datetime.min.time())
        items = [_ev(today.replace(hour=10), subject="standup")]

        def slow_list(client, action, **kw):
            time.sleep(0.3)
            return {"ok": True, "items": items}

        a = make_acct("seller")
        with mock.patch.object(cal_cmd, "handle", side_effect=slow_list):
            r = webapp.cal_events(a, date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat())
        self.assertTrue(r["ok"], r)
        self.assertEqual([e["subject"] for e in r["items"]], ["standup"])

    def _loaded(self, items):
        a = make_acct()
        today = date.today()
        a.cal.update(items=items, ts=time.time(), loaded=True,
                     range=(today - timedelta(days=7), today + timedelta(days=60)))
        return a

    def test_window_filter_keeps_overlapping_events(self):
        day = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
        a = self._loaded([
            _ev(day.replace(hour=9), subject="in"),
            _ev(day - timedelta(hours=1), 120, subject="overnight"),
            _ev(day + timedelta(days=1), subject="next-day"),
            _ev(day - timedelta(hours=2), 60, subject="before"),
            _ev(day.replace(hour=12), 0, subject="zero-length"),
        ])
        r = webapp.cal_events(a, day.date().isoformat(), (day.date() + timedelta(days=1)).isoformat())
        self.assertEqual(sorted(e["subject"] for e in r["items"]), ["in", "overnight", "zero-length"])

    def test_outside_cached_window_goes_to_server(self):
        a = self._loaded([])
        far = date.today() + timedelta(days=200)
        with mock.patch.object(webapp, "call", return_value={"ok": True, "items": ["x"]}) as c:
            r = webapp.cal_events(a, far.isoformat(), (far + timedelta(days=1)).isoformat())
        self.assertEqual(r["items"], ["x"])
        self.assertEqual(c.call_args.args[2]["action"], "list")

    def test_refresh_failure_reports_error(self):
        a = make_acct()
        with mock.patch.object(cal_cmd, "handle", side_effect=RuntimeError("401")):
            r = webapp.cal_events(a, date.today().isoformat(), date.today().isoformat())
        self.assertEqual((r["ok"], r["error"], r["message"]), (False, "calendar_unavailable", "401"))

    def test_concurrent_refreshes_collapse_into_one(self):
        calls = []

        def slow(client, action, **kw):
            calls.append(1)
            time.sleep(0.2)
            return {"ok": True, "items": []}

        a = make_acct()
        with mock.patch.object(cal_cmd, "handle", side_effect=slow):
            ts = [threading.Thread(target=webapp.cal_refresh_wait, args=(a,)) for _ in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(len(calls), 1)
        self.assertTrue(a.cal["loaded"])


def _mime_with_parts() -> bytes:
    m = EmailMessage()
    m["From"] = "Иван Петров <ivan@bank.test>"
    m["To"] = "me@bank.test"
    m["Cc"] = "a@x.test, b@x.test"
    m["Subject"] = "Отчёт"
    m.set_content("plain version")
    m.add_alternative('<p>Hi <img src="cid:logo123"></p>', subtype="html")
    html = m.get_payload()[1]
    html.add_related(b"\x89PNGlogo", maintype="image", subtype="png", cid="<logo123>")
    m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="отчёт.pdf")
    m.add_attachment(b"\x89PNGother", maintype="image", subtype="png", filename="photo.png")
    return bytes(m)


class RenderMessageTest(unittest.TestCase):
    def setUp(self):
        self.a = make_acct(backend=FakeBackend({"1:1": _mime_with_parts()}))

    def test_render_inlines_cid_and_lists_real_attachments(self):
        r = webapp.render_message(self.a, "1:1")
        self.assertEqual(r["subject"], "Отчёт")
        self.assertEqual(r["from"], [{"name": "Иван Петров", "address": "ivan@bank.test"}])
        self.assertEqual([x["address"] for x in r["cc"]], ["a@x.test", "b@x.test"])
        self.assertIn("data:image/png;base64,", r["html"])
        self.assertNotIn("cid:logo123", r["html"])
        self.assertEqual([x["name"] for x in r["attachments"]], ["отчёт.pdf", "photo.png"])
        self.assertEqual(r["attachments"][0]["size"], len(b"%PDF-1.4"))

    def test_attachment_bytes_by_index(self):
        name, ctype, data = webapp.attachment_bytes(self.a, "1:1", 0)
        self.assertEqual((name, ctype, data), ("отчёт.pdf", "application/pdf", b"%PDF-1.4"))
        self.assertIsNone(webapp.attachment_bytes(self.a, "1:1", 5))

    def test_mime_is_cached(self):
        webapp.render_message(self.a, "1:1")
        self.a.backend.mime.clear()
        self.assertEqual(webapp.render_message(self.a, "1:1")["subject"], "Отчёт")

    def test_missing_message_is_gone(self):
        with self.assertRaises(webapp.Gone):
            webapp.render_message(self.a, "9:9")

    def test_plain_text_message(self):
        m = EmailMessage()
        m["Subject"] = "t"
        m.set_content("hello")
        self.a.backend.mime["2:2"] = bytes(m)
        r = webapp.render_message(self.a, "2:2")
        self.assertEqual((r["html"], r["text"].strip(), r["attachments"]), (None, "hello", []))


_ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "METHOD:REQUEST", "BEGIN:VTIMEZONE", "TZID:Russian Standard Time",
    "BEGIN:STANDARD", "DTSTART:16010101T000000", "END:STANDARD", "END:VTIMEZONE",
    "BEGIN:VEVENT", "UID:abc-123", "SEQUENCE:2",
    'ORGANIZER;CN="Грекова Владена Дмитриевна":mailto:VDGrekova@alfabank.ru',
    "SUMMARY;LANGUAGE=ru-RU:Обучение вайбкодингу :)\\, часть 1",
    'DTSTART;TZID="(UTC+03:00) Moscow, St. Petersburg":20260924T100000',
    'DTEND;TZID="(UTC+03:00) Moscow, St. Petersburg":20260924T110000',
    "LOCATION:https://alfabank.ktalk.ru/vladena",
    "DESCRIPTION:длинное описание, которое", " продолжается на следующей строке",
    "END:VEVENT", "END:VCALENDAR", ""])


def _invite_mime(ics=_ICS) -> bytes:
    m = EmailMessage()
    m["Subject"] = "Обучение вайбкодингу :)"
    m["From"] = "VDGrekova@alfabank.ru"
    m.set_content("https://alfabank.ktalk.ru/vladena")
    m.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="вложение-1.ics")
    return bytes(m)


class InvitationInMailTest(unittest.TestCase):
    def setUp(self):
        self.a = make_acct(email="me@bank.test", backend=FakeBackend({"14:1": _invite_mime()}))

    def test_invitation_card_data(self):
        r = webapp.render_message(self.a, "14:1")
        inv = r["invite"]
        self.assertEqual(inv["method"], "request")
        self.assertEqual(inv["subject"], "Обучение вайбкодингу :), часть 1")
        self.assertEqual((inv["start"], inv["end"]), ("2026-09-24 10:00", "2026-09-24 11:00"))
        self.assertEqual(inv["location"], "https://alfabank.ktalk.ru/vladena")
        self.assertEqual(inv["organizer"], {"name": "Грекова Владена Дмитриевна", "address": "VDGrekova@alfabank.ru"})
        self.assertEqual(r["attachments"], [], "the .ics is shown as the card, not as a file")

    def test_plain_mail_has_no_invite(self):
        m = EmailMessage(); m["Subject"] = "x"; m.set_content("y")
        self.a.backend.mime["14:2"] = bytes(m)
        self.assertIsNone(webapp.render_message(self.a, "14:2")["invite"])

    def test_respond_via_server(self):
        with mock.patch.object(cal_cmd, "handle", return_value={"ok": True, "items": [{}]}) as h, \
             mock.patch.object(webapp, "cal_refresh_bg"):
            r = webapp.invite_respond(self.a, "14:1", "accept")
        self.assertEqual(r, {"ok": True, "via": "server"})
        self.assertEqual(h.call_args.kwargs["item_id"], "14:1")

    def test_falls_back_to_itip_reply_by_mail(self):
        from outlook_activesync_mcp.errors import EasStatusError
        with mock.patch.object(cal_cmd, "handle", side_effect=EasStatusError("MeetingResponse", 2, "x")):
            r = webapp.invite_respond(self.a, "14:1", "tentative")
        self.assertEqual((r["ok"], r["via"], r["organizer"]), (True, "mail", "VDGrekova@alfabank.ru"))
        sent = webapp.parse_raw(self.a.backend.sent[0])
        self.assertEqual(sent["To"], "VDGrekova@alfabank.ru")
        self.assertTrue(str(sent["Subject"]).startswith("Под вопросом: Обучение"))
        ics = next(p for p in sent.walk() if p.get_content_type() == "text/calendar").get_content()
        for line in ("METHOD:REPLY", "UID:abc-123", "SEQUENCE:2", "PARTSTAT=TENTATIVE", "mailto:me@bank.test",
                     'DTSTART;TZID="(UTC+03:00) Moscow, St. Petersburg":20260924T100000'):
            self.assertIn(line, ics)

    def test_bad_response_rejected(self):
        self.assertEqual(webapp.invite_respond(self.a, "14:1", "maybe")["error"], "bad_request")


class RsvpFallbackTest(unittest.TestCase):
    """Live: Exchange answered MeetingResponse with status 2 (from a letter) and
    3 (from the calendar). The answer must still reach the organizer."""

    def setUp(self):
        from outlook_activesync_mcp.models import pack_item_id
        self.a = make_acct(email="me@bank.test", backend=FakeBackend({"14:1": _invite_mime()}))
        self.occ = pack_item_id("20", "srv1", instance="20260924T070000Z")
        today = date.today()
        self.a.cal.update(loaded=True, ts=time.time(), range=(today - timedelta(days=7), today + timedelta(days=60)),
                          items=[{"item_id": self.occ, "uid": "abc-123", "subject": "Daily",
                                  "start_iso": "2026-09-24T07:00:00Z", "end": "2026-09-24 10:30",
                                  "organizer": {"name": "Org", "address": "org@bank.test"}}])

    def test_calendar_answer_falls_back_to_mail_for_one_occurrence(self):
        from outlook_activesync_mcp.errors import EasStatusError
        with mock.patch.object(cal_cmd, "handle", side_effect=EasStatusError("MeetingResponse", 3, "x")):
            r = webapp.call(self.a, "events", {"action": "respond", "item_id": self.occ, "response": "decline"})
        self.assertEqual((r["ok"], r["via"]), (True, "mail"))
        sent = webapp.parse_raw(self.a.backend.sent[0])
        self.assertEqual(sent["To"], "org@bank.test")
        ics = next(p for p in sent.walk() if p.get_content_type() == "text/calendar").get_content()
        for line in ("METHOD:REPLY", "UID:abc-123", "RECURRENCE-ID:20260924T070000Z", "PARTSTAT=DECLINED",
                     "DTSTART:20260924T070000Z"):
            self.assertIn(line, ics)

    def test_mail_answer_survives_full_sync_and_restart(self):
        """Code 3 → the answer went by mail, the server copy never changes: a
        full sync must not bring a declined meeting back (or reset «принял»)."""
        from outlook_activesync_mcp.errors import EasStatusError
        self.a.cal["items"][0]["end"] = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        server_copy = [dict(self.a.cal["items"][0], response_type="not_responded")]
        try:
            with mock.patch.object(cal_cmd, "handle", side_effect=EasStatusError("MeetingResponse", 3, "x")), \
                    mock.patch.object(webapp, "cal_refresh_bg"):
                r = webapp.call(self.a, "events", {"action": "respond", "item_id": self.occ, "response": "accept"})
            self.assertEqual(r["via"], "mail")
            webapp._cal_overlay_prune(self.a.cal, time.time() + 1)  # full sync: temporary layer gone
            self.a.cal["items"] = server_copy
            self.assertEqual(webapp._cal_item(self.a, item_id=self.occ)["response_type"], "accepted")
            b = make_acct(email="me@bank.test")  # restart: answers come back from disk
            b.cal.update(items=server_copy)
            webapp._rsvp_answers(b)
            self.assertEqual(webapp._cal_item(b, item_id=self.occ)["response_type"], "accepted")
            with mock.patch.object(cal_cmd, "handle", side_effect=EasStatusError("MeetingResponse", 3, "x")):
                webapp.call(self.a, "events", {"action": "respond", "item_id": self.occ, "response": "decline"})
            webapp._cal_overlay_prune(self.a.cal, time.time() + 1)
            self.assertIsNone(webapp._cal_item(self.a, item_id=self.occ), "declined stays hidden")
        finally:
            webapp._rsvp_path(self.a).unlink(missing_ok=True)

    def test_letter_answer_retries_on_the_calendar_item(self):
        from outlook_activesync_mcp.errors import EasStatusError
        seen = []

        def handle(client, action, **kw):
            seen.append(kw["item_id"])
            if kw["item_id"] == "14:1":
                raise EasStatusError("MeetingResponse", 2, "x")
            return {"ok": True, "items": [{}]}

        with mock.patch.object(cal_cmd, "handle", side_effect=handle), mock.patch.object(webapp, "cal_refresh_bg"):
            r = webapp.invite_respond(self.a, "14:1", "accept")
        self.assertEqual(r, {"ok": True, "via": "server"})
        self.assertEqual(seen, ["14:1", self.occ], "letter first, then the same meeting in the calendar by UID")
        self.assertEqual(self.a.backend.sent, [])


class WarmFoldersTest(unittest.TestCase):
    def test_warms_stale_skips_fresh_and_caps(self):
        a = make_acct()
        a.mail_boxes["fresh"] = {"ts": time.time()}
        seen = []
        with mock.patch.object(webapp, "call", side_effect=lambda acct, d, p: seen.append(p["folder"]) or {"ok": True}):
            r = webapp.warm_folders(a, ["fresh", "a", "b"] + [f"x{i}" for i in range(20)], 5, ["subject"])
            for _ in range(50):
                if len(seen) >= webapp._WARM_MAX - 1:
                    break
                time.sleep(0.02)
        self.assertEqual(r["warming"], webapp._WARM_MAX)
        self.assertNotIn("fresh", seen)
        self.assertEqual(seen[:2], ["a", "b"])


class SendOutageTest(unittest.TestCase):
    """Live: Seller's SendMail waited 60 s and answered status 120 for every
    message. After one such failure, fail fast with words a person understands."""

    def test_status_120_is_explained_and_then_fails_fast(self):
        from outlook_activesync_mcp.commands import mail_write
        from outlook_activesync_mcp.errors import EasStatusError
        a = make_acct("seller", email="me@seller.test")
        with mock.patch.object(mail_cmd, "handle", side_effect=EasStatusError("SendMail", 120, "status 120")) as h:
            r1 = webapp.call(a, "mail", {"action": "send", "to": ["x@y"], "subject": "s", "body": "b"})
            r2 = webapp.call(a, "mail", {"action": "send", "to": ["x@y"], "subject": "s", "body": "b"})
        self.assertIn("отправка почты на сервере не работает", r1["message"])
        self.assertEqual(r2["error"], "send_down")
        self.assertEqual(h.call_count, 1, "second send did not hit the broken server again")

    def test_invites_go_in_background_and_report_back(self):
        from outlook_activesync_mcp.errors import EasStatusError
        a = make_acct("seller", email="me@seller.test")
        a.backend.send = mock.Mock(side_effect=EasStatusError("SendMail", 120, "status 120"))
        webapp._send_invites_bg(a, {"subject": "План", "start": "2026-10-01T10:00", "end": "2026-10-01T11:00"}, ["x@y.test"])
        snap = webapp.unread_snapshot(a)
        self.assertEqual(len(snap["notices"]), 1)
        self.assertTrue(snap["notices"][0]["error"])
        self.assertIn("приглашения письмом не ушли", snap["notices"][0]["text"])
        self.assertEqual(webapp.unread_snapshot(a)["notices"], [], "each notice is delivered once")


class InviteTest(unittest.TestCase):
    def test_ics_request_excludes_self_and_escapes(self):
        a = make_acct(email="me@bank.test")
        sent = webapp.send_invites(a, {"subject": "План; итоги, Q3", "start": "2026-10-01T10:00",
                                       "end": "2026-10-01T11:00", "location": "Комн. 5"},
                                   ["x@y.test", "ME@bank.test", "not-an-email"])
        self.assertEqual(sent, ["x@y.test"])
        raw = a.backend.sent[0].decode("utf-8", "replace")
        self.assertIn("METHOD:REQUEST", raw)
        self.assertIn("SUMMARY:План\\; итоги\\, Q3", raw)
        self.assertIn("mailto:x@y.test", raw)
        self.assertNotIn("ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:ME@bank.test", raw)

    def test_no_recipients_sends_nothing(self):
        a = make_acct(email="me@bank.test")
        self.assertEqual(webapp.send_invites(a, {"start": "2026-10-01T10:00", "end": "2026-10-01T11:00"}, ["me@bank.test"]), [])
        self.assertEqual(a.backend.sent, [])


class AccountConfigTest(unittest.TestCase):
    def setUp(self):
        webapp.bridge.CONF_PATH.unlink(missing_ok=True)
        KEYCHAIN.clear()

    def test_first_run_config_has_default_server_and_needs_setup(self):
        cfg = webapp.ensure_config()
        self.assertEqual(cfg["url"], webapp.DEFAULT_EAS_URLS["main"])
        self.assertTrue(webapp.account_needs_setup(cfg))
        self.assertEqual(os.stat(webapp.bridge.CONF_PATH).st_mode & 0o777, 0o600)
        view = webapp.get_account_config()
        self.assertEqual(view["seller"]["url"], webapp.DEFAULT_EAS_URLS["seller"])
        self.assertFalse(view["main"]["has_password"])

    def test_plaintext_password_migrates_to_keychain(self):
        webapp._write_config({"url": "u", "username": "user", "password": "s3cret"})
        self.assertEqual(webapp.get_password("main", "s3cret"), "s3cret")
        self.assertEqual(KEYCHAIN["main"], "s3cret")
        self.assertNotIn("password", json.loads(webapp.bridge.CONF_PATH.read_text()))
        self.assertFalse(webapp.account_needs_setup(webapp.ensure_config()))

    def test_save_rejects_incomplete_input_without_touching_config(self):
        self.assertEqual(webapp.save_account_config({"target": "x"})["error"], "bad_request")
        self.assertEqual(webapp.save_account_config({"target": "main", "url": "u"})["error"], "bad_request")
        r = webapp.save_account_config({"target": "main", "url": "u", "username": "n", "email": "e@x"})
        self.assertIn("пароль", r["message"])
        self.assertEqual(webapp.ensure_config().get("username"), "")  # nothing half-saved

    def test_load_accounts_seller_only_with_username(self):
        KEYCHAIN["main"] = "pw"
        accts = webapp.load_accounts({"username": "u", "second": {"username": " ", "url": "s"}})
        self.assertEqual(list(accts), ["main"])
        accts = webapp.load_accounts({"username": "u", "second": {"username": "s@x", "url": "https://s"}})
        self.assertEqual(list(accts), ["main", "seller"])
        self.assertTrue(accts["seller"].green)
        self.assertEqual(accts["main"].cfg["url"], webapp.DEFAULT_EAS_URLS["main"])


if __name__ == "__main__":
    unittest.main()


class _DeltaFolderClient(_FolderClient):
    """Hierarchy delta off key "2": one folder added, one deleted."""

    def command(self, cmd, node, **kw):
        from outlook_activesync_mcp.wbxml import find, text_of
        key = text_of(find(node, "FolderHierarchy", "SyncKey"))
        if key != "2":
            return super().command(cmd, node, **kw)
        self.commands.append((cmd, key))
        row = FolderParsingTest._row
        return el("FolderHierarchy", "FolderSync", el("FolderHierarchy", "Status", text="1"),
                  el("FolderHierarchy", "SyncKey", text="3"),
                  el("FolderHierarchy", "Changes", row("Add", "new", "Новая", "12", "inbox"),
                     row("Delete", "i/f355f730")))


class FolderDeltaTest(unittest.TestCase):
    """Refresh of the folder list is a FolderSync delta off the stored key,
    not the SyncKey=0 relist (two round-trips, whole tree) every time."""

    def setUp(self):
        webapp._patch_deep_foldersync()
        from outlook_activesync_mcp.client import EasClient
        self.foldersync = EasClient.foldersync

    def _primed(self, cls=_DeltaFolderClient):
        c = cls()
        self.foldersync(c, force=True)  # full: keys 0 → 1 → stored "2"
        c.commands.clear()
        return c

    def test_forced_refresh_is_a_delta(self):
        c = self._primed()
        ids = sorted(f["id"] for f in self.foldersync(c, force=True))
        self.assertEqual(c.commands, [("FolderSync", "2")])
        self.assertEqual(ids, ["inbox", "new"])
        self.assertEqual(c.store.data["folders"]["sync_key"], "3")

    def test_own_create_or_delete_relists_in_full(self):
        c = self._primed()
        self.foldersync(c, force=True, full=True)
        self.assertEqual(c.commands[0], ("FolderSync", "0"))

    def test_dead_key_falls_back_to_full_tree(self):
        class Dead(_FolderClient):
            def command(self, cmd, node, **kw):
                from outlook_activesync_mcp.wbxml import find, text_of
                if text_of(find(node, "FolderHierarchy", "SyncKey")) == "2":
                    self.commands.append((cmd, "2"))
                    raise RuntimeError("status 9")
                return super().command(cmd, node, **kw)
        c = self._primed(Dead)
        ids = sorted(f["id"] for f in self.foldersync(c, force=True))
        self.assertEqual([k for _, k in c.commands], ["2", "0", "1"])
        self.assertEqual(ids, ["i/f355f730", "inbox"])


class MeetingChangesTest(unittest.TestCase):
    """Cancelled / moved meetings arriving in a calendar delta."""

    def test_meeting_status_bits(self):
        ms = webapp._meeting_status
        self.assertEqual([ms(v) for v in ("0", "1", "3", "5", "7", "9", "11", "13", "15", None, "x")],
                         ["appointment", "meeting", "meeting", "cancelled", "cancelled", "meeting",
                          "meeting", "cancelled", "cancelled", None, None])

    @staticmethod
    def _series(day):
        """Daily 10:00 series; tomorrow moved to 15:00, the day after cancelled."""
        C = "Calendar"
        t = lambda d, h: (day + timedelta(days=d)).strftime("%Y%m%dT") + f"{h:02d}0000Z"
        return el("AirSync", "ApplicationData",
                  el(C, "Subject", text="Планёрка"), el(C, "StartTime", text=t(0, 10)),
                  el(C, "EndTime", text=t(0, 11)), el(C, "OrganizerEmail", text="boss@bank.test"),
                  el(C, "MeetingStatus", text="3"),
                  el(C, "Recurrence", el(C, "Type", text="0"), el(C, "Interval", text="1")),
                  el(C, "Exceptions",
                     el(C, "Exception", el(C, "ExceptionStartTime", text=t(1, 10)),
                        el(C, "StartTime", text=t(1, 15)), el(C, "EndTime", text=t(1, 16))),
                     el(C, "Exception", el(C, "ExceptionStartTime", text=t(2, 10)),
                        el(C, "MeetingStatus", text="7"))))

    def test_moved_and_cancelled_occurrences_keep_the_series_fields(self):
        day = date.today()
        tree = el("AirSync", "Sync", el("AirSync", "Change", el("AirSync", "ServerId", text="1:5"),
                                          self._series(datetime.combine(day, datetime.min.time()))))
        masters: dict = {}
        webapp._cal_apply_tree(tree, masters)
        self.assertEqual(masters["1:5"]["meeting_status"], "meeting")
        items, _ = webapp._cal_expand(masters, "1", day, day + timedelta(days=4), webapp.CAL_FIELDS)
        by_day = {e["start_iso"][:10]: e for e in items}
        d = lambda n: (day + timedelta(days=n)).isoformat()
        moved, cancelled = by_day[d(1)], by_day[d(2)]
        self.assertEqual((moved["subject"], moved["start_iso"][11:16]), ("Планёрка", "15:00"))
        self.assertEqual(moved["organizer"]["address"], "boss@bank.test")
        self.assertEqual((cancelled["subject"], cancelled["meeting_status"]), ("Планёрка", "cancelled"))
        self.assertEqual(by_day[d(3)]["meeting_status"], "meeting")


class CalendarOddSeriesTest(unittest.TestCase):
    """Live 1.2.9 regression: an Exception without ExceptionStartTime made the
    expansion do None + timedelta and the whole Bank calendar failed to load."""

    def test_exception_without_start_and_a_broken_master(self):
        from datetime import timezone
        start = datetime.combine(date.today(), datetime.min.time()).replace(hour=9, tzinfo=timezone.utc)
        masters = {
            "1:1": {"subject": "daily", "start": start, "end": start + timedelta(hours=1),
                    "recurrence": {"type": "0", "interval": "1"},
                    "exceptions": [{"exception_start": "", "deleted": False, "start": None, "end": None,
                                    "subject": "", "location": None}]},
            "1:2": {"subject": "broken", "start": None, "end": None, "recurrence": {"type": "0"}},
            "1:3": {"subject": "one-off", "start": start, "end": start + timedelta(minutes=30)},
        }
        items, _ = webapp._cal_expand(masters, "1", date.today(), date.today() + timedelta(days=2), webapp.CAL_FIELDS)
        subjects = [e["subject"] for e in items]
        self.assertIn("one-off", subjects)
        self.assertGreaterEqual(subjects.count("daily"), 2)

    def test_a_series_that_cannot_expand_is_skipped_not_fatal(self):
        from datetime import timezone
        start = datetime.now(timezone.utc).replace(microsecond=0)
        masters = {"1:1": {"subject": "ok", "start": start, "end": start + timedelta(hours=1)},
                   "1:2": {"subject": "bad", "start": start, "end": start}}
        from outlook_activesync_mcp.commands import calendar as cal_mod
        orig = cal_mod._occurrences

        def occ(master, *a):
            if master.get("subject") == "bad":
                raise TypeError("unsupported operand type(s) for +: 'NoneType' and 'datetime.timedelta'")
            return orig(master, *a)
        with mock.patch.object(cal_mod, "_occurrences", side_effect=occ), self.assertLogs("eas-mail", "WARNING"):
            items, _ = webapp._cal_expand(masters, "1", date.today() - timedelta(days=1),
                                          date.today() + timedelta(days=1), webapp.CAL_FIELDS)
        self.assertEqual([e["subject"] for e in items], ["ok"])


class CalendarDiskCacheTest(unittest.TestCase):
    """Launch continues the calendar from disk with a delta, not a 30 s prime."""

    def setUp(self):
        from datetime import timezone
        self.a = make_acct()
        start = datetime.now(timezone.utc).replace(microsecond=0)
        today = date.today()
        self.win = (today - timedelta(days=7), today + timedelta(days=60))
        self.a.cal.update(masters={"1:1": {"subject": "saved", "start": start, "end": start + timedelta(hours=1)}},
                          gen=4, cal_id="1", filter="6", ts=123.0, truncated=False)
        webapp._cal_save(self.a)

    def tearDown(self):
        webapp._cal_forget(self.a)

    def test_round_trip_and_owner_check(self):
        b = make_acct()
        self.assertTrue(webapp._cal_load(b, *self.win))
        self.assertEqual((b.cal["gen"], b.cal["filter"], b.cal["ts"]), (4, "6", 123.0))
        self.assertEqual([e["subject"] for e in b.cal["items"]], ["saved"])
        self.assertEqual(os.stat(webapp._cal_cache_path(b)).st_mode & 0o777, 0o600)
        other = make_acct()
        other.cfg = {**other.cfg, "username": "someone-else"}
        self.assertFalse(webapp._cal_load(other, *self.win))

    def test_own_write_drops_the_file(self):
        with mock.patch.object(webapp, "cal_refresh_bg"):
            webapp._cal_after_write(self.a, {"action": "cancel", "item_id": "x"}, {"ok": True})
        self.assertFalse(webapp._cal_cache_path(self.a).exists())


class UnreadSweepTest(unittest.TestCase):
    """Badges for every Inbox / user folder, counted over the whole mail period
    (the box is drained with «Ещё» pages), and an account total without
    Drafts / Sent / Deleted."""

    TREE = [{"id": "inbox", "type": "2"}, {"id": "proj", "type": "12"}, {"id": "sent", "type": "5"},
            {"id": "trash", "type": "4"}]

    def _acct(self):
        a = make_acct()
        tree = self.TREE

        class Client:
            store = _Store({"folders": {"tree": tree}})

            def foldersync(self, force=False):
                return tree
        a.backend.client = Client()
        return a

    def test_sweep_drains_counts_and_totals(self):
        a = self._acct()
        calls = []

        def fake_call(acct, domain, params):
            calls.append((params["folder"], params.get("cursor")))
            fid = params.get("folder") or "inbox"
            box = webapp._mail_box(acct, fid)
            n = len(box["by_id"])
            box["by_id"].update({f"{fid}:{n + i}": {"is_read": i % 2 == 1} for i in range(40)})
            box["complete"] = fid != "inbox" or n >= 40  # Inbox needs one «Ещё» page
            box["next_cursor"] = None if box["complete"] else "c1"
            box["ts"] = time.time()
            return {"ok": True, "items": []}

        with mock.patch.object(webapp, "call", side_effect=fake_call):
            webapp.unread_sweep(a, 5, ["is_read"])
            for _ in range(100):
                if not a.unread_sweeping:
                    break
                time.sleep(0.01)
        snap = webapp.unread_snapshot(a)
        self.assertEqual(calls[0], ("inbox", None), "Inbox first")
        self.assertIn(("inbox", "c1"), calls, "Inbox dump drained with «Ещё»")
        self.assertNotIn("sent", [c[0] for c in calls], "no Sync for Sent / Deleted")
        self.assertEqual(snap["folders"], {"inbox": 40, "proj": 20})
        self.assertEqual((snap["total"], snap["partial"], snap["sweeping"]), (60, [], False))
        n = len(calls)
        with mock.patch.object(webapp, "call", side_effect=fake_call):
            webapp.unread_sweep(a, 5, ["is_read"])  # auto-sync right after: throttled
        self.assertEqual((len(calls), a.unread_sweeping), (n, False))

    def test_total_skips_sent_and_partial_is_reported(self):
        a = self._acct()
        box = webapp._mail_box(a, "inbox")
        box["by_id"] = {"inbox:1": {"is_read": False}}
        box["complete"] = False
        webapp.recount_unread_from_box(a, "inbox", box)
        webapp.recount_unread_from_items(a, "sent", [{"is_read": False}] * 3)
        snap = webapp.unread_snapshot(a)
        self.assertEqual((snap["total"], snap["partial"]), (1, ["inbox"]))
