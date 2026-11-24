"""Rush Cricket on-crease / on-bowling stat banners (ALL-TIME career stats).

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


_HEX = [(0, 250), (80, 30), (390, 30), (505, 250), (390, 470), (80, 470)]   # logo hexagon on the template


def _themed_banner(theme):
    """The stat banner redrawn on the rcsb theme image (same layout, theme colours)."""
    import match_theme
    style, img = theme
    pal = match_theme.palette(style)
    base = Image.open(_BANNER).convert("RGBA")
    w, h = base.size
    bg = match_theme.backdrop(img, (w, h), blur=5, dark=0.5, tint=pal["tint"]).convert("RGBA")
    # darker strip behind the stats so the numbers stay readable
    strip = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(strip)
    sd.rectangle((520, 272, w, h), fill=(*pal["tint"], 150))
    sd.rectangle((520, 0, w, 272), fill=(*pal["tint"], 70))
    bg = Image.alpha_composite(bg, strip)
    d = ImageDraw.Draw(bg, "RGBA")
    ln = (*pal["line"], 255)
    d.line((380, 14, w, 14), fill=ln, width=5)
    d.line((520, 272, w, 272), fill=(*pal["line"], 200), width=3)
    d.line((380, h - 12, w, h - 12), fill=ln, width=5)
    for x in _DIV:
        d.line((x, 300, x, 470), fill=(*pal["line"], 220), width=3)
    # keep the round logo from the template
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon(_HEX, fill=255)
    bg.paste(base, (0, 0), m)
    d.polygon(_HEX, outline=(*pal["line"], 255), width=6)
    return bg, pal


def _render(tag: str, accent, name: str, status: str, cells, theme=None) -> bytes:
    pal = None
    if theme and theme[0] and theme[1]:
        try:
            im, pal = _themed_banner(theme)
        except Exception as e:
            print(f"[stats] theme skipped: {type(e).__name__}: {e}")
            im = Image.open(_BANNER).convert("RGBA")
    else:
        im = Image.open(_BANNER).convert("RGBA")
    d = ImageDraw.Draw(im, "RGBA")
    if pal:   # theme colours
        accent = (*(pal["bat"] if tag == "BATTER" else pal["bowl"]), 255)
        _sub, _gold, _white = (*pal["sub"], 255), (*pal["hi"], 255), (*pal["text"], 255)
    else:
        _sub, _gold, _white = _SKY2, _GOLD, _WHITE
    tag_fg = (10, 14, 24, 255) if pal and sum(accent[:3]) > 520 else _WHITE

    # tag pill (BATTER / BOWLER) + status
    tf = _f(_LAB, 40)
    tw = d.textlength(tag, font=tf) + 60
    d.rounded_rectangle((640, 40, 640 + tw, 96), radius=8, fill=accent)
    d.text((640 + tw / 2, 68), tag, font=tf, fill=tag_fg, anchor="mm")
    if status:
        d.text((640 + tw + 28, 68), status.upper(), font=_f(_LAB, 36), fill=_sub, anchor="lm")
    d.text((im.width - 70, 68), "RUSH CRICKET  |  ALL TIME", font=_f(_LAB, 38), fill=_sub, anchor="rm")

    # name (shrinks to fit)
    nm = (name or "").upper()
    size = 100
    nf = _f(_NAME, size)
    while d.textlength(nm, font=nf) > 1380 and size > 40:
        size -= 4
        nf = _f(_NAME, size)
    if pal:
        d.text((644, 179), nm, font=nf, fill=(0, 0, 0, 160), anchor="lm")
    d.text((640, 175), nm, font=nf, fill=_white, anchor="lm")

    # 7 stat cells
    lf = _f(_LAB, 36)
    for i, (label, value, hi) in enumerate(cells):
        cx = (_DIV[i] + _DIV[i + 1]) // 2 + (15 if i == 0 else 0)
        _num(d, cx, _ROW_Y - 30, str(value), _gold if hi else _white, 150 if i == 0 else 190)
        d.text((cx, _ROW_Y + 48), label, font=lf, fill=(*pal["sub"], 255) if pal else _SKY, anchor="mm")

    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def generate_bat_card(name: str, stats: dict | None, card_image_bytes=None, status: str = "", theme=None) -> bytes:
    return _render("BATTER", _BLUE, name, status, _bat_cells(stats or {}), theme)


def generate_bowl_card(name: str, stats: dict | None, card_image_bytes=None, status: str = "CURRENT BOWLER",
                       theme=None) -> bytes:
    return _render("BOWLER", _RED, name, status, _bowl_cells(stats or {}), theme)
