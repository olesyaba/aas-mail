"""Build AppIcon.icns + TrayIcon.png from the AA mark (red↓ / green↑ lightning)."""
import os
import re
import subprocess

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))

# SVG viewBox of aa-logo-mark.svg
VB_X, VB_Y, VB_W, VB_H = 20, 40, 360, 320
PATHS = [
    ("#8B2E2E", "M95 248 L140 318 L200 200"),
    ("#8B2E2E", "M118 278 L178 242"),
    ("#1F5C3A", "M200 200 L260 82 L305 152"),
    ("#1F5C3A", "M222 158 L288 122"),
    ("#E07A7A", "M95 248 L72 262 L86 278 L48 302 L62 318 L28 342"),
    ("#6BCB77", "M305 152 L328 138 L314 122 L352 98 L338 82 L372 58"),
]


def _render_aa_mark(size: int, *, stroke_scale: float = 1.0) -> Image.Image:
    """AA lightning mark on a fully transparent square."""
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
    for color, dpath in PATHS:
        pts = parse_path(dpath)
        if len(pts) < 2:
            continue
        draw.line(pts, fill=color, width=sw)
        r = sw // 2
        for p in pts:
            draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=color)

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


def _app_icon_1024() -> Image.Image:
    """macOS Dock tile: soft rounded square + AA mark (no blue envelope)."""
    S = 1024
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Soft shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([72, 88, 952, 968], radius=210, fill=(20, 30, 40, 90))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(28)))

    # Warm off-white tile (reads well in light and dark Dock themes)
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle([72, 72, 952, 952], radius=210, fill=(248, 246, 242, 255))
    # Subtle top gloss
    gloss = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).rounded_rectangle([72, 72, 952, 520], radius=210, fill=(255, 255, 255, 55))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([72, 72, 952, 952], radius=210, fill=255)
    gloss.putalpha(Image.composite(gloss.split()[3], Image.new("L", (S, S), 0), mask))
    tile.alpha_composite(gloss)
    img.alpha_composite(tile)

    # AA mark centered with padding inside the tile
    mark = _render_aa_mark(680, stroke_scale=1.15)
    ox = (S - mark.size[0]) // 2
    oy = (S - mark.size[1]) // 2 + 8
    img.paste(mark, (ox, oy), mark)
    return img


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

    # Menu bar: transparent AA only (no tile)
    aa = _render_aa_mark(1024)
    aa.save(os.path.join(HERE, "TrayLogoSource.png"))
    for name, size in (("TrayIcon.png", 18), ("TrayIcon@2x.png", 36)):
        _render_aa_mark(size, stroke_scale=1.2).save(os.path.join(HERE, name))

    print("AppIcon.icns + TrayIcon.png (AA mark) built")


if __name__ == "__main__":
    main()
