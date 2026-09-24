"""Build AppIcon.icns + TrayIcon.png from the AA mark (red↓ / green↑ lightning)."""
import os
import re
import subprocess

from PIL import Image, ImageDraw, ImageFilter

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


def _lerp_pt(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


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


def _render_app_mark(size: int) -> Image.Image:
    """Dock mark: one clear A, left red / right green, lightning tips on the bases."""
    S = 1200
    mark = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mark)
    aw = 92
    tip_w = 48

    # Classic upright A, centred.
    apex = (600, 220)
    left = (260, 900)
    right = (940, 900)

    # Left half (red): left leg + left half of crossbar
    bar_l = _lerp_pt(apex, left, 0.58)
    bar_r = _lerp_pt(apex, right, 0.58)
    bar_m = _lerp_pt(bar_l, bar_r, 0.5)
    _stroke(draw, [left, apex], RED, aw)
    _stroke(draw, [bar_l, bar_m], RED, max(2, int(aw * 0.92)))

    # Right half (green): right leg + right half of crossbar
    _stroke(draw, [apex, right], GREEN, aw)
    _stroke(draw, [bar_m, bar_r], GREEN, max(2, int(aw * 0.92)))

    # Lightning tips from each base (outward, not competing with the letter)
    _stroke(draw, [
        left,
        (200, 960), (150, 1020), (90, 980), (40, 1060),
    ], RED, tip_w)
    _stroke(draw, [
        right,
        (1000, 960), (1050, 1020), (1110, 980), (1160, 1060),
    ], GREEN, tip_w)

    bbox = mark.getbbox()
    margin = aw
    bbox = (max(0, bbox[0] - margin), max(0, bbox[1] - margin),
            min(S, bbox[2] + margin), min(S, bbox[3] + margin))
    cropped = mark.crop(bbox)
    bw, bh = cropped.size
    side = max(bw, bh)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(cropped, ((side - bw) // 2, (side - bh) // 2), cropped)
    return sq.resize((size, size), Image.LANCZOS)


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def _dark_gradient_tile(size: int, radius: int) -> Image.Image:
    """Bank burgundy → deep charcoal → seller green diagonal on a rounded tile."""
    c0 = (80, 24, 32, 255)   # #501820 bank
    c1 = (18, 22, 28, 255)   # charcoal
    c2 = (0, 56, 48, 255)    # #003830 seller

    N = 64
    small = Image.new("RGBA", (N, N))
    px = small.load()
    for y in range(N):
        for x in range(N):
            t = (x + y) / (2 * (N - 1))
            px[x, y] = _lerp(c0, c1, t * 2) if t < 0.5 else _lerp(c1, c2, (t - 0.5) * 2)
    tile = small.resize((size, size), Image.LANCZOS)

    vig = Image.new("L", (size, size), 0)
    ImageDraw.Draw(vig).ellipse(
        [int(size * 0.08), int(size * 0.08), int(size * 0.92), int(size * 0.92)], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(int(size * 0.12)))
    lift = Image.new("RGBA", (size, size), (255, 255, 255, 18))
    lift.putalpha(vig.point(lambda v: int(v * 0.07)))
    tile = Image.alpha_composite(tile, lift)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out


def _app_icon_1024() -> Image.Image:
    """macOS Dock tile: dark brand gradient + readable AA + lightning."""
    S = 1024
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([72, 88, 952, 968], radius=210, fill=(0, 0, 0, 110))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(28)))

    tile = _dark_gradient_tile(880, radius=210)
    img.paste(tile, (72, 72), tile)

    gloss = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).rounded_rectangle([72, 72, 952, 480], radius=210, fill=(255, 255, 255, 28))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([72, 72, 952, 952], radius=210, fill=255)
    gloss.putalpha(Image.composite(gloss.split()[3], Image.new("L", (S, S), 0), mask))
    img.alpha_composite(gloss)

    mark = _render_app_mark(640)
    ox = (S - mark.size[0]) // 2
    oy = (S - mark.size[1]) // 2 + 6
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

    aa = _render_tray_mark(1024)
    aa.save(os.path.join(HERE, "TrayLogoSource.png"))
    for name, size in (("TrayIcon.png", 18), ("TrayIcon@2x.png", 36)):
        _render_tray_mark(size, stroke_scale=1.2).save(os.path.join(HERE, name))

    print("AppIcon.icns (split A + lightning) + TrayIcon.png built")


if __name__ == "__main__":
    main()
