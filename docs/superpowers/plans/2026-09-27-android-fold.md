# AAS mail для Android (Galaxy Z Fold 8) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** APK для личного Fold 8 с текущими возможностями AAS mail 1.2.19: тот же Python-сервер и тот же `web/` внутри Kotlin-оболочки, одна панель на внешнем экране, уведомления о письмах и встречах при закрытом приложении.

**Architecture:** Kotlin-приложение запускает `webapp.main()` через Chaquopy в потоке своего процесса (127.0.0.1:8780) и показывает его в WebView. Мосты Swift (`window.webkit.messageHandlers.*`) эмулируются JS-шимом → `@JavascriptInterface`, поэтому `web/` почти не меняется. Фон — WorkManager (письма, 15 мин) + AlarmManager (встречи); оба говорят с сервером так же, как `app/TrayAPIClient.swift` (POST + `X-Tok`).

**Tech Stack:** Kotlin 2.0.21, Android Gradle Plugin 8.7.3, Gradle wrapper 8.11.1, JDK 17 (brew `openjdk@17`), Chaquopy 16.1.0 (Python 3.12), compileSdk/targetSdk 35, minSdk 31, androidx.webkit 1.12.1, androidx.work 2.10.0, JUnit 4.13.2.

**Spec:** `docs/superpowers/specs/2026-09-27-android-fold-design.md`

## Global Constraints

- Поведение macOS-сборки не меняется: все правки общего кода (`webapp.py`, `bridge.py`, `web/`) — no-op на Mac; `bash tests/run_tests.sh` зелёный после каждой задачи.
- Общий код не форкается: `webapp.py`, `bridge.py`, `web/`, `vendor/outlook_activesync_mcp` копируются в APK скриптом сборки, в `android/` их копий в git нет.
- Сервер слушает только `127.0.0.1`; `X-Tok` и `_guard` без изменений.
- Пароли — только в Android Keystore (AES-GCM), никогда открытым текстом в `config.json` или логах.
- `android:allowBackup="false"`, данные — во внутренней памяти приложения (`filesDir`).
- Одна панель — при ширине окна `< 600` CSS-px; цели касания ≥ 44px при `(pointer: coarse)`.
- Напоминание о встрече — за `prefs.reminder_minutes` (по умолчанию 5; 0 = выкл).
- Уведомление «нет связи» — только после 3 неудач подряд по аккаунту.
- Маркеры аккаунтов: 🔴 Alfa-Bank (`main`), 🟢 Seller (`seller`), как в `TrayNotificationPlan.swift`.
- Package: `ru.olesyaba.aasmail`. Имя APK: `dist/AAS-mail-<version>-android.apk`.
- Код/идентификаторы — по-английски, тексты UI и комментарии — как в окружающем коде (русские строки UI).

## Review Focus

1. **Другое приложение на телефоне читает `GET /` и крадёт токен.** На Android любой процесс может постучаться в 127.0.0.1:8780, а страница содержит `X-Tok`. Ожидание: без секретного заголовка оболочки `GET /` → 403. Тест — Task 3 (`test_page_key_*`).
2. **Складывание с открытым письмом, потом «назад».** Ожидание: письмо на весь экран, «назад» → список, а не выход из приложения. Тест — Task 5 (`paneFor`, `needsBackEntry`).
3. **`/tmp` на Android не существует.** Клиент ActiveSync по умолчанию пишет вложения/переполнения в `/tmp/...`. Ожидание: пути берутся из окружения. Тест — Task 2.
4. **Первый синк после установки не должен вывалить уведомления на всё, что уже лежит во Входящих.** Ожидание: первый проход только запоминает `item_id`. Тест — Task 6 (`firstRunIsSilent`).
5. **Отменённая/перенесённая встреча не должна звонить по старому времени.** Ожидание: будильник снимается/переставляется. Тест — Task 6 (`cancelsDroppedAndCancelled`, `movedMeetingGetsNewKey`).

---

## Структура файлов

```
bridge.py                                  (modify) пути вложений/переполнений из env
webapp.py                                  (modify) EAS_MAIL_PAGE_KEY для GET /
web/index.html                             (modify) одна панель, back, deep link #open=
web/ui-kit/app.css                         (modify) @media <600px, pointer:coarse, safe-area
tests/python/test_android_entry.py         (create)
tests/python/test_http.py                  (modify) page key
tests/python/test_logic.py                 (modify) env-пути bridge
tests/js/ui_logic.test.mjs                 (modify) paneFor / needsBackEntry / parseOpenHash
android/
  .gitignore
  build_apk.sh                             staging + gradle + копия в dist/
  settings.gradle.kts, build.gradle.kts, gradle.properties, gradlew*, gradle/wrapper/*
  app/build.gradle.kts
  app/src/main/AndroidManifest.xml
  app/src/main/python/android_entry.py     env + keychain→Secrets + WEB + update
  app/src/main/res/xml/file_paths.xml
  app/src/main/res/mipmap-*/ic_launcher.png (из app/AppIcon.iconset)
  app/src/main/java/ru/olesyaba/aasmail/
    Secrets.kt        Keystore AES-GCM + SharedPreferences
    PyServer.kt       старт Python, ожидание порта, токен, page key
    Api.kt            POST /api/* с X-Tok
    MainActivity.kt   WebView, шим мостов, внешние ссылки, back, deep link
    NativeBridge.kt   @JavascriptInterface post(name, json)
    Attachments.kt    open / as / all через SAF и FileProvider
    Notifier.kt       каналы и уведомления (письма, встречи, связь)
    NewMail.kt        чистая функция diff новых писем
    MeetingPlan.kt    порт TrayNotificationPlan + joinUrl
    MeetingAlarms.kt  AlarmManager + MeetingReceiver
    SyncWorker.kt     периодический синк
    Permissions.kt    уведомления, точные будильники, батарея
  app/src/test/java/ru/olesyaba/aasmail/
    NewMailTest.kt, MeetingPlanTest.kt
  app/src/androidTest/java/ru/olesyaba/aasmail/SecretsTest.kt
```

---

### Task 1: Спайк — пустит ли Exchange новое устройство (ГЕЙТ)

Проверяем риск №1 до любого кода. Всё в scratchpad, в git ничего не попадает.

**Files:** только `$SCRATCH/eas_newdevice.py` (одноразовый).

**Interfaces:** Consumes: `outlook_activesync_mcp.client.EasClient`, `config.load_settings` из `dist/AAS mail.app/.../eas-bridge/vendor`. Produces: вердикт «ok / карантин» по каждому аккаунту.

- [ ] **Step 1: Написать проверку.** Новый `device_id`, `DeviceType` как у телефона, FolderSync по каждому аккаунту из `~/.config/eas-bridge/config.json`; пароль — из Keychain.

```python
# $SCRATCH/eas_newdevice.py — throwaway: does Exchange accept a brand-new ActiveSync device?
import json, secrets, subprocess, sys, tempfile, pathlib
from outlook_activesync_mcp.client import EasClient
from outlook_activesync_mcp.config import load_settings
from outlook_activesync_mcp.commands import folders

cfg = json.loads(pathlib.Path("~/.config/eas-bridge/config.json").expanduser().read_text())
accts = {"main": cfg, "seller": cfg.get("second") or {}}
for aid, a in accts.items():
    if not a.get("username"):
        continue
    pw = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", aid, "-s", "eas-bridge", "-w"],
                        capture_output=True, text=True).stdout.strip()
    tmp = tempfile.mkdtemp()
    env = {"EXCHANGE_USERNAME": a["username"], "EXCHANGE_PASSWORD": pw, "EAS_URL": a["url"],
           "EAS_DEVICE_ID": secrets.token_hex(16).upper(), "EAS_STATE_FILE": f"{tmp}/state.json",
           "EAS_ATTACHMENT_DIR": tmp, "EAS_OVERFLOW_DIR": tmp}
    c = EasClient(load_settings(env))
    c.s.device_type = "Android"
    try:
        res = folders.handle(c, "list")
        print(aid, "OK", "folders:", len(res.get("items", [])) if isinstance(res, dict) else res)
    except Exception as e:  # noqa: BLE001
        print(aid, "FAIL", type(e).__name__, e)
```

- [ ] **Step 2: Запустить.**

```bash
V="dist/AAS mail.app/Contents/Resources/eas-bridge"
PYTHONPATH="$V/vendor:$V/site-packages" "$V/python/bin/python3" "$SCRATCH/eas_newdevice.py"
```

Expected: по обоим аккаунтам `OK folders: N` (N > 0). Если `c.s.device_type` не существует (AttributeError) — смотреть `grep -n device_type vendor/outlook_activesync_mcp/transport.py` и выставить атрибут там, где его читает `transport.py:106`.

- [ ] **Step 3: Решение.** `OK` у обоих → дальше. `FAIL` со статусом 126/129/177, HTTP 449/403, «Quarantined»/«blocked» → **СТОП**: сообщить пользователю, какой аккаунт в карантине; дальнейшие задачи только для незаблокированного аккаунта или после одобрения устройства IT банка. Сетевая ошибка по Alfa → проверить маршрут (Karing, память `karing-alfa-routing`) и повторить. Новые устройства видны в OWA → Параметры → Мобильные устройства; удалить тестовое устройство оттуда после проверки.

---

### Task 2: Пути вложений и переполнений из окружения (`bridge.py`)

**Files:**
- Modify: `bridge.py:31-37` (dict `env` в `EasBackend.__init__`)
- Test: `tests/python/test_logic.py` (новый класс в конце файла)

**Interfaces:** Produces: `EasBackend` читает `EAS_ATTACHMENT_DIR` и `EAS_OVERFLOW_DIR` из `os.environ`, по умолчанию `/tmp/attachments` и `/tmp/outlook-activesync-overflow` (как сейчас на Mac). Task 4 выставляет их в `filesDir`.

