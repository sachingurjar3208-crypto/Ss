"""pitch_map.py
--------------
Where the ball landed on the pitch, whether the batter's shot matched it,
and a top-down picture of it.

Frames
  * Bowler frame: the landing column is measured as a bowler sees it. Negative
    = outside off for a RIGHT-hand batter.
  * Batter frame: negative = OFF side for the batter in the crease, so the
    same column means the mirror image for a LEFT-hand batter.
  * Shot directions (Cover, Square Leg, ...) are always in the batter frame.

Rows (top of picture = far end, bottom = batter's feet)
  Short, Good, Full, Yorker.

Columns (batter frame) -2 .. +2. -2 outside off, -1 off, 0 middle/straight,
+1 leg, +2 wide leg.

Timing
  perfect : landing row is right for the shot AND the column matches the
            shot direction.
  partial : one column off, or the row is wrong but the column matches.
  wrong   : anything else.

The result changes the outcome weights through `timing_mod` (see
logic.calculate_outcome). Fielders still decide catches and saves; this only
decides how well the ball was hit.
"""
from __future__ import annotations

import io
import os
import random

ROWS = ("Yorker", "Full", "Good", "Short")          # batter's crease -> bowler's end
COLS = (-2, -1, 0, 1, 2)                              # batter frame

# Batter-frame column of each compass direction (same keys as data.DIRECTIONS).
DIR_COL = {"UL": -2, "L": -2, "DL": -1, "D": 0, "U": 0, "UR": 2, "R": 2, "DR": 1}

# Where a delivery is aimed, in the BOWLER frame: (rows, cols). The landing
# spot is picked at random from these, so each ball is a little different.
DELIVERY_ZONES: dict[str, tuple[tuple[str, ...], tuple[int, ...]]] = {
    "Yorker":      (("Yorker",), (-1, 0, 1)),
    "Full":        (("Full",), (-2, -1, 0, 1)),
    "Good Length": (("Good",), (-1, 0, 1)),
    "Good":        (("Good",), (-1, 0, 1)),
    "Swing":       (("Good", "Full"), (-2, -1)),       # outswing: away from a RHB
    "Inswing":     (("Good", "Full"), (0, 1)),          # inswing: into a RHB
    "Outswing":    (("Good", "Full"), (-2, -1)),
    "Bouncer":     (("Short",), (-1, 0, 1)),
    "Fast":        (("Good",), (-1, 0, 1)),
    "Slow":        (("Good",), (-1, 0, 1)),
    "Off Break":   (("Good",), (-1, 0)),
    "Doosra":      (("Good",), (0, 1)),
    "Carrom Ball": (("Good", "Full"), (-1, 0)),
    "Arm Ball":    (("Full",), (-1, 0, 1)),
    "Top Spin":    (("Good",), (-1, 0, 1)),
    "Leg Break":   (("Good",), (0, 1)),
    "Googly":      (("Good",), (0, 1, 2)),
    "Flipper":     (("Full",), (-1, 0, 1)),
    "Drift Ball":  (("Good",), (-2, -1)),
    "Slider":      (("Full",), (0, 1)),
    "Top Spinner": (("Good",), (-1, 0, 1)),
    # button labels (what the batter/bowler actually presses)
    "Offspin":     (("Good",), (-1, 0)),
    "Legspin":     (("Good",), (0, 1)),
    "Carrom":      (("Good", "Full"), (-1, 0)),
    "Topspin":     (("Good",), (-1, 0, 1)),
    "Drifter":     (("Good",), (-2, -1)),
}
DEFAULT_DELIVERY = (("Good",), (-1, 0, 1))

# Rows where each shot is played well. Lofted shots are row-free: the loft
# can clear the ball from anywhere; only the direction matters.
SHOT_ROWS: dict[str, tuple[str, ...]] = {
    "Drive":         ("Full", "Yorker"),
    "Lofted":        ROWS,
    "Flick":         ("Good", "Short"),
    "Pull":          ("Short",),
    "Cut":           ("Good", "Short"),
    "Sweep":         ("Good",),
    "Reverse-Sweep": ("Good", "Full"),
    "Defend":        ("Good", "Full"),
}
# Defensive shots have no direction, so they are judged on the row only.
NO_DIRECTION = {"Defend"}

