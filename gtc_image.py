"""Images for Guess the Cricketer: the round picture and the leaderboard. Pure Pillow.

    round_image(fg, round_no, total)  -> BytesIO (JPEG, 1080x1080)
    board_image(title, subtitle, rows) -> BytesIO (PNG)  rows: [{"name", "avatar" (bytes|None), "points",
                                                                   "wins", "rcp", "badge" (str|None)}]
"""
from __future__ import annotations

import io
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

_HERE = Path(__file__).parent
_NUM, _LAB, _NAME = _HERE / "display_bold.ttf", _HERE / "label.ttf", _HERE / "name_font.ttf"
S = 2                                   # draw at 2x, scale down at the end

BG_TOP = (4, 34, 26)
BG_BOT = (1, 12, 9)
EMERALD = (51, 214, 143)
MINT = (160, 255, 205)
GOLD = (255, 206, 92)
GOLD_DEEP = (190, 128, 24)
WHITE = (240, 248, 244)
MUTED = (150, 196, 176)
SILVER = (214, 222, 232)
BRONZE = (214, 140, 82)


def _f(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def _fit(path: Path, text: str, max_w: int, start: int, min_size: int = 20) -> ImageFont.FreeTypeFont:
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    size = start
    while size > min_size:
        f = _f(path, size)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _f(path, min_size)


def _vgrad(w: int, h: int, top, bot) -> Image.Image:
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        col.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return col.resize((w, h))


def _glow(img: Image.Image, cx, cy, r, color, strength=1.0):
    g = Image.new("L", img.size, 0)
    ImageDraw.Draw(g).ellipse((cx - r, cy - r, cx + r, cy + r), fill=int(255 * strength))
    g = g.filter(ImageFilter.GaussianBlur(r * 0.45))
    img.paste(Image.new("RGB", img.size, color), (0, 0), g.point(lambda v: int(v * 0.55)))


def _logo():
    try:
        import card_full
        return card_full._brand_logo()
    except Exception:
        return None


def _background(w: int, h: int, seed: int = 7) -> Image.Image:
    img = _vgrad(w, h, BG_TOP, BG_BOT)
    _glow(img, w * 0.5, h * 0.42, h * 0.38, (24, 140, 92), 0.9)
    _glow(img, w * 0.92, h * 0.02, h * 0.22, (90, 200, 140), 0.5)
    _glow(img, w * 0.06, h * 0.02, h * 0.18, (40, 130, 90), 0.4)
    d = ImageDraw.Draw(img)
    # thin diagonal gold streaks, top-right and bottom-left
    for off, a in ((0, 150), (26 * S, 90), (52 * S, 50)):
        lyr = Image.new("L", img.size, 0)
        ImageDraw.Draw(lyr).line([(w * 0.70 + off, -10), (w + 10, h * 0.20 + off * 0.4)], fill=a, width=3 * S)
        ImageDraw.Draw(lyr).line([(-10, h * 0.80 - off * 0.4), (w * 0.30 - off, h + 10)], fill=a, width=3 * S)
        img.paste(Image.new("RGB", img.size, GOLD), (0, 0), lyr.filter(ImageFilter.GaussianBlur(1)))
    # faint hexagon mesh
    mesh = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mesh)
    R = 42 * S
    for row in range(-1, int(h / (R * 1.5)) + 2):
        for col in range(-1, int(w / (R * 1.732)) + 2):
            cx = col * R * 1.732 + (R * 0.866 if row % 2 else 0)
            cy = row * R * 1.5
            pts = [(cx + R * math.cos(math.radians(30 + 60 * k)), cy + R * math.sin(math.radians(30 + 60 * k)))
                   for k in range(6)]
            md.line(pts + [pts[0]], fill=255, width=S)
    img.paste(Image.new("RGB", img.size, MINT), (0, 0), mesh.point(lambda v: int(v * 0.05)))
    # sparkles
    rnd = random.Random(seed)
    for _ in range(60):
        x, y, r = rnd.uniform(0, w), rnd.uniform(0, h * 0.75), rnd.uniform(1, 2.6) * S
        d.ellipse((x - r, y - r, x + r, y + r), fill=(200, 255, 225))
    return img


def _pill(d: ImageDraw.ImageDraw, box, fill, outline=None, width=2):
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=(y1 - y0) // 2, fill=fill, outline=outline, width=width)


def _slant(d: ImageDraw.ImageDraw, box, fill, slant=16):
    x0, y0, x1, y1 = box
    d.polygon([(x0 + slant, y0), (x1, y0), (x1 - slant, y1), (x0, y1)], fill=fill)


def _trim(fg: Image.Image) -> Image.Image:
    bb = fg.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    return fg.crop(bb) if bb else fg


