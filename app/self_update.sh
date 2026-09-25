#!/bin/bash
# AAS mail self-update, started detached by webapp.update_install():
#   self_update.sh <gh> <owner/repo> <tag> <path/to/AAS mail.app> <expected version>
# Downloads the release zip with the user's own `gh` login, checks the version inside,
# quits the running app, swaps the bundle and relaunches. Restores the old app on failure.
set -u
GH="$1"; REPO="$2"; TAG="$3"; APP="$4"; WANT="$5"
TMP="$(mktemp -d)"; OLD="$APP.old-update"
say() { echo "$(date '+%F %T') $*"; }
fail() {
  say "FAIL: $*"
  [ -d "$OLD" ] && [ ! -d "$APP" ] && mv "$OLD" "$APP"
  open "$APP"; rm -rf "$TMP"; exit 1
}

say "download $TAG"
"$GH" release download "$TAG" --repo "$REPO" --pattern '*-mac.zip' --dir "$TMP" || fail "download"
( cd "$TMP" && unzip -q ./*-mac.zip ) || fail "unzip"
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
