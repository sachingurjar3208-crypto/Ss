"""Memorable match moments: milestone banners, pressure lines, win-probability bar
and playstyle commentary.

Everything here is COSMETIC: it only reads the match state and builds text /
banner items. It never changes a score, so a bug in here can never break a match
(every public function is wrapped so it returns "nothing" on any error).

Used by views.py (live match) and practice_views.py (practice match):

    after_ball(...)             -> {"style", "pressure", "items"}      once per ball, after the score is updated
    pressure_line(game)         -> "12 needed off 6 balls 😰"        (last overs of a chase only)
    win_bar_field(game)         -> (name, value) for the scoreboard embed, or None
    chase_win_probability(game) -> 0..1 for the chasing team, or None
    drain_pending_items(game)   -> banner items queued by GameState (maiden overs)
    undo_wicket(game)           -> call when DRS overturns an OUT (hat-trick counter)
    send_moment_item(ch, item)  -> posts one "moment:..." banner item
"""
from __future__ import annotations

import bisect
import random
from urllib.parse import quote, unquote

try:  # the engine's own delivery / shot groups, so playstyle lines match the real effects
    from logic import (
        _GOOGLY_DELIVERIES, _PACE_DELIVERIES, _SHOTS_360, _SWING_DELIVERIES, _YORKER_DELIVERIES,
    )
except Exception:  # pragma: no cover
    _SWING_DELIVERIES = {"Swing", "In Swinger", "Out Swinger", "Reverse Swing"}
    _YORKER_DELIVERIES = {"Yorker", "Yorker Full Toss"}
    _PACE_DELIVERIES = {"Swing", "Good Length", "Fast", "Bouncer", "Full", "Yorker"}
    _GOOGLY_DELIVERIES = {"Googly", "Leg Break"}
    _SHOTS_360 = {"Sweep", "Reverse-Sweep", "Flick", "Lofted"}


# ─────────────────────────────────────────────────────────────────────────────
# Banner items  ("moment:<kind>|<name>|<detail>", url-quoted)
# ─────────────────────────────────────────────────────────────────────────────

def make_item(kind: str, name: str, detail: str = "") -> str:
    return "moment:" + "|".join(quote(str(p), safe="") for p in (kind, name, detail))


async def send_moment_item(channel, item: str) -> None:
    """Post one banner. Never raises (a banner problem must not touch the match)."""
    try:
        parts = [unquote(p) for p in item[len("moment:"):].split("|")]
        kind, name, detail = (parts + ["", ""])[:3]
        from moment_gif import moment_file
        f = await moment_file(kind, name, detail)
        if f is not None:
            await channel.send(file=f)
    except Exception as e:
        print(f"[moments] banner skipped ({type(e).__name__}: {e})")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def short_name(name: str) -> str:
    """'Virat Kohli' -> 'Kohli', 'AB de Villiers' -> 'de Villiers'."""
    parts = str(name or "").split()
    if len(parts) <= 1:
        return str(name or "")
    if len(parts) >= 3 and parts[-2].lower() in {"de", "van", "der", "du", "al", "ul", "bin", "le", "da", "di"}:
        return " ".join(parts[-2:])
    return parts[-1]


# ─────────────────────────────────────────────────────────────────────────────
# Milestones: 50 / 100 (+150 / 200), hat-trick, 5-wicket haul, maiden over
# ─────────────────────────────────────────────────────────────────────────────

_BAT_MILESTONE_KIND = {50: "fifty", 100: "century", 150: "150", 200: "200"}


def _batting_items(game, sname: str, runs_before: int) -> list[str]:
    st = game.batsman_stats.get(sname, {})
    runs_after = st.get("runs", 0)
    for m in (50, 100, 150, 200):
        if runs_before < m <= runs_after:
            fours = st.get("4s", st.get("fours", 0))
            sixes = st.get("6s", st.get("sixes", 0))
            detail = f"{runs_after} ({st.get('balls', 0)})"
            if fours or sixes:
                detail += f" · {fours}x4 · {sixes}x6"
            return [make_item(_BAT_MILESTONE_KIND[m], sname, detail)]
    return []


