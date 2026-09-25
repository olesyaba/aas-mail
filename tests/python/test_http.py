"""HTTP layer and UI↔API wiring: guards, token, routes, and the contract between
web/index.html's api() calls and the backend/upstream handlers they reach."""
from __future__ import annotations

import http.client
import inspect
import json
import re
import threading
import time
import unittest
from email.message import EmailMessage

from harness import ROOT, FakeBackend, make_acct, webapp

INDEX = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
HOST = f"127.0.0.1:{webapp.PORT}"


class Client:
    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", webapp.PORT, timeout=10)
        h = {"Host": HOST}
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        payload = r.read()
        conn.close()
        return r, payload

    def api(self, path, body=None, **hdr):
        headers = {"X-Tok": webapp.TOKEN, "Content-Type": "application/json", **hdr}
        r, payload = self.request("POST", path, body if body is not None else {}, headers)
        return r.status, (json.loads(payload) if payload.startswith(b"{") else payload)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = webapp.ThreadingHTTPServer(("127.0.0.1", webapp.PORT), webapp.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = Client()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        webapp.ACCTS.clear()
        webapp.ACCTS["main"] = make_acct()


class GuardTest(ServerTest):
    def test_index_injects_token_and_csp(self):
        r, body = self.c.request("GET", "/")
        self.assertEqual(r.status, 200)
        page = body.decode()
        self.assertNotIn("__TOKEN__", page)
        self.assertIn(f'<meta name="tok" content="{webapp.TOKEN}">', page)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_foreign_host_and_origin_rejected(self):
        self.assertEqual(self.c.request("GET", "/", headers={"Host": "evil.test"})[0].status, 403)
        self.assertEqual(self.c.api("/api/about", Origin="http://evil.test")[0], 403)
        self.assertEqual(self.c.api("/api/about", Origin=f"http://{HOST}")[0], 200)

    def test_token_required(self):
        r, _ = self.c.request("POST", "/api/about", {}, {"X-Tok": "nope"})
        self.assertEqual(r.status, 403)
        self.assertEqual(self.c.request("GET", "/attachment?id=1:1&i=0&t=nope")[0].status, 403)

    def test_bad_json_and_non_object_body(self):
        r, _ = self.c.request("POST", "/api/about", raw=b"{oops", headers={"X-Tok": webapp.TOKEN})
        self.assertEqual(r.status, 400)
        r, _ = self.c.request("POST", "/api/about", raw=b"[1,2]", headers={"X-Tok": webapp.TOKEN})
        self.assertEqual(r.status, 400)

    def test_ui_kit_static_and_traversal(self):
        r, _ = self.c.request("GET", "/ui-kit/tokens.css")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "text/css; charset=utf-8"))
        self.assertIn(self.c.request("GET", "/ui-kit/../../webapp.py")[0].status, (403, 404))
        self.assertEqual(self.c.request("GET", "/ui-kit/missing.css")[0].status, 404)

    def test_font_is_bundled_and_nothing_loads_from_the_internet(self):
        """Golos Text ships inside the app: offline / behind VPN the UI keeps its font,
        and opening the app sends no request to Google Fonts (or any other host)."""
        import re
        html = (webapp.WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(html, r"(?i)<(link|script)[^>]+(href|src)=\"https?://")
        self.assertIn('href="/ui-kit/fonts.css"', html)
        css = (webapp.WEB / "ui-kit" / "fonts.css").read_text(encoding="utf-8")
        files = re.findall(r'url\("fonts/([^"]+\.woff2)"\)', css)
        self.assertGreaterEqual(len(files), 2, "cyrillic and latin at least")
        for name in files:
            r, body = self.c.request("GET", f"/ui-kit/fonts/{name}")
            self.assertEqual((r.status, r.getheader("Content-Type")), (200, "font/woff2"), name)
            self.assertEqual(body[:4], b"wOF2", name)
        self.assertTrue((webapp.WEB / "ui-kit" / "fonts" / "OFL.txt").is_file(), "the font licence ships with it")

    def test_unknown_get_is_404(self):
        self.assertEqual(self.c.request("GET", "/nope")[0].status, 404)


class ApiTest(ServerTest):
    def test_about_matches_app_meta(self):
        code, j = self.c.api("/api/about")
        self.assertEqual(code, 200)
        self.assertEqual(j["version"], webapp.APP_META["version"])
        self.assertEqual(j["defaults"], webapp.DEFAULT_EAS_URLS)

    def test_prefs_roundtrip(self):
        webapp.PREFS_PATH.unlink(missing_ok=True)
        _, j = self.c.api("/api/prefs", {"set": {"cal_view": "day"}})
        self.assertEqual(j["prefs"]["cal_view"], "day")
        _, j = self.c.api("/api/prefs", {})
        self.assertEqual(j["prefs"]["cal_view"], "day")

    def test_accounts_and_unknown_account(self):
        _, j = self.c.api("/api/accounts")
        self.assertEqual([a["id"] for a in j["accounts"]], ["main"])
        _, j = self.c.api("/api/unread", {"acct": "ghost"})
        self.assertEqual(j["error"], "unknown_account")

    def test_unread_snapshot(self):
        webapp.recount_unread_from_items(webapp.ACCTS["main"], "2", [{"is_read": False}])
        _, j = self.c.api("/api/unread", {"refresh": True})
        self.assertEqual((j["folders"], j["total"]), ({"2": 1}, 1))

    def test_message_renders_and_gone(self):
        m = EmailMessage()
        m["Subject"] = "Привет"
        m.set_content("тело")
        webapp.ACCTS["main"].backend.mime["1:1"] = bytes(m)
        _, j = self.c.api("/api/message", {"item_id": "1:1"})
        self.assertEqual((j["ok"], j["subject"]), (True, "Привет"))
        _, j = self.c.api("/api/message", {"item_id": "404:1"})
        self.assertEqual(j["error"], "gone")

    def test_message_does_not_block_other_account_work(self):
        """While a message is being fetched/rendered, the account lock stays free."""
        be = webapp.ACCTS["main"].backend
        m = EmailMessage()
        m["Subject"] = "x"
        m.set_content("y")
        be.mime["1:1"] = bytes(m)
        got = []
        be.fetch_hook = lambda: got.append(
            (lambda r: (r.join(2), r.ok)[1])(_LockProbe(be.lock)))
        self.c.api("/api/message", {"item_id": "1:1"})
        self.assertEqual(got, [True])

    def test_attachment_download(self):
        m = EmailMessage()
        m["Subject"] = "a"
        m.set_content("b")
        m.add_attachment(b"DATA", maintype="application", subtype="octet-stream", filename="файл.bin")
        webapp.ACCTS["main"].backend.mime["1:1"] = bytes(m)
        r, body = self.c.request("GET", f"/attachment?id=1:1&i=0&a=main&t={webapp.TOKEN}")
        self.assertEqual((r.status, body), (200, b"DATA"))
        self.assertIn("filename*=UTF-8''%D1%84", r.getheader("Content-Disposition"))
        self.assertEqual(self.c.request("GET", f"/attachment?id=1:1&i=7&t={webapp.TOKEN}")[0].status, 404)

    def test_events_list_served_from_cache(self):
        from datetime import date, timedelta
        a = webapp.ACCTS["main"]
        a.cal.update(items=[], ts=time.time(), loaded=True,
                     range=(date.today() - timedelta(days=7), date.today() + timedelta(days=60)))
        _, j = self.c.api("/api/events", {"action": "list", "start": date.today().isoformat(),
                                          "end": (date.today() + timedelta(days=1)).isoformat()})
        self.assertEqual((j["ok"], j["items"]), (True, []))


class _LockProbe(threading.Thread):
    """Try the lock from another thread (an RLock is always free to its owner)."""

    def __init__(self, lock):
        super().__init__(daemon=True)
        self.lock, self.ok = lock, False
        self.start()

    def run(self):
        if self.lock.acquire(timeout=1):
            self.ok = True
            self.lock.release()


# -- UI contract ---------------------------------------------------------------

def _split_top(s: str) -> list[str]:
    out, depth, cur, quote = [], 0, "", None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _object_at(src: str, start: int) -> str:
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start + 1:i]
    raise ValueError("unbalanced object")


