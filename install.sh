#!/bin/bash
# Install (or reinstall) the latest AAS mail from the private GitHub repo's Releases.
#   ./install.sh            — from a clone of the repo
# Needs access to github.com/olesyaba/aas-mail. Later updates come from the app itself
# (button «Обновить до …» / Настройки → О приложении).
set -euo pipefail
REPO="olesyaba/aas-mail"
DEST="$HOME/Applications"
APP="$DEST/AAS mail.app"

[ "$(uname -m)" = "arm64" ] || { echo "AAS mail работает только на Mac с Apple Silicon (M1 и новее)."; exit 1; }

GH="$(command -v gh || true)"
[ -z "$GH" ] && [ -x /opt/homebrew/bin/gh ] && GH=/opt/homebrew/bin/gh
if [ -z "$GH" ]; then
  if command -v brew >/dev/null || [ -x /opt/homebrew/bin/brew ]; then
    echo "==> Устанавливаю GitHub CLI (gh)…"
    "$(command -v brew || echo /opt/homebrew/bin/brew)" install gh
    GH="$(command -v gh || echo /opt/homebrew/bin/gh)"
  else
    echo "Нужен GitHub CLI: установите Homebrew (https://brew.sh) и выполните: brew install gh"
    echo "или скачайте gh с https://cli.github.com, затем запустите install.sh снова."
    exit 1
  fi
fi

if ! "$GH" auth status >/dev/null 2>&1; then
  echo "==> Вход в GitHub (один раз; нужен доступ к $REPO)…"
  "$GH" auth login --hostname github.com --git-protocol https --web
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "==> Скачиваю последнюю версию…"
"$GH" release download --repo "$REPO" --pattern '*-mac.zip' --dir "$TMP" \
  || { echo "Не удалось скачать релиз: проверьте доступ к $REPO (gh auth status)."; exit 1; }
( cd "$TMP" && unzip -q ./*-mac.zip )
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
