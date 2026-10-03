from __future__ import annotations

from __future__ import absolute_import
import random
from six.moves import range

# Rarity normalization: badge_rarity values are 0.03 (best) to 20.0 (worst)
# Normalized: rarity/20.0 gives 0-1 scale where lower = better bonus
MAX_RARITY_VALUE = 20.0


def _normalize_rarity(rarity: float) -> float:
    if not rarity:
        return 1.0
    r = float(rarity)
    return max(0.0, min(1.0, r / MAX_RARITY_VALUE))


BALL_SPEEDS: dict[str, tuple[float, float]] = {
    "Fast": (135, 150),
    "OutSwing": (122, 137),
    "InSwing": (123, 138),
    "Reverse Swing": (115, 130),
    "Yorker": (138, 148),
    "Bouncer": (135, 150),
    "Good Length": (125, 140),
    "Full": (126, 138),
    "Cutter": (118, 136),
    "Off Break": (85, 98),
    "Doosra": (88, 100),
    "Carrom Ball": (85, 96),
    "Arm Ball": (88, 98),
    "Top Spin": (85, 96),
    "Drift Ball": (82, 95),
    "Leg Break": (82, 95),
    "Googly": (82, 92),
    "Flipper": (88, 98),
    "Top Spinner": (83, 96),
    "Slider": (83, 95),
    "Stock": (82, 89),
    "Topspinner": (81, 90),
    "Undercutter": (83, 90),
    "Outswinger": (120, 135),
    "Inswinger": (121, 136),
    "Slower Ball": (105, 120),
    "Knuckle": (110, 125),
    "Off Cutter": (115, 130),
    "Leg Cutter": (115, 130),
}

FAST_DELIVERIES = [
    "Fast",
    "OutSwing",
    "InSwing",
    "Reverse Swing",
    "Yorker",
    "Bouncer",
    "Good Length",
    "Full",
    "Cutter",
]
OFF_SPIN_DELIVERIES = ["Off Break", "Doosra", "Carrom Ball", "Arm Ball", "Top Spin"]
LEG_SPIN_DELIVERIES = [
    "Drift Ball",
    "Leg Break",
    "Googly",
    "Flipper",
    "Top Spinner",
    "Slider",
]
ORTHODOX_DELIVERIES = ["Stock", "Arm Ball", "Topspinner", "Slider", "Undercutter"]
FAST_MEDIUM_SWING_DELIVERIES = [
    "Outswinger",
    "Inswinger",
    "Reverse Swing",
    "Cutter",
    "Slower Ball",
]
MEDIUM_PACE_DELIVERIES = ["Knuckle", "Off Cutter", "Leg Cutter", "Bouncer"]
ALL_SPIN = set(OFF_SPIN_DELIVERIES + LEG_SPIN_DELIVERIES + ORTHODOX_DELIVERIES)
BATTING_SHOTS = [
    "Drive",
    "Pull",
    "Cut",
    "Sweep",
    "Lofted",
    "Flick",
    "Defend",
    "Reverse-Sweep",
    "Back-foot",
    "Front-foot",
]


PREDEFINED_ORTHODOX_SHOT_PAIRS = {
    ("Stock", "Full Length"): ["Drive", "Defend"],
    ("Stock", "Good Length"): ["Defend", "Back-foot"],
    ("Stock", "Short"): ["Square Cut", "Back-foot"],
    ("Arm Ball", "Full"): ["Drive", "Front-foot"],
    ("Arm Ball", "Good"): ["Drive", "Front-foot"],
    ("Arm Ball", "Short"): ["Pull", "Back-foot"],
    ("Topspinner", "Full Length"): ["Sweep"],
    ("Topspinner", "Good Length"): ["Back-foot"],
    ("Topspinner", "Short"): ["Back-foot", "Pull"],
    ("Slider", "Full"): ["Drive", "Front-foot"],
    ("Slider", "Good"): ["Back-foot"],
    ("Slider", "Short"): ["Pull", "Back-foot"],
    ("Undercutter", "Full"): ["Drive"],
    ("Undercutter", "Good"): ["Back-foot"],
    ("Undercutter", "Short"): ["Cut", "Drive"],
}
ORTHODOX_LEGAL_SHOTS = {
    "Full Length": ["Drive", "Defend", "Front-foot", "Back-foot"],
    "Good Length": ["Drive", "Defend", "Back-foot", "Front-foot"],
    "Short": ["Pull", "Cut", "Back-foot", "Front-foot"],
}

BOWLING_DELIVERIES = {
    "Fast": FAST_DELIVERIES,
    "Off Spin": OFF_SPIN_DELIVERIES,
    "Leg Spin": LEG_SPIN_DELIVERIES,
    "Orthodox": ORTHODOX_DELIVERIES,
    "Fast-Medium Swing": FAST_MEDIUM_SWING_DELIVERIES,
    "Medium-Pace": MEDIUM_PACE_DELIVERIES,
}

FAST_BOWLING_STYLES = ["Fast", "InSwing", "OutSwing", "Cutter", "Slow"]
FAST_BOWLING_LENGTHS = [
    "Yorker",
    "Bouncer",
    "Good Length",
    "Full",
    "Slot",
    "Short Ball",
]
FAST_MEDIUM_SWING_STYLES = [
    "Outswinger",
    "Inswinger",
    "Reverse Swing",
    "Cutter",
    "Slower Ball",
]
FAST_MEDIUM_SWING_LENGTHS = ["Yorker", "Good Length", "Full", "Slot"]
MEDIUM_PACE_DELIVERIES_LIST = ["Knuckle", "Off Cutter", "Leg Cutter", "Bouncer"]
MEDIUM_PACE_LENGTHS = [
    "Knuckle",
    "Off Cutter",
    "Leg Cutter",
    "Bouncer (Fast)",
    "Bouncer (Slow)",
]

FAST_COMBO_MAP = {}
for style in FAST_BOWLING_STYLES:
    for length in FAST_BOWLING_LENGTHS:
        speed_mod = 0.0
        if style == "Slow" or style == "Cutter":
            speed_mod -= 15.0
        elif style == "Fast":
            speed_mod += 2.0
        if length == "Yorker":
            speed_mod += 1.0
        elif length == "Slow":
            speed_mod -= 2.0
        FAST_COMBO_MAP[(style, length)] = (length, speed_mod)

FAST_MEDIUM_SWING_COMBO_MAP = {}
for style in FAST_MEDIUM_SWING_STYLES:
    if style == "Cutter":
        continue
    for length in FAST_MEDIUM_SWING_LENGTHS:
        speed_mod = -3.0
        if length == "Yorker":
            speed_mod += 1.0
        FAST_MEDIUM_SWING_COMBO_MAP[(style, length)] = (style, speed_mod)

MEDIUM_PACE_COMBO_MAP = {
    ("Knuckle", "Knuckle"): ("Knuckle", -10.0),
    ("Off Cutter", "Off Cutter"): ("Off Cutter", -8.0),
    ("Leg Cutter", "Leg Cutter"): ("Leg Cutter", -8.0),
    ("Bouncer (Fast)", "Bouncer (Fast)"): ("Bouncer", 0.0),
    ("Bouncer (Slow)", "Bouncer (Slow)"): ("Good Length", -5.0),
}


