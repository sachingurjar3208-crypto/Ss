"""Game rules for squads and packs (no Discord code here, so it is easy to test).

Player cards come from the /cardmaker database (cards_maker.db). What a user
owns lives in economy.db. This module joins the two.
"""

from __future__ import annotations

import math
import secrets

import card_db
import economy
from card_narratives import ENGINE_ROLE, ROLES

_rng = secrets.SystemRandom()  # unpredictable, so pack results can't be guessed

MIN_BOWLERS = 4   # general "is this XI even playable" floor, independent of
                   # any specific match length — lines up with the new
                   # BOWL(2-4)/AR(2-3) role minimums (2+2 is the smallest
                   # legal combo now). The real, overs-aware requirement is
                   # bowlers_required_for() below — see xi_problem_for_match().


def bowlers_required_for(overs: int) -> int:
    """How many distinct bowling-capable players a team needs to legally
    cover a match of this length, given no bowler may bowl more than
    ceil(overs / 5) overs (same rule the match engine itself enforces via
    GameState.max_bowler_balls()). E.g. 20 overs → 4 overs/bowler max → 5
    bowlers needed; 1 over → 1 bowler needed."""
    overs = max(1, overs)
    max_overs_per_bowler = max(1, math.ceil(overs / 5))
    return max(1, math.ceil(overs / max_overs_per_bowler))


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


# Buy price by OVR (coins). Selling gives back (100 - SELL_CUT_PERCENT)% of this.
BUY_PRICES = {
    99: 28_000_000, 98: 21_000_000, 97: 16_000_000, 96: 12_000_000, 95: 9_000_000,
    94: 7_000_000, 93: 5_500_000, 92: 4_000_000, 91: 3_000_000, 90: 2_100_000,
    89: 1_800_000, 88: 1_250_000, 87: 900_000, 86: 690_000, 85: 500_000,
    84: 350_000, 83: 200_000, 82: 100_000, 81: 60_000, 80: 40_000,
    79: 20_000, 78: 10_000, 77: 7_800, 76: 5_000, 75: 3_000,
    74: 2_500, 73: 2_000, 72: 1_500, 71: 1_000, 70: 900,
    69: 800, 68: 700, 67: 650, 66: 600, 65: 300,
    64: 270, 63: 240, 62: 200, 61: 160, 60: 120,
}
SELL_CUT_PERCENT = 40   # 40% is cut on selling (also used for duplicate refunds)


def buy_price(card) -> int:
    """Price from the BUY_PRICES table. Above 99 uses the 99 price, below 60 the 60 price."""
    ovr = max(min(BUY_PRICES), min(max(BUY_PRICES), int(card["ovr"])))
    return BUY_PRICES[ovr]


def sell_value(card) -> int:
    """Buy price minus the 40% cut. A pack's price is higher than the average
    sell value of what it gives, so buy-pack-then-sell can't make endless profit."""
    return buy_price(card) * (100 - SELL_CUT_PERCENT) // 100


# ── Packs ────────────────────────────────────────────────────────────────────
# Every pack gives exactly ONE player.
#   "range"   = (min ovr, max ovr) of the player you can get
#   "weights" = chance of each ovr. Built with _loss_profit_weights() so that
#               every pack gives a card CHEAPER than the pack price (a loss)
#               70% of the time and a card worth the pack price or more
#               (profit) 30% of the time. The ovr is picked first, then a random
#               player of that ovr, so the odds do not depend on how many
#               players each ovr has.
PACK_LOSS_PERCENT = 70


def _loss_profit_weights(lo: int, hi: int, price: int, relative: dict | None = None) -> dict:
    """ovr -> weight so that P(card buy price < pack price) = PACK_LOSS_PERCENT %.
    `relative` (optional) keeps some ovrs rarer than others inside each group."""
    relative = relative or {}
    loss   = [o for o in range(lo, hi + 1) if BUY_PRICES[o] < price]
    profit = [o for o in range(lo, hi + 1) if BUY_PRICES[o] >= price]
    out = {}
    for group, share in ((loss, PACK_LOSS_PERCENT), (profit, 100 - PACK_LOSS_PERCENT)):
        total = sum(relative.get(o, 1) for o in group)
        for o in group:
            out[o] = share * relative.get(o, 1) / total
    return out


_BRONZE_PRICE, _SILVER_PRICE, _GOLD_PRICE, _LEGEND_PRICE = 1_500, 60_000, 690_000, 4_000_000

