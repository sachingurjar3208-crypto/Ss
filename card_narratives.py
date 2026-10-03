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
}

NARRATIVE_NAMES: list[str] = list(NARRATIVES)

# Role codes match the ones the match engine already uses (BAT / BOWL / AR / WK).
ROLES: dict[str, str] = {
    "BAT": "Batter",
    "BOWL": "Bowler",
    "AR": "All-Rounder",
    "WK": "Wicketkeeper",
}

# Words printed on the card image.
ROLE_CARD_WORD: dict[str, str] = {
    "BAT": "BATTER",
    "BOWL": "BOWLER",
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
