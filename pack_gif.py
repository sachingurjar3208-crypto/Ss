"""PACK OPENING banner GIF — made on the fly when a player opens a pack (PIL only).

    gif_bytes = make_pack_gif("bronze", name="V KOHLI", ovr=74, role="ALL-ROUNDER",
                               stats=(71, 68, 75, 70, 66, 73))
    file      = await pack_file("bronze", name=..., ovr=..., role=..., stats=...)

700x1000, 2.6 s, 15 fps. Fonts: label.ttf and name_font.ttf from the same folder.
Colours match card_style2.py's MEDAL_COLORS so bronze/silver/gold line up with
the medal icon drawn on the classic cards, and the card uses the same shield
outline as card_style2.SHIELD.
"""
from __future__ import annotations

import asyncio
import io
import math
import os
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_DIR = os.path.dirname(os.path.abspath(__file__))
_LABEL, _NAME = os.path.join(_DIR, "label.ttf"), os.path.join(_DIR, "name_font.ttf")
W, H, FPS, DUR = 700, 1000, 15, 2.6
INTRO_DUR = 2.2          # pack-only intro (no card): seconds
INTRO_WAIT = INTRO_DUR + 0.4   # how long csopen waits before showing the real card
CX, CY = 350, 540

# rim, face, ring, inner, gem, gem-light, gem-shadow — same numbers as card_style2.MEDAL_COLORS
PACK_COLORS = {
    "bronze": ((84, 44, 18), (205, 127, 68), (120, 66, 30), (176, 100, 48),
               (226, 160, 106), (242, 196, 150), (140, 78, 36)),
    "silver": ((70, 74, 82), (196, 202, 212), (110, 116, 126), (168, 174, 184),
               (222, 226, 232), (245, 247, 250), (140, 146, 156)),
    "gold":   ((120, 80, 24), (236, 196, 96), (150, 104, 34), (214, 168, 70),
               (252, 228, 150), (255, 246, 205), (196, 146, 54)),
    # fiery red/orange — the game has no card_style2 medal colour for this yet,
    # so this is a new palette matching the 🔥 "Legendary Pack" emoji.
    "legendary": ((70, 10, 8), (232, 64, 28), (120, 20, 12), (196, 48, 20),
                  (255, 176, 64), (255, 220, 140), (168, 36, 14)),
    # Halloween: pumpkin orange with a deep purple rim.
    "halloween": ((44, 14, 62), (255, 128, 30), (104, 34, 128), (204, 84, 18),
                  (255, 190, 80), (255, 226, 150), (160, 60, 12)),
}
LABELS = {"bronze": "BRONZE PACK", "silver": "SILVER PACK", "gold": "GOLD PACK",
          "legendary": "LEGENDARY PACK", "halloween": "HALLOWEEN PACK"}
GOLD_TRIM = (232, 214, 140)
CREAM = (238, 228, 165)

cl = lambda v, a=0.0, b=1.0: max(a, min(b, v))
L = lambda a, b, t: a + (b - a) * t
eo = lambda t: 1 - (1 - t) ** 3
ei = lambda t: t ** 3
eio = lambda t: 4 * t ** 3 if t < .5 else 1 - (-2 * t + 2) ** 3 / 2
pr = lambda t, a, d: cl((t - a) / d)

# Card shield outline, as fractions of the card's own box (same shape as the
# classic card's shield — see card_style2.SHIELD).
SHIELD_FRAC = [
    (0.0612, 0.1525), (0.4285, 0.0553), (0.4644, 0.0615), (0.5004, 0.0185),
    (0.5365, 0.0615), (0.5725, 0.0553), (0.6085, 0.0332), (0.9397, 0.1495),
    (0.9397, 0.8321), (0.9073, 0.8752), (0.5004, 0.9815), (0.0936, 0.8752),
    (0.0612, 0.8321),
]

SHAKE_END = 0.9
BURST_DUR = 0.5
CARD_START, CARD_DUR = 1.1, 0.7
SHINE_START, SHINE_DUR = 1.95, 0.5
PACK_W, PACK_H = 230, 335
CARD_W, CARD_H = 290, 425


@lru_cache(maxsize=32)
def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _shield_pts(w: float, h: float) -> list:
    return [(fx * w, fy * h) for fx, fy in SHIELD_FRAC]


def _hexagon(cx, cy, r):
    return [(cx + r * math.cos(math.radians(30 + 60 * k)),
             cy + r * math.sin(math.radians(30 + 60 * k))) for k in range(6)]


