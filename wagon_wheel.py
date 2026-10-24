"""wagon_wheel.py
----------------
Pictures for the ground:

  wagon_wheel_png(...)  one wagon wheel for a FOUR or a SIX (nothing else).
  catch_wheel_png(...)  a RED curved line from the batter to the fielder who
                        took a lofted catch, ending in OUT.
  field_png(...)        the 11 fielders placed on the ground (numbered 1-11).

Every picture can show the 11 fielders when the field is known. Returns PNG
bytes, or None if nothing should be shown / drawing failed. The caller must
never let a picture problem break a match.
"""
from __future__ import annotations

import io
import math
import random

from data import DIRECTIONS_BY_NAME
import field_logic as FL

# Screen angle (degrees, 0 = right, counter-clockwise). Same orientation as
# the batting buttons: straight at the bottom, off side left, leg side right,
# behind the batter at the top.
_ANGLE = {"D": -90, "DL": -135, "L": 180, "UL": 135, "U": 90, "UR": 45, "R": 0, "DR": -45}
_NAMES = {"D": "Straight", "DL": "Cover", "L": "Point", "UL": "Third Man",
          "U": "Behind", "UR": "Fine Leg", "R": "Square Leg", "DR": "Mid-wicket"}

# Inner-zone centres (degrees) and radius; outfield fielders sit at 182 px.
_INNER_ANGLE = {"IL": 150, "IS": -90, "IR": 30}
_INNER_R = 84
_OUTER_R = 182

_SIX = (255, 196, 0)
_FOUR = (0, 190, 255)
_FIELDER = (232, 72, 72)
_OUT = (255, 40, 40)

# Believable direction when the batter did not pick one (CPU batter etc.).
_FALLBACK = {
    "Drive": ["D", "DL"], "Pull": ["R", "DR"], "Cut": ["L", "UL"],
    "Flick": ["UR", "DR"], "Sweep": ["R", "DR"], "Reverse-Sweep": ["U", "UL"],
    "Lofted": ["D", "DL", "DR", "R", "UR", "L"],
}

def _ang(key: str, hand: str = "R") -> float:
    """Screen angle of a direction/zone key (batter frame). A LEFT-hand batter is
    the exact mirror image: Cover is bottom-RIGHT, Mid-wicket bottom-LEFT, and so
    on. RHB: off side = screen left."""
    base = _ANGLE[key] if key in _ANGLE else _INNER_ANGLE[key]
    return 180 - base if hand == "L" else base


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


# ── Fielder placement ────────────────────────────────────────────────────────
def fielder_points(counts, hand: str = "R") -> list[dict]:
    """Every fielder as {zone, x, y, num} in unscaled picture units.

    Fielders in one zone are fanned out along an arc so none overlap. The
    numbers run 1..11 in the editor's display order."""
    pts: list[dict] = []
    if not counts:
        return pts
    num = 0
    for zone in FL.ZONE_KEYS:
        n = int(counts.get(zone, 0))
        if n <= 0:
            continue
        if zone in FL.FIELD_POSITIONS:
            base, radius, step = _ang(zone, hand), _OUTER_R, 13.0
        else:
            base, radius, step = _ang(zone, hand), _INNER_R, 22.0
        for i in range(n):
            num += 1
            ang = math.radians(base + (i - (n - 1) / 2.0) * step * (-1 if hand == "L" else 1))
            pts.append({
                "zone": zone, "num": num,
                "x": math.cos(ang) * radius, "y": -math.sin(ang) * radius,
            })
    return pts


def _draw_fielders(d, cx, cy, S, counts, hand: str = "R") -> None:
    """One red dot per fielder, numbered 1-11."""
    for p in fielder_points(counts, hand):
        fx, fy = cx + p["x"] * S, cy + p["y"] * S
        d.ellipse([fx - 13 * S, fy - 13 * S, fx + 13 * S, fy + 13 * S],
                  fill=_FIELDER, outline=(255, 255, 255), width=2 * S)
        d.text((fx, fy), str(p["num"]), font=_font(12), fill=(255, 255, 255), anchor="mm")


def _catch_point(zone: str, counts, hand: str = "R") -> tuple[float, float] | None:
    """Picture position of the first fielder in `zone` (the one who takes it)."""
    for p in fielder_points(counts, hand):
        if p["zone"] == zone:
            return p["x"], p["y"]
    return None


