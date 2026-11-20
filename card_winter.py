"""Winter Wonder card design (theme "winter").

Entry point: build(card_row, fg_image, role) -> io.BytesIO (PNG, 1037 x 1517).
Called from card_full.build() for every card whose s2_theme is "winter".

Look: straight rectangle, icy frozen-lake background (winter_bg.jpg) graded to blue, a light burst
behind the player, big glowing OVR outline behind the head, the player colour-graded with a cyan
rim light and fading into frost mist, a frosted-glass stats panel (OVR | BAT | BOWL) that blurs
what is behind it, frost cracks running from the background into the panel, snow in front.
Static layers are built once per process; only the player, OVR and text change per card.
"""

from __future__ import annotations

import io
import math
import random
import threading
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

import card_image as ci
from card_narratives import ROLE_CARD_WORD

ROOT = Path(__file__).resolve().parent
W, H = ci.CANVAS_W, ci.CANVAS_H          # 1037 x 1517
ICE = (150, 225, 255)
ICE_HI = (225, 248, 255)
NAVY = (8, 16, 42)
BURST = (W // 2, 560)
PANEL = (52, 1112, W - 52, H - 52)
PLAYER_BOTTOM = 1290
PLAYER_HEAD_Y = 300                      # top of the head (overlaps the big OVR)
PLAYER_MAX_W = 1000

_LOCK = threading.Lock()
_STATIC: dict = {}


def _font(name, size):
    p = ROOT / name if not isinstance(name, Path) else name
    try:
        return ImageFont.truetype(str(p), size)
    except OSError:
        return ImageFont.truetype(str(ci.FONT_DISPLAY_BOLD), size)


def _fit(draw, text, name, size, max_w):
    while size > 18:
        f = _font(name, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(name, size)


def _cover(im, w, h):
    r = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * r + 1), int(im.height * r + 1)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def _grade(im, amt):
    rgb = im.convert("RGB")
    toned = ImageOps.colorize(ImageOps.grayscale(rgb), black=NAVY, mid=(60, 120, 190), white=ICE_HI)
    return Image.blend(rgb, toned, amt)


def _radial(size, center, radius, color, power=2.0):
    w, h = size
    sw, sh = max(1, w // 4), max(1, h // 4)
    m = Image.new("L", (sw, sh))
    px = m.load()
    cx, cy, r = center[0] / 4, center[1] / 4, radius / 4
    for y in range(sh):
        for x in range(sw):
            d = math.hypot(x - cx, y - cy) / r
            px[x, y] = int(255 * max(0.0, 1 - d) ** power)
    layer = Image.new("RGBA", size, color + (0,))
    layer.putalpha(m.resize(size, Image.BILINEAR))
    return layer


def _cracks(origin, n, seed, length):
    r = random.Random(seed)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def branch(x, y, ang, ln, w, depth):
        pts = [(x, y)]
        steps = max(3, int(ln / 28))
        for _ in range(steps):
            ang += r.uniform(-0.35, 0.35)
            x += math.cos(ang) * ln / steps
            y += math.sin(ang) * ln / steps
            pts.append((x, y))
            if depth < 2 and r.random() < 0.18:
                branch(x, y, ang + r.choice([-1, 1]) * r.uniform(0.5, 1.0), ln * 0.4, max(1, w - 1), depth + 1)
        d.line(pts, fill=ICE_HI + (210,), width=w)

    for i in range(n):
        branch(origin[0], origin[1], (i / n) * 2 * math.pi + r.uniform(-0.2, 0.2), r.uniform(*length), 3, 0)
    glow = layer.filter(ImageFilter.GaussianBlur(6))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(glow)
    out.alpha_composite(glow)
    out.alpha_composite(layer)
    return out


PLAYER_ICE = 0.10        # a little of the icy grade so the player still sits in the scene
PLAYER_SAT = 1.25        # colour saturation on the player (1 = photo as it is)
PLAYER_CONTRAST = 1.08
PLAYER_WARM = 0.22       # warm red / orange overlay on the player (0 = off)


def _player_look(player):
    """Normal saturation + warm red tone on the player (like football edits), slight ice grade."""
    from PIL import ImageEnhance
    x = _grade(player, PLAYER_ICE)
    x = ImageEnhance.Color(x).enhance(PLAYER_SAT)
    x = ImageEnhance.Contrast(x).enhance(PLAYER_CONTRAST)
    if PLAYER_WARM > 0:
        # overlay blend with a warm colour: shadows get a red tint, highlights stay bright
        r, g, b = x.split()
        out = []
        for ch, t in zip((r, g, b), (1.0, 0.45, 0.25)):
            lut = []
            for v in range(256):
                a = v / 255
                ov = 2 * a * t if a < 0.5 else 1 - 2 * (1 - a) * (1 - t)
                lut.append(int(max(0, min(1, a * (1 - PLAYER_WARM) + ov * PLAYER_WARM)) * 255))
            out.append(ch.point(lut))
        x = Image.merge("RGB", out)
    return x


def _glow_text(base, xy, text, fnt, fill, glow, blur, anchor="mm", alpha=255):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=fnt, fill=glow + (alpha,), anchor=anchor)
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))
    ImageDraw.Draw(base).text(xy, text, font=fnt, fill=fill, anchor=anchor)