def _draw_medal(d: ImageDraw.ImageDraw, cx, cy, r, colors):
    rim, face, ring, inner, gem_c, light, shadow = colors
    d.polygon(_hexagon(cx, cy, r), fill=rim)
    d.polygon(_hexagon(cx, cy, r * .93), fill=face)
    d.polygon(_hexagon(cx, cy, r * .72), fill=ring)
    d.polygon(_hexagon(cx, cy, r * .62), fill=inner)
    gem = [(cx, cy - r * .40), (cx + r * .34, cy), (cx, cy + r * .40), (cx - r * .34, cy)]
    d.polygon(gem, fill=gem_c)
    d.polygon([gem[0], gem[1], (cx, cy)], fill=light)
    d.polygon([gem[2], gem[3], (cx, cy)], fill=shadow)


@lru_cache(maxsize=4)
def _background(pack: str) -> Image.Image:
    face = PACK_COLORS[pack][1]
    col = Image.new("RGB", (1, H))
    top, bot = (24, 13, 8), (10, 5, 4)
    for y in range(H):
        t = y / H
        col.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    bg = col.resize((W, H), Image.NEAREST)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gr = 230
    gd.ellipse([CX - gr, CY - gr, CX + gr, CY + gr], fill=(*face, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(55))
    return Image.alpha_composite(bg.convert("RGBA"), glow).convert("RGB")


@lru_cache(maxsize=4)
def _ray_canvas(pack: str) -> Image.Image:
    """Static pinwheel of soft rays, pre-cropped to the canvas size (no per-frame rotation)."""
    light = PACK_COLORS[pack][5]
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = max(W, H) * 1.3
    for ang0 in range(10, 360, 46):
        a1, a2 = math.radians(ang0), math.radians(ang0 + 12)
        pts = [(CX, CY), (CX + r * math.cos(a1), CY + r * math.sin(a1)),
               (CX + r * math.cos(a2), CY + r * math.sin(a2))]
        d.polygon(pts, fill=(*light, 40))
    return im.filter(ImageFilter.GaussianBlur(2))


@lru_cache(maxsize=1)
def _sparks() -> tuple:
    rnd = random.Random(42)
    out = []
    for _ in range(22):
        ang = rnd.uniform(0, math.pi * 2)
        dist = rnd.uniform(60, 190)
        out.append((ang, dist, rnd.uniform(0, .12), rnd.uniform(2.5, 5.5)))
    return tuple(out)


def _rounded_rect_grad(w, h, colors, radius):
    rim, face, ring, inner = colors[0], colors[1], colors[2], colors[3]
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / h
        a, b = (face if t < .35 else inner), (inner if t < .35 else rim)
        k = (t / .35) if t < .35 else (t - .35) / .65
        col.putpixel((0, y), tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))
    grad = col.resize((w, h), Image.NEAREST).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    im = Image.composite(grad, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, outline=ring, width=2)
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.polygon([(w * .1, -h * .3), (w * .3, -h * .3), (w * -.1, h * 1.3), (w * -.3, h * 1.3)],
               fill=(255, 255, 255, 40))
    return Image.alpha_composite(im, Image.composite(sh, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask))


@lru_cache(maxsize=4)
def _pack_layer(pack: str, w: int, h: int) -> Image.Image:
    colors = PACK_COLORS[pack]
    body = _rounded_rect_grad(w, h, colors, radius=14)
    d = ImageDraw.Draw(body)
    d.text((w / 2, 16), "SHUTTLE STARS", font=_font(_LABEL, 11), fill=(*CREAM, 190), anchor="mm")
    _draw_medal(d, w / 2, h * .42, 30, colors)
    d.text((w / 2, h - 20), LABELS[pack], font=_font(_LABEL, 15), fill=(*CREAM, 255), anchor="mm")
    return body


