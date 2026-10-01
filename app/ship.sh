#!/bin/bash
# The whole release in one go, after the code is committed and RELEASE_NOTES.txt has a
# section «AAS mail <version> — что нового»:
#   app/ship.sh 1.2.41
# Bumps webapp.py, builds the Mac zip and both APKs, commits «Ship <version>», publishes
# (app/release.sh: tests, privacy scan, push, release, checksums), installs on this Mac
# and on any paired iPhone/iPad, syncs the Obsidian notes, keeps the last 3 builds in dist/.
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:?версия, например: app/ship.sh 1.2.41}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Версия вида 1.2.41"; exit 1; }
grep -q "^AAS mail $V — что нового" RELEASE_NOTES.txt || { echo "Сначала раздел «AAS mail $V — что нового» в RELEASE_NOTES.txt"; exit 1; }
# Only the bump and the notes may be uncommitted: everything else ships as committed.
OTHER="$(git status --porcelain --untracked-files=no | grep -v -E ' (webapp\.py|RELEASE_NOTES\.txt)$' || true)"
[ -z "$OTHER" ] || { echo "Незакоммиченные изменения:"; echo "$OTHER"; exit 1; }

sed -i '' "s/\"version\": \"[0-9.]*\"/\"version\": \"$V\"/" webapp.py
grep -q "\"version\": \"$V\"" webapp.py || { echo "Версия в webapp.py не обновилась"; exit 1; }

echo "==> Сборка $V"
bash app/build_dist.sh >/dev/null
export AAS_KEYSTORE_PASS="${AAS_KEYSTORE_PASS:-$(security find-generic-password -a aas -s aas-android-keystore -w)}"
bash android/build_apk.sh >/dev/null
bash android/build_apk.sh universal >/dev/null

git add webapp.py RELEASE_NOTES.txt
git diff --cached --quiet || git commit -q -m "Ship $V."
bash app/release.sh

echo "==> Установка"
bash install.sh 2>&1 | grep -E "Установлена|ошибка|Ошибка" || true
AAS_IOS_TEAM="${AAS_IOS_TEAM:-394TXD942T}" bash ios/install_ios.sh 2>&1 | grep -E "==>|установлено|не установлено|Нет подключённых" || true
[ -f tools/sync-obsidian.py ] && python3 tools/sync-obsidian.py || true

# dist/: the last 3 versions; older ones are on GitHub Releases.
python3 - <<'EOF'
import re, shutil
from pathlib import Path
dist = Path("dist")
ver = lambda p: tuple(map(int, re.match(r"AAS-mail-(\d+)\.(\d+)\.(\d+)", p.name).groups()))
olds = [p for p in dist.iterdir() if re.match(r"AAS-mail-\d+\.\d+\.\d+", p.name)]
keep = sorted({ver(p) for p in olds})[-3:]
for p in olds:
    if ver(p) not in keep:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
EOF
echo "Готово: $V"