def _track_hattrick(game, bname: str, outcome: str) -> bool:
    """True exactly when this wicket is the bowler's 3rd in a row (3 of his own consecutive
    legal balls, across overs too). Wides / no-balls are not legal balls, so they don't break it."""
    streaks = game.__dict__.setdefault("hattrick_streak", {})
    key = f"{game.innings}:{bname}"
    prev = streaks.get(key, 0)
    game.__dict__["_ht_undo"] = (key, prev)
    if outcome == "W":
        streaks[key] = prev + 1
        return streaks[key] == 3
    if outcome in ("0", "1", "2", "3", "4", "6"):
        streaks[key] = 0
    return False


def undo_wicket(game) -> None:
    """DRS overturned the OUT: that ball is now a normal delivery, so the hat-trick run is broken."""
    try:
        key, _prev = game.__dict__.get("_ht_undo", (None, 0))
        if key:
            game.__dict__.setdefault("hattrick_streak", {})[key] = 0
    except Exception:
        pass


def _bowling_items(game, bname: str, outcome: str, wkts_before: int) -> list[str]:
    items: list[str] = []
    if _track_hattrick(game, bname, outcome):
        items.append(make_item("hattrick", bname, "3 wickets in 3 balls"))
    if outcome == "W":
        bs = game.bowler_stats.get(bname, {})
        wk = bs.get("wickets", 0)
        if wk >= 5 and wkts_before < 5:
            items.append(make_item("fifer", bname, f"{wk}/{bs.get('runs', 0)} ({game.bowler_overs_str(bname)})"))
    return items


def drain_pending_items(game) -> list[str]:
    """Banners GameState queued by itself (maiden overs). Returns them and clears the queue."""
    try:
        pend = game.__dict__.get("pending_moments") or []
        game.__dict__["pending_moments"] = []
        items = []
        for ev in pend:
            if ev[0] == "maiden":
                _, name, count, balls = ev
                detail = f"0 runs off {balls} balls"
                if count > 1:
                    detail += f" · {_ordinal(count)} maiden"
                items.append(make_item("maiden", name, detail))
        return items
    except Exception as e:  # pragma: no cover
        print(f"[moments] pending skipped ({type(e).__name__}: {e})")
        return []


def last_over_items(game) -> list[str]:
    """The innings ended on the 6th ball of an over, so end_over() never ran for it: count a
    maiden for that final over too, and return its banner."""
    try:
        if game.current_over_balls >= 6:
            game.register_maiden()
        return drain_pending_items(game)
    except Exception as e:  # pragma: no cover
        print(f"[moments] last over skipped ({type(e).__name__}: {e})")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Win probability (chase) — small Monte-Carlo, cached per match state
# ─────────────────────────────────────────────────────────────────────────────

# Per-ball odds copied from logic.BASE_WEIGHTS (legal-ball outcomes + wides).
_OUTCOMES = (0, 1, 2, 3, 4, 6, "W", "Wd")
_SIMS = 400
_BAR_LEN = 12


def _cum_table(s: float, mode: str) -> tuple[list[float], list]:
    w = {0: 35 / s, 1: 22 * s, 2: 10 * s, 3: 3 * s, 4: 11 * s, 6: 5 * s, "W": 8.0, "Wd": 4.0}
    if mode == "hard":          # needs a lot per ball: bigger hits, more risk (same idea as the engine's panic mode)
        w[6] *= 1.35; w[4] *= 1.15; w[0] *= 1.15; w["W"] *= 1.40
    elif mode == "easy":        # coasting: rotate strike, fewer risks
        w[1] *= 1.25; w[2] *= 1.15; w["W"] *= 0.75; w[6] *= 0.80
    cum, tot = [], 0.0
    for o in _OUTCOMES:
        tot += w[o]
        cum.append(tot)
    return cum, list(_OUTCOMES)


