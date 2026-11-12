"""Full Size card design (used by every normal Style 2 card when /cardshape = Full Size).

Entry point: build(card_row, fg_image, role) -> io.BytesIO (PNG, 1037 x 1517).

Layout (no 6-stat grid):
  * name top-left: first name thin, surname big and bold
  * big player cutout in the middle (auto-trimmed, same size for every player)
  * bottom panel: role word on a raised centre tab, BATTING and BOWLING numbers
    left / right (main skill on the left), OVR inside a hexagon in the centre
    with the flag and the first playstyle badge on either side
  * accent colour follows the OVR: bronze < 70, silver 70-79, gold 80-89, ice 90+

Background: the card's own Style 2 background (s2_bg via /bgadder) if it has one,
otherwise a built-in dark navy design.
"""

from __future__ import annotations

import io
import math
import random
import threading

from PIL import Image, ImageDraw, ImageFilter, ImageOps

import card_image as ci
from card_narratives import ROLE_CARD_WORD

S = 2                                   # supersampling
OUT_W, OUT_H = ci.CANVAS_W, ci.CANVAS_H  # 1037 x 1517
W, H = OUT_W * S, OUT_H * S

RADIUS = 46                              # outer corner radius (output px)
INNER_INSET = 22                         # thin inner border line
WHITE = (245, 246, 250, 255)
PANEL_FILL = (7, 11, 26, 238)

# Player box (output px). Bottom sits just under the top of the bottom panel.
FG_BOX = {"x": 250, "y": 175, "w": 770, "h": 955}

# Bottom panel shape (output px)
PANEL_TOP = 1165                         # side shoulders
TAB_TOP = 1105                           # raised centre tab (role word)
TAB_X = (330, 707)
PANEL_BOTTOM = OUT_H - 44
PANEL_SIDE = 40

ACCENTS = [                               # (min ovr, colour)
    (90, (130, 225, 255)),                # ice
    (80, (236, 196, 96)),                 # gold
    (70, (205, 212, 224)),                # silver
    (0, (214, 138, 80)),                  # bronze
]


def _accent(ovr) -> tuple:
    try:
        o = int(ovr)
    except Exception:
        o = 0
    for low, col in ACCENTS:
        if o >= low:
            return col
    return ACCENTS[-1][1]


def _q(x: float, y: float) -> tuple[int, int]:
    return int(round(x * S)), int(round(y * S))


def _font(path, size):
    return ci._font(path, int(size * S))


def _fit(draw, text, path, size, max_w):
    """Largest font <= size (output px) whose text is at most max_w wide."""
    f = _font(path, size)
    while size > 20 and draw.textlength(text, font=f) > max_w * S:
        size -= 3
        f = _font(path, size)
    return f


# ── static pieces (built once) ───────────────────────────────────────────
_STATIC: dict = {}
_LOCK = threading.Lock()
_BG_CACHE: dict = {}


