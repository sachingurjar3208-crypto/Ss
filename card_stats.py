"""Stat sets for the Style 2 card (6 stats, two columns of 3).

Everything you may want to edit is in STAT_SETS below.

Meaning of the labels:
    BAT batting          BOW bowling          (these two are the card's own
                                               `bat` / `bowl` columns)
    PWR power hitting    RUN running between wickets    TIM timing
    CLU clutch           FIE fielding
    ACC bowling accuracy SWI swing            PAC pace / spin
    KEE wicketkeeping    CAT catching

Every label except BAT and BOW is an "extra stat". Extra stats are saved with
/cardstats. If one was never set, a value is DERIVED from the card's other
numbers, so old cards always render.
"""

from __future__ import annotations

import zlib

from card_db import effective_role, get_extra_stats

# First 3 = LEFT column, last 3 = RIGHT column.
STAT_SETS: dict[str, list[str]] = {
    "BAT":  ["BAT", "PWR", "RUN", "TIM", "CLU", "FIE"],
    "BOWL": ["BOW", "ACC", "SWI", "PAC", "CLU", "FIE"],
    "AR":   ["BAT", "BOW", "PWR", "ACC", "CLU", "FIE"],
    "WK":   ["BAT", "PWR", "RUN", "KEE", "CAT", "CLU"],
}

# Labels that come straight from the card columns (not from extra_stats).
CORE_LABELS = {"BAT": "bat", "BOW": "bowl"}

# Which card number an unset extra stat is based on.
DERIVE_BASE: dict[str, str] = {
    "PWR": "bat", "RUN": "bat", "TIM": "bat",
    "ACC": "bowl", "SWI": "bowl", "PAC": "bowl",
    "CLU": "ovr", "FIE": "ovr", "KEE": "ovr", "CAT": "ovr",
}

MIN_STAT, MAX_STAT = 1, 150


def _clamp(value: int) -> int:
    return max(MIN_STAT, min(MAX_STAT, int(value)))


def _wiggle(card, label: str) -> int:
    """Small fixed variation (-4..+4): same card + same stat = same number every redraw."""
    try:
        key = card["playername_key"]
    except (IndexError, KeyError):
        key = ""
    return zlib.crc32(f"{key}:{label.lower()}".encode("utf-8")) % 9 - 4


def card_stat_details(card) -> list[tuple[str, int, bool]]:
    """The 6 stats a card shows, in display order: (LABEL, value, is_derived)."""
    labels = STAT_SETS.get(effective_role(card), STAT_SETS["BAT"])
    extra = get_extra_stats(card)
    rows: list[tuple[str, int, bool]] = []
    for label in labels:
        if label in CORE_LABELS:
            rows.append((label, _clamp(card[CORE_LABELS[label]]), False))
            continue
        key = label.lower()
        if key in extra:
            rows.append((label, _clamp(extra[key]), False))
            continue
        base = card[DERIVE_BASE[label]]
        rows.append((label, _clamp(base + _wiggle(card, label)), True))
    return rows


def card_stat_rows(card) -> list[tuple[str, int]]:
    """The 6 (LABEL, value) pairs in display order (left column first)."""
    return [(label, value) for label, value, _ in card_stat_details(card)]


def allowed_extra_keys(role: str) -> list[str]:
    """Extra-stat keys (lowercase) that belong to this role, e.g. ['acc', 'swi', ...]."""
    labels = STAT_SETS.get(role, STAT_SETS["BAT"])
    return [label.lower() for label in labels if label not in CORE_LABELS]
