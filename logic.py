import random
from data import DELIVERY_BUTTON_MAP, SHOT_BUTTON_MAP

# Playstyle "narratives" chosen on a player's card (/cardmaker, /editcard,
# /setplaystylelogo) are looked up by player name and switched on here.
# If the card database can't be read, matches simply run without them.
try:
    from card_db import get_player_narratives
except Exception:  # pragma: no cover - never let a card problem break a match
    def get_player_narratives(name):
        return []


def player_attributes(player) -> list:
    """Attributes for one player dict: any set on the dict itself plus the
    narratives on that player's card (matched by name)."""
    attrs = list(player.get("attributes") or [])
    name = player.get("name")
    if name:
        try:
            attrs += [a for a in get_player_narratives(name) if a not in attrs]
        except Exception:
            pass
    return attrs

BASE_WEIGHTS = {
    "0": 35, "1": 22, "2": 10, "3": 3,
    "4": 11, "6": 5, "W": 8, "Wd": 4, "NB": 2,
}

MATCHUP_MATRIX = {
    # Fast deliveries
    ("Fast",        "Drive"):         {"bat": 1.2, "wkt": 0.9},
    ("Fast",        "Pull"):          {"bat": 0.8, "wkt": 1.3},
    ("Fast",        "Cut"):           {"bat": 1.0, "wkt": 1.0},
    ("Fast",        "Sweep"):         {"bat": 0.5, "wkt": 1.6},
    ("Fast",        "Lofted"):        {"bat": 1.3, "wkt": 1.4},
    ("Fast",        "Flick"):         {"bat": 1.1, "wkt": 0.9},
    ("Fast",        "Defend"):        {"bat": 0.3, "wkt": 0.5},
    ("Fast",        "Reverse-Sweep"): {"bat": 0.7, "wkt": 1.6},
    ("Swing",       "Drive"):         {"bat": 0.7, "wkt": 1.6},
    ("Swing",       "Pull"):          {"bat": 0.7, "wkt": 1.3},
    ("Swing",       "Cut"):           {"bat": 0.8, "wkt": 1.4},
    ("Swing",       "Sweep"):         {"bat": 0.6, "wkt": 1.4},
    ("Swing",       "Lofted"):        {"bat": 0.9, "wkt": 1.8},
    ("Swing",       "Flick"):         {"bat": 1.2, "wkt": 0.8},
    ("Swing",       "Defend"):        {"bat": 0.4, "wkt": 0.6},
    ("Swing",       "Reverse-Sweep"): {"bat": 0.5, "wkt": 1.8},
    ("Yorker",      "Drive"):         {"bat": 0.5, "wkt": 2.0},
    ("Yorker",      "Pull"):          {"bat": 0.3, "wkt": 2.5},
    ("Yorker",      "Cut"):           {"bat": 0.4, "wkt": 2.0},
    ("Yorker",      "Sweep"):         {"bat": 0.4, "wkt": 1.8},
    ("Yorker",      "Lofted"):        {"bat": 0.3, "wkt": 2.5},
    ("Yorker",      "Flick"):         {"bat": 0.9, "wkt": 1.2},
    ("Yorker",      "Defend"):        {"bat": 0.6, "wkt": 0.7},
    ("Yorker",      "Reverse-Sweep"): {"bat": 0.5, "wkt": 2.0},
    ("Bouncer",     "Drive"):         {"bat": 0.3, "wkt": 2.5},
    ("Bouncer",     "Pull"):          {"bat": 1.9, "wkt": 0.6},
    ("Bouncer",     "Cut"):           {"bat": 1.2, "wkt": 0.8},
    ("Bouncer",     "Sweep"):         {"bat": 0.4, "wkt": 2.0},
    ("Bouncer",     "Lofted"):        {"bat": 0.7, "wkt": 1.6},
    ("Bouncer",     "Flick"):         {"bat": 0.4, "wkt": 2.0},
    ("Bouncer",     "Defend"):        {"bat": 0.4, "wkt": 0.8},
    ("Bouncer",     "Reverse-Sweep"): {"bat": 0.3, "wkt": 2.5},
    ("Good Length", "Drive"):         {"bat": 1.0, "wkt": 1.0},
    ("Good Length", "Pull"):          {"bat": 0.7, "wkt": 1.2},
    ("Good Length", "Cut"):           {"bat": 0.8, "wkt": 1.1},
    ("Good Length", "Sweep"):         {"bat": 0.7, "wkt": 1.2},
    ("Good Length", "Lofted"):        {"bat": 1.2, "wkt": 1.3},
    ("Good Length", "Flick"):         {"bat": 0.9, "wkt": 1.0},
    ("Good Length", "Defend"):        {"bat": 0.4, "wkt": 0.5},
    ("Good Length", "Reverse-Sweep"): {"bat": 0.6, "wkt": 1.5},
    ("Full",        "Drive"):         {"bat": 1.9, "wkt": 0.5},
    ("Full",        "Pull"):          {"bat": 0.6, "wkt": 1.4},
    ("Full",        "Cut"):           {"bat": 0.8, "wkt": 1.1},
    ("Full",        "Sweep"):         {"bat": 0.8, "wkt": 1.1},
    ("Full",        "Lofted"):        {"bat": 1.8, "wkt": 1.0},
    ("Full",        "Flick"):         {"bat": 1.7, "wkt": 0.6},
    ("Full",        "Defend"):        {"bat": 0.3, "wkt": 0.4},
    ("Full",        "Reverse-Sweep"): {"bat": 0.5, "wkt": 1.6},
    # Off Spin
    ("Off Break",   "Drive"):         {"bat": 0.8, "wkt": 1.4},
    ("Off Break",   "Pull"):          {"bat": 0.6, "wkt": 1.4},
    ("Off Break",   "Cut"):           {"bat": 0.7, "wkt": 1.3},
    ("Off Break",   "Sweep"):         {"bat": 1.7, "wkt": 0.5},
    ("Off Break",   "Lofted"):        {"bat": 1.3, "wkt": 1.2},
    ("Off Break",   "Flick"):         {"bat": 1.1, "wkt": 0.9},
    ("Off Break",   "Defend"):        {"bat": 0.4, "wkt": 0.6},
    ("Off Break",   "Reverse-Sweep"): {"bat": 1.4, "wkt": 0.7},
    ("Doosra",      "Drive"):         {"bat": 0.7, "wkt": 1.5},
    ("Doosra",      "Pull"):          {"bat": 0.6, "wkt": 1.5},
    ("Doosra",      "Cut"):           {"bat": 0.7, "wkt": 1.4},
    ("Doosra",      "Sweep"):         {"bat": 0.5, "wkt": 2.0},
    ("Doosra",      "Lofted"):        {"bat": 1.0, "wkt": 1.4},
    ("Doosra",      "Flick"):         {"bat": 0.8, "wkt": 1.3},
    ("Doosra",      "Defend"):        {"bat": 0.5, "wkt": 0.8},
    ("Doosra",      "Reverse-Sweep"): {"bat": 1.5, "wkt": 0.8},
    ("Carrom Ball", "Drive"):         {"bat": 0.8, "wkt": 1.3},
    ("Carrom Ball", "Pull"):          {"bat": 0.7, "wkt": 1.3},
    ("Carrom Ball", "Cut"):           {"bat": 0.9, "wkt": 1.2},
    ("Carrom Ball", "Sweep"):         {"bat": 0.9, "wkt": 1.3},
    ("Carrom Ball", "Lofted"):        {"bat": 1.1, "wkt": 1.3},
    ("Carrom Ball", "Flick"):         {"bat": 1.0, "wkt": 1.0},
    ("Carrom Ball", "Defend"):        {"bat": 0.4, "wkt": 0.7},
    ("Carrom Ball", "Reverse-Sweep"): {"bat": 1.1, "wkt": 1.2},
    ("Arm Ball",    "Drive"):         {"bat": 1.2, "wkt": 0.9},
    ("Arm Ball",    "Pull"):          {"bat": 0.7, "wkt": 1.3},
    ("Arm Ball",    "Cut"):           {"bat": 0.8, "wkt": 1.2},
    ("Arm Ball",    "Sweep"):         {"bat": 0.6, "wkt": 1.5},
    ("Arm Ball",    "Lofted"):        {"bat": 1.2, "wkt": 1.2},
    ("Arm Ball",    "Flick"):         {"bat": 1.1, "wkt": 0.9},
    ("Arm Ball",    "Defend"):        {"bat": 0.4, "wkt": 0.6},
    ("Arm Ball",    "Reverse-Sweep"): {"bat": 0.7, "wkt": 1.5},
    ("Top Spin",    "Drive"):         {"bat": 0.7, "wkt": 1.4},
    ("Top Spin",    "Pull"):          {"bat": 0.7, "wkt": 1.3},
    ("Top Spin",    "Cut"):           {"bat": 0.7, "wkt": 1.3},
    ("Top Spin",    "Sweep"):         {"bat": 0.8, "wkt": 1.3},
    ("Top Spin",    "Lofted"):        {"bat": 0.9, "wkt": 1.5},
    ("Top Spin",    "Flick"):         {"bat": 0.9, "wkt": 1.1},
    ("Top Spin",    "Defend"):        {"bat": 0.5, "wkt": 0.7},
    ("Top Spin",    "Reverse-Sweep"): {"bat": 1.0, "wkt": 1.3},
    # Leg Spin
    ("Drift Ball",  "Drive"):         {"bat": 0.9, "wkt": 1.1},
    ("Drift Ball",  "Pull"):          {"bat": 0.7, "wkt": 1.2},
    ("Drift Ball",  "Cut"):           {"bat": 0.8, "wkt": 1.2},
    ("Drift Ball",  "Sweep"):         {"bat": 1.2, "wkt": 0.9},
    ("Drift Ball",  "Lofted"):        {"bat": 1.0, "wkt": 1.2},
    ("Drift Ball",  "Flick"):         {"bat": 1.1, "wkt": 0.9},
    ("Drift Ball",  "Defend"):        {"bat": 0.4, "wkt": 0.7},
    ("Drift Ball",  "Reverse-Sweep"): {"bat": 0.8, "wkt": 1.4},
    ("Leg Break",   "Drive"):         {"bat": 0.7, "wkt": 1.4},
    ("Leg Break",   "Pull"):          {"bat": 0.6, "wkt": 1.4},
    ("Leg Break",   "Cut"):           {"bat": 1.4, "wkt": 0.7},
    ("Leg Break",   "Sweep"):         {"bat": 0.7, "wkt": 1.4},
    ("Leg Break",   "Lofted"):        {"bat": 1.1, "wkt": 1.3},
    ("Leg Break",   "Flick"):         {"bat": 0.8, "wkt": 1.2},
    ("Leg Break",   "Defend"):        {"bat": 0.4, "wkt": 0.7},
    ("Leg Break",   "Reverse-Sweep"): {"bat": 0.8, "wkt": 1.5},
    ("Googly",      "Drive"):         {"bat": 0.6, "wkt": 1.8},
    ("Googly",      "Pull"):          {"bat": 0.6, "wkt": 1.6},
    ("Googly",      "Cut"):           {"bat": 0.7, "wkt": 1.5},
    ("Googly",      "Sweep"):         {"bat": 0.5, "wkt": 2.1},
    ("Googly",      "Lofted"):        {"bat": 0.9, "wkt": 1.6},
    ("Googly",      "Flick"):         {"bat": 0.7, "wkt": 1.5},
    ("Googly",      "Defend"):        {"bat": 0.5, "wkt": 0.8},
    ("Googly",      "Reverse-Sweep"): {"bat": 1.7, "wkt": 0.6},
    ("Flipper",     "Drive"):         {"bat": 0.7, "wkt": 1.6},
    ("Flipper",     "Pull"):          {"bat": 0.5, "wkt": 1.8},
    ("Flipper",     "Cut"):           {"bat": 0.6, "wkt": 1.5},
    ("Flipper",     "Sweep"):         {"bat": 0.5, "wkt": 2.0},
    ("Flipper",     "Lofted"):        {"bat": 0.6, "wkt": 1.8},
    ("Flipper",     "Flick"):         {"bat": 0.7, "wkt": 1.4},
    ("Flipper",     "Defend"):        {"bat": 0.5, "wkt": 0.9},
    ("Flipper",     "Reverse-Sweep"): {"bat": 0.7, "wkt": 1.7},
    ("Top Spinner", "Drive"):         {"bat": 0.7, "wkt": 1.5},
    ("Top Spinner", "Pull"):          {"bat": 0.7, "wkt": 1.4},
    ("Top Spinner", "Cut"):           {"bat": 0.8, "wkt": 1.3},
    ("Top Spinner", "Sweep"):         {"bat": 0.8, "wkt": 1.4},
    ("Top Spinner", "Lofted"):        {"bat": 0.9, "wkt": 1.5},
    ("Top Spinner", "Flick"):         {"bat": 0.9, "wkt": 1.2},
    ("Top Spinner", "Defend"):        {"bat": 0.5, "wkt": 0.8},
    ("Top Spinner", "Reverse-Sweep"): {"bat": 1.0, "wkt": 1.4},
    ("Slider",      "Drive"):         {"bat": 0.9, "wkt": 1.2},
    ("Slider",      "Pull"):          {"bat": 0.7, "wkt": 1.3},
    ("Slider",      "Cut"):           {"bat": 0.8, "wkt": 1.2},
    ("Slider",      "Sweep"):         {"bat": 1.4, "wkt": 0.7},
    ("Slider",      "Lofted"):        {"bat": 1.0, "wkt": 1.2},
    ("Slider",      "Flick"):         {"bat": 1.1, "wkt": 0.9},
    ("Slider",      "Defend"):        {"bat": 0.4, "wkt": 0.7},
    ("Slider",      "Reverse-Sweep"): {"bat": 0.9, "wkt": 1.3},
}

