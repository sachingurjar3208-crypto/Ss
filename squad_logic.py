"""Game rules for squads and packs (no Discord code here, so it is easy to test).

Player cards come from the /cardmaker database (cards_maker.db). What a user
owns lives in economy.db. This module joins the two.
"""

from __future__ import annotations

import secrets

import card_db
import economy
from card_narratives import ENGINE_ROLE, ROLES

_rng = secrets.SystemRandom()  # unpredictable, so pack results can't be guessed

MIN_BOWLERS = 5   # the match engine needs 5 bowlers to finish the overs


# ── Tiers and prices ─────────────────────────────────────────────────────────

def tier_of(ovr: int) -> str:
    if ovr >= 88:
        return "elite"
    if ovr >= 75:
        return "rare"
    return "common"


TIER_EMOJI = {"elite": "🥇", "rare": "🥈", "common": "🥉"}


def card_emoji(card) -> str:
    return TIER_EMOJI[tier_of(card["ovr"])]


# Custom role emojis (uploaded in the Discord developer portal, so the bot can
# use them in any server). Pick by role + bowling type.
ROLE_EMOJI = {
    "WK":       "<:Wicketkeeper:1518961376884822087>",
    "BAT":      "<:Batsman:1518862731103436900>",
    "AR":       "<:AllRounder2:1518961183963484272>",
    "Fast":     "<:FastBowler3:1518961249826766879>",
    "Off Spin": "<:Offspin4:1518961309981610145>",
    "Leg Spin": "<:Legspinner:1518862597208801502>",
    "BOWL":     "<:Normalnall:1518862733423022090>",   # bowler, unknown type
}


def role_emoji(card) -> str:
    """Emoji for a card's role: keeper / bat / all-rounder / bowling type."""
    role = card_db.effective_role(card)
    if role in ("WK", "BAT", "AR"):
        return ROLE_EMOJI[role]
    return ROLE_EMOJI.get(card_db.effective_bowling_type(card), ROLE_EMOJI["BOWL"])


