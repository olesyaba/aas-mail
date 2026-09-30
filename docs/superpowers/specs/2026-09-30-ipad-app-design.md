# AAS mail для iPad (iPad Pro 11, M4)

Дата: 2026-09-30 · Статус: подход согласован в чате, ждёт ревью спецификации

## Цель

Самостоятельное iPad-приложение AAS mail: почта и календарь Alfa-Bank и Seller без Mac.
Тот же интерфейс и та же серверная часть (`webapp.py`, `bridge.py`, vendored
`outlook_activesync_mcp`), что на Mac и Android. Успех: на iPad Pro 11 (M4) видны
папки и письма обоих аккаунтов, календарь, планировщик встреч; пароли в Keychain;
ссылки «Подключиться» и вложения открываются системными средствами.

## Решения

- **Подход:** SwiftUI-оболочка + WKWebView + встроенный Python 3.12 из BeeWare
  Python-Apple-support `3.12-b10` (`Python.xcframework`). Сервер — поток внутри
  приложения на `127.0.0.1:8780`, как Chaquopy на Android. Форка общего кода нет.
- **Подпись:** бесплатная личная команда («Олеся Бабакаева», сертификат Apple
  Development olesyabog@gmail.com). Установка кабелем из Xcode, профиль живёт 7 дней.
  Bundle id `ru.olesyaba.aasmail` (как на Android).
- **Сборка:** `ios/build_ios.sh` — готовит `ios/stage/` (Python-исходники, `web/`,
  зависимости `requests`, `urllib3`, `python-dateutil`, `six`, `certifi`, `idna`,
  `charset-normalizer` чистым Python), генерирует/использует Xcode-проект, собирает
  для симулятора или устройства через `xcodebuild` с `DEVELOPER_DIR` (без sudo).
  Скачанный `Python.xcframework` кэшируется в `ios/vendor/` (в .gitignore).
- **Минимальная iPadOS:** 17.0 (M4 выходит с 17.5; BeeWare 3.12 требует ≥ 13).

## Компоненты

1. **`ios/AASMail/` (Swift):**
   - `AASMailApp.swift` — SwiftUI App, одно окно, поддержка Split View / Stage Manager,
     все ориентации.
   - `PyServer.swift` — `Py_Initialize` c `PYTHONHOME` внутри бандла, `sys.path` на
     `app/` и `app_packages/`, поток `ios_entry.run(data_dir, cache_dir, page_key)`;
     ждёт `/api/about` с нашим токеном (как `PyServer.kt`), таймаут 30 с → экран ошибки
     с кнопкой «Поделиться логом».
   - `WebView.swift` — WKWebView на `http://127.0.0.1:8780/` с page key; навигация на
     внешние http(s) и схемы приложений (ktalk, msteams, zoommtg, tel, mailto) —
     `UIApplication.open`; загрузки (вложения) — `WKDownload` → временный файл →
     `UIActivityViewController`.
2. **`ios/python/ios_entry.py`** — аналог `android_entry.py`: переменные окружения
   (`EAS_BRIDGE_DATA_DIR` в Application Support, `EAS_ATTACHMENT_DIR`,
   `EAS_OVERFLOW_DIR`, `TMPDIR`, `EAS_MAIL_PAGE_KEY`), лог в файл, `webapp.WEB` на
   бандл, `_keychain_get/_keychain_set` через `ios_keychain`, `update_check` →
   `can_install: False`, причина «на iPad обновление ставится из Xcode».
3. **`ios/python/ios_keychain.py`** — Keychain через `ctypes` + Security.framework
   (`SecItemCopyMatching`, `SecItemAdd`, `SecItemUpdate`, `kSecClassGenericPassword`,
   service/account как на Mac, `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`).

## Поведение и ограничения iPadOS

- В фоне приложение засыпает: нет фоновой синхронизации и уведомлений о новой почте.
  При возврате в приложение страница сама делает догоняющую синхронизацию (как после
  сна Mac).
- Напоминания о встречах (локальные уведомления) — вне этой итерации.
- Webapp не должен звать `subprocess` на iOS: пути, где он используется (Keychain,
  self-update, открытие файлов), заменены или недоступны; проверяется тестом.

## Тесты и проверка

- Python: `ios_entry.install` подменяет keychain/update (юнит-тест как для Android),
  `ios_keychain` — тест на macOS против временной связки ключей (тот же API
  Security.framework).
- Сборка для симулятора iPad Pro 11 (M4), запуск, скриншоты: мастер входа, папки,
  письмо, календарь, «Новое событие» с планировщиком; `simctl` + лог.
- Установка на iPad по кабелю, вход в оба аккаунта, живой smoke-сценарий из UI.

## Не входит

Уведомления (почта и встречи), виджеты iPadOS, TestFlight/App Store, iPhone-вёрстка
(возможна тем же бандлом позже), автообновление.