- [ ] **Step 1: Падающий тест** (в конец `tests/python/test_logic.py`):

```python
class BridgePathsTest(unittest.TestCase):
    def _settings(self):
        import bridge
        with mock.patch("outlook_activesync_mcp.client.EasClient") as ctor:
            bridge.EasBackend({"username": "u", "password": "p", "url": "https://x/", "device_id": "D"})
        return ctor.call_args.args[0]

    def test_defaults_stay_in_tmp_on_mac(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EAS_ATTACHMENT_DIR", None); os.environ.pop("EAS_OVERFLOW_DIR", None)
            s = self._settings()
        self.assertEqual(s.attachment_dir, "/tmp/attachments")
        self.assertEqual(s.overflow_dir, "/tmp/outlook-activesync-overflow")

    def test_env_overrides_for_android(self):
        with mock.patch.dict(os.environ, {"EAS_ATTACHMENT_DIR": "/data/a", "EAS_OVERFLOW_DIR": "/data/o"}):
            s = self._settings()
        self.assertEqual((s.attachment_dir, s.overflow_dir), ("/data/a", "/data/o"))
```

- [ ] **Step 2: Убедиться, что падает.** `UNITTEST_ARGS="-v -k BridgePaths" bash tests/run_tests.sh py` → FAIL `test_env_overrides_for_android` (`'/tmp/attachments' != '/data/a'`).

- [ ] **Step 3: Реализация** — в `bridge.py` дополнить dict `env`:

```python
            "EAS_MAX_RESPONSE_TOKENS": "1000000",
            # Android has no /tmp: the shell points these into the app's private storage.
            "EAS_ATTACHMENT_DIR": os.environ.get("EAS_ATTACHMENT_DIR", "/tmp/attachments"),
            "EAS_OVERFLOW_DIR": os.environ.get("EAS_OVERFLOW_DIR", "/tmp/outlook-activesync-overflow"),
```

- [ ] **Step 4: Тесты.** `bash tests/run_tests.sh py` → PASS.
- [ ] **Step 5: Commit.** `git add bridge.py tests/python/test_logic.py && git commit -m "bridge: attachment/overflow dirs from env (Android has no /tmp)"`

---

### Task 3: Ключ страницы для `GET /` и Python-вход для Android

**Files:**
- Modify: `webapp.py` (рядом с `TOKEN = ...` в строке 35; ветка `if u.path in ("/", "/index.html")` в `do_GET`, ~строка 2906)
- Create: `android/app/src/main/python/android_entry.py`
- Test: `tests/python/test_http.py` (новые тесты), `tests/python/test_android_entry.py`

**Interfaces:**
- Produces (webapp): env `EAS_MAIL_PAGE_KEY`; если задан, `GET /` и `GET /index.html` без заголовка `X-Page-Key: <ключ>` → 403. Не задан → как сейчас.
- Produces (android_entry): `run(files_dir: str, cache_dir: str, page_key: str) -> None` (блокирует, крутит `webapp.main()`); `install(webapp, web_dir: Path, secrets) -> None`, где `secrets` имеет `get(account: str, service: str) -> str | None` и `set(account: str, service: str, password: str) -> None`.
- Consumes: Task 2 (env-пути). Kotlin-класс `ru.olesyaba.aasmail.Secrets` (Task 4) с `@JvmStatic get(account, service): String?` и `@JvmStatic set(account, service, password)`.

- [ ] **Step 1: Падающие тесты ключа страницы.** Посмотреть в `tests/python/test_http.py`, как поднимается тестовый сервер и делается GET (хелпер в `harness.py`); по тому же образцу добавить:

```python
class PageKeyTest(unittest.TestCase):
    # Server fixture: same as the other HTTP tests in this file.
    def test_page_key_unset_serves_page(self):
        with mock.patch.object(webapp, "PAGE_KEY", ""):
            self.assertEqual(self.get("/").status, 200)

    def test_page_key_required_when_set(self):
        with mock.patch.object(webapp, "PAGE_KEY", "k1"):
            self.assertEqual(self.get("/").status, 403)
            self.assertEqual(self.get("/index.html", headers={"X-Page-Key": "bad"}).status, 403)
            self.assertEqual(self.get("/", headers={"X-Page-Key": "k1"}).status, 200)
```

`self.get` — существующий GET-хелпер файла; если он не принимает `headers`, добавить параметр `headers: dict | None = None` и передавать в `http.client` запрос.

- [ ] **Step 2: Убедиться, что падают.** `UNITTEST_ARGS="-v -k PageKey" bash tests/run_tests.sh py` → FAIL (`AttributeError: PAGE_KEY`).

- [ ] **Step 3: Реализация в `webapp.py`.** После `TOKEN = secrets.token_urlsafe(24)`:

```python
# Android: any app on the phone can reach 127.0.0.1, and the page embeds TOKEN.
# The shell sends this key with the page request; unset on the Mac (no change).
PAGE_KEY = os.environ.get("EAS_MAIL_PAGE_KEY", "")
```

В `do_GET`, первой строкой внутри `if u.path in ("/", "/index.html"):`:

```python
            if PAGE_KEY and not secrets.compare_digest(self.headers.get("X-Page-Key", ""), PAGE_KEY):
                return self.send_error(403)
```

- [ ] **Step 4: Тесты.** `bash tests/run_tests.sh py` → PASS.

- [ ] **Step 5: Падающий тест входа** — `tests/python/test_android_entry.py`:

```python
"""android_entry.install: Keychain → Android Secrets, web dir, no self-update on Android."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from harness import TMP, webapp

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "android/app/src/main/python"))
import android_entry  # noqa: E402


class FakeSecrets:
    def __init__(self):
        self.d = {}
    def get(self, account, service):
        return self.d.get((account, service))
    def set(self, account, service, password):
        self.d[(account, service)] = password


class InstallTest(unittest.TestCase):
    def setUp(self):
        names = ("WEB", "_keychain_get", "_keychain_set", "update_check")
        self.saved = {n: getattr(webapp, n) for n in names}
        self.sec = FakeSecrets()

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(webapp, n, v)

    def test_passwords_go_through_secrets(self):
        android_entry.install(webapp, TMP / "web", self.sec)
        webapp._keychain_set("seller", "pw1")
        self.assertEqual(self.sec.d, {("seller", "eas-bridge"): "pw1"})
        self.assertEqual(webapp._keychain_get("seller"), "pw1")
        self.assertIsNone(webapp._keychain_get("main"))

    def test_web_dir(self):
        android_entry.install(webapp, TMP / "web", self.sec)
        self.assertEqual(webapp.WEB, TMP / "web")

    def test_update_never_installs(self):
        fake = {"ok": True, "available": True, "can_install": True, "reason": "", "url": "x.zip"}
        webapp.update_check = lambda force=False: dict(fake)
        android_entry.install(webapp, TMP / "web", self.sec)
        out = webapp.update_check()
        self.assertFalse(out["can_install"])
        self.assertIn("APK", out["reason"])
        self.assertTrue(out["page"].endswith("/releases/latest"))
```

- [ ] **Step 6: Убедиться, что падает.** `bash tests/run_tests.sh py` → ERROR `ModuleNotFoundError: android_entry`.

- [ ] **Step 7: Реализация** — `android/app/src/main/python/android_entry.py`:

```python
"""Android entry: point the shared server at the app's private storage and the
Android Keystore, then run it. Everything else is the same webapp.py as the Mac."""
from __future__ import annotations

import os
from pathlib import Path


def install(webapp, web_dir: Path, secrets) -> None:
    webapp.WEB = web_dir
    webapp._keychain_get = lambda aid, service="eas-bridge": secrets.get(aid, service) or None
    webapp._keychain_set = lambda aid, pw, service="eas-bridge": secrets.set(aid, service, pw)
    check = webapp.update_check

    def update_check(force: bool = False) -> dict:
        out = check(force)
        out["can_install"] = False  # no self_update.sh here: the APK is installed by hand
        out["page"] = f"https://github.com/{webapp.UPDATE_REPO}/releases/latest"
        if out.get("available"):
            out["reason"] = "На Android обновление ставится вручную: скачайте новый APK со страницы релиза"
        return out

    webapp.update_check = update_check


class _JavaSecrets:
    def __init__(self):
        from java import jclass  # Chaquopy
        self.k = jclass("ru.olesyaba.aasmail.Secrets")
    def get(self, account, service):
        v = self.k.get(account, service)
        return str(v) if v is not None else None
    def set(self, account, service, password):
        self.k.set(account, service, password)


def run(files_dir: str, cache_dir: str, page_key: str) -> None:
    data = Path(files_dir) / "eas-bridge"
    os.environ.update({
        "EAS_BRIDGE_DATA_DIR": str(data),
        "EAS_BRIDGE_CONFIG": str(data / "config.json"),
        "EAS_ATTACHMENT_DIR": str(data / "attachments"),
        "EAS_OVERFLOW_DIR": str(Path(cache_dir) / "overflow"),
        "EAS_MAIL_PAGE_KEY": page_key,
        "TMPDIR": cache_dir,
    })
    import webapp  # after the env: bridge and webapp read it at import time
    install(webapp, Path(files_dir) / "web", _JavaSecrets())
    webapp.main()
```

Проверить, что `UPDATE_REPO` существует в `webapp.py` (`grep -n "^UPDATE_REPO" webapp.py`); `webapp._latest_release` использует его же.

- [ ] **Step 8: Тесты.** `bash tests/run_tests.sh` → ALL PASSED.
- [ ] **Step 9: Commit.** `git add webapp.py android/app/src/main/python/android_entry.py tests/python/test_http.py tests/python/test_android_entry.py && git commit -m "Android entry: Keystore passwords, private dirs, page key for GET /"`

