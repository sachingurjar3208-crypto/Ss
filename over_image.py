"""Small TV-style scoreboard image, posted once at the end of every over.

build(data) -> io.BytesIO (PNG, 760x240, palette-compressed: ~25-45 KB, renders in a few ms)

data = {
  "over": 4, "team": "Kohli XI", "score": "34/2", "overs": "4.0", "total_overs": 5,
  "balls": ["1", "4", "0", "W", "6", "Wd", "2"],          # this over, in order
  "over_runs": 13,
  "batters": [("V Kohli", 23, 14, True), ("R Sharma", 8, 6, False)],   # name, runs, balls, on strike
  "bowler": ("J Bumrah", "1.0", 13, 1),                   # name, overs, runs, wickets
  "line": "CRR 8.50   |   Projected 42",                  # bottom line
  "win": (0.62, "Kohli XI", "Rohit XI") or None,          # chase only
  "innings": 1,
}
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).parent
W, H = 760, 240
GOLD = (240, 198, 104)
WHITE = (240, 244, 255)
DIM = (150, 162, 200)
INK = (10, 12, 26)
TEAM1, TEAM2 = (46, 204, 113), (243, 156, 18)          # innings 1 green, innings 2 orange (same as the embed)
CHIP = {"4": (52, 120, 255), "6": (150, 70, 230), "W": (225, 60, 60), "0": (70, 78, 108),
        "Wd": (205, 160, 40), "NB": (205, 160, 40)}
_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .")


@lru_cache(maxsize=32)
def _font(name: str, size: int):
    try:
        return ImageFont.truetype(str(_HERE / name), size)
    except Exception:
        return ImageFont.load_default()


def _num(text: str, size: int):
    """display_bold.ttf for plain text/numbers, label.ttf when the text has / - ( ) etc."""
    return _font("display_bold.ttf" if set(text) <= _SAFE else "label.ttf", size)


@lru_cache(maxsize=2)
def _background(innings: int) -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    a, b = (14, 18, 42), (26, 30, 66)
    for y in range(H):
        t = y / H
        d.line((0, y, W, y), fill=tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    d.rectangle((0, 0, W, 64), fill=(9, 11, 28))
    d.rectangle((0, 0, 8, 64), fill=TEAM1 if innings == 1 else TEAM2)
    d.line((0, 64, W, 64), fill=(60, 66, 110), width=2)
    d.line((0, H - 40, W, H - 40), fill=(40, 46, 86), width=1)
    d.line((470, 76, 470, H - 50), fill=(40, 46, 86), width=1)
    return img


def build(data: dict) -> io.BytesIO:
    img = _background(1 if data.get("innings", 1) == 1 else 2).copy()
    d = ImageDraw.Draw(img)

    # header: OVER n  |  team ............ score (overs)
    d.text((24, 18), f"OVER {data['over']}", font=_font("label.ttf", 16), fill=GOLD)
    team = str(data.get("team", ""))[:22].upper()
    d.text((24, 36), team, font=_font("label.ttf", 22), fill=WHITE)
    score = str(data["score"])
    ov = f"({data['overs']}/{data['total_overs']})"
    f_ov = _font("label.ttf", 20)
    d.text((W - 20, 34), ov, font=f_ov, fill=DIM, anchor="rm")
    x_score = W - 30 - d.textlength(ov, font=f_ov)
    d.text((x_score, 34), score, font=_num(score, 46), fill=GOLD, anchor="rm")

    # this over: ball chips + runs
    x, y = 24, 84
    r = 17
    for s in list(data.get("balls", []))[:10]:
        key = "NB" if s.startswith("NB") else s
        col = CHIP.get(key, (96, 106, 140))
        d.ellipse((x, y, x + 2 * r, y + 2 * r), fill=col)
        label = "•" if s == "0" else ("NB" if s.startswith("NB") else s)
        fnt = _font("label.ttf", 26 if label == "•" else (16 if len(label) < 2 else 12))
        d.text((x + r, y + r), label, font=fnt, fill=WHITE, anchor="mm")
        x += 2 * r + 8
    runs = int(data.get("over_runs", 0))
    d.text((x + 6, y + r), f"{runs} RUN{'S' if runs != 1 else ''}", font=_font("label.ttf", 18), fill=DIM, anchor="lm")

    # batters (left)
    y = 136
    for name, runs, balls, on_strike in list(data.get("batters", []))[:2]:
        nm = str(name)[:16] + ("*" if on_strike else "")
        d.text((24, y), nm, font=_font("label.ttf", 20), fill=WHITE if on_strike else DIM)
        d.text((360, y), str(runs), font=_num(str(runs), 24), fill=GOLD if on_strike else WHITE, anchor="ra")
        d.text((368, y + 4), f"({balls})", font=_font("label.ttf", 16), fill=DIM)
        y += 28

    # bowler (right)
    bw = data.get("bowler")
    if bw:
        name, overs, bruns, wkts = bw
        d.text((490, 136), "BOWLER", font=_font("label.ttf", 13), fill=DIM)
        d.text((490, 152), str(name)[:16], font=_font("label.ttf", 20), fill=WHITE)
        fig = f"{overs}-{bruns}-{wkts}"
        d.text((W - 20, 160), fig, font=_font("label.ttf", 22), fill=GOLD, anchor="rm")

    # bottom line + win probability (chase)
    d.text((24, H - 20), str(data.get("line", "")), font=_font("label.ttf", 16), fill=DIM, anchor="lm")
    win = data.get("win")
    if win:
        p, a_name, b_name = win
        bx0, bx1, by = 470, W - 20, H - 26
        d.rounded_rectangle((bx0, by, bx1, by + 12), 6, fill=TEAM1 if data.get("innings") == 1 else (200, 70, 70))
        split = bx0 + (bx1 - bx0) * max(0.02, min(0.98, p))
        d.rounded_rectangle((bx0, by, split, by + 12), 6, fill=TEAM2)
        pct = int(round(p * 100))
        d.text((bx0, by - 4), f"{str(a_name)[:10].upper()} {pct}%", font=_font("label.ttf", 12), fill=WHITE, anchor="lb")
        d.text((bx1, by - 4), f"{100 - pct}% {str(b_name)[:10].upper()}", font=_font("label.ttf", 12), fill=WHITE, anchor="rb")

    out = img.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
