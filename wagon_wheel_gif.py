"""wagon_wheel_gif.py  (v2 - broadcast style)
--------------------------------------------
Animated wagon wheel. wagon_wheel_gif(...) has the SAME signature as
wagon_wheel_png(...) but returns GIF bytes (or None). Never raises.

Look: mowed-stripe outfield, rope boundary, sector shading, 30-yard ring,
pitch with stumps, glass cards for headline / legend, fading glow trail,
glossy ball, impact ripple + sparks, distance pill for sixes.
"""
from __future__ import annotations

import io
import math
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import wagon_wheel as ww

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUT = 520                  # output px
_FPS = 20
_TRAVEL = 18
_HOLD = 28
_S = 2                      # supersampling
_W = _H = 700
_NAMES = ww._NAMES
_ANG = ww._ANGLE

_SIX = (255, 200, 40)
_FOUR = (40, 200, 255)


# ───────────────────────── helpers ─────────────────────────
@lru_cache(maxsize=64)
def _font(kind: str, size: int):
    files = {"head": "label.ttf", "name": "name_font.ttf", "small": "label.ttf"}
    for p in (os.path.join(_HERE, files.get(kind, "label.ttf")), ww._FONT):
        try:
            return ImageFont.truetype(p, size * _S)
        except Exception:
            continue
    return ImageFont.load_default()


def _ease_out(t):
    return 1 - (1 - t) ** 3


def _ease_io(t):
    return 3 * t * t - 2 * t * t * t


def _layer(size):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _glow(layer, radius, boost=1.0):
    """Blurred copy of a layer, to composite underneath for a neon halo."""
    g = layer.filter(ImageFilter.GaussianBlur(radius * _S))
    if boost != 1.0:
        a = g.getchannel("A").point(lambda v: min(255, int(v * boost)))
        g.putalpha(a)
    return g


def _pill(d, cx, cy, text, font, fg, bg, pad=(12, 5), outline=None):
    l, t, r, b = d.textbbox((0, 0), text, font=font, anchor="lt")
    w, h = r - l, b - t
    x0, y0 = cx - w / 2 - pad[0] * _S, cy - h / 2 - pad[1] * _S
    x1, y1 = cx + w / 2 + pad[0] * _S, cy + h / 2 + pad[1] * _S + 2 * _S
    d.rounded_rectangle([x0, y0, x1, y1], radius=(y1 - y0) / 2, fill=bg, outline=outline, width=_S)
    d.text((cx, (y0 + y1) / 2), text, font=font, fill=fg, anchor="mm")