def _simulate(rng: random.Random, tables, s: float, balls: int, wkts: int, needed: int | None) -> int | bool:
    """One innings. With a target: returns True if reached. Without: returns runs scored."""
    runs = 0
    base_rpb = 1.33 * s
    neutral, hard, easy = tables
    while balls > 0 and wkts < 10:
        if needed is not None:
            if runs >= needed:
                return True
            req = (needed - runs) / balls
            cum = hard if req > base_rpb * 1.3 else (easy if req < base_rpb * 0.7 else neutral)
        else:
            cum = neutral
        o = _OUTCOMES[bisect.bisect(cum, rng.random() * cum[-1])]
        if o == "W":
            wkts += 1; balls -= 1
        elif o == "Wd":
            runs += 1
        else:
            runs += o; balls -= 1
    return (runs >= needed) if needed is not None else runs


def _tables(s: float):
    return _cum_table(s, "neutral")[0], _cum_table(s, "hard")[0], _cum_table(s, "easy")[0]


def _calibrated_scale(game) -> float:
    """Scoring scale `s` at which the chasing side starts this chase at ~50/50 (i.e. it is as good
    as the side that set the target). Clamped, so a hopeless / tiny target still shows as such.
    Computed once per match; same random numbers every step so the search is smooth."""
    first, total_balls = game.runs[0], game.overs * 6
    cached = game.__dict__.get("_wp_scale")
    if cached and cached[0] == (first, total_balls):
        return cached[1]
    lo, hi = 0.4, 2.8
    for _ in range(8):
        mid = (lo + hi) / 2
        t = _tables(mid)
        rng = random.Random(12345)
        wins = sum(1 for _ in range(300) if _simulate(rng, t, mid, total_balls, 0, first + 1))
        if wins / 300 < 0.5:
            lo = mid
        else:
            hi = mid
    s = min(1.9, max(0.75, (lo + hi) / 2))
    game.__dict__["_wp_scale"] = ((first, total_balls), s)
    return s


def chase_win_probability(game) -> float | None:
    """Chance (0..1) that the team batting second wins from the current state; None in innings 1."""
    try:
        if game.innings != 2:
            return None
        target = game.runs[0] + 1
        runs, wk = game.runs[1], game.wickets[1]
        needed = target - runs
        balls_left = game.overs * 6 - game.legal_balls[1]
        if needed <= 0:
            return 1.0
        if wk >= 10 or balls_left <= 0:
            return 0.0
        cache = game.__dict__.setdefault("_wp_cache", {})
        key = (game.runs[0], game.overs, runs, wk, balls_left)
        if key in cache:
            return cache[key]
        s = _calibrated_scale(game)
        t = _tables(s)
        rng = random.Random(hash(key) & 0xFFFFFFFF)
        wins = sum(1 for _ in range(_SIMS) if _simulate(rng, t, s, balls_left, wk, needed))
        p = (wins + 0.5) / (_SIMS + 1)
        p = min(0.99, max(0.01, p))
        cache[key] = p
        return p
    except Exception as e:  # pragma: no cover
        print(f"[moments] win prob skipped ({type(e).__name__}: {e})")
        return None


def win_bar_field(game) -> tuple[str, str] | None:
    """(field name, field value) for the scoreboard embed during a chase."""
    p = chase_win_probability(game)
    if p is None:
        return None
    pct = int(round(p * 100))
    pct = min(99, max(1, pct)) if 0 < p < 1 else pct
    g = int(round(p * _BAR_LEN))
    if 0 < p < 1:
        g = min(_BAR_LEN - 1, max(1, g))
    bar = "▰" * g + "▱" * (_BAR_LEN - g)   # plain characters, no emoji
    bat = game.get_batting_team().get("name", "Batting")
    bowl = game.get_bowling_team().get("name", "Bowling")
    return "Win probability", f"{bar}\n**{bat}** {pct}%  ·  **{bowl}** {100 - pct}%"


# ─────────────────────────────────────────────────────────────────────────────
# Pressure lines (last overs of a chase)
# ─────────────────────────────────────────────────────────────────────────────

