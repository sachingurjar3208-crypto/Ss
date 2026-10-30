"""Star Pass system (pure logic + database, no Discord code).

How it works
- An admin starts a season with /starpassmaker (length 10D / 15D / 30D and a number of levels).
  Several DIFFERENT passes can run at the same time (the normal "Star Pass" has pass_key "star",
  an event pass like "Halloween Pass" has its own key, theme and background). Only ONE season
  per pass_key can run at a time. When its time is up it simply disappears: every function
  below treats an expired season as "that pass is not running".
- Daily quests give STARPASS_QUEST_CREDITS credits each (quest.py calls
  award_quest_credits() inside the SAME database transaction that marks the quest done,
  so a quest can never pay credits twice).
- Credits add up and decide how many levels are open. Level 1 is open for everybody.
  Level 2-15 cost 300 credits each, Level 16 and up cost 400 credits each.
- Every level has a FREE reward and a PREMIUM reward. The Premium row can only be
  collected by members with STARPASS_PREMIUM_ROLE_ID. Getting that role gives a one-time
  1000 credit bonus per season.
- Rewards are fixed (see default_reward). An admin can change single levels with
  /starpass-rewardadd at any time (even after players collected them); those changes are
  saved per season in starpass_reward. A season ended by mistake comes back with /seasonreopen.
- Collecting is ONE database transaction: the claim rows, the credits and the rewards are
  saved together or not at all, and a claim row is unique per (season, user, level, row),
  so pressing the button many times can never pay twice.

Tables (created automatically, see ensure_tables):
    starpass_season  one row per season (pass_key, theme, premium role and bonus per season)
    starpass_bg      custom background image per pass_key
    starpass_reward  admin overrides: (season, level, row) -> reward
    starpass_user    credits + "premium bonus given" flag per user per season
    starpass_claim   what a user has already collected
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import economy

# ── Settings ─────────────────────────────────────────────────────────────────
STARPASS_PREMIUM_ROLE_ID = 1561987229294006322
STARPASS_QUEST_CREDITS = 80          # credits for every finished daily quest
STARPASS_RANKED_CREDITS = 50         # credits for every finished RANKED match (each real player)
STARPASS_RANKED_MATCHES_PER_DAY = 6  # only the first 6 ranked matches of the day (IST) pay credits
STARPASS_RANKED_VS_BOT = True        # False = ranked matches against the bot pay no credits
PREMIUM_BONUS_CREDITS = 1000         # one-time bonus per season for the Premium role
LEVEL_COST_EARLY = 300               # credits for each of levels 2 .. EARLY_LAST_LEVEL
LEVEL_COST_LATE = 400                # credits for each level after that
EARLY_LAST_LEVEL = 15
LEVELS_PER_PAGE = 6
MAX_LEVELS = 500
DURATIONS = {"10D": 10, "15D": 15, "30D": 30}
RCSTARPASS_COOLDOWN = 2 * 60         # normal users; Premium / Star Pass role: none
DAY = 86_400

# ── Several passes at once ───────────────────────────────────────────────────
DEFAULT_PASS = "star"                # the normal Star Pass (all old seasons are "star")
THEMES = ("default", "halloween", "diwali", "christmas")   # looks, see starpass_image.THEMES
MAX_BG_BYTES = 8 * 1024 * 1024       # biggest background upload


def norm_key(text) -> str:
    """'Halloween Pass!' -> 'halloween'. Empty / None -> 'star'."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    if s.endswith("-pass") and len(s) > 5:           # "Halloween Pass" -> "halloween"
        s = s[:-5]
    s = s[:30].strip("-")
    return s or DEFAULT_PASS

FREE, PREMIUM = "free", "premium"
ROWS = (FREE, PREMIUM)

# Reward kinds
PACK_KINDS = ("legendary", "gold", "silver", "bronze")
KINDS = (*PACK_KINDS, "voucher", "coins", "player", "theme")
KIND_LABEL = {
    "legendary": "Legendary Pack", "gold": "Gold Pack", "silver": "Silver Pack",
    "bronze": "Bronze Pack", "voucher": "Voucher", "coins": "Coins", "player": "Player Card",
    "theme": "Summary Theme",
}

