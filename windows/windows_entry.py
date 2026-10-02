"""Windows entry: point the shared server at %APPDATA%, Windows Credential Manager
and a fcntl stand-in, then run it. Everything else is the same webapp.py as the Mac.

Run with UTF-8 mode on (`python -X utf8 windows_entry.py`): webapp reads and writes
its JSON with the locale encoding, which is cp1251 on a Russian Windows."""
from __future__ import annotations

import errno
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent  # the unpacked «AAS mail» folder (or the checkout)
DATA = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "eas-bridge"


def fcntl_shim() -> types.ModuleType:
    """The vendored client's state lock (flock on a .lock file) via msvcrt.locking.
    Only the calls state.py makes: flock(fd, LOCK_EX | LOCK_NB) and flock(fd, LOCK_UN)."""
    import msvcrt
    m = types.ModuleType("fcntl")
    m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 2, 4, 8

    def flock(fd: int, op: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)  # lock and unlock the same byte, wherever "a+" left us
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK if op & m.LOCK_UN else msvcrt.LK_NBLCK, 1)
        except OSError as e:
            if op & m.LOCK_UN:
                raise
            raise OSError(errno.EAGAIN, "locked") from e  # what state._acquire retries on

    m.flock = flock
    return m


class _KeyringSecrets:
    """Windows Credential Manager through keyring (WinVaultKeyring)."""

    def get(self, account, service):
        import keyring
        return keyring.get_password(service, account)

    def set(self, account, service, password):
        import keyring
        keyring.set_password(service, account, password)


def install(webapp, secrets) -> None:
    webapp._keychain_get = lambda aid, service="eas-bridge": secrets.get(aid, service) or None
    webapp._keychain_set = lambda aid, pw, service="eas-bridge": secrets.set(aid, service, pw)
    webapp.ASSET_SUFFIX = "-win.zip"
    # The unpacked zip (python\pythonw.exe beside windows\) updates itself; a checkout does not.
    webapp._app_bundle = lambda: APP_DIR if (APP_DIR / "python" / "pythonw.exe").exists() else None

    def update_install() -> dict:
        """Start self_update.ps1 detached: it stops this process, swaps the folder and relaunches."""
        info = webapp.update_check(force=True)
        if not info.get("can_install"):
            return {"ok": False, "error": "bad_request",
                    "message": info.get("reason") or "Новой версии нет — у вас последняя."}
        # Run a copy: the app folder (and the script in it) is about to be replaced.
        script = Path(tempfile.gettempdir()) / "aas-mail-self_update.ps1"
        shutil.copyfile(HERE / "self_update.ps1", script)
        with open(webapp.bridge.DATA_DIR / "update.log", "a") as logf:
            subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                              info["url"], info.get("sha256") or "-", str(APP_DIR), info["latest"], str(os.getpid())],
                             stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(script.parent),
                             creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        logging.getLogger("aas-win").info("self-update to %s started", info["latest"])
        return {"ok": True, "started": True, "latest": info["latest"]}

    webapp.update_install = update_install


def setup():
    """Env, logging, the fcntl stand-in and Credential Manager; returns the ready webapp module."""
    if not sys.flags.utf8_mode:
        sys.exit("AAS mail: запустите с python -X utf8 (иначе config.json читается в cp1251)")
    sys.modules.setdefault("fcntl", fcntl_shim())
    DATA.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("EAS_BRIDGE_DATA_DIR", str(DATA))
    os.environ.setdefault("EAS_BRIDGE_CONFIG", str(DATA / "config.json"))
    logging.basicConfig(filename=str(DATA / "webapp.log"), level=os.environ.get("EAS_MAIL_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.path.insert(0, str(APP_DIR))  # webapp.py sits one level up (checkout and the zip alike)
    import webapp  # after the env: bridge and webapp read it at import time
    install(webapp, _KeyringSecrets())
    return webapp


if __name__ == "__main__":
    setup().main()  # server only, no window: the app itself starts from main.py