@lru_cache(maxsize=8)
def _shield_mask(w: int, h: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(_shield_pts(w, h), fill=255)
    return mask


@lru_cache(maxsize=4)
def _card_base(pack: str, w: int, h: int, name: str, ovr: int, role: str, stats: tuple) -> Image.Image:
    colors = PACK_COLORS[pack]
    rim, face, ring, inner, gem_c, light, shadow = colors
    mask = _shield_mask(w, h)
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / h
        a, b = (light if t < .18 else face), (inner if t < .48 else rim)
        k = min(1.0, t / .48)
        col.putpixel((0, y), tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))
    grad = col.resize((w, h), Image.NEAREST).convert("RGBA")
    card = Image.composite(grad, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask)
    d = ImageDraw.Draw(card)
    d.polygon(_shield_pts(w, h), outline=GOLD_TRIM, width=3)
    f_tag = _font(_LABEL, 12)
    tag = LABELS[pack].split()[0]
    tw = d.textlength(tag, font=f_tag) + 20
    ty0 = 9
    d.rounded_rectangle([w / 2 - tw / 2, ty0, w / 2 + tw / 2, ty0 + 18], radius=9, fill=GOLD_TRIM)
    d.text((w / 2, ty0 + 9), tag, font=f_tag, fill=rim, anchor="mm")
    d.text((w / 2, h * .17), str(ovr), font=_font(_NAME, 40), fill=(255, 255, 255, 255), anchor="mm")
    d.text((w / 2, h * .225), role.upper(), font=_font(_LABEL, 11), fill=(*CREAM, 220), anchor="mm")
    _draw_medal(d, w / 2, h * .32, 19, colors)
    f_name = _font(_NAME, 20)
    nm = name.upper()
    while d.textlength(nm, font=f_name) > w * .82 and f_name.size > 10:
        f_name = _font(_NAME, f_name.size - 2)
    d.text((w / 2, h * .66), nm, font=f_name, fill=(255, 255, 255, 255), anchor="mm")
    labels = ["BAT", "BOWL", "FLD", "PWR", "PAC", "CTRL"]
    gx = [w * .22, w * .5, w * .78]
    gy = [h * .78, h * .84]
    f_stat = _font(_LABEL, 12)
    for i, (lab, val) in enumerate(zip(labels, stats)):
        d.text((gx[i % 3], gy[i // 3]), f"{lab} {val}", font=f_stat, fill=(255, 255, 255, 235), anchor="mm")
    return card


def _card_frame(pack, w, h, name, ovr, role, stats, shine_t):
    card = _card_base(pack, w, h, name, ovr, role, stats)
    if shine_t is None:
        return card
    mask = _shield_mask(w, h)
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sx = -w * .5 + shine_t * (w * 2.0)
    sd.polygon([(sx, -h * .2), (sx + w * .16, -h * .2), (sx - w * .08, h * 1.2), (sx - w * .24, h * 1.2)],
               fill=(255, 255, 255, 110))
    return Image.alpha_composite(card, Image.composite(sh, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask))


def _frame(t: float, pack: str, name: str, ovr: int, role: str, stats: tuple, with_card: bool = True) -> Image.Image:
    colors = PACK_COLORS[pack]
    base = _background(pack).copy()

    ray_a = pr(t, SHAKE_END, .5)
    if ray_a > 0:
        ray = _ray_canvas(pack)
        if ray_a < 1:
            r, g, b, a = ray.split()
            a = a.point(lambda v: int(v * ray_a))
            ray = Image.merge("RGBA", (r, g, b, a))
        base = Image.alpha_composite(base.convert("RGBA"), ray).convert("RGB")

    pre_p = pr(t, 0, SHAKE_END)
    if pre_p < 1:
        amp = 5 + 9 * pre_p
        shake_x = math.sin(t * 46) * amp * (0.3 + 0.7 * pre_p)
        shake_r = math.sin(t * 38) * 3 * pre_p
        layer = _pack_layer(pack, PACK_W, PACK_H)
        if abs(shake_r) > 0.2:
            layer = layer.rotate(shake_r, resample=Image.BILINEAR, expand=True)
        base.paste(layer, (int(CX - layer.width / 2 + shake_x), int(CY - layer.height / 2)), layer)
    else:
        burst_p = pr(t, SHAKE_END, BURST_DUR)
        if burst_p < 1:
            layer = _pack_layer(pack, PACK_W, PACK_H)
            lw, lh = layer.size
            half_w = lw // 2
            fly = eo(burst_p)
            fade = max(0, 1 - ei(burst_p))
            for piece, sign in (((0, 0, half_w, lh), -1), ((half_w, 0, lw, lh), 1)):
                im = layer.crop(piece)
                dx, dy, rot = sign * fly * 150, fly * 80, sign * fly * 50
                im = im.rotate(rot, resample=Image.BILINEAR, expand=True)
                r, g, b, a = im.split()
                a = a.point(lambda v: int(v * fade))
                im = Image.merge("RGBA", (r, g, b, a))
                ox = CX + (sign * half_w / 2) + dx - im.width / 2
                oy = CY + dy - im.height / 2
                base.paste(im, (int(ox), int(oy)), im)
            flash_p = pr(t, SHAKE_END, .3)
            if flash_p < 1:
                k = flash_p / .4 if flash_p < .4 else 1 - (flash_p - .4) / .6
                fr = int(170 * k)
                if fr > 2:
                    flash = Image.new("RGBA", (fr * 2, fr * 2), (0, 0, 0, 0))
                    fc = colors[5]
                    ImageDraw.Draw(flash).ellipse([0, 0, fr * 2, fr * 2], fill=(*fc, int(190 * k)))
                    flash = flash.filter(ImageFilter.GaussianBlur(14))
                    base.paste(flash, (int(CX - fr), int(CY - fr)), flash)

    sp_a = pr(t, SHAKE_END, 1.0)
    if 0 < sp_a < 1:
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(layer)
        for ang, dist, delay, rad in _sparks():
            u = pr(t, SHAKE_END + delay, .8)
            if 0 < u < 1:
                dd = dist * eo(u)
                x = CX + math.cos(ang) * dd
                y = CY + math.sin(ang) * dd - 18 * u
                a = int(255 * (1 - u))
                sd.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(*colors[4], a))
        base = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")

    rise_p = pr(t, CARD_START, CARD_DUR)
    if with_card and rise_p > 0:
        sc = L(.55, 1.06, eo(rise_p / .7)) if rise_p < .7 else L(1.06, 1.0, eio((rise_p - .7) / .3))
        alpha = min(1.0, rise_p * 3)
        y_off = L(60, 0, eo(rise_p))
        shine_t = None
        sp = pr(t, SHINE_START, SHINE_DUR)
        if 0 < sp < 1:
            shine_t = sp
        card = _card_frame(pack, CARD_W, CARD_H, name, ovr, role, stats, shine_t)
        nw, nh = max(1, int(CARD_W * sc)), max(1, int(CARD_H * sc))
        card_r = card.resize((nw, nh), Image.BILINEAR)
        if alpha < 1:
            r, g, b, a = card_r.split()
            a = a.point(lambda v: int(v * alpha))
            card_r = Image.merge("RGBA", (r, g, b, a))
        ox, oy = CX - nw / 2, CY - nh / 2 + y_off
        base.paste(card_r, (int(ox), int(oy)), card_r)

    return base


def make_pack_gif(pack: str = "bronze", name: str = "PLAYER NAME", ovr: int = 74,
                   role: str = "ALL-ROUNDER", stats: tuple = (71, 68, 75, 70, 66, 73),
                   with_card: bool = True) -> bytes:
    """with_card=False: only the pack shaking and bursting open (NO card is drawn, so nothing
    fake is shown). csopen uses this and then shows the player's REAL card image."""
    pack = pack if pack in PACK_COLORS else "bronze"
    stats = tuple(stats)
    n = int((DUR if with_card else INTRO_DUR) * FPS)
    frames = [_frame(i / FPS, pack, name, ovr, role, stats, with_card) for i in range(n)]
    key = [frames[i] for i in (0, n // 3, n // 2, int(n * .7), n - 1)]
    sheet = Image.new("RGB", (W, H * len(key)))
    for i, im in enumerate(key):
        sheet.paste(im, (0, i * H))
    q = getattr(Image, "Quantize", None)
    pal = sheet.quantize(colors=255, method=q.MEDIANCUT if q else Image.MEDIANCUT)
    pics = [f.quantize(palette=pal) for f in frames]
    buf = io.BytesIO()
    extra = {"loop": 0} if with_card else {}          # the intro plays once and stops on its last frame
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:],
                 duration=int(1000 / FPS), disposal=1, **extra)
    return buf.getvalue()


async def pack_file(pack: str = "bronze", name: str = "PLAYER NAME", ovr: int = 74,
                     role: str = "ALL-ROUNDER", stats: tuple = (71, 68, 75, 70, 66, 73)):
    """discord.File for a pack opening. Returns None if anything goes wrong (never blocks a draw)."""
    try:
        import discord
        data = await asyncio.to_thread(make_pack_gif, pack, name, ovr, role, stats)
        return discord.File(io.BytesIO(data), filename=f"pack_{pack}.gif")
    except Exception as e:
        print(f"[pack_gif] skipped ({type(e).__name__}: {e})")
        return None


async def intro_file(pack: str):
    """(discord.File, seconds_to_wait) for the built-in pack-opening intro with NO card in it.
    Used by csopen when no animation was added with `/pack animation add`.
    Returns (None, 0) if anything goes wrong (the real card is then shown straight away)."""
    try:
        import discord
        data = await asyncio.to_thread(make_pack_gif, pack, "", 0, "", (0, 0, 0, 0, 0, 0), False)
        return discord.File(io.BytesIO(data), filename=f"pack_{pack}.gif"), INTRO_WAIT
    except Exception as e:
        print(f"[pack_gif] intro skipped ({type(e).__name__}: {e})")
        return None, 0.0
