"""Team collage image (rcovr / rcxi image, rcstarterpack): the 11 player cards in a
4-3-4 grid on a stadium-night backdrop drawn in code.

    header   team name (glass bar), Rush Cricket logo in the middle, team OVR in gold
    cards    batting order number under every card, C badge on the captain, WK on keepers
    footer   average OVR of batters / all-rounders / keepers / bowlers + team chemistry

The backdrop (gradient, floodlights, faint cricket ground, light under every row,
vignette) is drawn once and cached. Only the logo is taken from starter_bg.png.
Uses card_cache's small display copies so it is fast. Pure Pillow, no Discord code.
"""
from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import card_cache

_HERE = Path(__file__).parent
LOGO_FILE = _HERE / "starter_bg.png"  # only the round Rush Cricket logo is cut out of it
FILENAME = "starterpack.jpg"          # embeds must use attachment://starterpack.jpg

W = 1254
MARGIN = 30
GAP = 12
ROWS = (4, 3, 4)                      # cards per row
TOP = 262                             # first card row
FOOT = 96                             # stats strip height
CARD_W = (W - 2 * MARGIN - (ROWS[0] - 1) * GAP) // ROWS[0]
CARD_H = round(CARD_W * 1517 / 1037)  # card PNG proportions
H = TOP + len(ROWS) * CARD_H + (len(ROWS) - 1) * GAP + 28 + FOOT

GOLD = (232, 196, 110)
WHITE = (255, 255, 255)
MUTED = (165, 175, 205)
LOGO_CX, LOGO_CY, LOGO_R = 627, 117, 111   # logo inside starter_bg.png scaled to 1254 wide


def _font(size: int, name: str = "display_bold.ttf") -> ImageFont.FreeTypeFont:
    for n in (name, "display_bold.ttf", "label.ttf", "name_font.ttf"):
        p = _HERE / n
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                pass
    return ImageFont.load_default()


def _label_font(size: int):
    return _font(size, "label.ttf")


def _fit_font(d: ImageDraw.ImageDraw, text: str, max_w: int, size: int) -> ImageFont.FreeTypeFont:
    f = _font(size)
    while d.textlength(text, font=f) > max_w and f.size > 22:
        f = _font(f.size - 4)
    return f


def _positions() -> list[tuple[int, int]]:
    out, y = [], TOP
    for n in ROWS:
        x = (W - (n * CARD_W + (n - 1) * GAP)) // 2
        for _ in range(n):
            out.append((x, y))
            x += CARD_W + GAP
        y += CARD_H + GAP
    return out