# ───────────────────────── static ground ─────────────────────────
def _ground(hand: str):
    """Pre-rendered ground (RGBA, big). Returns (img, cx, cy, R)."""
    S = _S
    W = H = _W * S
    cx, cy = W // 2, H // 2 + 28 * S
    R = 232 * S

    # background: deep navy with soft radial light
    bg = Image.new("RGB", (W, H), (11, 14, 24))
    rg = Image.radial_gradient("L").resize((W, H))            # 0 centre -> 255 edge
    light = Image.new("RGB", (W, H), (28, 38, 66))
    bg = Image.composite(Image.new("RGB", (W, H), (8, 10, 18)), light, rg)
    img = bg.convert("RGBA")

    # soft drop shadow under the field
    sh = _layer((W, H))
    ImageDraw.Draw(sh).ellipse([cx - R - 6 * S, cy - R + 4 * S, cx + R + 6 * S, cy + R + 16 * S], fill=(0, 0, 0, 150))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14 * S)))

    # outfield: concentric mowing stripes
    field = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(field)
    step = 21 * S
    greens = [(34, 116, 62), (40, 128, 70)]
    i = 0
    r = R
    while r > 0:
        fd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=greens[i % 2])
        r -= step
        i += 1

    # sector shading (8 zones, alternate lighter)
    sec = _layer((W, H))
    sd = ImageDraw.Draw(sec)
    for n, k in enumerate(_ANG):
        if n % 2 == 0:
            ang = ww._ang(k, hand)
            sd.pieslice([cx - R, cy - R, cx + R, cy + R], -ang - 22.5, -ang + 22.5, fill=(255, 255, 255, 12))
    field.alpha_composite(sec)

    # lighting: darker toward the rim
    vg = Image.radial_gradient("L").resize((2 * R, 2 * R))
    dark = Image.new("RGBA", (2 * R, 2 * R), (0, 20, 10, 0))
    dark.putalpha(vg.point(lambda v: int(max(0, v - 90) * 0.55)))
    field.alpha_composite(dark, (cx - R, cy - R))

    # clip to the circle
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([cx - R, cy - R, cx + R, cy + R], fill=255)
    field.putalpha(Image.composite(field.getchannel("A"), Image.new("L", (W, H), 0), mask))
    img.alpha_composite(field)

    d = ImageDraw.Draw(img)

    # inner rings: 30-yard (dashed) + faint guide rings
    for rr in (90, 160):
        d.ellipse([cx - rr * S, cy - rr * S, cx + rr * S, cy + rr * S], outline=(255, 255, 255, 34), width=S)
    r30 = 118 * S
    for a in range(0, 360, 10):
        d.arc([cx - r30, cy - r30, cx + r30, cy + r30], a, a + 5, fill=(255, 255, 255, 120), width=2 * S)

    # spokes
    sp = _layer((W, H))
    spd = ImageDraw.Draw(sp)
    for k in _ANG:
        a = math.radians(ww._ang(k, hand))
        spd.line([cx, cy, cx + R * math.cos(a), cy - R * math.sin(a)], fill=(255, 255, 255, 30), width=S)
    img.alpha_composite(sp)
    d = ImageDraw.Draw(img)

    # boundary rope: dark cushion + dashed red/white rope
    d.ellipse([cx - R - 12 * S, cy - R - 12 * S, cx + R + 12 * S, cy + R + 12 * S], outline=(10, 14, 22, 255), width=12 * S)
    rr = R + 3 * S
    for n, a in enumerate(range(0, 360, 6)):
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr], a, a + 6,
              fill=(246, 246, 248, 255) if n % 2 == 0 else (230, 70, 82, 255), width=6 * S)

    # pitch: strip + creases + stumps
    px0, px1, py0, py1 = cx - 9 * S, cx + 9 * S, cy - 46 * S, cy + 46 * S
    d.rounded_rectangle([px0 - 2 * S, py0 - 2 * S, px1 + 2 * S, py1 + 2 * S], radius=3 * S, fill=(150, 130, 92, 120))
    d.rounded_rectangle([px0, py0, px1, py1], radius=2 * S, fill=(212, 188, 138, 255))
    for yy in (py0 + 8 * S, py1 - 8 * S):
        d.line([px0 - 5 * S, yy, px1 + 5 * S, yy], fill=(255, 255, 255, 200), width=S)
    for yy in (py0 + 2 * S, py1 - 2 * S):
        for dx in (-3, 0, 3):
            d.line([cx + dx * S, yy - 2 * S, cx + dx * S, yy + 2 * S], fill=(60, 45, 25, 255), width=S)

    # direction labels as chips
    f = _font("small", 15)
    for k in _ANG:
        ang = ww._ang(k, hand)
        a = math.radians(ang)
        axis = round(ang) % 90 == 0
        dist = R + (40 if axis else 52) * S
        lx = cx + dist * math.cos(a)
        ly = cy - dist * math.sin(a) * (0.92 if axis else 1.0)
        lx = min(max(lx, 58 * S), W - 58 * S)
        ly = min(max(ly, 118 * S), H - 22 * S)
        _pill(d, lx, ly, _NAMES[k].upper(), f, (196, 206, 226, 255), (22, 28, 44, 215),
              pad=(9, 3), outline=(255, 255, 255, 28))

    return img, cx, cy, R


_GROUND_CACHE: dict = {}


