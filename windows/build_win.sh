#!/bin/bash
# Build dist/AAS-mail-X.Y.Z-win.zip on the Mac: the official embeddable CPython for
# Windows x64 + win_amd64 wheels + the same server/web files as the Mac bundle.
# Unpack anywhere on Windows 10/11, run «AAS mail.cmd». No installer, no admin rights.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VERSION="$(sed -n 's/^ *"version": "\([^"]*\)".*/\1/p' webapp.py | head -1)"
PY_VER=3.12.10   # the last 3.12 with an embeddable zip on python.org
CACHE="$ROOT/dist/.cache"
OUT="$ROOT/dist/win/AAS mail"
ZIP="$ROOT/dist/AAS-mail-$VERSION-win.zip"

mkdir -p "$CACHE"
EMBED="$CACHE/python-$PY_VER-embed-amd64.zip"
[ -f "$EMBED" ] || curl -fsSL -o "$EMBED" "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-embed-amd64.zip"

rm -rf "$ROOT/dist/win"; mkdir -p "$OUT/python" "$OUT/vendor" "$OUT/windows"
unzip -q "$EMBED" -d "$OUT/python"
# The embeddable interpreter ignores PYTHONPATH and reads its path from this file.
printf 'python312.zip\n.\n..\n..\\site-packages\n..\\vendor\n..\\windows\nimport site\n' > "$OUT/python/python312._pth"

# Wheels for Windows, fetched from here: --platform/--only-binary never runs their build.
# pip evaluates sys_platform markers for THIS Mac, so win32-only deps are listed by hand
# (pywin32-ctypes: keyring's Credential Manager backend).
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
"$PY" -m pip install --quiet --target "$OUT/site-packages" --platform win_amd64 --python-version 3.12 \
  --implementation cp --only-binary=:all: \
  "pythonnet>=3" "bottle" "typing_extensions" "six" "Pillow>=10" "keyring>=25" "pywin32-ctypes" \
  "requests" "urllib3" "python-dateutil"
# --no-deps: proxy_tools (pywebview's) is an sdist only, and pystray's Mac-only deps made pip
# fall back to pystray 0.10 (no notifications). pystray is pinned: main.notify() uses its _message.
"$PY" -m pip install --quiet --target "$OUT/site-packages" --no-deps "pywebview>=5,<6" "proxy_tools" "pystray==0.19.5"

cp webapp.py bridge.py "$OUT/"
git ls-files -z web | xargs -0 tar -cf - | tar -xf - -C "$OUT"   # tracked files only
cp windows/main.py windows/windows_entry.py windows/self_update.ps1 "$OUT/windows/"
mkdir -p "$OUT/windows/sounds"
for f in app/Sounds/*.caf; do afconvert -f WAVE -d LEI16 "$f" "$OUT/windows/sounds/$(basename "${f%.caf}").wav"; done
cp app/TrayLogoSource.png "$OUT/windows/icon.png"
MCP_SRC="$(ls -d "$HOME/.cache/uv/git-v0/checkouts/b02ba3756ad3eeb9/"*/src/outlook_activesync_mcp | head -1)"
[ -f "$MCP_SRC/client.py" ] || { echo "outlook_activesync_mcp not found — run app/build_app.sh once"; exit 1; }
cp -R "$MCP_SRC" "$OUT/vendor/"
rm -f "$OUT/vendor/outlook_activesync_mcp/server.py"
find "$OUT" -name "__pycache__" -type d -prune -exec rm -rf {} +

printf '@echo off\r\nstart "" "%%~dp0python\\pythonw.exe" -X utf8 "%%~dp0windows\\main.py"\r\n' > "$OUT/AAS mail.cmd"
cat > "$OUT/КАК_ЗАПУСТИТЬ.txt" <<EOF
AAS mail $VERSION для Windows 10/11 — тестовая сборка

1. Распакуйте архив целиком в любую папку, например «Документы\\AAS mail».
2. Запустите «AAS mail.cmd». Если Windows покажет «Windows защитила компьютер»:
   «Подробнее» → «Выполнить в любом случае» (сборка пока не подписана).
3. Введите логин и пароль в Настройках. Пароли хранятся в «Диспетчере учётных данных» Windows.

Закрытое окно продолжает работать в трее (значок у часов): «Открыть почту», «Календарь», «Выход».
Нужен Microsoft Edge WebView2 (есть в Windows 11 и в обновлённой Windows 10).
Если окно не открылось или что-то не так — пришлите разработчику файлы
%APPDATA%\\eas-bridge\\webapp.log и crash.log (если он есть) из той же папки.
EOF
sed -i '' 's/$/\r/' "$OUT/КАК_ЗАПУСТИТЬ.txt"

rm -f "$ZIP"
(cd "$ROOT/dist/win" && zip -qr -X "$ZIP" "AAS mail")
python3 tests/privacy_scan.py "$ZIP"
echo "→ $ZIP ($(du -h "$ZIP" | cut -f1))"
