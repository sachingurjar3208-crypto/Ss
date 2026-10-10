"""Coins, owned player cards, playing XI, packs and bans (economy.db).

This is also the module views.py already looks for: it calls
add_coins(), get_balance(), fmt_coins() and reward_match_winner() from here
(e.g. the fine for leaving a match, and the coins for winning one).

SAFETY RULES used everywhere in this file
- Every query is parameterised (no string-built SQL), so names typed by users
  can never change a query.
- Every change that touches more than one row runs inside ONE transaction
  opened with BEGIN IMMEDIATE. SQLite lets only one such writer in at a time,
  so two people (or one person spamming a command) can never double-spend
  coins or duplicate a card.
- Coins are integers, capped at MAX_PURSE, and spending checks the balance
  inside the same statement that deducts it.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "economy.db"

# ── Tunable numbers ──────────────────────────────────────────────────────────
START_PURSE   = 50_000
MAX_PURSE     = 2_000_000_000
DAILY_BASE    = 5_000
# Streak bonus (daily only): paid on every 7th streak day (7, 14, 21 ...) on top of the
# normal daily coins. Every 50th day is a milestone: 3x the bonus at 50, 6x at 100, 9x at 150 ...
STREAK_BONUS          = 10_000
STREAK_EVERY          = 7
STREAK_MILESTONE_EVERY = 50
STREAK_MILESTONE_MULT  = 3      # per STREAK_MILESTONE_EVERY days
WEEKLY_REWARD = 30_000
MONTHLY_REWARD = 100_000
WIN_REWARD    = 5_000
LOSS_REWARD   = 1_500
MATCH_REWARD_DAILY_CAP   = 10   # rewarded matches per player per 24h
MATCH_REWARD_PER_OPP_CAP = 3    # ... and against the same opponent per 24h (stops alt-account farming)
MIN_SQUAD     = 11               # you can never sell below this many players
XI_SIZE       = 11
IMPACT_SUBS_MAX = 4              # how many bench players a team can nominate as Impact Player subs

DAY = 86_400
COOLDOWNS = {"daily": DAY, "weekly": 7 * DAY, "monthly": 30 * DAY}
MONTHLY_UNLOCK_AFTER_DEBUT = 30 * DAY   # monthly reward opens 1 month after debut

# Every daily / weekly / monthly claim also gives one random player card whose
# overall rating (ovr) falls in this inclusive range.
REWARD_CARD_OVR = {"daily": (60, 78), "weekly": (80, 83), "monthly": (85, 88)}

_rng = secrets.SystemRandom()


def streak_bonus(streak: int) -> int:
    """Extra coins for reaching `streak` days in a row (0 on ordinary days)."""
    if streak > 0 and streak % STREAK_MILESTONE_EVERY == 0:
        return STREAK_BONUS * STREAK_MILESTONE_MULT * (streak // STREAK_MILESTONE_EVERY)
    if streak > 0 and streak % STREAK_EVERY == 0:
        return STREAK_BONUS
    return 0


def fmt_coins(n: int) -> str:
    return f"{int(n):,} <:CSCoin:1558139683702055062>"


# ── Connection / transaction helpers ────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)  # manual transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def _tx():
    """One atomic transaction: everything inside is saved together or not at all."""
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _uid(user_id) -> int:
    return int(user_id)


def init_economy_db() -> None:
    conn = _conn()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                team_name       TEXT    NOT NULL,
                purse           INTEGER NOT NULL DEFAULT 0,
                captain_key     TEXT,
                starter_claimed INTEGER NOT NULL DEFAULT 0,
                last_daily      REAL    NOT NULL DEFAULT 0,
                daily_streak    INTEGER NOT NULL DEFAULT 0,
                last_weekly     REAL    NOT NULL DEFAULT 0,
                last_monthly    REAL    NOT NULL DEFAULT 0,
                banned          INTEGER NOT NULL DEFAULT 0,
                ban_reason      TEXT,
                created_at      REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owned (
                user_id     INTEGER NOT NULL,
                player_key  TEXT    NOT NULL,
                acquired_at REAL    NOT NULL,
                PRIMARY KEY (user_id, player_key)
            );
            CREATE TABLE IF NOT EXISTS xi (
                user_id    INTEGER NOT NULL,
                slot       INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 11),
                player_key TEXT    NOT NULL,
                PRIMARY KEY (user_id, slot),
                UNIQUE (user_id, player_key)
            );
            CREATE TABLE IF NOT EXISTS packs (
                user_id   INTEGER NOT NULL,
                pack_type TEXT    NOT NULL,
                qty       INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
                PRIMARY KEY (user_id, pack_type)
            );
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER PRIMARY KEY,
                reason  TEXT,
                ts      REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reward_log (
                user_id INTEGER NOT NULL,
                opp_id  INTEGER NOT NULL,
                ts      REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reward_log ON reward_log (user_id, ts);
            CREATE TABLE IF NOT EXISTS ledger (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                delta         INTEGER NOT NULL,
                reason        TEXT,
                balance_after INTEGER NOT NULL,
                ts            REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger (user_id, ts);
            CREATE TABLE IF NOT EXISTS impact_subs (
                user_id    INTEGER NOT NULL,
                slot       INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 4),
                player_key TEXT    NOT NULL,
                PRIMARY KEY (user_id, slot),
                UNIQUE (user_id, player_key)
            );
            """
        )
    finally:
        conn.close()


