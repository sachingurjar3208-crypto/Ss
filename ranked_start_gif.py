"""RANKED MATCH intro GIF, made on the fly for two players (PIL only).

    gif_bytes = make_ranked_start_gif("Sachij XI", "Legendary", 124, "Sora", "Ruby", 210, overs=5)

960x270, ~3.4 s, 20 fps. Each side is painted in its ranked tier colour (Bronze ... Ruby),
a hexagon VS badge slams in the middle, the tier emblem + stars sit on each plate.
"""
from __future__ import annotations

import asyncio
import io
import math
import os
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

_DIR = os.path.dirname(os.path.abspath(__file__))
_LABEL, _NAME = os.path.join(_DIR, "label.ttf"), os.path.join(_DIR, "name_font.ttf")
W, H, S, FPS, DUR = 960, 270, 2, 20, 3.4
GOLD = (255, 211, 107)
TIER_COL = {
    "Bronze": (205, 127, 50), "Silver": (196, 206, 222), "Gold": (255, 196, 54),
    "Platinum": (80, 200, 255), "Legendary": (255, 104, 36), "Ruby": (232, 40, 88),
}

cl = lambda x, a=0.0, b=1.0: max(a, min(b, x))
L = lambda a, b, t: a + (b - a) * t
eo = lambda t: 1 - (1 - t) ** 3
ei = lambda t: t ** 3
eio = lambda t: 4 * t ** 3 if t < .5 else 1 - (-2 * t + 2) ** 3 / 2
pr = lambda t, a, d: cl((t - a) / d)


@lru_cache(maxsize=64)
def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _fit(text: str, path: str, max_w: int, start: int, floor: int):
    text = text.strip().upper()[:24] or "PLAYER"
    for size in range(start, floor - 1, -2):
        f = _font(path, size)
        if f.getlength(text) <= max_w:
            return text, f
    f = _font(path, floor)
    while len(text) > 3 and f.getlength(text + "…") > max_w:
        text = text[:-1]
    return text.rstrip() + "…", f


def _dark(c, k):
    return tuple(int(v * k) for v in c)


