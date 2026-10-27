"""MILESTONE banner GIF — made on the fly with the player's name on it (PIL only).

    gif_bytes = make_moment_gif("fifty", "Virat Kohli", "52 (31) · 4x4 · 2x6")
    file      = await moment_file("hattrick", "Jasprit Bumrah", "3 wickets in 3 balls")

Kinds: fifty | century | 150 | 200 | hattrick | maiden | fifer
Same look as the umpire banners (960x270, 20 fps) so the channel feels like one show.
Never raises into a match: moment_file() returns None on any problem.
"""
from __future__ import annotations

import asyncio
import io
import math
from collections import OrderedDict
from functools import lru_cache

from PIL import Image, ImageDraw

# Re-use the umpire banner's drawing helpers so both banners match.
from umpire_gif import (
    CX, CY, DUR, FPS, GOLD, H, S, W,
    _LABEL, _NAME, _background, _flash_mask, _font, _star, _with_alpha, _word_sprite,
    eio, eo, ei, pr, L,
)

# word = the big slam text, label = the pill on top, badge = short text in the left medallion
EVENTS = {
    "fifty":    dict(word="HALF CENTURY",   label="MILESTONE",       badge="50",  c=(60, 200, 255)),
    "century":  dict(word="CENTURY",        label="MILESTONE",       badge="100", c=(255, 196, 60)),
    "150":      dict(word="150 UP",         label="MILESTONE",       badge="150", c=(255, 150, 60)),
    "200":      dict(word="DOUBLE CENTURY", label="MILESTONE",       badge="200", c=(255, 63, 174)),
    "hattrick": dict(word="HAT-TRICK",      label="BOWLING SPECIAL", badge="HT",  c=(255, 64, 88)),
    "maiden":   dict(word="MAIDEN OVER",    label="BOWLING SPECIAL", badge="M",   c=(60, 220, 160)),
    "fifer":    dict(word="5-WICKET HAUL",  label="BOWLING SPECIAL", badge="5W",  c=(170, 120, 255)),
}
KINDS = tuple(EVENTS)

MID = CX + 110          # centre of the text block (same as the umpire banner)
BADGE_X = 170


def _clean(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


@lru_cache(maxsize=32)
def _pill_label(label: str, c: tuple) -> tuple[Image.Image, int]:
    gap = 3 * S
    f = _font(_LABEL, 22 * S)
    widths = [f.getlength(ch) for ch in label]
    pw = int(sum(widths) + gap * (len(label) - 1) + 66 * S)
    im = Image.new("RGBA", (pw, 32 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, pw - 1, 32 * S - 1], radius=16 * S, fill=(*c, 255))
    x = 34 * S
    for ch, w in zip(label, widths):
        d.text((x, 17 * S), ch, font=f, fill=(255, 255, 255, 255), anchor="lm")
        x += w + gap
    return im, pw


@lru_cache(maxsize=64)
def _text_sprite(text: str, fill: tuple, max_w: int, start: int, path: str) -> Image.Image:
    """One line of text, shrunk until it fits `max_w` (in 1x pixels)."""
    fs = start
    while fs > 18 and _font(path, fs * S).getlength(text) > max_w * S:
        fs -= 2
    f = _font(path, fs * S)
    pad = 8 * S
    w, h = int(f.getlength(text)) + 2 * pad, int(fs * S * 1.4) + 2 * pad
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((w / 2, h / 2), text, font=f, fill=fill, anchor="mm")
    return im


def _paste_centered(base: Image.Image, spr: Image.Image, cx: float, cy: float, alpha: float) -> None:
    if alpha <= 0:
        return
    sp = _with_alpha(spr, min(1.0, alpha))
    base.paste(sp, (int(cx * S - sp.width / 2), int(cy * S - sp.height / 2)), sp)


def _badge(base: Image.Image, text: str, c: tuple, t: float, alpha: float) -> None:
    """Round medallion on the left with the short badge text (50 / 100 / HT / M / 5W)."""
    k = eo(pr(t, .15, .45))
    if k <= 0 or alpha <= 0:
        return
    r = 78 * S * (0.55 + 0.45 * k)
    cx, cy = BADGE_X * S, (CY + 4) * S
    lay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.ellipse([cx - r - 10 * S, cy - r - 10 * S, cx + r + 10 * S, cy + r + 10 * S], outline=(*c, 120), width=3 * S)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(16, 22, 60, 245), outline=(*c, 255), width=6 * S)
    # slow-turning dashes on the outer ring
    for i in range(24):
        a = math.radians(i * 15 + t * 40)
        x0, y0 = cx + math.cos(a) * (r + 18 * S), cy + math.sin(a) * (r + 18 * S)
        d.ellipse([x0 - 2 * S, y0 - 2 * S, x0 + 2 * S, y0 + 2 * S], fill=(*GOLD, 200))
    spr = _text_sprite(text, (255, 255, 255, 255), 112, 78 if len(text) < 3 else 62, _NAME)
    lay.paste(spr, (int(cx - spr.width / 2), int(cy - spr.height / 2)), spr)
    lay = _with_alpha(lay, alpha)
    base.paste(lay, (0, 0), lay)


