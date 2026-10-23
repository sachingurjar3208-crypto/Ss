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

# Open question #6 (decided by the product owner: HIDDEN). When False the
# field editor is an ephemeral message only the bowling captain can see, the
# confirmed field is not posted in the channel, and `csfield` is DM-only for
# the bowling captain. Flip to True to make the field public / visible to the
# batting side (the "realistic" option).
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
#   boundary_mult  -> weight of "4"
#   single_mult    -> weights of "1" and "2"   (a stopped boundary becomes 1-2)
#   three_mult     -> weight of "3"
#   dot_mult       -> weight of "0"
#   wkt_mult       -> weight of "W"            (catch / run-out chance)
FIELDER_MOD: dict[str, float] = {
    "boundary_mult": 0.35, "single_mult": 1.15, "three_mult": 0.80,
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


# ── Loft shot INTO a fielder: out chance by bat / bowl rating ───────────────
# A lofted shot hit towards a direction that has a fielder is dismissed with a
# fixed probability that depends on the batter's BAT rating (and a bit on the
# bowler's BOWL rating). Requested bat table (out % vs an average bowler):
#   bat 99 -> 5%, 98 -> 6%, ... one point at a time ... 90 -> 14%
#   bat 89 -> 18%, 88 -> 22%, ... four points at a time ... 78 -> 62%
# The same pattern is continued below 78 (+4% per rating point) up to the cap.
LOFT_OUT_MIN = 0.02          # never lower than 2% (very good bat, weak bowler)
LOFT_OUT_MAX = 0.97          # never certain
LOFT_OUT_BAT_CAP = 95        # base % for the weakest batters (bat <= 69)
# Bowl: the table above is for a bowler rated LOFT_OUT_BOWL_REF. Every bowl
# point above/below that scales the chance by LOFT_OUT_BOWL_STEP (clamped).
LOFT_OUT_BOWL_REF = 80
LOFT_OUT_BOWL_STEP = 0.02
LOFT_OUT_BOWL_MULT_RANGE = (0.60, 1.40)


def loft_out_base_percent(bat: int | float) -> int:
    """Base out % for a lofted shot into a fielder, from the BAT rating."""
    r = int(round(bat))
    if r >= 99:
        return 5
    if r >= 90:
        return 5 + (99 - r)              # 98->6 ... 90->14
    if r >= 78:
        return 14 + 4 * (90 - r)         # 89->18 ... 78->62
    return min(LOFT_OUT_BAT_CAP, 62 + 4 * (78 - r))   # 77->66 ... capped


def loft_out_chance(bat: int | float, bowl: int | float) -> float:
    """Probability (0-1) that a lofted shot into a fielder gets the batter out."""
    lo, hi = LOFT_OUT_BOWL_MULT_RANGE
    mult = min(hi, max(lo, 1.0 + (bowl - LOFT_OUT_BOWL_REF) * LOFT_OUT_BOWL_STEP))
    p = loft_out_base_percent(bat) / 100.0 * mult
    return min(LOFT_OUT_MAX, max(LOFT_OUT_MIN, p))


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