MATCHUP_MATRIX: dict[tuple[str, str], dict] = {
    # ── Yorker ───────────────────────────────────────────────────────────────
    ("Yorker", "Drive"): {"bat": 1.20, "wkt": 0.40},
    ("Yorker", "Pull"): {"bat": 0.55, "wkt": 4.50},
    ("Yorker", "Cut"): {"bat": 0.60, "wkt": 3.80},
    ("Yorker", "Sweep"): {"bat": 0.50, "wkt": 5.00},
    ("Yorker", "Lofted"): {"bat": 0.45, "wkt": 4.00},
    ("Yorker", "Flick"): {"bat": 1.05, "wkt": 0.60},
    ("Yorker", "Defend"): {"bat": 1.10, "wkt": 0.30},
    ("Yorker", "Reverse-Sweep"): {"bat": 0.40, "wkt": 5.50},
    ("Yorker", "Back-foot"): {"bat": 0.65, "wkt": 3.00},
    ("Yorker", "Front-foot"): {"bat": 1.15, "wkt": 0.35},
    # ── Bouncer ─────────────────────────────────────────────────────────────
    ("Bouncer", "Drive"): {"bat": 0.60, "wkt": 2.50},
    ("Bouncer", "Pull"): {"bat": 1.30, "wkt": 0.50},
    ("Bouncer", "Cut"): {"bat": 1.10, "wkt": 1.20},
    ("Bouncer", "Sweep"): {"bat": 0.40, "wkt": 5.00},
    ("Bouncer", "Lofted"): {"bat": 0.80, "wkt": 2.00},
    ("Bouncer", "Flick"): {"bat": 0.70, "wkt": 3.00},
    ("Bouncer", "Defend"): {"bat": 0.90, "wkt": 1.80},
    ("Bouncer", "Reverse-Sweep"): {"bat": 0.30, "wkt": 6.00},
    ("Bouncer", "Back-foot"): {"bat": 1.00, "wkt": 1.00},
    ("Bouncer", "Front-foot"): {"bat": 0.50, "wkt": 4.00},
    # ── Good Length ─────────────────────────────────────────────────────────
    ("Good Length", "Drive"): {"bat": 1.10, "wkt": 0.70},
    ("Good Length", "Pull"): {"bat": 0.85, "wkt": 1.50},
    ("Good Length", "Cut"): {"bat": 1.15, "wkt": 0.80},
    ("Good Length", "Sweep"): {"bat": 0.90, "wkt": 1.80},
    ("Good Length", "Lofted"): {"bat": 0.75, "wkt": 2.20},
    ("Good Length", "Flick"): {"bat": 1.00, "wkt": 1.00},
    ("Good Length", "Defend"): {"bat": 1.05, "wkt": 0.50},
    ("Good Length", "Reverse-Sweep"): {"bat": 0.70, "wkt": 2.50},
    ("Good Length", "Back-foot"): {"bat": 1.05, "wkt": 0.80},
    ("Good Length", "Front-foot"): {"bat": 0.95, "wkt": 1.20},
    # ── Full / Full Length ───────────────────────────────────────────────────
    ("Full", "Drive"): {"bat": 1.25, "wkt": 0.30},
    ("Full", "Pull"): {"bat": 0.70, "wkt": 2.80},
    ("Full", "Cut"): {"bat": 0.75, "wkt": 2.50},
    ("Full", "Sweep"): {"bat": 1.20, "wkt": 0.60},
    ("Full", "Lofted"): {"bat": 1.15, "wkt": 0.80},
    ("Full", "Flick"): {"bat": 1.30, "wkt": 0.25},
    ("Full", "Defend"): {"bat": 1.00, "wkt": 0.40},
    ("Full", "Reverse-Sweep"): {"bat": 0.80, "wkt": 2.00},
    ("Full", "Back-foot"): {"bat": 0.80, "wkt": 1.80},
    ("Full", "Front-foot"): {"bat": 1.20, "wkt": 0.35},
    ("Full Length", "Drive"): {"bat": 1.25, "wkt": 0.30},
    ("Full Length", "Pull"): {"bat": 0.70, "wkt": 2.80},
    ("Full Length", "Cut"): {"bat": 0.75, "wkt": 2.50},
    ("Full Length", "Sweep"): {"bat": 1.20, "wkt": 0.60},
    ("Full Length", "Lofted"): {"bat": 1.15, "wkt": 0.80},
    ("Full Length", "Flick"): {"bat": 1.30, "wkt": 0.25},
    ("Full Length", "Defend"): {"bat": 1.00, "wkt": 0.40},
    ("Full Length", "Reverse-Sweep"): {"bat": 0.80, "wkt": 2.00},
    ("Full Length", "Back-foot"): {"bat": 0.80, "wkt": 1.80},
    ("Full Length", "Front-foot"): {"bat": 1.20, "wkt": 0.35},
    # ── Short / Short Ball ───────────────────────────────────────────────────
    ("Short", "Drive"): {"bat": 0.60, "wkt": 2.80},
    ("Short", "Pull"): {"bat": 1.35, "wkt": 0.40},
    ("Short", "Cut"): {"bat": 1.25, "wkt": 0.60},
    ("Short", "Sweep"): {"bat": 0.35, "wkt": 6.00},
    ("Short", "Lofted"): {"bat": 0.90, "wkt": 1.80},
    ("Short", "Flick"): {"bat": 0.75, "wkt": 2.50},
    ("Short", "Defend"): {"bat": 0.85, "wkt": 1.50},
    ("Short", "Reverse-Sweep"): {"bat": 0.30, "wkt": 6.50},
    ("Short", "Back-foot"): {"bat": 1.10, "wkt": 0.90},
    ("Short", "Front-foot"): {"bat": 0.55, "wkt": 3.50},
    ("Short Ball", "Drive"): {"bat": 0.60, "wkt": 2.80},
    ("Short Ball", "Pull"): {"bat": 1.35, "wkt": 0.40},
    ("Short Ball", "Cut"): {"bat": 1.25, "wkt": 0.60},
    ("Short Ball", "Sweep"): {"bat": 0.35, "wkt": 6.00},
    ("Short Ball", "Lofted"): {"bat": 0.90, "wkt": 1.80},
    ("Short Ball", "Flick"): {"bat": 0.75, "wkt": 2.50},
    ("Short Ball", "Defend"): {"bat": 0.85, "wkt": 1.50},
    ("Short Ball", "Reverse-Sweep"): {"bat": 0.30, "wkt": 6.50},
    ("Short Ball", "Back-foot"): {"bat": 1.10, "wkt": 0.90},
    ("Short Ball", "Front-foot"): {"bat": 0.55, "wkt": 3.50},
    # ── Slot ────────────────────────────────────────────────────────────────
    ("Slot", "Drive"): {"bat": 1.30, "wkt": 0.25},
    ("Slot", "Pull"): {"bat": 1.20, "wkt": 0.60},
    ("Slot", "Cut"): {"bat": 1.10, "wkt": 0.80},
    ("Slot", "Sweep"): {"bat": 0.95, "wkt": 1.50},
    ("Slot", "Lofted"): {"bat": 1.25, "wkt": 0.70},
    ("Slot", "Flick"): {"bat": 1.15, "wkt": 0.50},
    ("Slot", "Defend"): {"bat": 0.90, "wkt": 1.00},
    ("Slot", "Reverse-Sweep"): {"bat": 0.85, "wkt": 1.80},
    ("Slot", "Back-foot"): {"bat": 1.05, "wkt": 0.70},
    ("Slot", "Front-foot"): {"bat": 1.10, "wkt": 0.60},
    # ── Spin deliveries ─────────────────────────────────────────────────────
    ("Off Break", "Drive"): {"bat": 1.00, "wkt": 1.00},
    ("Off Break", "Pull"): {"bat": 0.70, "wkt": 2.50},
    ("Off Break", "Cut"): {"bat": 1.05, "wkt": 0.90},
    ("Off Break", "Sweep"): {"bat": 1.15, "wkt": 0.60},
    ("Off Break", "Lofted"): {"bat": 0.80, "wkt": 2.00},
    ("Off Break", "Flick"): {"bat": 1.10, "wkt": 0.70},
    ("Off Break", "Defend"): {"bat": 1.05, "wkt": 0.50},
    ("Off Break", "Reverse-Sweep"): {"bat": 0.75, "wkt": 2.20},
    ("Off Break", "Back-foot"): {"bat": 0.95, "wkt": 1.10},
    ("Off Break", "Front-foot"): {"bat": 1.00, "wkt": 0.90},
    ("Doosra", "Drive"): {"bat": 0.85, "wkt": 1.80},
    ("Doosra", "Pull"): {"bat": 0.65, "wkt": 3.00},
    ("Doosra", "Cut"): {"bat": 1.30, "wkt": 0.40},
    ("Doosra", "Sweep"): {"bat": 0.90, "wkt": 1.60},
    ("Doosra", "Lofted"): {"bat": 0.70, "wkt": 2.50},
    ("Doosra", "Flick"): {"bat": 0.80, "wkt": 2.00},
    ("Doosra", "Defend"): {"bat": 0.95, "wkt": 1.20},
    ("Doosra", "Reverse-Sweep"): {"bat": 0.60, "wkt": 3.50},
    ("Doosra", "Back-foot"): {"bat": 1.20, "wkt": 0.60},
    ("Doosra", "Front-foot"): {"bat": 0.75, "wkt": 2.20},
    ("Carrom Ball", "Drive"): {"bat": 0.80, "wkt": 2.00},
    ("Carrom Ball", "Pull"): {"bat": 0.60, "wkt": 3.20},
    ("Carrom Ball", "Cut"): {"bat": 1.25, "wkt": 0.50},
    ("Carrom Ball", "Sweep"): {"bat": 0.85, "wkt": 1.80},
    ("Carrom Ball", "Lofted"): {"bat": 0.65, "wkt": 2.80},
    ("Carrom Ball", "Flick"): {"bat": 0.75, "wkt": 2.20},
    ("Carrom Ball", "Defend"): {"bat": 0.90, "wkt": 1.40},
    ("Carrom Ball", "Reverse-Sweep"): {"bat": 0.55, "wkt": 4.00},
    ("Carrom Ball", "Back-foot"): {"bat": 1.15, "wkt": 0.70},
    ("Carrom Ball", "Front-foot"): {"bat": 0.70, "wkt": 2.50},
    ("Arm Ball", "Drive"): {"bat": 0.90, "wkt": 1.40},
    ("Arm Ball", "Pull"): {"bat": 0.65, "wkt": 3.00},
    ("Arm Ball", "Cut"): {"bat": 1.10, "wkt": 0.80},
    ("Arm Ball", "Sweep"): {"bat": 1.00, "wkt": 1.00},
    ("Arm Ball", "Lofted"): {"bat": 0.75, "wkt": 2.20},
    ("Arm Ball", "Flick"): {"bat": 0.95, "wkt": 1.20},
    ("Arm Ball", "Defend"): {"bat": 1.00, "wkt": 0.80},
    ("Arm Ball", "Reverse-Sweep"): {"bat": 0.70, "wkt": 2.50},
    ("Arm Ball", "Back-foot"): {"bat": 1.05, "wkt": 0.90},
    ("Arm Ball", "Front-foot"): {"bat": 0.80, "wkt": 1.80},
    ("Top Spin", "Drive"): {"bat": 0.95, "wkt": 1.20},
    ("Top Spin", "Pull"): {"bat": 0.70, "wkt": 2.80},
    ("Top Spin", "Cut"): {"bat": 1.00, "wkt": 1.00},
    ("Top Spin", "Sweep"): {"bat": 1.10, "wkt": 0.70},
    ("Top Spin", "Lofted"): {"bat": 0.80, "wkt": 2.00},
    ("Top Spin", "Flick"): {"bat": 0.90, "wkt": 1.40},
    ("Top Spin", "Defend"): {"bat": 1.05, "wkt": 0.60},
    ("Top Spin", "Reverse-Sweep"): {"bat": 0.65, "wkt": 3.00},
    ("Top Spin", "Back-foot"): {"bat": 1.00, "wkt": 1.00},
    ("Top Spin", "Front-foot"): {"bat": 0.85, "wkt": 1.60},
    ("Drift Ball", "Drive"): {"bat": 0.85, "wkt": 1.80},
    ("Drift Ball", "Pull"): {"bat": 0.60, "wkt": 3.50},
    ("Drift Ball", "Cut"): {"bat": 1.15, "wkt": 0.70},
    ("Drift Ball", "Sweep"): {"bat": 1.05, "wkt": 0.90},
    ("Drift Ball", "Lofted"): {"bat": 0.70, "wkt": 2.50},
    ("Drift Ball", "Flick"): {"bat": 0.80, "wkt": 2.00},
    ("Drift Ball", "Defend"): {"bat": 0.95, "wkt": 1.20},
    ("Drift Ball", "Reverse-Sweep"): {"bat": 0.60, "wkt": 3.20},
    ("Drift Ball", "Back-foot"): {"bat": 1.10, "wkt": 0.80},
    ("Drift Ball", "Front-foot"): {"bat": 0.75, "wkt": 2.20},
    ("Leg Break", "Drive"): {"bat": 1.00, "wkt": 1.00},
    ("Leg Break", "Pull"): {"bat": 0.80, "wkt": 2.00},
    ("Leg Break", "Cut"): {"bat": 1.20, "wkt": 0.60},
    ("Leg Break", "Sweep"): {"bat": 1.10, "wkt": 0.80},
    ("Leg Break", "Lofted"): {"bat": 0.85, "wkt": 1.80},
    ("Leg Break", "Flick"): {"bat": 1.05, "wkt": 0.90},
    ("Leg Break", "Defend"): {"bat": 1.00, "wkt": 0.70},
    ("Leg Break", "Reverse-Sweep"): {"bat": 0.75, "wkt": 2.20},
    ("Leg Break", "Back-foot"): {"bat": 1.10, "wkt": 0.70},
    ("Leg Break", "Front-foot"): {"bat": 0.90, "wkt": 1.30},
    ("Googly", "Drive"): {"bat": 1.00, "wkt": 1.00},
    ("Googly", "Pull"): {"bat": 0.65, "wkt": 3.00},
    ("Googly", "Cut"): {"bat": 0.75, "wkt": 2.40},
    ("Googly", "Sweep"): {"bat": 1.15, "wkt": 0.65},
    ("Googly", "Lofted"): {"bat": 0.70, "wkt": 2.50},
    ("Googly", "Flick"): {"bat": 0.75, "wkt": 2.20},
    ("Googly", "Defend"): {"bat": 0.90, "wkt": 1.40},
    ("Googly", "Reverse-Sweep"): {"bat": 0.60, "wkt": 3.50},
    ("Googly", "Back-foot"): {"bat": 1.20, "wkt": 0.60},
    ("Googly", "Front-foot"): {"bat": 0.70, "wkt": 2.50},
    ("Flipper", "Drive"): {"bat": 0.90, "wkt": 1.40},
    ("Flipper", "Pull"): {"bat": 0.70, "wkt": 2.80},
    ("Flipper", "Cut"): {"bat": 0.70, "wkt": 2.60},
    ("Flipper", "Sweep"): {"bat": 0.95, "wkt": 1.20},
    ("Flipper", "Lofted"): {"bat": 0.75, "wkt": 2.20},
    ("Flipper", "Flick"): {"bat": 0.85, "wkt": 1.60},
    ("Flipper", "Defend"): {"bat": 1.05, "wkt": 0.55},
    ("Flipper", "Reverse-Sweep"): {"bat": 0.65, "wkt": 3.00},
    ("Flipper", "Back-foot"): {"bat": 0.90, "wkt": 1.30},
    ("Flipper", "Front-foot"): {"bat": 0.80, "wkt": 1.80},
    ("Top Spinner", "Drive"): {"bat": 0.95, "wkt": 1.20},
    ("Top Spinner", "Pull"): {"bat": 0.75, "wkt": 2.50},
    ("Top Spinner", "Cut"): {"bat": 0.85, "wkt": 1.60},
    ("Top Spinner", "Sweep"): {"bat": 1.00, "wkt": 1.00},
    ("Top Spinner", "Lofted"): {"bat": 0.80, "wkt": 2.00},
    ("Top Spinner", "Flick"): {"bat": 0.90, "wkt": 1.40},
    ("Top Spinner", "Defend"): {"bat": 1.05, "wkt": 0.55},
    ("Top Spinner", "Reverse-Sweep"): {"bat": 0.70, "wkt": 2.50},
    ("Top Spinner", "Back-foot"): {"bat": 1.05, "wkt": 0.80},
    ("Top Spinner", "Front-foot"): {"bat": 0.85, "wkt": 1.50},
    ("Slider", "Drive"): {"bat": 0.85, "wkt": 1.80},
    ("Slider", "Pull"): {"bat": 0.60, "wkt": 3.50},
    ("Slider", "Cut"): {"bat": 1.20, "wkt": 0.60},
    ("Slider", "Sweep"): {"bat": 0.90, "wkt": 1.40},
    ("Slider", "Lofted"): {"bat": 0.70, "wkt": 2.50},
    ("Slider", "Flick"): {"bat": 0.80, "wkt": 2.00},
    ("Slider", "Defend"): {"bat": 0.95, "wkt": 1.20},
    ("Slider", "Reverse-Sweep"): {"bat": 0.55, "wkt": 4.00},
    ("Slider", "Back-foot"): {"bat": 1.15, "wkt": 0.70},
    ("Slider", "Front-foot"): {"bat": 0.70, "wkt": 2.20},
    # ── Orthodox ─────────────────────────────────────────────────────────────
    ("Stock", "Drive"): {"bat": 1.00, "wkt": 1.00},
    ("Stock", "Pull"): {"bat": 0.75, "wkt": 2.20},
    ("Stock", "Cut"): {"bat": 1.05, "wkt": 0.90},
    ("Stock", "Sweep"): {"bat": 1.00, "wkt": 1.00},
    ("Stock", "Lofted"): {"bat": 0.80, "wkt": 1.80},
    ("Stock", "Flick"): {"bat": 1.05, "wkt": 0.90},
    ("Stock", "Defend"): {"bat": 1.10, "wkt": 0.50},
    ("Stock", "Reverse-Sweep"): {"bat": 0.70, "wkt": 2.50},
    ("Stock", "Back-foot"): {"bat": 1.00, "wkt": 1.00},
    ("Stock", "Front-foot"): {"bat": 0.90, "wkt": 1.30},
    ("Topspinner", "Drive"): {"bat": 0.95, "wkt": 1.20},
    ("Topspinner", "Pull"): {"bat": 0.70, "wkt": 2.80},
    ("Topspinner", "Cut"): {"bat": 0.85, "wkt": 1.60},
    ("Topspinner", "Sweep"): {"bat": 1.00, "wkt": 1.00},
    ("Topspinner", "Lofted"): {"bat": 0.80, "wkt": 2.00},
    ("Topspinner", "Flick"): {"bat": 0.90, "wkt": 1.40},
    ("Topspinner", "Defend"): {"bat": 1.05, "wkt": 0.55},
    ("Topspinner", "Reverse-Sweep"): {"bat": 0.65, "wkt": 3.00},
    ("Topspinner", "Back-foot"): {"bat": 1.05, "wkt": 0.80},
    ("Topspinner", "Front-foot"): {"bat": 0.85, "wkt": 1.50},
    ("Undercutter", "Drive"): {"bat": 0.90, "wkt": 1.40},
    ("Undercutter", "Pull"): {"bat": 0.65, "wkt": 3.00},
    ("Undercutter", "Cut"): {"bat": 1.15, "wkt": 0.70},
    ("Undercutter", "Sweep"): {"bat": 0.95, "wkt": 1.20},
    ("Undercutter", "Lofted"): {"bat": 0.75, "wkt": 2.20},
    ("Undercutter", "Flick"): {"bat": 0.85, "wkt": 1.60},
    ("Undercutter", "Defend"): {"bat": 1.00, "wkt": 0.80},
    ("Undercutter", "Reverse-Sweep"): {"bat": 0.60, "wkt": 3.20},
    ("Undercutter", "Back-foot"): {"bat": 1.10, "wkt": 0.70},
    ("Undercutter", "Front-foot"): {"bat": 0.80, "wkt": 1.80},
    # ── Fast-Medium Swing ────────────────────────────────────────────────────
    ("Outswinger", "Drive"): {"bat": 0.90, "wkt": 1.40},
    ("Outswinger", "Pull"): {"bat": 0.75, "wkt": 2.20},
    ("Outswinger", "Cut"): {"bat": 1.20, "wkt": 0.60},
    ("Outswinger", "Sweep"): {"bat": 0.85, "wkt": 1.80},
    ("Outswinger", "Lofted"): {"bat": 0.70, "wkt": 2.50},
    ("Outswinger", "Flick"): {"bat": 0.95, "wkt": 1.20},
    ("Outswinger", "Defend"): {"bat": 1.00, "wkt": 0.90},
    ("Outswinger", "Reverse-Sweep"): {"bat": 0.65, "wkt": 2.80},
    ("Outswinger", "Back-foot"): {"bat": 1.05, "wkt": 0.80},
    ("Outswinger", "Front-foot"): {"bat": 0.90, "wkt": 1.40},
    ("Inswinger", "Drive"): {"bat": 0.85, "wkt": 1.80},
    ("Inswinger", "Pull"): {"bat": 0.70, "wkt": 2.50},
    ("Inswinger", "Cut"): {"bat": 0.75, "wkt": 2.20},
    ("Inswinger", "Sweep"): {"bat": 0.90, "wkt": 1.60},
    ("Inswinger", "Lofted"): {"bat": 0.75, "wkt": 2.20},
    ("Inswinger", "Flick"): {"bat": 1.15, "wkt": 0.55},
    ("Inswinger", "Defend"): {"bat": 0.95, "wkt": 1.20},
    ("Inswinger", "Reverse-Sweep"): {"bat": 0.60, "wkt": 3.00},
    ("Inswinger", "Back-foot"): {"bat": 1.00, "wkt": 1.10},
    ("Inswinger", "Front-foot"): {"bat": 0.85, "wkt": 1.80},
    ("Reverse Swing", "Drive"): {"bat": 0.90, "wkt": 1.40},
    ("Reverse Swing", "Pull"): {"bat": 0.75, "wkt": 2.20},
    ("Reverse Swing", "Cut"): {"bat": 1.15, "wkt": 0.80},
    ("Reverse Swing", "Sweep"): {"bat": 0.85, "wkt": 1.80},
    ("Reverse Swing", "Lofted"): {"bat": 0.70, "wkt": 2.50},
    ("Reverse Swing", "Flick"): {"bat": 0.95, "wkt": 1.20},
    ("Reverse Swing", "Defend"): {"bat": 1.00, "wkt": 0.90},
    ("Reverse Swing", "Reverse-Sweep"): {"bat": 0.65, "wkt": 2.80},
    ("Reverse Swing", "Back-foot"): {"bat": 1.05, "wkt": 0.80},
    ("Reverse Swing", "Front-foot"): {"bat": 0.90, "wkt": 1.40},
    ("Slower Ball", "Drive"): {"bat": 0.80, "wkt": 1.60},
    ("Slower Ball", "Pull"): {"bat": 0.65, "wkt": 2.80},
    ("Slower Ball", "Cut"): {"bat": 1.00, "wkt": 1.20},
    ("Slower Ball", "Sweep"): {"bat": 0.90, "wkt": 1.60},
    ("Slower Ball", "Lofted"): {"bat": 0.75, "wkt": 2.20},
    ("Slower Ball", "Flick"): {"bat": 0.85, "wkt": 1.40},
    ("Slower Ball", "Defend"): {"bat": 0.95, "wkt": 1.00},
    ("Slower Ball", "Reverse-Sweep"): {"bat": 0.60, "wkt": 3.00},
    ("Slower Ball", "Back-foot"): {"bat": 0.90, "wkt": 1.20},
    ("Slower Ball", "Front-foot"): {"bat": 0.85, "wkt": 1.50},
    ("Off Cutter", "Drive"): {"bat": 0.90, "wkt": 1.20},
    ("Off Cutter", "Pull"): {"bat": 0.75, "wkt": 2.00},
    ("Off Cutter", "Cut"): {"bat": 1.10, "wkt": 0.80},
    ("Off Cutter", "Sweep"): {"bat": 0.85, "wkt": 1.60},
    ("Off Cutter", "Lofted"): {"bat": 0.70, "wkt": 2.20},
    ("Off Cutter", "Flick"): {"bat": 0.95, "wkt": 1.00},
    ("Off Cutter", "Defend"): {"bat": 1.00, "wkt": 0.70},
    ("Off Cutter", "Reverse-Sweep"): {"bat": 0.65, "wkt": 2.50},
    ("Off Cutter", "Back-foot"): {"bat": 1.00, "wkt": 0.90},
    ("Off Cutter", "Front-foot"): {"bat": 0.90, "wkt": 1.20},
    ("Leg Cutter", "Drive"): {"bat": 0.90, "wkt": 1.20},
    ("Leg Cutter", "Pull"): {"bat": 0.75, "wkt": 2.00},
    ("Leg Cutter", "Cut"): {"bat": 1.10, "wkt": 0.80},
    ("Leg Cutter", "Sweep"): {"bat": 0.85, "wkt": 1.60},
    ("Leg Cutter", "Lofted"): {"bat": 0.70, "wkt": 2.20},
    ("Leg Cutter", "Flick"): {"bat": 0.95, "wkt": 1.00},
    ("Leg Cutter", "Defend"): {"bat": 1.00, "wkt": 0.70},
    ("Leg Cutter", "Reverse-Sweep"): {"bat": 0.65, "wkt": 2.50},
    ("Leg Cutter", "Back-foot"): {"bat": 1.00, "wkt": 0.90},
    ("Leg Cutter", "Front-foot"): {"bat": 0.90, "wkt": 1.20},
    ("Knuckle", "Drive"): {"bat": 0.80, "wkt": 1.00},
    ("Knuckle", "Pull"): {"bat": 0.65, "wkt": 2.00},
    ("Knuckle", "Cut"): {"bat": 1.00, "wkt": 1.00},
    ("Knuckle", "Sweep"): {"bat": 0.85, "wkt": 1.40},
    ("Knuckle", "Lofted"): {"bat": 0.70, "wkt": 1.80},
    ("Knuckle", "Flick"): {"bat": 0.85, "wkt": 1.20},
    ("Knuckle", "Defend"): {"bat": 0.95, "wkt": 0.80},
    ("Knuckle", "Reverse-Sweep"): {"bat": 0.60, "wkt": 2.50},
    ("Knuckle", "Back-foot"): {"bat": 0.90, "wkt": 1.00},
    ("Knuckle", "Front-foot"): {"bat": 0.85, "wkt": 1.20},
}


