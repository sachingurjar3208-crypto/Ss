"""Cricket Star end-of-match SUMMARY card (1536x1024), drawn on summary_base.png.

    png_bytes = generate_summary_card({
        "match_id": 194897, "venue": "National Stadium, Karachi",
        "team1": {"name": ..., "runs": 207, "wickets": 8, "overs": "20.0", "max_overs": "20",
                  "batters": [(name, runs, balls, not_out), ...],      # that team's top batters
                  "bowlers": [(name, wickets, runs, overs), ...]},     # that team's top bowlers
        "team2": {... same ...},
        "result": "TEAM X WON BY 1 RUN", "potm": "Jacques Kallis",
    })
team1 = batted first (blue section on top), team2 = batted second (red section).
Fonts are the bot's own: name_font.ttf (Montserrat ExtraBold), display_bold.ttf (Jersey M54), label.ttf (Barlow Condensed).
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).parent
_BASE = _HERE / "summary_base.png"
_NAME, _NUM, _LAB = _HERE / "name_font.ttf", _HERE / "display_bold.ttf", _HERE / "label.ttf"

WHITE = (255, 255, 255, 255)
GOLD = (255, 214, 102, 255)
SOFT = (200, 220, 255, 255)

# Row geometry measured on the template.  (top, bottom) of each of the 4 rows per section.
_ROWS_1 = [(262, 301), (308, 349), (357, 397), (405, 448)]
_ROWS_2 = [(546, 586), (593, 634), (641, 682), (689, 733)]
_LEFT_X = [172, 146, 118, 98]             # slanted left edge of the left name bars, per row
_L_NAME_END = 612
_R_NAME_X, _R_NAME_END = 832, 1310
_CELLS_L = [(635, 711), (717, 794)]      # left stat cells
_CELLS_R = [(1332, 1418), (1424, 1514)]  # right stat cells


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _fit(d, text, path, size, maxw, min_size=14):
    f = _f(path, size)
    while d.textlength(text, font=f) > maxw and size > min_size:
        size -= 2
        f = _f(path, size)
    return f


def _shadow(d, xy, text, font, fill, anchor="lm"):
    d.text((xy[0] + 2, xy[1] + 2), text, font=font, fill=(0, 0, 0, 170), anchor=anchor)
    d.text(xy, text, font=font, fill=fill, anchor=anchor)


def _num(d, cx, cy, text, size=36, fill=WHITE, maxw=70):
    """Value centred in a cell. Jersey lacks '-', '/', '*' so those use Montserrat."""
    while True:
        fj, fo = _f(_NUM, size), _f(_NAME, int(size * 0.62))
        parts = [(c, fj if (c.isdigit() or c == ".") else fo) for c in text]
        w = sum(d.textlength(c, font=f) for c, f in parts)
        if w <= maxw or size <= 18:
            break
        size -= 2
    x = cx - w / 2
    for c, f in parts:
        d.text((x, cy), c, font=f, fill=fill, anchor="lm")
        x += d.textlength(c, font=f)


def _row_text(d, rows, entries, left: bool, kind: str):
    for i, (top, bot) in enumerate(rows):
        if i >= len(entries):
            break
        cy = (top + bot) // 2
        e = entries[i]
        if kind == "bat":
            name, a, b = e[0], str(e[1]) + ("*" if len(e) > 3 and e[3] else ""), str(e[2])
        else:
            name, a, b = e[0], f"{e[1]}-{e[2]}", str(e[3])
        x0 = (_LEFT_X[i] + 34) if left else _R_NAME_X
        end = _L_NAME_END if left else _R_NAME_END
        f = _fit(d, name.upper(), _NAME, 27, end - x0 - 8)
        _shadow(d, (x0, cy), name.upper(), f, WHITE)
        cells = _CELLS_L if left else _CELLS_R
        _num(d, (cells[0][0] + cells[0][1]) // 2, cy - 1, a, 36, GOLD if (kind == "bowl" or True) and False else WHITE, cells[0][1] - cells[0][0] - 8)
        _num(d, (cells[1][0] + cells[1][1]) // 2, cy - 1, b, 36, SOFT, cells[1][1] - cells[1][0] - 8)


def _header(d, team: dict, cy: int):
    name = (team.get("name") or "TEAM").upper()
    f = _fit(d, name, _NAME, 40, 880)
    _shadow(d, (215, cy), name, f, WHITE)
    score = f"{team.get('runs', 0)}/{team.get('wickets', 0)}"
    _num(d, 1410, cy - 2, score, 58, WHITE, 200)
    ov = f"OVERS {team.get('overs', '0.0')}/{team.get('max_overs', '20')}"
    _shadow(d, (1250, cy - 14), "OVERS", _f(_LAB, 22), SOFT, anchor="lm")
    _shadow(d, (1250, cy + 10), f"{team.get('overs', '0.0')}", _f(_NUM, 34), WHITE, anchor="lm")


def generate_summary_card(data: dict) -> bytes:
    im = Image.open(_BASE).convert("RGBA")
    d = ImageDraw.Draw(im, "RGBA")
    t1, t2 = data["team1"], data["team2"]

    # top bar
    _shadow(d, (430, 58), "SUMMARY", _f(_NAME, 44), WHITE, anchor="mm")
    venue = (data.get("venue") or "").upper()
    if venue:
        _shadow(d, (430, 98), venue, _fit(d, venue, _LAB, 30, 340), SOFT, anchor="mm")
    mid = f"MATCH #{data.get('match_id', '')}"
    _shadow(d, (1105, 58), mid, _fit(d, mid, _NAME, 44, 330), WHITE, anchor="mm")
    fmt = f"{t1.get('max_overs', '20')} OVERS MATCH"
    _shadow(d, (1105, 98), fmt, _f(_LAB, 30), SOFT, anchor="mm")

    # section 1 (team1 batting, blue) + section 2 (team2 batting, red)
    _header(d, t1, 224)
    _row_text(d, _ROWS_1, t1.get("batters", []), True, "bat")
    _row_text(d, _ROWS_1, t2.get("bowlers", []), False, "bowl")
    _header(d, t2, 510)
    _row_text(d, _ROWS_2, t2.get("batters", []), True, "bat")
    _row_text(d, _ROWS_2, t1.get("bowlers", []), False, "bowl")

    # result bar
    res = (data.get("result") or "").upper()
    _shadow(d, (768, 812), res, _fit(d, res, _NAME, 46, 1250), WHITE, anchor="mm")
    potm = data.get("potm")
    if potm:
        txt = f"PLAYER OF THE MATCH:  {potm.upper()}"
        _shadow(d, (768, 856), txt, _fit(d, txt, _LAB, 32, 1100), GOLD, anchor="mm")

    # footer: cover the blurry area of the template with a clean dark band
    band = Image.new("RGBA", (1536, 70), (2, 8, 24, 235))
    im.alpha_composite(band, (0, 900))
    d = ImageDraw.Draw(im, "RGBA")
    d.text((768, 935), "CRICKET STAR", font=_f(_LAB, 34), fill=(140, 180, 255, 255), anchor="mm")

    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()
