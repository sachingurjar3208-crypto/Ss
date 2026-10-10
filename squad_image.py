"""Team collage image for csstarterpack: the 11 player cards in a 4-3-4 grid
on the CricketStar backdrop (starter_bg.png), with the team name on the left
of the logo and the team OVR on the right. Pure Pillow, no Discord code.

Uses card_cache's small display copies (800px JPEG) so it is fast; the copy
is rebuilt automatically when a card's PNG changes (e.g. after /editcard),
and falls back to the full card PNG if a display copy can't be built.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import card_cache

_HERE = Path(__file__).parent
BG_FILE = _HERE / "starter_bg.png"   # 1254x1254 backdrop with the CricketStar logo at top-centre
FILENAME = "starterpack.jpg"          # embeds must use attachment://starterpack.jpg

CANVAS_W = 1254                       # = backdrop width, so it is never resized sideways
MARGIN = 30
GAP = 12
ROWS = (4, 3, 4)                      # cards per row

# Where things sit inside starter_bg.png (pixels, at its native 1254x1254)
LOGO_X0, LOGO_X1, LOGO_BOTTOM = 525, 730, 225
HEADER_CY = 118                       # vertical centre of the team-name / OVR text
STRETCH_FROM, STRETCH_TO = 280, 560   # plain dark-blue band that is stretched taller


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("display_bold.ttf", "label.ttf", "name_font.ttf"):
        p = _HERE / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                pass
    return ImageFont.load_default()


def _load_card(path: str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        data = card_cache._get_sync(path)
        if data:
            return Image.open(io.BytesIO(data)).convert("RGB")
        return Image.open(path).convert("RGB")
    except Exception as e:
        print(f"[squad_image] could not load {path}: {e}")
        return None


def _backdrop(height: int) -> Image.Image | None:
    """The CricketStar backdrop made `height` px tall by stretching only its plain
    dark-blue middle band (logo, floodlights and stadium keep their size).
    None if the file is missing."""
    try:
        bg = Image.open(BG_FILE).convert("RGB")
        if bg.width != CANVAS_W:
            bg = bg.resize((CANVAS_W, round(bg.height * CANVAS_W / bg.width)), Image.LANCZOS)
        extra = height - bg.height
        if extra <= 0:
            return bg.crop((0, 0, CANVAS_W, height))
        top = bg.crop((0, 0, CANVAS_W, STRETCH_FROM))
        mid = bg.crop((0, STRETCH_FROM, CANVAS_W, STRETCH_TO))
        bottom = bg.crop((0, STRETCH_TO, CANVAS_W, bg.height))
        mid = mid.resize((CANVAS_W, mid.height + extra), Image.BICUBIC)
        out = Image.new("RGB", (CANVAS_W, height))
        out.paste(top, (0, 0))
        out.paste(mid, (0, top.height))
        out.paste(bottom, (0, top.height + mid.height))
        return out
    except Exception as e:
        print(f"[squad_image] backdrop unavailable: {e}")
        return None


def _gradient(h: int) -> Image.Image:
    img = Image.new("RGB", (CANVAS_W, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        d.line([(0, y), (CANVAS_W, y)], fill=(int(8 + 10 * t), int(16 + 18 * t), int(40 + 30 * t)))
    return img


def _fit_font(d: ImageDraw.ImageDraw, text: str, max_w: int, size: int) -> ImageFont.FreeTypeFont:
    f = _font(size)
    while d.textlength(text, font=f) > max_w and f.size > 22:
        f = _font(f.size - 4)
    return f


def _shadow_text(d, xy, text, font, anchor):
    x, y = xy
    d.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0), anchor=anchor)
    d.text((x, y), text, font=font, fill=(255, 255, 255), anchor=anchor)


def build_team_image(cards: list, team_name: str, team_ovr: int) -> io.BytesIO | None:
    """JPEG collage of up to 11 cards. Returns None if no card image could be loaded."""
    imgs = [_load_card(c["image_path"]) for c in cards]
    if not any(imgs):
        return None

    card_w = (CANVAS_W - 2 * MARGIN - (ROWS[0] - 1) * GAP) // ROWS[0]
    card_h = round(card_w * 1517 / 1037)                 # card PNG proportions
    grid_top = LOGO_BOTTOM + 20
    grid_h = len(ROWS) * card_h + (len(ROWS) - 1) * GAP
    height = grid_top + grid_h + MARGIN

    canvas = _backdrop(height)
    has_bg = canvas is not None
    if not has_bg:
        canvas = _gradient(height)
    d = ImageDraw.Draw(canvas)

    # header: team name left of the logo, OVR right of it
    name = (team_name or "TEAM").upper()
    if has_bg:
        left_w = LOGO_X0 - 20 - (MARGIN + 10)
        right_x0 = LOGO_X1 + 20
    else:   # no logo/backdrop -> plain header bar
        d.rectangle([0, 0, CANVAS_W, LOGO_BOTTOM - 100], fill=(14, 20, 36))
        left_w, right_x0 = CANVAS_W // 2 - 40, CANVAS_W // 2 + 40
    _shadow_text(d, (MARGIN + 10, HEADER_CY), name, _fit_font(d, name, left_w, 56), "lm")
    ovr_txt = f"OVR: {team_ovr}"
    _shadow_text(d, (CANVAS_W - MARGIN - 10, HEADER_CY), ovr_txt,
                 _fit_font(d, ovr_txt, CANVAS_W - MARGIN - 10 - right_x0, 56), "rm")

    # soft shadows under the cards, then the cards (each row centred)
    positions = []
    y = grid_top
    for n in ROWS:
        row_w = n * card_w + (n - 1) * GAP
        x = (CANVAS_W - row_w) // 2
        for _ in range(n):
            positions.append((x, y))
            x += card_w + GAP
        y += card_h + GAP

    shade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for (x, y) in positions:
        sd.rectangle([x + 4, y + 8, x + card_w + 4, y + card_h + 8], fill=(0, 0, 0, 150))
    shade = shade.filter(ImageFilter.GaussianBlur(9))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shade).convert("RGB")
    d = ImageDraw.Draw(canvas)

    for img, (x, y) in zip(imgs, positions):
        if img is not None:
            canvas.paste(img.resize((card_w, card_h), Image.LANCZOS), (x, y))
        else:
            d.rounded_rectangle([x, y, x + card_w, y + card_h], radius=10,
                                outline=(80, 90, 110), width=2)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=88, optimize=True)
    buf.seek(0)
    return buf