def pressure_line(game) -> str:
    """'😰 12 needed off 6 balls' in the last 2-3 overs of a chase, else ''."""
    try:
        if game.innings != 2:
            return ""
        needed = game.runs[0] + 1 - game.runs[1]
        balls_left = game.overs * 6 - game.legal_balls[1]
        if needed <= 0 or balls_left <= 0 or game.wickets[1] >= 10:
            return ""
        window = 3 if game.overs >= 10 else (2 if game.overs >= 5 else 1)
        if balls_left > window * 6:
            return ""
        p = chase_win_probability(game)
        p = 0.5 if p is None else p
        emoji = "😱" if p < 0.15 else "😰" if p < 0.35 else "😬" if p < 0.55 else "😮" if p < 0.75 else "😎"
        if balls_left == 1:
            return f"{emoji} **Last ball! {needed} needed to win**"
        return f"{emoji} **{needed} needed off {balls_left} balls**"
    except Exception as e:  # pragma: no cover
        print(f"[moments] pressure line skipped ({type(e).__name__}: {e})")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Playstyle commentary — a line only when the playstyle actually "worked"
# ─────────────────────────────────────────────────────────────────────────────

# NOTE: no "LBW", "PLUMB", "IN FRONT", "CAUGHT" or "CATCH" in these lines — the dismissal
# type / DRS prompt are guessed from the commentary text.
_BAT_LINES = {
    "Chase Master": [
        "{n}'s chase mode is ON 🔥",
        "Chase Master {n} is in the zone! 🎯",
        "{n} was born for run chases! 🔥",
    ],
    "Aggressive": [
        "{n} attacks from ball one, pure aggression! 💥",
        "Aggressive {n} refuses to sit back! 💥",
    ],
    "Breakthrough": [
        "{n}, the game changer, is turning this chase around! 💣",
        "Breakthrough moment from {n}! 💣",
    ],
    "Cover Drive Specialist": [
        "{n}'s signature cover drive, pure class! 🏏",
        "Textbook cover drive from {n}! 🏏",
    ],
    "Mr360": [
        "{n} goes 360°, there is no safe place to bowl! 🌀",
        "Mr 360 {n} finds the gap again! 🌀",
    ],
}

_BOWL_LINES = {
    "Swing King": ["Swing King {n} makes it talk! 🌪️", "The ball swings late, {n} strikes! 🌪️"],
    "Swing Master": ["Swing Master {n} bends it again! 🌪️", "Prodigious swing from {n}! 🌪️"],
    "Yorker King": ["Yorker King {n} nails it! 🎯", "Toe-crusher from {n}, the Yorker King delivers! 🎯"],
    "Lethal Pace": ["Lethal pace from {n}, too hot to handle! ⚡", "{n} is flat-out express! ⚡"],
    "Googly Master": ["{n}'s googly does the trick! 🌀", "Googly Master {n} strikes! 🌀"],
    "Mystery": ["Mystery ball from {n}, nobody read that! 🕵️", "{n}'s mystery delivery strikes! 🕵️"],
    "Early Breaker": ["Early Breaker {n} strikes at the start! 🔨", "{n} breaks through early! 🔨"],
    "Spell Finisher": ["{n} finishes the spell in style! 🏁", "Spell Finisher {n} saves his best for last! 🏁"],
    "Mind Games": ["Mind games from {n}, the batter never saw it coming! 🧠", "{n} changes it up and gets his man! 🧠"],
}

_STYLE_GAP_BALLS = 6   # the same player + playstyle can't repeat its line within one over


def _style_ok(game, who: str, style: str, min_gap: int) -> bool:
    last = game.__dict__.setdefault("_style_last", {})
    idx = (game.innings, game.legal_balls[game.innings - 1])
    key = (who, style)
    prev = last.get(key)
    if prev and prev[0] == idx[0] and idx[1] - prev[1] < min_gap:
        return False
    last[key] = idx
    return True


