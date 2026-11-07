"""Spawn system storage (economy.db).

Tables
  spawn_config  (guild_id PK, channel_id, enabled)     one spawn channel per server
  spawn_block   (player_key PK, by_id, ts)             cards switched off for spawning (/spawn card)
  spawn_stats   (user_id PK, catches)                  every SPAWN_VOUCHER_EVERY catches = 1 Draft Voucher
  spawn_log     (id, guild_id, player_key, ts, caught_by, caught_ts)

Unob cards (obtainable = 0) and cards of a disabled event never spawn: the pool comes from
card_db.list_obtainable_cards(). Blocked cards are removed from it too.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import economy

SPAWN_VOUCHER_EVERY = 30          # every 30th catch gives 1 Draft Voucher
IST = timezone(timedelta(hours=5, minutes=30))


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS spawn_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, "
                 "enabled INTEGER NOT NULL DEFAULT 1)")
    conn.execute("CREATE TABLE IF NOT EXISTS spawn_block (player_key TEXT PRIMARY KEY, by_id INTEGER, ts REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS spawn_stats (user_id INTEGER PRIMARY KEY, catches INTEGER NOT NULL DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS spawn_log (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, "
                 "player_key TEXT, ts REAL, caught_by INTEGER, caught_ts REAL)")
    try:   # admin spawns (/admin cricketer spawn) don't use up the daily limit
        conn.execute("ALTER TABLE spawn_log ADD COLUMN manual INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass


def init() -> None:
    with economy._tx() as conn:
        _ensure(conn)


# ── config ────────────────────────────────────────────────────────────────────

def set_channel(guild_id: int, channel_id: int) -> None:
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute("INSERT INTO spawn_config (guild_id, channel_id, enabled) VALUES (?, ?, 1) "
                     "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id", (guild_id, channel_id))


def set_enabled(guild_id: int, enabled: bool) -> None:
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute("INSERT INTO spawn_config (guild_id, channel_id, enabled) VALUES (?, NULL, ?) "
                     "ON CONFLICT(guild_id) DO UPDATE SET enabled = excluded.enabled", (guild_id, 1 if enabled else 0))


def get_config(guild_id: int) -> dict | None:
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT channel_id, enabled FROM spawn_config WHERE guild_id = ?", (guild_id,)).fetchone()
    if r is None:
        return None
    return {"channel_id": r["channel_id"], "enabled": bool(r["enabled"])}


def all_configs() -> dict[int, dict]:
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT guild_id, channel_id, enabled FROM spawn_config").fetchall()
    return {int(r["guild_id"]): {"channel_id": r["channel_id"], "enabled": bool(r["enabled"])} for r in rows}


# ── per-card on/off ───────────────────────────────────────────────────────────

def set_card_spawn(player_key: str, allowed: bool, by_id: int | None = None) -> None:
    key = player_key.strip().lower()
    with economy._tx() as conn:
        _ensure(conn)
        if allowed:
            conn.execute("DELETE FROM spawn_block WHERE player_key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO spawn_block (player_key, by_id, ts) VALUES (?, ?, ?)",
                         (key, by_id, time.time()))


def blocked_keys() -> set[str]:
    with economy._tx() as conn:
        _ensure(conn)
        return {r["player_key"] for r in conn.execute("SELECT player_key FROM spawn_block")}


# ── log + stats ───────────────────────────────────────────────────────────────

def log_spawn(guild_id: int, player_key: str, manual: bool = False) -> int:
    with economy._tx() as conn:
        _ensure(conn)
        cur = conn.execute("INSERT INTO spawn_log (guild_id, player_key, ts, manual) VALUES (?, ?, ?, ?)",
                           (guild_id, player_key, time.time(), 1 if manual else 0))
        return int(cur.lastrowid)


def spawns_today(guild_id: int) -> int:
    now = datetime.now(IST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT COUNT(*) AS n FROM spawn_log WHERE guild_id = ? AND ts >= ? AND manual = 0", (guild_id, start)).fetchone()
    return int(r["n"])


def last_spawn(guild_id: int):
    with economy._tx() as conn:
        _ensure(conn)
        return conn.execute("SELECT player_key, ts, caught_by FROM spawn_log WHERE guild_id = ? ORDER BY id DESC LIMIT 1",
                            (guild_id,)).fetchone()


def get_catches(user_id: int) -> int:
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT catches FROM spawn_stats WHERE user_id = ?", (int(user_id),)).fetchone()
    return int(r["catches"]) if r else 0


def catch(user_id: int, player_key: str, log_id: int | None = None) -> dict:
    """Give the caught card (one transaction). Returns
    {"ok": True, "pending": bool, "catches": n, "voucher": bool}
    or {"ok": False, "reason": "missing" | "banned" | "blocked"}."""
    uid = int(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        u = conn.execute("SELECT banned FROM users WHERE user_id = ?", (uid,)).fetchone()
        if u is None:
            return {"ok": False, "reason": "missing"}
        if int(u["banned"] or 0):
            return {"ok": False, "reason": "banned"}
        economy._ensure_pending_table(conn)
        res = economy._grant(conn, uid, player_key, "spawn")
        if res == "blocked":
            return {"ok": False, "reason": "blocked"}
        conn.execute("INSERT INTO spawn_stats (user_id, catches) VALUES (?, 1) "
                     "ON CONFLICT(user_id) DO UPDATE SET catches = catches + 1", (uid,))
        n = int(conn.execute("SELECT catches FROM spawn_stats WHERE user_id = ?", (uid,)).fetchone()["catches"])
        voucher = n % SPAWN_VOUCHER_EVERY == 0
        if voucher:
            economy._voucher_change(conn, uid, 1, f"Spawn: {n} catches")
        if log_id:
            conn.execute("UPDATE spawn_log SET caught_by = ?, caught_ts = ? WHERE id = ?", (uid, time.time(), log_id))
        return {"ok": True, "pending": res == "pending", "catches": n, "voucher": voucher}