def ui_api_calls() -> list[tuple[str, str | None, set[str]]]:
    """(domain, action, param keys) for every literal api('<domain>', {...}) in index.html."""
    calls = []
    for m in re.finditer(r"api\('([\w-]+)',\s*", INDEX):
        rest = INDEX[m.end():]
        if not rest.startswith("{"):
            # e.g. api('mail', cond ? {...} : {...}) — each literal branch counts
            objs = [m.end() + k.start() for k in re.finditer(r"\{action:", rest[:400])]
        else:
            objs = [m.end()]
        for pos in objs:
            keys, action = set(), None
            for tok in _split_top(_object_at(INDEX, pos)):
                if tok.startswith("..."):
                    continue
                k = tok.split(":", 1)[0].strip()
                if k == "action":
                    action = tok.split(":", 1)[1].strip().strip("'")
                else:
                    keys.add(k)
            calls.append((m.group(1), action, keys))
    return calls


def upstream_handler(domain: str, action: str):
    from outlook_activesync_mcp.commands import calendar, folders, mail, mail_write, people, settings  # noqa: F401
    if domain == "mail":
        own = {"list": mail._list, "search": mail._search, "get": mail._get}
        return own.get(action) or getattr(mail_write, "_" + action, None)
    if domain == "events":
        return getattr(calendar, "_" + action, None)
    if domain == "people":
        return getattr(people, "_" + action, None)
    return None


