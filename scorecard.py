"""Full scorecards for a match: records what the summary does not keep, builds the card data,
sends the two cards (scorecard_image.py draws them).

What is recorded during the match (per innings, on the GameState):
    sc_how   {batter name: "c Kohli b Jacks" / "lbw b Hazlewood" / "b Jacks" / "st Dhoni b Zampa"}
    sc_fow   [{"score", "over", "name"}]         fall of wickets
    sc_dots  {bowler name: dot balls}            legal balls with no run (wickets count as dots)
    sc_order [batter names in the order they came in]
    sc_impact {player names who came in as Impact Player}
Innings 1 values are copied into game.inn1_snapshot when innings 2 starts.

SCORECARDS = False switches the cards off (recording keeps running, it is harmless).
"""
from __future__ import annotations

import asyncio
import io
import random

SCORECARDS = True


def _pname(p) -> str:
    if p is None:
        return ""
    if isinstance(p, dict):
        return str(p.get("name", ""))
    return str(getattr(p, "name", p))


def _short(name: str) -> str:
    """Surname for the dismissal text ('Virat Kohli' -> 'Kohli', 'AB de Villiers' -> 'de Villiers')."""
    parts = name.split()
    if len(parts) >= 3 and parts[-2].lower() in ("de", "van", "du", "ul", "al"):
        return " ".join(parts[-2:])
    return parts[-1] if parts else name


def _rec(game) -> dict:
    d = game.__dict__
    d.setdefault("sc_how", {})
    d.setdefault("sc_fow", [])
    d.setdefault("sc_dots", {})
    d.setdefault("sc_order", [])
    d.setdefault("sc_impact", set())
    return d


# ── recording (called from views.py / game.py) ──────────────────────────────
def note_batter(game, player) -> None:
    """A batter is at the crease (keeps the batting order)."""
    try:
        name = _pname(player)
        order = _rec(game)["sc_order"]
        if name and name not in order:
            order.append(name)
    except Exception as e:
        print(f"[scorecard] note_batter: {type(e).__name__}: {e}")


def _keeper(game) -> str:
    for p in game._team_players(game.get_bowling_team()):
        role = str(game._player_value(p, "role", "") or "").upper()
        if role in ("WK", "WICKETKEEPER", "WICKET KEEPER", "KEEPER") or "KEEP" in role:
            return _pname(p)
    return ""


def note_wicket(game, kind: str | None, batter_name: str, bowler) -> None:
    """Right after game.add_wicket(): how he got out and the fall of wicket."""
    try:
        r = _rec(game)
        bw = _pname(bowler)
        k = (kind or "").lower()
        if k == "caught":
            team = [_pname(p) for p in game._team_players(game.get_bowling_team())]
            fielder = random.choice(team) if team else ""
            if not fielder or fielder == bw or random.random() < 0.06:
                how = f"c & b {_short(bw)}"
            else:
                how = f"c {_short(fielder)} b {_short(bw)}"
        elif k == "lbw":
            how = f"lbw b {_short(bw)}"
        elif k == "stumped":
            wk = _keeper(game)
            how = f"st {_short(wk)} b {_short(bw)}" if wk else f"st b {_short(bw)}"
        else:
            how = f"b {_short(bw)}"
        r["sc_how"][batter_name] = how
        r["sc_fow"].append({"score": game.current_runs, "over": game.overs_str(), "name": batter_name})
    except Exception as e:
        print(f"[scorecard] note_wicket: {type(e).__name__}: {e}")


def undo_wicket(game, batter_name: str) -> None:
    """DRS overturned the decision."""
    try:
        r = _rec(game)
        r["sc_how"].pop(batter_name, None)
        if r["sc_fow"] and r["sc_fow"][-1].get("name") == batter_name:
            r["sc_fow"].pop()
    except Exception as e:
        print(f"[scorecard] undo_wicket: {type(e).__name__}: {e}")


def note_dot(game, bowler, delta: int = 1) -> None:
    try:
        name = _pname(bowler)
        dots = _rec(game)["sc_dots"]
        dots[name] = max(0, dots.get(name, 0) + delta)
    except Exception as e:
        print(f"[scorecard] note_dot: {type(e).__name__}: {e}")


