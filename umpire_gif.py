"""UMPIRE SIGNAL banner GIF — made on the fly for a decision (PIL only).

    gif_bytes = make_umpire_gif("out")          # out | wide | noball | four | six
    file      = await umpire_file("six")        # discord.File (cached after the first render)
    await warm()                                # pre-render all five in the background

Drawn at 960x270, sent as 800x225 @12 fps (~250 KB), 2.6 s. Fonts: label.ttf and name_font.ttf from the same folder.
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
W, H, S, FPS, DUR = 960, 270, 2, 20, 2.6

# Output encoding (PERF): the banner is drawn at 960x270 / 2x supersampled, but sent as a
# smaller, lower-fps, no-dither GIF so each one is ~200-300 KB instead of ~1.5-2 MB.
# Uploading 1.5 MB on every 4/6/W/wide/no-ball was slowing the match on a small VPS.
OUT_W, OUT_H = 800, 225
OUT_FPS = 12
OUT_COLORS = 160
CX, CY = 480, 139
GOLD = (255, 211, 107)
WHITE, DARK, SKIN = (244, 241, 251), (22, 27, 61), (242, 185, 142)

# r / l = right / left arm angle in degrees from "straight down" (+ = screen right)
EVENTS = {
    "out":    dict(word="OUT",     sub="That's a wicket",           c=(255, 64, 88),   r=178, l=8),
    "wide":   dict(word="WIDE",    sub="+1 run, ball is re-bowled", c=(255, 196, 60),  r=90,  l=-90),
    "noball": dict(word="NO BALL", sub="+1 run, free hit next",     c=(255, 122, 48),  r=90,  l=8),
    "four":   dict(word="FOUR",    sub="Ball crosses the rope",     c=(60, 200, 255),  r=50,  l=8, wave=True),
    "six":    dict(word="SIX",     sub="Maximum, over the rope",    c=(255, 63, 174),  r=172, l=-172),
}

cl = lambda v, a=0.0, b=1.0: max(a, min(b, v))
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


@lru_cache(maxsize=8)
def _background(c: tuple) -> Image.Image:
    bg = Image.new("RGB", (W * S, H * S))
    d = ImageDraw.Draw(bg)
    for y in range(H * S):
        t = y / (H * S)
        d.line([(0, y), (W * S, y)], fill=(int(9 + 10 * t), int(14 + 16 * t), int(38 + 28 * t)))
    g = Image.new("RGBA", (W * S // 4, H * S // 4), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([g.width * .16, -g.height * .5, g.width * .84, g.height * 1.5], fill=(*c, 70))
    g = g.filter(ImageFilter.GaussianBlur(24)).resize((W * S, H * S), Image.BICUBIC)
    bg = Image.alpha_composite(bg.convert("RGBA"), g)
    d = ImageDraw.Draw(bg)
    for x in range(W * S):
        a = int(150 * math.sin(math.pi * x / (W * S)))
        for yy in (0, 1, H * S - 1, H * S - 2):
            d.point((x, yy), fill=(*c, a))
    return bg.convert("RGB")


@lru_cache(maxsize=1)
def _flash_mask() -> Image.Image:
    return Image.radial_gradient("L").resize((W * S, int(H * S * 1.6)), Image.BICUBIC).point(lambda v: 255 - v)


@lru_cache(maxsize=8)
def _word_sprite(word: str, c: tuple) -> Image.Image:
    fs = 120
    while fs > 50 and _font(_NAME, fs * S).getlength(word) > 490 * S:
        fs -= 4
    f = _font(_NAME, fs * S)
    pad = 12 * S
    w, h = int(f.getlength(word)) + 2 * pad, int(fs * S * 1.5) + 2 * pad
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
        d.text((w / 2 + dx * S, h / 2 + dy * S), word, font=f, fill=(*c, 115), anchor="mm")
    d.text((w / 2, h / 2), word, font=f, fill=(255, 255, 255, 255), anchor="mm")
    return im


@lru_cache(maxsize=1)
def _pill(c: tuple) -> tuple[Image.Image, int]:
    label, gap = "UMPIRE'S SIGNAL", 3 * S
    f = _font(_LABEL, 22 * S); widths = [f.getlength(ch) for ch in label]
    pw = int(sum(widths) + gap * (len(label) - 1) + 66 * S)
    im = Image.new("RGBA", (pw, 32 * S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, pw - 1, 32 * S - 1], radius=16 * S, fill=(*c, 255))
    x = 34 * S
    for ch, w in zip(label, widths):
        d.text((x, 17 * S), ch, font=f, fill=(255, 255, 255, 255), anchor="lm"); x += w + gap
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


def _tl(d, p0, p1, w, fill):
    """thick line with round ends"""
    d.line([p0, p1], fill=fill, width=max(1, int(w)))
    r = w / 2
    for p in (p0, p1):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=fill)


def _dot(d, p, r, fill):
    d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=fill)


def _umpire(base: Image.Image, ux: float, uy: float, k: float, ev: dict, t: float, alpha: float):
    m = eio(pr(t, .45, .5))
    ar, al = L(8, ev["r"], m), L(-8, ev["l"], m)
    s = k * S
    T = lambda px, py: ((ux + px * k) * S, (uy + py * k) * S)
    lay = Image.new("RGBA", base.size, (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    # legs, torso, belt
    _tl(d, T(-9, 40), T(-11, 92), 13 * s, (*DARK, 255)); _tl(d, T(9, 40), T(11, 92), 13 * s, (*DARK, 255))
    d.rounded_rectangle([*T(-24, -34), *T(24, 44)], radius=10 * s, fill=(*WHITE, 255))
    d.rectangle([*T(-24, 28), *T(24, 44)], fill=(*DARK, 255))
    # left arm (always a plain arm)
    def plain(sx, a):
        r = math.radians(a)
        e = (sx + math.sin(r) * 64, -26 + math.cos(r) * 64)
        _tl(d, T(sx, -26), T(*e), 12 * s, (*WHITE, 255))
        _dot(d, T(e[0] + math.sin(r) * 6, e[1] + math.cos(r) * 6), 7 * s, (*SKIN, 255))
    plain(-22, al)
    if ev.get("wave"):
        ex, ey = L(26, 42, m), L(-6, -2, m)
        on = 1 if m > .9 else 0
        wx = lambda u: 22 + 36 * math.sin((u - .9) * 12) * on
        hy = ey - 16
        ghost = Image.new("RGBA", base.size, (0, 0, 0, 0)); gd = ImageDraw.Draw(ghost)
        for i in (3, 2, 1):
            _dot(gd, T(wx(t - i * .04), hy), 7 * s, (*SKIN, int(255 * .16 * (4 - i))))
        lay = Image.alpha_composite(lay, ghost); d = ImageDraw.Draw(lay)
        _tl(d, T(22, -26), T(ex, ey), 12 * s, (*WHITE, 255)); _tl(d, T(ex, ey), T(wx(t), hy), 12 * s, (*WHITE, 255))
        _dot(d, T(wx(t), hy), 8 * s, (*SKIN, 255))
    else:
        plain(22, ar)
    # head + hat
    _dot(d, T(0, -52), 17 * s, (*SKIN, 255))
    c0 = T(0, -62)
    d.ellipse([c0[0] - 24 * s, c0[1] - 6 * s, c0[0] + 24 * s, c0[1] + 6 * s], fill=(*DARK, 255))
    d.rounded_rectangle([*T(-15, -76), *T(15, -60)], radius=6 * s, fill=(*DARK, 255))
    lay = _with_alpha(lay, alpha)
    base.paste(lay, (0, 0), lay)


def _frame(t: float, ev: dict) -> Image.Image:
    c = ev["c"]
    base = _background(c).copy()
    d = ImageDraw.Draw(base, "RGBA")
    pe = ei(pr(t, 2.3, .3)); exa = 1 - pe
    # plate + accent strip
    p = eo(pr(t, .1, .5)); off = (1 - p) * W * S * .6 + pe * W * S * .6
    sk, y0, y1 = 26 * S, (CY - 62) * S, (CY + 62) * S
    pa = min(1, p * 2) * exa
    plate = _para(-120 * S - off, W * S + 120 * S - off, y0, y1, sk)
    d.polygon(plate, fill=(16, 22, 60, int(240 * pa)))
    d.polygon(_para(300 * S - off, 312 * S - off, y0, y1, sk), fill=(*c, int(255 * pa)))
    d.line([plate[0], plate[1]], fill=(255, 255, 255, int(200 * pa)), width=2 * S)
    # umpire
    _umpire(base, 170 - off / S * .6, CY + 8, 1.2, ev, t, pa)
    d = ImageDraw.Draw(base, "RGBA")
    # pill
    pp = eo(pr(t, 0, .3)); py = L(-44, 22, pp) * S
    pill, pw = _pill(c); px0 = (CX + 110) * S - pw / 2
    pla = exa * pp
    base.paste(_with_alpha(pill, pla), (int(px0), int(py)), _with_alpha(pill, pla))
    dot = 5 * S * (.75 + .25 * math.sin(t * 9))
    d.ellipse([px0 + 19 * S - dot, py + 16 * S - dot, px0 + 19 * S + dot, py + 16 * S + dot], fill=(255, 255, 255, int(255 * pla)))
    # ring
    rp = pr(t, .6, .6)
    if 0 < rp < 1:
        r = L(30, 320, eo(rp)) * S
        d.ellipse([(CX + 110) * S - r, CY * S - r * .5, (CX + 110) * S + r, CY * S + r * .5],
                  outline=(*c, int(205 * (1 - rp))), width=5 * S)
    # word slam
    q = pr(t, .6, .35)
    if q > 0:
        sc = (L(2.3, 1.1, eo(q / .6)) if q < .6 else L(1.1, 1, eio((q - .6) / .4))) * (1 - pe * .4)
        a = min(1, q * 4) * exa
        spr = _word_sprite(ev["word"], c)
        if sc > .02 and a > 0:
            sp = _with_alpha(spr.resize((max(1, int(spr.width * sc)), max(1, int(spr.height * sc))), Image.BILINEAR), a)
            base.paste(sp, (int((CX + 110) * S - sp.width / 2), int((CY + 4) * S - sp.height / 2)), sp)
            d = ImageDraw.Draw(base, "RGBA")
    # subtitle
    sb = pr(t, .95, .3)
    if sb > 0:
        d.text(((CX + 110) * S, (CY + 82 + (1 - eo(sb)) * 10) * S), ev["sub"].upper(), font=_font(_LABEL, 28 * S),
               fill=(255, 235, 200, int(255 * eo(sb) * exa)), anchor="mm")
    # progress bar
    bp = eio(pr(t, .7, 1.5)); ba = int(255 * pr(t, .5, .3) * exa)
    bx0, by, bw = (CX - 160 + 110) * S, (H - 26) * S, 340 * S
    d.rounded_rectangle([bx0, by - 3 * S, bx0 + bw, by + 3 * S], radius=3 * S, fill=(255, 255, 255, int(40 * ba / 255)))
    if bp > 0:
        hx = bx0 + bw * bp
        d.rounded_rectangle([bx0, by - 3 * S, max(bx0 + 2, hx), by + 3 * S], radius=3 * S, fill=(*c, ba))
        d.ellipse([hx - 6 * S, by - 6 * S, hx + 6 * S, by + 6 * S], fill=(*GOLD, ba))
    # sparkles
    for i, (sx, sy) in enumerate([(430, 50), (880, 60), (640, 236), (900, 200), (360, 230), (790, 24)]):
        u = pr(t, .7 + i * .1, .8)
        if 0 < u < 1:
            k = L(0, 1.3, eo(u / .45)) if u < .45 else L(1.3, 1, (u - .45) / .55)
            _star(d, sx * S, sy * S, 11 * S * k, (*GOLD, int(255 * (1 if u < .45 else 1 - (u - .45) / .55))))
    # flash
    fp = pr(t, .6, .45)
    if 0 < fp < 1:
        k = fp / .3 if fp < .3 else 1 - (fp - .3) / .7
        fm = _flash_mask().point(lambda v: int(v * k * .7))
        base.paste((255, 235, 240), (110 * S, CY * S - fm.height // 2), fm)
    # shake on impact
    st = pr(t, .6, .3)
    if 0 < st < 1:
        amp = (1 - st) * 5 * S
        base = ImageChops.offset(base, int(math.sin(st * 40) * amp), int(math.cos(st * 33) * amp * .5))
    return base.reduce(S)


def encode_banner_gif(frames: list, colors: int = OUT_COLORS) -> bytes:
    """Small GIF from banner frames (rendered at OUT_FPS). Shared with moment_gif.py.
    One shared palette (no flicker), NO dithering (dither noise is what bloated the file),
    resized to OUT_W x OUT_H."""
    size = (OUT_W, OUT_H)
    frames = [f if f.size == size else f.resize(size, Image.LANCZOS) for f in frames]
    n = len(frames)
    key = [frames[min(n - 1, int(i * OUT_FPS / FPS))] for i in (14, 20, 28, 40)]
    sheet = Image.new("RGB", (OUT_W, OUT_H * len(key)))
    for i, im in enumerate(key):
        sheet.paste(im, (0, i * OUT_H))
    q = getattr(Image, "Quantize", None)
    pal = sheet.quantize(colors=colors, method=q.MEDIANCUT if q else Image.MEDIANCUT)
    dn = getattr(getattr(Image, "Dither", None), "NONE", 0)
    pics = [f.quantize(palette=pal, dither=dn) for f in frames]
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:],
                 duration=int(1000 / OUT_FPS), loop=0, disposal=1)
    return buf.getvalue()


def make_umpire_gif(event: str) -> bytes:
    ev = EVENTS[event]
    n = int(DUR * OUT_FPS)
    return encode_banner_gif([_frame(i / OUT_FPS, ev) for i in range(n)])


_cache: dict[str, bytes] = {}


async def umpire_file(event: str):
    """discord.File for the decision. Returns None if anything goes wrong (never blocks a match)."""
    try:
        import discord
        if event not in EVENTS:
            return None
        data = _cache.get(event)
        if data is None:
            data = await asyncio.to_thread(make_umpire_gif, event)
            _cache[event] = data
        return discord.File(io.BytesIO(data), filename=f"umpire_{event}.gif")
    except Exception as e:
        print(f"[umpire_gif] skipped ({type(e).__name__}: {e})")
        return None


async def warm() -> None:
    """Render all five banners one by one in the background so the first OUT/SIX is instant."""
    for ev in EVENTS:
        if ev not in _cache:
            try:
                _cache[ev] = await asyncio.to_thread(make_umpire_gif, ev)
            except Exception as e:
                print(f"[umpire_gif] warm {ev} failed ({type(e).__name__}: {e})")
