"""Draft events (Halloween Draft, Diwali Draft ...), the same idea as the Star Pass seasons.

  * The normal draft is key "main" and works exactly as before.
  * An admin starts an EVENT draft with /draftmaker (name, duration, theme, background image).
    It runs NEXT TO the normal draft and has its own:
        - Pool A / Pool B cards (added with `/pool add ... event:<name>`)
        - Pool C OVR range, Pool A chance and "guaranteed every N drafts" counter
        - theme colours + background image (changeable any time with /draftbg)
        - timer (3D / 7D / 10D / 15D / 30D); when it ends nobody can draft in it any more
  * Vouchers are shared with the normal draft (one voucher balance for everything).

Tables (all created by economy._ensure_draft_tables, so nothing to run by hand):
    draft_event        one row per event run (event_key, name, theme, start/end, pity, odds, Pool C range)
    draft_event_bg     custom background image per event_key (also "main" = the normal draft)
    draft_event_cards  Pool A / B cards per event_key with their chance
    draft_event_pity   "drafts since last Pool A card" per (event run, user)
"""
from __future__ import annotations

import re
import time

import economy

MAIN = "main"                         # the normal draft
DAY = 86_400
DURATIONS = {"3D": 3, "7D": 7, "10D": 10, "15D": 15, "30D": 30}
# True = when a NEW event starts, every player keeps the "drafts since last Pool A card" counter
# they had in the previous event (so 40/50 in Halloween is still 40/50 in the next event).
CARRY_OVER_PITY = True
THEMES = ("default", "halloween", "diwali", "christmas")      # looks, see draft_image.THEMES
MAIN_NAMES = {"main", "normal", "draft", "default", "star"}


