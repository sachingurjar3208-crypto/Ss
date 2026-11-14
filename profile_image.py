"""rcprofile image: team name, avatar, ranked tier, match record, top 3 cards, favourite,
collection and coins. 1080 x 1350, chamfered frame like the Full Size cards.

Entry point: build_profile_image(data) -> io.BytesIO (PNG). `data` keys:
    team, manager, avatar (bytes or None), tier, stars, matches, wins,
    cards (list of image paths, best first, up to 3), favourite, owned, total, coins
"""
from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import card_image as ci

FILENAME = "profile.png"
W, H = 1080, 1350
WHITE, DIM = (245, 246, 250, 255), (170, 182, 210, 255)
CYAN = (110, 215, 255)
TIER_COLORS = {
    "Bronze": (214, 138, 80), "Silver": (205, 212, 224), "Gold": (246, 204, 96),
    "Platinum": (110, 215, 255), "Legendary": (255, 120, 60), "Ruby": (255, 70, 110),
}
CARD_RATIO = 1517 / 1037
_BG: list = []


def _font(path, size):
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def _fit(d, text, path, size, max_w):
    f = _font(path, size)
    while size > 18 and d.textlength(text, font=f) > max_w:
        size -= 2
        f = _font(path, size)
    return f


def _spaced(d, xy, text, f, fill, sp, anchor="l"):
    ws = [d.textlength(c, font=f) for c in text]
    total = sum(ws) + sp * (len(text) - 1)
    x = xy[0] - (total / 2 if anchor == "m" else total if anchor == "r" else 0)
    for c, w in zip(text, ws):
        d.text((x, xy[1]), c, font=f, fill=fill, anchor="lm")
        x += w + sp


def fmt_short(n: int) -> str:
    n = int(n or 0)
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(n) >= div:
            v = n / div
            return (f"{v:.1f}".rstrip("0").rstrip(".")) + suf
    return str(n)


