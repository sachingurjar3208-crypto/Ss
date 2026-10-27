"""Rank card image for `csrank` (1200x560 PNG, drawn at 2x then scaled down for smooth edges).

generate_rank_card(...) returns PNG bytes. Uses the bot's own fonts:
  name_font.ttf (Montserrat ExtraBold)  display_bold.ttf (Jersey numbers)  label.ttf (Barlow Condensed)
"""
from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_HERE = Path(__file__).parent
_NAME, _NUM, _LAB = _HERE / "name_font.ttf", _HERE / "display_bold.ttf", _HERE / "label.ttf"

W, H, S = 1200, 560, 2   # final size, supersample factor

TIER_COLORS = {
    "Bronze":    (205, 127, 50),
    "Silver":    (200, 208, 220),
    "Gold":      (255, 200, 60),
    "Platinum":  (90, 200, 255),
    "Legendary": (255, 110, 40),
    "Ruby":      (235, 45, 85),
}


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), int(size * S))
    except Exception:
        try:
            return ImageFont.load_default(size=int(size * S))
        except TypeError:
            return ImageFont.load_default()


def _p(v):
    return int(round(v * S))


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _poly(cx, cy, r, n, rot=-90, inner=None):
    pts = []
    total = n * 2 if inner else n
    for i in range(total):
        rad = r if (not inner or i % 2 == 0) else inner
        a = math.radians(rot + i * 360 / total)
        pts.append((_p(cx + rad * math.cos(a)), _p(cy + rad * math.sin(a))))
    return pts


def _fit(d, text, path, size, maxw):
    while size > 14 and d.textlength(text, font=_f(path, size)) > _p(maxw):
        size -= 2
    return _f(path, size)


