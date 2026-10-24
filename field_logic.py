"""Field Placement: 11 fielders, formations, and their effect on the ball.

Every match has exactly 11 fielders on the ground. The bowling captain places
them into 11 zones:

  * 8 OUTFIELD zones (outside the 30-yard circle) - the existing compass keys
    UL, U, UR, L, R, DL, D, DR (DIRECTIONS in data.py). A fielder here saves
    the boundary and can take a lofted catch in that direction.
  * 3 INNER zones (inside the 30-yard circle) - IL (close off side),
    IS (close straight) and IR (close leg). Inner fielders stop singles and
    twos, take short catches and make boundaries harder, but cannot save a
    six over their head.

game.field_positions is a dict {zone_key: count} (counts > 0 only). It always
sums to PLAYERS_TOTAL when it is a legal field.

The number of fielders OUTSIDE the circle is capped by the phase
(powerplay: 2, otherwise 5). Inner zones have no cap of their own.

Nothing here touches the match directly: it is pure data + maths. views.py
asks the captain, logic.calculate_outcome() applies the multipliers. If a
match has no field (game.field_positions is None) nothing here is reached.
"""
from __future__ import annotations

from data import DIRECTIONS

# ── Feature switches ─────────────────────────────────────────────────────────
FIELD_FEATURE_ENABLED = True

# When False the field editor is an ephemeral message only the bowling captain
# can see. The batter sees the field on the wagon wheel of a four / six and
# through `csfield`.
FIELD_VISIBLE_TO_BATTER = False

# Seconds the captain gets before the default formation is auto-applied.
FIELD_VIEW_TIMEOUT = 120

PLAYERS_TOTAL = 11
MAX_PER_INNER_ZONE = 6

# ── Zones ────────────────────────────────────────────────────────────────────
# Outfield (outside the 30-yard circle). Same keys as DIRECTIONS, minus "C".
FIELD_POSITIONS: tuple[str, ...] = ("UL", "U", "UR", "L", "R", "DL", "D", "DR")
INNER_ZONES: tuple[str, ...] = ("IL", "IS", "IR")
ZONE_KEYS: tuple[str, ...] = FIELD_POSITIONS + INNER_ZONES

INNER_NAMES: dict[str, str] = {
    "IL": "Close Off", "IS": "Close Straight", "IR": "Close Leg",
}

# Which outfield directions each inner zone can stop / catch for.
INNER_COVERS: dict[str, tuple[str, ...]] = {
    "IL": ("UL", "L", "DL"),
    "IS": ("U", "D"),
    "IR": ("UR", "R", "DR"),
}

# Display order for the editor grid and for numbering the fielders.
GRID_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("UL", "U", "UR"),
    ("L", "IL", "IS", "IR", "R"),
    ("DL", "D", "DR"),
)


def zone_name(key: str) -> str:
    if key in INNER_NAMES:
        return INNER_NAMES[key]
    return DIRECTIONS[key][1]


def position_label(key: str) -> str:
    if key in INNER_NAMES:
        return f"◉ {INNER_NAMES[key]} (in circle)"
    arrow, name = DIRECTIONS[key]
    return f"{arrow} {name}"


# ── Formations (presets). Each sums to 11. ───────────────────────────────────
# Outside count shown in the comment. Presets with outside <= 2 are legal in
# the powerplay; all presets are legal in the open overs (outside <= 5).
FORMATIONS: dict[str, dict[str, int]] = {
    # out 2
    "Powerplay Standard": {"UL": 1, "UR": 1, "IL": 2, "IS": 4, "IR": 3},
    # out 2
    "Powerplay Attack":   {"L": 1, "R": 1, "IL": 3, "IS": 4, "IR": 2},
    # out 0
    "Close Ring":         {"IL": 4, "IS": 4, "IR": 3},
    # out 2
    "Spin Trap":          {"DL": 1, "UR": 1, "IL": 4, "IS": 3, "IR": 2},
    # out 3
    "Balanced Ring":      {"UL": 1, "U": 1, "DR": 1, "IL": 3, "IS": 3, "IR": 2},
    # out 4
    "Cover Wall":         {"L": 1, "DL": 1, "D": 1, "DR": 1, "IL": 2, "IS": 3, "IR": 2},
    # out 4
    "Off-Side Trap":      {"UL": 1, "L": 1, "DL": 1, "D": 1, "IL": 3, "IS": 2, "IR": 2},
    # out 4
    "Leg-Side Trap":      {"UR": 1, "R": 1, "DR": 1, "U": 1, "IL": 2, "IS": 2, "IR": 3},
    # out 5
    "Boundary Riders":    {"UL": 1, "UR": 1, "L": 1, "R": 1, "D": 1, "IL": 2, "IS": 2, "IR": 2},
    # out 5
    "Death Overs Wall":   {"U": 1, "D": 1, "L": 1, "R": 1, "DL": 1, "IL": 2, "IS": 2, "IR": 2},
}
DEFAULT_FORMATION_OPEN = "Balanced Ring"
DEFAULT_FORMATION_POWERPLAY = "Powerplay Standard"