def delivery_speed(delivery: str, speed_mod: float = 0.0) -> float:
    base_range = BALL_SPEEDS.get(delivery, (130.0, 140.0))
    return round(random.uniform(*base_range) + speed_mod, 1)


def apply_momentum(outcome_dict: dict, game=None, striker=None, delivery=None) -> dict:
    return outcome_dict


def build_payload(
    outcome_str,
    runs_batter,
    runs_extras,
    is_legal,
    extra_type,
    dismissal_type,
    timing_label,
    overthrows,
    umpire_given_out,
    actual_is_out,
    drs_eligible_wicket,
    review_reason,
    free_hit_saved,
    is_actual_nb,
    umpire_called_nb,
    drs_eligible_nb,
    nb_review_reason,
    is_direct_hit=False,
    fielder_name=None,
):
    return {
        "outcome_str": str(outcome_str),
        "runs_batter": runs_batter,
        "runs_extras": runs_extras,
        "is_legal": is_legal,
        "extra_type": extra_type,
        "dismissal_type": dismissal_type,
        "timing_label": timing_label,
        "overthrows": overthrows,
        "umpire_given_out": umpire_given_out,
        "actual_is_out": actual_is_out,
        "drs_eligible_wicket": drs_eligible_wicket,
        "review_reason": review_reason,
        "free_hit_saved": free_hit_saved,
        "is_actual_nb": is_actual_nb,
        "umpire_called_nb": umpire_called_nb,
        "drs_eligible_nb": drs_eligible_nb,
        "nb_review_reason": nb_review_reason,
        "is_direct_hit": is_direct_hit,
        "fielder_name": fielder_name,
    }


