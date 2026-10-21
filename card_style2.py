"""Style 2 card renderer: purple shield, gold border, left column, 6-stat grid.

Entry point: build_card_style2(card_row, fg_image) -> io.BytesIO (PNG, 1037 x 1517).

How it works
------------
The design is drawn in a 1288 x 1800 "design space" (same as the reference
image) at 2x supersampling. Only the card area (CROP) is rendered, then it is
shrunk to 1037 x 1517 so it lines up with the classic cards everywhere
(squad, csview, draft ...).

The static parts (shield mask, gradient background, texture, dark fades, gold
border) are built ONCE and cached. Each card only draws its own layers:
player cutout, text, flag, logos.

Layer order: background -> player -> dark fades -> text/flag/logos -> clip to
the shield -> gold border.
"""

from __future__ import annotations

import io
import random
import threading

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

import card_image as ci
from card_stats import card_stat_rows

# ── Sizes ────────────────────────────────────────────────────────────────
S = 2                                  # supersampling factor
DESIGN_W, DESIGN_H = 1288, 1800        # design space
CROP = (84, 52, 1195, 1678)            # card area inside the design space
OUT_W, OUT_H = ci.CANVAS_W, ci.CANVAS_H   # 1037 x 1517
CW, CH = (CROP[2] - CROP[0]) * S, (CROP[3] - CROP[1]) * S   # working size

# ── Colours ──────────────────────────────────────────────────────────────
CREAM = (238, 228, 165, 255)
GOLD = (232, 214, 140, 255)
NAVY = (14, 16, 28)                    # RGB of transparent pixels
SHADE = (14, 6, 30)                    # colour of the dark fades

SHIELD = [
    (152, 300), (560, 142), (600, 152), (640, 82), (680, 152), (720, 142),
    (760, 106), (1128, 295), (1128, 1405), (1092, 1475), (640, 1648),
    (188, 1475), (152, 1405),
]

# Foreground fit box (design space) for the player cutout.
FG_BOX = {"x": 430, "y": 215, "w": 690, "h": 880}

# "Transparency" option: the player cutout fades to nothing between these two (ends exactly at the line under the name)
# design-space y values, so legs / shorts never show behind the name and stats.
FADE_START_Y, FADE_END_Y = 1050, 1097   # 1097 = the line under the name

NAME_COLORS = {
    "red": (235, 45, 45, 255), "blue": (65, 135, 255, 255), "yellow": (255, 222, 45, 255),
    "pink": (255, 105, 180, 255), "green": (60, 205, 95, 255), "orange": (255, 140, 30, 255),
}
LINE_COLORS = {
    "black": (0, 0, 0, 255), "white": (255, 255, 255, 255), "blue": (65, 135, 255, 255),
}

ROLE_WORD = {"BAT": "BAT", "BOWL": "BOWL", "AR": "AR", "WK": "WK"}


# ── Small helpers ────────────────────────────────────────────────────────

def _p(x: float, y: float) -> tuple[int, int]:
    """Design-space point -> working-canvas pixel."""
    return int(round((x - CROP[0]) * S)), int(round((y - CROP[1]) * S))


def _lfont(size: float) -> ImageFont.FreeTypeFont:
    """label.ttf (Barlow Condensed Bold) at a design-space size."""
    return ci._font(ci.FONT_LABEL, int(size * S))


def _fit_font(draw: ImageDraw.ImageDraw, text: str, size: float, max_w: float):
    """label.ttf font that is shrunk until `text` is at most `max_w` design px wide."""
    font = _lfont(size)
    try:
        width = draw.textlength(text, font=font)
        limit = max_w * S
        if width > limit and width > 0:
            # Shrink in one step, then nudge down until it really fits.
            new_size = max(8, int(font.size * limit / width))
            font = font.font_variant(size=new_size)
            while new_size > 8 and draw.textlength(text, font=font) > limit:
                new_size -= 2
                font = font.font_variant(size=new_size)
    except Exception:
        pass   # keep the unshrunk font rather than crash
    return font


def _line(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, width=2, color=CREAM) -> None:
    draw.line([_p(x1, y1), _p(x2, y2)], fill=color, width=width * S)


def _erode(mask: Image.Image, dist: float) -> Image.Image:
    """Shrink a shield mask by about `dist` design px (blur, then threshold)."""
    blurred = mask.filter(ImageFilter.GaussianBlur(radius=max(1.0, dist / 2 * S)))
    return blurred.point(lambda v: 255 if v >= 250 else 0)


# ── Static layers (built once) ───────────────────────────────────────────