def buy_price(card) -> int:
    """Price grows fast with rating: ovr 60 ~ 52k, 80 ~ 164k, 96 ~ 340k."""
    return max(5_000, int((card["ovr"] / 100) ** 4 * 400_000) // 500 * 500)


# Resale (selling a card, or a duplicate from a pack) is a small FIXED amount per
# tier. It must stay well below what a pack costs, otherwise people could buy
# packs and sell the cards back for endless profit. The test in
# test_all.py checks this for every pack.
SELL_VALUE = {"common": 1_000, "rare": 4_000, "elite": 12_000}


def sell_value(card) -> int:
    return SELL_VALUE[tier_of(card["ovr"])]


# ── Packs ────────────────────────────────────────────────────────────────────
# weights = chance of a card from each tier. `guarantee` = at least one card
# of that tier (if the game has such cards).
PACKS = {
    "bronze": {"label": "Bronze Pack", "emoji": "🟤", "price": 10_000, "cards": 3,
               "weights": {"common": 70, "rare": 27, "elite": 3}, "guarantee": None},
    "silver": {"label": "Silver Pack", "emoji": "⚪", "price": 40_000, "cards": 3,
               "weights": {"common": 35, "rare": 55, "elite": 10}, "guarantee": "rare"},
    "gold":   {"label": "Gold Pack",   "emoji": "🟡", "price": 100_000, "cards": 3,
               "weights": {"common": 5, "rare": 55, "elite": 40}, "guarantee": "elite"},
}


def _pool_by_tier() -> dict[str, list]:
    tiers: dict[str, list] = {"common": [], "rare": [], "elite": []}
    for c in card_db.list_all_cards():
        tiers[tier_of(c["ovr"])].append(c)
    return tiers


def roll_pack(pack_type: str) -> list:
    """Pick the cards for one pack. Returns card rows (may repeat a card)."""
    spec = PACKS[pack_type]
    tiers = _pool_by_tier()
    if not any(tiers.values()):
        return []
    names = [t for t in ("common", "rare", "elite") if tiers[t]]
    picks: list = []
    if spec["guarantee"] and tiers[spec["guarantee"]]:
        picks.append(_rng.choice(tiers[spec["guarantee"]]))
    while len(picks) < spec["cards"]:
        weights = [spec["weights"][t] for t in names]
        tier = _rng.choices(names, weights=weights, k=1)[0]
        picks.append(_rng.choice(tiers[tier]))
    _rng.shuffle(picks)
    return picks


# ── Starter squad and auto XI ────────────────────────────────────────────────

def _bowler_capable(card) -> bool:
    return card_db.effective_bowling_type(card) is not None


def order_xi(cards: list) -> list:
    """Batting order: best batters first."""
    return sorted(cards, key=lambda c: (-c["bat"], -c["ovr"]))


def best_xi(cards: list) -> list:
    """Best 11 of `cards`: at least MIN_BOWLERS bowlers and (if possible) a
    wicketkeeper, otherwise highest overall. Returned in batting order."""
    if len(cards) <= economy.XI_SIZE:
        return order_xi(list(cards))
    remaining = sorted(cards, key=lambda c: -c["ovr"])
    chosen: list = []

    def take(card):
        chosen.append(card)
        remaining.remove(card)

    keepers = [c for c in remaining if card_db.effective_role(c) == "WK"]
    if keepers:
        take(keepers[0])
    bowlers = sorted((c for c in remaining if _bowler_capable(c)), key=lambda c: -c["bowl"])
    have = sum(1 for c in chosen if _bowler_capable(c))
    for c in bowlers:
        if have >= MIN_BOWLERS:
            break
        take(c)
        have += 1
    for c in list(remaining):
        if len(chosen) >= economy.XI_SIZE:
            break
        take(c)
    return order_xi(chosen)


def pick_starter_squad() -> list | None:
    """13 modest cards (no top stars): 5 batters, 1 keeper, 2 all-rounders,
    5 bowlers. Falls back to any cards if the pool is small. None if there
    aren't even 11 cards in the game yet."""
    cards = card_db.list_all_cards()
    if len(cards) < economy.XI_SIZE:
        return None
    modest = [c for c in cards if c["ovr"] <= 82] or list(cards)
    want = {"BAT": 5, "WK": 1, "AR": 2, "BOWL": 5}
    chosen: list = []
    for role, n in want.items():
        pool = [c for c in modest if card_db.effective_role(c) == role and c not in chosen]
        _rng.shuffle(pool)
        chosen.extend(pool[:n])
    if len(chosen) < 13:
        rest = [c for c in cards if c not in chosen]
        rest.sort(key=lambda c: (abs(c["ovr"] - 72), _rng.random()))
        chosen.extend(rest[: 13 - len(chosen)])
    return chosen[:13] if len(chosen) >= economy.XI_SIZE else None


# ── Reading a user's squad ───────────────────────────────────────────────────

def owned_cards(user_id) -> list:
    """Card rows for everything the user owns (players removed from the card
    database are skipped)."""
    out = []
    for key in economy.owned_keys(user_id):
        card = card_db.get_card(key)
        if card is not None:
            out.append(card)
    return out


def xi_cards(user_id) -> list:
    """Card rows in batting order (slot 1..11); a missing slot is skipped."""
    xi = economy.get_xi(user_id)
    out = []
    for slot in sorted(xi):
        card = card_db.get_card(xi[slot])
        if card is not None:
            out.append(card)
    return out


def xi_problem(user_id) -> str | None:
    """Why this user can't play a match yet, or None if their XI is ready."""
    cards = xi_cards(user_id)
    if len(cards) != economy.XI_SIZE:
        return f"has only {len(cards)}/{economy.XI_SIZE} players in the XI"
    bowlers = sum(1 for c in cards if _bowler_capable(c))
    if bowlers < MIN_BOWLERS:
        return f"needs at least {MIN_BOWLERS} bowlers in the XI (has {bowlers})"
    return None


def average_ovr(cards: list) -> int:
    return round(sum(c["ovr"] for c in cards) / len(cards)) if cards else 0


def build_match_team(user_id) -> dict:
    """The team dict the match engine expects, built from the user's XI.
    Player 'name' is the card's full name, which is how card narratives
    (Swing King, Chase Master ...) find their effects during the match."""
    user = economy.get_user(user_id)
    cards = xi_cards(user_id)
    players = []
    for c in cards:
        players.append({
            "name": c["playername"],
            "country": c["country"],
            "ovr": c["ovr"],
            "bat": c["bat"],
            "bowl": c["bowl"],
            "bowling_type": card_db.effective_bowling_type(c),
            "role": ENGINE_ROLE[card_db.effective_role(c)],
            "card": card_emoji(c),
        })
    countries: dict[str, int] = {}
    for c in cards:
        countries[c["country"]] = countries.get(c["country"], 0) + 1
    chem = min(99, 70 + 3 * max(countries.values(), default=0))
    captain = None
    if user is not None and user["captain_key"]:
        cap = card_db.get_card(user["captain_key"])
        captain = cap["playername"] if cap else None
    return {
        "name": user["team_name"] if user else "Team",
        "ovr": average_ovr(cards),
        "chem": chem,
        "players": players,
        "captain": captain,
    }


# ── Finding a player by what the user typed ──────────────────────────────────

def resolve_owned(user_id, text: str):
    """Find one owned card from typed text.
    Returns (card, None) or (None, error message)."""
    query = text.strip()
    if not query:
        return None, "Type a player name."
    owned = {c["playername_key"]: c for c in owned_cards(user_id)}
    q = query.lower()
    if q in owned:
        return owned[q], None
    matches = [c for k, c in owned.items() if q in k]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"You don't own a player matching `{discord_safe(query)}`."
    names = ", ".join(c["playername"] for c in matches[:6])
    return None, f"More than one match: {names}. Type a bit more of the name."


def resolve_any(text: str):
    """Find one card (owned or not) from typed text."""
    found = card_db.find_cards(text, limit=8)
    if not found:
        return None, f"No player found matching `{discord_safe(text)}`."
    if len(found) == 1 or found[0]["playername_key"] == text.strip().lower():
        return found[0], None
    names = ", ".join(c["playername"] for c in found[:6])
    return None, f"More than one match: {names}. Type a bit more of the name."


def discord_safe(text: str) -> str:
    return str(text).replace("`", "'").replace("@", "@\u200b")[:40]


ROLE_LABEL = ROLES
