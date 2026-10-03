#!/bin/bash
# Install AAS mail on every iPhone / iPad paired with this Mac (cable or the same Wi-Fi; unlocked,
# «Доверять», Developer Mode on). Each device is built for once so Xcode adds it to the team's profile.
#   AAS_IOS_TEAM=<team id> bash ios/install_ios.sh
# Free personal team: the app stops launching after 7 days — run this again to renew.
set -euo pipefail
cd "$(dirname "$0")/.."
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
: "${AAS_IOS_TEAM:?set AAS_IOS_TEAM to the team id}"
VER="$(python3 -c "import re;print(re.search(r'\"version\": \"([^\"]+)', open('webapp.py').read())[1])")"

JSON="$(mktemp)"; trap 'rm -f "$JSON"' EXIT
xcrun devicectl list devices --json-output "$JSON" >/dev/null
UDIDS="$(python3 - "$JSON" <<'EOF'
import json, sys
for d in json.load(open(sys.argv[1]))["result"]["devices"]:
    hw, conn = d.get("hardwareProperties", {}), d.get("connectionProperties", {})
    # reality is empty for a phone seen over Wi-Fi: anything that is not a simulator.
    if hw.get("reality") != "simulated" and conn.get("pairingState") == "paired" \
            and d.get("deviceProperties", {}).get("bootState") == "booted":
        print(hw["udid"], d["deviceProperties"].get("name", "?"), "AASMailPad" if hw.get("deviceType") == "iPad" else "AASMailPhone", sep="\t")
EOF
)"
[ -n "$UDIDS" ] || { echo "Нет подключённых iPhone/iPad: подключите кабелем, разблокируйте, «Доверять»"; exit 1; }

bash ios/build_ios.sh device both >/dev/null   # stage + project once
while IFS=$'\t' read -r UDID NAME T; do
  APP="ios/build/Build/Products/Release-iphoneos/$T.app"
  echo "==> $NAME ($UDID): $T"
  # A build for this very device registers it in the profile (free team: Xcode does it).
  if ! xcodebuild -quiet -project ios/AASMail.xcodeproj -scheme "$T" -configuration Release \
      -destination "id=$UDID" -destination-timeout 30 -derivedDataPath ios/build -allowProvisioningUpdates \
      DEVELOPMENT_TEAM="$AAS_IOS_TEAM" MARKETING_VERSION="$VER" build 2>&1 | grep -E "error:|Developer Mode" | grep -v IDERunDestination; then :; fi
  if OUT="$(xcrun devicectl device install app --device "$UDID" "$APP" 2>&1)"; then echo "    установлено"
  elif grep -q "locked" <<< "$OUT"; then echo "    не установлено — устройство заблокировано: разблокируйте его и запустите снова"
  elif grep -qi "developer mode" <<< "$OUT"; then echo "    не установлено — включите Настройки → Конфиденциальность и безопасность → Режим разработчика"
  else echo "    не установлено: $(grep -m1 -E "ERROR|error" <<< "$OUT" | sed 's/^ *//')"; fi
done <<< "$UDIDS"
