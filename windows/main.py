"""AAS mail for Windows 10/11 — a thin window (WebView2 via pywebview) and a tray icon
around the same local server as the Mac app. Started by «AAS mail.cmd»:
python\\pythonw.exe -X utf8 windows\\main.py

The page talks to the native side through window.webkit.messageHandlers (WKWebView on
the Mac); here a few lines of JS forward those calls to Api.post."""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import windows_entry

log = logging.getLogger("aas-win")
HANDLERS = ("aasTheme", "aasPalette", "aasPrefs", "aasNewMail", "aasPlaySound", "aasBadge", "aasSave")
SHIM = ("window.webkit = window.webkit || {messageHandlers: {}};" + "".join(
    f"window.webkit.messageHandlers.{n} = {{postMessage: b => window.pywebview.api.post('{n}', b)}};"
    for n in HANDLERS))
BAD_CHARS = str.maketrans({c: "_" for c in '<>:"/\\|?*'})
SOUNDS = windows_entry.HERE / "sounds"  # notice14.wav, short.wav (from app/Sounds/*.caf)


def play(sound: str) -> None:
    """Settings → «Звук нового письма»: notice14 | short | none."""
    import winsound
    f = SOUNDS / f"{sound}.wav"
    if sound != "none" and f.exists():
        winsound.PlaySound(str(f), winsound.SND_FILENAME | winsound.SND_ASYNC)


def notify(tray, title: str, text: str, view: str = "mail") -> None:
    """A tray toast without the system sound (ours plays instead): pystray's own
    notify() plus NIIF_NOSOUND. pystray is pinned in build_win.sh for this private call.
    A click on it opens `view` (see make_tray)."""
    from pystray._util import win32
    tray.aas_click_view = view
    tray._message(win32.NIM_MODIFY, win32.NIF_INFO, szInfo=text[:255], szInfoTitle=title[:63], dwInfoFlags=0x10)


def single_instance(name: str = "Local\\AASmail.show"):
    """The first launch owns a named event and gets its handle; a later one signals it
    (the first shows its window) and gets None."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateEventW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    k.SetEvent.argtypes = (wintypes.HANDLE,)
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    h = k.CreateEventW(None, False, False, name)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        k.SetEvent(h)
        return None
    return lambda: k.WaitForSingleObject(h, 0xFFFFFFFF)  # blocks until the next launch


def reminders(base: str, prefs, tray, stop: threading.Event) -> None:
    """Meeting toasts `reminder_minutes` before the start, like the Mac tray
    (TrayNotificationPlan): today and tomorrow, both accounts, each start once."""
    from datetime import date, datetime, timedelta
    shown: set[str] = set()
    while not stop.wait(30):
        lead = int(prefs().get("reminder_minutes", 5) or 0)
        if not lead:
            continue
        try:
            tok = (windows_entry.DATA / "runtime_token").read_text().strip()
            now = datetime.now().astimezone()
            for acct in ("main", "seller"):
                body = json.dumps({"acct": acct, "action": "list", "start": date.today().isoformat(),
                                   "end": (date.today() + timedelta(days=2)).isoformat(), "limit": 300}).encode()
                req = urllib.request.Request(base + "api/events", body, {"X-Tok": tok, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    res = json.loads(r.read())
                for e in res.get("items") or [] if res.get("ok") else []:
                    if e.get("is_all_day") or str(e.get("meeting_status") or "").lower() == "cancelled":
                        continue
                    start = datetime.fromisoformat(str(e.get("start_iso") or "").replace("Z", "+00:00"))
                    key = f"{acct}|{e.get('item_id')}|{e.get('start_iso')}"
                    # In the lead window, or just started (app launched late): once per start time.
                    if key in shown or not (start - timedelta(minutes=lead) <= now < start + timedelta(minutes=2)):
                        continue
                    shown.add(key)
                    left = max(0, round((start - now).total_seconds() / 60))
                    notify(tray, ("🟢 " if acct == "seller" else "🔴 ") + (f"Через {left} мин" if left else "Начинается"),
                           e.get("subject") or "(без темы)", view="cal")
                    play(prefs().get("mail_sound") or "notice14")
        except Exception:  # noqa: BLE001 — not set up yet / server busy / offline: next round
            log.debug("reminders round failed", exc_info=True)


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Api:
    """window.pywebview.api — what the Mac shell's WKScriptMessageHandler does."""

    def __init__(self, base: str, prefs):
        self._base, self._win, self._tray, self._prefs = base, None, None, prefs

    def post(self, name, body):
        try:
            getattr(self, "_" + name, lambda b: None)(body)
        except Exception:  # noqa: BLE001 — a native nicety failing must not break the page
            log.exception("native %s failed", name)

    def _aasBadge(self, n):
        n = int(n or 0)
        title = f"AAS mail — {n} непрочитанных" if n else "AAS mail"
        self._win.set_title(title)  # the taskbar button shows the window title
        if self._tray:
            self._tray.title = title

    def _aasNewMail(self, p):
        if not self._tray:
            return
        n = int(p.get("count") or 1)
        head = f"{n} новых писем" if n > 1 else (p.get("from") or "Новое письмо")
        notify(self._tray, head, f"{p.get('subject', '')}\n{p.get('preview', '')}")
        play(self._prefs().get("mail_sound") or "notice14")

    def _aasPlaySound(self, sound):
        play(str(sound))  # the preview button in Settings

    def _aasSave(self, p):
        import webview
        files = [(f["url"], (f.get("name") or "attachment").translate(BAD_CHARS)) for f in p.get("files") or []]
        if not files:
            return
        mode, downloads = p.get("mode"), str(Path.home() / "Downloads")
        if mode == "open":
            saved = self._fetch(files, Path(tempfile.gettempdir()) / "AAS mail attachments")
            if saved:
                os.startfile(saved[0])  # the default app for that file type
            return
        if mode == "as":
            dest = self._win.create_file_dialog(webview.SAVE_DIALOG, directory=downloads, save_filename=files[0][1])
            if not dest:
                return
            dest = Path(dest if isinstance(dest, str) else dest[0])
            saved = self._fetch([(files[0][0], dest.name)], dest.parent, overwrite=True)
        else:
            dirs = self._win.create_file_dialog(webview.FOLDER_DIALOG, directory=downloads)
            if not dirs:
                return
            saved = self._fetch(files, Path(dirs[0]))
        if saved:  # like Finder on the Mac: the saved file, selected
            subprocess.Popen(["explorer", f"/select,{saved[0]}"])
        total = 1 if mode == "as" else len(files)
        ok = len(saved) == total
        text = (f"Сохранено: {saved[0].name}" if ok else "Не удалось сохранить вложение") if total == 1 \
            else f"Сохранено {len(saved)} из {total}"
        self._win.evaluate_js(f"window.aasSaved && aasSaved({json.dumps(ok)}, {json.dumps(text)})")

    def _fetch(self, files, into: Path, overwrite=False) -> list[Path]:
        """Download from the local server; «name (2).ext» instead of overwriting."""
        into.mkdir(parents=True, exist_ok=True)
        saved = []
        for url, name in files:
            try:
                with urllib.request.urlopen(self._base + url.lstrip("/"), timeout=120) as r:
                    data = r.read()
            except OSError:
                continue
            dest, i = into / name, 2
            while not overwrite and dest.exists():
                dest, i = into / f"{Path(name).stem} ({i}){Path(name).suffix}", i + 1
            dest.write_bytes(data)
            saved.append(dest)
        if not saved:
            self._win.evaluate_js("window.aasSaved && aasSaved(false, 'Не удалось получить вложение с сервера')")
        return saved


