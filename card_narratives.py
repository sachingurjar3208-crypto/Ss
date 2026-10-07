"""Playstyle "narratives" a player card can carry (max 2 per card).

Each narrative name doubles as the name of its logo: add the logo image with
/logoadderofplaystyle using the SAME name (e.g. pathname "Swing King"), and
the logo is drawn on the card automatically whenever that narrative is chosen
in /cardmaker or /editcard.

These narratives now change how matches play out: logic.py reads a player's
card by name and applies the effect (see ATTR_EFFECTS there to tune numbers).
"Swing King" is the engine's existing "Swing Master"; "Balanced" is neutral.
"""

from __future__ import annotations

NARRATIVES: dict[str, str] = {
    "Swing King": "Bowler who gets a lot of swing: +25% wickets on swing deliveries.",
    "Yorker King": "Bowler who nails yorkers: +30% wickets on yorkers.",
    "Lethal Pace": "Express pace bowler: +25% wickets and -8% runs off pace deliveries.",
    "Aggressive": "Attacking batter: +10% fours/sixes, -10% dot balls.",
    "Balanced": "Steady all-round approach: no bonus, no penalty.",
    "Low Batting Quality": (
        "Weak batter: can't hit sixes or fours, mostly 1-2-3 runs; "
        "four/six chances are very low."
    ),
    "Low Bowling Quality": (
        "Weak bowler: easy to hit sixes against, and very low chance of taking wickets."
    ),
    "Cover Drive Specialist": "On the Drive shot: +40% fours, +10% 1-2-3 runs, -20% wicket chance.",
    "Chase Master": "Thrives when chasing: +15% scoring in the 2nd innings.",
    "Early Breaker": "Bowler who strikes early: +30% wickets in the first overs of the match.",
    "Googly Master": "Leg-spin wizard: +35% wickets on Googly and Leg Break deliveries.",
    "Mr360": "360-degree batter: +25% fours, +15% sixes, -10% wicket chance on Sweep, Reverse-Sweep, Flick and Lofted shots.",
    "Mystery": "Mystery bowler: +30% wickets and -8% boundaries on Mystery-button deliveries.",
    "Spell Finisher": "Bowler who saves his best for his last over(s): +25% wickets, -10% runs (last 1 over, last 2 if he bowls 4).",
    "Mind Games": "Trickster bowler: +25% wickets when the delivery type changes from the previous ball.",
}

NARRATIVE_NAMES: list[str] = list(NARRATIVES)

# Role codes match the ones the match engine already uses (BAT / BOWL / AR / WK).
ROLES: dict[str, str] = {
    "BAT": "Batter",
    "BOWL": "Bowler",
    "LBOWL": "Left Arm Bowler",
    "RBOWL": "Right Arm Bowler",
    "AR": "All-Rounder",
    "WK": "Wicketkeeper",
}

# LBOWL / RBOWL only change the word printed on the card; everywhere else
# (XI rules, match engine, squad screens) they count as a normal BOWL.
BASE_ROLE: dict[str, str] = {
    "BAT": "BAT", "BOWL": "BOWL", "AR": "AR", "WK": "WK",
    "LBOWL": "BOWL", "RBOWL": "BOWL",
}

# Words printed on the card image.
ROLE_CARD_WORD: dict[str, str] = {
    "BAT": "BATTER",
    "BOWL": "BOWLER",
    "LBOWL": "LEFT ARM BOWLER",
    "RBOWL": "RIGHT ARM BOWLER",
    "AR": "ALL-ROUNDER",
    "WK": "WICKETKEEPER",
}

HANDS: dict[str, str] = {
    "R": "Right hand",
    "L": "Left hand",
}

HAND_CARD_WORD: dict[str, str] = {
    "R": "RIGHT HAND BAT",
    "L": "LEFT HAND BAT",
}


# Bowling types the match engine understands (data.py MYSTERY_POOL keys).
BOWLING_TYPES: dict[str, str] = {
    "Fast": "Fast",
    "Off Spin": "Off Spin",
    "Leg Spin": "Leg Spin",
}

# Role names as the match engine and team embeds expect them.
ENGINE_ROLE: dict[str, str] = {
    "BAT": "Batter",
    "BOWL": "Bowler",
    "AR": "All-Rounder",
    "WK": "WK",
}
