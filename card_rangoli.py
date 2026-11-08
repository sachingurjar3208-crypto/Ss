"""RANGOLI card (s2_theme = "rangoli"): a festive Diwali Style 2 design.

  - shape: a pointed Indian arch (jharokha) on top, straight sides, rounded bottom corners
  - thick gold rim + thin inner gold line, a small diya sitting on the arch's peak
  - deep maroon/magenta background with a big gold rangoli mandala behind the player
  - a marigold toran (flower garland) hanging along the arch
  - left column: OVR, role, flag, playstyle logos
  - player cutout (same size/spot as his Style 2 card), fading out above the name
  - name, gold divider, 6 stats in two columns, two diyas and "DIWALI 2026" at the bottom
Output 1037 x 1517 PNG (transparent outside the card), drawn at 2x.
"""
from __future__ import annotations

import io
import math
import random
from datetime import datetime
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

import card_image as ci
from card_stats import card_stat_rows
from card_arena import _font, _fit, _q, W, H, S, CW, CH
from card_crest import _rim, _player as _crest_player

GOLD = (240, 196, 90)
CREAM = (255, 230, 168)
MAROON = (60, 8, 30)
MARIGOLD = (255, 140, 20)
MARIGOLD_DK = (214, 86, 10)
LEAF = (46, 120, 50)
RIM = 22
ARCH_BASE = 330            # y where the arch meets the straight sides
TOP = 70                   # y of the arch's peak (below the diya)
LEFT, RIGHT, BOTTOM = 34, W - 34, H - 34
CORNER = 70                # bottom corner radius
LABEL = f"DIWALI {datetime.now().year}"


def _bezier(p0, p1, p2, p3, n=60):
    out = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _arch(left, right, base, top):
    """Left-to-right points of an ogee arch: bulges out, then curls in to a point at the top."""
    mid = (left + right) / 2
    half = (right - left) / 2
    l = _bezier((left, base), (left, base - (base - top) * 0.78), (mid - half * 0.36, top + (base - top) * 0.30), (mid, top))
    r = _bezier((mid, top), (mid + half * 0.36, top + (base - top) * 0.30), (right, base - (base - top) * 0.78), (right, base))
    return l + r[1:]


def _outline():
    pts = _arch(LEFT, RIGHT, ARCH_BASE, TOP)
    # right side down, rounded bottom corners, left side up
    for i in range(0, 91, 10):
        a = math.radians(i)
        pts.append((RIGHT - CORNER + CORNER * math.cos(a), BOTTOM - CORNER + CORNER * math.sin(a)))
    for i in range(90, 181, 10):
        a = math.radians(i)
        pts.append((LEFT + CORNER + CORNER * math.cos(a), BOTTOM - CORNER + CORNER * math.sin(a)))
    return pts


@lru_cache(maxsize=1)
def _masks():
    outer = Image.new("L", (CW, CH), 0)
    ImageDraw.Draw(outer).polygon([_q(x, y) for x, y in _outline()], fill=255)
    inner = outer.filter(ImageFilter.GaussianBlur(RIM * S * 0.6)).point(lambda v: 255 if v >= 250 else 0)
    inner = inner.filter(ImageFilter.MinFilter(9))
    line = inner.filter(ImageFilter.MinFilter(13))
    return outer, inner, line


def _petal(cx, cy, ang, r0, r1, width):
    """Pointed petal from radius r0 to r1 around (cx, cy), pointing along `ang`."""
    pts = []
    for k in range(13):
        t = k / 12
        rr = r0 + (r1 - r0) * t
        w = width * math.sin(math.pi * t) ** 0.8
        pts.append((rr, w))
    for k in range(12, -1, -1):
        t = k / 12
        rr = r0 + (r1 - r0) * t
        w = -width * math.sin(math.pi * t) ** 0.8
        pts.append((rr, w))
    ca, sa = math.cos(ang), math.sin(ang)
    return [(cx + rr * ca - w * sa, cy + rr * sa + w * ca) for rr, w in pts]