def _ensure_impact_subs_table(conn: sqlite3.Connection) -> None:
    """Defensive: create the table on first use too, in case
    init_economy_db() wasn't run for this deployment."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS impact_subs (
            user_id    INTEGER NOT NULL,
            slot       INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 4),
            player_key TEXT    NOT NULL,
            PRIMARY KEY (user_id, slot),
            UNIQUE (user_id, player_key)
        )"""
    )


def _sync_impact_subs_with_xi(conn: sqlite3.Connection, uid: int) -> None:
    """A player who just entered the XI can't still be a bench sub — drop
    them from the Impact Player subs list if they're there."""
    _ensure_impact_subs_table(conn)
    xi = set(_xi_rows(conn, uid).values())
    if not xi:
        return
    subs = conn.execute("SELECT player_key FROM impact_subs WHERE user_id = ?", (uid,)).fetchall()
    for r in subs:
        if r["player_key"] in xi:
            conn.execute(
                "DELETE FROM impact_subs WHERE user_id = ? AND player_key = ?", (uid, r["player_key"])
            )


# ── Users ───────────────────────────────────────────────────────────────────

def get_user(user_id) -> sqlite3.Row | None:
    conn = _conn()
    try:
        return conn.execute("SELECT * FROM users WHERE user_id = ?", (_uid(user_id),)).fetchone()
    finally:
        conn.close()


def user_exists(user_id) -> bool:
    return get_user(user_id) is not None


def create_user(user_id, team_name: str) -> bool:
    """Register a new player. Returns False if they already debuted."""
    with _tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (user_id, team_name, purse, created_at) VALUES (?, ?, ?, ?)",
            (_uid(user_id), team_name, START_PURSE, time.time()),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO ledger (user_id, delta, reason, balance_after, ts) VALUES (?, ?, ?, ?, ?)",
            (_uid(user_id), START_PURSE, "Debut bonus", START_PURSE, time.time()),
        )
        return True


def set_team_name(user_id, team_name: str) -> None:
    with _tx() as conn:
        conn.execute("UPDATE users SET team_name = ? WHERE user_id = ?", (team_name, _uid(user_id)))


def list_users() -> list[sqlite3.Row]:
    conn = _conn()
    try:
        return conn.execute("SELECT * FROM users").fetchall()
    finally:
        conn.close()


# ── Coins ───────────────────────────────────────────────────────────────────