---

### Task 4: Android-проект — первый APK с работающей почтой

Результат: APK ставится на эмулятор/Fold, показывает тот же UI, логин и список писем работают. Уведомлений и одной панели ещё нет.

**Files:** всё под `android/` из «Структуры файлов», кроме `NativeBridge.kt`, `Attachments.kt`, `Notifier.kt`, `NewMail.kt`, `MeetingPlan.kt`, `MeetingAlarms.kt`, `SyncWorker.kt`, `Permissions.kt` и тестов к ним.

**Interfaces:**
- Consumes: `android_entry.run(files_dir, cache_dir, page_key)` (Task 3).
- Produces:
  - `object Secrets { fun init(ctx: Context); @JvmStatic fun get(account: String, service: String): String?; @JvmStatic fun set(account: String, service: String, password: String) }`
  - `object PyServer { const val BASE = "http://127.0.0.1:8780"; fun ensureStarted(ctx: Context); fun token(ctx: Context): String; val pageKey: String }` — `ensureStarted` идемпотентен, блокирует до ответа порта (≤ 30 с), иначе `IllegalStateException`.
  - `object Api { fun post(ctx: Context, path: String, body: JSONObject): JSONObject }` — бросает `IOException` при сетевой ошибке/не-200.
  - `class MainActivity` с `companion const val EXTRA_OPEN = "open"` (значение `"<acct>:<item_id>"`).

- [ ] **Step 1: Инструменты.** `brew list openjdk@17` уже есть. Эмулятор с профилем складного:

```bash
export JAVA_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"
export ANDROID_HOME=~/Library/Android/sdk
yes | $ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager "emulator" "platform-tools" "system-images;android-35;google_apis;arm64-v8a"
$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager list device | grep -i fold   # взять id «7.6in Foldable»/«pixel_fold»
echo no | $ANDROID_HOME/cmdline-tools/latest/bin/avdmanager create avd -n fold -k "system-images;android-35;google_apis;arm64-v8a" -d pixel_fold
```

Если скачивание образа тянется дольше ~10 мин — пропустить эмулятор, проверять на самом Fold 8 по `adb` (USB-отладка: Настройки → Сведения о телефоне → Сведения о ПО → 7× «Номер сборки»; затем «Параметры разработчика» → «Отладка по USB»).

- [ ] **Step 2: Каркас Gradle.**

`android/settings.gradle.kts`:
```kotlin
pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
    repositories { google(); mavenCentral() }
}
rootProject.name = "aas-mail"
include(":app")
```

`android/build.gradle.kts`:
```kotlin
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("com.chaquo.python") version "16.1.0" apply false
}
```

`android/gradle.properties`:
```
android.useAndroidX=true
org.gradle.jvmargs=-Xmx3g
kotlin.code.style=official
```

`android/.gitignore`:
```
.gradle/
build/
app/build/
local.properties
stage/
*.jks
```

Wrapper: `cd android && gradle wrapper --gradle-version 8.11.1` (создаст `gradlew`, `gradle/wrapper/*`; их коммитим).

`android/app/build.gradle.kts`:
```kotlin
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val appVersion: String = providers.gradleProperty("aasVersion").getOrElse("0.0.0")
val ks = file(System.getenv("AAS_KEYSTORE") ?: "${System.getProperty("user.home")}/.config/aas-mail/android-release.jks")

android {
    namespace = "ru.olesyaba.aasmail"
    compileSdk = 35
    defaultConfig {
        applicationId = "ru.olesyaba.aasmail"
        minSdk = 31
        targetSdk = 35
        versionName = appVersion
        versionCode = appVersion.split(".").fold(0) { acc, p -> acc * 100 + (p.toIntOrNull() ?: 0) }
        ndk { abiFilters += listOf("arm64-v8a") }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    signingConfigs {
        create("release") {
            storeFile = ks
            storePassword = System.getenv("AAS_KEYSTORE_PASS")
            keyAlias = "aas"
            keyPassword = System.getenv("AAS_KEYSTORE_PASS")
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    sourceSets["main"].assets.srcDir("../stage/assets")
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("requests")
            install("python-dateutil")
            install("certifi")
        }
    }
    sourceSets {
        getByName("main") { srcDir("src/main/python"); srcDir("../stage/python") }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
```

Если Chaquopy 16.1.0 не резолвится/не дружит с AGP 8.7.3 — взять последнюю версию из https://chaquo.com/chaquopy/doc/current/changelog.html и совместимый AGP из её таблицы; записать выбранные версии в Tech Stack этого плана.

- [ ] **Step 3: Скрипт сборки** — `android/build_apk.sh`:

```bash
#!/bin/bash
# Build the Android APK from the shared sources: stage webapp.py/bridge.py/vendor/web, run Gradle, copy to dist/.
#   bash android/build_apk.sh            # release (needs ~/.config/aas-mail/android-release.jks + AAS_KEYSTORE_PASS)
#   bash android/build_apk.sh debug      # debug, for the emulator
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"; A="$ROOT/android"; STAGE="$A/stage"
export JAVA_HOME="${JAVA_HOME:-$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
VER="$(python3 -c "import re;print(re.search(r'\"version\": \"([^\"]+)', open('webapp.py').read())[1])")"

VENDOR=""
for cand in "$ROOT/vendor" "$ROOT/dist/AAS mail.app/Contents/Resources/eas-bridge/vendor" \
            "$HOME/.cache/uv/git-v0/checkouts/b02ba3756ad3eeb9/"*/src; do
  [ -f "$cand/outlook_activesync_mcp/client.py" ] && { VENDOR="$cand"; break; }
done
[ -n "$VENDOR" ] || { echo "outlook_activesync_mcp not found — build the mac app once (app/build_app.sh)"; exit 1; }

rm -rf "$STAGE"; mkdir -p "$STAGE/python" "$STAGE/assets"
cp webapp.py bridge.py "$STAGE/python/"
cp -R "$VENDOR/outlook_activesync_mcp" "$STAGE/python/"
rm -f "$STAGE/python/outlook_activesync_mcp/server.py"
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp -R web "$STAGE/assets/web"

MODE="${1:-release}"
if [ "$MODE" = debug ]; then TASK=assembleDebug; OUT="$A/app/build/outputs/apk/debug/app-debug.apk"
else TASK=assembleRelease; OUT="$A/app/build/outputs/apk/release/app-release.apk"; fi
(cd "$A" && ./gradlew -q "$TASK" -PaasVersion="$VER")
mkdir -p dist
cp "$OUT" "dist/AAS-mail-$VER-android${MODE/release/}.apk"
echo "dist/AAS-mail-$VER-android${MODE/release/}.apk"
```

(Для debug имя получится `...-androiddebug.apk` — это нормально, в релиз уходит только release.)

Ключ подписи — один раз, вне репозитория:
```bash
mkdir -p ~/.config/aas-mail && keytool -genkeypair -v -keystore ~/.config/aas-mail/android-release.jks \
  -alias aas -keyalg RSA -keysize 4096 -validity 36500 -dname "CN=AAS mail"
```
Пароль хранить в Keychain: `security add-generic-password -a aas -s aas-android-keystore -w` и читать `export AAS_KEYSTORE_PASS=$(security find-generic-password -a aas -s aas-android-keystore -w)`. Потеря ключа = переустановка приложения с потерей данных, поэтому скопировать `.jks` в личный бэкап.

- [ ] **Step 4: Manifest** — `android/app/src/main/AndroidManifest.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.SCHEDULE_EXACT_ALARM" />
    <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
    <uses-permission android:name="android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS" />

    <application
        android:name="com.chaquo.python.android.PyApplication"
        android:label="AAS mail"
        android:icon="@mipmap/ic_launcher"
        android:allowBackup="false"
        android:dataExtractionRules="@xml/no_backup"
        android:usesCleartextTraffic="true"
        android:theme="@android:style/Theme.DeviceDefault.DayNight.NoActionBar">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:launchMode="singleTask"
            android:resizeableActivity="true"
            android:configChanges="screenSize|smallestScreenSize|screenLayout|orientation|density|keyboardHidden|uiMode">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <provider
            android:name="androidx.core.content.FileProvider"
            android:authorities="ru.olesyaba.aasmail.files"
            android:exported="false"
            android:grantUriPermissions="true">
            <meta-data android:name="android.support.FILE_PROVIDER_PATHS" android:resource="@xml/file_paths" />
        </provider>
    </application>
</manifest>
```

Трафик к 127.0.0.1 — http, поэтому `usesCleartextTraffic`; ограничение наружу даёт `shouldOverrideUrlLoading` (Step 7). `res/xml/no_backup.xml`:
```xml
<data-extraction-rules>
    <cloud-backup><exclude domain="root" /><exclude domain="file" /><exclude domain="database" /><exclude domain="sharedpref" /></cloud-backup>
    <device-transfer><exclude domain="root" /><exclude domain="file" /><exclude domain="database" /><exclude domain="sharedpref" /></device-transfer>
</data-extraction-rules>
```
`res/xml/file_paths.xml`:
```xml
<paths><cache-path name="att" path="att/" /></paths>
```
Иконки: `sips -z 192 192 app/AppIcon.iconset/icon_512x512.png --out android/app/src/main/res/mipmap-xxxhdpi/ic_launcher.png` (и `mipmap-xxhdpi` 144, `mipmap-xhdpi` 96).

- [ ] **Step 5: `Secrets.kt`** (+ instrumented test):

