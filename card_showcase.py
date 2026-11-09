"""SHOWCASE card (s2_theme = "showcase"): your Style 2 shield with a WIDE ARTWORK BOX.

Same shield shape, gold border, name and 6 stats as the normal card. In the middle sits a
landscape box (16:9) that shows YOUR OWN edited artwork, uploaded with /showcase (16:9 works best,
e.g. 1920x1080). It is saved in showcase_art/<card>.png, so the card's normal player photo is NOT
changed (turn Showcase off and the card is the same as before). Above the box: OVR, role, flag, playstyles.
The card background is a blurred, darkened copy of the same artwork, so every card gets its own colours.
/foregroundfix still works on the box: size bigger/smaller zooms, left/right/up/down moves.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import card_image as ci
import card_style2 as c2
from card_arena import _font

S, CROP = c2.S, c2.CROP
BOX = (196, 428, 1084, 928)          # design coords of the artwork box (16:9)
GOLD = c2.GOLD[:3]
CREAM = (250, 240, 214)
ART_DIR = Path(__file__).parent / "showcase_art"
MAX_SIDE = 2400                      # uploads are shrunk to this (keeps files small)


def art_path(playername: str) -> Path:
    return ART_DIR / (str(playername).strip().lower().replace(" ", "_").replace("/", "_") + ".png")


def save_art(playername: str, data: bytes) -> Path:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if max(img.size) > MAX_SIDE:
        k = MAX_SIDE / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    ART_DIR.mkdir(exist_ok=True)
    path = art_path(playername)
    img.save(path, "PNG", optimize=True)
    return path


def load_art(playername: str):
    p = art_path(playername)
    try:
        return Image.open(p).convert("RGBA") if p.exists() else None
    except Exception:
        return None


def _cover(art: Image.Image, w: int, h: int, card_row=None, zoom_ok=True) -> Image.Image:
    art = art.convert("RGBA")
    zoom = max(0.3, int(ci._col(card_row, "scale_pct", 100) or 100) / 100) if (card_row is not None and zoom_ok) else 1.0
    k = max(w / art.width, h / art.height) * zoom
    big = art.resize((max(1, int(art.width * k)), max(1, int(art.height * k))), Image.LANCZOS)
    ox = int(ci._col(card_row, "offset_x", 0) or 0) * S if card_row is not None and zoom_ok else 0
    oy = int(ci._col(card_row, "offset_y", 0) or 0) * S if card_row is not None and zoom_ok else 0
    x, y = (big.width - w) // 2 - ox, (big.height - h) // 2 - oy
    out = Image.new("RGBA", (w, h), (14, 6, 30, 255))
    out.alpha_composite(big, (-x, -y))
    return out


def build(card_row, fg_image, role: str) -> io.BytesIO:
    static = c2._static()
    W2, H2 = static["mask"].size
    canvas = static["bg"].convert("RGBA")
    fg_image = load_art(ci._col(card_row, "playername", "")) or fg_image   # the uploaded artwork

    if fg_image is not None:
        # ambient background: the artwork, huge, blurred and darkened
        amb = _cover(fg_image, W2 // 4, H2 // 4, None, False).filter(ImageFilter.GaussianBlur(18))
        amb = ImageEnhance.Brightness(ImageEnhance.Color(amb.convert("RGB")).enhance(1.25)).enhance(0.42)
        canvas.alpha_composite(amb.resize((W2, H2), Image.BICUBIC).convert("RGBA"))
        # keep a little of the purple card colour on top
        tint = static["bg"].convert("RGBA")
        tint.putalpha(70)
        canvas.alpha_composite(tint)

    # dark fade for name + stats
    fade = Image.new("L", (1, H2), 0)
    y0, y1 = int((930 - CROP[1]) * S), int((1130 - CROP[1]) * S)
    for y in range(H2):
        t = 0.0 if y < y0 else min(1.0, (y - y0) / max(1, y1 - y0))
        fade.putpixel((0, y), int(170 * t))
    dark = Image.new("RGBA", (W2, H2), c2.SHADE + (255,))
    dark.putalpha(fade.resize((W2, H2)))
    canvas.alpha_composite(dark)

    # ── the artwork box ─────────────────────────────────────────────────────
    x0, y0b = c2._p(BOX[0], BOX[1])
    x1, y1b = c2._p(BOX[2], BOX[3])
    bw, bh = x1 - x0, y1b - y0b
    sh = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y0b + 10 * S, x1, y1b + 14 * S), 22 * S, fill=(0, 0, 0, 170))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16 * S)))
    if fg_image is not None:
        art = _cover(fg_image, bw, bh, card_row)
    else:
        art = Image.new("RGBA", (bw, bh), (30, 20, 50, 255))
    m = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, bw - 1, bh - 1), 20 * S, fill=255)
    canvas.paste(art, (x0, y0b), m)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((x0, y0b, x1, y1b), 20 * S, outline=GOLD, width=5 * S)
    d.rounded_rectangle((x0 + 12 * S, y0b + 12 * S, x1 - 12 * S, y1b - 12 * S), 12 * S, outline=GOLD + (90,), width=S)

    # ── top row: OVR | role | flag | playstyles ─────────────────────────────
    ovr = str(ci._col(card_row, "ovr", ""))
    lay = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    ld.text(c2._p(392, 400), ovr, font=_font("display_bold.ttf", 150 * S), fill=GOLD, anchor="rs",
            stroke_width=3 * S, stroke_fill=(10, 4, 20, 200))
    ld.text(c2._p(408, 400), role, font=_font("display_bold.ttf", 74 * S), fill=CREAM, anchor="ls",
            stroke_width=3 * S, stroke_fill=(10, 4, 20, 200))
    canvas.alpha_composite(lay)
    d = ImageDraw.Draw(canvas)
    d.line((*c2._p(580, 300), *c2._p(580, 398)), fill=GOLD + (170,), width=2 * S)
    try:
        flag = ci._load_country_logo(ci._col(card_row, "country"))
        if flag is not None:
            ci._draw_logo(canvas, flag, c2._p(680, 350), 0, 0, 100, box=128 * S)
        else:
            raise ValueError
    except Exception:
        c = str(ci._col(card_row, "country", "") or "").upper()[:3]
        d.text(c2._p(680, 350), c, font=_font("display_bold.ttf", 64 * S), fill=CREAM, anchor="mm")
    x = 840
    for slot in (1, 2):
        try:
            logo = ci._load_playstyle_badge(card_row, slot)
            if logo is not None:
                ci._draw_logo(canvas, logo, c2._p(x, 350), 0, 0, 100, box=104 * S)
                x += 124
        except Exception:
            pass

    # ── name + 6 stats (same as the normal card) ─────────────────────────────
    c2._draw_name_and_stats(canvas, ImageDraw.Draw(canvas), card_row)
    canvas.paste(c2.GOLD, (0, 0), static["border"])

    solid = Image.new("RGB", canvas.size, c2.NAVY)
    solid.paste(canvas.convert("RGB"), (0, 0), static["mask"])
    del canvas
    solid = solid.resize((c2.OUT_W, c2.OUT_H), Image.LANCZOS)
    alpha = static["mask"].resize((c2.OUT_W, c2.OUT_H), Image.LANCZOS)
    out = solid.convert("RGBA")
    out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