def calculate_delta(
    bat_ovr: int, bowl_ovr: int, bat_rarity: float = 1.0, bowl_rarity: float = 1.0
) -> float:
    """
    Pure OVR delta. Rarity boost is applied separately at 10% in _build_run_weights.
    bat_ovr and bowl_ovr here are already the role-resolved effective values
    (match_bat for batter, match_bowl for bowler).
    """
    return bat_ovr - bowl_ovr


def _build_run_weights(
    timing_weight: float,
    bat_ovr: int = 0,
    bowl_ovr: int = 0,
    bat_rarity: float = 1.0,
    bowl_rarity: float = 1.0,
    bat_style: str = "balanced",
    bat_style_mod: float = 1.0,
    wkt_mult: float = 1.0,
    batter_runs: int = 0,
    memories_type: str | None = None,
) -> tuple[list[float], float]:
    """
    Build run/boundary/wicket weights using the rarity-adjusted delta system.
    bat_ovr and bowl_ovr must be the already role-resolved effective values —
    do NOT pass raw card OVR here.
    """
    delta = calculate_delta(bat_ovr, bowl_ovr, bat_rarity, bowl_rarity)

    wicket = 30.0
    single = 400.0
    aggressive_2 = 160.0
    aggressive_3 = 160.0
    boundary_4 = 140.0
    boundary_6 = 140.0
    dot_ball = 160.0

    if delta > 0:
        shift = int(delta)
        boundary_4 += shift * 2.0
        boundary_6 += shift * 2.0
        dot_ball -= shift * 3.0
        wicket -= shift * 1.0
    elif delta < 0:
        shift = int(abs(delta))
        aggressive_2 -= shift * 1.0
        aggressive_3 -= shift * 1.0
        boundary_4 -= shift * 1.0
        boundary_6 -= shift * 1.0
        single += shift * 1.5
        dot_ball += shift * 0.5
        wicket += shift * 2.0

    total_boundary = boundary_4 + boundary_6
    if total_boundary > 500.0:
        excess = total_boundary - 500.0
        boundary_4 = max(20.0, boundary_4 - excess / 2)
        boundary_6 = max(20.0, boundary_6 - excess / 2)
    wicket = max(5.0, min(200.0, wicket))
    dot_ball = max(0.0, dot_ball)

    timing_factor = timing_weight - 0.85
    if timing_factor > 0:
        boundary_4 += timing_factor * 25.0
        boundary_6 += timing_factor * 18.0
        wicket -= timing_factor * 15.0
    else:
        wicket -= timing_factor * 20.0
        aggressive_2 -= timing_factor * 10.0
        dot_ball -= timing_factor * 15.0

    _BAT_STYLE_BOUNDARY = {
        "aggressive": 3,
        "defensive": 0.90,
        "radical": 1.10,
        "balanced": 1.00,
        "vivstruction": 1.05,
        "none": 1.00,
        "": 1.00,
    }
    bat_boundary_mod = _BAT_STYLE_BOUNDARY.get(bat_style, 1.0)
    wb = bat_boundary_mod - 1.0
    boundary_4 += wb * 8.0
    boundary_6 += wb * 6.0
    single -= wb * 6.0
    aggressive_2 -= wb * 2.0

    if bat_style == "vivstruction":
        wicket *= 0.85

    if bat_style == "aggressive":
        wicket *= 1.15  # extra risk for aggressive intent

    if batter_runs >= 100:
        boundary_4 += 18.0
        boundary_6 += 15.0
        wicket -= 10.0
    elif batter_runs >= 50:
        boundary_4 += 8.0
        boundary_6 += 6.0
        wicket -= 5.0

    norm_bat_r = _normalize_rarity(bat_rarity)
    norm_bowl_r = _normalize_rarity(bowl_rarity)
    rarity_lead = (1.0 - norm_bat_r) - (1.0 - norm_bowl_r)
    rarity_factor = 0.1 * rarity_lead
    if rarity_factor:
        boundary_4 *= 1.0 + rarity_factor
        boundary_6 *= 1.0 + rarity_factor
        single *= 1.0 + rarity_factor
        dot_ball *= 1.0 - rarity_factor
        wicket *= 1.0 - rarity_factor

    wicket = wicket * wkt_mult

    if memories_type == "gayle":
        boundary_6 *= 1.15
        boundary_4 *= 1.10
        wicket *= 1.20
    elif memories_type == "mccullum":
        single *= 1.20
        aggressive_2 *= 1.15
        aggressive_3 *= 1.15
        boundary_6 *= 1.10
        if bowl_ovr >= 70:
            boundary_4 *= 1.25
            boundary_6 *= 1.25
            wicket *= 1.15

    w = [wicket, single, aggressive_2, aggressive_3, boundary_4, boundary_6, dot_ball]

    return [max(0.1, x * bat_style_mod) for x in w], wicket


