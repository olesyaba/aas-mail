# AAS mail — Design System

Клиент почты и календаря для **Alfa-Bank** и **Alfa-Seller** (Exchange ActiveSync).
Визуал опирается на dual-brand: Vinho банка + Verde/лайм Селлера (см. `_ref/alfa-seller/`).

## Theme

Светлый рабочий холст, акцент сменяется по активному аккаунту. Лайм `#30E203` — только CTA Селлера и data-focus, не длинный текст. Без банковского синего Outlook-клона и без purple AI-градиентов.

## Colors

| Token | Hex | Role |
|-------|-----|------|
| `--aas-bank` | `#501820` | Акцент Bank, вкладки, unread |
| `--aas-seller` | `#003830` | Акцент Seller |
| `--aas-lime` | `#30E203` | Primary CTA на Seller, маркер |
| `--aas-ink` | `#000000` | Ink-карточки, secondary CTA |
| `--aas-alfa-red` | `#EF3124` | Danger / бренд-марка |
| `--aas-mint` | `#E4FBDD` | Soft success |
| `--aas-panel` | `#FFFFFF` | Панели |
| `--aas-bg` | `#F5F6F8` | Фон app |
| `--aas-muted` | `#66707A` | Meta |
| `--aas-line` | `#E3E6EA` | Разделители |

Темы аккаунта: `body.theme-bank` / `body.theme-seller` (legacy: `body.green` = seller).
В dark mode акценты светлеют (`#c47a84` / `#3d8f7a`).

Оформление (`html[data-theme]`): `light` / `dark` / `system` + StylesBA
(`navy-orange`, `royal-velvet`, `eclipse-almond` и `*-light`). У StylesBA основная —
тёмная (modeHint), светлая — отдельный вариант.

## Typography

**Golos Text** 400–700. Шкала: 12 / 13 / 14 / 16 / 18 / 22. Display только в kit-hero и About.

## Radius / motion

Pills `999px`, inputs `12px`, cards `10px`, ink cards `20px`.
Motion `180ms` `cubic-bezier(0.16, 1, 0.3, 1)`. `prefers-reduced-motion` → `--aas-dur: 0`.

## Product map

| Функция клиента | Компоненты |
|-----------------|------------|
| Dual accounts | `.aas-tab--bank/seller`, `.aas-topnav--*`, dual-bar, acct cards |
| Папки + избранное + unread | `.aas-folders`, `.aas-folder`, `.aas-badge`, pin |
| Список / read icons | `.aas-row`, `.aas-row__ico`, unread |
| Письмо / вложения | `.aas-mc`, `.aas-att`, reader-tools |
| Compose / move | forms, `.aas-btn`, modal |
| Календарь day/week | `.aas-seg`, `.aas-ev--*`, `.aas-chip`, `.aas-nowline` |
| RSVP + attendees | `.aas-rsvp`, `.aas-attendee`, `.aas-avail` |
| Трей | `.aas-tray`, now-strip, rsvp |
| Настройки / About | `.aas-settings-tabs`, `.aas-about`, acct blocks |
| Sync / connection | `.aas-sync`, `.aas-status-dot` |

## Anti-patterns

- Синий Outlook accent на Bank/Seller
- Лайм на длинных абзацах
- Cards-everywhere без интерактива
- Inter / system-only без Golos
