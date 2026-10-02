#!/usr/bin/env python3
"""eas-mail: a local Outlook-style web UI over Exchange ActiveSync.

Serves web/index.html plus a small JSON API on 127.0.0.1 only. Every mail,
calendar and people action goes through the outlook-activesync-mcp client — the
same code path the MCP tools use — so it works with and without VPN.

Browser-side attack surface (any web page can try to hit localhost) is closed by:
loopback bind, Host + Origin checks, and a per-run token that only the page we
serve knows.
"""
from __future__ import annotations

import base64
import email
import email.policy
import json
import logging
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import bridge

log = logging.getLogger("eas-mail")
WEB = Path(__file__).parent / "web"
TOKEN = secrets.token_urlsafe(24)
# Android: any app on the phone can reach 127.0.0.1, and the page embeds TOKEN.
# The shell sends this key with the page request; unset on the Mac (no change).
PAGE_KEY = os.environ.get("EAS_MAIL_PAGE_KEY", "")
bridge.DATA_DIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("EAS_MAIL_PORT", "8780"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
MAX_BODY = 40 * 1024 * 1024

# Product identity (About page + UI chrome).
APP_META = {
    "name": "AAS mail",
    "version": "1.2.45",
    "description": "Локальный клиент почты и календаря Alfa / Alfa-Seller поверх Exchange ActiveSync.",
    "contact_mm": "@olesya_ba",
    "channel_mm": "https://mm.alfaops.ru/platforma-global/channels/aas-mail",
    "thanks_intro": "Спасибо за тест-рейды и светлые идеи:",
    "thanks": [
        {"emoji": "🧚", "title": "фее", "handle": "@vdgrekova", "name": "Грековой Владене"},
        {"emoji": "🧙", "title": "магу", "handle": "@ivan.rachenko", "name": "Раченко Ивану"},
    ],
    "thanks_outro": "Спасибо — вы сделали AAS mail чуть волшебнее ✨",
    # Soft tip jar in About — link + local QR (web/ui-kit/donate-qr.*). No paywall.
    "donate": {
        "title": "На кальян, кофе и антидепрессанты",
        "blurb": "Если AAS mail чуть спас нервы — можно угостить. По желанию, без обязательств.",
        "url": "https://t.tb.ru/pm_short/4G5KYLcCEXx",
        "cta": "Открыть Т-Банк",
        "qr": "/ui-kit/donate-qr.svg",
        "items": [
            {"emoji": "💨", "label": "кальян"},
            {"emoji": "☕", "label": "кофе"},
            {"emoji": "💊", "label": "антидепрессанты"},
        ],
    },
}

# Seeded into empty account-config fields so the settings form has working defaults.
DEFAULT_EAS_URLS = {
    "main": "https://owa.alfabank.ru/Microsoft-Server-ActiveSync",
    "seller": "https://sync.alfaops.ru/Microsoft-Server-ActiveSync",
}

# Mail folder Type codes that carry messages (FolderSync).
MAIL_FOLDER_TYPES = frozenset({"1", "2", "3", "4", "5", "6", "12"})

class Acct:
    """One mailbox: its own ActiveSync client, sync state, calendar cache and MIME cache.
    The client is created on first use, so an account whose login is not working yet
    costs nothing (no login attempts) until its tabs are opened."""

    def __init__(self, aid: str, name: str, cfg: dict, green: bool):
        self.id, self.name, self.cfg, self.green = aid, name, cfg, green
        self.email = cfg.get("email", "")
        self.backend: bridge.EasBackend | None = None
        self._init_lock = threading.Lock()
        self.mime_cache: dict[str, bytes] = {}
        self.mime_lock = threading.Lock()
        self.cal = {"items": [], "ts": 0.0, "loaded": False, "loading": False, "error": None,
                    "range": (None, None), "truncated": False, "gen": None, "cal_id": None,
                    "masters": {}, "filter": None}
        # Per-folder mail cache: SyncKey generation + items, so refresh is a delta
        # (Add/Change/Delete) instead of SyncKey=0 prime every time.
        self.mail_boxes: dict[str, dict] = {}
        # folder_id -> unread count (best-effort, refreshed in background).
        self.unread: dict[str, int] = {}
        self.unread_ts = 0.0
        # Folders whose count covers only part of the period (the box is not drained).
        self.unread_partial: set[str] = set()
        self.unread_sweeping = False
        self.unread_sweep_ts = 0.0  # last finished sweep
        self.unread_lock = threading.Lock()
        # Outgoing mail: when the server's submission is down (Stalwart answered
        # SendMail status 120 after a 60 s wait), fail fast for a while instead of
        # making every send wait a minute. Background results for the UI go to notices.
        self.send_down_until = 0.0
        self.notices: list[dict] = []
        self.cv = threading.Condition()

    def get(self) -> bridge.EasBackend:
        with self._init_lock:
            if self.backend is None:
                self.backend = bridge.EasBackend(self.cfg)
                threading.Thread(target=self.backend.ensure_identity, daemon=True).start()
        return self.backend


ACCTS: dict[str, Acct] = {}


def load_accounts(cfg: dict) -> dict[str, Acct]:
    import hashlib
    import socket
    if not cfg.get("device_id"):
        cfg["device_id"] = secrets.token_hex(16).upper()
    main_cfg = {**cfg, "password": get_password("main", cfg.get("password")), "state_name": "state.json"}
    # Placeholder username so EasClient can construct; real login happens after Settings.
    if not (main_cfg.get("username") or "").strip():
        main_cfg["username"] = "pending"
    if not (main_cfg.get("url") or "").strip():
        main_cfg["url"] = DEFAULT_EAS_URLS["main"]
    out = {"main": Acct("main", "Alfa-Bank", main_cfg, False)}
    s2 = cfg.get("second")
    if s2 and (s2.get("username") or "").strip():
        dev = s2.get("device_id") or hashlib.sha256(
            f"eas-bridge|{s2['username']}|{s2['url']}|{socket.gethostname()}".encode()).hexdigest()[:32].upper()
        out["seller"] = Acct("seller", s2.get("name", "Seller"), {
            "username": s2["username"], "password": get_password("seller", s2.get("password")), "url": s2["url"], "device_id": dev,
            "email": s2.get("email", s2["username"]), "state_name": "state-seller.json",
            "imap_window_filter": cfg.get("imap_window_filter", 5)}, True)
    return out


def acct_of(aid) -> Acct:
    a = ACCTS.get(aid or "main")
    if a is None:
        raise LookupError(f"неизвестная учётная запись {aid!r}")
    return a


# -- account connection settings (server URL / login / password) -----------

def ensure_config() -> dict:
    """Create a first-run config with default EAS URLs and no credentials.
    Colleagues fill login/password in Settings; servers stay pre-filled."""
    if bridge.CONF_PATH.exists():
        try:
            return json.loads(bridge.CONF_PATH.read_text())
        except (OSError, ValueError):
            pass
    cfg = {
        "url": DEFAULT_EAS_URLS["main"],
        "username": "",
        "email": "",
        "device_id": secrets.token_hex(16).upper(),
    }
    _write_config(cfg)
    log.info("created first-run config at %s (default servers, no credentials)", bridge.CONF_PATH)
    return cfg


def account_needs_setup(cfg: dict | None = None) -> bool:
    """True until the main account has both username and a password (Keychain)."""
    cfg = cfg if cfg is not None else ensure_config()
    if not (cfg.get("username") or "").strip():
        return True
    return not bool(get_password("main", cfg.get("password")))


def get_account_config() -> dict:
    cfg = ensure_config()
    s2 = cfg.get("second")
    main_url = cfg.get("url") or DEFAULT_EAS_URLS["main"]
    seller = None
    if s2:
        seller = {"name": s2.get("name", "Alfa-Seller"),
                  "url": s2.get("url") or DEFAULT_EAS_URLS["seller"],
                  "username": s2.get("username", ""), "email": s2.get("email", ""),
                  "has_password": bool(_keychain_get("seller") or s2.get("password"))}
    else:
        # Still expose defaults so the settings form can create the second account.
        seller = {"name": "Alfa-Seller", "url": DEFAULT_EAS_URLS["seller"],
                  "username": "", "email": "", "has_password": False}
    return {
        "main": {"url": main_url, "username": cfg.get("username", ""), "email": cfg.get("email", ""),
                 "has_password": bool(_keychain_get("main") or cfg.get("password"))},
        "seller": seller,
        "defaults": dict(DEFAULT_EAS_URLS),
        "needs_setup": account_needs_setup(cfg),
    }


def _write_config(cfg: dict):
    bridge.CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    bridge.CONF_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    os.chmod(bridge.CONF_PATH, 0o600)


def _keychain_get(account_id: str, service: str = "eas-bridge") -> str | None:
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", account_id, "-s", service, "-w"],
                            capture_output=True, text=True, check=True)
        pw = r.stdout.rstrip("\n")
        return pw or None
    except subprocess.CalledProcessError:
        return None


def _keychain_set(account_id: str, password: str, service: str = "eas-bridge"):
    subprocess.run(["/usr/bin/security", "add-generic-password", "-a", account_id, "-s", service,
                     "-w", password, "-U"], capture_output=True, text=True, check=True)


def _strip_config_password(account_id: str):
    """Remove a plaintext password from config.json after it has been copied
    into the Keychain, so the secret does not linger in two places until the
    user happens to re-save the account through the settings UI."""
    try:
        cfg = json.loads(bridge.CONF_PATH.read_text())
    except (OSError, ValueError) as e:  # nothing to clean up / unreadable config
        log.warning("не удалось прочитать config.json для очистки пароля: %s", e)
        return
    if account_id == "main":
        changed = cfg.pop("password", None) is not None
        # Left over from the retired IMAP bridge: a secret nothing reads any more.
        changed = cfg.pop("local_password", None) is not None or changed
    else:
        s2 = cfg.get("second")
        changed = isinstance(s2, dict) and s2.pop("password", None) is not None
    if not changed:
        return
    try:
        _write_config(cfg)
        log.info("пароль %r удалён из config.json (перенесён в Keychain)", account_id)
    except OSError as e:
        log.warning("не удалось переписать config.json после переноса пароля %r: %s", account_id, e)


def get_password(account_id: str, cfg_password: str | None, service: str = "eas-bridge") -> str:
    """Keychain first; a plaintext password still in config.json (pre-Keychain
    accounts) is migrated in on first read and returned so the caller can use
    it right away without a second round-trip. Once the Keychain write is
    confirmed — or the Keychain already has one — the plaintext copy is
    stripped from config.json."""
    pw = _keychain_get(account_id, service)
    if pw:
        # Already in the Keychain (an earlier migration, or the settings form):
        # a plaintext copy still in config.json is only a leak — drop it.
        if cfg_password and service == "eas-bridge":
            _strip_config_password(account_id)
        return pw
    if cfg_password:
        try:
            _keychain_set(account_id, cfg_password, service)
        except subprocess.CalledProcessError as e:
            log.warning("не удалось перенести пароль %r в Keychain: %s", account_id, e)
        else:
            # Only for the real service — a test service name must never touch
            # the user's actual config.json.
            if service == "eas-bridge":
                _strip_config_password(account_id)
        return cfg_password
    return ""


def save_account_config(p: dict) -> dict:
    """Update config.json for one account and reconnect it immediately, so
    the caller learns right away whether the new credentials actually work."""
    import hashlib
    import socket
    from outlook_activesync_mcp.commands import settings as settings_cmd
    target = p.get("target")
    if target not in ("main", "seller"):
        return {"ok": False, "error": "bad_request", "message": "неизвестный аккаунт"}
    url, username, email = (p.get("url") or "").strip(), (p.get("username") or "").strip(), (p.get("email") or "").strip()
    password = p.get("password") or ""
    if not url or not username or not email:
        return {"ok": False, "error": "bad_request", "message": "заполните сервер, логин и email"}
    cfg = ensure_config()
    if not cfg.get("device_id"):
        cfg["device_id"] = secrets.token_hex(16).upper()
    if target == "main":
        if not password:
            password = get_password("main", cfg.get("password"))
        if not password:
            return {"ok": False, "error": "bad_request", "message": "укажите пароль для аккаунта"}
        cfg.update(url=url, username=username, email=email)
        cfg.pop("password", None)
        _keychain_set("main", password)
        merged = {"url": url, "username": username, "email": email, "password": password,
                  "device_id": cfg["device_id"], "state_name": "state.json",
                  "imap_window_filter": cfg.get("imap_window_filter", 5)}
        aid, name, green = "main", "Alfa-Bank", False
    else:
        s2 = cfg.get("second") or {}
        if not password:
            password = get_password("seller", s2.get("password"))
        if not password:
            return {"ok": False, "error": "bad_request", "message": "укажите пароль для нового аккаунта"}
        name = (p.get("name") or s2.get("name") or "Alfa-Seller").strip() or "Alfa-Seller"
        dev = s2.get("device_id") or hashlib.sha256(
            f"eas-bridge|{username}|{url}|{socket.gethostname()}".encode()).hexdigest()[:32].upper()
        _keychain_set("seller", password)
        cfg["second"] = {"name": name, "url": url, "username": username, "email": email, "device_id": dev}
        merged = {"username": username, "password": password, "url": url, "device_id": dev,
                  "email": email, "state_name": "state-seller.json",
                  "imap_window_filter": cfg.get("imap_window_filter", 5)}
        aid, name, green = "seller", name, True
    _write_config(cfg)
    backend = bridge.EasBackend(merged)
    login_ok, message = True, None
    new_acct = Acct(aid, name, merged, green)
    try:
        with backend.lock:
            # The user just typed this password: try it for real. The upstream
            # latch remembers an earlier 401 by password hash, so re-saving the
            # same (correct) password failed at once without a single request.
            backend.client.store.clear_auth_failed()
            try:
                settings_cmd.handle(backend.client, "status")
            except Exception as e:  # noqa: BLE001
                if getattr(e, "code", None) != "auth_failed":
                    raise
                # Exchange answers an odd 401 under load; one retry tells a
                # wrong password from a hiccup (two tries cannot lock the account).
                log.info("[%s] login check: 401, one retry", aid)
                time.sleep(2)
                backend.client.store.clear_auth_failed()
                settings_cmd.handle(backend.client, "status")
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] login check after save failed: %s", aid, e)
        login_ok, message = False, _friendly_error(new_acct, "settings", "status", e)
    new_acct.backend = backend
    _mail_cache_load(new_acct)  # same login and server → keep the saved letters
    ACCTS[aid] = new_acct
    if login_ok:
        threading.Thread(target=backend.ensure_identity, daemon=True).start()
        cal_refresh_bg(new_acct)
    return {"ok": True, "login_ok": login_ok, "message": message}


# -- ActiveSync dispatch ----------------------------------------------------

# Cap of messages retained per folder after a dump/delta (UI window is usually 40).
_MAIL_BOX_CAP = 400
# Wall-clock budget for one UI list request. Stalwart (Seller) answers 1–2
# messages per Sync round, so "fill a 40-message page" took ~40 s on a big
# folder while the UI showed «Загрузка…». Show what arrived; the rest comes
# with «Ещё» / the next refresh.
_MAIL_PAGE_BUDGET = 2.5


def _mail_box(a: Acct, collection_id: str) -> dict:
    box = a.mail_boxes.get(collection_id)
    if box is None:
        box = {"filter": object(), "by_id": {}, "gen": None, "complete": False,
               "next_cursor": None, "label": None, "ts": 0.0}
        a.mail_boxes[collection_id] = box
    return box


def _mail_sorted_items(box: dict) -> list:
    items = list(box["by_id"].values())
    items.sort(key=lambda m: m.get("received") or "", reverse=True)
    if len(items) > _MAIL_BOX_CAP:
        keep = items[:_MAIL_BOX_CAP]
        box["by_id"] = {m["item_id"]: m for m in keep if m.get("item_id")}
        return keep
    return items


def _mail_apply_tree(tree, collection_id: str, label: str, proj: list, box: dict) -> None:
    """Merge one Sync response into the folder box (Add / Change / Delete)."""
    from outlook_activesync_mcp.model.mapping import project_mail
    from outlook_activesync_mcp.models import pack_item_id
    from outlook_activesync_mcp.wbxml import find, find_all, text_of

    by_id = box["by_id"]
    for node in find_all(tree, "AirSync", "Add"):
        sid = text_of(find(node, "AirSync", "ServerId"))
        appdata = find(node, "AirSync", "ApplicationData")
        if not sid or appdata is None:
            continue
        item_id = pack_item_id(collection_id, sid)
        by_id[item_id] = project_mail(appdata, proj, item_id=item_id, folder=label)
    for node in find_all(tree, "AirSync", "Change"):
        sid = text_of(find(node, "AirSync", "ServerId"))
        appdata = find(node, "AirSync", "ApplicationData")
        if not sid or appdata is None:
            continue
        item_id = pack_item_id(collection_id, sid)
        existing = by_id.get(item_id)
        if existing is None:
            by_id[item_id] = project_mail(appdata, proj, item_id=item_id, folder=label)
            continue
        # Change may be partial (e.g. only Read). Never treat missing Read as unread.
        if find(appdata, "Email", "Read") is not None:
            existing["is_read"] = text_of(find(appdata, "Email", "Read")) == "1"
        for tag, key in (("Subject", "subject"), ("ThreadTopic", "thread_topic")):
            if find(appdata, "Email", tag) is not None:
                existing[key] = text_of(find(appdata, "Email", tag))
        if find(appdata, "Email", "From") is not None:
            from outlook_activesync_mcp.model.mapping import parse_address
            existing["from"] = parse_address(text_of(find(appdata, "Email", "From")))
        if find(appdata, "Email", "DateReceived") is not None:
            from outlook_activesync_mcp.utils import to_local
            existing["received"] = to_local(text_of(find(appdata, "Email", "DateReceived")))
        if find(appdata, "AirSyncBase", "Body") is not None or find(appdata, "Email", "Body") is not None:
            preview = project_mail(appdata, ["preview"], item_id=item_id).get("preview")
            if preview:
                existing["preview"] = preview
    for node in find_all(tree, "AirSync", "Delete") + find_all(tree, "AirSync", "SoftDelete"):
        sid = text_of(find(node, "AirSync", "ServerId"))
        if sid:
            by_id.pop(pack_item_id(collection_id, sid), None)

def _mail_norm_filter(filt):
    if filt is None or filt == "":
        return None
    try:
        return int(filt)
    except (TypeError, ValueError):
        return filt


def _mail_store_gen(backend, collection_id: str):
    try:
        entry = backend.client.store.get("collections", {}).get(collection_id) or {}
        return entry.get("generation")
    except Exception:  # noqa: BLE001
        return None


def _mail_fetch_pages(backend: bridge.EasBackend, params: dict) -> dict:
    """One UI 'list' request may need several ActiveSync Sync round-trips: some
    servers (Stalwart, used for the Seller account) return only a couple of
    changes per Sync response regardless of the requested limit, unlike
    Exchange which tends to fill the whole window in one round-trip. Keep
    pulling with mail.handle's own cursor until the requested limit is met or
    the server has nothing more, so one 'list'/'Ещё' click from the UI
    actually returns a full page instead of 1-2 messages."""
    from outlook_activesync_mcp.commands import mail
    limit = int(params.get("limit") or 40)
    kw0 = {k: v for k, v in params.items() if k not in ("cursor", "limit", "force", "full")}
    items: list = []
    next_cursor = params.get("cursor")
    has_more, rounds, t0 = True, 0, time.monotonic()
    while len(items) < limit and has_more and rounds < 60:
        if items and time.monotonic() - t0 > _MAIL_PAGE_BUDGET:
            break  # enough to show; has_more/next_cursor let the UI continue
        rounds += 1
        backend._pace()
        kw = dict(cursor=next_cursor) if next_cursor else dict(kw0)
        r = mail.handle(backend.client, "list", limit=limit - len(items), **kw)
        if not r.get("ok", True) and r.get("error"):
            if items:
                break
            return r
        items.extend(r.get("items", []))
        next_cursor = r.get("next_cursor")
        has_more = bool(r.get("has_more") and next_cursor)
    return {"ok": True, "action": "list", "count": len(items), "items": items,
            "has_more": has_more, "next_cursor": next_cursor}


def _mail_apply_delta(backend, collection_id: str, label: str, filt, box: dict, fields) -> None:
    """Continue the stored SyncKey and merge Add/Change/Delete into the box."""
    from outlook_activesync_mcp.commands.mail import _read_options
    from outlook_activesync_mcp.models import MAIL_FIELDS, resolve_fields

    proj = resolve_fields(MAIL_FIELDS, fields)
    opts = _read_options(proj, filt)
    gen = box["gen"]
    rounds, t0, drained = 0, time.monotonic(), False
    while rounds < 30:
        if rounds and time.monotonic() - t0 > _MAIL_PAGE_BUDGET:
            break  # a slow server's backlog drains over the next refreshes
        rounds += 1
        backend._pace()
        prev_gen = gen
        tree, more, gen = backend.client.sync_round(
            collection_id, generation=gen, window=100,
            options_children=opts, get_changes=True)
        # sync_round re-primes (SyncKey=0) on a dead key and then hands back a
        # new generation: only then is this a fresh full listing that replaces
        # the cache. Many Adds alone mean nothing — continuing an unfinished
        # first dump legitimately delivers the older remainder as Adds, and
        # wiping on that used to throw away the newest mail.
        if gen != prev_gen:
            box["by_id"].clear()
        _mail_apply_tree(tree, collection_id, label, proj, box)
        box["gen"] = gen
        if not more:
            drained = True
            break
    box["ts"] = time.time()
    box["complete"] = drained
    box["next_cursor"] = None


def _mail_merge_newest(backend, collection_id: str, box: dict, r: dict) -> dict:
    """A fresh newest-first listing into a full box: it is the truth down to its oldest
    letter (anything cached in that span but not listed was deleted); older cached
    letters stay, so the list does not shrink to one page."""
    new = {it["item_id"]: it for it in r.get("items") or [] if it.get("item_id")}
    floor = min((m.get("received") or "" for m in new.values()), default="") if r.get("has_more") else ""
    old = {k: m for k, m in box["by_id"].items() if k not in new and (m.get("received") or "") < floor}
    box["by_id"] = {**old, **new}
    box["gen"] = _mail_store_gen(backend, collection_id)
    box["complete"] = not r.get("has_more")
    box["next_cursor"] = r.get("next_cursor")
    box["ts"] = time.time()
    items = _mail_sorted_items(box)
    has_more = not box["complete"] and bool(box["next_cursor"])
    return {"ok": True, "action": "list", "count": len(items), "items": items, "has_more": has_more,
            "next_cursor": box["next_cursor"] if has_more else None, "delta": True, "cached": len(box["by_id"])}


