# AAS mail — handoff

Канон: `/Users/olesyaba/AAS mail`

## Статус бэклога (23.09.2026, v1.2.4)

| # | Запрос | Статус |
|---|--------|--------|
| 1 | About (версия, описание, @olesya_ba) | ✅ Настройки → «О приложении» |
| 2 | Создание папок + перенос писем | ✅ «＋ Папка» под текущей; move в тулбаре письма |
| 3 | Избранные папки | ✅ ★ / ☆ |
| 4 | Unread на вкладке и у папок | ✅ |
| 5 | Серверы по умолчанию в дистрибутиве | ✅ Bank + Seller |
| 6 | Трей: без создания; start–end; now-strip; prev meeting | ✅ |
| 7 | Календарь: создание + availability | ✅ |
| 8 | Accept / decline / tentative | ✅ UI + tray |
| 9 | Edit attendees + availability | ✅ |
| 10 | Read/unread icons + массовая отметка | ✅ иконки ✓/✉/✕ |
| 11 | Тёмная иконка с градиентом | ✅ bank→charcoal→seller |
| 12 | Join из body (календарь + трей) | ✅ кнопка «Подключиться» + HTML strip |
| 13 | Всплывашка напоминаний (как OWA Widget) | ✅ FloatingReminderController + UN Join |
| 14 | Поиск/подстановка контактов в письме | ✅ recent cache + dropdown + GAL TTL cache |

## Баги
- **EAS 135**: прайм сериализован + short TTL peer cache (не вечный / не disk fallback).
- **Sync status 3**: clear cache + re-prime (Stalwart Seller).
- **FolderSync status 9**: retry SyncKey=0; follow-up round soft-fail.
- **NoneType.children**: empty Sync → empty tree; find/find_all None-safe.

## Дистрибутив
`dist/AAS-mail-*-mac.zip` (только в папке проекта, не на Desktop).
Пароли не входят; серверы дефолтные.