@lru_cache(maxsize=1)
def _background() -> Image.Image:
    bg = Image.new("RGB", (CW, CH))
    d = ImageDraw.Draw(bg)
    top, bot = (128, 22, 66), (36, 5, 20)
    for y in range(CH):
        t = (y / CH) ** 0.9
        d.line((0, y, CW, y), fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    bg = bg.convert("RGBA")
    # warm glow behind the player
    glow = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(_q(170, 160) + _q(930, 900), fill=(255, 150, 60, 85))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90 * S)))

    # rangoli mandala
    cx, cy = 560 * S, 520 * S
    man = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    md = ImageDraw.Draw(man)
    rings = [  # (n petals, r0, r1, width, colour, alpha, rotate)
        (8, 30, 120, 34, (255, 200, 90), 70, 0.0),
        (16, 120, 215, 26, (255, 120, 60), 60, 0.5),
        (16, 205, 300, 30, (255, 210, 120), 55, 0.0),
        (24, 300, 385, 20, (255, 90, 120), 50, 0.5),
        (32, 385, 455, 15, (255, 200, 90), 45, 0.0),
    ]
    for n, r0, r1, w, col, al, rot in rings:
        for i in range(n):
            ang = (i + rot) / n * 2 * math.pi
            pts = _petal(cx, cy, ang, r0 * S, r1 * S, w * S)
            md.polygon(pts, fill=col + (al,), outline=GOLD + (min(255, al + 50),))
    for r, al in ((120, 90), (300, 70), (455, 60)):
        md.ellipse((cx - r * S, cy - r * S, cx + r * S, cy + r * S), outline=GOLD + (al,), width=3 * S)
    for i in range(48):                                       # dot ring
        ang = i / 48 * 2 * math.pi
        x, y = cx + math.cos(ang) * 480 * S, cy + math.sin(ang) * 480 * S
        md.ellipse((x - 5 * S, y - 5 * S, x + 5 * S, y + 5 * S), fill=GOLD + (80,))
    bg.alpha_composite(man)

    # tiny sparkles
    rnd = random.Random(11)
    sp = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sp)
    for _ in range(70):
        x, y, r = rnd.uniform(60, W - 60) * S, rnd.uniform(120, 1000) * S, rnd.uniform(1.5, 3.5) * S
        sd.ellipse((x - r, y - r, x + r, y + r), fill=(255, 220, 150, rnd.randint(60, 150)))
    bg.alpha_composite(sp.filter(ImageFilter.GaussianBlur(S)))

    # darker bottom for the text
    dark = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dark)
    for y in range(880 * S, CH):
        dd.line((0, y, CW, y), fill=(20, 0, 10, min(150, int(150 * (y - 880 * S) / (240 * S)))))
    bg.alpha_composite(dark)
    return bg


def _marigold(d, x, y, r):
    """A marigold flower: ring of petals + dark ring + yellow centre (in 2x px)."""
    for i in range(10):
        a = i / 10 * 2 * math.pi
        px, py = x + math.cos(a) * r * 0.55, y + math.sin(a) * r * 0.55
        d.ellipse((px - r * 0.55, py - r * 0.55, px + r * 0.55, py + r * 0.55), fill=MARIGOLD)
    d.ellipse((x - r * 0.75, y - r * 0.75, x + r * 0.75, y + r * 0.75), fill=MARIGOLD_DK)
    d.ellipse((x - r * 0.5, y - r * 0.5, x + r * 0.5, y + r * 0.5), fill=(255, 176, 30))
    d.ellipse((x - r * 0.2, y - r * 0.2, x + r * 0.2, y + r * 0.2), fill=(255, 220, 90))


def _leaf(d, x, y, ang, ln):
    pts = _petal(x, y, ang, 0, ln, ln * 0.32)
    d.polygon(pts, fill=LEAF)
    d.line((x, y, x + math.cos(ang) * ln * 0.9, y + math.sin(ang) * ln * 0.9), fill=(120, 190, 90), width=S)


