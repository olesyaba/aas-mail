"""iPad entry: point the shared server at the app's sandbox and the Keychain, then run
it. Everything else is the same webapp.py as the Mac and Android."""
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
        out["can_install"] = False  # no self_update.sh here: the app is installed from Xcode
        out["page"] = f"https://github.com/{webapp.UPDATE_REPO}/releases/latest"
        if out.get("available"):
            out["reason"] = "На iPad обновление ставится из Xcode (по кабелю с Mac)"
        return out

    webapp.update_check = update_check


def run(data_dir: str, cache_dir: str, web_dir: str, page_key: str) -> None:
    data = Path(data_dir) / "eas-bridge"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "EAS_BRIDGE_DATA_DIR": str(data),
        "EAS_BRIDGE_CONFIG": str(data / "config.json"),
        "EAS_ATTACHMENT_DIR": str(data / "attachments"),
        "EAS_OVERFLOW_DIR": str(Path(cache_dir) / "overflow"),
        "EAS_MAIL_PAGE_KEY": page_key,
        "TMPDIR": cache_dir,
    })
    logging.basicConfig(filename=str(data / "eas-mail.log"), level=os.environ.get("EAS_MAIL_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import ios_keychain
    import webapp  # after the env: bridge and webapp read it at import time
    install(webapp, Path(web_dir), ios_keychain)
    webapp.main()
