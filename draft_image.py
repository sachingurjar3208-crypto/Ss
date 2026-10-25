"""Draft screen + draft result images (pure Pillow, no Discord code).

    build_draft_screen(...)  -> JPEG BytesIO   the FC Mobile style "DRAFT" screen
    build_result_image(...)  -> JPEG BytesIO   the cards the user just pulled

Speed notes (1GB VPS): the background + pool tab + card grid of each
(pool, page) is built ONCE and kept in memory; per user only the small
dynamic text (vouchers, "guaranteed in", buttons) is drawn on a copy.
Cards use card_cache's 800px display copies.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

import card_cache

_HERE = Path(__file__).parent
BG_FILE = _HERE / "draft_bg.png"           # 1280x720 blue backdrop
_NUM, _LAB, _NAME = _HERE / "display_bold.ttf", _HERE / "label.ttf", _HERE / "name_font.ttf"

W, H = 1280, 720
FILENAME = "draft.jpg"                      # embeds must use attachment://draft.jpg
CARDS_PER_PAGE = 6                          # 3 x 2 like the FC Mobile screen
MAX_BG_BYTES = 8 * 1024 * 1024              # biggest background upload

WHITE = (245, 247, 255)
DIM = (160, 175, 215)
RED = (255, 90, 100)
POOL_COLORS = {"A": (255, 160, 30), "B": (150, 90, 235), "C": (70, 140, 230)}
POOL_NAMES = {"A": "POOL A", "B": "POOL B", "C": "POOL C"}

# -- Themes: the look of a draft (colours only, the layout is the same) -------
# "default" is the normal blue draft. An event can use another theme; with no
# custom background image the theme draws its own gradient backdrop (top -> bottom).
THEMES = {
    "default": dict(pool=POOL_COLORS, top=(0, 40, 150), bot=(40, 110, 230), glow=(70, 140, 255),
                    title=WHITE, dim=DIM, pill=(0, 25, 100), pill_line=(120, 200, 255),
                    ticket=(120, 200, 255), hole=(0, 30, 110), card=(14, 40, 120), q=(120, 170, 255),
                    guar=(0, 20, 80), gold=(255, 190, 70), dot=(90, 120, 200)),
    "halloween": dict(pool={"A": (255, 140, 38), "B": (176, 120, 255), "C": (110, 200, 90)},
                      top=(14, 6, 22), bot=(58, 22, 34), glow=(255, 120, 20),
                      title=(255, 236, 214), dim=(200, 165, 170), pill=(36, 14, 26), pill_line=(255, 140, 38),
                      ticket=(255, 170, 70), hole=(30, 10, 24), card=(34, 16, 44), q=(255, 170, 90),
                      guar=(30, 12, 26), gold=(255, 170, 70), dot=(140, 90, 120)),
    "diwali": dict(pool={"A": (255, 196, 66), "B": (255, 100, 140), "C": (255, 150, 60)},
                   top=(22, 8, 34), bot=(70, 20, 52), glow=(255, 170, 40),
                   title=(255, 244, 220), dim=(220, 170, 190), pill=(44, 14, 40), pill_line=(255, 196, 66),
                   ticket=(255, 206, 90), hole=(40, 10, 36), card=(52, 20, 54), q=(255, 190, 110),
                   guar=(40, 12, 36), gold=(255, 206, 90), dot=(150, 90, 130)),
    "christmas": dict(pool={"A": (240, 200, 92), "B": (230, 70, 80), "C": (80, 205, 120)},
                      top=(22, 8, 12), bot=(10, 40, 30), glow=(220, 50, 60),
                      title=(255, 246, 232), dim=(190, 200, 190), pill=(14, 34, 26), pill_line=(240, 200, 92),
                      ticket=(240, 210, 110), hole=(10, 30, 22), card=(18, 48, 38), q=(130, 210, 150),
                      guar=(12, 30, 24), gold=(250, 214, 110), dot=(100, 140, 120)),
}


def theme_of(look: dict | None) -> dict:
    return THEMES.get((look or {}).get("theme") or "default", THEMES["default"])


def pool_color(pool: str, theme: str | None = None) -> tuple:
    return THEMES.get(theme or "default", THEMES["default"])["pool"].get(pool, POOL_COLORS["C"])


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _fit_font(path: Path, text: str, max_w: int, start: int, min_size: int = 30):
    size = start
    while size > min_size and ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=_f(path, size)) > max_w:
        size -= 2
    return _f(path, size)


_bg_cache: dict[tuple, Image.Image] = {}


def _gradient_bg(theme: str) -> Image.Image:
    """Theme backdrop used when an event has no custom image (and is not the blue default)."""
    key = ("grad", theme)
    hit = _bg_cache.get(key)
    if hit is not None:
        return hit
    th = THEMES.get(theme, THEMES["default"])
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(th["top"][i] + (th["bot"][i] - th["top"][i]) * t) for i in range(3)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W - 620, -260, W + 160, 420], fill=tuple(int(c * 0.55) for c in th["glow"]))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img = ImageChops.add(img, glow)
    _bg_cache[key] = img
    return img


def _bg(look: dict | None = None) -> Image.Image:
    look = look or {}
    raw = look.get("bg")
    if raw:
        key = ("img", look.get("stamp"), len(raw))
        hit = _bg_cache.get(key)
        if hit is None:
            try:
                hit = ImageOps.fit(Image.open(io.BytesIO(raw)).convert("RGB"), (W, H), method=Image.LANCZOS)
            except Exception as e:                       # a bad background must never break the draft
                print(f"[draft] background could not be loaded: {type(e).__name__}: {e}")
                hit = None
            if hit is not None:
                if len(_bg_cache) >= 12:
                    _bg_cache.pop(next(iter(_bg_cache)))
                _bg_cache[key] = hit
        if hit is not None:
            return hit.copy()
    theme = look.get("theme") or "default"
    if theme != "default":
        return _gradient_bg(theme).copy()
    try:
        return Image.open(BG_FILE).convert("RGB").resize((W, H))
    except Exception:
        img = Image.new("RGB", (W, H), (0, 40, 160))
        d = ImageDraw.Draw(img)
        for y in range(H):
            t = y / H
            d.line([(0, y), (W, y)], fill=(int(0 + 40 * t), int(40 + 70 * t), int(150 + 80 * t)))
        return img


def prepare_background(raw: bytes) -> bytes:
    """Admin upload -> the stored background: cropped to 1280x720 (16:9), saved as JPEG.
    Raises ValueError with a readable message when the file is not a usable image."""
    if len(raw) > MAX_BG_BYTES:
        raise ValueError("That image is too big (max 8 MB).")
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise ValueError("I could not read that file. Please upload a PNG or JPG image.")
    if im.width < 640 or im.height < 360:
        raise ValueError("That image is too small. Use at least 1280 x 720 pixels (16:9) for a sharp background.")
    im = ImageOps.exif_transpose(im).convert("RGB")
    im = ImageOps.fit(im, (W, H), method=Image.LANCZOS, centering=(0.5, 0.5))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _title(look: dict | None) -> str:
    return ((look or {}).get("title") or "DRAFT").upper()


def _load_card(path: str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        return card_cache.load_rgba(path)   # RGBA keeps Style 2's see-through corners
    except Exception:
        return None


def _paste_card(base: Image.Image, card, x: int, y: int, h: int, outline=None) -> int:
    """Paste one card at height h. Returns the width used."""
    img = _load_card(card["image_path"]) if card is not None else None
    d = ImageDraw.Draw(base)
    if img is None:
        w = int(h * 0.684)
        d.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=(20, 30, 70), outline=(90, 110, 170), width=2)
        name = (card["playername"] if card is not None else "?")[:14]
        d.text((x + w // 2, y + h // 2), name, font=_f(_LAB, 26), fill=WHITE, anchor="mm")
        return w
    w = max(1, round(img.width * h / img.height))
    img = img.resize((w, h), Image.LANCZOS)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=12, fill=255)
    mask = ImageChops.multiply(mask, img.getchannel("A"))   # transparent corners stay transparent
    base.paste(img.convert("RGB"), (x, y), mask)
    if outline:
        d.rounded_rectangle([x - 2, y - 2, x + w + 1, y + h + 1], radius=14, outline=outline, width=4)
    return w


def _ticket(d: ImageDraw.ImageDraw, x: int, y: int, h: int, color=(120, 200, 255), hole=(0, 30, 110)):
    """Small ticket icon (images can't show Discord emojis)."""
    w = int(h * 1.5)
    d.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=color)
    r = max(3, h // 6)
    d.ellipse([x - r, y + h // 2 - r, x + r, y + h // 2 + r], fill=hole)
    d.ellipse([x + w - r, y + h // 2 - r, x + w + r, y + h // 2 + r], fill=hole)
    d.line([(x + w // 2, y + 5), (x + w // 2, y + h - 5)], fill=hole, width=2)
    return w


# ── Draft screen ─────────────────────────────────────────────────────────────

_base_cache: dict[tuple, Image.Image] = {}


def pages_for(count: int) -> int:
    return max(1, (count + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)


def _stamp(cards: list) -> tuple:
    out = []
    for c in cards:
        try:
            m = Path(c["image_path"]).stat().st_mtime if c["image_path"] else 0
        except OSError:
            m = 0
        out.append((c["playername_key"], m))
    return tuple(out)


def _look_key(look: dict | None) -> tuple:
    look = look or {}
    return (look.get("theme") or "default", look.get("title") or "", look.get("stamp"), bool(look.get("bg")))


def _base_screen(pool: str, page: int, cards: list, c_range: tuple[int, int], odds: dict,
                 shares: dict | None = None, look: dict | None = None) -> Image.Image:
    shares = shares or {}
    key = (pool, page, _stamp(cards), c_range, odds.get(pool), tuple(sorted(shares.items())), _look_key(look))
    hit = _base_cache.get(key)
    if hit is not None:
        return hit
    th = theme_of(look)
    img = _bg(look)
    d = ImageDraw.Draw(img)
    col = th["pool"][pool]

    d.text((60, 62), _title(look), font=_fit_font(_LAB, _title(look), 880, 64), fill=th["title"], anchor="lm")

    # pool tab
    d.rounded_rectangle([60, 112, 300, 152], radius=6, fill=col)
    d.text((180, 132), POOL_NAMES[pool], font=_f(_LAB, 30), fill=(20, 20, 30), anchor="mm")

    gx, gy, ch, gap = 60, 168, 250, 16
    if pool == "C":
        cw = int(ch * 0.684)
        for i in range(CARDS_PER_PAGE):
            x = gx + (i % 3) * (cw + gap)
            y = gy + (i // 3) * (ch + gap)
            d.rounded_rectangle([x, y, x + cw, y + ch], radius=14, fill=th["card"], outline=col, width=3)
            d.text((x + cw // 2, y + ch // 2), "?", font=_f(_NUM, 110), fill=th["q"], anchor="mm")
    else:
        chunk = cards[page * CARDS_PER_PAGE:(page + 1) * CARDS_PER_PAGE]
        for i, c in enumerate(chunk):
            x = gx + (i % 3) * (int(ch * 0.684) + gap)
            y = gy + (i // 3) * (ch + gap)
            _paste_card(img, c, x, y, ch)
        if not chunk:
            d.text((gx, gy + 40), "No cards added to this pool yet", font=_f(_LAB, 30), fill=th["dim"], anchor="lm")

    # page dots
    total = pages_for(len(cards)) if pool != "C" else 1
    if total > 1:
        cw = int(ch * 0.684)
        cx = gx + (3 * cw + 2 * gap) // 2
        x0 = cx - (total - 1) * 12
        for i in range(total):
            d.ellipse([x0 + i * 24 - 5, 700 - 5, x0 + i * 24 + 5, 700 + 5], fill=WHITE if i == page else th["dot"])

    # right side info
    d.text((1230, 215), POOL_NAMES[pool], font=_f(_LAB, 120), fill=col, anchor="rm")
    if pool == "C":
        sub = f"RANDOM CARD  {c_range[0]}-{c_range[1]} OVR"
    else:
        sub = "FIXED CARDS ONLY"
    d.text((1230, 290), sub, font=_f(_LAB, 38), fill=th["title"], anchor="rm")

    if len(_base_cache) >= 24:
        _base_cache.pop(next(iter(_base_cache)))
    _base_cache[key] = img
    return img


def build_draft_screen(pool: str, page: int, cards: list, vouchers: int, since_a: int, pity_at: int,
                       odds: dict, c_range: tuple[int, int], cost_one: int, cost_multi: int,
                       multi: int, shares: dict | None = None, look: dict | None = None) -> io.BytesIO:
    """look = None for the normal draft, or {"theme", "title", "bg": jpeg bytes|None, "stamp", "left": "5d 3h"}."""
    th = theme_of(look)
    base = _base_screen(pool, page, cards, c_range, odds, shares, look).copy()
    d = ImageDraw.Draw(base)

    # time left (events only), under the title next to the pool tab
    if look and look.get("left"):
        d.text((322, 132), f"ENDS IN {str(look['left']).upper()}", font=_f(_LAB, 28), fill=th["gold"], anchor="lm")

    # voucher count (top right)
    tw = d.textlength(f"{vouchers:,}", font=_f(_NUM, 44))
    d.rounded_rectangle([1230 - tw - 110, 28, 1230, 92], radius=30, fill=th["pill"], outline=th["pill_line"], width=2)
    _ticket(d, int(1230 - tw - 90), 46, 28, th["ticket"], th["hole"])
    d.text((1214, 60), f"{vouchers:,}", font=_f(_NUM, 44), fill=WHITE, anchor="rm")

    # guarantee pill
    left = max(1, pity_at - since_a)
    d.rounded_rectangle([640, 470, 1230, 524], radius=10, fill=th["guar"])
    d.rounded_rectangle([640, 470, 790, 524], radius=10, fill=th["pool"]["A"])
    d.text((715, 497), "POOL A", font=_f(_LAB, 32), fill=(20, 20, 30), anchor="mm")
    d.text((806, 497), "Guaranteed in:", font=_f(_LAB, 32), fill=WHITE, anchor="lm")
    d.text((1214, 497), f"{left} Draft{'s' if left != 1 else ''}", font=_f(_LAB, 36), fill=th["gold"], anchor="rm")

    # buttons
    for x0, label, cost in ((640, "1 DRAFT", cost_one), (940, f"{multi} DRAFTS", cost_multi)):
        ok = vouchers >= cost
        d.rounded_rectangle([x0, 580, x0 + 290, 650], radius=14, fill=(235, 238, 250) if ok else (150, 160, 190))
        d.text((x0 + 22, 615), label, font=_f(_LAB, 36), fill=(20, 30, 70), anchor="lm")
        nf = _f(_NUM, 38)
        ntw = d.textlength(str(cost), font=nf)
        _ticket(d, int(x0 + 272 - ntw - 52), 601, 28, (0, 90, 200), (235, 238, 250) if ok else (150, 160, 190))
        d.text((x0 + 272, 615), str(cost), font=nf, fill=(20, 30, 70) if ok else RED, anchor="rm")

    buf = io.BytesIO()
    base.save(buf, format="JPEG", quality=88)
    buf.seek(0)
    return buf


# ── Result image ─────────────────────────────────────────────────────────────

def build_result_image(items: list[dict], look: dict | None = None) -> io.BytesIO:
    """items: [{"card": row|None, "pool": "A"/"B"/"C", "status": "added"/"pending"/"dupe"}]"""
    th = theme_of(look)
    img = _bg(look)
    d = ImageDraw.Draw(img)
    n = len(items)
    head = f"{_title(look)} RESULT" if look and look.get("title") else "DRAFT RESULT"
    d.text((W // 2, 40), head, font=_fit_font(_LAB, head, 1000, 50, 28), fill=th["title"], anchor="mm")
    a_col = th["pool"]["A"]

    if n == 1:
        ch = 590
        it = items[0]
        w = int(ch * 0.684)
        x, y = (W - w) // 2, 80
        w = _paste_card(img, it["card"], x, y, ch, a_col if it["pool"] == "A" else None)
        _badge(d, x + 12, y + 12, it, th)
    else:
        per_row = 5 if n > 5 else n
        rows = (n + per_row - 1) // per_row
        ch = 300 if rows == 2 else 400
        cw = int(ch * 0.684)
        gap = 18
        total_w = per_row * cw + (per_row - 1) * gap
        x0 = (W - total_w) // 2
        y0 = 90 if rows == 2 else 130
        for i, it in enumerate(items):
            x = x0 + (i % per_row) * (cw + gap)
            y = y0 + (i // per_row) * (ch + gap)
            _paste_card(img, it["card"], x, y, ch, a_col if it["pool"] == "A" else None)
            _badge(d, x + 8, y + 8, it, th)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    buf.seek(0)
    return buf


def _badge(d: ImageDraw.ImageDraw, x: int, y: int, it: dict, th: dict | None = None):
    th = th or THEMES["default"]
    col = th["pool"].get(it["pool"], th["pool"]["C"])
    d.rounded_rectangle([x, y, x + 74, y + 26], radius=6, fill=col)
    d.text((x + 37, y + 13), POOL_NAMES.get(it["pool"], "POOL"), font=_f(_LAB, 20), fill=(20, 20, 30), anchor="mm")
    if it.get("status") == "dupe":
        d.rounded_rectangle([x, y + 32, x + 74, y + 56], radius=6, fill=(40, 40, 60))
        d.text((x + 37, y + 44), "DUPLICATE", font=_f(_LAB, 17), fill=WHITE, anchor="mm")
