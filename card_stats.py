"""Stat sets for the Style 2 card (6 stats, two columns of 3).

Everything you may want to edit is in STAT_SETS below.

Meaning of the labels:
    BAT batting          BOW bowling          (these two are the card's own
                                               `bat` / `bowl` columns)
    PWR power hitting    RUN running between wickets    TIM timing
    CLU clutch           FIE fielding
    TRN turn             FLT flight / drift   VAR variations (googly, doosra...)
    CTL control (spinner)
    ACC bowling accuracy SWI swing            PAC pace / spin
    KEE wicketkeeping    CAT catching

Every label except BAT and BOW is an "extra stat". Extra stats are saved with
/cardstats. If one was never set, a random value just below OVR is used (fixed per
card, so it is the same on every redraw).

Rules for what the card SHOWS (the match engine still uses the card's real BAT / BOWL):
  * no stat is ever shown above the card's OVR
  * a bowler's RUN is about OVR - 60
"""

from __future__ import annotations

import random
import zlib

from card_db import effective_role, get_extra_stats

# First 3 = LEFT column, last 3 = RIGHT column.
STAT_SETS: dict[str, list[str]] = {
    "BAT":  ["BAT", "PWR", "RUN", "TIM", "CLU", "FIE"],
    "BOWL": ["BOW", "ACC", "SWI", "PAC", "CTL", "RUN"],     # pacer
    "AR":   ["BAT", "BOW", "PWR", "ACC", "CLU", "FIE"],
    "WK":   ["BAT", "KEE", "CAT", "RUN", "FIE", "CLU"],
    # Spinner (BOWL role): used automatically when the card's bowling type is
    # Off Spin / Leg Spin / Orthodox, or when any of TRN / FLT / VAR was set.
    "SPIN": ["BOW", "TRN", "FLT", "VAR", "PAC", "RUN"],
}

SPIN_TYPES = ("Off Spin", "Leg Spin", "Orthodox")
SPIN_KEYS = ("trn", "flt", "var")      # CTL is also a pacer stat, so it does not count

# Labels that come straight from the card columns (not from extra_stats).
CORE_LABELS = {"BAT": "bat", "BOW": "bowl"}

# Which card number an unset extra stat is based on.
DERIVE_BASE: dict[str, str] = {
    "PWR": "bat", "RUN": "bat", "TIM": "bat",
    "ACC": "bowl", "SWI": "bowl", "PAC": "bowl",
    "TRN": "bowl", "FLT": "bowl", "VAR": "bowl", "CTL": "bowl",
    "CLU": "ovr", "FIE": "ovr", "KEE": "ovr", "CAT": "ovr",
}

MIN_STAT, MAX_STAT = 1, 150

# Shown stats are never above the card's OVR. Unset stats are random (but fixed per card):
# OVR minus RANDOM_LOW..RANDOM_HIGH. A bowler's RUN (running between wickets) is OVR minus
# about BOWLER_RUN_DROP, because bowlers are poor runners / batters.
RANDOM_LOW, RANDOM_HIGH = 1, 8
BOWLER_RUN_DROP = 60          # e.g. OVR 90 bowler -> RUN around 26-30
BOWLER_RUN_SPREAD = 4


def _clamp(value: int) -> int:
    return max(MIN_STAT, min(MAX_STAT, int(value)))


def _seed(card, label: str) -> int:
    """Fixed number for this card + stat: the same 'random' value comes back on every redraw."""
    try:
        key = card["playername_key"]
    except (IndexError, KeyError):
        key = ""
    return zlib.crc32(f"{key}:{label.lower()}".encode("utf-8"))


def _ovr(card) -> int:
    try:
        return int(card["ovr"])
    except (IndexError, KeyError, TypeError, ValueError):
        return MAX_STAT


def _bowler_run(card) -> int:
    return max(MIN_STAT, _ovr(card) - BOWLER_RUN_DROP - _seed(card, "run") % (BOWLER_RUN_SPREAD + 1))


def stat_set_name(card) -> str:
    """Which STAT_SETS entry this card uses: BAT / BOWL / AR / WK / SPIN."""
    role = effective_role(card)
    if role == "BOWL":
        try:
            bt = card["bowling_type"]
        except (IndexError, KeyError):
            bt = None
        if bt in SPIN_TYPES or any(k in get_extra_stats(card) for k in SPIN_KEYS):
            return "SPIN"
    return role if role in STAT_SETS else "BAT"


def card_stat_details(card) -> list[tuple[str, int, bool]]:
    """The 6 stats a card shows, in display order: (LABEL, value, is_derived)."""
    set_name = stat_set_name(card)
    labels = STAT_SETS[set_name]
    extra = get_extra_stats(card)
    top = _ovr(card)
    is_bowler = set_name in ("BOWL", "SPIN")
    rows: list[tuple[str, int, bool]] = []
    for label in labels:
        if label == "RUN" and is_bowler:                     # bowlers: always far below OVR
            rows.append((label, _clamp(_bowler_run(card)), True))
            continue
        if label in CORE_LABELS:
            rows.append((label, _clamp(min(card[CORE_LABELS[label]], top)), False))
            continue
        key = label.lower()
        if key in extra:
            rows.append((label, _clamp(min(extra[key], top)), False))
            continue
        # not set: random but fixed, OVR - (RANDOM_LOW..RANDOM_HIGH)
        drop = RANDOM_LOW + _seed(card, label) % (RANDOM_HIGH - RANDOM_LOW + 1)
        rows.append((label, _clamp(top - drop), True))
    return rows


def card_stat_rows(card) -> list[tuple[str, int]]:
    """The 6 (LABEL, value) pairs in display order (left column first)."""
    return [(label, value) for label, value, _ in card_stat_details(card)]


def allowed_extra_keys(role: str) -> list[str]:
    """Extra-stat keys (lowercase) that belong to this role, e.g. ['acc', 'swi', ...]."""
    names = [role if role in STAT_SETS else "BAT"]
    if role == "BOWL":
        names.append("SPIN")          # a bowler card can also take the spinner stats
    keys: list[str] = []
    for name in names:
        for label in STAT_SETS[name]:
            if label not in CORE_LABELS and label.lower() not in keys:
                keys.append(label.lower())
    return keys


def random_extra_stats(card, reroll: bool = False, low: int = 1, high: int = 8, rng=None) -> dict:
    """Random values for the card's extra stats, each OVR - (low..high).
    Example: OVR 78 -> every stat is somewhere between 70 and 77. A bowler's RUN is OVR - ~60.
    Stats that are already saved are kept unless reroll=True."""
    rng = rng or random
    saved = get_extra_stats(card)
    ovr = int(card["ovr"])
    out: dict[str, int] = {}
    for label in STAT_SETS[stat_set_name(card)]:
        if label in CORE_LABELS:
            continue                      # BAT / BOW are the card's own numbers
        key = label.lower()
        if key in saved and not reroll:
            continue
        if label == "RUN" and stat_set_name(card) in ("BOWL", "SPIN"):
            out[key] = _clamp(ovr - BOWLER_RUN_DROP - rng.randint(0, BOWLER_RUN_SPREAD))
            continue
        out[key] = _clamp(ovr - rng.randint(low, high))
    return out
