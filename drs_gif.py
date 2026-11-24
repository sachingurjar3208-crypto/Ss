"""DRS REVIEW banner GIF (same banner size as the match intro), PIL only.

    data = make_drs_gif("LBW", "out")          # kind: LBW / Caught / Bowled / Stumped
    data = make_drs_gif("LBW", "ucall")        # result: out / notout / ucall (umpire's call, LBW only)
    file = await drs_file("Caught", "notout")  # discord.File, cached

Left panel = the TV replay for that kind of dismissal:
    LBW      ball tracking: ball pitches, hits the pad, projected path to the stumps
    Caught   UltraEdge: the sound wave scrolls past the bat, a spike = edge
    Bowled   front-foot no-ball check
    Stumped  back foot vs the crease when the bails come off
Right panel = the checks one by one, then the final call slams in.
Matches the wording of drs_text.py (the text frames shown at the same time).
"""
from __future__ import annotations

import asyncio
import io
import math

from PIL import Image, ImageDraw, ImageFilter

import umpire_gif as ug

W, H, S = 960, 270, 2
FPS = ug.OUT_FPS
DUR = 4.4
CYAN, GOLD = (70, 210, 255), (255, 211, 107)
GREEN, RED, AMBER = (60, 220, 130), (255, 64, 88), (255, 176, 48)
FAINT = (120, 130, 150)
_font = ug._font
L, eo, eio, pr = ug.L, ug.eo, ug.eio, ug.pr
NAME, LAB = ug._NAME, ug._LABEL


def _checks(kind: str, result: str):
    """(checks, final word, final colour, sub line) for one review."""
    if kind == "LBW":
        notout, uc = result == "notout", result == "ucall"
        checks = [("PITCHING", "IN LINE", GREEN),
                  ("IMPACT", "OUTSIDE OFF" if notout else "IN LINE", GREEN if notout else RED),
                  ("WICKETS", "UMPIRE'S CALL" if uc else ("-" if notout else "HITTING"),
                   AMBER if uc else (FAINT if notout else RED))]
        if notout:
            return checks, "NOT OUT", GREEN, "Impact outside the line"
        return checks, "OUT", RED, ("Umpire's call, review kept" if uc else "Out stands")
    if kind == "Caught":
        edge = result != "notout"
        checks = [("FRONT FOOT", "FAIR", GREEN),
                  ("ULTRAEDGE", "SPIKE AT BAT" if edge else "NO SPIKE", RED if edge else GREEN),
                  ("CARRY", "CLEAN" if edge else "-", RED if edge else FAINT)]
        return checks, ("OUT" if edge else "NOT OUT"), (RED if edge else GREEN), ("Thin edge" if edge else "Off the pad only")
    if kind == "Bowled":
        nb = result == "notout"
        checks = [("FRONT FOOT", "NO BALL" if nb else "BEHIND LINE", GREEN if nb else RED)]
        return checks, ("NOT OUT" if nb else "OUT"), (GREEN if nb else RED), ("No ball, free hit next" if nb else "Fair delivery")
    grounded = result == "notout"   # Stumped
    checks = [("BAILS OFF", "FRAME 3", CYAN), ("BACK FOOT", "GROUNDED" if grounded else "IN THE AIR", GREEN if grounded else RED)]
    return checks, ("NOT OUT" if grounded else "OUT"), (GREEN if grounded else RED), ("Just in time" if grounded else "Out of the crease")


# ── backdrop ─────────────────────────────────────────────────────────────────
_BG = None


