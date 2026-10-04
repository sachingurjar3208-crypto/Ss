import random

# ── Match condition pools ─────────────────────────────────────────────────────

VENUES = [
    ("Wankhede Stadium",              "Mumbai, India"),
    ("Eden Gardens",                  "Kolkata, India"),
    ("M. Chinnaswamy Stadium",        "Bengaluru, India"),
    ("Narendra Modi Stadium",         "Ahmedabad, India"),
    ("Melbourne Cricket Ground",      "Melbourne, Australia"),
    ("Sydney Cricket Ground",         "Sydney, Australia"),
    ("Adelaide Oval",                 "Adelaide, Australia"),
    ("Lord's Cricket Ground",         "London, England"),
    ("Edgbaston",                     "Birmingham, England"),
    ("Old Trafford",                  "Manchester, England"),
    ("Headingley",                    "Leeds, England"),
    ("Newlands",                      "Cape Town, South Africa"),
    ("SuperSport Park",               "Centurion, South Africa"),
    ("National Stadium",              "Karachi, Pakistan"),
    ("Gaddafi Stadium",               "Lahore, Pakistan"),
    ("Sharjah Cricket Stadium",       "Sharjah, UAE"),
    ("Dubai International Stadium",   "Dubai, UAE"),
    ("R. Premadasa Stadium",          "Colombo, Sri Lanka"),
    ("Shere Bangla National Stadium", "Dhaka, Bangladesh"),
    ("Sabina Park",                   "Kingston, West Indies"),
]

PITCH_REPORTS = [
    "Dry & Dusty",
    "Green & Lively",
    "Hard & Flat",
    "Soft & Slow",
    "Fresh & Bouncy",
    "Cracked & Turning",
    "Damp & Seaming",
    "Batting Paradise",
    "Two-Paced Track",
    "Sandy & Abrasive",
]

WEATHER_CONDITIONS = [
    ("Clear Skies",    ""),
    ("Partly Cloudy",  ""),
    ("Overcast",       ""),
    ("Hot & Sunny",    ""),
    ("Light Breeze",   ""),
    ("Humid & Heavy",  ""),
    ("Hazy Sunshine",  ""),
]

UMPIRES = [
    "Aleem Dar",
    "Kumar Dharmasena",
    "Paul Reiffel",
    "Rod Tucker",
    "Marais Erasmus",
    "Michael Gough",
    "Richard Illingworth",
    "Joel Wilson",
    "Chris Gaffaney",
    "Nitin Menon",
    "Adrian Holdstock",
    "Paul Wilson",
]

CROWD_MOODS = [
    "Electric",
    "Buzzing",
    "Explosive",
    "Roaring",
    "Tense",
    "Wild",
    "Deafening",
    "Fully Packed",
    "On Their Feet",
    "Absolutely Rocking",
]


def random_match_conditions() -> dict:
    venue_name, venue_loc = random.choice(VENUES)
    weather_label, weather_emoji = random.choice(WEATHER_CONDITIONS)
    return {
        "venue":         venue_name,
        "location":      venue_loc,
        "pitch":         random.choice(PITCH_REPORTS),
        "weather":       weather_label,
        "weather_emoji": weather_emoji,
        "temperature":   random.randint(15, 37),
        "umpire":        random.choice(UMPIRES),
        "crowd":         random.choice(CROWD_MOODS),
    }


TOSS_HEAD_EMOJI = "<:cs_head:1503331241133342864>"
TOSS_TAIL_EMOJI = "<:cs_tail:1503331245281513533>"

TIMELINE_EMOJIS = {
    "0":    "<:cs_dot:1508773202787307540>",
    "1":    "<:cs_1run:1508773195178573956>",
    "2":    "<:cs_2run:1508773198043418624>",
    "3":    "<:cs_3run:1508773200429842463>",
    "4":    "<a:Four:1509025601716097044>",
    "6":    "<a:Six:1509025607168823336>",
    "W":    "<:cs_wicket:1508773205370863708>",
    "NB":   "<:cs_noball:1509387611641483496>",
    "NB+1": "<:cs_noball1:1509387607560163358>",
    "Wd":   "<:cs_wide:1519933918306893824>",
}

NOT_OUT_EMOJI = "<:cs_notout:1508773208470454382>"
LBW_EMOJI     = "<:cs_lbw:1519933921385775156>"

BOWLING_TYPE_ICON = {
    "Fast":     "",
    "Off Spin": "",
    "Leg Spin": "",
}

