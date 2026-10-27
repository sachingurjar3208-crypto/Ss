"""Ranked mode (csmp ranked): stars, tiers, rewards, monthly season reset, bot opponents.

No Discord code here (except the tiny FakeMember stand-in), so it is easy to test.

HOW IT WORKS
- Win a ranked match = +1 star, lose = -1 star (never below 0). Ties change nothing.
- Tiers by stars: Bronze 0 | Silver 20 | Gold 40 | Platinum 60 | Legendary 100 | Ruby 200.
- Reaching a tier gives a ONE-TIME reward per season (TIER_UP_REWARDS).
- After Ruby stars keep counting (no more tier rewards): the global top list is by stars.
- Every month (UTC) the season resets automatically: everyone goes back to 0 stars and
  gets the SEASON_REWARDS for the tier they finished in. Rewards that arrive while the
  user is offline are shown to them the next time they use csrank / csmp ranked.
- Matchmaking only pairs players of the SAME tier. If nobody is found for BOT_WAIT_SECONDS,
  a bot with a human-looking name (BOT_NAMES) plays instead. Its strength depends on the
  tier (BOT_PROFILES): Bronze = easy ... Ruby = very hard.

All numbers you may want to tune are at the top of this file.
"""

from __future__ import annotations

import random
import secrets
import time
from datetime import datetime, timezone

import card_db
import economy
import emojis
import squad_logic as sl
from card_narratives import ENGINE_ROLE

_rng = secrets.SystemRandom()

# ── Tunable numbers ──────────────────────────────────────────────────────────
RANKED_OVERS = 2             # every ranked match is exactly this many overs
BOT_WAIT_SECONDS = 60        # nobody found for this long -> a bot opponent plays
SEARCH_TIMEOUT = 300         # global search gives up after 5 minutes (only if no bot can be made)

# Seasons are started and ended by the owner (csseasonstart / csseasonend). A season ends by itself
# after SEASON_DAYS days. Only players with MORE than 0 stars at the end get the season reward.
SEASON_DAYS = 30

# Matchmaking scope: True = players from ANY server can be matched (the match is shown in both
# players' channels, see ranked_mirror.py). False = same server only.
CROSS_SERVER = True

# Anti-alt rules
MIN_STARS = -5               # stars can go below 0 (only losing on Bronze gets you here); never below this
BLACKLIST_STARS = -5         # reaching this many stars (Bronze only, since nobody else can) = blacklisted
BLACKLIST_DAYS = 30          # ranked ban length
RECENT_OPP_BLOCK = 3         # after facing someone, you can't be matched with them for your next 3 matches

TIERS = [
    {"name": "Bronze",    "emoji": "🟤", "min": 0},
    {"name": "Silver",    "emoji": "⚪", "min": 20},
    {"name": "Gold",      "emoji": "🟡", "min": 40},
    {"name": "Platinum",  "emoji": "🔷", "min": 60},
    {"name": "Legendary", "emoji": "🔥", "min": 100},
    {"name": "Ruby",      "emoji": "♦️", "min": 200},
]
TIER_BY_NAME = {t["name"]: t for t in TIERS}

# One-time reward the first time a tier is reached in a season.
# "card": (min ovr, max ovr) of the random card.
TIER_UP_REWARDS = {
    "Silver":    {"vouchers": 2},
    "Gold":      {"vouchers": 5,  "card": (87, 87)},
    "Platinum":  {"vouchers": 8,  "card": (88, 88)},
    "Legendary": {"vouchers": 15, "card": (92, 92)},
    "Ruby":      {"vouchers": 20, "card": (95, 95)},
}

# Paid at the monthly reset, by the tier the player finished the season in.
SEASON_REWARDS = {
    "Ruby":      {"packs": {"legendary": 1}, "vouchers": 5, "coins": 500_000},
    "Legendary": {"vouchers": 10, "card": (84, 87), "coins": 300_000},
    "Platinum":  {"vouchers": 5, "coins": 200_000},
    "Gold":      {"vouchers": 5, "coins": 100_000},
    "Silver":    {"vouchers": 5},
    "Bronze":    {"coins": 50_000},
}

# Bot strength per tier. ovr = card rating range its XI is built from,
# skill = 0..1 chance it makes the smart choice (best shot, best bowler/batter ...).
BOT_PROFILES = {
    "Bronze":    {"ovr": (60, 70), "skill": 0.10},   # easy
    "Silver":    {"ovr": (66, 76), "skill": 0.30},
    "Gold":      {"ovr": (72, 82), "skill": 0.50},
    "Platinum":  {"ovr": (78, 88), "skill": 0.70},
    "Legendary": {"ovr": (85, 94), "skill": 0.85},
    "Ruby":      {"ovr": (90, 99), "skill": 1.00},   # very hard: best cards + always the smart choice
}


