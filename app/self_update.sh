#!/bin/bash
# AAS mail self-update, started detached by webapp.update_install():
#   self_update.sh <zip url> <sha256 | -> <path/to/AAS mail.app> <expected version>
# Downloads the release zip over HTTPS (public repo: no GitHub login needed), checks
# its SHA-256 when GitHub published one and the version inside, quits the running
# app AND its server, swaps the bundle and relaunches. Restores the old app on failure.
# SELF_UPDATE_LIB=1 only defines the functions (tests source it).
set -u
cd / || true   # the old bundle (our cwd) is about to be moved away

say() { echo "$(date '+%F %T') $*"; }
PORT="${EAS_MAIL_PORT:-8780}"

# Quit the app, then make sure the bundle's python server is gone too: a Swift shell
# killed by pkill never runs applicationWillTerminate, the server keeps :8780, and the
# relaunched app silently talks to the OLD backend (which then asks to update again).
stop_app() {
  local app="$1" srv="$1/Contents/Resources/eas-bridge/python/"
  osascript -e "tell application \"$app\" to quit" >/dev/null 2>&1
  for _ in $(seq 1 40); do pgrep -f "$app/Contents/MacOS/" >/dev/null || break; sleep 0.5; done
  pkill -f "$app/Contents/MacOS/" 2>/dev/null
  pkill -f "$srv" 2>/dev/null
  for _ in $(seq 1 20); do pgrep -f "$srv" >/dev/null || break; sleep 0.5; done
  pkill -9 -f "$srv" 2>/dev/null
  # The port is free once nobody answers there any more.
  for _ in $(seq 1 20); do curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$PORT/" || break; sleep 0.5; done
  return 0
}

[ "${SELF_UPDATE_LIB:-}" = "1" ] && return 0 2>/dev/null

URL="$1"; SHA="$2"; APP="$3"; WANT="$4"
TMP="$(mktemp -d)"; OLD="$APP.old-update"; ZIP="$TMP/update-mac.zip"
# One update at a time: a second click / auto-check while this one runs must not
# swap the bundle under it (the log showed two installs racing).
LOCK="$HOME/.config/eas-bridge/update.lock"
mkdir "$LOCK" 2>/dev/null || { say "another update is running — skip"; rm -rf "$TMP"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
fail() {
  say "FAIL: $*"
  [ -d "$OLD" ] && [ ! -d "$APP" ] && mv "$OLD" "$APP"
  open "$APP"; rm -rf "$TMP"; exit 1
}

case "$URL" in https://github.com/*) ;; *) fail "unexpected download url: $URL" ;; esac
say "download $URL"
curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 20 --max-time 900 -o "$ZIP" "$URL" || fail "download"
if [ "$SHA" != "-" ] && [ -n "$SHA" ]; then
  GOTSHA="$(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
  [ "$GOTSHA" = "$SHA" ] || fail "checksum mismatch ($GOTSHA, expected $SHA)"
  say "checksum ok"
fi
( cd "$TMP" && unzip -q "$ZIP" ) || fail "unzip"
NEW="$(find "$TMP" -maxdepth 3 -name '*.app' -type d | head -1)"
[ -n "$NEW" ] || fail "no .app in the archive"
GOT="$(defaults read "$NEW/Contents/Info" CFBundleShortVersionString 2>/dev/null)"
[ "$GOT" = "$WANT" ] || fail "archive has version '$GOT', expected '$WANT'"

say "quit running app and its server"
stop_app "$APP"

rm -rf "$OLD"; mv "$APP" "$OLD" || fail "move old app"
ditto "$NEW" "$APP" || { rm -rf "$APP"; fail "copy new app"; }
say "installed $GOT, relaunch"
open "$APP"
rm -rf "$OLD" "$TMP"
