"""Welcome image for rcdebut: Rush Cricket backdrop, WELCOME + player name, team and purse panel,
and the next commands. Pure Pillow, no Discord code.

    buf = build_debut_image("Sachin", "Sachin XI", "50,000")   # -> BytesIO (JPEG) or None
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_HERE = Path(__file__).parent
BG_FILE = _HERE / "starter_bg.png"
FILENAME = "debut.jpg"            # embeds must use attachment://debut.jpg

W = 1254
GOLD = (232, 214, 140)
WHITE = (255, 255, 255)
SOFT = (170, 190, 225)


def _font(size: int, name: str = "display_bold.ttf") -> ImageFont.FreeTypeFont:
    for n in (name, "label.ttf", "name_font.ttf"):
        p = _HERE / n
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                pass
    return ImageFont.load_default()


def _fit(d, text, max_w, size, name="display_bold.ttf"):
    f = _font(size, name)
    while d.textlength(text, font=f) > max_w and f.size > 20:
        f = _font(f.size - 4, name)
    return f


def _shadow(d, xy, text, font, fill, anchor="mm"):
    d.text((xy[0] + 3, xy[1] + 4), text, font=font, fill=(0, 0, 0), anchor=anchor)
    d.text(xy, text, font=font, fill=fill, anchor=anchor)


def _backdrop() -> Image.Image:
    """Logo area on top + stadium at the bottom (the plain middle band is cut out)."""
    try:
        bg = Image.open(BG_FILE).convert("RGB")
        if bg.width != W:
            bg = bg.resize((W, round(bg.height * W / bg.width)), Image.LANCZOS)
        fade = 70
        top, bottom = bg.crop((0, 0, W, 300)), bg.crop((0, 560 - fade, W, bg.height))
        out = Image.new("RGB", (W, top.height + bottom.height - fade))
        out.paste(bottom, (0, top.height - fade))
        ramp = Image.linear_gradient("L").resize((W, top.height)).transpose(Image.FLIP_TOP_BOTTOM)  # 255 top -> 0 bottom
        mask = Image.new("L", (W, top.height), 255)
        mask.paste(ramp.crop((0, 0, W, fade)).resize((W, fade)), (0, top.height - fade))
        out.paste(top, (0, 0), mask)
        return out
    except Exception:
        img = Image.new("RGB", (W, 900))
        d = ImageDraw.Draw(img)
        for y in range(900):
            d.line([(0, y), (W, y)], fill=(8, 16 + y // 40, 40 + y // 20))
        return img


def _panel(canvas, box, radius=26):
    """Dark translucent rounded panel with a thin gold border."""
    x0, y0, x1, y1 = box
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 8, x1, y1 + 8), radius, fill=(0, 0, 0, 120))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(14))).convert("RGB"))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle(box, radius, fill=(6, 14, 40, 205), outline=GOLD + (230,), width=3)
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB"))


def _coin(d, cx, cy, r):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(150, 104, 34))
    d.ellipse((cx - r + 4, cy - r + 4, cx + r - 4, cy + r - 4), fill=(240, 196, 80))
    d.ellipse((cx - r + 11, cy - r + 11, cx + r - 11, cy + r - 11), outline=(190, 140, 40), width=3)
    d.text((cx, cy + 1), "C", font=_font(int(r * 1.15)), fill=(150, 104, 34), anchor="mm")


def build_debut_image(display_name: str, team_name: str, purse_text: str) -> io.BytesIO | None:
    try:
        canvas = _backdrop()
        d = ImageDraw.Draw(canvas)

        _shadow(d, (W // 2, 330), "WELCOME", _font(128), GOLD)
        name = (display_name or "PLAYER").upper()
        _shadow(d, (W // 2, 440), name, _fit(d, name, 1000, 78), WHITE)
        d.line([(W // 2 - 140, 497), (W // 2 + 140, 497)], fill=GOLD, width=3)

        # team + purse panel
        box = (110, 540, W - 110, 700)
        _panel(canvas, box)
        d = ImageDraw.Draw(canvas)
        mid = W // 2
        d.line([(mid, 565), (mid, 675)], fill=(90, 110, 160), width=2)
        cx_l, cx_r = (box[0] + mid) // 2, (mid + box[2]) // 2
        d.text((cx_l, 585), "YOUR TEAM", font=_font(30, "label.ttf"), fill=SOFT, anchor="mm")
        team = (team_name or "TEAM").upper()
        _shadow(d, (cx_l, 642), team, _fit(d, team, 460, 62), WHITE)
        d.text((cx_r, 585), "STARTING PURSE", font=_font(30, "label.ttf"), fill=SOFT, anchor="mm")
        pf = _font(70, "label.ttf")
        tw = d.textlength(purse_text, font=pf)
        _shadow(d, (cx_r - 28, 642), purse_text, pf, GOLD)
        _coin(d, int(cx_r - 28 + tw / 2 + 42), 642, 30)

        # next steps
        d.text((mid, 770), "GET STARTED", font=_font(30, "label.ttf"), fill=SOFT, anchor="mm")
        steps = ["rcstarterpack", "rcxi", "rcteamname"]
        f = _font(46, "label.ttf")
        gap, pad = 26, 30
        widths = [int(d.textlength(s, font=f)) + 2 * pad for s in steps]
        x = (W - (sum(widths) + gap * (len(steps) - 1))) // 2
        for s, w in zip(steps, widths):
            d.rounded_rectangle((x, 802, x + w, 872), 18, fill=(10, 24, 66), outline=GOLD, width=2)
            d.text((x + w // 2, 838), s, font=f, fill=WHITE, anchor="mm")
            x += w + gap
        d.text((mid, 905), "Pack your first 11 players  •  See your XI  •  Rename your team",
               font=_font(28, "label.ttf"), fill=SOFT, anchor="mm")

        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=92)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"[debut_image] failed: {e}")
        return None
