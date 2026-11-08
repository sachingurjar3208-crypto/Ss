"""ARENA card: a full rectangular Style 2 design (s2_theme = "arena").

Layout (output 1037 x 1517, drawn at 2x):
  - full-bleed background (the card's own Style 2 background if set, else country-coloured light beams)
  - chamfered gold border
  - top-left: OVR + role in a chamfered badge, playstyle logos under it
  - top-right: flag tile
  - big player cutout, fading into the bottom panel
  - bottom: name on a slanted gold bar, BAT / BOWL meters, six stat pills
"""
from __future__ import annotations

import io
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import card_image as ci
from card_stats import card_stat_rows

W, H = ci.CANVAS_W, ci.CANVAS_H          # 1037 x 1517
S = 2
CW, CH = W * S, H * S
_HERE = Path(__file__).parent

GOLD = (236, 196, 92)
GOLD_HI = (255, 230, 150)
GOLD_DK = (150, 108, 36)
IVORY = (246, 240, 226)
INK = (20, 16, 12)
PANEL = (10, 14, 30)

# accent colours by country (beams + meters); anything else = teal / gold
COUNTRY_ACCENT = {
    "india": ((30, 90, 200), (255, 150, 40)), "australia": ((20, 110, 60), (250, 200, 40)),
    "south africa": ((10, 110, 70), (240, 190, 40)), "england": ((30, 50, 140), (210, 40, 60)),
    "new zealand": ((30, 30, 36), (200, 200, 210)), "pakistan": ((10, 100, 50), (120, 220, 120)),
    "sri lanka": ((20, 60, 150), (250, 200, 40)), "west indies": ((110, 20, 40), (250, 190, 50)),
    "bangladesh": ((10, 100, 60), (220, 40, 50)), "afghanistan": ((20, 60, 160), (220, 40, 50)),
    "zimbabwe": ((150, 20, 30), (40, 150, 70)), "ireland": ((20, 120, 70), (240, 240, 240)),
}
DEFAULT_ACCENT = ((14, 90, 110), (236, 196, 92))
ARENA_ZOOM = 1.0           # 1.0 = same size as the Style 2 card (keep it: the current size is the right one)
ARENA_CENTER_PULL = 0.0    # move the player toward the middle (0 = keep his Style 2 spot, 1 = exact centre)
PANEL_TOP = 1058           # y of the name bar (the cutout's bottom is hidden under it)
ROLE_WORD = {"BAT": "BATTER", "BOWL": "BOWLER", "AR": "ALL ROUNDER", "WK": "KEEPER"}


def _font(name: str, size: float):
    try:
        return ImageFont.truetype(str(_HERE / name), int(size))
    except Exception:
        return ImageFont.load_default()


def _fit(draw, text, name, size, max_w):
    f = _font(name, size)
    while size > 10 and draw.textlength(text, font=f) > max_w:
        size -= 2
        f = _font(name, size)
    return f


def _q(*v):
    return tuple(int(round(x * S)) for x in v)


def _chamfer(x0, y0, x1, y1, c):
    return [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1), (x0, y1 - c), (x0, y0 + c)]


def _accent(card_row):
    return COUNTRY_ACCENT.get(str(ci._col(card_row, "country", "") or "").strip().lower(), DEFAULT_ACCENT)


@lru_cache(maxsize=16)
def _background(accent) -> Image.Image:
    a1, a2 = accent
    bg = Image.new("RGB", (CW, CH))
    d = ImageDraw.Draw(bg)
    for y in range(CH):
        t = y / CH
        top = tuple(int(a1[i] * 0.55) for i in range(3))
        c = tuple(int(top[i] + (PANEL[i] - top[i]) * min(1, t * 1.25)) for i in range(3))
        d.line((0, y, CW, y), fill=c)
    bg = bg.convert("RGBA")
    rnd = random.Random(11)
    beams = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    bd = ImageDraw.Draw(beams)
    for _ in range(14):                                   # angled light beams, bottom-left -> top-right
        x = rnd.uniform(-0.3, 1.1) * CW
        w = rnd.uniform(0.02, 0.09) * CW
        col = a2 if rnd.random() < 0.55 else a1
        al = rnd.randint(28, 80)
        bd.polygon([(x, CH * 0.95), (x + w, CH * 0.95), (x + w + CW * 0.9, -CH * 0.05), (x + CW * 0.9, -CH * 0.05)],
                   fill=col + (al,))
    bg.alpha_composite(beams.filter(ImageFilter.GaussianBlur(18 * S)))
    sp = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sp)
    for _ in range(260):                                  # paint speckles
        x, y = rnd.uniform(0, CW), rnd.uniform(0, CH * 0.75)
        r = rnd.uniform(1, 5) * S
        sd.ellipse((x - r, y - r, x + r, y + r), fill=a2 + (rnd.randint(40, 140),))
    bg.alpha_composite(sp.filter(ImageFilter.GaussianBlur(1.2)))
    v = Image.new("L", (CW, CH), 0)                       # vignette
    ImageDraw.Draw(v).ellipse((-CW * 0.25, -CH * 0.15, CW * 1.25, CH * 1.05), fill=255)
    v = ImageChops.invert(v.filter(ImageFilter.GaussianBlur(120 * S))).point(lambda p: int(p * 0.6))
    dk = Image.new("RGBA", (CW, CH), (0, 0, 0, 255))
    dk.putalpha(v)
    bg.alpha_composite(dk)
    return bg