# ── Default rewards (edit here to change what every new season gives) ────────
# A cycle of 5 levels is repeated, so any number of levels is covered. (kind, quantity)
FREE_CYCLE = [("coins", 5_000), ("bronze", 1), ("coins", 10_000), ("voucher", 1), ("silver", 1)]
PREMIUM_CYCLE = [("coins", 20_000), ("silver", 1), ("voucher", 2), ("silver", 1), ("gold", 1)]
FREE_LAST = ("gold", 1)              # the very last level of a season
PREMIUM_LAST = ("legendary", 1)


@dataclass(frozen=True)
class Reward:
    kind: str                 # one of KINDS
    qty: int = 1
    player_key: str | None = None    # only for kind == "player"
    custom: bool = False             # True = set by an admin

    def label(self, player_name: str | None = None) -> str:
        if self.kind == "coins":
            return f"{self.qty:,} Coins"
        if self.kind == "player":
            return player_name or (self.player_key or "Player Card")
        if self.kind == "theme":                       # player_key holds the theme name (halloween ...)
            import summary_theme
            return f"{summary_theme.label(self.player_key)} Summary"
        base = KIND_LABEL[self.kind]
        return base if self.qty == 1 else f"{base} x{self.qty}"


def default_reward(level: int, row: str, total_levels: int) -> Reward:
    if level == total_levels and total_levels > 1:
        kind, qty = FREE_LAST if row == FREE else PREMIUM_LAST
        return Reward(kind, qty)
    cycle = FREE_CYCLE if row == FREE else PREMIUM_CYCLE
    kind, qty = cycle[(level - 1) % len(cycle)]
    return Reward(kind, qty)


# ── Level maths ──────────────────────────────────────────────────────────────

def level_cost(level: int) -> int:
    """Credits needed to open `level` once the one before it is open. Level 1 is free."""
    if level <= 1:
        return 0
    return LEVEL_COST_EARLY if level <= EARLY_LAST_LEVEL else LEVEL_COST_LATE


def credits_for_level(level: int) -> int:
    """Total credits a user needs to have to hold `level` open."""
    return sum(level_cost(n) for n in range(2, level + 1))


def level_for_credits(credits: int, total_levels: int) -> int:
    level = 1
    while level < total_levels and credits >= credits_for_level(level + 1):
        level += 1
    return level


def progress(credits: int, total_levels: int) -> dict:
    """{'level', 'into', 'need', 'maxed'}: progress bar numbers toward the next level."""
    level = level_for_credits(credits, total_levels)
    if level >= total_levels:
        return {"level": level, "into": 0, "need": 0, "maxed": True}
    base = credits_for_level(level)
    return {"level": level, "into": max(0, credits - base), "need": level_cost(level + 1), "maxed": False}


def page_ranges(total_levels: int) -> list[tuple[int, int]]:
    """[(first, last), ...] 6 levels a page. A single leftover level joins the last page,
    so 25 levels = 1-6, 7-12, 13-18, 19-25."""
    n = max(1, int(total_levels))
    pages = [(s, min(s + LEVELS_PER_PAGE - 1, n)) for s in range(1, n + 1, LEVELS_PER_PAGE)]
    if len(pages) > 1 and pages[-1][0] == pages[-1][1]:
        pages[-2] = (pages[-2][0], pages[-1][1])
        pages.pop()
    return pages


# ── Database ─────────────────────────────────────────────────────────────────

