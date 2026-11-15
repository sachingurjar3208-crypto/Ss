"""Gold / metallic UI pieces for the draft screen (themes with style="gold", e.g. Season 1).

Pure Pillow. Every piece is drawn at 2x on its own layer and scaled down, so edges stay smooth.
draft_image.py calls these instead of its flat shapes when the theme asks for them.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SS = 2                                                   # supersampling

GOLD = [(255, 244, 196), (255, 214, 102), (214, 150, 34), (255, 206, 92), (150, 96, 18)]
SILVER = [(255, 255, 255), (226, 232, 240), (160, 170, 186), (236, 240, 246), (120, 128, 144)]
DARK = [(16, 80, 60), (6, 44, 34), (3, 26, 20)]
PURPLE = [(236, 214, 255), (180, 130, 255), (110, 56, 200), (170, 120, 250), (70, 30, 140)]
EMERALD = [(210, 255, 230), (110, 230, 160), (30, 150, 96), (100, 220, 150), (14, 90, 58)]
POOL_STOPS = {"A": GOLD, "B": PURPLE, "C": EMERALD}
POOL_EDGE = {"A": (255, 236, 170), "B": (226, 200, 255), "C": (190, 255, 220)}
POOL_INK = {"A": (60, 34, 4), "B": (40, 10, 80), "C": (4, 50, 30)}


def _vgrad(w: int, h: int, stops: list) -> Image.Image:
    """Vertical gradient through the colour stops."""
    col = Image.new("RGB", (1, h))
    n = len(stops) - 1
    for y in range(h):
        t = y / max(1, h - 1) * n
        i = min(n - 1, int(t))
        f = t - i
        a, b = stops[i], stops[i + 1]
        col.putpixel((0, y), tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3)))
    return col.resize((w, h))


def _plate_pts(x0, y0, x1, y1, slant):
    """Parallelogram with a slanted left and right side."""
    return [(x0 + slant, y0), (x1, y0), (x1 - slant, y1), (x0, y1)]


def plate(base: Image.Image, box, stops=GOLD, slant=18, border=(255, 236, 170), glow=None, edge=3):
    """Metallic slanted plate (pool tab, buttons, guarantee label)."""
    x0, y0, x1, y1 = box
    pad = 40
    w, h = (x1 - x0 + 2 * pad) * SS, (y1 - y0 + 2 * pad) * SS
    pts = [((px - x0 + pad) * SS, (py - y0 + pad) * SS) for px, py in _plate_pts(x0, y0, x1, y1, slant)]
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if glow:
        g = Image.new("RGBA", (w, h), glow + (0,))
        g.putalpha(mask.filter(ImageFilter.GaussianBlur(14 * SS)).point(lambda v: int(v * 0.9)))
        layer.alpha_composite(g)
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(6 * SS)).point(lambda v: int(v * 0.6)))
    layer.alpha_composite(shadow, (0, 4 * SS))
    fill = _vgrad(w, (y1 - y0) * SS, stops)
    body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    body.paste(fill, (0, pad * SS))
    body.putalpha(mask)
    layer.alpha_composite(body)
    # glossy top half
    gloss = Image.new("L", (w, h), 0)
    ImageDraw.Draw(gloss).rectangle((0, 0, w, (pad + (y1 - y0) * 0.45) * SS), fill=70)
    gl = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    gl.putalpha(ImageChops.multiply(gloss, mask))
    layer.alpha_composite(gl)
    if border:
        ImageDraw.Draw(layer).line(pts + [pts[0]], fill=border + (255,), width=edge * SS, joint="curve")
    layer = layer.resize((w // SS, h // SS), Image.LANCZOS)
    base.paste(layer, (x0 - pad, y0 - pad), layer) if base.mode == "RGB" else base.alpha_composite(layer, (x0 - pad, y0 - pad))


def fancy_text(base: Image.Image, xy, text: str, font_path, size: int, stops=GOLD, anchor="mm",
               italic=0.18, outline=(60, 34, 4), outline_w=3, shadow=True, glow=None):
    """Text filled with a metallic gradient, slanted (italic), with outline + drop shadow."""
    font = ImageFont.truetype(str(font_path), size * SS)
    tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
    l, t, r, b = tmp.textbbox((0, 0), text, font=font, stroke_width=outline_w * SS)
    tw, th = r - l, b - t
    pad = 30 * SS
    shear = int(th * italic)
    w, h = tw + 2 * pad + shear, th + 2 * pad
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((pad - l, pad - t), text, font=font, fill=255)
    stroke = Image.new("L", (w, h), 0)
    ImageDraw.Draw(stroke).text((pad - l, pad - t), text, font=font, fill=255,
                                stroke_width=outline_w * SS, stroke_fill=255)
    if italic:
        tr = (1, italic, -italic * h, 0, 1, 0)                 # lean the top to the right
        mask = mask.transform((w, h), Image.AFFINE, tr, Image.BICUBIC)
        stroke = stroke.transform((w, h), Image.AFFINE, tr, Image.BICUBIC)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if glow:
        g = Image.new("RGBA", (w, h), glow + (0,))
        g.putalpha(stroke.filter(ImageFilter.GaussianBlur(10 * SS)).point(lambda v: int(v * 0.8)))
        layer.alpha_composite(g)
    if shadow:
        s = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        s.putalpha(stroke.filter(ImageFilter.GaussianBlur(4 * SS)).point(lambda v: int(v * 0.75)))
        layer.alpha_composite(s, (0, 5 * SS))
    o = Image.new("RGBA", (w, h), outline + (0,))
    o.putalpha(stroke)
    layer.alpha_composite(o)
    fill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fill.paste(_vgrad(w, th, stops), (0, pad))
    fill.putalpha(mask)
    layer.alpha_composite(fill)
    layer = layer.resize((w // SS, h // SS), Image.LANCZOS)
    lw, lh = layer.size
    x, y = xy
    ax = {"l": 0, "m": 0.5, "r": 1}[anchor[0]]
    ay = {"t": 0, "m": 0.5, "b": 1, "s": 0.85}[anchor[1]]
    px = int(x - pad / SS - (tw / SS) * ax)
    py = int(y - pad / SS - (th / SS) * ay)
    base.paste(layer, (px, py), layer)


def card_glow(base: Image.Image, box, color=(255, 200, 80)):
    """Soft gold glow behind a card."""
    x0, y0, x1, y1 = box
    pad = 30
    m = Image.new("L", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), 0)
    ImageDraw.Draw(m).rounded_rectangle((pad, pad, pad + x1 - x0, pad + y1 - y0), radius=16, fill=150)
    m = m.filter(ImageFilter.GaussianBlur(12))
    base.paste(Image.new("RGB", m.size, color), (x0 - pad, y0 - pad), m)


def card_frame(base: Image.Image, box, color=(255, 214, 110)):
    """Thin gold frame around a card."""
    d = ImageDraw.Draw(base)
    x0, y0, x1, y1 = box
    d.rounded_rectangle((x0 - 3, y0 - 3, x1 + 2, y1 + 2), radius=14, outline=color, width=3)