def get_balance(user_id) -> int:
    row = get_user(user_id)
    return int(row["purse"]) if row else 0


def _apply_coins(conn: sqlite3.Connection, uid: int, amount: int, reason: str) -> int | None:
    """Add (or subtract) coins inside an open transaction. Returns the new
    balance, or None if the user doesn't exist."""
    row = conn.execute("SELECT purse FROM users WHERE user_id = ?", (uid,)).fetchone()
    if row is None:
        return None
    new_balance = min(MAX_PURSE, int(row["purse"]) + int(amount))
    conn.execute("UPDATE users SET purse = ? WHERE user_id = ?", (new_balance, uid))
    conn.execute(
        "INSERT INTO ledger (user_id, delta, reason, balance_after, ts) VALUES (?, ?, ?, ?, ?)",
        (uid, int(amount), (reason or "")[:120], new_balance, time.time()),
    )
    return new_balance


def add_coins(user_id, amount: int, reason: str = "") -> int:
    """Add coins (negative amount = fine; the balance may go below zero).
    Returns the new balance, or 0 if the user has not debuted."""
    with _tx() as conn:
        result = _apply_coins(conn, _uid(user_id), int(amount), reason)
        return 0 if result is None else result


def reward_match_winner(winner_id, loser_id) -> None:
    """Called by views.py when a match finishes. Coins are paid only up to
    MATCH_REWARD_DAILY_CAP matches a day, and MATCH_REWARD_PER_OPP_CAP per
    opponent, so two accounts can't farm coins by playing each other."""
    w, l = _uid(winner_id), _uid(loser_id)
    if w == l:
        return
    since = time.time() - DAY
    with _tx() as conn:
        for uid, opp, amount, reason in ((w, l, WIN_REWARD, "Match win"), (l, w, LOSS_REWARD, "Match played")):
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM reward_log WHERE user_id = ? AND ts > ?", (uid, since)
            ).fetchone()["n"]
            pair = conn.execute(
                "SELECT COUNT(*) AS n FROM reward_log WHERE user_id = ? AND opp_id = ? AND ts > ?",
                (uid, opp, since),
            ).fetchone()["n"]
            if total >= MATCH_REWARD_DAILY_CAP or pair >= MATCH_REWARD_PER_OPP_CAP:
                continue
            conn.execute("INSERT INTO reward_log (user_id, opp_id, ts) VALUES (?, ?, ?)", (uid, opp, time.time()))
            _apply_coins(conn, uid, amount, reason)
        conn.execute("DELETE FROM reward_log WHERE ts < ?", (time.time() - 3 * DAY,))


# ── Rewards (daily / weekly / monthly) ──────────────────────────────────────

