#!/bin/bash
# Build AAS mail for iPad from the shared sources.
#   bash ios/build_ios.sh sim       # iPad simulator build (ad-hoc «Sign to Run Locally»)
#   bash ios/build_ios.sh device    # iPad device build, free personal team (AAS_IOS_TEAM=<team id>)
# Stages webapp.py/bridge.py/vendor/web + pure-Python deps, generates the Xcode project
# with xcodegen, builds with xcodebuild. Prints the path of the built .app.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"; I="$ROOT/ios"; STAGE="$I/stage"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
MODE="${1:-sim}"
VER="$(python3 -c "import re;print(re.search(r'\"version\": \"([^\"]+)', open('webapp.py').read())[1])")"
PYSUP="https://github.com/beeware/Python-Apple-support/releases/download/3.12-b10/Python-3.12-iOS-support.b10.tar.gz"

# 1. Embedded Python (downloaded once, kept out of git).
if [ ! -d "$I/vendor/Python.xcframework" ]; then
  mkdir -p "$I/vendor"
  curl -fsSL "$PYSUP" | tar xz -C "$I/vendor" Python.xcframework
fi

# 2. The shared server + web UI, and pure-Python dependencies.
VENDOR=""
for cand in "$ROOT/vendor" "$ROOT/dist/AAS mail.app/Contents/Resources/eas-bridge/vendor" \
            "$HOME/Applications/AAS mail.app/Contents/Resources/eas-bridge/vendor" \
            "$HOME/.cache/uv/git-v0/checkouts/b02ba3756ad3eeb9/"*/src; do
  [ -f "$cand/outlook_activesync_mcp/client.py" ] && { VENDOR="$cand"; break; }
done
[ -n "$VENDOR" ] || { echo "outlook_activesync_mcp not found — build the mac app once (app/build_app.sh)"; exit 1; }
rm -rf "$STAGE"; mkdir -p "$STAGE/app" "$STAGE/app_packages"
cp webapp.py bridge.py ios/python/*.py "$STAGE/app/"
cp -R "$VENDOR/outlook_activesync_mcp" "$STAGE/app/"
rm -f "$STAGE/app/outlook_activesync_mcp/server.py"
cp -R web "$STAGE/app/web"
python3 -m pip install -q --disable-pip-version-check --target "$STAGE/app_packages" --no-deps \
  --only-binary=:all: --platform any --python-version 3.12 \
  requests urllib3 idna charset-normalizer certifi python-dateutil six
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +

# 3. Xcode project + build.
(cd "$I" && xcodegen generate -q)
OUT="$I/build"
if [ "$MODE" = device ]; then
  : "${AAS_IOS_TEAM:?set AAS_IOS_TEAM to the personal team id}"
  xcodebuild -quiet -project "$I/AASMail.xcodeproj" -scheme AASMail -configuration Release \
    -destination 'generic/platform=iOS' -derivedDataPath "$OUT" -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$AAS_IOS_TEAM" MARKETING_VERSION="$VER" build
  echo "$OUT/Build/Products/Release-iphoneos/AASMail.app"
else
  xcodebuild -quiet -project "$I/AASMail.xcodeproj" -scheme AASMail -configuration Debug \
    -destination 'generic/platform=iOS Simulator' -derivedDataPath "$OUT" \
    ARCHS=arm64 ONLY_ACTIVE_ARCH=YES CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY=- DEVELOPMENT_TEAM= MARKETING_VERSION="$VER" build
  echo "$OUT/Build/Products/Debug-iphonesimulator/AASMail.app"
fi