def playstyle_line(
    game, *, outcome: str, shot_internal: str, delivery_internal: str, delivery_button: str,
    prev_delivery: str | None, striker_name: str, bowler_name: str,
    bat_attrs: list, bowl_attrs: list, over_index: int, bowler_balls_before: int,
) -> str:
    """One short English line when a playstyle's effect showed up on this ball, else ''."""
    try:
        chasing = game.innings == 2
        # ── batter playstyles: on a boundary ───────────────────────────────
        if outcome in ("4", "6"):
            fired = []
            if chasing and ("Chase Master" in bat_attrs or "Chaser" in bat_attrs):
                fired.append("Chase Master")
            if chasing and outcome == "6" and "Breakthrough" in bat_attrs:
                fired.append("Breakthrough")
            if "Cover Drive Specialist" in bat_attrs and shot_internal == "Drive" and outcome == "4":
                fired.append("Cover Drive Specialist")
            if "Mr360" in bat_attrs and shot_internal in _SHOTS_360:
                fired.append("Mr360")
            if "Aggressive" in bat_attrs:
                fired.append("Aggressive")
            for style in fired:   # strongest / most specific first (list order)
                if _style_ok(game, striker_name, style, _STYLE_GAP_BALLS):
                    return random.choice(_BAT_LINES[style]).format(n=f"**{short_name(striker_name)}**")
            return ""

        # ── bowler playstyles: on a wicket ─────────────────────────────────
        if outcome == "W":
            total_overs = game.overs
            fired = []
            if "Yorker King" in bowl_attrs and delivery_internal in _YORKER_DELIVERIES:
                fired.append("Yorker King")
            if "Swing King" in bowl_attrs and delivery_internal in _SWING_DELIVERIES:
                fired.append("Swing King")
            if "Swing Master" in bowl_attrs and delivery_internal in _SWING_DELIVERIES:
                fired.append("Swing Master")
            if "Googly Master" in bowl_attrs and delivery_internal in _GOOGLY_DELIVERIES:
                fired.append("Googly Master")
            if "Mystery" in bowl_attrs and delivery_button == "Mystery":
                fired.append("Mystery")
            if "Mind Games" in bowl_attrs and prev_delivery and delivery_internal != prev_delivery:
                fired.append("Mind Games")
            if "Early Breaker" in bowl_attrs and over_index < max(1, min(3, round(total_overs * 0.15))):
                fired.append("Early Breaker")
            if "Spell Finisher" in bowl_attrs:
                bmb = game.max_bowler_balls()
                if bmb > 0:
                    quota = max(1, bmb // 6)
                    final = 2 if quota >= 4 else 1
                    if bowler_balls_before // 6 >= quota - final:
                        fired.append("Spell Finisher")
            if "Lethal Pace" in bowl_attrs and delivery_internal in _PACE_DELIVERIES:
                fired.append("Lethal Pace")
            for style in fired:
                if _style_ok(game, bowler_name, style, 0):
                    return random.choice(_BOWL_LINES[style]).format(n=f"**{short_name(bowler_name)}**")
    except Exception as e:  # pragma: no cover
        print(f"[moments] playstyle line skipped ({type(e).__name__}: {e})")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# One call per ball
# ─────────────────────────────────────────────────────────────────────────────

def after_ball(
    game, *, outcome: str, shot_internal: str, delivery_internal: str, delivery_button: str,
    prev_delivery: str | None, striker_name: str, bowler_name: str,
    bat_attrs: list, bowl_attrs: list, striker_runs_before: int, bowler_wkts_before: int,
    over_index: int, bowler_balls_before: int,
) -> dict:
    """Call right after the ball has been applied to the game. Returns
    {"style": playstyle line or "", "pressure": chase line or "", "items": [banner items to post as GIFs]}."""
    out = {"style": "", "pressure": "", "items": []}
    try:
        out["style"] = playstyle_line(
            game, outcome=outcome, shot_internal=shot_internal, delivery_internal=delivery_internal,
            delivery_button=delivery_button, prev_delivery=prev_delivery, striker_name=striker_name,
            bowler_name=bowler_name, bat_attrs=bat_attrs, bowl_attrs=bowl_attrs,
            over_index=over_index, bowler_balls_before=bowler_balls_before,
        )
        if outcome in ("1", "2", "3", "4", "6"):
            out["items"] += _batting_items(game, striker_name, striker_runs_before)
        out["items"] += _bowling_items(game, bowler_name, outcome, bowler_wkts_before)
        out["pressure"] = pressure_line(game)
    except Exception as e:  # pragma: no cover
        print(f"[moments] after_ball skipped ({type(e).__name__}: {e})")
    return out
