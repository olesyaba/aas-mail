#!/bin/bash
# Publish the current version as a GitHub Release so installed apps can update:
#   app/release.sh
# Version comes from webapp.py APP_META; notes are its section of RELEASE_NOTES.txt.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="olesyaba/aas-mail"
VERSION="$(sed -n 's/^ *"version": "\([^"]*\)".*/\1/p' webapp.py | head -1)"
TAG="v$VERSION"
ZIP="dist/AAS-mail-$VERSION-mac.zip"
APK="dist/AAS-mail-$VERSION-android.apk"   # optional: android/build_apk.sh

gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 && { echo "$TAG уже опубликован — поднимите версию в webapp.py"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Есть незакоммиченные изменения — сначала commit/push"; exit 1; }

bash tests/run_tests.sh
[ -f "$ZIP" ] || bash app/build_dist.sh
python3 tests/privacy_scan.py "$ZIP"

NOTES="$(mktemp)"
awk -v v="$VERSION" '
  $0 ~ "^AAS mail " v " " {on=1; next}
  on && /^Ранее, в / {exit}
  on && !/^=+$/ {print}' RELEASE_NOTES.txt > "$NOTES"

git push origin HEAD
ASSETS=("$ZIP"); [ -f "$APK" ] && ASSETS+=("$APK")
UNI="dist/AAS-mail-$VERSION-android-universal.apk"; [ -f "$UNI" ] && ASSETS+=("$UNI")
gh release create "$TAG" "${ASSETS[@]}" --repo "$REPO" --target "$(git rev-parse HEAD)" \
  --title "AAS mail $VERSION" --notes-file "$NOTES"
rm -f "$NOTES"
echo "Опубликовано: $TAG — в режиме «Автоматически» приложения обновятся сами в течение 6 часов; вручную — Настройки → Обновления → «Проверить сейчас»."