def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS starpass_season ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, levels INTEGER NOT NULL, "
        "start_ts REAL NOT NULL, end_ts REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
        "created_by INTEGER)"
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(starpass_season)")}
    for col, ddl in (                      # old databases get the new columns, old rows become the normal "star" pass
        ("pass_key", "TEXT NOT NULL DEFAULT 'star'"),
        ("theme", "TEXT NOT NULL DEFAULT 'default'"),
        ("premium_role_id", "INTEGER"),
        ("bonus_credits", "INTEGER"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE starpass_season ADD COLUMN {col} {ddl}")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS starpass_bg ("
        "pass_key TEXT PRIMARY KEY, data BLOB NOT NULL, ts REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS starpass_reward ("
        "season_id INTEGER NOT NULL, level INTEGER NOT NULL, row TEXT NOT NULL, "
        "kind TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 1, player_key TEXT, "
        "PRIMARY KEY (season_id, level, row))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS starpass_user ("
        "season_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
        "credits INTEGER NOT NULL DEFAULT 0 CHECK (credits >= 0), "
        "bonus_given INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (season_id, user_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS starpass_ranked_daily ("
        "user_id INTEGER NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (user_id, day))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS starpass_claim ("
        "season_id INTEGER NOT NULL, user_id INTEGER NOT NULL, level INTEGER NOT NULL, "
        "row TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (season_id, user_id, level, row))"
    )


def ensure_tables() -> None:
    conn = economy._conn()
    try:
        _ensure(conn)
    finally:
        conn.close()


def _active(conn, pass_key: str = DEFAULT_PASS):
    """The running season row of one pass, or None (also None when the time is up)."""
    _ensure(conn)
    return conn.execute(
        "SELECT * FROM starpass_season WHERE active = 1 AND end_ts > ? AND pass_key = ? "
        "ORDER BY id DESC LIMIT 1",
        (time.time(), norm_key(pass_key)),
    ).fetchone()


def _active_all(conn) -> list:
    """Every running season (one per pass), oldest first."""
    _ensure(conn)
    return conn.execute(
        "SELECT * FROM starpass_season WHERE active = 1 AND end_ts > ? ORDER BY id", (time.time(),)
    ).fetchall()


def _season_dict(row) -> dict:
    left = max(0.0, float(row["end_ts"]) - time.time())
    keys = row.keys()
    role = row["premium_role_id"] if "premium_role_id" in keys else None
    bonus = row["bonus_credits"] if "bonus_credits" in keys else None
    return {
        "id": int(row["id"]), "name": row["name"], "levels": int(row["levels"]),
        "start_ts": float(row["start_ts"]), "end_ts": float(row["end_ts"]),
        "seconds_left": int(left), "days_left": int(-(-left // DAY)),     # rounds up: 0.2 day = 1
        "pass_key": (row["pass_key"] if "pass_key" in keys else None) or DEFAULT_PASS,
        "theme": (row["theme"] if "theme" in keys else None) or "default",
        "premium_role": int(role) if role else STARPASS_PREMIUM_ROLE_ID,
        "bonus": int(bonus) if bonus is not None else PREMIUM_BONUS_CREDITS,
    }


def get_active_season(pass_key: str = DEFAULT_PASS) -> dict | None:
    with economy._tx() as conn:
        row = _active(conn, pass_key)
        return _season_dict(row) if row else None


def list_active_seasons() -> list[dict]:
    """Every pass that is running right now (the normal Star Pass first)."""
    with economy._tx() as conn:
        out = [_season_dict(r) for r in _active_all(conn)]
    out.sort(key=lambda s: (s["pass_key"] != DEFAULT_PASS, s["id"]))
    return out


def list_pass_keys() -> list[tuple[str, str]]:
    """[(pass_key, latest season name)] of every pass that ever existed (for admin autocomplete)."""
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT pass_key, name FROM starpass_season ORDER BY id").fetchall()
    seen: dict[str, str] = {}
    for r in rows:
        seen[r["pass_key"] or DEFAULT_PASS] = r["name"]
    return list(seen.items())


# ── Backgrounds ──────────────────────────────────────────────────────────────

def set_background(pass_key: str, data: bytes | None) -> None:
    """Save (or with None remove) the custom background of a pass. `data` is a ready image."""
    key = norm_key(pass_key)
    with economy._tx() as conn:
        _ensure(conn)
        if data is None:
            conn.execute("DELETE FROM starpass_bg WHERE pass_key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO starpass_bg (pass_key, data, ts) VALUES (?, ?, ?)",
                         (key, data, time.time()))


def get_background(pass_key: str) -> bytes | None:
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT data FROM starpass_bg WHERE pass_key = ?", (norm_key(pass_key),)).fetchone()
    return bytes(r["data"]) if r else None


def create_season(levels: int, duration_key: str, admin_id: int = 0, pass_name: str | None = None,
                  theme: str = "default", premium_role_id: int | None = None,
                  bonus_credits: int | None = None) -> dict | str:
    """Start a new season. Returns the season dict, or an error message (string).

    pass_name   empty = the normal Star Pass ("Season N"). Otherwise a separate event pass such as
                "Halloween Pass": it runs next to the Star Pass and has its own credits and rewards.
    """
    if duration_key not in DURATIONS:
        return "Pick a duration of 10D, 15D or 30D."
    if theme not in THEMES:
        return "Unknown theme."
    pass_name = (pass_name or "").strip()
    key = norm_key(pass_name) if pass_name else DEFAULT_PASS
    if pass_name and key == DEFAULT_PASS and pass_name.lower() != DEFAULT_PASS:
        return "Please use letters or numbers in the pass name."
    levels = int(levels)
    if not 1 <= levels <= MAX_LEVELS:
        return f"The number of levels must be between 1 and {MAX_LEVELS}."
    now = time.time()
    with economy._tx() as conn:
        running = _active(conn, key)
        if running is not None:
            s = _season_dict(running)
            return (f"{s['name']} is still running with {s['days_left']} day(s) left. "
                    f"End it first with /starpassend (pass_key {key}) before starting a new one.")
        n = int(conn.execute("SELECT COUNT(*) AS n FROM starpass_season WHERE pass_key = ?",
                             (key,)).fetchone()["n"])
        if key == DEFAULT_PASS and not pass_name:
            name = f"Season {n + 1}"
        else:
            name = pass_name[:40] if n == 0 else f"{pass_name[:36]} {n + 1}"
        cur = conn.execute(
            "INSERT INTO starpass_season (name, levels, start_ts, end_ts, active, created_by, "
            "pass_key, theme, premium_role_id, bonus_credits) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (name, levels, now, now + DURATIONS[duration_key] * DAY, int(admin_id),
             key, theme, int(premium_role_id) if premium_role_id else None,
             int(bonus_credits) if bonus_credits is not None else None),
        )
        return _season_dict(conn.execute("SELECT * FROM starpass_season WHERE id = ?", (cur.lastrowid,)).fetchone())


def end_season(pass_key: str = DEFAULT_PASS) -> dict | None:
    """Stop the running season of one pass right now. Returns it, or None if nothing was running."""
    with economy._tx() as conn:
        row = _active(conn, pass_key)
        if row is None:
            return None
        conn.execute("UPDATE starpass_season SET active = 0 WHERE id = ?", (row["id"],))
        return _season_dict(row)


def reopen_season(season_no: int | None = None, days: int | None = None,
                  replace_running: bool = False, pass_key: str = DEFAULT_PASS) -> dict | str:
    """Admin: bring an ended (or expired) season back. Returns the season dict or an error message.

    season_no        the season number inside this pass (Season 1 = 1). Empty = the latest season
                     of this pass that is not running.
    pass_key         which pass (default "star").
    days             run it for this many days from now. Empty = keep the old end time; if that
                     time is already over, `days` is required.
    replace_running  another season of the SAME pass is running right now (for example Season 2):
                     it is paused (NOT deleted, all its credits/claims stay and it can be reopened
                     later) and the chosen season takes its place. Only ONE season per pass runs.
    """
    now = time.time()
    with economy._tx() as conn:
        _ensure(conn)
        key = norm_key(pass_key)
        running = _active(conn, key)
        if season_no is None:
            q = "SELECT * FROM starpass_season WHERE pass_key = ? "
            args: tuple = (key,)
            if running is not None:
                q += "AND id != ? "
                args = (key, int(running["id"]))
            row = conn.execute(q + "ORDER BY id DESC LIMIT 1", args).fetchone()
        else:                                    # the Nth season of this pass
            row = conn.execute("SELECT * FROM starpass_season WHERE pass_key = ? ORDER BY id LIMIT 1 OFFSET ?",
                               (key, max(0, int(season_no) - 1))).fetchone()
        if row is None:
            return "I could not find that season."
        if running is not None and int(running["id"]) == int(row["id"]):
            return f"{row['name']} is already running."
        if running is not None and not replace_running:
            s = _season_dict(running)
            return (f"{s['name']} is running right now. To bring back {row['name']} set "
                    f"replace_running to True (the running season is only paused, nothing is deleted).")
        if days is not None:
            end_ts = now + int(days) * DAY
        elif float(row["end_ts"]) > now:
            end_ts = float(row["end_ts"])
        else:
            return f"The time of {row['name']} is already over. Enter the days option to run it again."
        if running is not None:
            conn.execute("UPDATE starpass_season SET active = 0 WHERE id = ?", (running["id"],))
        conn.execute("UPDATE starpass_season SET active = 1, end_ts = ? WHERE id = ?", (end_ts, row["id"]))
        return _season_dict(conn.execute("SELECT * FROM starpass_season WHERE id = ?", (row["id"],)).fetchone())


# ── Rewards ──────────────────────────────────────────────────────────────────

def set_reward(level: int, row: str, kind: str, qty: int = 1, player_key: str | None = None,
               pass_key: str = DEFAULT_PASS) -> str | dict:
    """Admin: change the reward of one level in the running season of a pass.
    Returns the season dict on success or an error message."""
    if row not in ROWS or kind not in KINDS:
        return "Invalid reward."
    qty = int(qty)
    if kind in ("player", "theme"):
        qty = 1
    if kind == "theme":
        import summary_theme
        player_key = summary_theme.norm(player_key)
        if player_key is None:
            return "Unknown summary theme."
    if qty < 1 or qty > economy.MAX_PURSE:
        return "The quantity must be at least 1."
    if kind == "voucher" and qty > economy.MAX_VOUCHERS:
        return "That is too many vouchers."
    with economy._tx() as conn:
        season = _active(conn, pass_key)
        if season is None:
            return "That pass is not running right now. Start one with /starpassmaker first."
        if not 1 <= int(level) <= int(season["levels"]):
            return f"This season has levels 1 to {season['levels']}."
        # Admins may change a reward at any time, even after players collected it: those who
        # already collected keep what they got, everybody who collects later gets the new reward.
        conn.execute(
            "INSERT OR REPLACE INTO starpass_reward (season_id, level, row, kind, qty, player_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (season["id"], int(level), row, kind, qty, player_key),
        )
        return _season_dict(season)


def _rewards_map(conn, season) -> dict[tuple[int, str], Reward]:
    out = {}
    for r in conn.execute("SELECT * FROM starpass_reward WHERE season_id = ?", (season["id"],)):
        out[(int(r["level"]), r["row"])] = Reward(r["kind"], int(r["qty"]), r["player_key"], True)
    return out


def _reward_for(custom: dict, season, level: int, row: str) -> Reward:
    return custom.get((level, row)) or default_reward(level, row, int(season["levels"]))


# ── Credits ──────────────────────────────────────────────────────────────────

def _add_credits(conn, season_id: int, uid: int, amount: int) -> None:
    conn.execute(
        "INSERT INTO starpass_user (season_id, user_id, credits) VALUES (?, ?, ?) "
        "ON CONFLICT(season_id, user_id) DO UPDATE SET credits = credits + excluded.credits",
        (season_id, uid, int(amount)),
    )


def award_quest_credits(conn, user_id: int, finished_quests: int) -> int:
    """Called by quest.track() INSIDE its transaction. Returns the credits given (0 when no
    season is running). Never raises into the quest code."""
    try:
        n = int(finished_quests)
        if n <= 0:
            return 0
        seasons = _active_all(conn)                 # every running pass earns the credits
        if not seasons:
            return 0
        amount = n * STARPASS_QUEST_CREDITS
        for season in seasons:
            _add_credits(conn, int(season["id"]), int(user_id), amount)
        return amount
    except Exception as e:                      # a Star Pass problem must never break quests
        print(f"[starpass] could not add quest credits: {type(e).__name__}: {e}")
        return 0


_IST = timezone(timedelta(hours=5, minutes=30))


def award_ranked_match_credits(user_ids) -> dict:
    """A ranked match just finished: give STARPASS_RANKED_CREDITS to every real player, but only
    for their first STARPASS_RANKED_MATCHES_PER_DAY ranked matches of the day (IST).
    One database transaction, so a crash can never pay without counting. Does nothing when no
    season is running. Returns {user_id: {"credits": x, "n": matches_counted_today}} for the
    players who were paid (empty dict if nobody was)."""
    out: dict = {}
    now = datetime.now(_IST)
    day = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    with economy._tx() as conn:
        seasons = _active_all(conn)                 # every running pass earns the credits
        if not seasons:
            return out
        conn.execute("DELETE FROM starpass_ranked_daily WHERE day < ?", (cutoff,))
        for uid in {int(u) for u in user_ids if u is not None}:
            if uid <= 0:                       # bot players have negative ids
                continue
            if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
                continue
            row = conn.execute(
                "SELECT n FROM starpass_ranked_daily WHERE user_id = ? AND day = ?", (uid, day)
            ).fetchone()
            n = int(row["n"]) if row else 0
            if n >= STARPASS_RANKED_MATCHES_PER_DAY:
                continue
            conn.execute(
                "INSERT INTO starpass_ranked_daily (user_id, day, n) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, day) DO UPDATE SET n = n + 1",
                (uid, day),
            )
            for season in seasons:
                _add_credits(conn, int(season["id"]), uid, STARPASS_RANKED_CREDITS)
            out[uid] = {"credits": STARPASS_RANKED_CREDITS, "n": n + 1}
    return out


def grant_premium_bonus(user_id: int, pass_key: str = DEFAULT_PASS) -> int:
    """Give the one-time Premium bonus for the running season of a pass. Returns the credits
    given (0 if no season, or the bonus was already given this season)."""
    uid = int(user_id)
    with economy._tx() as conn:
        season = _active(conn, pass_key)
        if season is None:
            return 0
        bonus = _season_dict(season)["bonus"]
        if bonus <= 0:
            return 0
        sid = int(season["id"])
        conn.execute("INSERT OR IGNORE INTO starpass_user (season_id, user_id) VALUES (?, ?)", (sid, uid))
        cur = conn.execute(
            "UPDATE starpass_user SET credits = credits + ?, bonus_given = 1 "
            "WHERE season_id = ? AND user_id = ? AND bonus_given = 0",
            (bonus, sid, uid),
        )
        return bonus if cur.rowcount == 1 else 0


def admin_add_credits(user_id: int, amount: int, pass_key: str = DEFAULT_PASS) -> int | None:
    """Admin helper: add credits in the running season of a pass. Returns the new total or None."""
    uid = int(user_id)
    with economy._tx() as conn:
        season = _active(conn, pass_key)
        if season is None:
            return None
        _add_credits(conn, int(season["id"]), uid, int(amount))
        return int(conn.execute(
            "SELECT credits FROM starpass_user WHERE season_id = ? AND user_id = ?", (season["id"], uid)
        ).fetchone()["credits"])


# ── What the user sees ───────────────────────────────────────────────────────

def _state(level: int, row: str, unlocked: int, premium: bool, claimed: set) -> str:
    """claimed / ready / locked / premium_locked"""
    if (level, row) in claimed:
        return "claimed"
    if row == PREMIUM and not premium:
        return "premium_locked"
    return "ready" if level <= unlocked else "locked"


def get_board(user_id: int, premium: bool, pass_key: str = DEFAULT_PASS) -> dict | None:
    """Everything rcstarpass needs for one user, or None when that pass is not running."""
    uid = int(user_id)
    with economy._tx() as conn:
        season = _active(conn, pass_key)
        if season is None:
            return None
        sid, total = int(season["id"]), int(season["levels"])
        u = conn.execute(
            "SELECT credits FROM starpass_user WHERE season_id = ? AND user_id = ?", (sid, uid)
        ).fetchone()
        credits = int(u["credits"]) if u else 0
        claimed = {
            (int(r["level"]), r["row"])
            for r in conn.execute(
                "SELECT level, row FROM starpass_claim WHERE season_id = ? AND user_id = ?", (sid, uid)
            )
        }
        custom = _rewards_map(conn, season)
    unlocked = level_for_credits(credits, total)
    levels = []
    for lv in range(1, total + 1):
        entry = {"level": lv}
        for row in ROWS:
            entry[row] = {
                "reward": _reward_for(custom, season, lv, row),
                "state": _state(lv, row, unlocked, premium, claimed),
            }
        levels.append(entry)
    ready = sum(1 for e in levels for r in ROWS if e[r]["state"] == "ready")
    return {
        "season": _season_dict(season), "credits": credits, "unlocked": unlocked,
        "progress": progress(credits, total), "levels": levels, "ready": ready, "premium": bool(premium),
    }


# ── Collecting ───────────────────────────────────────────────────────────────

def collect(user_id: int, premium: bool, player_values: dict[str, int] | None = None,
            pass_key: str = DEFAULT_PASS) -> dict | str:
    """Collect every unlocked, not yet collected reward in ONE transaction.

    `player_values` = {player_key: coin value} used when the user already owns a card
    (the duplicate is paid out as coins, like pack duplicates).
    Returns {"items": [...], "coins": n, "vouchers": n, "packs": {...}, "new": [...],
    "pending": [...], "dupes": [...], "refund": n, "count": n} or an error message.
    Safe against button spam: a claim row can only be inserted once.
    """
    uid = int(user_id)
    values = player_values or {}
    with economy._tx() as conn:
        season = _active(conn, pass_key)
        if season is None:
            return "This Star Pass has ended."
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return "You haven't debuted yet."
        sid, total = int(season["id"]), int(season["levels"])
        u = conn.execute(
            "SELECT credits FROM starpass_user WHERE season_id = ? AND user_id = ?", (sid, uid)
        ).fetchone()
        unlocked = level_for_credits(int(u["credits"]) if u else 0, total)
        custom = _rewards_map(conn, season)

        out = {"items": [], "coins": 0, "vouchers": 0, "packs": {}, "new": [], "pending": [],
               "dupes": [], "refund": 0, "count": 0}
        for lv in range(1, unlocked + 1):
            for row in ROWS:
                if row == PREMIUM and not premium:
                    continue
                rw = _reward_for(custom, season, lv, row)
                if rw.kind == "player" and rw.player_key and economy._event_disabled(rw.player_key):
                    out["blocked"] = out.get("blocked", 0) + 1
                    continue                      # its event is disabled: not collected (and not used up)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO starpass_claim (season_id, user_id, level, row, ts) "
                    "VALUES (?, ?, ?, ?, ?)", (sid, uid, lv, row, time.time()),
                )
                if cur.rowcount != 1:
                    continue                      # already collected
                _pay(conn, uid, rw, lv, row, out, values)
                out["count"] += 1
                out["items"].append((lv, row, rw))
        if out["count"] == 0:
            if out.get("blocked"):
                return "The rewards waiting for you are cards of a disabled event, so they can't be collected right now."
            return "There is nothing to collect right now."
        return out


def _pay(conn, uid: int, rw: Reward, level: int, row: str, out: dict, values: dict) -> None:
    reason = f"Star Pass level {level} ({row})"      # same text for every pass (old logs stay readable)
    if rw.kind == "coins":
        economy._apply_coins(conn, uid, rw.qty, reason)
        out["coins"] += rw.qty
    elif rw.kind == "voucher":
        economy._voucher_change(conn, uid, rw.qty, reason)
        out["vouchers"] += rw.qty
    elif rw.kind in PACK_KINDS:
        conn.execute(
            "INSERT INTO packs (user_id, pack_type, qty) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, pack_type) DO UPDATE SET qty = qty + excluded.qty",
            (uid, rw.kind, rw.qty),
        )
        out["packs"][rw.kind] = out["packs"].get(rw.kind, 0) + rw.qty
    elif rw.kind == "theme" and rw.player_key:
        import summary_theme
        is_new = summary_theme.grant_conn(conn, uid, rw.player_key, f"Star Pass level {level}")
        out.setdefault("themes", []).append((rw.player_key, is_new))
    elif rw.kind == "player" and rw.player_key:
        key = rw.player_key
        if key in out["new"] or key in out["pending"]:
            status = "dupe"
        else:
            status = economy._grant(conn, uid, key, "Star Pass")
        if status == "added":
            out["new"].append(key)
        elif status == "pending":
            out["pending"].append(key)
        else:
            out["dupes"].append(key)
            refund = int(values.get(key, 0))
            if refund:
                economy._apply_coins(conn, uid, refund, f"Duplicate refund (Star Pass level {level})")
                out["refund"] += refund


def player_keys_in_pass(pass_key: str = DEFAULT_PASS) -> list[str]:
    """Every player card used as a reward in the running season of a pass (for duplicate pricing)."""
    with economy._tx() as conn:
        season = _active(conn, pass_key)
        if season is None:
            return []
        return [r["player_key"] for r in conn.execute(
            "SELECT player_key FROM starpass_reward WHERE season_id = ? AND kind = 'player' "
            "AND player_key IS NOT NULL", (season["id"],))]