# ── Base ground ──────────────────────────────────────────────────────────────
def _base(S, hand: str = "R"):
    """The ground (circle, 30-yard ring, pitch, spokes, labels). Shared by the
    wagon wheel, the catch wheel and the field picture. Returns (img, d, cx, cy, R)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (_W * S, _H * S), (18, 20, 28))
    d = ImageDraw.Draw(img)
    cx, cy = _W * S // 2, _H * S // 2 + 25 * S
    R = 245 * S

    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(38, 112, 58), outline=(240, 240, 240), width=4 * S)
    for r in (200, 150, 100):
        d.ellipse([cx - r * S, cy - r * S, cx + r * S, cy + r * S], outline=(46, 128, 68), width=2 * S)
    r30 = 120 * S
    for a in range(0, 360, 12):                     # dashed 30-yard circle
        d.arc([cx - r30, cy - r30, cx + r30, cy + r30], a, a + 6, fill=(235, 235, 235), width=2 * S)
    d.rectangle([cx - 9 * S, cy - 48 * S, cx + 9 * S, cy + 48 * S], fill=(196, 170, 120))   # pitch

    for k in _ANGLE:                                # faint spokes + labels (mirrored for LHB)
        ang = _ang(k, hand)
        a = math.radians(ang)
        d.line([cx, cy, cx + R * math.cos(a), cy - R * math.sin(a)], fill=(60, 140, 80), width=S)
        axis = (round(ang) % 90 == 0)
        lx = cx + (R + (44 if axis else 70) * S) * math.cos(a)
        ly = cy - (R + (26 if axis else 40) * S) * math.sin(a)
        lx = min(max(lx, 52 * S), (_W - 52) * S)
        d.text((lx, ly), _NAMES[k], font=_font(13), fill=(170, 176, 190), anchor="mm")
    return img, d, cx, cy, R


def _resolve_key(direction: str | None, shot_internal: str | None) -> str | None:
    key = DIRECTIONS_BY_NAME.get(direction or "")
    if key in _ANGLE:
        return key
    pool = _FALLBACK.get(shot_internal or "")
    return random.choice(pool) if pool else None


def _png(img) -> bytes:
    from PIL import Image
    img = img.resize((_W, _H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _count_line(counts) -> str:
    n = FL.total_count(counts)
    return f"{n} fielders" if n else ""


def field_png(field_positions, over_no: int | None = None, phase: str = "",
              max_allowed: int | None = None, hand: str = "R") -> bytes | None:
    """The current field as a picture: all fielders numbered on the ground.
    Returns PNG bytes, or None on any drawing problem."""
    if field_positions is None:
        return None
    try:
        S = _S
        img, d, cx, cy, R = _base(S, hand)
        _draw_fielders(d, cx, cy, S, field_positions, hand)
        d.ellipse([cx - 6 * S, cy - 6 * S, cx + 6 * S, cy + 6 * S], fill=(255, 255, 255))
        title = f"FIELD · OVER {over_no}" if over_no else "FIELD"
        d.text((24 * S, 22 * S), title, font=_font(30), fill=(255, 255, 255), anchor="la")
        outside = FL.outside_count(field_positions)
        sub = f"{outside} / {max_allowed} outside the circle" if max_allowed else f"{outside} outside the circle"
        if phase:
            sub = f"{phase} · {sub}"
        d.text((24 * S, 62 * S), sub, font=_font(17), fill=(210, 214, 224), anchor="la")
        d.text((24 * S, 86 * S), _count_line(field_positions) or "no fielders",
               font=_font(15), fill=(180, 186, 200), anchor="la")
        lx, y = (_W - 150) * S, 24 * S
        d.ellipse([lx, y + 4 * S, lx + 12 * S, y + 16 * S], fill=_FIELDER, outline=(0, 0, 0), width=S)
        d.text((lx + 20 * S, y + 10 * S), "Fielder", font=_font(15), fill=(210, 214, 224), anchor="lm")
        return _png(img)
    except Exception as e:          # never break a match over a picture
        print(f"[field image] skipped ({type(e).__name__}: {e})")
        return None


def wagon_wheel_png(
    direction: str | None,
    outcome: str,
    meters: int | None = None,
    batter: str = "",
    shot_internal: str | None = None,
    field_positions=None,
    hand: str = "R",
) -> bytes | None:
    if outcome not in ("4", "6"):
        return None
    try:
        return _draw(direction, outcome, meters, batter, shot_internal, field_positions, hand)
    except Exception as e:          # never break a match over a picture
        print(f"[wagon wheel] skipped ({type(e).__name__}: {e})")
        return None


def catch_wheel_png(
    direction: str | None,
    catch_zone: str,
    batter: str = "",
    field_positions=None,
    shot_internal: str | None = None,
    hand: str = "R",
) -> bytes | None:
    """A lofted shot that was caught: a RED curved line from the batter to the
    fielder who took it, with OUT at the end. None on any drawing problem."""
    try:
        from PIL import Image, ImageDraw
        S = _S
        img, d, cx, cy, R = _base(S, hand)
        _draw_fielders(d, cx, cy, S, field_positions, hand)

        pt = _catch_point(catch_zone, field_positions, hand)
        if pt is None:
            # No fielder recorded there: aim at the zone's own spot instead.
            a = math.radians(_ang(catch_zone, hand))
            rr = _OUTER_R if catch_zone in FL.FIELD_POSITIONS else _INNER_R
            pt = (math.cos(a) * rr, -math.sin(a) * rr)
        ex, ey = cx + pt[0] * S, cy + pt[1] * S

        # Quadratic curve: lifted up the picture like a high catch.
        dist = math.hypot(ex - cx, ey - cy)
        qx, qy = (cx + ex) / 2, (cy + ey) / 2 - (0.45 * dist + 40 * S)
        pts = []
        for i in range(61):
            t = i / 60
            pts.append(((1 - t) ** 2 * cx + 2 * (1 - t) * t * qx + t * t * ex,
                        (1 - t) ** 2 * cy + 2 * (1 - t) * t * qy + t * t * ey))

        for w_, alpha in ((18, 35), (11, 80)):
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).line(pts, fill=_OUT + (alpha,), width=w_ * S, joint="curve")
            img.paste(layer, (0, 0), layer)
        d = ImageDraw.Draw(img)
        d.line(pts, fill=_OUT, width=5 * S, joint="curve")

        # Batter end marker and the OUT badge at the fielder.
        d.ellipse([cx - 6 * S, cy - 6 * S, cx + 6 * S, cy + 6 * S], fill=(255, 255, 255))
        d.ellipse([ex - 16 * S, ey - 16 * S, ex + 16 * S, ey + 16 * S],
                  fill=_OUT, outline=(255, 255, 255), width=2 * S)
        d.text((ex, ey), "OUT", font=_font(12), fill=(255, 255, 255), anchor="mm")

        d.text((24 * S, 22 * S), "OUT · CAUGHT", font=_font(34), fill=_OUT, anchor="la")
        sub = f"{batter} · caught at {FL.zone_name(catch_zone)}" if batter else f"Caught at {FL.zone_name(catch_zone)}"
        d.text((24 * S, 66 * S), sub, font=_font(18), fill=(210, 214, 224), anchor="la")
        lx, y = (_W - 150) * S, 24 * S
        for i, (lab, c) in enumerate((("Caught", _OUT), ("Fielder", _FIELDER))):
            yy = y + i * 22 * S
            d.ellipse([lx, yy + 4 * S, lx + 12 * S, yy + 16 * S], fill=c, outline=(0, 0, 0), width=S)
            d.text((lx + 20 * S, yy + 10 * S), lab, font=_font(15), fill=(210, 214, 224), anchor="lm")
        return _png(img)
    except Exception as e:
        print(f"[catch wheel] skipped ({type(e).__name__}: {e})")
        return None


def _draw(direction, outcome, meters, batter, shot_internal, field_positions=None, hand="R") -> bytes | None:
    from PIL import Image, ImageDraw

    key = _resolve_key(direction, shot_internal)
    if key is None:
        return None
    S = _S
    img, d, cx, cy, R = _base(S, hand)

    if field_positions is not None:
        _draw_fielders(d, cx, cy, S, field_positions, hand)

    col = _SIX if outcome == "6" else _FOUR
    a = math.radians(_ang(key, hand))
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
        qx, qy = (cx + ex) / 2, (cy + ey) / 2 - lift
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
    _legend = [("Six", _SIX), ("Four", _FOUR)]
    if field_positions is not None:
        _legend.append(("Fielder", _FIELDER))
    for i, (lab, c) in enumerate(_legend):   # legend
        y = 24 * S + i * 22 * S
        lx = (_W - 110) * S
        d.ellipse([lx, y + 4 * S, lx + 12 * S, y + 16 * S], fill=c, outline=(0, 0, 0), width=S)
        d.text((lx + 20 * S, y + 10 * S), lab, font=_font(15), fill=(210, 214, 224), anchor="lm")

    return _png(img)
