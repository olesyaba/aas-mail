#!/usr/bin/env python3
"""Check a distribution zip for the builder's personal data before it is shared.

Needles come from the builder's own local config/prefs (logins, e-mails, device
ids, signatures, saved meeting links, session token) plus name/home-path markers.
Every file in the archive is searched as bytes (UTF-8 and UTF-16), binaries too.
Only needle *labels* and file paths are printed — never the values.

    python3 tests/privacy_scan.py dist/AAS-mail-1.2.6-mac.zip [dist/AAS-mail-1.2.6-android.apk …]
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

DATA = Path("~/.config/eas-bridge").expanduser()
# Intentional, public: the support contact shown in «О приложении» and README.
ALLOWED = {"@olesya_ba"}
FORBIDDEN_FILES = ("config.json", "prefs.json", "runtime_token", "webapp.log", ".env")


def needles() -> dict[str, str]:
    out: dict[str, str] = {}

    def add(label, value, min_len=5):
        v = str(value or "").strip()
        if len(v) >= min_len:
            out[label] = v

    try:
        cfg = json.loads((DATA / "config.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    add("main login", cfg.get("username"))
    add("main login (after domain)", str(cfg.get("username") or "").split("\\")[-1])
    add("main e-mail", cfg.get("email"))
    add("main device id", cfg.get("device_id"))
    add("config password (plaintext)", cfg.get("password"), 1)
    s2 = cfg.get("second") or {}
    add("seller login", s2.get("username"))
    add("seller e-mail", s2.get("email"))
    add("seller device id", s2.get("device_id"))
    add("seller password (plaintext)", s2.get("password"), 1)
    try:
        prefs = json.loads((DATA / "prefs.json").read_text())
    except (OSError, ValueError):
        prefs = {}
    for key in ("signature", "signature2"):
        for i, line in enumerate(str(prefs.get(key) or "").splitlines()):
            if len(line.strip()) >= 8 and line.strip().lower() not in ("с уважением,", "с уважением"):
                add(f"{key} line {i + 1}", line, 8)
    for i, link in enumerate(prefs.get("links") or []):
        add(f"saved meeting link {i + 1} url", link.get("url"))
        add(f"saved meeting link {i + 1} name", link.get("name"), 8)
    try:
        add("session token", (DATA / "runtime_token").read_text())
    except OSError:
        pass
    add("home path", str(Path.home()))
    for word in ("Бабакаева", "Babakaeva", "obabakaeva", "OBabakaeva"):
        add(f"name marker {word}", word, 3)
    return out


def _members(z: zipfile.ZipFile, prefix: str = ""):
    """(name, bytes) of every file, opening nested archives too: an APK keeps the
    Python code in assets/chaquopy/*.imy, which are zips of their own."""
    import io
    for n in z.namelist():
        if n.endswith("/"):
            continue
        data = z.read(n)
        yield prefix + n, data
        if n.endswith((".imy", ".zip", ".apk", ".jar")) and data[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as inner:
                    yield from _members(inner, prefix + n + "!/")
            except zipfile.BadZipFile:
                pass


def main(zip_path: str) -> int:
    marks = needles()
    print(f"scanning {zip_path} for {len(marks)} personal markers (values not printed)")
    hits: list[tuple[str, str]] = []
    bad_files: list[str] = []
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for n, data in _members(z):
            count += 1
            base = n.rsplit("/", 1)[-1]
            if base in FORBIDDEN_FILES or (base.startswith("state") and base.endswith(".json")):
                bad_files.append(n)
            low = data.lower()
            for label, v in marks.items():
                for enc in ("utf-8", "utf-16-le"):
                    b = v.encode(enc)
                    if b in data or b.lower() in low:
                        hits.append((label, n))
                        break
    print(f"  files in archive (nested included): {count}")
    if bad_files:
        print("  ✘ config/state/token files present:", *bad_files, sep="\n     ")
    else:
        print("  ✔ no config.json / prefs.json / state*.json / runtime_token / logs")
    if hits:
        for label, n in sorted(set(hits)):
            print(f"  ✘ {label}: {n}")
    else:
        print("  ✔ none of your logins, e-mails, device ids, signatures, meeting links, token or home path found")
    print(f"  ℹ intentionally public: {', '.join(sorted(ALLOWED))} (support contact in «О приложении»)")
    return 1 if hits or bad_files else 0


if __name__ == "__main__":
    # Every archive given (the Mac zip and both APKs on a release); fails if any fails.
    sys.exit(max(main(p) for p in sys.argv[1:]))