def _background(color):
    img = Image.new("RGB", (_p(W), _p(H)), (8, 12, 28))
    px = ImageDraw.Draw(img)
    top, bot = (16, 24, 52), (5, 8, 20)
    for y in range(_p(H)):
        px.line([(0, y), (_p(W), y)], fill=_mix(top, bot, y / _p(H)))
    glow = Image.new("RGB", img.size, (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([_p(620), _p(-200), _p(1300), _p(380)], fill=tuple(int(c * 0.32) for c in color))
    g.ellipse([_p(-150), _p(260), _p(350), _p(700)], fill=tuple(int(c * 0.16) for c in color))
    glow = glow.filter(ImageFilter.GaussianBlur(_p(110)))
    from PIL import ImageChops
    img = ImageChops.add(img, glow)
    # faint diagonal stripes for texture
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for x in range(-_p(H), _p(W), _p(46)):
        od.line([(x, _p(H)), (x + _p(H), 0)], fill=(255, 255, 255, 7), width=_p(14))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")   # RGB base so translucent fills blend


def _avatar(img, d, cx, cy, r, color, avatar_bytes, initial):
    ring = _p(r + 9)
    d.ellipse([_p(cx) - ring, _p(cy) - ring, _p(cx) + ring, _p(cy) + ring], outline=color + (255,), width=_p(5))
    d.ellipse([_p(cx) - _p(r + 2), _p(cy) - _p(r + 2), _p(cx) + _p(r + 2), _p(cy) + _p(r + 2)], fill=(10, 14, 30, 255))
    size = _p(r * 2)
    face = None
    if avatar_bytes:
        try:
            face = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((size, size), Image.LANCZOS)
        except Exception:
            face = None
    if face is None:
        face = Image.new("RGB", (size, size), _mix((30, 40, 78), color, 0.25))
        fd = ImageDraw.Draw(face)
        fd.text((size // 2, size // 2), (initial or "?")[:1].upper(), font=_f(_NAME, r * 1.2),
                fill=(255, 255, 255), anchor="mm")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    img.paste(face, (_p(cx) - size // 2, _p(cy) - size // 2), mask)


def _badge(d, cx, cy, color):
    """Hexagon shield with a star in the middle."""
    dark = _mix(color, (0, 0, 0), 0.55)
    d.polygon(_poly(cx, cy, 78, 6), fill=dark + (255,), outline=color + (255,), width=_p(5))
    d.polygon(_poly(cx, cy, 62, 6), fill=_mix(color, (0, 0, 0), 0.25) + (255,))
    d.polygon(_poly(cx, cy, 34, 5, inner=14), fill=(255, 255, 255, 245))


def generate_rank_card(*, name: str, tier: str, stars: int, wins: int, losses: int, played: int,
                       position: int | None, tier_min: int, next_name: str | None, next_min: int | None,
                       reset_days: int | None, avatar_bytes: bytes | None = None) -> bytes:
    color = TIER_COLORS.get(tier, (255, 200, 60))
    img = _background(color)
    d = ImageDraw.Draw(img, "RGBA")   # "RGBA" mode so translucent fills blend

    # card border
    d.rounded_rectangle([_p(14), _p(14), _p(W - 14), _p(H - 14)], radius=_p(26),
                        outline=color + (150,), width=_p(3))

    # header tag
    d.text((_p(48), _p(44)), "RANK UP MODE", font=_f(_LAB, 26), fill=color + (255,), anchor="lm")
    d.line([(_p(48), _p(66)), (_p(190), _p(66))], fill=color + (255,), width=_p(3))
    d.text((_p(W - 48), _p(44)), (f"SEASON ENDS IN {reset_days}D" if reset_days is not None else "NO SEASON RUNNING"), font=_f(_LAB, 24),
           fill=(170, 182, 214, 255), anchor="rm")

    # left: avatar + name
    _avatar(img, d, 210, 232, 92, color, avatar_bytes, name)
    nf = _fit(d, name.upper(), _NAME, 34, 330)
    d.text((_p(210), _p(386)), name.upper(), font=nf, fill=(255, 255, 255, 255), anchor="mm")
    played_txt = f"{played} MATCHES PLAYED"
    d.text((_p(210), _p(424)), played_txt, font=_f(_LAB, 22), fill=(150, 162, 196, 255), anchor="mm")

    # divider
    d.line([(_p(412), _p(100)), (_p(412), _p(470))], fill=(255, 255, 255, 30), width=_p(2))

    # right top: badge + tier + stars
    _badge(d, 500, 160, color)
    d.text((_p(600), _p(122)), tier.upper(), font=_fit(d, tier.upper(), _NAME, 64, 380),
           fill=color + (255,), anchor="lm")
    d.polygon(_poly(618, 190, 16, 5, inner=7), fill=(255, 214, 102, 255))
    d.text((_p(646), _p(190)), f"{stars}", font=_f(_NUM, 66), fill=(255, 255, 255, 255), anchor="lm")
    sw = d.textlength(f"{stars}", font=_f(_NUM, 66))
    d.text((_p(646) + sw + _p(12), _p(198)), "STARS", font=_f(_LAB, 28), fill=(170, 182, 214, 255), anchor="lm")

    # position pill (right)
    pill = f"GLOBAL #{position}" if position else "UNRANKED"
    pf = _f(_LAB, 28)
    pw = d.textlength(pill, font=pf) + _p(40)
    px0, py0 = _p(W - 48) - pw, _p(104)
    d.rounded_rectangle([px0, py0, _p(W - 48), py0 + _p(46)], radius=_p(23),
                        fill=color + (45,), outline=color + (255,), width=_p(2))
    d.text(((px0 + _p(W - 48)) / 2, py0 + _p(23)), pill, font=pf, fill=(255, 255, 255, 255), anchor="mm")

    # progress bar
    bx0, bx1, by, bh = 440, W - 48, 262, 30
    d.rounded_rectangle([_p(bx0), _p(by), _p(bx1), _p(by + bh)], radius=_p(15), fill=(255, 255, 255, 28))
    if next_min is not None:
        span = max(1, next_min - tier_min)
        frac = max(0.0, min(1.0, (stars - tier_min) / span))
        label_l = f"{max(stars, 0)} / {next_min}"
        label_r = f"NEXT: {next_name.upper()}"
    else:
        frac, label_l, label_r = 1.0, f"{stars} STARS", "MAX TIER"
    fw = max(frac, 0.04) * (bx1 - bx0)
    d.rounded_rectangle([_p(bx0), _p(by), _p(bx0 + fw), _p(by + bh)], radius=_p(15),
                        fill=_mix(color, (255, 255, 255), 0.15) + (255,))
    d.rounded_rectangle([_p(bx0), _p(by), _p(bx0 + fw), _p(by + bh // 2)], radius=_p(15),
                        fill=(255, 255, 255, 45))
    d.text((_p(bx0), _p(by + bh + 24)), label_l, font=_f(_LAB, 26), fill=(255, 255, 255, 255), anchor="lm")
    d.text((_p(bx1), _p(by + bh + 24)), label_r, font=_f(_LAB, 26), fill=color + (255,), anchor="rm")

    # stat boxes
    total = wins + losses
    winpct = f"{round(wins * 100 / total)}%" if total else "0%"
    boxes = [("WINS", str(wins), (90, 220, 130)), ("LOSSES", str(losses), (240, 90, 100)),
             ("WIN RATE", winpct, (255, 214, 102)), ("PLAYED", str(played), (120, 180, 255))]
    gap, bw = 18, (bx1 - bx0 - 18 * 3) / 4
    y0, y1 = 372, 488
    for i, (lab, val, col) in enumerate(boxes):
        x0 = bx0 + i * (bw + gap)
        d.rounded_rectangle([_p(x0), _p(y0), _p(x0 + bw), _p(y1)], radius=_p(16),
                            fill=(255, 255, 255, 20), outline=(255, 255, 255, 40), width=_p(2))
        d.rounded_rectangle([_p(x0), _p(y0 + 18), _p(x0 + 6), _p(y1 - 18)], radius=_p(3), fill=col + (255,))
        if val.endswith("%"):   # Jersey has no "%" glyph: number in Jersey, sign in Montserrat
            nf, pf2 = _f(_NUM, 62), _f(_NAME, 30)
            num = val[:-1]
            tw = d.textlength(num, font=nf) + d.textlength("%", font=pf2) + _p(4)
            sx = _p(x0 + bw / 2 + 4) - tw / 2
            d.text((sx, _p(y0 + 56)), num, font=nf, fill=(255, 255, 255, 255), anchor="lm")
            d.text((sx + d.textlength(num, font=nf) + _p(4), _p(y0 + 64)), "%", font=pf2,
                   fill=(255, 255, 255, 255), anchor="lm")
        else:
            d.text((_p(x0 + bw / 2 + 4), _p(y0 + 56)), val, font=_f(_NUM, 62), fill=(255, 255, 255, 255), anchor="mm")
        d.text((_p(x0 + bw / 2 + 4), _p(y1 - 22)), lab, font=_f(_LAB, 22), fill=(160, 172, 204, 255), anchor="mm")

    # footer
    d.text((_p(W // 2), _p(H - 38)), "PLAY RANKED WITH  csmp ranked", font=_f(_LAB, 22),
           fill=(120, 132, 170, 255), anchor="mm")

    out = img.convert("RGB").resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
