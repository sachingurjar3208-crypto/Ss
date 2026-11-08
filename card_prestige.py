"""PRESTIGE card (s2_theme = "prestige"): a premium collector-card Style 2 design.

  - black marble with gold veins (made in code; every player gets his own pattern)
  - big letter-spaced title on top (Cinzel serif, gold)
  - thin gold inner frame with stepped corners
  - flowing white "string-art" ribbons, one behind the player, one crossing in front at the bottom
  - player cutout (same size/spot as his Style 2 card) with a soft golden halo
  - left column: OVR, role, flag, playstyle logos
  - name standing vertically on the right side
  - 6 stats in one strip at the bottom of the frame
Output 1037 x 1517 PNG (rounded corners, transparent outside), drawn at 2x.
Font: cinzel.ttf (SIL Open Font License, see cinzel_OFL.txt).
"""
from __future__ import annotations

import hashlib
import io
import math
from functools import lru_cache
from pathlib import Path

try:                      # numpy makes the nicest marble; without it a simpler PIL marble is used
    import numpy as np
except Exception:         # pragma: no cover
    np = None
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import card_image as ci
from card_stats import card_stat_rows
from card_arena import _font, _fit, _q, W, H, S, CW, CH

_HERE = Path(__file__).parent
TITLE = "PRESTIGE"
FESTIVE_TITLE = "DIWALI"
GOLD = (226, 184, 96)
GOLD_HI = (255, 226, 150)
CREAM = (250, 236, 205)
RADIUS = 30
FRAME = (84, 236, W - 128, 1426)      # inner gold frame (wider gap on the right for the name)
STEP = 46                             # size of the stepped corners
FADE_Y0, FADE_Y1 = 1150, 1290         # player fades out above the stats strip


