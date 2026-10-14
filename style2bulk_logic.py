"""Pure logic of /style2bulk (no Discord here, so it can be tested on its own).

apply_style2_bulk() takes ONE existing card and
  1. switches it to Style 2 (+ background / icon / legs fade / colours),
  2. gives its role's stats random values just below its OVR (kept if already saved,
     unless reroll=True).
It does NOT draw the image - the cog does that.
"""

from __future__ import annotations

from card_db import (
    get_card, set_card_style, set_extra_stats, set_style2_options, _UNSET,
)
from card_stats import card_stat_details, random_extra_stats, stat_set_name

SET_TITLES = {"BAT": "Batter", "BOWL": "Pacer", "SPIN": "Spinner", "AR": "All-rounder", "WK": "Keeper"}


def apply_style2_bulk(
    name: str,
    *,
    bg=_UNSET,              # backgrounds.pathname, None = built-in purple, _UNSET = leave as is
    icon=_UNSET,            # "gold" / "bronze" / None
    fade_legs=_UNSET,       # True / False
    name_color=_UNSET,
    line_color=_UNSET,
    reroll: bool = False,
    rng=None,
) -> dict | None:
    """Returns None if there is no such card, else a small summary dict."""
    card = get_card(name)
    if card is None:
        return None

    set_card_style(card["playername"], "style2")
    set_style2_options(
        card["playername"], bg=bg, icon=icon, fade_legs=fade_legs,
        name_color=name_color, line_color=line_color,
    )
    new_stats = random_extra_stats(get_card(name), reroll=reroll, rng=rng)
    if new_stats:
        set_extra_stats(card["playername"], new_stats)

    card = get_card(name)
    return {
        "playername": card["playername"],
        "ovr": card["ovr"],
        "set": SET_TITLES.get(stat_set_name(card), "?"),
        "rows": [(label, value) for label, value, _ in card_stat_details(card)],
    }
