"""Build AppIcon.icns + Android launcher art from AppIconSource.png, and TrayIcon.png
from the AA mark (red↓ / green↑ lightning)."""
import os
import re
import subprocess

from PIL import Image, ImageDraw, ImageFilter

SOURCE = "AppIconSource.png"   # designer art: rounded tile on a dark field, 1024²

HERE = os.path.dirname(os.path.abspath(__file__))

# SVG viewBox of aa-logo-mark.svg — tray only (menu-bar needs the thin abstract mark).
VB_X, VB_Y, VB_W, VB_H = 20, 40, 360, 320
PATHS_TRAY = [
    ("#8B2E2E", "M95 248 L140 318 L200 200"),
    ("#8B2E2E", "M118 278 L178 242"),
    ("#1F5C3A", "M200 200 L260 82 L305 152"),
    ("#1F5C3A", "M222 158 L288 122"),
    ("#E07A7A", "M95 248 L72 262 L86 278 L48 302 L62 318 L28 342"),
    ("#6BCB77", "M305 152 L328 138 L314 122 L352 98 L338 82 L372 58"),
]

RED = "#EF3124"
GREEN = "#30E203"


def _stroke(draw: ImageDraw.ImageDraw, pts, color: str, width: int):
    if len(pts) < 2:
        return
    draw.line(pts, fill=color, width=width, joint="curve")
    r = max(1, width // 2)
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _render_tray_mark(size: int, *, stroke_scale: float = 1.0) -> Image.Image:
    """Muted abstract AA for the menu bar."""
    master = max(512, size * 4)
    scale = master / max(VB_W, VB_H)
    pad = int(master * 0.10)
    canvas = master + 2 * pad

    def map_pt(x, y):
        ox = pad + (master - VB_W * scale) / 2
        oy = pad + (master - VB_H * scale) / 2
        return (ox + (x - VB_X) * scale, oy + (y - VB_Y) * scale)

    def parse_path(d):
        nums = [float(n) for n in re.findall(r"[-+]?\d*\.?\d+", d)]
        return [map_pt(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]

    mark = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mark)
    sw = max(8, int(18 * scale * stroke_scale))
    for color, dpath in PATHS_TRAY:
        pts = parse_path(dpath)
        _stroke(draw, pts, color, sw)

    bbox = mark.getbbox()
    margin = int(sw * 0.7)
    bbox = (max(0, bbox[0] - margin), max(0, bbox[1] - margin),
            min(canvas, bbox[2] + margin), min(canvas, bbox[3] + margin))
    cropped = mark.crop(bbox)
    bw, bh = cropped.size
    side = max(bw, bh)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(cropped, ((side - bw) // 2, (side - bh) // 2), cropped)
    return sq.resize((size, size), Image.LANCZOS)


def _source_tile() -> Image.Image:
    """The square tile cut out of the source art (its dark surround dropped)."""
    src = Image.open(os.path.join(HERE, SOURCE)).convert("RGB")
    bg, px = src.getpixel((4, 4)), src.load()
    far = lambda p: sum(abs(p[i] - bg[i]) for i in range(3)) > 18
    w, h = src.size
    xs = [x for x in range(w) if far(px[x, h // 2])]
    ys = [y for y in range(h) if far(px[w // 2, y])]
    side = min(xs[-1] - xs[0], ys[-1] - ys[0]) + 1
    return src.crop((xs[0], ys[0], xs[0] + side, ys[0] + side)).convert("RGBA")


def _app_icon_1024() -> Image.Image:
    """macOS Dock tile: the source tile on Apple's 1024 grid, rounded, soft shadow."""
    S, T, R = 1024, 880, 210
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([72, 88, 952, 968], radius=R, fill=(0, 0, 0, 110))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(28)))
    tile = _source_tile().resize((T, T), Image.LANCZOS)
    mask = Image.new("L", (T, T), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, T - 1, T - 1], radius=R, fill=255)
    img.paste(tile, (72, 72), mask)
    return img


def _android_background(size: int = 432) -> Image.Image:
    """Adaptive-icon background (108dp @xxxhdpi). The launcher shows only the middle
    72dp in its own shape, so the tile sits at 90% — the spiral stays inside."""
    tile = _source_tile()
    out = Image.new("RGBA", (size, size), tile.getpixel((tile.width // 2, 2)))
    inner = int(size * 0.90)
    out.paste(tile.resize((inner, inner), Image.LANCZOS), ((size - inner) // 2,) * 2)
    return out.convert("RGB")


def main():
    out = _app_icon_1024()
    iconset = os.path.join(HERE, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        out.resize((base, base), Image.LANCZOS).save(os.path.join(iconset, f"icon_{base}x{base}.png"))
        out.resize((base * 2, base * 2), Image.LANCZOS).save(
            os.path.join(iconset, f"icon_{base}x{base}@2x.png"))
    out.save(os.path.join(HERE, "icon_preview.png"))
    subprocess.run(
        ["iconutil", "-c", "icns", iconset, "-o", os.path.join(HERE, "AppIcon.icns")],
        check=True)

    res = os.path.join(HERE, "..", "android", "app", "src", "main", "res", "drawable-nodpi")
    os.makedirs(res, exist_ok=True)
    _android_background().save(os.path.join(res, "ic_launcher_bg.png"))

    aa = _render_tray_mark(1024)
    aa.save(os.path.join(HERE, "TrayLogoSource.png"))
    for name, size in (("TrayIcon.png", 18), ("TrayIcon@2x.png", 36)):
        _render_tray_mark(size, stroke_scale=1.2).save(os.path.join(HERE, name))

    print("AppIcon.icns + Android ic_launcher_bg.png (from AppIconSource.png) + TrayIcon.png built")


if __name__ == "__main__":
    main()