PACKS = {
    "bronze": {"label": "Bronze Pack", "emoji": "🟤", "price": _BRONZE_PRICE, "cards": 1,
               "range": (60, 76),
               "weights": _loss_profit_weights(60, 76, _BRONZE_PRICE)},
    "silver": {"label": "Silver Pack", "emoji": "⚪", "price": _SILVER_PRICE, "cards": 1,
               "range": (77, 83),
               "weights": _loss_profit_weights(77, 83, _SILVER_PRICE)},
    "gold":   {"label": "Gold Pack",   "emoji": "🟡", "price": _GOLD_PRICE, "cards": 1,
               "range": (84, 88),
               "weights": _loss_profit_weights(84, 88, _GOLD_PRICE)},
    "legendary": {"label": "Legendary Pack", "emoji": "🔥", "price": _LEGEND_PRICE, "cards": 1,
                  "range": (90, 95),
                  "weights": _loss_profit_weights(90, 95, _LEGEND_PRICE,
                                                  {90: 60, 91: 50, 92: 45, 93: 30, 94: 20, 95: 5})},
}


def _closest_cards(cards: list, lo: int, hi: int) -> list:
    """Cards inside lo..hi; if there are none, the cards closest to that range
    (so a pack never fails just because one ovr has no player yet)."""
    inside = [c for c in cards if lo <= c["ovr"] <= hi]
    if inside:
        return inside
    mid = (lo + hi) / 2
    best = min(abs(c["ovr"] - mid) for c in cards)
    return [c for c in cards if abs(c["ovr"] - mid) == best]


def roll_pack(pack_type: str) -> list:
    """Pick the single card for one pack. Returns a list with 1 card row
    (empty list only if the game has no cards at all)."""
    spec = PACKS[pack_type]
    cards = list(card_db.list_all_cards())
    if not cards:
        return []
    lo, hi = spec["range"]
    weights = spec.get("weights")
    if weights:
        by_ovr: dict[int, list] = {}
        for c in cards:
            if lo <= c["ovr"] <= hi:
                by_ovr.setdefault(int(c["ovr"]), []).append(c)
        # only ovr values that have at least one player can be rolled
        usable = [o for o in weights if o in by_ovr]
        if usable:
            ovr = _rng.choices(usable, weights=[weights[o] for o in usable], k=1)[0]
            return [_rng.choice(by_ovr[ovr])]
    return [_rng.choice(_closest_cards(cards, lo, hi))]


# ── Starter squad and auto XI ────────────────────────────────────────────────

def _bowler_capable(card) -> bool:
    return card_db.effective_bowling_type(card) is not None


# Display/batting order: grouped by role in this order — Batters, then
# All-rounders, then Wicketkeepers, then Bowlers — highest OVR first
# within each group. (Previously this sorted everyone together by bat/ovr,
# so a high-OVR bowler could show up above lower-OVR batters.)
_XI_ROLE_ORDER = ["BAT", "AR", "WK", "BOWL"]


def order_xi(cards: list) -> list:
    """Batting order: grouped by role, highest OVR first within each role."""
    def sort_key(c):
        role = card_db.effective_role(c)
        role_rank = _XI_ROLE_ORDER.index(role) if role in _XI_ROLE_ORDER else len(_XI_ROLE_ORDER)
        return (role_rank, -c["ovr"])
    return sorted(cards, key=sort_key)


# Stat used to rank each role when auto-picking (higher is better for that role).
_ROLE_RANK_STAT = {"WK": "bat", "BAT": "bat", "AR": "ovr", "BOWL": "bowl"}


def best_xi(cards: list) -> list:
    """Best 11 of `cards`, satisfying ROLE_LIMITS when the owned pool allows
    it (WK 1-2, BAT 5-6, AR 1-3, BOWL 3-5), otherwise highest overall.
    Returned in batting order."""
    if len(cards) <= economy.XI_SIZE:
        return order_xi(list(cards))

    by_role: dict[str, list] = {role: [] for role in ROLE_LIMITS}
    for c in cards:
        role = card_db.effective_role(c)
        if role in by_role:
            by_role[role].append(c)
    for role, pool in by_role.items():
        pool.sort(key=lambda c, r=_ROLE_RANK_STAT[role]: -c[r])

    chosen: list = []

    # Pass 1: fill each role's minimum requirement first.
    for role, (lo, _hi) in ROLE_LIMITS.items():
        for c in by_role[role][:lo]:
            chosen.append(c)

    # Pass 2: fill remaining slots with the next-best players overall,
    # never exceeding a role's maximum.
    taken_ids = {id(c) for c in chosen}
    role_counts = {role: sum(1 for c in chosen if card_db.effective_role(c) == role)
                   for role in ROLE_LIMITS}
    leftovers = sorted(
        (c for c in cards if id(c) not in taken_ids),
        key=lambda c: -c["ovr"],
    )
    for c in leftovers:
        if len(chosen) >= economy.XI_SIZE:
            break
        role = card_db.effective_role(c)
        hi = ROLE_LIMITS.get(role, (0, 99))[1]
        if role_counts.get(role, 0) >= hi:
            continue
        chosen.append(c)
        role_counts[role] = role_counts.get(role, 0) + 1

    # Still short of 11 (owned pool too thin in some role) — top up with
    # whatever's left over, ignoring the max cap as a last resort.
    if len(chosen) < economy.XI_SIZE:
        taken_ids = {id(c) for c in chosen}
        for c in sorted((c for c in cards if id(c) not in taken_ids), key=lambda c: -c["ovr"]):
            if len(chosen) >= economy.XI_SIZE:
                break
            chosen.append(c)

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
    keys = economy.owned_keys(user_id)
    found = card_db.get_cards(keys)   # one query instead of one per card
    return [found[k.strip().lower()] for k in keys if k.strip().lower() in found]


