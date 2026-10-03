"""
match_records.py — Persistent storage for per-match W/L results.
Provides:
  • record_match_result()   — called once per completed match
  • get_head_to_head()      — W/L + avg score vs a specific opponent
  • get_form()              — last N match results with scores
  • get_weekly_mvp()        — most active / best avg-score player this week
  • get_achievements()      — badge evaluation for a user
  • init_match_records_db() — create tables (call on startup)
"""

import sqlite3
import os
import time
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "cards.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ── Schema ────────────────────────────────────────────────────────────────────

def init_match_records_db():
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS match_results (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            winner_id    TEXT    NOT NULL,
            loser_id     TEXT    NOT NULL,
            winner_score INTEGER NOT NULL DEFAULT 0,
            loser_score  INTEGER NOT NULL DEFAULT 0,
            overs        INTEGER NOT NULL DEFAULT 5,
            played_at    REAL    NOT NULL DEFAULT (unixepoch())
        )
    """)
    # Separate row per participant so queries stay simple
    c.execute("""
        CREATE TABLE IF NOT EXISTS match_participants (
            match_id  INTEGER NOT NULL REFERENCES match_results(id),
            user_id   TEXT    NOT NULL,
            won       INTEGER NOT NULL DEFAULT 0,
            my_score  INTEGER NOT NULL DEFAULT 0,
            opp_score INTEGER NOT NULL DEFAULT 0,
            opp_id    TEXT    NOT NULL,
            played_at REAL    NOT NULL DEFAULT (unixepoch())
        )
    """)
    # Index for fast lookups
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_mp_user ON match_participants(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mp_user_opp ON match_participants(user_id, opp_id)")
    except Exception:
        pass
    conn.commit()
    conn.close()


# ── Write ─────────────────────────────────────────────────────────────────────

def record_match_result(
    winner_id: int | str,
    loser_id: int | str,
    winner_score: int,
    loser_score: int,
    overs: int = 5,
):
    """Persist the outcome of a completed match. Tied matches are not recorded."""
    winner_id = str(winner_id)
    loser_id  = str(loser_id)
    now       = time.time()

    conn = _conn()
    c    = conn.cursor()
    c.execute(
        "INSERT INTO match_results (winner_id, loser_id, winner_score, loser_score, overs, played_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (winner_id, loser_id, winner_score, loser_score, overs, now),
    )
    match_id = c.lastrowid

    # Winner row
    c.execute(
        "INSERT INTO match_participants (match_id, user_id, won, my_score, opp_score, opp_id, played_at) "
        "VALUES (?, ?, 1, ?, ?, ?, ?)",
        (match_id, winner_id, winner_score, loser_score, loser_id, now),
    )
    # Loser row
    c.execute(
        "INSERT INTO match_participants (match_id, user_id, won, my_score, opp_score, opp_id, played_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?)",
        (match_id, loser_id, loser_score, winner_score, winner_id, now),
    )
    conn.commit()
    conn.close()


# ── Queries ───────────────────────────────────────────────────────────────────

def get_head_to_head(user_id: int | str, opp_id: int | str) -> dict:
    """Return W/L record and average scores between two users."""
    user_id = str(user_id)
    opp_id  = str(opp_id)

    conn = _conn()
    c    = conn.cursor()
    c.execute("""
        SELECT
            COUNT(*)              AS total,
            SUM(won)              AS wins,
            COUNT(*) - SUM(won)   AS losses,
            ROUND(AVG(my_score),1)  AS avg_for,
            ROUND(AVG(opp_score),1) AS avg_against
        FROM match_participants
        WHERE user_id = ? AND opp_id = ?
    """, (user_id, opp_id))
    row = dict(c.fetchone())
    conn.close()
    row["user_id"] = user_id
    row["opp_id"]  = opp_id
    return row


def get_form(user_id: int | str, limit: int = 5) -> list[dict]:
    """Return the last `limit` completed matches for a user, most-recent first."""
    user_id = str(user_id)

    conn = _conn()
    c    = conn.cursor()
    c.execute("""
        SELECT won, my_score, opp_score, opp_id, played_at
        FROM match_participants
        WHERE user_id = ?
        ORDER BY played_at DESC
        LIMIT ?
    """, (user_id, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_weekly_mvp(guild_id: int | str | None = None, limit: int = 1) -> list[dict]:
    """
    Return top player(s) by matches played this calendar week (Mon-Sun UTC).
    Tie-breaks on total runs scored (from career_stats).
    """
    # Start of this week (Monday 00:00 UTC)
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_ts = week_start.timestamp()

    conn = _conn()
    c    = conn.cursor()
    c.execute("""
        SELECT user_id,
               COUNT(*)       AS matches_played,
               SUM(won)       AS wins,
               ROUND(AVG(my_score), 1) AS avg_score
        FROM match_participants
        WHERE played_at >= ?
        GROUP BY user_id
        ORDER BY matches_played DESC, avg_score DESC
        LIMIT ?
    """, (week_ts, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Achievements ──────────────────────────────────────────────────────────────

ACHIEVEMENTS = [
    # (id, emoji, name, description, check_fn)
    # check_fn receives (user_id:str, conn) → bool
]

def _ach(aid, emoji, name, desc, fn):
    ACHIEVEMENTS.append((aid, emoji, name, desc, fn))


def _total_wins(uid, conn):
    r = conn.execute("SELECT SUM(won) FROM match_participants WHERE user_id=?", (uid,)).fetchone()
    return (r[0] or 0)

def _total_matches(uid, conn):
    r = conn.execute("SELECT COUNT(*) FROM match_participants WHERE user_id=?", (uid,)).fetchone()
    return (r[0] or 0)

def _max_score(uid, conn):
    r = conn.execute("SELECT MAX(my_score) FROM match_participants WHERE user_id=?", (uid,)).fetchone()
    return (r[0] or 0)

def _streak_wins(uid, conn):
    """Longest consecutive win streak."""
    rows = conn.execute(
        "SELECT won FROM match_participants WHERE user_id=? ORDER BY played_at ASC", (uid,)
    ).fetchall()
    best = cur = 0
    for row in rows:
        if row[0]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best

def _total_runs(uid, conn):
    try:
        r = conn.execute("SELECT SUM(bat_runs) FROM career_stats WHERE user_id=?", (uid,)).fetchone()
        return (r[0] or 0)
    except Exception:
        return 0

def _total_wickets(uid, conn):
    try:
        r = conn.execute("SELECT SUM(bowl_wickets) FROM career_stats WHERE user_id=?", (uid,)).fetchone()
        return (r[0] or 0)
    except Exception:
        return 0

def _has_century(uid, conn):
    try:
        r = conn.execute("SELECT SUM(bat_hundreds) FROM career_stats WHERE user_id=?", (uid,)).fetchone()
        return (r[0] or 0) >= 1
    except Exception:
        return False

def _has_five_wi(uid, conn):
    try:
        r = conn.execute("SELECT SUM(bowl_five_wi) FROM career_stats WHERE user_id=?", (uid,)).fetchone()
        return (r[0] or 0) >= 1
    except Exception:
        return False

# Register all badges
_ach("debut",       "🎽", "First Cap",        "Play your first match",
     lambda u, c: _total_matches(u, c) >= 1)
_ach("first_win",   "🏆", "First Win",         "Win your first match",
     lambda u, c: _total_wins(u, c) >= 1)
_ach("century",     "💯", "Century Maker",     "Score a century in any match",
     lambda u, c: _has_century(u, c))
_ach("five_wi",     "🎯", "Five-Fer Hero",     "Take a five-wicket haul",
     lambda u, c: _has_five_wi(u, c))
_ach("hat_trick",   "🎩", "Hat-Trick Hero",    "Win 3 matches in a row",
     lambda u, c: _streak_wins(u, c) >= 3)
_ach("road_runner", "🏃", "Road Runner",        "Play 10 matches",
     lambda u, c: _total_matches(u, c) >= 10)
_ach("veteran",     "⭐", "Veteran",            "Play 25 matches",
     lambda u, c: _total_matches(u, c) >= 25)
_ach("legend",      "👑", "Legend",             "Play 50 matches",
     lambda u, c: _total_matches(u, c) >= 50)
_ach("dominator",   "💪", "Dominator",          "Win 20 matches",
     lambda u, c: _total_wins(u, c) >= 20)
_ach("run_machine", "🔥", "Run Machine",        "Accumulate 1,000 career runs",
     lambda u, c: _total_runs(u, c) >= 1000)
_ach("wicket_witch","🌀", "Wicket Witch",       "Take 50 career wickets",
     lambda u, c: _total_wickets(u, c) >= 50)
_ach("high_scorer", "📈", "High Scorer",        "Post a team score of 100+ in a match",
     lambda u, c: _max_score(u, c) >= 100)


def get_achievements(user_id: int | str) -> list[dict]:
    """
    Return a list of dicts with keys: id, emoji, name, desc, earned (bool).
    """
    uid  = str(user_id)
    conn = _conn()
    results = []
    for aid, emoji, name, desc, fn in ACHIEVEMENTS:
        try:
            earned = bool(fn(uid, conn))
        except Exception:
            earned = False
        results.append({"id": aid, "emoji": emoji, "name": name, "desc": desc, "earned": earned})
    conn.close()
    return results
