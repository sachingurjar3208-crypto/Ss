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

from PIL import Image, ImageDraw, ImageFont

import card_cache

_HERE = Path(__file__).parent
BG_FILE = _HERE / "draft_bg.png"           # 1280x720 blue backdrop
_NUM, _LAB, _NAME = _HERE / "display_bold.ttf", _HERE / "label.ttf", _HERE / "name_font.ttf"

W, H = 1280, 720
FILENAME = "draft.jpg"                      # embeds must use attachment://draft.jpg
CARDS_PER_PAGE = 6                          # 3 x 2 like the FC Mobile screen

WHITE = (245, 247, 255)
DIM = (160, 175, 215)
RED = (255, 90, 100)
POOL_COLORS = {"A": (255, 160, 30), "B": (150, 90, 235), "C": (70, 140, 230)}
POOL_NAMES = {"A": "POOL A", "B": "POOL B", "C": "POOL C"}


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _bg() -> Image.Image:
    try:
        return Image.open(BG_FILE).convert("RGB").resize((W, H))
    except Exception:
        img = Image.new("RGB", (W, H), (0, 40, 160))
        d = ImageDraw.Draw(img)
        for y in range(H):
            t = y / H
            d.line([(0, y), (W, y)], fill=(int(0 + 40 * t), int(40 + 70 * t), int(150 + 80 * t)))
        return img