def _custom_bg(card_row):
    try:
        import card_style2 as c2
        img = c2._custom_bg(card_row)
        return img.convert("RGBA").resize((CW, CH), Image.LANCZOS) if img is not None else None
    except Exception:
        return None


def _player(canvas, card_row, fg):
    try:
        fg = fg.convert("RGBA")
        # Same box + same /foregroundfix offsets as the normal Style 2 card, mapped to this card's
        # size, so every player sits exactly where he already sits on his Style 2 card (no re-fixing).
        import card_style2 as c2
        k = W / (c2.CROP[2] - c2.CROP[0])                 # Style 2 design px -> this card's px
        b = c2.FG_BOX
        box = {"x": int((b["x"] - c2.CROP[0]) * k * S), "y": int((b["y"] - c2.CROP[1]) * k * S),
               "w": int(b["w"] * k * S), "h": int(b["h"] * k * S)}
        resized, pos = ci._fit_foreground(fg, box, int(int(ci._col(card_row, "offset_x", 0)) * k * S),
                                          int(int(ci._col(card_row, "offset_y", 0)) * k * S),
                                          int(ci._col(card_row, "scale_pct", 100)))
        # Arena has more room than the Style 2 card: make every player bigger (same for all cards,
        # so nothing has to be fixed by hand), keep his centre, and push the cut bottom of the
        # cutout under the name bar so it never shows as a hard edge.
        w0, h0 = resized.size
        cx, bottom = pos[0] + w0 / 2, pos[1] + h0
        right = pos[0] + w0
        nw, nh = int(w0 * ARENA_ZOOM), int(h0 * ARENA_ZOOM)
        big = resized.resize((nw, nh), Image.LANCZOS)
        cx = cx + (CW / 2 - cx) * ARENA_CENTER_PULL
        new_bottom = max(bottom + (nh - h0) * 0.35, PANEL_TOP * S + 40 * S)
        x = cx - nw / 2
        if right >= CW - 60 * S:              # cut at the card edge on Style 2: keep it cut at the edge here too
            x = max(x, right - nw + (nw - w0) * 0.5)
        pos = (int(x), int(new_bottom - nh))
        resized = big
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(resized, pos, resized)
        ramp = Image.new("L", (1, CH), 255)              # fade into the bottom panel
        y0, y1 = 930 * S, 1075 * S
        for y in range(y0, CH):
            t = 1.0 if y >= y1 else (y - y0) / (y1 - y0)
            ramp.putpixel((0, y), int(255 * (1 - t * t * (3 - 2 * t))))
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), ramp.resize((CW, CH))))
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sh.putalpha(layer.getchannel("A").filter(ImageFilter.GaussianBlur(14 * S)).point(lambda v: int(v * 0.5)))
        canvas.alpha_composite(sh, (8 * S, 10 * S))
        canvas.alpha_composite(layer)
    except Exception:
        pass