# Weight multipliers for each timing result (applied via timing_mod).
TIMING_MOD: dict[str, dict[str, float]] = {
    "perfect": {"0": 0.6, "1": 1.0, "2": 1.0, "3": 1.0, "4": 1.5, "6": 1.6, "W": 0.6},
    "partial": {"0": 1.0, "1": 1.0, "2": 1.0, "3": 1.0, "4": 1.0, "6": 1.0, "W": 1.0},
    "wrong":   {"0": 1.6, "1": 1.15, "2": 0.8, "3": 0.6, "4": 0.2, "6": 0.06, "W": 1.6},
}
TIMING_LABEL = {
    "perfect": "PERFECT TIMING",
    "partial": "PARTIAL CONTACT",
    "wrong":   "WRONG LINE",
}

ENABLED = True


def batter_hand(player) -> str:
    """'R' or 'L'. Unknown (old cards, CPU sides) counts as right-handed."""
    hand = None
    try:
        hand = player.get("batting_hand") if isinstance(player, dict) else None
    except Exception:
        hand = None
    return "L" if str(hand or "").upper().startswith("L") else "R"


# Left/right mirror of every direction / fielding zone key. Direction keys on the
# batting grid and the fielding zones are written for a RIGHT-hand batter (DL = Cover,
# DR = Mid-wicket, L = Point, R = Square Leg ...). For a LEFT-hand batter the same
# screen position is the opposite shot, so everything is mirrored with this map.
_MIRROR = {"L": "R", "R": "L", "UL": "UR", "UR": "UL", "DL": "DR", "DR": "DL", "IL": "IR", "IR": "IL"}


def mirror_dir(key):
    """The left/right mirror of a direction or zone key (centre keys stay as they are)."""
    return _MIRROR.get(key, key)


def mirror_field(counts, hand: str):
    """A {zone_key: count} field seen from a LEFT-hand batter: left and right swap.
    Right-hand batter (or no field): returned unchanged."""
    if hand != "L" or not counts:
        return counts
    return {mirror_dir(k): v for k, v in counts.items()}


def to_batter_col(bowler_col: int, hand: str) -> int:
    """A bowler-frame column in batter frame (a mirror for a LEFT-hand batter)."""
    return bowler_col if hand == "R" else -bowler_col


def pick_landing(delivery_button: str, hand: str, rng: random.Random | None = None):
    """Random landing spot for this delivery, as (row, batter-frame col)."""
    rng = rng or random
    rows, cols = DELIVERY_ZONES.get(delivery_button, DEFAULT_DELIVERY)
    row = rng.choice(rows)
    bcol = rng.choice(cols)
    return row, to_batter_col(bcol, hand)


def judge(landing, shot_button: str, direction: str | None):
    """(result, shot_col) where result is 'perfect', 'partial' or 'wrong'.
    direction is the compass key of the shot (None for a defensive shot)."""
    row, col = landing
    rows_ok = SHOT_ROWS.get(shot_button)
    if rows_ok is None:
        return None, None                       # unknown shot: no timing effect
    if shot_button in NO_DIRECTION or direction not in DIR_COL:
        row_ok = row in rows_ok
        return ("perfect" if row_ok and col == 0 else "partial" if row_ok else "wrong"), col
    shot_col = DIR_COL[direction]
    row_ok = row in rows_ok
    diff = abs(col - shot_col)
    if row_ok and diff == 0:
        return "perfect", shot_col
    if (row_ok and diff == 1) or (not row_ok and diff == 0):
        return "partial", shot_col
    return "wrong", shot_col


def timing_for(delivery_button: str, shot_button: str, direction: str | None,
               hand: str, rng: random.Random | None = None, landing=None):
    """Everything views needs for one ball. None when the feature is off or the
    shot/delivery is unknown. `landing` is the spot shown to the batter before
    the shot (game.pending_landing); when None a new one is picked.
    Keys: result, label, landing, shot_col, mod."""
    if not ENABLED:
        return None
    if landing is None:
        landing = pick_landing(delivery_button, hand, rng)
    result, shot_col = judge(landing, shot_button, direction)
    if result is None:
        return None
    return {
        "result": result,
        "label": TIMING_LABEL[result],
        "landing": landing,
        "shot_col": shot_col,
        "mod": dict(TIMING_MOD[result]),
    }


