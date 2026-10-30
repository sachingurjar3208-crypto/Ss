"""Leaderboard image for `rcranktop` (1200 wide PNG, drawn at 2x then scaled down for smooth edges).

generate_rank_top(players, season_text) -> PNG bytes

players = list of dicts, best first:
    {"name": str, "tier": "Gold", "stars": 120, "wins": 30, "losses": 10, "avatar": bytes | None}

Same look as rank_image.py (navy background, tier colours, the bot's own fonts):
  name_font.ttf  display_bold.ttf  label.ttf
Rank 1 gets a big gold "champion" card with a crown, ranks 2 and 3 get silver / bronze cards,
ranks 4+ are compact rows with a bar showing how close they are to rank 1.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

_HERE = Path(__file__).parent
_NAME, _NUM, _LAB = _HERE / "name_font.ttf", _HERE / "display_bold.ttf", _HERE / "label.ttf"

W, S = 1200, 2
FILENAME = "ranktop.png"

TIER_COLORS = {
    "Bronze":    (205, 127, 50),
    "Silver":    (200, 208, 220),
    "Gold":      (255, 200, 60),
    "Platinum":  (90, 200, 255),
    "Legendary": (255, 110, 40),
    "Ruby":      (235, 45, 85),
}
GOLD, SILVER, BRONZE = (255, 200, 60), (206, 214, 228), (205, 127, 50)
MEDAL = [GOLD, SILVER, BRONZE]
WHITE, DIM = (240, 244, 255), (150, 163, 198)


def _f(path: Path, size: float):
    try:
        return ImageFont.truetype(str(path), int(size * S))
    except Exception:
        try:
            return ImageFont.load_default(size=int(size * S))
        except TypeError:
            return ImageFont.load_default()


def _p(v):
    return int(round(v * S))


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rect(x0, y0, x1, y1):
    return [_p(x0), _p(y0), _p(x1), _p(y1)]


def _fit(d, text, path, size, maxw):
    while size > 12 and d.textlength(text, font=_f(path, size)) > _p(maxw):
        size -= 1
    return _f(path, size)


def _star_pts(cx, cy, r, rot=-90):
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.46
        a = math.radians(rot + i * 36)
        pts.append((_p(cx + rad * math.cos(a)), _p(cy + rad * math.sin(a))))
    return pts


def _star(d, cx, cy, r, color=GOLD):
    d.polygon(_star_pts(cx, cy, r), fill=color + (255,), outline=_mix(color, (0, 0, 0), 0.35) + (255,))


def _sparkle(d, cx, cy, r, color, alpha):
    pts = []
    for i in range(8):
        rad = r if i % 2 == 0 else r * 0.22
        a = math.radians(-90 + i * 45)
        pts.append((_p(cx + rad * math.cos(a)), _p(cy + rad * math.sin(a))))
    d.polygon(pts, fill=color + (alpha,))


def _crown(d, cx, cy, s=1.0):
    """Gold crown centred on (cx, cy)."""
    def pt(x, y):
        return (_p(cx + x * s), _p(cy + y * s))
    body = [pt(-40, 18), pt(-46, -22), pt(-21, -3), pt(0, -32), pt(21, -3), pt(46, -22), pt(40, 18)]
    d.polygon(body, fill=GOLD + (255,), outline=(150, 105, 10, 255))
    d.rectangle([pt(-40, 12)[0], pt(-40, 12)[1], pt(40, 24)[0], pt(40, 24)[1]], fill=(255, 220, 110, 255),
                outline=(150, 105, 10, 255))
    for x, y in ((-46, -22), (0, -32), (46, -22)):
        cxp, cyp = pt(x, y)
        r = _p(6 * s)
        d.ellipse([cxp - r, cyp - r, cxp + r, cyp + r], fill=(255, 245, 200, 255), outline=(150, 105, 10, 255))


def _avatar(img, d, cx, cy, r, color, avatar_bytes, initial, ring=5):
    rr = _p(r + ring + 3)
    d.ellipse([_p(cx) - rr, _p(cy) - rr, _p(cx) + rr, _p(cy) + rr], outline=color + (255,), width=_p(ring))
    d.ellipse([_p(cx) - _p(r + 2), _p(cy) - _p(r + 2), _p(cx) + _p(r + 2), _p(cy) + _p(r + 2)], fill=(10, 14, 30, 255))
    size = _p(r * 2)
    face = None
    if avatar_bytes:
        try:
            face = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((size, size), Image.LANCZOS)
        except Exception:
            face = None
    if face is None:
        face = Image.new("RGB", (size, size), _mix((30, 40, 78), color, 0.28))
        ImageDraw.Draw(face).text((size // 2, size // 2), (initial or "?")[:1].upper(), font=_f(_NAME, r * 1.15),
                                  fill=(255, 255, 255), anchor="mm")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    img.paste(face, (_p(cx) - size // 2, _p(cy) - size // 2), mask)


def _chip(d, x, y, text, color, size=20, h=34, anchor_left=True):
    """Small rounded tier tag. Returns its width."""
    font = _f(_LAB, size)
    tw = d.textlength(text.upper(), font=font) / S
    w = tw + 26
    x0 = x if anchor_left else x - w
    d.rounded_rectangle(_rect(x0, y, x0 + w, y + h), radius=_p(h / 2), fill=tuple(int(c * 0.22) for c in color) + (255,),
                        outline=color + (255,), width=_p(2))
    d.text((_p(x0 + w / 2), _p(y + h / 2 + 1)), text.upper(), font=font, fill=color, anchor="mm")
    return w


def _winrate(p):
    t = p["wins"] + p["losses"]
    return round(100 * p["wins"] / t) if t else 0


def _background(h, hero_rect):
    img = Image.new("RGB", (_p(W), _p(h)), (8, 12, 28))
    px = ImageDraw.Draw(img)
    top, bot = (17, 24, 50), (5, 8, 20)
    for y in range(_p(h)):
        px.line([(0, y), (_p(W), y)], fill=_mix(top, bot, y / _p(h)))
    glow = Image.new("RGB", img.size, (0, 0, 0))
    g = ImageDraw.Draw(glow)
    x0, y0, x1, y1 = hero_rect
    g.ellipse(_rect(x0 - 40, y0 - 90, x1 + 40, y1 + 80), fill=tuple(int(c * 0.30) for c in GOLD))
    g.ellipse(_rect(-200, h - 420, 380, h + 150), fill=(18, 26, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(_p(95)))
    img = ImageChops.add(img, glow)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for x in range(-_p(h), _p(W), _p(46)):
        od.line([(x, _p(h)), (x + _p(h), 0)], fill=(255, 255, 255, 6), width=_p(14))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


# ── pieces ───────────────────────────────────────────────────────────────────

def _hero(img, d, p, rect):
    x0, y0, x1, y1 = rect
    d.rounded_rectangle(_rect(x0, y0, x1, y1), radius=_p(30), fill=(30, 25, 12, 240), outline=GOLD + (255,), width=_p(3))
    d.rounded_rectangle(_rect(x0 + 8, y0 + 8, x1 - 8, y1 - 8), radius=_p(24), outline=GOLD + (70,), width=_p(1))
    # soft light on the left (behind the avatar) and diagonal sheen
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.ellipse(_rect(x0 - 40, y0 - 30, x0 + 380, y1 + 60), fill=GOLD + (46,))
    ov = ov.filter(ImageFilter.GaussianBlur(_p(50)))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(_rect(x0 + 2, y0 + 2, x1 - 2, y1 - 2), radius=_p(28), fill=255)
    img.paste(Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB"), (0, 0), mask)
    d = ImageDraw.Draw(img, "RGBA")
    # sparkles
    for sx, sy, sr, sa in ((x0 + 330, y0 + 40, 11, 200), (x0 + 440, y0 + 238, 7, 150), (x1 - 330, y0 + 36, 8, 170),
                           (x1 - 60, y0 + 56, 12, 210), (x1 - 150, y1 - 40, 6, 140), (x0 + 60, y1 - 38, 8, 160),
                           (x0 + 300, y1 - 56, 5, 120), (x1 - 420, y0 + 190, 5, 120)):
        _sparkle(d, sx, sy, sr, (255, 232, 150), sa)

    cy = (y0 + y1) / 2 + 22
    ax = x0 + 150
    _avatar(img, d, ax, cy, 82, GOLD, p.get("avatar"), p["name"], ring=7)
    _crown(d, ax, cy - 82 - 34, 1.2)

    nx = x0 + 300
    num = str(p["stars"])
    nf = _f(_NUM, 128)
    rx = x1 - 70
    tw = d.textlength(num, font=nf) / S
    name_w = max(200, (rx - tw - 44 - 34 - 24) - nx)          # stop before the star icon
    d.text((_p(nx), _p(y0 + 62)), "CHAMPION  -  RANK 1", font=_f(_LAB, 24), fill=GOLD + (255,), anchor="lm")
    d.text((_p(nx), _p(y0 + 124)), p["name"], font=_fit(d, p["name"], _NAME, 56, name_w), fill=WHITE + (255,), anchor="lm")
    tc = TIER_COLORS.get(p["tier"], GOLD)
    w = _chip(d, nx, y0 + 160, p["tier"], tc, size=24, h=40)
    d.text((_p(nx + w + 22), _p(y0 + 181)), f"{p['wins']}W   {p['losses']}L   {_winrate(p)}% WIN RATE",
           font=_f(_LAB, 28), fill=DIM + (255,), anchor="lm")

    # stars, big, on the right
    d.text((_p(rx), _p(cy - 14)), num, font=nf, fill=(255, 226, 120, 255), anchor="rm")
    _star(d, rx - tw - 44, cy - 14, 34)
    d.text((_p(rx), _p(cy + 62)), "STARS", font=_f(_LAB, 28), fill=GOLD + (230,), anchor="rm")


def _podium(img, d, p, rank, rect):
    x0, y0, x1, y1 = rect
    c = MEDAL[rank - 1]
    d.rounded_rectangle(_rect(x0, y0, x1, y1), radius=_p(24), fill=tuple(int(v * 0.13) for v in c) + (255,),
                        outline=c + (255,), width=_p(2))
    cy = (y0 + y1) / 2
    d.text((_p(x0 + 48), _p(cy + 4)), str(rank), font=_f(_NUM, 84), fill=c + (255,), anchor="mm")
    _avatar(img, d, x0 + 142, cy, 44, c, p.get("avatar"), p["name"], ring=4)
    nx = x0 + 214
    d.text((_p(nx), _p(y0 + 40)), p["name"], font=_fit(d, p["name"], _NAME, 30, (x1 - x0) - 214 - 150), fill=WHITE + (255,), anchor="lm")
    tc = TIER_COLORS.get(p["tier"], GOLD)
    _chip(d, nx, y0 + 62, p["tier"], tc, size=19, h=30)
    d.text((_p(nx), _p(y0 + 117)), f"{p['wins']}W   {p['losses']}L   {_winrate(p)}%", font=_f(_LAB, 25), fill=DIM + (255,), anchor="lm")
    num = str(p["stars"])
    nf = _f(_NUM, 62)
    rx = x1 - 62
    d.text((_p(rx), _p(cy - 6)), num, font=nf, fill=WHITE + (255,), anchor="rm")
    _star(d, rx + 28, cy - 6, 15, c)
    d.text((_p(rx + 14), _p(cy + 40)), "STARS", font=_f(_LAB, 20), fill=c + (230,), anchor="rm")


def _row(img, d, p, rank, rect, top_stars):
    x0, y0, x1, y1 = rect
    tc = TIER_COLORS.get(p["tier"], GOLD)
    d.rounded_rectangle(_rect(x0, y0, x1, y1), radius=_p(18), fill=(255, 255, 255, 12), outline=(255, 255, 255, 26), width=_p(1))
    ratio = max(0.10, min(1.0, p["stars"] / top_stars)) if top_stars else 0.1
    bar = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    bd.rounded_rectangle(_rect(x0, y0, x0 + (x1 - x0) * ratio, y1), radius=_p(18), fill=tc + (34,))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle(_rect(x0, y0, x1, y1), radius=_p(18), fill=255)
    comp = Image.alpha_composite(img.convert("RGBA"), bar).convert("RGB")
    img.paste(comp, (0, 0), m)
    d = ImageDraw.Draw(img, "RGBA")
    cy = (y0 + y1) / 2
    d.text((_p(x0 + 50), _p(cy + 2)), str(rank), font=_f(_NUM, 42), fill=(205, 214, 240, 255), anchor="mm")
    _avatar(img, d, x0 + 122, cy, 23, tc, p.get("avatar"), p["name"], ring=3)
    d.text((_p(x0 + 170), _p(cy)), p["name"], font=_fit(d, p["name"], _NAME, 28, 360), fill=WHITE + (255,), anchor="lm")
    _chip(d, x0 + 590, cy - 15, p["tier"], tc, size=19, h=30)
    d.text((_p(x0 + 770), _p(cy + 1)), f"{p['wins']}W   {p['losses']}L", font=_f(_LAB, 27), fill=DIM + (255,), anchor="lm")
    rx = x1 - 62
    d.text((_p(rx), _p(cy + 1)), str(p["stars"]), font=_f(_NUM, 46), fill=WHITE + (255,), anchor="rm")
    _star(d, rx + 30, cy, 14, GOLD)


def generate_rank_top(players: list[dict], season_text: str = "", footer: str = "rcrank for your own card   |   rcmp ranked to climb") -> bytes:
    n = len(players)
    hero_h, pod_h, row_h, gap = 262, 150, 68, 10
    y = 150
    hero_rect = (40, y, W - 40, y + hero_h)
    y += hero_h + 22
    pod_rects = []
    pods = players[1:3]
    if pods:
        if len(pods) == 1:
            pod_rects = [(40, y, W - 40, y + pod_h)]
        else:
            pod_rects = [(40, y, 592, y + pod_h), (608, y, W - 40, y + pod_h)]
        y += pod_h + 22
    row_rects = []
    for _ in players[3:]:
        row_rects.append((40, y, W - 40, y + row_h))
        y += row_h + gap
    h = y + (30 if row_rects else 8) + 40

    img = _background(h, hero_rect)
    d = ImageDraw.Draw(img, "RGBA")

    # header
    d.text((_p(52), _p(52)), "GLOBAL RANKED TOP 10", font=_f(_NAME, 44), fill=WHITE + (255,), anchor="lm")
    d.text((_p(54), _p(96)), "TOP PLAYERS BY STARS THIS SEASON", font=_f(_LAB, 26), fill=DIM + (255,), anchor="lm")
    if season_text:
        d.text((_p(W - 52), _p(96)), season_text.upper(), font=_f(_LAB, 26), fill=GOLD + (255,), anchor="rm")
    _star(d, W - 66, 50, 24)
    d.line([_p(52), _p(120), _p(W - 52), _p(120)], fill=(255, 255, 255, 28), width=_p(1))

    if n:
        _hero(img, d, players[0], hero_rect)
        d = ImageDraw.Draw(img, "RGBA")
    for i, (p, r) in enumerate(zip(pods, pod_rects)):
        _podium(img, d, p, i + 2, r)
    top = players[0]["stars"] if n else 1
    for i, (p, r) in enumerate(zip(players[3:], row_rects)):
        _row(img, d, p, i + 4, r, top)
        d = ImageDraw.Draw(img, "RGBA")

    d.text((_p(W / 2), _p(h - 26)), footer, font=_f(_LAB, 24), fill=(120, 133, 170, 255), anchor="mm")

    out = img.resize((W, int(round(h))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":      # python rank_top_image.py  ->  preview PNG with sample players
    sample = [
        ("Vikram", "Ruby", 214, 61, 12), ("Aarav", "Ruby", 187, 52, 16), ("Rohan", "Legendary", 163, 47, 19),
        ("Kabir", "Legendary", 131, 38, 17), ("Ishaan", "Legendary", 112, 33, 15), ("Dev", "Platinum", 98, 30, 18),
        ("Arjun", "Platinum", 84, 26, 17), ("Neel", "Platinum", 71, 23, 19), ("Yash", "Gold", 55, 19, 16),
        ("Sameer", "Gold", 43, 15, 14),
    ]
    pl = [{"name": n_, "tier": t, "stars": s_, "wins": w_, "losses": l_, "avatar": None} for n_, t, s_, w_, l_ in sample]
    Path("preview_ranktop.png").write_bytes(generate_rank_top(pl, "Season ends 30 Nov"))
    print("saved preview_ranktop.png")