ATTR_EFFECTS = {
    # ── Batting ──────────────────────────────────────────────────────────────
    "Finisher":         {"death_bat_mult":  1.20},  # +20% scoring in death overs
    "Aggressive":       {"boundary_mult":   1.10,   # +10% 4s/6s overall
                         "dot_reduce":      0.90},  # -10% dot balls
    "Anchor":           {"wkt_reduce":      0.70},  # -30% wicket chance
    "Opener":           {"pp_bat_mult":     1.15},  # +15% scoring in powerplay
    "Power Hitter":     {"boundary_mult":   1.25,   # +25% 4s/6s
                         "six_mult":        1.25,   # extra six boost
                         "wkt_increase":    1.15},  # slightly higher risk
    "Clinical":         {"dot_reduce":      0.85,   # -15% dot balls
                         "single_mult":     1.20},  # +20% singles/rotation
    "Chase Master":     {"chase_bat_mult":  1.15},  # +15% scoring when chasing
    "Centurion":        {"fifty_bat_mult":  1.10},  # +10% after scoring 50+ runs
    # ── Bowling ──────────────────────────────────────────────────────────────
    "Death Bowler":     {"death_wkt_mult":  1.20,   # +20% wickets in death
                         "death_run_reduce":0.88},  # -12% runs in death
    "Powerplay Bowler": {"pp_wkt_mult":     1.15},  # +15% wickets in powerplay
    "Spin Wizard":      {"spin_wkt_mult":   1.40},  # +40% wickets with spin deliveries
    "Yorker King":      {"yorker_wkt_mult": 1.30},  # +30% wickets on yorker
    "Swing Master":     {"swing_wkt_mult":  1.25},  # +25% wickets on swing
    "Bouncer Specialist":{"bouncer_wkt_mult":1.25}, # +25% wickets on bouncer
    "Economical":       {"run_reduce":      0.85},  # -15% runs given per over
    # ── All-rounder / Legacy ─────────────────────────────────────────────────
    "Match Winner":     {"death_bat_mult":  1.10,   # +10% batting in last 5 overs
                         "death_wkt_mult":  1.10},  # +10% wickets in last 5 overs
    "Wicket Hunter":    {"wkt_mult":        1.35},  # +35% wickets all the time
    "Pinch Hitter":     {"boundary_mult":   1.25,
                         "wkt_increase":    1.25},
    "Clutch":           {"clutch_bat_mult": 1.30},
    "Chaser":           {"chase_bat_mult":  1.20},
    # ── Card narratives ──────────────────────────────────────────────────────
    # (Aggressive, Chase Master and Yorker King are defined above; "Swing King"
    #  is an alias of "Swing Master"; "Balanced" is intentionally neutral.)
    "Lethal Pace":      {"pace_wkt_mult":   1.25,   # +25% wickets off pace deliveries
                         "pace_run_reduce": 0.92},  # -8% runs off pace deliveries
    "Cover Drive Specialist": {"drive_four_mult":   1.40,   # +40% fours when driving
                               "drive_run_mult":    1.10,   # +10% 1s/2s/3s when driving
                               "drive_wkt_reduce":  0.80},  # -20% wicket chance when driving
    "Low Batting Quality":    {"four_mult":   0.20,   # fours ~80% rarer
                               "six_mult":    0.08,   # sixes ~92% rarer
                               "rotate_mult": 1.30},  # more 1s/2s/3s instead
    "Low Bowling Quality":    {"six_mult":     1.80,   # sixes far easier to hit
                               "four_mult":    1.40,
                               "wkt_reduce":   0.30,   # wickets very unlikely
                               "six_cap_mult": 2.6},   # lifts the six-weight cap (normally 2.0)
    # ── New playstyles ───────────────────────────────────────────────────────
    "Early Breaker":   {"early_wkt_mult":   1.30},  # +30% wickets in the first overs of the match
    "Googly Master":   {"googly_wkt_mult":  1.35},  # +35% wickets on Googly and Leg Break
    "Mr360":           {"four_mult":        1.25,   # +25% fours on 360-style shots
                        "six_mult":         1.15,   # +15% sixes on 360-style shots
                        "wkt_reduce":       0.90},  # -10% wicket chance on those shots
    "Mystery":         {"wkt_mult":         1.30,   # +30% wickets on Mystery-button deliveries
                        "boundary_reduce":  0.92},  # -8% fours/sixes against them
    "Spell Finisher":  {"wkt_mult":         1.25,   # +25% wickets in the bowler's final over(s)
                        "run_reduce":       0.90},  # -10% runs in the bowler's final over(s)
    "Mind Games":      {"wkt_mult":         1.25},  # +25% wickets when the delivery type changes
}

