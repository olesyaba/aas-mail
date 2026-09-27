"""Android entry: point the shared server at the app's private storage and the
Android Keystore, then run it. Everything else is the same webapp.py as the Mac."""
from __future__ import annotations

import logging
import os
from pathlib import Path


def install(webapp, web_dir: Path, secrets) -> None:
    webapp.WEB = web_dir
    webapp._keychain_get = lambda aid, service="eas-bridge": secrets.get(aid, service) or None
    webapp._keychain_set = lambda aid, pw, service="eas-bridge": secrets.set(aid, service, pw)
    check = webapp.update_check

    def update_check(force: bool = False) -> dict:
        out = check(force)
        out["can_install"] = False  # no self_update.sh here: the APK is installed by hand
        out["page"] = f"https://github.com/{webapp.UPDATE_REPO}/releases/latest"
        if out.get("available"):
            out["reason"] = "На Android обновление ставится вручную: скачайте новый APK со страницы релиза"
        return out

    webapp.update_check = update_check


class _JavaSecrets:
    def __init__(self):
        from java import jclass  # Chaquopy
        self.k = jclass("ru.olesyaba.aasmail.Secrets")

    def get(self, account, service):
        v = self.k.get(account, service)
        return str(v) if v is not None else None

    def set(self, account, service, password):
        self.k.set(account, service, password)


def run(files_dir: str, cache_dir: str, page_key: str) -> None:
    data = Path(files_dir) / "eas-bridge"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "EAS_BRIDGE_DATA_DIR": str(data),
        "EAS_BRIDGE_CONFIG": str(data / "config.json"),
        "EAS_ATTACHMENT_DIR": str(data / "attachments"),
        "EAS_OVERFLOW_DIR": str(Path(cache_dir) / "overflow"),
        "EAS_MAIL_PAGE_KEY": page_key,
        "TMPDIR": cache_dir,
    })
    # The Mac shell redirects stdout to a log; here the log is a file the error screen can share.
    logging.basicConfig(filename=str(data / "eas-mail.log"), level=os.environ.get("EAS_MAIL_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import webapp  # after the env: bridge and webapp read it at import time
    install(webapp, Path(files_dir) / "web", _JavaSecrets())
    webapp.main()
