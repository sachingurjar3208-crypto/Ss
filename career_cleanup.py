"""One-time cleanup after Career Mode was removed. Runs at start-up, safe to run again (does nothing
the second time).

  * every career card is taken out of its owner's squad / XI / captaincy / impact subs; the free XI
    spot is filled with the owner's best bench player
  * the career cards are deleted from the card database (and their image files)
  * coins spent on career drinks are given back in full
The career_* tables themselves are left alone (unused), so nothing else is touched.
"""
from __future__ import annotations

import os
import sqlite3

import card_db
import economy

REFUND_REASON = "career drinks refund"


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _career_card_keys() -> set[str]:
    keys: set[str] = set()
    try:
        with card_db._conn() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(cards)")}
            if "career_owner" in cols:
                for r in conn.execute("SELECT playername_key FROM cards WHERE career_owner IS NOT NULL"):
                    keys.add(r[0])
    except sqlite3.Error:
        pass
    return keys


def _fill_xi_slot(conn, uid: int, slot: int) -> None:
    if conn.execute("SELECT 1 FROM xi WHERE user_id = ? AND slot = ?", (uid, slot)).fetchone():
        return
    in_xi = {r["player_key"] for r in conn.execute("SELECT player_key FROM xi WHERE user_id = ?", (uid,))}
    bench = [r["player_key"] for r in conn.execute("SELECT player_key FROM owned WHERE user_id = ?", (uid,))
             if r["player_key"] not in in_xi]
    if not bench:
        return
    cards = card_db.get_cards(bench)
    best = max(cards.values(), key=lambda c: int(c["ovr"]), default=None)
    if best is not None:
        conn.execute("INSERT OR IGNORE INTO xi (user_id, slot, player_key) VALUES (?, ?, ?)",
                     (uid, slot, best["playername_key"]))


def run() -> None:
    keys = _career_card_keys()
    removed = refunded_users = refunded_coins = 0
    with economy._tx() as conn:
        if _table_exists(conn, "career_players"):
            cols = {r[1] for r in conn.execute("PRAGMA table_info(career_players)")}
            if "card_key" in cols:
                for r in conn.execute("SELECT card_key FROM career_players WHERE card_key IS NOT NULL"):
                    keys.add(r["card_key"])
        # 1) out of every squad / XI
        for key in keys:
            for row in conn.execute("SELECT user_id FROM owned WHERE player_key = ?", (key,)).fetchall():
                uid = int(row["user_id"])
                slot = economy._remove_copies(conn, uid, key, 10 ** 6)
                removed += 1
                if slot is not None:
                    _fill_xi_slot(conn, uid, slot)
        if keys and _table_exists(conn, "career_players"):
            conn.execute("UPDATE career_players SET card_key = NULL")
        # 2) drinks: give back everything that was spent (once)
        spent = conn.execute(
            "SELECT user_id, -SUM(delta) AS coins FROM ledger WHERE reason LIKE 'career drink %' "
            "GROUP BY user_id HAVING coins > 0").fetchall()
        for r in spent:
            uid, coins = int(r["user_id"]), int(r["coins"])
            back = conn.execute("SELECT COALESCE(SUM(delta), 0) AS c FROM ledger WHERE user_id = ? AND reason = ?",
                                (uid, REFUND_REASON)).fetchone()["c"]
            due = coins - int(back)
            if due > 0 and economy._apply_coins(conn, uid, due, REFUND_REASON) is not None:
                refunded_users += 1
                refunded_coins += due
    # 3) the cards themselves
    for key in keys:
        try:
            row = card_db.delete_card(key)
            path = row["image_path"] if row is not None else None
            if path and os.path.isfile(path):
                os.remove(path)
        except Exception as e:
            print(f"[career cleanup] could not delete card {key}: {type(e).__name__}: {e}")
    if keys or refunded_users:
        print(f"✓ Career cleanup: {len(keys)} career cards deleted, {removed} taken out of squads, "
              f"{refunded_coins:,} coins refunded to {refunded_users} players")