def xi_cards(user_id) -> list:
    """Card rows in batting order (slot 1..11); a missing slot is skipped."""
    xi = economy.get_xi(user_id)
    out = []
    for slot in sorted(xi):
        card = card_db.get_card(xi[slot])
        if card is not None:
            out.append(card)
    return out


# Role composition required for a legal Playing XI.
# (min, max) count of each role, by card_db.effective_role().
ROLE_LIMITS = {
    "WK":   (1, 2),
    "BAT":  (3, 4),
    "AR":   (2, 3),
    "BOWL": (2, 4),
}

# BAT is allowed to dip to 2 in practice (match still runs) even though 3
# is the normal minimum everywhere else (auto-XI, the limits above, etc).
BAT_HARD_MIN = 2

XI_ROLE_LABEL = {"WK": "wicketkeeper(s)", "BAT": "batsman/batsmen", "AR": "all-rounder(s)", "BOWL": "bowler(s)"}


def xi_role_counts(cards: list) -> dict[str, int]:
    """{'WK': n, 'BAT': n, 'AR': n, 'BOWL': n} for a list of card rows."""
    counts = {role: 0 for role in ROLE_LIMITS}
    for c in cards:
        role = card_db.effective_role(c)
        if role in counts:
            counts[role] += 1
    return counts


def xi_problem(user_id) -> str | None:
    """Why this user can't play a match yet, or None if their XI is ready."""
    cards = xi_cards(user_id)
    if len(cards) != economy.XI_SIZE:
        return f"has only {len(cards)}/{economy.XI_SIZE} players in the XI"

    counts = xi_role_counts(cards)
    for role, (lo, hi) in ROLE_LIMITS.items():
        n = counts[role]
        # BAT specifically allows 2 as a hard floor — a match can still be
        # played with 2 batters even though 3 is the normal minimum.
        effective_lo = BAT_HARD_MIN if role == "BAT" else lo
        if n < effective_lo:
            return f"needs at least {effective_lo} {XI_ROLE_LABEL[role]} in the XI (has {n})"
        if n > hi:
            return f"can have at most {hi} {XI_ROLE_LABEL[role]} in the XI (has {n})"
    bowlers = sum(1 for c in cards if card_db.effective_bowling_type(c))
    if bowlers < MIN_BOWLERS:
        return (f"needs at least {MIN_BOWLERS} players who can bowl (bowlers / all-rounders) "
                f"in the XI (has {bowlers})")
    return None


def xi_problem_for_match(user_id, overs: int) -> str | None:
    """Like xi_problem(), but also checks this user's XI has enough distinct
    bowling options to legally cover a match of this specific length (no
    bowler may exceed ceil(overs/5) overs). xi_problem() alone can't catch
    this because it has no idea how many overs the match being set up is —
    a 4-bowler XI is fine for a 1-over match but not for a 20-over one."""
    base = xi_problem(user_id)
    if base:
        return base
    cards = xi_cards(user_id)
    bowlers = sum(1 for c in cards if card_db.effective_bowling_type(c))
    needed = bowlers_required_for(overs)
    if bowlers < needed:
        return (f"needs at least {needed} bowling option(s) (bowlers / all-rounders) "
                f"for a {overs}-over match (has {bowlers})")
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


def impact_sub_cards(user_id) -> list:
    """Card rows for this user's nominated Impact Player subs, in slot order."""
    subs = economy.get_impact_subs(user_id)
    out = []
    for slot in sorted(subs):
        card = card_db.get_card(subs[slot])
        if card is not None:
            out.append(card)
    return out


def build_impact_subs_players(user_id) -> list[dict]:
    """Same per-player dict shape as build_match_team()'s players list, for
    this user's nominated Impact Player subs. Used to fill the bench pool a
    live match draws from when the Impact Player button is used."""
    cards = impact_sub_cards(user_id)
    return [
        {
            "name": c["playername"],
            "country": c["country"],
            "ovr": c["ovr"],
            "bat": c["bat"],
            "bowl": c["bowl"],
            "bowling_type": card_db.effective_bowling_type(c),
            "role": ENGINE_ROLE[card_db.effective_role(c)],
            "card": card_emoji(c),
        }
        for c in cards
    ]


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
