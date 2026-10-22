"""Rename a card's player name (the ID / name used in commands).

Only `playername` changes. The big display name printed on the card image
(`displayname`) is NOT touched, so the card picture stays exactly the same.

Everyone who owns the card keeps it: the old name is replaced by the new one in
every table that points at a card (owned, XI, captain, impact sub, pending
cards, career stats, draft pool, commentary gifs, player media...).
All databases are changed in ONE transaction: it either all works or nothing changes.
"""
from __future__ import annotations

import sqlite3

import card_db
import economy

# Columns that hold a card key (the lowercase player name) in any table.
_KEY_COLUMNS = {"player_key", "captain_key", "playername_key"}
MAX_NAME_LEN = 60


def _cards_db_path() -> str:
    import gif_store
    return gif_store.DB_PATH          # cards.db (commentary gifs, player media)


def rename_card(old_name: str, new_name: str) -> tuple[str | None, int]:
    """Returns (error, rows_changed). error is None on success."""
    old_key = old_name.strip().lower()
    new_name = " ".join(new_name.split())
    new_key = new_name.lower()

    if not new_name:
        return "New name can't be empty.", 0
    if len(new_name) > MAX_NAME_LEN:
        return f"New name is too long (max {MAX_NAME_LEN} characters).", 0
    if "`" in new_name or "@" in new_name:
        return "New name can't contain ` or @.", 0

    card = card_db.get_card(old_key)
    if card is None:
        return f"No card named `{old_name}`.", 0
    if new_key != old_key and card_db.card_exists(new_key):
        return f"A card named **{new_name}** already exists. Pick a different name.", 0
    old_display_name = card["playername"]

    conn = sqlite3.connect(card_db.DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    changed = 0
    try:
        conn.execute("ATTACH DATABASE ? AS eco", (str(economy.DB_PATH),))
        conn.execute("ATTACH DATABASE ? AS cg", (str(_cards_db_path()),))
        conn.execute("BEGIN IMMEDIATE")
        try:
            if new_key != old_key:
                # If other cards were grouped under this card's old name, keep them grouped.
                if not card["player_group"]:
                    shared = conn.execute(
                        "SELECT 1 FROM main.cards WHERE player_group = ? LIMIT 1", (old_key,)
                    ).fetchone()
                    if shared:
                        conn.execute(
                            "UPDATE main.cards SET player_group = ? WHERE playername_key = ?",
                            (old_key, old_key),
                        )

                for schema in ("main", "eco", "cg"):
                    tables = [r[0] for r in conn.execute(
                        f"SELECT name FROM {schema}.sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                    for t in tables:
                        cols = {r[1] for r in conn.execute(f'PRAGMA {schema}.table_info("{t}")')}
                        for col in cols & _KEY_COLUMNS:
                            if schema == "main" and t == "cards" and col == "playername_key":
                                continue   # the cards row itself is done below
                            cur = conn.execute(
                                f'UPDATE OR REPLACE {schema}."{t}" SET "{col}" = ? WHERE "{col}" = ?',
                                (new_key, old_key),
                            )
                            changed += max(cur.rowcount, 0)
                # per-player gifs are stored by display-style name, matched without case
                if "player_name" in {r[1] for r in conn.execute('PRAGMA cg.table_info("player_media")')}:
                    cur = conn.execute(
                        'UPDATE OR REPLACE cg.player_media SET player_name = ? WHERE LOWER(player_name) = ?',
                        (new_name, old_display_name.lower()),
                    )
                    changed += max(cur.rowcount, 0)

            # The card itself: new key + new player name. displayname is left alone.
            conn.execute(
                "UPDATE main.cards SET playername_key = ?, playername = ? WHERE playername_key = ?",
                (new_key, new_name, old_key),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as e:
        return f"Database error, nothing was changed: {e}", 0
    finally:
        conn.close()
    return None, changed
