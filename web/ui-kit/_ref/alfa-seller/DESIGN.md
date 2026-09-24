# Design System — Альфа-Селлер

Источник: презентация «Альфа-Селлер · Позиционирование» (81 слайд).

## Theme
Светлая ecom-платформа: чистый белый холст, чёрная типографика, электрический лайм `#30E203` как сигнал роста, красный Альфа `#EF3124` как наследие банка. Плоские блоки, пилюли, толстые зелёно-чёрные полосы — без мягких теней и без «банковского синего».

## Colors

| Token | Hex | Role |
|-------|-----|------|
| `--as-white` | `#FFFFFF` | Холст |
| `--as-ink` | `#000000` | Текст, тёмные поверхности |
| `--as-lime` | `#30E203` | Акцент, CTA, данные, маркеры |
| `--as-alfa-red` | `#EF3124` | Бренд Альфа-Банка, критичные статусы |
| `--as-surface` | `#F2F2F2` | Вторичный фон, таблицы |
| `--as-mint` | `#E4FBDD` | Callout / soft success |
| `--as-muted` | `#8A9BA8` | Подписи, meta |
| `--as-line` | `#E6E6E6` | Разделители |

## Typography
- **UI / body:** [Golos Text](https://fonts.google.com/specimen/Golos+Text) — геометрический гротеск с кириллицей, близкий к слайдам.
- **Data / mono:** `ui-monospace`, SF Mono, Menlo — только для цифр в таблицах при необходимости.
- Шкала: 12 / 14 / 16 / 20 / 28 / 40 / 56. Заголовки `font-weight: 700`, body `400–500`.
- Letter-spacing display: `-0.02em` … `-0.03em`.

## Components
- **Pill tags** — fully rounded, чёрный фон / белый текст; lime-pill для ключевого месседжа.
- **Buttons** — pill primary (lime + ink), secondary (ink + white), ghost (line), danger (alfa-red).
- **Cards** — белая с тонкой линией; **ink card** с `border-radius: 28px` и белым текстом.
- **Marker** — короткий горизонтальный lime-бар над блоком (как на слайде ценности).
- **Dual bar** — сегмент lime + black (фирменный разделитель презентации).
- **Tables** — без вертикальных линий; lime-bars для приоритетных сегментов.
- **Icon disc** — чёрный круг, тонкая lime-обводка, белая глиф.

## Layout
Широкие поля, асимметрия бренд-слайдов → в продукте: 12-колоночная сетка, gutter 24, max content 1120–1280. Радиусы: 0 (bars/charts), 12 (inputs), 999 (pills), 28 (ink cards).

## Elevation
Плоскость по умолчанию. Тень только у модалок/dropdown: `0 16px 40px rgb(0 0 0 / 12%)`.

## Motion
150–220ms `cubic-bezier(0.16, 1, 0.3, 1)`. Hover: лёгкий translateY(-1px) или смена фона. Без bounce.
