"""Line + length + direction batting engine (fast / medium pacers and spinners).

Bowler (pace):   Type (Outswing / Inswing / Pace or Cutter / Slower)  +  Line  ->  Length
Batter:          Direction (7, fixed like the field map, same for right and left handers)  ->  Shot

How a ball is judged
  1. LINE fit:   does the direction suit the line?   (0 wrong, 1 ok, 2 natural)
  2. LENGTH fit: does the shot (ground / loft) suit the length for that direction?   (0, 1, 2)
  score = line + length  ->  4 perfect, 3 good, 2 ok, 1 risky, 0 bad
  3. TYPE: swing, pace and slower balls nudge the risk (edges, bowled, mistimed lofts).

The score only CHANGES THE CHANCES inside logic.calculate_outcome. Bat / bowl ratings, playstyles,
phase, pitch and every cap still apply, so a perfect shot can still get out and a bad one can still
go for six, just less often.
"""
from __future__ import annotations

import random

DIRECTIONS = ["Third Man", "Off Side", "Deep Cover", "Straight", "Mid Wicket", "Leg Side", "Fine Leg"]
INTENTS = ["Ground", "Loft", "Block", "Leave"]          # button text
INTENT_ENGINE = {"Ground": "Hit", "Loft": "Loft"}         # logic.py intent keys (Block / Leave: none)
LINES = ["Outside Off", "Stumps", "Leg Stump"]
LENGTHS = ["Yorker", "Full", "Good", "Short", "Bouncer"]

# ── what the bowler picked -> line / length / type ──────────────────────────
_LENGTH_OF_BUTTON = {
    "Yorker": "Yorker", "Full": "Full", "Good": "Good", "Good Length": "Good",
    "Short": "Short", "Bouncer": "Bouncer",
}
# spinners have no line / length buttons: each ball has a natural one
SPIN_LINE = {
    "Offspin": "Stumps", "Carrom": "Outside Off", "Arm Ball": "Stumps", "Doosra": "Outside Off",
    "Topspin": "Stumps", "Top Spinner": "Stumps", "Legspin": "Outside Off", "Googly": "Stumps",
    "Flipper": "Stumps", "Drifter": "Leg Stump", "Slider": "Stumps", "Mystery": "Stumps",
}
SPIN_LENGTH = {
    "Arm Ball": "Full", "Flipper": "Full", "Drifter": "Full", "Slider": "Full",
    "Topspin": "Good", "Top Spinner": "Good",
}
_TYPE_OF_STAGE1 = {
    "Outswing": "away", "Inswing": "in", "Fast": "pace", "Slow": "slow",
    "Cutter": "cutter", "Slower": "slow",
}


def length_of(delivery_button: str, is_spin: bool) -> str:
    if is_spin:
        return SPIN_LENGTH.get(delivery_button, "Good")
    return _LENGTH_OF_BUTTON.get(delivery_button, "Good")


def line_of(game, delivery_button: str, is_spin: bool) -> str:
    line = game.__dict__.get("pending_line") if game is not None else None
    if line in LINES:
        return line
    return SPIN_LINE.get(delivery_button, "Stumps") if is_spin else "Stumps"


def type_of(stage1: str, is_spin: bool) -> str:
    return "spin" if is_spin else _TYPE_OF_STAGE1.get(stage1 or "", "pace")


# ── fit tables ──────────────────────────────────────────────────────────────
LINE_FIT = {
    "Third Man":  {"Outside Off": 2, "Stumps": 1, "Leg Stump": 0},
    "Off Side":   {"Outside Off": 2, "Stumps": 1, "Leg Stump": 0},
    "Deep Cover": {"Outside Off": 2, "Stumps": 1, "Leg Stump": 0},
    "Straight":   {"Outside Off": 1, "Stumps": 2, "Leg Stump": 1},
    "Mid Wicket": {"Outside Off": 0, "Stumps": 2, "Leg Stump": 2},
    "Leg Side":   {"Outside Off": 0, "Stumps": 2, "Leg Stump": 2},
    "Fine Leg":   {"Outside Off": 0, "Stumps": 1, "Leg Stump": 2},
}
#                         Yorker Full Good Short Bouncer
_G = {  # along the ground
    "Third Man":  (0, 1, 2, 2, 1),   # late cut / dab
    "Off Side":   (0, 0, 1, 2, 1),   # square cut
    "Deep Cover": (0, 2, 2, 0, 0),   # cover drive
    "Straight":   (1, 2, 2, 0, 0),   # straight drive
    "Mid Wicket": (1, 2, 2, 1, 0),   # flick
    "Leg Side":   (0, 0, 1, 2, 1),   # pull
    "Fine Leg":   (1, 2, 2, 1, 1),   # leg glance
}
_L = {  # in the air
    "Third Man":  (0, 0, 0, 2, 2),   # upper cut
    "Off Side":   (0, 0, 1, 2, 1),   # cut over point
    "Deep Cover": (0, 2, 1, 0, 0),   # inside-out loft
    "Straight":   (1, 2, 1, 0, 0),   # lofted drive / helicopter on a yorker
    "Mid Wicket": (0, 2, 1, 1, 0),   # slog
    "Leg Side":   (0, 0, 1, 2, 1),   # pull for six
    "Fine Leg":   (0, 2, 0, 1, 2),   # scoop (full) / hook (bouncer)
}
# spinners: sweeps and slog sweeps work, cuts need width
_G_SPIN = dict(_G, **{"Leg Side": (0, 2, 2, 1, 1), "Mid Wicket": (1, 2, 2, 1, 1)})
_L_SPIN = dict(_L, **{"Mid Wicket": (0, 2, 2, 1, 0), "Straight": (1, 2, 2, 0, 0)})