def _mail_list_paged(a: Acct, backend: bridge.EasBackend, params: dict) -> dict:
    """List mail with a per-folder cache: first open primes SyncKey=0, later
    refreshes request only the delta (same FilterType + stored generation)."""
    from outlook_activesync_mcp.commands import mail
    from outlook_activesync_mcp.errors import CursorExpired, EasStatusError

    # Unit tests use FakeBackend without a client — keep the old paging path.
    if backend.client is None:
        return _mail_fetch_pages(backend, params)

    force = bool(params.get("force") or params.get("full"))
    cursor = params.get("cursor")
    filt = _mail_norm_filter(params.get("filter"))
    fields = params.get("fields")
    limit = int(params.get("limit") or 40)

    # «Ещё»: continue the incomplete dump cursor and append into the box.
    if cursor:
        r = _mail_fetch_pages(backend, params)
        if not r.get("ok", True):
            return r
        try:
            collection_id, _ = mail.resolve_collection(backend.client, params.get("folder")) \
                if params.get("folder") else (None, None)
        except Exception:  # noqa: BLE001
            collection_id = None
        if collection_id is None and r.get("items"):
            # Cursor embeds collection_id; recover from first item_id if needed.
            try:
                from outlook_activesync_mcp.models import unpack_item_id
                collection_id, _ = unpack_item_id(r["items"][0]["item_id"])
            except Exception:  # noqa: BLE001
                collection_id = None
        if collection_id:
            box = _mail_box(a, collection_id)
            for it in r.get("items") or []:
                if it.get("item_id"):
                    box["by_id"][it["item_id"]] = it
            box["gen"] = _mail_store_gen(backend, collection_id) or box.get("gen")
            box["complete"] = not r.get("has_more")
            box["next_cursor"] = r.get("next_cursor")
            box["ts"] = time.time()
        return r

    try:
        collection_id, label = mail.resolve_collection(backend.client, params.get("folder"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": "list", "count": 0, "items": [],
                "error": getattr(e, "code", None) or type(e).__name__,
                "message": _friendly_error(a, "mail", "list", e)}

    box = _mail_box(a, collection_id)
    # The box holds projections of the fields its first dump asked for: a request
    # for other fields (a probe, a different caller) must not get rows missing
    # sender/preview — nor rebuild the box the UI relies on with fewer fields.
    want_fields = tuple(sorted(fields)) if fields else None
    can_delta = (not force and box.get("gen") is not None
                 and box.get("filter") == filt and bool(box.get("by_id"))
                 and box.get("fields") == want_fields)

    # A full box whose first dump never finished (Sent Items over «всё время»: thousands
    # of letters): the key's queue holds only letters older than all we keep, and new
    # mail waits behind that backlog — it never showed. Exchange lists newest first, so
    # a fresh listing brings it at the cost of one round. Not Seller: Stalwart hands out
    # 1–2 letters a round in no set order.
    if can_delta and not a.green and not box.get("complete") and len(box["by_id"]) >= _MAIL_BOX_CAP:
        try:
            r = _mail_fetch_pages(backend, {**params, "filter": filt, "limit": max(limit, 100),
                                            "folder": params.get("folder") or collection_id})
            err = None if r.get("ok", True) else r
        except Exception as e:  # noqa: BLE001
            if getattr(e, "code", None) == "auth_failed":
                raise
            err = {"error": getattr(e, "code", None) or type(e).__name__,
                   "message": _friendly_error(a, "mail", "list", e), "_exc": e}
        if err is None:
            return _mail_merge_newest(backend, collection_id, box, r)
        if err.get("error") in ("throttled", "auth_failed", "unreachable") or \
                ("_exc" in err and _is_backoff(err["_exc"])):
            log.info("[%s] mail refresh on %s deferred: %s", a.id, collection_id, err.get("message"))
            items = _mail_sorted_items(box)
            return {"ok": True, "action": "list", "count": len(items), "items": items, "has_more": False,
                    "next_cursor": None, "delta": True, "stale": True, "cached": len(box["by_id"]),
                    "message": err.get("message")}
        can_delta = False  # anything else: a clean prime below

    if can_delta:
        try:
            _mail_apply_delta(backend, collection_id, label, filt, box, fields)
            items = _mail_sorted_items(box)
            # Soft refresh wants the whole cached window; hard UI page still
            # gets at least `limit` (usually the full box after first dump).
            out = items if len(items) <= max(limit, _MAIL_BOX_CAP) else items[:_MAIL_BOX_CAP]
            has_more = not box.get("complete") and bool(box.get("next_cursor"))
            return {"ok": True, "action": "list", "count": len(out), "items": out,
                    "has_more": has_more, "next_cursor": box.get("next_cursor") if has_more else None,
                    "delta": True, "cached": len(box["by_id"])}
        except CursorExpired:
            log.info("[%s] mail delta cursor expired on %s — full dump", a.id, collection_id)
            box["gen"], box["by_id"] = None, {}
        except EasStatusError as e:
            log.info("[%s] mail delta Sync status %s on %s — full dump", a.id,
                     getattr(e, "status", "?"), collection_id)
            box["gen"], box["by_id"] = None, {}
        except Exception as e:  # noqa: BLE001
            if getattr(e, "code", None) == "auth_failed":
                raise  # keep the box, but never hide a password problem behind cached mail
            if _is_backoff(e):
                # Throttled: keep the box and its SyncKey and show what we have. A full
                # dump now is exactly the extra load that kept Exchange throttling us
                # (13:55, 17:14 on 24.09).
                log.info("[%s] mail delta on %s deferred: %s", a.id, collection_id, e)
                items = _mail_sorted_items(box)
                has_more = not box.get("complete") and bool(box.get("next_cursor"))
                return {"ok": True, "action": "list", "count": len(items), "items": items,
                        "has_more": has_more, "next_cursor": box.get("next_cursor") if has_more else None,
                        "delta": True, "stale": True, "cached": len(box["by_id"]),
                        "message": _friendly_error(a, "mail", "list", e)}
            log.warning("[%s] mail delta failed on %s: %s — full dump", a.id, collection_id, e)
            box["gen"] = None

    # Full prime (SyncKey=0 via mail.handle).
    box["filter"] = filt
    box["fields"] = want_fields
    box["label"] = label
    box["by_id"] = {}
    box["complete"] = False
    box["next_cursor"] = None
    box["gen"] = None
    r = _mail_fetch_pages(backend, {**params, "filter": filt, "folder": params.get("folder") or collection_id})
    if not r.get("ok", True):
        return r
    for it in r.get("items") or []:
        if it.get("item_id"):
            box["by_id"][it["item_id"]] = it
    box["gen"] = _mail_store_gen(backend, collection_id)
    box["complete"] = not r.get("has_more")
    box["next_cursor"] = r.get("next_cursor")
    box["ts"] = time.time()
    r = dict(r)
    r["delta"] = False
    return r


def call(a: Acct, domain: str, params: dict) -> dict:
    from outlook_activesync_mcp.commands import calendar, folders, mail, people, settings
    mods = {"mail": mail, "folders": folders, "events": calendar, "people": people, "settings": settings}
    mod = mods.get(domain)
    action = params.pop("action", None)
    cache_only = bool(params.pop("cache_only", False))
    roles = params.pop("attendee_types", None) if domain == "events" else None
    extra = ({k: params.pop(k) for k in ("response_requested", "disallow_counter", "attachments") if k in params}
             if domain == "events" and action in ("create", "update") else {})
    if extra.get("attachments") and a.green:
        return {"ok": False, "error": "bad_request", "action": action, "count": 0, "items": [],
                "message": f"Сервер {a.name} не принимает вложения во встречах (ActiveSync 14.1)"}
    if domain == "mail" and action == "list" and cache_only:
        return mail_cached_list(a, params)
    if mod is None or not action:
        return {"ok": False, "error": "bad_request", "message": f"unknown call {domain}/{action}",
                "action": action or "", "count": 0, "items": []}
    # Folder create is not in upstream MCP (WBXML tables omit FolderCreate) —
    # we implement it here with a one-shot table patch.
    if domain == "folders" and action == "create":
        return folder_create(a, params.get("name") or "", params.get("parent_id") or "0")
    if domain == "events" and action == "respond":
        return respond_event(a, params)
    if domain == "events" and action == "forward":
        return forward_event(a, params)
    if domain == "folders" and action == "delete":
        return folder_delete(a, params.get("folder_id") or "")
    if domain == "folders" and action == "list":
        return folders_list(a, refresh=bool(params.get("refresh")))
    if domain == "people" and action == "find_free_slots":
        return _free_slots(a, params)
    if domain == "people" and action == "schedule":
        return _schedule(a, params)
    # GAL Search is slow (~0.5–2s); compose autocomplete fires on every pause.
    # Cache identical queries briefly so retyping / To+Cc sharing a prefix is free.
    if domain == "people" and action == "find":
        cached = _people_find_cached(a, params)
        if cached is not None:
            return cached
    if domain == "mail" and action in ("send", "reply", "forward") and time.time() < a.send_down_until:
        return {"ok": False, "action": action, "count": 0, "items": [], "error": "send_down",
                "message": str(_send_down_error(a))}
    if domain == "mail" and action in ("reply", "forward") and params.get("item_id") and not params.get("no_quote_header"):
        params["body"] = (params.get("body") or "").rstrip() + _outlook_quote_header(a, params["item_id"])
    params.pop("no_quote_header", None)
    backend = a.get()
    write = action in _WRITE_ACTIONS
    if not backend.lock.acquire(timeout=LOCK_WAIT_WRITE_S if write else LOCK_WAIT_S):
        return _busy_answer(a, domain, action, params)
    try:
        _ATT.types = ({str(k).lower(): v for k, v in roles.items() if v in _ATT_TYPE_CODE}
                      if isinstance(roles, dict) else {})
        _ATT.extra = extra
        return _call_locked(a, backend, mod, domain, action, params)
    finally:
        _ATT.types = {}
        _ATT.extra = {}
        backend.lock.release()


_WRITE_ACTIONS = frozenset({"send", "reply", "forward", "move", "delete", "mark_read", "flag",
                            "create", "update", "cancel", "respond"})


def _busy_answer(a: Acct, domain: str, action: str, params: dict) -> dict:
    """The mailbox is stuck on a slow call: the open folder from cache, else a clear error."""
    fid = str(params.get("folder") or "")
    box = a.mail_boxes.get(fid) if domain == "mail" and action == "list" and not params.get("cursor") else None
    msg = (f"Сервер {a.name} сейчас не отвечает" +
           (f", повторю через {int(acct_down_for(a)) + 1} с" if acct_down_for(a) else "") + ".")
    if box and box.get("by_id"):
        items = _mail_sorted_items(box)
        return {"ok": True, "action": "list", "count": len(items), "items": items, "has_more": False,
                "next_cursor": None, "delta": True, "stale": True, "cached": len(box["by_id"]), "message": msg}
    log.info("[%s] %s/%s: mailbox busy, answered without waiting", a.id, domain, action)
    return {"ok": False, "action": action, "count": 0, "items": [], "error": "unreachable", "message": msg}


def _call_locked(a: Acct, backend, mod, domain: str, action: str, params: dict) -> dict:
    try:
        if domain == "mail" and action == "list":
            res = _mail_list_paged(a, backend, params)
            fid = params.get("folder")
            if fid and res.get("ok", True):
                box = a.mail_boxes.get(str(fid))
                if box is not None and box.get("by_id"):
                    recount_unread_from_box(a, str(fid), box)
                elif not params.get("cursor"):
                    recount_unread_from_items(a, str(fid), res.get("items") or [])
            return res
        backend._pace()
        if domain == "mail" and action == "reply" and params.get("to"):
            res = _reply_to(backend.client, **params)  # the UI chose the recipients
        else:
            res = mod.handle(backend.client, action, **params)
        if domain == "people" and action == "find" and res.get("ok", True):
            _people_find_store(a, params, res)
        # Local mail-box patches after writes so the next delta refresh is coherent.
        if domain == "mail" and res.get("ok", True):
            _mail_box_after_write(a, action, params, res)
        return res
    except Exception as e:  # noqa: BLE001 — surfaced to the UI as data
        log.warning("[%s] %s/%s failed: %s", a.id, domain, action, e)
        if domain == "mail" and getattr(e, "status", None) == 120:
            a.send_down_until = time.time() + SEND_OUTAGE_S
        return {"ok": False, "action": action, "count": 0, "items": [],
                "error": getattr(e, "code", None) or type(e).__name__,
                "message": _friendly_error(a, domain, action, e)}


# MS-ASCMD MeetingResponse status codes → what the user can do about it.
_MEETING_RESPONSE_STATUS = {
    2: "сервер не принял ответ на это событие: скорее всего, встречу уже отменили или перенесли, "
       "или это не приглашение. Обновите календарь (⟳) и попробуйте снова.",
    3: "почтовый сервер временно не смог сохранить ответ. Попробуйте ещё раз через минуту.",
    4: "сервер организатора не принял ответ. Попробуйте позже или ответьте из письма-приглашения.",
}


def _is_backoff(e) -> bool:
    """Throttled (503) or password latch (401): more requests only make it worse —
    never answer these with a heavier fallback (full dump / full sync)."""
    return getattr(e, "code", None) in ("throttled", "auth_failed") or _is_unreachable(e)


def _auth_message(a: Acct) -> str:
    """Which account refused the password — name and login (AAS-24-01)."""
    login = (a.cfg.get("username") or "").strip()
    who = f"«{a.name}»" + (f" (логин {login})" if login and login != "pending" else "")
    return (f"Сервер {who} не принял логин или пароль. Проверьте их в Настройках → Аккаунты "
            "(и подключение к VPN) и нажмите «Сохранить».")


def _auth_refused(a: Acct) -> bool:
    """True while the client's 401 latch is set — without creating a client."""
    try:
        return bool(a.backend and a.backend.client.store.auth_failed())
    except Exception:  # noqa: BLE001
        return False


def _friendly_error(a: Acct, domain: str, action: str, e: Exception) -> str:
    """Keep the raw text in the log; give the UI a sentence a person can act on."""
    if getattr(e, "status", None) == 120 or "status 120" in str(e):
        return (f"Сервер {a.name} не смог отправить письмо (ошибка 120 — отправка почты на сервере "
                "не работает). Письмо не ушло. Если в веб-почте тоже не отправляется — это сбой сервера, "
                "сообщите администратору.")
    status = getattr(e, "status", None)
    if isinstance(status, int) and status > MOVE_STATUS_BASE:
        why = _MOVE_FAILURE.get(status - MOVE_STATUS_BASE, f"код {status - MOVE_STATUS_BASE}")
        return f"Письмо не перемещено: {why}."
    if getattr(e, "code", None) == "throttled":
        return (f"Сервер {a.name} временно ограничил число запросов. Подождите минуту — "
                "автосинхронизация повторит сама.")
    if getattr(e, "code", None) == "auth_failed":
        return _auth_message(a)
    if _is_unreachable(e):
        left = acct_down_for(a)
        return (f"Сервер {a.name} не отвечает (VPN или сбой на сервере)." +
                (f" Повторю через {int(left) + 1} с." if left else ""))
    if domain == "events" and action == "respond":
        status = getattr(e, "status", None)
        if status in _MEETING_RESPONSE_STATUS:
            return f"Ответ на встречу: {_MEETING_RESPONSE_STATUS[status]} (код {status})"
        if a.green:
            return (f"Ответ на встречу не отправлен: сервер {a.name} не поддерживает этот способ ответа "
                    f"через ActiveSync ({e}). Ответьте из письма-приглашения.")
        return f"Ответ на встречу не отправлен: {e}"
    return str(e)


def _free_slots(a: Acct, params: dict) -> dict:
    """«Свободно у всех»: a few options inside the user's working day.

    Upstream takes only count×4 raw free slots from `start` before dropping the
    ones outside working hours/weekends — mostly nights, so one option (or none)
    survived, and 09:00 was hard-coded. Ask it for plenty, then keep `count`
    slots that start *and end* inside the working day from Settings."""
    from datetime import datetime
    prefs = load_prefs()
    ws, we = int(params.pop("work_start", prefs["work_start"])), int(params.pop("work_end", prefs["work_end"]))
    want = max(1, min(int(params.pop("count", 3) or 3), 10))
    params.update(work_start=ws, work_end=we, count=want * 12)
    backend = a.get()
    with backend.lock:
        try:
            from outlook_activesync_mcp.commands import people
            backend._pace()
            res = people.handle(backend.client, "find_free_slots", **params)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] people/find_free_slots failed: %s", a.id, e)
            return {"ok": False, "action": "find_free_slots", "count": 0, "items": [],
                    "error": getattr(e, "code", None) or type(e).__name__, "message": str(e)}

    def fits(slot) -> bool:
        try:
            end = datetime.fromisoformat(str(slot["end"]).replace(" ", "T"))
        except (KeyError, ValueError):
            return True
        return end.hour < we or (end.hour == we and end.minute == 0)

    items = [x for x in res.get("items") or [] if fits(x)][:want]
    return {**res, "items": items, "count": len(items), "work_start": ws, "work_end": we}


_SCHED_CACHE: dict[tuple, tuple[float, dict]] = {}
_SCHED_BATCH = 20
_SCHED_CACHE_TTL = 120
_SCHED_CACHE_MAX = 64


def _sched_cache_prune(now: float | None = None) -> None:
    """Drop expired free/busy answers so the process does not keep them forever."""
    now = time.time() if now is None else now
    dead = [k for k, (t, _) in _SCHED_CACHE.items() if now - t >= _SCHED_CACHE_TTL]
    for k in dead:
        _SCHED_CACHE.pop(k, None)
    if len(_SCHED_CACHE) <= _SCHED_CACHE_MAX:
        return
    # Still too many: drop the oldest first.
    for k, _ in sorted(_SCHED_CACHE.items(), key=lambda kv: kv[1][0])[: len(_SCHED_CACHE) - _SCHED_CACHE_MAX]:
        _SCHED_CACHE.pop(k, None)


def _schedule(a: Acct, params: dict) -> dict:
    """Free/busy of everyone in a meeting for `days` days from local midnight of
    `start`: 48 codes a day per person (0 free, 1 tentative, 2 busy, 3 away,
    4 no data). One ResolveRecipients per 20 addresses, so big invitations stay
    under the server's recipient cap; cached 2 min so re-ranking, tab and
    constraint changes never hit Exchange again."""
    from datetime import datetime, timedelta
    fail = lambda err, msg: {"ok": False, "action": "schedule", "count": 0, "items": [], "error": err, "message": msg}
    # One address in any case: A@x and a@x must not become two people.
    who, seen = [], set()
    for x in params.get("who") or []:
        s = str(x).strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        who.append(s)
        if len(who) >= 200:
            break
    try:
        days = max(1, min(int(params.get("days") or 5), 7))
    except (TypeError, ValueError):
        return fail("bad_request", "days: нужно целое число от 1 до 7")
    try:
        d0 = datetime.strptime(str(params.get("start") or "")[:10], "%Y-%m-%d")
    except ValueError:
        return fail("bad_request", "start: нужна дата YYYY-MM-DD")
    key = (a.id, tuple(sorted(w.lower() for w in who)), d0.date().isoformat(), days)
    now = time.time()
    _sched_cache_prune(now)
    hit = _SCHED_CACHE.get(key)
    if hit and now - hit[0] < _SCHED_CACHE_TTL:
        return hit[1]
    fmt = "%Y-%m-%dT%H:%M"
    start, end = d0.strftime(fmt), (d0 + timedelta(days=days)).strftime(fmt)
    items, unresolved = [], []
    if who:
        backend = a.get()
        from outlook_activesync_mcp.commands import people
        for i in range(0, len(who), _SCHED_BATCH):
            batch = who[i:i + _SCHED_BATCH]
            # One batch at a time under the mailbox lock, and never wait long for it:
            # a full calendar resync must not freeze the editor (or mail) behind us.
            if not backend.lock.acquire(timeout=LOCK_WAIT_S):
                return fail("unreachable", f"Сервер {a.name} сейчас занят — попробуйте ещё раз через минуту.")
            try:
                backend._pace()
                r = people.handle(backend.client, "availability", who=batch, start=start, end=end)
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] people/schedule failed: %s", a.id, e)
                return fail(getattr(e, "code", None) or type(e).__name__, str(e))
            finally:
                backend.lock.release()
            # Answers come in request order with the unresolved left out: tag each one with
            # what was typed, so «Иванов» (no address yet) still finds his free/busy row.
            missed = {str(u.get("query")) for u in r.get("unresolved") or []}
            items += [{**x, "query": q} for q, x in zip([q for q in batch if q not in missed], r.get("items") or [])]
            unresolved += r.get("unresolved") or []
    n = days * 48
    out = [{"query": x.get("query"), "address": x.get("address"), "name": x.get("name"),
            "freebusy": (str(x.get("freebusy") or "") + "4" * n)[:n]} for x in items]
    res = {"ok": True, "action": "schedule", "count": len(out), "items": out, "unresolved": unresolved,
           "start": d0.date().isoformat(), "days": days}
    _SCHED_CACHE[key] = (time.time(), res)
    _sched_cache_prune()
    return res


_WARM_FRESH_S = 120
_WARM_MAX = 8


def warm_folders(a: Acct, folders: list, filt, fields) -> dict:
    """Pre-fetch folders the user is likely to open (favourites, recent, Inbox) in
    the background, so switching to them is served from the in-memory box. One
    folder at a time — the account lock is released between folders, so a click
    waits for at most one folder. Folders refreshed recently are skipped."""
    todo = [str(f) for f in (folders or []) if f][:_WARM_MAX]

    def run():
        for fid in todo:
            try:
                box = a.mail_boxes.get(fid)  # UI folder ids are the collection ids
                if box and time.time() - box.get("ts", 0) < _WARM_FRESH_S:
                    continue
                call(a, "mail", {"action": "list", "folder": fid, "limit": 40, "filter": filt, "fields": fields})
            except Exception:  # noqa: BLE001 — warming is best-effort
                log.debug("[%s] warm %s failed", a.id, fid, exc_info=True)
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "warming": len(todo)}


def _mail_box_after_write(a: Acct, action: str, params: dict, res: dict) -> None:
    """Best-effort update of the in-memory folder cache after mark/move/delete."""
    ids = list(params.get("item_ids") or [])
    if params.get("item_id"):
        ids.append(params["item_id"])
    if not ids:
        return
    try:
        from outlook_activesync_mcp.models import unpack_item_id
    except ImportError:
        return
    if action == "mark_read":
        read = bool(params.get("read", True))
        for iid in ids:
            try:
                cid, _ = unpack_item_id(iid)
            except Exception:  # noqa: BLE001
                continue
            box = a.mail_boxes.get(cid)
            row = (box or {}).get("by_id", {}).get(iid)
            if row is not None:
                row["is_read"] = read
                recount_unread_from_box(a, cid, box)
    elif action in ("delete", "move"):
        for iid in ids:
            try:
                cid, _ = unpack_item_id(iid)
            except Exception:  # noqa: BLE001
                continue
            box = a.mail_boxes.get(cid)
            if box and iid in box.get("by_id", {}):
                box["by_id"].pop(iid, None)
                recount_unread_from_box(a, cid, box)


# Short-lived GAL cache: (acct_id, query_lower, limit) → (expires_at, result)
_PEOPLE_FIND_TTL = 90.0
_people_find_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}
_people_find_lock = threading.Lock()


def _people_find_key(a: Acct, params: dict) -> tuple[str, str, int] | None:
    q = (params.get("query") or "").strip().lower()
    if len(q) < 2:
        return None
    return (a.id, q, int(params.get("limit") or 15))


def _people_find_cached(a: Acct, params: dict) -> dict | None:
    key = _people_find_key(a, params)
    if not key:
        return None
    with _people_find_lock:
        hit = _people_find_cache.get(key)
        if not hit:
            return None
        exp, res = hit
        if exp < time.time():
            _people_find_cache.pop(key, None)
            return None
        return dict(res)


def _people_find_store(a: Acct, params: dict, res: dict) -> None:
    key = _people_find_key(a, params)
    if not key:
        return
    with _people_find_lock:
        # Cap growth — drop expired first, then oldest half if still huge.
        now = time.time()
        dead = [k for k, (exp, _) in _people_find_cache.items() if exp < now]
        for k in dead:
            _people_find_cache.pop(k, None)
        if len(_people_find_cache) > 200:
            by_age = sorted(_people_find_cache.items(), key=lambda kv: kv[1][0])
            for k, _ in by_age[:100]:
                _people_find_cache.pop(k, None)
        _people_find_cache[key] = (now + _PEOPLE_FIND_TTL, dict(res))


def _parse_folder_changes(tree) -> tuple[dict[str, dict], str]:
    """FolderSync Changes → {server_id: row}, new SyncKey.

    Upstream ``parse_folders`` only reads ``Add``. Some servers (notably
    Stalwart) also emit user mailboxes as ``Update``, and/or only after a
    follow-up sync with the SyncKey returned from the initial SyncKey=0 round.
    """
    from outlook_activesync_mcp.wbxml import find, find_all, text_of
    rows: dict[str, dict] = {}
    for tag in ("Add", "Update"):
        for node in find_all(tree, "FolderHierarchy", tag):
            sid = text_of(find(node, "FolderHierarchy", "ServerId"))
            if not sid:
                continue
            rows[sid] = {
                "id": sid,
                "name": text_of(find(node, "FolderHierarchy", "DisplayName")) or sid,
                "type": text_of(find(node, "FolderHierarchy", "Type")) or "12",
                "parent_id": text_of(find(node, "FolderHierarchy", "ParentId")) or "0",
            }
    for node in find_all(tree, "FolderHierarchy", "Delete"):
        sid = text_of(find(node, "FolderHierarchy", "ServerId"))
        if sid:
            rows.pop(sid, None)
    key = text_of(find(tree, "FolderHierarchy", "SyncKey")) or "0"
    return rows, key


