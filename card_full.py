"""Full Size card design (used by every normal Style 2 card when /cardshape = Full Size).

Entry point: build(card_row, fg_image, role) -> io.BytesIO (PNG, 1037 x 1517).

Layout (no 6-stat grid):
  * name top-left: first name thin, surname big and bold
  * big player cutout in the middle (auto-trimmed, same size for every player)
  * bottom panel (straight top line): role word in a pill on the line, BATTING and BOWLING numbers
    left / right (main skill on the left), OVR inside a hexagon in the centre
    with the flag and the first playstyle badge on either side
  * accent colour follows the OVR: bronze < 70, silver 70-79, gold 80-89, ice 90+

Background: the card's own Style 2 background (s2_bg via /bgadder) if it has one (shield frame
images are skipped), otherwise a built-in glow in the card's theme colours (navy for normal cards).
Theme cards (Diwali, Halloween, EOAE, Rangoli, Prestige, Crest, Arena, Showcase) get their own
colours and a small tag top-right.
"""

from __future__ import annotations

import io
import math
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
FG_BOX = {"x": 133, "y": 175, "w": 770, "h": 955}   # centred on the card (518)
# Player is moved sideways so its HEAD sits exactly on the card's centre line (a bat or a
# stretched arm doesn't pull it off-centre). False = centre the whole cutout instead.
CENTER_ON_HEAD = True
CARD_CX = OUT_W / 2
# Same size for every player: the cutout is scaled so the HEAD is this wide (output px),
# whatever the photo is (head-and-shoulders, half body, full body). False = old box fit.
AUTO_HEAD_SIZE = True
HEAD_W = 265                     # head width on the card (output px); bigger = bigger players
HEAD_TOP_MIN = 150               # the head never goes higher than this (keeps clear of the top edge)
HEAD_TOP_MAX = 420               # ...and never lower: a head-and-shoulders photo is enlarged up to here
HEAD_SCALE_LIMITS = (0.45, 1.8)  # how much the head-size rule may shrink / grow the box-fit size

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


# Theme cards (s2_theme) in Full Size: own glow colours, accent colour and a small tag top-right.
# bg = (glow centre colour, edge colour), panel = (panel top colour, panel bottom colour).
THEMES = {
    # bg = (glow centre, edge) when the card has no background image,
    # panel = (panel top, panel bottom) of the stats panel, accent = numbers / line / hexagon.
    None:              {"bg": ((28, 44, 92), (5, 8, 20)),   "panel": ((22, 34, 72), (5, 8, 20)),    "accent": None,            "tag": None},
    "icon":            {"bg": ((78, 58, 18), (7, 6, 3)),    "panel": ((92, 70, 22), (16, 12, 4)),   "accent": (250, 210, 105), "tag": "ICON"},
    "bronze":          {"bg": ((92, 52, 28), (14, 8, 5)),   "panel": ((84, 46, 24), (16, 9, 5)),    "accent": (222, 146, 88),  "tag": None},
    "season1":         {"bg": ((22, 122, 86), (3, 16, 14)), "panel": ((14, 84, 56), (3, 16, 12)),   "accent": (120, 235, 160), "tag": None},
    "diwali":          {"bg": ((122, 44, 18), (28, 6, 10)), "panel": ((110, 36, 16), (26, 6, 8)),   "accent": (255, 196, 70),  "tag": "DIWALI"},
    "rangoli":         {"bg": ((132, 30, 72), (26, 5, 18)), "panel": ((104, 22, 60), (22, 4, 14)),  "accent": (255, 186, 60),  "tag": "RANGOLI"},
    "prestige_diwali": {"bg": ((110, 70, 24), (24, 12, 6)), "panel": ((96, 60, 20), (20, 11, 5)),   "accent": (255, 214, 120), "tag": "PRESTIGE DIWALI"},
    "halloween":       {"bg": ((74, 26, 96), (12, 4, 20)),  "panel": ((72, 22, 98), (14, 4, 22)),   "accent": (255, 146, 28),  "tag": "HALLOWEEN"},
    "eoae":            {"bg": ((64, 56, 42), (10, 9, 8)),   "panel": ((96, 50, 18), (18, 10, 6)),   "accent": (255, 184, 92),  "tag": "END OF AN ERA"},
    "prestige":        {"bg": ((74, 76, 88), (12, 12, 16)), "panel": ((62, 64, 76), (12, 12, 16)),  "accent": (236, 226, 200), "tag": "PRESTIGE"},
    "crest":           {"bg": ((18, 70, 70), (3, 14, 16)),  "panel": ((14, 70, 72), (3, 14, 16)),   "accent": (90, 224, 204),  "tag": "CREST"},
    "arena":           {"bg": ((96, 22, 22), (14, 4, 6)),   "panel": ((96, 18, 20), (16, 4, 6)),    "accent": (255, 96, 74),   "tag": "ARENA"},
    "showcase":        {"bg": ((28, 44, 92), (5, 8, 20)),   "panel": ((22, 34, 72), (5, 8, 20)),    "accent": None,            "tag": "SHOWCASE"},
}