def _frame(t: float, ev: dict, name: str, detail: str) -> Image.Image:
    c = ev["c"]
    base = _background(c).copy()
    d = ImageDraw.Draw(base, "RGBA")
    pe = ei(pr(t, 2.3, .3))          # exit progress
    exa = 1 - pe

    # plate + accent strip (slides in from the right, leaves to the right)
    p = eo(pr(t, .1, .5))
    off = (1 - p) * W * S * .6 + pe * W * S * .6
    sk, y0, y1 = 26 * S, (CY - 62) * S, (CY + 62) * S
    pa = min(1, p * 2) * exa
    plate = [(-120 * S - off + sk, y0), (W * S + 120 * S - off + sk, y0), (W * S + 120 * S - off, y1), (-120 * S - off, y1)]
    d.polygon(plate, fill=(16, 22, 60, int(240 * pa)))
    d.polygon([(300 * S - off + sk, y0), (312 * S - off + sk, y0), (312 * S - off, y1), (300 * S - off, y1)], fill=(*c, int(255 * pa)))
    d.line([plate[0], plate[1]], fill=(255, 255, 255, int(200 * pa)), width=2 * S)

    # medallion
    _badge(base, ev["badge"], c, t, pa)
    d = ImageDraw.Draw(base, "RGBA")

    # pill on top
    pp = eo(pr(t, 0, .3))
    py = L(-44, 20, pp) * S
    pill, pw = _pill_label(ev["label"], c)
    px0 = MID * S - pw / 2
    pla = exa * pp
    base.paste(_with_alpha(pill, pla), (int(px0), int(py)), _with_alpha(pill, pla))
    dot = 5 * S * (.75 + .25 * math.sin(t * 9))
    d.ellipse([px0 + 19 * S - dot, py + 16 * S - dot, px0 + 19 * S + dot, py + 16 * S + dot], fill=(255, 255, 255, int(255 * pla)))

    # ring pulse
    rp = pr(t, .6, .6)
    if 0 < rp < 1:
        r = L(30, 320, eo(rp)) * S
        d.ellipse([MID * S - r, CY * S - r * .5, MID * S + r, CY * S + r * .5], outline=(*c, int(205 * (1 - rp))), width=5 * S)

    # big word slam (sits a little higher than the umpire word to make room for the name)
    word_y = CY - 24
    q = pr(t, .6, .35)
    if q > 0:
        sc = (L(2.3, 1.1, eo(q / .6)) if q < .6 else L(1.1, 1, eio((q - .6) / .4))) * (1 - pe * .4)
        a = min(1, q * 4) * exa
        spr = _word_sprite(ev["word"], c)
        if sc > .02 and a > 0:
            sp = _with_alpha(spr.resize((max(1, int(spr.width * sc)), max(1, int(spr.height * sc))), Image.BILINEAR), a)
            base.paste(sp, (int(MID * S - sp.width / 2), int(word_y * S - sp.height / 2)), sp)
            d = ImageDraw.Draw(base, "RGBA")

    # player name (gold) + detail line
    nb = pr(t, .95, .3)
    if nb > 0:
        spr = _text_sprite(name.upper(), (*GOLD, 255), 560, 46, _NAME)
        _paste_centered(base, spr, MID, CY + 52 + (1 - eo(nb)) * 12, eo(nb) * exa)
    db = pr(t, 1.1, .3)
    if db > 0 and detail:
        spr = _text_sprite(detail.upper(), (255, 235, 200, 255), 560, 26, _LABEL)
        _paste_centered(base, spr, MID, CY + 92 + (1 - eo(db)) * 10, eo(db) * exa)

    # sparkles
    for i, (sx, sy) in enumerate([(430, 50), (880, 60), (640, 236), (900, 200), (360, 230), (790, 24)]):
        u = pr(t, .7 + i * .1, .8)
        if 0 < u < 1:
            k = L(0, 1.3, eo(u / .45)) if u < .45 else L(1.3, 1, (u - .45) / .55)
            _star(d, sx * S, sy * S, 11 * S * k, (*GOLD, int(255 * (1 if u < .45 else 1 - (u - .45) / .55))))

    # flash on impact
    fp = pr(t, .6, .45)
    if 0 < fp < 1:
        k = fp / .3 if fp < .3 else 1 - (fp - .3) / .7
        fm = _flash_mask().point(lambda v: int(v * k * .7))
        base.paste((255, 240, 220), (110 * S, CY * S - fm.height // 2), fm)

    return base.reduce(S)


def make_moment_gif(kind: str, name: str, detail: str = "") -> bytes:
    ev = EVENTS[kind]
    name, detail = _clean(name, 40) or "PLAYER", _clean(detail, 60)
    n = int(DUR * FPS)
    frames = [_frame(i / FPS, ev, name, detail) for i in range(n)]
    key = [frames[i] for i in (14, 20, 28, 40)]       # one shared palette -> no flicker
    sheet = Image.new("RGB", (W, H * len(key)))
    for i, im in enumerate(key):
        sheet.paste(im, (0, i * H))
    q = getattr(Image, "Quantize", None)
    pal = sheet.quantize(colors=255, method=q.MEDIANCUT if q else Image.MEDIANCUT)
    pics = [f.quantize(palette=pal) for f in frames]
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:], duration=int(1000 / FPS), loop=0, disposal=1)
    return buf.getvalue()


# Small LRU so a repeated banner (same player, same text) isn't re-rendered.
_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_CACHE_MAX = 24


async def moment_file(kind: str, name: str, detail: str = ""):
    """discord.File with the banner, or None if anything goes wrong (never blocks a match)."""
    try:
        import discord
        if kind not in EVENTS:
            return None
        key = (kind, name, detail)
        data = _cache.get(key)
        if data is None:
            data = await asyncio.to_thread(make_moment_gif, kind, name, detail)
            _cache[key] = data
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
        else:
            _cache.move_to_end(key)
        return discord.File(io.BytesIO(data), filename=f"moment_{kind}.gif")
    except Exception as e:
        print(f"[moment_gif] skipped ({type(e).__name__}: {e})")
        return None