# ── Picture ──────────────────────────────────────────────────────────────────
# View from above, camera behind the bowler (same as the wagon wheel and the
# shot buttons): batter at the TOP facing the camera. ONE rule for everything:
#   RHB: OFF side = screen LEFT,  LEG side = screen RIGHT, batter stands RIGHT of the stumps.
#   LHB: exact mirror image (OFF = screen RIGHT, batter stands LEFT of the stumps).
_W, _H = 600, 800
_S = 2
_PX0, _PX1 = 170, 430                  # pitch left/right
_PY0, _PY1 = 262, 712                  # pitch top (batter) / bottom (bowler)
_CX = (_PX0 + _PX1) / 2                # middle line of the pitch, always
_COL_W = (_PX1 - _PX0) / len(COLS)
# Length zones are NOT spread over the whole pitch. They start at the batter's
# crease and use their real size (metres) on a 20.12 m pitch, so they cover about
# half the pitch and the rest of it is plain: Yorker 0-2 m, Full 2-4 m, Good 4-8 m,
# Short 8-11 m.
_ZONE_M = {"Yorker": 2.0, "Full": 2.0, "Good": 4.0, "Short": 3.0}
_PX_PER_M = (_PY1 - _PY0) / 20.12
_ZY0 = _PY0 + 6                        # zones start at the batting crease
_ROW_HT = {r: _ZONE_M[r] * _PX_PER_M for r in ROWS}
_ROW_TOP = {}
_acc = _ZY0
for _r in ROWS:
    _ROW_TOP[_r] = _acc
    _acc += _ROW_HT[_r]
_ZY1 = _acc                            # bottom edge of the Short zone
_STUMP_H = 66                          # batter's-end stumps, about knee high on the batter
_STUMP_GAP = 12
_RESULT_COL = {"perfect": (60, 220, 110), "partial": (255, 196, 0), "wrong": (232, 72, 72)}

# Unit direction from the batter's crease (screen coords, RHB: off = LEFT).
# Same screen layout as the shot buttons and the wagon wheel.
DIR_VEC = {
    "D": (0.0, 1.0), "DL": (-0.7, 0.7), "L": (-1.0, 0.2), "UL": (-0.8, -0.5),
    "U": (0.0, -1.0), "UR": (0.8, -0.5), "R": (1.0, 0.2), "DR": (0.7, 0.7),
}


def _col_x(bcol: int, hand: str) -> float:
    """Screen x (centre of a column) for a batter-frame column."""
    ci = COLS.index(bcol)
    if hand == "L":                       # LHB: off side on screen RIGHT
        return _PX1 - (ci + 0.5) * _COL_W
    return _PX0 + (ci + 0.5) * _COL_W     # RHB: off side on screen LEFT


def _row_y(row: str) -> float:
    return _ROW_TOP[row] + _ROW_HT[row] / 2


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size * _S)
    except Exception:
        try:
            return ImageFont.load_default(size * _S)
        except Exception:
            return ImageFont.load_default()


