"""Winter Wonder sample card (standalone preview, not used by the bot).

Usage:  python tools/winter_sample.py [cutout.png] [out.png]
If no cutout is given, a generic placeholder bust is drawn.
"""
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops, ImageOps

ROOT = Path(__file__).resolve().parent.parent
W, H = 1037, 1517
ICE = (150, 225, 255)
ICE_HI = (225, 248, 255)
NAVY = (8, 16, 42)
rnd = random.Random(7)


def font(name, size):
    return ImageFont.truetype(str(ROOT / name), size)


def cover(im, w, h):
    r = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * r + 1), int(im.height * r + 1)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def grade(im, amt=0.55):
    """Map luminance onto a navy -> ice gradient and blend (unifies colours)."""
    rgb = im.convert("RGB")
    g = ImageOps.grayscale(rgb)
    toned = ImageOps.colorize(g, black=NAVY, mid=(60, 120, 190), white=ICE_HI)
    return Image.blend(rgb, toned, amt)


def radial(size, center, radius, color, power=2.0):
    w, h = size
    m = Image.new("L", (w // 4, h // 4))
    px = m.load()
    cx, cy, r = center[0] / 4, center[1] / 4, radius / 4
    for y in range(m.height):
        for x in range(m.width):
            d = math.hypot(x - cx, y - cy) / r
            px[x, y] = int(255 * max(0.0, 1 - d) ** power)
    m = m.resize(size, Image.BILINEAR)
    layer = Image.new("RGBA", size, color + (0,))
    layer.putalpha(m)
    return layer


def glow_text(base, xy, text, fnt, fill, glow, blur, anchor="mm", alpha=255):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text(xy, text, font=fnt, fill=glow + (alpha,), anchor=anchor)
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))
    d2 = ImageDraw.Draw(base)
    d2.text(xy, text, font=fnt, fill=fill, anchor=anchor)


def crack_lines(size, origin, n, seed, length=(250, 650)):
    r = random.Random(seed)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
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
                branch(x, y, ang + r.choice([-1, 1]) * r.uniform(0.5, 1.0),
                       ln * 0.4, max(1, w - 1), depth + 1)
        d.line(pts, fill=ICE_HI + (210,), width=w)

    for i in range(n):
        ang = (i / n) * 2 * math.pi + r.uniform(-0.2, 0.2)
        branch(origin[0], origin[1], ang, r.uniform(*length), 3, 0)
    glow = layer.filter(ImageFilter.GaussianBlur(6))
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.alpha_composite(glow)
    out.alpha_composite(glow)
    out.alpha_composite(layer)
    return out


def placeholder_player():
    """Generic bust silhouette with soft shading (stand-in for a real cutout)."""
    pw, ph = 900, 1150
    m = Image.new("L", (pw, ph), 0)
    d = ImageDraw.Draw(m)
    d.ellipse((330, 90, 570, 370), fill=255)                 # head
    d.rectangle((400, 330, 500, 470), fill=255)              # neck
    d.ellipse((90, 420, 810, 1000), fill=255)                # shoulders
    d.rectangle((150, 700, 750, ph), fill=255)               # torso
    m = m.filter(ImageFilter.GaussianBlur(3))
    body = Image.new("RGB", (pw, ph), (22, 34, 66))
    shade = radial((pw, ph), (380, 300), 700, (120, 150, 200), 1.6)
    body = body.convert("RGBA")
    body.alpha_composite(shade)
    # jersey stripe detail
    sd = ImageDraw.Draw(body)
    for i in range(-6, 12):
        sd.line([(i * 90, ph), (i * 90 + 500, 450)], fill=(40, 60, 105, 255), width=14)
    body.putalpha(m)
    return body