@lru_cache(maxsize=8)
def _cinzel(size: int, weight: int = 700):
    try:
        f = ImageFont.truetype(str(_HERE / "cinzel.ttf"), size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        return f
    except Exception:
        return _font("label.ttf", size)


def _noise(shape, cell, rng):
    h, w = shape
    g = rng.random((h // cell + 3, w // cell + 3)).astype(np.float32)
    img = Image.fromarray((g * 255).astype(np.uint8), "L").resize((w + 2 * cell, h + 2 * cell), Image.BICUBIC)
    return np.asarray(img, dtype=np.float32)[cell:cell + h, cell:cell + w] / 255.0


def _marble_pil(seed: int, festive: bool) -> Image.Image:
    """Same idea as the numpy marble, only with PIL (used when numpy isn't installed)."""
    import random
    rnd = random.Random(seed)
    w, h = CW // 2, CH // 2

    def noise(cell):
        gw, gh = w // cell + 3, h // cell + 3
        g = Image.frombytes("L", (gw, gh), bytes(rnd.getrandbits(8) for _ in range(gw * gh)))
        return g.resize((w + 2 * cell, h + 2 * cell), Image.BICUBIC).crop((cell, cell, cell + w, cell + h))
    n = Image.blend(Image.blend(noise(340), noise(150), 0.3 / 0.85), noise(60), 0.15)
    warp = Image.blend(noise(260), noise(90), 0.3)
    v = Image.blend(n, warp, 1.4 / 2.6)                     # (n*1.2 + warp*1.4) / 2.6
    vein_lut = [int(255 * max(0.0, 1 - abs(math.sin(p / 255 * 2.6 * math.pi * 2.2)) / 0.12) ** 1.3) for p in range(256)]
    smoke_lut = [int(255 * max(0.0, min(1.0, (p / 255 - 0.46) * 3.2)) ** 1.3 * 0.95) for p in range(256)]
    veins = v.point(vein_lut).filter(ImageFilter.GaussianBlur(0.8))
    smoke = warp.point(smoke_lut)
    cloud = (168, 98, 34) if festive else (150, 104, 38)
    img = Image.composite(Image.new("RGB", (w, h), tuple(10 + c for c in cloud)), Image.new("RGB", (w, h), (10, 8, 6)), smoke)
    img = Image.composite(Image.new("RGB", (w, h), (236, 190, 100)), img, veins)
    return img.resize((CW, CH), Image.BICUBIC)


@lru_cache(maxsize=32)
def _marble(seed: int, festive: bool = False) -> Image.Image:
    """Black marble with gold smoke and thin gold veins (made at half size, then scaled up)."""
    if np is None:
        return _finish_marble(_marble_pil(seed, festive), festive)
    w, h = CW // 2, CH // 2
    rng = np.random.default_rng(seed)
    n = (_noise((h, w), 340, rng) * 0.55 + _noise((h, w), 150, rng) * 0.3 + _noise((h, w), 60, rng) * 0.15)
    warp = _noise((h, w), 260, rng) * 0.7 + _noise((h, w), 90, rng) * 0.3
    v = n * 1.2 + warp * 1.4
    veins = np.clip(1.0 - np.abs(np.sin(v * math.pi * 2.2)) / 0.05, 0, 1) ** 1.3
    fine = np.clip(1.0 - np.abs(np.sin((v * 1.7 + _noise((h, w), 40, rng) * 0.25) * math.pi * 3.0)) / 0.025, 0, 1) * 0.5
    smoke = np.clip((warp - 0.46) * 3.2, 0, 1) ** 1.3
    base = np.array([10, 8, 6], np.float32)
    cloud = np.array([168, 98, 34] if festive else [150, 104, 38], np.float32)
    vein = np.array([236, 190, 100], np.float32)
    rgb = base + cloud * smoke[..., None] * 0.95
    rgb = rgb * (1 - veins[..., None]) + vein * veins[..., None]
    rgb = rgb + vein * fine[..., None] * smoke[..., None]
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB").resize((CW, CH), Image.BICUBIC)
    return _finish_marble(img, festive)


def _finish_marble(img: Image.Image, festive: bool) -> Image.Image:
    # darken the middle a little so the player stands out
    img = img.convert("RGBA")
    vg = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    ImageDraw.Draw(vg).ellipse(_q(160, 300) + _q(W - 160, 1250), fill=(0, 0, 0, 120))
    img.alpha_composite(vg.filter(ImageFilter.GaussianBlur(120 * S)))
    if festive:   # warm magenta / saffron glow at the top and bottom
        gl = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        gd = ImageDraw.Draw(gl)
        gd.ellipse(_q(-200, -260) + _q(W + 200, 420), fill=(200, 40, 110, 70))
        gd.ellipse(_q(-150, 1180) + _q(W + 150, 1700), fill=(255, 120, 30, 60))
        img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(110 * S)))
    return img


def _bez(p, t):
    a, b, c, d = p
    u = 1 - t
    return (u ** 3 * a[0] + 3 * u * u * t * b[0] + 3 * u * t * t * c[0] + t ** 3 * d[0],
            u ** 3 * a[1] + 3 * u * u * t * b[1] + 3 * u * t * t * c[1] + t ** 3 * d[1])


def _ribbon(draw, rail_a, rail_b, lines=26, twist=2.4, phase=0.0, width=2, alpha=225):
    """String-art ribbon: many curves woven between two rails, twisting along the way."""
    steps = 90
    for i in range(lines):
        off = i / (lines - 1)
        pts = []
        for k in range(steps + 1):
            t = k / steps
            a, b = _bez(rail_a, t), _bez(rail_b, t)
            w = 0.5 + 0.5 * math.sin(phase + t * twist * math.pi + off * math.pi)
            pts.append(_q(a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w))
        draw.line(pts, fill=(255, 255, 255, alpha), width=width * S, joint="curve")


def _ribbon_layer(rails, **kw) -> Image.Image:
    lay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    _ribbon(ImageDraw.Draw(lay), *rails, **kw)
    sh = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    sh.putalpha(lay.getchannel("A").filter(ImageFilter.GaussianBlur(6 * S)).point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    out.alpha_composite(sh, (4 * S, 6 * S))
    out.alpha_composite(lay)
    return out


@lru_cache(maxsize=1)
def _ribbons():
    back = _ribbon_layer((((430, -60), (820, 140), (1120, 260), (860, 820)),
                          ((640, -80), (1100, 40), (900, 480), (1100, 980))), lines=30, twist=2.0, phase=0.6, width=3)
    side = _ribbon_layer((((-60, 560), (300, 720), (40, 1000), (330, 1200)),
                          ((-90, 780), (190, 820), (330, 1060), (20, 1280))), lines=24, twist=1.7, phase=1.4, width=3)
    front = _ribbon_layer((((-40, 1230), (300, 1120), (640, 1330), (1080, 1150)),
                           ((-60, 1330), (360, 1230), (700, 1420), (1090, 1250))), lines=26, twist=2.6, phase=0.2,
                          alpha=240, width=3)
    return back, side, front


def _frame(d):
    x0, y0, x1, y1 = FRAME
    s = STEP

    def poly(o):
        a0, b0, a1, b1 = x0 + o, y0 + o, x1 - o, y1 - o
        st = s - o * 0.4
        return [(a0 + st, b0), (a1 - st, b0), (a1 - st, b0 + st * 0.0), (a1, b0 + st), (a1, b1 - st),
                (a1 - st, b1), (a0 + st, b1), (a0, b1 - st), (a0, b0 + st)]
    for o, wdt, col in ((0, 5, GOLD), (14, 2, GOLD + (170,))):
        pts = [_q(x, y) for x, y in poly(o)]
        d.line(pts + [pts[0]], fill=col, width=wdt * S, joint="curve")
    # little step blocks in the corners
    for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        d.rectangle(_q(cx - 7, cy - 7) + _q(cx + 7, cy + 7), fill=GOLD)


def _player(canvas, card_row, fg):
    try:
        import card_style2 as c2
        fg = fg.convert("RGBA")
        k = W / (c2.CROP[2] - c2.CROP[0])
        b = c2.FG_BOX
        box = {"x": int((b["x"] - c2.CROP[0]) * k * S), "y": int((b["y"] - c2.CROP[1]) * k * S),
               "w": int(b["w"] * k * S), "h": int(b["h"] * k * S)}
        resized, pos = ci._fit_foreground(fg, box, int(int(ci._col(card_row, "offset_x", 0)) * k * S),
                                          int(int(ci._col(card_row, "offset_y", 0)) * k * S),
                                          int(ci._col(card_row, "scale_pct", 100)))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(resized, pos, resized)
        # fade the photo out at the bottom. If the photo itself ends higher up (cut at the waist),
        # fade over ITS last part instead, so there is no hard straight edge.
        y0, y1 = FADE_Y0 * S, FADE_Y1 * S
        bb = layer.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
        if bb and bb[3] < y1:
            y1 = bb[3]
            y0 = min(y0, y1 - 160 * S)
        ramp = Image.new("L", (1, CH), 255)
        for y in range(max(0, y0), CH):
            t = min(1.0, (y - y0) / max(1, y1 - y0))
            ramp.putpixel((0, y), int(255 * (1 - t * t * (3 - 2 * t))))
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), ramp.resize((CW, CH))))
        a = layer.getchannel("A")
        halo = Image.new("RGBA", canvas.size, GOLD_HI + (0,))
        halo.putalpha(a.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(18 * S)).point(lambda v: int(v * 0.75)))
        canvas.alpha_composite(halo)
        canvas.alpha_composite(layer)
    except Exception:
        pass