@lru_cache(maxsize=36)
def _background(ca, cb) -> Image.Image:
    """Indigo night, left half glows in tier A colour, right half in tier B colour."""
    w, h = W * S, H * S
    bg = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(bg)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(10 + 8 * t), int(8 + 8 * t), int(28 + 22 * t)))
    for col, box in ((ca, [-w * .15, -h * .6, w * .55, h * 1.6]), (cb, [w * .45, -h * .6, w * 1.15, h * 1.6])):
        g = Image.new("L", (w // 4, h // 4), 0)
        ImageDraw.Draw(g).ellipse([v / 4 for v in box], fill=95)
        g = g.filter(ImageFilter.GaussianBlur(26)).resize((w, h), Image.BICUBIC)
        bg.paste(col, (0, 0), g)
    # fine diagonal lines
    lines = Image.new("L", (w, h), 0); ld = ImageDraw.Draw(lines)
    for x in range(-h, w, 18 * S):
        ld.line([(x, 0), (x + h, h)], fill=14, width=S)
    bg.paste((200, 200, 255), (0, 0), lines)
    return bg


def _hex(cx, cy, r, rot=-90):
    return [(cx + r * math.cos(math.radians(rot + 60 * k)), cy + r * math.sin(math.radians(rot + 60 * k))) for k in range(6)]


def _star(d, cx, cy, r, fill, points=5):
    pts = []
    for i in range(points * 2):
        rad = r if i % 2 == 0 else r * .45
        a = math.radians(-90 + i * 180 / points)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    d.polygon(pts, fill=fill)


@lru_cache(maxsize=12)
def _emblem(col) -> Image.Image:
    """Tier emblem: layered hexagon with a star."""
    r = 34 * S
    im = Image.new("RGBA", (2 * r + 8 * S, 2 * r + 8 * S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    c = im.width / 2
    d.polygon(_hex(c, c, r), fill=(*_dark(col, .45), 255))
    d.polygon(_hex(c, c, r - 5 * S), fill=(*col, 255))
    d.polygon(_hex(c, c, r - 14 * S), fill=(*tuple(min(255, int(v * 1.15 + 35)) for v in col), 255))
    _star(d, c, c + 1 * S, r * .42, (*_dark(col, .35), 255) if sum(col) > 600 else (255, 255, 255, 255))
    return im


@lru_cache(maxsize=1)
def _vs_badge() -> Image.Image:
    r = 70 * S
    im = Image.new("RGBA", (2 * r + 10 * S, 2 * r + 10 * S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    c = im.width / 2
    d.polygon(_hex(c, c, r, 0), fill=(*GOLD, 255))
    d.polygon(_hex(c, c, r - 7 * S, 0), fill=(22, 16, 46, 255))
    d.polygon(_hex(c, c, r - 13 * S, 0), outline=(*GOLD, 200), width=2 * S)
    f = _font(_NAME, 60 * S)
    d.text((c + 2 * S, c + 4 * S), "VS", font=f, fill=(0, 0, 0, 160), anchor="mm")
    d.text((c, c), "VS", font=f, fill=(255, 255, 255, 255), anchor="mm")
    return im


@lru_cache(maxsize=1)
def _pill() -> tuple[Image.Image, int]:
    label, gap = "RANKED MATCH", 3 * S
    f = _font(_LABEL, 24 * S); widths = [f.getlength(ch) for ch in label]
    tw = sum(widths) + gap * (len(label) - 1); pw = int(tw + 84 * S)
    im = Image.new("RGBA", (pw, 36 * S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, pw - 1, 36 * S - 1], radius=18 * S, fill=(*GOLD, 255))
    _star(d, 26 * S, 18 * S, 10 * S, (40, 26, 0, 255))
    x = 46 * S
    for ch, w in zip(label, widths):
        d.text((x, 18 * S), ch, font=f, fill=(30, 18, 0, 255), anchor="lm"); x += w + gap
    return im, pw


def _alpha(im: Image.Image, k: float) -> Image.Image:
    out = im.copy(); out.putalpha(im.getchannel("A").point(lambda v: int(v * k))); return out


def _para(x0, x1, y0, y1, sk):
    return [(x0 + sk, y0), (x1 + sk, y0), (x1, y1), (x0, y1)]


SPARK = [(80, 48), (200, 222), (320, 36), (440, 232), (520, 30), (640, 228), (760, 44), (880, 214), (130, 140), (840, 132)]


def _frame(t, A, B, overs):
    (ta, fa, tier_a, stars_a, ca), (tb, fb, tier_b, stars_b, cb) = A, B
    cx, cy = W * S // 2, int(140 * S)
    base = _background(ca, cb).copy()
    d = ImageDraw.Draw(base, "RGBA")
    pe = ei(pr(t, 3.05, .35)); ex = 1 - pe

    # plates slide in
    p = eo(pr(t, .15, .6)); off = (1 - p) * W * S * .55 + pe * W * S * .6
    sk, y0, y1 = 24 * S, cy - 56 * S, cy + 56 * S
    xr, xl = cx - 96 * S, cx + 96 * S
    pa = int(235 * min(1, p * 2) * ex)
    plates = [(_para(-140 * S - off, xr - off, y0, y1, sk), ca), (_para(xl + off, W * S + 140 * S + off, y0, y1, sk), cb)]
    for pl, col in plates:
        d.polygon(pl, fill=(*_dark(col, .22), pa))
        d.line([pl[0], pl[1]], fill=(*col, pa), width=3 * S)
        d.line([pl[3], pl[2]], fill=(*_dark(col, .6), pa), width=2 * S)
    a = int(255 * min(1, p * 2.2) * ex)
    d.polygon(_para(xr - 12 * S - off, xr - off, y0, y1, sk), fill=(*ca, a))
    d.polygon(_para(xl + off, xl + 12 * S + off, y0, y1, sk), fill=(*cb, a))

    # emblems + names + tier lines
    for side, (txt, f, tier, stars, col) in ((-1, (ta, fa, tier_a, stars_a, ca)),
                                             (1, (tb, fb, tier_b, stars_b, cb))):
        em = _alpha(_emblem(col), a / 255)
        ex_ = cx + side * (150 * S) + side * off
        base.paste(em, (int(ex_ - em.width / 2), int(cy - em.height / 2)), em)
        d = ImageDraw.Draw(base, "RGBA")
        nx = ex_ + side * 52 * S
        anchor = "lm" if side > 0 else "rm"
        d.text((nx + 2 * S, cy - 14 * S + 3 * S), txt, font=f, fill=(0, 0, 0, int(a * .6)), anchor=anchor)
        d.text((nx, cy - 14 * S), txt, font=f, fill=(255, 255, 255, a), anchor=anchor)
        tf = _font(_LABEL, 22 * S)
        line = f"{tier.upper()}  ·  {stars}"
        tw = tf.getlength(line)
        lx = nx if side > 0 else nx - tw - 22 * S
        d.text((lx, cy + 26 * S), line, font=tf, fill=(*col, a), anchor="lm")
        if isinstance(stars, int):
            _star(d, lx + tw + 12 * S, cy + 25 * S, 8 * S, (*GOLD, a))

    # VS badge
    q = pr(t, .8, .45); sc = al = 0.0
    if q > 0:
        sc = L(2.6, 1.1, eo(q / .6)) if q < .6 else L(1.1, 1.0, eio((q - .6) / .4))
        sc *= (1 + .025 * math.sin(t * 7) * pr(t, 1.3, .1)) * (1 - pe)
        al = min(1, q * 4) * ex
        for r, aa in ((110, 22), (86, 36), (66, 52)):
            d.ellipse([cx - r * S * sc, cy - r * S * sc * .8, cx + r * S * sc, cy + r * S * sc * .8], fill=(*GOLD, int(aa * al)))
        if sc > .02:
            vb = _vs_badge()
            sp = vb.resize((max(1, int(vb.width * sc)), max(1, int(vb.height * sc))), Image.BILINEAR)
            sp.putalpha(sp.getchannel("A").point(lambda v: int(v * al)))
            base.paste(sp, (int(cx - sp.width / 2), int(cy - sp.height / 2)), sp)
            d = ImageDraw.Draw(base, "RGBA")
    # shock ring
    rp = pr(t, .8, .6)
    if 0 < rp < 1:
        r = L(40, 320, eo(rp)) * S
        d.ellipse([cx - r, cy - r * .55, cx + r, cy + r * .55], outline=(255, 236, 190, int(230 * (1 - rp))), width=5 * S)

    # top pill
    pp = eo(pr(t, 0, .35)); py = L(-46, 14, pp) * S
    pill, pw = _pill()
    sp = _alpha(pill, ex * pp)
    base.paste(sp, (int(cx - pw / 2), int(py)), sp)
    d = ImageDraw.Draw(base, "RGBA")

    # bottom info + progress
    ia = int(255 * pr(t, 1.1, .4) * ex)
    info = f"{overs} OVERS   ·   WIN +1   ·   LOSS -1"
    d.text((cx, (H - 40) * S), info, font=_font(_LABEL, 20 * S), fill=(220, 214, 255, ia), anchor="mm")
    bp = eio(pr(t, 1.0, 1.9)); bx0, bx1, by = cx - 230 * S, cx + 230 * S, (H - 18) * S
    d.rounded_rectangle([bx0, by - 3 * S, bx1, by + 3 * S], radius=3 * S, fill=(255, 255, 255, int(40 * ia / 255)))
    if bp > 0:
        hx = bx0 + (bx1 - bx0) * bp
        d.rounded_rectangle([bx0, by - 3 * S, hx, by + 3 * S], radius=3 * S, fill=(*GOLD, ia))
        d.ellipse([hx - 6 * S, by - 6 * S, hx + 6 * S, by + 6 * S], fill=(255, 255, 255, ia))

    # sparkles
    for i, (sx, sy) in enumerate(SPARK):
        u = pr(t, 1.0 + i * .09, .9)
        if 0 < u < 1:
            k = L(0, 1.3, eo(u / .45)) if u < .45 else L(1.3, 1, (u - .45) / .55)
            al2 = 1 if u < .45 else 1 - (u - .45) / .55
            _star(d, sx * S, sy * S, 9 * S * k, (*GOLD, int(255 * al2)), points=4)

    # flash + shake on impact
    fp = pr(t, .8, .5)
    if 0 < fp < 1:
        k = fp / .3 if fp < .3 else 1 - (fp - .3) / .7
        fm = Image.radial_gradient("L").resize((W * S, int(H * S * 1.6)), Image.BICUBIC).point(lambda v: int((255 - v) * k * .75))
        base.paste((255, 240, 205), (0, cy - fm.height // 2), fm)
    st = pr(t, .8, .3)
    if 0 < st < 1:
        amp = (1 - st) * 6 * S
        base = ImageChops.offset(base, int(math.sin(st * 40) * amp), int(math.cos(st * 33) * amp * .5))
    return base.reduce(S)


def make_ranked_start_gif(name_a, tier_a, stars_a, name_b, tier_b, stars_b, overs=5) -> bytes:
    max_w = (W // 2 - 220 - 20) * S
    ta, fa = _fit(name_a, _NAME, max_w, 38 * S, 20 * S)
    tb, fb = _fit(name_b, _NAME, max_w, 38 * S, 20 * S)
    A = (ta, fa, tier_a, stars_a, TIER_COL.get(tier_a, GOLD))
    B = (tb, fb, tier_b, stars_b, TIER_COL.get(tier_b, GOLD))
    n = int(DUR * FPS)
    frames = [_frame(i / FPS, A, B, overs) for i in range(n)]
    key = [frames[i] for i in (15, 24, 36, 52)]
    sheet = Image.new("RGB", (W, H * len(key)))
    for i, im in enumerate(key):
        sheet.paste(im, (0, i * H))
    pal = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    pics = [f.quantize(palette=pal) for f in frames]
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:], duration=int(1000 / FPS), loop=0, disposal=1)
    return buf.getvalue()


async def ranked_start_file(name_a, tier_a, stars_a, name_b, tier_b, stars_b, overs=5):
    """discord.File with the banner, or None (never blocks a match)."""
    try:
        import discord
        data = await asyncio.to_thread(make_ranked_start_gif, name_a, tier_a, stars_a, name_b, tier_b, stars_b, overs)
        return discord.File(io.BytesIO(data), filename="ranked_start.gif")
    except Exception as e:
        print(f"[ranked_start_gif] skipped ({type(e).__name__}: {e})")
        return None


if __name__ == "__main__":
    data = make_ranked_start_gif("Sachij XI", "Legendary", 124, "Sora Strikers", "Ruby", 210)
    open("ranked_start_preview.gif", "wb").write(data)
    print(len(data) // 1024, "KB")
