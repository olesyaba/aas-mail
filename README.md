# AAS mail

Локальный клиент почты и календаря для **Alfa-Bank** и **Alfa-Seller** поверх Exchange ActiveSync.

- Нативное macOS-приложение (WKWebView + menu-bar tray)
- Веб-UI + локальный сервер `webapp.py` (127.0.0.1:8780)
- Пароли в Keychain; адреса серверов по умолчанию:
  - Bank: `https://owa.alfabank.ru/Microsoft-Server-ActiveSync`
  - Seller: `https://sync.alfaops.ru/Microsoft-Server-ActiveSync`

## Быстрый старт (разработка)

```bash
cd app
bash build_app.sh          # → ~/Applications/AAS mail.app (или Почта EAS.app)
open ~/Applications/AAS\ mail.app
```

## Пакет для коллег (без кредов)

```bash
cd app
bash build_dist.sh         # → ~/Desktop/AAS-mail-*-mac.zip
```

Коллеги вводят свои логин/пароль в Настройках; серверы уже подставлены.

## Структура

```
aas-mail/
  webapp.py      # HTTP API + UI server
  bridge.py      # ActiveSync backend wrapper
  web/           # index.html UI
  app/           # Swift shell, tray, build_*.sh
  docs/          # specs / plans
```

Контакт: Mattermost `@olesya_ba`
