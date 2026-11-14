"""Role icons for the card stats panel, drawn by the bot itself (no image links).

    get_icon("BOWL") -> PIL RGBA image (384 x 384)

BOWL = cricket ball on fire, BAT = bat on fire, AR = bat and ball, WK = keeping glove.
Icons are cached in memory; to change a design edit its function below.
"""
from __future__ import annotations

import math
import threading

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = 384
S = 1024
_LOCK = threading.Lock()
_MEM: dict = {}


def _radial(size, inner, outer, cx, cy, r):
    """Radial gradient circle (RGBA) of radius r."""
    small = Image.new("RGB", (size // 8, size // 8))
    px = small.load()
    for y in range(small.height):
        for x in range(small.width):
            t = min(1.0, math.hypot(x - cx / 8, y - cy / 8) / (r / 8))
            px[x, y] = tuple(int(inner[i] + (outer[i] - inner[i]) * t) for i in range(3))
    return small.resize((size, size), Image.BICUBIC)


def _flame(img, cx, cy, length, width, angle):
    """Comet-style flame trail starting at (cx, cy) and streaming out along `angle` (degrees)."""
    def tongue(oy, l, w, ph):
        top, bot = [], []
        n = 60
        for i in range(n + 1):
            t = i / n
            prof = w * (1 - t) ** 0.85 * (0.75 + 0.25 * math.sin(math.pi * min(1.0, t * 2.2)))
            wav = math.sin(t * 11 + ph) * w * 0.10 * t + math.sin(t * 23 + ph * 2) * w * 0.04 * t
            yc = cy + oy * t * 1.4 + math.sin(t * 4 + ph) * w * 0.12 * t
            top.append((cx + l * t, yc - prof + wav))
            bot.append((cx + l * t, yc + prof + wav * 0.6))
        return top + bot[::-1]

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(tongue(0, length * 1.05, width * 1.25, 0.4), fill=(255, 80, 10, 170))
    layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.035)))
    groups = (
        ((200, 24, 8, 255), [(-0.55, 0.78, 0.55, 0.3), (0.5, 0.86, 0.6, 2.2), (0, 1.0, 1.0, 1.0)], 0.006),
        ((246, 92, 18, 255), [(-0.4, 0.62, 0.42, 1.4), (0.35, 0.7, 0.5, 2.8), (0, 0.84, 0.78, 0.2)], 0.005),
        ((255, 168, 32, 255), [(-0.2, 0.5, 0.36, 0.7), (0.15, 0.62, 0.5, 1.9)], 0.005),
        ((255, 228, 110, 255), [(0, 0.44, 0.36, 1.1)], 0.006),
        ((255, 252, 228, 255), [(0, 0.24, 0.22, 0.5)], 0.008),
    )
    for col, tongues, blur in groups:
        g = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(g)
        for oy, l, w, ph in tongues:
            gd.polygon(tongue(oy * width, length * l, width * w, ph), fill=col)
        layer.alpha_composite(g.filter(ImageFilter.GaussianBlur(S * blur)))
    d = ImageDraw.Draw(layer)
    for k, (t, off) in enumerate(((0.9, 1.0), (1.05, -0.7), (0.75, -1.2), (1.15, 0.4), (0.6, 1.3))):
        sx, sy = cx + length * t, cy + width * off
        r = S * (0.011 - k * 0.0015)
        d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(255, 196, 70, 235))
    img.alpha_composite(layer.rotate(-angle, resample=Image.BICUBIC, center=(cx, cy)))