def formation_counts(name: str) -> dict[str, int]:
    return dict(FORMATIONS[name])


def default_formation_name(max_allowed: int) -> str:
    return DEFAULT_FORMATION_POWERPLAY if max_allowed <= MAX_OUTSIDE_POWERPLAY else DEFAULT_FORMATION_OPEN


def formation_fits(name: str, max_allowed: int) -> bool:
    return outside_count(FORMATIONS[name]) <= max_allowed


# ── Phase / caps ─────────────────────────────────────────────────────────────
POWERPLAY_FRACTION = 0.30
POWERPLAY_OVERS_TABLE: dict[int, int] = {2: 1, 4: 2, 6: 2, 10: 3, 15: 4, 20: 6}
MAX_OUTSIDE_POWERPLAY = 2   # fielders outside the circle in the powerplay
MAX_OUTSIDE_OPEN = 5        # fielders outside the circle otherwise


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
    """Max fielders allowed OUTSIDE the circle in the given (0-based) over."""
    return MAX_OUTSIDE_POWERPLAY if is_powerplay(over_index, total_overs) else MAX_OUTSIDE_OPEN


# ── Validation helpers ───────────────────────────────────────────────────────
def total_count(counts) -> int:
    return sum(int(v) for v in (counts or {}).values())


def outside_count(counts) -> int:
    return sum(int(v) for k, v in (counts or {}).items() if k in FIELD_POSITIONS)


def counts_valid(counts, max_allowed: int) -> bool:
    if not counts:
        return False
    if any(k not in ZONE_KEYS or int(v) < 0 for k, v in counts.items()):
        return False
    if any(int(v) > MAX_PER_INNER_ZONE for k, v in counts.items() if k in INNER_ZONES):
        return False
    return total_count(counts) == PLAYERS_TOTAL and outside_count(counts) <= max_allowed


def clean_counts(counts) -> dict[str, int]:
    """Drop zero / unknown entries so the stored dict only holds real fielders."""
    return {k: int(v) for k, v in (counts or {}).items() if k in ZONE_KEYS and int(v) > 0}


def default_field(max_allowed: int) -> dict[str, int]:
    """Sensible field for timeout / autoplay, legal for the given cap."""
    return formation_counts(default_formation_name(max_allowed))


# ── Ground shots ─────────────────────────────────────────────────────────────
# Each inner fielder that covers the shot's direction:
INNER_DOT_STEP = 1.06       # more dot balls
INNER_THREE_STEP = 0.90     # fewer threes
INNER_WKT_STEP = 1.06       # more run-outs / close catches
INNER_BOUNDARY_STEP = 0.92  # a close fielder slows the ball a little

# Multipliers applied on top of the existing weights in
# logic.calculate_outcome() (after MATCHUP_MATRIX etc.; nothing is rewritten).
#   no_boundary    -> True: weights of "4" AND "6" are set to 0 (stopped ball)
#   boundary_mult  -> weight of "4"
#   single_mult    -> weights of "1" and "2"
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


def inner_cover_count(direction_key: str | None, counts) -> int:
    """How many fielders INSIDE the circle cover this direction."""
    if not counts or direction_key is None:
        return 0
    n = 0
    for zone, dirs in INNER_COVERS.items():
        if direction_key in dirs:
            n += int(counts.get(zone, 0))
    return n


def get_field_modifier(direction_key: str | None, field_positions,
                       fielder_stat: int | None = None) -> dict[str, float] | None:
    """Multiplier dict for a GROUND shot into `direction_key`, or None.

    field_positions: {zone_key: count} (None = no field set -> no modifier).
    An outfield fielder in the direction stops the boundary. Inner fielders
    covering the direction make dots more likely, threes rarer, run-outs and
    close catches a bit likelier, and slow down the boundary.
    `fielder_stat` is reserved for a later FIE-based version and ignored.
    """
    if field_positions is None:
        return None
    if direction_key not in FIELD_POSITIONS:   # includes None and "C"
        return None
    outer = int(field_positions.get(direction_key, 0))
    inner = inner_cover_count(direction_key, field_positions)
    mod = dict(FIELDER_MOD if outer > 0 else GAP_MOD)
    if inner:
        mod["dot_mult"] = mod["dot_mult"] * (INNER_DOT_STEP ** inner)
        mod["three_mult"] = mod["three_mult"] * (INNER_THREE_STEP ** inner)
        mod["wkt_mult"] = mod["wkt_mult"] * (INNER_WKT_STEP ** inner)
        if not mod.get("no_boundary"):
            mod["boundary_mult"] = mod["boundary_mult"] * (INNER_BOUNDARY_STEP ** inner)
    return mod