def _deep_folder_tree(client) -> tuple[list[dict], str]:
    """Full folder tree: FolderSync(0) plus one follow-up round with the returned
    key, merging Add *and* Update. Stalwart (Seller) sends user mailboxes only as
    Update and/or only on the follow-up round; upstream reads just Add of round 0.
    Rows use upstream's shape ({id, name, type, parent_id}). Returns (rows, key)."""
    from outlook_activesync_mcp.commands import provision as prov
    from outlook_activesync_mcp.wbxml import find, find_all, text_of
    client.ensure_provisioned()
    by_id, key = _parse_folder_changes(client.command("FolderSync", prov.build_foldersync("0")))
    if key and key != "0":
        # Status 9 here (another FolderSync advanced the key) must not lose round 0.
        try:
            tree1 = client.command("FolderSync", prov.build_foldersync(key))
            more, key2 = _parse_folder_changes(tree1)
            by_id.update(more)
            for node in find_all(tree1, "FolderHierarchy", "Delete"):
                by_id.pop(text_of(find(node, "FolderHierarchy", "ServerId")) or "", None)
            key = key2 or key
        except Exception as e:  # noqa: BLE001
            log.info("follow-up FolderSync skipped: %s", e)
    return list(by_id.values()), key


# Set while a hierarchy delta is in flight: a dead SyncKey must surface as an
# error (→ full tree) instead of _patch_foldersync_invalid_key's quiet SyncKey=0
# retry, whose shallow round-0 answer would be merged as if it were a delta.
_fs_delta = threading.local()


def _folder_delta(client, cache: dict) -> tuple[list[dict], str] | None:
    """FolderSync with the stored key: only Add/Update/Delete since last time,
    merged into the cached tree. None when there is no usable key."""
    from outlook_activesync_mcp.commands import provision as prov
    from outlook_activesync_mcp.wbxml import find, find_all, text_of
    key = cache.get("sync_key")
    if not key or key == "0" or cache.get("tree") is None:
        return None
    client.ensure_provisioned()
    _fs_delta.strict = True
    try:
        tree = client.command("FolderSync", prov.build_foldersync(key))
    finally:
        _fs_delta.strict = False
    status = text_of(find(tree, "FolderHierarchy", "Status"))
    if status and status != "1":
        return None
    changes, new_key = _parse_folder_changes(tree)
    by_id = {f["id"]: f for f in cache["tree"]}
    by_id.update(changes)
    for node in find_all(tree, "FolderHierarchy", "Delete"):
        by_id.pop(text_of(find(node, "FolderHierarchy", "ServerId")) or "", None)
    return list(by_id.values()), (new_key if new_key != "0" else key)


def _patch_deep_foldersync():
    """Make the client's own ``foldersync()`` deep. Every upstream path —
    resolving a folder for mail/list, move, calendar lookup, the forced refresh
    after FolderCreate — goes through it; while it stayed shallow it overwrote
    the shared cache with system folders only, and opening a user folder failed
    with «папка 'i/…' не найдена». Caches from the shallow era (no ``deep``
    flag) are replaced on first use."""
    try:
        from outlook_activesync_mcp import client as client_mod
        from outlook_activesync_mcp.client import EasClient
    except ImportError:
        return
    if getattr(EasClient.foldersync, "_eas_deep", False):
        return

    def foldersync(self, *, force: bool = False, full: bool = False) -> list[dict]:
        """``force`` skips the TTL; the refresh is then a hierarchy delta off the
        stored SyncKey. ``full`` re-lists from SyncKey=0 — needed after our own
        FolderCreate/Delete, which the server never echoes back in a delta."""
        cache = self.store.get("folders")
        deep = bool(cache and cache.get("deep") and cache.get("tree") is not None)
        if not force and deep:
            if time.time() - client_mod._cache_epoch(cache.get("cached_at")) < client_mod._FOLDER_TTL:
                return cache["tree"]
        got, mode = None, "full"
        if deep and not full:
            try:
                got, mode = _folder_delta(self, cache), "delta"
            except Exception as e:  # noqa: BLE001
                if _is_unreachable(e) or _is_backoff(e):
                    # No server: keep showing the folders we know; a full FolderSync
                    # would only fail the same way (and cost more when it is back).
                    log.info("folder delta deferred (%s) — cached tree", e)
                    return cache["tree"]
                log.info("folder delta failed (%s) — full FolderSync", e)
        try:
            folders, key = got or _deep_folder_tree(self)
        except Exception as e:  # noqa: BLE001
            if deep and (_is_unreachable(e) or _is_backoff(e)):
                return cache["tree"]
            raise
        with self.store.transaction() as st:
            st["folders"] = {"cached_at": client_mod._now_epoch_iso(), "tree": folders,
                             "deep": True, "sync_key": key}
        log.info("foldersync %s: %d folders (mail=%d)", mode if got else "full", len(folders),
                 sum(1 for f in folders if str(f.get("type")) in MAIL_FOLDER_TYPES))
        return folders

    foldersync._eas_deep = True  # type: ignore[attr-defined]
    EasClient.foldersync = foldersync


def folders_list(a: Acct, refresh: bool = False) -> dict:
    """Full folder tree for the account (deep sync lives in the patched client)."""
    from outlook_activesync_mcp.commands.provision import FOLDER_TYPES
    from outlook_activesync_mcp.models import envelope
    backend = a.get()
    if not backend.lock.acquire(timeout=LOCK_WAIT_S):
        return _busy_answer(a, "folders", "list", {})
    try:
        try:
            backend._pace()
            tree = backend.client.foldersync(force=refresh)
            return envelope("list", [{
                "folder_id": f["id"], "name": f["name"],
                "kind": FOLDER_TYPES.get(f["type"], f"type-{f['type']}"),
                "type": f["type"], "parent_id": f.get("parent_id") or None,
            } for f in tree], total=len(tree), has_more=False)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] folders/list failed: %s", a.id, e)
            return {"ok": False, "action": "list", "count": 0, "items": [],
                    "error": getattr(e, "code", None) or type(e).__name__,
                    "message": _friendly_error(a, "folders", "list", e)}
    finally:
        backend.lock.release()


def _ensure_foldercreate_tokens():
    """MS-ASWBXML FolderHierarchy page: FolderCreate=0x13, FolderDelete=0x14,
    FolderUpdate=0x15 — omitted from outlook-activesync-mcp's TAGS table."""
    from outlook_activesync_mcp.wbxml import tables
    ns, tokmap = tables.TAGS[7]
    if 0x13 in tokmap:
        return
    tokmap[0x13] = "FolderCreate"
    tokmap[0x14] = "FolderDelete"
    tokmap[0x15] = "FolderUpdate"
    tables.TOKENS[(ns, "FolderCreate")] = (7, 0x13)
    tables.TOKENS[(ns, "FolderDelete")] = (7, 0x14)
    tables.TOKENS[(ns, "FolderUpdate")] = (7, 0x15)


def folder_create(a: Acct, name: str, parent_id: str = "0") -> dict:
    """Create a user mail folder (Type 12) under parent_id (0 = mailbox root)."""
    from outlook_activesync_mcp.wbxml import el, find, text_of
    name = (name or "").strip()
    if not name or len(name) > 64:
        return {"ok": False, "error": "bad_request", "message": "укажите имя папки (до 64 символов)",
                "action": "create", "count": 0, "items": []}
    parent_id = (parent_id or "0").strip() or "0"
    _ensure_foldercreate_tokens()
    backend = a.get()
    with backend.lock:
        try:
            backend._pace()
            # FolderCreate needs the current FolderSync SyncKey. Upstream only
            # ever sends SyncKey 0 (full tree); Exchange answers with Status 9
            # until we do one FolderSync and read the returned key. We keep the
            # key on the account for the next create.
            sync_key = (backend.client.store.get("folders") or {}).get("sync_key") or "0"
            if sync_key == "0":
                backend.client.foldersync(force=True, full=True)
                sync_key = (backend.client.store.get("folders") or {}).get("sync_key") or "0"
            body = el("FolderHierarchy", "FolderCreate",
                      el("FolderHierarchy", "SyncKey", text=sync_key),
                      el("FolderHierarchy", "ParentId", text=parent_id),
                      el("FolderHierarchy", "DisplayName", text=name),
                      el("FolderHierarchy", "Type", text="12"))
            tree = backend.client.command("FolderCreate", body)
            status = text_of(find(tree, "FolderHierarchy", "Status")) or "?"
            if status not in ("1", "1.0"):
                return {"ok": False, "error": "eas_status", "message": f"FolderCreate status {status}",
                        "action": "create", "count": 0, "items": []}
            server_id = text_of(find(tree, "FolderHierarchy", "ServerId"))
            # Full relist: a delta never echoes our own FolderCreate.
            backend.client.foldersync(force=True, full=True)
            return {"ok": True, "action": "create", "count": 1,
                    "items": [{"folder_id": server_id, "name": name, "parent_id": parent_id, "type": "12"}]}
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] folder create failed: %s", a.id, e)
            return {"ok": False, "action": "create", "count": 0, "items": [],
                    "error": type(e).__name__, "message": str(e)}


def folder_delete(a: Acct, folder_id: str) -> dict:
    """Delete a user mail folder (FolderDelete). System folders are refused."""
    from outlook_activesync_mcp.wbxml import el, find, text_of
    folder_id = (folder_id or "").strip()
    _ensure_foldercreate_tokens()
    backend = a.get()
    with backend.lock:
        try:
            tree = {f["id"]: f for f in backend.client.foldersync()}
            row = tree.get(folder_id)
            if not row or str(row.get("type")) != "12":
                return {"ok": False, "error": "bad_request", "message": "удалить можно только свою папку",
                        "action": "delete", "count": 0, "items": []}
            backend._pace()
            sync_key = (backend.client.store.get("folders") or {}).get("sync_key") or "0"
            res = backend.client.command("FolderDelete", el("FolderHierarchy", "FolderDelete",
                                         el("FolderHierarchy", "SyncKey", text=sync_key),
                                         el("FolderHierarchy", "ServerId", text=folder_id)))
            status = text_of(find(res, "FolderHierarchy", "Status")) or "?"
            if status != "1":
                why = {"3": "это системная папка", "4": "папка уже удалена — обновите список папок",
                       "6": "ошибка сервера, повторите через минуту",
                       "9": "список папок устарел — обновите его и повторите"}.get(status, f"код {status}")
                return {"ok": False, "error": "eas_status", "message": f"Папка не удалена: {why}",
                        "action": "delete", "count": 0, "items": []}
            # The folder is gone; the follow-up resync is best-effort. A slow or failing
            # foldersync here must not turn a successful delete into an error (the UI would
            # freeze on a phantom failure while the folder is actually already deleted).
            try:
                backend.client.foldersync(force=True, full=True)
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] post-delete foldersync failed (folder already gone): %s", a.id, e)
            return {"ok": True, "action": "delete", "count": 1, "items": [{"folder_id": folder_id}]}
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] folder delete failed: %s", a.id, e)
            return {"ok": False, "action": "delete", "count": 0, "items": [],
                    "error": type(e).__name__, "message": str(e)}


# Folders that count towards the account total: Inbox and user mail folders.
# Drafts / Sent / Deleted / Outbox have no meaningful "unread" (Outlook agrees).
UNREAD_TOTAL_TYPES = frozenset({"1", "2", "12"})


def _counted_folder_ids(a: Acct) -> set[str] | None:
    """Folder ids of UNREAD_TOTAL_TYPES from the stored tree (no network)."""
    try:
        tree = (a.backend.client.store.get("folders") or {}).get("tree") or []
    except AttributeError:
        return None
    return {str(f["id"]) for f in tree if str(f.get("type")) in UNREAD_TOTAL_TYPES} or None


def unread_snapshot(a: Acct) -> dict:
    counted = _counted_folder_ids(a) if a.backend is not None else None
    with a.unread_lock:
        notices, a.notices = a.notices, []  # delivered once
        total = sum(n for f, n in a.unread.items() if counted is None or f in counted)
        return {"ok": True, "folders": dict(a.unread), "total": total, "ts": a.unread_ts,
                "partial": sorted(a.unread_partial), "sweeping": a.unread_sweeping, "notices": notices}


def recount_unread_from_box(a: Acct, folder_id: str, box: dict) -> None:
    """Exact count for the mail period once the box is drained; "N+" before."""
    n = sum(1 for m in box["by_id"].values() if not m.get("is_read"))
    with a.unread_lock:
        a.unread[folder_id] = n
        if box.get("complete"):
            a.unread_partial.discard(folder_id)
        else:
            a.unread_partial.add(folder_id)
        a.unread_ts = time.time()


_SWEEP_FRESH_S = 60     # a folder refreshed this recently is only recounted
_SWEEP_DRAIN_CALLS = 6  # «Ещё» pages per folder per sweep (Stalwart: ~2.5 s each)
_SWEEP_BACKOFF_S = 600  # after Exchange throttled a sweep, leave it alone for a while
_SWEEP_MIN_GAP_S = 240  # auto-sync ticks every 1–2 min; one Sync per folder that often invites Exchange throttling


def unread_sweep(a: Acct, filt, fields, *, force: bool = False) -> dict:
    """Count unread in every Inbox / user folder in the background. Each folder
    goes through the same per-folder box as the list (first open = one prime,
    later = SyncKey delta), then its dump is drained a few pages at a time until
    the whole mail period is in — so badges are real counts, not the first page.
    One folder at a time: the account lock is free between Sync calls."""
    with a.unread_lock:
        skip = a.unread_sweeping or (not force and time.time() - a.unread_sweep_ts < _SWEEP_MIN_GAP_S)
        if not skip:
            a.unread_sweeping = True
    if skip:  # outside the lock: unread_snapshot takes it (not reentrant)
        return unread_snapshot(a)

    def run():
        backoff = False
        try:
            backend = a.get()
            with backend.lock:
                tree = backend.client.foldersync()
            counted = [f for f in tree if str(f.get("type")) in UNREAD_TOTAL_TYPES]
            ids = [str(f["id"]) for f in sorted(counted, key=lambda f: str(f.get("type")) != "2")]  # Inbox first
            for fid in ids:
                try:
                    box = a.mail_boxes.get(fid)
                    if not (box and box.get("complete") and time.time() - box.get("ts", 0) < _SWEEP_FRESH_S):
                        r = call(a, "mail", {"action": "list", "folder": fid, "limit": 40, "filter": filt, "fields": fields})
                        backoff = r.get("stale") or r.get("error") in ("throttled", "auth_failed")
                    for _ in range(_SWEEP_DRAIN_CALLS):
                        box = a.mail_boxes.get(fid) or {}
                        if backoff or box.get("complete") or not box.get("next_cursor") or len(box.get("by_id") or {}) >= _MAIL_BOX_CAP:
                            break
                        r = call(a, "mail", {"action": "list", "folder": fid, "cursor": box["next_cursor"], "limit": 100})
                        backoff = r.get("error") in ("throttled", "auth_failed")
                    box = a.mail_boxes.get(fid)
                    if box is not None and box.get("by_id") is not None:
                        recount_unread_from_box(a, fid, box)
                except Exception:  # noqa: BLE001 — one bad folder must not stop the rest
                    log.debug("[%s] unread sweep %s failed", a.id, fid, exc_info=True)
                if backoff:
                    log.info("[%s] unread sweep stopped at %s: server is throttling — back off %d s",
                             a.id, fid, _SWEEP_BACKOFF_S)
                    break
        except Exception as e:  # noqa: BLE001
            log.info("[%s] unread sweep failed: %s", a.id, e)
        finally:
            with a.unread_lock:
                a.unread_sweeping = False
                a.unread_ts = a.unread_sweep_ts = time.time()
                if backoff:  # the next sweep only after the gap + back-off
                    a.unread_sweep_ts += _SWEEP_BACKOFF_S

    threading.Thread(target=run, daemon=True).start()
    return unread_snapshot(a)


def refresh_unread(a: Acct, *, force: bool = False) -> dict:
    """Best-effort unread badges without advancing folder SyncKeys.

    Walking every folder via ``mail.list`` (Sync GetChanges) used to burn the
    Inbox SyncKey through years of older mail — new messages never reached the
    UI. Counts are taken only from folders the UI already listed this session
    (``a.unread`` seeds) plus a cheap Inbox-only estimate from the last list
    snapshot when present; otherwise we skip Sync entirely and keep the last
    known badges.
    """
    if not force and a.unread_ts and time.time() - a.unread_ts < 90:
        return unread_snapshot(a)
    # Do not call Sync/list here — badges update when the user opens a folder
    # (see loadList) or when the sync button forces a recount from the current
    # in-memory page.
    with a.unread_lock:
        if a.unread:
            a.unread_ts = time.time()
    return unread_snapshot(a)


def recount_unread_from_items(a: Acct, folder_id: str, items: list) -> None:
    """Update one folder's badge from a mail.list page (no extra Sync)."""
    if not folder_id:
        return
    n = sum(1 for m in items if not m.get("is_read"))
    with a.unread_lock:
        a.unread[folder_id] = n
        a.unread_ts = time.time()


# -- iMIP invitations ---------------------------------------------------------
# Stalwart (the Seller server) does not mail invitations for ActiveSync meetings and Exchange
# does not always deliver them to external domains, so the bridge can send the invitation itself:
# a text/calendar METHOD:REQUEST message, which Outlook/Gmail/Stalwart show as a meeting request.

SEND_OUTAGE_S = 300


def _send_down_error(a: Acct) -> Exception:
    return RuntimeError(f"Сервер {a.name} сейчас не отправляет почту (ошибка 120 при прошлой попытке). "
                        "Повторите через несколько минут; если не проходит и в веб-почте — это сбой на сервере.")


def _send_mime(a: Acct, mime: bytes) -> None:
    """SendMail with the outage guard (see Acct.send_down_until)."""
    if time.time() < a.send_down_until:
        raise _send_down_error(a)
    try:
        a.get().send(mime)
    except Exception as e:  # noqa: BLE001
        if getattr(e, "status", None) == 120:
            a.send_down_until = time.time() + SEND_OUTAGE_S
        raise


def _notice(a: Acct, text: str, error: bool = False) -> None:
    with a.unread_lock:
        a.notices = (a.notices + [{"text": text, "error": error, "ts": time.time()}])[-10:]


def _send_invites_bg(a: Acct, p: dict, attendees: list[str]) -> None:
    """Invitation mails after the meeting is already created: a slow or broken
    mail server must not keep the «Создать» button spinning for minutes."""
    try:
        sent = send_invites(a, p, attendees)
        if sent:
            _notice(a, f"Приглашения «{p.get('subject') or 'встреча'}» отправлены: {', '.join(sent)}")
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] invite mail failed: %s", a.id, e)
        _notice(a, f"Встреча «{p.get('subject') or ''}» создана, но приглашения письмом не ушли: "
                   f"{_friendly_error(a, 'mail', 'send', e)}", error=True)