def _vertical_text(canvas, text, font, fill, center, max_len):
    d = ImageDraw.Draw(canvas)
    f = font
    tw = d.textlength(text, font=f)
    if tw > max_len:
        f = font.font_variant(size=max(10, int(font.size * max_len / tw)))
    l, t, r, b = d.textbbox((0, 0), text, font=f, stroke_width=2 * S)
    img = Image.new("RGBA", (r - l + 8 * S, b - t + 8 * S), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((4 * S - l, 4 * S - t), text, font=f, fill=fill, stroke_width=2 * S, stroke_fill=(0, 0, 0, 170))
    img = img.rotate(90, expand=True, resample=Image.BICUBIC)
    canvas.alpha_composite(img, (int(center[0] - img.width / 2), int(center[1] - img.height / 2)))


def _fireworks(canvas, seed):
    """Static firework bursts in the background (drawn behind the player)."""
    import random
    rnd = random.Random(seed)
    lay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for (bx, by, r, col) in ((780, 330, 150, (255, 196, 80)), (420, 300, 110, (255, 110, 150)),
                             (640, 610, 120, (255, 150, 50)), (860, 700, 80, (255, 226, 150))):
        for i in range(30):
            ang = i / 30 * 2 * math.pi + rnd.uniform(-0.05, 0.05)
            rr = r * rnd.uniform(0.75, 1.0)
            for seg in range(6):                                  # dotted trails, brighter at the tip
                t0, t1 = 0.25 + seg * 0.125, 0.25 + seg * 0.125 + 0.08
                al = int(40 + 120 * seg / 5)
                d.line(_q(bx + math.cos(ang) * rr * t0, by + math.sin(ang) * rr * t0 + 10 * t0 ** 2) +
                       _q(bx + math.cos(ang) * rr * t1, by + math.sin(ang) * rr * t1 + 10 * t1 ** 2),
                       fill=col + (al,), width=2 * S)
            tx, ty = bx + math.cos(ang) * rr, by + math.sin(ang) * rr + 10
            d.ellipse(_q(tx - 3, ty - 3) + _q(tx + 3, ty + 3), fill=(255, 245, 220, 200))
        d.ellipse(_q(bx - 10, by - 10) + _q(bx + 10, by + 10), fill=col + (200,))
    glow = lay.filter(ImageFilter.GaussianBlur(14 * S))
    canvas.alpha_composite(glow)
    canvas.alpha_composite(glow)
    canvas.alpha_composite(lay)


def _festive_frame(canvas):
    """Marigolds on the frame corners + a small garland hanging from the top edge."""
    from card_rangoli import _marigold, _leaf
    x0, y0, x1, y1 = FRAME
    lay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    # garland: two dips between the corners
    pts = []
    for i in range(41):
        t = i / 40
        x = x0 + 40 + (x1 - x0 - 80) * t
        y = y0 + 6 + 34 * abs(math.sin(t * 2 * math.pi))
        pts.append((x, y))
    for i, (x, y) in enumerate(pts):
        if i % 2 == 0:
            _leaf(d, x * S, y * S, math.radians(100 if i % 4 == 0 else 80), 22 * S)
    for i, (x, y) in enumerate(pts):
        _marigold(d, x * S, y * S, 12 * S)
    for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        _marigold(d, cx * S, cy * S, 24 * S)
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.putalpha(lay.getchannel("A").filter(ImageFilter.GaussianBlur(5 * S)).point(lambda v: int(v * 0.6)))
    canvas.alpha_composite(sh, (0, 4 * S))
    canvas.alpha_composite(lay)


def build(card_row, fg_image, role: str, festive: bool = False) -> io.BytesIO:
    key = str(ci._col(card_row, "playername", "") or "x")
    seed = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    canvas = _marble(seed % 997, festive).copy()
    back, side, front = _ribbons()
    if festive:
        _fireworks(canvas, seed)
    canvas.alpha_composite(back)
    canvas.alpha_composite(side)
    d = ImageDraw.Draw(canvas)
    _frame(d)
    if festive:
        _festive_frame(canvas)

    _player(canvas, card_row, fg_image)
    canvas.alpha_composite(front)

    # title (drawn after the player, so a raised bat never covers it)
    title = " ".join(FESTIVE_TITLE if festive else TITLE)
    tf = _cinzel(96 * S, 700)
    tl = ImageDraw.Draw(canvas).textlength(title, font=tf)
    if tl > (W - 120) * S:
        tf = _cinzel(int(96 * S * (W - 120) * S / tl), 700)
    tlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tlay)
    td.text(_q(W / 2, 128), title, font=tf, fill=(0, 0, 0, 0), anchor="mm", stroke_width=5 * S, stroke_fill=(0, 0, 0, 200))
    td.text(_q(W / 2, 128), title, font=tf, fill=CREAM, anchor="mm", stroke_width=2 * S, stroke_fill=GOLD)
    canvas.alpha_composite(tlay)

    d = ImageDraw.Draw(canvas)

    # left column: OVR, role, flag, playstyles
    cx = 190
    ovr = str(ci._col(card_row, "ovr", ""))
    lay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    ld.text(_q(cx, 400), ovr, font=_font("display_bold.ttf", 150 * S), fill=GOLD_HI, anchor="ms",
            stroke_width=3 * S, stroke_fill=(0, 0, 0, 200))
    ld.text(_q(cx, 466), role, font=_font("display_bold.ttf", 60 * S), fill=CREAM, anchor="ms",
            stroke_width=3 * S, stroke_fill=(0, 0, 0, 200))
    canvas.alpha_composite(lay)
    d = ImageDraw.Draw(canvas)
    d.line((*_q(cx - 60, 496), *_q(cx + 60, 496)), fill=GOLD, width=2 * S)
    flag_done = False
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _q(cx, 556), 0, 0, 100, box=110 * S)
            flag_done = True
    except Exception:
        flag_done = False
    d = ImageDraw.Draw(canvas)
    if not flag_done:
        c = str(ci._col(card_row, "country", "") or "").upper()[:12]
        d.text(_q(cx, 556), c, font=_fit(d, c, "label.ttf", 30 * S, 140 * S), fill=CREAM, anchor="mm")
    d.line((*_q(cx - 60, 616), *_q(cx + 60, 616)), fill=GOLD, width=2 * S)
    y = 684
    for slot in (1, 2):
        try:
            logo = ci._load_playstyle_badge(card_row, slot)
        except Exception:
            logo = None
        if logo is not None:
            try:
                ci._draw_logo(canvas, logo, _q(cx, y), 0, 0, 100, box=100 * S)
                y += 116
            except Exception:
                pass

    # vertical name on the right
    name = str(ci._col(card_row, "displayname", "") or ci._col(card_row, "playername", "")).upper()
    _vertical_text(canvas, name, _font("display_bold.ttf", 84 * S), GOLD_HI, _q((FRAME[2] + W) / 2, 830), 1000 * S)

    # stats strip
    d = ImageDraw.Draw(canvas)
    sx0, sy0, sx1, sy1 = FRAME[0] + 26, 1296, FRAME[2] - 26, 1400
    strip = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(strip).rounded_rectangle(_q(sx0, sy0) + _q(sx1, sy1), 18 * S, fill=(8, 6, 4, 205),
                                            outline=GOLD + (200,), width=2 * S)
    canvas.alpha_composite(strip)
    d = ImageDraw.Draw(canvas)
    rows = card_stat_rows(card_row)[:6]
    cw = (sx1 - sx0) / max(1, len(rows))
    for i, (lab, val) in enumerate(rows):
        x = sx0 + cw * (i + 0.5)
        d.text(_q(x, sy0 + 54), str(val), font=_font("display_bold.ttf", 54 * S), fill=GOLD_HI, anchor="ms")
        d.text(_q(x, sy0 + 88), str(lab).upper()[:3], font=_font("label.ttf", 22 * S), fill=CREAM, anchor="ms")
        if i:
            xx = sx0 + cw * i
            d.line((*_q(xx, sy0 + 20), *_q(xx, sy1 - 20)), fill=GOLD + (110,), width=S)

    # bottom line
    d.text(_q(W / 2, 1470), " ".join("RUSH CRICKET"), font=_cinzel(26 * S, 600), fill=GOLD, anchor="mm")
    if festive:
        from card_rangoli import _diya
        _diya(canvas, 232, 1468, 0.6)
        _diya(canvas, W - 300, 1468, 0.6)
        d = ImageDraw.Draw(canvas)

    # rounded card + thin gold edge
    d.rounded_rectangle(_q(6, 6) + _q(W - 6, H - 6), (RADIUS - 4) * S, outline=GOLD + (200,), width=3 * S)
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CW - 1, CH - 1), RADIUS * S, fill=255)
    canvas.putalpha(mask)
    out = canvas.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
