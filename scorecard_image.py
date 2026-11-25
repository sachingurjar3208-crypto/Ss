"""Full scorecards (after the 1st innings and at the end of the match). Rush Cricket's own design.

Two cards, 1536x1024:
    batting_card(data)  -> PNG bytes   every batter: how out, R, B, 4s, 6s, SR (+ SR meter)
    bowling_card(data)  -> PNG bytes   every bowler: O, M, dots, R, W (+ wicket pips), econ,
                                       and the fall of wickets drawn as a run line

Look: dark graphite glass panels. Batting = teal accent, bowling = amber accent, gold for the
standouts (not out, best bowler, top scorer).

data = {
  "team": "GoneWithTheWind", "bowling_team": "Sora", "innings": 1, "venue": "Bay Oval",
  "match_id": 211347, "runs": 186, "wickets": 9, "overs": "20.0", "max_overs": 20, "extras": 2,
  "batters": [{"name", "runs", "balls", "fours", "sixes", "how": "c Kohli b Jacks" | "not out" |
               "yet to bat", "impact": bool}],
  "bowlers": [{"name", "balls", "maidens", "dots", "runs", "wickets"}],
  "fow": [{"score": 22, "over": "2.4", "name": "Gavaskar"}],
  "target_line": "Sora need 187 off 120 balls",     # optional
}
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_HERE = Path(__file__).parent
_BASE = _HERE / "summary_base.png"          # only the round logo is taken from it
_NAME, _NUM, _LAB = _HERE / "name_font.ttf", _HERE / "display_bold.ttf", _HERE / "label.ttf"
W, H = 1536, 1024

WHITE = (246, 248, 252)
MUTED = (142, 152, 170)
FAINT = (88, 96, 112)
GOLD = (255, 205, 92)
TEAL = (38, 214, 182)
AMBER = (255, 158, 48)
BG_TOP, BG_BOT = (16, 20, 28), (8, 10, 15)


def _f(path, size):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _fit(d, text, path, size, maxw, min_size=12):
    while size > min_size and d.textlength(text, font=_f(path, size)) > maxw:
        size -= 1
    return _f(path, size)


def _txt(d, xy, text, font, fill, anchor="lm", shadow=True):
    if shadow:
        d.text((xy[0] + 1, xy[1] + 2), text, font=font, fill=(0, 0, 0), anchor=anchor)
    d.text(xy, text, font=font, fill=fill, anchor=anchor)


_THEME = None      # (style, image) while a themed card is being drawn


def _backdrop(accent) -> Image.Image:
    """Graphite gradient, faint diagonal grain, a soft accent glow top-left, round logo.
    With an rcsb theme: the theme image (blurred, darkened) instead of the graphite."""
    if _THEME:
        import match_theme
        pal = match_theme.palette(_THEME[0])
        im = match_theme.backdrop(_THEME[1], (W, H), blur=30, dark=0.6, tint=pal["tint"])
    else:
        im = Image.new("RGB", (W, H))
        px = ImageDraw.Draw(im)
        for y in range(H):
            t = y / H
            px.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))
        grain = Image.new("L", (W, H), 0)
        g = ImageDraw.Draw(grain)
        for x in range(-H, W, 22):
            g.line([(x, 0), (x + H, H)], fill=10, width=1)
        im.paste(Image.new("RGB", (W, H), (60, 70, 90)), (0, 0), grain)
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse([-420, -520, 760, 420], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    im.paste(Image.new("RGB", (W, H), accent), (0, 0), glow)
    try:   # the bot's round logo
        base = Image.open(_BASE).convert("RGB").resize((W, H))
        cx, cy, r = 768, 72, 68
        logo = base.crop((cx - r, cy - r, cx + r, cy + r)).resize((112, 112), Image.LANCZOS)
        m = Image.new("L", (112, 112), 0)
        ImageDraw.Draw(m).ellipse([0, 0, 111, 111], fill=255)
        ImageDraw.Draw(im).ellipse([768 - 62, 70 - 62, 768 + 62, 70 + 62], outline=accent, width=3)
        im.paste(logo, (768 - 56, 70 - 56), m)
    except Exception:
        pass
    return im


def _panel(im, box, radius=14, fill=(255, 255, 255), alpha=14):
    """Frosted glass rectangle (rounded)."""
    x0, y0, x1, y1 = box
    m = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, x1 - x0 - 1, y1 - y0 - 1], radius=radius, fill=alpha)
    im.paste(Image.new("RGB", m.size, fill), (x0, y0), m)


def _pill(d, x, y, text, fill, fg=(10, 12, 16), font=None, pad=14, h=34):
    font = font or _f(_LAB, 24)
    w = d.textlength(text, font=font) + pad * 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=fill)
    d.text((x + w / 2, y + h / 2), text, font=font, fill=fg, anchor="mm")
    return x + w


def _balls(overs) -> int:
    try:
        o, _, b = str(overs).partition(".")
        return int(o) * 6 + int(b or 0)
    except Exception:
        return 0


def _header(im, d, data, batting: bool):
    accent = TEAL if batting else AMBER
    team = data.get("team", "") if batting else data.get("bowling_team", "")
    other = data.get("bowling_team", "") if batting else data.get("team", "")
    _txt(d, (48, 56), team.upper(), _fit(d, team.upper(), _NAME, 50, 560), WHITE)
    inn = "1ST INNINGS" if data.get("innings", 1) == 1 else "2ND INNINGS"
    x = _pill(d, 48, 92, "BATTING" if batting else "BOWLING", accent)
    x = _pill(d, x + 10, 92, inn, (40, 46, 58), fg=WHITE)
    venue = (data.get("venue") or "").upper()
    if venue:
        _txt(d, (x + 16, 109), venue, _f(_LAB, 24), MUTED, shadow=False)
    total = f"{data.get('runs', 0)}/{data.get('wickets', 0)}"
    _txt(d, (1488, 62), total, _f(_NAME, 66), WHITE, "rm")
    sub = f"{data.get('overs', '0')} OF {data.get('max_overs', 20)} OVERS   ·   VS {other.upper()}"
    _txt(d, (1488, 112), sub, _fit(d, sub, _LAB, 24, 560), MUTED, "rm", shadow=False)
    d.line([48, 150, 1488, 150], fill=(40, 46, 58), width=2)
    d.line([48, 150, 300, 150], fill=accent, width=4)


def _footer(im, d, data, accent):
    y = 940
    balls = _balls(data.get("overs", "0"))
    rr = (data.get("runs", 0) * 6 / balls) if balls else 0.0
    x = 48
    bats = [b for b in (data.get("batters") or []) if b.get("how") != "yet to bat"]
    third = (("MATCH", f"#{data['match_id']}") if data.get("match_id") else
             ("4s / 6s", f"{sum(b.get('fours', 0) for b in bats)} / {sum(b.get('sixes', 0) for b in bats)}"))
    for lab, val in (("RUN RATE", f"{rr:.2f}"), ("EXTRAS", str(data.get("extras", 0))), third):
        _panel(im, (x, y, x + 230, y + 58), radius=12, alpha=16)
        _txt(d, (x + 16, y + 29), lab, _f(_LAB, 22), MUTED, shadow=False)
        _txt(d, (x + 214, y + 30), val, _fit(d, val, _NAME, 28, 130), WHITE, "rm", shadow=False)
        x += 246
    tl = data.get("target_line")
    if tl:
        text = tl.upper()
        f = _fit(d, text, _LAB, 30, 700)
        w = d.textlength(text, font=f) + 44
        d.rounded_rectangle([1488 - w, y, 1488, y + 58], radius=12, fill=accent)
        d.text((1488 - w / 2, y + 29), text, font=f, fill=(12, 14, 18), anchor="mm")
    else:
        _txt(d, (1488, y + 29), "RUSH CRICKET", _f(_LAB, 26), FAINT, "rm", shadow=False)


def _png(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


_HOW_WORDS = {"c", "b", "lbw", "st", "&"}


def _how_text(how: str) -> str:
    """'c Kohli b Jacks' -> 'c Kohli   b Jacks' (lower-case c / b / lbw / st, names as they are)."""
    if not how or how in ("not out", "yet to bat"):
        return (how or "").upper()
    words = [w.lower() if w.lower() in _HOW_WORDS else w for w in how.split()]
    return " ".join(words).replace(" b ", "   b ")


# ── batting card ────────────────────────────────────────────────────────────
_BAT_COLS = [("B", 1150), ("4s", 1230), ("6s", 1302), ("SR", 1392)]


def _batting_card(data: dict) -> bytes:
    im = _backdrop(TEAL)
    d = ImageDraw.Draw(im)
    _header(im, d, data, True)
    rows = (data.get("batters") or [])[:11]
    top_score = max((b.get("runs", 0) for b in rows if b.get("how") != "yet to bat"), default=-1)

    y = 178
    for lab, x in (("BATTER", 96), ("DISMISSAL", 560)):
        _txt(d, (x, y), lab, _f(_LAB, 22), FAINT, shadow=False)
    _txt(d, (1036, y), "RUNS", _f(_LAB, 22), FAINT, "mm", shadow=False)
    for lab, cx in _BAT_COLS:
        _txt(d, (cx, y), lab, _f(_LAB, 22), FAINT, "mm", shadow=False)

    top, rh = 198, 66
    for i, b in enumerate(rows):
        y0, y1 = top + i * rh, top + i * rh + rh - 8
        cy = (y0 + y1) // 2
        how = b.get("how") or ""
        not_out, yet = how == "not out", how == "yet to bat"
        star = not yet and b.get("runs", 0) == top_score and top_score > 0
        stripe = GOLD if not_out else (FAINT if yet else TEAL)
        _panel(im, (48, y0, 1488, y1), radius=12, alpha=10 if yet else 18)
        d.rounded_rectangle([48, y0, 56, y1], radius=4, fill=stripe)
        _txt(d, (76, cy), str(i + 1), _f(_LAB, 24), FAINT, "mm", shadow=False)
        name = b["name"].upper()
        nf = _fit(d, name, _NAME, 27, 430)
        _txt(d, (96, cy), name, nf, FAINT if yet else (GOLD if not_out else WHITE))
        if b.get("impact"):
            ix = 106 + d.textlength(name, font=nf)
            _pill(d, ix, cy - 13, "IMPACT", (60, 220, 150), font=_f(_LAB, 17), pad=8, h=26)
        ht = _how_text(how)
        _txt(d, (560, cy), ht, _fit(d, ht, _LAB, 28, 400), GOLD if not_out else (FAINT if yet else MUTED),
             shadow=False)
        if yet:
            continue
        cell = GOLD if star else (52, 60, 76)
        d.rounded_rectangle([990, y0 + 6, 1082, y1 - 6], radius=10, fill=cell)
        rtxt = str(b["runs"]) + ("*" if not_out else "")
        _txt(d, (1036, cy + 1), rtxt, _f(_NAME, 30), (14, 14, 18) if star else WHITE, "mm", shadow=False)
        sr = b["runs"] * 100 / b["balls"] if b.get("balls") else 0
        vals = [str(b.get("balls", 0)), str(b.get("fours", 0)), str(b.get("sixes", 0)),
                f"{sr:.0f}" if b.get("balls") else "-"]
        for (lab, cx), v in zip(_BAT_COLS, vals):
            _txt(d, (cx, cy - (8 if lab == "SR" else 0)), v, _f(_NAME, 24), TEAL if lab == "SR" else WHITE, "mm",
                 shadow=False)
        mx0, mx1 = 1352, 1432        # strike-rate meter (0-250)
        d.rounded_rectangle([mx0, cy + 12, mx1, cy + 17], radius=3, fill=(44, 50, 62))
        d.rounded_rectangle([mx0, cy + 12, mx0 + max(4, int((mx1 - mx0) * min(1, sr / 250))), cy + 17],
                            radius=3, fill=TEAL)
    _footer(im, d, data, TEAL)
    return _png(im)


# ── bowling card ────────────────────────────────────────────────────────────
_BOWL_COLS = [("O", 720), ("M", 810), ("DOTS", 905), ("R", 1000), ("W", 1100), ("ECON", 1400)]


def _econ_col(e: float):
    return (92, 220, 140) if e < 7.5 else ((255, 205, 92) if e < 10 else (255, 110, 96))


def _bowling_card(data: dict) -> bytes:
    im = _backdrop(AMBER)
    d = ImageDraw.Draw(im)
    _header(im, d, data, False)
    rows = (data.get("bowlers") or [])[:8]
    best = max((b.get("wickets", 0) for b in rows), default=0)

    y = 178
    _txt(d, (96, y), "BOWLER", _f(_LAB, 22), FAINT, shadow=False)
    for lab, cx in _BOWL_COLS:
        _txt(d, (cx, y), lab, _f(_LAB, 22), FAINT, "mm", shadow=False)

    top, rh = 198, 58
    for i in range(8):
        y0, y1 = top + i * rh, top + i * rh + rh - 8
        cy = (y0 + y1) // 2
        if i >= len(rows):
            _panel(im, (48, y0, 1488, y1), radius=12, alpha=6)
            continue
        b = rows[i]
        star = best > 0 and b.get("wickets", 0) == best
        _panel(im, (48, y0, 1488, y1), radius=12, alpha=18)
        d.rounded_rectangle([48, y0, 56, y1], radius=4, fill=GOLD if star else AMBER)
        name = b["name"].upper()
        _txt(d, (96, cy), name, _fit(d, name, _NAME, 27, 540), GOLD if star else WHITE)
        balls = b.get("balls", 0)
        econ = b["runs"] * 6 / balls if balls else 0
        vals = [f"{balls // 6}.{balls % 6}", str(b.get("maidens", 0)), str(b.get("dots", 0)), str(b.get("runs", 0))]
        for (lab, cx), v in zip(_BOWL_COLS[:4], vals):
            _txt(d, (cx, cy), v, _f(_NAME, 24), WHITE, "mm", shadow=False)
        wk = b.get("wickets", 0)
        d.rounded_rectangle([1066, y0 + 6, 1134, y1 - 6], radius=10, fill=GOLD if star else (52, 60, 76))
        _txt(d, (1100, cy + 1), str(wk), _f(_NAME, 30), (14, 14, 18) if star else WHITE, "mm", shadow=False)
        for k in range(min(wk, 6)):    # one ball per wicket
            px = 1160 + k * 26
            d.ellipse([px, cy - 9, px + 18, cy + 9], fill=GOLD if star else AMBER)
            d.arc([px + 3, cy - 9, px + 15, cy + 9], 250, 290, fill=(255, 255, 255), width=2)
        ec = _econ_col(econ)
        d.rounded_rectangle([1350, y0 + 8, 1450, y1 - 8], radius=12, outline=ec, width=2)
        _txt(d, (1400, cy + 1), f"{econ:.2f}" if balls else "-", _f(_NAME, 22), ec, "mm", shadow=False)

    # fall of wickets as a run line: 0 ... total, a marker at every wicket
    fy0 = top + 8 * rh + 14
    _panel(im, (48, fy0, 1488, 922), radius=16, alpha=14)
    _txt(d, (76, fy0 + 30), "FALL OF WICKETS", _f(_LAB, 26), AMBER, shadow=False)
    total = max(1, int(data.get("runs", 0)))
    fow = (data.get("fow") or [])[:10]
    lx0, lx1, ly = 110, 1440, fy0 + 126
    d.line([lx0, ly, lx1, ly], fill=(60, 66, 80), width=6)
    d.line([lx0, ly, lx1, ly], fill=AMBER, width=6)
    _txt(d, (lx0 - 18, ly), "0", _f(_LAB, 22), MUTED, "rm", shadow=False)
    _txt(d, (lx1 + 14, ly), str(data.get("runs", 0)), _f(_LAB, 22), MUTED, "lm", shadow=False)
    xs = [lx0 + (lx1 - lx0) * min(1, w["score"] / total) for w in fow]
    # each label goes to the first lane where it does not touch the previous label
    lanes = [ly - 58, ly + 42, ly + 84]          # above, below, further below
    last_in_lane = [-999.0] * len(lanes)
    for k, (w, x) in enumerate(zip(fow, xs)):
        lane = next((j for j in range(len(lanes)) if x - last_in_lane[j] >= 118), None)
        if lane is None:
            lane = min(range(len(lanes)), key=lambda j: last_in_lane[j])
        last_in_lane[lane] = x
        ty = lanes[lane]
        up = ty < ly
        d.line([x, ly + (-13 if up else 13), x, ty + (24 if up else -12)], fill=(110, 84, 44), width=1)
        _txt(d, (x, ty), str(w["score"]), _f(_NAME, 22), WHITE, "mm", shadow=False)
        nm = w["name"].split()[-1].upper() if w.get("name") else ""
        sub = f"{nm} {w.get('over', '')}" if nm else f"{w.get('over', '')} OV"
        _txt(d, (x, ty + 20), sub, _fit(d, sub, _LAB, 16, 112, 10), MUTED, "mm", shadow=False)
    for k, x in enumerate(xs):                   # markers on top of the lines
        d.ellipse([x - 12, ly - 12, x + 12, ly + 12], fill=(20, 22, 28), outline=AMBER, width=3)
        d.text((x, ly), str(k + 1), font=_f(_LAB, 17), fill=WHITE, anchor="mm")
    _footer(im, d, data, AMBER)
    return _png(im)


def _themed(fn, data: dict) -> bytes:
    """Draw with the match's rcsb theme colours (data["theme"] = (style, image)), else the normal look."""
    global _THEME, WHITE, MUTED, FAINT, GOLD, TEAL, AMBER
    theme = data.get("theme")
    pal = None
    if theme and theme[0] and theme[1]:
        import match_theme
        pal = match_theme.palette(theme[0])
    if not pal:
        return fn(data)
    keep = (WHITE, MUTED, FAINT, GOLD, TEAL, AMBER)
    _THEME = theme
    WHITE, GOLD, TEAL, AMBER = pal["text"], pal["hi"], pal["bat"], pal["bowl"]
    MUTED = pal["sub"]
    FAINT = tuple(int(c * 0.72) for c in pal["sub"])
    try:
        return fn(data)
    except Exception as e:
        print(f"[scorecard] theme skipped: {type(e).__name__}: {e}")
        _THEME = None
        WHITE, MUTED, FAINT, GOLD, TEAL, AMBER = keep
        return fn(data)
    finally:
        _THEME = None
        WHITE, MUTED, FAINT, GOLD, TEAL, AMBER = keep


