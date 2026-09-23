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
bridge.DATA_DIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("EAS_MAIL_PORT", "8780"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
MAX_BODY = 40 * 1024 * 1024

# Product identity (About page + UI chrome).
APP_META = {
    "name": "AAS mail",
    "version": "1.2.0",
    "description": "Локальный клиент почты и календаря Alfa / Alfa-Seller поверх Exchange ActiveSync.",
    "contact_mm": "@olesya_ba",
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
                    "range": (None, None), "truncated": False}
        # folder_id -> unread count (best-effort, refreshed in background).
        self.unread: dict[str, int] = {}
        self.unread_ts = 0.0
        self.unread_lock = threading.Lock()
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
    confirmed, the plaintext copy is stripped from config.json."""
    pw = _keychain_get(account_id, service)
    if pw:
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
    try:
        with backend.lock:
            settings_cmd.handle(backend.client, "status")
    except Exception as e:  # noqa: BLE001
        login_ok, message = False, str(e)
    new_acct = Acct(aid, name, merged, green)
    new_acct.backend = backend
    ACCTS[aid] = new_acct
    if login_ok:
        threading.Thread(target=backend.ensure_identity, daemon=True).start()
        cal_refresh_bg(new_acct)
    return {"ok": True, "login_ok": login_ok, "message": message}


# -- ActiveSync dispatch ----------------------------------------------------

def _mail_list_paged(backend: bridge.EasBackend, params: dict) -> dict:
    """One UI 'list' request may need several ActiveSync Sync round-trips: some
    servers (Stalwart, used for the Seller account) return only a couple of
    changes per Sync response regardless of the requested limit, unlike
    Exchange which tends to fill the whole window in one round-trip. Keep
    pulling with mail.handle's own cursor until the requested limit is met or
    the server has nothing more, so one 'list'/'Ещё' click from the UI
    actually returns a full page instead of 1-2 messages."""
    from outlook_activesync_mcp.commands import mail
    limit = int(params.get("limit") or 40)
    kw0 = {k: v for k, v in params.items() if k not in ("cursor", "limit")}
    items: list = []
    next_cursor = params.get("cursor")
    has_more, rounds = True, 0
    while len(items) < limit and has_more and rounds < 60:
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


def call(a: Acct, domain: str, params: dict) -> dict:
    from outlook_activesync_mcp.commands import calendar, folders, mail, people, settings
    mods = {"mail": mail, "folders": folders, "events": calendar, "people": people, "settings": settings}
    mod = mods.get(domain)
    action = params.pop("action", None)
    if mod is None or not action:
        return {"ok": False, "error": "bad_request", "message": f"unknown call {domain}/{action}",
                "action": action or "", "count": 0, "items": []}
    # Folder create is not in upstream MCP (WBXML tables omit FolderCreate) —
    # we implement it here with a one-shot table patch.
    if domain == "folders" and action == "create":
        return folder_create(a, params.get("name") or "", params.get("parent_id") or "0")
    if domain == "folders" and action == "list":
        return folders_list(a, refresh=bool(params.get("refresh")))
    backend = a.get()
    with backend.lock:
        try:
            if domain == "mail" and action == "list":
                return _mail_list_paged(backend, params)
            backend._pace()
            return mod.handle(backend.client, action, **params)
        except Exception as e:  # noqa: BLE001 — surfaced to the UI as data
            log.warning("[%s] %s/%s failed: %s", a.id, domain, action, e)
            return {"ok": False, "action": action, "count": 0, "items": [],
                    "error": getattr(e, "code", None) or type(e).__name__, "message": str(e)}


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


def folders_list(a: Acct, refresh: bool = False) -> dict:
    """Full folder tree for the account — deeper than upstream FolderSync(0)/Add."""
    from datetime import datetime, timezone
    from outlook_activesync_mcp.commands import provision as prov
    from outlook_activesync_mcp.commands.provision import FOLDER_TYPES
    from outlook_activesync_mcp.models import envelope
    from outlook_activesync_mcp.wbxml import find, find_all, text_of
    import time as _time

    def _rows(tree_list: list) -> list[dict]:
        return [{
            "folder_id": f["id"], "name": f["name"],
            "kind": FOLDER_TYPES.get(f["type"], f"type-{f['type']}"),
            "type": f["type"],
            "parent_id": f.get("parent_id") or None,
        } for f in tree_list]

    def _cache_age(cached_at) -> float:
        if not cached_at:
            return 1e9
        try:
            if isinstance(cached_at, (int, float)):
                return _time.time() - float(cached_at)
            s = str(cached_at).replace("Z", "+00:00")
            return _time.time() - datetime.fromisoformat(s).timestamp()
        except Exception:
            return 1e9

    backend = a.get()
    with backend.lock:
        try:
            client = backend.client
            if not refresh:
                cache = client.store.get("folders") or {}
                tree = cache.get("tree")
                if tree is not None:
                    mailish = [f for f in tree if str(f.get("type")) in MAIL_FOLDER_TYPES]
                    # Stalwart often caches only the 6 system mail folders — don't
                    # trust that as complete; force a deep sync next.
                    truncated = a.id == "seller" and len(mailish) <= 6
                    if _cache_age(cache.get("cached_at")) < 15 * 60 and not truncated:
                        return envelope("list", _rows(tree), total=len(tree), has_more=False)

            client.ensure_provisioned()
            backend._pace()
            tree0 = client.command("FolderSync", prov.build_foldersync("0"))
            by_id, key = _parse_folder_changes(tree0)
            # Second round with the returned SyncKey — picks up user folders
            # some servers withhold on the initial full sync.
            if key and key != "0":
                a._folder_sync_key = key
                backend._pace()
                tree1 = client.command("FolderSync", prov.build_foldersync(key))
                more, key2 = _parse_folder_changes(tree1)
                by_id.update(more)
                for node in find_all(tree1, "FolderHierarchy", "Delete"):
                    sid = text_of(find(node, "FolderHierarchy", "ServerId"))
                    if sid:
                        by_id.pop(sid, None)
                if key2:
                    a._folder_sync_key = key2

            folders = list(by_id.values())
            log.info("[%s] folders_list deep: %d folders (mail=%d)",
                     a.id, len(folders),
                     sum(1 for f in folders if str(f.get("type")) in MAIL_FOLDER_TYPES))
            try:
                from outlook_activesync_mcp.client import _now_epoch_iso
                stamp = _now_epoch_iso()
            except Exception:
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with client.store.transaction() as st:
                st["folders"] = {"cached_at": stamp, "tree": folders}
            return envelope("list", _rows(folders), total=len(folders), has_more=False)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] folders/list failed: %s", a.id, e)
            return {"ok": False, "action": "list", "count": 0, "items": [],
                    "error": getattr(e, "code", None) or type(e).__name__, "message": str(e)}


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
            sync_key = getattr(a, "_folder_sync_key", None) or "0"
            if sync_key == "0":
                from outlook_activesync_mcp.commands import provision as prov
                tree0 = backend.client.command("FolderSync", prov.build_foldersync("0"))
                sync_key = text_of(find(tree0, "FolderHierarchy", "SyncKey")) or "0"
                a._folder_sync_key = sync_key
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
            new_key = text_of(find(tree, "FolderHierarchy", "SyncKey"))
            if new_key:
                a._folder_sync_key = new_key
            server_id = text_of(find(tree, "FolderHierarchy", "ServerId"))
            # Invalidate the foldersync cache so the next list sees the new folder.
            backend.client.foldersync(force=True)
            return {"ok": True, "action": "create", "count": 1,
                    "items": [{"folder_id": server_id, "name": name, "parent_id": parent_id, "type": "12"}]}
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] folder create failed: %s", a.id, e)
            return {"ok": False, "action": "create", "count": 0, "items": [],
                    "error": type(e).__name__, "message": str(e)}


def unread_snapshot(a: Acct) -> dict:
    with a.unread_lock:
        return {"ok": True, "folders": dict(a.unread), "total": sum(a.unread.values()), "ts": a.unread_ts}


def refresh_unread(a: Acct, *, force: bool = False) -> dict:
    """Best-effort unread counts for every mail folder. Counts the unread items
    in the first page of each folder (filter=5 ≈ last month) — enough for
    badges without a full-mailbox crawl."""
    if not force and a.unread_ts and time.time() - a.unread_ts < 90:
        return unread_snapshot(a)
    from outlook_activesync_mcp.commands import folders as folders_mod
    backend = a.get()
    try:
        with backend.lock:
            backend._pace()
            fl = folders_mod.handle(backend.client, "list", refresh=False)
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] unread folders list failed: %s", a.id, e)
        return unread_snapshot(a)
    counts: dict[str, int] = {}
    for f in fl.get("items") or []:
        if str(f.get("type")) not in MAIL_FOLDER_TYPES:
            continue
        fid = f.get("folder_id")
        if not fid:
            continue
        try:
            with backend.lock:
                backend._pace()
                page = _mail_list_paged(backend, {
                    "folder": fid, "limit": 80, "filter": 5,
                    "fields": ["is_read"],
                })
            counts[fid] = sum(1 for m in (page.get("items") or []) if not m.get("is_read"))
        except Exception as e:  # noqa: BLE001
            log.debug("[%s] unread %s: %s", a.id, fid, e)
    with a.unread_lock:
        a.unread = counts
        a.unread_ts = time.time()
    return unread_snapshot(a)


def _unread_keepfresh():
    while True:
        time.sleep(120)
        if time.time() - _last_activity[0] > 900:
            continue
        for a in list(ACCTS.values()):
            try:
                refresh_unread(a)
            except Exception:  # noqa: BLE001
                log.debug("[%s] unread refresh failed", a.id, exc_info=True)
# -- iMIP invitations ---------------------------------------------------------
# Stalwart (the Seller server) does not mail invitations for ActiveSync meetings and Exchange
# does not always deliver them to external domains, so the bridge can send the invitation itself:
# a text/calendar METHOD:REQUEST message, which Outlook/Gmail/Stalwart show as a meeting request.

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
    lines = ["BEGIN:VCALENDAR", "PRODID:-//eas-mail//RU", "VERSION:2.0", "METHOD:REQUEST", "BEGIN:VEVENT",
             f"UID:{uuid.uuid4()}@eas-mail", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
             f"DTSTART{';VALUE=DATE' if day else ''}:{utc(p['start'])}", f"DTEND{';VALUE=DATE' if day else ''}:{utc(p['end'])}",
             f"SUMMARY:{esc(p.get('subject') or '(без темы)')}", f"ORGANIZER;CN={esc(a.name)}:mailto:{me}"]
    if p.get("location"):
        lines.append(f"LOCATION:{esc(p['location'])}")
    if p.get("body"):
        lines.append(f"DESCRIPTION:{esc(p['body'])}")
    lines += [f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{x}" for x in to]
    lines += ["SEQUENCE:0", "STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    m = EmailMessage()
    m["From"], m["To"] = me, ", ".join(to)
    m["Subject"] = "Приглашение: " + (p.get("subject") or "(без темы)")
    m["Date"], m["Message-ID"] = formatdate(localtime=True), make_msgid(domain="eas-mail")
    m.set_content(f"{a.name} приглашает вас на встречу «{p.get('subject') or ''}»\n{p['start'].replace('T', ' ')} – {p['end'].replace('T', ' ')}\n"
                  + (f"\n{p['location']}\n" if p.get("location") else "") + (f"\n{p['body']}\n" if p.get("body") else ""))
    m.add_alternative("\r\n".join(lines) + "\r\n", subtype="calendar", params={"method": "REQUEST", "charset": "UTF-8"})
    a.get().send(m.as_bytes())
    return to

# -- calendar cache -----------------------------------------------------------
# One ActiveSync calendar sync costs ~30 s whatever the window (server side), so
# the whole window [today-7d, today+60d] is fetched once per account, kept in
# memory and refreshed in the background. Mail calls are not blocked meanwhile:
# the calendar fetch does not take backend.lock (the client guards its own state).

CAL_FIELDS = ["subject", "start", "end", "location", "is_all_day", "is_recurring", "organizer",
              "busy_status", "attendees", "response_type", "meeting_status", "body", "reminder"]
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


def _cal_refresh(a: Acct):
    from datetime import date, timedelta
    from outlook_activesync_mcp.commands import calendar
    c = a.cal
    with a.cv:
        if c["loading"]:
            return
        c["loading"] = True
    try:
        backend = a.get()
        today = date.today()
        start, end = today - timedelta(days=7), today + timedelta(days=60)
        backend._pace()
        r = calendar.handle(backend.client, "list", start=start.isoformat(), end=end.isoformat(),
                            limit=1000, fields=CAL_FIELDS)
        with a.cv:
            c.update(items=r.get("items", []), ts=time.time(), loaded=True, error=None,
                     range=(start, end), truncated=bool(r.get("truncated")))
        log.info("[%s] calendar cache: %d events%s", a.id, len(r.get("items", [])), " (truncated)" if r.get("truncated") else "")
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] calendar refresh failed: %s", a.id, e)
        with a.cv:
            c["error"] = str(e)
    finally:
        with a.cv:
            c["loading"] = False
            a.cv.notify_all()


def cal_refresh_bg(a: Acct):
    threading.Thread(target=_cal_refresh, args=(a,), daemon=True).start()


def cal_events(a: Acct, start: str, end: str) -> dict:
    from datetime import date, datetime
    c = a.cal
    ds, de = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    with a.cv:
        stale = time.time() - c["ts"] > CAL_TTL
        if not c["loading"] and (stale or not c["loaded"]) and not (c["error"] and not c["loaded"] and time.time() - c["ts"] < 5):
            cal_refresh_bg(a)
        if not c["loaded"]:
            a.cv.wait_for(lambda: c["loaded"] or not c["loading"], timeout=150)
        if not c["loaded"]:
            return {"ok": False, "action": "list", "count": 0, "items": [],
                    "error": "calendar_unavailable", "message": c["error"] or "календарь ещё загружается, повторите через минуту"}
        lo, hi = c["range"]
        items = list(c["items"])
    if ds < lo or de > hi:  # outside the cached window: ask Exchange directly
        return call(a, "events", {"action": "list", "start": start, "end": end, "limit": 1000, "fields": CAL_FIELDS})

    def ts(x):
        return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp() if x else 0
    lo_t = datetime.combine(ds, datetime.min.time()).timestamp()
    hi_t = datetime.combine(de, datetime.min.time()).timestamp()

    def end_ts(e):  # "end" is local "YYYY-MM-DD HH:MM"; fall back to the start
        try:
            return datetime.fromisoformat(str(e["end"]).replace(" ", "T")).timestamp()
        except (KeyError, ValueError):
            return ts(e.get("start_iso"))
    out = [e for e in items if ts(e.get("start_iso")) < hi_t and max(end_ts(e), ts(e.get("start_iso")) + 1) > lo_t]
    return {"ok": True, "action": "list", "count": len(out), "items": out,
            "truncated": c["truncated"], "cached_age": int(time.time() - c["ts"])}


def cal_refresh_wait(a: Acct):
    """Forced calendar sync: run (or join) a refresh and return when it is done."""
    with a.cv:
        if a.cal["loading"]:
            a.cv.wait_for(lambda: not a.cal["loading"], timeout=120)
            return
    _cal_refresh(a)


def retry_login(a: Acct) -> dict:
    """Clear a rejected-password latch (after fixing the password / connecting the VPN)."""
    from outlook_activesync_mcp.commands import settings
    backend = a.get()
    with backend.lock:
        r = settings.handle(backend.client, "reset_auth")
    a.cal["error"] = None
    return r


# -- preferences (signature, view, sync) ---------------------------------------

PREFS_PATH = bridge.DATA_DIR / "prefs.json"
DEFAULT_PREFS = {
    "signature": "", "signature2": "", "sig_replies": True, "threads": True,
    "auto_sync": 2, "cal_view": "week", "links": [],
    # Pinned mail folders as "acct:folderId" strings (e.g. "main:42").
    "favorite_folders": [],
}
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
            if type(v) is not type(DEFAULT_PREFS[k]):
                continue
            if k in ("signature", "signature2"):
                v = v[:5000]
            if k == "cal_view" and v not in ("day", "work", "week"):
                continue
            if k == "auto_sync" and v not in (0, 1, 2, 5, 10):
                continue
            if k == "links":
                v = [{"name": str(x.get("name", ""))[:80], "url": str(x.get("url", ""))[:500]}
                     for x in v[:30] if isinstance(x, dict) and str(x.get("url", "")).startswith(("http://", "https://"))]
            cur[k] = v
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


def parse_raw(raw: bytes):
    """Exchange sometimes sends raw UTF-8 in headers; decode as text first so the
    names come out right instead of as surrogate escapes."""
    try:
        return email.message_from_string(raw.decode("utf-8"), policy=email.policy.default)
    except UnicodeDecodeError:
        return email.message_from_bytes(raw, policy=email.policy.default)


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
    atts = [{"idx": i, "name": _att_name(p, i), "type": p.get_content_type(), "size": len(_part_bytes(p))}
            for i, p in enumerate(parts)]
    if not atts and has_att:
        try:
            atts = eas_attachments(a, item_id)
        except Exception:  # noqa: BLE001 — the message itself still opens
            log.warning("attachment list from server failed", exc_info=True)
    if html_doc:
        for p in msg.walk():
            cid = (p.get("Content-ID") or "").strip("<> ")
            if cid and p.get_content_maintype() == "image":
                data = base64.b64encode(p.get_payload(decode=True) or b"").decode()
                html_doc = html_doc.replace(f"cid:{cid}", f"data:{p.get_content_type()};base64,{data}")
    return {"subject": str(msg.get("Subject", "")), "date": str(msg.get("Date", "")),
            "from": _addr_list(msg, "From"), "to": _addr_list(msg, "To"), "cc": _addr_list(msg, "Cc"),
            "html": html_doc, "text": text, "attachments": atts,
            "message_id": str(msg.get("Message-ID", ""))}


def attachment_bytes(a: Acct, item_id: str, idx: int):
    msg = parse_raw(get_mime(a, item_id))
    parts = _attachment_parts(msg, _body_html(msg))
    if not 0 <= idx < len(parts):
        return None
    p = parts[idx]
    return _att_name(p, idx), p.get_content_type(), _part_bytes(p)


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
            page = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            csp = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                   "img-src 'self' data: https:; frame-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            return self._send(200, page.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
        if u.path == "/attachment":
            if q.get("t", [""])[0] != TOKEN:
                return self.send_error(403)
            try:
                a = acct_of(q.get("a", ["main"])[0])
                if "ref" in q:
                    data = eas_attachment_bytes(a, q["ref"][0])
                    name = q.get("n", ["attachment"])[0]
                    got = (name, mimetypes.guess_type(name)[0] or "application/octet-stream", data) if data is not None else None
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
            return self._json({"ok": False, "error": "bad_request", "message": "invalid JSON"}, 400)
        path = urlparse(self.path).path
        aid = params.pop("acct", "main") if isinstance(params, dict) else "main"
        try:
            if path == "/api/prefs":
                return self._json({"ok": True, "prefs": update_prefs(params.get("set") or {}) if params.get("set") is not None else load_prefs()})
            if path == "/api/about":
                cfg = ensure_config()
                return self._json({"ok": True, **APP_META,
                                   "needs_setup": account_needs_setup(cfg),
                                   "defaults": dict(DEFAULT_EAS_URLS)})
            if path == "/api/accounts":
                return self._json({"ok": True, "accounts": [
                    {"id": x.id, "name": x.name, "email": x.email, "green": x.green,
                     "unread": sum(x.unread.values())} for x in ACCTS.values()]})
            if path == "/api/account-config":
                if params.get("action") == "get":
                    return self._json({"ok": True, **get_account_config()})
                if params.get("action") == "save":
                    return self._json(save_account_config(params))
                return self._json({"ok": False, "error": "bad_request", "message": "unknown action"}, 400)
            a = acct_of(aid)
            if path == "/api/unread":
                if params.get("refresh"):
                    return self._json(refresh_unread(a, force=True))
                snap = unread_snapshot(a)
                if not snap["folders"]:
                    threading.Thread(target=lambda: refresh_unread(a), daemon=True).start()
                return self._json(snap)
            if path == "/api/message":
                try:
                    with a.get().lock:
                        return self._json({"ok": True, **render_message(a, params["item_id"], bool(params.get("has_att")))})
                except Gone as e:
                    return self._json({"ok": False, "error": "gone", "message": str(e)})
            if path == "/api/retry":
                r = retry_login(a)
                return self._json({"ok": True, "result": r.get("items")})
            if path == "/api/sync":
                if params.get("calendar"):
                    cal_refresh_wait(a)
                threading.Thread(target=lambda: refresh_unread(a, force=True), daemon=True).start()
                return self._json({"ok": True, "calendar_error": a.cal["error"]})
            if path == "/api/events" and params.get("action") == "list":
                return self._json(cal_events(a, params["start"], params["end"]))
            if path.startswith("/api/"):
                invite = params.pop("mime_invite", False) if path == "/api/events" else False
                res = call(a, path[5:], dict(params))
                if invite and res.get("ok", True) and params.get("action") == "create" and params.get("attendees"):
                    try:
                        sent = send_invites(a, params, params["attendees"])
                        res["mime_invited"] = sent
                    except Exception as e:  # noqa: BLE001
                        log.warning("[%s] invite mail failed: %s", a.id, e)
                        res["mime_error"] = str(e)
                if path == "/api/events" and res.get("ok", True):
                    cal_refresh_bg(a)  # a write happened: refresh the cache
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
        if attendees is None:
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
        else:
            # Empty roster → keep as appointment (no attendees block).
            fields.append(el("Calendar", "MeetingStatus", text="0"))
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


def main():
    logging.basicConfig(level=os.environ.get("EAS_MAIL_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _patch_empty_sync_tree()
    _patch_calendar_attendees()
    _patch_event_update_attendees()
    _write_runtime_token()
    cfg = ensure_config()
    ACCTS.update(load_accounts(cfg))
    main_acct = ACCTS["main"]
    # Don't poke Exchange until the user has entered credentials (first-run dist).
    if not account_needs_setup(cfg):
        main_acct.get()               # identity check runs in the background
        cal_refresh_bg(main_acct)     # warm the main calendar while the UI loads
    threading.Thread(target=_cal_keepfresh, daemon=True).start()
    threading.Thread(target=_unread_keepfresh, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log.info("eas-mail on http://127.0.0.1:%s (accounts: %s%s)", PORT, ", ".join(ACCTS),
             "; needs_setup" if account_needs_setup(cfg) else "")
    srv.serve_forever()


if __name__ == "__main__":
    main()