def note_impact(game, player) -> None:
    try:
        _rec(game)["sc_impact"].add(_pname(player))
    except Exception:
        pass


def snapshot(game) -> dict:
    """Innings-1 records, saved before innings 2 resets them."""
    r = _rec(game)
    snap = {"sc_how": dict(r["sc_how"]), "sc_fow": list(r["sc_fow"]), "sc_dots": dict(r["sc_dots"]),
            "sc_order": list(r["sc_order"])}
    r["sc_how"], r["sc_fow"], r["sc_dots"], r["sc_order"] = {}, [], {}, []
    return snap


# ── card data ───────────────────────────────────────────────────────────────
def build_data(game, innings: int) -> dict | None:
    import match_summary as ms
    first, second = ms._team_uids(game)
    bat_uid, bowl_uid = (first, second) if innings == 1 else (second, first)
    bstats, wstats, wballs, wmaid, dismissed = ms._innings_stats(game, innings)
    if innings == 1 and getattr(game, "inn1_snapshot", None):
        rec = game.inn1_snapshot
        overs = game.inn1_snapshot.get("overs") or game.overs_str()
    else:
        rec = _rec(game)
        overs = game.overs_str()
    how_map, order = rec.get("sc_how", {}), list(rec.get("sc_order", []))
    impact = _rec(game)["sc_impact"]

    batters = {b["name"]: b for b in ms._batters(game, bat_uid, bstats, dismissed)}
    xi = [_pname(p) for p in ms._players(game, bat_uid)]
    names = [n for n in order if n in batters] + [n for n in xi if n in batters and n not in order]
    names += [n for n in xi if n not in names]            # yet to bat
    rows = []
    for n in names[:11]:
        b = batters.get(n)
        if b is None:
            rows.append({"name": n, "how": "yet to bat", "impact": n in impact})
            continue
        how = "not out" if b["not_out"] else how_map.get(n, "out")
        rows.append({**b, "how": how, "impact": n in impact})

    dots = rec.get("sc_dots", {})
    bowlers = [{**w, "dots": dots.get(w["name"], 0)} for w in ms._bowlers(game, bowl_uid, wstats, wballs, wmaid)]

    i = innings - 1
    runs, wkts = game.runs[i], game.wickets[i]
    extras = max(0, runs - sum(b.get("runs", 0) for b in batters.values()))
    cond = getattr(game, "conditions", None) or {}
    data = {
        "team": ms._team_name(game, bat_uid), "bowling_team": ms._team_name(game, bowl_uid),
        "innings": innings, "venue": cond.get("venue") or "",
        "match_id": getattr(game, "match_record_id", None) or "",
        "runs": runs, "wickets": wkts, "overs": overs, "max_overs": game.overs, "extras": extras,
        "batters": rows, "bowlers": bowlers, "fow": list(rec.get("sc_fow", [])),
    }
    if innings == 1:
        balls = game.overs * 6
        rrr = (runs + 1) * 6 / balls if balls else 0
        data["target_line"] = f"{data['bowling_team']} need {runs + 1} off {balls} balls  ·  RRR {rrr:.2f}"
    else:
        try:
            data["target_line"] = ms.re.sub(r"\s*\([^)]*\)", "", game.match_result()).replace("*", "").rstrip("!").strip()
        except Exception:
            pass
    return data


async def send_cards(channel, game, innings: int) -> None:
    """Post the batting + bowling card of one innings. Never breaks the match."""
    if not SCORECARDS:
        return
    try:
        import discord
        import scorecard_image as si
        data = await asyncio.to_thread(build_data, game, innings)
        if not data:
            return
        bat = await asyncio.to_thread(si.batting_card, data)
        bowl = await asyncio.to_thread(si.bowling_card, data)
        await channel.send(files=[discord.File(io.BytesIO(bat), filename=f"scorecard_{innings}_batting.png"),
                                  discord.File(io.BytesIO(bowl), filename=f"scorecard_{innings}_bowling.png")])
    except Exception as e:
        print(f"[scorecard] cards skipped: {type(e).__name__}: {e}")