def _background() -> Image.Image:
    if _BG:
        return _BG[0].copy()
    small = Image.new("RGB", (W // 4, H // 4))
    px = small.load()
    for y in range(small.height):
        for x in range(small.width):
            t = min(1, math.hypot(x - small.width * 0.5, y - small.height * 0.3) / (small.width * 0.95)) ** 1.1
            px[x, y] = (int(30 - 25 * t), int(46 - 38 * t), int(98 - 78 * t))
    img = small.resize((W, H), Image.BICUBIC).convert("RGBA")
    fx = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fx)
    for off, a in ((0, 150), (30, 70)):
        fd.polygon([(W + 40 - off, 260 + off), (W - 260 - off, -40 + off), (W - 272 - off, -40 + off),
                    (W + 40 - off, 272 + off)], fill=CYAN + (a,))
    img.alpha_composite(fx)
    _BG.append(img)
    return img.copy()


def _avatar(data: bytes | None, letter: str, size: int) -> Image.Image:
    av = None
    if data:
        try:
            av = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            av = None
    if av is None:
        av = Image.new("RGBA", (size, size))
        ad = ImageDraw.Draw(av)
        for y in range(size):
            ad.line([(0, y), (size, y)], fill=(int(70 + 80 * y / size), int(40 + 20 * y / size), int(150 - 40 * y / size), 255))
        ad.text((size / 2, size / 2), (letter or "?")[:1].upper(), font=_font(ci.FONT_DISPLAY_BOLD, size // 2), fill=WHITE, anchor="mm")
    m = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size * 3 - 1, size * 3 - 1), fill=255)
    av.putalpha(m.resize((size, size), Image.LANCZOS))
    return av


def _card(path, w) -> Image.Image | None:
    try:
        if path and Path(path).is_file():
            return Image.open(path).convert("RGBA").resize((w, int(w * CARD_RATIO)), Image.LANCZOS)
    except Exception:
        pass
    return None


def build_profile_image(data: dict) -> io.BytesIO:
    img = _background()
    d = ImageDraw.Draw(img)
    tier = data.get("tier") or "Bronze"
    tcol = TIER_COLORS.get(tier, (246, 204, 96))

    # brand
    try:
        import card_full
        lg = card_full._brand_logo()
    except Exception:
        lg = None
    if lg is not None:
        d.ellipse((46, 36, 124, 114), fill=WHITE)
        img.alpha_composite(lg.resize((70, 70), Image.LANCZOS), (50, 40))
    d.text((136, 62), "RUSH", font=_font(ci.FONT_DISPLAY_BOLD, 34), fill=WHITE, anchor="lm")
    d.text((136, 94), "CRICKET", font=_font(ci.FONT_LABEL, 22), fill=DIM, anchor="lm")
    _spaced(d, (W - 300, 76), "PLAYER PROFILE", _font(ci.FONT_LABEL, 24), CYAN + (255,), 4, anchor="r")

    # avatar + team + manager + tier
    av, ax, ay = 170, 60, 160
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((ax - 10, ay - 10, ax + av + 10, ay + av + 10), fill=tcol + (200,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(img)
    d.ellipse((ax - 6, ay - 6, ax + av + 6, ay + av + 6), fill=tcol + (255,))
    img.alpha_composite(_avatar(data.get("avatar"), data.get("manager", ""), av), (ax, ay))
    d = ImageDraw.Draw(img)
    tx = ax + av + 40
    name_font = ci.FONT_NAME if Path(ci.FONT_NAME).is_file() else ci.FONT_DISPLAY_BOLD
    team = str(data.get("team") or "My Team").upper()
    d.text((tx, ay + 30), team, font=_fit(d, team, name_font, 62, W - tx - 60), fill=WHITE, anchor="lm")
    mgr = f"Manager: {data.get('manager') or ''}"
    d.text((tx, ay + 88), mgr, font=_fit(d, mgr, ci.FONT_LABEL, 30, W - tx - 60), fill=DIM, anchor="lm")
    stars = int(data.get("stars") or 0)
    pill = f"{tier.upper()}  ·  {stars} STAR{'S' if stars != 1 else ''}"
    pf = _font(ci.FONT_LABEL, 28)
    pw = d.textlength(pill, font=pf) + 50
    d.rounded_rectangle((tx, ay + 120, tx + pw, ay + 170), radius=25,
                        fill=tuple(int(c * 0.18) for c in tcol) + (255,), outline=tcol + (255,), width=3)
    d.text((tx + pw / 2, ay + 146), pill, font=pf, fill=tcol + (255,), anchor="mm")

    # record tiles
    matches, wins = int(data.get("matches") or 0), int(data.get("wins") or 0)
    losses = max(0, matches - wins)
    pct = f"{round(wins * 100 / matches)}%" if matches else "0%"
    stats = [("MATCHES", str(matches)), ("WINS", str(wins)), ("LOSSES", str(losses)), ("WIN %", pct)]
    tw, th, gap = 225, 130, 20
    sx, sy = (W - (4 * tw + 3 * gap)) // 2, 400
    for i, (lab, val) in enumerate(stats):
        x = sx + i * (tw + gap)
        d.rounded_rectangle((x, sy, x + tw, sy + th), radius=18, fill=(10, 16, 36, 230), outline=(255, 255, 255, 40), width=2)
        col = tcol + (255,) if lab == "WIN %" else WHITE
        d.text((x + tw / 2, sy + 56), val, font=_fit(d, val, ci.FONT_DISPLAY_BOLD, 64, tw - 20), fill=col, anchor="mm")
        _spaced(d, (x + tw / 2, sy + 104), lab, _font(ci.FONT_LABEL, 22), DIM, 3, anchor="m")

    # top cards (best in the centre)
    _spaced(d, (60, 590), "TOP CARDS", _font(ci.FONT_LABEL, 30), WHITE, 5)
    d.line([(250, 590), (W - 60, 590)], fill=(255, 255, 255, 50), width=2)
    paths = list(data.get("cards") or [])[:3]
    cw, bw = 280, 320
    ch, bh = int(cw * CARD_RATIO), int(bw * CARD_RATIO)
    y0 = 630
    slots = {0: ((W - bw) // 2, y0, bw, True), 1: (70, y0 + (bh - ch) // 2 + 10, cw, False),
             2: (W - 70 - cw, y0 + (bh - ch) // 2 + 10, cw, False)}
    if not paths:
        d.text((W / 2, y0 + bh / 2), "No cards yet", font=_font(ci.FONT_LABEL, 40), fill=DIM, anchor="mm")
    for i in (1, 2, 0):                                  # centre card drawn last, on top
        if i >= len(paths):
            continue
        x, y, w, big = slots[i]
        c = _card(paths[i], w)
        if c is None:
            continue
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sh.paste((tcol if big else (0, 0, 0)) + (200,), (x, y), c.getchannel("A"))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(22 if big else 14)))
        img.alpha_composite(c, (x, y))
    d = ImageDraw.Draw(img)

    # footer strip
    fy = y0 + bh + 40
    d.rounded_rectangle((60, fy, W - 60, fy + 110), radius=20, fill=(10, 16, 36, 235), outline=(255, 255, 255, 40), width=2)
    fav = str(data.get("favourite") or "Not set").upper()
    items = [("FAVOURITE", fav), ("CARDS", f"{data.get('owned', 0)} OF {data.get('total', 0)}"),
             ("COINS", fmt_short(data.get("coins", 0)))]
    cwid = (W - 120) / 3
    for i, (lab, val) in enumerate(items):
        cx = 60 + cwid * i + cwid / 2
        _spaced(d, (cx, fy + 34), lab, _font(ci.FONT_LABEL, 22), DIM, 3, anchor="m")
        d.text((cx, fy + 74), val, font=_fit(d, val, ci.FONT_DISPLAY_BOLD, 40, cwid - 30), fill=WHITE, anchor="mm")
        if i:
            d.line([(60 + cwid * i, fy + 22), (60 + cwid * i, fy + 88)], fill=(255, 255, 255, 50), width=2)

    # chamfered frame
    cut = 50
    pts = [(18, 18), (W - 18 - cut, 18), (W - 18, 18 + cut), (W - 18, H - 18), (18 + cut, H - 18), (18, H - 18 - cut)]
    d.line(pts + [pts[0]], fill=WHITE, width=4, joint="curve")
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon([(0, 0), (W - cut - 8, 0), (W, cut + 8), (W, H), (cut + 8, H), (0, H - cut - 8)], fill=255)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
