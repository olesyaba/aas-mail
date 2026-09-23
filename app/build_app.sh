#!/bin/bash
# Build ~/Applications/AAS mail.app (or Почта EAS.app) — native WKWebView shell.
# With BUNDLE_SERVER=1 also embeds eas-bridge + vendored ActiveSync client + venv
# so the .app is self-contained for colleagues (they only enter their own creds).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

if [ -z "${DEVELOPER_DIR:-}" ]; then
  if [ -d /Applications/Xcode.app/Contents/Developer ]; then
    export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  fi
fi

APP_NAME="${APP_NAME:-AAS mail}"
APP="${APP_DIR:-$HOME/Applications}/${APP_NAME}.app"
BUNDLE_SERVER="${BUNDLE_SERVER:-0}"
VERSION="${VERSION:-1.2.0}"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -O *.swift -o "$APP/Contents/MacOS/EASMail" \
  -framework Cocoa -framework WebKit -framework SwiftUI -framework Combine -framework UserNotifications

[ -f AppIcon.icns ] || python3 make_icon.py
cp AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
[ -f TrayIcon.png ] || python3 make_icon.py
cp TrayIcon.png "$APP/Contents/Resources/TrayIcon.png"
[ -f "TrayIcon@2x.png" ] && cp "TrayIcon@2x.png" "$APP/Contents/Resources/TrayIcon@2x.png"

if [ "$BUNDLE_SERVER" = "1" ]; then
  echo "Bundling eas-bridge server into the app…"
  DEST="$APP/Contents/Resources/eas-bridge"
  mkdir -p "$DEST/web" "$DEST/vendor"
  cp "$ROOT/webapp.py" "$ROOT/bridge.py" "$DEST/"
  cp -R "$ROOT/web/"* "$DEST/web/"

  # Vendor outlook_activesync_mcp (client only — no fastmcp needed for the UI).
  MCP_SRC=""
  for cand in \
    "$HOME/.cache/uv/git-v0/checkouts/b02ba3756ad3eeb9/"*/src/outlook_activesync_mcp \
    /var/folders/*/T/cursor-sandbox-cache/*/uv/git-v0/checkouts/b02ba3756ad3eeb9/*/src/outlook_activesync_mcp
  do
    if [ -d "$cand" ] && [ -f "$cand/client.py" ]; then MCP_SRC="$cand"; break; fi
  done
  if [ -z "$MCP_SRC" ]; then
    echo "Cloning outlook-activesync-mcp for vendor…"
    TMP="$(mktemp -d)"
    git clone --depth 1 https://github.com/mainpart/outlook-activesync-mcp "$TMP/mcp"
    MCP_SRC="$TMP/mcp/src/outlook_activesync_mcp"
  fi
  rm -rf "$DEST/vendor/outlook_activesync_mcp"
  cp -R "$MCP_SRC" "$DEST/vendor/outlook_activesync_mcp"
  # Drop MCP-server-only module if present (keeps import surface lean).
  rm -f "$DEST/vendor/outlook_activesync_mcp/server.py" 2>/dev/null || true

  UV="${UV:-$HOME/.local/bin/uv}"
  if [ ! -x "$UV" ]; then UV="$(command -v uv || true)"; fi
  if [ -z "$UV" ]; then
    echo "error: uv not found — needed to create the bundled Python venv" >&2
    exit 1
  fi
  "$UV" venv "$DEST/.venv" --python 3.12
  "$UV" pip install --python "$DEST/.venv" "requests" "urllib3" "python-dateutil"
  # No project_dir.txt → main.swift uses Resources/eas-bridge
else
  echo "$ROOT" > "$APP/Contents/Resources/project_dir.txt"
fi

cat > "$APP/Contents/Info.plist" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>AAS mail</string>
<key>CFBundleDisplayName</key><string>AAS mail</string>
<key>CFBundleIdentifier</key><string>local.eas.mail</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundleExecutable</key><string>EASMail</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>${VERSION}</string>
<key>CFBundleVersion</key><string>${VERSION}</string>
<key>LSMinimumSystemVersion</key><string>12.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
P

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "built $APP (BUNDLE_SERVER=$BUNDLE_SERVER)"
