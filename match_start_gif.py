"""MATCH STARTING banner GIF — made on the fly for any two team names (PIL only).

    gif_bytes = make_match_start_gif("Mumbai Indians", "Chennai Super Kings")
    file      = await match_start_file("mike_01's XI", "Cricket Star")   # discord.File

960x270 (32:9), ~3.2 s, 20 fps, about 1-2 MB. Rendering takes a second or two, so
the async helper runs it in a thread and remembers the last results.
Fonts: label.ttf and name_font.ttf from the same folder (falls back to a default font).
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
W, H, S, FPS, DUR = 960, 270, 2, 20, 3.2
PINK, PINK_D, GOLD = (255, 63, 174), (176, 18, 127), (255, 211, 107)

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
    text = text.strip().upper()[:26] or "TEAM"
    for size in range(start, floor - 1, -2):
        f = _font(path, size)
        if f.getlength(text) <= max_w:
            return text, f
    f = _font(path, floor)
    while len(text) > 3 and f.getlength(text + "…") > max_w:
        text = text[:-1]
    return text.rstrip() + "…", f


# ── assets that never change (built once) ─────────────────────────────
@lru_cache(maxsize=1)
def _background() -> Image.Image:
    bg = Image.new("RGB", (W * S, H * S))
    d = ImageDraw.Draw(bg)
    for y in range(H * S):
        t = y / (H * S)
        d.line([(0, y), (W * S, y)], fill=(int(9 + 10 * t), int(14 + 16 * t), int(38 + 28 * t)))
    g = Image.new("RGBA", (W * S // 4, H * S // 4), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([g.width * .16, -g.height * .5, g.width * .84, g.height * 1.5], fill=(255, 63, 174, 80))
    g = g.filter(ImageFilter.GaussianBlur(24)).resize((W * S, H * S), Image.BICUBIC)
    bg = Image.alpha_composite(bg.convert("RGBA"), g)
    d = ImageDraw.Draw(bg)
    for x in range(W * S):                       # pink hairlines, fading to the sides
        a = int(150 * math.sin(math.pi * x / (W * S)))
        d.point((x, 0), fill=(*PINK, a)); d.point((x, 1), fill=(*PINK, a))
        d.point((x, H * S - 1), fill=(*PINK, a)); d.point((x, H * S - 2), fill=(*PINK, a))
    return bg.convert("RGB")


@lru_cache(maxsize=1)
def _badge() -> Image.Image:
    w, h, sk = 190 * S, 130 * S, 26 * S
    poly = [(sk, 0), (w, 0), (w - sk, h), (0, h)]
    grad = Image.new("RGB", (w, h)); gd = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / h
        gd.line([(0, y), (w, y)], fill=tuple(int(L(a, b, t)) for a, b in zip((255, 108, 196), PINK_D)))
    mask = Image.new("L", (w, h), 0); ImageDraw.Draw(mask).polygon(poly, fill=255)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); im.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(im)
    d.line(poly + [poly[0]], fill=(255, 255, 255, 235), width=3 * S, joint="curve")
    f = _font(_NAME, 62 * S)
    d.text((w // 2 + 1 * S, h // 2 + 3 * S), "VS", font=f, fill=(90, 0, 60, 150), anchor="mm")
    d.text((w // 2, h // 2), "VS", font=f, fill=(255, 255, 255, 255), anchor="mm")
    return im


@lru_cache(maxsize=1)
def _flash_mask() -> Image.Image:
    return Image.radial_gradient("L").resize((W * S, int(H * S * 1.6)), Image.BICUBIC).point(lambda v: 255 - v)


@lru_cache(maxsize=64)
def _name_sprite(txt: str, font) -> Image.Image:
    pad = 8 * S
    w, h = int(font.getlength(txt)) + 2 * pad, int(font.size * 1.5) + 2 * pad
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
        d.text((pad + dx * S / 2, h / 2 + dy * S / 2), txt, font=font, fill=(*PINK, 110), anchor="lm")
    d.text((pad, h / 2), txt, font=font, fill=(255, 255, 255, 255), anchor="lm")
    return im


@lru_cache(maxsize=1)
def _pill() -> tuple[Image.Image, int]:
    label, gap = "MATCH STARTING", 3 * S
    f = _font(_LABEL, 24 * S); widths = [f.getlength(c) for c in label]
    tw = sum(widths) + gap * (len(label) - 1); pw = int(tw + 70 * S)
    im = Image.new("RGBA", (pw, 34 * S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, pw - 1, 34 * S - 1], radius=17 * S, fill=(*PINK, 255))
    x = 36 * S
    for c, w in zip(label, widths):
        d.text((x, 17 * S), c, font=f, fill=(255, 255, 255, 255), anchor="lm"); x += w + gap
    return im, pw


def _with_alpha(im: Image.Image, k: float) -> Image.Image:
    out = im.copy(); out.putalpha(im.getchannel("A").point(lambda v: int(v * k))); return out


def _para(x0, x1, y0, y1, sk):
    return [(x0 + sk, y0), (x1 + sk, y0), (x1, y1), (x0, y1)]


def _star(d, cx, cy, r, fill):
    pts = []
    for i in range(8):
        rad = r if i % 2 == 0 else r * .28
        a = math.radians(-90 + i * 45)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    d.polygon(pts, fill=fill)


SPARK = [(70, 52), (190, 214), (300, 40), (430, 226), (530, 36), (660, 222), (770, 46), (890, 206), (120, 130), (850, 128)]


def _frame(t: float, ta, fa, tb, fb) -> Image.Image:
    cx, cy = W * S // 2, int(139 * S)
    base = _background().copy()
    d = ImageDraw.Draw(base, "RGBA")            # draws with transparency straight onto the frame
    pe = ei(pr(t, 2.85, .35))                   # exit
    ex_a = 1 - pe
    # VS badge numbers (the glow behind it is drawn first, the badge itself last)
    q = pr(t, .75, .4)
    sc = al = 0.0
    if q > 0:
        sc = L(2.4, 1.12, eo(q / .6)) if q < .6 else L(1.12, 1.0, eio((q - .6) / .4))
        sc *= (1 + .02 * math.sin(t * 7) * pr(t, 1.2, .1)) * (1 - pe)
        al = min(1, q * 4) * ex_a
        for r, aa in ((92, 20), (72, 34), (54, 50)):
            d.ellipse([cx - r * S * sc, cy - r * S * sc * .8, cx + r * S * sc, cy + r * S * sc * .8], fill=(*PINK, int(aa * al)))
    # plates + names slide in from both sides
    p = eo(pr(t, .15, .6)); off = (1 - p) * W * S * .55 + pe * W * S * .6
    sk, y0, y1 = 26 * S, cy - 50 * S, cy + 50 * S
    xr, xl = cx - 102 * S, cx + 102 * S
    plates = [_para(-120 * S - off, xr - off, y0, y1, sk), _para(xl + off, W * S + 120 * S + off, y0, y1, sk)]
    for pl in plates:
        d.polygon(pl, fill=(16, 22, 60, int(240 * min(1, p * 2) * ex_a)))
    a = int(255 * min(1, p * 2.2) * ex_a)
    d.polygon(_para(xr - 10 * S - off, xr - off, y0, y1, sk), fill=(*PINK, a))
    d.polygon(_para(xl + off, xl + 10 * S + off, y0, y1, sk), fill=(*PINK, a))
    for pl in plates:
        d.line([pl[0], pl[1]], fill=(255, 255, 255, int(200 * min(1, p * 2) * ex_a)), width=2 * S)
    for (x, right, txt, f) in ((cx - 130 * S - off, True, ta, fa), (cx + 130 * S + off, False, tb, fb)):
        sp = _with_alpha(_name_sprite(txt, f), a / 255); pad = 8 * S
        base.paste(sp, (int(x - (sp.width - pad) if right else x - pad), cy - sp.height // 2), sp)
    d = ImageDraw.Draw(base, "RGBA")
    # shimmer across the plates (only the plate strip is processed, so it stays cheap)
    sw = pr(t, 1.35, .75)
    if 0 < sw < 1:
        bx = L(-200 * S, W * S + 100 * S, eio(sw)); hh = y1 - y0 + 1
        band = Image.new("L", (W * S, hh), 0); ImageDraw.Draw(band).polygon(_para(bx, bx + 90 * S, 0, hh - 1, sk), fill=110)
        mask = Image.new("L", (W * S, hh), 0); md = ImageDraw.Draw(mask)
        for pl in plates: md.polygon([(x, y - y0) for x, y in pl], fill=255)
        base.paste((255, 255, 255), (0, y0), ImageChops.multiply(band, mask))
        d = ImageDraw.Draw(base, "RGBA")
    # VS badge on top
    if q > 0 and sc > .02:
        bg_ = _badge()
        sp = bg_.resize((max(1, int(bg_.width * sc)), max(1, int(bg_.height * sc))), Image.BILINEAR)
        sp.putalpha(sp.getchannel("A").point(lambda v: int(v * al)))
        base.paste(sp, (int(cx - sp.width / 2), int(cy - sp.height / 2)), sp)
        d = ImageDraw.Draw(base, "RGBA")
    rp = pr(t, .75, .6)
    if 0 < rp < 1:
        r = L(30, 300, eo(rp)) * S
        d.ellipse([cx - r, cy - r * .55, cx + r, cy + r * .55], outline=(255, 205, 238, int(230 * (1 - rp))), width=5 * S)
    # top pill: MATCH STARTING
    pp = eo(pr(t, 0, .35)); py = L(-44, 26, pp) * S
    pill, pw = _pill(); px0 = cx - pw / 2
    pa = int(255 * ex_a * pp)
    sp = _with_alpha(pill, pa / 255)
    base.paste(sp, (int(px0), int(py)), sp)
    dot = 5 * S * (.75 + .25 * math.sin(t * 9))
    d.ellipse([px0 + 20 * S - dot, py + 17 * S - dot, px0 + 20 * S + dot, py + 17 * S + dot], fill=(255, 255, 255, pa))
    # progress bar
    bp = eio(pr(t, .9, 1.9)); ba = int(255 * pr(t, .6, .3) * ex_a)
    bx0, bx1, by = cx - 260 * S, cx + 260 * S, (H - 30) * S
    d.rounded_rectangle([bx0, by - 3 * S, bx1, by + 3 * S], radius=3 * S, fill=(255, 255, 255, int(40 * ba / 255)))
    if bp > 0:
        hx = bx0 + (bx1 - bx0) * bp
        d.rounded_rectangle([bx0, by - 3 * S, hx, by + 3 * S], radius=3 * S, fill=(*PINK, ba))
        d.ellipse([hx - 6 * S, by - 6 * S, hx + 6 * S, by + 6 * S], fill=(*GOLD, ba))
    # gold sparkles
    for i, (sx, sy) in enumerate(SPARK):
        u = pr(t, .95 + i * .09, .9)
        if 0 < u < 1:
            k = L(0, 1.3, eo(u / .45)) if u < .45 else L(1.3, 1, (u - .45) / .55)
            _star(d, sx * S, sy * S, 11 * S * k, (*GOLD, int(255 * (1 if u < .45 else 1 - (u - .45) / .55))))
    # flash
    fp = pr(t, .75, .5)
    if 0 < fp < 1:
        k = fp / .3 if fp < .3 else 1 - (fp - .3) / .7
        fm = _flash_mask().point(lambda v: int(v * k * .8))
        base.paste((255, 215, 240), (0, cy - fm.height // 2), fm)
    # small shake on impact
    st = pr(t, .75, .3)
    if 0 < st < 1:
        amp = (1 - st) * 5 * S
        base = ImageChops.offset(base, int(math.sin(st * 40) * amp), int(math.cos(st * 33) * amp * .5))
    return base.reduce(S)


def make_match_start_gif(team_a: str, team_b: str) -> bytes:
    max_w = (W // 2 - 130 - 30) * S
    ta, fa = _fit(team_a, _LABEL, max_w, 62 * S, 26 * S)
    tb, fb = _fit(team_b, _LABEL, max_w, 62 * S, 26 * S)
    n = int(DUR * FPS)
    frames = [_frame(i / FPS, ta, fa, tb, fb) for i in range(n)]
    # one shared palette for every frame, so the GIF doesn't flicker
    key = [frames[i] for i in (14, 22, 34, 50)]
    sheet = Image.new("RGB", (W, H * len(key)))
    for i, im in enumerate(key):
        sheet.paste(im, (0, i * H))
    q = getattr(Image, "Quantize", None)
    pal = sheet.quantize(colors=255, method=q.MEDIANCUT if q else Image.MEDIANCUT)
    pics = [f.quantize(palette=pal) for f in frames]
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:], duration=int(1000 / FPS), loop=0, disposal=1)
    return buf.getvalue()


_cache: dict[tuple[str, str], bytes] = {}


async def match_start_file(team_a: str, team_b: str):
    """discord.File with the banner. Returns None if anything goes wrong (never blocks a match)."""
    try:
        import discord
        key = (team_a.strip().lower(), team_b.strip().lower())
        data = _cache.get(key)
        if data is None:
            data = await asyncio.to_thread(make_match_start_gif, team_a, team_b)
            if len(_cache) >= 24:
                _cache.pop(next(iter(_cache)))
            _cache[key] = data
        return discord.File(io.BytesIO(data), filename="match_start.gif")
    except Exception as e:
        print(f"[match_start_gif] skipped ({type(e).__name__}: {e})")
        return None
