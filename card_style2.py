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

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

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


def _line(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, width=2) -> None:
    draw.line([_p(x1, y1), _p(x2, y2)], fill=CREAM, width=width * S)


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
    ys = [810] if len(logos) == 1 else [790, 905]
    for logo, y in zip(logos, ys):
        try:
            ci._draw_logo(canvas, logo, _p(cx, y), 0, 0, 100, box=100 * S)
        except Exception:
            pass


def _draw_name_and_stats(draw: ImageDraw.ImageDraw, card_row) -> None:
    # Name
    try:
        name = str(card_row["displayname"]).upper()
        font = _fit_font(draw, name, 150, 640)
        draw.text(_p(640, 1045), name, font=font, fill=CREAM, anchor="ms")
    except Exception:
        pass
    _line(draw, 303, 1097, 998, 1097)

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
        canvas = static["bg"].convert("RGBA")

        _draw_player(canvas, card_row, fg_image)

        canvas.paste(SHADE + (255,), (0, 0), static["shade"])   # dark fades

        draw = ImageDraw.Draw(canvas)
        _draw_left_column(canvas, draw, card_row, role)
        _draw_name_and_stats(draw, card_row)

        # Gold border, then clip to the shield. Pixels outside the shield get an
        # RGB of NAVY, so code that flattens to RGB shows navy and not pure black.
        canvas.paste(GOLD, (0, 0), static["border"])
        out = Image.new("RGBA", canvas.size, NAVY + (0,))
        out.paste(canvas, (0, 0), static["mask"])
        del canvas

        out = out.resize((OUT_W, OUT_H), Image.LANCZOS)

    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
