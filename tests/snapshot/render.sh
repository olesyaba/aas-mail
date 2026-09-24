#!/bin/bash
# Render the tray popover (light + dark) from live server data: render.sh [out_dir] [day_offset]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="${1:-$(mktemp -d)}"; DAY="${2:-0}"
[ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ] \
  && export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
BIN="$(mktemp -d)/tray-snapshot"
SRCS=$(ls app/*.swift | grep -v '/main.swift$')
# shellcheck disable=SC2086
swiftc -O $SRCS tests/snapshot/main.swift -o "$BIN" \
  -framework Cocoa -framework SwiftUI -framework Combine -framework UserNotifications 2>&1 | grep -E "error" || true
"$BIN" "$OUT" "$DAY"
