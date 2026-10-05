import os
import discord
from game import GameState, _pname

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "cricstar_logo.png")


def logo_file() -> discord.File:
    """Return a fresh discord.File for the CricStar logo attachment."""
    return discord.File(_LOGO_PATH, filename="cricstar_logo.png")


def _ground_line(conditions: dict) -> str:
    from data import STADIUM_TYPES, GROUND_EFFECT_PCT
    ptype = conditions.get("pitch_type", "medium_pace")
    return f"{STADIUM_TYPES.get(ptype, 'Medium Pace')} friendly (±{GROUND_EFFECT_PCT}% effect)"


def build_match_invite_embed(
    challenger: discord.Member,
    opponent: discord.Member,
    overs: int,
    conditions: dict,
    gif_url: str | None = None,
) -> discord.Embed:
    sep = "──────────────────────"
    embed = discord.Embed(
        title="CRICKET CHALLENGE",
        color=discord.Color.from_rgb(8, 22, 60),
    )
    embed.description = (
        f"{sep}\n"
        f"**{challenger.mention}** has challenged **{opponent.mention}** to a **{overs}-over** match!\n"
        f"{sep}\n\n"
        f"**Venue:**      {conditions['venue']}, {conditions['location']}\n"
        f"**Pitch:**      {conditions['pitch']}\n"
        f"**Ground:**     {_ground_line(conditions)}\n"
        + (f"**Soil:**       {conditions['soil']}\n" if conditions.get("soil") else "")
        +
        f"**Weather:**    {conditions['weather']}  •  {conditions['temperature']}°C\n"
        f"**Umpire:**     {conditions['umpire']}\n"
        f"**Crowd:**      {conditions['crowd']}\n"
        f"{sep}"
    )
    embed.set_footer(text=f"{opponent.display_name}, accept or decline below.")
    if gif_url:
        embed.set_image(url=gif_url)
    return embed


