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
VERSION="${VERSION:-$(sed -n 's/^ *"version": "\([^"]*\)".*/\1/p' "$ROOT/webapp.py" | head -1)}"  # single source: APP_META

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Deployment target must match LSMinimumSystemVersion below — without it swiftc
# targets the build machine's macOS and the app silently needs that version.
MACOS_MIN="${MACOS_MIN:-12.0}"
swiftc -O -target "$(uname -m)-apple-macos${MACOS_MIN}" *.swift -o "$APP/Contents/MacOS/EASMail" \
  -framework Cocoa -framework WebKit -framework SwiftUI -framework Combine -framework UserNotifications

[ -f AppIcon.icns ] || python3 make_icon.py
cp AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
[ -f TrayIcon.png ] || python3 make_icon.py
cp TrayIcon.png "$APP/Contents/Resources/TrayIcon.png"
[ -f "TrayIcon@2x.png" ] && cp "TrayIcon@2x.png" "$APP/Contents/Resources/TrayIcon@2x.png"
# New-mail alert sounds for UNNotificationSound.named (must sit in Resources root).
if [ -d Sounds ]; then
  cp Sounds/*.caf "$APP/Contents/Resources/" 2>/dev/null || true
fi

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
  # Bytecode from the builder's uv cache embeds that cache's path (/Users/<you>/…);
  # the bundle compiles its own below.
  find "$DEST/vendor" -name "__pycache__" -type d -prune -exec rm -rf {} +
  # Drop MCP-server-only module if present (keeps import surface lean).
  rm -f "$DEST/vendor/outlook_activesync_mcp/server.py" 2>/dev/null || true

  UV="${UV:-$HOME/.local/bin/uv}"
  if [ ! -x "$UV" ]; then UV="$(command -v uv || true)"; fi
  if [ -z "$UV" ]; then
    echo "error: uv not found — needed to create the bundled Python venv" >&2
    exit 1
  fi
  # Embed the interpreter itself, not a venv: a venv's bin/python is a symlink
  # to the builder's ~/.local/share/uv/…, which does not exist on colleagues'
  # Macs. uv-managed CPython (python-build-standalone) is a static, relocatable
  # binary that finds its stdlib relative to itself.
  "$UV" python install 3.12 >/dev/null
  PY_BIN="$("$UV" python find --managed-python 3.12)"
  PY_HOME="$(cd "$(dirname "$(readlink -f "$PY_BIN")")/.." && pwd)"
  PY_LIB="$(basename "$(ls -d "$PY_HOME"/lib/python3.*/ | head -1)")"   # python3.12
  mkdir -p "$DEST/python/bin" "$DEST/python/lib"
  cp "$(readlink -f "$PY_BIN")" "$DEST/python/bin/python3"
  # Stdlib minus what a headless HTTP server never imports (GUI, tests, pip, headers).
  rsync -a --exclude '__pycache__' --exclude 'test/' --exclude 'idlelib/' --exclude 'tkinter/' \
    --exclude 'turtledemo/' --exclude 'ensurepip/' --exclude 'lib2to3/' --exclude 'pydoc_data/' \
    --exclude 'site-packages/*' --exclude 'config-3.*' --exclude '_tkinter*' --exclude '_test*' \
    --exclude 'xxlimited*' "$PY_HOME/lib/$PY_LIB" "$DEST/python/lib/"
  # sysconfig's build-time data records where the interpreter was installed — the
  # builder's home directory. Nothing at runtime needs it; neutralise the path so a
  # shared package carries no trace of who built it.
  for f in "$DEST/python/lib/$PY_LIB"/_sysconfigdata*.py; do
    [ -f "$f" ] && LC_ALL=C sed -i '' "s#${PY_HOME}#/opt/aas-mail/python#g; s#${HOME}#/Users/builder#g" "$f"
  done
  "$UV" pip install --quiet --python "$DEST/python/bin/python3" --target "$DEST/site-packages" \
    "requests" "urllib3" "python-dateutil"
  # Precompile once at build time: the bundle is read-only at runtime
  # (PYTHONDONTWRITEBYTECODE) and must not be modified after signing, and
  # hash-based .pyc stay valid whatever mtimes the zip round-trip leaves.
  "$DEST/python/bin/python3" -m compileall -q -j0 --invalidation-mode unchecked-hash \
    "$DEST/python/lib/$PY_LIB" "$DEST/site-packages" "$DEST/vendor" "$DEST/bridge.py" >/dev/null
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
<key>LSMinimumSystemVersion</key><string>${MACOS_MIN}</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
P

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "built $APP (BUNDLE_SERVER=$BUNDLE_SERVER)"