def claim_reward(user_id, kind: str, card_pool: list[str] | None = None,
                 dupe_values: dict[str, int] | None = None) -> dict:
    """Claim a daily/weekly/monthly reward atomically: coins + one random card
    from `card_pool` (player keys already filtered to the right ovr range).
    A card the user already owns is skipped if they have any unowned one left in
    the pool; otherwise it turns into coins using `dupe_values`.
    Returns {"ok": True, "coins", "base", "bonus", "balance", "streak",
             "card": key|None, "dupe": bool, "refund": n}
    or {"ok": False, "wait": seconds_left}."""
    if kind not in COOLDOWNS:
        raise ValueError("unknown reward kind")
    uid = _uid(user_id)
    col = f"last_{kind}"  # column name comes from the fixed COOLDOWNS keys, never from user input
    now = time.time()
    with _tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            return {"ok": False, "wait": 0, "missing": True}
        if kind == "monthly":
            unlock_left = MONTHLY_UNLOCK_AFTER_DEBUT - (now - float(row["created_at"]))
            if unlock_left > 0:
                return {"ok": False, "wait": int(unlock_left) + 1, "locked": True}
        left = COOLDOWNS[kind] - (now - float(row[col]))
        if left > 0:
            return {"ok": False, "wait": int(left) + 1}
        streak = int(row["daily_streak"])
        base, bonus = 0, 0
        if kind == "daily":
            # Next claim must come within 24h after the 24h cooldown ends, else the streak restarts at 1.
            last = float(row["last_daily"])
            streak = streak + 1 if last > 0 and now - last <= 2 * DAY else 1
            base, bonus = DAILY_BASE, streak_bonus(streak)
            conn.execute("UPDATE users SET daily_streak = ? WHERE user_id = ?", (streak, uid))
        elif kind == "weekly":
            base = WEEKLY_REWARD
        else:
            base = MONTHLY_REWARD
        coins = base + bonus
        conn.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (now, uid))
        balance = _apply_coins(conn, uid, coins, f"{kind} reward" + (f" (streak {streak} bonus)" if bonus else ""))
        card_key, dupe, refund = None, False, 0
        pool = list(card_pool or [])
        if pool:
            owned = {r["player_key"] for r in conn.execute("SELECT player_key FROM owned WHERE user_id = ?", (uid,))}
            fresh = [k for k in pool if k not in owned]
            if fresh:
                card_key = _rng.choice(fresh)
                conn.execute(
                    "INSERT INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)", (uid, card_key, now)
                )
                _fill_empty_slots(conn, uid, [card_key])
            else:
                card_key, dupe = _rng.choice(pool), True
                refund = int((dupe_values or {}).get(card_key, 0))
                if refund:
                    balance = _apply_coins(conn, uid, refund, f"Duplicate refund ({kind} reward)")
        return {"ok": True, "coins": coins, "base": base, "bonus": bonus, "balance": balance,
                "streak": streak, "card": card_key, "dupe": dupe, "refund": refund}


# ── Owned cards ─────────────────────────────────────────────────────────────

def owned_keys(user_id) -> list[str]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT player_key FROM owned WHERE user_id = ? ORDER BY acquired_at", (_uid(user_id),)
        ).fetchall()
        return [r["player_key"] for r in rows]
    finally:
        conn.close()


def owns(user_id, player_key: str) -> bool:
    conn = _conn()
    try:
        return conn.execute(
            "SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (_uid(user_id), player_key)
        ).fetchone() is not None
    finally:
        conn.close()


def _xi_rows(conn: sqlite3.Connection, uid: int) -> dict[int, str]:
    rows = conn.execute("SELECT slot, player_key FROM xi WHERE user_id = ?", (uid,)).fetchall()
    return {r["slot"]: r["player_key"] for r in rows}


def _fill_empty_slots(conn: sqlite3.Connection, uid: int, keys: list[str]) -> None:
    """Put new players into the first empty XI slots, the rest stay on the bench."""
    taken = _xi_rows(conn, uid)
    free = [s for s in range(1, XI_SIZE + 1) if s not in taken]
    for slot, key in zip(free, keys):
        conn.execute("INSERT INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)", (uid, slot, key))


# ── XI / captain ────────────────────────────────────────────────────────────

def get_xi(user_id) -> dict[int, str]:
    """{slot: player_key} for slots 1..11 (a slot can be missing)."""
    conn = _conn()
    try:
        return _xi_rows(conn, _uid(user_id))
    finally:
        conn.close()


def set_full_xi(user_id, keys: list[str]) -> bool:
    """Replace the whole XI with `keys` (in batting order). All must be owned.
    Keys are matched ignoring case/spaces, and the key actually stored in
    `owned` is what goes into the XI."""
    uid = _uid(user_id)
    norm = [str(k).strip().lower() for k in keys]
    if len(keys) > XI_SIZE or len(set(norm)) != len(norm):
        return False
    with _tx() as conn:
        owned = {
            str(r["player_key"]).strip().lower(): r["player_key"]
            for r in conn.execute("SELECT player_key FROM owned WHERE user_id = ?", (uid,))
        }
        stored = []
        for k in norm:
            if k not in owned:
                return False
            stored.append(owned[k])
        conn.execute("DELETE FROM xi WHERE user_id = ?", (uid,))
        for slot, k in enumerate(stored, start=1):
            conn.execute("INSERT INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)", (uid, slot, k))
        cap = conn.execute("SELECT captain_key FROM users WHERE user_id = ?", (uid,)).fetchone()
        if cap is not None and cap["captain_key"] not in stored:
            conn.execute("UPDATE users SET captain_key = NULL WHERE user_id = ?", (uid,))
        _sync_impact_subs_with_xi(conn, uid)
        return True