def _ball(img, cx, cy, r):
    """Red leather ball with shading, white seam and stitches."""
    shade = _radial(S, (238, 70, 60), (110, 8, 14), cx - r * 0.35, cy - r * 0.4, r * 1.6)
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    ball = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ball.paste(shade, (0, 0), m)
    d = ImageDraw.Draw(ball)
    # seam: a curved band across the ball
    seam = []
    for i in range(61):
        t = -1 + i / 30
        seam.append((cx + t * r * 0.98 + r * 0.22 * (1 - t * t), cy - t * r * 0.98 * 0.9 + r * 0.22 * (1 - t * t)))
    for off, col, wd in ((0, (250, 246, 236, 255), int(r * 0.07)),):
        d.line([(x + off, y + off) for x, y in seam], fill=col, width=wd, joint="curve")
    for side in (-1, 1):
        for i in range(4, 58, 4):
            x, y = seam[i]
            x2, y2 = seam[i + 1]
            nx, ny = -(y2 - y), x2 - x
            nl = math.hypot(nx, ny) or 1
            nx, ny = nx / nl, ny / nl
            a = (x + nx * r * 0.06 * side, y + ny * r * 0.06 * side)
            b = (x + nx * r * 0.13 * side + (x2 - x) * 0.6, y + ny * r * 0.13 * side + (y2 - y) * 0.6)
            d.line([a, b], fill=(255, 240, 225, 230), width=max(2, int(r * 0.025)))
    ball.putalpha(ImageChops.multiply(ball.getchannel("A"), m))
    # highlight
    hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse((cx - r * 0.62, cy - r * 0.7, cx - r * 0.12, cy - r * 0.32), fill=(255, 255, 255, 110))
    hl = hl.filter(ImageFilter.GaussianBlur(r * 0.08))
    hl.putalpha(ImageChops.multiply(hl.getchannel("A"), m))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((cx - r, cy - r + r * 0.08, cx + r, cy + r + r * 0.08), fill=(0, 0, 0, 120))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(r * 0.08)))
    img.alpha_composite(ball)
    img.alpha_composite(hl)


def _bat(img, cx, cy, length, angle):
    """Willow bat centred on (cx, cy), rotated by `angle` degrees (0 = handle up)."""
    w = length * 0.2
    blade_l, handle_l = length * 0.68, length * 0.32
    top = cy - length / 2
    pts_blade = [(cx - w / 2, top + handle_l), (cx + w / 2, top + handle_l),
                 (cx + w / 2, top + length - w * 0.25), (cx + w * 0.3, top + length),
                 (cx - w * 0.3, top + length), (cx - w / 2, top + length - w * 0.25)]
    pts_handle = [(cx - w * 0.16, top), (cx + w * 0.16, top),
                  (cx + w * 0.16, top + handle_l + w * 0.1), (cx - w * 0.16, top + handle_l + w * 0.1)]

    def rot(pts):
        a = math.radians(angle)
        ca, sa = math.cos(a), math.sin(a)
        return [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca) for x, y in pts]

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(rot(pts_handle), fill=(30, 30, 36, 255))
    for k in range(6):                       # rubber grip rings
        y0 = top + handle_l * (0.1 + k * 0.14)
        d.line(rot([(cx - w * 0.16, y0), (cx + w * 0.16, y0)]), fill=(70, 70, 80, 255), width=int(w * 0.04))
    d.polygon(rot(pts_blade), fill=(226, 186, 120, 255))
    # wood grain + edge shading
    d.polygon(rot([(cx + w * 0.18, top + handle_l), (cx + w / 2, top + handle_l),
                   (cx + w / 2, top + length - w * 0.25), (cx + w * 0.3, top + length),
                   (cx + w * 0.18, top + length)]), fill=(186, 140, 78, 255))
    for g in (-0.25, -0.05, 0.1):
        d.line(rot([(cx + w * g, top + handle_l + w * 0.3), (cx + w * g, top + length - w * 0.3)]),
               fill=(200, 158, 96, 255), width=int(w * 0.03))
    # sticker band
    d.polygon(rot([(cx - w / 2, top + handle_l + w * 0.5), (cx + w / 2, top + handle_l + w * 0.5),
                   (cx + w / 2, top + handle_l + w * 0.85), (cx - w / 2, top + handle_l + w * 0.85)]),
              fill=(200, 30, 40, 255))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon(rot(pts_blade), fill=(0, 0, 0, 120))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(S * 0.012)))
    img.alpha_composite(layer)


