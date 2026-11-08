"""END OF AN ERA card (Style 2 theme "eoae") for players who retire.

The look: "the sun sets on a career"
  - dusk sky (indigo -> plum -> coral -> amber horizon) with a setting sun, light rays and a
    stadium silhouette with floodlights, soft film grain and a vignette
  - the player in a warm SEPIA tone (an old photo) with an amber rim light
  - the jersey number big and faded behind the player
  - gold OVR inside a laurel wreath, role, flag / country, playstyle badges (same left column
    as every Style 2 card, in gold / ivory)
  - name, career years ("2012 — 2026"), a gold-foil "END OF AN ERA" ribbon, the 6 stats
  - frame: dark band + double gold foil lines, small gold diamonds on the corners and
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
    rnd = random.Random(7)
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    stops = [(0.00, (10, 12, 34)), (0.20, (24, 22, 64)), (0.40, (66, 36, 92)), (0.53, (142, 58, 92)),
             (0.62, (222, 108, 82)), (0.685, (255, 176, 108)), (0.715, (255, 214, 150)),
             (0.74, (90, 44, 40)), (0.84, (16, 10, 16)), (1.0, (6, 5, 9))]
    for y in range(H):
        t = y / H
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= t <= b:
                k = (t - a) / (b - a)
                k = k * k * (3 - 2 * k)
                d.line((0, y, W, y), fill=tuple(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3)))
                break
    bg = bg.convert("RGBA")
    hy = H * 0.725                                   # horizon / top of the stands

    # wide horizon glow (no sun disc) + very soft rays fanning up
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    ld.ellipse((W * -0.15, hy - H * 0.16, W * 1.15, hy + H * 0.08), fill=(255, 196, 120, 120))
    ld.ellipse((W * 0.18, hy - H * 0.07, W * 0.82, hy + H * 0.03), fill=(255, 230, 170, 150))
    for k in range(11):
        a = math.radians(196 + k * 14.8)
        w = 0.025
        ld.polygon([(W / 2, hy), (W / 2 + math.cos(a - w) * H, hy + math.sin(a - w) * H),
                    (W / 2 + math.cos(a + w) * H, hy + math.sin(a + w) * H)], fill=(255, 210, 150, 16))
    bg.alpha_composite(lay.filter(ImageFilter.GaussianBlur(34 * S)))

    # gold dust / bokeh in the sky
    dust = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dust)
    for _ in range(70):
        x, y = rnd.uniform(0, W), rnd.uniform(H * 0.05, hy)
        r = rnd.uniform(2, 13) * S
        dd.ellipse((x - r, y - r, x + r, y + r), fill=(255, 214, 150, rnd.randint(18, 70)))
    bg.alpha_composite(dust.filter(ImageFilter.GaussianBlur(3 * S)))
    stars = ImageDraw.Draw(bg)
    for _ in range(90):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H * 0.32)
        r = rnd.choice((1, 1, 1.5, 2)) * S
        stars.ellipse((x - r, y - r, x + r, y + r), fill=(255, 245, 225, rnd.randint(60, 170)))

    # floodlight beams (behind the stands)
    beams = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(beams)
    towers = ((W * 0.11, hy - 300 * S), (W * 0.89, hy - 300 * S))
    for tx, ty in towers:
        dirx = 1 if tx < W / 2 else -1
        bd.polygon([(tx, ty), (tx + dirx * W * 0.75, ty + H * 0.28), (tx + dirx * W * 0.55, ty + H * 0.36)],
                   fill=(255, 236, 200, 26))
    bg.alpha_composite(beams.filter(ImageFilter.GaussianBlur(20 * S)))

    # stands: a bowl, higher at the sides, two tiers, tiny camera-flash lights
    st = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(st)
    def tier(lift, col):
        pts = []
        for i in range(0, W + 1, max(1, W // 120)):
            u = (i / W - 0.5) * 2
            pts.append((i, hy - (lift + 70 * u * u) * S))
        sd.polygon([(0, H)] + pts + [(W, H)], fill=col)
        return pts
    back = tier(34, (30, 16, 26, 255))
    front = tier(4, (12, 8, 13, 255))
    for (x, y) in back[::3]:
        if rnd.random() < 0.55:
            yy = y + rnd.uniform(4, 24) * S
            r = rnd.uniform(0.8, 1.8) * S
            sd.ellipse((x - r, yy - r, x + r, yy + r), fill=(255, 226, 170, rnd.randint(90, 220)))
    # roof edge highlight
    sd.line(back, fill=(255, 170, 110, 90), width=2 * S)
    # floodlight towers
    for tx, ty in towers:
        sd.polygon([(tx - 3 * S, ty), (tx + 3 * S, ty), (tx + 9 * S, hy), (tx - 9 * S, hy)], fill=(12, 8, 13, 255))
        sd.rounded_rectangle((tx - 38 * S, ty - 40 * S, tx + 38 * S, ty + 4 * S), 4 * S, fill=(14, 10, 15, 255))
    bg.alpha_composite(st)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for tx, ty in towers:
        gd.ellipse((tx - 80 * S, ty - 70 * S, tx + 80 * S, ty + 30 * S), fill=(255, 240, 210, 120))
        gd.line((tx - 150 * S, ty - 18 * S, tx + 150 * S, ty - 18 * S), fill=(255, 240, 210, 90), width=3 * S)
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14 * S)))
    hd = ImageDraw.Draw(bg)
    for tx, ty in towers:
        for c in range(4):
            for r in range(2):
                cx, cy = tx - 27 * S + c * 18 * S, ty - 28 * S + r * 18 * S
                hd.rounded_rectangle((cx - 6 * S, cy - 6 * S, cx + 6 * S, cy + 6 * S), 2 * S, fill=(255, 250, 235, 255))

    # film grain + vignette
    grain = Image.effect_noise((W // 2, H // 2), 40).resize((W, H)).convert("L").point(lambda v: int((v - 128) * 0.35 + 128))
    bg.alpha_composite(Image.merge("RGBA", [grain, grain, grain, Image.new("L", (W, H), 18)]))
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
            # translucent dark strip behind the left column (OVR / role / flag), like every Style 2 card
            band = Image.new("L", (CW, CH), 0)
            bd = ImageDraw.Draw(band)
            for y in range(200, 1090):
                t = (y - 200) / 890
                bd.line([c2._p(275, y), c2._p(480, y)], fill=int(130 * min(1.0, t * 6) * min(1.0, (1 - t) * 6)), width=c2.S)
            _STATIC["band"] = band
        return _STATIC


# ── pieces ────────────────────────────────────────────────────────────────────

def _sepia_player(card_row, fg, size, c2) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    c2._draw_player(layer, card_row, fg)
    alpha = layer.getchannel("A")
    gray = ImageOps.grayscale(layer.convert("RGB"))
    gray = ImageOps.autocontrast(gray, cutoff=1)
    sep = ImageOps.colorize(gray, black=(34, 20, 14), white=(255, 238, 208)).convert("RGBA")
    sep = Image.blend(sep, layer.convert("RGBA"), 0.80)        # warm grade, most of the real colour stays
    sep.putalpha(alpha)
    # warm rim light behind the player
    rim = alpha.filter(ImageFilter.GaussianBlur(6 * c2.S)).point(lambda a: int(a * 0.5))
    glow = Image.new("RGBA", size, (255, 180, 90, 0))
    glow.putalpha(rim)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.alpha_composite(glow, (int(6 * c2.S), 0))
    out.alpha_composite(sep)
    return out


def _ghost(player: Image.Image, S: int) -> Image.Image:
    """Big faded gold copies of the player behind the main one (the 'double' look)."""
    W, H = player.size
    a = player.getchannel("A")
    box = a.getbbox()
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if not box:
        return out
    cut = player.crop(box)
    gray = ImageOps.autocontrast(ImageOps.grayscale(cut.convert("RGB")), cutoff=2)
    tone = ImageOps.colorize(gray, black=(40, 20, 30), white=(255, 214, 150)).convert("RGBA")
    for scale, cx, top, op, flip in ((1.30, 0.20, 0.04, 0.34, False), (1.10, 0.84, 0.10, 0.28, True)):
        t = tone.copy()
        t.putalpha(cut.getchannel("A").point(lambda v, o=op: int(v * o)))
        if flip:
            t = ImageOps.mirror(t)
        nw, nh = int(t.width * scale), int(t.height * scale)
        t = t.resize((nw, nh), Image.LANCZOS)
        out.alpha_composite(t, (int(W * cx - nw / 2), int(H * top)))
    # fade the ghosts out towards the bottom so they melt into the sky
    fade = Image.linear_gradient("L").resize((W, H)).point(lambda v: max(0, int(255 - max(0, v - 90) * 1.6)))
    out.putalpha(ImageChops.multiply(out.getchannel("A"), fade))
    return out.filter(ImageFilter.GaussianBlur(1.2 * S))


COLLAGE_SLOTS = (          # (centre x, top, height) as parts of the card, for photo 1, 2, 3
    (0.30, 0.15, 0.64),
    (0.56, 0.02, 0.38),
    (0.50, -0.02, 0.42),
)


def _load_photos(card_row) -> list:
    out = []
    for path in str(_col(card_row, "eoae_photos", "") or "").split("|"):
        path = path.strip()
        if not path:
            continue
        try:
            out.append(Image.open(path).convert("RGBA"))
        except Exception:
            pass
    return out[:3]


def _collage(photos: list, size, S: int) -> Image.Image:
    """Career moments behind the main player: gold-toned, faded, edges melting away."""
    W, H = size
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for im, (cx, top, hh) in zip(photos, COLLAGE_SLOTS):
        a = im.getchannel("A")
        if a.getextrema()[0] == 255:            # a normal photo (no cutout): soft oval edges
            m = Image.new("L", im.size, 0)
            ImageDraw.Draw(m).ellipse((im.width * 0.08, im.height * 0.05, im.width * 0.92, im.height * 0.98), fill=255)
            a = m.filter(ImageFilter.GaussianBlur(min(im.size) * 0.08))
        box = a.getbbox() or (0, 0, im.width, im.height)
        im, a = im.crop(box), a.crop(box)
        gray = ImageOps.autocontrast(ImageOps.grayscale(im.convert("RGB")), cutoff=1)
        tone = ImageOps.colorize(gray, black=(46, 22, 34), mid=(196, 120, 70), white=(255, 226, 170)).convert("RGBA")
        nh = int(H * hh)
        nw = int(tone.width * nh / tone.height)
        tone = tone.resize((nw, nh), Image.LANCZOS)
        a = a.resize((nw, nh), Image.LANCZOS)
        ramp = Image.linear_gradient("L").resize((nw, nh)).point(lambda v: max(0, int(255 - max(0, v - 120) * 2.0)))
        tone.putalpha(ImageChops.multiply(a, ramp).point(lambda v: int(v * 0.46)))
        out.alpha_composite(tone, (int(W * cx - nw / 2), int(H * top)))
    # keep the left column (OVR / role / country) clean: photos fade out behind it
    keep = Image.new("L", (W, H), 255)
    ImageDraw.Draw(keep).rounded_rectangle((-W * 0.1, -H * 0.1, W * 0.34, H * 0.40), int(40 * S), fill=70)
    out.putalpha(ImageChops.multiply(out.getchannel("A"), keep.filter(ImageFilter.GaussianBlur(40 * S))))
    return out.filter(ImageFilter.GaussianBlur(1.6 * S))


def _leaf(L: int, Wd: int) -> Image.Image:
    im = Image.new("RGBA", (L, Wd), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    top = [(L * t, Wd / 2 - math.sin(math.pi * t) ** 0.8 * Wd / 2) for t in [i / 20 for i in range(21)]]
    bot = [(x, Wd - y) for x, y in reversed(top)]
    d.polygon(top + bot, fill=GOLD[:3] + (255,))
    d.polygon(top + [(L, Wd / 2), (0, Wd / 2)], fill=GOLD_HI[:3] + (255,))
    d.line([(0, Wd / 2), (L * 0.92, Wd / 2)], fill=GOLD_DK, width=max(1, Wd // 9))
    return im


def _laurel(canvas: Image.Image, cx: float, cy: float, S: int) -> None:
    R = 116 * S
    lay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for side in (-1, 1):
        a0, a1 = 112, 250                      # from bottom to the top on this side
        stem = []
        for k in range(30):
            deg = a0 + (a1 - a0) * k / 29
            a = math.radians(deg if side < 0 else 180 - deg)
            stem.append((cx + math.cos(a) * R, cy + math.sin(a) * R * 1.04))
        d.line(stem, fill=GOLD_DK, width=4 * S)
        for k in range(9):
            deg = a0 + 8 + k * 15
            a = math.radians(deg if side < 0 else 180 - deg)
            ox, oy = cx + math.cos(a) * R, cy + math.sin(a) * R * 1.04
            tang = math.degrees(math.atan2(math.sin(a), math.cos(a))) + (90 if side < 0 else -90)
            size = 1.0 - k * 0.045
            for out in (1, -1):
                lf = _leaf(int(48 * S * size), int(18 * S * size))
                ang = -(tang + side * 0) + out * 34 * side * -1 + (180 if side > 0 else 0)
                lf = lf.rotate(ang, expand=True, resample=Image.BICUBIC)
                off = 10 * S * out
                lx = ox + math.cos(a) * off
                ly = oy + math.sin(a) * off
                lay.alpha_composite(lf, (int(lx - lf.width / 2), int(ly - lf.height / 2)))
    # a small bow at the bottom
    bx, by = cx, cy + R * 1.04
    d.polygon([(bx - 22 * S, by - 4 * S), (bx, by + 6 * S), (bx - 30 * S, by + 26 * S)], fill=GOLD)
    d.polygon([(bx + 22 * S, by - 4 * S), (bx, by + 6 * S), (bx + 30 * S, by + 26 * S)], fill=GOLD)
    d.ellipse((bx - 7 * S, by - 4 * S, bx + 7 * S, by + 10 * S), fill=GOLD_HI, outline=GOLD_DK)
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.putalpha(lay.getchannel("A").filter(ImageFilter.GaussianBlur(4 * S)).point(lambda v: int(v * 0.6)))
    canvas.alpha_composite(sh, (0, 3 * S))
    canvas.alpha_composite(lay)


def _ribbon(canvas: Image.Image, x0: float, x1: float, y: float, h: float, text: str, font, S: int) -> None:
    W, H = canvas.size
    d = ImageDraw.Draw(canvas)
    # folded tails behind the band
    t = 46 * S
    for sx, dirx in ((x0, -1), (x1, 1)):
        tail = [(sx - dirx * 30 * S, y + 14 * S), (sx + dirx * t, y + 14 * S), (sx + dirx * (t - 20 * S), y + h / 2 + 14 * S),
                (sx + dirx * t, y + h + 14 * S), (sx - dirx * 30 * S, y + h + 14 * S)]
        d.polygon(tail, fill=(118, 78, 30, 255))
        d.polygon([(sx - dirx * 30 * S, y + h), (sx, y + h), (sx - dirx * 30 * S, y + h + 14 * S)], fill=(60, 36, 14, 255))
    # main band
    shape = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shape).rectangle((x0 - 30 * S, y, x1 + 30 * S, y + h), fill=255)
    col = Image.new("RGB", (1, int(h) + 2))
    c = [(160, 112, 44), (255, 230, 160), (220, 172, 86), (132, 88, 34)]
    for yy in range(int(h) + 2):
        tt = yy / h
        seg = min(2, int(tt * 3))
        k = tt * 3 - seg
        col.putpixel((0, yy), tuple(int(c[seg][i] + (c[seg + 1][i] - c[seg][i]) * k) for i in range(3)))
    foil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    foil.paste(col.resize((W, int(h) + 2)), (0, int(y)))
    foil.putalpha(shape)
    canvas.alpha_composite(foil)
    d = ImageDraw.Draw(canvas)
    for yy in (y + 6 * S, y + h - 7 * S):
        d.line((x0 - 22 * S, yy, x1 + 22 * S, yy), fill=(110, 72, 26, 200), width=max(1, S))
    d.text(((x0 + x1) / 2, y + h / 2 + S), text, font=font, fill=INK, anchor="mm")


def _spaced(text: str) -> str:
    return "   ".join(" ".join(w) for w in text.upper().split())


# ── the card ──────────────────────────────────────────────────────────────────

def build(card_row, fg_image, role: str, template: bool = False) -> io.BytesIO:
    """template=True: only background, laurel, ribbon and frame (no player, name or stats)."""
    c2 = _c2()
    S, p = c2.S, c2._p
    st = _static()
    base = c2._static()
    mask = base["mask"]
    CW, CH = mask.size
    canvas = st["bg"].copy()

    # jersey number behind the player
    jersey = "" if template else str(_col(card_row, "eoae_jersey", "") or "").strip()[:3]
    if jersey:
        lay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        f = ci._font(ci.FONT_DISPLAY_BOLD, 560 * S)
        ImageDraw.Draw(lay).text(p(760, 590), jersey, font=f, fill=(255, 220, 160, 20 if _col(card_row, "eoae_photos") else 34), anchor="mm")
        canvas.alpha_composite(lay)

    # player in sepia, then the dark bottom
    if fg_image is not None and not template:
        player = _sepia_player(card_row, fg_image, (CW, CH), c2)
        photos = _load_photos(card_row)
        if photos:
            canvas.alpha_composite(_collage(photos, (CW, CH), S))
        canvas.alpha_composite(player)
    canvas.paste((8, 6, 10, 255), (0, 0), st["fade"])
    canvas.paste((14, 8, 20, 255), (0, 0), st["band"])

    # left column: laurel behind the OVR, then the shared column in gold / ivory
    _laurel(canvas, *p(372, 372), S)
    draw = ImageDraw.Draw(canvas)
    if not template:
        c2._draw_left_column(canvas, draw, card_row, role, TH)
        draw = ImageDraw.Draw(canvas)

    # name
    try:
        if template:
            raise ValueError
        name = str(card_row["displayname"]).upper()
        font = c2._fit_font(draw, name, 140, 640)
        c2._glow_text(canvas, p(640, 1040), name, font, "ms", (255, 150, 60), 10)
        draw = ImageDraw.Draw(canvas)
        draw.text(p(640, 1040), name, font=font, fill=IVORY, anchor="ms",
                  stroke_width=max(2, round(font.size * 0.05)), stroke_fill=INK)
    except Exception:
        pass
    # career years
    y0, y1 = (None, None) if template else (_col(card_row, "eoae_start"), _col(card_row, "eoae_end"))
    if y0 or y1:
        years = f"{_spaced(str(y0 or ''))}   —   {_spaced(str(y1 or ''))}".strip(" —")
        f = c2._lfont(40)
        draw.text(p(640, 1090), years, font=f, fill=GOLD_HI, anchor="ms", stroke_width=3 * S, stroke_fill=INK)
        half = draw.textlength(years, font=f) / 2 + 26 * S
        X, Y = p(640, 1076)
        for dirx in (-1, 1):
            draw.line((X + dirx * half, Y, X + dirx * (half + 120 * S), Y), fill=GOLD, width=2 * S)
            r = 5 * S
            ex = X + dirx * (half + 126 * S)
            draw.polygon([(ex, Y - r), (ex + r, Y), (ex, Y + r), (ex - r, Y)], fill=GOLD_HI)
    # END OF AN ERA ribbon
    ribbon = ("" if template else str(_col(card_row, "eoae_ribbon", "") or "").strip()[:30]) or DEFAULT_RIBBON
    txt = _spaced(ribbon)
    rx0, ry = p(232, 1112)
    rx1, _ = p(1048, 1112)
    font = c2._fit_font(draw, txt, 46, (rx1 - rx0) / S - 80)
    _ribbon(canvas, rx0, rx1, ry, 64 * S, txt, font, S)
    draw = ImageDraw.Draw(canvas)

    # stats
    font = c2._lfont(100)
    for i, (label, value) in enumerate([] if template else card_stat_rows(card_row)[:6]):
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
    X, Y = p(640, 112)
    r = 14 * S
    draw.polygon([(X, Y - r), (X + r, Y), (X, Y + r), (X - r, Y)], fill=GOLD_HI, outline=GOLD_DK)
    r = 6 * S
    draw.polygon([(X, Y - r), (X + r, Y), (X, Y + r), (X - r, Y)], fill=GOLD_DK)

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