# Names a card can carry that the engine knows under another name.
_ATTR_ALIASES = {"Swing King": "Swing Master"}

# Delivery types classified as spin
_SPIN_DELIVERIES = {
    "Off Break", "Doosra", "Carrom Ball", "Arm Ball", "Top Spin",
    "Drift Ball", "Leg Break", "Googly", "Flipper", "Top Spinner", "Slider",
}
_SWING_DELIVERIES  = {"Swing", "In Swinger", "Out Swinger", "Reverse Swing"}
_YORKER_DELIVERIES = {"Yorker", "Yorker Full Toss"}
_PACE_DELIVERIES   = {"Swing", "Good Length", "Fast", "Bouncer", "Full", "Yorker"}
_BOUNCER_DELIVERIES = {"Bouncer", "Short Ball"}
_SEAM_DELIVERIES   = {"Swing", "In Swinger", "Out Swinger", "Good Length", "Full"}
_GOOGLY_DELIVERIES = {"Googly", "Leg Break"}
_SHOTS_360         = {"Sweep", "Reverse-Sweep", "Flick", "Lofted"}

# ── Phase modifiers ──────────────────────────────────────────────────────────
# Each phase tweaks boundary/wicket/six weights independently.
PHASE_MODIFIERS = {
    "powerplay": {
        "4":  1.30,   # more fours — field restrictions
        "6":  1.15,   # slightly more sixes
        "W":  0.85,   # fewer wickets — batsmen on top
        "0":  0.90,
    },
    "middle": {
        # baseline — no change
    },
    "death": {
        "6":  1.45,   # big hits attempted
        "4":  1.15,
        "W":  1.25,   # more wickets — pressure shots
        "0":  1.10,   # more dot balls too (yorkers/bouncers)
        "1":  0.85,
        "2":  0.80,   # twos rare in death — batsmen swing for boundaries
    },
}