```kotlin
package ru.olesyaba.aasmail

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Account passwords: AES-GCM with a key that never leaves the Android Keystore
 *  (the Mac's Keychain counterpart). Called from Python via Chaquopy. */
object Secrets {
    private const val ALIAS = "aas-secrets"
    private lateinit var prefs: SharedPreferences

    fun init(ctx: Context) {
        prefs = ctx.applicationContext.getSharedPreferences("secrets", Context.MODE_PRIVATE)
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build())
        }.generateKey()
    }

    @JvmStatic fun set(account: String, service: String, password: String) {
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val blob = c.iv + c.doFinal(password.toByteArray())
        prefs.edit().putString("$service/$account", Base64.encodeToString(blob, Base64.NO_WRAP)).apply()
    }

    @JvmStatic fun get(account: String, service: String): String? {
        val blob = Base64.decode(prefs.getString("$service/$account", null) ?: return null, Base64.NO_WRAP)
        return try {
            val c = Cipher.getInstance("AES/GCM/NoPadding")
                .apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, blob, 0, 12)) }
            String(c.doFinal(blob, 12, blob.size - 12))
        } catch (e: Exception) {
            null  // key reset (e.g. restore on a new phone): the user re-enters the password
        }
    }
}
```

`android/app/src/androidTest/java/ru/olesyaba/aasmail/SecretsTest.kt`:
```kotlin
package ru.olesyaba.aasmail

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SecretsTest {
    @Test fun roundTripAndMissing() {
        Secrets.init(InstrumentationRegistry.getInstrumentation().targetContext)
        Secrets.set("t", "test-svc", "пароль-1")
        assertEquals("пароль-1", Secrets.get("t", "test-svc"))
        assertNull(Secrets.get("nobody", "test-svc"))
        val raw = InstrumentationRegistry.getInstrumentation().targetContext
            .getSharedPreferences("secrets", 0).getString("test-svc/t", "")!!
        assert(!raw.contains("пароль"))
    }
}
```

- [ ] **Step 6: `PyServer.kt` и `Api.kt`:**

```kotlin
package ru.olesyaba.aasmail

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.SecureRandom
import android.util.Base64

/** The shared webapp.py, running in a thread of this process (like the Mac app's bundled server). */
object PyServer {
    const val BASE = "http://127.0.0.1:8780"
    val pageKey: String = Base64.encodeToString(ByteArray(24).also { SecureRandom().nextBytes(it) }, Base64.NO_WRAP or Base64.URL_SAFE)
    @Volatile private var started = false

    @Synchronized fun ensureStarted(ctx: Context) {
        val app = ctx.applicationContext
        if (!started) {
            Secrets.init(app)
            copyWeb(app)
            if (!Python.isStarted()) Python.start(AndroidPlatform(app))
            Thread({
                Python.getInstance().getModule("android_entry")
                    .callAttr("run", app.filesDir.path, app.cacheDir.path, pageKey)
            }, "webapp").apply { isDaemon = true }.start()
            started = true
        }
        repeat(150) { if (alive()) return; Thread.sleep(200) }
        throw IllegalStateException("webapp did not start")
    }

    fun token(ctx: Context): String =
        File(ctx.filesDir, "eas-bridge/runtime_token").readText().trim()

    private fun alive() = try {
        (URL("$BASE/api/about").openConnection() as HttpURLConnection).run {
            connectTimeout = 300; readTimeout = 300; requestMethod = "POST"; doOutput = true
            outputStream.close(); responseCode; disconnect(); true   // 403 without a token is still "up"
        }
    } catch (e: Exception) { false }

    /** assets/web → filesDir/web once per APK version (webapp.WEB points there). */
    private fun copyWeb(ctx: Context) {
        val stamp = File(ctx.filesDir, "web/.version")
        val ver = ctx.packageManager.getPackageInfo(ctx.packageName, 0).lastUpdateTime.toString()
        if (stamp.exists() && stamp.readText() == ver) return
        File(ctx.filesDir, "web").deleteRecursively()
        fun copy(path: String) {
            val kids = ctx.assets.list(path) ?: emptyArray()
            if (kids.isEmpty()) {
                val out = File(ctx.filesDir, path).apply { parentFile?.mkdirs() }
                ctx.assets.open(path).use { i -> out.outputStream().use { i.copyTo(it) } }
            } else kids.forEach { copy("$path/$it") }
        }
        copy("web")
        stamp.writeText(ver)
    }
}
```

```kotlin
package ru.olesyaba.aasmail

import android.content.Context
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** POST /api/* with X-Tok — the Kotlin twin of app/TrayAPIClient.swift. */
object Api {
    fun post(ctx: Context, path: String, body: JSONObject): JSONObject {
        val c = URL(PyServer.BASE + path).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 5_000; c.readTimeout = 90_000
            c.requestMethod = "POST"; c.doOutput = true
            c.setRequestProperty("X-Tok", PyServer.token(ctx))
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(body.toString().toByteArray()) }
            if (c.responseCode != 200) throw IOException("HTTP ${c.responseCode} for $path")
            return JSONObject(c.inputStream.bufferedReader().readText())
        } finally {
            c.disconnect()
        }
    }
}
```

- [ ] **Step 7: `MainActivity.kt`** (без мостов — они в Task 7):

```kotlin
package ru.olesyaba.aasmail

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

class MainActivity : ComponentActivity() {
    companion object { const val EXTRA_OPEN = "open" }
    lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(v: WebView, r: WebResourceRequest): Boolean {
                if (r.url.host == "127.0.0.1") return false
                startActivity(Intent(Intent.ACTION_VIEW, r.url))   // mail links, meeting links → other apps
                return true
            }
            // The page embeds X-Tok: fetch it ourselves with the page key (see webapp.PAGE_KEY).
            override fun shouldInterceptRequest(v: WebView, r: WebResourceRequest): WebResourceResponse? {
                val u = r.url
                if (u.host != "127.0.0.1" || (u.path ?: "/") !in setOf("/", "/index.html")) return null
                val c = URL(u.toString()).openConnection() as HttpURLConnection
                c.setRequestProperty("X-Page-Key", PyServer.pageKey)
                val code = c.responseCode
                return WebResourceResponse("text/html", "utf-8", code, c.responseMessage ?: "OK",
                    c.headerFields.filterKeys { it != null }.mapValues { it.value.joinToString(",") },
                    if (code >= 400) c.errorStream else c.inputStream)
            }
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (web.canGoBack()) web.goBack() else finish() }
        })
        setContentView(TextView(this).apply { text = "AAS mail запускается…"; setPadding(48, 96, 48, 48) })
        thread {
            try {
                PyServer.ensureStarted(this)
                runOnUiThread { setContentView(web); web.loadUrl(startUrl(intent)) }
            } catch (e: Exception) {
                runOnUiThread { showStartError() }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val open = intent.getStringExtra(EXTRA_OPEN) ?: return
        val (acct, id) = open.split(":", limit = 2)
        web.evaluateJavascript("window.aasOpen && aasOpen(${org.json.JSONObject.quote(acct)}, ${org.json.JSONObject.quote(id)})", null)
    }

    private fun startUrl(i: Intent?) =
        PyServer.BASE + "/" + (i?.getStringExtra(EXTRA_OPEN)?.let { "#open=" + Uri.encode(it) } ?: "")

    private fun showStartError() {
        val log = File(filesDir, "eas-bridge/eas-mail.log")
        setContentView(TextView(this).apply {
            text = "Не удалось запустить почтовый сервер.\n\nНажмите, чтобы отправить лог."
            setPadding(48, 96, 48, 48)
            setOnClickListener {
                val send = Intent(Intent.ACTION_SEND).setType("text/plain")
                    .putExtra(Intent.EXTRA_TEXT, if (log.exists()) log.readText().takeLast(20_000) else "лога нет")
                startActivity(Intent.createChooser(send, "Отправить лог"))
            }
        })
    }
}
```

Проверить, где webapp пишет лог: `grep -n "eas-mail.log\|FileHandler" webapp.py`. Если лог только в stderr — в `android_entry.run` перед `webapp.main()` добавить `logging.basicConfig(filename=str(data / "eas-mail.log"), level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")` (при уже настроенном root-логгере `basicConfig` в `main()` станет no-op — это и нужно).

- [ ] **Step 8: Сборка и запуск.**

```bash
bash android/build_apk.sh debug
$ANDROID_HOME/emulator/emulator -avd fold -no-snapshot &      # или подключённый Fold 8
$ANDROID_HOME/platform-tools/adb wait-for-device
$ANDROID_HOME/platform-tools/adb install -r dist/AAS-mail-*-androiddebug.apk
$ANDROID_HOME/platform-tools/adb shell am start -n ru.olesyaba.aasmail/.MainActivity
sleep 8; $ANDROID_HOME/platform-tools/adb exec-out screencap -p > "$SCRATCH/a1.png"
```

Expected: на скриншоте экран первого запуска («укажите логин и пароль»). Ввести учётку Seller → список писем. Посмотреть скриншот глазами. Логи: `adb logcat -s python.stdout python.stderr`.