def _bg() -> Image.Image:
    global _BG
    if _BG is not None:
        return _BG
    w, h = W * S, H * S
    im = Image.new("RGB", (w, h)); d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(6 + 6 * t), int(13 + 12 * t), int(30 + 22 * t)))
    g = Image.new("L", (w // 4, h // 4), 0)
    ImageDraw.Draw(g).ellipse([-w / 4 * .05, -h / 4 * .4, w / 4 * .62, h / 4 * 1.4], fill=90)
    im.paste(CYAN, (0, 0), g.filter(ImageFilter.GaussianBlur(24)).resize((w, h), Image.BICUBIC).point(lambda v: int(v * .45)))
    sl = Image.new("L", (w, h), 0); sd = ImageDraw.Draw(sl)
    for y in range(0, h, 4 * S):
        sd.line([(0, y), (w, y)], fill=14, width=S)
    im.paste((120, 200, 255), (0, 0), sl)
    _BG = im
    return im


SX0, SY0, SX1, SY1 = 34, 20, 470, 250          # replay screen box (unscaled)


def _screen(d, t):
    a = int(255 * eo(pr(t, 0, .3)))
    d.rounded_rectangle([SX0 * S, SY0 * S, SX1 * S, SY1 * S], radius=14 * S, fill=(4, 10, 22, int(a * .6)),
                        outline=(*CYAN, int(a * .7)), width=2 * S)


def _tag(d, t, label):
    a = int(255 * eo(pr(t, 0, .3)))
    d.rounded_rectangle([(SX0 + 12) * S, (SY0 + 10) * S, (SX0 + 96) * S, (SY0 + 32) * S], radius=6 * S, fill=(200, 30, 40, a))
    if int(t * 2) % 2 == 0:
        d.ellipse([(SX0 + 20) * S, (SY0 + 17) * S, (SX0 + 28) * S, (SY0 + 25) * S], fill=(255, 255, 255, a))
    d.text(((SX0 + 60) * S, (SY0 + 21) * S), "REPLAY", font=_font(LAB, 15 * S), fill=(255, 255, 255, a), anchor="mm")
    d.text(((SX0 + 108) * S, (SY0 + 21) * S), label, font=_font(LAB, 16 * S), fill=(*CYAN, a), anchor="lm")


# ── LBW: ball tracking ───────────────────────────────────────────────────────
def _proj(u, v):
    top_y, bot_y = 46, 236
    y = L(top_y, bot_y, u ** 1.2)
    half = L(22, 96, u)
    return ((252 + v * half) * S, y * S)


def _draw_stumps(d, u, hit=0.0, scale=1.0, alpha=255):
    x, y = _proj(u, 0)
    w = 30 * S * scale; hgt = 64 * S * scale
    col = (255, int(255 - 140 * hit), int(255 - 170 * hit), alpha)
    for k in (-1, 0, 1):
        sx = x + k * w / 3
        d.rounded_rectangle([sx - 3 * S * scale, y - hgt, sx + 3 * S * scale, y], radius=int(2 * S * scale), fill=col)
    d.rectangle([x - w / 2 - 4 * S * scale, y - hgt - 4 * S * scale, x + w / 2 + 4 * S * scale, y - hgt], fill=col)


def _lbw(im, d, t, result):
    d.polygon([_proj(0, -1.15), _proj(0, 1.15), _proj(1.08, 1.15), _proj(1.08, -1.15)], fill=(24, 66, 42, 255))
    d.polygon([_proj(0, -.62), _proj(0, .62), _proj(1.08, .62), _proj(1.08, -.62)], fill=(158, 134, 86, 255))
    d.polygon([_proj(0, -.13), _proj(0, .13), _proj(1.08, .13), _proj(1.08, -.13)], fill=(70, 160, 255, 80))
    for u in (0.07, 0.93):
        d.line([_proj(u, -1.1), _proj(u, 1.1)], fill=(240, 240, 240, 210), width=2 * S)
    _draw_stumps(d, 0.02, scale=.42)
    notout = result == "notout"
    rel, bounce = (0.0, .70), (0.56, .34)
    pad = (0.86, .36 if notout else .10)
    end = (1.0, .26 if result == "ucall" else (.70 if notout else 0.0))
    real, proj = [], []
    for i in range(32):
        k = i / 31
        u = L(rel[0], bounce[0], k); v = L(rel[1], bounce[1], k)
        x, y = _proj(u, v); y -= math.sin(math.pi * k) * 48 * S * (1 - u * .4)
        real.append((x, y))
    for i in range(1, 16):
        k = i / 15
        u = L(bounce[0], pad[0], k); v = L(bounce[1], pad[1], k)
        x, y = _proj(u, v); y -= math.sin(math.pi * k * .5) * 30 * S
        real.append((x, y))
    for i in range(13):
        k = i / 12
        u = L(pad[0], end[0], k); v = L(pad[1], end[1], k)
        x, y = _proj(u, v); y -= (30 - 6 * k) * S
        proj.append((x, y))
    pa = pr(t, .3, 1.25)
    if pa > 0:
        n = max(2, int(len(real) * pa))
        glow = Image.new("L", im.size, 0)
        ImageDraw.Draw(glow).line(real[:n], fill=255, width=20 * S, joint="curve")
        im.paste((255, 70, 70), (0, 0), glow.filter(ImageFilter.GaussianBlur(8 * S)).point(lambda v: int(v * .7)))
        d = ImageDraw.Draw(im, "RGBA")
        d.line(real[:n], fill=(255, 120, 120, 255), width=9 * S, joint="curve")
        bx, by = real[n - 1]
        d.ellipse([bx - 11 * S, by - 11 * S, bx + 11 * S, by + 11 * S], fill=(225, 30, 40, 255), outline=(255, 255, 255, 255), width=3 * S)
        if pa >= 1:
            for mx, my in (real[31], real[-1]):
                d.ellipse([mx - 12 * S, my - 6 * S, mx + 12 * S, my + 6 * S], outline=(255, 255, 255, 230), width=2 * S)
    pp = pr(t, 1.6, .8)
    hit_k = 0.0
    if pp > 0:
        m = max(2, int(len(proj) * pp))
        for i in range(m - 1):
            if i % 2 == 0:
                d.line([proj[i], proj[i + 1]], fill=(*CYAN, 255), width=9 * S)
        ex, ey = proj[m - 1]
        d.ellipse([ex - 10 * S, ey - 10 * S, ex + 10 * S, ey + 10 * S], fill=(*CYAN, 220), outline=(255, 255, 255, 240), width=2 * S)
        if pp >= 1 and not notout:
            q = pr(t, 2.4, .5); hit_k = 1 - abs(1 - 2 * q)
    _draw_stumps(d, 1.0, hit=hit_k)
    return d


# ── Caught: UltraEdge ────────────────────────────────────────────────────────
def _snicko(im, d, t, result):
    edge = result != "notout"
    x0, x1, yc = (SX0 + 24) * S, (SX1 - 24) * S, 150 * S
    for k in range(9):
        gx = L(x0, x1, k / 8)
        d.line([gx, 70 * S, gx, 230 * S], fill=(40, 70, 110, 255), width=S)
    d.line([x0, yc, x1, yc], fill=(60, 100, 150, 255), width=S)
    bx = (x0 + x1) / 2
    d.line([bx, 74 * S, bx, 226 * S], fill=(255, 255, 255, 120), width=2 * S)
    d.text((bx, 82 * S), "BAT", font=_font(LAB, 14 * S), fill=(255, 255, 255, 200), anchor="mm")
    prog = pr(t, .4, 1.8)
    n = int(240 * prog)
    pts = []
    for i in range(n):
        x = L(x0, x1, i / 239)
        amp = math.sin(i * 1.7) * 3 + math.sin(i * 4.3) * 2
        if edge and abs(x - bx) < 14 * S:
            amp += math.sin(i * 2.1) * 52 * (1 - abs(x - bx) / (14 * S))
        pts.append((x, yc + amp * S))
    if edge and prog > .52:
        g = Image.new("L", im.size, 0)
        ImageDraw.Draw(g).ellipse([bx - 40 * S, yc - 60 * S, bx + 40 * S, yc + 60 * S], fill=120)
        im.paste((255, 70, 70), (0, 0), g.filter(ImageFilter.GaussianBlur(18 * S)))
        d = ImageDraw.Draw(im, "RGBA")
    if len(pts) > 1:
        col = (255, 110, 110) if edge and prog > .52 else CYAN
        d.line(pts, fill=(*col, 255), width=3 * S)
    return d


# ── Bowled / Stumped: foot vs crease ─────────────────────────────────────────
def _crease(im, d, t, kind, result):
    front = kind == "Bowled"
    safe = (result != "notout") if front else (result == "notout")   # True = foot behind the line / grounded
    d.rectangle([(SX0 + 10) * S, 48 * S, (SX1 - 10) * S, (SY1 - 10) * S], fill=(28, 74, 46, 255))
    d.rectangle([(SX0 + 10) * S, 100 * S, (SX1 - 10) * S, (SY1 - 10) * S], fill=(156, 132, 84, 255))
    lx = 250 * S
    d.rectangle([lx - 3 * S, 48 * S, lx + 3 * S, (SY1 - 10) * S], fill=(250, 250, 250, 255))
    p = eo(pr(t, .4, 1.2))
    fy = 168 * S
    if front:   # bowler's front foot lands moving right; the heel must stay on the line's left side
        heel = L(60, 216 if safe else 262, p) * S
        shoe = [(heel, fy - 22 * S), (heel + 74 * S, fy - 22 * S), (heel + 92 * S, fy), (heel + 74 * S, fy + 22 * S), (heel, fy + 22 * S)]
    else:       # batter's back foot coming back from the right; it must cross back to the left of the line
        heel = L(440, 236 if safe else 268, p) * S
        shoe = [(heel, fy - 22 * S), (heel - 74 * S, fy - 22 * S), (heel - 92 * S, fy), (heel - 74 * S, fy + 22 * S), (heel, fy + 22 * S)]
        shoe = [(2 * lx - x, y) for x, y in shoe]
    lift = 16 * S * pr(t, 1.5, .4) if (not front and not safe) else 0
    shoe = [(x, y - lift) for x, y in shoe]
    d.polygon([(x + 5 * S, y + 7 * S + lift) for x, y in shoe], fill=(0, 0, 0, 90))
    d.polygon(shoe, fill=(235, 238, 246, 255), outline=(30, 40, 60, 255))
    zp = pr(t, 1.7, .3)
    if zp > 0:
        col = GREEN if result == "notout" else RED      # same colour as the final call
        d.rounded_rectangle([lx - 46 * S, fy - 46 * S, lx + 46 * S, fy + 46 * S], radius=8 * S, outline=(*col, int(255 * zp)), width=3 * S)
    return d


# ── one frame ────────────────────────────────────────────────────────────────
def _frame(t, kind, result):
    im = _bg().copy(); d = ImageDraw.Draw(im, "RGBA")
    _screen(d, t)
    if kind == "LBW":
        d = _lbw(im, d, t, result); label = "BALL TRACKING"
    elif kind == "Caught":
        d = _snicko(im, d, t, result); label = "ULTRAEDGE"
    else:
        d = _crease(im, d, t, kind, result); label = "FRONT FOOT" if kind == "Bowled" else "BACK FOOT"
    d = ImageDraw.Draw(im, "RGBA")
    _tag(d, t, label)

    checks, word, col, sub = _checks(kind, result)
    tx = 500 * S
    tp = eo(pr(t, 0, .35))
    d.text((tx + (1 - tp) * 60 * S, 40 * S), "DRS", font=_font(NAME, 44 * S), fill=(255, 255, 255, int(255 * tp)), anchor="lm")
    d.text((tx + 104 * S + (1 - tp) * 60 * S, 44 * S), "REVIEW", font=_font(LAB, 22 * S), fill=(*CYAN, int(255 * tp)), anchor="lm")
    d.line([tx, 66 * S, (W - 34) * S, 66 * S], fill=(*CYAN, int(120 * tp)), width=2 * S)
    if .3 < t < 1.0:
        d.text((tx, 92 * S), "ANALYSING" + "." * (1 + int(t * 4) % 3), font=_font(LAB, 22 * S), fill=(200, 215, 240, 220), anchor="lm")
    gap = 34 if len(checks) == 3 else 40
    for i, (lab, val, ccol) in enumerate(checks):
        ct = pr(t, 1.0 + i * .5, .3)
        if ct <= 0:
            continue
        y = (90 + i * gap) * S
        a = int(255 * eo(ct))
        d.text((tx, y), lab, font=_font(LAB, 22 * S), fill=(220, 228, 245, a), anchor="lm")
        cw = 168 * S; x0 = (W - 34) * S - cw - (1 - eo(ct)) * 80 * S
        d.rounded_rectangle([x0, y - 14 * S, x0 + cw, y + 14 * S], radius=8 * S, fill=(*ccol, a))
        d.text((x0 + cw / 2, y), val, font=_font(LAB, 19 * S), fill=(10, 14, 20, a), anchor="mm")
    fp = pr(t, 3.0, .35)
    if fp > 0:
        sc = L(1.8, 1.0, eo(fp)); a = int(255 * min(1, fp * 3))
        bx0, bx1, cy_ = tx - 8 * S, (W - 30) * S, 208 * S
        by0, by1 = cy_ - 24 * S * sc, cy_ + 24 * S * sc
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=10 * S, fill=(*col, a))
        d.text(((bx0 + bx1) / 2, cy_ - 2 * S), word, font=_font(NAME, int(38 * S * sc)), fill=(255, 255, 255, a), anchor="mm")
        if fp >= 1:
            d.text(((bx0 + bx1) / 2, by1 + 13 * S), sub.upper(), font=_font(LAB, 16 * S), fill=(220, 228, 245, 230), anchor="mm")
        else:
            im.paste(col, (0, 0), Image.new("L", im.size, int(80 * (1 - fp))))
    return im.reduce(S)


def _encode(frames: list) -> bytes:
    size = (ug.OUT_W, ug.OUT_H)
    frames = [f.resize(size, Image.LANCZOS) for f in frames]
    n = len(frames)
    key = [frames[int(n * k)] for k in (.2, .45, .7, .99)]
    sheet = Image.new("RGB", (size[0], size[1] * len(key)))
    for i, im in enumerate(key):
        sheet.paste(im, (0, i * size[1]))
    pal = sheet.quantize(colors=200, method=Image.Quantize.MEDIANCUT)
    pics = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:], duration=int(1000 / FPS), loop=0, disposal=1)
    return buf.getvalue()