def _draw_base(d, S):
    """Grass, pitch, grid, row names, stumps at both ends and the crease lines."""
    d.rectangle([0, 0, _W * S, _H * S], fill=(40, 118, 64))
    d.rectangle([(_PX0 - 14) * S, (_PY0 - 12) * S, (_PX1 + 14) * S, (_PY1 + 12) * S],
                fill=(196, 170, 120))
    for row in ROWS:
        for ci in range(len(COLS)):
            x0 = _PX0 + ci * _COL_W
            y0 = _ROW_TOP[row]
            d.rectangle([x0 * S, y0 * S, (x0 + _COL_W) * S, (y0 + _ROW_HT[row]) * S],
                        outline=(120, 96, 64), width=S)
    # row names on the left (they read top = batter's end)
    for row in ROWS:
        d.text(((_PX0 - 20) * S, _row_y(row) * S), row, font=_font(13), fill=(230, 230, 230), anchor="rm")
    # creases
    d.line([(_PX0 - 14) * S, (_PY0 + 6) * S, (_PX1 + 14) * S, (_PY0 + 6) * S], fill=(240, 240, 240), width=2 * S)
    d.line([(_PX0 - 14) * S, (_PY1 - 6) * S, (_PX1 + 14) * S, (_PY1 - 6) * S], fill=(240, 240, 240), width=2 * S)
    # stumps at the bowler's end (bottom, small) ...
    for dx in (-8, 0, 8):
        d.rectangle([(_CX + dx - 2) * S, (_PY1 + 6) * S, (_CX + dx + 2) * S, (_PY1 + 30) * S],
                    fill=(245, 245, 245))
    d.line([(_CX - 9) * S, (_PY1 + 6) * S, (_CX + 9) * S, (_PY1 + 6) * S], fill=(240, 180, 40), width=2 * S)
    # ... and the batter's-end stumps (top): tall, about knee high on the batter
    base = _PY0 + 2
    for dx in (-_STUMP_GAP, 0, _STUMP_GAP):
        x = _CX + dx
        top = base - _STUMP_H
        d.rectangle([(x - 3) * S, top * S, (x + 3) * S, base * S], fill=(196, 198, 204))
        d.rectangle([(x - 3) * S, top * S, (x - 1) * S, base * S], fill=(250, 250, 252))   # lit edge
        d.rectangle([(x + 2) * S, top * S, (x + 3) * S, base * S], fill=(150, 152, 160))   # shaded edge
    for dx in (-_STUMP_GAP / 2, _STUMP_GAP / 2):                                           # bails
        d.line([(_CX + dx - 5) * S, (base - _STUMP_H - 2) * S, (_CX + dx + 5) * S, (base - _STUMP_H - 2) * S],
               fill=(240, 180, 40), width=2 * S)


def _bx(p, q):
    """Box from two corner points, normalised so mirrored coordinates still work."""
    return [min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])]


def _shade(col, f):
    """Lighten (f>0) or darken (f<0) an RGB colour."""
    if f >= 0:
        return tuple(int(c + (255 - c) * f) for c in col)
    return tuple(int(c * (1 + f)) for c in col)


_SPRITE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batter_rhb.png")
_SPRITE_H = 168                       # batter height on the picture (before supersampling)
_BATTER_DX = 12
_sprite_cache: dict = {}


def _batter_side(hand: str) -> int:
    """Screen side of the middle line the batter stands on. A RHB stands on the
    leg side = screen RIGHT of the stumps (his chest faces the off side, screen
    left). A LHB is the mirror image."""
    return 1 if hand == "R" else -1


def _batter_sprite(S: int, hand: str):
    """The batter photo-cutout (RHB, faces right) scaled for this picture, mirrored
    for a LHB. None when the file is missing, then the drawn figure is used."""
    key = (S, hand)
    if key not in _sprite_cache:
        try:
            from PIL import Image
            im = Image.open(_SPRITE_FILE).convert("RGBA")
            h = _SPRITE_H * S
            im = im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)
            if hand == "L":
                im = im.transpose(Image.FLIP_LEFT_RIGHT)
            _sprite_cache[key] = im
        except Exception as e:
            print(f"[pitch map] batter sprite not used ({type(e).__name__}: {e})")
            _sprite_cache[key] = None
    return _sprite_cache[key]


def _draw_batter(d, S, hand: str, img=None):
    """Batter beside the stumps: side-on stance, facing the bowler, bat in backlift.
    Uses the batter_rhb.png cutout (mirrored for a LHB) with a soft ground shadow."""
    spr = _batter_sprite(S, hand) if img is not None else None
    if spr is None:
        return _draw_batter_vector(d, S, hand, img)
    from PIL import Image, ImageDraw, ImageFilter
    cx = _CX + _batter_side(hand) * _BATTER_DX
    feet_y = _PY0 + 2
    # soft shadow on the ground under the feet
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).ellipse(
        [(cx - 34) * S, (feet_y - 7) * S, (cx + 40) * S, (feet_y + 9) * S], fill=(10, 30, 18, 150))
    lay = lay.filter(ImageFilter.GaussianBlur(4 * S))
    img.paste(lay, (0, 0), lay)
    img.paste(spr, (int(cx * S - spr.width / 2), int(feet_y * S - spr.height)), spr)


