#!/usr/bin/env python3
"""Shared ActiveSync backend for eas-mail (webapp.py): config paths and EasBackend,
a serialised wrapper around the outlook-activesync-mcp client."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("eas-bridge")

CONF_PATH = Path(os.environ.get("EAS_BRIDGE_CONFIG", "~/.config/eas-bridge/config.json")).expanduser()
DATA_DIR = Path(os.environ.get("EAS_BRIDGE_DATA_DIR", "~/.config/eas-bridge")).expanduser()
LIST_FIELDS = ["subject", "from", "received", "is_read", "has_attachments", "to", "cc", "size"]


# ---------------------------------------------------------------------------
# Backend: everything Exchange-specific lives here so the IMAP layer is testable
# ---------------------------------------------------------------------------

class EasBackend:
    """Blocking ActiveSync calls, serialised by one lock (the client keeps
    SyncKeys in a state file and is not re-entrant)."""

    def __init__(self, cfg: dict):
        from outlook_activesync_mcp.client import EasClient
        from outlook_activesync_mcp.config import load_settings
        env = {
            "EXCHANGE_USERNAME": cfg["username"], "EXCHANGE_PASSWORD": cfg["password"],
            "EAS_URL": cfg["url"], "EAS_DEVICE_ID": cfg["device_id"],
            "EAS_STATE_FILE": str(DATA_DIR / cfg.get("state_name", "state.json")),
            "EAS_MAX_RESPONSE_TOKENS": "1000000",
        }
        self.client = EasClient(load_settings(env))
        self.lock = threading.RLock()
        self.window = int(cfg.get("imap_window_filter", 5))
        self.self_address = cfg.get("email", "")
        self._last_call = 0.0

    def ensure_identity(self):
        """The client needs our own SMTP address for send/reply/forward. Ask the
        server (settings status stores it); fall back to the configured address."""
        from outlook_activesync_mcp.commands import settings
        try:
            with self.lock:
                settings.handle(self.client, "status")
        except Exception as e:  # noqa: BLE001
            log.warning("status check failed: %s", e)
        if not self.client.store.get("smtp_address") and self.self_address:
            with self.client.store.transaction() as st:
                st["smtp_address"] = self.self_address

    def _pace(self):
        gap = 0.3 - (time.monotonic() - self._last_call)
        if gap > 0:
            time.sleep(gap)
        self._last_call = time.monotonic()

    def folders(self) -> list[dict]:
        with self.lock:
            self._pace()
            return self.client.foldersync()

    def list_messages(self, collection_id: str) -> list[dict]:
        from outlook_activesync_mcp.commands import mail
        out, cursor = [], None
        with self.lock:
            while True:
                self._pace()
                kw = dict(cursor=cursor) if cursor else dict(folder=collection_id)
                r = mail.handle(self.client, "list", limit=200, filter=self.window,
                                fields=LIST_FIELDS, **kw)
                if not r.get("ok", True) and r.get("error"):
                    raise RuntimeError(r.get("message") or r["error"])
                out.extend(r.get("items", []))
                cursor = r.get("next_cursor")
                if not (r.get("has_more") and cursor):
                    return out

    def fetch_mime(self, item_id: str) -> bytes:
        from outlook_activesync_mcp.commands.sync import body_preference
        from outlook_activesync_mcp.model.mapping import _body_data
        from outlook_activesync_mcp.models import unpack_item_id
        from outlook_activesync_mcp.wbxml import el, find, text_of
        cid, sid = unpack_item_id(item_id)
        with self.lock:
            self._pace()
            self.client.ensure_provisioned()
            req = el("ItemOperations", "ItemOperations",
                     el("ItemOperations", "Fetch",
                        el("ItemOperations", "Store", text="Mailbox"),
                        el("AirSync", "CollectionId", text=cid),
                        el("AirSync", "ServerId", text=sid),
                        el("ItemOperations", "Options",
                           el("AirSync", "MIMESupport", text="2"),
                           body_preference(type_code="4"))))
            tree = self.client.command("ItemOperations", req)
        fetch = find(tree, "ItemOperations", "Fetch")
        props = find(fetch, "ItemOperations", "Properties") if fetch is not None else None
        body = find(props, "AirSyncBase", "Body") if props is not None else None
        data = _body_data(body) if body is not None else None
        if data is None:
            raise KeyError(item_id)
        return data if isinstance(data, bytes) else data.encode("utf-8", "surrogateescape")

    def mark_read(self, item_ids: list[str], read: bool):
        from outlook_activesync_mcp.commands import mail
        with self.lock:
            self._pace()
            mail.handle(self.client, "mark_read", item_ids=item_ids, read=read)

    def delete(self, item_ids: list[str]):
        from outlook_activesync_mcp.commands import mail
        with self.lock:
            self._pace()
            mail.handle(self.client, "delete", item_ids=item_ids)

    def move(self, item_ids: list[str], folder_id: str):
        from outlook_activesync_mcp.commands import mail
        with self.lock:
            self._pace()
            mail.handle(self.client, "move", item_ids=item_ids, folder=folder_id)

    def send(self, mime: bytes):
        from outlook_activesync_mcp.commands.mail_write import send_raw_mime
        with self.lock:
            self._pace()
            send_raw_mime(self.client, mime, save_to_sent=True)