def make_drs_gif(kind: str = "LBW", result: str = "out") -> bytes:
    kind = kind if kind in ("LBW", "Caught", "Bowled", "Stumped") else "LBW"
    if result == "ucall" and kind != "LBW":
        result = "out"
    n = int(DUR * FPS)
    return _encode([_frame(i / FPS, kind, result) for i in range(n)])


_cache: dict[tuple[str, str], bytes] = {}


async def drs_file(kind: str, result: str):
    """discord.File for the review, or None (never blocks a match)."""
    try:
        import discord
        key = (kind, result)
        if key not in _cache:
            _cache[key] = await asyncio.to_thread(make_drs_gif, kind, result)
        return discord.File(io.BytesIO(_cache[key]), filename="drs_review.gif")
    except Exception as e:
        print(f"[drs_gif] skipped ({type(e).__name__}: {e})")
        return None


if __name__ == "__main__":
    for k, r in (("LBW", "out"), ("LBW", "notout"), ("LBW", "ucall"), ("Caught", "out"), ("Caught", "notout"),
                 ("Bowled", "out"), ("Bowled", "notout"), ("Stumped", "out"), ("Stumped", "notout")):
        open(f"drs_{k}_{r}.gif", "wb").write(make_drs_gif(k, r))
    print("ok")
