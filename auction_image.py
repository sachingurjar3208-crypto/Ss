"""Image for the auction start: one mystery card ("?") per lot, LOT number + base price under each.
No card picture and no name: each player is revealed when his lot starts."""
from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_HERE = Path(__file__).parent
GOLD = (240, 198, 104)
GOLD_HI = (255, 230, 160)
GOLD_DK = (150, 104, 40)
IVORY = (246, 240, 226)
DIM = (165, 172, 205)
INK = (24, 16, 10)


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(str(_HERE / name), size)
    except Exception:
        return ImageFont.load_default()


def _short(n: int) -> str:
    if n >= 10_000_000:
        return f"{n / 10_000_000:.2f}".rstrip("0").rstrip(".") + " CR"
    if n >= 100_000:
        return f"{n / 100_000:.2f}".rstrip("0").rstrip(".") + " L"
    if n >= 1_000:
        return f"{n / 1_000:.1f}".rstrip("0").rstrip(".") + " K"
    return str(n)


def build_lineup(lots: list[tuple[str | None, str, int, int]], minutes: int = 2) -> io.BytesIO | None:
    """lots = [(image_path or None, player name, ovr, base price), ...]. None if nothing to draw."""
    if not lots:
        return None
    cw, ch = 300, 439                       # card size (same ratio as the 1037x1517 cards)
    gap, pad = 34, 60
    n = len(lots)
    W = pad * 2 + n * cw + (n - 1) * gap
    H = 250 + ch + 170
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        a, b = (12, 14, 36), (30, 22, 58)
        d.line((0, y, W, y), fill=tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    img = img.convert("RGBA")
    # soft gold glow + faint streaks
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    ld.ellipse((W * 0.2, -180, W * 0.8, 260), fill=(255, 190, 110, 45))
    img.alpha_composite(lay.filter(ImageFilter.GaussianBlur(70)))
    rnd = random.Random(5)
    st = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(st)
    for _ in range(50):
        x, y = rnd.randint(-200, W), rnd.randint(0, H)
        ln = rnd.randint(120, 380)
        sd.line((x, y, x + ln, y - ln), fill=(255, 210, 140, rnd.randint(6, 14)), width=rnd.randint(2, 4))
    img.alpha_composite(st)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((14, 14, W - 14, H - 14), 28, outline=GOLD, width=4)
    d.rounded_rectangle((26, 26, W - 26, H - 26), 22, outline=GOLD_DK, width=2)

    d.text((W / 2, 100), "LIVE AUCTION", font=_font("display_bold.ttf", 110), fill=GOLD_HI, anchor="mm",
           stroke_width=3, stroke_fill=INK)
    d.text((W / 2, 180), f"{n} PLAYERS   |   {minutes} MIN EACH   |   TYPE  BID 30L  IN CHAT",
           font=_font("label.ttf", 30), fill=IVORY, anchor="mm")

    y0 = 230
    for i, (path, name, ovr, base) in enumerate(lots):
        x0 = pad + i * (cw + gap)
        # mystery card: no picture, no name, only a big "?" (the player is revealed when the lot starts)
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((x0 + 6, y0 + 14, x0 + cw + 6, y0 + ch + 14), 26, fill=(0, 0, 0, 150))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12)))
        card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        for yy in range(ch):
            t = yy / ch
            a, b = (58, 36, 96), (16, 12, 30)
            cd.line((0, yy, cw, yy), fill=tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3)) + (255,))
        m = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, cw - 1, ch - 1), 26, fill=255)
        card.putalpha(m)
        glow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((cw * 0.12, ch * 0.18, cw * 0.88, ch * 0.72), fill=(255, 190, 110, 70))
        card.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, cw - 1, ch - 1), 26, outline=GOLD, width=4)
        cd.rounded_rectangle((12, 12, cw - 13, ch - 13), 20, outline=GOLD_DK, width=2)
        cd.text((cw / 2, ch * 0.45), "?", font=_font("label.ttf", 240), fill=GOLD_HI, anchor="mm",
                stroke_width=4, stroke_fill=INK)
        cd.text((cw / 2, ch * 0.82), "MYSTERY PLAYER", font=_font("label.ttf", 26), fill=IVORY, anchor="mm")
        img.alpha_composite(card, (x0, y0))
        d = ImageDraw.Draw(img)
        cx = x0 + cw / 2
        ty = y0 + ch + 30
        d.rounded_rectangle((cx - 62, ty - 20, cx + 62, ty + 20), 20, fill=GOLD)
        d.text((cx, ty), f"LOT {i + 1}", font=_font("label.ttf", 26), fill=INK, anchor="mm")
        d.text((cx, ty + 52), "BASE", font=_font("label.ttf", 24), fill=DIM, anchor="mm")
        d.text((cx, ty + 92), _short(base), font=_font("display_bold.ttf", 52), fill=IVORY, anchor="mm")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    buf.seek(0)
    return buf
