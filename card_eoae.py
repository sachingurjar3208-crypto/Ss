"""END OF AN ERA card (Style 2 theme "eoae") for players who retire.

The look: "the sun sets on a career"
  - dusk sky (indigo -> plum -> coral -> amber horizon) with a setting sun, light rays and a
    stadium silhouette with floodlights, soft film grain and a vignette
  - the player in a warm SEPIA tone (an old photo) with an amber rim light
  - the jersey number big and faded behind the player
  - gold OVR inside a laurel wreath, role, flag / country, playstyle badges (same left column
    as every Style 2 card, in gold / ivory)
  - name, career years ("2012 — 2026"), a gold-foil "END OF AN ERA" ribbon, the 6 stats
  - frame: dark band + double gold foil lines, small gold diamonds on the corners, a gold sun
    on the top spike, "FAREWELL · LEGEND" at the bottom

Per card (set with /eoae): eoae_start, eoae_end (years), eoae_jersey, eoae_ribbon (text).
Entry point: build(card_row, fg_image, role) -> io.BytesIO (PNG, same size as every card).
"""
from __future__ import annotations

import io
import math
import random
import threading

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

import card_image as ci
from card_stats import card_stat_rows

GOLD = (222, 186, 112, 255)
GOLD_HI = (255, 232, 170, 255)
GOLD_DK = (130, 92, 40, 255)
IVORY = (244, 236, 220, 255)
SILVER = (200, 200, 210, 255)
INK = (28, 14, 10)
TH = {   # colours for the shared left column (card_style2._draw_left_column)
    "num": GOLD_HI, "label": IVORY, "line": GOLD, "outline": (30, 16, 30), "glow": (255, 150, 60),
}
DEFAULT_RIBBON = "END OF AN ERA"

_STATIC: dict = {}
_LOCK = threading.Lock()


def _c2():
    import card_style2 as c2
    return c2


def _col(row, key, default=None):
    return ci._col(row, key, default)


# ── background (built once, cached) ───────────────────────────────────────────

