#!/bin/bash
# Package AAS mail for colleagues: self-contained .app + README, no credentials.
# Output: ~/Desktop/AAS-mail-1.2.0-mac.zip (or DIST_DIR).
set -euo pipefail
cd "$(dirname "$0")"
VERSION="${VERSION:-1.2.0}"
DIST_DIR="${DIST_DIR:-$HOME/Desktop}"
STAGE="$(mktemp -d)/AAS-mail-${VERSION}"
mkdir -p "$STAGE" "$DIST_DIR"

echo "==> Building bundled app…"
BUNDLE_SERVER=1 APP_NAME="AAS mail" APP_DIR="$STAGE" VERSION="$VERSION" \
  bash ./build_app.sh

cat > "$STAGE/КАК_УСТАНОВИТЬ.txt" <<'EOF'
AAS mail — локальная почта и календарь (Alfa-Bank + Alfa-Seller)
================================================================

Что внутри
----------
• Приложение «AAS mail.app» — всё нужное уже внутри (сервер + зависимости).
• Ваши пароли НЕ входят в пакет: каждый вводит свои в Настройках.
• Адреса серверов ActiveSync уже прописаны по умолчанию:
    Alfa-Bank:   https://owa.alfabank.ru/Microsoft-Server-ActiveSync
    Alfa-Seller: https://sync.alfaops.ru/Microsoft-Server-ActiveSync

Установка (macOS 12+, Apple Silicon / Intel — собрано на вашей машине)
----------------------------------------------------------------------
1. Распакуйте архив.
2. Перетащите «AAS mail.app» в «Программы» (Applications).
3. Первый запуск: правый клик → «Открыть» (Gatekeeper, т.к. без нотаризации Apple).
   Подтвердите «Открыть» в диалоге.
4. Откроются Настройки — укажите:
     • Alfa-Bank: логин вида moscow\U_XXXXX, email, пароль
     • Alfa-Seller (по желанию): email и пароль
   Серверы уже заполнены — менять не нужно, если у вас стандартные.
5. «Сохранить» → приложение переподключится.

Где хранятся данные
-------------------
• Пароли — в связке ключей macOS (служба eas-bridge), не в файлах.
• Настройки — ~/.config/eas-bridge/ (только на вашем Mac).
• Лог при проблемах — ~/.config/eas-bridge/webapp.log

Требования
----------
• Сеть до owa.alfabank.ru / sync.alfaops.ru (VPN, если нужен у вас в компании).
• Python 3.12 внутри приложения уже есть (venv); отдельно ставить ничего не нужно.

Контакт
-------
Mattermost: @olesya_ba
EOF

ZIP="$DIST_DIR/AAS-mail-${VERSION}-mac.zip"
rm -f "$ZIP"
(
  cd "$(dirname "$STAGE")"
  ditto -c -k --keepParent "$(basename "$STAGE")" "$ZIP"
)
# Also copy app alone for quick local install
cp -R "$STAGE/AAS mail.app" "$DIST_DIR/" 2>/dev/null || true

echo ""
echo "Готово для передачи:"
echo "  $ZIP"
ls -lh "$ZIP"
echo ""
echo "Содержимое пакета:"
ls -la "$STAGE"