def _toran(canvas):
    """Marigold garland along the inside of the arch, with hanging strings."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    path = _arch(LEFT + 48, RIGHT - 48, ARCH_BASE + 40, TOP + 62)
    # even spacing along the curve
    acc, marks, last = 0.0, [], path[0]
    marks.append(path[0])
    for p in path[1:]:
        acc += math.dist(p, last)
        last = p
        if acc >= 40:
            marks.append(p)
            acc = 0.0
    marks = [m for m in marks if m[1] < 250]                 # only the top of the arch (keeps the OVR clear)
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sdd = ImageDraw.Draw(sh)
    for k, (x, y) in enumerate(marks):
        sdd.ellipse(_q(x - 20, y - 16) + _q(x + 20, y + 24), fill=(0, 0, 0, 120))
    layer.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6 * S)))
    d = ImageDraw.Draw(layer)
    for k, (x, y) in enumerate(marks):
        if k % 2 == 0:
            _leaf(d, x * S, y * S, math.radians(110 if x < W / 2 else 70), 34 * S)
        _marigold(d, x * S, y * S, 19 * S)
    # hanging strings at a few points
    n = len(marks)
    for k in (n // 4, n - 1 - n // 4):
        x, y = marks[k]
        d.line((*_q(x, y + 16), *_q(x, y + 108)), fill=(255, 210, 120), width=2 * S)
        for j, yy in enumerate((y + 40, y + 70, y + 98)):
            _marigold(d, x * S, yy * S, (15 - j * 2) * S)
        _leaf(d, x * S, (y + 112) * S, math.radians(90), 26 * S)
    canvas.alpha_composite(layer)


def _diya(canvas, x, y, scale=1.0):
    """A clay diya with a flame (x, y = centre of the bowl's top, output px)."""
    s = scale
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse(_q(x - 70 * s, y - 110 * s) + _q(x + 70 * s, y + 20 * s), fill=(255, 150, 40, 120))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(26 * S * s)))
    d = ImageDraw.Draw(canvas)
    # bowl
    d.chord(_q(x - 52 * s, y - 34 * s) + _q(x + 52 * s, y + 34 * s), 0, 180, fill=(170, 70, 26))
    d.chord(_q(x - 52 * s, y - 34 * s) + _q(x + 52 * s, y + 34 * s), 0, 180, outline=GOLD, width=3 * S)
    d.ellipse(_q(x - 52 * s, y - 9 * s) + _q(x + 52 * s, y + 9 * s), fill=(110, 40, 14), outline=GOLD, width=3 * S)
    d.polygon([_q(x + 40 * s, y - 2 * s), _q(x + 66 * s, y - 12 * s), _q(x + 48 * s, y + 8 * s)], fill=(170, 70, 26), outline=GOLD)
    for i in range(5):                                       # little dots on the bowl
        xx = x - 30 * s + i * 15 * s
        d.ellipse(_q(xx - 3 * s, y + 16 * s) + _q(xx + 3 * s, y + 22 * s), fill=GOLD)
    # flame
    fx, fy = x + 54 * s, y - 14 * s
    d.polygon([_q(fx - 11 * s, fy), _q(fx, fy - 46 * s), _q(fx + 11 * s, fy)], fill=(255, 150, 30))
    d.ellipse(_q(fx - 11 * s, fy - 12 * s) + _q(fx + 11 * s, fy + 8 * s), fill=(255, 150, 30))
    d.polygon([_q(fx - 6 * s, fy), _q(fx, fy - 30 * s), _q(fx + 6 * s, fy)], fill=(255, 240, 180))
    d.ellipse(_q(fx - 6 * s, fy - 6 * s) + _q(fx + 6 * s, fy + 5 * s), fill=(255, 240, 180))


def _stroked(canvas, xy, text, font, fill, stroke, anchor):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=fill, anchor=anchor, stroke_width=3 * S, stroke_fill=stroke)
    canvas.alpha_composite(layer)


def _divider(d, y, half=330):
    cx = W / 2
    d.line((*_q(cx - half, y), *_q(cx - 22, y)), fill=GOLD + (220,), width=2 * S)
    d.line((*_q(cx + 22, y), *_q(cx + half, y)), fill=GOLD + (220,), width=2 * S)
    d.polygon([_q(cx, y - 12), _q(cx + 12, y), _q(cx, y + 12), _q(cx - 12, y)], fill=GOLD)