def _draw_batter_vector(d, S, hand: str, img=None):
    """Batter in the crease, side-on stance: knees bent, back arched, head forward,
    bat raised beside the head. RHB stands right of the middle line, LHB left of it
    (the figure is mirrored for LHB).

    Every part is drawn as a shaded round "tube" or "sphere" (dark rim, mid tone,
    soft highlight, light from the upper left on screen) so the figure has volume
    instead of looking like flat paper cut-outs. Pass `img` for a soft ground shadow."""
    side = 1 if hand == "R" else -1
    K = 1.12
    cx = _CX + side * 52
    feet_y = _PY0 - 2
    flip = side
    LX, LY = -0.62, -0.78                      # light direction on screen (upper left)

    def P(x, y):
        return ((cx + x * K * flip) * S, (feet_y + y * K) * S)

    def tube(a, b, w, col, outline=True):
        (x0, y0), (x1, y1) = P(*a), P(*b)
        r = w * K * S / 2
        passes = [(1.00, _shade(col, -0.38), 0.0),
                  (0.86, _shade(col, -0.16), 0.10),
                  (0.66, col, 0.18),
                  (0.34, _shade(col, 0.35), 0.30)]
        if not outline:
            passes = passes[1:]
        for k, c, off in passes:
            rr = r * k
            ox, oy = LX * r * off, LY * r * off
            d.line([x0 + ox, y0 + oy, x1 + ox, y1 + oy], fill=c, width=max(1, int(rr * 2)))
            for (px, py) in ((x0, y0), (x1, y1)):
                d.ellipse([px + ox - rr, py + oy - rr, px + ox + rr, py + oy + rr], fill=c)

    def ball(c, rad, col, flat=1.0):
        """Shaded sphere at local point c with local radius rad."""
        px, py = P(*c)
        r = rad * K * S
        for k, f, off in ((1.00, -0.40, 0.0), (0.88, -0.15, 0.12), (0.66, 0.0, 0.22), (0.34, 0.38, 0.34)):
            rr = r * k
            ox, oy = LX * r * off, LY * r * off
            d.ellipse([px + ox - rr, py + oy - rr * flat, px + ox + rr, py + oy + rr * flat],
                      fill=_shade(col, f))

    WHITE = (238, 240, 244)
    PAD = (228, 230, 236)
    SKIN = (226, 178, 140)
    GREEN, GREEN_DK = (36, 120, 78), (20, 70, 46)
    WOOD = (214, 170, 100)
    GLOVE = (245, 245, 245)
    SHOE = (46, 46, 52)

    # ground shadow (long, soft, falls to the lower right of the light)
    sh = (60, 100, 66)
    if img is not None:
        try:
            from PIL import Image, ImageDraw, ImageFilter
            lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay)
            sx, sy = P(8, -2)
            ld.ellipse([sx - 58 * K * S, sy - 7 * K * S, sx + 62 * K * S, sy + 13 * K * S],
                       fill=(10, 30, 18, 150))
            lay = lay.filter(ImageFilter.GaussianBlur(5 * S))
            img.paste(lay, (0, 0), lay)
            d = ImageDraw.Draw(img)
        except Exception:
            d.ellipse([P(-34, -4)[0], P(-34, -4)[1], P(40, 10)[0], P(40, 10)[1]], fill=sh)
    else:
        d.ellipse(_bx(P(-34, -4), P(40, 10)), fill=sh)

    # rear (far) leg: thigh, pad, shoe
    tube((8, -62), (18, -36), 17, WHITE)
    tube((18, -36), (15, -9), 15, PAD)
    ball((18, -36), 8.5, (214, 218, 226))                 # knee roll
    tube((15, -22), (15, -20), 17, _shade(PAD, -0.25))     # pad strap
    tube((12, -9), (22, -3), 11, SHOE)                      # shoe
    # rear arm (far)
    tube((2, -98), (12, -84), 9, _shade(WHITE, -0.12))
    tube((12, -84), (6, -90), 8, _shade(WHITE, -0.12))

    # torso: hips to shoulders, leaning forward with an arched back
    tube((4, -64), (-5, -97), 25, WHITE)
    tube((3, -80), (6, -90), 22, _shade(WHITE, -0.06))     # arched back
    tube((-3, -66), (-9, -92), 6, GREEN, outline=False)    # team stripe on the side

    # front leg: thigh, pad, shoe (nearer the camera, drawn after the torso)
    tube((-2, -62), (-22, -38), 17, WHITE)
    tube((-22, -38), (-14, -9), 15, PAD)
    ball((-22, -38), 8.5, (214, 218, 226))
    for t in (0.30, 0.55, 0.80):                            # pad ridges
        px = -22 + 8 * t
        py = -38 + 29 * t
        tube((px - 3, py), (px + 3, py), 3, _shade(PAD, -0.30), outline=False)
    tube((-17, -9), (-31, -3), 12, SHOE)
    ball((-30, -4), 5.5, _shade(SHOE, 0.15))

    # neck, head and helmet
    tube((-7, -100), (-11, -108), 9, SKIN)
    ball((-14, -116), 10, SKIN)
    # helmet shell: a dome over the top of the head, peak at the front
    ball((-13, -120), 11.5, GREEN)
    ball((-14, -113), 9.5, SKIN)                            # face shows below the shell
    tube((-24, -114), (-24, -104), 2, GREEN_DK, outline=False)   # grille bars
    tube((-20, -112), (-20, -102), 2, GREEN_DK, outline=False)
    tube((-26, -110), (-8, -110), 2, GREEN_DK, outline=False)
    tube((-26, -106), (-8, -106), 2, GREEN_DK, outline=False)
    tube((-27, -120), (-17, -123), 5, GREEN_DK, outline=False)   # peak

    # front arm, then both gloves holding the handle
    tube((-6, -97), (-17, -85), 9.5, WHITE)
    tube((-17, -85), (1, -92), 8.5, WHITE)

    # bat: handle (grip), then the blade rising up and back beside the head
    tube((3, -92), (12, -104), 5, (40, 40, 44))
    tube((12, -104), (36, -138), 12, WOOD)
    tube((12, -104), (36, -138), 3, _shade(WOOD, -0.30), outline=False)   # spine
    ball((2, -92), 5.5, GLOVE)
    ball((6, -95), 5.0, _shade(GLOVE, -0.08))


