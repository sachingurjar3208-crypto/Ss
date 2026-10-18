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
WEEKLY_VOUCHERS = 5      # every weekly claim also gives this many vouchers
STREAK_VOUCHERS = 3      # vouchers on every 7th daily-streak day (7, 14, 21 ...). No vouchers on other days.
MONTHLY_REWARD = 100_000
LOSS_REWARD_PER_OVER = 150       # loser gets overs x this, but only in matches of LOSS_MIN_OVERS or more
LOSS_MIN_OVERS       = 10        # (10 overs = 1,500 ... 20 overs = 3,000; under 10 overs = nothing)
MATCH_REWARD_DAILY_CAP   = 10   # rewarded matches per player per 24h
MATCH_REWARD_PER_OPP_CAP = 3    # ... and against the same opponent per 24h (stops alt-account farming)
MIN_SQUAD     = 11               # you can never sell below this many players
SQUAD_MAX     = 25               # a squad can never hold more than this many players
TRADE_FEE     = 5_000            # coins EACH of the two players pays for every completed trade
WIN_REWARD_PER_OVER = 1_000      # match win = overs x this (1 over = 1,000 ... 20 overs = 20,000)
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
            CREATE TABLE IF NOT EXISTS pending_cards (
                user_id    INTEGER NOT NULL,
                player_key TEXT    NOT NULL,
                source     TEXT,
                ts         REAL    NOT NULL,
                PRIMARY KEY (user_id, player_key)
            );
            CREATE TABLE IF NOT EXISTS vouchers (
                user_id INTEGER PRIMARY KEY,
                amount  INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0)
            );
            CREATE TABLE IF NOT EXISTS voucher_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                delta         INTEGER NOT NULL,
                reason        TEXT,
                balance_after INTEGER NOT NULL,
                ts            REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_voucher_log_user ON voucher_log (user_id, ts);
            CREATE TABLE IF NOT EXISTS draft_pity (
                user_id INTEGER PRIMARY KEY,
                since_a INTEGER NOT NULL DEFAULT 0 CHECK (since_a >= 0)
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


def win_reward_for(overs) -> int:
    """Coins for winning a match: 1,000 per over (1 over = 1,000 ... 20 overs = 20,000)."""
    try:
        return max(0, int(overs)) * WIN_REWARD_PER_OVER
    except (TypeError, ValueError):
        return 0


def loss_reward_for(overs) -> int:
    """Coins the LOSER gets: nothing under LOSS_MIN_OVERS overs, otherwise 150 per over
    (10 overs = 1,500, 15 overs = 2,250, 20 overs = 3,000)."""
    try:
        o = int(overs)
    except (TypeError, ValueError):
        return 0
    return o * LOSS_REWARD_PER_OVER if o >= LOSS_MIN_OVERS else 0


def reward_match_winner(winner_id, loser_id, overs: int = 0) -> None:
    """Called by views.py when a match finishes. The winner gets win_reward_for(overs);
    the loser loss_reward_for(overs). Coins are paid only up to
    MATCH_REWARD_DAILY_CAP matches a day, and MATCH_REWARD_PER_OPP_CAP per
    opponent, so two accounts can't farm coins by playing each other."""
    w, l = _uid(winner_id), _uid(loser_id)
    if w == l:
        return
    since = time.time() - DAY
    with _tx() as conn:
        for uid, opp, amount, reason in ((w, l, win_reward_for(overs), f"Match win ({int(overs)} over(s))"), (l, w, loss_reward_for(overs), "Match played")):
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM reward_log WHERE user_id = ? AND ts > ?", (uid, since)
            ).fetchone()["n"]
            pair = conn.execute(
                "SELECT COUNT(*) AS n FROM reward_log WHERE user_id = ? AND opp_id = ? AND ts > ?",
                (uid, opp, since),
            ).fetchone()["n"]
            if amount <= 0:   # nothing to pay (e.g. loser of a short match): don't use up a daily slot
                continue
            if total >= MATCH_REWARD_DAILY_CAP or pair >= MATCH_REWARD_PER_OPP_CAP:
                continue
            conn.execute("INSERT INTO reward_log (user_id, opp_id, ts) VALUES (?, ?, ?)", (uid, opp, time.time()))
            _apply_coins(conn, uid, amount, reason)
        conn.execute("DELETE FROM reward_log WHERE ts < ?", (time.time() - 3 * DAY,))