# ── Lofted shots ─────────────────────────────────────────────────────────────
# Shot into an OUTFIELD fielder: fixed probabilities of a SIX and a CATCH that
# depend on the batter's BAT rating. Requested table (bat -> six% / wicket%):
#   99 -> 46 / 20,  98 -> 45 / 21,  ...  90 -> 37 / 29  (six -1, wicket +1 per point)
#   below 90 it flips to -2 / +2 per point: 89 -> 35 / 31, 88 -> 33 / 33 ...
LOFT_SIX_AT_99, LOFT_WKT_AT_99 = 46, 20
LOFT_SIX_MIN = 1             # six chance never below 1% (weak batters)
LOFT_WKT_MAX = 95            # wicket chance never above 95%
LOFT_TOTAL_MAX = 0.97        # six + wicket never above 97%
# Bowl: the table is for a bowler rated LOFT_BOWL_REF. Every bowl point above /
# below scales the wicket chance up / down and the six chance the other way.
LOFT_BOWL_REF = 80
LOFT_BOWL_STEP = 0.02
LOFT_BOWL_MULT_RANGE = (0.60, 1.40)

# Shot over an INNER fielder: almost always a catch, a six is rare.
LOFT_INNER_SIX_MULT = 0.35
LOFT_INNER_WKT_STEP = 0.10   # +10% catch chance per extra inner fielder covering


def loft_field_base_percent(bat: int | float) -> tuple[int, int]:
    """(six %, wicket %) for a lofted shot into an outfield fielder."""
    r = min(99, int(round(bat)))
    if r >= 90:
        d = 99 - r                                  # 1 point = -1 six / +1 wicket
        six, wkt = LOFT_SIX_AT_99 - d, LOFT_WKT_AT_99 + d
    else:
        d = 90 - r                                  # 1 point = -2 six / +2 wicket
        six, wkt = (LOFT_SIX_AT_99 - 9) - 2 * d, (LOFT_WKT_AT_99 + 9) + 2 * d
    return max(LOFT_SIX_MIN, six), min(LOFT_WKT_MAX, wkt)


def _bowl_mult(bowl: int | float) -> float:
    lo, hi = LOFT_BOWL_MULT_RANGE
    return min(hi, max(lo, 1.0 + (bowl - LOFT_BOWL_REF) * LOFT_BOWL_STEP))


def loft_field_chances(bat: int | float, bowl: int | float) -> dict[str, float]:
    """{"six": p, "wkt": p} (0-1) for a lofted shot into an OUTFIELD
    fielder, BEFORE the batter's playstyle six boost (logic.py applies that)."""
    six, wkt = loft_field_base_percent(bat)
    m = _bowl_mult(bowl)
    return {"six": six / 100.0 / m, "wkt": min(LOFT_WKT_MAX / 100.0, wkt / 100.0 * m)}


def loft_inner_chances(bat: int | float, bowl: int | float, inner_n: int = 1) -> dict[str, float]:
    """{"six": p, "wkt": p} for a lofted shot that only INNER fielders can
    reach. The catch is the likely outcome; the six is much rarer."""
    base = loft_field_chances(bat, bowl)
    extra = 1.0 + LOFT_INNER_WKT_STEP * max(0, inner_n - 1)
    wkt = min(LOFT_WKT_MAX / 100.0, base["wkt"] * extra)
    six = base["six"] * LOFT_INNER_SIX_MULT
    return {"six": six, "wkt": wkt}


def catch_zone_for(direction_key: str | None, counts) -> str | None:
    """The zone that would take a LOFTED shot in this direction: the outfield
    fielder if there is one, else an inner fielder covering it, else None."""
    if not counts or direction_key is None:
        return None
    if int(counts.get(direction_key, 0)) > 0 and direction_key in FIELD_POSITIONS:
        return direction_key
    for zone in INNER_ZONES:
        if direction_key in INNER_COVERS[zone] and int(counts.get(zone, 0)) > 0:
            return zone
    return None


def loft_field_for(direction_key: str | None, counts, bat, bowl) -> dict[str, float] | None:
    """Six / wicket chances for a lofted shot into a fielded zone, or None."""
    zone = catch_zone_for(direction_key, counts)
    if zone is None:
        return None
    if zone in FIELD_POSITIONS:
        return loft_field_chances(bat, bowl)
    return loft_inner_chances(bat, bowl, inner_cover_count(direction_key, counts))


# ── Loft power (how far a batter can hit a lofted ball) ──────────────────────
# Used for a lofted shot that is NOT into a fielder. Low bat ratings struggle
# to clear the rope (six weight drops, boundaries drop, and they get out more
# when they go after it). High bat ratings clear the stadium.
LOFT_POWER_FLOOR_BAT = 62     # at or below this the batter can barely clear
LOFT_POWER_MIN = 0.04
LOFT_POWER_MAX = 2.0


def loft_power(bat: int | float) -> float:
    x = (float(bat) - LOFT_POWER_FLOOR_BAT) / 26.0
    if x <= 0:
        return LOFT_POWER_MIN
    return min(LOFT_POWER_MAX, max(LOFT_POWER_MIN, x ** 1.6))


# ── Display ──────────────────────────────────────────────────────────────────
def describe_field(counts) -> str:
    if not counts:
        return "No field set yet."
    parts = []
    for k in ZONE_KEYS:
        n = int((counts or {}).get(k, 0))
        if n:
            parts.append(f"{zone_name(k)} x{n}" if n > 1 else zone_name(k))
    return ", ".join(parts)
