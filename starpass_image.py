"""Star Pass image for `csstarpass` (pure Pillow, no Discord code).

    build_starpass_image(board, page) -> PNG BytesIO

`board` is the dict from starpass.get_board(). Layout (like the FIFA Mobile Star Pass):
header (season, credits, level + progress bar, time left), then the PREMIUM row on top,
the level numbers in the middle and the FREE row at the bottom, one page of levels at a time.
No emoji: every icon (packs, coins, voucher, lock, tick) is drawn with shapes, and player
cards use the real card image. Runs in a worker thread (asyncio.to_thread), never in the loop.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import starpass

_HERE = Path(__file__).parent
_NUM, _LAB, _NAME = _HERE / "display_bold.ttf", _HERE / "label.ttf", _HERE / "name_font.ttf"

FILENAME = "starpass.png"                 # embeds must use attachment://starpass.png

W, H = 1400, 930
PAD = 30
X0, GAP, MAX_CW = 170, 12, 190
PY, PH = 250, 262
NY, NH = PY + PH + 10, 64
FY = NY + NH + 10

BG_TOP, BG_BOT = (10, 14, 28), (20, 27, 50)
WHITE, DIM = (240, 244, 255), (135, 150, 190)
TEAL, GOLD = (47, 191, 155), (244, 196, 82)
CARD, LINE = (24, 31, 52), (42, 53, 84)
LOCK_COL = (120, 132, 165)

# ── Themes: the look of a pass (colors only, the layout is the same) ─────────
# gold = Premium row accent, teal = Free row accent. "default" is the normal Star Pass look.
THEMES = {
    "default": dict(bg_top=BG_TOP, bg_bot=BG_BOT, glow1=(110, 70, 230), glow2=(47, 191, 155),
                    gold=GOLD, teal=TEAL, prem_line=(150, 118, 44), free_line=(52, 100, 120),
                    prem_bg=(44, 36, 20), free_bg=(18, 44, 50), pill_bg=(70, 52, 18), star_hi=(255, 226, 130)),
    "halloween": dict(bg_top=(16, 8, 24), bg_bot=(34, 14, 30), glow1=(255, 120, 20), glow2=(130, 60, 220),
                      gold=(255, 140, 38), teal=(170, 112, 255), prem_line=(170, 92, 28), free_line=(92, 62, 150),
                      prem_bg=(52, 28, 14), free_bg=(32, 20, 52), pill_bg=(72, 38, 12), star_hi=(255, 190, 110)),
    "diwali": dict(bg_top=(22, 10, 30), bg_bot=(44, 16, 40), glow1=(255, 170, 40), glow2=(255, 70, 130),
                   gold=(255, 196, 66), teal=(255, 100, 140), prem_line=(170, 128, 40), free_line=(150, 56, 90),
                   prem_bg=(52, 38, 14), free_bg=(48, 20, 38), pill_bg=(74, 52, 12), star_hi=(255, 226, 130)),
    "christmas": dict(bg_top=(22, 8, 12), bg_bot=(10, 28, 22), glow1=(220, 50, 60), glow2=(60, 190, 110),
                      gold=(240, 200, 92), teal=(80, 205, 120), prem_line=(150, 120, 50), free_line=(48, 112, 78),
                      prem_bg=(46, 36, 20), free_bg=(16, 44, 34), pill_bg=(70, 52, 18), star_hi=(255, 236, 170)),
}

K = 4                                      # icons are drawn 4x and scaled down (smooth edges)

PACKS = {
    "legendary": ((190, 70, 235), (88, 26, 140), (255, 224, 130), "LEGEND"),
    "gold":      ((246, 200, 70), (168, 116, 20), (255, 246, 200), "GOLD"),
    "silver":    ((206, 214, 230), (112, 124, 150), (255, 255, 255), "SILVER"),
    "bronze":    ((214, 142, 86), (120, 64, 28), (255, 226, 190), "BRONZE"),
}


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


# ── Small drawing helpers ────────────────────────────────────────────────────

def _grad(size, c1, c2):
    w, h = size
    g = Image.new("RGB", size)
    d = ImageDraw.Draw(g)
    for i in range(h):
        t = i / max(1, h - 1)
        d.line([(0, i), (w, i)], fill=tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)))
    return g


def _star(cx, cy, R, r, n=5, rot=-90):
    pts = []
    for i in range(n * 2):
        a = math.radians(rot + i * 180 / n)
        rad = R if i % 2 == 0 else r
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def _paste_grad(layer, bbox, c1, c2, mask_fn):
    x0, y0, x1, y1 = [int(v) for v in bbox]
    m = Image.new("L", layer.size, 0)
    mask_fn(ImageDraw.Draw(m))
    full = Image.new("RGB", layer.size)
    full.paste(_grad((max(1, x1 - x0), max(1, y1 - y0)), c1, c2), (x0, y0))
    layer.paste(full, (0, 0), m)


def _darker(c, n):
    return tuple(max(0, v - n) for v in c)


def _fit_font(path: Path, text: str, max_w: int, start: int, min_size: int = 11):
    size = start
    while size > min_size:
        f = _f(path, size)
        if ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=f) <= max_w:
            return f
        size -= 1
    return _f(path, min_size)


def _shorten(text: str, path: Path, size: int, max_w: int) -> str:
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    f = _f(path, size)
    if d.textlength(text, font=f) <= max_w:
        return text
    while len(text) > 3 and d.textlength(text + "..", font=f) > max_w:
        text = text[:-1]
    return text.rstrip() + ".."


# ── Icons ────────────────────────────────────────────────────────────────────

def _icon_pack(size, kind):
    c1, c2, trim, label = PACKS[kind]
    S = size * K
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pw, ph = S * 0.56, S * 0.84
    x0, y0 = (S - pw) / 2, (S - ph) / 2
    x1, y1 = x0 + pw, y0 + ph
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((x0, y1 - S * 0.05, x1, y1 + S * 0.05), fill=(0, 0, 0, 110))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(S * 0.02)))
    body = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _paste_grad(body, (x0, y0, x1, y1), c1, c2, lambda m: m.rectangle((x0, y0, x1, y1), fill=255))
    im.alpha_composite(body)
    d = ImageDraw.Draw(im)
    t, n = S * 0.045, 14
    step = pw / n
    for yy, dirn in ((y0, 1), (y1, -1)):
        for i in range(n):
            xa = x0 + i * step
            d.polygon([(xa, yy), (xa + step, yy), (xa + step / 2, yy + dirn * t * 2)], fill=_darker(c2, 35))
    d.rectangle((x0, y0 + t * 2, x1, y0 + t * 2 + S * 0.012), fill=trim)
    d.rectangle((x0, y1 - t * 2 - S * 0.012, x1, y1 - t * 2), fill=trim)
    cx, cy = S / 2, S * 0.48
    d.ellipse((cx - S * 0.2, cy - S * 0.2, cx + S * 0.2, cy + S * 0.2), fill=_darker(c1, 45),
              outline=trim, width=int(S * 0.012))
    d.polygon(_star(cx, cy, S * 0.145, S * 0.06), fill=trim)
    d.text((cx, y1 - S * 0.17), label, font=_f(_NUM, int(S * 0.075)), fill=trim, anchor="mm")
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).polygon(
        [(x0 + pw * 0.12, y0), (x0 + pw * 0.34, y0), (x0 + pw * 0.06, y1), (x0 - pw * 0.16, y1)],
        fill=(255, 255, 255, 40))
    clip = Image.new("L", (S, S), 0)
    ImageDraw.Draw(clip).rectangle((x0, y0, x1, y1), fill=255)
    sheen.putalpha(Image.composite(sheen.split()[3], Image.new("L", (S, S), 0), clip))
    im.alpha_composite(sheen)
    return im.resize((size, size), Image.LANCZOS)


def _coin(d, cx, cy, r):
    d.ellipse((cx - r, cy - r + r * 0.12, cx + r, cy + r + r * 0.12), fill=(150, 100, 20))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(250, 205, 80), outline=(176, 120, 30),
              width=max(1, int(r * 0.07)))
    d.ellipse((cx - r * 0.78, cy - r * 0.78, cx + r * 0.78, cy + r * 0.78), fill=(255, 226, 125),
              outline=(214, 158, 42), width=max(1, int(r * 0.05)))
    d.polygon(_star(cx, cy, r * 0.5, r * 0.21), fill=(214, 158, 42))


def _icon_coins(size):
    S = size * K
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((S * 0.14, S * 0.78, S * 0.86, S * 0.92), fill=(0, 0, 0, 90))
    _coin(d, S * 0.34, S * 0.60, S * 0.21)
    _coin(d, S * 0.66, S * 0.62, S * 0.21)
    _coin(d, S * 0.5, S * 0.42, S * 0.25)
    return im.resize((size, size), Image.LANCZOS)


def _icon_voucher(size):
    S = size * K
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    x0, x1, y0, y1 = S * 0.06, S * 0.94, S * 0.28, S * 0.74
    nr = S * 0.07

    def md(m):
        m.rounded_rectangle((x0, y0, x1, y1), radius=S * 0.04, fill=255)
        for x in (x0, x1):
            m.ellipse((x - nr, (y0 + y1) / 2 - nr, x + nr, (y0 + y1) / 2 + nr), fill=0)

    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((S * 0.12, y1 - S * 0.02, S * 0.88, y1 + S * 0.07), fill=(0, 0, 0, 100))
    im.alpha_composite(sh)
    t = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _paste_grad(t, (x0, y0, x1, y1), (122, 92, 240), (70, 52, 170), md)
    im.alpha_composite(t)
    d = ImageDraw.Draw(im)
    xs = S * 0.66
    for yy in range(int(y0 + S * 0.04), int(y1 - S * 0.02), int(S * 0.05)):
        d.line((xs, yy, xs, yy + S * 0.028), fill=(220, 210, 255), width=int(S * 0.012))
    d.rectangle((x0 + S * 0.05, y0 + S * 0.04, x1 - S * 0.05, y0 + S * 0.05), fill=(190, 176, 255))
    d.rectangle((x0 + S * 0.05, y1 - S * 0.05, x1 - S * 0.05, y1 - S * 0.04), fill=(190, 176, 255))
    d.text((S * 0.36, (y0 + y1) / 2 - S * 0.025), "VOUCHER", font=_f(_NUM, int(S * 0.095)), fill=WHITE, anchor="mm")
    d.text((S * 0.36, (y0 + y1) / 2 + S * 0.075), "FREE PACK", font=_f(_LAB, int(S * 0.06)),
           fill=(206, 194, 255), anchor="mm")
    d.polygon(_star(S * 0.80, (y0 + y1) / 2, S * 0.085, S * 0.036), fill=GOLD)
    return im.resize((size, size), Image.LANCZOS)


def _icon_player(size, card):
    """The real card image if it exists, otherwise a drawn placeholder with the player's name."""
    path = card["image_path"] if card is not None and "image_path" in card.keys() else None
    if path:
        try:
            import card_cache
            src = card_cache.load_rgba(path)
        except Exception:
            src = None
        if src is not None:
            h = size
            w = max(1, round(src.width * h / src.height))
            thumb = src.resize((w, h), Image.LANCZOS)
            im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            sh = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(sh).ellipse(((size - w) / 2, size - 8, (size + w) / 2, size + 4), fill=(0, 0, 0, 110))
            im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(3)))
            im.alpha_composite(thumb, ((size - w) // 2, 0))
            return im
    S = size * K
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pw, ph = S * 0.62, S * 0.88
    x0, y0 = (S - pw) / 2, (S - ph) / 2
    x1, y1 = x0 + pw, y0 + ph
    rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _paste_grad(rim, (x0, y0, x1, y1), (255, 226, 130), (176, 120, 30),
                lambda m: m.rounded_rectangle((x0, y0, x1, y1), radius=S * 0.06, fill=255))
    im.alpha_composite(rim)
    b = S * 0.016
    inner = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _paste_grad(inner, (x0 + b, y0 + b, x1 - b, y1 - b), (40, 64, 140), (14, 22, 60),
                lambda m: m.rounded_rectangle((x0 + b, y0 + b, x1 - b, y1 - b), radius=S * 0.05, fill=255))
    im.alpha_composite(inner)
    cx = (x0 + x1) / 2
    d = ImageDraw.Draw(im)
    d.ellipse((cx - S * 0.085, y0 + S * 0.22, cx + S * 0.085, y0 + S * 0.39), fill=(120, 150, 225, 255))
    d.ellipse((cx - S * 0.2, y0 + S * 0.40, cx + S * 0.2, y0 + S * 0.80), fill=(96, 126, 205, 255))
    name = (card["playername"] if card is not None else "PLAYER").upper()
    d.rectangle((x0 + b, y1 - S * 0.17, x1 - b, y1 - b), fill=(14, 22, 60))
    d.text((cx, y1 - S * 0.09), _shorten(name, _NAME, int(S * 0.06), int(pw * 0.9)),
           font=_f(_NAME, int(S * 0.06)), fill=WHITE, anchor="mm")
    return im.resize((size, size), Image.LANCZOS)


_icon_cache: dict = {}


def _make_icon(kind: str, size: int, card=None):
    key = (kind, size, card["playername_key"] if card is not None else None)
    hit = _icon_cache.get(key)
    if hit is not None and kind != "player":
        return hit
    if kind in PACKS:
        out = _icon_pack(size, kind)
    elif kind == "coins":
        out = _icon_coins(size)
    elif kind == "voucher":
        out = _icon_voucher(size)
    else:
        out = _icon_player(size, card)
    if kind != "player" and len(_icon_cache) < 64:
        _icon_cache[key] = out
    return out


# ── The image ────────────────────────────────────────────────────────────────

def _spaced(d, xy, text, font, fill, sp=4, anchor="l"):
    ws = [d.textlength(c, font=font) for c in text]
    total = sum(ws) + sp * max(0, len(text) - 1)
    x, y = xy
    if anchor == "r":
        x -= total
    elif anchor == "m":
        x -= total / 2
    for c, w in zip(text, ws):
        d.text((x, y), c, font=font, fill=fill)
        x += w + sp
    return total


def _number(d, x, y, value: int, font, fill) -> int:
    """Draw 12,345. The display font has no comma, so it is drawn as a small shape."""
    text = f"{int(value):,}"
    cx = x
    for ch in text:
        if ch == ",":
            d.ellipse((cx + 2, y + 36, cx + 8, y + 42), fill=fill)
            d.polygon([(cx + 5, y + 40), (cx + 9, y + 40), (cx + 4, y + 50), (cx + 1, y + 50)], fill=fill)
            cx += 13
        else:
            d.text((cx, y), ch, font=font, fill=fill)
            cx += d.textlength(ch, font=font)
    return int(cx - x)


def _lock(d, cx, cy, s, col, hole):
    d.rounded_rectangle((cx - s, cy - s * 0.25, cx + s, cy + s * 0.95), radius=s * 0.2, fill=col)
    d.arc((cx - s * 0.65, cy - s * 1.15, cx + s * 0.65, cy + s * 0.55), 180, 360, fill=col, width=max(3, int(s * 0.22)))
    d.ellipse((cx - s * 0.15, cy + s * 0.2, cx + s * 0.15, cy + s * 0.5), fill=hole)


def _tick(d, cx, cy, s, col, w=4):
    d.line([(cx - s, cy), (cx - s * 0.3, cy + s * 0.7), (cx + s, cy - s * 0.7)], fill=col, width=w, joint="curve")


def prepare_background(raw: bytes) -> bytes:
    """Admin upload -> the stored background: cropped to the Star Pass size, saved as JPEG.
    Raises ValueError with a readable message when the file is not a usable image."""
    if len(raw) > starpass.MAX_BG_BYTES:
        raise ValueError("That image is too big (max 8 MB).")
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise ValueError("I could not read that file. Please upload a PNG or JPG image.")
    if im.width < 600 or im.height < 300:
        raise ValueError("That image is too small. Use at least 1400 x 930 pixels for a sharp background.")
    im = ImageOps.exif_transpose(im).convert("RGB")
    im = ImageOps.fit(im, (W, H), method=Image.LANCZOS, centering=(0.5, 0.5))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _custom_background(pass_key: str):
    """The saved background as an RGBA image of size (W, H), or None."""
    try:
        raw = starpass.get_background(pass_key)
        if not raw:
            return None
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        return ImageOps.fit(im, (W, H), method=Image.LANCZOS).convert("RGBA")
    except Exception as e:                      # a bad background must never break the Star Pass
        print(f"[starpass] background could not be loaded: {type(e).__name__}: {e}")
        return None


def _time_left(season: dict) -> tuple[str, str]:
    left = int(season["seconds_left"])
    if left >= starpass.DAY:
        n = int(season["days_left"])
        return str(n), "DAY" if n == 1 else "DAYS"
    hours = max(1, -(-left // 3600))
    return str(hours), "HOUR" if hours == 1 else "HOURS"


def build_starpass_image(board: dict, page: int = 0) -> io.BytesIO:
    import card_db

    season = board["season"]
    th = THEMES.get(season.get("theme") or "default", THEMES["default"])
    BG_TOP, BG_BOT, GOLD, TEAL = th["bg_top"], th["bg_bot"], th["gold"], th["teal"]   # this pass' colors
    main_pass = season.get("pass_key", starpass.DEFAULT_PASS) == starpass.DEFAULT_PASS
    pages = starpass.page_ranges(season["levels"])
    page = max(0, min(int(page), len(pages) - 1))
    first, last = pages[page]
    shown = [e for e in board["levels"] if first <= e["level"] <= last]
    n = len(shown)
    premium = board["premium"]
    unlocked = board["unlocked"]

    cw = min(MAX_CW, (1200 - GAP * (n - 1)) // n)
    row_w = n * cw + (n - 1) * GAP

    # one query for every player card on this page
    keys = [e[r]["reward"].player_key for e in shown for r in starpass.ROWS
            if e[r]["reward"].kind == "player" and e[r]["reward"].player_key]
    cards = card_db.get_cards(keys) if keys else {}

    # background
    img = Image.new("RGB", (W, H), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))
    bg = _custom_background(season.get("pass_key", starpass.DEFAULT_PASS))
    if bg is not None:                          # own background: darken it so text and cards stay readable
        img = Image.alpha_composite(bg, Image.new("RGBA", (W, H), (6, 8, 18, 115)))
    else:
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((-200, -250, 520, 300), fill=th["glow1"] + (60,))
        gd.ellipse((900, -300, 1600, 260), fill=th["glow2"] + (45,))
        img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(90)))
    d = ImageDraw.Draw(img)

    # header (a little see-through on a custom background)
    if bg is not None:
        hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(hl).rounded_rectangle((PAD, 26, W - PAD, 216), radius=22, fill=CARD + (215,))
        img = Image.alpha_composite(img, hl)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((PAD, 26, W - PAD, 216), radius=22, outline=LINE, width=2)
    else:
        d.rounded_rectangle((PAD, 26, W - PAD, 216), radius=22, fill=CARD, outline=LINE, width=2)
    d.polygon(_star(82, 84, 34, 14), fill=GOLD)
    d.polygon(_star(82, 84, 26, 10), fill=th["star_hi"])
    if main_pass:
        _spaced(d, (135, 38), "STAR PASS", _f(_NUM, 62), WHITE, 5)
        d.text((137, 112), season["name"].upper(), font=_f(_LAB, 24), fill=DIM)
    else:                                       # event pass: its own name is the title
        title = season["name"].upper()
        tf = _fit_font(_NUM, title, 280 - 5 * max(0, len(title) - 1), 62, 26)
        _spaced(d, (135, 38 + (62 - getattr(tf, "size", 62)) // 2), title, tf, WHITE, 5)
        d.text((137, 112), "STAR PASS EVENT", font=_f(_LAB, 24), fill=DIM)
    if premium:
        d.rounded_rectangle((137, 150, 300, 188), radius=19, fill=th["pill_bg"], outline=GOLD, width=2)
        _spaced(d, (218, 158), "PREMIUM", _f(_NUM, 22), GOLD, 3, "m")
    else:
        d.rounded_rectangle((137, 150, 300, 188), radius=19, fill=(30, 40, 64), outline=(70, 84, 120), width=2)
        _spaced(d, (218, 158), "FREE PASS", _f(_NUM, 22), DIM, 3, "m")

    _spaced(d, (470, 52), "CREDITS", _f(_LAB, 19), DIM, 3)
    _number(d, 470, 92, board["credits"], _f(_NUM, 46), WHITE)
    _spaced(d, (700, 52), "LEVEL", _f(_LAB, 19), DIM, 3)
    lvl_font = _f(_NUM, 46)
    d.text((700, 92), str(unlocked), font=lvl_font, fill=WHITE)
    d.text((700 + d.textlength(str(unlocked), font=lvl_font) + 14, 112), f"of {season['levels']}",
           font=_f(_LAB, 24), fill=DIM)

    prog = board["progress"]
    bx0, bx1, by = 470, 1000, 178
    _spaced(d, (470, 150), "MAX LEVEL" if prog["maxed"] else "NEXT LEVEL", _f(_LAB, 17), DIM, 3)
    d.rounded_rectangle((bx0, by - 9, bx1, by + 9), radius=9, fill=(36, 46, 76))
    frac = 1.0 if prog["maxed"] else (prog["into"] / prog["need"] if prog["need"] else 1.0)
    d.rounded_rectangle((bx0, by - 9, bx0 + max(18, int((bx1 - bx0) * frac)), by + 9), radius=9, fill=TEAL)
    if not prog["maxed"]:
        d.text((bx1, 150), f"{prog['into']} / {prog['need']}", font=_f(_LAB, 20), fill=WHITE, anchor="ra")

    d.line((1060, 52, 1060, 190), fill=LINE, width=2)
    num, unit = _time_left(season)
    _spaced(d, (1100, 52), "ENDS IN", _f(_LAB, 19), DIM, 3)
    nf = _f(_NUM, 46)
    d.text((1100, 92), num, font=nf, fill=WHITE)
    d.text((1100 + d.textlength(num, font=nf) + 14, 112), unit, font=_f(_LAB, 24), fill=DIM)
    _spaced(d, (1100, 150), "PAGE", _f(_LAB, 17), DIM, 3)
    d.text((1170, 146), f"{page + 1} / {len(pages)}", font=_f(_LAB, 24), fill=WHITE)

    # reward cells
    def cell(x, y, entry, row):
        nonlocal img, d
        rw = entry[row]["reward"]
        st = entry[row]["state"]
        prem_row = row == starpass.PREMIUM
        base_line = th["prem_line"] if prem_row else th["free_line"]
        base_fill = (34, 30, 38) if prem_row else (22, 34, 52)
        accent = GOLD if prem_row else TEAL
        ready = st == "ready"
        if ready:
            g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(g).rounded_rectangle((x - 2, y - 2, x + cw + 2, y + PH + 2), radius=20, fill=accent + (150,))
            img = Image.alpha_composite(img, g.filter(ImageFilter.GaussianBlur(14)))
            d = ImageDraw.Draw(img)
        d.rounded_rectangle((x, y, x + cw, y + PH), radius=18, fill=base_fill,
                            outline=accent if ready else base_line, width=3 if ready else 2)
        d.rounded_rectangle((x + 3, y + 3, x + cw - 3, y + 46), radius=15, fill=tuple(min(255, v + 10) for v in base_fill))

        isz = min(170, cw - 18)
        card = cards.get((rw.player_key or "").lower()) if rw.kind == "player" else None
        ic = _make_icon(rw.kind, isz, card)
        if st != "ready":
            locked = st in ("locked", "premium_locked")
            is_card = rw.kind == "player"        # keep player cards clearly visible, just a bit dimmer
            dim = (0.75 if locked else 0.6) if is_card else (0.4 if locked else 0.55)
            a = ic.split()[3].point(lambda v: int(v * dim))
            if locked and not is_card:
                ic = ic.convert("LA").convert("RGBA")
            else:
                ic = ic.copy()
            ic.putalpha(a)
        img.paste(ic, (x + (cw - isz) // 2, y + 52), ic)
        d = ImageDraw.Draw(img)

        name = card["playername"] if card is not None else None
        label = rw.label(name).upper()
        col = WHITE if ready else (DIM if st == "claimed" else (95, 108, 140))
        font = _fit_font(_LAB, label, cw - 16, 19)
        d.text((x + cw / 2, y + PH - 26), label, font=font, fill=col, anchor="mm")

        tag = {"ready": "READY", "claimed": "CLAIMED", "locked": "LOCKED", "premium_locked": "PREMIUM ONLY"}[st]
        tc = {"ready": accent, "claimed": (110, 200, 150), "locked": (95, 108, 140), "premium_locked": th["prem_line"]}[st]
        _spaced(d, (x + cw / 2, y + 12), tag, _f(_LAB, 16 if cw >= 170 else 14), tc, 3, "m")
        if st in ("locked", "premium_locked"):
            _lock(d, x + cw - 26, y + 80, 11, LOCK_COL, base_fill)
        elif st == "claimed":
            d.ellipse((x + cw - 46, y + 56, x + cw - 12, y + 90), fill=(30, 92, 70))
            _tick(d, x + cw - 29, y + 73, 8, (200, 255, 225))

    for i, entry in enumerate(shown):
        x = X0 + i * (cw + GAP)
        cell(x, PY, entry, starpass.PREMIUM)
        cell(x, FY, entry, starpass.FREE)

    # level strip
    d.rounded_rectangle((X0 - 6, NY + 6, X0 + row_w + 6, NY + NH - 6), radius=26, fill=(18, 24, 42), outline=LINE, width=2)
    for i, entry in enumerate(shown):
        x = X0 + i * (cw + GAP)
        cx, cy = x + cw // 2, NY + NH // 2
        open_ = entry["level"] <= unlocked
        if i < n - 1:
            nxt_open = shown[i + 1]["level"] <= unlocked
            d.line((cx + 26, cy, x + cw + GAP + cw // 2 - 26, cy), fill=TEAL if nxt_open else (50, 62, 95), width=5)
        d.ellipse((cx - 24, cy - 24, cx + 24, cy + 24), fill=TEAL if open_ else (38, 48, 76),
                  outline=(150, 235, 210) if open_ else (70, 84, 120), width=3)
        d.text((cx, cy + 1), str(entry["level"]), font=_f(_NUM, 28 if entry["level"] < 100 else 22),
               fill=(8, 30, 28) if open_ else DIM, anchor="mm")

    # side labels (rotated text)
    def side(y, text, col, bg):
        d.rounded_rectangle((PAD, y, X0 - 14, y + PH), radius=16, fill=bg, outline=col, width=2)
        f = _f(_NUM, 26)
        tmp = Image.new("RGBA", (PH, 70), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        ws = [td.textlength(c, font=f) for c in text]
        tot = int(sum(ws) + 5 * (len(text) - 1))
        xx = (PH - tot) / 2
        for c, w in zip(text, ws):
            td.text((xx, 35), c, font=f, fill=col, anchor="lm")
            xx += w + 5
        rot = tmp.rotate(90, expand=True)
        img.paste(rot, (PAD + (X0 - 14 - PAD - 70) // 2, y), rot)

    side(PY, "PREMIUM", GOLD, th["prem_bg"])
    side(FY, "FREE", TEAL, th["free_bg"])
    d = ImageDraw.Draw(img)

    fy = FY + PH + 16
    d.text((PAD, fy + 10), f"Every finished daily quest gives {starpass.STARPASS_QUEST_CREDITS} credits.",
           font=_f(_LAB, 20), fill=DIM)
    d.text((W - PAD, fy + 10), f"Levels {first} - {last}", font=_f(_LAB, 22), fill=WHITE, anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=False)
    buf.seek(0)
    return buf
