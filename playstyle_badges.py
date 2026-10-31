"""Playstyle badges: one metal hexagon per playstyle and level, drawn by the bot itself.

    get_badge("Swing Master", 3)  -> PIL RGBA image (384 x 384) or None for an unknown playstyle

Levels (the match power lives in logic.PLAYSTYLE_LEVEL_POWER):
    1 = SILVER       (half power)
    2 = GOLD         (normal power, also every card without a level)
    3 = GOLDEN PINK  (1.5x power, rose-gold with sparkles)

Every badge is a brushed-metal hexagon with a dark engraved icon. Badges are drawn once and
saved in playstyle_badges/ (a cache, safe to delete: they are redrawn on the next card).
To change a design, edit its icon function below and delete the cache folder.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

CACHE_DIR = Path(__file__).parent / "playstyle_badges"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT = 384                      # card logos are drawn ~200 px, so 384 is plenty
S = 1024                       # drawing size (2x supersampling)
C = S / 2
_LOCK = threading.Lock()
_MEM: dict = {}

LEVEL_NAMES = {1: "Silver", 2: "Gold", 3: "Golden Pink"}

METALS = {
    1: {"rim": ((238, 241, 247), (112, 118, 132)), "face": ((226, 231, 239), (146, 154, 170)),
        "sheen": (255, 255, 255), "edge": (88, 94, 108), "lo": (255, 255, 255), "hi": (40, 44, 54)},
    2: {"rim": ((255, 236, 160), (150, 98, 20)), "face": ((246, 206, 96), (198, 140, 34)),
        "sheen": (255, 244, 200), "edge": (120, 78, 14), "lo": (255, 236, 170), "hi": (60, 40, 8)},
    3: {"rim": ((255, 222, 214), (170, 82, 104)), "face": ((250, 200, 196), (214, 120, 146)),
        "sheen": (255, 240, 214), "edge": (128, 52, 76), "lo": (255, 232, 228), "hi": (80, 24, 44)},
}


# ── geometry helpers ─────────────────────────────────────────────────────

def _hexagon(r, cx=C, cy=C):
    return [(cx + r * math.cos(math.radians(-90 + 60 * i)), cy + r * math.sin(math.radians(-90 + 60 * i)))
            for i in range(6)]


def _poly_mask(pts):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


def _rot(pts, ang_deg, cx, cy):
    a = math.radians(ang_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca) for x, y in pts]


def _lin_grad(c1, c2, angle=45):
    row = Image.new("RGB", (S * 2, 1))
    for x in range(S * 2):
        t = x / (S * 2 - 1)
        row.putpixel((x, 0), tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    big = row.resize((S * 2, S * 2)).rotate(-angle, resample=Image.BILINEAR)
    return big.crop((S // 2, S // 2, S // 2 + S, S // 2 + S))


def _line(d, p1, p2, w):
    d.line((p1, p2), fill=255, width=int(w))
    for (x, y) in (p1, p2):
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=255)


def _ring(d, cx, cy, r, w):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    d.ellipse((cx - r + w, cy - r + w, cx + r - w, cy + r - w), fill=0)


# ── icon parts (all draw WHITE on an "L" mask) ───────────────────────────

def _ball(m, cx, cy, r, tilt=-35):
    """Solid ball with its seam + stitches cut out."""
    d = ImageDraw.Draw(m)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    a = math.radians(tilt)

    def rp(x, y):
        return (cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))
    gap = r * 0.11
    for off in (-gap, gap):
        pts = [rp(off + r * 0.05 * math.cos((i / 40 - .5) * 2.2), (i / 40 - 0.5) * 2 * r * 1.02) for i in range(41)]
        d.line(pts, fill=0, width=max(3, int(r * 0.07)))
    for k in range(-8, 9):
        y = k / 9 * r * 0.88
        for off in (-gap, gap):
            d.line((rp(off, y), rp(off * 1.9, y + r * 0.03)), fill=0, width=max(2, int(r * 0.035)))


def _swoosh(m, cx, cy, R, a0, a1, wmax):
    """Tapered curved trail from angle a0 to a1 (degrees) around (cx, cy)."""
    d = ImageDraw.Draw(m)
    outer, inner = [], []
    n = 120
    for i in range(n + 1):
        t = i / n
        ang = math.radians(a0 + (a1 - a0) * t)
        w = 4 + wmax * math.sin(math.pi * min(1, t * 1.15)) ** 0.9
        rr = R + 20 * math.sin(math.pi * t)
        outer.append((cx + (rr + w / 2) * math.cos(ang), cy + (rr + w / 2) * math.sin(ang)))
        inner.append((cx + (rr - w / 2) * math.cos(ang), cy + (rr - w / 2) * math.sin(ang)))
    d.polygon(outer + inner[::-1], fill=255)


def _speed_lines(m, x, y, n=3, length=180, gap=70, w=24):
    d = ImageDraw.Draw(m)
    for i in range(n):
        l = length * (1 - 0.18 * abs(i - (n - 1) / 2))
        yy = y + (i - (n - 1) / 2) * gap
        _line(d, (x - l, yy + 10), (x, yy), w * (1 - 0.15 * abs(i - (n - 1) / 2)))


def _bat(m, cx, cy, length, width, angle, broken=False):
    """Cricket bat (blade + handle) centred at (cx, cy), rotated by angle (deg, 0 = handle up)."""
    blade_l = length * 0.68
    handle_l = length * 0.32
    top = cy - length / 2
    blade = [(cx - width / 2, top + handle_l), (cx + width / 2, top + handle_l),
             (cx + width / 2, top + length - width * 0.25), (cx, top + length),
             (cx - width / 2, top + length - width * 0.25)]
    handle = [(cx - width * 0.16, top), (cx + width * 0.16, top),
              (cx + width * 0.16, top + handle_l + 4), (cx - width * 0.16, top + handle_l + 4)]
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    d.polygon(_rot(blade, angle, cx, cy), fill=255)
    d.polygon(_rot(handle, angle, cx, cy), fill=255)
    # groove down the blade
    g = [(cx - width * 0.05, top + handle_l + 30), (cx + width * 0.05, top + handle_l + 30),
         (cx + width * 0.05, top + length - 60), (cx - width * 0.05, top + length - 60)]
    d.polygon(_rot(g, angle, cx, cy), fill=0)
    if broken:   # a zig-zag gap across the blade
        yb = top + handle_l + blade_l * 0.42
        zig = [(cx - width, yb - 20), (cx - width * 0.25, yb + 22), (cx + width * 0.2, yb - 24),
               (cx + width, yb + 14), (cx + width, yb + 74), (cx + width * 0.2, yb + 36),
               (cx - width * 0.25, yb + 82), (cx - width, yb + 40)]
        d.polygon(_rot(zig, angle, cx, cy), fill=0)
    return ImageChops.lighter(m, mask)


def _crown(m, cx, cy, w):
    d = ImageDraw.Draw(m)
    h = w * 0.55
    pts = [(cx - w / 2, cy + h / 2), (cx - w / 2, cy - h / 2 + 10), (cx - w / 4, cy),
           (cx, cy - h / 2 - 10), (cx + w / 4, cy), (cx + w / 2, cy - h / 2 + 10), (cx + w / 2, cy + h / 2)]
    d.polygon(pts, fill=255)
    for (x, y) in ((cx - w / 2, cy - h / 2 + 4), (cx, cy - h / 2 - 16), (cx + w / 2, cy - h / 2 + 4)):
        d.ellipse((x - 14, y - 14, x + 14, y + 14), fill=255)


def _stumps(m, cx, base, h, gap=58, w=30):
    d = ImageDraw.Draw(m)
    for i in (-1, 0, 1):
        x = cx + i * gap
        d.rounded_rectangle((x - w / 2, base - h, x + w / 2, base), radius=8, fill=255)
    d.rounded_rectangle((cx - gap - w / 2, base - h - 26, cx - 4, base - h - 10), radius=6, fill=255)
    d.rounded_rectangle((cx + 4, base - h - 26, cx + gap + w / 2, base - h - 10), radius=6, fill=255)


def _arrow_head(d, tip, ang_deg, size):
    a = math.radians(ang_deg)
    p1 = (tip[0] - size * math.cos(a - 0.5), tip[1] - size * math.sin(a - 0.5))
    p2 = (tip[0] - size * math.cos(a + 0.5), tip[1] - size * math.sin(a + 0.5))
    d.polygon([tip, p1, p2], fill=255)


def _arc_arrow(m, cx, cy, r, a0, a1, w, head=70):
    """Thick arc from a0 to a1 (degrees, clockwise) with an arrow head at a1."""
    d = ImageDraw.Draw(m)
    d.arc((cx - r, cy - r, cx + r, cy + r), a0, a1, fill=255, width=int(w))
    a = math.radians(a1)
    tip_r = r - w / 2
    tip = (cx + tip_r * math.cos(a), cy + tip_r * math.sin(a))
    _arrow_head(d, (tip[0] - 20 * math.sin(a), tip[1] + 20 * math.cos(a)), a1 + 90, head)


def _font(size):
    for f in ("display_bold.ttf", "name_font.ttf", "label.ttf"):
        p = Path(__file__).with_name(f)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── one icon per playstyle ───────────────────────────────────────────────

def _i_swing_master(m):
    _ball(m, C + 70, C - 20, 165)
    _swoosh(m, C + 55, C - 10, 225, 165, -50, 46)
    _speed_lines(m, C - 150, C - 40)


def _i_swing_king(m):
    _ball(m, C + 60, C + 40, 150)
    _swoosh(m, C + 45, C + 50, 205, 170, -40, 40)
    _crown(m, C - 20, C - 230, 230)


def _i_yorker_king(m):
    _stumps(m, C - 30, C + 230, 300)
    _ball(m, C + 150, C + 175, 72)
    _crown(m, C - 30, C - 215, 220)


def _i_lethal_pace(m):
    d = ImageDraw.Draw(m)
    bolt = [(C + 40, C - 300), (C - 150, C + 20), (C - 20, C + 20), (C - 90, C + 300),
            (C + 150, C - 50), (C + 20, C - 50), (C + 110, C - 300)]
    d.polygon(bolt, fill=255)
    _speed_lines(m, C - 170, C - 150, n=3, length=150, gap=55, w=20)


def _i_breakthrough(m):
    m.paste(_bat(m, C - 60, C + 40, 560, 120, -40))
    d = ImageDraw.Draw(m)
    bx, by = C + 120, C - 150
    for k in range(10):
        a = math.radians(k * 36 + 10)
        r1, r2 = 70, 190 if k % 2 == 0 else 140
        p1 = (bx + r1 * math.cos(a - 0.12), by + r1 * math.sin(a - 0.12))
        p2 = (bx + r2 * math.cos(a), by + r2 * math.sin(a))
        p3 = (bx + r1 * math.cos(a + 0.12), by + r1 * math.sin(a + 0.12))
        d.polygon([p1, p2, p3], fill=255)
    d.ellipse((bx - 50, by - 50, bx + 50, by + 50), fill=255)


def _i_aggressive(m):
    d = ImageDraw.Draw(m)
    for (x, h, w) in ((C - 150, 300, 120), (C, 400, 150), (C + 150, 300, 120)):   # three flames
        base = C + 260
        pts = []
        for i in range(41):
            t = i / 40
            y = base - h * t
            half = w / 2 * math.sin(math.pi * (1 - t) ** 0.7) * (1 - t * 0.2)
            pts.append((x + half + 18 * math.sin(t * 6), y))
        for i in range(40, -1, -1):
            t = i / 40
            y = base - h * t
            half = w / 2 * math.sin(math.pi * (1 - t) ** 0.7) * (1 - t * 0.2)
            pts.append((x - half + 18 * math.sin(t * 6), y))
        d.polygon(pts, fill=255)
    m.paste(ImageChops.subtract(m, _bat(Image.new("L", (S, S), 0), C, C - 30, 520, 150, 25).filter(ImageFilter.MaxFilter(25))))
    m.paste(_bat(m, C, C - 30, 520, 120, 25))


def _i_balanced(m):
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((C - 18, C - 230, C + 18, C + 230), radius=10, fill=255)
    d.rounded_rectangle((C - 140, C + 210, C + 140, C + 250), radius=12, fill=255)
    _line(d, (C - 250, C - 170), (C + 250, C - 170), 30)
    d.ellipse((C - 34, C - 214, C + 34, C - 146), fill=255)
    for sx in (-1, 1):
        x = C + sx * 230
        _line(d, (x, C - 170), (x - 90, C + 30), 10)
        _line(d, (x, C - 170), (x + 90, C + 30), 10)
        d.chord((x - 120, C - 50, x + 120, C + 110), 0, 180, fill=255)


def _i_anchor(m):
    d = ImageDraw.Draw(m)
    _ring(d, C, C - 230, 60, 28)
    d.rounded_rectangle((C - 22, C - 175, C + 22, C + 230), radius=10, fill=255)
    _line(d, (C - 150, C - 110), (C + 150, C - 110), 36)
    d.arc((C - 260, C - 60, C + 260, C + 260), 20, 160, fill=255, width=40)
    for sx in (-1, 1):
        tip = (C + sx * 245, C + 160 - 60)
        _arrow_head(d, (tip[0] + sx * 10, tip[1] - 30), -90 + sx * -30, 90)


def _i_low_batting(m):
    m.paste(_bat(m, C, C, 660, 170, 30, broken=True))


def _i_low_bowling(m):
    _ball(m, C, C - 110, 150)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((C - 30, C + 50, C + 30, C + 200), radius=10, fill=255)
    d.polygon([(C - 110, C + 170), (C + 110, C + 170), (C, C + 300)], fill=255)


def _i_cover_drive(m):
    m.paste(_bat(m, C - 110, C + 20, 520, 115, -35))
    d = ImageDraw.Draw(m)
    _arc_arrow(m, C - 40, C + 40, 300, 200, 320, 34, head=80)
    _ball(m, C + 220, C - 210, 62)


def _i_chase_master(m):
    d = ImageDraw.Draw(m)
    for r in (300, 200, 100):
        _ring(d, C, C, r, 46)
    d.ellipse((C - 40, C - 40, C + 40, C + 40), fill=255)
    _line(d, (C + 30, C - 30), (C + 300, C - 300), 30)
    for k in (0, 1):   # fletching
        off = 70 + k * 50
        d.polygon([(C + 230 - k * 40, C - 230 + k * 40), (C + 230 - k * 40 + 70, C - 230 + k * 40 - 10),
                   (C + 230 - k * 40 + 10, C - 230 + k * 40 - 70)], fill=255)


def _i_early_breaker(m):
    d = ImageDraw.Draw(m)
    hz = C + 120
    d.pieslice((C - 200, hz - 200, C + 200, hz + 200), 180, 360, fill=255)
    for k in range(7):
        a = math.radians(190 + k * 26.6)
        _line(d, (C + 245 * math.cos(a), hz + 245 * math.sin(a)), (C + 320 * math.cos(a), hz + 320 * math.sin(a)), 30)
    d.rounded_rectangle((C - 330, hz + 30, C + 330, hz + 66), radius=14, fill=255)
    d.rounded_rectangle((C - 220, hz + 110, C + 220, hz + 140), radius=12, fill=255)


def _i_googly_master(m):
    _ball(m, C, C, 170, tilt=20)
    _arc_arrow(m, C, C, 285, 200, 330, 36, head=80)
    _arc_arrow(m, C, C, 285, 20, 150, 36, head=80)


def _i_mr360(m):
    _arc_arrow(m, C, C, 310, -60, 250, 46, head=100)
    d = ImageDraw.Draw(m)
    d.text((C, C + 10), "360", font=_font(250), fill=255, anchor="mm")


def _i_mystery(m):
    """A hand-drawn question mark (the bundled fonts have no '?')."""
    d = ImageDraw.Draw(m)
    cx, cy, r, w = C, C - 130, 160, 74
    d.arc((cx - r, cy - r, cx + r, cy + r), 185, 410, fill=255, width=w)
    a = math.radians(410)
    end = (cx + (r - w / 2) * math.cos(a), cy + (r - w / 2) * math.sin(a))
    _line(d, end, (C, C + 70), w)
    _line(d, (C, C + 70), (C, C + 120), w)
    d.ellipse((C - 50, C + 190, C + 50, C + 290), fill=255)


def _i_spell_finisher(m):
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((C - 200, C - 300, C + 200, C - 260), radius=14, fill=255)
    d.rounded_rectangle((C - 200, C + 260, C + 200, C + 300), radius=14, fill=255)
    glass = [(C - 160, C - 250), (C + 160, C - 250), (C + 30, C - 10), (C + 30, C + 10),
             (C + 160, C + 250), (C - 160, C + 250), (C - 30, C + 10), (C - 30, C - 10)]
    d.polygon(glass, fill=255)
    inner_top = [(C - 110, C - 215), (C + 110, C - 215), (C + 14, C - 30), (C - 14, C - 30)]
    d.polygon(inner_top, fill=0)
    d.polygon([(C - 110, C + 215), (C + 110, C + 215), (C, C + 110)], fill=0)
    d.polygon([(C - 80, C + 200), (C + 80, C + 200), (C, C + 130)], fill=255)   # sand at the bottom
    d.polygon([(C - 60, C - 200), (C + 60, C - 200), (C, C - 120)], fill=255)   # sand left at the top


def _i_mind_games(m):
    d = ImageDraw.Draw(m)
    pts = []
    for i in range(400):
        t = i / 399
        a = t * math.pi * 6.2
        r = 20 + 290 * t
        pts.append((C + r * math.cos(a), C + r * math.sin(a)))
    for a, b in zip(pts, pts[1:]):
        _line(d, a, b, 34)


ICONS = {
    "Swing Master": _i_swing_master,
    "Swing King": _i_swing_king,
    "Yorker King": _i_yorker_king,
    "Lethal Pace": _i_lethal_pace,
    "Breakthrough": _i_breakthrough,
    "Aggressive": _i_aggressive,
    "Balanced": _i_balanced,
    "Anchor": _i_anchor,
    "Low Batting Quality": _i_low_batting,
    "Low Bowling Quality": _i_low_bowling,
    "Cover Drive Specialist": _i_cover_drive,
    "Chase Master": _i_chase_master,
    "Early Breaker": _i_early_breaker,
    "Googly Master": _i_googly_master,
    "Mr360": _i_mr360,
    "Mystery": _i_mystery,
    "Spell Finisher": _i_spell_finisher,
    "Mind Games": _i_mind_games,
}


# ── the badge ────────────────────────────────────────────────────────────

def _sparkle(d, x, y, r, col):
    d.polygon([(x, y - r), (x + r * 0.22, y - r * 0.22), (x + r, y), (x + r * 0.22, y + r * 0.22),
               (x, y + r), (x - r * 0.22, y + r * 0.22), (x - r, y), (x - r * 0.22, y - r * 0.22)], fill=col)


def _render(name: str, level: int) -> Image.Image:
    mt = METALS[level]
    outer = _poly_mask(_hexagon(480))
    face = _poly_mask(_hexagon(440))
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste((0, 0, 0, 255), (0, 14), outer.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.5)))
    img.paste(_lin_grad(*mt["rim"], 45).convert("RGBA"), (0, 0), outer)
    facec = _lin_grad(*mt["face"], 60)
    sheen = Image.new("L", (S, S), 0)
    sd = ImageDraw.Draw(sheen)
    for i in range(200):
        v = int(110 * math.exp(-((i - 100) / 45) ** 2))
        sd.line((-150 + i * 3, S, 350 + i * 3, 0), fill=v, width=4)
    sheen = sheen.filter(ImageFilter.GaussianBlur(20))
    facec = Image.composite(Image.new("RGB", (S, S), mt["sheen"]), facec, sheen)
    noise = Image.effect_noise((S, S // 8), 18).resize((S, S))
    facec = Image.blend(facec, Image.merge("RGB", (noise, noise, noise)), 0.05)
    img.paste(facec.convert("RGBA"), (0, 0), face)
    edge = ImageChops.subtract(_poly_mask(_hexagon(442)), _poly_mask(_hexagon(435)))
    img.paste(mt["edge"] + (255,), (0, 0), edge)

    # engraved icon
    icon = Image.new("L", (S, S), 0)
    ICONS[name](icon)
    icon = ImageChops.multiply(icon, _poly_mask(_hexagon(410)))
    icon_s = icon.filter(ImageFilter.GaussianBlur(0.8))
    tex = Image.new("L", (S, S), 0)
    td = ImageDraw.Draw(tex)
    for x in range(-S, S, 13):
        td.line((x, 0, x + S, S), fill=60, width=2)
        td.line((x + S, 0, x, S), fill=60, width=2)
    engraved = Image.composite(Image.new("RGBA", (S, S), (70, 62, 52, 255)),
                               Image.new("RGBA", (S, S), (26, 22, 18, 255)), tex)
    lo = ImageChops.subtract(icon_s, ImageChops.offset(icon_s, 0, -5))
    hi = ImageChops.subtract(icon_s, ImageChops.offset(icon_s, 0, 5))
    img.paste(engraved, (0, 0), icon_s)
    img.paste(mt["lo"] + (255,), (0, 0), lo.point(lambda v: int(v * 0.9)))
    img.paste(mt["hi"] + (255,), (0, 0), hi.point(lambda v: int(v * 0.6)))

    if level == 3:   # Golden Pink: a few sparkles on the rim
        d = ImageDraw.Draw(img)
        for (x, y, r) in ((C + 330, C - 300, 46), (C - 360, C + 250, 34), (C + 395, C + 120, 26)):
            _sparkle(d, x, y, r, (255, 250, 240, 255))
    return img.resize((OUT, OUT), Image.LANCZOS)


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def canonical(name: str | None) -> str | None:
    if not name:
        return None
    low = {k.lower(): k for k in ICONS}
    return low.get(str(name).strip().lower())


def get_badge(name: str | None, level: int | None) -> Image.Image | None:
    """The badge image for a playstyle at a level (None level = Gold). None if unknown."""
    key = canonical(name)
    if key is None:
        return None
    lvl = level if level in (1, 2, 3) else 2
    ck = (key, lvl)
    hit = _MEM.get(ck)
    if hit is not None:
        return hit.copy()
    path = CACHE_DIR / f"{_slug(key)}_L{lvl}.png"
    with _LOCK:
        try:
            if path.exists():
                img = Image.open(path).convert("RGBA")
            else:
                img = _render(key, lvl)
                img.quantize(colors=256, method=Image.FASTOCTREE, dither=Image.NONE).save(path, "PNG", optimize=True)
        except Exception as e:
            print(f"[playstyle badge] {key} L{lvl} failed: {type(e).__name__}: {e}")
            return None
    if len(_MEM) > 60:
        _MEM.clear()
    _MEM[ck] = img
    return img.copy()


if __name__ == "__main__":     # python playstyle_badges.py  -> draws every badge into the cache
    for n in ICONS:
        for lv in (1, 2, 3):
            get_badge(n, lv)
    print("done:", len(ICONS) * 3, "badges in", CACHE_DIR)