def _background(CW: int, CH: int, S: int) -> Image.Image:
    W, H = CW, CH
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    stops = [(0.00, (12, 14, 38)), (0.22, (28, 22, 62)), (0.42, (78, 34, 84)), (0.56, (168, 64, 74)),
             (0.66, (236, 128, 70)), (0.71, (255, 190, 110)), (0.745, (120, 56, 40)),
             (0.84, (20, 12, 18)), (1.0, (6, 6, 10))]
    for y in range(H):
        t = y / H
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= t <= b:
                k = (t - a) / (b - a)
                d.line((0, y, W, y), fill=tuple(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3)))
                break
    bg = bg.convert("RGBA")
    # setting sun + rays
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    sx, sy = W * 0.5, H * 0.70
    ld.ellipse((sx - W * 0.22, sy - W * 0.22, sx + W * 0.22, sy + W * 0.22), fill=(255, 200, 120, 190))
    for k in range(18):
        a = math.radians(180 + k * 10)
        ld.polygon([(sx, sy), (sx + math.cos(a - 0.02) * H, sy + math.sin(a - 0.02) * H),
                    (sx + math.cos(a + 0.02) * H, sy + math.sin(a + 0.02) * H)], fill=(255, 200, 120, 22))
    bg.alpha_composite(lay.filter(ImageFilter.GaussianBlur(30 * S)))
    # stadium silhouette with floodlights on the horizon
    st = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(st)
    hy = H * 0.735
    pts = [(0, hy)] + [(i, hy - (30 + 18 * math.sin(i / W * math.pi * 3)) * S) for i in range(0, W + 1, max(1, W // 40))]
    sd.polygon(pts + [(W, hy), (W, H), (0, H)], fill=(10, 8, 12, 255))
    for x in (W * 0.18, W * 0.82):
        sd.rectangle((x - 4 * S, hy - 260 * S, x + 4 * S, hy), fill=(10, 8, 12, 255))
        sd.rectangle((x - 34 * S, hy - 290 * S, x + 34 * S, hy - 252 * S), fill=(10, 8, 12, 255))
        for c in range(4):
            for r in range(2):
                cx, cy = x - 24 * S + c * 16 * S, hy - 282 * S + r * 16 * S
                sd.ellipse((cx - 4 * S, cy - 4 * S, cx + 4 * S, cy + 4 * S), fill=(255, 236, 190, 255))
    bg.alpha_composite(st)
    # film grain + vignette
    grain = Image.effect_noise((W // 2, H // 2), 40).resize((W, H)).convert("L").point(lambda v: int((v - 128) * 0.35 + 128))
    bg.alpha_composite(Image.merge("RGBA", [grain, grain, grain, Image.new("L", (W, H), 22)]))
    v = Image.new("L", (W, H), 0)
    ImageDraw.Draw(v).ellipse((-W * 0.3, -H * 0.12, W * 1.3, H * 1.1), fill=255)
    v = ImageChops.invert(v.filter(ImageFilter.GaussianBlur(160 * S))).point(lambda p: int(p * 0.55))
    dk = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    dk.putalpha(v)
    bg.alpha_composite(dk)
    return bg


def _frame_layers(mask: Image.Image, c2) -> dict:
    """Dark outer band + three gold lines, as masks."""
    out = {"band": ImageChops.subtract(mask, c2._erode(mask, 20)), "lines": []}
    for a, b, col in ((2, 6, GOLD_HI), (13, 15, GOLD), (26, 27.5, GOLD_DK)):
        m1 = c2._erode(mask, a)
        m2 = c2._erode(mask, b)
        out["lines"].append((ImageChops.subtract(m1, m2), col))
    return out


def _static() -> dict:
    with _LOCK:
        if not _STATIC:
            c2 = _c2()
            st = c2._static()
            mask = st["mask"]
            CW, CH = mask.size
            _STATIC["bg"] = _background(CW, CH, c2.S)
            _STATIC["frame"] = _frame_layers(mask, c2)
            # dark bottom (behind the stats), only inside the card
            fade = Image.new("L", (CW, CH), 0)
            fd = ImageDraw.Draw(fade)
            y0, y1 = int(CH * 0.70), int(CH * 0.80)
            for y in range(y0, CH):
                fd.line((0, y, CW, y), fill=min(255, int((y - y0) / max(1, y1 - y0) * 255)))
            _STATIC["fade"] = ImageChops.multiply(fade, mask).point(lambda p: int(p * 0.92))
        return _STATIC


# ── pieces ────────────────────────────────────────────────────────────────────

def _sepia_player(card_row, fg, size, c2) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    c2._draw_player(layer, card_row, fg)
    alpha = layer.getchannel("A")
    gray = ImageOps.grayscale(layer.convert("RGB"))
    gray = ImageOps.autocontrast(gray, cutoff=1)
    sep = ImageOps.colorize(gray, black=(34, 20, 14), white=(255, 238, 208)).convert("RGBA")
    sep = Image.blend(sep, layer.convert("RGBA"), 0.18)        # a little of the real colour stays
    sep.putalpha(alpha)
    # warm rim light behind the player
    rim = alpha.filter(ImageFilter.GaussianBlur(6 * c2.S)).point(lambda a: int(a * 0.5))
    glow = Image.new("RGBA", size, (255, 180, 90, 0))
    glow.putalpha(rim)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.alpha_composite(glow, (int(6 * c2.S), 0))
    out.alpha_composite(sep)
    return out


def _laurel(canvas: Image.Image, cx: float, cy: float, S: int) -> None:
    R = 112 * S
    for side in (-1, 1):
        for k in range(8):
            deg = 125 + k * 17
            a = math.radians(deg if side < 0 else 180 - deg)
            ox, oy = cx + math.cos(a) * R, cy + math.sin(a) * R * 1.05
            tang = math.degrees(a) + (90 if side < 0 else -90)
            leaf = Image.new("RGBA", (46 * S, 18 * S), (0, 0, 0, 0))
            ImageDraw.Draw(leaf).ellipse((0, 0, 46 * S, 18 * S), fill=GOLD[:3] + (235,), outline=GOLD_DK)
            leaf = leaf.rotate(-tang + 30 * side, expand=True, resample=Image.BICUBIC)
            canvas.alpha_composite(leaf, (int(ox - leaf.width / 2), int(oy - leaf.height / 2)))


def _ribbon(canvas: Image.Image, x0: float, x1: float, y: float, h: float, text: str, font, S: int) -> None:
    W, H = canvas.size
    shape = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shape).polygon([(x0, y), (x1, y), (x1 - 22 * S, y + h / 2), (x1, y + h), (x0, y + h),
                                   (x0 + 22 * S, y + h / 2)], fill=255)
    col = Image.new("RGB", (1, int(h) + 2))
    c = [(150, 104, 40), (255, 226, 150), (214, 166, 80), (120, 80, 30)]
    for yy in range(int(h) + 2):
        t = yy / h
        seg = min(2, int(t * 3))
        k = t * 3 - seg
        col.putpixel((0, yy), tuple(int(c[seg][i] + (c[seg + 1][i] - c[seg][i]) * k) for i in range(3)))
    foil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    foil.paste(col.resize((W, int(h) + 2)), (0, int(y)))
    foil.putalpha(shape)
    canvas.alpha_composite(foil)
    ImageDraw.Draw(canvas).text(((x0 + x1) / 2, y + h / 2), text, font=font, fill=INK, anchor="mm")


def _spaced(text: str) -> str:
    return "   ".join(" ".join(w) for w in text.upper().split())


# ── the card ──────────────────────────────────────────────────────────────────

def build(card_row, fg_image, role: str) -> io.BytesIO:
    c2 = _c2()
    S, p = c2.S, c2._p
    st = _static()
    base = c2._static()
    mask = base["mask"]
    CW, CH = mask.size
    canvas = st["bg"].copy()

    # jersey number behind the player
    jersey = str(_col(card_row, "eoae_jersey", "") or "").strip()[:3]
    if jersey:
        lay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        f = ci._font(ci.FONT_DISPLAY_BOLD, 560 * S)
        ImageDraw.Draw(lay).text(p(760, 590), jersey, font=f, fill=(255, 220, 160, 34), anchor="mm")
        canvas.alpha_composite(lay)

    # player in sepia, then the dark bottom
    if fg_image is not None:
        canvas.alpha_composite(_sepia_player(card_row, fg_image, (CW, CH), c2))
    canvas.paste((8, 6, 10, 255), (0, 0), st["fade"])

    # left column: laurel behind the OVR, then the shared column in gold / ivory
    _laurel(canvas, *p(372, 372), S)
    draw = ImageDraw.Draw(canvas)
    c2._draw_left_column(canvas, draw, card_row, role, TH)
    draw = ImageDraw.Draw(canvas)

    # name
    try:
        name = str(card_row["displayname"]).upper()
        font = c2._fit_font(draw, name, 140, 640)
        c2._glow_text(canvas, p(640, 1040), name, font, "ms", (255, 150, 60), 10)
        draw = ImageDraw.Draw(canvas)
        draw.text(p(640, 1040), name, font=font, fill=IVORY, anchor="ms",
                  stroke_width=max(2, round(font.size * 0.05)), stroke_fill=INK)
    except Exception:
        pass
    # career years
    y0, y1 = _col(card_row, "eoae_start"), _col(card_row, "eoae_end")
    if y0 or y1:
        years = f"{_spaced(str(y0 or ''))}   —   {_spaced(str(y1 or ''))}".strip(" —")
        draw.text(p(640, 1090), years, font=c2._lfont(40), fill=GOLD_HI, anchor="ms",
                  stroke_width=3 * S, stroke_fill=INK)
    # END OF AN ERA ribbon
    ribbon = str(_col(card_row, "eoae_ribbon", "") or "").strip()[:30] or DEFAULT_RIBBON
    txt = _spaced(ribbon)
    rx0, ry = p(232, 1112)
    rx1, _ = p(1048, 1112)
    font = c2._fit_font(draw, txt, 46, (rx1 - rx0) / S - 80)
    _ribbon(canvas, rx0, rx1, ry, 64 * S, txt, font, S)
    draw = ImageDraw.Draw(canvas)

    # stats
    font = c2._lfont(100)
    for i, (label, value) in enumerate(card_stat_rows(card_row)[:6]):
        col, y = i // 3, [1262, 1358, 1454][i % 3]
        num_x, lab_x = (400, 420) if col == 0 else (850, 870)
        draw.text(p(num_x, y), str(value), font=font, fill=GOLD_HI, anchor="rm", stroke_width=S * 2, stroke_fill=INK)
        draw.text(p(lab_x, y), label, font=font, fill=IVORY, anchor="lm", stroke_width=S * 2, stroke_fill=INK)
    draw.line([p(646, 1215), p(646, 1478)], fill=GOLD[:3] + (170,), width=2 * S)
    draw.text(p(640, 1556), "F A R E W E L L   ·   L E G E N D", font=c2._lfont(26), fill=GOLD, anchor="mm")

    # frame
    canvas.paste((16, 12, 14, 255), (0, 0), st["frame"]["band"])
    for m, col in st["frame"]["lines"]:
        canvas.paste(col, (0, 0), m)
    draw = ImageDraw.Draw(canvas)
    for (x, y) in ((152, 300), (1128, 295), (188, 1475), (1092, 1475), (152, 1405), (1128, 1405)):
        X, Y = p(x, y)
        r = 11 * S
        draw.polygon([(X, Y - r), (X + r, Y), (X, Y + r), (X - r, Y)], fill=GOLD_HI, outline=GOLD_DK)
    X, Y = p(640, 128)
    glow = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((X - 60 * S, Y - 60 * S, X + 60 * S, Y + 60 * S), fill=(255, 180, 90, 150))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18 * S)))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((X - 22 * S, Y - 22 * S, X + 22 * S, Y + 22 * S), fill=GOLD_HI, outline=GOLD_DK, width=2 * S)
    for k in range(12):
        a = math.radians(k * 30)
        draw.line((X + math.cos(a) * 30 * S, Y + math.sin(a) * 30 * S, X + math.cos(a) * 42 * S,
                   Y + math.sin(a) * 42 * S), fill=GOLD_HI, width=3 * S)

    # clip to the shield: solid colour layer on NAVY (same as every Style 2 card)
    solid = Image.new("RGB", canvas.size, c2.NAVY)
    solid.paste(canvas.convert("RGB"), (0, 0), mask)
    solid = solid.resize((c2.OUT_W, c2.OUT_H), Image.LANCZOS)
    out = solid.convert("RGBA")
    out.putalpha(mask.resize((c2.OUT_W, c2.OUT_H), Image.LANCZOS))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