# Cards with no theme get a look from their EVENT name first, then their background's name
# (background or s2_bg): a name containing the word on the left uses the theme on the right.
BG_NAME_THEMES = [("icon", "icon"), ("bronze", "bronze"), ("season", "season1"), ("s1", "season1"),
                  ("diwali", "diwali"), ("halloween", "halloween"), ("end of an era", "eoae"), ("eoae", "eoae")]


def _event(card_row) -> dict | None:
    """The card's event row (eventname, bg ...) or None."""
    key = ci._col(card_row, "event_key")
    if not key:
        return None
    try:
        from card_db import get_event
        return get_event(key)
    except Exception:
        return None


def _theme(card_row) -> dict:
    """Theme colours. Event cards show the EVENT NAME as their tag (Diwali 2026, Icon ...)."""
    key = str(ci._col(card_row, "s2_theme", "") or "").strip().lower() or None
    ev = _event(card_row)
    ev_tag = str(ev["eventname"]).upper() if ev else None
    if key and key in THEMES:
        th = THEMES[key]
    else:
        th = THEMES[None]
        sources = [str(ev["eventname"]).lower() if ev else "",
                   " ".join(str(ci._col(card_row, c, "") or "") for c in ("background", "s2_bg")).lower()]
        found = next((t for src in sources for w, t in BG_NAME_THEMES if src and w in src), None)
        if found:
            th = THEMES[found]
    if ev_tag:
        th = dict(th, tag=ev_tag)
    return th


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

    # bottom panel: straight top, rounded bottom corners, vertical gradient
    panel = Image.new("L", (W, H), 0)
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle((_q(PANEL_SIDE, PANEL_TOP), _q(OUT_W - PANEL_SIDE, PANEL_BOTTOM)),
                         radius=PANEL_RADIUS * S, fill=255)
    pd.rectangle((_q(PANEL_SIDE, PANEL_TOP), _q(OUT_W - PANEL_SIDE, PANEL_TOP + PANEL_RADIUS)), fill=255)

    # soft dark fade above the panel so the player's legs melt into it
    fade = Image.new("L", (W, H), 0)
    fd = ImageDraw.Draw(fade)
    y0, y1 = 930, PANEL_TOP
    for y in range(y0, OUT_H):
        a = 200 if y >= y1 else int(200 * ((y - y0) / (y1 - y0)) ** 1.4)
        fd.line([_q(0, y), _q(OUT_W, y)], fill=a, width=S)

    return {"mask": mask, "panel": panel, "fade": fade}


def _static() -> dict:
    with _LOCK:
        if not _STATIC:
            _STATIC.update(_build_static())
        return _STATIC


_THEMED: dict = {}