def norm_key(text) -> str:
    """'Halloween Draft!' -> 'halloween'. Empty / None -> 'main'."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    if s.endswith("-draft") and len(s) > 6:
        s = s[:-6]
    s = s[:30].strip("-")
    if not s or s in MAIN_NAMES:
        return MAIN
    return s


def fmt_left(seconds: float) -> str:
    """5d 3h / 7h 20m / 12m"""
    s = max(0, int(seconds))
    if s >= DAY:
        return f"{s // DAY}d {(s % DAY) // 3600}h"
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{max(1, s // 60)}m"


def _ensure(conn) -> None:
    economy._ensure_draft_tables(conn)


def _event_dict(row) -> dict:
    left = max(0, int(row["end_ts"] - time.time()))
    return {
        "id": int(row["id"]),
        "event_key": row["event_key"],
        "name": row["name"],
        "theme": row["theme"] or "default",
        "start_ts": float(row["start_ts"]),
        "end_ts": float(row["end_ts"]),
        "active": bool(row["active"]),
        "seconds_left": left,
        "days_left": left // DAY,
        "pity_every": int(row["pity_every"]),
        "odds_a": float(row["odds_a"]),
        "c_min": int(row["c_min"]),
        "c_max": int(row["c_max"]),
    }


def _active(conn, key: str):
    _ensure(conn)
    return conn.execute(
        "SELECT * FROM draft_event WHERE active = 1 AND end_ts > ? AND event_key = ? ORDER BY id DESC LIMIT 1",
        (time.time(), norm_key(key)),
    ).fetchone()


# ── Reading events ───────────────────────────────────────────────────────────

def get_active(key: str) -> dict | None:
    """The running event of this key, or None (also None when its time is up)."""
    with economy._tx() as conn:
        row = _active(conn, key)
        return _event_dict(row) if row else None


def list_active() -> list[dict]:
    """Every running event, oldest first."""
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT * FROM draft_event WHERE active = 1 AND end_ts > ? ORDER BY id", (time.time(),)
        ).fetchall()
        return [_event_dict(r) for r in rows]


def get_latest(key: str) -> dict | None:
    """The newest run of this key, running or not."""
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM draft_event WHERE event_key = ? ORDER BY id DESC LIMIT 1", (norm_key(key),)
        ).fetchone()
        return _event_dict(row) if row else None


def list_keys() -> list[tuple[str, str]]:
    """[(event_key, latest name)] of every event that ever existed (admin autocomplete)."""
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT event_key, name FROM draft_event ORDER BY id").fetchall()
    seen: dict[str, str] = {}
    for r in rows:
        seen[r["event_key"]] = r["name"]
    return list(seen.items())


# ── Create / end / reopen ────────────────────────────────────────────────────

def create_event(name: str, duration_key: str, admin_id: int = 0, theme: str = "default",
                 pity_every: int = 50, odds_a: float = 3.0, c_min: int = 60, c_max: int = 79) -> dict | str:
    """Start an event. Returns the event dict, or an error message (string)."""
    if duration_key not in DURATIONS:
        return "Pick a duration of 3D, 7D, 10D, 15D or 30D."
    if theme not in THEMES:
        return "Unknown theme."
    name = (name or "").strip()
    key = norm_key(name)
    if not name or key == MAIN:
        return "Give the event a name with letters or numbers, for example Halloween Draft."
    if not 2 <= int(pity_every) <= 1000:
        return "The guarantee must be between 2 and 1000 drafts."
    if not 0 <= float(odds_a) <= 100:
        return "The Pool A chance must be between 0 and 100."
    if not (1 <= int(c_min) <= int(c_max) <= 120):
        return "Pool C range is wrong (the minimum can't be above the maximum)."
    now = time.time()
    with economy._tx() as conn:
        running = _active(conn, key)
        if running is not None:
            e = _event_dict(running)
            return (f"{e['name']} is still running with {e['days_left']} day(s) left. "
                    f"End it first with /draftend (event {key}) before starting a new one.")
        n = int(conn.execute("SELECT COUNT(*) AS n FROM draft_event WHERE event_key = ?", (key,)).fetchone()["n"])
        shown = name[:40] if n == 0 else f"{name[:36]} {n + 1}"
        cur = conn.execute(
            "INSERT INTO draft_event (event_key, name, theme, start_ts, end_ts, active, created_by, "
            "pity_every, odds_a, c_min, c_max) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (key, shown, theme, now, now + DURATIONS[duration_key] * DAY, int(admin_id),
             int(pity_every), float(odds_a), int(c_min), int(c_max)),
        )
        new_id = int(cur.lastrowid)
        if CARRY_OVER_PITY:
            prev = conn.execute("SELECT MAX(id) AS pid FROM draft_event WHERE id < ?", (new_id,)).fetchone()["pid"]
            if prev is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO draft_event_pity (event_id, user_id, since_a) "
                    "SELECT ?, user_id, MIN(since_a, ?) FROM draft_event_pity WHERE event_id = ? AND since_a > 0",
                    (new_id, int(pity_every), int(prev)),
                )
        return _event_dict(conn.execute("SELECT * FROM draft_event WHERE id = ?", (new_id,)).fetchone())


def end_event(key: str) -> dict | None:
    """Stop the running event of this key right now. Returns it, or None if nothing was running."""
    with economy._tx() as conn:
        row = _active(conn, key)
        if row is None:
            return None
        conn.execute("UPDATE draft_event SET active = 0 WHERE id = ?", (row["id"],))
        return _event_dict(row)


def reopen_event(key: str, days: int) -> dict | str:
    """Bring the latest ended event of this key back for `days` days. Returns the event or an error text."""
    key = norm_key(key)
    if not 1 <= int(days) <= 60:
        return "Days must be between 1 and 60."
    with economy._tx() as conn:
        _ensure(conn)
        if _active(conn, key) is not None:
            return "That event is already running."
        row = conn.execute("SELECT * FROM draft_event WHERE event_key = ? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        if row is None:
            return "I could not find that event."
        conn.execute("UPDATE draft_event SET active = 1, end_ts = ? WHERE id = ?",
                     (time.time() + int(days) * DAY, row["id"]))
        return _event_dict(conn.execute("SELECT * FROM draft_event WHERE id = ?", (row["id"],)).fetchone())


# ── Backgrounds ──────────────────────────────────────────────────────────────

def set_background(key: str, data: bytes | None) -> None:
    """Save (or with None remove) the custom background of an event ('main' = the normal draft)."""
    key = norm_key(key)
    with economy._tx() as conn:
        _ensure(conn)
        if data is None:
            conn.execute("DELETE FROM draft_event_bg WHERE event_key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO draft_event_bg (event_key, data, ts) VALUES (?, ?, ?)",
                         (key, data, time.time()))


def get_background(key: str) -> tuple[bytes, float] | None:
    """(image bytes, saved-at timestamp) or None."""
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT data, ts FROM draft_event_bg WHERE event_key = ?", (norm_key(key),)).fetchone()
    return (bytes(r["data"]), float(r["ts"])) if r else None


# ── Pool A / B cards of an event ─────────────────────────────────────────────

def pool_set(key: str, pool: str, player_key: str, weight: float) -> None:
    """Put a card in Pool A or B of an event with its chance (adding again updates / moves it)."""
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO draft_event_cards (event_key, player_key, pool, weight) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(event_key, player_key) DO UPDATE SET pool = excluded.pool, weight = excluded.weight",
            (norm_key(key), player_key.strip().lower(), pool, float(weight)),
        )


def pool_remove(key: str, player_key: str) -> str | None:
    """Take a card out of an event pool. Returns the pool it was in, or None."""
    k, pk = norm_key(key), player_key.strip().lower()
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute("SELECT pool FROM draft_event_cards WHERE event_key = ? AND player_key = ?", (k, pk)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM draft_event_cards WHERE event_key = ? AND player_key = ?", (k, pk))
        return row["pool"]


def pool_cards(key: str) -> list[tuple[str, str, float]]:
    """[(pool, player_key, weight)] of one event."""
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT pool, player_key, weight FROM draft_event_cards WHERE event_key = ?", (norm_key(key),)
        ).fetchall()
    return [(r["pool"], r["player_key"], float(r["weight"])) for r in rows]


# ── Pool A counter of one event run ──────────────────────────────────────────

def get_pity(event_id: int, user_id) -> int:
    """How many drafts since the user's last Pool A card in this event."""
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT since_a FROM draft_event_pity WHERE event_id = ? AND user_id = ?",
            (int(event_id), economy._uid(user_id)),
        ).fetchone()
    return int(row["since_a"]) if row else 0