def _build_static() -> dict:
    # outer rounded mask
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), radius=RADIUS * S, fill=255)

    # built-in background: radial navy glow + faint diagonal strokes
    small = Image.new("RGB", (OUT_W // 4, OUT_H // 4))
    cx, cy = small.width * 0.55, small.height * 0.38
    maxd = math.hypot(small.width, small.height) * 0.62
    inner, outer = (28, 44, 92), (5, 8, 20)
    px = small.load()
    for y in range(small.height):
        for x in range(small.width):
            t = min(1.0, math.hypot(x - cx, y - cy) / maxd) ** 1.15
            px[x, y] = tuple(int(inner[i] + (outer[i] - inner[i]) * t) for i in range(3))
    bg = small.resize((W, H), Image.BICUBIC).convert("RGBA")
    tex = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tex)
    rnd = random.Random(11)
    for _ in range(70):
        x, y = rnd.randint(-100, OUT_W), rnd.randint(0, OUT_H)
        ln = rnd.randint(150, 420)
        td.line([_q(x, y), _q(x + ln, y - ln)], fill=(255, 255, 255, rnd.randint(4, 12)),
                width=rnd.randint(2, 5) * S)
    bg = Image.alpha_composite(bg, tex)

    # bottom panel polygon (raised centre tab, cut corners on the tab)
    t0, t1 = TAB_X
    panel_pts = [
        _q(PANEL_SIDE, PANEL_TOP), _q(t0 - 34, PANEL_TOP), _q(t0, TAB_TOP),
        _q(t1, TAB_TOP), _q(t1 + 34, PANEL_TOP), _q(OUT_W - PANEL_SIDE, PANEL_TOP),
        _q(OUT_W - PANEL_SIDE, PANEL_BOTTOM), _q(PANEL_SIDE, PANEL_BOTTOM),
    ]
    panel = Image.new("L", (W, H), 0)
    ImageDraw.Draw(panel).polygon(panel_pts, fill=255)

    # soft dark fade above the panel so the player's legs melt into it
    fade = Image.new("L", (W, H), 0)
    fd = ImageDraw.Draw(fade)
    y0, y1 = 930, PANEL_TOP + 10
    for y in range(y0, OUT_H):
        a = 200 if y >= y1 else int(200 * ((y - y0) / (y1 - y0)) ** 1.4)
        fd.line([_q(0, y), _q(OUT_W, y)], fill=a, width=S)

    return {"mask": mask, "bg": bg.convert("RGB"), "panel": panel, "panel_pts": panel_pts, "fade": fade}


def _static() -> dict:
    with _LOCK:
        if not _STATIC:
            _STATIC.update(_build_static())
        return _STATIC


def _card_bg(card_row) -> Image.Image | None:
    name = ci._col(card_row, "s2_bg")
    if not name:
        return None
    try:
        from pathlib import Path
        from card_db import get_background
        row = get_background(name)
        if row is None:
            return None
        path = row["local_path"]
        key = (path, Path(path).stat().st_mtime)
        hit = _BG_CACHE.get(key)
        if hit is None:
            hit = ImageOps.fit(Image.open(path).convert("RGB"), (W, H), Image.LANCZOS)
            if len(_BG_CACHE) >= 3:
                _BG_CACHE.pop(next(iter(_BG_CACHE)))
            _BG_CACHE[key] = hit
        return hit
    except Exception:
        return None


# ── drawing helpers ──────────────────────────────────────────────────────

def _shadow_text(canvas, xy, text, font, fill, anchor="ls", blur=6, alpha=170):
    try:
        d = ImageDraw.Draw(canvas)
        x0, y0, x1, y1 = d.textbbox(xy, text, font=font, anchor=anchor)
        pad = blur * S * 3
        box = (int(x0) - pad, int(y0) - pad, int(x1) + pad, int(y1) + pad)
        layer = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
        ImageDraw.Draw(layer).text((xy[0] - box[0], xy[1] - box[1] + 3 * S), text, font=font,
                                   fill=alpha, anchor=anchor)
        layer = layer.filter(ImageFilter.GaussianBlur(blur * S))
        canvas.paste((0, 0, 0, 255), (box[0], box[1]), layer)
    except Exception:
        pass
    ImageDraw.Draw(canvas).text(xy, text, font=font, fill=fill, anchor=anchor)


def _hexagon(cx, cy, r):
    return [_q(cx + r * math.cos(math.radians(90 + 60 * k)), cy + r * math.sin(math.radians(90 + 60 * k)))
            for k in range(6)]


def _draw_player(canvas, card_row, fg):
    try:
        import card_style2 as c2
        fg = c2._trim_foreground(fg.convert("RGBA"))
        manual = c2.FULL_USE_MANUAL_FIX
        box = {k: v * S for k, v in FG_BOX.items()}
        resized, pos = ci._fit_foreground(
            fg, box,
            int(ci._col(card_row, "offset_x", 0)) * S if manual else 0,
            int(ci._col(card_row, "offset_y", 0)) * S if manual else 0,
            int(ci._col(card_row, "scale_pct", 100)) if manual else 100,
        )
        # soft glow behind the player
        glow = Image.new("L", canvas.size, 0)
        glow.paste(resized.getchannel("A"), pos)
        glow = glow.resize((W // 8, H // 8)).filter(ImageFilter.GaussianBlur(6)).resize((W, H))
        canvas.paste((255, 255, 255, 255), (0, 0), glow.point(lambda v: int(v * 0.16)))
        canvas.alpha_composite(resized, pos)
    except Exception as e:
        print(f"[card_full] player skipped ({type(e).__name__}: {e})")


def _draw_name(canvas, card_row):
    name = str(card_row["displayname"] or "").strip().upper()
    if not name:
        return
    d = ImageDraw.Draw(canvas)
    parts = name.split()
    first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", parts[0])
    bold = ci.FONT_NAME if ci.FONT_NAME.is_file() else ci.FONT_DISPLAY_BOLD
    x, max_w = 68, 520
    last_font = _fit(d, last, bold, 92, max_w)
    if first:
        first_font = _fit(d, first, ci.FONT_LABEL, 70, max_w)
        _shadow_text(canvas, _q(x, 150), first, first_font, WHITE)
        _shadow_text(canvas, _q(x, 248), last, last_font, WHITE)
    else:
        _shadow_text(canvas, _q(x, 200), last, last_font, WHITE)


def _draw_panel(canvas, card_row, role, accent):
    st = _static()
    acc = accent + (255,)
    canvas.paste(PANEL_FILL[:3] + (255,), (0, 0), st["panel"].point(lambda v: int(v * PANEL_FILL[3] / 255)))
    d = ImageDraw.Draw(canvas)
    # accent edge along the top of the panel
    top = st["panel_pts"][:6]
    d.line(top, fill=acc, width=5 * S, joint="curve")
    # thin dividers between the three blocks
    lines = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    for x in (300, 737):
        ld.line([_q(x, 1215), _q(x, PANEL_BOTTOM - 40)], fill=(255, 255, 255, 55), width=2 * S)
    canvas.alpha_composite(lines)
    del lines
    d = ImageDraw.Draw(canvas)

    # role word on the tab
    word = ROLE_CARD_WORD.get(ci._col(card_row, "role")) or ROLE_CARD_WORD.get(role, role)
    f = _fit(d, word, ci.FONT_LABEL, 46, TAB_X[1] - TAB_X[0] - 40)
    d.text(_q(518, 1140), word, font=f, fill=WHITE, anchor="mm")

    # batting / bowling (main skill on the left)
    bat, bowl = card_row["bat"], card_row["bowl"]
    is_batter = role in ("BAT", "WK") or (role == "AR" and bat >= bowl)
    left = (bat, "BATTING") if is_batter else (bowl, "BOWLING")
    right = (bowl, "BOWLING") if is_batter else (bat, "BATTING")
    num_f = _font(ci.FONT_DISPLAY_BOLD, 190)
    lab_f = _font(ci.FONT_LABEL, 36)
    for cx, (val, lab) in ((170, left), (867, right)):
        d.text(_q(cx, 1290), str(val), font=num_f, fill=acc, anchor="mm")
        d.text(_q(cx, 1405), lab, font=lab_f, fill=WHITE, anchor="mm")

    # OVR hexagon in the centre
    cx, cy, r = 518, 1285, 92
    d.polygon(_hexagon(cx, cy, r), fill=(14, 20, 42, 255))
    d.line(_hexagon(cx, cy, r) + [_hexagon(cx, cy, r)[0]], fill=acc, width=5 * S, joint="curve")
    d.text(_q(cx, cy - 4), str(card_row["ovr"]), font=_font(ci.FONT_DISPLAY_BOLD, 120), fill=acc, anchor="mm")
    d.text(_q(cx, 1405), "OVR", font=lab_f, fill=WHITE, anchor="mm")

    # flag (left of the hexagon) and first playstyle badge (right)
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _q(372, 1285), 0, 0, 100, box=78 * S)
        else:
            country = str(ci._col(card_row, "country", "") or "").upper()[:10]
            if country:
                d.text(_q(372, 1285), country, font=_fit(d, country, ci.FONT_LABEL, 26, 120),
                       fill=WHITE, anchor="mm")
    except Exception:
        pass
    try:
        badge = ci._load_playstyle_badge(card_row, 1)
        if badge is not None:
            ci._draw_logo(canvas, badge, _q(664, 1285), 0, 0, 100, box=84 * S)
    except Exception:
        pass


def build(card_row, fg_image, role) -> io.BytesIO:
    st = _static()
    accent = _accent(card_row["ovr"])
    canvas = (_card_bg(card_row) or st["bg"]).convert("RGBA")

    # faint ring behind the player (own layer, so its transparency blends)
    ring = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    rcx, rcy, rr = 635, 640, 400
    ImageDraw.Draw(ring).ellipse((_q(rcx - rr, rcy - rr), _q(rcx + rr, rcy + rr)),
                                 outline=(255, 255, 255, 24), width=26 * S)
    canvas.alpha_composite(ring)
    del ring

    _draw_player(canvas, card_row, fg_image)
    canvas.paste((4, 6, 16, 255), (0, 0), st["fade"])
    _draw_name(canvas, card_row)
    _draw_panel(canvas, card_row, role, accent)

    # thin inner border line + accent outer edge
    edge = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    i = INNER_INSET
    ImageDraw.Draw(edge).rounded_rectangle((_q(i, i), _q(OUT_W - i, OUT_H - i)), radius=(RADIUS - 14) * S,
                                           outline=(255, 255, 255, 140), width=3 * S)
    canvas.alpha_composite(edge)
    del edge
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=RADIUS * S, outline=accent + (255,), width=6 * S)

    solid = Image.new("RGB", (W, H), (5, 8, 20))
    solid.paste(canvas.convert("RGB"), (0, 0), st["mask"])
    del canvas
    out = solid.resize((OUT_W, OUT_H), Image.LANCZOS).convert("RGBA")
    out.putalpha(st["mask"].resize((OUT_W, OUT_H), Image.LANCZOS))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