_STATIC: dict = {}
_STATIC_LOCK = threading.Lock()
_RENDER_LOCK = threading.Lock()   # one card at a time keeps RAM low on a small VPS


def _build_static() -> dict:
    # Shield mask
    mask = Image.new("L", (CW, CH), 0)
    ImageDraw.Draw(mask).polygon([_p(x, y) for x, y in SHIELD], fill=255)

    # Gradient background: top -> mid (45%) -> bottom, made as 1 px wide column.
    top, mid, bot = (160, 75, 240), (88, 40, 150), (22, 8, 44)
    column = Image.new("RGB", (1, CH))
    for y in range(CH):
        t = (y / S + CROP[1]) / DESIGN_H     # position in design space
        t = min(max(t, 0.0), 1.0)
        if t < 0.45:
            a, b, k = top, mid, t / 0.45
        else:
            a, b, k = mid, bot, (t - 0.45) / 0.55
        column.putpixel((0, y), tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))
    bg = column.resize((CW, CH), Image.NEAREST).convert("RGBA")

    # Faint diagonal texture strokes + a few light bars on the upper right.
    tex = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    td = ImageDraw.Draw(tex)
    rnd = random.Random(7)
    for _ in range(90):
        x, y = rnd.randint(100, 1150), rnd.randint(100, 1600)
        length = rnd.randint(120, 420)
        td.line([_p(x, y), _p(x + length, y - length)],
                fill=(255, 255, 255, rnd.randint(5, 16)), width=rnd.randint(2, 6) * S)
    for i in range(5):
        x = 860 + i * 50
        td.polygon([_p(x, 500), _p(x + 22, 500), _p(x - 160, 900), _p(x - 182, 900)],
                   fill=(255, 255, 255, 18))
    bg = Image.alpha_composite(bg, tex)
    del tex
    bg_rgb = bg.convert("RGB")
    del bg

    # Dark fades as ONE alpha mask (the colour is always SHADE).
    shade = Image.new("L", (CW, CH), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(930, 1500):           # bottom fade, up to alpha ~215
        a = int(215 * ((y - 930) / (1500 - 930)) ** 1.2)
        sd.line([_p(0, y), _p(DESIGN_W, y)], fill=a, width=S)
    sd.rectangle([_p(0, 1500), (CW, CH)], fill=215)   # keep the fade solid below it
    band = Image.new("L", (CW, CH), 0)
    bd = ImageDraw.Draw(band)
    for y in range(200, 1090):           # translucent band behind the left column
        t = (y - 200) / 890
        a = int(120 * min(1.0, t * 6) * min(1.0, (1 - t) * 6))
        bd.line([_p(275, y), _p(480, y)], fill=a, width=S)
    shade = ImageChops.lighter(shade, band)

    # Gold border: thick outer ring + faint thin inner ring.
    inner1 = _erode(mask, 15)
    ring = ImageChops.subtract(mask, inner1)
    thin_out = _erode(mask, 27)
    thin_in = _erode(mask, 30)
    thin = ImageChops.subtract(thin_out, thin_in).point(lambda v: int(v * 0.22))
    border = ImageChops.lighter(ring, thin)

    return {"mask": mask, "bg": bg_rgb, "shade": shade, "border": border}


def _static() -> dict:
    with _STATIC_LOCK:
        if not _STATIC:
            _STATIC.update(_build_static())
        return _STATIC



# ── Optional per-card background (set with /cardstats background:...) ────
_BG_CACHE: dict = {}     # (path, mtime) -> fitted RGB image; tiny on purpose (RAM)


def _custom_bg(card_row) -> Image.Image | None:
    """The card's own Style 2 background, fitted to the card. None = use the
    built-in purple gradient (also when the template is missing/unreadable)."""
    name = ci._col(card_row, "s2_bg")
    if not name:
        return None
    try:
        from card_db import get_background
        row = get_background(name)
        if row is None:
            return None
        path = row["local_path"]
        key = (path, Path(path).stat().st_mtime)
        hit = _BG_CACHE.get(key)
        if hit is None:
            img = Image.open(path).convert("RGB")
            hit = ImageOps.fit(img, (CW, CH), Image.LANCZOS, centering=(0.5, 0.5))
            if len(_BG_CACHE) >= 3:
                _BG_CACHE.pop(next(iter(_BG_CACHE)))
            _BG_CACHE[key] = hit
        return hit
    except Exception:
        return None


def _hexagon(cx: float, cy: float, r: float) -> list:
    return [_p(cx + r * math.cos(math.radians(30 + 60 * k)),
               cy + r * math.sin(math.radians(30 + 60 * k))) for k in range(6)]


# Medal colours: (rim, face, inner ring, inner face, gem, gem light facet, gem shadow facet)
MEDAL_COLORS = {
    "gold": ((120, 80, 24, 255), (236, 196, 96, 255), (150, 104, 34, 255), (214, 168, 70, 255),
             (252, 228, 150, 255), (255, 246, 205, 255), (196, 146, 54, 255)),
    "bronze": ((84, 44, 18, 255), (205, 127, 68, 255), (120, 66, 30, 255), (176, 100, 48, 255),
               (226, 160, 106, 255), (242, 196, 150, 255), (140, 78, 36, 255)),
}


def _draw_medal_icon(canvas: Image.Image, cx: float, cy: float, r: float, kind: str = "gold") -> None:
    """Small medal (hexagon + gem) in the "gold" or "bronze" colours. Drawn in code,
    so no image file is needed."""
    rim, face, ring, inner, gem_c, light, shadow = MEDAL_COLORS.get(kind, MEDAL_COLORS["gold"])
    d = ImageDraw.Draw(canvas)
    d.polygon(_hexagon(cx, cy, r), fill=rim)
    d.polygon(_hexagon(cx, cy, r * 0.93), fill=face)
    d.polygon(_hexagon(cx, cy, r * 0.72), fill=ring)
    d.polygon(_hexagon(cx, cy, r * 0.62), fill=inner)
    gem = [_p(cx, cy - r * 0.40), _p(cx + r * 0.34, cy), _p(cx, cy + r * 0.40), _p(cx - r * 0.34, cy)]
    d.polygon(gem, fill=gem_c)
    d.polygon([gem[0], gem[1], _p(cx, cy)], fill=light)     # light facet
    d.polygon([gem[2], gem[3], _p(cx, cy)], fill=shadow)    # shadow facet


# ── Per-card drawing ─────────────────────────────────────────────────────

def _draw_player(canvas: Image.Image, card_row, fg) -> None:
    try:
        fg = fg.convert("RGBA")
        box = {
            "x": (FG_BOX["x"] - CROP[0]) * S, "y": (FG_BOX["y"] - CROP[1]) * S,
            "w": FG_BOX["w"] * S, "h": FG_BOX["h"] * S,
        }
        resized, pos = ci._fit_foreground(
            fg, box,
            int(ci._col(card_row, "offset_x", 0)) * S,
            int(ci._col(card_row, "offset_y", 0)) * S,
            int(ci._col(card_row, "scale_pct", 100)),
        )
        if ci._col(card_row, "s2_fade_legs", 0) in (1, "1", True):
            # Multiply the cutout's own alpha by a vertical gradient: 1 above
            # FADE_START_Y, 0 from FADE_END_Y down (smooth in between).
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            layer.paste(resized, pos, resized)
            y0 = int((FADE_START_Y - CROP[1]) * S)
            y1 = int((FADE_END_Y - CROP[1]) * S)
            ramp = Image.new("L", (1, canvas.size[1]), 255)
            for y in range(max(0, y0), canvas.size[1]):
                t = 1.0 if y >= y1 else (y - y0) / max(1, (y1 - y0))
                t = t * t * (3 - 2 * t)                    # smoothstep
                ramp.putpixel((0, y), int(round(255 * (1 - t))))
            ramp = ramp.resize(canvas.size, Image.NEAREST)
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), ramp))
            canvas.alpha_composite(layer)
        else:
            canvas.paste(resized, pos, resized)
    except Exception:
        pass   # a bad cutout must never stop the card


