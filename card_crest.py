"""CREST card (s2_theme = "crest"): an original crest-shaped Style 2 design.

Layout (output 1037 x 1517, drawn at 2x, transparent outside the crest):
  - crest outline: angled shoulders, raised peak at the top, stepped point at the bottom
  - thick gold rim + thin inner line, faceted country-coloured background
  - left column: OVR, role, gold rule, flag, gold rule, playstyle logos
  - player cutout (same size/spot as his Style 2 card), fading out above the name
  - name, gold rule, 6 stats in two columns ("93 BAT"), "RUSH CRICKET" at the bottom
No club/league logo anywhere.
"""
from __future__ import annotations

import io
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

import card_image as ci
from card_stats import card_stat_rows
from card_arena import _font, _fit, _q, _accent, W, H, S, CW, CH

GOLD = (236, 196, 92)
GOLD_HI = (255, 228, 150)
GOLD_DK = (140, 98, 30)
TEXT = (248, 222, 150)           # gold-ish text, like the stats on the card
ROLE_WORD = {"BAT": "BAT", "BOWL": "BOWL", "AR": "AR", "WK": "WK"}

# crest outline in output px (1037 x 1517)
OUTLINE = [
    (34, 168), (150, 96), (392, 96), (458, 52), (518, 20), (578, 52), (644, 96), (886, 96), (1003, 168),
    (1003, 1250), (772, 1408), (578, 1408), (518, 1494), (458, 1408), (264, 1408), (34, 1250),
]
RIM = 22                          # gold rim thickness (output px)
FADE_Y0, FADE_Y1 = 860, 985       # player fades out between these y values


@lru_cache(maxsize=1)
def _masks():
    outer = Image.new("L", (CW, CH), 0)
    ImageDraw.Draw(outer).polygon([_q(x, y) for x, y in OUTLINE], fill=255)
    # erode by RIM: blur + hard threshold (fast, gives clean rounded inner corners)
    r = RIM * S
    inner = outer.filter(ImageFilter.GaussianBlur(r * 0.6)).point(lambda v: 255 if v >= 250 else 0)
    inner = inner.filter(ImageFilter.MinFilter(9))
    line = inner.filter(ImageFilter.MinFilter(13))
    return outer, inner, line


@lru_cache(maxsize=16)
def _interior(accent) -> Image.Image:
    a1, a2 = accent
    base = tuple(max(0, int(c * 0.62)) for c in a1)
    deep = tuple(max(0, int(c * 0.22)) for c in a1)
    bg = Image.new("RGB", (CW, CH))
    d = ImageDraw.Draw(bg)
    for y in range(CH):
        t = y / CH
        d.line((0, y, CW, y), fill=tuple(int(base[i] + (deep[i] - base[i]) * t) for i in range(3)))
    bg = bg.convert("RGBA")
    # low-poly facets: a jittered triangle grid, each triangle a little lighter/darker
    rnd = random.Random(7)
    fac = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fac)
    step = 150 * S
    pts = {}
    for gx in range(-1, CW // step + 3):
        for gy in range(-1, CH // step + 3):
            pts[gx, gy] = (gx * step + rnd.uniform(-0.35, 0.35) * step, gy * step + rnd.uniform(-0.35, 0.35) * step)
    for gx in range(-1, CW // step + 2):
        for gy in range(-1, CH // step + 2):
            p, q, r_, s_ = pts[gx, gy], pts[gx + 1, gy], pts[gx, gy + 1], pts[gx + 1, gy + 1]
            for tri in ((p, q, s_), (p, s_, r_)):
                if rnd.random() < 0.5:
                    fd.polygon(tri, fill=(255, 255, 255, rnd.randint(4, 22)))
                else:
                    fd.polygon(tri, fill=(0, 0, 0, rnd.randint(10, 40)))
                fd.line(tri + (tri[0],), fill=a2 + (rnd.randint(10, 30),), width=S)
    bg.alpha_composite(fac)
    # light from top-right + big faint gold ring behind the player
    glow = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((CW * 0.35, -CH * 0.1, CW * 1.2, CH * 0.5), fill=a2 + (60,))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(110 * S)))
    ring = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    cx, cy, rr = CW * 0.6, CH * 0.33, CW * 0.36
    rd.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=GOLD + (55,), width=10 * S)
    rd.ellipse((cx - rr * 0.8, cy - rr * 0.8, cx + rr * 0.8, cy + rr * 0.8), outline=GOLD + (30,), width=3 * S)
    bg.alpha_composite(ring)
    # bottom darkening for the text area
    dark = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dark)
    for y in range(900 * S, CH):
        dd.line((0, y, CW, y), fill=(0, 0, 0, min(120, int(120 * (y - 900 * S) / (220 * S)))))
    bg.alpha_composite(dark)
    return bg