def main():
    cut_path = sys.argv[1] if len(sys.argv) > 1 else None
    out_path = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "generated" / "winter_sample.png")

    # 1. background, graded to icy palette
    bg = cover(Image.open(ROOT / "winter_bg.jpg"), W, H)
    bg = grade(bg, 0.35).convert("RGBA")
    dark = Image.new("RGBA", (W, H), NAVY + (90,))
    bg.alpha_composite(dark)

    burst_c = (W // 2, 560)
    bg.alpha_composite(radial((W, H), burst_c, 620, ICE, 1.8))
    bg.alpha_composite(radial((W, H), burst_c, 260, ICE_HI, 2.2))

    # 2. light streaks from the burst
    streaks = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(streaks)
    for i in range(26):
        a = rnd.uniform(0, 2 * math.pi)
        ln = rnd.uniform(500, 1100)
        sd.line([burst_c, (burst_c[0] + math.cos(a) * ln, burst_c[1] + math.sin(a) * ln)],
                fill=ICE_HI + (rnd.randint(25, 70),), width=rnd.randint(2, 6))
    bg.alpha_composite(streaks.filter(ImageFilter.GaussianBlur(4)))

    # 3. frost cracks behind the player
    bg.alpha_composite(crack_lines((W, H), burst_c, 7, 3, (200, 480)))

    # 4. huge OVR behind the player
    ovr = "94"
    big = font("display_bold.ttf", 470)
    gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(gl).text((W // 2, 330), ovr, font=big, fill=(0, 0, 0, 0), anchor="mm",
                            stroke_width=10, stroke_fill=ICE + (255,))
    bg.alpha_composite(gl.filter(ImageFilter.GaussianBlur(18)))
    ImageDraw.Draw(bg).text((W // 2, 330), ovr, font=big, fill=(150, 210, 245, 45), anchor="mm",
                            stroke_width=5, stroke_fill=ICE_HI + (235,))

    # 5. player: grade + rim light + bottom fade
    player = Image.open(cut_path).convert("RGBA") if cut_path else placeholder_player()
    ph = 1060
    player = player.resize((int(player.width * ph / player.height), ph), Image.LANCZOS)
    px = (W - player.width) // 2
    py = 1290 - ph
    a = player.getchannel("A")
    graded = grade(player, 0.45).convert("RGBA")
    graded.putalpha(a)

    # rim light: edge of mask, shifted away from the light, blurred
    inner = a.filter(ImageFilter.MinFilter(15))
    edge = ImageChops.subtract(a, inner)
    edge = edge.filter(ImageFilter.GaussianBlur(5))
    rim = Image.new("RGBA", player.size, ICE_HI + (0,))
    rim.putalpha(edge.point(lambda v: min(255, int(v * 1.1))))

    fade = Image.new("L", player.size, 255)
    fd = ImageDraw.Draw(fade)
    f0, f1 = int(ph * 0.66), int(ph * 0.95)
    for y in range(f0, player.height):
        t = min(1.0, (y - f0) / (f1 - f0))
        fd.line([(0, y), (player.width, y)], fill=int(255 * (1 - t) ** 1.4))
    graded.putalpha(ImageChops.multiply(a, fade))
    rim.putalpha(ImageChops.multiply(rim.getchannel("A"), fade))

    # outer glow halo so the player sits inside the light
    halo = Image.new("RGBA", player.size, ICE + (0,))
    halo.putalpha(ImageChops.multiply(a, fade).filter(ImageFilter.GaussianBlur(28)).point(lambda v: v // 2))

    comp = bg.copy()
    comp.alpha_composite(halo, (px, py))
    comp.alpha_composite(graded, (px, py))
    comp.alpha_composite(rim, (px, py))

    # 6. frost mist rising from the bottom
    mist = Image.new("L", (W // 8, H // 8), 0)
    md = ImageDraw.Draw(mist)
    for _ in range(140):
        x = rnd.uniform(-10, W / 8 + 10)
        y = rnd.uniform(H / 8 * 0.62, H / 8 + 10)
        rr = rnd.uniform(6, 18)
        md.ellipse((x - rr, y - rr * 0.6, x + rr, y + rr * 0.6), fill=rnd.randint(60, 140))
    mist = mist.filter(ImageFilter.GaussianBlur(5)).resize((W, H), Image.BICUBIC)
    mlayer = Image.new("RGBA", (W, H), (200, 230, 255, 0))
    mlayer.putalpha(mist)
    comp.alpha_composite(mlayer)

    # 10. vignette + grain + thin inset line
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).rectangle((0, 0, W, H), fill=255)
    vig = radial((W, H), (W // 2, H // 2), 1000, (0, 0, 0), 1.0).getchannel("A")
    vig = vig.point(lambda v: int((255 - v) * 0.55))
    vl = Image.new("RGBA", (W, H), (2, 6, 18, 0))
    vl.putalpha(vig)
    comp.alpha_composite(vl)

    # 7. frosted glass stats panel (blurs what's behind it)
    x0, y0, x1, y1 = 52, 1112, W - 52, H - 52
    region = comp.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(18))
    tint = Image.new("RGBA", region.size, (120, 170, 220, 70))
    region.alpha_composite(tint)
    shine = Image.new("L", region.size, 0)
    sh = ImageDraw.Draw(shine)
    for y in range(region.height):
        sh.line([(0, y), (region.width, y)], fill=int(60 * (1 - y / region.height) ** 2))
    sl = Image.new("RGBA", region.size, (255, 255, 255, 0))
    sl.putalpha(shine)
    region.alpha_composite(sl)
    pmask = Image.new("L", region.size, 0)
    ImageDraw.Draw(pmask).rounded_rectangle((0, 0, region.width - 1, region.height - 1), 30, fill=255)
    comp.paste(region, (x0, y0), pmask)

    # cracks continue into the panel (connects panel to background)
    pc = crack_lines((W, H), (W // 2, y0 - 40), 7, 11, (180, 420))
    pcm = Image.new("L", (W, H), 0)
    ImageDraw.Draw(pcm).rounded_rectangle((x0, y0, x1, y1), 30, fill=90)
    pc.putalpha(ImageChops.multiply(pc.getchannel("A"), pcm))
    comp.alpha_composite(pc)

    # glowing border + top edge
    edge_l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge_l)
    ed.rounded_rectangle((x0, y0, x1, y1), 30, outline=ICE + (200,), width=3)
    ed.line([(x0 + 60, y0), (x1 - 60, y0)], fill=ICE_HI + (255,), width=5)
    comp.alpha_composite(edge_l.filter(ImageFilter.GaussianBlur(7)))
    comp.alpha_composite(edge_l)

    # 8. panel text
    d = ImageDraw.Draw(comp)
    cx = W // 2
    glow_text(comp, (cx, y0 + 68), "PLAYER NAME", font("cinzel.ttf", 64),
              fill=(240, 250, 255, 255), glow=ICE, blur=10)
    d.text((cx, y0 + 122), "ALL-ROUNDER  ·  INDIA", font=font("label.ttf", 28),
           fill=(185, 220, 245, 255), anchor="mm")
    d.line([(x0 + 90, y0 + 156), (x1 - 90, y0 + 156)], fill=ICE + (160,), width=2)

    cols = [(x0 + (x1 - x0) * f) for f in (1 / 6, 3 / 6, 5 / 6)]
    stats = [("OVR", ovr), ("BAT", "92"), ("BOWL", "78")]
    for (lbl, val), c in zip(stats, cols):
        glow_text(comp, (c, y0 + 235), val, font("display_bold.ttf", 96),
                  fill=(255, 255, 255, 255), glow=ICE, blur=12)
        d.text((c, y0 + 305), lbl, font=font("label.ttf", 30), fill=(165, 215, 245, 255), anchor="mm")
    for c in (x0 + (x1 - x0) / 3, x0 + (x1 - x0) * 2 / 3):
        d.line([(c, y0 + 190), (c, y0 + 320)], fill=ICE + (110,), width=2)

    # header tag
    glow_text(comp, (cx, 70), "❄  WINTER WONDER  ❄" if False else "WINTER  WONDER", font("cinzel.ttf", 40),
              fill=(235, 248, 255, 255), glow=ICE, blur=8)

    # 9. foreground snow in front of everything (depth)
    snow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sn = ImageDraw.Draw(snow)
    for _ in range(220):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        r = rnd.choice([1.2, 1.6, 2.2, 2.8])
        sn.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rnd.randint(140, 230)))
    comp.alpha_composite(snow)
    bokeh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bk = ImageDraw.Draw(bokeh)
    for _ in range(14):
        x, y = rnd.uniform(0, W), rnd.choice([rnd.uniform(0, 380), rnd.uniform(900, H)])
        r = rnd.uniform(10, 26)
        bk.ellipse((x - r, y - r, x + r, y + r), fill=(230, 245, 255, rnd.randint(70, 130)))
    comp.alpha_composite(bokeh.filter(ImageFilter.GaussianBlur(7)))

    noise = Image.effect_noise((W, H), 28).convert("RGBA")
    noise.putalpha(18)
    comp.alpha_composite(noise)

    il = ImageDraw.Draw(comp)
    il.rectangle((20, 20, W - 21, H - 21), outline=(235, 248, 255, 200), width=2)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    comp.convert("RGB").save(out_path, quality=95)
    print(out_path)


if __name__ == "__main__":
    main()