def _resolve_role_effectives(
    bat_ovr: int,
    bowl_ovr: int,
    striker_role: str,
    bowler_role: str,
) -> tuple[float, float]:
    """
    Resolves match_bat and match_bowl into effective values based on each
    player's match_role.

    Rules:
      BAT  — full bat_ovr for batting; bowl_ovr raw (no boost) if they bowl
      BOWL — full bowl_ovr for bowling; bat_ovr raw (no boost) when batting
      AR   — both at 0.93 discount
      WK   — bat_ovr at 0.88 discount; bowl_ovr always 0 (keepers cannot bowl)

    Parameters
    ----------
    bat_ovr      : striker's match_bat value
    bowl_ovr     : bowler's match_bowl value
    striker_role : striker's match_role  (Bat / Bowl / AR / WK)
    bowler_role  : bowler's match_role   (Bat / Bowl / AR / WK)
    """

    # Normalise role strings
    def _norm(r: str) -> str:
        r = (r or "BAT").upper().replace("-", "").replace(" ", "")
        if r in ("AR", "ALLROUNDER"):
            return "AR"
        return r

    sr = _norm(striker_role)
    br = _norm(bowler_role)

    # ── Batting effective (driven by match_bat) ──────────────────────────────
    if sr == "AR":
        bat_effective = bat_ovr * 0.93
    elif sr == "WK":
        bat_effective = bat_ovr * 0.88
    else:
        # BAT or BOWL — use raw match_bat, no role boost
        bat_effective = float(bat_ovr)

    # ── Bowling effective (driven by match_bowl) ─────────────────────────────
    if br == "WK":
        bowl_effective = 0.0
    elif br == "AR":
        bowl_effective = bowl_ovr * 0.93
    elif br == "BOWL":
        bowl_effective = bowl_ovr * 1.10
    else:
        bowl_effective = bowl_ovr * 0.80

    return bat_effective, bowl_effective