def _draw_left_column(canvas: Image.Image, draw: ImageDraw.ImageDraw, card_row, role: str) -> None:
    cx = 372
    # OVR number
    try:
        text = str(card_row["ovr"])
        font = _fit_font(draw, text, 250, 190)
        draw.text(_p(cx, 365), text, font=font, fill=CREAM, anchor="mm")
    except Exception:
        pass
    # Role word
    try:
        word = ROLE_WORD.get(role, role)
        font = _fit_font(draw, word, 110, 190)
        draw.text(_p(cx, 500), word, font=font, fill=CREAM, anchor="mm")
    except Exception:
        pass
    _line(draw, 322, 558, 424, 558)

    # Flag (or the country name in small text if there is no flag)
    flag_done = False
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _p(371, 633), 0, 0, 100, box=160 * S)
            flag_done = True
    except Exception:
        flag_done = False
    if not flag_done:
        try:
            country = str(ci._col(card_row, "country", "")).upper()
            if country:
                font = _fit_font(draw, country, 44, 170)
                draw.text(_p(371, 633), country, font=font, fill=CREAM, anchor="mm")
        except Exception:
            pass
    _line(draw, 322, 712, 424, 712)

    # Playstyle logos (slot 1, then slot 2). No logo = nothing drawn.
    logos = []
    for slot in (1, 2):
        try:
            logo = ci._load_logo(ci._col(card_row, f"playstyle{slot}"))
        except Exception:
            logo = None
        if logo is not None:
            logos.append(logo)

    medal = str(ci._col(card_row, "s2_icon", "") or "").lower()
    if medal in MEDAL_COLORS:
        # (icon y, icon radius, logo ys, logo box) for 0 / 1 / 2 logos
        icon_y, icon_r, ys, box = {
            0: (805, 52, [], 100),
            1: (772, 44, [895], 92),
            2: (752, 38, [838, 925], 78),
        }[len(logos)]
        try:
            _draw_medal_icon(canvas, cx, icon_y, icon_r, medal)
        except Exception:
            pass
    else:
        ys = [810] if len(logos) == 1 else [790, 905]
        box = 100
    for logo, y in zip(logos, ys):
        try:
            ci._draw_logo(canvas, logo, _p(cx, y), 0, 0, 100, box=box * S)
        except Exception:
            pass


