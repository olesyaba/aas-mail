#!/bin/bash
# Publish the current version as a GitHub Release so installed apps can update:
#   app/release.sh
# Version comes from webapp.py APP_META; notes are its section of RELEASE_NOTES.txt.
# Assets: the Mac zip, plus the Fold and universal APKs when built (android/build_apk.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="olesyaba/aas-mail"
VERSION="$(sed -n 's/^ *"version": "\([^"]*\)".*/\1/p' webapp.py | head -1)"
TAG="v$VERSION"
ZIP="dist/AAS-mail-$VERSION-mac.zip"

gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 && { echo "$TAG уже опубликован — поднимите версию в webapp.py"; exit 1; }
# Untracked files (notes, logs, promo renders) do not matter: builds take tracked files only.
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "Есть незакоммиченные изменения — сначала commit"; exit 1; }
grep -q "^AAS mail $VERSION " RELEASE_NOTES.txt || { echo "Нет раздела «AAS mail $VERSION» в RELEASE_NOTES.txt"; exit 1; }

bash tests/run_tests.sh py js swift
[ -f "$ZIP" ] || bash app/build_dist.sh
WINZIP="dist/AAS-mail-$VERSION-win.zip"   # Windows apps update from this asset
[ -f "$WINZIP" ] || bash windows/build_win.sh
ASSETS=("$ZIP" "$WINZIP")
for apk in "dist/AAS-mail-$VERSION-android.apk" "dist/AAS-mail-$VERSION-android-universal.apk"; do
  [ -f "$apk" ] && ASSETS+=("$apk")
done
python3 tests/privacy_scan.py "${ASSETS[@]}"

NOTES="$(mktemp)"; trap 'rm -f "$NOTES"' EXIT
awk -v v="$VERSION" '
  $0 ~ "^AAS mail " v " " {on=1; next}
  on && /^Ранее, в / {exit}
  on && !/^=+$/ {print}' RELEASE_NOTES.txt > "$NOTES"

git push origin HEAD
# GitHub's TLS handshake times out now and then (1.2.34): try a few times.
for i in 1 2 3; do
  gh release create "$TAG" "${ASSETS[@]}" --repo "$REPO" --target "$(git rev-parse HEAD)" \
    --title "AAS mail $VERSION" --notes-file "$NOTES" && break
  [ "$i" = 3 ] && { echo "Релиз не создан"; exit 1; }
  sleep 5
  gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 && gh release delete "$TAG" --repo "$REPO" --yes --cleanup-tag
done

# What GitHub serves must be byte-for-byte what was built and scanned.
for f in "${ASSETS[@]}"; do
  want="$(shasum -a 256 "$f" | cut -d' ' -f1)"
  got="$(gh release view "$TAG" --repo "$REPO" --json assets \
    --jq ".assets[] | select(.name == \"$(basename "$f")\") | .digest" | sed 's/^sha256://')"
  [ "$want" = "$got" ] || { echo "Контрольная сумма $(basename "$f") не совпала: $got"; exit 1; }
done
echo "Опубликовано: $TAG (${#ASSETS[@]} файла, суммы сверены) — в режиме «Автоматически» приложения обновятся сами в течение 6 часов; вручную — Настройки → Обновления → «Проверить сейчас»."
