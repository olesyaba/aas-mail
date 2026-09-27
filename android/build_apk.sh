#!/bin/bash
# Build the Android APK from the shared sources: stage webapp.py/bridge.py/vendor/web, run Gradle, copy to dist/.
#   bash android/build_apk.sh            # release (needs ~/.config/aas-mail/android-release.jks + AAS_KEYSTORE_PASS)
#   bash android/build_apk.sh debug      # debug, for the emulator
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"; A="$ROOT/android"; STAGE="$A/stage"
export JAVA_HOME="${JAVA_HOME:-$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
[ -f "$A/local.properties" ] || echo "sdk.dir=$ANDROID_HOME" > "$A/local.properties"
VER="$(python3 -c "import re;print(re.search(r'\"version\": \"([^\"]+)', open('webapp.py').read())[1])")"

VENDOR=""
for cand in "$ROOT/vendor" "$ROOT/dist/AAS mail.app/Contents/Resources/eas-bridge/vendor" \
            "$HOME/.cache/uv/git-v0/checkouts/b02ba3756ad3eeb9/"*/src; do
  [ -f "$cand/outlook_activesync_mcp/client.py" ] && { VENDOR="$cand"; break; }
done
[ -n "$VENDOR" ] || { echo "outlook_activesync_mcp not found — build the mac app once (app/build_app.sh)"; exit 1; }

rm -rf "$STAGE"; mkdir -p "$STAGE/python" "$STAGE/assets"
cp webapp.py bridge.py "$STAGE/python/"
cp -R "$VENDOR/outlook_activesync_mcp" "$STAGE/python/"
rm -f "$STAGE/python/outlook_activesync_mcp/server.py"
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp -R web "$STAGE/assets/web"

MODE="${1:-release}"
if [ "$MODE" = debug ]; then TASK=assembleDebug; OUT="$A/app/build/outputs/apk/debug/app-debug.apk"; SUF="-debug"
else TASK=assembleRelease; OUT="$A/app/build/outputs/apk/release/app-release.apk"; SUF=""; fi
(cd "$A" && ./gradlew -q "$TASK" -PaasVersion="$VER")
mkdir -p dist
cp "$OUT" "dist/AAS-mail-$VER-android$SUF.apk"
echo "dist/AAS-mail-$VER-android$SUF.apk"