# ── Squad limit: max SQUAD_MAX players ──────────────────────────────────────
# A card that arrives while the squad is full is NOT added. It waits in
# `pending_cards` until the player chooses: sell it, or replace one of their
# players with it (see resolve_pending). Nothing is ever lost or auto-sold.

def _ensure_pending_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pending_cards (
            user_id    INTEGER NOT NULL,
            player_key TEXT    NOT NULL,
            source     TEXT,
            ts         REAL    NOT NULL,
            PRIMARY KEY (user_id, player_key)
        )"""
    )


def _squad_count(conn: sqlite3.Connection, uid: int) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM owned WHERE user_id = ?", (uid,)).fetchone()["n"])


def _holds(conn: sqlite3.Connection, uid: int, key: str) -> bool:
    """Owns the card, or it is waiting in their pending list."""
    _ensure_pending_table(conn)
    return (
        conn.execute("SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (uid, key)).fetchone() is not None
        or conn.execute("SELECT 1 FROM pending_cards WHERE user_id = ? AND player_key = ?", (uid, key)).fetchone()
        is not None
    )


def _grant(conn: sqlite3.Connection, uid: int, key: str, source: str) -> str:
    """Give one card. Returns "added", "pending" (squad full) or "dupe"."""
    if _holds(conn, uid, key):
        return "dupe"
    now = time.time()
    if _squad_count(conn, uid) >= SQUAD_MAX:
        conn.execute(
            "INSERT OR IGNORE INTO pending_cards (user_id, player_key, source, ts) VALUES (?, ?, ?, ?)",
            (uid, key, source, now),
        )
        return "pending"
    conn.execute("INSERT INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)", (uid, key, now))
    _fill_empty_slots(conn, uid, [key])
    return "added"


def squad_count(user_id) -> int:
    conn = _conn()
    try:
        return _squad_count(conn, _uid(user_id))
    finally:
        conn.close()


def get_pending(user_id) -> list[str]:
    """Player keys waiting for the user's decision (oldest first)."""
    conn = _conn()
    try:
        _ensure_pending_table(conn)
        rows = conn.execute(
            "SELECT player_key FROM pending_cards WHERE user_id = ? ORDER BY ts", (_uid(user_id),)
        ).fetchall()
        return [r["player_key"] for r in rows]
    finally:
        conn.close()


