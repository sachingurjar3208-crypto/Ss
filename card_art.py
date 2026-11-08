"""ARTWORK card (s2_theme = "art"): your own full design + the bot's stats on top.

You design the whole card picture yourself (player, background, effects, anything) in any
app, and set it as the card's FOREGROUND image (same place as the cutout today). No cutout
needed. The bot:
  - fills the card with your artwork (crop position / zoom = /foregroundfix offset + size)
  - adds its own stats layer on top, same stats as every card:
      top-left  : a hanging tag with OVR, role, flag and the playstyle logos
      bottom    : a frosted-glass panel (your art blurred behind it) with the name and 6 stats
  - rounded rectangle card with a thin light border

Artwork size: best 1037 x 1517 (or any portrait image; it is cropped to fit).
Keep the bottom ~28% and the top-left corner calm: the stats go there.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import card_image as ci
from card_stats import card_stat_rows

W, H = ci.CANVAS_W, ci.CANVAS_H          # 1037 x 1517
S = 2
CW, CH = W * S, H * S
_HERE = Path(__file__).parent

GOLD = (240, 202, 110)
GOLD_HI = (255, 232, 160)
IVORY = (248, 244, 234)
DIM = (205, 210, 225)
ROLE_WORD = {"BAT": "BAT", "BOWL": "BOWL", "AR": "AR", "WK": "WK"}
RADIUS = 40


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


def _artwork(card_row, art) -> Image.Image:
    """Cover-fit the artwork to the card. /foregroundfix offset moves the crop, size zooms."""
    canvas = Image.new("RGBA", (CW, CH), (12, 16, 32, 255))
    if art is None:
        return canvas
    art = art.convert("RGBA")
    zoom = max(0.5, int(ci._col(card_row, "scale_pct", 100) or 100) / 100.0)
    k = max(CW / art.width, CH / art.height) * zoom
    nw, nh = int(art.width * k), int(art.height * k)
    art = art.resize((nw, nh), Image.LANCZOS)
    x = (CW - nw) // 2 + int(ci._col(card_row, "offset_x", 0) or 0) * S
    y = (CH - nh) // 2 + int(ci._col(card_row, "offset_y", 0) or 0) * S
    canvas.alpha_composite(art, (x, y)) if (x >= 0 and y >= 0) else canvas.paste(art, (x, y), art)
    return canvas


def _glass(canvas, box, radius, tint=(8, 12, 28, 150)):
    """Frosted glass: the art behind `box` blurred and darkened, with a soft light edge."""
    x0, y0, x1, y1 = box
    region = canvas.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(22 * S))
    region.alpha_composite(Image.new("RGBA", region.size, tint))
    m = Image.new("L", region.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, region.width - 1, region.height - 1), radius, fill=255)
    canvas.paste(region, (x0, y0), m)
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius, outline=(255, 255, 255, 70), width=2 * S)


# OVR tier colours: 90+ gold, 80-89 silver, below 80 bronze
TIERS = [(90, (240, 202, 110), (255, 232, 160)),
         (80, (196, 204, 220), (236, 242, 252)),
         (0, (200, 132, 78), (240, 186, 140))]
ROLE_LONG = {"BAT": "BATTER", "BOWL": "BOWLER", "AR": "ALL-ROUNDER", "WK": "WICKETKEEPER"}


def _tier(ovr: int):
    for lo, col, hi in TIERS:
        if ovr >= lo:
            return col, hi
    return TIERS[-1][1], TIERS[-1][2]


def _country_code(country: str) -> str:
    """Short code when there is no flag: 'Papua New Guinea' -> PNG, 'India' -> IND."""
    words = [w for w in str(country or "").replace("-", " ").split() if w]
    if not words:
        return ""
    if len(words) > 1:
        return "".join(w[0] for w in words[:3]).upper()
    return words[0][:3].upper()


def build(card_row, fg_image, role: str) -> io.BytesIO:
    """Full artwork + hanging tag + frosted glass panel (the Artwork look)."""
    canvas = _artwork(card_row, fg_image)
    try:
        ovr = int(ci._col(card_row, "ovr", 0) or 0)
    except (TypeError, ValueError):
        ovr = 0
    acc, acc_hi = _tier(ovr)

    # soft vignette (edges) + dark fade at the bottom so the panel reads on any artwork
    vg = Image.new("L", (CW, CH), 0)
    ImageDraw.Draw(vg).rounded_rectangle(_q(-40, -40, W + 40, H + 40), 120 * S, outline=255, width=110 * S)
    dark = Image.new("RGBA", (CW, CH), (0, 0, 0, 255))
    dark.putalpha(vg.filter(ImageFilter.GaussianBlur(70 * S)).point(lambda v: int(v * 0.55)))
    canvas.alpha_composite(dark)
    fade = Image.new("L", (1, CH), 0)
    for y in range(int(CH * 0.58), CH):
        fade.putpixel((0, y), int(185 * max(0.0, (y - CH * 0.58) / (CH * 0.42)) ** 1.3))
    dark = Image.new("RGBA", (CW, CH), (0, 0, 0, 255))
    dark.putalpha(fade.resize((CW, CH)))
    canvas.alpha_composite(dark)

    # ── bottom glass panel: name, role line, 6 stats ───────────────────────────
    px0, py0, px1, py1 = 40, 1060, W - 40, H - 40
    _glass(canvas, _q(px0, py0, px1, py1), 34 * S, tint=(6, 10, 24, 165))
    # diagonal sheen on the glass
    sheen = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(sheen).polygon([_q(px0 + 120, py0), _q(px0 + 330, py0), _q(px0 + 150, py1), _q(px0 - 60, py1)], fill=26)
    pm = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(pm).rounded_rectangle(_q(px0, py0, px1, py1), 34 * S, fill=255)
    sh = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
    sh.putalpha(ImageChops.multiply(sheen.filter(ImageFilter.GaussianBlur(20 * S)), pm))
    canvas.alpha_composite(sh)
    # coloured light along the top edge of the panel
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).line((*_q(px0 + 70, py0), *_q(px1 - 70, py0)), fill=acc + (230,), width=6 * S)
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(8 * S)))
    d = ImageDraw.Draw(canvas)
    d.line((*_q(px0 + 90, py0), *_q(px1 - 90, py0)), fill=acc_hi, width=2 * S)

    name = str(ci._col(card_row, "displayname", "") or ci._col(card_row, "playername", "")).upper()
    f = _fit(d, name, "display_bold.ttf", 90 * S, (px1 - px0 - 80) * S)
    d.text(_q((px0 + px1) / 2, py0 + 70), name, font=f, fill=IVORY, anchor="mm")
    hand = str(ci._col(card_row, "batting_hand", "") or "").strip().upper()[:1]
    sub = ROLE_LONG.get(role, role)
    if hand in ("L", "R"):
        sub += "  ·  " + ("LEFT" if hand == "L" else "RIGHT") + " HAND BAT"
    d.text(_q((px0 + px1) / 2, py0 + 128), " ".join(sub).replace("·", "·"), font=_font("label.ttf", 21 * S),
           fill=acc_hi, anchor="mm")
    d.line((*_q(px0 + 60, py0 + 160), *_q(px1 - 60, py0 + 160)), fill=(255, 255, 255, 60), width=S)
    rows = card_stat_rows(card_row)[:6]
    colw = (px1 - px0 - 60) / 3
    for k, (lab, val) in enumerate(rows):
        cx = px0 + 30 + colw * (k % 3) + colw / 2
        top = py0 + 176 + (k // 3) * 112
        d.text(_q(cx, top + 62), str(val), font=_font("display_bold.ttf", 70 * S), fill=acc_hi, anchor="ms")
        d.text(_q(cx, top + 92), " ".join(str(lab).upper()[:3]), font=_font("label.ttf", 20 * S), fill=DIM, anchor="ms")
    for i in (1, 2):                                    # thin column dividers
        x = px0 + 30 + colw * i
        d.line((*_q(x, py0 + 186), *_q(x, py1 - 34)), fill=(255, 255, 255, 45), width=S)
    d.line((*_q(px0 + 80, py0 + 288), *_q(px1 - 80, py0 + 288)), fill=(255, 255, 255, 35), width=S)

    # double border in the tier colour (drawn before the tag, so the tag hangs over it)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((*_q(10, 10), *_q(W - 10, H - 10)), (RADIUS - 6) * S, outline=acc + (230,), width=3 * S)
    d.rounded_rectangle((*_q(20, 20), *_q(W - 20, H - 20)), (RADIUS - 14) * S, outline=(255, 255, 255, 60), width=S)

    # ── top-left hanging tag: OVR, role, flag (or country code), playstyles ──────
    tx0, tx1, ty1 = 56, 228, 392
    tag = [_q(tx0, 0), _q(tx1, 0), _q(tx1, ty1), _q((tx0 + tx1) / 2, ty1 + 40), _q(tx0, ty1)]
    tag_box = (*_q(tx0, 0), *_q(tx1, ty1 + 40))
    region = canvas.crop(tag_box).filter(ImageFilter.GaussianBlur(16 * S))
    region.alpha_composite(Image.new("RGBA", region.size, (6, 10, 24, 185)))
    m = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(m).polygon(tag, fill=255)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(region, tag_box[:2])
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), m))
    tsh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    tsh.putalpha(m.filter(ImageFilter.GaussianBlur(12 * S)).point(lambda v: int(v * 0.5)))
    canvas.alpha_composite(tsh, (0, 6 * S))
    canvas.alpha_composite(layer)
    d = ImageDraw.Draw(canvas)
    d.line(tag[1:] + [tag[0]], fill=acc, width=4 * S)
    inner = [_q(tx0 + 12, 0), _q(tx1 - 12, 0), _q(tx1 - 12, ty1 - 8), _q((tx0 + tx1) / 2, ty1 + 24), _q(tx0 + 12, ty1 - 8)]
    d.line(inner[1:] + [inner[0]], fill=acc + (90,), width=S)
    cx = (tx0 + tx1) / 2
    d.text(_q(cx, 104), str(ci._col(card_row, "ovr", "")), font=_font("display_bold.ttf", 124 * S), fill=acc_hi, anchor="mm")
    d.text(_q(cx, 186), ROLE_WORD.get(role, role), font=_font("display_bold.ttf", 50 * S), fill=IVORY, anchor="mm")
    d.line((*_q(tx0 + 30, 222), *_q(tx1 - 30, 222)), fill=acc + (200,), width=2 * S)
    flag_done = False
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _q(cx, 280), 0, 0, 100, box=104 * S)
            flag_done = True
    except Exception:
        flag_done = False
    d = ImageDraw.Draw(canvas)
    if not flag_done:
        code = _country_code(ci._col(card_row, "country", ""))
        d.text(_q(cx, 280), code, font=_fit(d, code, "display_bold.ttf", 54 * S, (tx1 - tx0 - 30) * S),
               fill=IVORY, anchor="mm")
    d.line((*_q(tx0 + 30, 336), *_q(tx1 - 30, 336)), fill=acc + (200,), width=2 * S)
    y = 492
    for slot in (1, 2):                                 # playstyle logos under the tag, on a dark disc
        try:
            logo = ci._load_playstyle_badge(card_row, slot)
            if logo is not None:
                disc = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
                ImageDraw.Draw(disc).ellipse(_q(cx - 52, y - 52) + _q(cx + 52, y + 52), fill=(6, 10, 24, 150),
                                             outline=acc + (160,), width=2 * S)
                canvas.alpha_composite(disc)
                ci._draw_logo(canvas, logo, _q(cx, y), 0, 0, 100, box=80 * S)
                y += 118
        except Exception:
            pass

    # ── rounded card ───────────────────────────────────────────────────────────
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CW - 1, CH - 1), RADIUS * S, fill=255)
    canvas.putalpha(ImageChops.multiply(canvas.getchannel("A"), mask))
    out = canvas.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── MATCH PASS look (optional, build_pass) ─────────────────────────────────────────────────
# The bottom of the card is a paper match ticket: torn-edge notches, a dashed tear line,
# the player as the "pass holder" with his 6 stats as ticket fields, and a stub on the right
# with the OVR, role, flag and a barcode. Your artwork stays clean above it.
PAPER = (246, 240, 226)
INK = (24, 22, 30)
GREY = (120, 116, 112)
STUB_ACCENT = {"BAT": (176, 30, 44), "BOWL": (22, 70, 160), "AR": (20, 120, 80), "WK": (150, 90, 20)}


def _paper(size) -> Image.Image:
    import random as _r
    w, h = size
    p = Image.new("RGBA", size, PAPER + (255,))
    noise = Image.effect_noise((max(1, w // 3), max(1, h // 3)), 18).resize(size).convert("L")
    tex = Image.merge("RGBA", [noise, noise, noise, Image.new("L", size, 22)])
    p.alpha_composite(tex)
    return p


def _barcode(d, x0, y0, x1, y1, seed: str):
    import random as _r
    rnd = _r.Random(seed)
    x = x0
    while x < x1:
        w = rnd.choice((1, 1, 2, 3)) * S
        if rnd.random() < 0.62:
            d.rectangle((x, y0, min(x1, x + w - 1), y1), fill=INK)
        x += w + rnd.choice((1, 2)) * S


def build_pass(card_row, fg_image, role: str) -> io.BytesIO:
    """Match-pass ticket look (not used by default)."""
    canvas = _artwork(card_row, fg_image)
    tx0, ty0, tx1, ty1 = 34, 1092, W - 34, H - 34          # ticket box (output px)
    tear = 742                                             # x of the tear line (main | stub)
    notch = 22

    # ticket shape: rounded box with half-circle notches top and bottom at the tear line
    shape = Image.new("L", canvas.size, 0)
    sd = ImageDraw.Draw(shape)
    sd.rounded_rectangle((*_q(tx0, ty0), *_q(tx1, ty1)), 22 * S, fill=255)
    for yy in (ty0, ty1):
        sd.ellipse((*_q(tear - notch, yy - notch), *_q(tear + notch, yy + notch)), fill=0)
    for yy in range(ty0 + 60, ty1 - 40, 46):             # small side perforation
        for xx in (tx0, tx1):
            sd.ellipse((*_q(xx - 7, yy - 7), *_q(xx + 7, yy + 7)), fill=0)
    # drop shadow
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.putalpha(shape.filter(ImageFilter.GaussianBlur(14 * S)).point(lambda v: int(v * 0.55)))
    canvas.alpha_composite(sh, (0, 8 * S))
    paper = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    paper.paste(_paper((CW, CH)), (0, 0))
    paper.putalpha(shape)
    canvas.alpha_composite(paper)
    d = ImageDraw.Draw(canvas)

    accent = STUB_ACCENT.get(role, (176, 30, 44))
    # dashed tear line
    y = ty0 + notch + 6
    while y < ty1 - notch - 6:
        d.line((*_q(tear, y), *_q(tear, min(y + 10, ty1 - notch - 6))), fill=GREY, width=2 * S)
        y += 20

    # main part: header, name, 6 stat fields
    mx0, mx1 = tx0 + 40, tear - 34
    d.text(_q(mx0, ty0 + 40), "P L A Y E R   P A S S", font=_font("label.ttf", 22 * S), fill=accent, anchor="lm")
    d.rectangle((*_q(mx0, ty0 + 58), *_q(mx0 + 64, ty0 + 62)), fill=accent)
    name = str(ci._col(card_row, "displayname", "") or ci._col(card_row, "playername", "")).upper()
    f = _fit(d, name, "display_bold.ttf", 82 * S, (mx1 - mx0) * S)
    d.text(_q(mx0, ty0 + 112), name, font=f, fill=INK, anchor="lm")
    d.line((*_q(mx0, ty0 + 158), *_q(mx1, ty0 + 158)), fill=(200, 192, 180), width=2 * S)
    rows = card_stat_rows(card_row)[:6]
    colw = (mx1 - mx0) / 3
    for k, (lab, val) in enumerate(rows):
        x = mx0 + colw * (k % 3)
        yy = ty0 + 196 + (k // 3) * 92
        d.text(_q(x, yy), str(lab).upper()[:4], font=_font("label.ttf", 22 * S), fill=GREY, anchor="lm")
        d.text(_q(x, yy + 44), str(val), font=_font("display_bold.ttf", 60 * S), fill=INK, anchor="lm")

    # stub: OVR, role, flag, barcode
    sx0, sx1 = tear + 26, tx1 - 30
    cx = (sx0 + sx1) / 2
    d.text(_q(cx, ty0 + 40), "O V R", font=_font("label.ttf", 22 * S), fill=GREY, anchor="mm")
    d.text(_q(cx, ty0 + 112), str(ci._col(card_row, "ovr", "")), font=_font("display_bold.ttf", 118 * S),
           fill=accent, anchor="mm")
    d.rounded_rectangle((*_q(cx - 62, ty0 + 172), *_q(cx + 62, ty0 + 212)), 20 * S, fill=accent)
    d.text(_q(cx, ty0 + 192), role, font=_font("display_bold.ttf", 34 * S), fill=PAPER, anchor="mm")
    flag_done = False
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _q(cx, ty0 + 262), 0, 0, 100, box=92 * S)
            flag_done = True
    except Exception:
        flag_done = False
    d = ImageDraw.Draw(canvas)
    if not flag_done:
        c = str(ci._col(card_row, "country", "") or "").upper()[:10]
        d.text(_q(cx, ty0 + 262), c, font=_fit(d, c, "label.ttf", 26 * S, (sx1 - sx0 - 10) * S), fill=INK, anchor="mm")
    _barcode(d, *_q(sx0 + 12, ty1 - 92, sx1 - 12, ty1 - 40), seed=name)

    # playstyle logos: small, on the artwork, top-right corner (kept away from the art's centre)
    x = W - 90
    for slot in (1, 2):
        try:
            logo = ci._load_playstyle_badge(card_row, slot)
            if logo is not None:
                ci._draw_logo(canvas, logo, _q(x, 90), 0, 0, 100, box=80 * S)
                x -= 96
        except Exception:
            pass

    # rounded card + thin light border
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((*_q(14, 14), *_q(W - 14, H - 14)), (RADIUS - 10) * S, outline=(255, 255, 255, 110), width=2 * S)
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CW - 1, CH - 1), RADIUS * S, fill=255)
    canvas.putalpha(ImageChops.multiply(canvas.getchannel("A"), mask))
    out = canvas.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
