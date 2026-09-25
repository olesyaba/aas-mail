#!/bin/bash
# Install (or reinstall) the latest AAS mail from the GitHub Releases of olesyaba/aas-mail.
#   curl -fsSL https://raw.githubusercontent.com/olesyaba/aas-mail/main/install.sh | bash
#   ./install.sh            — or from a clone of the repo
# Needs only access to github.com (the repo is public). Later updates come from the app itself
# (Настройки → Обновления: автоматически или по запросу).
set -euo pipefail
REPO="olesyaba/aas-mail"
DEST="$HOME/Applications"
APP="$DEST/AAS mail.app"

[ "$(uname -m)" = "arm64" ] || { echo "AAS mail работает только на Mac с Apple Silicon (M1 и новее)."; exit 1; }

# The repo is public: the latest release is fetched over plain HTTPS, no GitHub login.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "==> Ищу последнюю версию…"
TAG="$(curl -fsSI "https://github.com/$REPO/releases/latest" | awk -F/ 'tolower($1) ~ /^location:/ {print $NF}' | tr -d '\r')"
case "$TAG" in v*) ;; *) echo "Не удалось узнать последнюю версию: нет связи с github.com."; exit 1 ;; esac
ZIPNAME="AAS-mail-${TAG#v}-mac.zip"
echo "==> Скачиваю $TAG…"
curl -fL --retry 3 --progress-bar -o "$TMP/$ZIPNAME" "https://github.com/$REPO/releases/download/$TAG/$ZIPNAME" \
  || { echo "Не удалось скачать $ZIPNAME с github.com."; exit 1; }
# Check the archive against the SHA-256 GitHub publishes for it (when the API answers).
WANT="$(curl -fsS "https://api.github.com/repos/$REPO/releases/tags/$TAG" 2>/dev/null \
  | grep -o '"digest": *"sha256:[0-9a-f]*"' | head -1 | sed 's/.*sha256://; s/"//')"
if [ -n "$WANT" ]; then
  GOT="$(shasum -a 256 "$TMP/$ZIPNAME" | cut -d' ' -f1)"
  [ "$GOT" = "$WANT" ] || { echo "Архив повреждён (контрольная сумма не совпала). Запустите install.sh ещё раз."; exit 1; }
fi
( cd "$TMP" && unzip -q "./$ZIPNAME" )
NEW="$(find "$TMP" -maxdepth 3 -name 'AAS mail.app' -type d | head -1)"
[ -n "$NEW" ] || { echo "В архиве нет AAS mail.app"; exit 1; }
VER="$(defaults read "$NEW/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo '?')"

if pgrep -f "$APP/Contents/MacOS/" >/dev/null; then
  echo "==> Закрываю запущенную AAS mail…"
  osascript -e "tell application \"$APP\" to quit" >/dev/null 2>&1 || true
  for _ in $(seq 1 40); do pgrep -f "$APP/Contents/MacOS/" >/dev/null || break; sleep 0.5; done
  pkill -f "$APP/Contents/MacOS/" 2>/dev/null || true
fi

mkdir -p "$DEST"
rm -rf "$APP"
ditto "$NEW" "$APP"
echo "==> Установлена AAS mail $VER в $APP"
open "$APP"
echo "Готово. Пароли вводятся в приложении: Настройки → Аккаунты. Обновления приложение найдёт само."