def resolve_pending(user_id, new_key: str, action: str, replace_key: str | None = None,
                    new_value: int = 0, old_value: int = 0) -> str | None:
    """Decide what happens to a waiting card. None on success, else an error text.

    action "sell":    the waiting card is sold for `new_value` coins.
    action "replace": `replace_key` (one of the user's players) is sold for `old_value`
                      coins and the waiting card takes its place (and its XI spot).
    action "keep":    only if the squad has room again (e.g. after selling someone).
    """
    uid = _uid(user_id)
    with _tx() as conn:
        _ensure_pending_table(conn)
        if conn.execute(
            "SELECT 1 FROM pending_cards WHERE user_id = ? AND player_key = ?", (uid, new_key)
        ).fetchone() is None:
            return "That card is no longer waiting."
        if action == "sell":
            conn.execute("DELETE FROM pending_cards WHERE user_id = ? AND player_key = ?", (uid, new_key))
            _apply_coins(conn, uid, int(new_value), f"Sold {new_key} (squad full)")
            return None
        if action == "keep":
            if _squad_count(conn, uid) >= SQUAD_MAX:
                return f"Your squad is full ({SQUAD_MAX}/{SQUAD_MAX})."
            conn.execute("DELETE FROM pending_cards WHERE user_id = ? AND player_key = ?", (uid, new_key))
            conn.execute(
                "INSERT INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)", (uid, new_key, time.time())
            )
            _fill_empty_slots(conn, uid, [new_key])
            return None
        if action == "replace":
            if not replace_key or conn.execute(
                "SELECT 1 FROM owned WHERE user_id = ? AND player_key = ?", (uid, replace_key)
            ).fetchone() is None:
                return "You don't own the player you picked to replace."
            slot_row = conn.execute(
                "SELECT slot FROM xi WHERE user_id = ? AND player_key = ?", (uid, replace_key)
            ).fetchone()
            conn.execute("DELETE FROM xi WHERE user_id = ? AND player_key = ?", (uid, replace_key))
            conn.execute("DELETE FROM owned WHERE user_id = ? AND player_key = ?", (uid, replace_key))
            conn.execute("UPDATE users SET captain_key = NULL WHERE user_id = ? AND captain_key = ?", (uid, replace_key))
            _ensure_impact_subs_table(conn)
            conn.execute("DELETE FROM impact_subs WHERE user_id = ? AND player_key = ?", (uid, replace_key))
            conn.execute("DELETE FROM pending_cards WHERE user_id = ? AND player_key = ?", (uid, new_key))
            conn.execute(
                "INSERT INTO owned (user_id, player_key, acquired_at) VALUES (?, ?, ?)", (uid, new_key, time.time())
            )
            if slot_row is not None:
                conn.execute(
                    "INSERT INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)", (uid, slot_row["slot"], new_key)
                )
            _apply_coins(conn, uid, int(old_value), f"Sold {replace_key} (replaced)")
            return None
        return "Unknown choice."


# ── Rewards (daily / weekly / monthly) ──────────────────────────────────────

