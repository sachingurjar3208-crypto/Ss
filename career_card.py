"""Career profile card image (rccareer). Pure PIL, ~0.1s. Colours follow the stage."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import career as cr

_HERE = Path(__file__).parent
W, H = 1100, 620
STAGE_COL = [(205, 127, 50), (196, 204, 214), (255, 200, 70), (60, 220, 210), (190, 120, 255)]
WHITE, DIM = (245, 246, 250), (150, 158, 178)
NAMES = {"bat": "BATTING", "bowl": "BOWLING", "fit": "FITNESS"}


def _font(name, size):
    try:
        return ImageFont.truetype(str(_HERE / name), int(size))
    except Exception:
        return ImageFont.load_default()


def _bar(d, x, y, w, h, frac, col, back=(34, 38, 60)):
    d.rounded_rectangle((x, y, x + w, y + h), h // 2, fill=back)
    fw = int(w * max(0.0, min(1.0, frac)))
    if fw >= h:
        d.rounded_rectangle((x, y, x + fw, y + h), h // 2, fill=col)
    elif fw > 0:
        d.ellipse((x, y, x + h, y + h), fill=col)


def _background(col):
    img = Image.new("RGB", (W, H), (10, 12, 28))
    px = Image.linear_gradient("L").resize((W, H))
    img = Image.composite(Image.new("RGB", (W, H), (22, 20, 52)), img, px)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-260, -260, 420, 420), fill=col + (110,))
    gd.ellipse((W - 380, H - 300, W + 220, H + 260), fill=col + (60,))
    img = img.convert("RGBA")
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    for i in range(-H, W, 46):                       # thin diagonal lines
        ld.line((i, H, i + H, 0), fill=(255, 255, 255, 9), width=2)
    ld.polygon([(0, 0), (330, 0), (250, H), (0, H)], fill=(0, 0, 0, 70))   # left panel
    ld.line((330, 0, 250, H), fill=col + (255,), width=4)
    img.alpha_composite(lay)
    return img


def render(p: dict, display_name: str, avatar: bytes | None = None) -> io.BytesIO:
    stage = p["stage"]
    col = STAGE_COL[min(stage, len(STAGE_COL) - 1)]
    img = _background(col)
    d = ImageDraw.Draw(img)
    big, lab = "display_bold.ttf", "label.ttf"
    o = cr.ovr(p)

    # left panel: OVR, role, avatar
    d.text((140, 150), str(o), font=_font(big, 190), fill=col, anchor="mm", stroke_width=4, stroke_fill=(8, 8, 20))
    d.text((140, 262), "OVR", font=_font(lab, 34), fill=WHITE, anchor="mm")
    d.text((136, 318), cr.ROLES[p["role"]].upper(), font=_font(big, 34), fill=WHITE, anchor="mm")
    if avatar:
        try:
            av = Image.open(io.BytesIO(avatar)).convert("RGBA").resize((150, 150), Image.LANCZOS)
            m = Image.new("L", (150, 150), 0)
            ImageDraw.Draw(m).ellipse((0, 0, 149, 149), fill=255)
            img.paste(av, (55, 380), m)
            d.ellipse((52, 377, 208, 533), outline=col, width=5)
        except Exception:
            pass
    d.text((130, 566), display_name[:18], font=_font(lab, 26), fill=DIM, anchor="mm")

    # header
    x0 = 380
    name = p["name"].upper()
    f = _font(big, 76)
    while d.textlength(name, font=f) > W - x0 - 40 and f.size > 36:
        f = _font(big, f.size - 4)
    d.text((x0, 26), name, font=f, fill=WHITE)
    d.rounded_rectangle((x0, 118, x0 + 14, 152), 4, fill=col)
    d.text((x0 + 26, 135), cr.STAGES[stage][0].upper(), font=_font(big, 32), fill=col, anchor="lm")
    nxt = cr.STAGES[stage + 1] if stage + 1 < len(cr.STAGES) else None
    if nxt:
        d.text((W - 40, 135), f"NEXT: {nxt[0].upper()} AT OVR {nxt[1]}", font=_font(lab, 24), fill=DIM, anchor="rm")

    # stats
    stats = ["bat", "bowl", "fit"]
    y = 190
    for st in stats:
        main = st in cr.SPLIT[p["role"]]
        need = cr.xp_need(p[st])
        d.text((x0, y + 20), NAMES[st], font=_font(lab, 28), fill=WHITE if main else DIM, anchor="lm")
        d.text((x0 + 210, y + 20), str(p[st]), font=_font(big, 50), fill=col if main else DIM, anchor="mm")
        if main:
            _bar(d, x0 + 270, y + 8, 330, 24, p["xp_" + st] / need, col)
            d.text((x0 + 615, y + 20), f"{p['xp_' + st]}/{need} XP", font=_font(lab, 22), fill=DIM, anchor="lm")
        else:
            d.text((x0 + 270, y + 20), "not trained by this role", font=_font(lab, 22), fill=(90, 96, 116),
                   anchor="lm")
        y += 66

    # stamina + daily xp
    s = cr.stamina_now(p)
    scol = (80, 220, 120) if s >= 50 else ((255, 190, 60) if s >= cr.MATCH_COST else (240, 80, 80))
    y = 408
    d.text((x0, y + 14), "STAMINA", font=_font(lab, 26), fill=WHITE, anchor="lm")
    _bar(d, x0 + 170, y, 380, 28, s / 100, scol)
    d.text((x0 + 565, y + 14), f"{s:.0f}/100", font=_font(lab, 30), fill=scol, anchor="lm")
    y = 458
    xt = p.get("xp_today", 0)
    d.text((x0, y + 14), "TODAY XP", font=_font(lab, 26), fill=WHITE, anchor="lm")
    _bar(d, x0 + 170, y, 380, 28, xt / cr.DAILY_XP_CAP, col)
    d.text((x0 + 565, y + 14), f"{xt}/{cr.DAILY_XP_CAP}", font=_font(lab, 30), fill=col, anchor="lm")

    # footer boxes
    dr = p.get("drinks", {})
    boxes = [("MATCHES", str(p["matches"])), ("WINS", str(p["wins"])),
             ("WIN STREAK", str(p.get("streak") or 0)), ("WATER", str(dr.get("water", 0))),
             ("ENERGY", str(dr.get("energy", 0))), ("POWER", str(dr.get("power", 0)))]
    bw = (W - x0 - 40 - 5 * 12) // 6
    for i, (k, v) in enumerate(boxes):
        bx = x0 + i * (bw + 12)
        d.rounded_rectangle((bx, 512, bx + bw, 596), 12, fill=(18, 20, 40), outline=(60, 66, 96), width=2)
        d.text((bx + bw // 2, 540), v, font=_font(big, 34), fill=WHITE, anchor="mm")
        d.text((bx + bw // 2, 576), k, font=_font(lab, 17), fill=DIM, anchor="mm")

    d.rounded_rectangle((3, 3, W - 4, H - 4), 22, outline=col, width=4)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, W - 1, H - 1), 24, fill=255)
    out.paste(img, (0, 0), m)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    buf.seek(0)
    return buf