def _role_bonus(chem: int, role: str) -> int:
    extras = {"Batter": 0, "WK": 1, "All-Rounder": 0, "Bowler": -1}
    return max(1, (chem // 20) + extras.get(role, 0))


def build_playing_xi_embed(team: dict) -> discord.Embed:
    chem = team.get("chem", 80)
    ovr  = team.get("ovr", 80)
    embed = discord.Embed(
        title=team.get("name", "Team"),
        description=f"OVR:**{ovr}** • CHEM:**{chem}**",
        color=discord.Color.dark_blue(),
    )

    header = "`Player           | OVR | BAT | BOWL`"
    embed.add_field(name="\u200b", value=header, inline=False)

    _ROLE_TO_BUCKET = {
        "Batsman":       "Batter",
        "Wicket Keeper": "WK",
        "All Rounder":   "All-Rounder",
        "Fast Bowler":   "Bowler",
        "Off Spinner":   "Bowler",
        "Leg Spinner":   "Bowler",
    }
    role_order = [
        ("Batter",      "Batters"),
        ("WK",          "WK"),
        ("All-Rounder", "All-Rounders"),
        ("Bowler",      "Bowlers"),
    ]

    for role_key, role_label in role_order:
        players = [
            p for p in team.get("players", [])
            if _ROLE_TO_BUCKET.get(p.get("role", ""), "Batter") == role_key
        ]
        if not players:
            continue
        bonus = _role_bonus(chem, role_key)
        label = f"**{role_label}  +{bonus}**"
        rows = []
        for p in players:
            pn       = _pname(p)
            bowl_val = str(p.get("bowl", "—")) if p.get("bowling_type") else " —"
            name_pad = pn[:16].ljust(16)
            ovr_val  = p.get("ovr", "?")
            bat_val  = p.get("bat", "?")
            rows.append(
                f"`{name_pad}` | {ovr_val} | {bat_val} | {bowl_val:>3}"
            )
        embed.add_field(name=label, value="\n".join(rows), inline=False)

    embed.set_footer(text=f"{team.get('name', 'Team')} • Playing XI")
    return embed


def _pad(text: str, width: int) -> str:
    text = str(text)
    return text[:width] if len(text) > width else text.ljust(width)


def _rpad(text: str, width: int) -> str:
    text = str(text)
    return text[:width] if len(text) > width else text.rjust(width)


def _table(headers: list[str], widths: list[int], rows: list[list[str]]) -> str:
    """A fixed-width table inside a code block, so it lines up on mobile."""
    head = _pad(headers[0], widths[0]) + " " + " ".join(_rpad(h, w) for h, w in zip(headers[1:], widths[1:]))
    body = [
        _pad(row[0], widths[0]) + " " + " ".join(_rpad(c, w) for c, w in zip(row[1:], widths[1:]))
        for row in rows
    ]
    return "```\n" + "\n".join([head] + body) + "\n```"


# Colour-coded ball tokens for the Timeline field — every outcome gets its
# own colour (not just boundaries/wickets), so the shape of an over is
# readable at a glance: white = dot, dark = ones/twos/threes, green = four,
# blue = six, red = wicket, yellow = wide/no-ball.
_BALL_EMOJI = {
    "0":    "`0`",
    "1":    "`1`",
    "2":    "`2`",
    "3":    "`3`",
    "4":    "`4`",
    "6":    "`6`",
    "W":    "`W`",
    "Wd":   "`wd`",
    "NB":   "`nb`",
    "NB+1": "`nb+1`",
}


def _ball_token(outcome: str) -> str:
    return _BALL_EMOJI.get(outcome, outcome)


def _timeline_line(game: GameState, max_balls: int = 12) -> str | None:
    """One single line showing the most recent over.

    The timeline stores a "|" marker when an over finishes, so splitting on
    it gives exact over boundaries (wides / no-balls don't break the grouping).
    While an over is in progress its balls are shown; once it completes they
    stay on screen until the next ball is bowled. The over number is prefixed
    so it is clear where the over started.
    """
    overs: list[list[str]] = []
    current: list[str] = []
    for item in game.timeline:
        if item == "|":
            overs.append(current)
            current = []
        else:
            current.append(item)

    if current:
        over_no, balls = len(overs) + 1, current
    elif overs and overs[-1]:
        over_no, balls = len(overs), overs[-1]
    else:
        return None

    shown = balls[-max_balls:]
    tokens = " ".join(_ball_token(b) for b in shown)
    return f"`Ov {over_no}` ┃ {tokens}"


def build_scoreboard_embed(game: GameState) -> discord.Embed:
    """Live scoreboard shown after every ball, built from native Discord
    embed fields so the stat chips (P'SHIP / CRR / RRR …) render as their
    own little boxes side-by-side on both mobile and desktop, instead of
    one long block of plain text."""
    inn = game.innings
    r, w = game.current_runs, game.current_wickets

    batting_team = game.get_batting_team()
    bowling_team = game.get_bowling_team()
    bat_team_name = batting_team.get("name", "Batting")
    bowl_team_name = bowling_team.get("name", "Bowling")

    color = discord.Color.green() if inn == 1 else discord.Color.orange()
    overs_done = game.overs_str()
    overs_total = f"{game.overs}.0"

    # ── Header: score + chase context ───────────────────────────────────────
    title = f"{bat_team_name}  —  {r}/{w}  ({overs_done}/{overs_total})"
    if inn == 1:
        description = f"{bowl_team_name}  ·  *Yet to bat*"
    else:
        r1, w1 = game.runs[0], game.wickets[0]
        description = f"{bowl_team_name} 1st innings:  **{r1}/{w1}**"

    embed = discord.Embed(title=title, description=description, color=color)

    # ── Batters field ────────────────────────────────────────────────────────
    bat_rows = []
    for p, is_striker in ((game.striker, True), (game.non_striker, False)):
        if not p:
            continue
        pn = _pname(p)
        s = game.batsman_stats.get(pn, {"runs": 0, "balls": 0})
        mark = "*" if is_striker else ""  # striker marked with a *, like on-strike in real scorecards
        bat_rows.append([f"{pn[:14]}{mark}", str(s["runs"]), str(s["balls"]), game.sr(pn) or "0.0"])
    if bat_rows:
        embed.add_field(name="Batters", value=_table(["BATTER", "R", "B", "SR"], [15, 3, 3, 6], bat_rows), inline=False)

    # ── Stat chips: one inline line in code boxes, like the Glenn McGrath
    # scorecard:  `P'Ship: 5(2)`  `CRR: 15.0`  `Proj: 300` ─────────────────
    if inn == 2:
        chip_last = f"`RRR: {game.rrr()}`"
    else:
        chip_last = f"`Proj: {game.projected()}`"
    embed.add_field(
        name="\u200b",
        value=f"`P'Ship: {game.partnership_runs}({game.partnership_balls})`  `CRR: {game.crr()}`  {chip_last}",
        inline=False,
    )

    # ── Bowler field ─────────────────────────────────────────────────────────
    if game.current_bowler:
        bn = _pname(game.current_bowler)
        bs = game.bowler_stats.get(bn, {"balls": 0, "runs": 0, "wickets": 0})
        ov = game.bowler_overs_str(bn)
        embed.add_field(
            name="Bowler",
            value=_table(["BOWLER", "O", "R", "W"], [15, 4, 3, 3], [[bn[:14], ov, str(bs["runs"]), str(bs["wickets"])]]),
            inline=False,
        )

    # ── Timeline field ───────────────────────────────────────────────────────
    timeline = _timeline_line(game)
    if timeline:
        embed.add_field(name="Timeline", value=timeline, inline=False)

    # ── Chase status (innings 2 only) ───────────────────────────────────────
    if inn == 2:
        tgt = game.target()
        needed = max(0, (tgt - r)) if tgt else 0
        balls_left = max(0, game.overs * 6 - game.current_legal_balls)
        embed.add_field(
            name="\u200b",
            value=f"**{bat_team_name}** need **{needed}** runs to win off **{balls_left}** balls",
            inline=False,
        )

    if getattr(game, "toss_note", None):
        embed.set_footer(text=game.toss_note)
    return embed


def build_result_embed(game: GameState) -> discord.Embed:
    result = game.match_result()
    embed  = discord.Embed(
        title="MATCH RESULT",
        description=result,
        color=discord.Color.gold(),
    )

    t1 = game.runs[0]
    w1 = game.wickets[0]
    t2 = game.runs[1]
    w2 = game.wickets[1]

    bat1_team = None
    bat2_team = None
    for uid, team in game.teams.items():
        if uid == game.bowling_user_id:
            bat1_team = team.get("name", "Team 1")
        else:
            bat2_team = team.get("name", "Team 2")

    # ── Over-by-over summary for result embed ─────────────────────────────
    inn1_ovr = game.overs_str() if game.innings >= 1 else "—"

    embed.add_field(
        name="Scorecard",
        value=(
            f"**{bat1_team}:** {t1}/{w1}\n"
            f"**{bat2_team}:** {t2}/{w2}"
        ),
        inline=False,
    )
    embed.set_footer(text="Thanks for playing Cric Star!")
    return embed
