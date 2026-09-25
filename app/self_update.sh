#!/bin/bash
# AAS mail self-update, started detached by webapp.update_install():
#   self_update.sh <zip url> <sha256 | -> <path/to/AAS mail.app> <expected version>
# Downloads the release zip over HTTPS (public repo: no GitHub login needed), checks
# its SHA-256 when GitHub published one and the version inside, quits the running
# app, swaps the bundle and relaunches. Restores the old app on failure.
set -u
URL="$1"; SHA="$2"; APP="$3"; WANT="$4"
TMP="$(mktemp -d)"; OLD="$APP.old-update"; ZIP="$TMP/update-mac.zip"
say() { echo "$(date '+%F %T') $*"; }
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

say "quit running app"
osascript -e "tell application \"$APP\" to quit" >/dev/null 2>&1
for _ in $(seq 1 40); do pgrep -f "$APP/Contents/MacOS/" >/dev/null || break; sleep 0.5; done
pkill -f "$APP/Contents/MacOS/" 2>/dev/null; sleep 1

rm -rf "$OLD"; mv "$APP" "$OLD" || fail "move old app"
ditto "$NEW" "$APP" || { rm -rf "$APP"; fail "copy new app"; }
say "installed $GOT, relaunch"
open "$APP"
rm -rf "$OLD" "$TMP"