QUALITY = {4: "perfect", 3: "good", 2: "ok", 1: "risky", 0: "bad"}
# weight multipliers per quality (applied to the outcome weights in logic.py)
_Q_MULT = {
    "perfect": {"0": 0.88, "1": 1.05, "2": 1.08, "3": 1.08, "4": 1.30, "6": 1.28, "W": 0.68},
    "good":    {"0": 0.92, "1": 1.05, "2": 1.05, "3": 1.05, "4": 1.18, "6": 1.12, "W": 0.80},
    "ok":      {},
    "risky":   {"0": 1.12, "1": 0.95, "2": 0.95, "3": 0.90, "4": 0.75, "6": 0.70, "W": 1.30},
    "bad":     {"0": 1.25, "1": 0.85, "2": 0.85, "3": 0.80, "4": 0.50, "6": 0.45, "W": 1.70},
}
_OFF = {"Third Man", "Off Side", "Deep Cover"}
_LEG = {"Mid Wicket", "Leg Side", "Fine Leg"}


# ── shot names ──────────────────────────────────────────────────────────────
def shot_for(direction: str | None, intent: str, length: str, is_spin: bool) -> tuple[str, str]:
    """-> (engine shot key from data.BATTING_BUTTONS, name shown in commentary)."""
    if intent == "Block":
        return "Defend", "Block"
    if intent == "Leave":
        return "Leave", "Leave"
    d = direction or "Straight"
    if intent == "Ground":
        if d == "Third Man":
            return "Cut", "Late Cut"
        if d == "Off Side":
            return "Cut", "Square Cut"
        if d == "Deep Cover":
            return "Drive", "Cover Drive"
        if d == "Straight":
            return "Drive", ("Back-foot Punch" if length == "Short" else "Straight Drive")
        if d == "Mid Wicket":
            return ("Sweep", "Sweep") if is_spin and length in ("Good", "Short") else ("Flick", "Flick")
        if d == "Leg Side":
            return ("Sweep", "Sweep") if is_spin else ("Pull", "Pull")
        return "Flick", "Leg Glance"
    # Loft
    if d == "Third Man":
        return "Cut", "Upper Cut"
    if d == "Off Side":
        return "Cut", "Lofted Cut"
    if d == "Deep Cover":
        return "Loft", "Inside-out Loft"
    if d == "Straight":
        return "Loft", ("Helicopter Shot" if length == "Yorker" else "Lofted Drive")
    if d == "Mid Wicket":
        return ("Sweep", "Slog Sweep") if is_spin else ("Loft", "Slog")
    if d == "Leg Side":
        return "Pull", ("Hook" if length == "Bouncer" else "Pull")
    if length in ("Bouncer", "Short"):
        return "Pull", "Hook"
    return "Scoop", "Scoop"


