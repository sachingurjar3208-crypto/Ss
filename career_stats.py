"""Per-card career stats.

Stats are saved once per innings (when the innings ends) and read back by
`csview`. Stored in economy.db so they live next to the squads.
"""
from __future__ import annotations

import sqlite3

import card_db
import economy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_career (
    player_key     TEXT PRIMARY KEY,
    bat_inns       INTEGER NOT NULL DEFAULT 0,
    runs           INTEGER NOT NULL DEFAULT 0,
    balls          INTEGER NOT NULL DEFAULT 0,
    fours          INTEGER NOT NULL DEFAULT 0,
    sixes          INTEGER NOT NULL DEFAULT 0,
    not_outs       INTEGER NOT NULL DEFAULT 0,
    ducks          INTEGER NOT NULL DEFAULT 0,
    fifties        INTEGER NOT NULL DEFAULT 0,
    hundreds       INTEGER NOT NULL DEFAULT 0,
    highest        INTEGER NOT NULL DEFAULT 0,
    bowl_inns      INTEGER NOT NULL DEFAULT 0,
    balls_bowled   INTEGER NOT NULL DEFAULT 0,
    runs_conceded  INTEGER NOT NULL DEFAULT 0,
    wickets        INTEGER NOT NULL DEFAULT 0,
    maidens        INTEGER NOT NULL DEFAULT 0,
    three_fers     INTEGER NOT NULL DEFAULT 0,
    five_fers      INTEGER NOT NULL DEFAULT 0,
    best_w         INTEGER NOT NULL DEFAULT 0,
    best_r         INTEGER NOT NULL DEFAULT 0
)
"""

_ZERO = {
    "bat_inns": 0, "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
    "not_outs": 0, "ducks": 0, "fifties": 0, "hundreds": 0, "highest": 0,
    "bowl_inns": 0, "balls_bowled": 0, "runs_conceded": 0, "wickets": 0,
    "maidens": 0, "three_fers": 0, "five_fers": 0, "best_w": 0, "best_r": 0,
}


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)


def _key_for(name: str) -> str | None:
    """Map an in-match player name to a card key (None if not a card)."""
    card = card_db.get_card(name)
    if card is None:
        found = card_db.find_cards(name, limit=1)
        card = found[0] if found else None
    return card["playername_key"] if card else None


def save_innings(game, is_final: bool = False) -> None:
    """Add this innings' batting and bowling figures to each card's career.

    Safe to call more than once for the same innings: it only saves once.
    """
    inn = getattr(game, "innings", 0)
    saved: set = getattr(game, "_career_saved_innings", set())
    if inn in saved:
        return
    saved.add(inn)
    game._career_saved_innings = saved

    dismissed = set(getattr(game, "dismissed", []) or [])

    bat_rows: dict[str, dict] = {}
    for name, s in (game.batsman_stats or {}).items():
        balls = int(s.get("balls", s.get("b", 0)) or 0)
        runs = int(s.get("runs", s.get("r", 0)) or 0)
        if balls == 0 and runs == 0 and name not in dismissed:
            continue  # never came in
        key = _key_for(name)
        if not key:
            continue
        out = name in dismissed
        d = bat_rows.setdefault(key, dict(_ZERO))
        d["bat_inns"] += 1
        d["runs"] += runs
        d["balls"] += balls
        d["fours"] += int(s.get("fours", 0) or 0)
        d["sixes"] += int(s.get("sixes", 0) or 0)
        d["not_outs"] += 0 if out else 1
        if out and runs == 0:
            d["ducks"] += 1
        if runs >= 100:
            d["hundreds"] += 1
        elif runs >= 50:
            d["fifties"] += 1
        d["highest"] = max(d["highest"], runs)

    bowl_rows: dict[str, dict] = {}
    for name, b in (game.bowler_stats or {}).items():
        balls = int(b.get("b", b.get("balls", 0)) or 0)
        if balls == 0:
            continue
        key = _key_for(name)
        if not key:
            continue
        wkts = int(b.get("w", b.get("wickets", 0)) or 0)
        runs = int(b.get("r", b.get("runs", 0)) or 0)
        maidens = int(b.get("m", 0) or 0)
        d = bowl_rows.setdefault(key, dict(_ZERO))
        d["bowl_inns"] += 1
        d["balls_bowled"] += balls
        d["runs_conceded"] += runs
        d["wickets"] += wkts
        d["maidens"] += maidens
        if wkts >= 5:
            d["five_fers"] += 1
        elif wkts >= 3:
            d["three_fers"] += 1
        if wkts > d["best_w"] or (wkts == d["best_w"] and wkts and runs < d["best_r"]):
            d["best_w"], d["best_r"] = wkts, runs

    keys = set(bat_rows) | set(bowl_rows)
    if not keys:
        return
    with economy._tx() as conn:
        _ensure_table(conn)
        for key in keys:
            b = bat_rows.get(key, dict(_ZERO))
            w = bowl_rows.get(key, dict(_ZERO))
            conn.execute("INSERT OR IGNORE INTO card_career (player_key) VALUES (?)", (key,))
            cur = conn.execute("SELECT best_w, best_r, highest FROM card_career WHERE player_key = ?", (key,)).fetchone()
            best_w, best_r = cur["best_w"], cur["best_r"]
            if w["best_w"] > best_w or (w["best_w"] == best_w and w["best_w"] > 0 and w["best_r"] < best_r):
                best_w, best_r = w["best_w"], w["best_r"]
            highest = max(cur["highest"], b["highest"])
            conn.execute(
                """
                UPDATE card_career SET
                    bat_inns = bat_inns + ?, runs = runs + ?, balls = balls + ?,
                    fours = fours + ?, sixes = sixes + ?, not_outs = not_outs + ?,
                    ducks = ducks + ?, fifties = fifties + ?, hundreds = hundreds + ?,
                    highest = ?,
                    bowl_inns = bowl_inns + ?, balls_bowled = balls_bowled + ?,
                    runs_conceded = runs_conceded + ?, wickets = wickets + ?,
                    maidens = maidens + ?, three_fers = three_fers + ?,
                    five_fers = five_fers + ?, best_w = ?, best_r = ?
                WHERE player_key = ?
                """,
                (
                    b["bat_inns"], b["runs"], b["balls"], b["fours"], b["sixes"],
                    b["not_outs"], b["ducks"], b["fifties"], b["hundreds"], highest,
                    w["bowl_inns"], w["balls_bowled"], w["runs_conceded"], w["wickets"],
                    w["maidens"], w["three_fers"], w["five_fers"], best_w, best_r, key,
                ),
            )


def get_career(player_key: str) -> dict:
    """Career totals for one card (all zeros if it has never played)."""
    with economy._tx() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM card_career WHERE player_key = ?", (player_key,)
        ).fetchone()
    if row is None:
        return dict(_ZERO)
    return {k: row[k] for k in _ZERO}


def batting_figures(c: dict) -> list[tuple[str, str]]:
    dismissals = c["bat_inns"] - c["not_outs"]
    avg = f"{c['runs'] / dismissals:.1f}" if dismissals > 0 else "-"
    sr = f"{c['runs'] / c['balls'] * 100:.1f}" if c["balls"] else "0.0"
    return [
        ("Inns", str(c["bat_inns"])),
        ("Runs", str(c["runs"])),
        ("50s", str(c["fifties"])),
        ("100s", str(c["hundreds"])),
        ("4/6", f"{c['fours']}/{c['sixes']}"),
        ("Avg", avg),
        ("SR", sr),
        ("Ducks", str(c["ducks"])),
        ("HS", str(c["highest"]) if c["bat_inns"] else "-"),
    ]


def bowling_figures(c: dict) -> list[tuple[str, str]]:
    overs = c["balls_bowled"] / 6
    avg = f"{c['runs_conceded'] / c['wickets']:.1f}" if c["wickets"] else "-"
    econ = f"{c['runs_conceded'] / overs:.1f}" if overs else "0.0"
    sr = f"{c['balls_bowled'] / c['wickets']:.1f}" if c["wickets"] else "-"
    bbf = f"{c['best_w']}/{c['best_r']}" if c["best_w"] else "-"
    return [
        ("Inns", str(c["bowl_inns"])),
        ("Wickets", str(c["wickets"])),
        ("3-Fers", str(c["three_fers"])),
        ("5-Fers", str(c["five_fers"])),
        ("Avg", avg),
        ("Economy", econ),
        ("SR", sr),
        ("Maidens", str(c["maidens"])),
        ("BBF", bbf),
    ]