_RRULE_FREQ = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}
_RRULE_DAY = {"mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}


def ics_rrule(p: dict) -> str | None:
    """The form's repeat_* params as an iCalendar RRULE: without it a series created on
    Seller reached attendees as a single meeting."""
    freq = _RRULE_FREQ.get(str(p.get("repeat") or "").lower())
    if not freq:
        return None
    parts = [f"FREQ={freq}"]
    n = int(p.get("repeat_interval") or 1)
    if n > 1:
        parts.append(f"INTERVAL={n}")
    days = [_RRULE_DAY[d] for d in (p.get("repeat_days") or []) if d in _RRULE_DAY]
    if freq == "WEEKLY" and days:
        parts.append("BYDAY=" + ",".join(days))
    if p.get("repeat_count"):
        parts.append(f"COUNT={int(p['repeat_count'])}")
    elif p.get("repeat_until"):
        parts.append("UNTIL=" + str(p["repeat_until"])[:10].replace("-", "") + "T235959Z")
    return "RRULE:" + ";".join(parts)


def reinvite_moved(a: Acct, item_id: str, start: str, end: str) -> dict:
    """A meeting dragged to a new time on Seller: Stalwart does not tell the attendees,
    so they get the updated invitation (same UID, a higher SEQUENCE) by mail."""
    e = _cal_item(a, item_id=item_id)
    if not e or not e.get("uid"):
        return {"ok": False, "error": "not_found", "message": "встреча не найдена в календаре — обновите календарь"}
    me = (a.email or "").lower()
    to = [x["address"] for x in e.get("attendees") or [] if x.get("address") and x["address"].lower() != me]
    sent = send_invites(a, {"uid": e["uid"], "subject": e.get("subject"), "location": e.get("location"),
                            "body": e.get("body"), "start": start, "end": end, "all_day": e.get("is_all_day"),
                            "sequence": int(time.time() // 60)}, to)  # minutes: grows with every move
    return {"ok": True, "action": "reinvite", "count": len(sent), "items": [{"address": x} for x in sent]}


def cancel_notice_target(a: Acct, item_id: str) -> tuple[dict, list[str]] | None:
    """Seller: the meeting I organize that is about to be cancelled, and who must hear
    of it. Read before the cancel — afterwards the cache hides the item. Stalwart
    deletes it silently, so without our CANCEL mail attendees keep the meeting."""
    if not a.green or not item_id:
        return None
    e = _cal_item(a, item_id=item_id)
    if not e or not e.get("uid"):
        return None
    me = (a.email or "").lower()
    org = ((e.get("organizer") or {}).get("address") or "").lower()
    if not (e.get("response_type") == "organizer" or org == me):
        return None  # an attendee removing someone else's meeting tells nobody
    to = _uniq_emails([x.get("address") for x in e.get("attendees") or []
                       if (x.get("address") or "").lower() != me])
    return (e, to) if to else None


def _send_cancel_bg(a: Acct, target: tuple[dict, list[str]], item_id: str) -> None:
    from outlook_activesync_mcp.models import instance_of
    e, to = target
    p = {**_event_invite_payload(e), "uid": e["uid"], "method": "CANCEL",
         "recurrence_id": instance_of(item_id), "sequence": int(time.time() // 60)}
    try:
        send_invites(a, p, to)
        _notice(a, f"Участникам «{e.get('subject') or 'встреча'}» отправлена отмена: {', '.join(to)}")
    except Exception as ex:  # noqa: BLE001
        log.warning("[%s] cancel mail failed: %s", a.id, ex)
        _notice(a, f"Встреча «{e.get('subject') or ''}» отменена, но участники письмом не уведомлены: "
                   f"{_friendly_error(a, 'mail', 'send', ex)}", error=True)


def send_invites(a: Acct, p: dict, attendees: list[str]) -> list[str]:
    import uuid
    from datetime import datetime, timezone
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid
    me = a.email
    to = [x for x in attendees if "@" in x and x.lower() != me.lower()]
    if not to:
        return []

    def utc(v):
        d = datetime.fromisoformat(v.replace("Z", "")[:16])
        return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") if not p.get("all_day") else d.strftime("%Y%m%d")

    def esc(t):
        return (t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")

    day = bool(p.get("all_day"))
    cancel = p.get("method") == "CANCEL"
    method = "CANCEL" if cancel else "REQUEST"
    uid = (p.get("uid") or "").strip() or f"{uuid.uuid4()}@eas-mail"
    note = (p.get("note") or "").strip()
    desc = ((note + "\n\n") if note else "") + (p.get("body") or "")
    lines = ["BEGIN:VCALENDAR", "PRODID:-//eas-mail//RU", "VERSION:2.0", f"METHOD:{method}", "BEGIN:VEVENT",
             f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
             f"DTSTART{';VALUE=DATE' if day else ''}:{utc(p['start'])}", f"DTEND{';VALUE=DATE' if day else ''}:{utc(p['end'])}",
             f"SUMMARY:{esc(p.get('subject') or '(без темы)')}", f"ORGANIZER;CN={esc(a.name)}:mailto:{me}"]
    if p.get("recurrence_id"):
        lines.append(f"RECURRENCE-ID:{p['recurrence_id']}")  # one occurrence of a series
    elif ics_rrule(p):
        lines.append(ics_rrule(p))
    if p.get("disallow_counter"):
        lines.append("X-MICROSOFT-DISALLOW-COUNTER:TRUE")
    if str(p.get("sensitivity")) == "2":
        lines.append("CLASS:PRIVATE")
    if p.get("location"):
        lines.append(f"LOCATION:{esc(p['location'])}")
    if desc:
        lines.append(f"DESCRIPTION:{esc(desc)}")
    rsvp = "FALSE" if cancel or p.get("response_requested") is False else "TRUE"
    lines += [f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP={rsvp}:mailto:{x}" for x in to]
    lines += [f"SEQUENCE:{int(p.get('sequence') or 0)}", f"STATUS:{'CANCELLED' if cancel else 'CONFIRMED'}",
              "END:VEVENT", "END:VCALENDAR"]
    m = EmailMessage()
    m["From"], m["To"] = me, ", ".join(to)
    m["Subject"] = ("Отменено: " if cancel else "Приглашение: ") + (p.get("subject") or "(без темы)")
    m["Date"], m["Message-ID"] = formatdate(localtime=True), make_msgid(domain="eas-mail")
    plain = ((note + "\n\n") if note else "") + (
        f"{a.name} {'отменяет встречу' if cancel else 'приглашает вас на встречу'} «{p.get('subject') or ''}»\n"
        f"{p['start'].replace('T', ' ')} – {p['end'].replace('T', ' ')}\n"
        + (f"\n{p['location']}\n" if p.get("location") else "")
        + (f"\n{p['body']}\n" if p.get("body") else ""))
    m.set_content(plain)
    m.add_alternative("\r\n".join(lines) + "\r\n", subtype="calendar", params={"method": method, "charset": "UTF-8"})
    _send_mime(a, m.as_bytes())
    return to

# -- calendar cache -----------------------------------------------------------
# One ActiveSync calendar sync costs ~30 s whatever the window (server side), so
# the whole window [today-7d, today+60d] is fetched once per account, kept in
# memory and refreshed in the background.
#
# MUST hold backend.lock for the Sync round-trips. The upstream client only
# locks its state file around read/write of SyncKeys, not across the network
# SyncKey=0 prime. Two concurrent primes on the same DeviceId → EAS 135
# (SyncStateAlreadyExists) and both mail/list and calendar die.

CAL_FIELDS = ["subject", "start", "end", "location", "is_all_day", "is_recurring", "organizer",
              "busy_status", "attendees", "response_type", "meeting_status", "body", "reminder",
              "categories", "uid", "online_meeting"]  # online_meeting: the separate «Ссылка на встречу» field
# Event body: keep plain text (safe to render), but a small cap dropped long
# descriptions and the join link that vendors (Yandex, Teams) put near the end —
# so read enough of it to keep both. (#13/#14)
CAL_BODY_TRUNCATION = 20000
CAL_TTL = 180
_last_activity = [time.time()]  # last UI request; the refresher idles when the app is not in use


def _cal_keepfresh():
    """Keep loaded calendar caches fresh while the UI is in use (idle app = no traffic)."""
    while True:
        time.sleep(60)
        for a in list(ACCTS.values()):
            c = a.cal
            if c["loaded"] and time.time() - c["ts"] > CAL_TTL and time.time() - _last_activity[0] < 900:
                _cal_refresh(a)


def _cal_claim(a: Acct) -> bool:
    """Mark a refresh as in flight; False if one already is. Done synchronously
    by whoever starts the refresh, so a waiter never sees loaded=False and
    loading=False in the gap before a background thread gets scheduled."""
    with a.cv:
        if a.cal["loading"]:
            return False
        a.cal["loading"] = True
        return True


def _calendar_ids(client) -> list[str]:
    """All Exchange/Stalwart calendars (FolderType 8). Stalwart may expose several
    type-8 folders; the first is sometimes an empty stub — callers must merge or pick."""
    ids = [f["id"] for f in client.foldersync() if str(f.get("type")) == "8" and f.get("id")]
    if ids:
        return ids
    from outlook_activesync_mcp.commands import calendar as cal_mod
    return [cal_mod._calendar_id(client)]


def _cal_expand(masters: dict, calendar_id: str, win_start, win_end, fields) -> tuple[list, list]:
    """Expand stored masters into occurrence rows for the UI window."""
    import copy
    from outlook_activesync_mcp.commands import calendar as cal_mod
    from outlook_activesync_mcp.models import EVENT_FIELDS, pack_item_id, resolve_fields
    from outlook_activesync_mcp.model.mapping import project_event

    from outlook_activesync_mcp.utils import default_window

    proj = resolve_fields(EVENT_FIELDS, fields)
    # Upstream compares occurrences against tz-aware datetimes; the cache window
    # is kept as dates — normalise exactly like calendar.list does.
    win_start, win_end = default_window(str(win_start), str(win_end), days=7)
    notes: list = []
    items = []
    for key, master in masters.items():
        # Multi-calendar masters carry _cid/_sid; single-calendar keeps plain keys.
        server_id = master.get("_sid") or key
        cid = master.get("_cid") or calendar_id
        body = {k: v for k, v in master.items() if k not in ("_cid", "_sid")}
        # One odd series must never blank the whole calendar: fall back to the
        # plain upstream expansion, and failing that skip just this meeting.
        try:
            occs = cal_mod._occurrences(_cal_inherit_exceptions(copy.deepcopy(body)), win_start, win_end, notes)
        except Exception as e:  # noqa: BLE001
            log.warning("calendar: series %s not expanded with exceptions (%s: %s)", server_id, type(e).__name__, e)
            try:
                occs = cal_mod._occurrences(copy.deepcopy(body), win_start, win_end, notes)
            except Exception as e2:  # noqa: BLE001
                log.warning("calendar: series %s skipped (%s: %s)", server_id, type(e2).__name__, e2)
                continue
        occs += _cal_orphan_exceptions(body, occs, win_start, win_end)
        for occ in occs:
            occ_item_id = pack_item_id(cid, server_id, instance=occ.get("instance_start"))
            row = project_event(occ, proj, item_id=occ_item_id)
            row.update({k: occ[k] for k in CAL_EXTRA if occ.get(k) is not None})
            items.append(row)
    items.sort(key=lambda e: e.get("start_iso") or "")
    return items, notes


def _cal_orphan_exceptions(master: dict, occs: list, win_start, win_end) -> list:
    """Changed occurrences the rule never generates. Stalwart (Seller) moves a series'
    start to its next regular date and keeps the earlier one only as an exception
    («Демо» every 2nd Friday: series from 16.10, the 02.10 meeting an exception) —
    upstream applies exceptions to generated dates only, so 02.10 vanished."""
    import copy
    from outlook_activesync_mcp.model.recurrence import _normalize_key
    if not master.get("recurrence") or not master.get("exceptions"):
        return []
    have = {o.get("instance_start") for o in occs}
    out = []
    for ex in _cal_inherit_exceptions(copy.deepcopy(master))["exceptions"]:
        key = _normalize_key(ex.get("exception_start"))
        start, end = ex.get("start"), ex.get("end")
        if ex.get("deleted") or not key or key in have or start is None or end is None:
            continue
        if end < win_start or start > win_end:
            continue
        row = {k: v for k, v in ex.items() if k not in ("exceptions", "exception_start", "deleted")}
        row.update(is_recurring=True, instance_start=key)
        out.append(row)
    return out


def _cal_inherit_exceptions(master: dict) -> dict:
    """An EAS Exception carries only what changed for that occurrence (moved
    time, new subject…). Upstream builds the occurrence from the exception alone,
    so a moved meeting lost its subject, organizer, attendees and status — and
    one with only a new subject got start=None. Fill the gaps from the series."""
    from datetime import timedelta
    from outlook_activesync_mcp.utils import parse_datetime
    exceptions = master.get("exceptions") or []
    if not exceptions:
        return master
    base = {k: v for k, v in master.items() if k != "exceptions"}
    dur = (master["end"] - master["start"]) if master.get("start") and master.get("end") else timedelta(hours=1)
    out = []
    for ex in exceptions:
        if ex.get("deleted"):
            out.append(ex)
            continue
        row = {**base, **{k: v for k, v in ex.items() if v not in (None, "")}}  # "" = not overridden
        if ex.get("start") is None:
            try:
                row["start"] = parse_datetime(ex.get("exception_start"))  # "" → None, no error
            except (TypeError, ValueError, AttributeError):
                row["start"] = None
        if row["start"] is None:
            out.append(ex)  # no usable ExceptionStartTime: upstream ignores such an entry
            continue
        if ex.get("end") is None:
            row["end"] = row["start"] + dur
        out.append(row)
    master["exceptions"] = out
    return master


def _meeting_status(raw: str | None) -> str | None:
    """MS-ASCAL MeetingStatus is a bit field: 1 = meeting, 2 = received,
    4 = cancelled (8 = "same as"). Upstream maps 5 (organizer cancelled) to
    «meeting» and 9 (plain meeting) to «cancelled»."""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return "appointment" if v == 0 else "cancelled" if v & 4 else "meeting"


_SENSITIVITY = {"0": "normal", "1": "personal", "2": "private", "3": "confidential"}
# What upstream parse_event/project_event drop and the event card/form need.
CAL_EXTRA = ("sensitivity", "response_requested", "disallow_counter", "attachments")


def _cal_extras(appdata) -> dict:
    """The item's own (not an Exception's) Sensitivity, response options and attachments."""
    from outlook_activesync_mcp.wbxml import find, find_all, text_of
    own = {c.tag: c for c in appdata.children if c.ns in ("Calendar", "AirSyncBase")}
    out = {}
    if "Sensitivity" in own:
        out["sensitivity"] = _SENSITIVITY.get(text_of(own["Sensitivity"]))
    if "ResponseRequested" in own:
        out["response_requested"] = text_of(own["ResponseRequested"]) != "0"
    if "DisallowNewTimeProposal" in own:
        out["disallow_counter"] = text_of(own["DisallowNewTimeProposal"]) == "1"
    if "Attachments" in own:
        atts = []
        for x in find_all(own["Attachments"], "AirSyncBase", "Attachment"):
            ref = text_of(find(x, "AirSyncBase", "FileReference"))
            if ref and text_of(find(x, "AirSyncBase", "IsInline")) != "1":
                size = text_of(find(x, "AirSyncBase", "EstimatedDataSize"))
                atts.append({"ref": ref, "name": text_of(find(x, "AirSyncBase", "DisplayName")) or "вложение",
                             "size": int(size) if (size or "").isdigit() else 0})
        out["attachments"] = atts
    return out


def _cal_parse(appdata) -> dict:
    """parse_event plus what upstream drops: correct MeetingStatus, the occurrence
    key of each exception (EAS 16.x), and per-occurrence status/busy for
    cancelled or changed occurrences of a series."""
    from outlook_activesync_mcp.model import mapping
    from outlook_activesync_mcp.wbxml import find, find_all, text_of
    ev = mapping.parse_event(appdata)
    # find() searches descendants: take the item's own MeetingStatus, not an Exception's.
    own = next((c for c in appdata.children if c.tag == "MeetingStatus"), None)
    if own is not None:
        ev["meeting_status"] = _meeting_status(text_of(own))
    ev.update(_cal_extras(appdata))
    box = find(appdata, "Calendar", "Exceptions")
    if box is not None and ev.get("exceptions"):
        for ex, node in zip(ev["exceptions"], find_all(box, "Calendar", "Exception")):
            # EAS 16.x drops Calendar:ExceptionStartTime and names the occurrence
            # by AirSyncBase:InstanceId. Upstream reads only the former, so every
            # exception had no key and was ignored: cancelled occurrences showed
            # as normal, moved ones stayed put, deleted ones came back.
            if not ex.get("exception_start"):
                ex["exception_start"] = text_of(find(node, "AirSyncBase", "InstanceId")) or ""
            st = _meeting_status(text_of(find(node, "Calendar", "MeetingStatus")))
            if st:
                ex["meeting_status"] = st
            busy = mapping._BUSY.get(text_of(find(node, "Calendar", "BusyStatus")))
            if busy:
                ex["busy_status"] = busy
    return ev


def _cal_apply_tree(tree, masters: dict, calendar_id: str | None = None) -> tuple[int, int]:
    """Apply one calendar Sync tree into masters. Returns (n_add, n_del).

    With ``calendar_id`` (multi type-8), keys are ``{calendar_id}/{ServerId}`` so
    stubs and the real Stalwart calendar never overwrite each other."""
    from outlook_activesync_mcp.wbxml import find, find_all, text_of

    def key(sid: str) -> str:
        return f"{calendar_id}/{sid}" if calendar_id else sid

    n_add = 0
    for node in find_all(tree, "AirSync", "Add") + find_all(tree, "AirSync", "Change"):
        sid = text_of(find(node, "AirSync", "ServerId"))
        appdata = find(node, "AirSync", "ApplicationData")
        if not sid or appdata is None:
            continue
        ev = _cal_parse(appdata)
        if calendar_id:
            ev["_cid"], ev["_sid"] = calendar_id, sid
        masters[key(sid)] = ev
        n_add += 1
    n_del = 0
    for node in find_all(tree, "AirSync", "Delete") + find_all(tree, "AirSync", "SoftDelete"):
        sid = text_of(find(node, "AirSync", "ServerId"))
        if sid and key(sid) in masters:
            masters.pop(key(sid), None)
            n_del += 1
    return n_add, n_del


# -- calendar cache on disk -----------------------------------------------------
# Masters + the SyncKey generation survive a restart, so launch shows the last
# known calendar at once and catches up with a delta (~0.5 s) instead of a
# SyncKey=0 prime (~30 s). The generation is checked against the client's state
# file on the first round: a mismatch is CursorExpired → full sync.
CAL_CACHE_VERSION = 4  # 4: sensitivity, response options, attachments; 3: multi type-8 calendars (Stalwart); 2: InstanceId exceptions


def _cal_cache_path(a: Acct):
    return bridge.DATA_DIR / f"calcache-{a.id}.pkl"


def _cal_cache_owner(a: Acct) -> list:
    return [a.cfg.get("username"), a.cfg.get("url")]


def _cal_save(a: Acct) -> None:
    import pickle
    c = a.cal
    with a.cv:
        blob = {"v": CAL_CACHE_VERSION, "owner": _cal_cache_owner(a), "ts": c["ts"], "gen": c["gen"],
                "cal_id": c["cal_id"], "cal_ids": c.get("cal_ids"), "gens": c.get("gens"),
                "filter": c.get("filter"), "masters": c["masters"], "truncated": c["truncated"]}
    path = _cal_cache_path(a)
    tmp = path.with_suffix(".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            pickle.dump(blob, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        log.info("[%s] calendar cache not saved: %s", a.id, e)


def _cal_forget(a: Acct) -> None:
    """Our own write is not in the masters (the server never echoes it): the
    next launch must not trust the file, or a cancelled meeting comes back."""
    _cal_cache_path(a).unlink(missing_ok=True)


def _cal_load(a: Acct, start, end) -> bool:
    """Fill the in-memory cache from disk (not yet refreshed: ts is the save time)."""
    import pickle
    try:
        with open(_cal_cache_path(a), "rb") as f:
            blob = pickle.load(f)
        if blob.get("v") != CAL_CACHE_VERSION or blob.get("owner") != _cal_cache_owner(a):
            return False
        items, _ = _cal_expand(blob["masters"], blob["cal_id"], start, end, CAL_FIELDS)
    except FileNotFoundError:
        return False
    except Exception as e:  # noqa: BLE001
        log.info("[%s] calendar cache unreadable: %s", a.id, e)
        return False
    with a.cv:
        a.cal.update(items=items, masters=blob["masters"], gen=blob["gen"], cal_id=blob["cal_id"],
                     cal_ids=blob.get("cal_ids"), gens=blob.get("gens"),
                     filter=blob.get("filter"), ts=blob["ts"], loaded=True, error=None,
                     range=(start, end), truncated=bool(blob.get("truncated")))
        a.cv.notify_all()  # a cold cal_events() is waiting for loaded
    log.info("[%s] calendar from disk: %d masters → %d events", a.id, len(blob["masters"]), len(items))
    return True


# Mail folders survive a restart the same way: the cached letters of every folder the
# app has listed, with the SyncKey generation they belong to. On launch the open
# folder is shown from disk at once and refreshed with a delta (Add/Change/Delete
# since last time, well under a second) instead of a SyncKey=0 prime of the whole
# window. The generation is checked against the client's state file on the first
# delta round: a mismatch re-primes that folder (see _mail_apply_delta).
MAIL_CACHE_VERSION = 1
MAIL_CACHE_EVERY_S = 15
_MAIL_BOX_KEYS = ("filter", "fields", "label", "gen", "complete", "next_cursor", "ts")


def _mail_cache_path(a: Acct):
    return bridge.DATA_DIR / f"mailcache-{a.id}.pkl"


def _mail_cache_sig(a: Acct) -> tuple:
    """Cheap fingerprint of the mail cache: changes when letters arrive, go or flip read."""
    out = []
    for cid, b in list(a.mail_boxes.items()):
        by_id = b.get("by_id") or {}
        out.append((cid, b.get("gen"), round(float(b.get("ts") or 0), 3), len(by_id),
                    sum(1 for m in list(by_id.values()) if not m.get("is_read"))))
    return tuple(sorted(out, key=lambda x: str(x[0])))


def _mail_cache_save(a: Acct) -> bool:
    """Snapshot the primed folders under the mailbox lock (skip if it is busy) and write
    them atomically, readable by this user only. Returns True when a file was written."""
    import pickle
    lock = getattr(a.backend, "lock", None)
    if lock is not None and not lock.acquire(timeout=2):
        return False
    try:
        # The SyncKey each folder was at goes along: on load it must still be the one in
        # the client's state file, or the letters in between would never come again.
        try:
            keys = a.backend.client.store.get("collections", {}) if a.backend.client else {}
        except Exception:  # noqa: BLE001
            keys = {}
        boxes = {cid: {**{k: b.get(k) for k in _MAIL_BOX_KEYS}, "by_id": dict(b["by_id"]),
                       "sync_key": (keys.get(cid) or {}).get("sync_key")}
                 for cid, b in a.mail_boxes.items()
                 if b.get("by_id") and b.get("gen") is not None and type(b.get("filter")) is not object}
    finally:
        if lock is not None:
            lock.release()
    path = _mail_cache_path(a)
    tmp = path.with_suffix(".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            pickle.dump({"v": MAIL_CACHE_VERSION, "owner": _cal_cache_owner(a), "boxes": boxes}, f,
                        protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
        return True
    except Exception as e:  # noqa: BLE001
        log.info("[%s] mail cache not saved: %s", a.id, e)
        return False


def _mail_cache_load(a: Acct) -> int:
    """Fill a.mail_boxes from disk; another login or server on this account → ignored."""
    import pickle
    try:
        with open(_mail_cache_path(a), "rb") as f:
            blob = pickle.load(f)
        if blob.get("v") != MAIL_CACHE_VERSION or blob.get("owner") != _cal_cache_owner(a):
            return 0
        boxes = blob["boxes"]
    except FileNotFoundError:
        return 0
    except Exception as e:  # noqa: BLE001
        log.info("[%s] mail cache unreadable: %s", a.id, e)
        return 0
    # A folder whose SyncKey moved on after the save (the app quit between a refresh
    # and the next save) keeps its letters on screen but gets one full listing.
    try:
        state = json.loads((bridge.DATA_DIR / a.cfg.get("state_name", "state.json")).read_text())
        cols = state.get("collections") or {}
    except Exception:  # noqa: BLE001
        cols = {}
    behind = 0
    for cid, b in boxes.items():
        cur = cols.get(cid) or {}
        if not b.get("sync_key") or cur.get("sync_key") != b["sync_key"] or cur.get("generation") != b.get("gen"):
            b["gen"], behind = None, behind + 1
        b.pop("sync_key", None)
        if cid not in a.mail_boxes:
            a.mail_boxes[cid] = b
    a._mail_saved_sig = _mail_cache_sig(a)
    log.info("[%s] mail from disk: %d folders, %d letters (%d to re-list)", a.id, len(boxes),
             sum(len(b["by_id"]) for b in boxes.values()), behind)
    return len(boxes)


def _mail_cache_keeper() -> None:
    """Write each account's mail cache when it changed (checked every 15 s)."""
    while True:
        time.sleep(MAIL_CACHE_EVERY_S)
        for a in list(ACCTS.values()):
            try:
                sig = _mail_cache_sig(a)
                if sig and sig != getattr(a, "_mail_saved_sig", None) and _mail_cache_save(a):
                    a._mail_saved_sig = sig
            except Exception as e:  # noqa: BLE001
                log.info("[%s] mail cache keeper: %s", a.id, e)


def mail_cached_list(a: Acct, params: dict) -> dict:
    """The open folder straight from cache, no server call and no lock: the UI draws it
    at once and then asks for the real (delta) list."""
    fid = str(params.get("folder") or "")
    box = a.mail_boxes.get(fid)
    fields = params.get("fields")
    want_fields = tuple(sorted(fields)) if fields else None
    if (not box or not box.get("by_id") or box.get("filter") != _mail_norm_filter(params.get("filter"))
            or box.get("fields") != want_fields):
        return {"ok": True, "action": "list", "count": 0, "items": [], "from_cache": True, "miss": True}
    items = _mail_sorted_items(box)
    return {"ok": True, "action": "list", "count": len(items), "items": items, "has_more": False,
            "next_cursor": None, "from_cache": True, "cached": len(box["by_id"])}


# AAS-24-02. Upstream stops a calendar listing after 8 pages. Exchange fills a page
# with up to 25 events, Stalwart (Seller) with 1–2 — so a fresh Seller login showed
# ~3 events and stopped. Page until the server is done, bounded by time instead;
# whatever is left continues right away in the background (delta on the same key).
_CAL_BUDGET_S = 90
_CAL_MAX_PAGES = 400


def _cal_more_soon(a: Acct) -> None:
    """The listing was cut by the time budget: carry on from the same SyncKey."""
    threading.Timer(1.0, cal_refresh_bg, args=(a,)).start()


def _cal_sync_one(backend, calendar_id: str, *, gen, opts, window: int, pages_budget: int,
                  t_deadline: float, masters: dict, multi: bool) -> tuple[dict, int | None, bool, int]:
    """Sync one calendar collection into ``masters``. Returns (masters, gen, more, pages)."""
    pages = 0
    more = True
    while pages < pages_budget and (pages == 0 or time.monotonic() < t_deadline):
        pages += 1
        prev_gen = gen
        backend._pace()
        tree, more, gen = backend.client.sync_round(
            calendar_id, generation=gen, window=window,
            options_children=opts if pages == 1 else None, get_changes=True)
        if gen != prev_gen and prev_gen is not None:
            # Fresh prime: drop only this calendar's rows.
            prefix = f"{calendar_id}/"
            if multi:
                masters = {k: v for k, v in masters.items() if not str(k).startswith(prefix)}
            else:
                masters = {}
        _cal_apply_tree(tree, masters, calendar_id if multi else None)
        if not more:
            break
    return masters, gen, more, pages


def _cal_full_sync(a: Acct, backend, start, end) -> None:
    """Prime SyncKey=0 and fill masters + expanded items (same window as before).

    Stalwart (Alfa-Seller) may expose several FolderType=8 calendars; the first is
    often empty. We sync every type-8 folder and merge so meetings are not lost."""
    from outlook_activesync_mcp.commands import calendar as cal_mod
    from outlook_activesync_mcp.commands.sync import body_preference
    from outlook_activesync_mcp.wbxml import el

    days = max(1, (end - start).days)
    t0 = time.time()
    cal_ids = _calendar_ids(backend.client)
    multi = len(cal_ids) > 1
    opts = [el("AirSync", "FilterType", text=cal_mod._filter_type(days)),
            body_preference(type_code="1", truncation=CAL_BODY_TRUNCATION)]
    masters: dict = {}
    gens: dict = {}
    truncated = False
    pages_total = 0
    t_start = time.monotonic()
    a.cal_step = "полная загрузка календаря"
    for i, calendar_id in enumerate(cal_ids):
        a.cal_step = (f"полная загрузка календаря ({i + 1}/{len(cal_ids)})"
                      if multi else "полная загрузка календаря")
        backend._pace()
        # generation=None primes SyncKey=0 for a fresh listing of this folder.
        left = max(1, _CAL_MAX_PAGES - pages_total)
        masters, gen, more, pages = _cal_sync_one(
            backend, calendar_id, gen=None, opts=opts, window=cal_mod._WINDOW,
            pages_budget=left, t_deadline=t_start + _CAL_BUDGET_S, masters=masters, multi=multi)
        gens[calendar_id] = gen
        pages_total += pages
        if more:
            truncated = True
        if pages_total >= _CAL_MAX_PAGES or time.monotonic() - t_start >= _CAL_BUDGET_S:
            truncated = truncated or more
            break
    primary = cal_ids[0]
    if multi:
        primary = max(cal_ids, key=lambda cid: sum(1 for k in masters if str(k).startswith(f"{cid}/")))
    items, _notes = _cal_expand(masters, primary, start, end, CAL_FIELDS)
    with a.cv:
        a.cal.update(items=items, masters=masters, gen=gens.get(primary), cal_id=primary,
                     cal_ids=cal_ids, gens=gens,
                     ts=time.time(), loaded=True, error=None, range=(start, end),
                     truncated=truncated, filter=cal_mod._filter_type(days))
        _cal_overlay_prune(a.cal, t0)  # the server's view now includes earlier writes
    _cal_save(a)
    log.info("[%s] calendar full: %d masters → %d events in %d pages%s%s", a.id, len(masters), len(items),
             pages_total, f" across {len(cal_ids)} calendars" if multi else "",
             " (continuing)" if truncated else "")
    if truncated:
        _cal_more_soon(a)


def _cal_delta_sync(a: Acct, backend, start, end) -> None:
    """Continue calendar SyncKey; merge Add/Change/Delete and re-expand."""
    from outlook_activesync_mcp.commands import calendar as cal_mod
    from outlook_activesync_mcp.commands.sync import body_preference
    from outlook_activesync_mcp.wbxml import el

    c = a.cal
    days = max(1, (end - start).days)
    opts = [el("AirSync", "FilterType", text=cal_mod._filter_type(days)),
            body_preference(type_code="1", truncation=CAL_BODY_TRUNCATION)]
    masters = dict(c.get("masters") or {})
    cal_ids = list(c.get("cal_ids") or ([c["cal_id"]] if c.get("cal_id") else _calendar_ids(backend.client)))
    multi = len(cal_ids) > 1
    gens = dict(c.get("gens") or ({c["cal_id"]: c["gen"]} if c.get("cal_id") is not None and c.get("gen") is not None else {}))
    pages_total = changed = 0
    truncated = False
    t_start = time.monotonic()
    for i, calendar_id in enumerate(cal_ids):
        gen = gens.get(calendar_id)
        if gen is None:
            continue  # never primed in this cache — full sync will pick it up
        a.cal_step = (f"обновление календаря ({i + 1}/{len(cal_ids)}), страница 1"
                      if multi else "обновление календаря, страница 1")
        backend._pace()
        left = max(1, _CAL_MAX_PAGES - pages_total)
        before = len(masters)
        masters, gen, more, pages = _cal_sync_one(
            backend, calendar_id, gen=gen, opts=opts, window=cal_mod._WINDOW,
            pages_budget=left, t_deadline=t_start + _CAL_BUDGET_S, masters=masters, multi=multi)
        gens[calendar_id] = gen
        # Approximate change count from size delta when multi (apply already merged).
        changed += abs(len(masters) - before)
        pages_total += pages
        if more:
            truncated = True
        if pages_total >= _CAL_MAX_PAGES or time.monotonic() - t_start >= _CAL_BUDGET_S:
            break
    primary = c.get("cal_id") or cal_ids[0]
    if multi:
        primary = max(cal_ids, key=lambda cid: sum(1 for k in masters if str(k).startswith(f"{cid}/")))
    items, _notes = _cal_expand(masters, primary, start, end, CAL_FIELDS)
    with a.cv:
        a.cal.update(items=items, masters=masters, gen=gens.get(primary), cal_id=primary,
                     cal_ids=cal_ids, gens=gens,
                     ts=time.time(), loaded=True, error=None, range=(start, end),
                     truncated=truncated, filter=cal_mod._filter_type(days))
    _cal_save(a)
    log.info("[%s] calendar delta: ~%d changes, %d masters → %d events%s", a.id, changed, len(masters),
             len(items), " (continuing)" if truncated else "")
    if truncated:
        _cal_more_soon(a)


def _cal_refresh(a: Acct, claimed: bool = False, force: bool = False):
    from datetime import date, timedelta
    from outlook_activesync_mcp.commands import calendar
    from outlook_activesync_mcp.errors import CursorExpired, EasStatusError
    c = a.cal
    if not claimed and not _cal_claim(a):
        return
    force = force or bool(c.pop("force_next", False))
    try:
        backend = a.get()
        today = date.today()
        # Back far enough for this month's grid (the mail-side month rail starts on the
        # Monday before the 1st); 93 days keep the server's «3 months» FilterType.
        start, end = today - timedelta(days=CAL_PAST_DAYS), today + timedelta(days=60)
        # Tests / no client: keep the old calendar.handle path.
        if backend.client is None:
            with backend.lock:
                backend._pace()
                r = calendar.handle(backend.client, "list", start=start.isoformat(), end=end.isoformat(),
                                    limit=1000, fields=CAL_FIELDS)
            with a.cv:
                c.update(items=r.get("items", []), ts=time.time(), loaded=True, error=None,
                         range=(start, end), truncated=bool(r.get("truncated")),
                         gen=None, masters={}, cal_id=None)
            log.info("[%s] calendar cache: %d events%s", a.id, len(r.get("items", [])),
                     " (truncated)" if r.get("truncated") else "")
            return

        if not c.get("loaded") and not force:
            _cal_load(a, start, end)
        # The day rolling over shifts the window but not the server FilterType:
        # the masters already cover it, so the delta just re-expands them.
        same_filter = c.get("filter") == calendar._filter_type(max(1, (end - start).days))
        can_delta = (not force and c.get("loaded") and c.get("gen") is not None
                     and c.get("cal_id") and same_filter and c.get("masters") is not None)
        # Empty cache on a multi-calendar account is usually the Stalwart stub
        # (first type-8 folder) — do not keep delta-syncing nothing.
        if can_delta and not c.get("masters"):
            try:
                if len(_calendar_ids(backend.client)) > 1:
                    can_delta = False
            except Exception:  # noqa: BLE001
                pass
        with backend.lock:
            if can_delta:
                try:
                    _cal_delta_sync(a, backend, start, end)
                    return
                except CursorExpired:
                    log.info("[%s] calendar delta cursor expired — full sync", a.id)
                except EasStatusError as e:
                    log.info("[%s] calendar delta Sync status %s — full sync",
                             a.id, getattr(e, "status", "?"))
                except Exception as e:  # noqa: BLE001
                    if _is_backoff(e):
                        raise  # the cached calendar stays; a full sync would only add load
                    log.warning("[%s] calendar delta failed: %s — full sync", a.id, e)
            _cal_full_sync(a, backend, start, end)
    except Exception as e:  # noqa: BLE001
        step = getattr(a, "cal_step", "") or "синхронизация календаря"
        log.warning("[%s] calendar refresh failed at «%s»: %s", a.id, step, e)
        msg = _friendly_error(a, "events", "list", e)
        if a.name not in msg:  # say which account and where it stopped (AAS-24-02)
            msg = f"Календарь «{a.name}»: {msg} (шаг: {step}). Нажмите «Синхронизировать», чтобы продолжить."
        with a.cv:
            c["error"], c["error_ts"] = msg, time.time()
    finally:
        with a.cv:
            c["loading"] = False
            a.cv.notify_all()


def cal_refresh_bg(a: Acct, force: bool = False):
    if _cal_claim(a):
        threading.Thread(target=_cal_refresh, args=(a, True, force), daemon=True).start()
    elif force:
        a.cal["force_next"] = True  # the running (delta) refresh cannot see our write


# -- local overlay for our own calendar writes ---------------------------------
# ActiveSync never echoes a client's own Sync commands back to it, so the delta
# refresh cannot see an event we just deleted, answered or created — it stayed on
# screen until a manual full sync. Writes are mirrored here right away; a full
# sync that started after the write drops the overlay (the server has it then).

def _utc_iso(local: str) -> str:
    from datetime import datetime, timezone
    return datetime.fromisoformat(str(local)[:16]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cal_overlay_prune(c: dict, before: float) -> None:
    c["hidden"] = {k: t for k, t in (c.get("hidden") or {}).items() if t >= before}
    c["overrides"] = {k: v for k, v in (c.get("overrides") or {}).items() if v[0] >= before}
    c["pending"] = [e for e in (c.get("pending") or []) if e.get("_ts", 0) >= before]


def _cal_after_write(a: Acct, params: dict, res: dict) -> None:
    action, iid, now = params.get("action"), params.get("item_id"), time.time()
    if action in ("cancel", "respond", "update", "create"):
        _cal_forget(a)
    c = a.cal
    with a.cv:
        hidden = c.setdefault("hidden", {})
        overrides = c.setdefault("overrides", {})
        pending = c.setdefault("pending", [])
        if action == "cancel" and iid:
            hidden[iid] = now
        elif action == "respond" and iid:
            resp = str(params.get("response") or "").lower()
            if resp == "decline":
                hidden[iid] = now  # Exchange removes a declined meeting from the calendar
            else:
                patch = {"response_type": {"accept": "accepted"}.get(resp, resp)}
                overrides[iid] = (now, {**overrides.get(iid, (0, {}))[1], **patch})
        elif action == "update" and iid:
            patch = {k: params[k] for k in ("subject", "location", "body") if params.get(k) is not None}
            if params.get("start"):
                patch.update(start=str(params["start"]).replace("T", " ")[:16], start_iso=_utc_iso(params["start"]))
            if params.get("end"):
                patch["end"] = str(params["end"]).replace("T", " ")[:16]
            if params.get("attendees") is not None:
                patch["attendees"] = [{"address": x, "type": (params.get("attendee_types") or {}).get(x, "required")}
                                      for x in params["attendees"]]
            overrides[iid] = (now, {**overrides.get(iid, (0, {}))[1], **patch})
        elif action == "create" and params.get("start"):
            made = (res.get("items") or [{}])[0]
            att = params.get("attendees") or []
            pending.append({
                "_ts": now, "item_id": made.get("item_id") or f"pending-{now}",
                "subject": params.get("subject") or "", "location": params.get("location") or None,
                "body": params.get("body") or None, "is_all_day": bool(params.get("all_day")),
                "start": str(params["start"]).replace("T", " ")[:16], "start_iso": _utc_iso(params["start"]),
                "end": str(params.get("end") or params["start"]).replace("T", " ")[:16],
                "busy_status": "busy", "is_recurring": bool(params.get("repeat")),
                "meeting_status": "meeting" if att else "appointment", "response_type": "organizer",
                "organizer": {"name": a.name, "address": a.email},
                "attendees": [{"address": x, "type": (params.get("attendee_types") or {}).get(x, "required")} for x in att],
            })
    if action in ("create", "update"):
        cal_refresh_bg(a, force=True)  # converge on the server's version in the background


# -- RSVPs that went out by mail ------------------------------------------------
# When Exchange refuses MeetingResponse (status 2/3) the answer reaches the
# organizer as an iTIP mail, but the server copy of the meeting never changes:
# a full sync brought a declined meeting back and showed an accepted one as
# «без ответа». These answers are kept on disk until the meeting is over.
_RSVP_TYPE = {"accept": "accepted", "tentative": "tentative", "decline": "declined"}


def _rsvp_path(a: Acct):
    return bridge.DATA_DIR / f"rsvp-{a.id}.json"


def _rsvp_answers(a: Acct) -> dict:
    c = a.cal
    if "answered" not in c:
        try:
            c["answered"] = json.loads(_rsvp_path(a).read_text())
        except (OSError, ValueError):
            c["answered"] = {}
    now = time.time()
    c["answered"] = {k: v for k, v in c["answered"].items() if v.get("until", 0) > now}
    return c["answered"]


def _rsvp_remember(a: Acct, item_id: str, response: str) -> None:
    from datetime import datetime
    ev = _cal_item(a, item_id=item_id) or {}
    try:
        until = datetime.fromisoformat(str(ev["end"]).replace(" ", "T")).timestamp()
    except (KeyError, ValueError):
        until = time.time() + 14 * 86400
    with a.cv:
        answers = _rsvp_answers(a)
        answers[item_id] = {"response": response, "until": until}
        blob = json.dumps(answers)
    try:
        fd = os.open(_rsvp_path(a), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(blob)
    except OSError as e:
        log.info("[%s] RSVP not saved: %s", a.id, e)


def _cal_overlay(c: dict, items: list) -> list:
    hidden, overrides = c.get("hidden") or {}, c.get("overrides") or {}
    answered = c.get("answered") or {}
    if answered:
        items = [({**e, "response_type": _RSVP_TYPE[answered[e["item_id"]]["response"]]}
                  if e.get("item_id") in answered else e)
                 for e in items if answered.get(e.get("item_id"), {}).get("response") != "decline"]
    out = [({**e, **overrides[e["item_id"]][1]} if e.get("item_id") in overrides else e)
           for e in items if e.get("item_id") not in hidden]
    have = {e.get("item_id") for e in out}
    out += [{k: v for k, v in e.items() if k != "_ts"} for e in c.get("pending") or []
            if e["item_id"] not in have and e["item_id"] not in hidden]
    out.sort(key=lambda e: e.get("start_iso") or "")
    return out


CAL_PAST_DAYS = 33


def cal_events(a: Acct, start: str, end: str) -> dict:
    from datetime import date, datetime
    c = a.cal
    ds, de = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    with a.cv:  # RLock-backed: cal_refresh_bg may re-acquire it
        stale = time.time() - c["ts"] > CAL_TTL
        # After a failed first load, answer with that error for a few seconds
        # instead of hammering the server on every UI/tray request.
        backoff = c["error"] and not c["loaded"] and time.time() - c.get("error_ts", 0) < 5
        if (stale or not c["loaded"]) and not backoff:
            cal_refresh_bg(a)
        if not c["loaded"]:
            a.cv.wait_for(lambda: c["loaded"] or not c["loading"], timeout=150)
        if not c["loaded"]:
            return {"ok": False, "action": "list", "count": 0, "items": [],
                    "error": "calendar_unavailable", "message": c["error"] or "календарь ещё загружается, повторите через минуту"}
        lo, hi = c["range"]
        _rsvp_answers(a)
        items = _cal_overlay(c, c["items"])
    if ds < lo or de > hi:  # outside the cached window: ask Exchange directly
        direct = call(a, "events", {"action": "list", "start": start, "end": end, "limit": 1000, "fields": CAL_FIELDS})
        if de <= lo or ds >= hi or not direct.get("ok", True):
            return direct
        # Partly cached: the direct list can miss what the cache has — Stalwart answers
        # it from the empty stub calendar (the cache merges all calendars), so the
        # month rail stayed blank. Keep the cached meetings of the overlap as well.
        seen = {(e.get("uid") or e.get("item_id"), e.get("start_iso")) for e in direct.get("items") or []}
        items = [*(direct.get("items") or []),
                 *(e for e in items if (e.get("uid") or e.get("item_id"), e.get("start_iso")) not in seen)]

    def ts(x):
        return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp() if x else 0
    # ``end`` is exclusive in the UI (day view sends tomorrow). Same calendar day
    # for start and end used to collapse the window to midnight→midnight = empty.
    from datetime import timedelta
    hi_day = de if de > ds else de + timedelta(days=1)
    lo_t = datetime.combine(ds, datetime.min.time()).timestamp()
    hi_t = datetime.combine(hi_day, datetime.min.time()).timestamp()

    def end_ts(e):  # "end" is local "YYYY-MM-DD HH:MM"; fall back to the start
        try:
            return datetime.fromisoformat(str(e["end"]).replace(" ", "T")).timestamp()
        except (KeyError, ValueError):
            return ts(e.get("start_iso"))
    out = with_join_links(a, [e for e in items if ts(e.get("start_iso")) < hi_t and max(end_ts(e), ts(e.get("start_iso")) + 1) > lo_t])
    return {"ok": True, "action": "list", "count": len(out), "items": out,
            "truncated": c["truncated"], "cached_age": int(time.time() - c["ts"])}


def cal_refresh_wait(a: Acct, force: bool = False):
    """Run a calendar refresh and return when it is done. A plain request joins
    one already in flight; a forced one waits for it and then does its own full
    sync (the in-flight one may be a delta that cannot fix a drifted cache)."""
    for _ in range(2):
        if _cal_claim(a):
            _cal_refresh(a, claimed=True, force=force)
            return
        with a.cv:
            a.cv.wait_for(lambda: not a.cal["loading"], timeout=120)
        if not force:
            return


def retry_login(a: Acct) -> dict:
    """Clear a rejected-password latch (after fixing the password / connecting the VPN)."""
    from outlook_activesync_mcp.commands import settings
    backend = a.get()
    with backend.lock:
        r = settings.handle(backend.client, "reset_auth")
    a.cal["error"] = None
    return r


# -- self-update from GitHub Releases ------------------------------------------
# The repo is public, so any colleague's app finds and downloads new releases over
# plain HTTPS — no GitHub CLI, no login. The latest release comes from the REST API
# (with the asset's SHA-256); if that is rate-limited (60 calls/hour per IP — a whole
# office behind one address), from the /releases/latest redirect and the fixed asset
# name. app/self_update.sh downloads, checks the checksum and the version inside,
# swaps the bundle and relaunches. Dev builds (a checkout, not a bundle) only report.

UPDATE_REPO = "olesyaba/aas-mail"
UPDATE_CHECK_S = 6 * 3600
_update_cache: dict = {}
_update_lock = threading.Lock()


def _ver_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


ASSET_SUFFIX = "-mac.zip"  # the release file this build updates from (Windows: -win.zip)


def _app_bundle() -> Path | None:
    """The .app this server runs from (dist build), or None for a dev checkout."""
    for p in Path(__file__).resolve().parents:
        if p.suffix == ".app":
            return p
    return None


def _latest_release() -> dict:
    """{tag, notes, url, sha256} of the newest release; raises when GitHub is unreachable."""
    import requests
    ua = {"User-Agent": f"AAS-mail/{APP_META['version']}"}
    r = requests.get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest", timeout=20,
                     headers={**ua, "Accept": "application/vnd.github+json"})
    if r.status_code == 200:
        j = r.json()
        asset = next((a for a in j.get("assets") or [] if str(a.get("name", "")).endswith(ASSET_SUFFIX)), None)
        digest = str((asset or {}).get("digest") or "")
        return {"tag": str(j.get("tag_name") or ""), "notes": str(j.get("body") or "")[:3000],
                "url": (asset or {}).get("browser_download_url") or "",
                "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else ""}
    # Rate-limited or the API is blocked: the web redirect names the latest tag.
    h = requests.head(f"https://github.com/{UPDATE_REPO}/releases/latest", allow_redirects=False,
                      timeout=20, headers=ua)
    tag = (h.headers.get("location") or "").rstrip("/").rsplit("/", 1)[-1]
    if not tag.startswith("v"):
        raise RuntimeError(f"GitHub ответил {r.status_code}")
    ver = tag.lstrip("v")
    return {"tag": tag, "notes": "", "sha256": "",
            "url": f"https://github.com/{UPDATE_REPO}/releases/download/{tag}/AAS-mail-{ver}{ASSET_SUFFIX}"}


def update_check(force: bool = False) -> dict:
    """Latest release vs this version. Cached for UPDATE_CHECK_S; never raises."""
    with _update_lock:
        if not force and _update_cache and time.time() - _update_cache.get("ts", 0) < UPDATE_CHECK_S:
            return dict(_update_cache)
    cur = APP_META["version"]
    out = {"ok": True, "current": cur, "latest": None, "tag": None, "available": False, "notes": "",
           "can_install": False, "reason": "", "ts": time.time(), "url": "", "sha256": ""}
    try:
        rel = _latest_release()
        out.update(latest=rel["tag"].lstrip("v"), tag=rel["tag"], notes=rel["notes"],
                   url=rel["url"], sha256=rel["sha256"])
        out["available"] = bool(rel["url"]) and _ver_tuple(out["latest"]) > _ver_tuple(cur)
    except Exception as e:  # noqa: BLE001 — offline, proxy, timeout, bad JSON
        log.info("update check failed: %s", e)
        out["reason"] = "Не удалось проверить обновления: нет связи с github.com"
    bundle = _app_bundle()
    out["can_install"] = bool(out["available"] and bundle)
    if out["available"] and not bundle:
        out["reason"] = "Сборка из исходников: обновите через git pull и app/build_app.sh"
    with _update_lock:
        _update_cache.clear()
        _update_cache.update(out)
    return dict(out)


def update_install() -> dict:
    """Start app/self_update.sh detached: it outlives this server when the app quits."""
    info = update_check(force=True)
    if not info.get("can_install"):
        return {"ok": False, "error": "bad_request",
                "message": info.get("reason") or "Новой версии нет — у вас последняя."}
    here = Path(__file__).resolve().parent
    script = next((p for p in (here / "self_update.sh", here / "app" / "self_update.sh") if p.exists()), None)
    if not script:
        return {"ok": False, "error": "bad_request", "message": "В сборке нет self_update.sh"}
    with open(bridge.DATA_DIR / "update.log", "a") as logf:
        subprocess.Popen(["/bin/bash", str(script), info["url"], info.get("sha256") or "-",
                          str(_app_bundle()), info["latest"]],
                         stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    log.info("self-update to %s started", info["latest"])
    return {"ok": True, "started": True, "latest": info["latest"]}


# -- preferences (signature, view, sync) ---------------------------------------

PREFS_PATH = bridge.DATA_DIR / "prefs.json"
DEFAULT_PREFS = {
    "signature": "", "signature2": "", "sig_replies": True, "threads": True,
    "auto_sync": 1, "cal_view": "week", "links": [],
    # Pinned mail folders as "acct:folderId" strings (e.g. "main:42").
    "favorite_folders": [],
    # Category name → "#RRGGBB". ActiveSync carries category names but not
    # Outlook's colour table, so colours are picked here (auto or by the user).
    "category_colors": {},
    # The Seller mailbox has its own tags: same name, different category.
    "category_colors_seller": {},
    # Appearance: "system" follows macOS, or force "light" / "dark".
    "theme": "system",
    # Meeting reminder lead (minutes) for the banner + macOS notification; 0 = off.
    "reminder_minutes": 5,
    # New-mail alert sound: bundled CAF name, or "none" for a silent banner.
    "mail_sound": "notice14",
    # Mail period filter (EAS FilterType), per account: 3=1 wk, 4=2 wk, 5=1 mo, 0=all.
    # main uses mail_window; the Seller mailbox keeps its own mail_window_seller.
    "mail_window": 5,
    "mail_window_seller": 5,
    # Mail list order (AAS-24-10), per account: date_desc (default) | date_asc | from | subject.
    "mail_sort": "date_desc",
    "mail_sort_seller": "date_desc",
    # Self-update: "auto" (check every 6 h and install) or "manual" (only on request).
    "update_mode": "auto",
    # Working day for «Свободно у всех» suggestions (local hours).
    "work_start": 9, "work_end": 18,
    # Meeting scheduler: saved rooms ({name, address}) and the «Лучшие варианты» constraints.
    "rooms": [],
    "sched_cons": {"not_before_10": True, "lunch": True, "fri_late": True, "room": True},
    # Unified view: month + day agenda as a rail beside the mail.
    "unified_cal": False,
    # Meeting form: a Seller invitee also gets their Alfa-Bank address added.
    "twin_bank": False,
}
MAIL_SORTS = ("date_desc", "date_asc", "from", "subject")
_prefs_lock = threading.Lock()


def load_prefs() -> dict:
    try:
        saved = json.loads(PREFS_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    out = {**DEFAULT_PREFS}
    for k, v in saved.items():
        if k not in DEFAULT_PREFS:
            continue
        if k == "favorite_folders" and isinstance(v, list):
            out[k] = [str(x)[:80] for x in v if isinstance(x, str)][:80]
        elif type(v) is type(DEFAULT_PREFS[k]):
            out[k] = v
    return out


def update_prefs(patch: dict) -> dict:
    with _prefs_lock:
        cur = load_prefs()
        for k, v in (patch or {}).items():
            if k not in DEFAULT_PREFS:
                continue
            if k == "favorite_folders":
                if not isinstance(v, list):
                    continue
                cur[k] = [str(x)[:80] for x in v if isinstance(x, str)][:80]
                continue
            if k.startswith("category_colors"):
                if isinstance(v, dict):
                    cur[k] = {str(n)[:60]: c for n, c in list(v.items())[:80]
                              if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c)}
                continue
            if type(v) is not type(DEFAULT_PREFS[k]):
                continue
            if k in ("signature", "signature2"):
                v = v[:5000]
            if k == "cal_view" and v not in ("day", "work", "week"):
                continue
            if k == "auto_sync" and v not in (0, 0.5, 1, 2, 5, 10):
                continue
            if k in ("mail_window", "mail_window_seller") and v not in (0, 3, 4, 5):
                continue
            if k in ("mail_sort", "mail_sort_seller") and v not in MAIL_SORTS:
                continue
            if k == "update_mode" and v not in ("auto", "manual"):
                continue
            # Appearance: system | light | dark | StylesBA palettes (dark + light).
            if k == "theme" and v not in (
                "system", "light", "dark",
                "navy-orange", "navy-orange-light",
                "royal-velvet", "royal-velvet-light",
                "eclipse-almond", "eclipse-almond-light",
                "mist", "mist-light", "forest", "forest-light",
                "coral-mint", "coral-mint-light",
                "amber", "amber-light", "dusk", "dusk-light",
                # *-auto: palette that follows macOS light/dark (resolved in the web UI)
                "navy-orange-auto", "royal-velvet-auto", "eclipse-almond-auto",
                "mist-auto", "forest-auto", "coral-mint-auto",
                "amber-auto", "dusk-auto",
            ):
                continue
            if k == "reminder_minutes" and v not in (0, 1, 2, 5, 10, 15, 30):
                continue
            if k == "mail_sound" and v not in ("notice14", "short", "none"):
                continue
            if k in ("work_start", "work_end") and not 0 <= v <= 24:
                continue
            if k == "rooms":
                v = [{"name": str(x.get("name", ""))[:120], "address": str(x.get("address", ""))[:200]}
                     for x in v[:30] if isinstance(x, dict) and "@" in str(x.get("address", ""))]
            if k == "sched_cons":
                v = {c: bool(v.get(c, d)) for c, d in DEFAULT_PREFS["sched_cons"].items()}
            if k == "links":
                v = [{"name": str(x.get("name", ""))[:80], "url": str(x.get("url", ""))[:500]}
                     for x in v[:30] if isinstance(x, dict) and str(x.get("url", "")).startswith(("http://", "https://"))]
            cur[k] = v
        if cur["work_end"] <= cur["work_start"]:
            cur["work_start"], cur["work_end"] = DEFAULT_PREFS["work_start"], DEFAULT_PREFS["work_end"]
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(json.dumps(cur, ensure_ascii=False))
        os.chmod(PREFS_PATH, 0o600)
        return cur


# -- message rendering ------------------------------------------------------

class Gone(Exception):
    """The message no longer exists on the server (deleted/moved, or a stale search hit)."""


def get_mime(a: Acct, item_id: str) -> bytes:
    with a.mime_lock:
        raw = a.mime_cache.get(item_id)
    if raw is None:
        try:
            raw = a.get().fetch_mime(item_id)
        except KeyError:
            raise Gone("Письмо недоступно: удалено или перемещено. Обновите список (⟳).") from None
        with a.mime_lock:
            if len(a.mime_cache) > 60:
                a.mime_cache.pop(next(iter(a.mime_cache)))
            a.mime_cache[item_id] = raw
    return raw


def _addr_list(msg, name):
    out = []
    for h in msg.get_all(name, []) or []:
        for a in getattr(h, "addresses", []):
            out.append({"name": a.display_name, "address": a.addr_spec})
    return out


def _leaves(part, top=True):
    """Leaf parts; an attached message/rfc822 counts as one leaf (not descended into)."""
    if not top and part.get_content_type() == "message/rfc822":
        yield part
    elif part.is_multipart():
        for p in part.iter_parts():
            yield from _leaves(p, False)
    else:
        yield part


def _body_html(msg):
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is not None and body.get_content_subtype() == "html":
        try:
            return body.get_content()
        except Exception:  # noqa: BLE001
            return (body.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return None


SKIP_TYPES = {"application/pkcs7-signature", "application/x-pkcs7-signature"}


def _attachment_parts(msg, html_doc=None):
    """Real attachments: everything that is not the body, a signature, or a picture
    the HTML shows inline (referenced by cid:)."""
    body = msg.get_body(preferencelist=("html", "plain"))
    refs = {c.lower() for c in re.findall(r"cid:([^\"'\s>)]+)", html_doc or "", re.I)}
    parts = []
    for p in _leaves(msg):
        ctype, disp = p.get_content_type(), p.get_content_disposition()
        cid = (p.get("Content-ID") or "").strip("<> ").lower()
        if p is body or ctype in SKIP_TYPES:
            continue
        if ctype in ("text/plain", "text/html") and not p.get_filename() and disp != "attachment":
            continue  # the other body alternative
        if cid and cid in refs and disp != "attachment":
            continue  # shown inside the HTML
        parts.append(p)
    return parts


def _inline_images(msg, html_doc=None):
    """Pictures shown inside the HTML (cid:) — not attachments, but still saveable."""
    refs = {c.lower() for c in re.findall(r"cid:([^\"'\s>)]+)", html_doc or "", re.I)}
    out = []
    for p in _leaves(msg):
        cid = (p.get("Content-ID") or "").strip("<> ").lower()
        if cid and cid in refs and p.get_content_maintype() == "image":
            out.append(p)
    return out


def _img_name(p, i):
    return p.get_filename() or f"картинка-{i + 1}{mimetypes.guess_extension(p.get_content_type()) or ''}"


def _att_name(p, i):
    if p.get_filename():
        return p.get_filename()
    ct = p.get_content_type()
    ext = {"message/rfc822": ".eml", "text/calendar": ".ics"}.get(ct) or mimetypes.guess_extension(ct) or ""
    return f"вложение-{i + 1}{ext}"


def _part_bytes(p) -> bytes:
    if p.get_content_type() == "message/rfc822":
        inner = p.get_payload(0)
        return inner.as_bytes() if inner is not None else b""
    return p.get_payload(decode=True) or b""


def eas_attachments(a: Acct, item_id: str) -> list[dict]:
    """Attachment list straight from ActiveSync (used when the MIME carries none)."""
    from outlook_activesync_mcp.commands import mail
    backend = a.get()
    with backend.lock:
        backend._pace()
        r = mail.handle(backend.client, "get", item_id=item_id, fields=["attachments"])
    out = []
    for a in (r.get("items") or [{}])[0].get("attachments") or []:
        if a.get("is_inline") or not a.get("attachment_id"):
            continue
        name = a.get("name") or "вложение"
        out.append({"ref": a["attachment_id"], "name": name, "size": a.get("size") or 0,
                    "type": mimetypes.guess_type(name)[0] or "application/octet-stream"})
    return out


def eas_attachment_bytes(a: Acct, ref: str) -> bytes | None:
    from outlook_activesync_mcp.wbxml import el, find
    backend = a.get()
    with backend.lock:
        backend._pace()
        backend.client.ensure_provisioned()
        req = el("ItemOperations", "ItemOperations",
                 el("ItemOperations", "Fetch", el("ItemOperations", "Store", text="Mailbox"),
                    el("AirSyncBase", "FileReference", text=ref)))
        tree = backend.client.command("ItemOperations", req)
    fetch = find(tree, "ItemOperations", "Fetch")
    props = find(fetch, "ItemOperations", "Properties") if fetch is not None else None
    node = find(props, "ItemOperations", "Data") if props is not None else None
    if node is None:
        return None
    return node.data if node.data is not None else base64.b64decode((node.text or "").encode("ascii"), validate=False)


_RU_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_RU_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря")


def _outlook_addrs(value) -> str:
    """«Имя <addr>; Имя2 <addr2>» — how Outlook lists people in the header block."""
    from email.utils import getaddresses
    out = []
    for name, addr in getaddresses([str(value or "")]):
        if name and addr:
            out.append(f"{name} <{addr}>")
        elif addr or name:
            out.append(addr or name)
    return "; ".join(out)


def _outlook_quote_header(a: Acct, item_id: str) -> str:
    """The block Outlook puts between your text and the quoted original:

        ________________________________
        От: Имя <addr>
        Отправлено: четверг, 24 сентября 2026 г. 9:30
        Кому: …
        Копия: …
        Тема: …

    Exchange's SmartReply/SmartForward append the original right after our
    text but without it, so the recipient could not see who wrote what, when."""
    from email.utils import parsedate_to_datetime
    try:
        msg = parse_raw(get_mime(a, item_id))
    except Exception as e:  # noqa: BLE001 — the reply still goes, just without the block
        log.info("[%s] reply header block skipped: %s", a.id, e)
        return ""
    when = ""
    try:
        d = parsedate_to_datetime(str(msg["Date"])).astimezone()
        when = f"{_RU_WEEKDAYS[d.weekday()]}, {d.day} {_RU_MONTHS[d.month - 1]} {d.year} г. {d.hour}:{d.minute:02d}"
    except (TypeError, ValueError, IndexError):
        pass
    lines = ["", "", "_" * 32, f"От: {_outlook_addrs(msg['From'])}"]
    if when:
        lines.append(f"Отправлено: {when}")
    lines.append(f"Кому: {_outlook_addrs(msg['To'])}")
    if msg["Cc"]:
        lines.append(f"Копия: {_outlook_addrs(msg['Cc'])}")
    lines += [f"Тема: {str(msg['Subject'] or '')}", ""]
    return "\n".join(lines)


def parse_raw(raw: bytes):
    """Exchange sometimes sends raw UTF-8 in headers; decode as text first so the
    names come out right instead of as surrogate escapes."""
    try:
        return email.message_from_string(raw.decode("utf-8"), policy=email.policy.default)
    except UnicodeDecodeError:
        return email.message_from_bytes(raw, policy=email.policy.default)


# -- meeting invitations inside mail (iCalendar) ---------------------------------

def _ics_parse(text: str) -> tuple[str, dict[str, tuple[str, str]]]:
    """Minimal RFC 5545 reader: METHOD and the first VEVENT's properties as
    {NAME: (params, value)}. Lines are unfolded; later duplicates are ignored."""
    method, props, in_ev = "", {}, False
    for ln in re.sub(r"\r?\n[ \t]", "", text).splitlines():
        # Name/params end at the first ':' outside quotes — Exchange writes
        # TZID="(UTC+03:00) Moscow, St. Petersburg", with a colon inside.
        m = re.match(r'((?:[^:"]|"[^"]*")*):(.*)', ln)
        if not m:
            continue
        head, val = m.group(1), m.group(2)
        name, _, params = head.partition(";")
        name = name.strip().upper()
        if name == "METHOD" and not in_ev:
            method = val.strip().upper()
        elif name == "BEGIN" and val.strip().upper() == "VEVENT":
            in_ev = True
        elif name == "END" and val.strip().upper() == "VEVENT":
            break
        elif in_ev and name not in props:
            props[name] = (params, val)
    return method, props


def _ics_unescape(v: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v or "").strip()


def _ics_time(prop: tuple[str, str] | None) -> tuple[str, str, bool]:
    """(local "YYYY-MM-DD HH:MM", UTC ISO or "", all_day). A TZID time is shown as
    written — invitations here come from the same (Moscow) time zone."""
    from datetime import datetime, timezone
    if not prop:
        return "", "", False
    params, v = prop[0].upper(), prop[1].strip()
    if "VALUE=DATE" in params and len(v) == 8:
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}", "", True
    try:
        if v.endswith("Z"):
            d = datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return d.astimezone().strftime("%Y-%m-%d %H:%M"), d.strftime("%Y-%m-%dT%H:%M:%SZ"), False
        d = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
        return d.strftime("%Y-%m-%d %H:%M"), d.astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), False
    except ValueError:
        return "", "", False


def _find_invite(msg) -> tuple[dict | None, object | None, dict]:
    """(invite for the UI, the text/calendar part, raw VEVENT props) or (None, None, {})."""
    for p in msg.walk():
        if p.get_content_type() != "text/calendar":
            continue
        # iCalendar is UTF-8 by spec; the declared charset (often a bare
        # "us-ascii" default) is only a fallback — trusting it garbled Cyrillic.
        data = p.get_payload(decode=True) or b""
        try:
            txt = data.decode("utf-8")
        except UnicodeDecodeError:
            txt = data.decode(p.get_content_charset() or "cp1251", "replace")
        method, props = _ics_parse(txt)
        if method not in ("REQUEST", "CANCEL") or "DTSTART" not in props:
            continue
        start, start_iso, all_day = _ics_time(props.get("DTSTART"))
        end, _, _ = _ics_time(props.get("DTEND"))
        org_params, org_val = props.get("ORGANIZER", ("", ""))
        cn = re.search(r"CN=(\"[^\"]*\"|[^;:]*)", org_params, re.I)
        invite = {
            "method": method.lower(), "uid": props.get("UID", ("", ""))[1].strip(),
            "subject": _ics_unescape(props.get("SUMMARY", ("", ""))[1]),
            "location": _ics_unescape(props.get("LOCATION", ("", ""))[1]),
            # The separate «Ссылка на встречу» field, as calendars write it into iCalendar.
            "online": next((_ics_unescape(props[k][1]).strip() for k in
                            ("CONFERENCE", "X-MICROSOFT-SKYPETEAMSMEETINGURL", "X-GOOGLE-CONFERENCE", "URL")
                            if k in props and props[k][1].strip()), ""),
            "start": start, "end": end, "start_iso": start_iso, "all_day": all_day,
            "organizer": {"name": (cn.group(1).strip('"') if cn else ""),
                          "address": re.sub(r"^mailto:", "", org_val.strip(), flags=re.I)},
        }
        return invite, p, props
    return None, None, {}


_RSVP_WORD = {"accept": "Принято", "tentative": "Под вопросом", "decline": "Отклонено"}
_RSVP_PARTSTAT = {"accept": "ACCEPTED", "tentative": "TENTATIVE", "decline": "DECLINED"}


def _mail_itip_reply(a: Acct, *, organizer: str, uid: str, subject: str, when: str,
                     event_lines: list[str], response: str, note: str = "") -> str:
    """Send an iTIP METHOD:REPLY — how Outlook/Gmail answer when the server cannot
    record a MeetingResponse. `event_lines` carry DTSTART/DTEND/… of the meeting."""
    from datetime import datetime, timezone
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid
    if "@" not in (organizer or "") or not uid:
        raise ValueError("не хватает организатора или идентификатора встречи для ответа письмом")
    lines = ["BEGIN:VCALENDAR", "PRODID:-//eas-mail//RU", "VERSION:2.0", "METHOD:REPLY", "BEGIN:VEVENT",
             f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}", *event_lines,
             f"ATTENDEE;PARTSTAT={_RSVP_PARTSTAT[response]};CN={a.name}:mailto:{a.email}",
             "END:VEVENT", "END:VCALENDAR"]
    word = _RSVP_WORD[response]
    m = EmailMessage()
    m["From"], m["To"] = a.email, organizer
    m["Subject"] = f"{word}: {subject or '(без темы)'}"
    m["Date"], m["Message-ID"] = formatdate(localtime=True), make_msgid(domain="eas-mail")
    m.set_content((note.strip() + "\n\n" if note.strip() else "") + f"{word}: {subject}\n{when}\n")
    m.add_alternative("\r\n".join(lines) + "\r\n", subtype="calendar", params={"method": "REPLY", "charset": "UTF-8"})
    _send_mime(a, m.as_bytes())
    return organizer


def _send_imip_reply(a: Acct, item_id: str, response: str, note: str = "") -> str:
    """Answer the invitation contained in mail `item_id` by iTIP reply. Returns organizer."""
    invite, _part, props = _find_invite(parse_raw(get_mime(a, item_id)))
    if not invite or invite["method"] != "request":
        raise ValueError("в письме нет приглашения, на которое можно ответить")
    raw = lambda n: f"{n}{';' + props[n][0] if props[n][0] else ''}:{props[n][1]}"  # noqa: E731
    return _mail_itip_reply(
        a, organizer=invite["organizer"]["address"], uid=invite["uid"], subject=invite["subject"],
        when=f"{invite['start']} – {invite['end']}", response=response, note=note,
        event_lines=[raw(n) for n in ("DTSTART", "DTEND", "SEQUENCE", "RECURRENCE-ID", "ORGANIZER", "SUMMARY") if n in props])


def _cal_item(a: Acct, *, item_id: str | None = None, uid: str | None = None) -> dict | None:
    with a.cv:
        items = _cal_overlay(a.cal, a.cal.get("items") or [])
    for e in items:
        if (item_id and e.get("item_id") == item_id) or (uid and e.get("uid") and e.get("uid") == uid):
            return e
    return None


# -- join links recovered from invitations ------------------------------------
# Some servers (Stalwart, Alfa-Seller) turn an invitation into a calendar item and
# drop its meeting link: no online_meeting, empty Место and description, while the
# letter's iCalendar still carries it (CONFERENCE / URL / X-…MEETINGURL). The link
# is looked up once per meeting UID in its invitation letter and remembered.
JOIN_RETRY_S = 12 * 3600       # a letter without a link: look again after this
JOIN_BATCH = 8                 # invitations searched per background pass
_join_memo: dict[str, dict] = {}
_join_lock = threading.Lock()
_join_busy: set[str] = set()


def _join_path(a: Acct) -> Path:
    return bridge.DATA_DIR / f"joinlinks-{a.id}.json"


def _join_map(a: Acct) -> dict:
    """{uid: {"url": str, "ts": float}} for this account (loaded once from disk)."""
    with _join_lock:
        if a.id not in _join_memo:
            try:
                _join_memo[a.id] = json.loads(_join_path(a).read_text())
            except (OSError, ValueError):
                _join_memo[a.id] = {}
        return _join_memo[a.id]


def _join_save(a: Acct):
    with _join_lock:
        data = json.dumps(_join_memo.get(a.id) or {}, ensure_ascii=False)
    try:
        p = _join_path(a)
        p.write_text(data)
        os.chmod(p, 0o600)
    except OSError as e:
        log.warning("[%s] join links not saved: %s", a.id, e)


def _invite_link(invite: dict | None) -> str:
    if not invite:
        return ""
    if invite.get("online"):
        return invite["online"]
    m = re.search(r"https?://\S+", invite.get("location") or "")
    return m.group(0) if m else ""


def remember_join(a: Acct, invite: dict | None):
    """An opened invitation teaches its meeting's link (no search needed later)."""
    url, uid = _invite_link(invite), (invite or {}).get("uid") or ""
    if url and uid and _join_map(a).get(uid, {}).get("url") != url:
        _join_map(a)[uid] = {"url": url, "ts": time.time()}
        _join_save(a)


def _has_link(e: dict) -> bool:
    return bool(e.get("online_meeting")) or bool(re.search(r"https?://", f"{e.get('location') or ''} {e.get('body') or ''}"))


def with_join_links(a: Acct, items: list[dict]) -> list[dict]:
    """Fill the link of link-less meetings from their invitations; look up unknown ones
    in the background (the next calendar answer carries what was found)."""
    memo, todo = _join_map(a), []
    out = []
    for e in items:
        uid = e.get("uid") or ""
        if not uid or _has_link(e):
            out.append(e)
            continue
        got = memo.get(uid)
        if got and got.get("url"):
            e = {**e, "online_meeting": got["url"]}
        # The organizer's own item loses the link too; their invitation is in Sent Items.
        elif (not got or time.time() - got.get("ts", 0) > JOIN_RETRY_S) and e.get("subject") \
                and e.get("meeting_status") != "cancelled":
            todo.append(e)
        out.append(e)
    if todo and a.id not in _join_busy:
        _join_busy.add(a.id)
        threading.Thread(target=_join_backfill, args=(a, todo[:JOIN_BATCH]), daemon=True).start()
    return out


def _join_backfill(a: Acct, events: list[dict]):
    try:
        for e in events:
            uid, url = e["uid"], ""
            try:
                res = call(a, "mail", {"action": "search", "query": evt_subject(e), "limit": 5})
                for m in (res.get("items") or [])[:5]:
                    invite, _, _ = _find_invite(parse_raw(get_mime(a, m["item_id"])))
                    if invite and invite.get("uid") == uid:
                        url = _invite_link(invite)
                        if url:
                            break
            except Exception as ex:  # noqa: BLE001 — offline / busy: try again later
                log.info("[%s] join link lookup failed: %s", a.id, ex)
                continue
            _join_map(a)[uid] = {"url": url, "ts": time.time()}
            if url:
                log.info("[%s] join link recovered from the invitation", a.id)
        _join_save(a)
    finally:
        _join_busy.discard(a.id)


def evt_subject(e: dict) -> str:
    """Search text for an invitation: the subject without reply prefixes."""
    return re.sub(r"^\s*((re|fwd?|fw|отв|пер)\s*:\s*)+", "", str(e.get("subject") or ""), flags=re.I).strip()[:120]


def _reply_from_calendar(a: Acct, item_id: str, response: str, note: str = "") -> str:
    """iTIP reply built from a cached calendar item (answer from the calendar view)."""
    from datetime import datetime, timezone
    from outlook_activesync_mcp.models import instance_of
    ev = _cal_item(a, item_id=item_id)
    if not ev:
        raise ValueError("встреча не найдена в календаре — обновите календарь")
    start = datetime.fromisoformat(str(ev.get("start_iso")).replace("Z", "+00:00"))
    lines = [f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}"]
    if ev.get("end"):
        end = datetime.fromisoformat(str(ev["end"]).replace(" ", "T")).astimezone(timezone.utc)
        lines.append(f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}")
    inst = instance_of(item_id)
    if inst:
        lines.append(f"RECURRENCE-ID:{inst}")  # this one occurrence of a series
    org = (ev.get("organizer") or {}).get("address") or ""
    lines.append(f"ORGANIZER:mailto:{org}")
    return _mail_itip_reply(a, organizer=org, uid=ev.get("uid") or "", subject=ev.get("subject") or "",
                            when=f"{ev.get('start', '')} – {ev.get('end', '')}", event_lines=lines, response=response,
                            note=note)


def _meeting_response(a: Acct, client, item_id: str, response: str, note: str, notify: bool = True) -> bool:
    """MeetingResponse; with a note, the text rides in SendResponse>Body (EAS 16.x) so the
    organizer gets one answer with it, like from Outlook. A server that refuses the body
    gets the plain answer instead — returns False then, and the caller mails the note."""
    from outlook_activesync_mcp.commands import calendar
    if note and notify:
        try:
            _respond_with_note(client, item_id, response, note)
            return True
        except Exception as e:  # noqa: BLE001
            if _is_unreachable(e) or _is_backoff(e):
                raise
            log.info("[%s] MeetingResponse with a note refused (%s) — plain answer + note by mail", a.id, e)
    calendar.handle(client, "respond", item_id=item_id, response=response, notify=notify)
    return not note


def _respond_with_note(client, item_id: str, response: str, note: str, propose: tuple | None = None) -> None:
    """Upstream `calendar._respond` plus the answer text (MS-ASCMD SendResponse>Body) and,
    with ``propose`` (start, end datetimes), a new time for the organizer (EAS 16.0+
    SendResponse>ProposedStartTime/EndTime — Outlook shows it as «Предложено новое время»)."""
    from outlook_activesync_mcp.commands import calendar
    from outlook_activesync_mcp.errors import EasStatusError
    from outlook_activesync_mcp.models import instance_of, unpack_item_id
    from outlook_activesync_mcp.utils import format_datetime, from_compact
    from outlook_activesync_mcp.wbxml import el, find, text_of
    collection_id, server_id = unpack_item_id(item_id)
    instance = instance_of(item_id)
    client.ensure_provisioned()
    req = el("MeetingResponse", "Request",
             el("MeetingResponse", "UserResponse", text=calendar._RESPONSE_CODES[response]),
             el("MeetingResponse", "CollectionId", text=collection_id),
             el("MeetingResponse", "RequestId", text=server_id))
    if instance:
        req.add(el("MeetingResponse", "InstanceId", text=format_datetime(from_compact(instance), millis=0)))
    send = el("MeetingResponse", "SendResponse")
    if note:
        send.add(el("AirSyncBase", "Body", el("AirSyncBase", "Type", text="1"), el("AirSyncBase", "Data", text=note)))
    if propose:
        send.add(el("MeetingResponse", "ProposedStartTime", text=format_datetime(propose[0], millis=0)))
        send.add(el("MeetingResponse", "ProposedEndTime", text=format_datetime(propose[1], millis=0)))
    req.add(send)
    tree = client.command("MeetingResponse", el("MeetingResponse", "MeetingResponse", req))
    result = find(tree, "MeetingResponse", "Result") or tree
    status = text_of(find(result, "MeetingResponse", "Status"))
    if status and status != "1":
        raise EasStatusError("MeetingResponse", int(status), f"ответ на встречу отвергнут (status {status})")


def _send_rsvp_note(a: Acct, organizer: str, subject: str, response: str, note: str) -> None:
    """The answer went without its text: send the text to the organizer as a letter."""
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid
    if "@" not in (organizer or ""):
        log.info("[%s] RSVP note not sent: no organizer address", a.id)
        return
    m = EmailMessage()
    m["From"], m["To"] = a.email, organizer
    m["Subject"] = f"{_RSVP_WORD[response]}: {subject or '(без темы)'}"
    m["Date"], m["Message-ID"] = formatdate(localtime=True), make_msgid(domain="eas-mail")
    m.set_content(note.strip() + "\n")
    _send_mime(a, m.as_bytes())


def _reply_to(client, *, item_id=None, body="", to=None, cc=None, reply_all=False, attachments=None, **_) -> dict:
    """Reply / reply-all to recipients the user edited (upstream `_reply` computes its own
    list and puts everyone in To). SmartReply, so the server still quotes the original."""
    from outlook_activesync_mcp.commands import mail_write as mw, people
    from outlook_activesync_mcp.errors import BadRequest
    from outlook_activesync_mcp.models import envelope, unpack_item_id
    from outlook_activesync_mcp.wbxml import el
    to_l, cc_l = mw._as_list(to), mw._as_list(cc)
    if not to_l:
        raise BadRequest("укажите получателя ответа")
    collection_id, server_id = unpack_item_id(item_id)
    parts = mw._load_attachments(client, attachments)
    client.ensure_provisioned()
    resolved = people.resolve_for_send(client, to_l + cc_l)
    to_a, cc_a = resolved[:len(to_l)], resolved[len(to_l):]
    subject = mw._fetch_headers(client, collection_id, server_id)["subject"] or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    mime = mw.build_message(from_addr=mw._self_address(client), to=to_a, cc=cc_a,
                            subject=subject, body=body, attachments=parts)
    client.command("SmartReply", el("ComposeMail", "SmartReply",
                                    el("ComposeMail", "ClientId", text=mw._client_id()),
                                    el("ComposeMail", "Source",
                                       el("ComposeMail", "FolderId", text=collection_id),
                                       el("ComposeMail", "ItemId", text=server_id)),
                                    el("ComposeMail", "SaveInSentItems"),
                                    el("ComposeMail", "Mime", data=mime)), allow_empty=True)
    item = {"sent": True, "to": to_a, "cc": cc_a, "subject": subject, "source_item_id": item_id,
            "reply_all": bool(reply_all)}
    if parts:
        item["attachments"] = mw._attachment_rows(parts)
    return envelope("reply", [item])


def _event_invite_payload(ev: dict, *, note: str = "") -> dict:
    """Fields `send_invites` needs, taken from a cached calendar row."""
    start = str(ev.get("start") or "").replace(" ", "T")[:16]
    end = str(ev.get("end") or start).replace(" ", "T")[:16]
    return {
        "subject": ev.get("subject") or "",
        "start": start, "end": end,
        "location": ev.get("location") or "",
        "body": ev.get("body") or "",
        "all_day": bool(ev.get("is_all_day")),
        "note": note,
    }


def _uniq_emails(addrs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in addrs:
        a = (x or "").strip()
        k = a.lower()
        if not a or "@" not in a or k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out


def forward_event(a: Acct, params: dict) -> dict:
    """Forward a meeting as an iMIP invitation to the addresses the user picks.

    Organizer: add people on the server (Exchange notifies; Seller also gets a
    METHOD:REQUEST mail). Attendee: send a new invitation from yourself with the
    same time/place — Outlook-style «переслать встречу»."""
    item_id = params.get("item_id") or ""
    to = _uniq_emails([x for x in (params.get("to") or []) if isinstance(x, str)])
    note = str(params.get("body") or "").strip()
    if not item_id:
        return {"ok": False, "action": "forward", "count": 0, "items": [], "error": "bad_request",
                "message": "не указана встреча"}
    if not to:
        return {"ok": False, "action": "forward", "count": 0, "items": [], "error": "bad_request",
                "message": "Укажите получателя"}
    if time.time() < a.send_down_until:
        return {"ok": False, "action": "forward", "count": 0, "items": [], "error": "send_down",
                "message": str(_send_down_error(a))}
    ev = _cal_item(a, item_id=item_id)
    if not ev:
        return {"ok": False, "action": "forward", "count": 0, "items": [], "error": "not_found",
                "message": "встреча не найдена в календаре — обновите календарь"}
    payload = _event_invite_payload(ev, note=note)
    if not payload["start"] or not payload["end"]:
        return {"ok": False, "action": "forward", "count": 0, "items": [], "error": "bad_request",
                "message": "у встречи нет времени — переслать нельзя"}
    me = (a.email or "").lower()
    org = ((ev.get("organizer") or {}).get("address") or "").lower()
    i_am_org = ev.get("response_type") == "organizer" or (bool(org) and org == me)
    new_to = [x for x in to if x.lower() != me]
    if not new_to:
        return {"ok": False, "action": "forward", "count": 0, "items": [], "error": "bad_request",
                "message": "Укажите получателя кроме себя"}

    if i_am_org:
        existing = [x.get("address") for x in (ev.get("attendees") or []) if x.get("address")]
        have = {x.lower() for x in existing}
        only_new = [x for x in new_to if x.lower() not in have]
        merged = _uniq_emails([*existing, *new_to])
        types = {((x.get("address") or "").lower()): (x.get("type") or "required")
                 for x in (ev.get("attendees") or []) if x.get("address")}
        for x in only_new:
            types.setdefault(x.lower(), "required")
        upd = call(a, "events", {"action": "update", "item_id": item_id, "attendees": merged,
                                 "attendee_types": types})
        if not upd.get("ok", True):
            return {**upd, "action": "forward"}
        # Seller does not mail invites on update; Bank/Exchange does.
        sent = send_invites(a, payload, only_new) if a.green and only_new else []
        return {"ok": True, "action": "forward", "count": len(only_new) or len(new_to),
                "via": "update" + ("+mail" if sent else ""),
                "items": [{"item_id": item_id, "to": only_new or new_to, "sent": sent}]}

    try:
        sent = send_invites(a, payload, new_to)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": "forward", "count": 0, "items": [],
                "error": type(e).__name__, "message": _friendly_error(a, "mail", "send", e)}
    return {"ok": True, "action": "forward", "count": len(sent), "via": "mail",
            "items": [{"item_id": item_id, "to": sent, "sent": sent}]}


def _trash_invite_mail(a: Acct, item_id: str) -> bool:
    """After RSVP, move the invitation letter to Deleted (Outlook does the same)."""
    if not item_id:
        return False
    try:
        r = call(a, "mail", {"action": "delete", "item_ids": [item_id]})
        ok = r.get("ok", True) and not r.get("error")
        if not ok:
            log.info("[%s] invite %s not moved to Deleted: %s", a.id, item_id[:24], r.get("message") or r.get("error"))
        return bool(ok)
    except Exception as e:  # noqa: BLE001 — answer already sent; trash is best-effort
        log.info("[%s] invite %s not moved to Deleted: %s", a.id, item_id[:24], e)
        return False


def _proposal(params: dict) -> tuple | None:
    """`propose_start`/`propose_end` (local «YYYY-MM-DDTHH:MM») as aware datetimes."""
    from datetime import datetime
    s, e = params.get("propose_start"), params.get("propose_end")
    if not s or not e:
        return None
    try:
        s, e = (datetime.fromisoformat(str(v)[:16]).astimezone() for v in (s, e))
    except ValueError:
        return None
    return (s, e) if e > s else None


def respond_event(a: Acct, params: dict) -> dict:
    """RSVP from the calendar (web/tray). Exchange's MeetingResponse first; if the
    server refuses it (status 2/3 were seen live), answer the organizer by mail so
    the answer still goes through instead of an error."""
    item_id, response = params.get("item_id") or "", str(params.get("response") or "").lower()
    if response not in _RSVP_WORD:
        return {"ok": False, "action": "respond", "count": 0, "items": [], "error": "bad_request",
                "message": "ответ: accept, tentative или decline"}
    note = str(params.get("note") or "").strip()
    backend = a.get()
    propose = _proposal(params)
    if propose and response == "accept":
        return {"ok": False, "action": "respond", "count": 0, "items": [], "error": "bad_request",
                "message": "другое время предлагают с ответом «Под вопросом» или «Отклонить»"}
    if propose and not a.green:  # Seller speaks ActiveSync 14.1: no proposals there
        try:
            with backend.lock:
                backend._pace()
                _respond_with_note(backend.client, item_id, response, note, propose)
            return {"ok": True, "action": "respond", "count": 1,
                    "items": [{"responded": response, "item_id": item_id, "note": bool(note), "proposed": True}]}
        except Exception as e:  # noqa: BLE001
            if _is_unreachable(e) or _is_backoff(e):
                return {"ok": False, "action": "respond", "count": 0, "items": [],
                        "error": getattr(e, "code", None) or type(e).__name__,
                        "message": _friendly_error(a, "events", "respond", e)}
            log.info("[%s] proposal refused (%s) — the new time goes as text", a.id, e)
    if propose:  # the organizer still learns the time, as a line of the answer's comment
        when = f"{propose[0].astimezone():%d.%m %H:%M}–{propose[1].astimezone():%H:%M}"
        note = (note + "\n\n" if note else "") + f"Предлагаю другое время: {when}"
    try:
        with backend.lock:
            backend._pace()
            noted = _meeting_response(a, backend.client, item_id, response, note,
                                      notify=params.get("notify", True))
        if note and not noted:
            ev = _cal_item(a, item_id=item_id) or {}
            _send_rsvp_note(a, (ev.get("organizer") or {}).get("address") or "", ev.get("subject") or "", response, note)
        if item_id in _rsvp_answers(a):  # the server has the answer now: an older mail one must not mask it
            _rsvp_remember(a, item_id, response)
        return {"ok": True, "action": "respond", "count": 1,
                "items": [{"responded": response, "item_id": item_id, "note": bool(note)}]}
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] MeetingResponse failed (%s) — answering by mail", a.id, e)
        first = e
    try:
        org = _reply_from_calendar(a, item_id, response, note)
        _rsvp_remember(a, item_id, response)
    except Exception as e2:  # noqa: BLE001
        log.warning("[%s] mail reply fallback failed: %s", a.id, e2)
        return {"ok": False, "action": "respond", "count": 0, "items": [],
                "error": getattr(first, "code", None) or type(first).__name__,
                "message": _friendly_error(a, "events", "respond", first)}
    return {"ok": True, "action": "respond", "count": 1, "via": "mail",
            "items": [{"responded": response, "item_id": item_id, "organizer": org}]}


def invite_respond(a: Acct, item_id: str, response: str, note: str = "") -> dict:
    """Accept / tentative / decline an invitation right from the mail. Exchange's
    MeetingResponse (updates your calendar and notifies the organizer) first; if the
    server can't do it, fall back to a standard iTIP reply by mail. On success the
    invitation letter is moved to Deleted, like in Outlook."""
    response = str(response or "").lower()
    if response not in ("accept", "tentative", "decline"):
        return {"ok": False, "error": "bad_request", "message": "ответ: accept, tentative или decline"}
    backend = a.get()

    note = str(note or "").strip()

    def meeting_response(target: str) -> bool:
        try:
            with backend.lock:
                backend._pace()
                noted = _meeting_response(a, backend.client, target, response, note)
            if note and not noted:
                inv = None
                try:
                    inv, _p, _props = _find_invite(parse_raw(get_mime(a, item_id)))
                except Exception:  # noqa: BLE001
                    pass
                _send_rsvp_note(a, ((inv or {}).get("organizer") or {}).get("address") or "",
                                (inv or {}).get("subject") or "", response, note)
            return True
        except Exception as e:  # noqa: BLE001
            log.info("[%s] MeetingResponse on %s failed: %s", a.id, target[:12], e)
            return False

    def done(via: str, **extra) -> dict:
        cal_refresh_bg(a)
        trashed = _trash_invite_mail(a, item_id)
        return {"ok": True, "via": via, "trashed": trashed, **extra}

    if meeting_response(item_id):  # the invitation letter itself
        return done("server")
    # Same meeting in the calendar (matched by iCalendar UID) — Exchange accepts
    # answers on the calendar item even when it refuses them on the letter.
    try:
        invite, _p, _props = _find_invite(parse_raw(get_mime(a, item_id)))
    except Exception:  # noqa: BLE001
        invite = None
    ev = _cal_item(a, uid=invite["uid"]) if invite and invite.get("uid") else None
    if ev and meeting_response(ev["item_id"]):
        return done("server")
    res = {"message": "сервер не принял ответ"}
    try:
        org = _send_imip_reply(a, item_id, response, note)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "respond_failed", "message": res.get("message") or str(e)}
    if ev:
        _rsvp_remember(a, ev["item_id"], response)
    return done("mail", organizer=org)


def render_message(a: Acct, item_id: str, has_att: bool = False) -> dict:
    raw = get_mime(a, item_id)
    msg = parse_raw(raw)
    body = msg.get_body(preferencelist=("html", "plain"))
    html_doc, text = None, ""
    if body is not None:
        try:
            content = body.get_content()
        except Exception:  # noqa: BLE001 — odd charsets
            content = (body.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if body.get_content_subtype() == "html":
            html_doc = content
        else:
            text = content
    parts = _attachment_parts(msg, html_doc)
    invite, invite_part, _ = _find_invite(msg)
    remember_join(a, invite)
    # The .ics is shown as the invitation card — don't list it as a file too.
    atts = [{"idx": i, "name": _att_name(p, i), "type": p.get_content_type(), "size": len(_part_bytes(p))}
            for i, p in enumerate(parts) if p is not invite_part]
    if not atts and has_att:
        try:
            atts = eas_attachments(a, item_id)
        except Exception:  # noqa: BLE001 — the message itself still opens
            log.warning("attachment list from server failed", exc_info=True)
    images = [{"img": i, "name": _img_name(p, i), "type": p.get_content_type(), "size": len(_part_bytes(p))}
              for i, p in enumerate(_inline_images(msg, html_doc))]
    if html_doc and "cid:" in html_doc:
        for p in msg.walk():
            cid = (p.get("Content-ID") or "").strip("<> ")
            if cid and p.get_content_maintype() == "image" and f"cid:{cid}" in html_doc:
                data = base64.b64encode(p.get_payload(decode=True) or b"").decode()
                html_doc = html_doc.replace(f"cid:{cid}", f"data:{p.get_content_type()};base64,{data}")
    return {"subject": str(msg.get("Subject", "")), "date": str(msg.get("Date", "")),
            "from": _addr_list(msg, "From"), "to": _addr_list(msg, "To"), "cc": _addr_list(msg, "Cc"),
            "html": html_doc, "text": text, "attachments": atts, "images": images, "invite": invite,
            "message_id": str(msg.get("Message-ID", ""))}


def attachment_bytes(a: Acct, item_id: str, idx: int):
    msg = parse_raw(get_mime(a, item_id))
    parts = _attachment_parts(msg, _body_html(msg))
    if not 0 <= idx < len(parts):
        return None
    p = parts[idx]
    return _att_name(p, idx), p.get_content_type(), _part_bytes(p)


def inline_image_bytes(a: Acct, item_id: str, idx: int):
    msg = parse_raw(get_mime(a, item_id))
    imgs = _inline_images(msg, _body_html(msg))
    if not 0 <= idx < len(imgs):
        return None
    p = imgs[idx]
    return _img_name(p, idx), p.get_content_type(), _part_bytes(p)


# -- HTTP -------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "eas-mail"

    def log_message(self, fmt, *args):  # quiet; never log URLs with tokens
        log.debug("%s", fmt % args)

    def _guard(self) -> bool:
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            self.send_error(403, "bad host")
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc not in ALLOWED_HOSTS:
            self.send_error(403, "bad origin")
            return False
        return True

    def _send(self, code, body: bytes, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=True, default=str).encode())

    def do_GET(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            if PAGE_KEY and not secrets.compare_digest(self.headers.get("X-Page-Key", ""), PAGE_KEY):
                return self.send_error(403)
            page = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            csp = (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com data:; "
                "img-src 'self' data: https:; frame-src 'self'; connect-src 'self'; "
                "frame-ancestors 'none'"
            )
            return self._send(200, page.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
        # Design system + kit showcase under web/ui-kit/
        if u.path.startswith("/ui-kit/"):
            target = (WEB / u.path.lstrip("/")).resolve()
            root = WEB.resolve()
            if not str(target).startswith(str(root) + os.sep) and target != root:
                return self.send_error(403)
            if not target.is_file():
                return self.send_error(404)
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if target.suffix == ".css":
                ctype = "text/css; charset=utf-8"
            elif target.suffix == ".html":
                ctype = "text/html; charset=utf-8"
            elif target.suffix == ".woff2":
                ctype = "font/woff2"  # the bundled Golos Text (mimetypes may not know it)
            return self._send(200, target.read_bytes(), ctype)
        if u.path == "/attachment":
            if q.get("t", [""])[0] != TOKEN:
                return self.send_error(403)
            try:
                a = acct_of(q.get("a", ["main"])[0])
                if "ref" in q:
                    data = eas_attachment_bytes(a, q["ref"][0])
                    name = q.get("n", ["attachment"])[0]
                    got = (name, mimetypes.guess_type(name)[0] or "application/octet-stream", data) if data is not None else None
                elif "ii" in q:
                    got = inline_image_bytes(a, q["id"][0], int(q["ii"][0]))
                else:
                    got = attachment_bytes(a, q["id"][0], int(q["i"][0]))
            except Exception:  # noqa: BLE001
                log.warning("attachment download failed", exc_info=True)
                got = None
            if not got:
                return self.send_error(404)
            name, ctype, data = got
            return self._send(200, data, "application/octet-stream" if ctype == "text/html" else ctype,
                              {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
        self.send_error(404)

    def do_POST(self):
        if not self._guard():
            return
        if self.headers.get("X-Tok", "") != TOKEN:
            return self.send_error(403, "bad token")
        _last_activity[0] = time.time()
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self.send_error(413)
        try:
            params = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            params = None
        if not isinstance(params, dict):
            return self._json({"ok": False, "error": "bad_request", "message": "invalid JSON"}, 400)
        path = urlparse(self.path).path
        aid = params.pop("acct", "main")
        try:
            if path == "/api/prefs":
                return self._json({"ok": True, "prefs": update_prefs(params.get("set") or {}) if params.get("set") is not None else load_prefs()})
            if path == "/api/about":
                cfg = ensure_config()
                return self._json({"ok": True, **APP_META,
                                   "needs_setup": account_needs_setup(cfg),
                                   "defaults": dict(DEFAULT_EAS_URLS)})
            if path == "/api/update":
                if params.get("action") == "install":
                    return self._json(update_install())
                return self._json(update_check(force=bool(params.get("force"))))
            if path == "/api/accounts":
                return self._json({"ok": True, "accounts": [
                    {"id": x.id, "name": x.name, "email": x.email, "green": x.green,
                     "unread": sum(x.unread.values()),
                     # Named per account, so the UI can say WHICH one refused (AAS-24-01).
                     "auth_error": _auth_message(x) if _auth_refused(x) else None}
                    for x in ACCTS.values()]})
            if path == "/api/account-config":
                if params.get("action") == "get":
                    return self._json({"ok": True, **get_account_config()})
                if params.get("action") == "save":
                    return self._json(save_account_config(params))
                return self._json({"ok": False, "error": "bad_request", "message": "unknown action"}, 400)
            a = acct_of(aid)
            if path == "/api/unread":
                if params.get("sweep"):
                    return self._json(unread_sweep(a, params.get("filter"), params.get("fields"),
                                                   force=bool(params.get("force"))))
                if params.get("tick"):  # one line per auto-sync tick: shows it keeps its interval
                    log.info("[%s] auto-sync tick (%s window)", a.id, params["tick"])
                return self._json(refresh_unread(a, force=bool(params.get("refresh"))))
            if path == "/api/message":
                # No account lock here: fetch_mime / eas_attachments take it only
                # for their round-trips, so parsing a big MIME does not stall
                # mail lists and calendar syncs of the same account.
                try:
                    return self._json({"ok": True, **render_message(a, params["item_id"], bool(params.get("has_att")))})
                except Gone as e:
                    return self._json({"ok": False, "error": "gone", "message": str(e)})
            if path == "/api/warm":
                return self._json(warm_folders(a, params.get("folders") or [], params.get("filter"), params.get("fields")))
            if path == "/api/invite":
                return self._json(invite_respond(a, params.get("item_id") or "", params.get("response"),
                                                 note=params.get("note") or ""))
            if path == "/api/retry":
                r = retry_login(a)
                return self._json({"ok": True, "result": r.get("items")})
            if path == "/api/sync":
                if params.get("calendar") == "delta":
                    # Tab switch / app launch: catch up with the server's changes
                    # (moved or cancelled meetings) — a delta, well under a second.
                    cal_refresh_wait(a)
                elif params.get("calendar"):
                    # The explicit «Синхронизировать» button is the user's escape hatch:
                    # a full resync, not another delta on top of a possibly drifted cache.
                    cal_refresh_wait(a, force=True)
                return self._json({"ok": True, "calendar_error": a.cal["error"]})
            if path == "/api/events" and params.get("action") == "reinvite":
                return self._json(reinvite_moved(a, params.get("item_id") or "", params.get("start") or "",
                                                 params.get("end") or ""))
            if path == "/api/events" and params.get("action") == "list":
                return self._json(cal_events(a, params["start"], params["end"]))
            if path.startswith("/api/"):
                invite = params.pop("mime_invite", False) if path == "/api/events" else False
                gone = (cancel_notice_target(a, params.get("item_id") or "")
                        if path == "/api/events" and params.get("action") == "cancel" else None)
                res = call(a, path[5:], dict(params))
                if gone and res.get("ok", True):
                    threading.Thread(target=_send_cancel_bg, args=(a, gone, params["item_id"]), daemon=True).start()
                if invite and res.get("ok", True) and params.get("action") == "create" and params.get("attendees"):
                    res["mime_invite_pending"] = True
                    threading.Thread(target=_send_invites_bg, args=(a, dict(params), list(params["attendees"])),
                                     daemon=True).start()
                if path == "/api/events" and res.get("ok", True):
                    _cal_after_write(a, params, res)  # show our own write right away
                return self._json(res)
        except LookupError as e:
            return self._json({"ok": False, "error": "unknown_account", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("request failed")
            return self._json({"ok": False, "error": type(e).__name__, "message": str(e)}, 500)
        self.send_error(404)


def _write_runtime_token():
    """Publish this run's TOKEN for local native clients (the tray app).

    Called from main() only: at module scope, merely *importing* webapp.py
    (tests, `python3 -c "import webapp"`) would overwrite a running server's
    token file with a fresh, non-matching value and silently break its
    clients' auth. The file is created with 0600 in one step (os.open),
    so it never exists at the default umask even for an instant.
    """
    path = bridge.DATA_DIR / "runtime_token"
    bridge.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):  # Windows (Python 3.12): none, and %APPDATA% is per-user anyway
            os.fchmod(fd, 0o600)  # an already-existing file keeps its old mode otherwise
        with os.fdopen(fd, "w") as f:
            fd = -1
            f.write(TOKEN)
    finally:
        if fd >= 0:
            os.close(fd)


def _patch_calendar_attendees():
    """Upstream parse_event never fills `attendees` even though `_attendees()`
    exists — without this, RSVP cards and participant lists stay empty."""
    try:
        from outlook_activesync_mcp.model import mapping
    except ImportError:
        return
    if getattr(mapping.parse_event, "_eas_attendees_patched", False):
        return
    orig = mapping.parse_event

    def wrapped(node):
        ev = orig(node)
        if not ev.get("attendees"):
            ev["attendees"] = mapping._attendees(node)
        # is_recurring used by the web UI badge
        if "is_recurring" not in ev:
            ev["is_recurring"] = bool(ev.get("recurrence"))
        return ev

    wrapped._eas_attendees_patched = True  # type: ignore[attr-defined]
    mapping.parse_event = wrapped


# Roles for the attendees of the one events create/update running on this thread:
# {address (lower case): "optional" | "resource"}. Set by call() under the account
# lock, read by the patched _attendees_block (thread-local: two accounts may write at once).
_ATT = threading.local()
_ATT_TYPE_CODE = {"optional": "2", "resource": "3"}


def _patch_attendee_types():
    """Upstream writes every attendee as Required (AttendeeType 1): honour the
    roles the editor sends in ``attendee_types`` (optional → 2, room → 3), so
    Outlook shows «необязательный» and the room books itself."""
    try:
        from outlook_activesync_mcp.commands import calendar
        from outlook_activesync_mcp.wbxml import el
    except ImportError:
        return
    if getattr(calendar._attendees_block, "_eas_types_patched", False):
        return

    def block(attendees):
        roles = getattr(_ATT, "types", None) or {}
        people = []
        for a in attendees:
            email = a if isinstance(a, str) else (a.get("address") or a.get("email"))
            name = email if isinstance(a, str) else (a.get("name") or email)
            people.append(el("Calendar", "Attendee",
                             el("Calendar", "AttendeeEmail", text=email),
                             el("Calendar", "AttendeeName", text=name),
                             el("Calendar", "AttendeeType",
                                text=_ATT_TYPE_CODE.get(roles.get(str(email).lower(), ""), "1"))))
        return el("Calendar", "Attendees", *people)

    orig_resolve = calendar._resolve_attendees

    def resolve(client, attendees):
        # A typed name («Петров») becomes an address here: carry its role over.
        out = orig_resolve(client, attendees)
        roles = getattr(_ATT, "types", None)
        if roles:
            asked = attendees if isinstance(attendees, list) else [attendees]
            for q, got in zip(asked, out):
                role = roles.get(str(q).lower())
                if role:
                    roles.setdefault(str(got).lower(), role)
        return out

    block._eas_types_patched = True  # type: ignore[attr-defined]
    calendar._attendees_block = block
    calendar._resolve_attendees = resolve


def _cal_extra_els(client) -> list:
    """Response options and new attachments of the events create/update running on
    this thread (see call()), as ApplicationData children. Attachments: EAS 16.x
    AirSyncBase:Attachments>Add — Exchange stores the file on the item and sends it
    to the attendees with the invitation."""
    from outlook_activesync_mcp.model.attachments import load_attachments
    from outlook_activesync_mcp.wbxml import el
    x = getattr(_ATT, "extra", None) or {}
    out = []
    if "disallow_counter" in x:
        out.append(el("Calendar", "DisallowNewTimeProposal", text="1" if x["disallow_counter"] else "0"))
    if "response_requested" in x:
        out.append(el("Calendar", "ResponseRequested", text="1" if x["response_requested"] else "0"))
    parts = load_attachments(x.get("attachments"), max_total_bytes=client.s.max_attachment_bytes)
    if parts:
        import uuid
        out.append(el("AirSyncBase", "Attachments", *[
            el("AirSyncBase", "Add",
               el("AirSyncBase", "ClientId", text=str(uuid.uuid4())),
               el("AirSyncBase", "Content", data=p["data"]),
               el("AirSyncBase", "Method", text="1"),
               el("AirSyncBase", "DisplayName", text=p["name"]),
               el("AirSyncBase", "ContentType", text=f"{p['maintype']}/{p['subtype']}"))
            for p in parts]))
    return out


def _patch_event_create_extras():
    """Upstream create knows nothing of response options or attachments: append
    them to the ApplicationData it builds."""
    try:
        from outlook_activesync_mcp.commands import calendar
    except ImportError:
        return
    if getattr(calendar._create, "_eas_extras_patched", False):
        return
    orig_create, orig_appdata = calendar._create, calendar._create_appdata
    state = threading.local()

    def appdata(**kw):
        ad, s, e = orig_appdata(**kw)
        for x in getattr(state, "els", None) or []:
            ad.add(x)
        return ad, s, e

    def create(client, **kw):
        state.els = _cal_extra_els(client)  # files read before anything reaches the network
        try:
            return orig_create(client, **kw)
        finally:
            state.els = None

    create._eas_extras_patched = True  # type: ignore[attr-defined]
    calendar._create, calendar._create_appdata = create, appdata


def _patch_event_update_attendees():
    """Upstream events/update ignores ``attendees``. Extend Sync Change so the
    organizer can add/remove people and Exchange re-sends the invite."""
    try:
        from outlook_activesync_mcp.commands import calendar
        from outlook_activesync_mcp.errors import BadRequest
        from outlook_activesync_mcp.models import envelope, unpack_item_id
        from outlook_activesync_mcp.wbxml import el
        from outlook_activesync_mcp.utils import parse_datetime, to_compact
    except ImportError:
        return
    if getattr(calendar._update, "_eas_attendees_patched", False):
        return
    orig = calendar._update

    def wrapped(client, *, item_id=None, attendees=None, subject=None, start=None, end=None,
                location=None, body=None, busy_status=None, all_day=None, reminder=None,
                sensitivity=None, **_):
        from outlook_activesync_mcp.models import instance_of, pack_item_id
        if instance_of(item_id):
            # One occurrence: upstream dropped the instance and wrote the occurrence's time
            # into the whole series. Exchange (EAS 16) refuses every way to move just one
            # (InstanceId beside ServerId → 4, in ApplicationData → 6, Exceptions → 6), so
            # times are refused here and the rest (subject, place, roster) goes to the series.
            if start is not None or end is not None or all_day is not None:
                raise BadRequest("время одной встречи серии сервер через ActiveSync не меняет — "
                                 "перенесите её в Outlook или OWA")
            item_id = pack_item_id(*unpack_item_id(item_id))
        extras = _cal_extra_els(client)
        if attendees is None and not extras:
            return orig(client, item_id=item_id, subject=subject, start=start, end=end,
                        location=location, body=body, busy_status=busy_status,
                        all_day=all_day, reminder=reminder, sensitivity=sensitivity)
        collection_id, server_id = unpack_item_id(item_id)
        fields = []
        if all_day is not None:
            fields.append(el("Calendar", "AllDayEvent", text="1" if all_day else "0"))
        if busy_status is not None:
            fields.append(el("Calendar", "BusyStatus", text=str(busy_status)))
        if start is not None:
            fields.append(el("Calendar", "StartTime", text=to_compact(parse_datetime(start))))
        if end is not None:
            fields.append(el("Calendar", "EndTime", text=to_compact(parse_datetime(end))))
        if location is not None:
            fields.append(calendar._location_el(location))
        if subject is not None:
            fields.append(el("Calendar", "Subject", text=subject))
        if sensitivity is not None:
            fields.append(el("Calendar", "Sensitivity", text=str(sensitivity)))
        if reminder is not None:
            fields.append(el("Calendar", "Reminder", text=str(reminder)))
        if body is not None:
            fields.append(el("AirSyncBase", "Body",
                             el("AirSyncBase", "Type", text="1"),
                             el("AirSyncBase", "Data", text=body)))
        resolved = calendar._resolve_attendees(client, attendees) if attendees else []
        if resolved:
            fields.append(calendar._attendees_block(resolved))
            fields.append(el("Calendar", "MeetingStatus", text="1"))
        elif attendees is not None:
            # Empty roster → keep as appointment (no attendees block).
            fields.append(el("Calendar", "MeetingStatus", text="0"))
        fields += extras
        if not fields:
            raise BadRequest("update: не переданы поля для изменения")
        change = el("AirSync", "Change", el("AirSync", "ServerId", text=server_id),
                    el("AirSync", "ApplicationData", *fields))
        tree, _m, _g = client.sync_round(collection_id, command_children=[change], get_changes=False)
        calendar._raise_if_write_failed(tree, "Change", server_id, "обновление события")
        result = {"updated": True, "item_id": item_id}
        if resolved:
            result["attendees"] = resolved
        return envelope("update", [result])

    wrapped._eas_attendees_patched = True  # type: ignore[attr-defined]
    calendar._update = wrapped


def _patch_sync_status_135():
    """EAS 135 SyncStateAlreadyExists (MS-ASCMD): SyncKey=0 while the server
    already has state — typically a race of concurrent primes (mail + calendar
    on the same DeviceId).

    Serialize ``_prime`` per collection and share a *fresh* key via a short-lived
    in-process cache for concurrent peers. Do **not** keep that cache forever:
    Stalwart/Exchange invalidate keys, and a stale cache entry made every
    subsequent list fail with Sync status 3.

    Also: never fall back to the on-disk SyncKey after 135 — that key is exactly
    what the caller asked to replace with SyncKey=0.
    """
    try:
        from outlook_activesync_mcp.client import EasClient
        from outlook_activesync_mcp.errors import EasStatusError
        from outlook_activesync_mcp.commands import provision as prov
    except ImportError:
        return
    locks: dict[tuple[int, str], threading.Lock] = {}
    # (client_id, collection_id) → (sync_key, expires_monotonic)
    cache: dict[tuple[int, str], tuple[str, float]] = {}
    locks_guard = threading.Lock()
    CACHE_TTL = 8.0  # only covers the concurrent-prime race window

    def _lock_for(client, collection_id: str) -> tuple[threading.Lock, tuple[int, str]]:
        key = (id(client), collection_id)
        with locks_guard:
            lock = locks.get(key)
            if lock is None:
                lock = threading.Lock()
                locks[key] = lock
            return lock, key

    def _cache_get(ckey):
        hit = cache.get(ckey)
        if not hit:
            return None
        sk, exp = hit
        if exp < time.monotonic():
            cache.pop(ckey, None)
            return None
        return sk

    def _cache_put(ckey, sk):
        cache[ckey] = (sk, time.monotonic() + CACHE_TTL)

    def _cache_drop(ckey):
        cache.pop(ckey, None)

    def _drop_stored(client, collection_id: str) -> None:
        try:
            with client.store.transaction() as st:
                cols = st.setdefault("collections", {})
                cols.pop(collection_id, None)
        except Exception:
            pass

    orig_prime = getattr(EasClient._prime, "_eas_135_orig", None) or EasClient._prime

    def _prime_safe(self, collection_id: str) -> str:
        lock, ckey = _lock_for(self, collection_id)
        with lock:
            existing = _cache_get(ckey)
            if existing:
                return existing
            last_err = None
            for attempt in range(4):
                try:
                    sk = orig_prime(self, collection_id)
                    _cache_put(ckey, sk)
                    return sk
                except EasStatusError as e:
                    last_err = e
                    if getattr(e, "status", None) != 135:
                        raise
                    time.sleep(0.35 * (attempt + 1))
                    peer = _cache_get(ckey)
                    if peer:
                        log.info("Sync 135 on %s: reusing in-flight peer SyncKey", collection_id)
                        return peer
            # Do NOT reuse the on-disk SyncKey — it is what SyncKey=0 was meant
            # to replace, and feeding it back causes a status-3 loop on Stalwart.
            _drop_stored(self, collection_id)
            _cache_drop(ckey)
            raise EasStatusError(
                "Sync", 135,
                f"SyncStateAlreadyExists на {collection_id} — нет свежего SyncKey после ретраев"
            ) from last_err

    _prime_safe._eas_135_safe = True  # type: ignore[attr-defined]
    _prime_safe._eas_135_orig = orig_prime  # type: ignore[attr-defined]
    EasClient._prime = _prime_safe

    # On Sync status 3/132/134 that escapes sync_round's one retry (stale peer
    # cache): drop cache + stored key and force one more fresh listing.
    orig_round = getattr(EasClient.sync_round, "_eas_3_orig", None) or EasClient.sync_round

    def sync_round_safe(self, collection_id: str, *, generation=None, window=None,
                        options_children=None, command_children=None, get_changes=True):
        try:
            return orig_round(self, collection_id, generation=generation, window=window,
                              options_children=options_children,
                              command_children=command_children, get_changes=get_changes)
        except EasStatusError as e:
            if getattr(e, "status", None) not in prov.NEEDS_RESYNC:
                raise
            # Writes (command_children) must not be silently reissued as a list.
            if command_children is not None:
                raise
            log.info("Sync status %s on %s — clearing SyncKey cache and re-priming",
                     e.status, collection_id)
            _cache_drop((id(self), collection_id))
            _drop_stored(self, collection_id)
            return orig_round(self, collection_id, generation=None, window=window,
                              options_children=options_children,
                              command_children=None, get_changes=get_changes)

    sync_round_safe._eas_3_orig = orig_round  # type: ignore[attr-defined]
    EasClient.sync_round = sync_round_safe


MOVE_STATUS_BASE = 1000
_MOVE_FAILURE = {
    1: "письмо уже перемещено или удалено в другом месте — список обновится",
    2: "папка назначения не найдена — обновите список папок",
    4: "письмо уже лежит в этой папке",
    5: "сервер не смог переместить письмо, повторите через минуту",
    7: "письмо сейчас заблокировано сервером, повторите через минуту",
}


def _patch_moveitems_status():
    """AAS-24-03. In MoveItems, Status 3 means *success* (MS-ASCMD: 1 bad source,
    2 bad destination, 3 success, 4 same folder, 5+ failures). Upstream read it with
    Sync semantics (3 = stale SyncKey → «recovery resync») and re-sent the move; the
    message was already in «Удалённые», so the retry failed and the user saw an error
    for a delete that had worked — and «succeeded» on the second click."""
    try:
        from outlook_activesync_mcp.client import EasClient
        from outlook_activesync_mcp.wbxml import find_all, text_of
    except ImportError:
        return
    if getattr(EasClient._status_of, "_eas_move_ok", False):
        return
    orig = EasClient._status_of

    def _status_of(self, cmd, tree):
        if cmd == "MoveItems" and tree is not None:
            codes = [text_of(n) for n in find_all(tree, "Move", "Status")]
            bad = [c for c in codes if c and c != "3"]
            if not bad:
                return "1"  # every move succeeded → plain success for the client
            # A MoveItems failure code must not be read as a generic one ("1" would
            # mean success to the client) — shift it into its own range.
            return str(MOVE_STATUS_BASE + int(bad[0]))
        return orig(self, cmd, tree)

    _status_of._eas_move_ok = True  # type: ignore[attr-defined]
    EasClient._status_of = _status_of


def _patch_foldersync_invalid_key():
    """FolderSync status 9 = invalid SyncKey (MS-ASCMD). Upstream treats it as
    «transient» and resends the *same* body — if that body had a non-zero SyncKey
    (our deep folders_list second round), three identical retries exhaust and the
    UI shows «восстановление исчерпано». Retry once with SyncKey=0 instead."""
    try:
        from outlook_activesync_mcp.client import EasClient
        from outlook_activesync_mcp.commands import provision as prov
        from outlook_activesync_mcp.errors import EasStatusError
        from outlook_activesync_mcp.wbxml import find, text_of
    except ImportError:
        return
    if getattr(EasClient._run, "_eas_fs9_safe", False):
        return
    orig_run = EasClient._run

    def _run(self, cmd, node, *, policy_key, allow_empty, applied, attempts):
        try:
            return orig_run(self, cmd, node, policy_key=policy_key, allow_empty=allow_empty,
                            applied=applied, attempts=attempts)
        except EasStatusError as e:
            msg = str(e)
            is_fs9 = (cmd == "FolderSync" and (
                getattr(e, "status", None) == 9
                or "восстановление исчерпано" in msg))
            if not is_fs9:
                raise
            req_key = text_of(find(node, "FolderHierarchy", "SyncKey")) or "0"
            if req_key == "0" or "foldersync:0" in applied or getattr(_fs_delta, "strict", False):
                raise
            log.info("FolderSync fail with SyncKey=%s — retry with SyncKey=0", req_key)
            return orig_run(self, cmd, prov.build_foldersync("0"),
                            policy_key=policy_key, allow_empty=allow_empty,
                            applied=applied | {"foldersync:0"}, attempts=0)

    _run._eas_fs9_safe = True  # type: ignore[attr-defined]
    EasClient._run = _run


def _patch_empty_sync_tree():
    """Exchange sometimes answers Sync with HTTP 200 and an empty body.
    Upstream ``command()`` returns ``None``; ``sync_round`` then calls
    ``find(None, ...)`` → ``'NoneType' object has no attribute 'children'``.
    Treat empty Sync as «no changes» instead of crashing mail/list."""
    try:
        from outlook_activesync_mcp.client import EasClient
        from outlook_activesync_mcp.wbxml import el
    except ImportError:
        return
    if getattr(EasClient.command, "_eas_empty_safe", False):
        return

    orig_cmd = EasClient.command

    def command(self, cmd: str, node, *, policy_key=None, allow_empty=False):
        tree = orig_cmd(self, cmd, node, policy_key=policy_key, allow_empty=allow_empty)
        if tree is None and cmd == "Sync":
            return el("AirSync", "Sync")
        return tree

    command._eas_empty_safe = True  # type: ignore[attr-defined]
    EasClient.command = command

    # Harden find/find_all for any other None trees (FolderSync, etc.).
    try:
        from outlook_activesync_mcp.wbxml import node as nmod
        import outlook_activesync_mcp.wbxml as wbxml_mod
        if getattr(nmod.find, "_eas_none_safe", False):
            return
        _find, _find_all, _iter = nmod.find, nmod.find_all, nmod.iter_descendants

        def find(node, ns, tag=None):
            if node is None:
                return None
            return _find(node, ns, tag)

        def find_all(node, ns, tag=None):
            if node is None:
                return []
            return _find_all(node, ns, tag)

        def iter_descendants(node):
            if node is None:
                return iter(())
            return _iter(node)

        find._eas_none_safe = True  # type: ignore[attr-defined]
        nmod.find = find
        nmod.find_all = find_all
        nmod.iter_descendants = iter_descendants
        wbxml_mod.find = find
        wbxml_mod.find_all = find_all
        for modname in (
            "outlook_activesync_mcp.client",
            "outlook_activesync_mcp.commands.mail",
            "outlook_activesync_mcp.commands.calendar",
            "outlook_activesync_mcp.commands.provision",
            "outlook_activesync_mcp.commands.sync",
            "outlook_activesync_mcp.commands.people",
            "outlook_activesync_mcp.commands.folders",
            "outlook_activesync_mcp.commands.mail_write",
            "outlook_activesync_mcp.model.mapping",
        ):
            try:
                mod = __import__(modname, fromlist=["*"])
                if getattr(mod, "find", None) is _find:
                    mod.find = find
                if getattr(mod, "find_all", None) is _find_all:
                    mod.find_all = find_all
            except Exception:
                pass
    except Exception:
        log.exception("wbxml None-safe patch failed")


# ── Unreachable server: fail fast instead of queueing ─────────────────────────
# Every ActiveSync call of one mailbox runs under its backend lock. When Exchange
# stops answering (VPN drop, «Connection reset», 90 s read timeouts), each queued
# call used to wait out its own timeout in turn: the Bank list hung for minutes and
# the browser's six connections to this server filled with waiting Bank requests,
# so even Seller mail sat on «Загрузка…». After a failure the client now refuses to
# call out for a short, growing pause; the next call after it is the probe.
UNREACH_BACKOFF_S = (10, 20, 40, 60)
LOCK_WAIT_S = 20        # a read waits this long for a busy mailbox, then answers from cache
LOCK_WAIT_WRITE_S = 60  # sends / moves / deletes may wait longer


def _is_unreachable(e) -> bool:
    """The server could not be contacted (as opposed to a refusal or a bad request)."""
    if getattr(e, "code", None) == "unreachable":
        return True
    msg = str(e)
    return getattr(e, "code", None) == "not_authenticated" and "недоступен" in msg


def _down_state(client) -> dict:
    return client.__dict__.setdefault("_eas_down", {"until": 0.0, "n": 0})


def acct_down_for(a: "Acct") -> float:
    """Seconds left in this mailbox's fail-fast pause (0 when it is reachable)."""
    b = a.backend
    c = getattr(b, "client", None) if b is not None else None
    if c is None:
        return 0.0
    return max(0.0, _down_state(c)["until"] - time.time())


def _fail_fast_command(orig):
    from outlook_activesync_mcp.errors import NotAuthenticated

    class ServerDown(NotAuthenticated):
        code = "unreachable"

    def command(self, cmd, node, *, policy_key=None, allow_empty=False):
        st = _down_state(self)
        left = st["until"] - time.time()
        if left > 0:
            raise ServerDown(f"сервер не отвечает, повторю через {int(left) + 1} с")
        try:
            tree = orig(self, cmd, node, policy_key=policy_key, allow_empty=allow_empty)
        except Exception as e:  # noqa: BLE001
            if _is_unreachable(e):
                st["n"] += 1
                pause = UNREACH_BACKOFF_S[min(st["n"], len(UNREACH_BACKOFF_S)) - 1]
                st["until"] = time.time() + pause
                log.warning("%s: server unreachable (%s) — fail fast for %s s", cmd, e, pause)
                raise ServerDown(f"сервер не отвечает ({e}); повторю через {pause} с") from e
            raise
        if st["n"]:
            log.info("%s: server reachable again after %s failures", cmd, st["n"])
        st["n"], st["until"] = 0, 0.0
        return tree

    command._eas_fail_fast = True  # type: ignore[attr-defined]
    return command


def _patch_unreachable_fail_fast():
    try:
        from outlook_activesync_mcp.client import EasClient
    except ImportError:
        return
    if getattr(EasClient.command, "_eas_fail_fast", False):
        return
    EasClient.command = _fail_fast_command(EasClient.command)


def _patch_message_date():
    """Upstream build_message sets neither Date nor Message-ID. Exchange adds them on
    SendMail; Alfa-Seller's server (Stalwart) stores the letter as is, so our sent
    mail had no date and sank to the bottom of «Отправленные»."""
    from email.utils import formatdate, make_msgid
    try:
        from outlook_activesync_mcp.commands import mail_write
    except ImportError:
        return
    build = mail_write.build_message
    if getattr(build, "_eas_dated", False):
        return

    def dated(*args, **kwargs) -> bytes:
        raw = build(*args, **kwargs)
        head = raw.split(b"\r\n\r\n", 1)[0].lower()
        extra = b"" if head.startswith(b"date:") or b"\r\ndate:" in head else \
            f"Date: {formatdate(localtime=True)}\r\n".encode()
        if not (head.startswith(b"message-id:") or b"\r\nmessage-id:" in head):
            extra += f"Message-ID: {make_msgid(domain='eas-mail')}\r\n".encode()
        return extra + raw

    dated._eas_dated = True
    mail_write.build_message = dated


def main():
    logging.basicConfig(level=os.environ.get("EAS_MAIL_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _patch_empty_sync_tree()
    _patch_deep_foldersync()
    _patch_sync_status_135()
    _patch_foldersync_invalid_key()
    _patch_moveitems_status()
    _patch_calendar_attendees()
    _patch_event_update_attendees()
    _patch_event_create_extras()
    _patch_attendee_types()
    _patch_message_date()
    _patch_unreachable_fail_fast()  # last: wraps the other command patches
    _write_runtime_token()
    bridge.drop_legacy_tmp()
    cfg = ensure_config()
    ACCTS.update(load_accounts(cfg))
    for acct in ACCTS.values():
        _mail_cache_load(acct)      # letters on screen at once; the first refresh is a delta
    threading.Thread(target=_mail_cache_keeper, daemon=True).start()
    main_acct = ACCTS["main"]
    # Don't poke Exchange until the user has entered credentials (first-run dist).
    if not account_needs_setup(cfg):
        main_acct.get()               # identity check runs in the background
        cal_refresh_bg(main_acct)     # warm the main calendar while the UI loads
    threading.Thread(target=_cal_keepfresh, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log.info("eas-mail on http://127.0.0.1:%s (accounts: %s%s)", PORT, ", ".join(ACCTS),
             "; needs_setup" if account_needs_setup(cfg) else "")
    srv.serve_forever()


if __name__ == "__main__":
    main()
