"""Create ~/.config/eas-bridge/config.json (mode 600) from the existing
outlook-activesync-mcp entry in ~/.claude.json. Never prints the password."""
import hashlib
import json
import os
import secrets
import socket
from pathlib import Path

src = json.load(open(Path("~/.claude.json").expanduser()))["mcpServers"]["outlook-activesync-mcp"]["env"]
cfgp = Path("~/.config/eas-bridge/config.json").expanduser()
old = json.load(open(cfgp)) if cfgp.exists() else {}
cfg = {
    "url": src["EAS_URL"],
    "username": src["EXCHANGE_USERNAME"],
    "password": src["EXCHANGE_PASSWORD"],
    "email": "OBabakaeva@alfabank.ru",
    "device_id": hashlib.sha256(
        f"eas-bridge|{src['EXCHANGE_USERNAME']}|{socket.gethostname()}".encode()).hexdigest()[:32].upper(),
    "local_password": old.get("local_password") or secrets.token_urlsafe(12),
    "imap_port": 1143, "smtp_port": 1025, "http_port": 8443,
    "imap_window_filter": 5,   # EAS FilterType: 3=1 week 4=2 weeks 5=1 month
}
cfgp.parent.mkdir(parents=True, exist_ok=True)
cfgp.write_text(json.dumps(cfg, indent=2))
os.chmod(cfgp, 0o600)
print("written", cfgp)
print("local_password:", cfg["local_password"])
