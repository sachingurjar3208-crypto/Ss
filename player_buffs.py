"""Hidden per-player match buffs / nerfs (owner & staff tool).

A buff is a percentage stored per cricket player (by name). It is applied to the match stats of
that player only, by role:
  Batter / WK      -> batting only
  Bowler           -> bowling only
  All-rounder (AR) -> batting AND bowling
Positive % = buff, negative % = nerf.

Nothing here is ever shown to players: no message, no card change, no text. Admins see the
result only in their own DM (see ranked_cog.py: csbuff / csbuffoff / csbufflist).
"""
from __future__ import annotations

import time

import economy

MIN_PCT = -90.0
MAX_PCT = 100.0

_DDL = (
    "CREATE TABLE IF NOT EXISTS player_buffs ("
    "player_key TEXT PRIMARY KEY, pct REAL NOT NULL, ts REAL NOT NULL, admin_id INTEGER NOT NULL)"
)


def norm_key(name) -> str:
    """Player name -> key: lower-case, no spaces at the ends, '(LOR)' suffix removed (same card)."""
    s = str(name or "").strip()
    if s.lower().endswith("(lor)"):
        s = s[:-5].strip()
    return s.lower()


def apply(role: str | None, bat: int, bowl: int, pct: float) -> tuple[int, int]:
    """Return (bat, bowl) after the buff/nerf for this role. Stats never go below 0."""
    if not pct:
        return bat, bowl
    mult = 1.0 + float(pct) / 100.0
    r = (role or "").upper()
    hit_bat = r not in ("BOWL", "LBOWL", "RBOWL")       # Batter, WK, All-rounder, unknown
    hit_bowl = r in ("BOWL", "LBOWL", "RBOWL", "AR")    # Bowler, All-rounder
    if hit_bat:
        bat = max(0, int(round(bat * mult)))
    if hit_bowl:
        bowl = max(0, int(round(bowl * mult)))
    return bat, bowl


def load_map() -> dict[str, float]:
    """{player_key: pct} for every active buff. Empty dict on any DB error (never blocks a match)."""
    try:
        conn = economy._conn()
        try:
            conn.execute(_DDL)
            return {r["player_key"]: float(r["pct"])
                    for r in conn.execute("SELECT player_key, pct FROM player_buffs")}
        finally:
            conn.close()
    except Exception as e:
        print(f"[player_buffs] load failed: {type(e).__name__}: {e}")
        return {}


def set_buff(admin_id, player_name: str, pct: float) -> str:
    """Set (or with pct 0, clear) a buff. Returns the stored key. Raises ValueError on bad input."""
    key = norm_key(player_name)
    if not key:
        raise ValueError("empty player name")
    pct = float(pct)
    if not (MIN_PCT <= pct <= MAX_PCT):
        raise ValueError(f"percent must be between {MIN_PCT:g} and {MAX_PCT:g}")
    with economy._tx() as conn:
        conn.execute(_DDL)
        if pct == 0:
            conn.execute("DELETE FROM player_buffs WHERE player_key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO player_buffs (player_key, pct, ts, admin_id) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(player_key) DO UPDATE SET pct = excluded.pct, ts = excluded.ts, "
                "admin_id = excluded.admin_id",
                (key, pct, time.time(), int(admin_id)),
            )
    return key


def clear_buff(admin_id, player_name: str) -> bool:
    """Remove a buff. True if one existed."""
    key = norm_key(player_name)
    with economy._tx() as conn:
        conn.execute(_DDL)
        cur = conn.execute("DELETE FROM player_buffs WHERE player_key = ?", (key,))
        return cur.rowcount > 0


def list_buffs() -> list[tuple[str, float]]:
    conn = economy._conn()
    try:
        conn.execute(_DDL)
        return [(r["player_key"], float(r["pct"]))
                for r in conn.execute("SELECT player_key, pct FROM player_buffs ORDER BY player_key")]
    finally:
        conn.close()