def _draw_name_and_stats(draw: ImageDraw.ImageDraw, card_row) -> None:
    # Name
    try:
        name = str(card_row["displayname"]).upper()
        font = _fit_font(draw, name, 150, 640)
        name_fill = NAME_COLORS.get(str(ci._col(card_row, "s2_name_color", "")).lower(), CREAM)
        draw.text(_p(640, 1045), name, font=font, fill=name_fill, anchor="ms",
                  stroke_width=max(2, round(font.size * 0.05)), stroke_fill=(0, 0, 0))
    except Exception:
        pass
    line_fill = LINE_COLORS.get(str(ci._col(card_row, "s2_line_color", "")).lower(), CREAM)
    _line(draw, 303, 1097, 998, 1097, color=line_fill)

    # 6-stat grid. Numbers are right-aligned so 3-digit values never touch the label.
    font = _lfont(112)
    rows = card_stat_rows(card_row)
    ys = [1190, 1302, 1415]
    for i, (label, value) in enumerate(rows[:6]):
        col, y = i // 3, ys[i % 3]
        num_x, lab_x = (400, 420) if col == 0 else (850, 870)
        draw.text(_p(num_x, y), str(value), font=font, fill=CREAM, anchor="rm")
        draw.text(_p(lab_x, y), label, font=font, fill=CREAM, anchor="lm")
    _line(draw, 646, 1143, 646, 1448)
    _line(draw, 573, 1495, 721, 1495)


def build_card_style2(card_row, fg_image) -> io.BytesIO:
    """Render one Style 2 card. CPU-bound: call it with asyncio.to_thread."""
    from card_db import effective_role

    static = _static()
    role = effective_role(card_row)

    with _RENDER_LOCK:
        canvas = (_custom_bg(card_row) or static["bg"]).convert("RGBA")

        _draw_player(canvas, card_row, fg_image)

        canvas.paste(SHADE + (255,), (0, 0), static["shade"])   # dark fades

        draw = ImageDraw.Draw(canvas)
        _draw_left_column(canvas, draw, card_row, role)
        _draw_name_and_stats(draw, card_row)

        # Gold border, then clip to the shield. Pixels outside the shield get an
        # RGB of NAVY, so code that flattens to RGB shows navy and not pure black.
        canvas.paste(GOLD, (0, 0), static["border"])

        # Solid colour layer: shield on NAVY. It is resized on its own (no alpha),
        # so the hidden colour under transparent pixels is NAVY and NEVER black.
        # (Resizing an RGBA image turns fully transparent pixels black, which is
        # what showed up as black boxes wherever a viewer/collage flattened the PNG.)
        solid = Image.new("RGB", canvas.size, NAVY)
        solid.paste(canvas.convert("RGB"), (0, 0), static["mask"])
        del canvas
        solid = solid.resize((OUT_W, OUT_H), Image.LANCZOS)

        # Outside of the shield is always see-through (same as the original Style 2).
        alpha = static["mask"].resize((OUT_W, OUT_H), Image.LANCZOS)
        out = solid.convert("RGBA")
        out.putalpha(alpha)
        del solid

    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