@lru_cache(maxsize=1)
def _rim() -> Image.Image:
    """Gold rim texture: vertical metallic gradient with soft diagonal shine."""
    g = Image.new("RGBA", (CW, CH))
    d = ImageDraw.Draw(g)
    stops = [(0.0, GOLD_HI), (0.18, GOLD), (0.42, GOLD_DK), (0.62, GOLD), (0.8, GOLD_HI), (1.0, GOLD_DK)]
    for y in range(CH):
        t = y / CH
        for i in range(len(stops) - 1):
            if stops[i][0] <= t <= stops[i + 1][0]:
                a, b = stops[i][1], stops[i + 1][1]
                u = (t - stops[i][0]) / (stops[i + 1][0] - stops[i][0])
                d.line((0, y, CW, y), fill=tuple(int(a[k] + (b[k] - a[k]) * u) for k in range(3)) + (255,))
                break
    sh = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for x in (0.15, 0.55, 0.9):
        sd.polygon([(CW * x, 0), (CW * x + 90 * S, 0), (CW * x - 300 * S, CH), (CW * x - 390 * S, CH)],
                   fill=(255, 250, 220, 70))
    g.alpha_composite(sh.filter(ImageFilter.GaussianBlur(20 * S)))
    return g


def _player(canvas, card_row, fg, clip):
    try:
        import card_style2 as c2
        fg = fg.convert("RGBA")
        k = W / (c2.CROP[2] - c2.CROP[0])                 # Style 2 design px -> this card's px (same size)
        b = c2.FG_BOX
        box = {"x": int((b["x"] - c2.CROP[0]) * k * S), "y": int((b["y"] - c2.CROP[1]) * k * S),
               "w": int(b["w"] * k * S), "h": int(b["h"] * k * S)}
        resized, pos = ci._fit_foreground(fg, box, int(int(ci._col(card_row, "offset_x", 0)) * k * S),
                                          int(int(ci._col(card_row, "offset_y", 0)) * k * S),
                                          int(ci._col(card_row, "scale_pct", 100)))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(resized, pos, resized)
        ramp = Image.new("L", (1, CH), 255)
        for y in range(FADE_Y0 * S, CH):
            t = min(1.0, (y - FADE_Y0 * S) / ((FADE_Y1 - FADE_Y0) * S))
            ramp.putpixel((0, y), int(255 * (1 - t * t * (3 - 2 * t))))
        a = ImageChops.multiply(layer.getchannel("A"), ramp.resize((CW, CH)))
        layer.putalpha(ImageChops.multiply(a, clip))
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sh.putalpha(layer.getchannel("A").filter(ImageFilter.GaussianBlur(16 * S)).point(lambda v: int(v * 0.45)))
        canvas.alpha_composite(sh, (10 * S, 8 * S))
        canvas.alpha_composite(layer)
    except Exception:
        pass


