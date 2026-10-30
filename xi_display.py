"""One look for every Playing XI shown as text (rcxi, match XI, bot XI ...).

One row per player:

    <card emoji> <role emoji> `Virat Kohli      94  94  36` <flag>

  * card emoji  the player's EVENT emoji (/eventmaker); players that are not in an
                event get emojis.DEFAULT_CARD_EMOJI
  * role emoji  keeper / batter / all-rounder / pacer / spinner (emojis.py)
  * the code part is monospace, so name + OVR / BAT / BOWL always line up
                (custom emojis can't live inside code, so they sit outside it)
  * flag        the country flag of the card

Everything here is defensive: a missing field just leaves that part out, it never raises.
"""

from __future__ import annotations

import emojis

NAME_W = 15

# Used only when a player dict has no "remoji" (e.g. the built-in demo teams in data.py).
_ENGINE_ROLE_EMOJI = (
    ("wk", emojis.WICKETKEEPER),
    ("wicket", emojis.WICKETKEEPER),
    ("all", emojis.ALLROUNDER),
)
_BOWL_TYPE_EMOJI = {
    "Fast": emojis.PACER,
    "Medium Pacer": emojis.MEDIUMPACER,
    "Off Spin": emojis.OFFSPIN,
    "Leg Spin": emojis.LEGSPIN,
    "Orthodox": emojis.OFFSPIN,
}


def _role_emoji(p: dict) -> str:
    if p.get("remoji"):
        return p["remoji"]
    role = str(p.get("role") or "").lower()
    for token, em in _ENGINE_ROLE_EMOJI:
        if token in role:
            return em
    if "bowl" in role or "spin" in role:
        return _BOWL_TYPE_EMOJI.get(p.get("bowling_type") or "", emojis.PACER)
    return emojis.BATTER


def _flag(p: dict) -> str:
    if p.get("flag"):
        return p["flag"]
    try:
        from card_countries import resolve_country
        found = resolve_country(str(p.get("country") or ""))
        return found[1] if found else ""
    except Exception:
        return ""


def entry_from_card(card) -> dict:
    """A card database row -> the same dict shape the match engine uses for a player."""
    import card_db
    import squad_logic as sl
    return {
        "name": card["playername"],
        "country": card["country"],
        "ovr": card["ovr"],
        "bat": card["bat"],
        "bowl": card["bowl"],
        "bowling_type": card_db.effective_bowling_type(card),
        "batting_hand": card["batting_hand"] if "batting_hand" in card.keys() else None,
        "card": card_db.card_emoji(card),
        "remoji": sl.role_emoji(card),
        "flag": card["country_emoji"],
    }


def _clean(text) -> str:
    return str(text).replace("`", "'").replace("\n", " ")


def player_row(p: dict, slot: int | None = None, captain: bool = False) -> str:
    """One XI line. `slot` (1-11) adds the batting position in front."""
    name = _clean(p.get("name", "?"))[:NAME_W]
    bowl = str(p.get("bowl", "-")) if p.get("bowling_type") else "-"
    code = f"{name:<{NAME_W}} {str(p.get('ovr', '?')):>3} {str(p.get('bat', '?')):>3} {bowl:>3}"
    if slot is not None:
        code = f"{slot:>2}  {code}"
    card = p.get("card") or emojis.DEFAULT_CARD_EMOJI
    tail = _flag(p)
    if captain:
        tail = f"{tail} {emojis.CAPTAIN}".strip()
    return f"{card} {_role_emoji(p)} `{code}`" + (f" {tail}" if tail else "")


def empty_row(slot: int) -> str:
    return f"`{slot:>2}  — empty —`"


COLUMNS_NOTE = "Columns: OVR · BAT · BOWL"
