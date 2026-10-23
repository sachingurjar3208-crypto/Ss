"""Field Placement (Phase 1: directions only, no named fielders).

The bowling captain sets, at the end of every over, which of the 8 outfield
directions of the existing DIRECTIONS grid (everything except "C"/Defend) have
a fielder OUTSIDE the circle. Ground shots played into a covered direction
score less and get out more often; shots into a gap score slightly more.

Nothing here touches the match: it is pure data + maths. game.py stores the
state, views.py asks the captain, logic.calculate_outcome() applies the
multipliers. If a match has no field (game.field_positions is None) every
function here is simply never reached and behaviour is exactly as before.

Phase 2 (NOT built): put a player name on each position and use that
player's FIE stat (card_stats.py). game.field_positions is therefore a
dict {direction_key: fielder_name_or_None}; Phase 1 always stores None as
the value, and get_field_modifier() already takes an optional
`fielder_stat` argument that is ignored for now.

NOTE: unrelated to engine.py's per-player "field_effects" (playstyle bonuses).
"""
from __future__ import annotations

from data import DIRECTIONS

# ── Feature switches ─────────────────────────────────────────────────────────
FIELD_FEATURE_ENABLED = True

# Open question #6 (decided: HIDDEN while the batter plays shots). When False
# the field editor is an ephemeral message only the bowling captain can see and
# the confirmed field is not posted in the channel. The batter can still look
# at the field on demand with `csfield` (batter-only, 20 s cooldown), and it is
# shown on the wagon wheel of a four / six. Flip to True to also post the field
# picture publicly after every confirm.
FIELD_VISIBLE_TO_BATTER = False

# Seconds the captain gets before the default field is auto-applied.
FIELD_VIEW_TIMEOUT = 120

# ── Positions ────────────────────────────────────────────────────────────────
# The 8 settable positions, in display order. "C" (Defend) is keeper/bowler
# territory and is deliberately not settable.
FIELD_POSITIONS: tuple[str, ...] = ("UL", "U", "UR", "L", "R", "DL", "D", "DR")

# ── Powerplay scaling ────────────────────────────────────────────────────────
# Requested targets (match overs -> powerplay overs). These are NOT one clean
# formula, so listed lengths use this table verbatim:
#    2->1   4->2   6->2   10->3   15->4   20->6
POWERPLAY_OVERS_TABLE: dict[int, int] = {2: 1, 4: 2, 6: 2, 10: 3, 15: 4, 20: 6}

# For any length NOT in the table we use ~30% of the match, rounded half-up,
# minimum 1 (same 30% idea logic.py uses for its own "powerplay" phase).
# Check against the table: 6->2 (1.8), 10->3, 20->6, 2->1 (0.6) all match;
# 15 -> 4.5 would round to 5 (target is 4) and 4 -> 1.2 gives 1 (target is 2),
# which is why those two are pinned in the table above. A 5-over (ranked)
# match -> 2 powerplay overs.
# Discrepancy to be aware of: logic.py's phase code uses round(0.30*overs),
# which gives 1 for a 4-over match, so the engine's "powerplay" phase and this
# fielding powerplay differ by one over there.
POWERPLAY_FRACTION = 0.30

MAX_OUTSIDE_POWERPLAY = 2   # overs inside the powerplay
MAX_OUTSIDE_OPEN = 5        # non-restriction overs


def powerplay_overs(total_overs: int) -> int:
    """How many overs (from over 1) are powerplay for a match of `total_overs`."""
    total_overs = max(1, int(total_overs))
    if total_overs in POWERPLAY_OVERS_TABLE:
        return POWERPLAY_OVERS_TABLE[total_overs]
    return min(total_overs, max(1, int(total_overs * POWERPLAY_FRACTION + 0.5)))


def is_powerplay(over_index: int, total_overs: int) -> bool:
    """over_index is 0-based (0 = first over)."""
    return over_index < powerplay_overs(total_overs)


def max_fielders_allowed(over_index: int, total_overs: int) -> int:
    """Max fielders allowed outside the circle in the given (0-based) over."""
    return MAX_OUTSIDE_POWERPLAY if is_powerplay(over_index, total_overs) else MAX_OUTSIDE_OPEN


# Fallback / auto-apply order. Symmetric pairs first so any count looks
# balanced: Third Man + Fine Leg, then Cover + Mid-wicket, then Straight...
DEFAULT_FIELD_PRIORITY: tuple[str, ...] = ("UL", "UR", "DL", "DR", "D", "U", "L", "R")


def default_field(max_allowed: int) -> list[str]:
    """Sensible symmetric field used on timeout / autoplay."""
    return list(DEFAULT_FIELD_PRIORITY[:max(0, max_allowed)])