def calculate_outcome(
    delivery_internal: str,
    shot_internal: str,
    bowler_bowl: int,
    batsman_bat: int,
    bowler_attrs: list | None = None,
    batsman_attrs: list | None = None,
    innings: int = 1,
    current_over: int = 0,
    rrr: float = 0.0,
    balls_since_wicket: int = 999,
    partnership_runs: int = 0,
    is_recommended_shot: bool = False,
    guide_entry_exists: bool = False,
    total_overs: int = 20,
    ground_type: str | None = None,
    delivery_button: str | None = None,
    prev_delivery: str | None = None,
    bowler_balls_done: int = 0,
    bowler_max_balls: int = 0,
) -> tuple[str, bool]:
    """
    Simulate a single ball outcome.

    Extra parameters vs original:
      rrr               – required run rate (innings 2 only); drives chase pressure
      balls_since_wicket– balls faced by current batsman; <8 = new bat vulnerability
      partnership_runs  – runs in current partnership; ≥30 = momentum boost
      total_overs       – match length; used to scale phase boundaries so that
                          short matches (e.g. 5-over) still have a death phase.
      delivery_button   – button the bowler pressed (e.g. "Mystery")
      prev_delivery     – internal delivery type of the previous ball (None at start)
      bowler_balls_done – legal balls this bowler has already bowled
      bowler_max_balls  – this bowler's max balls in the match (overs/5 rule)
    """
    bowler_attrs  = [_ATTR_ALIASES.get(a, a) for a in (bowler_attrs  or [])]
    batsman_attrs = [_ATTR_ALIASES.get(a, a) for a in (batsman_attrs or [])]

    m = MATCHUP_MATRIX.get((delivery_internal, shot_internal), {"bat": 1.0, "wkt": 1.0})

    # ── Bat / Bowl rating scaling ─────────────────────────────────────────────
    # 70 = average benchmark.  Ratings DIRECTLY drive the multipliers — OVR is
    # intentionally NOT used here.  A bat=99 batter is dramatically stronger
    # than a bat=15 bowler trying to slog; a bowl=94 bowler is a major threat.
    bat_factor = max(0.20, batsman_bat / 70.0)   # bat=99 → 1.41 | bat=15 → 0.21 (capped 0.20)
    wkt_factor = max(0.20, bowler_bowl  / 70.0)  # bowl=94→ 1.34 | bowl=0 → 0.20

    w = dict(BASE_WEIGHTS)

    # Runs: all scoring chances scale with bat ability
    for k in ("1", "2", "3", "4", "6"):
        w[k] = max(0.3, w[k] * m["bat"] * bat_factor)

    # Dot balls: inverse of bat — weak batters face far more dots
    w["0"] = max(1.0, w["0"] / bat_factor)

    # Wickets: scale with bowl ability
    w["W"] = max(0.3, w["W"] * m["wkt"] * wkt_factor)

    # ── Extra six power (only TRULY elite batters, bat > 82) ─────────────────
    # bat=99 → ×1.50 bonus; bat=90 → ×1.24; bat=82 → ×1.0 (no bonus).
    # Threshold raised from 65 → 82 so mid-tier 80-OVR players don't get an
    # automatic six bonus just for being above the seeded minimum.
    six_power = 1.0 + max(0.0, (batsman_bat - 82) / 34.0)
    w["6"] = max(0.3, w["6"] * six_power)

    # ── Extra wicket power (only elite bowlers, bowl > 65) ───────────────────
    # Gentler formula so stacking with matchup multipliers stays realistic.
    # bowl=99 → ×1.49;  bowl=80 → ×1.21;  bowl=65 → ×1.0 (no bonus)
    wkt_power = 1.0 + max(0.0, (bowler_bowl - 65) / 70.0)
    w["W"] = max(0.3, w["W"] * wkt_power)

    # ── Phase modifiers ──────────────────────────────────────────────────────
    # BUG FIX #2: thresholds were hardcoded for 20-over matches (powerplay <6,
    # death >=15), so in a 5-over match the death phase never triggered and
    # Finisher/Clutch attributes were permanently dead.
    # Now thresholds scale proportionally with total_overs so every match
    # length has a meaningful powerplay (~30%), middle, and death (~25%) phase.
    _pp_end    = max(1, round(total_overs * 0.30))   # e.g. 20ov→6, 10ov→3, 5ov→2
    _death_start = max(_pp_end + 1, round(total_overs * 0.75))  # e.g. 20ov→15, 10ov→8, 5ov→4
    if current_over < _pp_end:
        phase = "powerplay"
    elif current_over < _death_start:
        phase = "middle"
    else:
        phase = "death"

    for key, mult in PHASE_MODIFIERS.get(phase, {}).items():
        if key in w:
            w[key] = max(0.5, w[key] * mult)

    # ── New-batsman vulnerability (first 8 balls) ────────────────────────────
    if balls_since_wicket < 8:
        # Scale: ball 0 = +40% wicket, ball 7 = +5%, linear decay
        vuln = 1.40 - (balls_since_wicket * 0.05)
        w["W"] = w["W"] * vuln

    # ── Partnership momentum (30+ run stand) ────────────────────────────────
    if partnership_runs >= 30:
        momentum = min(1.20, 1.05 + (partnership_runs - 30) * 0.002)  # caps at +20%
        for k in ("1", "2", "3", "4", "6"):
            w[k] = w[k] * momentum

    # ── Chase pressure (innings 2 only) ─────────────────────────────────────
    if innings == 2 and rrr > 0:
        if rrr > 12:
            # Panic mode: more boundaries attempted, far more wickets
            w["6"]  = w["6"]  * 1.35
            w["4"]  = w["4"]  * 1.15
            w["W"]  = w["W"]  * 1.40
            w["0"]  = w["0"]  * 1.15
        elif rrr < 6:
            # Coasting: more singles/twos, fewer wickets, fewer boundaries
            w["1"]  = w["1"]  * 1.25
            w["2"]  = w["2"]  * 1.15
            w["W"]  = w["W"]  * 0.75
            w["6"]  = w["6"]  * 0.80

    # ── Batsman attribute effects ────────────────────────────────────────────

    # FINISHER: +scoring in death overs
    if "Finisher" in batsman_attrs and current_over >= _death_start:
        mult = ATTR_EFFECTS["Finisher"]["death_bat_mult"]
        for k in ("1","2","3","4","6"): w[k] = w[k] * mult

    # AGGRESSIVE: +boundaries, -dots
    if "Aggressive" in batsman_attrs:
        bm = ATTR_EFFECTS["Aggressive"]["boundary_mult"]
        w["4"] = w["4"] * bm; w["6"] = w["6"] * bm
        w["0"] = w["0"] * ATTR_EFFECTS["Aggressive"]["dot_reduce"]

    # ANCHOR: -wicket chance
    if "Anchor" in batsman_attrs:
        w["W"] = w["W"] * ATTR_EFFECTS["Anchor"]["wkt_reduce"]

    # OPENER: +scoring in powerplay
    if "Opener" in batsman_attrs and phase == "powerplay":
        mult = ATTR_EFFECTS["Opener"]["pp_bat_mult"]
        for k in ("1","2","3","4","6"): w[k] = w[k] * mult

    # POWER HITTER: +boundaries, +sixes, slightly higher risk
    if "Power Hitter" in batsman_attrs:
        bm = ATTR_EFFECTS["Power Hitter"]["boundary_mult"]
        w["4"] = w["4"] * bm
        w["6"] = w["6"] * bm * ATTR_EFFECTS["Power Hitter"]["six_mult"]
        w["W"] = w["W"] * ATTR_EFFECTS["Power Hitter"]["wkt_increase"]

    # CLINICAL: -dots, +singles/rotation
    if "Clinical" in batsman_attrs:
        w["0"] = w["0"] * ATTR_EFFECTS["Clinical"]["dot_reduce"]
        sm = ATTR_EFFECTS["Clinical"]["single_mult"]
        w["1"] = w["1"] * sm; w["2"] = w["2"] * sm

    # CHASE MASTER / CHASER: +scoring when chasing (innings 2).
    # "Chaser" is the legacy name for "Chase Master" — they are mutually
    # exclusive. If a player somehow has both (old data), only the stronger
    # "Chase Master" multiplier is applied so they never stack (x1.38).
    if innings == 2:
        if "Chase Master" in batsman_attrs:
            mult = ATTR_EFFECTS["Chase Master"]["chase_bat_mult"]
            for k in ("1","2","3","4","6"): w[k] = w[k] * mult
        elif (
            "Chaser" in batsman_attrs
            and (rrr > 7 or current_over >= max(1, round(total_overs * 0.50)))
        ):
            # Legacy attribute — only fires when "Chase Master" is absent.
            mult = ATTR_EFFECTS["Chaser"]["chase_bat_mult"]
            for k in ("1","2","3","4","6"): w[k] = w[k] * mult

    # CENTURION: +scoring after 50 runs (track via balls_since_wicket as proxy for set batsman)
    if "Centurion" in batsman_attrs and balls_since_wicket >= 30:
        mult = ATTR_EFFECTS["Centurion"]["fifty_bat_mult"]
        for k in ("1","2","3","4","6"): w[k] = w[k] * mult

    # PINCH HITTER: +boundaries, higher risk
    if "Pinch Hitter" in batsman_attrs:
        bm = ATTR_EFFECTS["Pinch Hitter"]["boundary_mult"]
        w["4"] = w["4"] * bm; w["6"] = w["6"] * bm
        w["W"] = w["W"] * ATTR_EFFECTS["Pinch Hitter"]["wkt_increase"]

    # CLUTCH: big boost in final overs of a chase
    _clutch_over = max(_death_start, round(total_overs * 0.85))
    if "Clutch" in batsman_attrs and innings == 2 and current_over >= _clutch_over:
        mult = ATTR_EFFECTS["Clutch"]["clutch_bat_mult"]
        for k in ("1","2","3","4","6"): w[k] = w[k] * mult

    # MATCH WINNER: +batting in last 5 overs
    if "Match Winner" in batsman_attrs and current_over >= _death_start:
        mult = ATTR_EFFECTS["Match Winner"]["death_bat_mult"]
        for k in ("1","2","3","4","6"): w[k] = w[k] * mult

    # COVER DRIVE SPECIALIST: better outcomes whenever the drive is played
    if "Cover Drive Specialist" in batsman_attrs and shot_internal == "Drive":
        cd = ATTR_EFFECTS["Cover Drive Specialist"]
        w["4"] = w["4"] * cd["drive_four_mult"]
        for k in ("1", "2", "3"): w[k] = w[k] * cd["drive_run_mult"]
        w["W"] = w["W"] * cd["drive_wkt_reduce"]

    # LOW BATTING QUALITY: almost no fours/sixes, mostly 1-2-3 runs
    if "Low Batting Quality" in batsman_attrs:
        lb = ATTR_EFFECTS["Low Batting Quality"]
        w["4"] = w["4"] * lb["four_mult"]
        w["6"] = w["6"] * lb["six_mult"]
        for k in ("1", "2", "3"): w[k] = w[k] * lb["rotate_mult"]

    # MR360: better outcomes on Sweep / Reverse-Sweep / Flick / Lofted shots
    if "Mr360" in batsman_attrs and shot_internal in _SHOTS_360:
        m360 = ATTR_EFFECTS["Mr360"]
        w["4"] = w["4"] * m360["four_mult"]
        w["6"] = w["6"] * m360["six_mult"]
        w["W"] = w["W"] * m360["wkt_reduce"]

    # ── Bowler attribute effects ─────────────────────────────────────────────

    # WICKET HUNTER: +wickets always
    if "Wicket Hunter" in bowler_attrs:
        w["W"] = w["W"] * ATTR_EFFECTS["Wicket Hunter"]["wkt_mult"]

    # SPIN WIZARD: +wickets on spin deliveries
    if "Spin Wizard" in bowler_attrs and delivery_internal in _SPIN_DELIVERIES:
        w["W"] = w["W"] * ATTR_EFFECTS["Spin Wizard"]["spin_wkt_mult"]

    # DEATH BOWLER: +wickets and -runs in death overs
    if "Death Bowler" in bowler_attrs and current_over >= _death_start:
        w["W"] = w["W"] * ATTR_EFFECTS["Death Bowler"]["death_wkt_mult"]
        rr = ATTR_EFFECTS["Death Bowler"]["death_run_reduce"]
        for k in ("1","2","3","4","6"): w[k] = w[k] * rr

    # POWERPLAY BOWLER: +wickets in powerplay
    if "Powerplay Bowler" in bowler_attrs and phase == "powerplay":
        w["W"] = w["W"] * ATTR_EFFECTS["Powerplay Bowler"]["pp_wkt_mult"]

    # YORKER KING: +wickets on yorker deliveries
    if "Yorker King" in bowler_attrs and delivery_internal in _YORKER_DELIVERIES:
        w["W"] = w["W"] * ATTR_EFFECTS["Yorker King"]["yorker_wkt_mult"]

    # SWING MASTER: +wickets on swing deliveries
    if "Swing Master" in bowler_attrs and delivery_internal in _SWING_DELIVERIES:
        w["W"] = w["W"] * ATTR_EFFECTS["Swing Master"]["swing_wkt_mult"]

    # BOUNCER SPECIALIST: +wickets on bouncers
    if "Bouncer Specialist" in bowler_attrs and delivery_internal in _BOUNCER_DELIVERIES:
        w["W"] = w["W"] * ATTR_EFFECTS["Bouncer Specialist"]["bouncer_wkt_mult"]

    # ECONOMICAL: -runs given per over
    if "Economical" in bowler_attrs:
        rr = ATTR_EFFECTS["Economical"]["run_reduce"]
        for k in ("1","2","3","4","6"): w[k] = w[k] * rr

    # MATCH WINNER: +wickets in death overs
    if "Match Winner" in bowler_attrs and current_over >= _death_start:
        w["W"] = w["W"] * ATTR_EFFECTS["Match Winner"]["death_wkt_mult"]

    # LETHAL PACE: more wickets and fewer runs off pace deliveries
    if "Lethal Pace" in bowler_attrs and delivery_internal in _PACE_DELIVERIES:
        lp = ATTR_EFFECTS["Lethal Pace"]
        w["W"] = w["W"] * lp["pace_wkt_mult"]
        for k in ("1", "2", "3", "4", "6"): w[k] = w[k] * lp["pace_run_reduce"]

    # EARLY BREAKER: +wickets in the first overs of the match
    # (first 3 overs of a 20-over game; scaled down for shorter matches)
    _early_end = max(1, min(3, round(total_overs * 0.15)))
    if "Early Breaker" in bowler_attrs and current_over < _early_end:
        w["W"] = w["W"] * ATTR_EFFECTS["Early Breaker"]["early_wkt_mult"]

    # GOOGLY MASTER: +wickets on Googly and Leg Break
    if "Googly Master" in bowler_attrs and delivery_internal in _GOOGLY_DELIVERIES:
        w["W"] = w["W"] * ATTR_EFFECTS["Googly Master"]["googly_wkt_mult"]

    # MYSTERY: +wickets (and slightly fewer boundaries) when the Mystery button is used
    if "Mystery" in bowler_attrs and delivery_button == "Mystery":
        my = ATTR_EFFECTS["Mystery"]
        w["W"] = w["W"] * my["wkt_mult"]
        w["4"] = w["4"] * my["boundary_reduce"]
        w["6"] = w["6"] * my["boundary_reduce"]

    # MIND GAMES: +wickets when this delivery type differs from the previous ball
    if (
        "Mind Games" in bowler_attrs
        and prev_delivery
        and delivery_internal != prev_delivery
    ):
        w["W"] = w["W"] * ATTR_EFFECTS["Mind Games"]["wkt_mult"]

    # SPELL FINISHER: better bowling in the bowler's own last over(s).
    #   max 1 over  -> that over | max 2 overs -> last 1 | max 3 overs -> last 1
    #   max 4 overs -> last 2    (first overs of the spell stay normal)
    if "Spell Finisher" in bowler_attrs and bowler_max_balls > 0:
        _quota_overs = max(1, bowler_max_balls // 6)
        _final_overs = 2 if _quota_overs >= 4 else 1
        _bowler_over = bowler_balls_done // 6          # 0-based over of this spell
        if _bowler_over >= _quota_overs - _final_overs:
            sf = ATTR_EFFECTS["Spell Finisher"]
            w["W"] = w["W"] * sf["wkt_mult"]
            for k in ("1", "2", "3", "4", "6"): w[k] = w[k] * sf["run_reduce"]

    # LOW BOWLING QUALITY: sixes/fours are easy, wickets very unlikely
    _six_cap_mult = 2.0
    if "Low Bowling Quality" in bowler_attrs:
        lq = ATTR_EFFECTS["Low Bowling Quality"]
        w["6"] = w["6"] * lq["six_mult"]
        w["4"] = w["4"] * lq["four_mult"]
        w["W"] = max(0.15, w["W"] * lq["wkt_reduce"])
        _six_cap_mult = lq["six_cap_mult"]

    # ── Hard cap on stacked six weight ───────────────────────────────────────
    # Even with every multiplier firing, six weight can't exceed 2× its
    # post-bat_factor baseline. Prevents pathological stacking when an
    # elite batter with Power Hitter + Pinch Hitter faces a weak bowler
    # in the powerplay/death.
    _six_cap = BASE_WEIGHTS["6"] * bat_factor * _six_cap_mult
    if w["6"] > _six_cap:
        w["6"] = _six_cap

    # Split no-balls deterministically into a 70:30 NB vs NB+1 ratio.
    # ~30% of no-balls also score a single off the free hit (NB+1), the rest
    # are pure no-balls. Replaces a per-call random coin flip that confused
    # the global wicket cap and made the distribution non-deterministic.
    if w.get("NB", 0) > 0:
        nb_total = w["NB"]
        w["NB"]   = nb_total * 0.70
        w["NB+1"] = nb_total * 0.30

    # ── Recommended shot bonus / wrong-shot penalty ───────────────────────────
    # A recommended shot slightly reduces wicket risk and boosts boundaries.
    # A poor shot choice increases wicket risk (on top of matchup matrix effect).
    # NOTE: the penalty only fires when a guide entry exists for this delivery
    # but the batter chose outside it.  When no entry exists (delivery not in
    # FAST_SHOT_GUIDE / SPIN_SHOT_GUIDE), is_recommended_shot arrives as False
    # but we treat it as neutral — no bonus, no penalty — to avoid punishing
    # batters for combinations the guide intentionally does not cover.
    if is_recommended_shot:
        w["W"] = max(0.3, w["W"] * 0.75)
        w["4"] = w["4"] * 1.15
        w["6"] = w["6"] * 1.10
    elif guide_entry_exists:
        # Guide covered this delivery but batter ignored it → wrong-shot penalty
        w["W"] = w["W"] * 1.25

    # ── Global wicket cap ─────────────────────────────────────────────────────
    # No matter how stacked the multipliers get, P(W) cannot exceed ~14%.
    # This keeps good-stats players from losing wickets on every single ball.
    other_total = sum(v for k, v in w.items() if k != "W")
    w["W"] = min(w["W"], other_total * 0.165)   # 0.165 / 1.165 ≈ 14%

    # ── Stadium effect — only GROUND_EFFECT_PCT (15%) ────────────────────────
    # Applied AFTER the global wicket cap so the full 15% always shows up.
    if ground_type in ("batting", "pacer", "spinner", "medium_pace"):
        from data import GROUND_EFFECT_PCT
        up   = 1.0 + GROUND_EFFECT_PCT / 100.0
        down = 1.0 - GROUND_EFFECT_PCT / 100.0
        is_spin_ball = delivery_internal in _SPIN_DELIVERIES
        if ground_type == "batting":
            w["4"] = w["4"] * up
            w["6"] = w["6"] * up
            w["W"] = w["W"] * down
        elif ground_type == "pacer":
            w["W"] = w["W"] * (down if is_spin_ball else up)
        elif ground_type == "spinner":
            w["W"] = w["W"] * (up if is_spin_ball else down)
        elif ground_type == "medium_pace":
            if delivery_internal in _SEAM_DELIVERIES:
                w["W"] = w["W"] * up

    outcome = random.choices(list(w.keys()), weights=list(w.values()), k=1)[0]
    is_extra = outcome in ("Wd", "NB", "NB+1")
    return outcome, is_extra
    