def _load_card(path: str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        data = card_cache._get_sync(path)
        if data:
            return Image.open(io.BytesIO(data)).convert("RGB")
        return Image.open(path).convert("RGB")
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
    base.paste(img, (x, y), mask)
    if outline:
        d.rounded_rectangle([x - 2, y - 2, x + w + 1, y + h + 1], radius=14, outline=outline, width=4)
    return w


def _ticket(d: ImageDraw.ImageDraw, x: int, y: int, h: int, color=(120, 200, 255)):
    """Small ticket icon (images can't show Discord emojis)."""
    w = int(h * 1.5)
    d.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=color)
    r = max(3, h // 6)
    d.ellipse([x - r, y + h // 2 - r, x + r, y + h // 2 + r], fill=(0, 30, 110))
    d.ellipse([x + w - r, y + h // 2 - r, x + w + r, y + h // 2 + r], fill=(0, 30, 110))
    d.line([(x + w // 2, y + 5), (x + w // 2, y + h - 5)], fill=(0, 30, 110), width=2)
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


def _base_screen(pool: str, page: int, cards: list, c_range: tuple[int, int], odds: dict) -> Image.Image:
    key = (pool, page, _stamp(cards), c_range, odds.get(pool))
    hit = _base_cache.get(key)
    if hit is not None:
        return hit
    img = _bg()
    d = ImageDraw.Draw(img)
    col = POOL_COLORS[pool]

    d.text((60, 62), "DRAFT", font=_f(_LAB, 64), fill=WHITE, anchor="lm")

    # pool tab
    d.rounded_rectangle([60, 112, 300, 152], radius=6, fill=col)
    d.text((180, 132), POOL_NAMES[pool], font=_f(_LAB, 30), fill=(20, 20, 30), anchor="mm")

    gx, gy, ch, gap = 60, 168, 250, 16
    if pool == "C":
        cw = int(ch * 0.684)
        for i in range(CARDS_PER_PAGE):
            x = gx + (i % 3) * (cw + gap)
            y = gy + (i // 3) * (ch + gap)
            d.rounded_rectangle([x, y, x + cw, y + ch], radius=14, fill=(14, 40, 120), outline=col, width=3)
            d.text((x + cw // 2, y + ch // 2), "?", font=_f(_NUM, 110), fill=(120, 170, 255), anchor="mm")
    else:
        chunk = cards[page * CARDS_PER_PAGE:(page + 1) * CARDS_PER_PAGE]
        for i, c in enumerate(chunk):
            x = gx + (i % 3) * (int(ch * 0.684) + gap)
            y = gy + (i // 3) * (ch + gap)
            _paste_card(img, c, x, y, ch)
        if not chunk:
            d.text((gx, gy + 40), "No cards added to this pool yet", font=_f(_LAB, 30), fill=DIM, anchor="lm")

    # page dots
    total = pages_for(len(cards)) if pool != "C" else 1
    if total > 1:
        cw = int(ch * 0.684)
        cx = gx + (3 * cw + 2 * gap) // 2
        x0 = cx - (total - 1) * 12
        for i in range(total):
            d.ellipse([x0 + i * 24 - 5, 700 - 5, x0 + i * 24 + 5, 700 + 5], fill=WHITE if i == page else (90, 120, 200))

    # right side info
    d.text((1230, 215), POOL_NAMES[pool], font=_f(_LAB, 120), fill=col, anchor="rm")
    if pool == "C":
        sub = f"RANDOM CARD  {c_range[0]}-{c_range[1]} OVR"
    else:
        sub = "FIXED CARDS ONLY"
    d.text((1230, 290), sub, font=_f(_LAB, 38), fill=WHITE, anchor="rm")
    d.text((1230, 340), f"CHANCE  {odds.get(pool, 0)}%  PER DRAFT", font=_f(_LAB, 32), fill=DIM, anchor="rm")

    if len(_base_cache) >= 24:
        _base_cache.pop(next(iter(_base_cache)))
    _base_cache[key] = img
    return img


def build_draft_screen(pool: str, page: int, cards: list, vouchers: int, since_a: int, pity_at: int,
                       odds: dict, c_range: tuple[int, int], cost_one: int, cost_multi: int,
                       multi: int) -> io.BytesIO:
    base = _base_screen(pool, page, cards, c_range, odds).copy()
    d = ImageDraw.Draw(base)

    # voucher count (top right)
    tw = d.textlength(f"{vouchers:,}", font=_f(_NUM, 44))
    d.rounded_rectangle([1230 - tw - 110, 28, 1230, 92], radius=30, fill=(0, 25, 100), outline=(120, 200, 255), width=2)
    _ticket(d, int(1230 - tw - 90), 46, 28)
    d.text((1214, 60), f"{vouchers:,}", font=_f(_NUM, 44), fill=WHITE, anchor="rm")

    # guarantee pill
    left = max(1, pity_at - since_a)
    d.rounded_rectangle([640, 470, 1230, 524], radius=10, fill=(0, 20, 80))
    d.rounded_rectangle([640, 470, 790, 524], radius=10, fill=POOL_COLORS["A"])
    d.text((715, 497), "POOL A", font=_f(_LAB, 32), fill=(20, 20, 30), anchor="mm")
    d.text((806, 497), "Guaranteed in:", font=_f(_LAB, 32), fill=WHITE, anchor="lm")
    d.text((1214, 497), f"{left} Draft{'s' if left != 1 else ''}", font=_f(_LAB, 36), fill=(255, 190, 70), anchor="rm")

    # buttons
    for x0, label, cost in ((640, "1 DRAFT", cost_one), (940, f"{multi} DRAFTS", cost_multi)):
        ok = vouchers >= cost
        d.rounded_rectangle([x0, 580, x0 + 290, 650], radius=14, fill=(235, 238, 250) if ok else (150, 160, 190))
        d.text((x0 + 22, 615), label, font=_f(_LAB, 36), fill=(20, 30, 70), anchor="lm")
        nf = _f(_NUM, 38)
        ntw = d.textlength(str(cost), font=nf)
        _ticket(d, int(x0 + 272 - ntw - 52), 601, 28, (0, 90, 200))
        d.text((x0 + 272, 615), str(cost), font=nf, fill=(20, 30, 70) if ok else RED, anchor="rm")

    buf = io.BytesIO()
    base.save(buf, format="JPEG", quality=88)
    buf.seek(0)
    return buf


# ── Result image ─────────────────────────────────────────────────────────────

def build_result_image(items: list[dict]) -> io.BytesIO:
    """items: [{"card": row|None, "pool": "A"/"B"/"C", "status": "added"/"pending"/"dupe"}]"""
    img = _bg()
    d = ImageDraw.Draw(img)
    n = len(items)
    d.text((W // 2, 40), "DRAFT RESULT", font=_f(_LAB, 50), fill=WHITE, anchor="mm")

    if n == 1:
        ch = 590
        it = items[0]
        w = int(ch * 0.684)
        x, y = (W - w) // 2, 80
        w = _paste_card(img, it["card"], x, y, ch, POOL_COLORS["A"] if it["pool"] == "A" else None)
        _badge(d, x + 12, y + 12, it)
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
            _paste_card(img, it["card"], x, y, ch, POOL_COLORS["A"] if it["pool"] == "A" else None)
            _badge(d, x + 8, y + 8, it)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    buf.seek(0)
    return buf


def _badge(d: ImageDraw.ImageDraw, x: int, y: int, it: dict):
    col = POOL_COLORS.get(it["pool"], POOL_COLORS["C"])
    d.rounded_rectangle([x, y, x + 74, y + 26], radius=6, fill=col)
    d.text((x + 37, y + 13), POOL_NAMES.get(it["pool"], "POOL"), font=_f(_LAB, 20), fill=(20, 20, 30), anchor="mm")
    if it.get("status") == "dupe":
        d.rounded_rectangle([x, y + 32, x + 74, y + 56], radius=6, fill=(40, 40, 60))
        d.text((x + 37, y + 44), "DUPLICATE", font=_f(_LAB, 17), fill=WHITE, anchor="mm")