def _get_ground(hand):
    if hand not in _GROUND_CACHE:
        _GROUND_CACHE[hand] = _ground(hand)
    return _GROUND_CACHE[hand]


# ───────────────────────── ball ─────────────────────────
def _ball(d, x, y, r, col):
    S = _S
    d.ellipse([x - r * S, y - r * S, x + r * S, y + r * S], fill=(214, 40, 52, 255), outline=(255, 255, 255, 255), width=2 * S)
    d.arc([x - r * S * 0.62, y - r * S * 0.62, x + r * S * 0.62, y + r * S * 0.62], 200, 340,
          fill=(255, 235, 235, 230), width=max(1, S))
    d.ellipse([x - r * S * 0.55, y - r * S * 0.62, x - r * S * 0.1, y - r * S * 0.2], fill=(255, 255, 255, 120))


# ───────────────────────── frames ─────────────────────────
def _build_frames(direction, outcome, meters, batter, shot_internal, hand):
    key = ww._resolve_key(direction, shot_internal)
    if key is None:
        return None
    S = _S
    base, cx, cy, R = _get_ground(hand)
    size = base.size
    six = outcome == "6"
    col = _SIX if six else _FOUR

    a = math.radians(ww._ang(key, hand))
    end = R + 14 * S if six else R
    ex, ey = cx + end * math.cos(a), cy - end * math.sin(a)
    lift = 0.55 * math.hypot(ex - cx, ey - cy) + 60 * S
    bow = 44 * S * (1 if math.cos(a) >= -1e-6 else -1) * (1.0 if abs(math.cos(a)) < 0.3 else 0.35)
    qx, qy = (cx + ex) / 2 + bow, (cy + ey) / 2 - lift

    def ground(t):
        return cx + (ex - cx) * t, cy + (ey - cy) * t

    def air(t):
        return ((1 - t) ** 2 * cx + 2 * (1 - t) * t * qx + t * t * ex,
                (1 - t) ** 2 * cy + 2 * (1 - t) * t * qy + t * t * ey)

    path = air if six else ground

    # ---- header card + legend (static, pre-built per pop level) ----
    def header(pop):
        lay = _layer(size)
        d = ImageDraw.Draw(lay)
        x0, y0, x1, y1 = 18 * S, 16 * S, 292 * S, 104 * S
        slide = (1 - _ease_out(pop)) * 40 * S
        x0 -= slide; x1 -= slide
        al = int(255 * min(1, pop * 2))
        d.rounded_rectangle([x0, y0, x1, y1], radius=16 * S, fill=(12, 16, 28, int(225 * min(1, pop * 2))),
                            outline=(255, 255, 255, int(40 * min(1, pop * 2))), width=S)
        d.rounded_rectangle([x0 + 10 * S, y0 + 12 * S, x0 + 15 * S, y1 - 12 * S], radius=3 * S, fill=col + (al,))
        d.text((x0 + 28 * S, y0 + 8 * S), "SIX!" if six else "FOUR", font=_font("head", 46), fill=col + (al,), anchor="la")
        nm = batter or ""
        if nm:
            d.text((x0 + 28 * S, y0 + 58 * S), nm[:20], font=_font("name", 16), fill=(236, 240, 250, al), anchor="la")
        shot = _NAMES[key]
        sw = d.textlength(shot.upper(), font=_font("small", 14)) + 22 * S
        sx = x1 - 14 * S - sw
        d.rounded_rectangle([sx, y0 + 14 * S, sx + sw, y0 + 38 * S], radius=12 * S, fill=col + (int(40 * min(1, pop * 2)),),
                            outline=col + (al,), width=S)
        d.text((sx + sw / 2, y0 + 26 * S), shot.upper(), font=_font("small", 14), fill=col + (al,), anchor="mm")
        return lay

    legend = _layer(size)
    ld = ImageDraw.Draw(legend)
    lx0, ly0 = (_W - 118) * S, 18 * S
    ld.rounded_rectangle([lx0, ly0, lx0 + 100 * S, ly0 + 62 * S], radius=14 * S, fill=(12, 16, 28, 215),
                         outline=(255, 255, 255, 36), width=S)
    for i, (lab, c) in enumerate([("SIX", _SIX), ("FOUR", _FOUR)]):
        y = ly0 + (18 + i * 26) * S
        ld.ellipse([lx0 + 16 * S, y - 6 * S, lx0 + 28 * S, y + 6 * S], fill=c + (255,), outline=(0, 0, 0, 255), width=S)
        ld.text((lx0 + 40 * S, y), lab, font=_font("small", 15), fill=(214, 220, 236, 255), anchor="lm")

    # distance pill position
    # sits just inside the rope, nudged sideways so it never covers the trail or a label
    tx = cx + (R - 34 * S) * math.cos(a) + 64 * S * math.sin(a)
    ty = cy - (R - 34 * S) * math.sin(a) + 64 * S * math.cos(a)
    tx = min(max(tx, 90 * S), (_W - 90) * S)
    ty = min(max(ty, 150 * S), (_H - 90) * S)

    frames = []
    for f in range(_TRAVEL + _HOLD):
        tt = min(1.0, f / (_TRAVEL - 1))
        t = _ease_io(tt) if six else tt ** 1.5
        n_after = f - (_TRAVEL - 1)

        img = base.copy()
        fx = _layer(size)           # things that glow
        fd = ImageDraw.Draw(fx)
        top = _layer(size)          # crisp things
        td = ImageDraw.Draw(top)

        # ground shadow for the six
        if six:
            n = 40
            for i in range(0, n, 2):
                t0, t1 = i / n, (i + 1) / n
                if t1 > t:
                    break
                x0, y0 = ground(t0)
                x1, y1 = ground(t1)
                td.line([x0, y0, x1, y1], fill=(6, 40, 20, 190), width=3 * S)

        # fading trail
        steps = max(2, int(54 * t))
        pts = [path(t * i / steps) for i in range(steps + 1)]
        for i in range(steps):
            u = (i + 1) / steps
            al = int(110 + 145 * u ** 1.2)
            w = (2 + 4 * u) * S
            fd.line([pts[i], pts[i + 1]], fill=col + (al,), width=int(w * 2.2))
            td.line([pts[i], pts[i + 1]], fill=col + (al,), width=int(w))
            td.line([pts[i], pts[i + 1]], fill=(255, 255, 255, int(al * 0.45)), width=max(1, int(w * 0.35)))

        # target ring on the rope, pulses until landing
        if n_after < 0:
            pulse = 1 + 0.15 * math.sin(f * 0.9)
            rr = 10 * S * pulse
            td.ellipse([ex - rr, ey - rr, ex + rr, ey + rr], outline=col + (110,), width=2 * S)

        # ball + shadow
        bx, by = path(t)
        r = 8.5
        if six:
            r = 8.5 + 6.5 * math.sin(math.pi * t)
            sx, sy = ground(t)
            td.ellipse([sx - 7 * S, sy - 3 * S, sx + 7 * S, sy + 3 * S], fill=(0, 30, 12, 150))
        if n_after < 0 or six is False:
            fd.ellipse([bx - (r + 5) * S, by - (r + 5) * S, bx + (r + 5) * S, by + (r + 5) * S], fill=col + (200,))
            if n_after < 0:
                _ball(td, bx, by, r, col)

        # impact: ripples, sparks, landing marker
        if n_after >= 0:
            p = min(1.0, n_after / 12)
            for k in range(3 if six else 2):
                pk = max(0.0, p - 0.18 * k)
                if pk <= 0:
                    continue
                rr = (8 + 62 * _ease_out(pk)) * S
                al = int(230 * (1 - pk))
                if al > 0:
                    fd.ellipse([ex - rr, ey - rr, ex + rr, ey + rr], outline=col + (al,), width=3 * S)
                    td.ellipse([ex - rr, ey - rr, ex + rr, ey + rr], outline=col + (al,), width=2 * S)
            if six and n_after < 14:
                for j in range(14):
                    ang = j / 14 * 2 * math.pi + 0.2
                    dist = (6 + 70 * _ease_out(p) * (0.7 + 0.3 * (j % 3) / 2)) * S
                    sr = max(1, int(5 * (1 - p))) * S
                    sx = ex + dist * math.cos(ang)
                    sy = ey + dist * math.sin(ang)
                    c2 = col if j % 2 else (255, 255, 255)
                    td.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=c2 + (int(255 * (1 - p)),))
            fd.ellipse([ex - 12 * S, ey - 12 * S, ex + 12 * S, ey + 12 * S], fill=col + (190,))
            td.ellipse([ex - 8 * S, ey - 8 * S, ex + 8 * S, ey + 8 * S], fill=col + (255,), outline=(255, 255, 255, 255), width=2 * S)

        # batter marker
        pr = 6 + (3 * (1 - t) if n_after < 0 else 0)
        td.ellipse([cx - (pr + 4) * S, cy - (pr + 4) * S, cx + (pr + 4) * S, cy + (pr + 4) * S], outline=(255, 255, 255, 120), width=S)
        td.ellipse([cx - pr * S, cy - pr * S, cx + pr * S, cy + pr * S], fill=(255, 255, 255, 255), outline=(0, 0, 0, 160), width=S)

        img.alpha_composite(_glow(fx, 7, 1.3))
        img.alpha_composite(top)
        img.alpha_composite(legend)

        # distance pill (six)
        if six and meters and n_after >= 3:
            bp = _ease_out(min(1.0, (n_after - 3) / 7))
            lay = _layer(size)
            ldd = ImageDraw.Draw(lay)
            txt = f"{meters} m"
            fnt = _font("head", int(28 * (0.5 + 0.5 * bp)))
            _pill(ldd, tx, ty, txt, fnt, (30, 22, 0, int(255 * bp)), _SIX + (int(255 * bp),),
                  pad=(int(16 * bp), 4), outline=(255, 255, 255, int(255 * bp)))
            img.alpha_composite(_glow(lay, 6, 0.9))
            img.alpha_composite(lay)

        # header card pops in around landing
        pop = 0.0 if n_after < -5 else min(1.0, (n_after + 6) / 7)
        if pop > 0:
            img.alpha_composite(header(pop))

        frames.append(img.convert("RGB").resize((_OUT, _OUT), Image.LANCZOS))
    return frames