# ── Tiers ────────────────────────────────────────────────────────────────────

def tier_of(stars: int) -> str:
    name = TIERS[0]["name"]
    for t in TIERS:
        if stars >= t["min"]:
            name = t["name"]
    return name


def tier_emoji(name: str) -> str:
    return TIER_BY_NAME[name]["emoji"]


def next_tier(stars: int):
    """(tier dict, stars still needed) for the next tier, or None at the top (Ruby)."""
    base = max(stars, 0)   # stars can be negative on Bronze; the next tier is still Silver
    for t in TIERS:
        if base < t["min"]:
            return t, t["min"] - stars
    return None


def _calendar_season_name(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def _calendar_month_end(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    y, m = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp())


def next_reset_ts(now: datetime | None = None) -> int:
    """Unix time the current season ends (set when the owner starts it; 0 if no season is running)."""
    info = season_info()
    return int(info["end"]) if info["active"] else 0


# ── Database ─────────────────────────────────────────────────────────────────
# Lives in economy.db. Tables are created on first use (init_economy_db isn't run at start-up).

_PLAYERS_DDL = (
    "CREATE TABLE IF NOT EXISTS ranked_players ("
    "user_id INTEGER PRIMARY KEY, stars INTEGER NOT NULL DEFAULT 0, "
    "played INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0, "
    "losses INTEGER NOT NULL DEFAULT 0, claimed TEXT NOT NULL DEFAULT '', "
    "blacklist_until REAL NOT NULL DEFAULT 0)"
)
_SCHEMA_OK = False


def _migrate_players(conn) -> None:
    """Old databases have `CHECK (stars >= 0)` on ranked_players and no blacklist column.
    SQLite can't drop a CHECK, so the table is rebuilt once (data kept). Runs once per process."""
    global _SCHEMA_OK
    if _SCHEMA_OK:
        return
    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ranked_players'").fetchone()
        sql = (row["sql"] if row else "") or ""
        if "CHECK" in sql.upper():
            conn.execute("ALTER TABLE ranked_players RENAME TO ranked_players_old")
            conn.execute(_PLAYERS_DDL)
            conn.execute(
                "INSERT INTO ranked_players (user_id, stars, played, wins, losses, claimed) "
                "SELECT user_id, stars, played, wins, losses, claimed FROM ranked_players_old"
            )
            conn.execute("DROP TABLE ranked_players_old")
        elif "blacklist_until" not in sql:
            conn.execute("ALTER TABLE ranked_players ADD COLUMN blacklist_until REAL NOT NULL DEFAULT 0")
        if own_tx:
            conn.execute("COMMIT")
    except BaseException:
        if own_tx:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        raise
    _SCHEMA_OK = True


def _ensure(conn) -> None:
    conn.execute(_PLAYERS_DDL)
    _migrate_players(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS ranked_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ranked_notices ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ranked_history ("
        "season TEXT NOT NULL, user_id INTEGER NOT NULL, stars INTEGER NOT NULL, tier TEXT NOT NULL)"
    )
    # last opponents of every player (opp_id 0 = a bot), used to stop alt farming
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ranked_recent ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, opp_id INTEGER NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ranked_recent_user ON ranked_recent (user_id)")


def _row(conn, uid: int):
    conn.execute("INSERT OR IGNORE INTO ranked_players (user_id) VALUES (?)", (uid,))
    return conn.execute("SELECT * FROM ranked_players WHERE user_id = ?", (uid,)).fetchone()


def _user_exists(conn, uid: int) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is not None


# ── Rewards ──────────────────────────────────────────────────────────────────

def _cards_index():
    rows = {c["playername_key"]: c for c in card_db.list_obtainable_cards()}
    by_ovr: dict[int, list[str]] = {}
    for k, c in rows.items():
        by_ovr.setdefault(int(c["ovr"]), []).append(k)
    return by_ovr, rows


def _grant_random_card(conn, uid: int, lo: int, hi: int, source: str, idx) -> dict | None:
    """Random card with OVR lo..hi (nearest OVR if none exists). A card the user doesn't own
    is preferred; if they own them all it becomes coins (same as duplicates everywhere else)."""
    by_ovr, rows = idx
    pool = [k for o in range(lo, hi + 1) for k in by_ovr.get(o, [])]
    if not pool and by_ovr:
        mid = (lo + hi) / 2
        best = min(by_ovr, key=lambda o: abs(o - mid))
        pool = list(by_ovr[best])
    if not pool:
        return None
    economy._ensure_pending_table(conn)
    owned = {r["player_key"] for r in conn.execute("SELECT player_key FROM owned WHERE user_id = ?", (uid,))}
    owned |= {r["player_key"] for r in conn.execute("SELECT player_key FROM pending_cards WHERE user_id = ?", (uid,))}
    fresh = [k for k in pool if k not in owned]
    if fresh:
        key = _rng.choice(fresh)
        return {"key": key, "status": economy._grant(conn, uid, key, source), "refund": 0}
    key = _rng.choice(pool)
    refund = int(sl.sell_value(rows[key]))
    if refund:
        economy._apply_coins(conn, uid, refund, f"Duplicate refund ({source})")
    return {"key": key, "status": "dupe", "refund": refund}


def _grant_bundle(conn, uid: int, spec: dict, source: str, idx_cache: list) -> tuple[list[str], bool]:
    """Give one reward bundle inside the open transaction. Returns (text lines, card_is_pending)."""
    lines: list[str] = []
    pending = False
    if spec.get("vouchers"):
        n = int(spec["vouchers"])
        economy._voucher_change(conn, uid, n, source)
        lines.append(f"{emojis.VOUCHER} **{n}** vouchers")
    if spec.get("coins"):
        n = int(spec["coins"])
        economy._apply_coins(conn, uid, n, source)
        lines.append(f"💰 **{n:,}** coins")
    for pack, qty in (spec.get("packs") or {}).items():
        conn.execute(
            "INSERT INTO packs (user_id, pack_type, qty) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, pack_type) DO UPDATE SET qty = qty + excluded.qty",
            (uid, pack, int(qty)),
        )
        label = sl.PACKS.get(pack, {}).get("label", pack)
        lines.append(f"📦 **{int(qty)}× {label}**")
    if spec.get("card"):
        if not idx_cache:
            idx_cache.append(_cards_index())
        lo, hi = spec["card"]
        res = _grant_random_card(conn, uid, lo, hi, source, idx_cache[0])
        if res is not None:
            card = idx_cache[0][1][res["key"]]
            text = f"🎴 **{card['playername']}** ({card['ovr']} OVR)"
            if res["status"] == "dupe":
                text += f" — you already had it, sold for **{res['refund']:,}** coins"
            elif res["status"] == "pending":
                text += " — squad full, decide with `cspending`"
                pending = True
            lines.append(text)
    return lines, pending


# ── Season rollover ──────────────────────────────────────────────────────────
# ranked_meta keys: season (name), season_start, season_end (unix), season_active ("1"/"0"), season_no.

def _meta_get(conn, key: str, default=None):
    r = conn.execute("SELECT value FROM ranked_meta WHERE key = ?", (key,)).fetchone()
    return r["value"] if r is not None else default


def _meta_set(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO ranked_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def _ensure_season_meta(conn) -> None:
    """Old databases (monthly seasons) have no season_end: keep their running season until the end of
    that calendar month, then it behaves like every other season (owner starts the next one)."""
    if _meta_get(conn, "season_end") is not None:
        return
    name = _meta_get(conn, "season") or _calendar_season_name()
    _meta_set(conn, "season", name)
    _meta_set(conn, "season_start", int(time.time()))
    _meta_set(conn, "season_end", _calendar_month_end())
    _meta_set(conn, "season_active", "1")


def season_info() -> dict:
    """{"active": bool, "name": str, "start": int, "end": int}"""
    with economy._tx() as conn:
        _ensure(conn)
        _ensure_season_meta(conn)
        return {
            "active": _meta_get(conn, "season_active", "1") == "1",
            "name": _meta_get(conn, "season", ""),
            "start": int(float(_meta_get(conn, "season_start", 0))),
            "end": int(float(_meta_get(conn, "season_end", 0))),
        }


def season_active() -> bool:
    return season_info()["active"]


def _settle_season(conn, old_season: str) -> dict:
    """Pay the season-end rewards to everyone with MORE than 0 stars, then reset every player.
    Each reward message is saved in ranked_notices (the cog DMs it; csrank shows it if the DM failed)."""
    idx_cache: list = []
    rows = conn.execute("SELECT * FROM ranked_players").fetchall()
    paid = 0
    for prow in rows:
        uid, stars = int(prow["user_id"]), int(prow["stars"])
        if stars <= 0 or not _user_exists(conn, uid):
            continue
        tier = tier_of(stars)
        spec = SEASON_REWARDS.get(tier)
        if not spec:
            continue
        lines, pending = _grant_bundle(conn, uid, spec, f"Ranked {old_season} ({tier})", idx_cache)
        text = (
            f"🏁 **Ranked {old_season} ended!** You finished {tier_emoji(tier)} **{tier}** "
            f"with **{stars}** ⭐\n🎁 Rewards: " + " · ".join(lines)
            + ("\n⚠️ A card is waiting for you: use `cspending`." if pending else "")
        )
        conn.execute("INSERT INTO ranked_notices (user_id, text, ts) VALUES (?, ?, ?)", (uid, text, time.time()))
        conn.execute(
            "INSERT INTO ranked_history (season, user_id, stars, tier) VALUES (?, ?, ?, ?)",
            (old_season, uid, stars, tier),
        )
        paid += 1
    conn.execute("UPDATE ranked_players SET stars = 0, played = 0, wins = 0, losses = 0, claimed = ''")
    conn.execute("DELETE FROM ranked_notices WHERE ts < ?", (time.time() - 60 * 86400,))
    return {"season": old_season, "paid": paid}


def _rollover_locked(conn) -> dict | None:
    """Ends the running season if its 30 days are over. Ranked then waits for the owner's csseasonstart."""
    _ensure_season_meta(conn)
    if _meta_get(conn, "season_active", "1") != "1":
        return None
    if time.time() < float(_meta_get(conn, "season_end", 0)):
        return None
    summary = _settle_season(conn, _meta_get(conn, "season", ""))
    _meta_set(conn, "season_active", "0")
    return summary


def rollover_if_needed() -> dict | None:
    """Call any time (the cog calls it every few minutes). Pays season rewards once the season's end passes."""
    conn = economy._conn()
    try:
        _ensure(conn)
        end = _meta_get(conn, "season_end")
        active = _meta_get(conn, "season_active", "1") == "1"
        if end is not None and (not active or time.time() < float(end)):
            return None
    finally:
        conn.close()
    with economy._tx() as conn:
        _ensure(conn)
        return _rollover_locked(conn)


def start_season(admin_id, days: int | None = None) -> dict:
    """Owner: start a new season now; it ends by itself `days` (default 30) days later."""
    days = int(days or SEASON_DAYS)
    with economy._tx() as conn:
        _ensure(conn)
        _ensure_season_meta(conn)
        _rollover_locked(conn)
        if _meta_get(conn, "season_active", "1") == "1":
            return {"ok": False, "name": _meta_get(conn, "season", ""), "end": int(float(_meta_get(conn, "season_end", 0)))}
        no = int(_meta_get(conn, "season_no", 0)) + 1
        name = f"Season {no}"
        now = int(time.time())
        conn.execute("UPDATE ranked_players SET stars = 0, played = 0, wins = 0, losses = 0, claimed = ''")
        _meta_set(conn, "season_no", no)
        _meta_set(conn, "season", name)
        _meta_set(conn, "season_start", now)
        _meta_set(conn, "season_end", now + days * 86400)
        _meta_set(conn, "season_active", "1")
        _admin_log(conn, admin_id, 0, "season_start", f"{name}, {days} days")
        return {"ok": True, "name": name, "start": now, "end": now + days * 86400, "days": days}


def end_season_now(admin_id) -> dict | None:
    """Owner: end the running season right now (rewards paid, stars reset). None if no season is running."""
    with economy._tx() as conn:
        _ensure(conn)
        _ensure_season_meta(conn)
        if _meta_get(conn, "season_active", "1") != "1":
            return None
        summary = _settle_season(conn, _meta_get(conn, "season", ""))
        _meta_set(conn, "season_active", "0")
        _admin_log(conn, admin_id, 0, "season_end", f"{summary['season']}, paid {summary['paid']}")
        return summary


def list_notices() -> list[dict]:
    """Every season-end message still waiting (id, user_id, text), oldest first."""
    conn = economy._conn()
    try:
        _ensure(conn)
        return [{"id": int(r["id"]), "user_id": int(r["user_id"]), "text": r["text"]}
                for r in conn.execute("SELECT id, user_id, text FROM ranked_notices ORDER BY id")]
    finally:
        conn.close()


def delete_notice(notice_id: int) -> None:
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute("DELETE FROM ranked_notices WHERE id = ?", (int(notice_id),))


# ── Reading ──────────────────────────────────────────────────────────────────

def get_player(user_id) -> dict:
    rollover_if_needed()
    uid = int(user_id)
    conn = economy._conn()
    try:
        _ensure(conn)
        r = conn.execute("SELECT * FROM ranked_players WHERE user_id = ?", (uid,)).fetchone()
        stars = int(r["stars"]) if r else 0
        return {
            "stars": stars, "tier": tier_of(stars),
            "played": int(r["played"]) if r else 0,
            "wins": int(r["wins"]) if r else 0,
            "losses": int(r["losses"]) if r else 0,
            "blacklist_until": float(r["blacklist_until"] or 0) if r else 0.0,
        }
    finally:
        conn.close()


def global_position(user_id) -> int | None:
    """1-based place on the global star list (None if 0 stars)."""
    uid = int(user_id)
    conn = economy._conn()
    try:
        _ensure(conn)
        r = conn.execute("SELECT stars FROM ranked_players WHERE user_id = ?", (uid,)).fetchone()
        if r is None or int(r["stars"]) <= 0:
            return None
        n = conn.execute("SELECT COUNT(*) AS n FROM ranked_players WHERE stars > ?", (int(r["stars"]),)).fetchone()["n"]
        return int(n) + 1
    finally:
        conn.close()


def top_players(limit: int = 10) -> list[dict]:
    conn = economy._conn()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT user_id, stars, wins, losses FROM ranked_players WHERE stars > 0 "
            "ORDER BY stars DESC, wins DESC LIMIT ?", (int(limit),),
        ).fetchall()
        return [{"user_id": int(r["user_id"]), "stars": int(r["stars"]),
                 "wins": int(r["wins"]), "losses": int(r["losses"]), "tier": tier_of(int(r["stars"]))} for r in rows]
    finally:
        conn.close()


def pop_notices(user_id) -> list[str]:
    """Season-end messages waiting for this user (shown once, then deleted)."""
    uid = int(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT id, text FROM ranked_notices WHERE user_id = ? ORDER BY id", (uid,)).fetchall()
        if rows:
            conn.execute("DELETE FROM ranked_notices WHERE user_id = ?", (uid,))
        return [r["text"] for r in rows]


# ── Match result ─────────────────────────────────────────────────────────────

def apply_result(winner_id, loser_id) -> dict:
    """Winner +1 star, loser -1 star (min MIN_STARS: Bronze can go negative), tier-up rewards for the winner.
    A loser who reaches BLACKLIST_STARS is banned from ranked for BLACKLIST_DAYS (info["blacklist_until"]).
    Bot players (negative ids) are skipped. Returns {user_id: info} for the real players:
    info = {won, old, new, delta, tier_old, tier_new, rewards: [text], pending: bool}."""
    out: dict[int, dict] = {}
    w, l = int(winner_id), int(loser_id)
    with economy._tx() as conn:
        _ensure(conn)
        _rollover_locked(conn)
        if _meta_get(conn, "season_active", "1") != "1":
            return out        # no season running (it just ended): this match gives no stars
        idx_cache: list = []
        for uid, delta, won in ((w, +1, True), (l, -1, False)):
            if uid <= 0 or not _user_exists(conn, uid):
                continue
            row = _row(conn, uid)
            old = int(row["stars"])
            new = max(MIN_STARS, old + delta)
            blacklist_until = 0.0
            if not won and new <= BLACKLIST_STARS:
                blacklist_until = time.time() + BLACKLIST_DAYS * 86400
            claimed = {t for t in (row["claimed"] or "").split(",") if t}
            rewards: list[str] = []
            pending = False
            if won:
                for t in TIERS[1:]:
                    if new >= t["min"] and t["name"] not in claimed and t["name"] in TIER_UP_REWARDS:
                        lines, pend = _grant_bundle(
                            conn, uid, TIER_UP_REWARDS[t["name"]], f"Ranked: reached {t['name']}", idx_cache
                        )
                        claimed.add(t["name"])
                        pending = pending or pend
                        rewards.append(f"{t['emoji']} **{t['name']} reached!** " + " · ".join(lines))
            conn.execute(
                "UPDATE ranked_players SET stars = ?, played = played + 1, wins = wins + ?, "
                "losses = losses + ?, claimed = ?, blacklist_until = CASE WHEN ? > 0 THEN ? ELSE blacklist_until END "
                "WHERE user_id = ?",
                (new, 1 if won else 0, 0 if won else 1, ",".join(sorted(claimed)),
                 blacklist_until, blacklist_until, uid),
            )
            out[uid] = {
                "won": won, "old": old, "new": new, "delta": new - old,
                "tier_old": tier_of(old), "tier_new": tier_of(new),
                "rewards": rewards, "pending": pending,
                "blacklist_until": int(blacklist_until),
            }
    return out


# ── Anti-alt: blacklist + "don't meet the same player again" ─────────────────

def blacklist_remaining(user_id) -> int:
    """Seconds left on the player's ranked blacklist (0 = free to play).
    When a ban has just run out, negative stars are reset to 0 so the player starts fresh."""
    uid = int(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT blacklist_until FROM ranked_players WHERE user_id = ?", (uid,)).fetchone()
        until = float(r["blacklist_until"] or 0) if r else 0.0
        if until <= 0:
            return 0
        left = int(until - time.time())
        if left > 0:
            return left
        conn.execute(
            "UPDATE ranked_players SET blacklist_until = 0, stars = CASE WHEN stars < 0 THEN 0 ELSE stars END "
            "WHERE user_id = ?", (uid,),
        )
        return 0


def clear_blacklist(user_id) -> bool:
    """Owner tool: lift a ranked blacklist right now and reset negative stars to 0.
    Returns True if there was a blacklist (or negative stars) to clear."""
    uid = int(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT blacklist_until, stars FROM ranked_players WHERE user_id = ?", (uid,)).fetchone()
        if r is None:
            return False
        had = float(r["blacklist_until"] or 0) > 0 or int(r["stars"] or 0) < 0
        conn.execute(
            "UPDATE ranked_players SET blacklist_until = 0, "
            "stars = CASE WHEN stars < 0 THEN 0 ELSE stars END WHERE user_id = ?", (uid,),
        )
        return had


def record_match(uid_a, uid_b) -> None:
    """Remember who each real player just faced (a bot counts as opponent 0, so bot games also
    push old opponents out). Only the last RECENT_OPP_BLOCK matches are kept per player."""
    a, b = int(uid_a), int(uid_b)
    with economy._tx() as conn:
        _ensure(conn)
        for me, opp in ((a, b), (b, a)):
            if me <= 0:
                continue
            conn.execute("INSERT INTO ranked_recent (user_id, opp_id) VALUES (?, ?)", (me, opp if opp > 0 else 0))
            conn.execute(
                "DELETE FROM ranked_recent WHERE user_id = ? AND id NOT IN "
                "(SELECT id FROM ranked_recent WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
                (me, me, RECENT_OPP_BLOCK),
            )


def recently_faced(uid_a, uid_b) -> bool:
    """True if either player still has the other among their last RECENT_OPP_BLOCK opponents."""
    a, b = int(uid_a), int(uid_b)
    conn = economy._conn()
    try:
        _ensure(conn)
        r = conn.execute(
            "SELECT 1 FROM ranked_recent WHERE (user_id = ? AND opp_id = ?) OR (user_id = ? AND opp_id = ?) LIMIT 1",
            (a, b, b, a),
        ).fetchone()
        return r is not None
    except Exception as e:   # never block matchmaking because of a DB hiccup
        print(f"[ranked] recently_faced failed: {type(e).__name__}: {e}")
        return False
    finally:
        conn.close()


# ── Bot opponents ────────────────────────────────────────────────────────────

BOT_NAMES = [
    # Indian
    "Rakesh", "Suresh", "Aman", "Rohit", "Vikas", "Ankit", "Rahul", "Sahil", "Mohit", "Nitin",
    "Deepak", "Manish", "Pankaj", "Sumit", "Gaurav", "Harsh", "Yash", "Kunal", "Varun", "Arjun",
    "Karan", "Naveen", "Pradeep", "Sandeep", "Ajay", "Vijay", "Sanjay", "Ravi", "Amit", "Anil",
    "Sunil", "Mukesh", "Dinesh", "Ramesh", "Mahesh", "Lokesh", "Rajat", "Tarun", "Vishal", "Abhishek",
    "Shubham", "Ashish", "Akash", "Aditya", "Ishaan", "Kabir", "Nikhil", "Piyush", "Prateek", "Raghav",
    "Rishabh", "Saurabh", "Shivam", "Tushar", "Utkarsh", "Vivek", "Yogesh", "Lakshay", "Dhruv", "Hardik",
    "Jatin", "Kartik", "Mayank", "Naman", "Parth", "Ritik", "Siddharth", "Dev", "Chirag", "Divyansh",
    "Rohan", "Jay", "Om", "Neeraj", "Bhavesh", "Chetan", "Devansh", "Faisal", "Imran", "Zaid",
    "Arman", "Salman", "Rizwan", "Danish", "Aryan", "Gautam", "Tanmay", "Vedant", "Ansh", "Daksh",
    "Krish", "Manav", "Pranav", "Sarthak", "Tanish", "Vansh", "Priya", "Neha", "Riya", "Anjali",
    "Pooja", "Sneha", "Kavya", "Isha",
    # English
    "Alex", "Ryan", "Jack", "Luke", "Mike", "Chris", "Daniel", "Ethan", "Noah", "Liam",
    "Oliver", "Harry", "Jake", "Tom", "Sam", "Ben", "Adam", "Josh", "Max", "Leo",
    "Nathan", "Connor", "Dylan", "Aaron", "Kevin", "Brian", "Jason", "Tyler", "Mason", "Owen",
    "Emma", "Sophia",
]

_recent_names: list[str] = []


def random_bot_name(avoid=()) -> str:
    """A believable player name: plain, with numbers, lower-case with underscore, or with an initial."""
    avoid_l = {str(a).lower() for a in avoid} | {n.lower() for n in _recent_names}
    for _ in range(30):
        base = random.choice(BOT_NAMES)
        r = random.random()
        if r < 0.45:
            name = base
        elif r < 0.65:
            name = f"{base}{random.randint(1, 99)}"
        elif r < 0.78:
            name = f"{base.lower()}_{random.choice(['07', '18', '45', '77', '10', '99', '01'])}"
        elif r < 0.90:
            name = f"{base} {random.choice('ABCDGHJKMNPRSTVY')}"
        else:
            name = f"{base}{random.choice(['_', ''])}{random.choice(['YT', 'XD', 'Pro', 'OP', '777'])}"
        if name.lower() not in avoid_l:
            _recent_names.append(name)
            del _recent_names[:-40]
            return name
    return random.choice(BOT_NAMES)


class _Avatar:
    def __init__(self, url: str):
        self.url = url

    def __str__(self) -> str:
        return self.url


class FakeMember:
    """Looks like a Discord member to the match code (id / display_name / mention).
    The id is negative so it can never clash with a real Discord id."""

    is_fake = True
    bot = False
    roles: list = []
    guild = None

    def __init__(self, name: str):
        self.id = -random.randint(10**8, 10**9)
        self.name = self.display_name = self.global_name = name
        self.mention = f"@{name}"
        self.display_avatar = self.avatar = _Avatar(
            f"https://cdn.discordapp.com/embed/avatars/{random.randint(0, 5)}.png"
        )

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other) -> bool:
        return getattr(other, "id", None) == self.id

    def __hash__(self) -> int:
        return hash(self.id)


def _legal_xi(cards: list, overs: int) -> bool:
    """Same rules sl.xi_problem_for_match() uses for real users."""
    if len(cards) != economy.XI_SIZE:
        return False
    counts = sl.xi_role_counts(cards)
    for role, (lo, hi) in sl.ROLE_LIMITS.items():
        eff_lo = sl.BAT_HARD_MIN if role == "BAT" else lo
        if counts[role] < eff_lo or counts[role] > hi:
            return False
    bowlers = sum(1 for c in cards if card_db.effective_bowling_type(c))
    return bowlers >= max(sl.MIN_BOWLERS, sl.bowlers_required_for(overs))


def _player_dict(c) -> dict:
    return {
        "name": c["playername"],
        "country": c["country"],
        "ovr": c["ovr"],
        "bat": c["bat"],
        "bowl": c["bowl"],
        "bowling_type": card_db.effective_bowling_type(c),
        "batting_hand": c["batting_hand"] if "batting_hand" in c.keys() else None,
        "role": ENGINE_ROLE[card_db.effective_role(c)],
        "card": sl.card_emoji(c),
        "flag": c["country_emoji"],
        "remoji": sl.role_emoji(c),
    }


def build_bot_team(tier: str, bot_name: str, overs: int = RANKED_OVERS) -> dict | None:
    """The bot's playing XI (a legal XI of real cards, rated by tier). None if the game has too few cards."""
    prof = BOT_PROFILES[tier]
    lo, hi = prof["ovr"]
    cards = list(card_db.list_obtainable_cards())
    if len(cards) < economy.XI_SIZE:
        return None
    xi = None
    for widen in range(0, 45, 3):
        top = 999 if tier == "Ruby" else hi + widen // 2
        pool = [c for c in cards if lo - widen <= int(c["ovr"]) <= top]
        if len(pool) < economy.XI_SIZE:
            continue
        tries = [pool] if tier == "Ruby" else (
            [random.sample(pool, max(economy.XI_SIZE, int(len(pool) * 0.8))) for _ in range(5)] + [pool]
        )
        for sub in tries:
            cand = sl.best_xi(sub)
            if _legal_xi(cand, overs):
                xi = cand
                break
        if xi:
            break
    if xi is None:
        starter = sl.pick_starter_squad()
        if starter and _legal_xi(starter, overs):
            xi = sl.order_xi(starter)
    if xi is None:
        return None
    countries: dict[str, int] = {}
    for c in xi:
        countries[c["country"]] = countries.get(c["country"], 0) + 1
    team_name = random.choice([f"{bot_name} XI", f"{bot_name}'s XI", f"Team {bot_name}"])
    return {
        "name": team_name[:24],
        "ovr": sl.average_ovr(xi),
        "chem": min(99, 70 + 3 * max(countries.values(), default=0)),
        "players": [_player_dict(c) for c in xi],
        "captain": None,
    }


# ── Admin tools (used by csrankgive / csrankreset / csrankban) ────────────────

def _admin_log(conn, admin_id: int, target_id: int, action: str, detail: str) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ranked_admin_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, admin_id INTEGER NOT NULL, "
        "target_id INTEGER NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO ranked_admin_log (ts, admin_id, target_id, action, detail) VALUES (?, ?, ?, ?, ?)",
        (time.time(), int(admin_id), int(target_id), action, detail[:200]),
    )


def admin_give_stars(admin_id, user_id, delta: int) -> dict:
    """Add (or, with a negative delta, remove) stars. NO tier rewards are paid, and every tier the
    player now stands in is marked as claimed, so the next win can't pay it out again."""
    uid, delta = int(user_id), int(delta)
    with economy._tx() as conn:
        _ensure(conn)
        _rollover_locked(conn)
        row = _row(conn, uid)
        old = int(row["stars"])
        new = max(MIN_STARS, old + delta)
        claimed = {t for t in (row["claimed"] or "").split(",") if t}
        for t in TIERS[1:]:
            if new >= t["min"]:
                claimed.add(t["name"])
        conn.execute("UPDATE ranked_players SET stars = ?, claimed = ? WHERE user_id = ?",
                     (new, ",".join(sorted(claimed)), uid))
        _admin_log(conn, admin_id, uid, "give", f"{old} -> {new} ({delta:+d})")
    return {"old": old, "new": new, "tier_old": tier_of(old), "tier_new": tier_of(new)}


def admin_reset_player(admin_id, user_id) -> dict:
    """Stars, wins, losses and played back to 0. Claimed tier rewards are kept (no re-farming)."""
    uid = int(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        _rollover_locked(conn)
        row = _row(conn, uid)
        old = {"stars": int(row["stars"]), "wins": int(row["wins"]), "losses": int(row["losses"])}
        conn.execute("UPDATE ranked_players SET stars = 0, played = 0, wins = 0, losses = 0 WHERE user_id = ?", (uid,))
        _admin_log(conn, admin_id, uid, "reset", f"was {old['stars']} stars, {old['wins']}W/{old['losses']}L")
    return old


def admin_reset_all(admin_id) -> int:
    """Everyone's stars / wins / losses / played back to 0 (claimed tier rewards kept). Returns players changed."""
    with economy._tx() as conn:
        _ensure(conn)
        _rollover_locked(conn)
        n = conn.execute("SELECT COUNT(*) AS n FROM ranked_players").fetchone()["n"]
        conn.execute("UPDATE ranked_players SET stars = 0, played = 0, wins = 0, losses = 0")
        _admin_log(conn, admin_id, 0, "reset_all", f"{n} players")
    return int(n)


def admin_ban(admin_id, user_id, days: int, reason: str = "") -> int:
    """Ranked ban for `days` days. Returns the unix time it ends."""
    uid = int(user_id)
    until = time.time() + int(days) * 86400
    with economy._tx() as conn:
        _ensure(conn)
        _row(conn, uid)
        conn.execute("UPDATE ranked_players SET blacklist_until = ? WHERE user_id = ?", (until, uid))
        _admin_log(conn, admin_id, uid, "ban", f"{days}d {reason}".strip())
    return int(until)


def admin_unban(admin_id, user_id) -> bool:
    """Lift a ranked ban (negative stars go back to 0). True if there was something to clear."""
    had = clear_blacklist(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        _admin_log(conn, admin_id, int(user_id), "unban", "had ban" if had else "no ban")
    return had


# ── Rewards overview (csrankrewards) ─────────────────────────────────────────

def _bundle_parts(spec: dict) -> list[str]:
    """Short text for one reward bundle, e.g. ["5 Vouchers", "100,000 Coins"]."""
    parts: list[str] = []
    if spec.get("vouchers"):
        parts.append(f"{int(spec['vouchers'])} Vouchers")
    if spec.get("coins"):
        parts.append(f"{int(spec['coins']):,} Coins")
    for pack, qty in (spec.get("packs") or {}).items():
        parts.append(f"{int(qty)}x {sl.PACKS.get(pack, {}).get('label', pack)}")
    if spec.get("card"):
        lo, hi = spec["card"]
        parts.append(f"{lo} OVR Card" if lo == hi else f"{lo}-{hi} OVR Card")
    return parts


def reward_summary(user_id) -> dict:
    """Everything csrankrewards needs for one player: every tier with its tier-up reward (and whether
    this player already claimed it this season), its season-end reward, the player's stars / tier,
    the next tier with the stars still needed, and when the season ends."""
    uid = int(user_id)
    info = get_player(uid)
    season = season_info()
    conn = economy._conn()
    try:
        _ensure(conn)
        r = conn.execute("SELECT claimed FROM ranked_players WHERE user_id = ?", (uid,)).fetchone()
    finally:
        conn.close()
    claimed = {t for t in ((r["claimed"] if r else "") or "").split(",") if t}
    tiers = []
    for t in TIERS:
        name = t["name"]
        up = TIER_UP_REWARDS.get(name)
        sea = SEASON_REWARDS.get(name)
        tiers.append({
            "name": name,
            "emoji": t["emoji"],
            "min": t["min"],
            "tier_up": _bundle_parts(up) if up else [],
            "claimed": name in claimed,
            "season": _bundle_parts(sea) if sea else [],
        })
    nxt = next_tier(info["stars"])
    return {
        "tiers": tiers,
        "stars": info["stars"],
        "tier": info["tier"],
        "next_name": nxt[0]["name"] if nxt else None,
        "next_min": nxt[0]["min"] if nxt else None,
        "next_needed": nxt[1] if nxt else None,
        "season_active": season["active"],
        "season_name": season["name"],
        "season_end": season["end"],
    }
