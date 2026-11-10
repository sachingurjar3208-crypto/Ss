"""Career Mode (data + rules). One own cricketer per player.

He gets stronger ONLY from real matches: `rcmp ranked` and `rcmp @user` (practice / CPU matches don't count).
It is slow on purpose: a player who hits the daily max EVERY day needs about 4.5 months to reach OVR 85
(about 11 months at 4 matches a day).

  * Every counted match costs the career player 25 stamina and gives XP: 10 for playing, +10 for a win.
  * Bonus XP: +10 on the first match of the day, +5 per win from 3 wins in a row.
  * Max 150 career XP a day (All-rounder gets x1.5). Non-ranked matches: max 3 a day vs the same opponent count.
  * No stamina (< 25) = the match still plays normally, but the career player gets no XP.
  * Stamina refills on its own: +10 an hour (faster with high FITNESS). Drinks: Water +20, Energy +50,
    Power Shot +100 (max 3 drinks a day).
  * XP goes into the role's stats: Batter 70% BAT / 30% FITNESS, Bowler 70% BOWL / 30% FITNESS,
    All-rounder 35% BAT / 35% BOWL / 30% FITNESS. Each stat point costs more XP the higher it is.
  * Stages by OVR: Club 0, State 65, T20 League 72, National A 80, International 85 (coins on promotion).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import economy

IST = timezone(timedelta(hours=5, minutes=30))

ROLES = {"bat": "Batter", "bowl": "Bowler", "ar": "All-rounder"}
START_STATS = {"bat": (62, 35, 55), "bowl": (35, 62, 55), "ar": (55, 55, 55)}   # bat, bowl, fit
STAT_CAP = 99
SPLIT = {"bat": {"bat": 0.7, "fit": 0.3}, "bowl": {"bowl": 0.7, "fit": 0.3},
         "ar": {"bat": 0.35, "bowl": 0.35, "fit": 0.3}}

MAX_STAMINA = 100.0
REGEN_PER_HOUR = 10.0                # at FITNESS 55; +1% speed per FITNESS point above 55
MATCH_COST = 25
XP_PLAY, XP_WIN = 10, 10
XP_FIRST_OF_DAY = 10                 # bonus on the first counted match of the day
XP_STREAK = 5                        # bonus per win once you have 3+ wins in a row
STREAK_FROM = 3
DAILY_XP_CAP = 150
XP_MULT = {"bat": 1.0, "bowl": 1.0, "ar": 1.5}   # all-rounder trains 3 stats, so gets more XP
PER_OPP_CAP = 3
MIN_OVERS = 2
DRINKS_PER_DAY = 3

DRINKS = {
    "water":  {"label": "Water",        "stamina": 20,  "price": 100_000, "boost": 0},
    "energy": {"label": "Energy Drink", "stamina": 50,  "price": 500_000, "boost": 0},
    "power":  {"label": "Power Shot",   "stamina": 100, "price": 0,       "boost": 0},   # events / codes only
}
START_DRINKS = {"water": 3, "energy": 1}

STAGES = [   # name, OVR needed, coins on promotion
    ("Club Cricket", 0, 0),
    ("State Team", 65, 10_000),
    ("T20 League", 72, 25_000),
    ("National A", 80, 50_000),
    ("International", 85, 100_000),
]


def _ensure(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS career_players ("
        "user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,"
        "bat INTEGER, bowl INTEGER, fit INTEGER, xp_bat INTEGER DEFAULT 0, xp_bowl INTEGER DEFAULT 0,"
        "xp_fit INTEGER DEFAULT 0, stamina REAL DEFAULT 100, stamina_ts REAL,"
        "stage INTEGER DEFAULT 0, stage_matches INTEGER DEFAULT 0, form TEXT DEFAULT '[]',"
        "matches INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, runs INTEGER DEFAULT 0, balls INTEGER DEFAULT 0,"
        "outs INTEGER DEFAULT 0, wickets INTEGER DEFAULT 0, conceded INTEGER DEFAULT 0, bowled INTEGER DEFAULT 0,"
        "best_bat TEXT DEFAULT '', best_bowl TEXT DEFAULT '', injured_until REAL DEFAULT 0,"
        "boost INTEGER DEFAULT 0, flags TEXT DEFAULT '[]', created REAL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS career_drinks ("
        "user_id INTEGER NOT NULL, drink TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0,"
        "PRIMARY KEY (user_id, drink))")
    conn.execute("CREATE TABLE IF NOT EXISTS career_drink_day (user_id INTEGER PRIMARY KEY, day TEXT, used INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS career_xp_day (user_id INTEGER PRIMARY KEY, day TEXT, xp INTEGER)")
    try:
        conn.execute("ALTER TABLE career_players ADD COLUMN streak INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.execute("CREATE TABLE IF NOT EXISTS career_opp_day ("
                 "user_id INTEGER, opp_id INTEGER, day TEXT, n INTEGER, PRIMARY KEY (user_id, opp_id, day))")


def init():
    with economy._tx() as conn:
        _ensure(conn)


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


# ── reading ──────────────────────────────────────────────────────────────────
def _row(conn, uid):
    r = conn.execute("SELECT * FROM career_players WHERE user_id = ?", (uid,)).fetchone()
    return dict(r) if r else None


def regen_rate(fit: int) -> float:
    return REGEN_PER_HOUR * (1 + max(0, fit - 55) / 100)


def stamina_now(p: dict, now: float | None = None) -> float:
    now = now or time.time()
    s = float(p["stamina"]) + regen_rate(p["fit"]) * max(0.0, now - float(p["stamina_ts"] or now)) / 3600
    return min(MAX_STAMINA, s)


def ovr(p: dict) -> int:
    if p["role"] == "bat":
        v = p["bat"] * 0.8 + p["fit"] * 0.2
    elif p["role"] == "bowl":
        v = p["bowl"] * 0.8 + p["fit"] * 0.2
    else:
        v = p["bat"] * 0.4 + p["bowl"] * 0.4 + p["fit"] * 0.2 + 4     # all-rounder bonus
    return round(v)


def stage_for(o: int) -> int:
    s = 0
    for i, (_, need, _) in enumerate(STAGES):
        if o >= need:
            s = i
    return s


def xp_need(stat: int) -> int:
    return 100 + max(0, stat - 50) * 18


def _xp_today(conn, uid) -> int:
    r = conn.execute("SELECT day, xp FROM career_xp_day WHERE user_id = ?", (uid,)).fetchone()
    return r["xp"] if r and r["day"] == _today() else 0


def get(uid: int) -> dict | None:
    with economy._tx() as conn:
        _ensure(conn)
        p = _row(conn, uid)
        if p:
            p["drinks"] = {r["drink"]: r["qty"] for r in conn.execute(
                "SELECT drink, qty FROM career_drinks WHERE user_id = ?", (uid,))}
            d = conn.execute("SELECT day, used FROM career_drink_day WHERE user_id = ?", (uid,)).fetchone()
            p["drinks_today"] = d["used"] if d and d["day"] == _today() else 0
            p["xp_today"] = _xp_today(conn, uid)
        return p


def top(limit: int = 10) -> list[dict]:
    with economy._tx() as conn:
        _ensure(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM career_players")]
    rows.sort(key=lambda p: (-ovr(p), -(p["bat"] + p["bowl"] + p["fit"]), -p["wins"], p["created"] or 0))
    return rows[:limit]


def rank_of(uid: int) -> int | None:
    with economy._tx() as conn:
        _ensure(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM career_players")]
    rows.sort(key=lambda p: (-ovr(p), -(p["bat"] + p["bowl"] + p["fit"]), -p["wins"], p["created"] or 0))
    for i, p in enumerate(rows, 1):
        if p["user_id"] == uid:
            return i
    return None


# ── writing ──────────────────────────────────────────────────────────────────
def _set_stamina(conn, uid, value, now=None):
    conn.execute("UPDATE career_players SET stamina = ?, stamina_ts = ? WHERE user_id = ?",
                 (max(0.0, min(MAX_STAMINA, value)), now or time.time(), uid))


def create(uid: int, name: str, role: str) -> str | None:
    if role not in ROLES:
        return "Pick a role: bat, bowl or ar."
    b, bw, f = START_STATS[role]
    now = time.time()
    with economy._tx() as conn:
        _ensure(conn)
        if _row(conn, uid):
            return "You already have a career player. Type `rccareer` to see him."
        conn.execute("INSERT INTO career_players (user_id, name, role, bat, bowl, fit, stamina, stamina_ts, created)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (uid, name, role, b, bw, f, MAX_STAMINA, now, now))
        for d, q in START_DRINKS.items():
            conn.execute("INSERT OR REPLACE INTO career_drinks VALUES (?, ?, ?)", (uid, d, q))
    return None


def add_drink(uid: int, drink: str, qty: int) -> None:
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute("INSERT INTO career_drinks VALUES (?, ?, ?) ON CONFLICT(user_id, drink) "
                     "DO UPDATE SET qty = qty + excluded.qty", (uid, drink, qty))


def buy_drink(uid: int, drink: str, qty: int) -> str | None:
    d = DRINKS.get(drink)
    if not d or not d["price"]:
        return "That drink can't be bought. Water and Energy Drink are in the shop."
    cost = d["price"] * qty
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute("SELECT purse FROM users WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            return "You haven't debuted yet. Type `rcdebut` first."
        if row["purse"] < cost:
            return f"Not enough coins. {qty} x {d['label']} costs {economy.fmt_coins(cost)}."
        economy._apply_coins(conn, uid, -cost, f"career drink {drink} x{qty}")
        conn.execute("INSERT INTO career_drinks VALUES (?, ?, ?) ON CONFLICT(user_id, drink) "
                     "DO UPDATE SET qty = qty + excluded.qty", (uid, drink, qty))
    return None


def use_drink(uid: int, drink: str) -> tuple[str | None, float]:
    d = DRINKS.get(drink)
    if not d:
        return "Unknown drink. Use water, energy or power.", 0
    with economy._tx() as conn:
        _ensure(conn)
        p = _row(conn, uid)
        if not p:
            return "Make your career player first: `rccareer start <name>`.", 0
        q = conn.execute("SELECT qty FROM career_drinks WHERE user_id = ? AND drink = ?", (uid, drink)).fetchone()
        if not q or q["qty"] <= 0:
            return f"You have no {d['label']}. Buy one with `rcdrink buy {drink}`.", 0
        day = conn.execute("SELECT day, used FROM career_drink_day WHERE user_id = ?", (uid,)).fetchone()
        used = day["used"] if day and day["day"] == _today() else 0
        if used >= DRINKS_PER_DAY:
            return f"You already had {DRINKS_PER_DAY} drinks today. Come back tomorrow.", 0
        s = stamina_now(p)
        if s >= MAX_STAMINA - 0.5:
            return "Your stamina is already full.", s
        new = min(MAX_STAMINA, s + d["stamina"])
        _set_stamina(conn, uid, new)
        conn.execute("UPDATE career_drinks SET qty = qty - 1 WHERE user_id = ? AND drink = ?", (uid, drink))
        conn.execute("INSERT OR REPLACE INTO career_drink_day VALUES (?, ?, ?)", (uid, _today(), used + 1))
    return None, new


def _add_xp(p: dict, stat: str, xp: int) -> int:
    """Adds xp to p[stat] in place, returns stat points gained."""
    v, x, gained = p[stat], p["xp_" + stat] + max(0, xp), 0
    while v < STAT_CAP and x >= xp_need(v):
        x -= xp_need(v)
        v += 1
        gained += 1
    p[stat], p["xp_" + stat] = v, x
    return gained


def award_match(uid: int, won: bool, opp_id: int | None, ranked: bool) -> dict | None:
    """One counted real match for this user. None = no career player."""
    now = time.time()
    with economy._tx() as conn:
        _ensure(conn)
        p = _row(conn, uid)
        if not p:
            return None
        day = _today()
        if not ranked and opp_id is not None:
            r = conn.execute("SELECT n FROM career_opp_day WHERE user_id = ? AND opp_id = ? AND day = ?",
                             (uid, opp_id, day)).fetchone()
            if r and r["n"] >= PER_OPP_CAP:
                return {"skipped": "opp_cap"}
            conn.execute("INSERT INTO career_opp_day VALUES (?, ?, ?, 1) ON CONFLICT(user_id, opp_id, day) "
                         "DO UPDATE SET n = n + 1", (uid, opp_id, day))
        s = stamina_now(p, now)
        if s < MATCH_COST:
            return {"skipped": "tired", "stamina": s}
        today_xp = _xp_today(conn, uid)
        if today_xp >= DAILY_XP_CAP:
            return {"skipped": "cap"}
        streak = ((p.get("streak") or 0) + 1) if won else 0
        bonus = []
        raw = XP_PLAY + (XP_WIN if won else 0)
        if today_xp == 0:
            raw += XP_FIRST_OF_DAY
            bonus.append(f"first match today +{XP_FIRST_OF_DAY}")
        if streak >= STREAK_FROM:
            raw += XP_STREAK
            bonus.append(f"{streak} wins in a row +{XP_STREAK}")
        base = min(raw, DAILY_XP_CAP - today_xp)
        xp = round(base * XP_MULT[p["role"]])
        old_ovr, old_stage = ovr(p), p["stage"]
        ups = {}
        split = SPLIT[p["role"]]
        given = 0
        items = list(split.items())
        for i, (stat, share) in enumerate(items):
            part = xp - given if i == len(items) - 1 else round(xp * share)
            given += part
            g = _add_xp(p, stat, part)
            if g:
                ups[stat] = g
        new_ovr = ovr(p)
        stage = max(old_stage, stage_for(new_ovr))
        coins = sum(STAGES[i][2] for i in range(old_stage + 1, stage + 1))
        conn.execute("UPDATE career_players SET bat=?, bowl=?, fit=?, xp_bat=?, xp_bowl=?, xp_fit=?, stage=?,"
                     " matches = matches + 1, wins = wins + ?, streak = ? WHERE user_id = ?",
                     (p["bat"], p["bowl"], p["fit"], p["xp_bat"], p["xp_bowl"], p["xp_fit"], stage,
                      1 if won else 0, streak, uid))
        _set_stamina(conn, uid, s - MATCH_COST, now)
        conn.execute("INSERT OR REPLACE INTO career_xp_day VALUES (?, ?, ?)", (uid, day, today_xp + base))
        if coins:
            economy._apply_coins(conn, uid, coins, "career promotion")
        return {"xp": xp, "ups": ups, "ovr": new_ovr, "ovr_up": new_ovr - old_ovr, "stamina": s - MATCH_COST,
                "xp_today": today_xp + base, "stage_up": STAGES[stage][0] if stage > old_stage else None,
                "coins": coins, "name": p["name"], "bonus": bonus}


def on_match_end(game, uid_a, uid_b, runs_a, runs_b) -> dict:
    """Called once when a real match finishes (views._save_match_record). uid_a batted first.
    Returns {user_id: result} for users who have a career player."""
    ranked = bool(getattr(game, "ranked", False))
    if not ranked and (getattr(game, "is_bot_match", False) or getattr(game, "cpu_difficulty", None) is not None):
        return {}
    try:
        if int(getattr(game, "overs", 0) or 0) < MIN_OVERS:
            return {}
        a, b = int(uid_a), int(uid_b)
    except (TypeError, ValueError):
        return {}
    if a == b:
        return {}
    out = {}
    for me, opp, won in ((a, b, runs_a > runs_b), (b, a, runs_b > runs_a)):
        r = award_match(me, won, opp, ranked)
        if r is not None:
            out[me] = r
    return out
