"""Cricket Star on-crease / on-bowling stat banners (ALL-TIME career stats).

views.py calls generate_bat_card() / generate_bowl_card() and sends the PNG
bytes as a discord.File.  Template: stat_banner.png (2172x502).
Fonts (same ones the card maker uses):
  display_bold.ttf = Jersey M54 (numbers)   name_font.ttf = Montserrat ExtraBold (name)
  label.ttf        = Barlow Condensed Bold (tags / small labels)
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).parent
_BANNER = _HERE / "stat_banner.png"
_NUM, _LAB, _NAME = (_HERE / "display_bold.ttf", _HERE / "label.ttf", _HERE / "name_font.ttf")

# x of the 8 divider lines in the template -> 7 stat cells
_DIV = [560, 746, 977, 1215, 1447, 1668, 1888, 2104]
_ROW_Y = 371          # vertical centre of the stat strip (cropped banner coords)
_BLUE = (0, 120, 255, 255)
_RED = (230, 60, 70, 255)
_GOLD = (255, 214, 102, 255)
_WHITE = (255, 255, 255, 255)
_SKY = (120, 180, 255, 255)
_SKY2 = (140, 200, 255, 255)


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _num(d: ImageDraw.ImageDraw, cx: int, cy: int, text: str, fill, maxw: int, size: int = 104):
    """Draw a value centred in its cell. Jersey has no '/' or '*', so those use Montserrat."""
    while True:
        fj, fo = _f(_NUM, size), _f(_NAME, int(size * 0.8))
        parts = [(c, fj if (c.isdigit() or c == ".") else fo) for c in text]
        w = sum(d.textlength(c, font=f) for c, f in parts)
        if w <= maxw or size <= 30:
            break
        size -= 4
    x = cx - w / 2
    for c, f in parts:
        d.text((x, cy), c, font=f, fill=fill, anchor="lm")
        x += d.textlength(c, font=f)


def _fmt_int(n) -> str:
    return str(int(n or 0))


def _bat_cells(c: dict):
    inns = int(c.get("bat_inns", 0) or 0)
    dismissals = inns - int(c.get("not_outs", 0) or 0)
    balls = int(c.get("balls", 0) or 0)
    runs = int(c.get("runs", 0) or 0)
    avg = f"{runs / dismissals:.1f}" if dismissals > 0 else "-"
    sr = f"{runs / balls * 100:.1f}" if balls else "-"
    hs = _fmt_int(c.get("highest")) if inns else "-"
    return [
        ("INN", _fmt_int(inns), False),
        ("RUNS", _fmt_int(runs), True),
        ("AVG", avg, False),
        ("S/R", sr, False),
        ("H/S", hs, False),
        ("50s", _fmt_int(c.get("fifties")), False),
        ("100s", _fmt_int(c.get("hundreds")), False),
    ]


def _bowl_cells(c: dict):
    inns = int(c.get("bowl_inns", 0) or 0)
    balls = int(c.get("balls_bowled", 0) or 0)
    conc = int(c.get("runs_conceded", 0) or 0)
    wk = int(c.get("wickets", 0) or 0)
    avg = f"{conc / wk:.1f}" if wk else "-"
    econ = f"{conc / (balls / 6):.1f}" if balls else "-"
    sr = f"{balls / wk:.1f}" if wk else "-"
    best = f"{c.get('best_w', 0)}/{c.get('best_r', 0)}" if c.get("best_w") else "-"
    return [
        ("INN", _fmt_int(inns), False),
        ("WKTS", _fmt_int(wk), True),
        ("AVG", avg, False),
        ("ECON", econ, False),
        ("S/R", sr, False),
        ("5W", _fmt_int(c.get("five_fers")), False),
        ("BEST", best, False),
    ]


def _render(tag: str, accent, name: str, status: str, cells) -> bytes:
    im = Image.open(_BANNER).convert("RGBA")
    d = ImageDraw.Draw(im, "RGBA")

    # tag pill (BATTER / BOWLER) + status
    tf = _f(_LAB, 40)
    tw = d.textlength(tag, font=tf) + 60
    d.rounded_rectangle((640, 40, 640 + tw, 96), radius=8, fill=accent)
    d.text((640 + tw / 2, 68), tag, font=tf, fill=_WHITE, anchor="mm")
    if status:
        d.text((640 + tw + 28, 68), status.upper(), font=_f(_LAB, 36), fill=_SKY2, anchor="lm")
    d.text((im.width - 70, 68), "CRICKET STAR  |  ALL TIME", font=_f(_LAB, 38), fill=_SKY2, anchor="rm")

    # name (shrinks to fit)
    nm = (name or "").upper()
    size = 100
    nf = _f(_NAME, size)
    while d.textlength(nm, font=nf) > 1380 and size > 40:
        size -= 4
        nf = _f(_NAME, size)
    d.text((640, 175), nm, font=nf, fill=_WHITE, anchor="lm")

    # 7 stat cells
    lf = _f(_LAB, 36)
    for i, (label, value, hi) in enumerate(cells):
        cx = (_DIV[i] + _DIV[i + 1]) // 2 + (15 if i == 0 else 0)
        _num(d, cx, _ROW_Y - 30, str(value), _GOLD if hi else _WHITE, 150 if i == 0 else 190)
        d.text((cx, _ROW_Y + 48), label, font=lf, fill=_SKY, anchor="mm")

    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def generate_bat_card(name: str, stats: dict | None, card_image_bytes=None, status: str = "") -> bytes:
    return _render("BATTER", _BLUE, name, status, _bat_cells(stats or {}))


def generate_bowl_card(name: str, stats: dict | None, card_image_bytes=None, status: str = "CURRENT BOWLER") -> bytes:
    return _render("BOWLER", _RED, name, status, _bowl_cells(stats or {}))
