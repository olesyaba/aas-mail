#!/bin/bash
# Run every test suite: Python backend + HTTP/UI wiring, web UI JS logic, Swift tray logic.
#   bash tests/run_tests.sh            # all
#   bash tests/run_tests.sh py js      # a subset (py | js | swift | privacy)
# Live checks against the running app: tests/live_smoke.sh (read-only).
# Python runs in the same environment the app uses: a Python 3.12 venv with the
# vendored outlook_activesync_mcp client on PYTHONPATH.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SUITES=("$@"); [ ${#SUITES[@]} -eq 0 ] && SUITES=(py js swift privacy)
FAILED=()

BUNDLE="$ROOT/dist/AAS mail.app/Contents/Resources/eas-bridge"   # the shipped runtime

find_python() {
  for py in "$ROOT/.venv/bin/python" "$BUNDLE/python/bin/python3"; do
    [ -x "$py" ] && { echo "$py"; return; }
  done
  command -v python3
}

find_vendor() {
  for cand in "$ROOT/vendor" \
              "$BUNDLE/vendor" \
              "$HOME/.cache/uv/git-v0/checkouts/b02ba3756ad3eeb9/"*/src; do
    [ -f "$cand/outlook_activesync_mcp/client.py" ] && { echo "$cand"; return; }
  done
}

for s in "${SUITES[@]}"; do
  case "$s" in
    py)
      PY="$(find_python)"; VENDOR="$(find_vendor)"
      echo "==> Python ($("$PY" --version 2>&1), vendor: ${VENDOR:-none})"
      if [ -z "$VENDOR" ]; then echo "outlook_activesync_mcp not found — build the dist app once"; FAILED+=(py); continue; fi
      PYTHONPATH="$VENDOR:$BUNDLE/site-packages:$ROOT/tests/python" PYTHONDONTWRITEBYTECODE=1 \
        "$PY" -m unittest discover -s tests/python -t tests/python "${UNITTEST_ARGS:--q}" || FAILED+=(py)
      ;;
    js)
      echo "==> Web UI (node $(node --version))"
      node --test tests/js/*.test.mjs || FAILED+=(js)
      ;;
    swift)
      echo "==> Swift tray logic"
      [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ] \
        && export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
      OUT="$(mktemp -d)/tray-tests"
      # The whole app must type-check for the minimum macOS the bundle declares.
      (cd app && swiftc -typecheck -target "$(uname -m)-apple-macos12.0" *.swift 2>&1 | grep -E "error:") \
        && { echo "app does not build for macOS 12"; FAILED+=(swift); }
      swiftc -O app/TrayModels.swift app/TrayEventMerge.swift app/TrayLabelFormatter.swift \
        app/TrayNotificationPlan.swift app/TrayFormatters.swift app/TrayNotifications.swift \
        tests/swift/main.swift -o "$OUT" \
        -framework AppKit -framework UserNotifications && "$OUT" || FAILED+=(swift)
      ;;
    privacy)
      ZIP="$(ls -t dist/AAS-mail-*-mac.zip 2>/dev/null | head -1)"
      echo "==> Privacy scan ($ZIP)"
      if [ -z "$ZIP" ]; then echo "no dist zip — run app/build_dist.sh"; FAILED+=(privacy)
      else python3 tests/privacy_scan.py "$ZIP" || FAILED+=(privacy); fi
      ;;
    *) echo "unknown suite $s"; FAILED+=("$s") ;;
  esac
done

if [ ${#FAILED[@]} -gt 0 ]; then echo "FAILED: ${FAILED[*]}"; exit 1; fi
echo "ALL PASSED"
