"""
Rush Cricket embeds — clean text style.

Design rules (no copied scoreboard look):
- No box-drawing separators (no long line or box-drawing characters).
- Light separators only: ·, •, —, |, ›
- Monospace chips/tables via Discord code blocks.
- Bold labels via **...**.
"""

import os
import discord
from game import GameState, _pname

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "cricstar_logo.png")


def logo_file() -> discord.File:
    """Return a fresh discord.File for the Rush Cricket logo attachment."""
    return discord.File(_LOGO_PATH, filename="cricstar_logo.png")


def _ground_line(conditions: dict) -> str:
    from data import STADIUM_TYPES, GROUND_EFFECT_PCT
    ptype = conditions.get("pitch_type", "medium_pace")
    return f"{STADIUM_TYPES.get(ptype, 'Medium Pace')} friendly (±{GROUND_EFFECT_PCT}% effect)"


def _conditions_block(conditions: dict) -> str:
    """Shared venue / pitch / weather block for invite-style embeds."""
    lines = [
        f"**Venue:**   {conditions['venue']}, {conditions['location']}",
        f"**Pitch:**   {conditions['pitch']}",
        f"**Ground:**  {_ground_line(conditions)}",
    ]
    if conditions.get("soil"):
        lines.append(f"**Soil:**    {conditions['soil']}")
    lines += [
        f"**Weather:** {conditions['weather']} • {conditions['temperature']}°C",
        f"**Umpire:**  {conditions['umpire']}",
        f"**Crowd:**   {conditions['crowd']}",
    ]
    return "\n".join(lines)


def build_match_invite_embed(
    challenger: discord.Member,
    opponent: discord.Member,
    overs: int,
    conditions: dict,
    gif_url: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="Match Challenge",
        color=discord.Color.from_rgb(8, 22, 60),
    )
    embed.description = (
        f"**{challenger.mention}** has challenged **{opponent.mention}**\n"
        f"**{overs} over** match\n\n"
        + _conditions_block(conditions)
    )
    embed.set_footer(text=f"{opponent.display_name}, accept or decline below.")
    if gif_url:
        embed.set_image(url=gif_url)
    return embed


def build_ranked_match_embed(
    challenger,
    opponent,
    overs: int,
    conditions: dict,
    gif_url: str | None = None,
) -> discord.Embed:
    """Ranked variant of the invite — no Accept / Decline, star stakes in footer."""
    embed = discord.Embed(
        title="Rank Up Mode",
        color=discord.Color.from_rgb(8, 22, 60),
    )
    embed.description = (
        f"**{challenger.mention}** vs **{opponent.mention}**\n"
        f"**{overs} over** match\n\n"
        + _conditions_block(conditions)
    )
    embed.set_footer(text="Win: +1 star  ·  Lose: -1 star")
    if gif_url:
        embed.set_image(url=gif_url)
    return embed


