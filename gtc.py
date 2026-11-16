"""Guess the Cricketer (GTC): database + name matching. No Discord code here.

Rules (see gtc_cog.py for the game loop):
- 6 games a day at 09:00, 11:00, 14:00, 19:00, 20:30, 23:00 India time, in the channel the owner set.
- A game = ROUNDS rounds. Each round shows a player's photo (foreground); the first person to type
  the right FULL name in chat gets 1 point.
- Most points in a game wins GAME_WIN_RCP (5) RCP. A player can win at most DAILY_RCP_CAP (15) RCP
  a day from games.
- After the last game of the day (23:00) the day's top scorer gets DAILY_TOP_RCP (5) extra RCP
  (on top of the cap), and the day's leaderboard is posted.
- RCP goes into the same wallet as the Signature Box / store (sigbox.rcp_change).

Tables (economy.db):
    gtc_settings  key -> value   (channel, ping_role)
    gtc_daily     (day, user_id) -> points, game wins, rcp won that day
    gtc_games     one row per game played (day, slot, winner, scores json)
    gtc_alltime   user_id -> total points, game wins, days won, rcp
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

import economy

IST = timezone(timedelta(hours=5, minutes=30))
GAME_HOURS = [(9, 0), (11, 0), (14, 0), (19, 0), (20, 30), (23, 0)]   # India time
ROUNDS = 6
GAME_WIN_RCP = 5
DAILY_RCP_CAP = 15
DAILY_TOP_RCP = 5
OVR_RANGE = (50, 99)


def today() -> str:
    return datetime.now(IST).date().isoformat()


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS gtc_settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS gtc_daily (day TEXT NOT NULL, user_id INTEGER NOT NULL, "
                 "points INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0, rcp INTEGER NOT NULL DEFAULT 0, "
                 "PRIMARY KEY (day, user_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS gtc_games (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, "
                 "slot TEXT, channel_id INTEGER, winner_id INTEGER, rcp INTEGER NOT NULL DEFAULT 0, "
                 "scores TEXT, ts REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS gtc_alltime (user_id INTEGER PRIMARY KEY, points INTEGER NOT NULL DEFAULT 0, "
                 "wins INTEGER NOT NULL DEFAULT 0, days_won INTEGER NOT NULL DEFAULT 0, rcp INTEGER NOT NULL DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS gtc_daytop (day TEXT PRIMARY KEY, user_id INTEGER, points INTEGER)")


def ensure_tables() -> None:
    with economy._tx() as conn:
        _ensure(conn)


# ── settings ──────────────────────────────────────────────────────────────────
def get_setting(key: str) -> str | None:
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute("SELECT value FROM gtc_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str | None) -> None:
    with economy._tx() as conn:
        _ensure(conn)
        if value is None:
            conn.execute("DELETE FROM gtc_settings WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO gtc_settings (key, value) VALUES (?, ?)", (key, str(value)))


# ── names ─────────────────────────────────────────────────────────────────────
# Common spellings of the same first name, all treated as one word.
_SAME = [
    ("mohammad", ("mohd", "md", "mohammed", "muhammad", "muhammed", "mohamed", "mohamad", "mohammad")),
    ("abdul", ("abd", "abdul")),
    ("syed", ("sayed", "saiyed", "syed")),
    ("ravichandran", ("r", "ravichandran")),
]
_ALIAS = {v: k for k, vs in _SAME for v in vs}


def norm(text: str) -> str:
    """Lower case, letters and digits only, (S1)/(HW)-style tags removed, name spellings unified."""
    t = str(text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [_ALIAS.get(w, w) for w in t.split()]
    return " ".join(words)


def answer_names(card) -> set[str]:
    """The FULL name is needed: the card name without its event tag, the player group, or the name
    on the card (displayname) when that is a full name too (two words or more). A surname alone
    does not count. Spellings like Mohd / Mohammed / Muhammad are treated as the same."""
    def col(k):
        try:
            return card[k]
        except (IndexError, KeyError):
            return None
    names = {norm(card["playername"]), norm(col("player_group"))}
    shown = norm(col("displayname"))
    if len(shown.split()) >= 2:
        names.add(shown)
    # a name that starts with a single initial ("a raghuvanshi", "s sreesanth") also counts without it,
    # because the initial is not really part of the name people know
    for n in list(names):
        w = n.split()
        if len(w) >= 2 and len(w[0]) == 1:
            names.add(" ".join(w[1:]))
    return {n for n in names if len(n) >= 3}


def _close(a: str, b: str) -> bool:
    """One typo allowed for long names (a letter wrong, missing, extra, or two swapped)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 6 or abs(len(a) - len(b)) > 1:
        return False
    # Damerau-Levenshtein distance <= 1
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb] <= 1


def is_correct(guess: str, names: set[str]) -> bool:
    """Exact full name only (no typos). Capital letters, dots, extra spaces and "(S1)"-style tags
    don't matter, and Mohd / Mohammed / Muhammad count as the same word."""
    g = norm(guess)
    return len(g) >= 3 and g in names