def _load_card(path: str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        return card_cache.load_rgba(path)   # RGBA: Style 2 cards keep their see-through corners
    except Exception as e:
        print(f"[squad_image] could not load {path}: {e}")
        return None


@lru_cache(maxsize=1)
def _logo() -> Image.Image | None:
    try:
        im = Image.open(LOGO_FILE).convert("RGB")
        im = im.resize((W, round(im.height * W / im.width)), Image.LANCZOS)
        r = LOGO_R
        c = im.crop((LOGO_CX - r, LOGO_CY - r, LOGO_CX + r, LOGO_CY + r)).convert("RGBA")
        m = Image.new("L", (r * 8, r * 8), 0)
        ImageDraw.Draw(m).ellipse((0, 0, r * 8 - 1, r * 8 - 1), fill=255)
        c.putalpha(m.resize((2 * r, 2 * r), Image.LANCZOS))
        return c
    except Exception as e:
        print(f"[squad_image] logo unavailable: {e}")
        return None


@lru_cache(maxsize=1)
def _backdrop() -> Image.Image:
    """Stadium-night backdrop + header bar + logo + stats strip frame (no text). Cached."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        if t < 0.5:
            a = t / 0.5
            c = (int(10 + 4 * a), int(16 + 10 * a), int(38 + 20 * a))
        else:
            a = (t - 0.5) / 0.5
            c = (int(14 - 8 * a), int(26 - 16 * a), int(58 - 40 * a))
        d.line((0, y, W, y), fill=c)
    img = img.convert("RGBA")

    # floodlight glow + light beams from both top corners
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    for sx in (-60, W + 60):
        for k, ang in enumerate((62, 70, 78)):
            a = math.radians(ang if sx < 0 else 180 - ang)
            ln, wd = H * 1.1, 110 + k * 40
            ex, ey = sx + ln * math.cos(a), -40 + ln * math.sin(a)
            px, py = -math.sin(a) * wd, math.cos(a) * wd
            ld.polygon([(sx, -40), (ex + px, ey + py), (ex - px, ey - py)], fill=(150, 190, 255, 10))
        ld.ellipse((sx - 300, -300, sx + 300, 300), fill=(170, 205, 255, 95))
    img.alpha_composite(lay.filter(ImageFilter.GaussianBlur(40)))

    # floodlight lamps
    lamps = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    lm = ImageDraw.Draw(lamps)
    for px0 in (40, W - 40 - 132):
        for r in range(3):
            for c in range(7):
                x, y = px0 + c * 22, 16 + r * 17
                lm.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(235, 245, 255, 230))
    halo = lamps.filter(ImageFilter.GaussianBlur(10))
    img.alpha_composite(halo)
    img.alpha_composite(halo)
    img.alpha_composite(lamps)

    # faint cricket ground seen from above: boundary, 30-yard circle, pitch
    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    cx, cy = W / 2, H * 0.55
    for rx, ry, al in ((W * 0.62, H * 0.42, 26), (W * 0.40, H * 0.27, 22)):
        rd.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=(150, 190, 255, al), width=3)
    rd.rectangle((cx - 34, cy - 150, cx + 34, cy + 150), outline=(150, 190, 255, 24), width=3)
    for yy in (cy - 120, cy + 120):
        rd.line((cx - 60, yy, cx + 60, yy), fill=(150, 190, 255, 24), width=3)
    img.alpha_composite(ring.filter(ImageFilter.GaussianBlur(1)))

    # fine diagonal texture
    tex = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tex)
    for x in range(-H, W, 14):
        td.line((x, H, x + H, 0), fill=(255, 255, 255, 5), width=1)
    img.alpha_composite(tex)

    # light "stage" under every card row
    fl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fl)
    y = TOP
    for n in ROWS:
        half = (n * CARD_W + (n - 1) * GAP) / 2 + 40
        fy = y + CARD_H - 2
        fd.ellipse((W / 2 - half, fy - 30, W / 2 + half, fy + 30), fill=(110, 160, 255, 150))
        fd.ellipse((W / 2 - half * 0.7, fy - 9, W / 2 + half * 0.7, fy + 9), fill=(215, 232, 255, 220))
        y += CARD_H + GAP
    img.alpha_composite(fl.filter(ImageFilter.GaussianBlur(22)))

    # vignette
    v = Image.new("L", (W, H), 0)
    ImageDraw.Draw(v).ellipse((-W * 0.25, -H * 0.15, W * 1.25, H * 1.15), fill=255)
    v = v.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 170))
    dark.putalpha(ImageChops.invert(v).point(lambda p: int(p * 0.75)))
    img.alpha_composite(dark)

    # header glass bar + gold line, logo with a soft red glow
    bar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    bd.rounded_rectangle((30, 78, W - 30, 158), 18, fill=(8, 12, 30, 175))
    fy = H - FOOT + 8
    bd.rounded_rectangle((30, fy, W - 30, H - 18), 18, fill=(8, 12, 30, 190))
    img.alpha_composite(bar)
    d = ImageDraw.Draw(img)
    d.line((48, 158, W - 48, 158), fill=GOLD, width=2)
    d.line((48, fy, W - 48, fy), fill=GOLD, width=2)
    logo = _logo()
    if logo is not None:
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((W / 2 - 130, -10, W / 2 + 130, 250), fill=(255, 90, 70, 70))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(30)))
        img.alpha_composite(logo, (int(W / 2 - logo.width / 2), 6))
    return img


def _role(card) -> str:
    try:
        import card_db
        return card_db.effective_role(card) or ""
    except Exception:
        return str(card["role"] or "") if "role" in card.keys() else ""


def _chemistry(cards) -> int | None:
    try:
        import squad_logic
        return int(squad_logic.chem_of(cards))
    except Exception:
        return None


def _badge(d, x, y, text, fill, ink):
    d.ellipse((x - 24, y - 24, x + 24, y + 24), fill=fill, outline=WHITE, width=3)
    d.text((x, y + 1), text, font=_font(26 if len(text) == 1 else 22), fill=ink, anchor="mm")


def build_team_image(cards: list, team_name: str, team_ovr: int, captain_key: str | None = None) -> io.BytesIO | None:
    """JPEG collage of up to 11 cards (in batting order). None if no card image could be loaded."""
    imgs = [_load_card(c["image_path"]) for c in cards]
    if not any(imgs):
        return None

    canvas = _backdrop().copy()
    d = ImageDraw.Draw(canvas)

    # header text
    name = (team_name or "TEAM").upper()
    d.text((60, 118), name, font=_fit_font(d, name, 440, 52), fill=WHITE, anchor="lm")
    d.text((60, 172), "PLAYING XI", font=_label_font(20), fill=MUTED, anchor="lt")
    f = _font(52)
    ovr = str(team_ovr)
    tw = d.textlength(ovr, font=f)
    d.text((W - 60, 118), ovr, font=f, fill=GOLD, anchor="rm")
    d.text((W - 60 - tw - 14, 124), "OVR", font=_label_font(26), fill=(200, 205, 225), anchor="rm")

    # card shadows (real card shape), then the cards
    pos = _positions()
    sized = [im.resize((CARD_W, CARD_H), Image.LANCZOS) if im is not None else None for im in imgs[:len(pos)]]
    shade = Image.new("L", canvas.size, 0)
    for im, (x, y) in zip(sized, pos):
        m = im.getchannel("A").point(lambda v: int(v * 0.65)) if im is not None else Image.new("L", (CARD_W, CARD_H), 150)
        shade.paste(m, (x + 5, y + 10), m)
    canvas.paste((0, 0, 0, 255), (0, 0), shade.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(canvas)
    for im, (x, y) in zip(sized, pos):
        if im is not None:
            canvas.alpha_composite(im, (x, y))
        else:
            d.rounded_rectangle([x, y, x + CARD_W, y + CARD_H], radius=10, outline=(80, 90, 110), width=2)
    d = ImageDraw.Draw(canvas)

    # batting order numbers, captain / keeper badges
    for i, (card, (x, y)) in enumerate(zip(cards, pos)):
        cx, by = x + CARD_W / 2, y + CARD_H + 2
        d.rounded_rectangle((cx - 20, by - 14, cx + 20, by + 12), 12, fill=(10, 14, 32), outline=(90, 110, 170), width=2)
        d.text((cx, by - 1), str(i + 1), font=_font(20), fill=(225, 230, 245), anchor="mm")
        bx, byy = x + CARD_W - 30, y + 44
        if captain_key and card["playername_key"] == captain_key:
            _badge(d, bx, byy, "C", GOLD, (30, 20, 10))
            byy += 56
        if _role(card) == "WK":
            _badge(d, bx, byy, "WK", (60, 140, 230), WHITE)

    # footer: average OVR per role + chemistry
    groups = {"BATTING": "BAT", "ALL-ROUND": "AR", "KEEPER": "WK", "BOWLING": "BOWL"}
    stats = []
    for label, role in groups.items():
        ovrs = [int(c["ovr"]) for c in cards if _role(c) == role]
        stats.append((label, str(round(sum(ovrs) / len(ovrs))) if ovrs else "-"))
    chem = _chemistry(cards)
    if chem is not None:
        stats.append(("CHEMISTRY", str(chem)))
    fy = H - FOOT + 8
    colw = (W - 60) / len(stats)
    for i, (label, val) in enumerate(stats):
        cx = 30 + colw * i + colw / 2
        d.text((cx, fy + 28), val, font=_font(34), fill=(120, 220, 170) if label == "CHEMISTRY" else GOLD, anchor="mm")
        d.text((cx, fy + 58), label, font=_label_font(17), fill=MUTED, anchor="mm")
        if i:
            d.line((30 + colw * i, fy + 14, 30 + colw * i, H - 32), fill=(60, 70, 110), width=1)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
    buf.seek(0)
    return buf