# ── Gameplay modifier ────────────────────────────────────────────────────────
# Multipliers applied on top of the existing weights in
# logic.calculate_outcome() (after MATCHUP_MATRIX etc.; nothing is rewritten).
#   no_boundary    -> True: weights of "4" AND "6" are set to 0 (stopped ball)
#   boundary_mult  -> weight of "4"
#   single_mult    -> weights of "1" and "2"   (a stopped boundary becomes 1-2)
#   three_mult     -> weight of "3"
#   dot_mult       -> weight of "0"
#   wkt_mult       -> weight of "W"            (catch / run-out chance)
FIELDER_MOD: dict = {
    "no_boundary": True, "boundary_mult": 0.0, "single_mult": 1.15, "three_mult": 0.80,
    "dot_mult": 1.10, "wkt_mult": 1.25,
}
GAP_MOD: dict[str, float] = {
    "boundary_mult": 1.12, "single_mult": 1.00, "three_mult": 1.00,
    "dot_mult": 0.95, "wkt_mult": 1.00,
}


def get_field_modifier(direction_key: str | None, field_positions,
                       fielder_stat: int | None = None) -> dict[str, float] | None:
    """Multiplier dict for a GROUND shot into `direction_key`, or None.

    field_positions: any container of direction keys (game.field_positions is
    a dict key->fielder, iterating/`in` works on its keys). None means "no
    field set" -> no modifier (backward compatible). An EMPTY field is a real
    choice (no fielders) and makes every direction a gap.
    `fielder_stat` is reserved for Phase 2 (FIE) and ignored now.
    """
    if field_positions is None:
        return None
    if direction_key not in FIELD_POSITIONS:   # includes None and "C"
        return None
    return dict(FIELDER_MOD if direction_key in field_positions else GAP_MOD)


# ── Shots INTO a fielder ─────────────────────────────────────────────────────
# GROUND shot towards a fielder: the ball is stopped -> only 0/1/2/3 runs or a
#   wicket. No four and no six (FIELDER_MOD["no_boundary"], applied in logic.py).
# LOFTED shot towards a fielder: fixed probabilities of a SIX and a WICKET
#   (catch) that depend on the batter's BAT rating; everything else is 0-3 runs.
#   No four is possible (it can't go over the fielder along the ground).
#   Requested table (bat -> six% / wicket%):
#     99 -> 46 / 20,  98 -> 45 / 21,  97 -> 44 / 22,  96 -> 43 / 23, ...
#     one point at a time down to 90 -> 37 / 29  (six -1, wicket +1 per point)
#     below 90 it flips to -2 / +2 per point: 89 -> 35 / 31, 88 -> 33 / 33 ...
#   The six % is then multiplied by the batter's PLAYSTYLE six boost
#   (Power Hitter, Aggressive, Breakthrough ...) — see logic.calculate_outcome.
LOFT_SIX_AT_99, LOFT_WKT_AT_99 = 46, 20
LOFT_SIX_MIN = 1             # six chance never below 1% (weak batters)
LOFT_WKT_MAX = 95            # wicket chance never above 95%
LOFT_TOTAL_MAX = 0.97        # six + wicket never above 97%
# Bowl: the table is for a bowler rated LOFT_BOWL_REF. Every bowl point
# above/below scales the wicket chance up/down by LOFT_BOWL_STEP (clamped) and
# the six chance the opposite way.
LOFT_BOWL_REF = 80
LOFT_BOWL_STEP = 0.02
LOFT_BOWL_MULT_RANGE = (0.60, 1.40)


def loft_field_base_percent(bat: int | float) -> tuple[int, int]:
    """(six %, wicket %) for a lofted shot into a fielder, from the BAT rating."""
    r = min(99, int(round(bat)))
    if r >= 90:
        d = 99 - r                                  # 1 point = -1 six / +1 wicket
        six, wkt = LOFT_SIX_AT_99 - d, LOFT_WKT_AT_99 + d
    else:
        d = 90 - r                                  # 1 point = -2 six / +2 wicket
        six, wkt = (LOFT_SIX_AT_99 - 9) - 2 * d, (LOFT_WKT_AT_99 + 9) + 2 * d
    return max(LOFT_SIX_MIN, six), min(LOFT_WKT_MAX, wkt)


def loft_field_chances(bat: int | float, bowl: int | float) -> dict[str, float]:
    """{"six": p, "wkt": p} (0-1) for a lofted shot into a fielder, BEFORE the
    batter's playstyle six boost (logic.py applies that)."""
    six, wkt = loft_field_base_percent(bat)
    lo, hi = LOFT_BOWL_MULT_RANGE
    m = min(hi, max(lo, 1.0 + (bowl - LOFT_BOWL_REF) * LOFT_BOWL_STEP))
    return {"six": six / 100.0 / m, "wkt": min(LOFT_WKT_MAX / 100.0, wkt / 100.0 * m)}


# ── Display ──────────────────────────────────────────────────────────────────
def position_label(key: str) -> str:
    arrow, name = DIRECTIONS[key]
    return f"{arrow} {name}"


def describe_field(field_positions) -> str:
    if field_positions is None:
        return "No field set yet."
    keys = [k for k in FIELD_POSITIONS if k in field_positions]
    if not keys:
        return "Nobody outside the circle (all gaps)."
    return ", ".join(position_label(k) for k in keys)