# ── results ───────────────────────────────────────────────────────────────────
def record_game(day: str, slot: str, channel_id: int, scores: dict[int, int], winner: int | None) -> dict:
    """Save a finished game. Adds everyone's points; gives the winner GAME_WIN_RCP (up to the daily cap).
    Returns {"rcp": given, "capped": bool}."""
    out = {"rcp": 0, "capped": False}
    with economy._tx() as conn:
        _ensure(conn)
        for uid, pts in scores.items():
            if pts <= 0:
                continue
            conn.execute("INSERT INTO gtc_daily (day, user_id, points) VALUES (?, ?, ?) "
                         "ON CONFLICT(day, user_id) DO UPDATE SET points = points + excluded.points", (day, uid, pts))
            conn.execute("INSERT INTO gtc_alltime (user_id, points) VALUES (?, ?) "
                         "ON CONFLICT(user_id) DO UPDATE SET points = points + excluded.points", (uid, pts))
        if winner:
            conn.execute("INSERT INTO gtc_daily (day, user_id, wins) VALUES (?, ?, 1) "
                         "ON CONFLICT(day, user_id) DO UPDATE SET wins = wins + 1", (day, winner))
            conn.execute("INSERT INTO gtc_alltime (user_id, wins) VALUES (?, 1) "
                         "ON CONFLICT(user_id) DO UPDATE SET wins = wins + 1", (winner,))
            got = conn.execute("SELECT rcp FROM gtc_daily WHERE day = ? AND user_id = ?", (day, winner)).fetchone()
            room = DAILY_RCP_CAP - int(got["rcp"] if got else 0)
            give = max(0, min(GAME_WIN_RCP, room))
            out["capped"] = give < GAME_WIN_RCP
            if give:
                conn.execute("UPDATE gtc_daily SET rcp = rcp + ? WHERE day = ? AND user_id = ?", (give, day, winner))
                conn.execute("UPDATE gtc_alltime SET rcp = rcp + ? WHERE user_id = ?", (give, winner))
                out["rcp"] = give
        conn.execute("INSERT INTO gtc_games (day, slot, channel_id, winner_id, rcp, scores, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (day, slot, channel_id, winner, out["rcp"], json.dumps({str(k): v for k, v in scores.items()}),
                      time.time()))
    return out


def daily_board(day: str, limit: int = 10) -> list[dict]:
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT user_id, points, wins, rcp FROM gtc_daily WHERE day = ? AND points > 0 "
                            "ORDER BY points DESC, wins DESC, rcp DESC, user_id LIMIT ?", (day, limit)).fetchall()
        return [dict(r) for r in rows]


def alltime_board(limit: int = 10) -> list[dict]:
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT user_id, points, wins, days_won, rcp FROM gtc_alltime WHERE points > 0 "
                            "ORDER BY points DESC, wins DESC, user_id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def user_stats(user_id: int, day: str) -> dict:
    with economy._tx() as conn:
        _ensure(conn)
        d = conn.execute("SELECT points, wins, rcp FROM gtc_daily WHERE day = ? AND user_id = ?", (day, user_id)).fetchone()
        a = conn.execute("SELECT points, wins, days_won, rcp FROM gtc_alltime WHERE user_id = ?", (user_id,)).fetchone()
        rank = None
        if d and d["points"] > 0:
            rank = 1 + conn.execute("SELECT COUNT(*) FROM gtc_daily WHERE day = ? AND (points > ? OR (points = ? AND wins > ?))",
                                    (day, d["points"], d["points"], d["wins"])).fetchone()[0]
        return {"today": dict(d) if d else {"points": 0, "wins": 0, "rcp": 0},
                "all": dict(a) if a else {"points": 0, "wins": 0, "days_won": 0, "rcp": 0},
                "rank": rank}


def close_day(day: str) -> dict | None:
    """Day over: the top scorer gets DAILY_TOP_RCP extra (once per day). Returns the top row or None."""
    with economy._tx() as conn:
        _ensure(conn)
        if conn.execute("SELECT 1 FROM gtc_daytop WHERE day = ?", (day,)).fetchone():
            return None
        top = conn.execute("SELECT user_id, points, wins FROM gtc_daily WHERE day = ? AND points > 0 "
                           "ORDER BY points DESC, wins DESC, rcp DESC, user_id LIMIT 1", (day,)).fetchone()
        conn.execute("INSERT INTO gtc_daytop (day, user_id, points) VALUES (?, ?, ?)",
                     (day, top["user_id"] if top else None, top["points"] if top else 0))
        if not top:
            return None
        conn.execute("UPDATE gtc_alltime SET days_won = days_won + 1, rcp = rcp + ? WHERE user_id = ?",
                     (DAILY_TOP_RCP, top["user_id"]))
        return dict(top)


def next_game_ts(now: datetime | None = None) -> int:
    now = now or datetime.now(IST)
    for h, m in GAME_HOURS:
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t > now:
            return int(t.timestamp())
    h, m = GAME_HOURS[0]
    return int((now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0).timestamp())