# ── judging one ball ────────────────────────────────────────────────────────
def evaluate(direction: str | None, intent: str, line: str, length: str, btype: str) -> dict:
    """Everything the engine and the commentary need for one ball."""
    is_spin = btype == "spin"
    shot_key, shot_name = shot_for(direction, intent, length, is_spin)
    res = {"shot_key": shot_key, "shot_name": shot_name, "direction": direction, "intent": intent,
           "line": line, "length": length, "btype": btype, "score": 2, "quality": "ok",
           "mult": {}, "kind_weights": None, "use_matrix": is_spin}
    if intent in ("Block", "Leave"):
        res["kind_weights"] = _block_kinds(line, length, intent)
        return res

    d = direction or "Straight"
    li = LENGTHS.index(length) if length in LENGTHS else 2
    table = (_G_SPIN if intent == "Ground" else _L_SPIN) if is_spin else (_G if intent == "Ground" else _L)
    score = LINE_FIT[d].get(line, 1) + table[d][li]
    q = QUALITY[score]
    mult = dict(_Q_MULT[q])

    def bump(k, f):
        mult[k] = mult.get(k, 1.0) * f

    # ball type: real-life tendencies, small nudges only
    if btype == "away" and d in _OFF:
        bump("W", 1.15)                                   # chasing the away swinger = edges
    if btype == "away" and d in _LEG and line != "Leg Stump":
        bump("W", 1.12)                                   # playing across the swing
    if btype == "in" and d in _OFF and line != "Outside Off":
        bump("W", 1.22)                                   # gate open, bowled
    if btype == "in" and d in _LEG:
        bump("4", 1.08)                                   # into the pads, easy to work
    if btype == "pace" and score >= 3:
        bump("4", 1.10); bump("6", 1.08)                  # use the pace
    if btype == "pace" and score <= 1:
        bump("W", 1.10)
    if btype in ("slow", "cutter") and intent == "Loft":
        bump("W", 1.20); bump("6", 0.85)                  # early on the shot, skied
    if btype == "cutter" and d in _OFF and intent == "Ground":
        bump("W", 1.10)
    if is_spin and intent == "Loft" and line == "Outside Off" and d in _LEG:
        bump("W", 1.15)                                   # against the turn

    res.update(score=score, quality=q, mult=mult,
               kind_weights=_kinds(d, intent, line, length, btype))
    return res


def _kinds(d, intent, line, length, btype) -> dict:
    """How a wicket on this ball happens (commentary + DRS use the kind)."""
    if intent == "Loft":
        w = {"caught": 82, "bowled": 12, "lbw": 6}
        if btype == "spin":
            w["stumped"] = 18
        return w
    if length == "Yorker":
        return {"bowled": 50, "lbw": 40, "caught": 10}
    if length in ("Short", "Bouncer"):
        return {"caught": 85, "bowled": 15}
    if d in _OFF:
        w = {"caught": 60, "bowled": 28, "lbw": 12}      # edge to slips / gully, or through the gate
        if line != "Outside Off":
            w = {"bowled": 45, "lbw": 25, "caught": 30}
        return w
    if d in _LEG:
        w = {"lbw": 45, "caught": 35, "bowled": 20}
        if line == "Outside Off":
            w = {"caught": 55, "bowled": 25, "lbw": 20}
        return w
    w = {"bowled": 40, "lbw": 30, "caught": 30}
    if btype == "spin":
        w["stumped"] = 10
    return w


def _block_kinds(line, length, intent) -> dict:
    if intent == "Leave":
        return {"bowled": 65, "lbw": 35}
    if line == "Outside Off":
        return {"caught": 60, "bowled": 25, "lbw": 15}
    return {"bowled": 45, "lbw": 40, "caught": 15}


def leave_outcome(line: str, length: str, btype: str, bowl: int) -> str:
    """Leaving the ball: safe outside off, a gamble on the stumps."""
    if line == "Outside Off":
        return "W" if (btype == "in" and random.random() < 0.06) else "0"   # swings back in, rare
    if line == "Leg Stump":
        return "W" if random.random() < 0.03 else "0"
    base = 0.22 if length in ("Yorker", "Full", "Good") else 0.04          # short balls go over
    factor = max(0.6, min(1.5, 1 + (bowl - 80) * 0.025))
    if btype == "away":
        base *= 0.6                                                       # moves away from the stumps
    return "W" if random.random() < base * factor else "0"


# ── fielding position for the commentary ────────────────────────────────────
_ZONES = {   # (inner, deep)
    "Third Man":  (["gully", "short third man"], ["third man", "deep third"]),
    "Off Side":   (["point", "backward point"], ["deep point", "deep backward point"]),
    "Deep Cover": (["cover", "extra cover"], ["deep cover", "deep extra cover"]),
    "Straight":   (["mid-off", "mid-on"], ["long-off", "long-on"]),
    "Mid Wicket": (["mid-wicket"], ["deep mid-wicket", "cow corner"]),
    "Leg Side":   (["square leg"], ["deep square leg", "deep backward square"]),
    "Fine Leg":   (["short fine leg", "leg slip"], ["fine leg", "deep fine leg"]),
}
_ZONE_WORDS = ("cover", "point", "third man", "mid-off", "mid-on", "long-off", "long-on", "mid-wicket",
               "square", "fine leg", "gully", "slip", "keeper", "cow corner", "rope", "fence")