def make_tray(api: Api, show, quit_app):
    import pystray
    from PIL import Image
    icon_png = windows_entry.HERE / "icon.png"
    menu = pystray.Menu(
        pystray.MenuItem("Открыть почту", lambda: show("mail"), default=True),
        pystray.MenuItem("Календарь", lambda: show("cal")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", quit_app))
    icon = pystray.Icon("AAS mail", Image.open(icon_png), "AAS mail", menu)
    icon.aas_click_view = "mail"
    # A click on our toast arrives as NIN_BALLOONUSERCLICK on the icon's callback message.
    from pystray._util import win32
    on_notify = icon._message_handlers[win32.WM_NOTIFY]
    icon._message_handlers[win32.WM_NOTIFY] = lambda w, l: (
        show(icon.aas_click_view) if l == 0x405 else on_notify(w, l))
    return icon


def main() -> None:
    wait_next_launch = single_instance()
    if not wait_next_launch:
        return  # already running: it has just been asked to show its window
    webapp = windows_entry.setup()
    import webview
    port = webapp.PORT
    if not port_open(port):  # open = a server left by a crashed run: talk to it
        threading.Thread(target=webapp.main, daemon=True).start()
    deadline = time.time() + 90
    while not port_open(port) and time.time() < deadline:
        time.sleep(0.3)

    base = f"http://127.0.0.1:{port}/"
    api = Api(base, webapp.load_prefs)
    win = webview.create_window("AAS mail", base, js_api=api, width=1400, height=900, min_size=(640, 480),
                                text_select=True)
    api._win = win
    quitting = threading.Event()

    def show(view: str):
        win.show()
        win.restore()
        if view == "cal":
            win.evaluate_js("typeof showView==='function' && (showView('cal'), location.hash='cal')")
        else:
            win.evaluate_js("typeof showView==='function' && view!=='mail' && showView('mail', ACCT)")

    def quit_app():
        quitting.set()
        if api._tray:
            api._tray.stop()
        win.destroy()

    def on_closing():
        if quitting.is_set():
            return True
        win.hide()  # like the Mac: closing only hides, the tray brings it back
        return False

    def nudge_sync():
        # WebView2 slows a hidden page's timers too: ask it every minute whether a sync is due.
        while not quitting.wait(60):
            try:
                win.evaluate_js("typeof autoSyncDue==='function' ? autoSyncDue()"
                                " : (typeof autoSyncTick==='function' && autoSyncTick())")
            except Exception:  # noqa: BLE001 — window not ready / gone
                pass

    win.events.loaded += lambda: win.evaluate_js(SHIM)
    win.events.closing += on_closing

    def started():
        api._tray = make_tray(api, show, quit_app)
        api._tray.run_detached()
        threading.Thread(target=nudge_sync, daemon=True).start()
        threading.Thread(target=reminders, args=(base, webapp.load_prefs, api._tray, quitting), daemon=True).start()

        def on_next_launch():
            while not quitting.is_set():
                wait_next_launch()
                show("mail")
        threading.Thread(target=on_next_launch, daemon=True).start()

    if not port_open(port):
        log.error("server did not start, see %s", windows_entry.DATA / "webapp.log")
    webview.settings["ALLOW_DOWNLOADS"] = True  # the plain attachment link saves to «Загрузки»
    webview.start(started, private_mode=False, storage_path=str(windows_entry.DATA / "webview"))


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # pythonw has no console: leave testers something to send
        import traceback
        windows_entry.DATA.mkdir(parents=True, exist_ok=True)
        (windows_entry.DATA / "crash.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
