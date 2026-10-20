"""Daily Quest system.

Every day (resets at midnight IST) there are 5 quests, the same for everybody:
3 easy + 1 medium + 1 hard. Finishing ALL of the day's quests gives QUEST_VOUCHER voucher(s)
(one voucher for the whole day, NOT one per quest).
The 5 quests change every day and are never the same set as yesterday.

How it works
- Game events call  track(user_id, {metric: amount})  (e.g. {"sixes": 2}).
- Counters are stored per user per day. When a counter reaches a quest's target the
  quest is marked done. When the LAST quest of the day is marked done, the single daily
  voucher is paid in the SAME database transaction, so it can never pay twice and a crash
  can never pay without marking it.
- Practice (bot) matches never reach these hooks, so they never count.
- Match quests only count real matches of QUEST_MIN_OVERS+ overs, and at most
  QUEST_PER_OPP_CAP matches a day against the same opponent (stops alt farming).

To add / change a quest, edit the QUESTS list below. Nothing else needs touching.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import economy
import starpass

# ── Settings ─────────────────────────────────────────────────────────────────
QUEST_VOUCHER = 1            # vouchers for finishing ALL quests of the day (paid once)
QUEST_MIN_OVERS = 2          # a match shorter than this does not count for quests
QUEST_PER_OPP_CAP = 3        # matches per day vs the SAME opponent that count
SLOTS = (("easy", 3), ("medium", 1), ("hard", 1))     # 5 quests a day
KEEP_DAYS = 10               # old quest rows are deleted after this many days

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class Quest:
    id: str
    tier: str        # easy / medium / hard
    title: str       # shown to the player (keep it short: ~34 characters max)
    metric: str      # which counter it watches
    target: int


# ── Metrics ──────────────────────────────────────────────────────────────────
# "sum" metrics add up through the day. "max" metrics keep the best single value.
MAX_METRICS = {"best_bat", "bowlers_used"}

# ── The quest pool ───────────────────────────────────────────────────────────
# Rule used when picking a day: never two quests with the same metric, so you
# don't get "Play 1 match" and "Play 2 matches" on the same day.
QUESTS: list[Quest] = [
    # -- easy ---------------------------------------------------------------
    Quest("e_play1",   "easy", "Play 1 match",                    "matches_played", 1),
    Quest("e_daily",   "easy", "Claim your daily reward",         "daily_claim",    1),
    Quest("e_six3",    "easy", "Hit 3 sixes",                     "sixes",          3),
    Quest("e_four5",   "easy", "Hit 5 fours",                     "fours",          5),
    Quest("e_runs40",  "easy", "Score 40 runs",                   "runs",           40),
    Quest("e_wkt2",    "easy", "Take 2 wickets",                  "wickets",        2),
    Quest("e_draft1",  "easy", "Do 1 draft",                      "drafts",         1),
    Quest("e_open1",   "easy", "Open 1 pack",                     "packs_opened",   1),
    Quest("e_xi",      "easy", "Check your playing XI",           "xi_view",        1),
    Quest("e_captain", "easy", "Set your captain",                "captain_set",    1),
    Quest("e_profile", "easy", "Check your profile",              "profile_view",   1),
    Quest("e_board",   "easy", "Check the leaderboard",           "lb_view",        1),
    Quest("e_5ov",     "easy", "Play a 5+ over match",            "matches_5ov",    1),
    Quest("e_six2",    "easy", "Hit 2 sixes",                     "sixes",          2),
    Quest("e_four3",   "easy", "Hit 3 fours",                     "fours",          3),
    Quest("e_runs25",  "easy", "Score 25 runs",                   "runs",           25),
    Quest("e_wkt1",    "easy", "Take 1 wicket",                   "wickets",        1),
    Quest("e_bat20",   "easy", "Score 20+ with one batter",       "best_bat",       20),
    # -- medium -------------------------------------------------------------
    Quest("m_play2",   "medium", "Play 2 matches",                "matches_played", 2),
    Quest("m_win1",    "medium", "Win 1 match",                   "matches_won",    1),
    Quest("m_six6",    "medium", "Hit 6 sixes",                   "sixes",          6),
    Quest("m_four10",  "medium", "Hit 10 fours",                  "fours",          10),
    Quest("m_runs80",  "medium", "Score 80 runs",                 "runs",           80),
    Quest("m_wkt4",    "medium", "Take 4 wickets",                "wickets",        4),
    Quest("m_bat30",   "medium", "Score 30+ with one batter",     "best_bat",       30),
    Quest("m_maiden",  "medium", "Bowl a maiden over",            "maidens",        1),
    Quest("m_bowl3",   "medium", "Use 3 different bowlers",       "bowlers_used",   3),
    Quest("m_draft3",  "medium", "Do 3 drafts",                   "drafts",         3),
    Quest("m_10ov",    "medium", "Play a 10+ over match",         "matches_10ov",   1),
    Quest("m_buy",     "medium", "Buy 1 pack",                    "packs_bought",   1),
    Quest("m_runs60",  "medium", "Score 60 runs",                 "runs",           60),
    Quest("m_wkt3",    "medium", "Take 3 wickets",                "wickets",        3),
    Quest("m_six5",    "medium", "Hit 5 sixes",                   "sixes",          5),
    Quest("m_four8",   "medium", "Hit 8 fours",                   "fours",          8),
    Quest("m_bat25",   "medium", "Score 25+ with one batter",     "best_bat",       25),
    Quest("m_draft2",  "medium", "Do 2 drafts",                   "drafts",         2),
    # -- hard (a bit harder, still friendly) ---------------------------------
    Quest("h_win2",    "hard", "Win 2 matches",                   "matches_won",    2),
    Quest("h_chase",   "hard", "Win a match while chasing",       "chase_wins",     1),
    Quest("h_defend",  "hard", "Win a match batting first",       "defend_wins",    1),
    Quest("h_play4",   "hard", "Play 4 matches",                  "matches_played", 4),
    Quest("h_runs150", "hard", "Score 150 runs",                  "runs",           150),
    Quest("h_wkt8",    "hard", "Take 8 wickets",                  "wickets",        8),
    Quest("h_six10",   "hard", "Hit 10 sixes",                    "sixes",          10),
    Quest("h_bat40",   "hard", "Score 40+ with one batter",       "best_bat",       40),
    Quest("h_bat35",   "hard", "Score 35+ with one batter",       "best_bat",       35),
    Quest("h_runs120", "hard", "Score 120 runs",                  "runs",           120),
    Quest("h_wkt6",    "hard", "Take 6 wickets",                  "wickets",        6),
    Quest("h_six8",    "hard", "Hit 8 sixes",                     "sixes",          8),
    Quest("h_play3",   "hard", "Play 3 matches",                  "matches_played", 3),
]
BY_ID: dict[str, Quest] = {q.id: q for q in QUESTS}
TIER_ORDER = {"easy": 0, "medium": 1, "hard": 2}


# ── Time (day = IST calendar day) ────────────────────────────────────────────

def today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _yesterday(day: str) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def seconds_to_reset() -> int:
    now = datetime.now(IST)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((nxt - now).total_seconds()))


# ── Picking the day's quests ─────────────────────────────────────────────────

def _pick(day: str, exclude: set[str]) -> list[str]:
    """3 easy + 1 medium + 1 hard for `day` (same result every time for the same
    day and exclude set). Never two quests on the same metric."""
    rng = random.Random(f"csquest:{day}")
    chosen: list[str] = []
    metrics: set[str] = set()
    for tier, n in SLOTS:
        tier_pool = [q for q in QUESTS if q.tier == tier]
        rng.shuffle(tier_pool)
        # best: a quest AND a quest type that wasn't used yesterday; then just a different quest;
        # then (only if the pool runs dry) anything.
        y_metrics = {BY_ID[i].metric for i in exclude if i in BY_ID}
        ordered = (
            [q for q in tier_pool if q.id not in exclude and q.metric not in y_metrics]
            + [q for q in tier_pool if q.id not in exclude and q.metric in y_metrics]
            + [q for q in tier_pool if q.id in exclude]
        )
        got = 0
        for q in ordered:
            if q.metric in metrics:
                continue
            chosen.append(q.id)
            metrics.add(q.metric)
            got += 1
            if got == n:
                break
    return chosen


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS quest_day (day TEXT PRIMARY KEY, ids TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quest_counter ("
        "user_id INTEGER NOT NULL, day TEXT NOT NULL, metric TEXT NOT NULL, value INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (user_id, day, metric))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quest_done ("
        "user_id INTEGER NOT NULL, day TEXT NOT NULL, quest_id TEXT NOT NULL, ts REAL NOT NULL, "
        "PRIMARY KEY (user_id, day, quest_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quest_opp ("
        "user_id INTEGER NOT NULL, day TEXT NOT NULL, opp_id INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (user_id, day, opp_id))"
    )


def ensure_tables() -> None:
    conn = economy._conn()
    try:
        _ensure(conn)
    finally:
        conn.close()


def _day_ids(conn, day: str) -> list[str]:
    """The saved quests of `day` (created on first use and then frozen for the day,
    so editing QUESTS never changes a day that already started)."""
    row = conn.execute("SELECT ids FROM quest_day WHERE day = ?", (day,)).fetchone()
    if row is not None:
        ids = [i for i in row["ids"].split(",") if i in BY_ID]
        if len(ids) == sum(n for _, n in SLOTS):
            return ids
    y = _yesterday(day)
    yrow = conn.execute("SELECT ids FROM quest_day WHERE day = ?", (y,)).fetchone()
    y_ids = set(yrow["ids"].split(",")) if yrow else set(_pick(y, set()))
    ids = _pick(day, y_ids)
    conn.execute("INSERT OR REPLACE INTO quest_day (day, ids) VALUES (?, ?)", (day, ",".join(ids)))
    cutoff = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for table in ("quest_day", "quest_counter", "quest_done", "quest_opp"):
        conn.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff,))
    return ids


def today_quests() -> list[Quest]:
    with economy._tx() as conn:
        _ensure(conn)
        ids = _day_ids(conn, today())
    return sorted((BY_ID[i] for i in ids), key=lambda q: TIER_ORDER[q.tier])


# ── Tracking ─────────────────────────────────────────────────────────────────

# (user_id, quest_id) pairs finished by match code, waiting to be announced in the channel.
_NOTES: list[tuple[int, str]] = []
# (user_id, day) pairs whose final quest was finished by a track() call -> voucher just paid.
_ALL_DONE: set[tuple[int, str]] = set()


def track(user_id, metrics: dict) -> list[str]:
    """Add to today's counters and pay every quest that just reached its target.
    Returns the ids of the quests finished by THIS call (usually empty)."""
    uid = int(user_id)
    metrics = {m: int(v) for m, v in metrics.items() if v}
    metrics = {m: v for m, v in metrics.items() if v > 0}
    if not metrics:
        return []
    day = today()
    with economy._tx() as conn:
        _ensure(conn)
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return []
        ids = _day_ids(conn, day)
        for m, v in metrics.items():
            if m in MAX_METRICS:
                conn.execute(
                    "INSERT INTO quest_counter (user_id, day, metric, value) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, day, metric) DO UPDATE SET value = MAX(value, excluded.value)",
                    (uid, day, m, v),
                )
            else:
                conn.execute(
                    "INSERT INTO quest_counter (user_id, day, metric, value) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, day, metric) DO UPDATE SET value = value + excluded.value",
                    (uid, day, m, v),
                )
        finished: list[str] = []
        for qid in ids:
            q = BY_ID[qid]
            if q.metric not in metrics:
                continue
            row = conn.execute(
                "SELECT value FROM quest_counter WHERE user_id = ? AND day = ? AND metric = ?",
                (uid, day, q.metric),
            ).fetchone()
            if row is None or int(row["value"]) < q.target:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO quest_done (user_id, day, quest_id, ts) VALUES (?, ?, ?, ?)",
                (uid, day, qid, time.time()),
            )
            if cur.rowcount == 1:
                finished.append(qid)
        if finished:
            # Star Pass credits, paid in this SAME transaction (quest_done rows are unique, so a
            # quest can never pay credits twice). Does nothing when no season is running.
            starpass.award_quest_credits(conn, uid, len(finished))
            n_done = conn.execute(
                "SELECT COUNT(*) AS n FROM quest_done WHERE user_id = ? AND day = ?", (uid, day)
            ).fetchone()["n"]
            if int(n_done) >= len(ids):
                # whole day cleared: pay the one daily voucher (quest_done rows are unique
                # per quest, so this block runs exactly once per user per day)
                economy._voucher_change(conn, uid, QUEST_VOUCHER, "Quest: all daily quests complete")
                _ALL_DONE.add((uid, day))
        return finished


def get_board(user_id) -> dict:
    """Everything the csquest screen needs for one user."""
    uid = int(user_id)
    day = today()
    with economy._tx() as conn:
        _ensure(conn)
        ids = _day_ids(conn, day)
        counters = {
            r["metric"]: int(r["value"])
            for r in conn.execute(
                "SELECT metric, value FROM quest_counter WHERE user_id = ? AND day = ?", (uid, day)
            )
        }
        done = {
            r["quest_id"]
            for r in conn.execute("SELECT quest_id FROM quest_done WHERE user_id = ? AND day = ?", (uid, day))
        }
        user = conn.execute("SELECT daily_streak FROM users WHERE user_id = ?", (uid,)).fetchone()
    rows = []
    for q in sorted((BY_ID[i] for i in ids), key=lambda q: TIER_ORDER[q.tier]):
        is_done = q.id in done
        value = q.target if is_done else min(counters.get(q.metric, 0), q.target)
        rows.append({"id": q.id, "tier": q.tier, "title": q.title, "target": q.target,
                     "progress": value, "done": is_done})
    return {
        "day": day,
        "quests": rows,
        "done_count": sum(1 for r in rows if r["done"]),
        "vouchers_earned": QUEST_VOUCHER if rows and all(r["done"] for r in rows) else 0,
        "reset_in": seconds_to_reset(),
        "streak": int(user["daily_streak"]) if user else 0,
    }


def reset_user_today(user_id) -> int:
    """Admin: wipe one user's quest progress for today (vouchers already paid stay).
    Returns how many finished quests were cleared."""
    uid, day = int(user_id), today()
    with economy._tx() as conn:
        _ensure(conn)
        n = conn.execute("SELECT COUNT(*) AS n FROM quest_done WHERE user_id = ? AND day = ?", (uid, day)).fetchone()["n"]
        conn.execute("DELETE FROM quest_done WHERE user_id = ? AND day = ?", (uid, day))
        conn.execute("DELETE FROM quest_counter WHERE user_id = ? AND day = ?", (uid, day))
        conn.execute("DELETE FROM quest_opp WHERE user_id = ? AND day = ?", (uid, day))
        return int(n)


# ── Match hooks (called from views.py / career_stats.py: real matches only) ──

def _is_practice(game) -> bool:
    return bool(getattr(game, "is_bot_match", False)) or getattr(game, "cpu_difficulty", None) is not None


def _gate(game, a, b) -> dict:
    """{user_id: counts_for_quests} for this match. Decided once per match:
    needs enough overs and no more than QUEST_PER_OPP_CAP matches a day vs the same opponent."""
    gate = getattr(game, "_quest_gate", None)
    if gate is not None:
        return gate
    gate = {}
    try:
        a, b = int(a), int(b)
    except (TypeError, ValueError):
        return {}
    gate = {a: False, b: False}
    try:
        overs = int(getattr(game, "overs", 0) or 0)
    except (TypeError, ValueError):
        overs = 0
    if a != b and overs >= QUEST_MIN_OVERS and not _is_practice(game):
        day = today()
        with economy._tx() as conn:
            _ensure(conn)
            for me, opp in ((a, b), (b, a)):
                conn.execute(
                    "INSERT INTO quest_opp (user_id, day, opp_id, n) VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(user_id, day, opp_id) DO UPDATE SET n = n + 1",
                    (me, day, opp),
                )
                n = conn.execute(
                    "SELECT n FROM quest_opp WHERE user_id = ? AND day = ? AND opp_id = ?", (me, day, opp)
                ).fetchone()["n"]
                gate[me] = int(n) <= QUEST_PER_OPP_CAP
    game._quest_gate = gate
    return gate


def _record(uid: int, metrics: dict) -> None:
    for qid in track(uid, metrics):
        _NOTES.append((uid, qid))


def on_innings(game, batting_uid, bowling_uid) -> None:
    """One innings just ended: credit runs / sixes / fours / wickets to the right user."""
    if batting_uid is None or bowling_uid is None or _is_practice(game):
        return
    gate = _gate(game, batting_uid, bowling_uid)
    bat_uid, bowl_uid = int(batting_uid), int(bowling_uid)

    if gate.get(bat_uid):
        runs = fours = sixes = best = 0
        for s in (getattr(game, "batsman_stats", None) or {}).values():
            r = int(s.get("runs", s.get("r", 0)) or 0)
            runs += r
            fours += int(s.get("fours", s.get("4s", 0)) or 0)
            sixes += int(s.get("sixes", s.get("6s", 0)) or 0)
            best = max(best, r)
        _record(bat_uid, {"runs": runs, "fours": fours, "sixes": sixes, "best_bat": best})

    if gate.get(bowl_uid):
        wkts = maidens = used = 0
        for b in (getattr(game, "bowler_stats", None) or {}).values():
            if int(b.get("b", b.get("balls", 0)) or 0) <= 0:
                continue
            used += 1
            wkts += int(b.get("w", b.get("wickets", 0)) or 0)
            maidens += int(b.get("m", 0) or 0)
        _record(bowl_uid, {"wickets": wkts, "maidens": maidens, "bowlers_used": used})


def on_match_end(game, inn1_uid, inn2_uid, t1: int, t2: int) -> None:
    """Match finished (called for ties too). inn1_uid batted first, inn2_uid chased."""
    if inn1_uid is None or inn2_uid is None or _is_practice(game):
        return
    gate = _gate(game, inn1_uid, inn2_uid)
    try:
        overs = int(getattr(game, "overs", 0) or 0)
    except (TypeError, ValueError):
        overs = 0
    winner = None
    if t2 > t1:
        winner = int(inn2_uid)
    elif t1 > t2:
        winner = int(inn1_uid)
    for uid in (int(inn1_uid), int(inn2_uid)):
        if not gate.get(uid):
            continue
        m = {"matches_played": 1, "matches_5ov": 1 if overs >= 5 else 0, "matches_10ov": 1 if overs >= 10 else 0}
        if winner == uid:
            m["matches_won"] = 1
            m["chase_wins" if uid == int(inn2_uid) else "defend_wins"] = 1
        _record(uid, m)


# ── Announcing finished quests ───────────────────────────────────────────────

def pop_notes(user_ids) -> list[tuple[int, str]]:
    ids = {int(u) for u in user_ids if u is not None}
    out = [n for n in _NOTES if n[0] in ids]
    _NOTES[:] = [n for n in _NOTES if n[0] not in ids]
    return out


def _notice_embed(pairs: list[tuple[int, str]]):
    import discord
    lines = []
    day = today()
    for uid, qid in pairs:
        q = BY_ID.get(qid)
        if q:
            lines.append(f"<@{uid}> finished **{q.title}**")
        if (uid, day) in _ALL_DONE:
            _ALL_DONE.discard((uid, day))
            lines.append(f"<@{uid}> cleared ALL daily quests  ·  +{QUEST_VOUCHER} voucher")
    if not lines:
        return None
    embed = discord.Embed(title="Quest complete", description="\n".join(lines), color=0x2FBF9B)
    embed.set_footer(text="csquest to see today's quests")
    return embed


async def _send_notice(dest, pairs) -> None:
    import discord
    embed = _notice_embed(pairs)
    if embed is not None:
        await dest.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def flush_notes(channel, user_ids) -> None:
    """Match code: announce quests finished during this match (never raises)."""
    try:
        pairs = pop_notes(user_ids)
        if pairs:
            await _send_notice(channel, pairs)
    except Exception as e:
        print(f"[quest] could not announce quests: {type(e).__name__}: {e}")


async def track_and_notify(dest, user_id, metrics: dict) -> None:
    """Command code: track, and tell the player in `dest` (ctx / channel) if a quest finished.
    Never raises, so a quest problem can never break the real command."""
    try:
        import asyncio
        done = await asyncio.to_thread(track, user_id, metrics)
        if done and dest is not None:
            await _send_notice(dest, [(int(user_id), q) for q in done])
    except Exception as e:
        print(f"[quest] track failed: {type(e).__name__}: {e}")