def build(card_row, fg_image, role: str) -> io.BytesIO:
    accent = _accent(card_row)
    canvas = (_custom_bg(card_row) or _background(accent)).copy()
    _player(canvas, card_row, fg_image)
    d = ImageDraw.Draw(canvas)

    # bottom panel (dark glass) with a gold top edge
    panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    for y in range(1020 * S, CH):
        a = min(235, int(235 * (y - 1020 * S) / (90 * S)))
        pd.line((0, y, CW, y), fill=PANEL + (a,))
    canvas.alpha_composite(panel)
    d = ImageDraw.Draw(canvas)

    # name on a slanted gold bar
    name = str(ci._col(card_row, "displayname", "") or ci._col(card_row, "playername", "")).upper()
    bx0, bx1, by0, by1, sl = 60, 977, 1058, 1150, 34
    bar = [_q(bx0 + sl, by0), _q(bx1, by0), _q(bx1 - sl, by1), _q(bx0, by1)]
    d.polygon(bar, fill=GOLD)
    d.polygon([_q(bx0 + sl, by0), _q(bx1, by0), _q(bx1 - sl * 0.5, (by0 + by1) / 2), _q(bx0 + sl * 0.5, (by0 + by1) / 2)],
              fill=GOLD_HI)
    f = _fit(d, name, "display_bold.ttf", 88 * S, (bx1 - bx0 - 2 * sl - 40) * S)
    d.text(_q((bx0 + bx1) / 2, (by0 + by1) / 2 + 3), name, font=f, fill=INK, anchor="mm")

    # BAT / BOWL meters
    bat, bowl = int(ci._col(card_row, "bat", 0) or 0), int(ci._col(card_row, "bowl", 0) or 0)
    for i, (label, val) in enumerate((("BAT", bat), ("BOWL", bowl))):
        x0 = 80 + i * 450
        d.text(_q(x0, 1238), str(val), font=_font("display_bold.ttf", 92 * S), fill=GOLD_HI, anchor="ls")
        d.text(_q(x0 + 118, 1196), label, font=_font("label.ttf", 30 * S), fill=IVORY, anchor="ls")
        mx0, mx1, my = x0 + 118, x0 + 400, 1214
        d.rounded_rectangle((*_q(mx0, my), *_q(mx1, my + 18)), 9 * S, fill=(40, 46, 70))
        fill = mx0 + (mx1 - mx0) * max(0.03, min(1.0, val / 99))
        d.rounded_rectangle((*_q(mx0, my), *_q(fill, my + 18)), 9 * S, fill=accent[1])

    # six stat pills (3 x 2)
    rows = card_stat_rows(card_row)[:6]
    for k, (lab, val) in enumerate(rows):
        cx = 80 + (k % 3) * 300
        cy = 1290 + (k // 3) * 90
        d.rounded_rectangle((*_q(cx, cy), *_q(cx + 270, cy + 70)), 14 * S, fill=(22, 28, 50), outline=GOLD_DK, width=2 * S)
        d.text(_q(cx + 24, cy + 35), str(val), font=_font("display_bold.ttf", 46 * S), fill=GOLD_HI, anchor="lm")
        d.text(_q(cx + 246, cy + 35), str(lab).upper()[:4], font=_font("label.ttf", 28 * S), fill=IVORY, anchor="rm")

    # OVR badge (top-left)
    ox0, oy0, ox1, oy1 = 52, 50, 252, 300
    badge = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(badge).polygon([_q(x, y) for x, y in _chamfer(ox0, oy0, ox1, oy1, 30)], fill=(10, 14, 30, 200))
    canvas.alpha_composite(badge)
    d = ImageDraw.Draw(canvas)
    d.line([_q(x, y) for x, y in _chamfer(ox0, oy0, ox1, oy1, 30)] + [_q(ox0 + 30, oy0)], fill=GOLD, width=4 * S)
    ovr = str(ci._col(card_row, "ovr", ""))
    d.text(_q((ox0 + ox1) / 2, 150), ovr, font=_font("display_bold.ttf", 140 * S), fill=GOLD_HI, anchor="mm")
    rw = ROLE_WORD.get(role, role)
    d.text(_q((ox0 + ox1) / 2, 252), rw, font=_fit(d, rw, "display_bold.ttf", 44 * S, 170 * S), fill=IVORY, anchor="mm")
    y = 340
    for slot in (1, 2):                                   # playstyle logos under the badge
        try:
            logo = ci._load_playstyle_badge(card_row, slot)
        except Exception:
            logo = None
        if logo is not None:
            try:
                ci._draw_logo(canvas, logo, _q((ox0 + ox1) / 2, y), 0, 0, 100, box=86 * S)
                y += 100
            except Exception:
                pass

    # flag tile (top-right)
    fx0, fy0, fx1, fy1 = 815, 62, 975, 168
    d = ImageDraw.Draw(canvas)
    d.polygon([_q(x, y) for x, y in _chamfer(fx0, fy0, fx1, fy1, 16)], fill=(10, 14, 30))
    done = False
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _q((fx0 + fx1) / 2, (fy0 + fy1) / 2), 0, 0, 100, box=120 * S)
            done = True
    except Exception:
        done = False
    d = ImageDraw.Draw(canvas)
    if not done:
        c = str(ci._col(card_row, "country", "") or "").upper()[:12]
        d.text(_q((fx0 + fx1) / 2, (fy0 + fy1) / 2), c, font=_fit(d, c, "label.ttf", 30 * S, 140 * S), fill=IVORY, anchor="mm")
    d.line([_q(x, y) for x, y in _chamfer(fx0, fy0, fx1, fy1, 16)] + [_q(fx0 + 16, fy0)], fill=GOLD, width=3 * S)

    # chamfered gold border + faint inner line
    outer = _chamfer(14, 14, W - 14, H - 14, 44)
    d.line([_q(x, y) for x, y in outer] + [_q(14 + 44, 14)], fill=GOLD, width=6 * S)
    inner = _chamfer(28, 28, W - 28, H - 28, 38)
    d.line([_q(x, y) for x, y in inner] + [_q(28 + 38, 28)], fill=GOLD_DK + (255,), width=2 * S)

    # clip outside the chamfered border, down-sample
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).polygon([_q(x, y) for x, y in _chamfer(11, 11, W - 11, H - 11, 46)], fill=255)
    canvas.putalpha(ImageChops.multiply(canvas.getchannel("A"), mask))
    out = canvas.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
