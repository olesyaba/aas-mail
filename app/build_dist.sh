#!/bin/bash
# Package AAS mail for colleagues: self-contained .app + README, no credentials.
# Output: <project>/dist/AAS-mail-*-mac.zip (override with DIST_DIR).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
VERSION="${VERSION:-$(sed -n 's/^ *"version": "\([^"]*\)".*/\1/p' "$ROOT/webapp.py" | head -1)}"  # single source: APP_META
DIST_DIR="${DIST_DIR:-$ROOT/dist}"
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

Установка (macOS 12+, Mac на Apple Silicon — M1 и новее; на Intel не запустится)
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

Или одной командой в Терминале (скачает и поставит последнюю версию):
    curl -fsSL https://raw.githubusercontent.com/olesyaba/aas-mail/main/install.sh | bash

Обновления
----------
Приходят сами, ничего ставить не нужно (ни GitHub CLI, ни входа в GitHub):
Настройки → «Обновления» → «Автоматически» (раз в 6 часов) или «Проверить сейчас».
Новая версия скачивается с github.com, проверяется контрольная сумма, приложение
перезапускается примерно за минуту. Настройки и пароли сохраняются.

Где хранятся данные
-------------------
• Пароли — в связке ключей macOS (служба eas-bridge), не в файлах.
• Настройки — ~/.config/eas-bridge/ (только на вашем Mac).
• Лог при проблемах — ~/.config/eas-bridge/webapp.log

Требования
----------
• Сеть до owa.alfabank.ru / sync.alfaops.ru (VPN, если нужен у вас в компании).
• Всё нужное (включая Python) уже внутри приложения — отдельно ставить ничего не нужно.

Контакт
-------
Mattermost: @olesya_ba
EOF

# Release notes for colleagues (source: RELEASE_NOTES.txt in the project root).
cp "$ROOT/RELEASE_NOTES.txt" "$STAGE/ЧТО_НОВОГО.txt"

ZIP="$DIST_DIR/AAS-mail-${VERSION}-mac.zip"
rm -f "$ZIP"
(
  cd "$(dirname "$STAGE")"
  # No xattrs/resource forks: they become "._*" files under plain `unzip`
  # and break the app's code signature ("sealed resource is missing").
  ditto -c -k --norsrc --noextattr --noqtn --keepParent "$(basename "$STAGE")" "$ZIP"
)
# Also copy app alone for quick local install
# Replace, never merge: files removed from the app must not linger in the copy.
rm -rf "$DIST_DIR/AAS mail.app"
cp -R "$STAGE/AAS mail.app" "$DIST_DIR/" 2>/dev/null || true

echo ""
echo "Готово для передачи:"
echo "  $ZIP"
ls -lh "$ZIP"
echo ""
echo "Содержимое пакета:"
ls -la "$STAGE"