# ── round picture ─────────────────────────────────────────────────────────────
def round_image(fg: Image.Image, round_no: int, total: int) -> io.BytesIO:
    W = H = 1080
    w, h = W * S, H * S
    img = _background(w, h, seed=round_no)
    d = ImageDraw.Draw(img)

    # big "?" behind the player
    q = Image.new("L", img.size, 0)
    ImageDraw.Draw(q).text((w // 2, int(h * 0.47)), "?", font=_f(_NUM, 880 * S), fill=255, anchor="mm")
    img.paste(Image.new("RGB", img.size, (30, 120, 84)), (0, 0), q.point(lambda v: int(v * 0.45)))

    # player: fill the space between the header and the bottom bar, centred
    p = _trim(fg.convert("RGBA"))
    top, bottom = int(170 * S), int(H * S - 150 * S)
    k = min((bottom - top) / p.height, (w * 0.86) / p.width)
    p = p.resize((max(1, int(p.width * k)), max(1, int(p.height * k))), Image.LANCZOS)
    px, py = (w - p.width) // 2, bottom - p.height
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 170), (px, py + 14 * S), p.getchannel("A"))
    img.paste(Image.new("RGB", img.size, (0, 0, 0)), (0, 0), sh.getchannel("A").filter(ImageFilter.GaussianBlur(18 * S)))
    rim = p.getchannel("A").filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(10 * S))
    img.paste(Image.new("RGB", p.size, (120, 255, 190)), (px, py), rim.point(lambda v: int(v * 0.35)))
    img.paste(p.convert("RGB"), (px, py), p.getchannel("A"))
    d = ImageDraw.Draw(img)

    # header: logo + title + round pill
    lg = _logo()
    x = 48 * S
    if lg is not None:
        l = lg.resize((92 * S, 92 * S), Image.LANCZOS)
        ring = Image.new("L", (l.width + 10 * S, l.height + 10 * S), 0)
        ImageDraw.Draw(ring).ellipse((0, 0, ring.width - 1, ring.height - 1), fill=255)
        img.paste((255, 255, 255), (x - 5 * S, 40 * S), ring)
        img.paste(l, (x, 45 * S), l)
        x += 116 * S
    d.text((x, 62 * S), "GUESS THE", font=_f(_LAB, 34 * S), fill=MUTED, anchor="lm")
    d.text((x - 3 * S, 108 * S), "CRICKETER", font=_f(_NUM, 66 * S), fill=WHITE, anchor="lm")
    rt = f"ROUND {round_no} OF {total}"
    rf = _f(_NUM, 40 * S)
    rw = d.textlength(rt, font=rf)
    _slant(d, (w - 48 * S - rw - 70 * S, 52 * S, w - 48 * S, 116 * S), GOLD, 18 * S)
    d.text((w - 48 * S - (rw + 70 * S) / 2, 85 * S), rt, font=rf, fill=(40, 24, 0), anchor="mm")

    # progress dots under the pill
    dx = w - 48 * S - 22 * S
    for i in range(total, 0, -1):
        col = GOLD if i < round_no else (WHITE if i == round_no else (40, 80, 64))
        d.ellipse((dx - 9 * S, 136 * S, dx + 9 * S, 154 * S), fill=col)
        dx -= 30 * S

    # bottom bar
    bar = Image.new("RGBA", (w, 130 * S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    bd.rectangle((0, 0, w, 130 * S), fill=(2, 20, 15, 235))
    bd.line((0, 0, w, 0), fill=GOLD + (255,), width=4 * S)
    img.paste(bar.convert("RGB"), (0, h - 130 * S), bar.getchannel("A"))
    d = ImageDraw.Draw(img)
    d.text((w // 2, h - 82 * S), "TYPE THE FULL NAME IN CHAT", font=_f(_NUM, 46 * S), fill=WHITE, anchor="mm")
    d.text((w // 2, h - 36 * S), "First correct answer wins the point", font=_f(_LAB, 28 * S), fill=MINT, anchor="mm")

    out = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    return buf


# ── leaderboard ───────────────────────────────────────────────────────────────
def _avatar(data: bytes | None, size: int, name: str) -> Image.Image:
    av = None
    if data:
        try:
            av = Image.open(io.BytesIO(data)).convert("RGB").resize((size, size), Image.LANCZOS)
        except Exception:
            av = None
    if av is None:
        av = _vgrad(size, size, (24, 120, 84), (6, 50, 36))
        ImageDraw.Draw(av).text((size // 2, size // 2), (name[:1] or "?").upper(), font=_f(_NUM, int(size * 0.55)),
                                fill=WHITE, anchor="mm")
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(av, (0, 0), m)
    return out


def board_image(title: str, subtitle: str, rows: list[dict], footer: str = "") -> io.BytesIO:
    """The title font has no apostrophe or slash: keep titles to letters, digits and spaces."""
    title = "".join(ch for ch in title if ch.isalnum() or ch == " ")
    W = 1080
    n = max(1, len(rows))
    podium = rows[:3]
    rest = rows[3:]
    H = 300 + (420 if podium else 0) + len(rest) * 96 + 120
    w, h = W * S, H * S
    img = _background(w, h, seed=3)
    d = ImageDraw.Draw(img)

    # header
    lg = _logo()
    if lg is not None:
        l = lg.resize((84 * S, 84 * S), Image.LANCZOS)
        img.paste(l, (w // 2 - 42 * S, 36 * S), l)
    d.text((w // 2, 168 * S), title.upper(), font=_fit(_NUM, title.upper(), int(980 * S), 72 * S), fill=WHITE, anchor="mm")
    d.text((w // 2, 226 * S), subtitle, font=_fit(_LAB, subtitle, int(980 * S), 32 * S), fill=MINT, anchor="mm")
    d.line((w // 2 - 160 * S, 262 * S, w // 2 + 160 * S, 262 * S), fill=GOLD, width=3 * S)

    y0 = 300 * S
    if not rows:
        d.text((w // 2, y0 + 80 * S), "No points yet", font=_f(_NUM, 52 * S), fill=MUTED, anchor="mm")
    # podium: 2nd | 1st | 3rd
    if podium:
        order = [(1, podium[1] if len(podium) > 1 else None), (0, podium[0]), (2, podium[2] if len(podium) > 2 else None)]
        cols = [w * 0.20, w * 0.50, w * 0.80]
        colors = [GOLD, SILVER, BRONZE]
        for slot, (rank_i, r) in enumerate(order):
            if r is None:
                continue
            cx = cols[slot]
            big = rank_i == 0
            av_size = (190 if big else 150) * S
            top = y0 + (0 if big else 50 * S)
            ring_col = colors[rank_i]
            ring = Image.new("L", (av_size + 16 * S, av_size + 16 * S), 0)
            ImageDraw.Draw(ring).ellipse((0, 0, ring.width - 1, ring.height - 1), fill=255)
            glow = ring.filter(ImageFilter.GaussianBlur(14 * S))
            img.paste(ring_col, (int(cx - ring.width / 2), top), glow.point(lambda v: int(v * 0.6)))
            img.paste(ring_col, (int(cx - ring.width / 2), top), ring)
            av = _avatar(r.get("avatar"), av_size, r["name"])
            img.paste(av, (int(cx - av_size / 2), top + 8 * S), av)
            d = ImageDraw.Draw(img)
            # rank badge
            by = top + av_size + 2 * S
            d.ellipse((cx - 26 * S, by - 26 * S, cx + 26 * S, by + 26 * S), fill=ring_col, outline=(20, 20, 20), width=2 * S)
            d.text((cx, by), str(rank_i + 1), font=_f(_NUM, 38 * S), fill=(30, 20, 0), anchor="mm")
            ny = by + 56 * S
            nm = r["name"]
            d.text((cx, ny), nm, font=_fit(_NAME, nm, int(300 * S), (36 if big else 30) * S, 16 * S), fill=WHITE, anchor="mm")
            d.text((cx, ny + 50 * S), f"{r['points']} PTS", font=_f(_NUM, (52 if big else 42) * S), fill=ring_col, anchor="mm")
            wn = int(r.get("wins", 0))
            extra = f"{wn} win" + ("" if wn == 1 else "s")
            if r.get("rcp"):
                extra += f"  ·  {r['rcp']} RCP"
            d.text((cx, ny + 94 * S), extra, font=_f(_LAB, 24 * S), fill=MUTED, anchor="mm")
        y0 += 420 * S

    # rows 4+
    for i, r in enumerate(rest, start=4):
        ry = y0 + (i - 4) * 96 * S
        card = Image.new("RGBA", (w - 120 * S, 80 * S), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, card.width - 1, card.height - 1), radius=18 * S, fill=(8, 44, 32, 230),
                             outline=(60, 140, 104, 255), width=2 * S)
        img.paste(card.convert("RGB"), (60 * S, ry), card.getchannel("A"))
        d = ImageDraw.Draw(img)
        d.text((110 * S, ry + 40 * S), str(i), font=_f(_NUM, 40 * S), fill=MUTED, anchor="mm")
        av = _avatar(r.get("avatar"), 56 * S, r["name"])
        img.paste(av, (160 * S, ry + 12 * S), av)
        d.text((236 * S, ry + 40 * S), r["name"], font=_fit(_NAME, r["name"], int(480 * S), 30 * S, 16 * S), fill=WHITE, anchor="lm")
        right = w - 90 * S
        d.text((right, ry + 30 * S), f"{r['points']} PTS", font=_f(_NUM, 38 * S), fill=GOLD, anchor="rm")
        wn = int(r.get("wins", 0))
        sub = f"{wn} win" + ("" if wn == 1 else "s") + (f" · {r['rcp']} RCP" if r.get("rcp") else "")
        d.text((right, ry + 60 * S), sub, font=_f(_LAB, 22 * S), fill=MUTED, anchor="rm")

    if footer:
        d.text((w // 2, h - 60 * S), footer, font=_fit(_LAB, footer, int(980 * S), 28 * S), fill=MUTED, anchor="mm")

    out = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
