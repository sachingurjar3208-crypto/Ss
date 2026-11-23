"""Playstyle "narratives" a player card can carry (max 2 per card).

Each narrative name doubles as the name of its logo: add the logo image with
/logoadderofplaystyle using the SAME name (e.g. pathname "Swing King"), and
the logo is drawn on the card automatically whenever that narrative is chosen
in /cardmaker or /editcard.

These narratives now change how matches play out: logic.py reads a player's
card by name and applies the effect (see ATTR_EFFECTS there to tune numbers).
"Swing King" and "Swing Master" are two separate playstyles; "Balanced" is neutral.
Players only ever see the playstyle NAME (never these descriptions or any percentages).
"""

from __future__ import annotations

NARRATIVES: dict[str, str] = {
    "Swing King": "Bowler who gets a lot of swing: +25% wickets on swing deliveries.",
    "Swing Master": "Swing specialist: batters struggle to hit sixes and wickets come more often on swing deliveries.",
    "Breakthrough": "Game changer batter (Maxwell / Russell / Pollard type): hits more sixes and can win the match in a chase, but is slightly more likely to get out.",
    "Yorker King": "Bowler who nails yorkers: +30% wickets on yorkers.",
    "Lethal Pace": "Express pace bowler: +25% wickets and -8% 1s-4s off pace deliveries, but concedes +15% sixes.",
    "Aggressive": "Attacking batter: +25% fours/sixes, -10% dot balls.",
    "Balanced": "Steady all-round approach: no bonus, no penalty.",
    "Anchor": "Rock-solid batter: -35% chance of getting out.",
    "Low Batting Quality": (
        "Weak batter: can't hit sixes or fours, mostly 1-2-3 runs; "
        "four/six chances are very low."
    ),
    "Low Bowling Quality": (
        "Weak bowler: easy to hit sixes against, and very low chance of taking wickets."
    ),
    "Cover Drive Specialist": "On the Drive shot: +40% fours, +10% 1-2-3 runs.",
    "Chase Master": "Thrives when chasing: +25% scoring in the 2nd innings.",
    "Early Breaker": "Bowler who strikes early: +30% wickets in the first overs of the match.",
    "Googly Master": "Leg-spin wizard: +35% wickets on Googly and Leg Break deliveries.",
    "Mr360": "360-degree batter: +25% fours, +15% sixes on Sweep, Reverse-Sweep, Flick and Lofted shots.",
    "Mystery": "Mystery bowler: +30% wickets and -8% boundaries on Mystery-button deliveries.",
    "Spell Finisher": "Bowler who saves his best for his last over(s): +25% wickets, -10% runs (last 1 over, last 2 if he bowls 4).",
    "Mind Games": "Trickster bowler: +25% wickets when the delivery type changes from the previous ball.",
    "Accumulator": "Batter who builds long innings: the more balls he faces, the better he plays (up to +15% runs, -15% wicket chance at 30 balls).",
    "Blitz": "Explosive starter: +25% fours/sixes and -15% dot balls in the powerplay.",
    "Disruptor": "Partnership breaker: +20% wickets when the batting pair has added 30+ runs.",
    "Maestro": "All-rounder: +8% batting and +8% wickets, all match long.",
    # older engine playstyles, now available on cards too
    "Centurion": "Big-innings batter: +10% scoring after reaching 50.",
    "Finisher": "Closes out innings: +20% scoring in the death overs.",
    "Opener": "Top-order starter: +15% scoring in the powerplay.",
    "Power Hitter": "Hits the ball a mile: +25% fours/sixes and extra sixes, but a slightly higher risk of getting out.",
    "Pinch Hitter": "All-out attack: +25% fours/sixes, but a higher risk of getting out.",
    "Clinical": "Keeps the board moving: -15% dot balls, +20% singles.",
    "Clutch": "Delivers under pressure: +30% scoring in tight finishes.",
    "Match Winner": "Big-moment player: +10% batting and +10% wickets in the last overs.",
    "Chaser": "Good in a chase: +20% scoring in the 2nd innings.",
    "Death Bowler": "Bowls the tough overs: +20% wickets and -12% runs in the death overs.",
    "Powerplay Bowler": "Takes the new ball: +15% wickets in the powerplay.",
    "Spin Wizard": "Spin master: +40% wickets with spin deliveries.",
    "Bouncer Specialist": "Short-ball expert: +25% wickets with bouncers.",
    "Economical": "Hard to score off: -15% runs conceded.",
    "Wicket Hunter": "Always attacking: +35% wickets all match long.",
    "CheatCode": ("Unstoppable batter. Golden Pink (L3): sixes almost every ball until he reaches 50, then up to "
                  "2 sixes and 1 four an over with a single on the last ball 70% of the time. Ranked: plays at Gold. "
                  "Silver / Gold: +50% fours/sixes, -40% wicket chance."),
    "Genius": ("Mastermind bowler: 85% of his overs bring 2 wickets and 2 dot balls (Silver: about half as often), "
               "plus +20% wickets and -10% runs on the other balls."),
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
    "Medium Pacer": "Medium Pacer",
    "Off Spin": "Off Spin",
    "Leg Spin": "Leg Spin",
    "Orthodox": "Orthodox Spin",
}

# Role names as the match engine and team embeds expect them.
ENGINE_ROLE: dict[str, str] = {
    "BAT": "Batter",
    "BOWL": "Bowler",
    "AR": "All-Rounder",
    "WK": "WK",
}