def _to_gif(frames) -> bytes:
    pick = frames[:: max(1, len(frames) // 8)][:8] + [frames[-1]]
    sheet = Image.new("RGB", (_OUT, _OUT * len(pick)))
    for i, fr in enumerate(pick):
        sheet.paste(fr, (0, i * _OUT))
    pal = sheet.quantize(colors=250, method=Image.MEDIANCUT)
    keys = [_SIX, _FOUR, (255, 255, 255), (214, 40, 52), (230, 70, 82), (212, 188, 138)]
    flat = list(pal.getpalette()[:750])
    flat += [v for c in keys for v in c]
    flat += [0] * (768 - len(flat))
    pal.putpalette(flat)                   # key colours get exact slots (legend stays true gold / cyan)
    pics = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    dur = [int(1000 / _FPS)] * len(pics)
    dur[-1] = 2800
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:],
                 duration=dur, loop=0, disposal=1, optimize=False)
    return buf.getvalue()


def wagon_wheel_gif(
    direction: str | None,
    outcome: str,
    meters: int | None = None,
    batter: str = "",
    shot_internal: str | None = None,
    hand: str = "R",
) -> bytes | None:
    """Drop-in animated twin of wagon_wheel_png(). Returns GIF bytes or None."""
    if outcome not in ("4", "6"):
        return None
    try:
        frames = _build_frames(direction, outcome, meters, batter, shot_internal, hand)
        return _to_gif(frames) if frames else None
    except Exception as e:
        print(f"[wagon wheel gif] skipped ({type(e).__name__}: {e})")
        return None