# Button label → internal delivery name
DELIVERY_BUTTON_MAP = {
    # Fast
    "Inswing":  "Swing",
    "Outswing": "Swing",
    "Slow":     "Good Length",
    "Fast":     "Fast",
    "Bouncer":  "Bouncer",
    "Good":     "Good Length",
    "Full":     "Full",
    "Yorker":   "Yorker",
    # Off Spin
    "Offspin":  "Off Break",
    "Carrom":   "Carrom Ball",
    "Arm Ball": "Arm Ball",
    "Doosra":   "Doosra",
    "Topspin":  "Top Spin",
    # Leg Spin
    "Legspin":  "Leg Break",
    "Googly":   "Googly",
    "Flipper":  "Flipper",
    "Drifter":  "Drift Ball",
    "Slider":   "Slider",
}

SHOT_BUTTON_MAP = {
    "Drive":  "Drive",
    "Loft":   "Lofted",
    "Flick":  "Flick",
    "Defend": "Defend",
    "Sweep":  "Sweep",
    "Cut":    "Cut",
    "Leave":  "Defend",
    "Pull":   "Pull",
    "Scoop":  "Reverse-Sweep",
}

BALL_SPEEDS = {
    "Inswing":    (120, 138),
    "Outswing":   (122, 140),
    "Slow":       (100, 118),
    "Fast":       (135, 150),
    "Bouncer":    (135, 150),
    "Good":       (125, 140),
    "Full":       (126, 138),
    "Yorker":     (138, 148),
    "Offspin":    (85,  98),
    "Carrom":     (85,  96),
    "Arm Ball":   (88,  98),
    "Doosra":     (88, 100),
    "Topspin":    (85,  96),
    "Legspin":    (82,  95),
    "Googly":     (82,  92),
    "Flipper":    (88,  98),
    "Drifter":    (82,  95),
    "Slider":     (83,  95),
    "Swing":       (120, 135),
    "Good Length": (125, 140),
    "Off Break":   (85,  98),
    "Carrom Ball": (85,  96),
    "Top Spin":    (85,  96),
    "Drift Ball":  (82,  95),
    "Leg Break":   (82,  95),
    "Top Spinner": (83,  96),
}

BALL_DESCRIPTIONS = {
    "Inswing":    "inswinging delivery",
    "Outswing":   "outswinging delivery",
    "Slow":       "slow delivery",
    "Fast":       "fast delivery",
    "Bouncer":    "throat-high bouncer",
    "Good":       "good length delivery",
    "Full":       "full-pitched delivery",
    "Yorker":     "toe-crushing yorker",
    "Offspin":    "off-break",
    "Carrom":     "carrom ball",
    "Arm Ball":   "arm ball",
    "Doosra":     "doosra",
    "Topspin":    "top-spinner",
    "Legspin":    "leg-break",
    "Googly":     "googly",
    "Flipper":    "flipper",
    "Drifter":    "drifting delivery",
    "Slider":     "slider",
    "Swing":       "swinging delivery",
    "Good Length": "good length delivery",
    "Off Break":   "off-break",
    "Carrom Ball": "carrom ball",
    "Top Spin":    "top-spinner",
    "Drift Ball":  "drifting delivery",
    "Leg Break":   "leg-break",
    "Top Spinner": "top-spinner",
}

SHOT_DESCRIPTIONS = {
    "Drive":         "drives",
    "Lofted":        "lofts",
    "Defend":        "defends",
    "Sweep":         "sweeps",
    "Cut":           "cuts",
    "Pull":          "pulls",
    "Reverse-Sweep": "reverse-sweeps",
}

# Fast bowler two-stage button groups
FAST_STAGE1 = ["Outswing", "Inswing", "Fast", "Slow"]
FAST_STAGE2 = ["Bouncer", "Full", "Good", "Yorker"]

OFF_SPIN_BUTTONS = ["Offspin", "Carrom", "Arm Ball", "Doosra", "Topspin"]
LEG_SPIN_BUTTONS = ["Legspin", "Googly", "Flipper", "Drifter", "Slider"]

BOWLING_BUTTONS = {
    "Fast":     FAST_STAGE2,   # used for spin, fast uses two-stage
    "Off Spin": OFF_SPIN_BUTTONS,
    "Leg Spin": LEG_SPIN_BUTTONS,
}

BATTING_BUTTONS = ["Drive", "Loft", "Flick", "Pull", "Cut", "Sweep", "Scoop", "Defend", "Leave"]

# ── Shot guide ────────────────────────────────────────────────────────────────
# Maps delivery combination → list of recommended shot BUTTON labels.
# Using a recommended shot lowers wicket chance and boosts boundaries.

