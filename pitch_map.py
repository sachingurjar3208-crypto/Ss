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
import random

ROWS = ("Short", "Good", "Full", "Yorker")          # far end -> batter's feet
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
    "wrong":   {"0": 1.5, "1": 1.15, "2": 0.9, "3": 0.8, "4": 0.5, "6": 0.4, "W": 1.5},
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
               hand: str, rng: random.Random | None = None):
    """Everything views needs for one ball. None when the feature is off or the
    shot/delivery is unknown. Keys: result, label, landing, shot_col, mod."""
    if not ENABLED:
        return None
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
_W, _H = 600, 640
_S = 2
_PITCH_X0, _PITCH_X1 = 170, 430
_PITCH_Y0, _PITCH_Y1 = 110, 530
_COL_W = (_PITCH_X1 - _PITCH_X0) / len(COLS)
_ROW_H = (_PITCH_Y1 - _PITCH_Y0) / len(ROWS)
# Unit direction from the batter for each compass key (RHB: off side on the left).
DIR_VEC = {
    "D": (0.0, -1.0), "DL": (-0.7, -0.7), "L": (-1.0, -0.2), "UL": (-0.8, 0.5),
    "U": (0.0, 1.0), "UR": (0.8, 0.5), "R": (1.0, -0.2), "DR": (0.7, -0.7),
}
_RESULT_COL = {"perfect": (60, 220, 110), "partial": (255, 196, 0), "wrong": (232, 72, 72)}


def _col_index(bcol: int, hand: str) -> int:
    """Screen column index for a batter-frame column (mirrored for LHB)."""
    ci = COLS.index(bcol)
    return ci if hand == "R" else len(COLS) - 1 - ci


def _cell_center(row: str, bcol: int, hand: str):
    """Pixel centre of a (row, batter-frame col) cell. Batter at the bottom,
    off side on the batter's left (screen left for RHB, screen right for LHB)."""
    ri = ROWS.index(row)
    ci = _col_index(bcol, hand)
    x = _PITCH_X0 + (ci + 0.5) * _COL_W
    y = _PITCH_Y0 + (ri + 0.5) * _ROW_H
    return x, y


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size * _S)
    except Exception:
        try:
            return ImageFont.load_default(size * _S)
        except Exception:
            return ImageFont.load_default()