def _draw_fielder_marker(d, S, direction, zone_present, hand, origin):
    """A fielder (red dot) where the shot would go, or the words 'no fielder'."""
    if direction not in DIR_VEC:
        return
    vx, vy = DIR_VEC[direction]
    if hand == "L":
        vx = -vx
    ox, oy = origin
    reach = 300 if zone_present else 230
    tx, ty = ox + vx * reach, oy + vy * reach
    d.line([ox * S, oy * S, tx * S, ty * S], fill=(70, 150, 255), width=4 * S)
    if zone_present:
        d.ellipse([(tx - 13) * S, (ty - 13) * S, (tx + 13) * S, (ty + 13) * S],
                  fill=(232, 72, 72), outline=(255, 255, 255), width=2 * S)
    else:
        d.text((tx * S, ty * S), "no fielder", font=_font(14), fill=(255, 220, 120), anchor="mm")


def _batter_origin(hand):
    return (_CX + _batter_side(hand) * 46, _PY0 - 36)


def _landing_dot(d, S, landing, hand):
    lr, lc = landing
    lx, ly = _col_x(lc, hand), _row_y(lr)
    d.ellipse([(lx - 11) * S, (ly - 11) * S, (lx + 11) * S, (ly + 11) * S],
              fill=(255, 255, 255), outline=(232, 72, 72), width=4 * S)