def _glove(img, cx, cy, size):
    """Wicket-keeping glove: padded palm, webbed thumb, white with a coloured cuff."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    s = size
    body = (246, 246, 240, 255)
    edge = (170, 172, 180, 255)
    # fingers
    for i, (fx, fl) in enumerate(((-0.3, 0.42), (-0.1, 0.5), (0.1, 0.5), (0.3, 0.42))):
        x0, x1 = cx + s * (fx - 0.1), cx + s * (fx + 0.1)
        d.rounded_rectangle((x0, cy - s * fl - s * 0.1, x1, cy + s * 0.05), radius=int(s * 0.1), fill=body, outline=edge,
                            width=int(s * 0.02))
    # palm
    d.rounded_rectangle((cx - s * 0.42, cy - s * 0.15, cx + s * 0.42, cy + s * 0.42), radius=int(s * 0.16), fill=body,
                        outline=edge, width=int(s * 0.02))
    # thumb: a padded capsule angled out from the palm, joined by a web
    th = Image.new("RGBA", img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(th)
    td.rounded_rectangle((cx - s * 0.52, cy - s * 0.02, cx - s * 0.3, cy + s * 0.5), radius=int(s * 0.11), fill=body,
                         outline=edge, width=int(s * 0.02))
    th = th.rotate(-38, resample=Image.BICUBIC, center=(cx - s * 0.36, cy + s * 0.32))
    layer.alpha_composite(th, (0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((cx - s * 0.42, cy - s * 0.15, cx + s * 0.42, cy + s * 0.42), radius=int(s * 0.16), fill=body,
                        outline=edge, width=int(s * 0.02))
    d.arc((cx - s * 0.3, cy + s * 0.02, cx + s * 0.3, cy + s * 0.3), 20, 160, fill=edge, width=int(s * 0.018))
    # cuff
    d.rounded_rectangle((cx - s * 0.44, cy + s * 0.36, cx + s * 0.44, cy + s * 0.6), radius=int(s * 0.06),
                        fill=(22, 120, 210, 255))
    d.line([(cx - s * 0.4, cy + s * 0.48), (cx + s * 0.4, cy + s * 0.48)], fill=(255, 255, 255, 200), width=int(s * 0.025))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 130), (0, 0), layer.getchannel("A"))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(S * 0.015)))
    img.alpha_composite(layer)


def _draw(role: str) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if role == "BAT":
        _flame(img, S * 0.56, S * 0.44, S * 0.5, S * 0.17, -150)
        _bat(img, S * 0.5, S * 0.52, S * 0.78, 40)
    elif role == "AR":
        _bat(img, S * 0.44, S * 0.5, S * 0.8, -35)
        _flame(img, S * 0.66, S * 0.62, S * 0.34, S * 0.12, -165)
        _ball(img, S * 0.66, S * 0.62, S * 0.17)
    elif role == "WK":
        _glove(img, S * 0.52, S * 0.48, S * 0.5)
    else:                                    # BOWL / RBOWL
        _flame(img, S * 0.66, S * 0.64, S * 0.62, S * 0.17, -148)
        _ball(img, S * 0.66, S * 0.64, S * 0.19)
    bbox = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if bbox:                                 # trim and centre in a square
        img = img.crop(bbox)
        side = max(img.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = sq
    return img.resize((OUT, OUT), Image.LANCZOS)


def get_icon(role) -> Image.Image | None:
    key = str(role or "").upper().strip()
    key = {"RBOWL": "BOWL", "BOWLER": "BOWL", "BATTER": "BAT", "ALLROUNDER": "AR", "KEEPER": "WK"}.get(key, key)
    if key not in ("BAT", "BOWL", "AR", "WK"):
        return None
    with _LOCK:
        hit = _MEM.get(key)
        if hit is None:
            hit = _MEM[key] = _draw(key)
        return hit
