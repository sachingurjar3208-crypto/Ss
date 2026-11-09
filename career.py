"""Career Mode (data + rules). One own cricketer per player who grows from Club cricket upwards.

Stamina 0-100 refills on its own (about 10 hours from 0 to full, faster with a high FITNESS).
A career match costs 10 stamina to start plus a little per ball (aggressive shots and hard deliveries
cost more). Training costs 15. Drinks give stamina back (max 3 drinks a day).
Low stamina (< 30) = -10% performance and a 10% injury chance.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import economy

IST = timezone(timedelta(hours=5, minutes=30))

ROLES = {"bat": "Batter", "bowl": "Bowler", "ar": "All-rounder"}
START_STATS = {"bat": (62, 35, 55), "bowl": (35, 62, 55), "ar": (55, 55, 55)}   # bat, bowl, fit
STAT_CAP = 99

MAX_STAMINA = 100.0
REGEN_PER_HOUR = 10.0                # at FITNESS 55; +1% speed per FITNESS point above 55
MATCH_START_COST = 10
MATCH_MIN_STAMINA = 25
TRAIN_COST = 15
LOW_STAMINA = 30
INJURY_CHANCE = 0.10
INJURY_HOURS = 24
DRINKS_PER_DAY = 3

DRINKS = {
    "water":  {"label": "Water",        "stamina": 20,  "price": 100_000, "boost": 0},
    "energy": {"label": "Energy Drink", "stamina": 50,  "price": 500_000, "boost": 0},
    "power":  {"label": "Power Shot",   "stamina": 100, "price": 0,     "boost": 5},   # events / codes only
}
START_DRINKS = {"water": 3, "energy": 1}

# stage: name, balls you bat, balls you bowl, opponent rating range, needs to move up (ovr, matches)
STAGES = [
    {"name": "Club Cricket", "bat_balls": 18, "bowl_balls": 12, "opp": (52, 64), "up": (65, 5), "reward": 5_000},
    {"name": "State Team",   "bat_balls": 24, "bowl_balls": 18, "opp": (62, 74), "up": (72, 10), "reward": 10_000},
    {"name": "T20 League",   "bat_balls": 30, "bowl_balls": 24, "opp": (70, 82), "up": None, "reward": 0},
]
LAST_OPEN_STAGE = 1                  # League not open yet in this version


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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS career_drink_day (user_id INTEGER PRIMARY KEY, day TEXT, used INTEGER)")


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


def form_avg(p: dict) -> float | None:
    f = json.loads(p.get("form") or "[]")
    return sum(f) / len(f) if f else None


def form_bonus(p: dict) -> int:
    a = form_avg(p)
    if a is None or len(json.loads(p["form"])) < 3:
        return 0
    return 3 if a >= 7.5 else (-3 if a < 4.5 else 0)


def xp_need(stat: int) -> int:
    return 40 + max(0, stat - 50) * 4


def get(uid: int) -> dict | None:
    with economy._tx() as conn:
        _ensure(conn)
        p = _row(conn, uid)
        if p:
            p["drinks"] = {r["drink"]: r["qty"] for r in conn.execute(
                "SELECT drink, qty FROM career_drinks WHERE user_id = ?", (uid,))}
            d = conn.execute("SELECT day, used FROM career_drink_day WHERE user_id = ?", (uid,)).fetchone()
            p["drinks_today"] = d["used"] if d and d["day"] == _today() else 0
        return p


# ── writing ──────────────────────────────────────────────────────────────────
def _set_stamina(conn, uid, p, value, now=None):
    now = now or time.time()
    conn.execute("UPDATE career_players SET stamina = ?, stamina_ts = ? WHERE user_id = ?",
                 (max(0.0, min(MAX_STAMINA, value)), now, uid))


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
        if s >= MAX_STAMINA - 0.5 and not d["boost"]:
            return "Your stamina is already full.", s
        new = min(MAX_STAMINA, s + d["stamina"])
        _set_stamina(conn, uid, p, new)
        conn.execute("UPDATE career_drinks SET qty = qty - 1 WHERE user_id = ? AND drink = ?", (uid, drink))
        conn.execute("INSERT OR REPLACE INTO career_drink_day VALUES (?, ?, ?)", (uid, _today(), used + 1))
        if d["boost"]:
            conn.execute("UPDATE career_players SET boost = ? WHERE user_id = ?", (d["boost"], uid))
    return None, new


def can_act(p: dict, cost: float) -> str | None:
    now = time.time()
    if p["injured_until"] and p["injured_until"] > now:
        h = (p["injured_until"] - now) / 3600
        return f"Your player is injured. Back in about {h:.0f}h {int((h % 1) * 60)}m."
    s = stamina_now(p, now)
    if s < cost:
        need = (cost - s) / regen_rate(p["fit"]) * 60
        return (f"Not enough stamina ({s:.0f}/100, need {cost:.0f}). It refills on its own in about "
                f"{need:.0f} min, or drink something: `rcdrink`.")
    return None


def _level_up(p: dict, stat: str, xp_gain: int) -> tuple[int, int, int]:
    """Returns (new stat, new xp, points gained)."""
    v, xp, gained = p[stat], p["xp_" + stat] + max(0, xp_gain), 0
    while v < STAT_CAP and xp >= xp_need(v):
        xp -= xp_need(v)
        v += 1
        gained += 1
    return v, xp, gained


def train(uid: int, stat: str, perfect: bool) -> tuple[str | None, dict]:
    with economy._tx() as conn:
        _ensure(conn)
        p = _row(conn, uid)
        if not p:
            return "Make your career player first: `rccareer start <name>`.", {}
        err = can_act(p, TRAIN_COST)
        if err:
            return err, {}
        xp = 70 if perfect else 35
        v, nxp, gained = _level_up(p, stat, xp)
        _set_stamina(conn, uid, p, stamina_now(p) - TRAIN_COST)
        conn.execute(f"UPDATE career_players SET {stat} = ?, xp_{stat} = ? WHERE user_id = ?", (v, nxp, uid))
        return None, {"xp": xp, "gained": gained, "value": v, "xp_now": nxp, "need": xp_need(v)}


def start_match(uid: int) -> tuple[str | None, dict]:
    """Takes the start cost. Returns the player + whether he started tired."""
    with economy._tx() as conn:
        _ensure(conn)
        p = _row(conn, uid)
        if not p:
            return "Make your career player first: `rccareer start <name>`.", {}
        err = can_act(p, MATCH_MIN_STAMINA)
        if err:
            return err, {}
        s = stamina_now(p)
        _set_stamina(conn, uid, p, s - MATCH_START_COST)
        conn.execute("UPDATE career_players SET boost = 0 WHERE user_id = ?", (uid,))
        p["stamina_start"] = s
        p["stamina"] = s - MATCH_START_COST
        return None, p


def rating(bat: dict | None, bowl: dict | None) -> float:
    pts = 0.0
    if bat and bat["balls"]:
        sr = bat["runs"] * 100 / bat["balls"]
        pts += bat["runs"] * 0.09 + (sr - 120) / 60 - (0.8 if bat["out"] and bat["runs"] < 10 else 0)
    if bowl and bowl["balls"]:
        econ = bowl["runs"] * 6 / bowl["balls"]
        pts += bowl["wkts"] * 1.3 + (8.0 - econ) * 0.3
    return round(max(1.0, min(10.0, 4.5 + pts)), 1)


def finish_match(uid: int, res: dict) -> dict:
    """res: bat/bowl dicts, stamina_left, won, tired_start. Saves everything, pays out. Returns summary."""
    import random
    out = {"coins": 0, "ups": {}, "stage_up": None, "injured": False, "water": False, "milestones": []}
    with economy._tx() as conn:
        _ensure(conn)
        p = _row(conn, uid)
        bat, bowl, won = res.get("bat"), res.get("bowl"), res["won"]
        r = res["rating"]
        form = (json.loads(p["form"] or "[]") + [r])[-5:]
        flags = set(json.loads(p["flags"] or "[]"))
        upd = {"form": json.dumps(form), "matches": p["matches"] + 1, "wins": p["wins"] + (1 if won else 0),
               "stage_matches": p["stage_matches"] + 1}
        coins = 300 + (700 if won else 0)
        xp = {"bat": 0, "bowl": 0, "fit": 8}
        if bat and bat["balls"]:
            upd["runs"] = p["runs"] + bat["runs"]
            upd["balls"] = p["balls"] + bat["balls"]
            upd["outs"] = p["outs"] + (1 if bat["out"] else 0)
            coins += bat["runs"] * 15
            xp["bat"] = bat["runs"] + bat["balls"] // 2
            best = int((p["best_bat"] or "0").rstrip("*") or 0)
            if bat["runs"] > best:
                upd["best_bat"] = f"{bat['runs']}{'' if bat['out'] else '*'}"
            if bat["runs"] >= 50 and "first50" not in flags:
                flags.add("first50"); coins += 3_000; out["milestones"].append("First fifty! +3,000 coins")
        if bowl and bowl["balls"]:
            upd["wickets"] = p["wickets"] + bowl["wkts"]
            upd["conceded"] = p["conceded"] + bowl["runs"]
            upd["bowled"] = p["bowled"] + bowl["balls"]
            coins += bowl["wkts"] * 250
            xp["bowl"] = bowl["wkts"] * 15 + bowl["balls"] // 2
            bw, br = bowl["wkts"], bowl["runs"]
            if p["best_bowl"]:
                ow, orr = map(int, p["best_bowl"].split("/"))
            else:
                ow, orr = -1, 999
            if (bw, -br) > (ow, -orr):
                upd["best_bowl"] = f"{bw}/{br}"
            if bw >= 3 and "first3fer" not in flags:
                flags.add("first3fer"); coins += 2_000; out["milestones"].append("First 3-wicket haul! +2,000 coins")
        upd["flags"] = json.dumps(sorted(flags))
        for stat in ("bat", "bowl", "fit"):
            if xp[stat]:
                v, nxp, gained = _level_up({**p, **upd}, stat, xp[stat])
                upd[stat], upd["xp_" + stat] = v, nxp
                if gained:
                    out["ups"][stat] = gained
        if res.get("tired_start") and random.random() < INJURY_CHANCE:
            upd["injured_until"] = time.time() + INJURY_HOURS * 3600
            out["injured"] = True
        cols = ", ".join(f"{k} = ?" for k in upd)
        conn.execute(f"UPDATE career_players SET {cols} WHERE user_id = ?", (*upd.values(), uid))
        _set_stamina(conn, uid, p, res["stamina_left"])
        if won and random.random() < 0.30:
            conn.execute("INSERT INTO career_drinks VALUES (?, 'water', 1) ON CONFLICT(user_id, drink) "
                         "DO UPDATE SET qty = qty + 1", (uid,))
            out["water"] = True
        # stage up?
        p2 = _row(conn, uid)
        st = STAGES[p2["stage"]]
        if st["up"] and p2["stage"] < LAST_OPEN_STAGE and ovr(p2) >= st["up"][0] and p2["stage_matches"] >= st["up"][1]:
            conn.execute("UPDATE career_players SET stage = stage + 1, stage_matches = 0 WHERE user_id = ?", (uid,))
            coins += st["reward"]
            out["stage_up"] = STAGES[p2["stage"] + 1]["name"]
        economy._apply_coins(conn, uid, coins, "career match")
        out["coins"] = coins
    return out