def _themed(th: dict) -> dict:
    """Background glow + panel gradient for one theme's colours, built once."""
    key = (th["bg"], th["panel"])
    with _LOCK:
        hit = _THEMED.get(key)
        if hit is not None:
            return hit
        small = Image.new("RGB", (OUT_W // 4, OUT_H // 4))
        cx, cy = small.width * 0.55, small.height * 0.38
        maxd = math.hypot(small.width, small.height) * 0.62
        inner, outer = th["bg"]
        px = small.load()
        for y in range(small.height):
            for x in range(small.width):
                t = min(1.0, math.hypot(x - cx, y - cy) / maxd) ** 1.15
                px[x, y] = tuple(int(inner[i] + (outer[i] - inner[i]) * t) for i in range(3))
        bg = small.resize((W, H), Image.BICUBIC)
        grad = Image.new("RGB", (1, H))
        top_c, bot_c = th["panel"]
        for y in range(H):
            t = min(1.0, max(0.0, (y / S - PANEL_TOP) / (PANEL_BOTTOM - PANEL_TOP)))
            grad.putpixel((0, y), tuple(int(top_c[k] + (bot_c[k] - top_c[k]) * t) for k in range(3)))
        hit = {"bg": bg, "panel_fill": grad.resize((W, H), Image.NEAREST), "hex": top_c + (255,)}
        if len(_THEMED) >= 4:
            _THEMED.pop(next(iter(_THEMED)))
        _THEMED[key] = hit
        return hit


def _card_bg(card_row) -> Image.Image | None:
    """Event background (/eventbg) first, then the card's own Style 2 background."""
    ev = _event(card_row)
    for name in ((ev or {}).get("bg"), ci._col(card_row, "s2_bg")):
        if name:
            img = _bg_image(name)
            if img is not None:
                return img
    return None


def _bg_image(name) -> Image.Image | None:
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
            src = Image.open(path)
            if src.mode in ("RGBA", "LA", "P") and src.convert("RGBA").getpixel((0, 0))[3] < 128:
                return None            # a shield frame (Diwali / Halloween), not a background
            hit = ImageOps.fit(src.convert("RGB"), (W, H), Image.LANCZOS)
            if len(_BG_CACHE) >= 3:
                _BG_CACHE.pop(next(iter(_BG_CACHE)))
            _BG_CACHE[key] = hit
        return hit
    except Exception:
        return None


# ── Rush Cricket logo watermark ──────────────────────────────────────────
# A clean logo can be dropped next to this file as rush_logo.png (square, transparent
# corners); otherwise the round logo is cut out of starter_bg.png (same as squad_image.py).
SHOW_LOGO = False                          # True = faint Rush Cricket logo behind the player
LOGO_FILE = Path(__file__).with_name("rush_logo.png")
LOGO_SRC = Path(__file__).with_name("starter_bg.png")
LOGO_SRC_CIRCLE = (627, 117, 111)          # centre x, centre y, radius in starter_bg scaled to 1254 wide
# Fixed spot, same on every card: centred, ~38% down, diameter ~86% of the width inside the white border.
LOGO_CENTER = (518, 585)                   # output px
# False = logo always at LOGO_CENTER (same place on every card).
# True  = logo follows the player (centred on the head, LOGO_FOLLOW_Y down the cutout).
LOGO_FOLLOW_PLAYER = False
LOGO_FOLLOW_Y = 0.36
LOGO_SIZE = 830                            # diameter, output px
LOGO_OPACITY = 0.22                        # 0 = invisible, 1 = solid
_LOGO: list = []


def _logo_layer():
    """RGBA logo at working size, built once. None if no logo file."""
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
        out = lg
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
        # /foregroundfix in Full Size saves full_dx / full_dy / full_scale (own values, not the shield ones)
        fdx = int(ci._col(card_row, "full_dx", 0) or 0)
        fdy = int(ci._col(card_row, "full_dy", 0) or 0)
        fsc = int(ci._col(card_row, "full_scale", 100) or 100)
        resized, pos = ci._fit_foreground(
            fg, box,
            int(ci._col(card_row, "offset_x", 0)) * S if manual else 0,
            int(ci._col(card_row, "offset_y", 0)) * S if manual else 0,
            int(ci._col(card_row, "scale_pct", 100)) * fsc // 100 if manual else fsc,
        )
        if AUTO_HEAD_SIZE and not manual:
            hw = _head_width(resized)
            if hw:
                k = max(HEAD_SCALE_LIMITS[0], min(HEAD_SCALE_LIMITS[1], HEAD_W * S * fsc / 100 / hw))
                if abs(k - 1) > 0.02:
                    resized = resized.resize((max(1, int(resized.width * k)), max(1, int(resized.height * k))),
                                             Image.LANCZOS)
                # short photo (head and shoulders): grow it until the head reaches HEAD_TOP_MAX
                bottom = box["y"] + box["h"]
                if bottom - resized.height > HEAD_TOP_MAX * S:
                    k2 = (bottom - HEAD_TOP_MAX * S) / resized.height
                    resized = resized.resize((max(1, int(resized.width * k2)), max(1, int(resized.height * k2))),
                                             Image.LANCZOS)
                # bottom stays on the panel; a big player slides down behind the panel
                # instead of poking out of the top of the card
                y = bottom - resized.height
                y = max(y, HEAD_TOP_MIN * S)
                pos = (box["x"] + (box["w"] - resized.width) // 2, y)
        if CENTER_ON_HEAD and not manual:
            hx = _head_center_x(resized)
            if hx is not None:
                pos = (int(CARD_CX * S - hx), pos[1])
        pos = (pos[0] + fdx * S, pos[1] + fdy * S)
        _draw_logo_behind(canvas, resized, pos)
        # soft glow behind the player
        glow = Image.new("L", canvas.size, 0)
        glow.paste(resized.getchannel("A"), pos)
        glow = glow.resize((W // 8, H // 8)).filter(ImageFilter.GaussianBlur(6)).resize((W, H))
        canvas.paste((255, 255, 255, 255), (0, 0), glow.point(lambda v: int(v * 0.16)))
        canvas.alpha_composite(resized, pos)
    except Exception as e:
        print(f"[card_full] player skipped ({type(e).__name__}: {e})")


def _head_width(player):
    """Width of the head in the cutout (px). Rows are read from the top; each row's WIDEST solid
    run counts (bats / sticks are separate runs). The head ends about 1.1 head-widths below its top,
    so reading stops there, before the shoulders."""
    try:
        k = max(1, player.width // 300)
        a = player.getchannel("A").resize((max(1, player.width // k), max(1, player.height // k)))
        w, h = a.size
        px = a.load()
        top = next((y for y in range(h) if any(px[x, y] >= 40 for x in range(w))), None)
        if top is None:
            return None
        hw = 0
        for y in range(top, h):
            if hw and y - top > 1.1 * hw:
                break
            best, start = 0, None
            for x in range(w + 1):
                solid = x < w and px[x, y] >= 40
                if solid and start is None:
                    start = x
                elif not solid and start is not None:
                    best = max(best, x - start)
                    start = None
            hw = max(hw, best)
        return hw * k if hw > 4 else None
    except Exception:
        return None


def _head_center_x(player):
    """x of the head's centre inside the cutout: on each row of the top part, the WIDEST solid
    run is taken (a thin bat or stick is ignored), and the middle of those rows is used."""
    try:
        k = max(1, player.width // 240)                       # work small, it's only a measurement
        a = player.getchannel("A").resize((max(1, player.width // k), max(1, player.height // k)))
        w, h = a.size
        px = a.load()
        top = next((y for y in range(h) if any(px[x, y] >= 40 for x in range(w))), None)
        if top is None:
            return None
        centres = []
        for y in range(top + int(h * 0.03), min(h, top + int(h * 0.16))):
            best, run_start, best_c = 0, None, None
            for x in range(w + 1):
                solid = x < w and px[x, y] >= 40
                if solid and run_start is None:
                    run_start = x
                elif not solid and run_start is not None:
                    if x - run_start > best:
                        best, best_c = x - run_start, (run_start + x) / 2
                    run_start = None
            if best_c is not None:
                centres.append(best_c)
        if not centres:
            return None
        centres.sort()
        return centres[len(centres) // 2] * k
    except Exception:
        return None


def _draw_logo_behind(canvas, player, pos):
    """Faint Rush Cricket logo behind the player: fixed in the middle (default) or following the player."""
    if not SHOW_LOGO:
        return
    logo = _logo_layer()
    if logo is None:
        return
    size = logo.width
    cx, cy = LOGO_CENTER[0] * S, LOGO_CENTER[1] * S
    if LOGO_FOLLOW_PLAYER:
        try:
            a = player.getchannel("A").point(lambda v: 255 if v >= 40 else 0)
            head = a.crop((0, 0, a.width, max(1, int(a.height * 0.20))))   # arms / bats don't pull it sideways
            bb = head.getbbox() or a.getbbox()
            if bb:
                cx = pos[0] + (bb[0] + bb[2]) / 2
                cy = min(pos[1] + player.height * LOGO_FOLLOW_Y, PANEL_TOP * S - size / 2 + 90 * S)
        except Exception:
            pass
    canvas.alpha_composite(logo, (int(cx - size / 2), int(cy - size / 2)))


def _draw_name(canvas, card_row):
    name = str(card_row["displayname"] or "").strip().upper()
    if not name:
        return
    d = ImageDraw.Draw(canvas)
    parts = name.split()
    first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", parts[0])
    bold = ci.FONT_NAME if ci.FONT_NAME.is_file() else ci.FONT_DISPLAY_BOLD
    x, max_w = 68, 430          # stays left of the (centred) head; long names shrink
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


def _draw_panel(canvas, card_row, role, accent, themed):
    st = _static()
    acc = accent + (255,)
    dim = accent + (150,)

    # panel body (90% opaque, so a custom background still shows a little)
    canvas.paste(themed["panel_fill"], (0, 0), st["panel"].point(lambda v: int(v * 0.93)))

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
    d.polygon(hexa, fill=themed["hex"])
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
            country = str(ci._col(card_row, "country", "") or "").upper().strip()
            if country:
                words = country.split()
                # long names go on two lines (PAPUA NEW / GUINEA) instead of being cut off
                lines = [country] if len(words) == 1 or len(country) <= 10 else \
                    [" ".join(words[:(len(words) + 1) // 2]), " ".join(words[(len(words) + 1) // 2:])]
                f = min((_fit(d, ln, ci.FONT_LABEL, 24, 110) for ln in lines), key=lambda x: x.size)
                step = f.size / S + 4
                y0 = 1300 - step * (len(lines) - 1) / 2
                for k, ln in enumerate(lines):
                    d.text(_q(372, y0 + k * step), ln, font=f, fill=WHITE, anchor="mm")
    except Exception:
        pass
    try:
        badge = ci._load_playstyle_badge(card_row, 1)
        if badge is not None:
            ci._draw_logo(canvas, badge, _q(664, 1300), 0, 0, 100, box=82 * S)
    except Exception:
        pass


def _draw_tag(canvas, text, accent):
    """Small event tag top-right (DIWALI, END OF AN ERA ...)."""
    d = ImageDraw.Draw(canvas)
    f = _fit(d, text, ci.FONT_LABEL, 26, 330)
    tw = d.textlength(text, font=f) / S + 2.5 * (len(text) - 1)
    x1, y = OUT_W - 60, 92
    d.rounded_rectangle((_q(x1 - tw - 36, y - 22), _q(x1, y + 22)), radius=22 * S,
                        outline=accent + (255,), width=3 * S, fill=(0, 0, 0, 255))
    _spaced(d, _q(x1 - tw / 2 - 18, y + 1), text, f, accent + (255,), 2.5)


def build(card_row, fg_image, role) -> io.BytesIO:
    st = _static()
    th = _theme(card_row)
    themed = _themed(th)
    accent = th["accent"] or _accent(card_row["ovr"])
    canvas = (_card_bg(card_row) or themed["bg"]).convert("RGBA")

    _draw_player(canvas, card_row, fg_image)
    canvas.paste((4, 6, 16, 255), (0, 0), st["fade"])
    _draw_name(canvas, card_row)
    if th["tag"]:
        _draw_tag(canvas, th["tag"], accent)
    _draw_panel(canvas, card_row, role, accent, themed)

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
