"""wagon_wheel.py
----------------
One wagon-wheel picture for a FOUR or a SIX (nothing else gets a wheel).

    png = wagon_wheel_png(direction, outcome, meters=98, batter="Kohli")

  direction : the DIRECTIONS display name the batting UI recorded
              ("Cover", "Straight", ...). If it is None (CPU batter, old
              resumed game) a believable one is picked from `shot_internal`.
  outcome   : "4" or "6". Anything else returns None (no wheel).
  meters    : distance of the six. Shown ONLY for sixes.

Colours: six = gold (a curve that rises up the picture, like a lofted ball),
         four = blue (a straight line to the rope).

Returns PNG bytes, or None if nothing should be shown / drawing failed — the
caller must never let a picture problem break a match.
"""
from __future__ import annotations

import io
import math
import random

from data import DIRECTIONS_BY_NAME

# Screen angle (degrees, 0 = right, counter-clockwise). Same orientation as
# the batting buttons: straight at the bottom, off side left, leg side right,
# behind the batter at the top.
_ANGLE = {"D": -90, "DL": -135, "L": 180, "UL": 135, "U": 90, "UR": 45, "R": 0, "DR": -45}
_NAMES = {"D": "Straight", "DL": "Cover", "L": "Point", "UL": "Third Man",
          "U": "Behind", "UR": "Fine Leg", "R": "Square Leg", "DR": "Mid-wicket"}

_SIX  = (255, 196, 0)
_FOUR = (0, 190, 255)

# Believable direction when the batter did not pick one (CPU batter etc.).
_FALLBACK = {
    "Drive": ["D", "DL"], "Pull": ["R", "DR"], "Cut": ["L", "UL"],
    "Flick": ["UR", "DR"], "Sweep": ["R", "DR"], "Reverse-Sweep": ["U", "UL"],
    "Lofted": ["D", "DL", "DR", "R", "UR", "L"],
}

_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_W = _H = 700
_S = 2                                  # supersampling


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(_FONT, size * _S)
    except Exception:
        try:
            return ImageFont.load_default(size * _S)   # Pillow >= 10.1
        except Exception:
            return ImageFont.load_default()


def _resolve_key(direction: str | None, shot_internal: str | None) -> str | None:
    key = DIRECTIONS_BY_NAME.get(direction or "")
    if key in _ANGLE:
        return key
    pool = _FALLBACK.get(shot_internal or "")
    return random.choice(pool) if pool else None


def wagon_wheel_png(
    direction: str | None,
    outcome: str,
    meters: int | None = None,
    batter: str = "",
    shot_internal: str | None = None,
) -> bytes | None:
    if outcome not in ("4", "6"):
        return None
    try:
        return _draw(direction, outcome, meters, batter, shot_internal)
    except Exception as e:          # never break a match over a picture
        print(f"[wagon wheel] skipped ({type(e).__name__}: {e})")
        return None