class UiContractTest(unittest.TestCase):
    ROUTED = {"prefs", "about", "accounts", "account-config", "update", "unread", "message", "retry", "sync", "invite", "warm",
              "mail", "folders", "events", "people", "settings"}

    def test_parser_sees_the_calls(self):
        self.assertGreater(len(ui_api_calls()), 30)

    def test_every_ui_domain_is_routed(self):
        src = inspect.getsource(webapp.Handler.do_POST)
        for domain, _, _ in ui_api_calls():
            self.assertIn(domain, self.ROUTED, domain)
            if domain not in ("mail", "folders", "events", "people", "settings"):
                self.assertIn(f'"/api/{domain}"', src, domain)

    def test_every_ui_action_exists_and_params_are_accepted(self):
        """Upstream handlers swallow unknown kwargs (**_), so a typo in the UI
        would be silently ignored — check keys against the real signatures."""
        webapp._patch_event_update_attendees()
        from outlook_activesync_mcp.commands import calendar
        checked = 0
        for domain, action, keys in ui_api_calls():
            if domain not in ("mail", "events", "people") or action is None:
                continue
            if (domain, action) == ("events", "list"):
                continue  # served by cal_events (start/end), limit is advisory
            fn = calendar._update if (domain, action) == ("events", "update") else upstream_handler(domain, action)
            self.assertIsNotNone(fn, f"{domain}/{action} has no upstream handler")
            named = {n for n, p in inspect.signature(fn).parameters.items()
                     if p.kind is p.KEYWORD_ONLY or (p.kind is p.POSITIONAL_OR_KEYWORD and n != "client")}
            unknown = keys - named - {"mime_invite", "cache_only", "note", "to", "cc"}  # app-level, handled in webapp
            self.assertFalse(unknown, f"{domain}/{action}: UI sends {sorted(unknown)} not accepted by upstream")
            checked += 1
        self.assertGreater(checked, 15)

    def test_folder_actions_handled_by_webapp(self):
        acts = {a for d, a, _ in ui_api_calls() if d == "folders"}
        self.assertEqual(acts, {"list", "create", "delete"})  # all routed in webapp.api_call

    def test_dom_ids_used_by_script_exist(self):
        script = INDEX[INDEX.index("<script>"):]
        used = set(re.findall(r"\$\('#([\w-]+)'\)", script)) | set(re.findall(r"getElementById\('([\w-]+)'\)", script))
        declared = set(re.findall(r'id="([\w-]+)"', INDEX))
        # ids created dynamically from a prefix template (acctFields: `${prefix}_url`)
        declared |= {f"{p}_{s}" for p in ("m", "s") for s in ("url", "user", "email", "pass")}
        self.assertEqual(used - declared, set())

    def test_token_placeholder_present_once(self):
        self.assertEqual(INDEX.count("__TOKEN__"), 1)


if __name__ == "__main__":
    unittest.main()
