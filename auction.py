"""Daily Live Auction: database side (economy.db).

Every bid HOLDS the coins right away (they leave the purse). When someone bids higher,
the previous top bidder gets their coins back in the same transaction. When a lot ends,
the top bid is "won": the coins stay spent and the card is given. If the bot stops in
the middle of a lot, recover() gives every held coin back on the next start.

Tables
  auction_runs      one row per auction (day = "YYYY-MM-DD" for the daily one, so it runs once a day)
  auction_bids      every bid: status held -> refunded / won
  auction_settings  key/value (ping role id)
"""
from __future__ import annotations

import time

import economy

MAX_WINS_PER_RUN = 2          # one person can win at most this many lots in one auction
MIN_RAISE_PCT = 2.5           # a new bid must be at least this % above the top bid
MIN_RAISE_ABS = 1_000         # ... and at least this many coins above it


def ensure_tables() -> None:
    with economy._tx() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS auction_runs (
                   id       INTEGER PRIMARY KEY AUTOINCREMENT,
                   day      TEXT UNIQUE,
                   started  REAL NOT NULL,
                   status   TEXT NOT NULL DEFAULT 'running'
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS auction_bids (
                   id         INTEGER PRIMARY KEY AUTOINCREMENT,
                   run_id     INTEGER NOT NULL,
                   lot        INTEGER NOT NULL,
                   player_key TEXT    NOT NULL,
                   user_id    INTEGER NOT NULL,
                   amount     INTEGER NOT NULL,
                   status     TEXT    NOT NULL DEFAULT 'held',
                   ts         REAL    NOT NULL
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auction_bids_lot ON auction_bids (run_id, lot, status)")
        conn.execute("CREATE TABLE IF NOT EXISTS auction_settings (key TEXT PRIMARY KEY, value TEXT)")


# ── settings ──────────────────────────────────────────────────────────────────
def get_setting(key: str) -> str | None:
    with economy._tx() as conn:
        row = conn.execute("SELECT value FROM auction_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str | None) -> None:
    with economy._tx() as conn:
        if value is None:
            conn.execute("DELETE FROM auction_settings WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO auction_settings (key, value) VALUES (?, ?)", (key, value))


# ── runs ──────────────────────────────────────────────────────────────────────
def start_run(day: str | None) -> int | None:
    """New auction. `day` set = the daily one (None if it already ran that day)."""
    with economy._tx() as conn:
        if day and conn.execute("SELECT 1 FROM auction_runs WHERE day = ?", (day,)).fetchone():
            return None
        cur = conn.execute("INSERT INTO auction_runs (day, started) VALUES (?, ?)", (day, time.time()))
        return int(cur.lastrowid)


def end_run(run_id: int, status: str = "done") -> None:
    with economy._tx() as conn:
        conn.execute("UPDATE auction_runs SET status = ? WHERE id = ?", (status, run_id))


def min_next_bid(top: int | None, base: int) -> int:
    if not top:
        return base
    raise_by = max(MIN_RAISE_ABS, int(top * MIN_RAISE_PCT / 100))
    raise_by = -(-raise_by // 1000) * 1000            # round up to the next 1,000
    return top + raise_by


def top_bid(run_id: int, lot: int) -> dict | None:
    with economy._tx() as conn:
        row = conn.execute(
            "SELECT user_id, amount FROM auction_bids WHERE run_id = ? AND lot = ? AND status = 'held' "
            "ORDER BY amount DESC, id ASC LIMIT 1", (run_id, lot)).fetchone()
        return {"user_id": int(row["user_id"]), "amount": int(row["amount"])} if row else None


def place_bid(run_id: int, lot: int, player_key: str, user_id: int, amount: int, base: int) -> dict:
    """One bid, all in one transaction. Returns {"ok": True, "prev": (uid, amount) | None, "balance"}
    or {"ok": False, "reason": ..., ...}. Reasons: no_user, banned, low (needs "min"),
    already_top, max_wins, no_coins (has "balance")."""
    uid = int(user_id)
    amount = int(amount)
    with economy._tx() as conn:
        user = conn.execute("SELECT purse, banned FROM users WHERE user_id = ?", (uid,)).fetchone()
        if user is None:
            return {"ok": False, "reason": "no_user"}
        if int(user["banned"] or 0):
            return {"ok": False, "reason": "banned"}
        top = conn.execute(
            "SELECT id, user_id, amount FROM auction_bids WHERE run_id = ? AND lot = ? AND status = 'held' "
            "ORDER BY amount DESC, id ASC LIMIT 1", (run_id, lot)).fetchone()
        need = min_next_bid(int(top["amount"]) if top else None, base)
        if top and int(top["user_id"]) == uid:
            return {"ok": False, "reason": "already_top"}
        if amount < need:
            return {"ok": False, "reason": "low", "min": need}
        wins = conn.execute("SELECT COUNT(*) FROM auction_bids WHERE run_id = ? AND user_id = ? AND status = 'won'",
                            (run_id, uid)).fetchone()[0]
        if wins >= MAX_WINS_PER_RUN:
            return {"ok": False, "reason": "max_wins"}
        if int(user["purse"]) < amount:
            return {"ok": False, "reason": "no_coins", "balance": int(user["purse"])}
        balance = economy._apply_coins(conn, uid, -amount, f"auction bid (lot {lot})")
        prev = None
        if top:
            conn.execute("UPDATE auction_bids SET status = 'refunded' WHERE id = ?", (top["id"],))
            economy._apply_coins(conn, int(top["user_id"]), int(top["amount"]), f"auction outbid refund (lot {lot})")
            prev = (int(top["user_id"]), int(top["amount"]))
        conn.execute("INSERT INTO auction_bids (run_id, lot, player_key, user_id, amount, ts) VALUES (?, ?, ?, ?, ?, ?)",
                     (run_id, lot, player_key, uid, amount, time.time()))
        return {"ok": True, "prev": prev, "balance": balance}


def settle_lot(run_id: int, lot: int, player_key: str) -> dict | None:
    """Lot over: the top bid wins (coins stay spent, card given). None = unsold."""
    with economy._tx() as conn:
        top = conn.execute(
            "SELECT id, user_id, amount FROM auction_bids WHERE run_id = ? AND lot = ? AND status = 'held' "
            "ORDER BY amount DESC, id ASC LIMIT 1", (run_id, lot)).fetchone()
        if not top:
            return None
        # any other held bid of this lot (should not exist) goes back
        for r in conn.execute("SELECT id, user_id, amount FROM auction_bids WHERE run_id = ? AND lot = ? "
                              "AND status = 'held' AND id != ?", (run_id, lot, top["id"])).fetchall():
            conn.execute("UPDATE auction_bids SET status = 'refunded' WHERE id = ?", (r["id"],))
            economy._apply_coins(conn, int(r["user_id"]), int(r["amount"]), f"auction refund (lot {lot})")
        conn.execute("UPDATE auction_bids SET status = 'won' WHERE id = ?", (top["id"],))
        economy._ensure_pending_table(conn)
        res = economy._grant(conn, int(top["user_id"]), player_key, "auction")
        if res == "blocked":          # card's event got disabled meanwhile: give the coins back
            conn.execute("UPDATE auction_bids SET status = 'refunded' WHERE id = ?", (top["id"],))
            economy._apply_coins(conn, int(top["user_id"]), int(top["amount"]), f"auction refund (lot {lot}, card blocked)")
            return None
        return {"user_id": int(top["user_id"]), "amount": int(top["amount"]), "pending": res == "pending"}


def refund_lot(run_id: int, lot: int) -> int:
    """Give back every held coin of a lot (auction cancelled). Returns how many bids were refunded."""
    with economy._tx() as conn:
        rows = conn.execute("SELECT id, user_id, amount FROM auction_bids WHERE run_id = ? AND lot = ? AND status = 'held'",
                            (run_id, lot)).fetchall()
        for r in rows:
            conn.execute("UPDATE auction_bids SET status = 'refunded' WHERE id = ?", (r["id"],))
            economy._apply_coins(conn, int(r["user_id"]), int(r["amount"]), f"auction refund (lot {lot})")
        return len(rows)


def recover() -> int:
    """Bot (re)start: an auction that was running is over; every held coin goes back."""
    with economy._tx() as conn:
        rows = conn.execute("SELECT id, user_id, amount, lot FROM auction_bids WHERE status = 'held'").fetchall()
        for r in rows:
            conn.execute("UPDATE auction_bids SET status = 'refunded' WHERE id = ?", (r["id"],))
            economy._apply_coins(conn, int(r["user_id"]), int(r["amount"]), f"auction refund (bot restart, lot {r['lot']})")
        conn.execute("UPDATE auction_runs SET status = 'aborted' WHERE status = 'running'")
        return len(rows)


def last_results(limit: int = 10) -> list:
    with economy._tx() as conn:
        return conn.execute(
            "SELECT b.player_key, b.user_id, b.amount, b.ts FROM auction_bids b WHERE b.status = 'won' "
            "ORDER BY b.ts DESC LIMIT ?", (limit,)).fetchall()


def user_wins(user_id: int, limit: int = 15) -> list:
    with economy._tx() as conn:
        return conn.execute("SELECT player_key, amount, ts FROM auction_bids WHERE user_id = ? AND status = 'won' "
                            "ORDER BY ts DESC LIMIT ?", (int(user_id), limit)).fetchall()