def pitch_png(batter: str, hand: str, shot_button: str, direction: str | None,
              timing: dict, delivery_name: str = "", fielder_zone: str | None = None,
              outcome_text: str = "") -> bytes | None:
    """Top-down pitch picture for one ball. None if drawing fails."""
    if timing is None:
        return None
    try:
        from PIL import Image, ImageDraw
        S = _S
        img = Image.new("RGB", (_W * S, _H * S), (22, 26, 36))
        d = ImageDraw.Draw(img)

        def box(x0, y0, x1, y1, **kw):
            d.rectangle([x0 * S, y0 * S, x1 * S, y1 * S], **kw)

        # Outfield and pitch
        box(0, 0, _W, _H, fill=(40, 118, 64))
        box(_PITCH_X0 - 12, _PITCH_Y0 - 10, _PITCH_X1 + 12, _PITCH_Y1 + 10, fill=(196, 170, 120))

        # Cell grid
        for ri in range(len(ROWS)):
            for ci in range(len(COLS)):
                x0 = _PITCH_X0 + ci * _COL_W
                y0 = _PITCH_Y0 + ri * _ROW_H
                d.rectangle([x0 * S, y0 * S, (x0 + _COL_W) * S, (y0 + _ROW_H) * S],
                            outline=(120, 96, 64), width=S)
        # Row names on the left edge (short at top, yorker at the crease)
        for ri, row in enumerate(ROWS):
            y = _PITCH_Y0 + (ri + 0.5) * _ROW_H
            d.text(((_PITCH_X0 - 18) * S, y * S), row, font=_font(13),
                   fill=(230, 230, 230), anchor="rm")

        # Stumps and crease at the batter's end
        for dx in (-8, 0, 8):
            box(_W / 2 + dx - 1.5, _PITCH_Y1 + 14, _W / 2 + dx + 1.5, _PITCH_Y1 + 30,
                fill=(245, 245, 245))
        box(_PITCH_X0 - 40, _PITCH_Y1 + 18, _PITCH_X1 + 40, _PITCH_Y1 + 20, fill=(240, 240, 240))

        # Perfect zone for this shot (green outline)
        row_list = SHOT_ROWS.get(shot_button, ())
        if shot_button in NO_DIRECTION or direction not in DIR_COL:
            zone_cols = [timing.get("shot_col", 0)]
        else:
            zone_cols = [timing["shot_col"]]
        for col in zone_cols:
            ci = _col_index(col if col in COLS else 0, hand)
            x0 = _PITCH_X0 + ci * _COL_W
            ys = [ROWS.index(r) for r in row_list] if row_list else [0]
            y0 = _PITCH_Y0 + min(ys) * _ROW_H
            y1 = _PITCH_Y0 + (max(ys) + 1) * _ROW_H
            d.rounded_rectangle([(x0 + 3) * S, (y0 + 3) * S, (x0 + _COL_W - 3) * S, (y1 - 3) * S],
                                radius=6 * S, outline=(60, 220, 110), width=3 * S)

        # Batter stands at the bottom facing up (straight = up). Each shot
        # direction points somewhere on the ground around him.
        bx, by = _W / 2, _PITCH_Y1 + 60
        if direction in DIR_VEC:
            vx, vy = DIR_VEC[direction]
            vx = vx if hand == "R" else -vx          # mirror for a LEFT-hander
            reach = 230 if fielder_zone in DIR_VEC or fielder_zone is None else 150
            tx, ty = bx + vx * reach, by + vy * reach
            # the shot line
            d.line([bx * S, by * S, tx * S, ty * S], fill=(70, 150, 255), width=4 * S)
            if fielder_zone:                          # a fielder is there: catch chance
                r = 13
                d.ellipse([(tx - r) * S, (ty - r) * S, (tx + r) * S, (ty + r) * S],
                          fill=(232, 72, 72), outline=(255, 255, 255), width=2 * S)
            else:
                d.text((tx * S, ty * S), "no fielder", font=_font(12),
                       fill=(255, 220, 120), anchor="mm")

        # Landing spot (red dot)
        lr, lc = timing["landing"]
        lx, ly = _cell_center(lr, lc, hand)
        d.ellipse([(lx - 11) * S, (ly - 11) * S, (lx + 11) * S, (ly + 11) * S],
                  fill=(255, 255, 255), outline=(232, 72, 72), width=4 * S)
        d.ellipse([(bx - 9) * S, (by - 9) * S, (bx + 9) * S, (by + 9) * S],
                  fill=(255, 255, 255), outline=(0, 0, 0), width=2 * S)

        # Batter's side labels (off side on the batter's left)
        off_left = hand == "R"
        d.text(((22 if off_left else _W - 22) * S, _H / 2 * S), "OFF",
               font=_font(14), fill=(200, 205, 215), anchor="mm")
        d.text(((_W - 22 if off_left else 22) * S, _H / 2 * S), "LEG",
               font=_font(14), fill=(200, 205, 215), anchor="mm")

        # Header and result
        col = _RESULT_COL.get(timing["result"], (255, 255, 255))
        d.text((20 * S, 18 * S), timing["label"], font=_font(26), fill=col, anchor="la")
        sub = f"{batter} · {hand}HB · {shot_button}" + (f" {direction}" if direction else "")
        d.text((20 * S, 52 * S), sub, font=_font(16), fill=(210, 214, 224), anchor="la")
        if delivery_name:
            d.text((20 * S, 74 * S), f"Ball: {delivery_name}", font=_font(15),
                   fill=(180, 186, 200), anchor="la")
        if outcome_text:
            d.text((_W / 2 * S, _H - 26 * S), outcome_text, font=_font(18),
                   fill=(255, 255, 255), anchor="mm")

        buf = io.BytesIO()
        img = img.resize((_W, _H), Image.LANCZOS)
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception as e:           # never break a match over a picture
        print(f"[pitch map] skipped ({type(e).__name__}: {e})")
        return None