# Fast bowler: (stage1_choice, stage2_button) → recommended shots
FAST_SHOT_GUIDE: dict[tuple[str, str], list[str]] = {
    # Yorkers — Drive vs Yorker is (bat:0.5, wkt:2.0) in MATCHUP_MATRIX,
    # the worst possible pairing.  Flick (bat:0.9, wkt:1.2) and Defend
    # (bat:0.6, wkt:0.7) are the only sensible options for all variants.
    ("Fast",     "Yorker"):  ["Flick", "Defend"],
    ("Slow",     "Yorker"):  ["Flick", "Defend"],
    ("Inswing",  "Yorker"):  ["Flick", "Defend"],
    ("Outswing", "Yorker"):  ["Flick", "Defend"],
    # Full
    ("Fast",     "Full"):    ["Loft", "Flick"],
    ("Slow",     "Full"):    ["Drive", "Sweep"],
    ("Inswing",  "Full"):    ["Loft", "Flick"],
    ("Outswing", "Full"):    ["Drive", "Cut"],
    # Good Length
    ("Fast",     "Good"):    ["Drive", "Loft"],
    ("Slow",     "Good"):    ["Cut", "Pull"],
    ("Inswing",  "Good"):    ["Drive", "Pull", "Flick"],
    ("Outswing", "Good"):    ["Cut"],
    # Bouncer
    ("Fast",     "Bouncer"): ["Cut"],
    ("Slow",     "Bouncer"): ["Loft", "Pull"],
    ("Inswing",  "Bouncer"): ["Loft", "Pull"],
    ("Outswing", "Bouncer"): ["Cut"],
}

# Spinner: delivery_button → recommended shots
SPIN_SHOT_GUIDE: dict[str, list[str]] = {
    # Leg Spin
    "Legspin":  ["Cut", "Drive"],
    "Googly":   ["Pull", "Sweep"],
    "Flipper":  ["Drive", "Flick"],
    "Drifter":  ["Loft", "Flick"],
    "Slider":   ["Cut", "Flick"],
    # Off Spin
    "Offspin":  ["Drive", "Sweep"],
    "Carrom":   ["Cut"],
    "Arm Ball": ["Drive", "Loft", "Flick"],
    "Doosra":   ["Cut", "Loft"],
    "Topspin":  ["Cut", "Pull"],
    # Cutters / Knuckle (fast bowler special)
    "Off cutter": ["Drive", "Sweep"],
    "Leg cutter": ["Cut"],
    "Knuckle":    ["Pull", "Loft"],
}

FAST_MYSTERY_POOL     = ["Fast", "Swing", "Yorker", "Bouncer", "Good Length", "Full"]
OFF_SPIN_MYSTERY_POOL = ["Off Break", "Doosra", "Carrom Ball", "Arm Ball", "Top Spin"]
LEG_SPIN_MYSTERY_POOL = ["Drift Ball", "Leg Break", "Googly", "Flipper", "Top Spinner", "Slider"]

MYSTERY_POOL = {
    "Fast":     FAST_MYSTERY_POOL,
    "Off Spin": OFF_SPIN_MYSTERY_POOL,
    "Leg Spin": LEG_SPIN_MYSTERY_POOL,
}

COUNTRY_FLAGS = {
    "India":        "🇮🇳",
    "Australia":    "🇦🇺",
    "England":      "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Pakistan":     "🇵🇰",
    "South Africa": "🇿🇦",
    "New Zealand":  "🇳🇿",
    "West Indies":  "🌴",
    "Bangladesh":   "🇧🇩",
    "Sri Lanka":    "🇱🇰",
    "Afghanistan":  "🇦🇫",
    "Zimbabwe":     "🇿🇼",
    "Ireland":      "🇮🇪",
}