def swap_players(user_id, key_a: str, key_b: str) -> str | None:
    """Swap two owned players. Both in XI -> they trade batting positions.
    One in XI, one on the bench -> the bench player takes the XI spot.
    Returns None on success or an error message."""
    uid = _uid(user_id)
    if key_a == key_b:
        return "Pick two different players."
    with _tx() as conn:
        for k in (key_a, key_b):
            if conn.execute("SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (uid, k)).fetchone() is None:
                return "You don't own one of those players."
        xi = _xi_rows(conn, uid)
        slot_of = {v: s for s, v in xi.items()}
        sa, sb = slot_of.get(key_a), slot_of.get(key_b)
        if sa is None and sb is None:
            return "Both players are on the bench — at least one must be in your XI."
        # Remove first, then re-insert, so the UNIQUE(player_key) rule never trips halfway.
        if sa is not None:
            conn.execute("DELETE FROM xi WHERE user_id = ? AND slot = ?", (uid, sa))
        if sb is not None:
            conn.execute("DELETE FROM xi WHERE user_id = ? AND slot = ?", (uid, sb))
        if sa is not None:
            conn.execute("INSERT INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)", (uid, sa, key_b))
        if sb is not None:
            conn.execute("INSERT INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)", (uid, sb, key_a))
        cap = conn.execute("SELECT captain_key FROM users WHERE user_id = ?", (uid,)).fetchone()
        if cap is not None and cap["captain_key"] in (key_a, key_b):
            # If the captain just moved to the bench, the captaincy is cleared.
            new_xi = set(_xi_rows(conn, uid).values())
            if cap["captain_key"] not in new_xi:
                conn.execute("UPDATE users SET captain_key = NULL WHERE user_id = ?", (uid,))
        _sync_impact_subs_with_xi(conn, uid)
        return None


def set_captain(user_id, player_key: str) -> str | None:
    uid = _uid(user_id)
    with _tx() as conn:
        if player_key not in _xi_rows(conn, uid).values():
            return "Your captain must be in your playing XI."
        conn.execute("UPDATE users SET captain_key = ? WHERE user_id = ?", (player_key, uid))
        return None


# ── Buying / selling / packs / starter ──────────────────────────────────────

def buy_card(user_id, player_key: str, price: int) -> str | None:
    """Buy one card. Returns None on success or an error message."""
    uid = _uid(user_id)
    with _tx() as conn:
        row = conn.execute("SELECT purse FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            return "You haven't debuted yet."
        if conn.execute("SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (uid, player_key)).fetchone():
            return "You already own this player."
        if int(row["purse"]) < price:
            return "Not enough coins."
        _apply_coins(conn, uid, -price, f"Bought {player_key}")
        conn.execute(
            "INSERT INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)", (uid, player_key, time.time())
        )
        _fill_empty_slots(conn, uid, [player_key])
        return None


def sell_card(user_id, player_key: str, value: int, replacement_key: str | None) -> str | None:
    """Sell one card for `value` coins. If the player was in the XI, the
    `replacement_key` (a bench player) takes their batting spot."""
    uid = _uid(user_id)
    with _tx() as conn:
        if conn.execute("SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (uid, player_key)).fetchone() is None:
            return "You don't own that player."
        count = conn.execute("SELECT COUNT(*) AS n FROM owned WHERE user_id = ?", (uid,)).fetchone()["n"]
        if count <= MIN_SQUAD:
            return f"You must keep at least {MIN_SQUAD} players."
        slot_row = conn.execute("SELECT slot FROM xi WHERE user_id = ? AND player_key = ?", (uid, player_key)).fetchone()
        conn.execute("DELETE FROM xi WHERE user_id = ? AND player_key = ?", (uid, player_key))
        conn.execute("DELETE FROM owned WHERE user_id = ? AND player_key = ?", (uid, player_key))
        conn.execute("UPDATE users SET captain_key = NULL WHERE user_id = ? AND captain_key = ?", (uid, player_key))
        _ensure_impact_subs_table(conn)
        conn.execute("DELETE FROM impact_subs WHERE user_id = ? AND player_key = ?", (uid, player_key))
        if slot_row is not None and replacement_key:
            ok = conn.execute(
                "SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (uid, replacement_key)
            ).fetchone()
            in_xi = conn.execute(
                "SELECT 1 FROM xi WHERE user_id = ? AND player_key = ?", (uid, replacement_key)
            ).fetchone()
            if ok and not in_xi:
                conn.execute(
                    "INSERT INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)",
                    (uid, slot_row["slot"], replacement_key),
                )
        _apply_coins(conn, uid, value, f"Sold {player_key}")
        return None


def add_pack(user_id, pack_type: str, qty: int = 1) -> None:
    with _tx() as conn:
        conn.execute(
            "INSERT INTO packs (user_id, pack_type, qty) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, pack_type) DO UPDATE SET qty = qty + excluded.qty",
            (_uid(user_id), pack_type, int(qty)),
        )


def get_packs(user_id) -> dict[str, int]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT pack_type, qty FROM packs WHERE user_id = ? AND qty > 0", (_uid(user_id),)
        ).fetchall()
        return {r["pack_type"]: int(r["qty"]) for r in rows}
    finally:
        conn.close()


def buy_pack(user_id, pack_type: str, price: int) -> str | None:
    uid = _uid(user_id)
    with _tx() as conn:
        row = conn.execute("SELECT purse FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            return "You haven't debuted yet."
        if int(row["purse"]) < price:
            return "Not enough coins."
        _apply_coins(conn, uid, -price, f"Bought {pack_type} pack")
        conn.execute(
            "INSERT INTO packs (user_id, pack_type, qty) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, pack_type) DO UPDATE SET qty = qty + 1",
            (uid, pack_type),
        )
        return None


def open_pack(user_id, pack_type: str, keys: list[str], dupe_values: dict[str, int]) -> dict | str:
    """Use up one pack and hand over `keys`. Cards the user already owns are
    turned into coins using `dupe_values`. Returns {"new": [...], "dupes": [...],
    "refund": n} or an error message."""
    uid = _uid(user_id)
    with _tx() as conn:
        cur = conn.execute(
            "UPDATE packs SET qty = qty - 1 WHERE user_id = ? AND pack_type = ? AND qty > 0",
            (uid, pack_type),
        )
        if cur.rowcount == 0:
            return "You don't have that pack."
        new, dupes, refund = [], [], 0
        for k in keys:
            if k in new:
                dupes.append(k)
                refund += dupe_values.get(k, 0)
                continue
            got = conn.execute(
                "INSERT OR IGNORE INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)",
                (uid, k, time.time()),
            )
            if got.rowcount:
                new.append(k)
            else:
                dupes.append(k)
                refund += dupe_values.get(k, 0)
        _fill_empty_slots(conn, uid, new)
        if refund:
            _apply_coins(conn, uid, refund, f"Duplicate refund ({pack_type} pack)")
        return {"new": new, "dupes": dupes, "refund": refund}


def claim_starter(user_id, keys: list[str], xi_order: list[str]) -> str | None:
    """Give the one-time starter squad and set the XI. None on success."""
    uid = _uid(user_id)
    with _tx() as conn:
        row = conn.execute("SELECT starter_claimed FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            return "You haven't debuted yet."
        if row["starter_claimed"]:
            return "You already claimed your starter pack."
        now = time.time()
        for k in keys:
            conn.execute(
                "INSERT OR IGNORE INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)", (uid, k, now)
            )
        conn.execute("DELETE FROM xi WHERE user_id = ?", (uid,))
        for slot, k in enumerate(xi_order[:XI_SIZE], start=1):
            conn.execute("INSERT INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)", (uid, slot, k))
        conn.execute("UPDATE users SET starter_claimed = 1 WHERE user_id = ?", (uid,))
        return None


def reset_cooldowns(user_id, starter: bool = False, weekly: bool = False, daily: bool = False) -> dict | None:
    """Owner tool (/cooldown reset). Clears the chosen cooldowns for one user.
    None if the user hasn't debuted. Otherwise {"starter", "weekly", "daily"} -> True if reset.

    - starter: lets the user claim csstarterpack again.
    - weekly:  csweekly is available right now.
    - daily:   csdaily is available right now. The streak is KEPT: the 24h cooldown is
               ended but the claim still falls inside the 48h streak window.
    """
    uid = _uid(user_id)
    done = {"starter": False, "weekly": False, "daily": False}
    with _tx() as conn:
        row = conn.execute("SELECT last_daily FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            return None
        if starter:
            conn.execute("UPDATE users SET starter_claimed = 0 WHERE user_id = ?", (uid,))
            done["starter"] = True
        if weekly:
            conn.execute("UPDATE users SET last_weekly = 0 WHERE user_id = ?", (uid,))
            done["weekly"] = True
        if daily:
            last = float(row["last_daily"])
            if last > 0:   # never claimed before = nothing to reset (and no streak to lose)
                conn.execute(
                    "UPDATE users SET last_daily = ? WHERE user_id = ?",
                    (min(last, time.time() - DAY - 1), uid),
                )
            done["daily"] = True
    return done


# ── Trading ─────────────────────────────────────────────────────────────────

def execute_trade(a_id, a_key: str, b_id, b_key: str) -> str | None:
    """Swap one player each between two users, atomically. Everything is
    re-checked inside the transaction, so a stale or forged request fails."""
    a, b = _uid(a_id), _uid(b_id)
    if a == b:
        return "You can't trade with yourself."
    with _tx() as conn:
        def has(u, k):
            return conn.execute("SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (u, k)).fetchone() is not None

        if not has(a, a_key) or not has(b, b_key):
            return "One of the players is no longer available."
        if has(a, b_key) or has(b, a_key):
            return "One of you already owns the player you'd receive."
        for u, out_k, in_k in ((a, a_key, b_key), (b, b_key, a_key)):
            slot_row = conn.execute("SELECT slot FROM xi WHERE user_id = ? AND player_key = ?", (u, out_k)).fetchone()
            conn.execute("DELETE FROM xi WHERE user_id = ? AND player_key = ?", (u, out_k))
            conn.execute("DELETE FROM owned WHERE user_id = ? AND player_key = ?", (u, out_k))
            conn.execute("UPDATE users SET captain_key = NULL WHERE user_id = ? AND captain_key = ?", (u, out_k))
            _ensure_impact_subs_table(conn)
            conn.execute("DELETE FROM impact_subs WHERE user_id = ? AND player_key = ?", (u, out_k))
            conn.execute(
                "INSERT INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)", (u, in_k, time.time())
            )
            if slot_row is not None:
                conn.execute(
                    "INSERT INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)", (u, slot_row["slot"], in_k)
                )
        return None


# ── Owner tools: bans and gifts ─────────────────────────────────────────────

def set_banned(user_id, banned: bool, reason: str | None = None) -> bool:
    """Ban or unban a user (works even if they never debuted)."""
    uid = _uid(user_id)
    with _tx() as conn:
        if banned:
            conn.execute(
                "INSERT OR REPLACE INTO bans (user_id, reason, ts) VALUES (?, ?, ?)",
                (uid, (reason or "")[:200], time.time()),
            )
        else:
            conn.execute("DELETE FROM bans WHERE user_id = ?", (uid,))
        return True


def banned_ids() -> set[int]:
    conn = _conn()
    try:
        return {int(r["user_id"]) for r in conn.execute("SELECT user_id FROM bans")}
    finally:
        conn.close()


def give_card(user_id, player_key: str) -> bool:
    uid = _uid(user_id)
    with _tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return False
        got = conn.execute(
            "INSERT OR IGNORE INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)",
            (uid, player_key, time.time()),
        )
        if got.rowcount:
            _fill_empty_slots(conn, uid, [player_key])
        return bool(got.rowcount)


def reset_all_cards(user_id) -> int:
    """(Admin) Remove every card the user owns, clear their XI and captain.
    Coins and team name are kept. Returns how many cards were removed."""
    uid = _uid(user_id)
    with _tx() as conn:
        n = conn.execute("SELECT COUNT(*) FROM owned WHERE user_id = ?", (uid,)).fetchone()[0]
        conn.execute("DELETE FROM owned WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM xi WHERE user_id = ?", (uid,))
        conn.execute("UPDATE users SET captain_key = NULL WHERE user_id = ?", (uid,))
        _ensure_impact_subs_table(conn)
        conn.execute("DELETE FROM impact_subs WHERE user_id = ?", (uid,))
        return int(n)


# ── Impact Player subs (bench players nominated for in-match substitution) ──

def get_impact_subs(user_id) -> dict[int, str]:
    """{slot: player_key} for this user's nominated Impact Player subs (1..4)."""
    conn = _conn()
    try:
        _ensure_impact_subs_table(conn)
        rows = conn.execute(
            "SELECT slot, player_key FROM impact_subs WHERE user_id = ?", (_uid(user_id),)
        ).fetchall()
        return {r["slot"]: r["player_key"] for r in rows}
    finally:
        conn.close()


def add_impact_sub(user_id, player_key: str) -> str | None:
    """Nominate an owned, bench (not in XI) player as an Impact Player sub.
    Returns None on success or an error message."""
    uid = _uid(user_id)
    with _tx() as conn:
        _ensure_impact_subs_table(conn)
        if conn.execute("SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (uid, player_key)).fetchone() is None:
            return "You don't own that player."
        xi = set(_xi_rows(conn, uid).values())
        if player_key in xi:
            return "That player is in your playing XI — Impact subs must come from the bench."
        existing = conn.execute(
            "SELECT slot, player_key FROM impact_subs WHERE user_id = ?", (uid,)
        ).fetchall()
        if any(r["player_key"] == player_key for r in existing):
            return "That player is already on your Impact subs list."
        if len(existing) >= IMPACT_SUBS_MAX:
            return f"You can only nominate {IMPACT_SUBS_MAX} subs — remove one first with `cssubs remove`."
        taken_slots = {r["slot"] for r in existing}
        slot = next(s for s in range(1, IMPACT_SUBS_MAX + 1) if s not in taken_slots)
        conn.execute(
            "INSERT INTO impact_subs (user_id, slot, player_key) VALUES (?, ?, ?)", (uid, slot, player_key)
        )
        return None


def remove_impact_sub(user_id, player_key: str) -> str | None:
    uid = _uid(user_id)
    with _tx() as conn:
        _ensure_impact_subs_table(conn)
        got = conn.execute(
            "DELETE FROM impact_subs WHERE user_id = ? AND player_key = ?", (uid, player_key)
        )
        if got.rowcount == 0:
            return "That player isn't on your Impact subs list."
        return None


def clear_impact_subs(user_id) -> None:
    uid = _uid(user_id)
    with _tx() as conn:
        _ensure_impact_subs_table(conn)
        conn.execute("DELETE FROM impact_subs WHERE user_id = ?", (uid,))
