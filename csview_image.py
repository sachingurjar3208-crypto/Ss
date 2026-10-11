"""Cricket Star `csview` stat card: the player's card on the left, OVR/BAT/BOWL
bars, and batting + bowling panels on the right (1400x840 PNG).

    generate_view_card(...) -> PNG bytes

Fonts are the same ones the rest of the bot uses:
  display_bold.ttf = Jersey (numbers)   name_font.ttf = Montserrat ExtraBold (name)
  label.ttf        = Barlow Condensed Bold (labels)
Jersey has no "/", "-" or "*", so those characters fall back to Montserrat.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).parent
_NUM, _LAB, _NAME = _HERE / "display_bold.ttf", _HERE / "label.ttf", _HERE / "name_font.ttf"

W, H = 1400, 840
GOLD = (255, 214, 102)
BLUE = (0, 150, 255)
RED = (235, 70, 80)
WHITE = (245, 247, 255)
DIM = (150, 165, 200)
PANEL = (22, 32, 66)
TRACK = (35, 48, 90)


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _value(d: ImageDraw.ImageDraw, x: int, y: int, text: str, size: int, fill, anchor: str = "l"):
    """Draw a number with Jersey; characters Jersey lacks use Montserrat.
    anchor 'l' = text starts at x, 'r' = text ends at x."""
    fj, fo = _f(_NUM, size), _f(_NAME, int(size * 0.62))
    parts = [(c, fj if (c.isdigit() or c in ".,") else fo) for c in text]
    total = sum(d.textlength(c, font=f) for c, f in parts)
    cx = x - total if anchor == "r" else x
    for c, f in parts:
        d.text((cx, y), c, font=f, fill=fill, anchor="lm")
        cx += d.textlength(c, font=f)


def _fit_name(d: ImageDraw.ImageDraw, text: str, maxw: int, size: int = 64):
    """Returns (font, text): shrinks the name to fit, and as a last resort cuts it with '...'."""
    while size > 36:
        f = _f(_NAME, size)
        if d.textlength(text, font=f) <= maxw:
            return f, text
        size -= 4
    f = _f(_NAME, 36)
    cut = text
    while len(cut) > 3 and d.textlength(cut + "...", font=f) > maxw:
        cut = cut[:-1]
    return f, cut.rstrip() + "..."



_LABELS = {
    "mat": "MATCHES", "runs": "RUNS", "50": "FIFTIES", "100": "HUNDREDS", "4s/6s": "4S / 6S",
    "ave": "AVERAGE", "s/r": "STRIKE RATE", "0s": "DUCKS", "hs": "HIGH SCORE",
    "wkts": "WICKETS", "3w": "3-WICKET HAULS", "5w": "5-WICKET HAULS", "econ": "ECONOMY",
    "mdns": "MAIDENS", "bbi": "BEST FIGURES",
}


def _bar(d, y: int, label: str, val: int, col):
    d.text((520, y), label, font=_f(_LAB, 34), fill=DIM, anchor="lm")
    d.rounded_rectangle((650, y - 14, 1050, y + 14), 14, fill=TRACK)
    pct = max(0, min(100, int(val)))
    if pct:
        d.rounded_rectangle((650, y - 14, 650 + max(28, 400 * pct // 100), y + 14), 14, fill=col)
    _value(d, 1090, y, str(val), 56, WHITE)


def _panel(d, x: int, title: str, col, items: list[tuple[str, str]]):
    d.rounded_rectangle((x, 410, x + 410, 800), 22, fill=PANEL)
    d.rounded_rectangle((x, 410, x + 410, 424), 7, fill=col)
    d.rectangle((x, 417, x + 410, 424), fill=col)
    d.text((x + 24, 455), title, font=_f(_LAB, 38), fill=col, anchor="lm")
    for i, (k, v) in enumerate(items[:9]):
        y = 500 + i * 33
        d.text((x + 24, y), _LABELS.get(k.lower(), k.upper()), font=_f(_LAB, 27), fill=DIM, anchor="lm")
        _value(d, x + 386, y, v, 38, WHITE, anchor="r")


def _card_image(path: str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        import card_cache   # reuses the small cached copy, never the full-size PNG
        data = card_cache._get_sync(path)
        if data is not None:
            return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        pass
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


def generate_view_card(
    name: str,
    ovr: int,
    bat: int,
    bowl: int,
    owner: str,
    value_text: str,
    bat_figs: list[tuple[str, str]],
    bowl_figs: list[tuple[str, str]],
    image_path: str | None = None,
    styles: list[str] | None = None,
    subtitle: str = "PLAYER STATS",
) -> bytes:
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for y in range(H):   # soft vertical gradient
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(12 + 20 * t), int(18 + 14 * t), int(38 + 40 * t)))

    # card frame (left)
    fx, fy, fw, fh = 50, 110, 420, 614
    d.rounded_rectangle((fx, fy, fx + fw, fy + fh), 28, fill=(25, 35, 70), outline=GOLD, width=5)
    card = _card_image(image_path)
    if card is not None:
        pad = 12
        iw, ih = fw - 2 * pad, fh - 2 * pad
        scale = min(iw / card.width, ih / card.height)
        card = card.resize((max(1, int(card.width * scale)), max(1, int(card.height * scale))), Image.LANCZOS)
        mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, card.width, card.height), 18, fill=255)
        im.paste(card, (fx + (fw - card.width) // 2, fy + (fh - card.height) // 2), mask)
    else:
        d.text((fx + fw // 2, fy + fh // 2), "NO IMAGE", font=_f(_NUM, 80), fill=(60, 80, 130), anchor="mm")

    # header
    title = (name or "").upper()
    name_font, title = _fit_name(d, title, 540)
    d.text((520, 80), title, font=name_font, fill=WHITE, anchor="lm")
    sub = f"{subtitle}  ·  OWNER: {(owner or '').upper()[:22]}"
    d.text((520, 138), sub, font=_f(_LAB, 30), fill=DIM, anchor="lm")
    if styles:
        d.text((520, 176), " · ".join(styles)[:60].upper(), font=_f(_LAB, 28), fill=GOLD, anchor="lm")
    d.rounded_rectangle((1090, 55, 1350, 120), 14, fill=(40, 55, 100))
    d.text((1220, 88), value_text.upper(), font=_f(_LAB, 34), fill=GOLD, anchor="mm")

    # rating bars
    _bar(d, 245, "OVR", ovr, GOLD)
    _bar(d, 305, "BAT", bat, BLUE)
    _bar(d, 365, "BOWL", bowl, RED)

    # stat panels
    _panel(d, 520, "BATTING", BLUE, bat_figs)
    _panel(d, 940, "BOWLING", RED, bowl_figs)

    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