def _build_static() -> dict:
    rnd = random.Random(7)
    src = ROOT / "winter_bg.jpg"
    bg = _cover(Image.open(src), W, H) if src.is_file() else Image.new("RGB", (W, H), (20, 40, 80))
    bg = _grade(bg, 0.35).convert("RGBA")
    bg.alpha_composite(Image.new("RGBA", (W, H), NAVY + (90,)))
    bg.alpha_composite(_radial((W, H), BURST, 620, ICE, 1.8))
    bg.alpha_composite(_radial((W, H), BURST, 260, ICE_HI, 2.2))

    streaks = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(streaks)
    for _ in range(26):
        a, ln = rnd.uniform(0, 2 * math.pi), rnd.uniform(500, 1100)
        sd.line([BURST, (BURST[0] + math.cos(a) * ln, BURST[1] + math.sin(a) * ln)],
                fill=ICE_HI + (rnd.randint(25, 70),), width=rnd.randint(2, 6))
    bg.alpha_composite(streaks.filter(ImageFilter.GaussianBlur(4)))
    bg.alpha_composite(_cracks(BURST, 7, 3, (200, 480)))

    # frost mist rising from the bottom
    mist = Image.new("L", (W // 8, H // 8), 0)
    md = ImageDraw.Draw(mist)
    for _ in range(140):
        x, y = rnd.uniform(-10, W / 8 + 10), rnd.uniform(H / 8 * 0.62, H / 8 + 10)
        rr = rnd.uniform(6, 18)
        md.ellipse((x - rr, y - rr * 0.6, x + rr, y + rr * 0.6), fill=rnd.randint(60, 140))
    mist_l = Image.new("RGBA", (W, H), (200, 230, 255, 0))
    mist_l.putalpha(mist.filter(ImageFilter.GaussianBlur(5)).resize((W, H), Image.BICUBIC))

    vig = _radial((W, H), (W // 2, H // 2), 1000, (0, 0, 0), 1.0).getchannel("A")
    vig_l = Image.new("RGBA", (W, H), (2, 6, 18, 0))
    vig_l.putalpha(vig.point(lambda v: int((255 - v) * 0.55)))

    x0, y0, x1, y1 = PANEL
    pw, ph = x1 - x0, y1 - y0
    shine = Image.new("L", (pw, ph), 0)
    sh = ImageDraw.Draw(shine)
    for y in range(ph):
        sh.line([(0, y), (pw, y)], fill=int(60 * (1 - y / ph) ** 2))
    shine_l = Image.new("RGBA", (pw, ph), (255, 255, 255, 0))
    shine_l.putalpha(shine)
    pmask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(pmask).rounded_rectangle((0, 0, pw - 1, ph - 1), 30, fill=255)

    pc = _cracks((W // 2, y0 - 40), 7, 11, (180, 420))
    pcm = Image.new("L", (W, H), 0)
    ImageDraw.Draw(pcm).rounded_rectangle(PANEL, 30, fill=90)
    pc.putalpha(ImageChops.multiply(pc.getchannel("A"), pcm))

    edge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge)
    ed.rounded_rectangle(PANEL, 30, outline=ICE + (200,), width=3)
    ed.line([(x0 + 60, y0), (x1 - 60, y0)], fill=ICE_HI + (255,), width=5)
    edge_glow = edge.filter(ImageFilter.GaussianBlur(7))

    snow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sn = ImageDraw.Draw(snow)
    for _ in range(220):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        r = rnd.choice([1.2, 1.6, 2.2, 2.8])
        sn.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rnd.randint(140, 230)))
    bokeh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bk = ImageDraw.Draw(bokeh)
    for _ in range(14):
        x, y = rnd.uniform(0, W), rnd.choice([rnd.uniform(0, 380), rnd.uniform(900, H)])
        r = rnd.uniform(10, 26)
        bk.ellipse((x - r, y - r, x + r, y + r), fill=(230, 245, 255, rnd.randint(70, 130)))
    snow.alpha_composite(bokeh.filter(ImageFilter.GaussianBlur(7)))

    noise = Image.effect_noise((W, H), 28).convert("RGBA")
    noise.putalpha(18)

    return {"bg": bg, "mist": mist_l, "vig": vig_l, "shine": shine_l, "pmask": pmask,
            "pcracks": pc, "edge": edge, "edge_glow": edge_glow, "snow": snow, "noise": noise}


def _static() -> dict:
    if not _STATIC:
        with _LOCK:
            if not _STATIC:
                _STATIC.update(_build_static())
    return _STATIC


def _place_player(card_row, fg):
    """Trim, scale and position the cutout. /foregroundfix full_dx / full_dy / full_scale still apply."""
    import card_full as cf
    try:
        import card_style2 as c2
        fg = c2._trim_foreground(fg.convert("RGBA"))
    except Exception:
        fg = fg.convert("RGBA")
    fdx = int(ci._col(card_row, "full_dx", 0) or 0)
    fdy = int(ci._col(card_row, "full_dy", 0) or 0)
    fsc = int(ci._col(card_row, "full_scale", 100) or 100)

    info = None
    try:
        info = cf._head_info(fg)
    except Exception:
        pass
    head_top = info[0] if info and fg.height - info[0] > fg.height * 0.3 else 0
    k = (PLAYER_BOTTOM - PLAYER_HEAD_Y) / max(1, fg.height - head_top)
    k = min(k, PLAYER_MAX_W / fg.width) * fsc / 100
    p = fg.resize((max(1, int(fg.width * k)), max(1, int(fg.height * k))), Image.LANCZOS)
    hx = None
    try:
        hx = cf._player_center_x(p)
    except Exception:
        pass
    if hx is None:
        hx = p.width / 2
    return p, (int(W / 2 - hx) + fdx, PLAYER_BOTTOM - p.height + fdy)


def build(card_row, fg_image, role) -> io.BytesIO:
    st = _static()
    comp = st["bg"].copy()
    ovr = str(int(card_row["ovr"] or 0))
    bat, bowl = int(card_row["bat"] or 0), int(card_row["bowl"] or 0)

    # big glowing OVR outline behind the player
    big = _font(ci.FONT_DISPLAY_BOLD, 470)
    gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(gl).text((W // 2, 330), ovr, font=big, fill=(0, 0, 0, 0), anchor="mm",
                            stroke_width=10, stroke_fill=ICE + (255,))
    comp.alpha_composite(gl.filter(ImageFilter.GaussianBlur(18)))
    del gl
    ImageDraw.Draw(comp).text((W // 2, 330), ovr, font=big, fill=(150, 210, 245, 45), anchor="mm",
                              stroke_width=5, stroke_fill=ICE_HI + (235,))

    # player: colour grade + rim light + halo + bottom fade into the mist
    if fg_image is not None:
        try:
            player, pos = _place_player(card_row, fg_image)
            pad = 90                                  # room for the halo / rim blur (no hard box edge)
            padded = Image.new("RGBA", (player.width + 2 * pad, player.height + 2 * pad), (0, 0, 0, 0))
            padded.paste(player, (pad, pad))
            player, pos = padded, (pos[0] - pad, pos[1] - pad)
            a = player.getchannel("A")
            fade = Image.new("L", player.size, 255)
            fd = ImageDraw.Draw(fade)
            f0 = PLAYER_BOTTOM - 360 - pos[1]
            f1 = PLAYER_BOTTOM - 60 - pos[1]
            for y in range(max(0, f0), player.height):
                t = min(1.0, (y - f0) / max(1, f1 - f0))
                fd.line([(0, y), (player.width, y)], fill=int(255 * (1 - t) ** 1.4))
            af = ImageChops.multiply(a, fade)

            graded = _player_look(player).convert("RGBA")
            graded.putalpha(af)
            edge = ImageChops.subtract(a, a.filter(ImageFilter.MinFilter(15))).filter(ImageFilter.GaussianBlur(5))
            rim = Image.new("RGBA", player.size, ICE_HI + (0,))
            rim.putalpha(ImageChops.multiply(edge.point(lambda v: min(255, int(v * 1.1))), fade))
            halo = Image.new("RGBA", player.size, ICE + (0,))
            halo.putalpha(af.filter(ImageFilter.GaussianBlur(28)).point(lambda v: v // 2))

            # no glow on the player edges (owner: keep the cutout border clean)
            _paste(comp, graded, pos)
        except Exception as e:
            print(f"[card_winter] player skipped ({type(e).__name__}: {e})")

    comp.alpha_composite(st["mist"])
    comp.alpha_composite(st["vig"])

    # frosted glass panel: blur what is behind it
    x0, y0, x1, y1 = PANEL
    region = comp.crop(PANEL).filter(ImageFilter.GaussianBlur(18))
    region.alpha_composite(Image.new("RGBA", region.size, (120, 170, 220, 70)))
    region.alpha_composite(st["shine"])
    comp.paste(region, (x0, y0), st["pmask"])
    comp.alpha_composite(st["pcracks"])
    comp.alpha_composite(st["edge_glow"])
    comp.alpha_composite(st["edge"])

    # panel text
    d = ImageDraw.Draw(comp)
    cx = W // 2
    name = str(card_row["displayname"] or "").strip().upper()
    if name:
        _glow_text(comp, (cx, y0 + 68), name, _fit(d, name, "cinzel.ttf", 64, x1 - x0 - 90),
                   fill=(240, 250, 255, 255), glow=ICE, blur=10)
    role_word = ROLE_CARD_WORD.get(str(role or "").upper(), str(role or "").upper())
    country = str(ci._col(card_row, "country", "") or "").strip().upper()
    sub = "  ·  ".join(x for x in (role_word, country) if x)
    d = ImageDraw.Draw(comp)
    if sub:
        d.text((cx, y0 + 122), sub, font=_fit(d, sub, ci.FONT_LABEL, 28, x1 - x0 - 120),
               fill=(185, 220, 245, 255), anchor="mm")
    d.line([(x0 + 90, y0 + 156), (x1 - 90, y0 + 156)], fill=ICE + (160,), width=2)

    num_f, lab_f = _font(ci.FONT_DISPLAY_BOLD, 96), _font(ci.FONT_LABEL, 30)
    cols = [x0 + (x1 - x0) * f for f in (1 / 6, 3 / 6, 5 / 6)]
    for (lab, val), c in zip((("OVR", ovr), ("BAT", str(bat)), ("BOWL", str(bowl))), cols):
        _glow_text(comp, (c, y0 + 235), val, num_f, fill=(255, 255, 255, 255), glow=ICE, blur=12)
        ImageDraw.Draw(comp).text((c, y0 + 305), lab, font=lab_f, fill=(165, 215, 245, 255), anchor="mm")
    d = ImageDraw.Draw(comp)
    for c in (x0 + (x1 - x0) / 3, x0 + (x1 - x0) * 2 / 3):
        d.line([(c, y0 + 190), (c, y0 + 320)], fill=ICE + (110,), width=2)

    _glow_text(comp, (W - 56, 92), "WINTER  WONDER", _font("cinzel.ttf", 34),
               fill=(235, 248, 255, 255), glow=ICE, blur=8, anchor="rm")

    _brand_and_badge(comp, card_row)

    comp.alpha_composite(st["snow"])
    comp.alpha_composite(st["noise"])
    ImageDraw.Draw(comp).rectangle((20, 20, W - 21, H - 21), outline=(235, 248, 255, 200), width=2)

    buf = io.BytesIO()
    comp.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


def _brand_and_badge(comp, card_row):
    """Rush Cricket logo + RUSH / CRICKET top-left (like normal cards), playstyle badge under it."""
    import card_full as cf
    x, y, size = 50, 44, 96
    try:
        lg = cf._brand_logo()
        if lg is not None:
            halo = Image.new("RGBA", comp.size, (0, 0, 0, 0))
            ImageDraw.Draw(halo).ellipse((x - 8, y - 8, x + size + 8, y + size + 8), fill=ICE + (150,))
            comp.alpha_composite(halo.filter(ImageFilter.GaussianBlur(10)))
            del halo
            ImageDraw.Draw(comp).ellipse((x - 4, y - 4, x + size + 4, y + size + 4),
                                         outline=ICE_HI + (255,), width=3)
            comp.alpha_composite(lg.resize((size, size), Image.LANCZOS), (x, y))
        d = ImageDraw.Draw(comp)
        tx = x + size + 16
        d.text((tx, y + 36), "RUSH", font=_font(ci.FONT_DISPLAY_BOLD, 40), fill=(240, 250, 255, 255), anchor="lm")
        d.text((tx, y + 72), "CRICKET", font=_font(ci.FONT_LABEL, 26), fill=(185, 220, 245, 255), anchor="lm")
    except Exception as e:
        print(f"[card_winter] brand skipped ({type(e).__name__}: {e})")
    try:
        badge = ci._load_playstyle_badge(card_row, 1)
        if badge is not None:
            cxy = (x + size // 2, y + size + 80)
            glow = Image.new("RGBA", comp.size, (0, 0, 0, 0))
            ImageDraw.Draw(glow).ellipse((cxy[0] - 52, cxy[1] - 52, cxy[0] + 52, cxy[1] + 52), fill=ICE + (110,))
            comp.alpha_composite(glow.filter(ImageFilter.GaussianBlur(16)))
            del glow
            ci._draw_logo(comp, badge, cxy, 0, 0, 100, box=104)
    except Exception as e:
        print(f"[card_winter] badge skipped ({type(e).__name__}: {e})")


def _paste(base, layer, pos):
    """alpha_composite that also works when the layer sticks out past the top / left edge."""
    x, y = pos
    sx, sy = max(0, -x), max(0, -y)
    if sx >= layer.width or sy >= layer.height:
        return
    ex = min(layer.width, base.width - x)
    ey = min(layer.height, base.height - y)
    if ex <= sx or ey <= sy:
        return
    layer = layer.crop((sx, sy, ex, ey))
    base.alpha_composite(layer, (max(0, x), max(0, y)))