def claim_reward(user_id, kind: str, card_pool: list[str] | None = None,
                 dupe_values: dict[str, int] | None = None) -> dict:
    """Claim a daily/weekly/monthly reward atomically: coins + one random card
    from `card_pool` (player keys already filtered to the right ovr range).
    A card the user already owns is skipped if they have any unowned one left in
    the pool; otherwise it turns into coins using `dupe_values`.
    Returns {"ok": True, "coins", "base", "bonus", "balance", "streak",
             "card": key|None, "dupe": bool, "refund": n, "pending": bool (squad full),
             "vouchers": n (weekly: 5, daily: 3 on every 7th streak day, else 0)}
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
        # Vouchers (same transaction as the claim: both happen or neither does).
        vouchers = 0
        if kind == "weekly":
            vouchers = WEEKLY_VOUCHERS
            _voucher_change(conn, uid, vouchers, "Weekly reward")
        elif kind == "daily" and streak > 0 and streak % STREAK_EVERY == 0:
            vouchers = STREAK_VOUCHERS
            _voucher_change(conn, uid, vouchers, f"Daily streak {streak}")
        card_key, dupe, refund, pending = None, False, 0, False
        pool = list(card_pool or [])
        if pool:
            _ensure_pending_table(conn)
            owned = {r["player_key"] for r in conn.execute("SELECT player_key FROM owned WHERE user_id = ?", (uid,))}
            owned |= {r["player_key"] for r in conn.execute("SELECT player_key FROM pending_cards WHERE user_id = ?", (uid,))}
            fresh = [k for k in pool if k not in owned]
            if fresh:
                card_key = _rng.choice(fresh)
                pending = _grant(conn, uid, card_key, f"{kind} reward") == "pending"
            else:
                card_key, dupe = _rng.choice(pool), True
                refund = int((dupe_values or {}).get(card_key, 0))
                if refund:
                    balance = _apply_coins(conn, uid, refund, f"Duplicate refund ({kind} reward)")
        return {"ok": True, "coins": coins, "base": base, "bonus": bonus, "balance": balance,
                "streak": streak, "card": card_key, "dupe": dupe, "refund": refund, "pending": pending,
                "vouchers": vouchers}


# ── csclaim: a free random card every hour ──────────────────────────────────
# Cooldown: 60 min for normal users, 35 min for Premium (the cog picks which).
CLAIM_COOLDOWN = 60 * 60
CLAIM_COOLDOWN_PREMIUM = 35 * 60

# 60-75 OVR cards share the leftover chance and are picked at random (no table needed).
CLAIM_LOW_RANGE = (60, 75)

# Chance in % for every higher OVR. Edit these numbers to change the odds.
# 76-83: the higher the OVR, the lower the chance.  84-88: fixed rare chances.
# Everything that is NOT listed here (the rest of the 100%) gives a 60-75 card.
CLAIM_RARITY = {
    76: 10.0, 77: 7.0, 78: 5.0, 79: 3.5, 80: 2.5, 81: 1.8, 82: 1.2, 83: 0.8,
    84: 2.0, 85: 1.0, 86: 0.5, 87: 0.3, 88: 0.1,
}


def _ensure_claim_column(conn: sqlite3.Connection) -> None:
    """Adds users.last_claim on old databases (init_economy_db isn't run at start-up)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "last_claim" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_claim REAL NOT NULL DEFAULT 0")


def _roll_claim_ovr() -> int | None:
    """Returns the OVR that was rolled (76-88), or None = a 60-75 card."""
    r = _rng.random() * 100
    acc = 0.0
    for ovr, pct in sorted(CLAIM_RARITY.items()):
        acc += pct
        if r < acc:
            return ovr
    return None


def _claim_pool(by_ovr: dict[int, list[str]], ovr: int | None) -> list[str]:
    """Cards for the rolled OVR. If there are no cards at that OVR yet, step down to the
    next lower rated one (never below 76), then to the 60-75 group."""
    if ovr is not None:
        for o in range(ovr, CLAIM_LOW_RANGE[1], -1):
            if by_ovr.get(o):
                return list(by_ovr[o])
    lo, hi = CLAIM_LOW_RANGE
    return [k for o in range(lo, hi + 1) for k in by_ovr.get(o, [])]


def claim_card(user_id, cooldown: int, by_ovr: dict[int, list[str]],
               dupe_values: dict[str, int] | None = None) -> dict:
    """csclaim: one random card, once per `cooldown` seconds, all in one transaction.

    `by_ovr` maps OVR -> player keys. A card the user already has is skipped when
    an unowned one exists at the rolled OVR; otherwise it turns into coins
    (`dupe_values`). Squad full (25) -> the card waits in pending_cards, so the
    sell / replace buttons appear.
    Returns {"ok": True, "card", "ovr", "dupe", "refund", "pending", "balance"}
    or {"ok": False, "wait": seconds} / {"ok": False, "missing": True} / {"ok": False, "empty": True}."""
    uid = _uid(user_id)
    now = time.time()
    with _tx() as conn:
        _ensure_claim_column(conn)
        row = conn.execute("SELECT purse, last_claim FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            return {"ok": False, "missing": True, "wait": 0}
        left = cooldown - (now - float(row["last_claim"]))
        if left > 0:
            return {"ok": False, "wait": int(left) + 1}
        pool = _claim_pool(by_ovr, _roll_claim_ovr())
        if not pool:
            return {"ok": False, "empty": True, "wait": 0}   # no cards exist: cooldown NOT used
        _ensure_pending_table(conn)
        owned = {r["player_key"] for r in conn.execute("SELECT player_key FROM owned WHERE user_id = ?", (uid,))}
        owned |= {r["player_key"] for r in conn.execute("SELECT player_key FROM pending_cards WHERE user_id = ?", (uid,))}
        fresh = [k for k in pool if k not in owned]
        balance = int(row["purse"])
        dupe, refund, pending = False, 0, False
        if fresh:
            key = _rng.choice(fresh)
            pending = _grant(conn, uid, key, "csclaim") == "pending"
        else:
            key, dupe = _rng.choice(pool), True
            refund = int((dupe_values or {}).get(key, 0))
            if refund:
                balance = _apply_coins(conn, uid, refund, "Duplicate refund (csclaim)")
        conn.execute("UPDATE users SET last_claim = ? WHERE user_id = ?", (now, uid))
        return {"ok": True, "card": key, "dupe": dupe, "refund": refund, "pending": pending, "balance": balance}


# ── cscd: read-only snapshot of every claim cooldown ─────────────────────────

def reward_cooldowns(user_id, premium: bool = False) -> dict | None:
    """For `cscd`: seconds left on daily / weekly / monthly / csclaim, without
    touching anything (pure read, no transaction). None if the user hasn't
    debuted. `monthly_locked` is the extra wait before the monthly reward
    unlocks at all (1 month after debut) and is separate from `monthly`."""
    uid = _uid(user_id)
    conn = _conn()
    try:
        _ensure_claim_column(conn)
        row = conn.execute(
            "SELECT last_daily, last_weekly, last_monthly, last_claim, created_at "
            "FROM users WHERE user_id = ?", (uid,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    now = time.time()

    def left(col: str, cooldown: int) -> int:
        return max(0, int(cooldown - (now - float(row[col]))) )

    claim_cooldown = CLAIM_COOLDOWN_PREMIUM if premium else CLAIM_COOLDOWN
    return {
        "daily": left("last_daily", COOLDOWNS["daily"]),
        "weekly": left("last_weekly", COOLDOWNS["weekly"]),
        "monthly": left("last_monthly", COOLDOWNS["monthly"]),
        "monthly_locked": max(0, int(MONTHLY_UNLOCK_AFTER_DEBUT - (now - float(row["created_at"])))),
        "claim": left("last_claim", claim_cooldown),
        "claim_cooldown": claim_cooldown,
    }


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
        if _holds(conn, uid, player_key):
            return "You already own this player (or it is waiting in your pending cards)."
        if _squad_count(conn, uid) >= SQUAD_MAX:
            return f"Your squad is full ({SQUAD_MAX}/{SQUAD_MAX}). Sell a player first."
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
    turned into coins using `dupe_values`. Returns {"new": [...], "pending": [...] (squad
    was full: waiting for sell/replace), "dupes": [...], "refund": n} or an error message."""
    uid = _uid(user_id)
    with _tx() as conn:
        cur = conn.execute(
            "UPDATE packs SET qty = qty - 1 WHERE user_id = ? AND pack_type = ? AND qty > 0",
            (uid, pack_type),
        )
        if cur.rowcount == 0:
            return "You don't have that pack."
        new, pending, dupes, refund = [], [], [], 0
        for k in keys:
            if k in new or k in pending:
                dupes.append(k)
                refund += dupe_values.get(k, 0)
                continue
            status = _grant(conn, uid, k, f"{pack_type} pack")
            if status == "added":
                new.append(k)
            elif status == "pending":
                pending.append(k)
            else:
                dupes.append(k)
                refund += dupe_values.get(k, 0)
        if refund:
            _apply_coins(conn, uid, refund, f"Duplicate refund ({pack_type} pack)")
        return {"new": new, "pending": pending, "dupes": dupes, "refund": refund}


# ── Vouchers & Draft ─────────────────────────────────────────────────────────
# Safety rules (same style as coins):
#  * vouchers are integers that can NEVER go below 0 (DB CHECK + checked inside
#    the transaction), and are capped at MAX_VOUCHERS.
#  * a draft takes the vouchers, rolls the cards, hands them over and moves the
#    "guarantee" counter in ONE transaction: a crash or a button spam can never
#    give a card without paying, or charge without giving.
#  * every change is written to voucher_log (who, how many, why).

MAX_VOUCHERS = 1_000_000


def _ensure_draft_tables(conn: sqlite3.Connection) -> None:
    """Create the voucher/draft tables on first use. The live database was made
    before these existed and init_economy_db() is not run at start-up, so every
    voucher function makes sure they are there (cheap, safe to repeat, and uses
    plain execute() so it never ends a running transaction)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vouchers ("
        "user_id INTEGER PRIMARY KEY, amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS voucher_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, delta INTEGER NOT NULL, "
        "reason TEXT, balance_after INTEGER NOT NULL, ts REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_voucher_log_user ON voucher_log (user_id, ts)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS draft_pity ("
        "user_id INTEGER PRIMARY KEY, since_a INTEGER NOT NULL DEFAULT 0 CHECK (since_a >= 0))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS draft_pool_cards ("
        "player_key TEXT PRIMARY KEY, pool TEXT NOT NULL CHECK (pool IN ('A','B')), "
        "weight REAL NOT NULL CHECK (weight > 0))"
    )


def ensure_draft_tables() -> None:
    conn = _conn()
    try:
        _ensure_draft_tables(conn)
    finally:
        conn.close()


def get_vouchers(user_id) -> int:
    conn = _conn()
    try:
        _ensure_draft_tables(conn)
        row = conn.execute("SELECT amount FROM vouchers WHERE user_id = ?", (_uid(user_id),)).fetchone()
        return int(row["amount"]) if row else 0
    finally:
        conn.close()


def _voucher_change(conn: sqlite3.Connection, uid: int, delta: int, reason: str) -> int:
    """Add/remove vouchers inside an open transaction. Never below 0, never above MAX_VOUCHERS."""
    _ensure_draft_tables(conn)
    row = conn.execute("SELECT amount FROM vouchers WHERE user_id = ?", (uid,)).fetchone()
    cur = int(row["amount"]) if row else 0
    new = max(0, min(MAX_VOUCHERS, cur + int(delta)))
    conn.execute(
        "INSERT INTO vouchers (user_id, amount) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET amount = excluded.amount",
        (uid, new),
    )
    conn.execute(
        "INSERT INTO voucher_log (user_id, delta, reason, balance_after, ts) VALUES (?, ?, ?, ?, ?)",
        (uid, new - cur, (reason or "")[:120], new, time.time()),
    )
    return new


def add_vouchers(user_id, amount: int, reason: str = "") -> int | None:
    """Give vouchers. Returns the new amount, or None if the user hasn't debuted."""
    uid = _uid(user_id)
    amount = int(amount)
    if amount <= 0:
        return None
    with _tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return None
        return _voucher_change(conn, uid, amount, reason)


def remove_vouchers(user_id, amount: int, reason: str = "") -> tuple[int, int] | None:
    """Take vouchers away (stops at 0). Returns (removed, new_amount) or None if the user hasn't debuted."""
    uid = _uid(user_id)
    amount = int(amount)
    if amount <= 0:
        return None
    with _tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return None
        row = conn.execute("SELECT amount FROM vouchers WHERE user_id = ?", (uid,)).fetchone()
        cur = int(row["amount"]) if row else 0
        new = _voucher_change(conn, uid, -amount, reason)
        return cur - new, new


def pool_set(pool: str, player_key: str, weight: float) -> None:
    """Put a card in Pool A or B with its chance (a card lives in ONE pool; adding again
    updates it / moves it)."""
    with _tx() as conn:
        _ensure_draft_tables(conn)
        conn.execute(
            "INSERT INTO draft_pool_cards (player_key, pool, weight) VALUES (?, ?, ?) "
            "ON CONFLICT(player_key) DO UPDATE SET pool = excluded.pool, weight = excluded.weight",
            (player_key.strip().lower(), pool, float(weight)),
        )


def pool_remove(player_key: str) -> str | None:
    """Take a card out of its pool. Returns the pool it was in, or None if it wasn't in one."""
    with _tx() as conn:
        _ensure_draft_tables(conn)
        row = conn.execute(
            "SELECT pool FROM draft_pool_cards WHERE player_key = ?", (player_key.strip().lower(),)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM draft_pool_cards WHERE player_key = ?", (player_key.strip().lower(),))
        return row["pool"]


def pool_cards(pool: str | None = None) -> list[tuple[str, str, float]]:
    """[(pool, player_key, weight)] for Pool A / B (or one of them)."""
    conn = _conn()
    try:
        _ensure_draft_tables(conn)
        if pool:
            rows = conn.execute(
                "SELECT pool, player_key, weight FROM draft_pool_cards WHERE pool = ?", (pool,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT pool, player_key, weight FROM draft_pool_cards").fetchall()
        return [(r["pool"], r["player_key"], float(r["weight"])) for r in rows]
    finally:
        conn.close()


def get_draft_pity(user_id) -> int:
    """How many drafts since the user's last Pool A card."""
    conn = _conn()
    try:
        _ensure_draft_tables(conn)
        row = conn.execute("SELECT since_a FROM draft_pity WHERE user_id = ?", (_uid(user_id),)).fetchone()
        return int(row["since_a"]) if row else 0
    finally:
        conn.close()


def draft_spin(user_id, count: int, cost_each: int, pity_at: int, roll_fn, dupe_value_fn) -> dict | str:
    """Run `count` drafts in ONE transaction. Returns a dict or an error text.

    roll_fn(force_a: bool) -> (pool_letter, player_key) | None   (picks one card)
    dupe_value_fn(player_key) -> coins given back if the card is a duplicate

    Counter: every draft adds 1 to since_a. When it reaches `pity_at` that draft
    is a guaranteed Pool A card. Any Pool A card (lucky or guaranteed) resets it.
    Result: {"results": [{"pool","key","status","refund"}], "refund": n,
             "vouchers": left, "pity": counter}
    """
    uid = _uid(user_id)
    count = int(count)
    cost = int(cost_each) * count
    if count <= 0 or cost <= 0:
        return "Invalid draft."
    with _tx() as conn:
        _ensure_draft_tables(conn)
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return "You haven't debuted yet."
        # Squad limit: every pack can add one card, so the squad (owned + cards already
        # waiting) must have room for ALL packs. Checked before anything is charged.
        _ensure_pending_table(conn)
        held = _squad_count(conn, uid) + int(
            conn.execute("SELECT COUNT(*) AS n FROM pending_cards WHERE user_id = ?", (uid,)).fetchone()["n"]
        )
        if held + count > SQUAD_MAX:
            need = held + count - SQUAD_MAX
            return (
                f"Your squad limit is {SQUAD_MAX}. You have {held} card(s) and this opens {count} pack(s). "
                f"You need to sell at least {need} card(s) first (`cssell`)."
            )
        row = conn.execute("SELECT amount FROM vouchers WHERE user_id = ?", (uid,)).fetchone()
        have = int(row["amount"]) if row else 0
        if have < cost:
            return f"Not enough vouchers (need {cost}, you have {have})."
        prow = conn.execute("SELECT since_a FROM draft_pity WHERE user_id = ?", (uid,)).fetchone()
        since = int(prow["since_a"]) if prow else 0

        left = _voucher_change(conn, uid, -cost, f"Draft x{count}")
        results, seen, total_refund = [], set(), 0
        for _ in range(count):
            since += 1
            force_a = since >= int(pity_at)
            rolled = roll_fn(force_a)
            if rolled is None:
                # nothing could be rolled (no cards in game) -> undo everything
                raise sqlite3.Error("draft roll failed")
            pool, key = rolled
            if pool == "A":
                since = 0
            if key in seen:
                status = "dupe"
            else:
                status = _grant(conn, uid, key, f"Draft pool {pool}")
                seen.add(key)
            refund = 0
            if status == "dupe":
                refund = int(dupe_value_fn(key))
                total_refund += refund
            results.append({"pool": pool, "key": key, "status": status, "refund": refund})
        if total_refund:
            _apply_coins(conn, uid, total_refund, f"Duplicate refund (draft x{count})")
        conn.execute(
            "INSERT INTO draft_pity (user_id, since_a) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET since_a = excluded.since_a",
            (uid, since),
        )
        return {"results": results, "refund": total_refund, "vouchers": left, "pity": since}


def claim_starter(user_id, keys: list[str], xi_order: list[str]) -> str | None:
    """Give the one-time starter squad and set the XI. None on success."""
    uid = _uid(user_id)
    with _tx() as conn:
        row = conn.execute("SELECT starter_claimed FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            return "You haven't debuted yet."
        if row["starter_claimed"]:
            return "You already claimed your starter pack."
        have = {r["player_key"] for r in conn.execute("SELECT player_key FROM owned WHERE user_id = ?", (uid,))}
        incoming = [k for k in dict.fromkeys(keys) if k not in have]
        if len(have) + len(incoming) > SQUAD_MAX:
            return (
                f"Your squad would go over {SQUAD_MAX} players. "
                f"Sell {len(have) + len(incoming) - SQUAD_MAX} player(s) first, then try again."
            )
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

def execute_trade(a_id, a_key: str, b_id, b_key: str, fee: int = TRADE_FEE) -> str | None:
    """Swap one player each between two users, atomically. Both players pay `fee`
    coins (every trade, always). Everything is re-checked inside the transaction,
    so a stale or forged request fails."""
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
        for u in (a, b):
            row = conn.execute("SELECT purse FROM users WHERE user_id = ?", (u,)).fetchone()
            if row is None or int(row["purse"]) < fee:
                return f"<@{u}> doesn't have the {fee:,} coins needed for the trade fee."
        for u in (a, b):
            _apply_coins(conn, u, -fee, "Trade fee")
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
        if _squad_count(conn, uid) >= SQUAD_MAX:
            return False   # squad is full (25/25)
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


def reset_everything(user_id) -> dict:
    """(Admin) Full wipe of a user's economy and collection: coins set to 0,
    every card and pack removed, XI / captain / impact subs / pending cards cleared.
    The debut is undone too (the user row is deleted), so the user can type csdebut
    again and gets a fresh team, the starting purse and a new debut pack. Ban state
    lives in its own table and is kept. Returns {"cards": n, "coins": old_purse}."""
    uid = _uid(user_id)
    with _tx() as conn:
        row = conn.execute("SELECT purse FROM users WHERE user_id = ?", (uid,)).fetchone()
        old = int(row["purse"]) if row else 0
        n = conn.execute("SELECT COUNT(*) FROM owned WHERE user_id = ?", (uid,)).fetchone()[0]
        conn.execute("DELETE FROM owned WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM xi WHERE user_id = ?", (uid,))
        _ensure_impact_subs_table(conn)
        conn.execute("DELETE FROM impact_subs WHERE user_id = ?", (uid,))
        _ensure_pending_table(conn)
        conn.execute("DELETE FROM pending_cards WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM packs WHERE user_id = ?", (uid,))
        _ensure_draft_tables(conn)
        conn.execute("DELETE FROM vouchers WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM draft_pity WHERE user_id = ?", (uid,))
        if old:
            conn.execute(
                "INSERT INTO ledger (user_id, delta, reason, balance_after, ts) VALUES (?, ?, ?, 0, ?)",
                (uid, -old, "Admin full reset", time.time()),
            )
        conn.execute("DELETE FROM users WHERE user_id = ?", (uid,))   # un-debut: csdebut works again
        return {"cards": int(n), "coins": old}


def top_users(limit: int = 20, by: str = "coins") -> list[dict]:
    """(Admin) Top users with their purse and card count.
    by = "coins" or "cards". Each row: user_id, team_name, purse, cards."""
    order = "cards DESC, u.purse DESC" if by == "cards" else "u.purse DESC, cards DESC"
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT u.user_id, u.team_name, u.purse, "
            "(SELECT COUNT(*) FROM owned o WHERE o.user_id = u.user_id) AS cards "
            f"FROM users u ORDER BY {order} LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def economy_snapshot() -> list[dict]:
    """(Admin) Every user with purse + the list of player keys they own.
    Each row: user_id, team_name, purse, keys (list[str], lower-case)."""
    conn = _conn()
    try:
        users = conn.execute("SELECT user_id, team_name, purse FROM users").fetchall()
        owned: dict = {}
        for r in conn.execute("SELECT user_id, player_key FROM owned").fetchall():
            owned.setdefault(r["user_id"], []).append(str(r["player_key"]).strip().lower())
        return [
            {"user_id": u["user_id"], "team_name": u["team_name"], "purse": int(u["purse"]),
             "keys": owned.get(u["user_id"], [])}
            for u in users
        ]
    finally:
        conn.close()


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