def zone_line(ev: dict | None, outcome: str, kind: str, main_text: str) -> str:
    """A short "where did it go" sentence, or "" (never repeats a place the line already names)."""
    if not ev or ev.get("intent") in ("Leave",) or outcome in ("Wd", "NB", "NB+1"):
        return ""
    d = ev.get("direction")
    if d not in _ZONES or any(w in main_text.lower() for w in _ZONE_WORDS):
        return ""
    inner, deep = _ZONES[d]
    loft = ev.get("intent") == "Loft"
    if outcome == "W":
        if kind != "caught":
            return ""
        if d in _OFF and not loft:
            place = random.choice(["second slip", "gully", "the keeper"] if d == "Third Man"
                                  else inner + ["second slip"])
        else:
            place = random.choice(deep if loft else inner)
        return f"Taken at {place}."
    if outcome == "6":
        return f"Over {random.choice(deep)}."
    if outcome == "4":
        return f"Past {random.choice(deep)}."
    if outcome in ("1", "2", "3"):
        return f"Into the gap at {random.choice(deep if outcome != '1' else inner)}."
    if outcome == "0" and ev.get("intent") == "Ground" and not any(
            w in main_text.lower() for w in ("beaten", "miss", "past the bat", "beats the bat", "nothing")):
        return f"Straight to {random.choice(inner)}."
    return ""


# boundary lines that name the right part of the ground ({shot} = the shot name above)
# Same word rules as commentary.py: no stump / caught / catch / lbw / plumb / in front here.
DIR_LINES = {
    "Third Man": {
        ("Ground", "4"): ["{shot}, opens the face and it runs away to the third man fence.",
                          "Dabbed late, beats short third man. Four."],
        ("Loft", "4"):   ["{shot}, one bounce over third man. Four."],
        ("Loft", "6"):   ["{shot}! Sails over third man for six.",
                          "Rides the bounce and slaps it over the third man rope!"],
    },
    "Off Side": {
        ("Ground", "4"): ["{shot}, crunched past point. Four.",
                          "Rocks back and cuts hard, point has no chance."],
        ("Loft", "4"):   ["{shot}, clears point and races away. Four."],
        ("Loft", "6"):   ["{shot} over point, all the way!", "Cuts it high over deep point. Six!"],
    },
    "Deep Cover": {
        ("Ground", "4"): ["Glorious {shot}, pierces the gap at extra cover.",
                          "Leans into it and drives through cover. Four."],
        ("Loft", "4"):   ["Lofted over cover, bounces once and over the rope."],
        ("Loft", "6"):   ["{shot} over extra cover! Six.", "Makes room and launches it over deep cover!"],
    },
    "Straight": {
        ("Ground", "4"): ["{shot}, past the bowler and to the rope.",
                          "{shot} down the ground, mid-off does not move. Four."],
        ("Loft", "4"):   ["{shot}, not fully timed, but it clears long-off. Four."],
        ("Loft", "6"):   ["{shot}! Straight back over the bowler's head.",
                          "{shot}, and it lands way back over long-on!"],
    },
    "Mid Wicket": {
        ("Ground", "4"): ["{shot} through mid-wicket. Four.", "Whipped off the pads, mid-wicket dives in vain."],
        ("Loft", "4"):   ["{shot}, clears mid-wicket and runs away. Four."],
        ("Loft", "6"):   ["{shot} over deep mid-wicket! Huge.", "Clears the front leg, cow corner, gone!"],
    },
    "Leg Side": {
        ("Ground", "4"): ["{shot}, square leg can only watch. Four.", "{shot} hard, beats deep square. Four."],
        ("Loft", "4"):   ["{shot}, one bounce over square leg. Four."],
        ("Loft", "6"):   ["{shot} for six over deep square leg!", "{shot}, and it goes over the rope behind square!"],
    },
    "Fine Leg": {
        ("Ground", "4"): ["{shot} off the pads, races to the fine leg fence.",
                          "Just a touch of the wrists, fine leg has no chance. Four."],
        ("Loft", "4"):   ["{shot}, fine leg runs, it beats him. Four."],
        ("Loft", "6"):   ["{shot} over fine leg, all the way!", "{shot}, and it sails over the fine leg rope!"],
    },
}


def dir_pool(ev: dict | None, outcome: str) -> list[str] | None:
    if not ev:
        return None
    return DIR_LINES.get(ev.get("direction"), {}).get((ev.get("intent"), outcome))


# ── autoplay / ranked bots ──────────────────────────────────────────────────
def auto_pick(line: str, length: str, btype: str, skill: float | None) -> tuple[str | None, str]:
    """(direction, intent) for the bot. Better bots read the ball better."""
    options = []
    loft_cost = random.choice((0.6, 0.6, 0.0))      # about one ball in three the bot is happy to go big
    for d in DIRECTIONS:
        for it in ("Ground", "Loft"):
            ev = evaluate(d, it, line, length, btype)
            options.append((ev["score"] - (loft_cost if it == "Loft" else 0), d, it))
    options.sort(key=lambda o: o[0], reverse=True)
    sk = 0.75 if skill is None else max(0.0, min(1.0, skill))
    if random.random() < 0.06:
        return None, "Block"
    if random.random() < sk:
        top = [o for o in options if o[0] >= options[0][0] - 0.5]
        _, d, it = random.choice(top)
    else:
        _, d, it = random.choice(options[:10])
    return d, it