def batting_card(data: dict) -> bytes:
    return _themed(_batting_card, data)


def bowling_card(data: dict) -> bytes:
    return _themed(_bowling_card, data)


SAMPLE = {
    "team": "GoneWithTheWind", "bowling_team": "Sora", "innings": 1, "venue": "Bay Oval",
    "match_id": 211347, "runs": 186, "wickets": 9, "overs": "20.0", "max_overs": 20, "extras": 2,
    "target_line": "Sora need 187 off 120 balls  ·  RRR 9.35",
    "batters": [
        {"name": "Sachin Tendulkar", "runs": 69, "balls": 29, "fours": 7, "sixes": 4, "how": "c Kohli b Jacks"},
        {"name": "Sunil Gavaskar", "runs": 8, "balls": 4, "fours": 2, "sixes": 0, "how": "c Zampa b Cummins"},
        {"name": "Virat Kohli", "runs": 0, "balls": 2, "fours": 0, "sixes": 0, "how": "c Kohli b Jacks"},
        {"name": "Shubman Gill", "runs": 14, "balls": 9, "fours": 2, "sixes": 0, "how": "c Hazlewood b Jacks"},
        {"name": "K Sangakkara", "runs": 32, "balls": 16, "fours": 3, "sixes": 2, "how": "c de Villiers b Stokes"},
        {"name": "Jacques Kallis", "runs": 3, "balls": 4, "fours": 0, "sixes": 0, "how": "lbw b Hazlewood"},
        {"name": "Don Bradman", "runs": 7, "balls": 7, "fours": 1, "sixes": 0, "how": "c Sharma b Green", "impact": True},
        {"name": "Zen Zhu", "runs": 18, "balls": 9, "fours": 1, "sixes": 1, "how": "b Jacks"},
        {"name": "Shaun Pollock", "runs": 27, "balls": 17, "fours": 2, "sixes": 1, "how": "st de Villiers b Zampa"},
        {"name": "Mitchell Starc", "runs": 3, "balls": 4, "fours": 0, "sixes": 0, "how": "not out"},
        {"name": "Shane Warne", "runs": 3, "balls": 2, "fours": 0, "sixes": 0, "how": "not out"},
    ],
    "bowlers": [
        {"name": "Josh Hazlewood", "balls": 18, "maidens": 0, "dots": 3, "runs": 28, "wickets": 1},
        {"name": "Pat Cummins", "balls": 18, "maidens": 0, "dots": 5, "runs": 40, "wickets": 1},
        {"name": "Will Jacks", "balls": 24, "maidens": 1, "dots": 7, "runs": 27, "wickets": 4},
        {"name": "Ben Stokes", "balls": 18, "maidens": 0, "dots": 3, "runs": 39, "wickets": 1},
        {"name": "Adam Zampa", "balls": 18, "maidens": 0, "dots": 2, "runs": 23, "wickets": 1},
        {"name": "Cameron Green", "balls": 24, "maidens": 0, "dots": 7, "runs": 29, "wickets": 1},
    ],
    "fow": [{"score": 22, "over": "2.4", "name": "Gavaskar"}, {"score": 25, "over": "3.1", "name": "Kohli"},
            {"score": 45, "over": "5.2", "name": "Gill"}, {"score": 117, "over": "11.3", "name": "Tendulkar"},
            {"score": 125, "over": "12.4", "name": "Sangakkara"}, {"score": 131, "over": "13.5", "name": "Kallis"},
            {"score": 135, "over": "14.2", "name": "Bradman"}, {"score": 175, "over": "18.1", "name": "Zhu"},
            {"score": 182, "over": "19.2", "name": "Pollock"}],
}

if __name__ == "__main__":
    Path("scorecard_batting_preview.png").write_bytes(batting_card(SAMPLE))
    Path("scorecard_bowling_preview.png").write_bytes(bowling_card(SAMPLE))
    print("saved")