def _stroked(canvas, xy, text, font, fill, stroke, anchor):
    """Text with a soft dark outline, blended (drawing it straight would make the outline see-through)."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=fill, anchor=anchor, stroke_width=2 * S, stroke_fill=stroke)
    canvas.alpha_composite(layer)


def _gold_rule(d, x0, x1, y, width=2):
    d.line((*_q(x0, y), *_q(x1, y)), fill=GOLD + (200,), width=width * S)


def build(card_row, fg_image, role: str) -> io.BytesIO:
    outer, inner, line = _masks()
    accent = _accent(card_row)
    canvas = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))

    # drop shadow, gold rim, interior
    sh = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    sh.putalpha(outer.filter(ImageFilter.GaussianBlur(10 * S)).point(lambda v: int(v * 0.5)))
    canvas.alpha_composite(sh)
    rim = _rim().copy()
    rim.putalpha(outer)
    canvas.alpha_composite(rim)
    inside = _interior(accent).copy()
    inside.putalpha(inner)
    canvas.alpha_composite(inside)
    # thin inner gold line
    edge = ImageChops.subtract(line.filter(ImageFilter.MaxFilter(5)), line)
    il = Image.new("RGBA", (CW, CH), GOLD + (255,))
    il.putalpha(edge.point(lambda v: int(v * 0.8)))
    canvas.alpha_composite(il)

    _player(canvas, card_row, fg_image, line)
    d = ImageDraw.Draw(canvas)

    # left column: OVR, role, rule, flag, rule, playstyles
    cx = 205
    ovr = str(ci._col(card_row, "ovr", ""))
    _stroked(canvas, _q(cx, 262), ovr, _font("display_bold.ttf", 170 * S), TEXT, (0, 0, 0, 90), "ms")
    d = ImageDraw.Draw(canvas)
    d.text(_q(cx, 352), ROLE_WORD.get(role, role), font=_font("display_bold.ttf", 76 * S), fill=TEXT, anchor="ms")
    _gold_rule(d, cx - 72, cx + 72, 392)
    flag_done = False
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _q(cx, 462), 0, 0, 100, box=128 * S)
            flag_done = True
    except Exception:
        flag_done = False
    d = ImageDraw.Draw(canvas)
    if not flag_done:
        c = str(ci._col(card_row, "country", "") or "").upper()[:12]
        d.text(_q(cx, 462), c, font=_fit(d, c, "label.ttf", 32 * S, 150 * S), fill=TEXT, anchor="mm")
    _gold_rule(d, cx - 72, cx + 72, 532)
    y = 610
    for slot in (1, 2):
        try:
            logo = ci._load_playstyle_badge(card_row, slot)
        except Exception:
            logo = None
        if logo is not None:
            try:
                ci._draw_logo(canvas, logo, _q(cx, y), 0, 0, 100, box=112 * S)
                y += 130
            except Exception:
                pass
    d = ImageDraw.Draw(canvas)

    # name
    name = str(ci._col(card_row, "displayname", "") or ci._col(card_row, "playername", "")).upper()
    f = _fit(d, name, "display_bold.ttf", 100 * S, 800 * S)
    _stroked(canvas, _q(W / 2, 1040), name, f, TEXT, (0, 0, 0, 110), "ms")
    d = ImageDraw.Draw(canvas)
    _gold_rule(d, 180, W - 180, 1072)

    # six stats, two columns of three: "93 BAT"
    rows = card_stat_rows(card_row)[:6]
    fv, fl = _font("display_bold.ttf", 72 * S), _font("display_bold.ttf", 64 * S)
    for k, (lab, val) in enumerate(rows):
        col, row_i = k // 3, k % 3
        x = 200 if col == 0 else 560
        yy = 1150 + row_i * 82
        d.text(_q(x, yy), str(val), font=fv, fill=TEXT, anchor="ls")
        d.text(_q(x + 100, yy), str(lab).upper()[:3], font=fl, fill=TEXT, anchor="ls")
    d.line((*_q(W / 2, 1092), *_q(W / 2, 1330)), fill=GOLD + (150,), width=2 * S)
    _gold_rule(d, W / 2 - 60, W / 2 + 60, 1340)
    d.text(_q(W / 2, 1374), "R U S H   C R I C K E T", font=_font("label.ttf", 22 * S), fill=GOLD, anchor="ms")

    out = canvas.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