- [ ] **Step 9: Проверка безопасности страницы** (Review Focus #1) — из shell телефона:

```bash
adb shell "toybox nc 127.0.0.1 8780 <<< $'GET / HTTP/1.0\r\nHost: 127.0.0.1:8780\r\n\r\n' | head -1"
```
Expected: `HTTP/1.0 403`.

- [ ] **Step 10: Instrumented-тест Secrets.** `cd android && ./gradlew connectedDebugAndroidTest` → PASS. Затем `bash tests/run_tests.sh` → ALL PASSED.
- [ ] **Step 11: Commit.** `git add android && git commit -m "Android shell: Chaquopy server, WebView, Keystore secrets, APK build"`

---

### Task 5: Одна панель на внешнем экране, «назад», deep link (`web/`)

**Files:**
- Modify: `web/index.html` (новый блок после `showReaderEmpty`/`openGroup`; вызовы в `openGroup` ~1340, `renderList` ~1208; обработка hash в конце boot ~2822)
- Modify: `web/ui-kit/app.css` (в конец)
- Test: `tests/js/ui_logic.test.mjs`

**Interfaces:**
- Produces (глобалы inline-скрипта): `ONE_PANE = 600`; `paneFor(width: number, open: boolean): 'both'|'list'|'reader'`; `needsBackEntry(pane: string, state: object|null): boolean`; `parseOpenHash(hash: string): {acct: string, id: string} | null`; `paintPane(): void`; `window.aasOpen(acct: string, id: string): Promise<boolean>`.
- Consumes: существующие `curKey`, `calMode`, `prefs`, `showView`, `openGroup`, `groupsOnScreen`, `loadCal`, `view`.

- [ ] **Step 1: Падающие тесты** (в конец `tests/js/ui_logic.test.mjs`, по образцу существующих `load([...])`):

```js
test('one pane below 600px: list until a letter is open, then the letter', () => {
  const {paneFor} = load(['paneFor']);
  assert.equal(paneFor(900, false), 'both');
  assert.equal(paneFor(900, true), 'both');
  assert.equal(paneFor(599, false), 'list');
  assert.equal(paneFor(412, true), 'reader');
});

test('back needs one history entry per opened letter on the narrow screen', () => {
  const {needsBackEntry} = load(['needsBackEntry']);
  assert.equal(needsBackEntry('reader', null), true);          // folded with a letter open
  assert.equal(needsBackEntry('reader', {reading: 1}), false); // already pushed
  assert.equal(needsBackEntry('list', null), false);
  assert.equal(needsBackEntry('both', null), false);
});

test('notification deep link #open=acct:item_id (the id may contain colons)', () => {
  const {parseOpenHash} = load(['parseOpenHash']);
  assert.deepEqual(plain(parseOpenHash('#open=seller%3AaS8%3A1')), {acct: 'seller', id: 'aS8:1'});
  assert.equal(parseOpenHash('#cal'), null);
  assert.equal(parseOpenHash('#open=nocolon'), null);
});
```

- [ ] **Step 2: Убедиться, что падают.** `node --test tests/js/ui_logic.test.mjs` → FAIL (`paneFor is not defined`).

- [ ] **Step 3: Реализация в `web/index.html`** — новый блок сразу после функции `openGroup`:

```js
/* Phone / Fold cover screen: one pane at a time. The list until a letter is
   opened, then the letter full-screen; Android «back» pops to the list. */
const ONE_PANE = 600;
const paneFor = (w, open) => w >= ONE_PANE ? 'both' : open ? 'reader' : 'list';
const needsBackEntry = (pane, state) => pane === 'reader' && !state?.reading;
function paintPane() {
  const p = paneFor(innerWidth, !!curKey);
  document.body.classList.toggle('one-pane', p !== 'both');
  document.body.classList.toggle('reading', p === 'reader');
  if (needsBackEntry(p, history.state)) history.pushState({reading: 1}, '');
  // The week grid does not fit the cover screen: day view there, the saved mode when unfolded.
  const want = p === 'both' ? prefs.cal_view : 'day';
  if (calMode !== want) { calMode = want; if (view === 'cal') loadCal(); }
}
addEventListener('resize', paintPane);
addEventListener('popstate', () => {
  if (!document.body.classList.contains('reading')) return;
  curKey = null; curGroup = null;
  document.querySelectorAll('.aas-row.on').forEach(r => r.classList.remove('on'));
  paintPane();
});
const parseOpenHash = h => {
  const m = /^#open=(.+)$/.exec(h || ''); if (!m) return null;
  const s = decodeURIComponent(m[1]), i = s.indexOf(':');
  return i > 0 ? {acct: s.slice(0, i), id: s.slice(i + 1)} : null;
};
/** Open a letter from a notification: switch account, wait for the list, open its group. */
window.aasOpen = async (acct, id) => {
  if (view !== 'mail' || ACCT !== acct) showView('mail', acct);
  for (let t = 0; t < 40; t++) {
    const g = groupsOnScreen().find(x => x.items.some(m => m.item_id === id));
    if (g) { openGroup(g); return true; }
    await new Promise(r => setTimeout(r, 300));
  }
  return false;
};
```

Вызвать `paintPane();` последней строкой в `openGroup(g)` и последней строкой в `renderList(hasMore)`. В boot (после строки `if (location.hash === '#cal') showView('cal');`) добавить:

```js
  const deep = parseOpenHash(location.hash);
  if (deep) { history.replaceState(null, '', '/'); aasOpen(deep.acct, deep.id); }
  paintPane();
```

Если `prefs` в этом месте ещё не определена как глобал верхнего уровня (`grep -n "^let prefs\|^const prefs" web/index.html`), брать сохранённый режим из того же места, откуда его берёт boot (`calMode = prefs.cal_view` в строке ~2809).

- [ ] **Step 4: CSS** — в конец `web/ui-kit/app.css`:

```css
/* ═══ Phone / Fold cover screen (< 600px): one pane ════════════════════════ */
body { padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
@media (max-width: 599px) {
  body.one-pane #list { width: 100%; flex: 1 1 auto; border-right: 0; }
  body.one-pane #reader { display: none; }
  body.one-pane.reading #list { display: none; }
  body.one-pane.reading #reader { display: flex; }
  #listresize, #foldresize { display: none; }
  #newbtn {
    position: fixed; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom)); z-index: 40;
    width: 56px; height: 56px; padding: 0; border-radius: 999px; box-shadow: var(--aas-shadow);
  }
  body.reading #newbtn { display: none; }
}
/* Touch: 44px targets (the Mac's pointer is fine, so this never applies there). */
@media (pointer: coarse) {
  .aas-btn, .aas-tab, .aas-sect button, .aas-seg button, #foldtoggle { min-height: 44px; min-width: 44px; }
  .aas-row { min-height: 56px; }
}
```

Проверить имена селекторов: `grep -n "listresize\|class=\"aas-row\|aas-seg" web/index.html | head` — если ручки ресайза списка называются иначе, взять реальный id. `body` padding с `env()` на Mac = 0.

- [ ] **Step 5: Тесты.** `bash tests/run_tests.sh` → ALL PASSED (JS-тесты и остальные).
- [ ] **Step 6: Вручную на Mac** — сузить окно до минимума (640px): раскладка как раньше (2 панели, меню папок). Ничего не поменялось.
- [ ] **Step 7: Вручную на эмуляторе/Fold** — `bash android/build_apk.sh debug && adb install -r ...`:
  - внешний экран (эмулятор: сложить кнопкой «Fold» на панели эмулятора): список → тап → письмо на весь экран → жест «назад» → список; «назад» ещё раз → выход;
  - раскрыть с открытым письмом → список + это письмо; сложить обратно → письмо на весь экран, «назад» → список;
  - календарь на внешнем экране → день; раскрыть → сохранённый режим (неделя).
  - Скриншоты `adb exec-out screencap -p > $SCRATCH/pane-*.png`, посмотреть.
- [ ] **Step 8: Commit.** `git add web tests/js && git commit -m "Web: one-pane layout under 600px, back to list, #open= deep link, touch targets"`

---

### Task 6: Чистая логика уведомлений (Kotlin + JUnit)

**Files:**
- Create: `android/app/src/main/java/ru/olesyaba/aasmail/NewMail.kt`, `MeetingPlan.kt`
- Test: `android/app/src/test/java/ru/olesyaba/aasmail/NewMailTest.kt`, `MeetingPlanTest.kt`

**Interfaces:**
- Produces:
  - `data class MailItem(val itemId: String, val isRead: Boolean, val from: String, val subject: String)`
  - `object NewMail { fun diff(items: List<MailItem>, known: Set<String>?): Pair<List<MailItem>, Set<String>>; fun parse(items: JSONArray): List<MailItem> }` — `known == null` = первый проход (тихо запомнить). Возвращает (новые непрочитанные, обновлённое множество ≤ 2500 последних).
  - `data class Meeting(val accountId: String, val accountName: String, val itemId: String, val subject: String, val start: Long /*epoch ms*/, val cancelled: Boolean, val allDay: Boolean, val joinUrl: String?)` с `val id get() = "$accountId-$itemId"`.
  - `data class Reminder(val key: String, val fireAt: Long, val title: String, val body: String, val joinUrl: String?)`
  - `object MeetingPlan { fun plan(events: List<Meeting>, now: Long, leadMinutes: Int): List<Reminder>; fun joinUrl(location: String?, body: String?): String?; fun parse(accountId: String, accountName: String, items: JSONArray): List<Meeting> }` — `key = "$id|$start"` (перенос встречи → новый ключ).

- [ ] **Step 1: Падающие тесты.**

`NewMailTest.kt`:
```kotlin
package ru.olesyaba.aasmail

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NewMailTest {
    private fun m(id: String, read: Boolean = false) = MailItem(id, read, "Кто-то", "Тема $id")

    @Test fun firstRunIsSilent() {
        val (fresh, known) = NewMail.diff(listOf(m("a"), m("b")), null)
        assertTrue(fresh.isEmpty())
        assertEquals(setOf("a", "b"), known)
    }

    @Test fun onlyUnseenUnread() {
        val (fresh, known) = NewMail.diff(listOf(m("c"), m("d", read = true), m("a")), setOf("a"))
        assertEquals(listOf("c"), fresh.map { it.itemId })
        assertEquals(setOf("a", "c", "d"), known)
    }

    @Test fun knownSetIsBounded() {
        val big = (1..4001).map { "x$it" }.toSet()
        val (_, known) = NewMail.diff(listOf(m("new")), big)
        assertTrue(known.size <= 2500)
        assertTrue("new" in known)
    }
}
```

`MeetingPlanTest.kt`:
```kotlin
package ru.olesyaba.aasmail

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MeetingPlanTest {
    private val now = 1_000_000_000_000L
    private fun ev(id: String, inMin: Long, cancelled: Boolean = false, allDay: Boolean = false, acct: String = "main") =
        Meeting(acct, if (acct == "main") "Alfa-Bank" else "Seller", id, "Созвон $id", now + inMin * 60_000, cancelled, allDay, null)

    @Test fun schedulesLeadMinutesBefore() {
        val r = MeetingPlan.plan(listOf(ev("1", 30)), now, 5).single()
        assertEquals(now + 25 * 60_000, r.fireAt)
        assertEquals("🔴 Alfa-Bank", r.title)
        assertEquals("Созвон 1 через 5 мин", r.body)
    }

    @Test fun cancelsDroppedAndCancelled() {
        val out = MeetingPlan.plan(listOf(ev("gone", 30, cancelled = true), ev("past", 3), ev("day", 60, allDay = true)), now, 5)
        assertEquals(emptyList<Reminder>(), out)
    }

    @Test fun movedMeetingGetsNewKey() {
        val a = MeetingPlan.plan(listOf(ev("1", 30)), now, 5).single().key
        val b = MeetingPlan.plan(listOf(ev("1", 45)), now, 5).single().key
        assert(a != b)
    }

    @Test fun offWhenLeadIsZero() {
        assertEquals(emptyList<Reminder>(), MeetingPlan.plan(listOf(ev("1", 30)), now, 0))
    }

    @Test fun sellerMarker() {
        assertEquals("🟢 Seller", MeetingPlan.plan(listOf(ev("1", 30, acct = "seller")), now, 5).single().title)
    }

    @Test fun joinUrlPrefersMeetingHosts() {
        assertEquals("https://teams.microsoft.com/l/x",
            MeetingPlan.joinUrl(null, "wiki https://wiki.corp/a then <a href=\"https://teams.microsoft.com/l/x\">join</a>"))
        assertEquals("https://zoom.us/j/1", MeetingPlan.joinUrl("https://zoom.us/j/1", null))
        assertEquals("https://wiki.corp/a", MeetingPlan.joinUrl(null, "see https://wiki.corp/a."))
        assertNull(MeetingPlan.joinUrl("Переговорка 5", null))
    }
}
```

Отличие от Swift осознанное: all-day не напоминаем (баннер на Mac их тоже не показывает).

- [ ] **Step 2: Убедиться, что падают.** `cd android && ./gradlew testDebugUnitTest` → compilation FAIL (`Unresolved reference: NewMail`).

- [ ] **Step 3: Реализация.**

`NewMail.kt`:
```kotlin
package ru.olesyaba.aasmail

import org.json.JSONArray

data class MailItem(val itemId: String, val isRead: Boolean, val from: String, val subject: String)

/** New-mail detection between two background syncs — the same rule as the web UI's
 *  knownMailIds: the first look at an Inbox only remembers, later looks report unseen unread. */
object NewMail {
    private const val KEEP = 2500

    fun diff(items: List<MailItem>, known: Set<String>?): Pair<List<MailItem>, Set<String>> {
        val seen = LinkedHashSet(known ?: emptySet())
        val fresh = if (known == null) emptyList() else items.filter { !it.isRead && it.itemId !in seen }
        items.forEach { seen.add(it.itemId) }
        val trimmed = if (seen.size > KEEP) seen.toList().takeLast(KEEP).toSet() else seen
        return fresh to trimmed
    }

    fun parse(items: JSONArray): List<MailItem> = (0 until items.length()).map { i ->
        val o = items.getJSONObject(i)
        val f = o.optJSONObject("from")
        MailItem(o.getString("item_id"), o.optBoolean("is_read"),
            f?.optString("name")?.ifBlank { null } ?: f?.optString("address").orEmpty(),
            o.optString("subject").ifBlank { "(без темы)" })
    }
}
```

`MeetingPlan.kt`:
```kotlin
package ru.olesyaba.aasmail

import org.json.JSONArray
import java.time.OffsetDateTime

data class Meeting(val accountId: String, val accountName: String, val itemId: String, val subject: String,
                   val start: Long, val cancelled: Boolean, val allDay: Boolean, val joinUrl: String?) {
    val id get() = "$accountId-$itemId"
}

data class Reminder(val key: String, val fireAt: Long, val title: String, val body: String, val joinUrl: String?)

/** Port of app/TrayNotificationPlan.swift + TrayJoinLink (TrayModels.swift). */
object MeetingPlan {
    fun plan(events: List<Meeting>, now: Long, leadMinutes: Int): List<Reminder> {
        if (leadMinutes <= 0) return emptyList()
        return events.filter { !it.cancelled && !it.allDay }.mapNotNull { e ->
            val fire = e.start - leadMinutes * 60_000L
            if (fire <= now) return@mapNotNull null
            val marker = if (e.accountId == "seller") "🟢" else "🔴"
            Reminder("${e.id}|${e.start}", fire, "$marker ${e.accountName}", "${e.subject} через $leadMinutes мин", e.joinUrl)
        }
    }

    fun parse(accountId: String, accountName: String, items: JSONArray): List<Meeting> = (0 until items.length()).mapNotNull { i ->
        val o = items.getJSONObject(i)
        val start = runCatching { OffsetDateTime.parse(o.getString("start_iso")).toInstant().toEpochMilli() }.getOrNull()
            ?: return@mapNotNull null
        Meeting(accountId, accountName, o.getString("item_id"),
            o.optString("subject").replace(Regex("""^\s*((re|fwd?|fw|отв|пер)\s*:\s*)+""", RegexOption.IGNORE_CASE), "").ifBlank { "(без темы)" },
            start, o.optString("meeting_status").equals("cancelled", true), o.optBoolean("is_all_day"),
            joinUrl(o.optString("location").ifBlank { null }, o.optString("body").ifBlank { null }))
    }

    private val hosts = listOf("teams.microsoft", "teams.live", "zoom.us", "ktalk", "kontur",
        "meet.google", "trueconf", "jazz.sber", "telemost.yandex", "webex.com")
    private val urlRe = Regex("""https?://[^\s<>'")\]]+""")

    fun joinUrl(location: String?, body: String?): String? {
        location?.trim()?.let { if (Regex("^https?://\\S+$").matches(it)) return it }
        val urls = listOfNotNull(location, body).flatMap { t ->
            val norm = t.replace("\\/", "/").replace("&amp;", "&")
            linkedSetOf(t, norm, norm.replace(Regex("<[^>]+>"), " "))
        }.flatMap { urlRe.findAll(it).map { m -> m.value.trimEnd('.', ',', ';', ')', ']') } }
        return urls.firstOrNull { u -> hosts.any { u.substringAfter("://").substringBefore('/').lowercase().contains(it) } } ?: urls.firstOrNull()
    }
}
```

Формат `start_iso` проверить на живом ответе: `curl ... /api/events {"action":"list","start":"<сегодня>","end":"<послезавтра>","acct":"seller"}` — если там нет смещения (`2026-09-27T10:00:00` без `Z`/`+03:00`), парсить `LocalDateTime.parse(...).atZone(ZoneId.systemDefault())` и добавить тест на этот формат.

- [ ] **Step 4: Тесты.** `cd android && ./gradlew testDebugUnitTest` → PASS.
- [ ] **Step 5: Commit.** `git add android/app/src && git commit -m "Android: new-mail diff and meeting reminder plan (ports of the tray logic)"`

---

### Task 7: Нативные мосты — уведомления из UI, вложения, связь с сервером

**Files:**
- Create: `NativeBridge.kt`, `Attachments.kt`, `Notifier.kt`
- Modify: `MainActivity.kt` (шим + мосты + DownloadListener)
- Modify: `web/index.html` (~714: `acct`, `item_id` в `aasNewMail`)

**Interfaces:**
- Consumes: `MainActivity.web`, `MainActivity.EXTRA_OPEN`, `PyServer.token`, `MailItem`, `Reminder`.
- Produces:
  - `object Notifier { fun channels(ctx: Context); fun newMail(ctx: Context, acctId: String, acctName: String, items: List<MailItem>); fun meeting(ctx: Context, r: Reminder); fun unreachable(ctx: Context, acctName: String) }`
  - `class Attachments(activity: MainActivity) { fun handle(mode: String, files: JSONArray) }` — `mode` ∈ `open|as|all`, `files[i] = {url, name}`; результат в UI через `aasSaved(ok, text)`.

Шим — одна строка JS, благодаря ему `web/` не трогаем: все `window.webkit?.messageHandlers?.X?.postMessage(v)` попадают в `AASNative.post("X", JSON)`.

- [ ] **Step 1: `Notifier.kt`:**

```kotlin
package ru.olesyaba.aasmail

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

object Notifier {
    private const val MAIL = "mail"; private const val MEET = "meetings"; private const val STATUS = "status"

    fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(MAIL, "Новые письма", NotificationManager.IMPORTANCE_DEFAULT))
        nm.createNotificationChannel(NotificationChannel(MEET, "Встречи", NotificationManager.IMPORTANCE_HIGH))
        nm.createNotificationChannel(NotificationChannel(STATUS, "Связь с сервером", NotificationManager.IMPORTANCE_LOW))
    }

    private fun open(ctx: Context, req: Int, extra: String?) = PendingIntent.getActivity(ctx, req,
        Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .apply { extra?.let { putExtra(MainActivity.EXTRA_OPEN, it) } },
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun post(ctx: Context, id: Int, n: NotificationCompat.Builder) {
        try { NotificationManagerCompat.from(ctx).notify(id, n.build()) } catch (_: SecurityException) { /* permission denied */ }
    }

    fun newMail(ctx: Context, acctId: String, acctName: String, items: List<MailItem>) {
        if (items.isEmpty()) return
        val marker = if (acctId == "seller") "🟢" else "🔴"
        val top = items.first()
        val n = NotificationCompat.Builder(ctx, MAIL).setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(if (items.size == 1) "$marker ${top.from}" else "$marker $acctName: ${items.size} новых")
            .setContentText(top.subject).setNumber(items.size).setAutoCancel(true)
            .setGroup("mail-$acctId")
            .setContentIntent(open(ctx, acctId.hashCode(), "$acctId:${top.itemId}"))
        post(ctx, "mail-$acctId".hashCode(), n)
    }

    fun meeting(ctx: Context, r: Reminder) {
        val n = NotificationCompat.Builder(ctx, MEET).setSmallIcon(android.R.drawable.ic_menu_my_calendar)
            .setContentTitle(r.title).setContentText(r.body).setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_EVENT).setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open(ctx, r.key.hashCode(), null))
        r.joinUrl?.let {
            n.addAction(0, "Подключиться", PendingIntent.getActivity(ctx, r.key.hashCode() + 1,
                Intent(Intent.ACTION_VIEW, Uri.parse(it)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE))
        }
        post(ctx, r.key.hashCode(), n)
    }

    fun unreachable(ctx: Context, acctName: String) {
        post(ctx, "net-$acctName".hashCode(), NotificationCompat.Builder(ctx, STATUS)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("Нет связи с $acctName")
            .setContentText("Проверьте сеть или VPN. Почта обновится сама, когда связь появится.")
            .setAutoCancel(true).setContentIntent(open(ctx, 7, null)))
    }
}
```

- [ ] **Step 2: `Attachments.kt`:**

```kotlin
package ru.olesyaba.aasmail

import android.content.Intent
import android.net.Uri
import android.webkit.MimeTypeMap
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import androidx.documentfile.provider.DocumentFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URL
import kotlin.concurrent.thread

/** «Открыть» / «Сохранить как…» / «Сохранить все…» — the Mac shell's aasSave, via the Storage Access Framework. */
class Attachments(private val a: MainActivity) {
    private var pending: List<Pair<String, String>> = emptyList()  // (url, name)

    private val saveOne = a.registerForActivityResult(ActivityResultContracts.CreateDocument("*/*")) { uri ->
        uri?.let { u -> io { write(pending.first().first, u); "Сохранено: ${pending.first().second}" } }
    }
    private val saveAll = a.registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { tree ->
        tree ?: return@registerForActivityResult
        io {
            val dir = DocumentFile.fromTreeUri(a, tree)!!
            pending.forEach { (url, name) ->
                val f = dir.createFile(mime(name), name) ?: error("не удалось создать $name")
                write(url, f.uri)
            }
            "Сохранено файлов: ${pending.size}"
        }
    }

    fun handle(mode: String, files: JSONArray) {
        pending = (0 until files.length()).map { files.getJSONObject(it).let { f -> abs(f.getString("url")) to f.getString("name") } }
        if (pending.isEmpty()) return
        when (mode) {
            "as" -> saveOne.launch(pending.first().second)
            "all" -> saveAll.launch(null)
            else -> io {  // open
                val (url, name) = pending.first()
                val f = File(a.cacheDir, "att/$name").apply { parentFile?.mkdirs() }
                URL(url).openStream().use { i -> f.outputStream().use { i.copyTo(it) } }
                val uri = FileProvider.getUriForFile(a, "ru.olesyaba.aasmail.files", f)
                a.startActivity(Intent.createChooser(Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime(name))
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), name))
                null
            }
        }
    }

    private fun abs(u: String) = if (u.startsWith("http")) u else PyServer.BASE + u
    private fun mime(name: String) =
        MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase()) ?: "application/octet-stream"
    private fun write(url: String, to: Uri) {
        URL(url).openStream().use { i -> a.contentResolver.openOutputStream(to)!!.use { i.copyTo(it) } }
    }
    private fun io(job: () -> String?) = thread {
        val (ok, text) = try { true to job() } catch (e: Exception) { false to "Не удалось сохранить вложение: ${e.message}" }
        text?.let { t -> a.runOnUiThread { a.web.evaluateJavascript("window.aasSaved && aasSaved($ok, ${JSONObject.quote(t)})", null) } }
    }
}
```

Добавить зависимость в `app/build.gradle.kts`: `implementation("androidx.documentfile:documentfile:1.0.1")`.

- [ ] **Step 3: `NativeBridge.kt`:**

```kotlin
package ru.olesyaba.aasmail

import android.webkit.JavascriptInterface
import org.json.JSONObject

/** Receives what the web UI posts to the Mac shell (window.webkit.messageHandlers.<name>). */
class NativeBridge(private val a: MainActivity, private val attachments: Attachments) {
    @JavascriptInterface
    fun post(name: String, json: String) {
        when (name) {
            "aasSave" -> JSONObject(json).let { p -> a.runOnUiThread { attachments.handle(p.optString("mode"), p.getJSONArray("files")) } }
            // The open app notifies like the tray does; the background worker covers the closed app.
            "aasNewMail" -> JSONObject(json).let { p ->
                val top = MailItem(p.optString("item_id"), false, p.optString("from"), p.optString("subject"))
                Notifier.newMail(a, p.optString("acct", "main"), p.optString("account"),
                    List(p.optInt("count", 1).coerceAtLeast(1)) { top })
            }
            else -> Unit  // aasTheme/aasPalette/aasPrefs/aasBadge/aasPlaySound: Mac-only (tray, Dock, sounds)
        }
    }
}
```

В `web/index.html` (~строка 714, `aasNewMail.postMessage({...})`) добавить в объект два поля: `acct,` и `item_id: top.item_id,`. Swift-трей лишние ключи игнорирует (проверить: `grep -n "aasNewMail" -A8 app/main.swift` — читает только известные ключи через `as?`). Тогда тап по уведомлению из открытого приложения тоже открывает письмо. В Files этой задачи добавить `web/index.html`.

- [ ] **Step 4: Подключить в `MainActivity.onCreate`** (до `loadUrl`), импорты `androidx.webkit.WebViewCompat`, `androidx.webkit.WebViewFeature`:

```kotlin
        val attachments = Attachments(this)
        web.addJavascriptInterface(NativeBridge(this, attachments), "AASNative")
        // The web UI talks to the Mac shell through window.webkit.messageHandlers: route that to AASNative.
        val shim = "window.webkit={messageHandlers:new Proxy({},{get:(_,n)=>({postMessage:v=>AASNative.post(String(n),JSON.stringify(v===undefined?null:v))})})};"
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT))
            WebViewCompat.addDocumentStartJavaScript(web, shim, setOf("http://127.0.0.1:8780"))
        web.setDownloadListener { url, _, disposition, _, _ ->
            val name = Regex("filename\\*=UTF-8''([^;]+)").find(disposition ?: "")?.groupValues?.get(1)
                ?.let { java.net.URLDecoder.decode(it, "UTF-8") } ?: android.net.Uri.parse(url).lastPathSegment ?: "attachment"
            attachments.handle("as", org.json.JSONArray().put(org.json.JSONObject().put("url", url).put("name", name)))
        }
        Notifier.channels(this)
```

`addJavascriptInterface` виден только страницам WebView, а WebView не уходит с 127.0.0.1 (`shouldOverrideUrlLoading`), так что чужой сайт до `AASNative` не доберётся. Письма рендерятся в `iframe srcdoc` с CSP без скриптов (`web/index.html:1565`) — проверить, что `script-src` там не разрешён: `sed -n 1560,1566p web/index.html`.

- [ ] **Step 5: Сборка и ручная проверка** на эмуляторе/Fold:
  - письмо с вложением: «Открыть» → системный выбор приложения; «Сохранить как…» → диалог файлов, файл появился; «Сохранить все» → выбор папки;
  - ссылка в письме и «Подключиться» во встрече → открываются в браузере/Teams, а не внутри WebView;
  - отправить себе письмо с Mac при открытом приложении → уведомление.
- [ ] **Step 6: Тесты + commit.** `bash tests/run_tests.sh && (cd android && ./gradlew testDebugUnitTest)` → PASS. `git add android web/index.html && git commit -m "Android: native bridges — notifications, attachments via SAF, external links"`

---

### Task 8: Фон — синк писем, будильники встреч, разрешения

**Files:**
- Create: `SyncWorker.kt`, `MeetingAlarms.kt`, `Permissions.kt`
- Modify: `AndroidManifest.xml` (receiver-ы), `MainActivity.kt` (планирование воркера + разрешения)

**Interfaces:**
- Consumes: `PyServer.ensureStarted`, `Api.post`, `NewMail.diff/parse`, `MeetingPlan.plan/parse`, `Notifier.*`.
- Produces: `SyncWorker.schedule(ctx: Context)`; `MeetingAlarms.reschedule(ctx: Context, reminders: List<Reminder>)`; `class MeetingReceiver : BroadcastReceiver`; `class BootReceiver : BroadcastReceiver`; `Permissions.ask(a: ComponentActivity)`.

- [ ] **Step 1: `MeetingAlarms.kt`:**

```kotlin
package ru.olesyaba.aasmail

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Exact reminders; the set of scheduled keys lives in prefs so stale ones are cancelled. */
object MeetingAlarms {
    private const val PREFS = "alarms"

    private fun pi(ctx: Context, r: Reminder?, key: String) = PendingIntent.getBroadcast(ctx, key.hashCode(),
        Intent(ctx, MeetingReceiver::class.java).apply {
            putExtra("key", key)
            r?.let { putExtra("title", it.title); putExtra("body", it.body); putExtra("join", it.joinUrl); putExtra("at", it.fireAt) }
        }, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    fun reschedule(ctx: Context, reminders: List<Reminder>) {
        val am = ctx.getSystemService(AlarmManager::class.java)
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val old = p.getStringSet("keys", emptySet())!!
        val want = reminders.associateBy { it.key }
        (old - want.keys).forEach { am.cancel(pi(ctx, null, it)) }
        want.values.forEach { r ->
            val op = pi(ctx, r, r.key)
            if (am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.fireAt, op)
            else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.fireAt, op)  // no permission: a few minutes late
        }
        p.edit().putStringSet("keys", want.keys).apply()
    }
}

class MeetingReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        Notifier.meeting(ctx, Reminder(i.getStringExtra("key") ?: return, i.getLongExtra("at", 0),
            i.getStringExtra("title").orEmpty(), i.getStringExtra("body").orEmpty(), i.getStringExtra("join")))
    }
}

class BootReceiver : BroadcastReceiver() {
    // Alarms die on reboot; the next sync re-plans them, so just sync now.
    override fun onReceive(ctx: Context, i: Intent) { SyncWorker.runNow(ctx) }
}
```

- [ ] **Step 2: `SyncWorker.kt`:**

```kotlin
package ru.olesyaba.aasmail

import android.content.Context
import androidx.work.*
import org.json.JSONObject
import java.io.IOException
import java.time.LocalDate
import java.util.concurrent.TimeUnit

/** Every 15 minutes (Android's floor): new Inbox mail → notification, next 24–48 h of meetings → alarms. */
class SyncWorker(ctx: Context, p: WorkerParameters) : Worker(ctx, p) {
    companion object {
        fun schedule(ctx: Context) = WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("sync",
            ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED)).build())
        fun runNow(ctx: Context) = WorkManager.getInstance(ctx).enqueue(OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED)).build())
    }

    override fun doWork(): Result {
        val ctx = applicationContext
        try { PyServer.ensureStarted(ctx) } catch (e: Exception) { return Result.retry() }
        val st = ctx.getSharedPreferences("sync", Context.MODE_PRIVATE)
        val accounts = try { Api.post(ctx, "/api/accounts", JSONObject()).getJSONArray("accounts") } catch (e: IOException) { return Result.retry() }
        val lead = runCatching { Api.post(ctx, "/api/prefs", JSONObject()).getJSONObject("prefs").optInt("reminder_minutes", 5) }.getOrDefault(5)
        val meetings = mutableListOf<Meeting>()
        val today = LocalDate.now()
        for (i in 0 until accounts.length()) {
            val a = accounts.getJSONObject(i)
            val id = a.getString("id"); val name = a.getString("name")
            if (!a.isNull("auth_error")) continue  // wrong password: the UI already says so
            try {
                val r = Api.post(ctx, "/api/mail", JSONObject().put("action", "list").put("limit", 20).put("acct", id))
                if (!r.optBoolean("ok", true)) throw IOException(r.optString("error"))
                val known = st.getStringSet("known-$id", null)
                val (fresh, next) = NewMail.diff(NewMail.parse(r.getJSONArray("items")), known)
                st.edit().putStringSet("known-$id", next).putInt("fail-$id", 0).apply()
                Notifier.newMail(ctx, id, name, fresh)
                val ev = Api.post(ctx, "/api/events", JSONObject().put("action", "list").put("acct", id)
                    .put("start", today.toString()).put("end", today.plusDays(2).toString()).put("limit", 300))
                if (ev.optBoolean("ok")) meetings += MeetingPlan.parse(id, name, ev.getJSONArray("items"))
            } catch (e: IOException) {
                val n = st.getInt("fail-$id", 0) + 1
                st.edit().putInt("fail-$id", n).apply()
                if (n == 3) Notifier.unreachable(ctx, name)
            }
        }
        MeetingAlarms.reschedule(ctx, MeetingPlan.plan(meetings, System.currentTimeMillis(), lead))
        return Result.success()
    }
}
```

Если один аккаунт не ответил, его старые будильники снимутся при `reschedule`. Это осознанно: лучше без напоминания, чем с неверным временем. Потолок известен — `ponytail:` комментарий не нужен, это прямое правило спеки «не звонить по старому времени».

Проверить, что `/api/mail` без `folder` отдаёт Входящие (так было в живом прогоне 27.09) — если нет, взять id Входящих из `/api/folders {action:list}` по `type == 2` и передать `folder`.

- [ ] **Step 3: `Permissions.kt`:**

```kotlin
package ru.olesyaba.aasmail

import android.Manifest
import android.app.AlarmManager
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import android.app.AlertDialog

/** First run: notifications, exact alarms, no battery optimisation (One UI puts idle apps to sleep). */
object Permissions {
    fun ask(a: ComponentActivity) {
        val notif = a.registerForActivityResult(ActivityResultContracts.RequestPermission()) { next(a) }
        notif.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun next(a: ComponentActivity) {
        val am = a.getSystemService(AlarmManager::class.java)
        val pm = a.getSystemService(PowerManager::class.java)
        val steps = buildList {
            if (!am.canScheduleExactAlarms()) add("Точные напоминания о встречах" to
                Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${a.packageName}")))
            if (!pm.isIgnoringBatteryOptimizations(a.packageName)) add("Проверка почты в фоне" to
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${a.packageName}")))
        }
        val (title, intent) = steps.firstOrNull() ?: return
        AlertDialog.Builder(a).setTitle(title)
            .setMessage("Без этого разрешения уведомления будут приходить с опозданием или не придут.")
            .setPositiveButton("Разрешить") { _, _ -> a.startActivity(intent) }
            .setNegativeButton("Позже", null).show()
    }
}
```

`registerForActivityResult` должен вызываться до `STARTED` — вызывать `Permissions.ask(this)` в `onCreate` синхронно (не из потока). Диалог про второе разрешение появится при следующем запуске — это нормально и не навязчиво.

- [ ] **Step 4: Подключить.** В `MainActivity.onCreate` сразу после `super.onCreate`: `Permissions.ask(this)`; после успешного `PyServer.ensureStarted` (в потоке): `SyncWorker.schedule(this); SyncWorker.runNow(this)`. В манифест внутрь `<application>`:

```xml
        <receiver android:name=".MeetingReceiver" android:exported="false" />
        <receiver android:name=".BootReceiver" android:exported="true">
            <intent-filter><action android:name="android.intent.action.BOOT_COMPLETED" /></intent-filter>
        </receiver>
```

- [ ] **Step 5: Проверка на устройстве.**

```bash
bash android/build_apk.sh debug && adb install -r dist/AAS-mail-*-androiddebug.apk
adb shell am start -n ru.olesyaba.aasmail/.MainActivity   # выдать разрешения, дождаться списка
adb shell am force-stop ru.olesyaba.aasmail              # «закрыто»
# отправить себе письмо на Seller с Mac, затем форсировать воркер:
adb shell cmd jobscheduler run -f ru.olesyaba.aasmail 0 2>/dev/null || adb shell am broadcast -a android.intent.action.BOOT_COMPLETED -p ru.olesyaba.aasmail
adb shell dumpsys alarm | grep -A3 ru.olesyaba.aasmail   # будильники встреч
```
Expected: уведомление «🟢 …» с темой письма; тап открывает именно это письмо; в `dumpsys alarm` — будильники на сегодняшние встречи за N минут. Создать себе тестовую встречу через 10 мин с Teams-ссылкой → напоминание через 5 мин с кнопкой «Подключиться».

Первый синк после установки уведомлений не даёт (Review Focus #4) — проверить: свежая установка, во Входящих есть непрочитанные → тишина.

- [ ] **Step 6: Тесты + commit.** `bash tests/run_tests.sh && (cd android && ./gradlew testDebugUnitTest)` → PASS. `git add android && git commit -m "Android: background mail sync, exact meeting alarms, first-run permissions"`

---

### Task 9: Приёмка на Fold 8 и релиз

**Files:** Modify: `RELEASE_NOTES.txt`, `README.md` (раздел «Android»), `app/release.sh` (приложить APK, если есть в `dist/`).

- [ ] **Step 1: Release-сборка.** `export AAS_KEYSTORE_PASS=$(security find-generic-password -a aas -s aas-android-keystore -w); bash android/build_apk.sh` → `dist/AAS-mail-<ver>-android.apk`. `$ANDROID_HOME/build-tools/35.0.0/apksigner verify --print-certs dist/AAS-mail-*-android.apk` → подпись «CN=AAS mail».
- [ ] **Step 2: Чек-лист на Fold 8** (снять скриншоты `adb exec-out screencap -p`, посмотреть каждый):
  1. Оба аккаунта: список, чтение, ответ, пересылка, новое письмо, вложение в исходящем.
  2. Календарь: день на внешнем, неделя на внутреннем, RSVP с комментарием.
  3. Складывание/раскрытие с открытым письмом и с открытым черновиком — ничего не теряется.
  4. Уведомление о письме при закрытом приложении; тап открывает письмо.
  5. Напоминание о встрече + «Подключиться».
  6. Тёмная тема системы → тёмная тема приложения.
  7. Без сети/без VPN: приложение открывается с кэшем, после 3 неудачных синков — одно уведомление «Нет связи».
  8. `adb shell run-as ru.olesyaba.aasmail cat shared_prefs/secrets.xml` → release-сборка не debuggable, команда должна отказать.
- [ ] **Step 3: Документы.** `RELEASE_NOTES.txt` — строка про Android APK; `README.md` — как поставить APK (разрешить установку из неизвестных источников для файлового менеджера, выдать разрешения при первом запуске), как собрать (`bash android/build_apk.sh`).
- [ ] **Step 4: Релиз.** В `app/release.sh` найти место, где к релизу прикладывается `-mac.zip`, и приложить `dist/AAS-mail-$VER-android.apk`, если файл есть. Прогнать `bash tests/run_tests.sh` → ALL PASSED.
- [ ] **Step 5: Commit.** `git add RELEASE_NOTES.txt README.md app/release.sh && git commit -m "Android APK in releases; README section"`