def _draw(direction, outcome, meters, batter, shot_internal) -> bytes | None:
    from PIL import Image, ImageDraw

    key = _resolve_key(direction, shot_internal)
    if key is None:
        return None
    S = _S
    img = Image.new("RGB", (_W * S, _H * S), (18, 20, 28))
    d = ImageDraw.Draw(img)
    cx, cy = _W * S // 2, _H * S // 2 + 25 * S
    R = 245 * S

    # field
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(38, 112, 58), outline=(240, 240, 240), width=4 * S)
    for r in (200, 150, 100):
        d.ellipse([cx - r * S, cy - r * S, cx + r * S, cy + r * S], outline=(46, 128, 68), width=2 * S)
    r30 = 120 * S
    for a in range(0, 360, 12):                     # dashed 30-yard circle
        d.arc([cx - r30, cy - r30, cx + r30, cy + r30], a, a + 6, fill=(235, 235, 235), width=2 * S)
    d.rectangle([cx - 9 * S, cy - 48 * S, cx + 9 * S, cy + 48 * S], fill=(196, 170, 120))   # pitch

    for k, ang in _ANGLE.items():                   # faint spokes + labels
        a = math.radians(ang)
        d.line([cx, cy, cx + R * math.cos(a), cy - R * math.sin(a)], fill=(60, 140, 80), width=S)
        axis = (ang % 90 == 0)
        lx = cx + (R + (44 if axis else 70) * S) * math.cos(a)
        ly = cy - (R + (26 if axis else 40) * S) * math.sin(a)
        lx = min(max(lx, 52 * S), (_W - 52) * S)
        d.text((lx, ly), _NAMES[k], font=_font(13), fill=(170, 176, 190), anchor="mm")

    col = _SIX if outcome == "6" else _FOUR
    a = math.radians(_ANGLE[key])
    end = R + 16 * S if outcome == "6" else R
    ex, ey = cx + end * math.cos(a), cy - end * math.sin(a)

    def glow(points, widths):
        nonlocal img, d
        for w_, alpha in widths:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).line(points, fill=col + (alpha,), width=w_ * S, joint="curve")
            img.paste(layer, (0, 0), layer)
        d = ImageDraw.Draw(img)

    if outcome == "6":
        n = 36                                       # ground shadow (dashed)
        for i in range(0, n, 2):
            t0, t1 = i / n, (i + 1) / n
            d.line([cx + (ex - cx) * t0, cy + (ey - cy) * t0, cx + (ex - cx) * t1, cy + (ey - cy) * t1],
                   fill=(20, 70, 35), width=3 * S)
        lift = 0.55 * math.hypot(ex - cx, ey - cy) + 60 * S
        qx, qy = (cx + ex) / 2, (cy + ey) / 2 - lift     # control point pulled UP the picture
        pts = []
        for i in range(61):
            t = i / 60
            pts.append(((1 - t) ** 2 * cx + 2 * (1 - t) * t * qx + t * t * ex,
                        (1 - t) ** 2 * cy + 2 * (1 - t) * t * qy + t * t * ey))
        glow(pts, ((18, 35), (11, 80)))
        d.line(pts, fill=col, width=5 * S, joint="curve")
    else:
        glow([cx, cy, ex, ey], ((16, 40), (10, 80)))
        d.line([cx, cy, ex, ey], fill=col, width=5 * S)

    d.ellipse([ex - 9 * S, ey - 9 * S, ex + 9 * S, ey + 9 * S], fill=col, outline=(0, 0, 0), width=2 * S)
    d.ellipse([cx - 6 * S, cy - 6 * S, cx + 6 * S, cy + 6 * S], fill=(255, 255, 255))

    if outcome == "6" and meters:                    # metres: sixes only
        tx = cx + (end + 62 * S) * math.cos(a)
        ty = cy - (end + 62 * S) * math.sin(a)
        tx = min(max(tx, 70 * S), (_W - 70) * S)
        ty = min(max(ty, 60 * S), (_H - 30) * S)
        d.rounded_rectangle([tx - 52 * S, ty - 19 * S, tx + 52 * S, ty + 19 * S], radius=10 * S,
                            fill=_SIX, outline=(0, 0, 0), width=2 * S)
        d.text((tx, ty), f"{meters} m", font=_font(22), fill=(20, 20, 20), anchor="mm")

    d.text((24 * S, 22 * S), "SIX!" if outcome == "6" else "FOUR", font=_font(34), fill=col, anchor="la")
    sub = f"{batter} · {_NAMES[key]}" if batter else _NAMES[key]
    d.text((24 * S, 66 * S), sub, font=_font(18), fill=(210, 214, 224), anchor="la")
    for i, (lab, c) in enumerate((("Six", _SIX), ("Four", _FOUR))):   # legend
        y = 24 * S + i * 22 * S
        lx = (_W - 110) * S
        d.ellipse([lx, y + 4 * S, lx + 12 * S, y + 16 * S], fill=c, outline=(0, 0, 0), width=S)
        d.text((lx + 20 * S, y + 10 * S), lab, font=_font(15), fill=(210, 214, 224), anchor="lm")

    img = img.resize((_W, _H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