def _role_bonus(chem: int, role: str) -> int:
    extras = {"Batter": 0, "WK": 1, "All-Rounder": 0, "Bowler": -1}
    return max(1, (chem // 20) + extras.get(role, 0))


def _pad(text: str, width: int) -> str:
    text = str(text)
    return text[:width] if len(text) > width else text.ljust(width)


def _rpad(text: str, width: int) -> str:
    text = str(text)
    return text[:width] if len(text) > width else text.rjust(width)


def _table(headers: list[str], widths: list[int], rows: list[list[str]]) -> str:
    """Fixed-width table inside a code block so columns line up on mobile."""
    head = _pad(headers[0], widths[0]) + "  " + "  ".join(_rpad(h, w) for h, w in zip(headers[1:], widths[1:]))
    body = [
        _pad(row[0], widths[0]) + "  " + "  ".join(_rpad(c, w) for c, w in zip(row[1:], widths[1:]))
        for row in rows
    ]
    return "```\n" + "\n".join([head] + body) + "\n```"


def build_playing_xi_embed(team: dict) -> discord.Embed:
    """Playing XI used in every match: grouped by role, one monospace row per player with the
    card / event emoji, role emoji and flag (see xi_display.py)."""
    import xi_display as xd
    chem = team.get("chem", 80)
    ovr  = team.get("ovr", 80)

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
        ("WK",          "Wicket Keeper"),
        ("All-Rounder", "All-Rounders"),
        ("Bowler",      "Bowlers"),
    ]

    captain = team.get("captain")
    lines = [f"OVR **{ovr}**  ·  CHEM **{chem}**"]
    for role_key, role_label in role_order:
        players = [
            p for p in team.get("players", [])
            if _ROLE_TO_BUCKET.get(p.get("role", ""), "Batter") == role_key
        ]
        if not players:
            continue
        lines.append("")
        lines.append(f"**{role_label}**  (+{_role_bonus(chem, role_key)})")
        for p in players:
            lines.append(xd.player_row(p, captain=bool(captain) and p.get("name") == captain))

    embed = discord.Embed(
        title=team.get("name", "Team"),
        description="\n".join(lines)[:4000],
        color=discord.Color.dark_blue(),
    )
    embed.set_footer(text=f"{team.get('name', 'Team')} · Playing XI · {xd.COLUMNS_NOTE}")
    return embed


# Ball tokens for the Timeline — plain monospace, with boundaries and
# wickets in bold so the shape of an over reads at a glance.
_BALL_TOKEN = {
    "0":    "0",
    "1":    "1",
    "2":    "2",
    "3":    "3",
    "4":    "**4**",
    "6":    "**6**",
    "W":    "**W**",
    "Wd":   "*wd*",
    "NB":   "*nb*",
    "NB+1": "*nb+1*",
}


def _ball_token(outcome: str) -> str:
    return _BALL_TOKEN.get(outcome, outcome)


def _timeline_line(game: GameState, max_balls: int = 12) -> str | None:
    """Most recent over as one line: `Ov 15` · 1 4 0 W 6

    The timeline stores a "|" marker at each over boundary, so wides and
    no-balls don't break the grouping. A live over is shown as it unfolds;
    a completed over stays visible until the next ball is bowled.
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
    return f"`Ov {over_no}`  ·  {tokens}"


def build_scoreboard_embed(game: GameState) -> discord.Embed:
    """Live scoreboard after every ball. Native embed fields keep the stat
    chips as small boxes side-by-side on mobile and desktop."""
    inn = game.innings
    r, w = game.current_runs, game.current_wickets

    batting_team = game.get_batting_team()
    bowling_team = game.get_bowling_team()
    bat_team_name = batting_team.get("name", "Batting")
    bowl_team_name = bowling_team.get("name", "Bowling")

    color = discord.Color.green() if inn == 1 else discord.Color.orange()
    overs_done = game.overs_str()
    overs_total = f"{game.overs}.0"

    # Header: score + chase context
    title = f"{bat_team_name}  {r}/{w}  ({overs_done}/{overs_total})"
    if inn == 1:
        description = f"{bowl_team_name} · yet to bat"
    else:
        r1, w1 = game.runs[0], game.wickets[0]
        description = f"{bowl_team_name} made **{r1}/{w1}**"

    embed = discord.Embed(title=title, description=description, color=color)

    # Batters
    bat_rows = []
    for p, is_striker in ((game.striker, True), (game.non_striker, False)):
        if not p:
            continue
        pn = _pname(p)
        s = game.batsman_stats.get(pn, {"runs": 0, "balls": 0})
        mark = "*" if is_striker else ""  # * marks the striker, like real scorecards
        bat_rows.append([f"{pn[:14]}{mark}", str(s["runs"]), str(s["balls"]), game.sr(pn) or "0.0"])
    if bat_rows:
        embed.add_field(name="Batting", value=_table(["BATTER", "R", "B", "SR"], [15, 3, 3, 6], bat_rows), inline=False)

    # Stat chips
    if inn == 2:
        chip_last = f"`RRR {game.rrr()}`"
    else:
        chip_last = f"`Proj {game.projected()}`"
    embed.add_field(
        name="\u200b",
        value=f"`P'ship {game.partnership_runs}({game.partnership_balls})`   `CRR {game.crr()}`   {chip_last}",
        inline=False,
    )

    # Bowler
    if game.current_bowler:
        bn = _pname(game.current_bowler)
        bs = game.bowler_stats.get(bn, {"balls": 0, "runs": 0, "wickets": 0})
        ov = game.bowler_overs_str(bn)
        embed.add_field(
            name="Bowling",
            value=_table(["BOWLER", "O", "R", "W"], [15, 4, 3, 3], [[bn[:14], ov, str(bs["runs"]), str(bs["wickets"])]]),
            inline=False,
        )

    # Timeline
    timeline = _timeline_line(game)
    if timeline:
        embed.add_field(name="This over", value=timeline, inline=False)

    # Chase status (innings 2 only)
    if inn == 2:
        tgt = game.target()
        needed = max(0, (tgt - r)) if tgt else 0
        balls_left = max(0, game.overs * 6 - game.current_legal_balls)
        embed.add_field(
            name="\u200b",
            value=f"**{bat_team_name}** need **{needed}** off **{balls_left}** balls",
            inline=False,
        )
        # Live win-probability bar (cosmetic — never lets a problem here break the scoreboard)
        try:
            from moments import win_bar_field
            _wb = win_bar_field(game)
            if _wb:
                embed.add_field(name=_wb[0], value=_wb[1], inline=False)
        except Exception as _e:
            print(f"[win bar] skipped ({type(_e).__name__}: {_e})")

    if getattr(game, "toss_note", None):
        embed.set_footer(text=game.toss_note)
    return embed


def build_result_embed(game: GameState) -> discord.Embed:
    result = game.match_result()
    embed = discord.Embed(
        title="Match Result",
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

    embed.add_field(
        name="Scorecard",
        value=(
            f"**{bat1_team}** — {t1}/{w1}\n"
            f"**{bat2_team}** — {t2}/{w2}"
        ),
        inline=False,
    )
    embed.set_footer(text="Thanks for playing Cric Star")
    return embed
