# AAS mail UI Kit

Дизайн-система веб-клиента AAS mail (почта + календарь, Bank / Seller).

## Файлы

| Path | Role |
|------|------|
| `tokens.css` | CSS-переменные, темы bank/seller, dark, reduced motion |
| `components.css` | Классы `.aas-*` под функции клиента |
| `index.html` | Живой showcase |
| `DESIGN.md` | Токены + product map |
| `_ref/alfa-seller/` | Исходный кит Альфа-Селлер (лайм / ink) |

## Подключение

```html
<link href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="./ui-kit/tokens.css" />
<link rel="stylesheet" href="./ui-kit/components.css" />
```

Тема аккаунта на `<body>`:

```html
<body class="aas-root theme-bank">   <!-- или theme-seller / green -->
```

## Showcase

```bash
open "web/ui-kit/index.html"
# или через локальный webapp: http://127.0.0.1:8780/ui-kit/
```

(Сервер отдаёт статику из `web/` — путь зависит от `webapp.py`.)

## Миграция

Клиент `web/index.html` уже на ките: Golos Text, `tokens.css` + `components.css` + `app.css`, классы `.aas-*`, темы `theme-bank` / `theme-seller` (legacy `body.green` = seller).

Showcase: `web/ui-kit/index.html` или `http://127.0.0.1:8780/ui-kit/`.