def _png(img) -> bytes:
    from PIL import Image
    img = img.resize((_W, _H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def landing_png(batter: str, hand: str, delivery_name: str, landing) -> bytes | None:
    """Shown BEFORE the batter picks a shot: where this ball is going to pitch."""
    try:
        from PIL import Image, ImageDraw
        S = _S
        img = Image.new("RGB", (_W * S, _H * S), (22, 26, 36))
        d = ImageDraw.Draw(img)
        _draw_base(d, S)
        _draw_batter(d, S, hand, img)
        _landing_dot(d, S, landing, hand)
        d.text((20 * S, 18 * S), "BALL INCOMING", font=_font(26), fill=(255, 255, 255), anchor="la")
        d.text((20 * S, 52 * S), f"{batter} · {hand}HB", font=_font(16), fill=(210, 214, 224), anchor="la")
        d.text((20 * S, 74 * S), f"Ball: {delivery_name} · pitching {landing[0]}",
               font=_font(15), fill=(180, 186, 200), anchor="la")
        _side_labels(d, S, hand)
        return _png(img)
    except Exception as e:
        print(f"[pitch map] landing picture skipped ({type(e).__name__}: {e})")
        return None


def _side_labels(d, S, hand):
    off_right = hand == "L"
    d.text(((_W - 22 if off_right else 22) * S, _H / 2 * S), "OFF", font=_font(14), fill=(200, 205, 215), anchor="mm")
    d.text(((22 if off_right else _W - 22) * S, _H / 2 * S), "LEG", font=_font(14), fill=(200, 205, 215), anchor="mm")


def pitch_png(batter: str, hand: str, shot_button: str, direction: str | None,
              timing: dict, delivery_name: str = "", fielder_zone: str | None = None,
              outcome_text: str = "") -> bytes | None:
    """Result picture after the shot: the perfect LENGTH band (always centred on
    the middle line), the landing spot, the shot line and the fielder."""
    if timing is None:
        return None
    try:
        from PIL import Image, ImageDraw
        S = _S
        img = Image.new("RGB", (_W * S, _H * S), (22, 26, 36))
        d = ImageDraw.Draw(img)
        _draw_base(d, S)

        # Perfect length rows for this shot, drawn in the middle line only.
        rows = SHOT_ROWS.get(shot_button, ())
        if rows:
            idx = [ROWS.index(r) for r in rows]
            y0 = _ROW_TOP[ROWS[min(idx)]]
            y1 = _ROW_TOP[ROWS[max(idx)]] + _ROW_HT[ROWS[max(idx)]]
            band = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(band).rounded_rectangle(
                [(_CX - _COL_W / 2 - 6) * S, (y0 + 4) * S, (_CX + _COL_W / 2 + 6) * S, (y1 - 4) * S],
                radius=8 * S, fill=(60, 220, 110, 110), outline=(60, 220, 110, 255), width=3 * S)
            img.paste(band, (0, 0), band)
            d = ImageDraw.Draw(img)

        _draw_batter(d, S, hand, img)
        d = ImageDraw.Draw(img)
        _draw_fielder_marker(d, S, direction, fielder_zone is not None, hand, _batter_origin(hand))
        _landing_dot(d, S, timing["landing"], hand)
        _side_labels(d, S, hand)

        col = _RESULT_COL.get(timing["result"], (255, 255, 255))
        d.text((20 * S, 18 * S), timing["label"], font=_font(26), fill=col, anchor="la")
        sub = f"{batter} · {hand}HB · {shot_button}" + (f" {direction}" if direction else "")
        d.text((20 * S, 52 * S), sub, font=_font(16), fill=(210, 214, 224), anchor="la")
        if delivery_name:
            d.text((20 * S, 74 * S), f"Ball: {delivery_name}", font=_font(15), fill=(180, 186, 200), anchor="la")
        if outcome_text:
            d.text((_W / 2 * S, (_H - 22) * S), outcome_text, font=_font(18), fill=(255, 255, 255), anchor="mm")
        return _png(img)
    except Exception as e:           # never break a match over a picture
        print(f"[pitch map] skipped ({type(e).__name__}: {e})")
        return None
