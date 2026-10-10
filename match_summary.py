"""Builds the end-of-match SUMMARY card from a finished GameState.

    png = build_summary_png(game)      # bytes, send as discord.File

Player of the Match (POTM) - computed for every player of BOTH teams from both innings:
  * Statistical performance : runs (bonus for strike-rate above 100, 50+/100+), wickets,
                              economy and maidens.
  * Match impact            : share of the team's total runs / wickets the player owned.
  * Winning contribution    : players of the winning team get a x1.25 multiplier, so a loser
                              only wins POTM with a clearly extraordinary performance.
"""
from __future__ import annotations

import re
import time

from summary_image import generate_summary_card


# ── helpers ──────────────────────────────────────────────────────────────────
def _pname(p) -> str:
    if isinstance(p, dict):
        return str(p.get("name", ""))
    return str(getattr(p, "name", p))


def _g(d: dict, *keys, default=0):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _overs_from_balls(b: int) -> str:
    return f"{b // 6}" if b % 6 == 0 else f"{b // 6}.{b % 6}"


def _team_uids(game):
    first = game.innings1_batting_user_id
    uids = list(game.teams.keys())
    second = next((u for u in uids if u != first), None)
    return first, second


def _team_name(game, uid) -> str:
    team = game.teams.get(uid, {})
    name = team.get("name") if isinstance(team, dict) else None
    if name:
        return str(name)
    for u in (game.challenger, game.opponent):
        if u is not None and getattr(u, "id", None) == uid:
            return u.display_name
    return "Team"


def _players(game, uid) -> list:
    return game._team_players(game.teams.get(uid, {}))


def _innings_stats(game, innings: int):
    """(batsman_stats, bowler_stats, bowler_balls, bowler_maidens, dismissed) for 1 or 2."""
    if innings == 1:
        snap = getattr(game, "inn1_snapshot", None)
        if snap:
            return (snap["batsman_stats"], snap["bowler_stats"], snap["bowler_ball_count"],
                    snap.get("maidens", {}), snap["dismissed"])
    return (game.batsman_stats, game.bowler_stats, game.bowler_ball_count,
            getattr(game, "bowler_maidens", {}), game.dismissed)


def _batters(game, uid, stats, dismissed) -> list[dict]:
    out = []
    for p in _players(game, uid):
        s = stats.get(game._player_key(p))
        if not s:
            continue
        name = _pname(p)
        out.append({
            "name": name,
            "runs": int(_g(s, "r", "runs")),
            "balls": int(_g(s, "b", "balls")),
            "fours": int(_g(s, "4s", "fours")),
            "sixes": int(_g(s, "6s", "sixes")),
            "not_out": name not in dismissed,
        })
    return out


def _bowlers(game, uid, bstats, bballs, bmaid) -> list[dict]:
    out = []
    for p in _players(game, uid):
        name = _pname(p)
        s = bstats.get(name)
        balls = int(bballs.get(name, _g(s or {}, "b", "balls")))
        if not s or balls <= 0:
            continue
        out.append({
            "name": name,
            "wickets": int(_g(s, "w", "wickets")),
            "runs": int(_g(s, "r", "runs")),
            "balls": balls,
            "maidens": int(bmaid.get(name, _g(s, "m", "maidens"))),
        })
    return out


# ── POTM ─────────────────────────────────────────────────────────────────────
def _bat_points(b: dict, team_total: int) -> float:
    runs, balls = b["runs"], b["balls"]
    if runs <= 0 and balls <= 0:
        return 0.0
    sr = runs * 100 / balls if balls else 100
    pts = runs * (1 + max(0.0, sr - 100) / 200)
    pts += 25 if runs >= 100 else (10 if runs >= 50 else 0)
    if team_total > 0:
        pts += 30 * runs / team_total
    return pts


def _bowl_points(w: dict, team_wkts_taken: int) -> float:
    overs = w["balls"] / 6
    econ = w["runs"] / overs if overs else 0
    pts = w["wickets"] * 25
    pts += max(-20.0, min(30.0, (8 - econ) * overs * 1.5))
    pts += w["maidens"] * 8
    if team_wkts_taken > 0:
        pts += 20 * w["wickets"] / team_wkts_taken
    return pts


def pick_potm(game, inn_data: dict) -> str | None:
    """inn_data: {team_uid: {"batters": [...], "bowlers": [...]}} (both innings merged)."""
    t1, t2 = game.runs[0], game.runs[1]
    first, second = _team_uids(game)
    winner = first if t1 > t2 else (second if t2 > t1 else None)
    totals = {first: t1, second: t2}
    scores: dict[str, float] = {}
    for uid, d in inn_data.items():
        wkts_taken = sum(w["wickets"] for w in d["bowlers"])
        per: dict[str, float] = {}
        for b in d["batters"]:
            per[b["name"]] = per.get(b["name"], 0) + _bat_points(b, totals.get(uid, 0))
        for w in d["bowlers"]:
            per[w["name"]] = per.get(w["name"], 0) + _bowl_points(w, wkts_taken)
        mult = 1.25 if (winner is not None and uid == winner) else 1.0
        for n, p in per.items():
            scores[f"{uid}|{n}"] = p * mult
    if not scores:
        return None
    best = max(scores, key=scores.get)
    return best.split("|", 1)[1] if scores[best] > 0 else None


# ── public ───────────────────────────────────────────────────────────────────
def build_summary_data(game) -> dict:
    first, second = _team_uids(game)
    i1 = _innings_stats(game, 1)   # first team batted, second team bowled
    i2 = _innings_stats(game, 2)   # second team batted, first team bowled

    data_inn = {
        first:  {"batters": _batters(game, first, i1[0], i1[4]),
                 "bowlers": _bowlers(game, first, i2[1], i2[2], i2[3])},
        second: {"batters": _batters(game, second, i2[0], i2[4]),
                 "bowlers": _bowlers(game, second, i1[1], i1[2], i1[3])},
    }
    snap = getattr(game, "inn1_snapshot", None)
    ovr1 = snap["overs"] if snap else "0.0"

    def team(uid, runs, wkts, overs):
        d = data_inn[uid]
        bats = sorted(d["batters"], key=lambda b: (-b["runs"], b["balls"]))[:4]
        bowls = sorted(d["bowlers"], key=lambda w: (-w["wickets"], w["runs"]))[:4]
        return {
            "name": _team_name(game, uid), "runs": runs, "wickets": wkts,
            "overs": overs, "max_overs": str(game.overs),
            "batters": [(b["name"], b["runs"], b["balls"], b["not_out"]) for b in bats],
            "bowlers": [(w["name"], w["wickets"], w["runs"], _overs_from_balls(w["balls"])) for w in bowls],
        }

    result = re.sub(r"\s*\([^)]*\)", "", game.match_result()).replace("*", "").rstrip("!").strip()
    cond = getattr(game, "conditions", None) or {}
    venue = ", ".join(x for x in (cond.get("venue"), cond.get("location")) if x)
    match_id = getattr(game, "match_record_id", None) or (int(time.time()) % 1000000)

    return {
        "match_id": match_id,
        "venue": venue,
        "team1": team(first, game.runs[0], game.wickets[0], ovr1),
        "team2": team(second, game.runs[1], game.wickets[1], game.overs_str()),
        "result": result,
        "potm": pick_potm(game, data_inn),
    }


def build_summary_png(game) -> bytes:
    return generate_summary_card(build_summary_data(game))
