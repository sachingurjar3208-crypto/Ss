"""Full Size card design (used by every normal Style 2 card when /cardshape = Full Size).

Entry point: build(card_row, fg_image, role) -> io.BytesIO (PNG, 1037 x 1517).

Layout (no 6-stat grid):
  * name top-left: first name thin, surname big and bold
  * big player cutout in the middle (auto-trimmed, same size for every player)
  * bottom panel (straight top line): role word in a pill on the line, BATTING and BOWLING numbers
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
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

import card_image as ci
from card_narratives import ROLE_CARD_WORD

S = 2                                   # supersampling
OUT_W, OUT_H = ci.CANVAS_W, ci.CANVAS_H  # 1037 x 1517
W, H = OUT_W * S, OUT_H * S

RADIUS = 28                              # outer corner radius (output px)
INNER_INSET = 22                         # white border line distance from the edge
BORDER_W = 6                             # white border line thickness
BORDER_RADIUS = 18                       # white border corner radius
WHITE = (245, 246, 250, 255)

# Player box (output px). Bottom sits just under the top of the bottom panel.
FG_BOX = {"x": 250, "y": 175, "w": 770, "h": 955}

# Bottom panel (output px): straight top edge, rounded bottom corners
PANEL_TOP = 1150
PANEL_BOTTOM = OUT_H - 40
PANEL_SIDE = 38
PANEL_RADIUS = 30

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

    # bottom panel: straight top, rounded bottom corners, vertical gradient
    panel = Image.new("L", (W, H), 0)
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle((_q(PANEL_SIDE, PANEL_TOP), _q(OUT_W - PANEL_SIDE, PANEL_BOTTOM)),
                         radius=PANEL_RADIUS * S, fill=255)
    pd.rectangle((_q(PANEL_SIDE, PANEL_TOP), _q(OUT_W - PANEL_SIDE, PANEL_TOP + PANEL_RADIUS)), fill=255)
    grad = Image.new("RGB", (1, H))
    top_c, bot_c = (18, 27, 58), (5, 8, 20)
    for y in range(H):
        t = min(1.0, max(0.0, (y / S - PANEL_TOP) / (PANEL_BOTTOM - PANEL_TOP)))
        grad.putpixel((0, y), tuple(int(top_c[k] + (bot_c[k] - top_c[k]) * t) for k in range(3)))
    panel_fill = grad.resize((W, H), Image.NEAREST)

    # soft dark fade above the panel so the player's legs melt into it
    fade = Image.new("L", (W, H), 0)
    fd = ImageDraw.Draw(fade)
    y0, y1 = 930, PANEL_TOP
    for y in range(y0, OUT_H):
        a = 200 if y >= y1 else int(200 * ((y - y0) / (y1 - y0)) ** 1.4)
        fd.line([_q(0, y), _q(OUT_W, y)], fill=a, width=S)

    return {"mask": mask, "bg": bg.convert("RGB"), "panel": panel, "panel_fill": panel_fill, "fade": fade}


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


# ── Rush Cricket logo watermark ──────────────────────────────────────────
# A clean logo can be dropped next to this file as rush_logo.png (square, transparent
# corners); otherwise the round logo is cut out of starter_bg.png (same as squad_image.py).
LOGO_FILE = Path(__file__).with_name("rush_logo.png")
LOGO_SRC = Path(__file__).with_name("starter_bg.png")
LOGO_SRC_CIRCLE = (627, 117, 111)          # centre x, centre y, radius in starter_bg scaled to 1254 wide
LOGO_CENTER = (635, 610)                   # output px (behind the player)
LOGO_SIZE = 660                            # diameter, output px
LOGO_OPACITY = 0.22                        # 0 = invisible, 1 = solid
_LOGO: list = []


def _logo_layer():
    """(RGBA image, paste position) at working size, built once. None if no logo file."""
    if _LOGO:
        return _LOGO[0]
    out = None
    try:
        if LOGO_FILE.exists():
            lg = Image.open(LOGO_FILE).convert("RGBA")
        else:
            src = Image.open(LOGO_SRC).convert("RGB")
            k = src.width / 1254
            cx, cy, r = (v * k for v in LOGO_SRC_CIRCLE)
            r *= 0.97                                  # stay inside the logo's own edge
            lg = src.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r))).convert("RGBA")
            m = Image.new("L", (lg.width * 4, lg.height * 4), 0)
            ImageDraw.Draw(m).ellipse((0, 0, m.width - 1, m.height - 1), fill=255)
            lg.putalpha(m.resize(lg.size, Image.LANCZOS))
        size = LOGO_SIZE * S
        lg = lg.resize((size, size), Image.LANCZOS).filter(ImageFilter.GaussianBlur(S * 0.8))
        lg.putalpha(lg.getchannel("A").point(lambda v: int(v * LOGO_OPACITY)))
        out = (lg, (int(LOGO_CENTER[0] * S - size / 2), int(LOGO_CENTER[1] * S - size / 2)))
    except Exception as e:
        print(f"[card_full] logo unavailable ({type(e).__name__}: {e})")
    _LOGO.append(out)
    return out


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


def _spaced(draw, xy, text, font, fill, spacing):
    """Centre-anchored text with extra letter spacing (output px)."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * S * (len(text) - 1)
    x = xy[0] - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, xy[1]), ch, font=font, fill=fill, anchor="lm")
        x += w + spacing * S