SAMPLE_TEAMS = {
    "India": {
        "name": "India",
        "ovr": 90,
        "chem": 94,
        "players": [
            {"name": "Rohit Sharma",     "country": "India", "ovr": 92, "bat": 95, "bowl": 45, "bowling_type": "Fast",     "role": "Batter",      "card": "🥇"},
            {"name": "Virat Kohli",      "country": "India", "ovr": 94, "bat": 97, "bowl": 40, "bowling_type": None,       "role": "Batter",      "card": "🥇"},
            {"name": "Shubman Gill",     "country": "India", "ovr": 88, "bat": 90, "bowl": 28, "bowling_type": None,       "role": "Batter",      "card": "🥈"},
            {"name": "Suryakumar Yadav", "country": "India", "ovr": 91, "bat": 93, "bowl": 22, "bowling_type": None,       "role": "Batter",      "card": "🥇"},
            {"name": "Rishabh Pant",     "country": "India", "ovr": 88, "bat": 90, "bowl": 15, "bowling_type": None,       "role": "WK",          "card": "🥇"},
            {"name": "Hardik Pandya",    "country": "India", "ovr": 87, "bat": 82, "bowl": 84, "bowling_type": "Fast",     "role": "All-Rounder", "card": "🥇"},
            {"name": "Ravindra Jadeja",  "country": "India", "ovr": 89, "bat": 78, "bowl": 88, "bowling_type": "Off Spin", "role": "All-Rounder", "card": "🥇"},
            {"name": "Kuldeep Yadav",    "country": "India", "ovr": 83, "bat": 38, "bowl": 87, "bowling_type": "Leg Spin", "role": "Bowler",      "card": "🥈"},
            {"name": "Jasprit Bumrah",   "country": "India", "ovr": 92, "bat": 28, "bowl": 96, "bowling_type": "Fast",     "role": "Bowler",      "card": "🥇"},
            {"name": "Mohammed Siraj",   "country": "India", "ovr": 84, "bat": 25, "bowl": 85, "bowling_type": "Fast",     "role": "Bowler",      "card": "🥈"},
            {"name": "Yuzvendra Chahal", "country": "India", "ovr": 82, "bat": 22, "bowl": 84, "bowling_type": "Leg Spin", "role": "Bowler",      "card": "🥈"},
        ],
    },
    "Australia": {
        "name": "Australia",
        "ovr": 89,
        "chem": 91,
        "players": [
            {"name": "David Warner",   "country": "Australia", "ovr": 91, "bat": 93, "bowl": 42, "bowling_type": None,       "role": "Batter",      "card": "🥇"},
            {"name": "Travis Head",    "country": "Australia", "ovr": 89, "bat": 91, "bowl": 50, "bowling_type": "Off Spin", "role": "Batter",      "card": "🥇"},
            {"name": "Steve Smith",    "country": "Australia", "ovr": 92, "bat": 94, "bowl": 55, "bowling_type": "Leg Spin", "role": "Batter",      "card": "🥇"},
            {"name": "Glenn Maxwell",  "country": "Australia", "ovr": 88, "bat": 86, "bowl": 80, "bowling_type": "Off Spin", "role": "All-Rounder", "card": "🥇"},
            {"name": "Matthew Wade",   "country": "Australia", "ovr": 82, "bat": 80, "bowl": 15, "bowling_type": None,       "role": "WK",          "card": "🥈"},
            {"name": "Cameron Green",  "country": "Australia", "ovr": 84, "bat": 80, "bowl": 82, "bowling_type": "Fast",     "role": "All-Rounder", "card": "🥈"},
            {"name": "Pat Cummins",    "country": "Australia", "ovr": 90, "bat": 60, "bowl": 93, "bowling_type": "Fast",     "role": "Bowler",      "card": "🥇"},
            {"name": "Mitchell Starc", "country": "Australia", "ovr": 89, "bat": 42, "bowl": 91, "bowling_type": "Fast",     "role": "Bowler",      "card": "🥇"},
            {"name": "Adam Zampa",     "country": "Australia", "ovr": 84, "bat": 28, "bowl": 85, "bowling_type": "Leg Spin", "role": "Bowler",      "card": "🥈"},
            {"name": "Josh Hazlewood", "country": "Australia", "ovr": 87, "bat": 22, "bowl": 88, "bowling_type": "Fast",     "role": "Bowler",      "card": "🥇"},
            {"name": "Nathan Lyon",    "country": "Australia", "ovr": 84, "bat": 35, "bowl": 85, "bowling_type": "Off Spin", "role": "Bowler",      "card": "🥈"},
        ],
    },
}


def resolve_delivery(button_label: str, bowling_type: str) -> str:
    if button_label == "Mystery":
        pool = MYSTERY_POOL.get(bowling_type, FAST_MYSTERY_POOL)
        return random.choice(pool)
    return DELIVERY_BUTTON_MAP.get(button_label, button_label)


def get_delivery_speed(button_label: str) -> tuple:
    return BALL_SPEEDS.get(button_label, (100, 140))


def get_delivery_description(button_label: str, internal: str) -> str:
    if button_label == "Mystery":
        return BALL_DESCRIPTIONS.get(internal, internal.lower())
    return BALL_DESCRIPTIONS.get(button_label, button_label.lower())