def calculate_outcome(
    delivery: str,
    shot: str,
    bowler_ovr: int,  # pass match_bowl (the bowler's bowling OVR)
    batsman_ovr: int,  # pass match_bat  (the batter's batting OVR)
    bat_rarity: float = 1.0,
    bowl_rarity: float = 1.0,
    bat_power: int = 0,  # deprecated — kept for API compat, no longer used
    bowl_power: int = 0,  # deprecated — kept for API compat, no longer used
    batter_runs: int = 0,
    MATCHUP_MATRIX: dict = None,
    bat_style: str = "balanced",
    striker_role: str = "Bat",
    bowler_role: str = "Bowl",
    memories_type: str | None = None,
    fielder_grade: int = 1,
    narrative_attrs: list[str] | None = None,
    free_hit: bool = False,
) -> dict:
    """
    Resolve a single delivery.

    IMPORTANT — callers must pass:
        batsman_ovr = striker.match_bat   (NOT striker.ovr)
        bowler_ovr  = bowler.match_bowl   (NOT bowler.ovr)
        striker_role = striker.match_role
        bowler_role  = bowler.match_role

    bat_power / bowl_power are no longer used; pass 0 or omit.
    """
    if MATCHUP_MATRIX is None:
        MATCHUP_MATRIX = globals().get("MATCHUP_MATRIX", {})

    # ── 1. Role-based effective values ───────────────────────────────────────
    # This is the single source of truth for how role affects performance.
    # bat_effective  → used as bat_ovr  in _build_run_weights
    # bowl_effective → used as bowl_ovr in _build_run_weights
    bat_effective, bowl_effective = _resolve_role_effectives(
        bat_ovr=batsman_ovr,
        bowl_ovr=bowler_ovr,
        striker_role=striker_role,
        bowler_role=bowler_role,
    )

    # ── 2. Bat style scoring modifier ────────────────────────────────────────
    _BAT_STYLE_MOD = {
        "aggressive": 1.15,
        "defensive": 1.00,
        "radical": 1.08,
        "balanced": 1.02,
        "vivstruction": 1.20,
        "none": 1.00,
        "": 1.00,
    }
    bat_style_mod = _BAT_STYLE_MOD.get(bat_style, 1.0)

    # ── 3. Timing roll ───────────────────────────────────────────────────────
    tw = max(0.1, min(1.25, random.gauss(0.8, 0.25)))
    if tw >= 1.05:
        timing_label = "Perfect"
    elif tw >= 0.85:
        timing_label = "Good"
    elif tw >= 0.60:
        timing_label = "Early" if random.random() > 0.5 else "Late"
    else:
        timing_label = "Mistimed"

    # ── 4. Matchup matrix ────────────────────────────────────────────────────
    m = MATCHUP_MATRIX.get((delivery, shot), {"bat": 1.0, "wkt": 1.0})
    base_mod, wkt_mult = m["bat"], m["wkt"]
    es_adjust = 0.0
    forced_dismissal = None
    review_reason = None

    # ── 5. No-ball / wide logic ───────────────────────────────────────────────
    is_actual_nb = random.random() < 0.005
    umpire_called_nb = (
        random.random() < 0.80 if is_actual_nb else random.random() < 0.002
    )
    nb_review_reason = "Front Foot"
    drs_eligible_nb = umpire_called_nb and random.random() < 0.001
    if delivery in ("Bouncer", "Fast"):
        nb_review_reason = random.choice(["Front Foot", "Double Bounce"])
    elif delivery in ("Yorker", "Full"):
        nb_review_reason = random.choice(["Front Foot", "Waist High Full Toss"])

    if not is_actual_nb and not umpire_called_nb and random.random() < (1 / 150):
        return build_payload(
            "Wd",
            0,
            1,
            False,
            "Wd",
            None,
            "Extra",
            0,
            False,
            None,
            False,
            None,
            False,
            None,
        )
    # ── 6. Delivery-specific wicket adjustments ───────────────────────────────
    # Real cricket probabilities:
    # Bowled      : Yorker/Full/Good Length > Bouncer > Spin
    # LBW         : Yorker/Full/Good Length > Spin > Bouncer/Short
    # Caught      : Edges (Pull/Cut/Drive/Sweep) > lofted > Defend
    # Stumped     : Spin/Drift/Arm Ball only, very low
    # Run Out     : Any shot, higher on multi-run attempts
    # Hit Wicket  : Very rare, aggressive shots
    _wkt_base = wkt_mult

    if delivery == "Yorker":
        if shot in ("Pull", "Sweep", "Reverse-Sweep", "Hook"):
            wkt_mult *= 3.5
            forced_dismissal = random.choices(["Bowled", "LBW"], weights=[65, 35])[0]
        elif shot in ("Drive", "Flick", "Defend"):
            wkt_mult *= 2.0
            forced_dismissal = random.choices(
                ["Bowled", "LBW", "Caught"], weights=[45, 35, 20]
            )[0]
        elif shot == "Lofted":
            wkt_mult *= 2.0
            forced_dismissal = random.choices(["Caught", "Bowled"], weights=[60, 40])[0]
    elif delivery == "Bouncer":
        if shot in ("Pull", "Hook", "Cut"):
            wkt_mult *= 2.0
            forced_dismissal = "Caught"
        elif shot in ("Drive", "Defend", "Flick"):
            wkt_mult *= 1.3
            forced_dismissal = random.choices(["Bowled", "Caught"], weights=[50, 50])[0]
        elif shot == "Lofted":
            wkt_mult *= 1.8
            forced_dismissal = "Caught"
    elif delivery == "Full":
        if shot in ("Drive", "Sweep", "Flick", "Front-foot"):
            wkt_mult *= 2.0
            forced_dismissal = random.choices(
                ["LBW", "Caught", "Bowled"], weights=[40, 35, 25]
            )[0]
        elif shot in ("Defend", "Back-foot"):
            wkt_mult *= 1.5
            forced_dismissal = random.choices(["Bowled", "LBW"], weights=[50, 50])[0]
        elif shot == "Lofted":
            wkt_mult *= 1.8
            forced_dismissal = "Caught"
    elif delivery == "Good Length":
        if shot in ("Drive", "Cut", "Flick", "Front-foot"):
            wkt_mult *= 2.0
            forced_dismissal = random.choices(
                ["LBW", "Caught", "Bowled"], weights=[35, 40, 25]
            )[0]
        elif shot in ("Defend", "Back-foot"):
            wkt_mult *= 1.5
            forced_dismissal = random.choices(["Bowled", "LBW"], weights=[45, 55])[0]
        elif shot == "Lofted":
            wkt_mult *= 1.8
            forced_dismissal = "Caught"
    elif delivery in ALL_SPIN:
        if shot in ("Sweep", "Reverse-Sweep"):
            wkt_mult *= 2.0
            forced_dismissal = random.choices(
                ["LBW", "Caught", "Bowled"], weights=[35, 45, 20]
            )[0]
        elif shot in ("Drive", "Cut", "Flick"):
            wkt_mult *= 1.6
            forced_dismissal = random.choices(
                ["LBW", "Caught", "Stumped"], weights=[30, 50, 20]
            )[0]
        elif shot in ("Defend", "Back-foot", "Front-foot"):
            wkt_mult *= 1.2
            forced_dismissal = random.choices(
                ["LBW", "Bowled", "Stumped"], weights=[40, 35, 25]
            )[0]
        elif shot == "Lofted":
            wkt_mult *= 2.0
            forced_dismissal = random.choices(["Caught", "Stumped"], weights=[70, 30])[
                0
            ]
    elif delivery in (
        "OutSwing",
        "InSwing",
        "Reverse Swing",
        "Outswinger",
        "Inswinger",
    ):
        if shot in ("Drive", "Cut", "Flick"):
            wkt_mult *= 2.0
            forced_dismissal = random.choices(
                ["Caught", "Bowled", "LBW"], weights=[45, 30, 25]
            )[0]
        elif shot in ("Defend", "Back-foot"):
            wkt_mult *= 1.5
            forced_dismissal = random.choices(["Bowled", "LBW"], weights=[40, 60])[0]
    elif delivery in ("Cutter", "Slow", "Off Cutter", "Leg Cutter"):
        if shot in ("Drive", "Cut", "Lofted"):
            wkt_mult *= 2.0
            forced_dismissal = "Caught"
        elif shot in ("Defend", "Back-foot", "Sweep"):
            wkt_mult *= 1.5
            forced_dismissal = random.choices(["LBW", "Bowled"], weights=[50, 50])[0]
    elif delivery in ("Slower Ball", "Knuckle"):
        if shot in ("Drive", "Cut", "Lofted"):
            wkt_mult *= 1.6
            forced_dismissal = random.choices(["Caught", "Bowled"], weights=[60, 40])[0]
        elif shot in ("Defend", "Back-foot", "Sweep"):
            wkt_mult *= 1.2
            forced_dismissal = random.choices(["LBW", "Bowled"], weights=[50, 50])[0]

    if batter_runs >= 50:
        wkt_mult *= 1.20
    if batter_runs >= 100:
        wkt_mult *= 1.10

    # ── 7. Shot-plane modifiers ───────────────────────────────────────────────
    bat_mod = 1.0
    if shot == "Defend":
        bat_mod = 0.90
        base_mod *= 0.95
        wkt_mult *= 0.60
    elif shot == "Back-foot":
        if delivery in ("Yorker", "Full", "Full Length"):
            bat_mod = 0.60
            base_mod *= 0.85
        elif delivery in ("Good Length",):
            bat_mod = 1.0
        elif delivery in ("Bouncer", "Short"):
            bat_mod = 0.70
            base_mod *= 0.75
        else:
            base_mod *= 0.90
    elif shot == "Front-foot":
        if delivery in ("Yorker", "Full", "Full Length"):
            bat_mod = 1.25
            base_mod *= 1.10
        elif delivery in ("Good Length",):
            bat_mod = 0.90
            base_mod *= 0.90
        elif delivery in ("Bouncer", "Short"):
            bat_mod = 0.65
            base_mod *= 0.70
        else:
            base_mod *= 0.80

    if batter_runs >= 50:
        wkt_mult *= 1.30

    # ── 8. Build run weights using ROLE-RESOLVED effective values ─────────────
    # bat_effective  = role-adjusted match_bat
    # bowl_effective = role-adjusted match_bowl  (0 for WK, raw for BAT role)
    # This is the critical fix: _build_run_weights sees the correct numbers so
    # a BAT-role bowler with match_bowl=28 genuinely bowls at 28, not at their
    # card OVR or any boosted value.
    run_weights, _ = _build_run_weights(
        tw,
        bat_ovr=int(bat_effective),
        bowl_ovr=int(bowl_effective),
        bat_rarity=bat_rarity,
        bowl_rarity=bowl_rarity,
        bat_style=bat_style,
        bat_style_mod=bat_style_mod,
        wkt_mult=wkt_mult,
        batter_runs=batter_runs,
        memories_type=memories_type,
    )

    outcomes = ["W", "1", "2", "3", "4", "6", "0"]

    # Defend shots cannot hit boundaries
    if shot == "Defend":
        run_weights = list(run_weights)
        run_weights[4] = 0.0  # no boundary 4
        run_weights[5] = 0.0  # no boundary 6
        run_weights[0] *= 0.05  # significantly reduce wicket chance on defend

    if memories_type == "mccullum":
        if delivery in ("Fast", "Bouncer", "Yorker", "Full"):
            run_weights = list(run_weights)
            run_weights[4] *= 1.30
            run_weights[5] *= 1.25
            run_weights[1] *= 1.10
        elif delivery in ALL_SPIN:
            if shot == "Defend":
                run_weights = list(run_weights)
                run_weights[4] *= 1.20
                run_weights[5] *= 1.20
                run_weights[1] *= 0.80

    # ── Narrative attribute effects ────────────────────────────────────────────
    _narrative_bat_effects = {}
    _narrative_bowl_effects = {}
    _narrative_field_effects = {}
    if narrative_attrs:
        try:
            from .engine import NARRATIVE_ATTRIBUTES

            for attr in narrative_attrs:
                attr_data = NARRATIVE_ATTRIBUTES.get(attr, {})
                _narrative_bat_effects.setdefault("six_mult", 1.0)
                _narrative_bat_effects.setdefault("four_mult", 1.0)
                _narrative_bat_effects.setdefault("wicket_risk", 1.0)
                _narrative_bat_effects.setdefault("death_over_bonus", 1.0)
                _narrative_bat_effects.setdefault("powerplay_boundary_mult", 1.0)
                _narrative_bowl_effects.setdefault("bouncer_wicket_mult", 1.0)
                _narrative_bowl_effects.setdefault("yorker_wicket_mult", 1.0)
                _narrative_bowl_effects.setdefault("spin_wicket_mult", 1.0)
                _narrative_bowl_effects.setdefault("swing_wicket_mult", 1.0)
                _narrative_bowl_effects.setdefault("leg_spin_wicket_mult", 1.0)
                _narrative_bowl_effects.setdefault("googly_effectiveness", 1.0)
                _narrative_bowl_effects.setdefault("variation_bonus", 1.0)
                _narrative_bowl_effects.setdefault("yorker_death_mult", 1.0)
                _narrative_field_effects.setdefault("catch_mult", 1.0)
                _narrative_field_effects.setdefault("stumping_chance", 1.0)
                _narrative_field_effects.setdefault("run_out_chance", 1.0)
                _narrative_field_effects.setdefault("direct_hit_chance", 1.0)

                bat_effects = attr_data.get("bat_effects", {})
                if bat_effects:
                    for k in (
                        "six_mult",
                        "four_mult",
                        "wicket_risk",
                        "death_over_bonus",
                        "powerplay_boundary_mult",
                    ):
                        if k in bat_effects:
                            _narrative_bat_effects[k] *= bat_effects[k]

                bowl_effects = attr_data.get("bowl_effects", {})
                if bowl_effects:
                    for k in (
                        "bouncer_wicket_mult",
                        "yorker_wicket_mult",
                        "spin_wicket_mult",
                        "swing_wicket_mult",
                        "leg_spin_wicket_mult",
                        "googly_effectiveness",
                        "variation_bonus",
                        "yorker_death_mult",
                    ):
                        if k in bowl_effects:
                            _narrative_bowl_effects[k] *= bowl_effects[k]

                field_effects = attr_data.get("field_effects", {})
                if field_effects:
                    for k in (
                        "catch_mult",
                        "stumping_chance",
                        "run_out_chance",
                        "direct_hit_chance",
                    ):
                        if k in field_effects:
                            _narrative_field_effects[k] *= field_effects[k]

            # Apply batting effects to run weights
            six_mult = _narrative_bat_effects.get("six_mult", 1.0)
            four_mult = _narrative_bat_effects.get("four_mult", 1.0)
            wicket_risk = _narrative_bat_effects.get("wicket_risk", 1.0)
            death_over_bonus = _narrative_bat_effects.get("death_over_bonus", 1.0)
            powerplay_boundary_mult = _narrative_bat_effects.get(
                "powerplay_boundary_mult", 1.0
            )
            if six_mult != 1.0:
                run_weights = list(run_weights)
                run_weights[5] *= six_mult
            if four_mult != 1.0:
                run_weights = list(run_weights)
                run_weights[4] *= four_mult
            if wicket_risk != 1.0:
                run_weights = list(run_weights)
                run_weights[0] *= wicket_risk
            if death_over_bonus != 1.0:
                current_over = getattr(delivery, "over_number", 10)
                if current_over >= 15:
                    run_weights = list(run_weights)
                    for i in range(1, 6):
                        run_weights[i] *= death_over_bonus
            if powerplay_boundary_mult != 1.0:
                current_over = getattr(delivery, "over_number", 10)
                if current_over <= 6:
                    run_weights = list(run_weights)
                    run_weights[4] *= powerplay_boundary_mult
                    run_weights[5] *= powerplay_boundary_mult
        except Exception:
            pass

    outcome = random.choices(outcomes, weights=run_weights, k=1)[0]

    # ── 9. Dismissal resolution ───────────────────────────────────────────────
    actual_out = False
    umpire_out = False
    drs_eligible_wicket = False
    dismissal = None
    review_reason = None
    overthrows = 0
    is_direct_hit = False
    fielder_name = None
    free_hit_saved = False

    # ── 9a. No-ball wicket handling ───────────────────────────────────────────
    if umpire_called_nb and outcome == "W":
        # On a no-ball, only Run Out can dismiss; everything else is free hit
        if forced_dismissal and forced_dismissal == "Run Out":
            dismissal = "Run Out"
            actual_out = True
            umpire_out = True
            drs_eligible_wicket = random.random() < 0.001
            is_direct_hit = random.random() < 0.35
            fielder_name = "Fielder"
        else:
            # No-ball - wicket nullified, free hit
            actual_out = False
            umpire_out = False
            free_hit_saved = True
        runs = 0
        outcome_str = "W" if umpire_out else f"{runs}NB"

    # ── 9b. Regular wicket handling ───────────────────────────────────────────
    elif outcome == "W":
        actual_out = True
        runs = 0
        outcome_str = "W"

        if forced_dismissal:
            dismissal = forced_dismissal
        else:
            # Base dismissal weights adjusted by delivery/shot combination
            catch_w = 50 + (fielder_grade - 1) * 8
            bowled_w = 20
            lbw_w = 18
            stumped_w = 7
            hit_wicket_w = 2
            obstructing_w = 1

            # Delivery-specific adjustments
            if delivery in ("Yorker", "Full", "Good Length"):
                bowled_w += 15
                lbw_w += 12
            elif delivery in ("Bouncer", "Short"):
                bowled_w -= 5
                lbw_w -= 10
                catch_w += 10
            elif delivery in ALL_SPIN:
                stumped_w += 8
                lbw_w += 5
                catch_w += 5

            # Shot-specific adjustments
            if shot in ("Pull", "Cut", "Sweep", "Reverse-Sweep", "Hook"):
                catch_w += 20
            elif shot == "Lofted":
                catch_w += 15
                hit_wicket_w += 3
            elif shot in ("Defend", "Back-foot", "Front-foot"):
                bowled_w += 5
                lbw_w += 5
                catch_w -= 5

            # Rare dismissals
            if random.random() < 0.003:
                dismissal = "Hit Wicket"
            elif random.random() < 0.001:
                dismissal = "Obstructing"
            elif random.random() < 0.0005:
                dismissal = "Timed Out"
            else:
                total_w = (
                    catch_w
                    + bowled_w
                    + lbw_w
                    + stumped_w
                    + hit_wicket_w
                    + obstructing_w
                )
                if total_w <= 0:
                    total_w = 1
                dismissal = random.choices(
                    ["Caught", "Bowled", "LBW", "Stumped", "Hit Wicket", "Obstructing"],
                    weights=[
                        catch_w,
                        bowled_w,
                        lbw_w,
                        stumped_w,
                        hit_wicket_w,
                        obstructing_w,
                    ],
                )[0]

        # Set DRS eligibility and umpire decision per dismissal type
        if dismissal == "LBW":
            drs_eligible_wicket = True
            review_reason = "LBW"
            # Umpire gives LBW out ~80% of the time when actually out
            umpire_out = actual_out and random.random() < 0.80
        elif dismissal == "Caught":
            drs_eligible_wicket = False
            review_reason = "Caught"
            umpire_out = actual_out and random.random() < 0.90
            fielder_name = "Fielder"
        elif dismissal == "Stumped":
            drs_eligible_wicket = random.random() < 0.001
            review_reason = "Stumped"
            umpire_out = actual_out and random.random() < 0.85
            fielder_name = "Keeper"
        elif dismissal == "Bowled":
            drs_eligible_wicket = False
            umpire_out = actual_out and random.random() < 0.95
        elif dismissal == "Run Out":
            drs_eligible_wicket = False
            review_reason = "Run Out"
            umpire_out = actual_out and random.random() < 0.90
            is_direct_hit = random.random() < 0.35
            fielder_name = "Fielder"
        elif dismissal == "Hit Wicket":
            drs_eligible_wicket = random.random() < 0.001
            review_reason = "Hit Wicket"
            umpire_out = actual_out and random.random() < 0.90
        elif dismissal == "Obstructing":
            drs_eligible_wicket = random.random() < 0.001
            review_reason = "Obstructing"
            umpire_out = actual_out and random.random() < 0.70
        elif dismissal == "Timed Out":
            drs_eligible_wicket = False
            umpire_out = True
        else:
            drs_eligible_wicket = False
            umpire_out = actual_out

    # ── 9c. Non-wicket outcomes ───────────────────────────────────────────────
    else:
        runs = int(outcome)
        actual_out = False
        umpire_out = False
        drs_eligible_wicket = False
        dismissal = None
        outcome_str = str(runs)

    # ── 10. Byes / leg-byes on dot balls ─────────────────────────────────────
    if runs == 0 and not umpire_called_nb and outcome != "W":
        if random.random() < 0.01:
            byes_leg_byes = random.choice(["B", "LB"])
            extras_scored = random.choices([1, 2, 4], weights=[65, 25, 10])[0]
            return build_payload(
                f"{extras_scored}{byes_leg_byes}",
                0,
                extras_scored,
                True,
                byes_leg_byes,
                None,
                timing_label,
                0,
                False,
                None,
                False,
                is_actual_nb,
                umpire_called_nb,
                drs_eligible_nb,
                nb_review_reason,
                False,
                None,
            )

    # ── 11. Run-outs and overthrows ───────────────────────────────────────────
    if not umpire_called_nb and not umpire_out and not actual_out:
        if outcome in ("1", "2", "3"):
            base_run_out = (
                0.008 if outcome == "1" else (0.025 if outcome == "2" else 0.06)
            )
            fielder_mod = 1.0 + (fielder_grade - 1) * 0.15
            if _narrative_field_effects:
                run_out_mult = _narrative_field_effects.get("run_out_chance", 1.0)
                direct_hit_mult = _narrative_field_effects.get("direct_hit_chance", 1.0)
                fielder_mod *= run_out_mult
                if random.random() < (0.035 * direct_hit_mult):
                    pass
            fielder_mod = min(fielder_mod, 1.60)
            run_out_chance = base_run_out * fielder_mod
            if random.random() < run_out_chance:
                actual_out, umpire_out, dismissal = True, True, "Run Out"
                runs = max(0, runs - 1)
                drs_eligible_wicket, review_reason = random.random() < 0.001, "Run Out"
                is_direct_hit = random.random() < 0.35
                fielder_name = "Fielder"
                free_hit_saved = False
            elif random.random() < 0.0025:
                overthrows = random.choices([1, 2, 4], weights=[70, 20, 10])[0]
    elif umpire_called_nb and not umpire_out and not actual_out:
        if random.random() < 0.0025:
            overthrows = random.choices([1, 2], weights=[80, 20])[0]

    # ── 12. Free hit protection ───────────────────────────────────────────────
    if (
        (umpire_called_nb or free_hit)
        and actual_out
        and dismissal not in ("Run Out", None)
    ):
        umpire_out = False
        actual_out = False
        free_hit_saved = True

    is_legal = not umpire_called_nb
    extra_type = "NB" if umpire_called_nb else None
    runs_extras = 1 if umpire_called_nb else 0

    if umpire_out:
        outcome_str = "W"
    elif umpire_called_nb:
        outcome_str = f"{runs + overthrows}NB" if (runs + overthrows) > 0 else "NB"
    elif free_hit_saved:
        outcome_str = str(runs + overthrows)

    return build_payload(
        outcome_str,
        runs,
        runs_extras,
        is_legal,
        extra_type,
        dismissal,
        timing_label,
        overthrows,
        umpire_out,
        actual_out,
        drs_eligible_wicket,
        review_reason,
        free_hit_saved,
        is_actual_nb,
        umpire_called_nb,
        drs_eligible_nb,
        nb_review_reason,
        is_direct_hit,
        fielder_name,
    )