def build(card_row, fg_image, role: str) -> io.BytesIO:
    outer, inner, line = _masks()
    canvas = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))

    sh = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    sh.putalpha(outer.filter(ImageFilter.GaussianBlur(10 * S)).point(lambda v: int(v * 0.5)))
    canvas.alpha_composite(sh)
    rim = _rim().copy()
    rim.putalpha(outer)
    canvas.alpha_composite(rim)
    inside = _background().copy()
    inside.putalpha(inner)
    canvas.alpha_composite(inside)
    edge = ImageChops.subtract(line.filter(ImageFilter.MaxFilter(5)), line)
    il = Image.new("RGBA", (CW, CH), GOLD + (255,))
    il.putalpha(edge.point(lambda v: int(v * 0.8)))
    canvas.alpha_composite(il)

    _toran(canvas)
    _crest_player(canvas, card_row, fg_image, line)
    _diya(canvas, W / 2 - 54, TOP - 4, 0.62)                 # on the arch's peak
    d = ImageDraw.Draw(canvas)

    # left column
    cx = 190
    ovr = str(ci._col(card_row, "ovr", ""))
    _stroked(canvas, _q(cx, 400), ovr, _font("display_bold.ttf", 160 * S), CREAM, MAROON + (200,), "ms")
    _stroked(canvas, _q(cx, 474), role, _font("display_bold.ttf", 66 * S), CREAM, MAROON + (200,), "ms")
    d = ImageDraw.Draw(canvas)
    d.line((*_q(cx - 64, 506), *_q(cx + 64, 506)), fill=GOLD + (220,), width=2 * S)
    flag_done = False
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _q(cx, 568), 0, 0, 100, box=116 * S)
            flag_done = True
    except Exception:
        flag_done = False
    d = ImageDraw.Draw(canvas)
    if not flag_done:
        c = str(ci._col(card_row, "country", "") or "").upper()[:12]
        d.text(_q(cx, 568), c, font=_fit(d, c, "label.ttf", 30 * S, 140 * S), fill=CREAM, anchor="mm")
    d.line((*_q(cx - 64, 630), *_q(cx + 64, 630)), fill=GOLD + (220,), width=2 * S)
    y = 700
    for slot in (1, 2):
        try:
            logo = ci._load_playstyle_badge(card_row, slot)
        except Exception:
            logo = None
        if logo is not None:
            try:
                ci._draw_logo(canvas, logo, _q(cx, y), 0, 0, 100, box=104 * S)
                y += 120
            except Exception:
                pass

    # name + stats
    name = str(ci._col(card_row, "displayname", "") or ci._col(card_row, "playername", "")).upper()
    d = ImageDraw.Draw(canvas)
    f = _fit(d, name, "display_bold.ttf", 100 * S, 790 * S)
    _stroked(canvas, _q(W / 2, 1040), name, f, CREAM, MAROON + (230,), "ms")
    d = ImageDraw.Draw(canvas)
    _divider(d, 1078)
    rows = card_stat_rows(card_row)[:6]
    fv, fl = _font("display_bold.ttf", 70 * S), _font("display_bold.ttf", 62 * S)
    for k, (lab, val) in enumerate(rows):
        col, r_i = k // 3, k % 3
        x = 200 if col == 0 else 565
        yy = 1160 + r_i * 80
        d.text(_q(x, yy), str(val), font=fv, fill=GOLD, anchor="ls")
        d.text(_q(x + 98, yy), str(lab).upper()[:3], font=fl, fill=CREAM, anchor="ls")
    d.line((*_q(W / 2, 1102), *_q(W / 2, 1330)), fill=GOLD + (140,), width=2 * S)

    # bottom: two diyas + label
    _diya(canvas, 150, 1410, 0.75)
    _diya(canvas, W - 190, 1410, 0.75)
    d = ImageDraw.Draw(canvas)
    d.text(_q(W / 2, 1412), " ".join(LABEL), font=_font("label.ttf", 26 * S), fill=GOLD, anchor="mm")

    out = canvas.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