def _draw_panel(canvas, card_row, role, accent):
    st = _static()
    acc = accent + (255,)
    dim = accent + (150,)

    # panel body (90% opaque, so a custom background still shows a little)
    canvas.paste(st["panel_fill"], (0, 0), st["panel"].point(lambda v: int(v * 0.93)))

    fx = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fx)
    y = PANEL_TOP
    # neon glow above the straight top line
    fd.rectangle((_q(PANEL_SIDE + 20, y - 6), _q(OUT_W - PANEL_SIDE - 20, y + 2)), fill=accent + (170,))
    fx = fx.filter(ImageFilter.GaussianBlur(14 * S))
    fd = ImageDraw.Draw(fx)
    # dividers (fade in from the top)
    for x in (305, 732):
        for k in range(0, 200, 2):
            a = int(60 * min(1.0, k / 60) * min(1.0, (200 - k) / 60))
            fd.line([_q(x, 1215 + k), _q(x, 1217 + k)], fill=(255, 255, 255, a), width=2 * S)
    canvas.alpha_composite(fx)
    del fx

    d = ImageDraw.Draw(canvas)
    # straight accent line + thin light line under it
    d.line([_q(PANEL_SIDE, y), _q(OUT_W - PANEL_SIDE, y)], fill=acc, width=5 * S)
    d.line([_q(PANEL_SIDE + 30, y + 9), _q(OUT_W - PANEL_SIDE - 30, y + 9)], fill=(255, 255, 255, 40), width=1 * S)

    # role word in an accent pill sitting on the line
    word = ROLE_CARD_WORD.get(ci._col(card_row, "role")) or ROLE_CARD_WORD.get(role, role)
    pf = _fit(d, word, ci.FONT_LABEL, 34, 330)
    tw = d.textlength(word, font=pf) / S + 3 * (len(word) - 1)
    pw, ph = tw + 64, 54
    d.rounded_rectangle((_q(518 - pw / 2, y - ph / 2), _q(518 + pw / 2, y + ph / 2)),
                        radius=int(ph / 2 * S), fill=acc)
    _spaced(d, _q(518, y + 1), word, pf, (8, 11, 24, 255), 3)

    # batting / bowling (main skill on the left)
    bat, bowl = card_row["bat"], card_row["bowl"]
    is_batter = role in ("BAT", "WK") or (role == "AR" and bat >= bowl)
    left = (bat, "BATTING") if is_batter else (bowl, "BOWLING")
    right = (bowl, "BOWLING") if is_batter else (bat, "BATTING")
    num_f = _font(ci.FONT_DISPLAY_BOLD, 175)
    lab_f = _font(ci.FONT_LABEL, 30)
    for cx, (val, lab) in ((172, left), (865, right)):
        _shadow_text(canvas, _q(cx, 1300), str(val), num_f, acc, anchor="mm", blur=5, alpha=200)
        d = ImageDraw.Draw(canvas)
        _spaced(d, _q(cx, 1410), lab, lab_f, (225, 230, 240, 255), 4)

    # OVR: double-ring hexagon in the centre
    cx, cy, r = 518, 1300, 96
    hexa = _hexagon(cx, cy, r)
    d.polygon(hexa, fill=(12, 18, 40, 255))
    d.line(hexa + [hexa[0]], fill=acc, width=6 * S, joint="curve")
    inner = _hexagon(cx, cy, r - 14)
    d.line(inner + [inner[0]], fill=dim, width=2 * S, joint="curve")
    _shadow_text(canvas, _q(cx, cy - 2), str(card_row["ovr"]), _font(ci.FONT_DISPLAY_BOLD, 118), WHITE,
                 anchor="mm", blur=4, alpha=200)
    d = ImageDraw.Draw(canvas)
    _spaced(d, _q(cx, 1410), "OVR", lab_f, (225, 230, 240, 255), 4)

    # flag (left of the hexagon) and first playstyle badge (right)
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, _q(372, 1300), 0, 0, 100, box=76 * S)
        else:
            country = str(ci._col(card_row, "country", "") or "").upper()[:10]
            if country:
                d.text(_q(372, 1300), country, font=_fit(d, country, ci.FONT_LABEL, 24, 110),
                       fill=WHITE, anchor="mm")
    except Exception:
        pass
    try:
        badge = ci._load_playstyle_badge(card_row, 1)
        if badge is not None:
            ci._draw_logo(canvas, badge, _q(664, 1300), 0, 0, 100, box=82 * S)
    except Exception:
        pass


def build(card_row, fg_image, role) -> io.BytesIO:
    st = _static()
    accent = _accent(card_row["ovr"])
    canvas = (_card_bg(card_row) or st["bg"]).convert("RGBA")

    # Rush Cricket logo, faint, in the middle behind the player
    logo = _logo_layer()
    if logo is not None:
        canvas.alpha_composite(logo[0], logo[1])

    _draw_player(canvas, card_row, fg_image)
    canvas.paste((4, 6, 16, 255), (0, 0), st["fade"])
    _draw_name(canvas, card_row)
    _draw_panel(canvas, card_row, role, accent)

    # thin inner border line + accent outer edge
    # clean white border line (inset), dark navy outside it
    d = ImageDraw.Draw(canvas)
    i = INNER_INSET
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=RADIUS * S, outline=(6, 10, 24, 255), width=i * S)
    d.rounded_rectangle((_q(i, i), _q(OUT_W - i, OUT_H - i)), radius=BORDER_RADIUS * S,
                        outline=(246, 248, 252, 255), width=BORDER_W * S)

    solid = Image.new("RGB", (W, H), (5, 8, 20))
    solid.paste(canvas.convert("RGB"), (0, 0), st["mask"])
    del canvas
    out = solid.resize((OUT_W, OUT_H), Image.LANCZOS).convert("RGBA")
    out.putalpha(st["mask"].resize((OUT_W, OUT_H), Image.LANCZOS))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
