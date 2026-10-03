from __future__ import annotations

from __future__ import absolute_import
import asyncio
import logging
import random
from typing import TYPE_CHECKING

import discord
from asgiref.sync import sync_to_async
from bd_models.models import BallInstance, MatchMedia

from .commentary import build_ball_commentary
from .game import GameState, MatchPlayer, Partnership
from .logic import (
    BATTING_SHOTS,
    BOWLING_DELIVERIES,
    FAST_BOWLING_LENGTHS,
    FAST_BOWLING_STYLES,
    FAST_COMBO_MAP,
    FAST_MEDIUM_SWING_COMBO_MAP,
    FAST_MEDIUM_SWING_LENGTHS,
    FAST_MEDIUM_SWING_STYLES,
    MEDIUM_PACE_COMBO_MAP,
    MEDIUM_PACE_DELIVERIES_LIST,
    ORTHODOX_DELIVERIES,
    apply_momentum,
    calculate_outcome,
    delivery_speed,
)
from .match_image import (
    _get_card_stats,
    send_batter_dismissal_image,
    send_batter_intro_image,
    send_bowler_intro_image,
)
from .match_media import collect_match_event_media
from .tv_summary import _pad_lines, _safe_get, generate_tv_broadcast_summary
from .xi import (
    load_subs,
    load_xi,
    save_subs,
)

log = logging.getLogger(__name__)

# Sentinel used by bot_match.py — never a real Discord user id
BOT_USER_ID: int = 0


async def _safe_defer_or_fallback(
    interaction: discord.Interaction,
    fallback_channel: discord.abc.Messageable | None = None,
    error_msg: str = "❌ This action expired. Use `/csrecover` to continue.",
) -> bool:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
            return True
    except (discord.NotFound, discord.HTTPException):
        pass
    return False


active_games: dict[int, GameState] = {}
channel_busy: set[int] = set()


def is_user_in_active_match(user_id: int) -> bool:
    """Return True if the user is a participant in any currently running match."""
    for game in active_games.values():
        c1 = getattr(game, "challenger", None)
        c2 = getattr(game, "opponent", None)
        if c1 and getattr(c1, "id", None) == user_id:
            return True
        if c2 and getattr(c2, "id", None) == user_id:
            return True
    return False


def get_active_match_for_user(user_id: int) -> GameState | None:
    """Return the active GameState for a user, or None."""
    for game in active_games.values():
        c1 = getattr(game, "challenger", None)
        c2 = getattr(game, "opponent", None)
        if c1 and getattr(c1, "id", None) == user_id:
            return game
        if c2 and getattr(c2, "id", None) == user_id:
            return game
    return None


TIMELINE_CHARS: dict[str, str] = {
    "0": "<:cs_dot:1504775841606012949>",
    "1": "<:cs_1run:1504775846026543144>",
    "2": "<:cs_2run:1504775849113554965>",
    "3": "<:cs_3run:1504775852833898496>",
    "4": "<a:cs_4run:1504775859415023687>",
    "6": "<a:cs_6run:1504775864582279188>",
    "W": "<a:Wicket:1505299207794879619>",
    "Wd": "<:Wide:1505299280615706784>",
    "NB": "<:Noball:1504775867589464166>",
}

# Only the most recent 14 balls are ever shown in the live timeline row —
# older balls slide off the front as new ones are bowled.
TIMELINE_MAX_BALLS = 14

HEAD_EMOJI = "<:cs_head:1504775828821774397>"
TAIL_EMOJI = "<:cs_tail:1504775832932057118>"


def _build_bench_for_user(
    game: GameState, user_id: int
) -> list[tuple[MatchPlayer, int]]:
    sub_pks: list[int] = []
    if hasattr(game, "substitutes") and isinstance(game.substitutes, dict):
        sub_pks = game.substitutes.get(user_id, []) or []
    if not sub_pks:
        return []
    xi_pks = set(game.team_pks.get(user_id, []))
    bench: list[tuple[MatchPlayer, int]] = []
    seen_names: set[str] = set()
    for pk in sub_pks:
        if pk in xi_pks:
            continue
        try:
            inst = BallInstance.objects.select_related("ball").get(pk=pk)
        except BallInstance.DoesNotExist:
            continue
        base = (getattr(inst.ball, "country", "") or "").strip().lower()
        if base in seen_names:
            continue
        seen_names.add(base)
        from .engine import (
            apply_lor_bonus,
            apply_shiny_boost,
            get_cricket_stats,
            has_lor_bonus,
            is_shiny,
        )

        ovr_val, bat_val, bowl_val = get_cricket_stats(inst.ball)
        if has_lor_bonus(inst.ball):
            bat_val, bowl_val = apply_lor_bonus(bat_val, bowl_val)
        if is_shiny(inst):
            ovr_val, bat_val, bowl_val = apply_shiny_boost(ovr_val, bat_val, bowl_val)
        mp = MatchPlayer(
            inst_pk=inst.pk,
            name=base.title(),
            ovr=ovr_val,
            bat=bat_val,
            bowl=bowl_val,
            bowling_type=getattr(inst.ball, "bowling_type", "Orthodox") or "Orthodox",
            fielder_grade=getattr(inst.ball, "fielder_grade", 1) or 1,
            narrative_attrs=[],
        )
        bench.append((mp, inst.pk))
    return bench


# ── Base View ─────────────────────────────────────────────────────────────────


class MatchView(discord.ui.View):
    # Never expire during an active match; UI state is driven by game actions.
    def __init__(self, timeout: int | None = None):
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        game = getattr(self, "game", None)
        if (
            game is None
            or active_games.get(getattr(game, "channel_id", None)) is not game
        ):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "This match has already been cancelled or finished.",
                        ephemeral=True,
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return False
        return True

    async def on_timeout(self) -> None:
        # With timeout=None, this should normally never run.
        # Keep a safe fallback: do not disable UI mid-match.
        try:
            for item in self.children:
                item.disabled = False
        except Exception:
            pass
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                log.debug(
                    f"Message for view {self.__class__.__name__} already edited or deleted on timeout."
                )


# ── Utility Helpers ───────────────────────────────────────────────────────────


def _tchar(outcome: str) -> str:
    o = str(outcome)
    if o in TIMELINE_CHARS:
        return TIMELINE_CHARS[o]
    # No-ball with runs off the bat/overthrows, e.g. "1NB", "4NB", "6NB"
    if o.endswith("NB"):
        return TIMELINE_CHARS.get("NB", o)
    # Byes / leg-byes, e.g. "1B", "2B", "4B", "1LB", "2LB", "4LB"
    if o.endswith("LB"):
        digits = o[:-2]
        return TIMELINE_CHARS.get(digits, TIMELINE_CHARS.get("0", o))
    if o.endswith("B"):
        digits = o[:-1]
        return TIMELINE_CHARS.get(digits, TIMELINE_CHARS.get("0", o))
    return TIMELINE_CHARS.get(o, o)


def _fmt_bat_row(name: str, is_striker: bool, r: int, b: int, fours: int, sixes: int, sr) -> str:
    tag = (name[:18] + ("*" if is_striker else "")).ljust(19)
    return f"{tag}{r:>4} ({b:>2}) {fours:>3} {sixes:>3} {sr:>7}"


def _fmt_bowl_row(name: str, overs: str, maidens: int, r: int, w: int, econ) -> str:
    tag = name[:18].ljust(19)
    return f"{tag}{overs:>5} {maidens:>4} {r:>4} {w:>3} {econ:>7}"


def _build_scoreboard_embed(game: GameState) -> discord.Embed:
    color = discord.Color.green() if game.innings == 1 else discord.Color.orange()
    bat = game.batting_user.display_name
    bowl = game.bowling_user.display_name
    ov_str = game.overs_str()

    embed = discord.Embed(
        title=f"🏏 MATCH SUMMARY: {game.challenger.display_name} vs {game.opponent.display_name} (T{game.overs})",
        color=color,
    )

    # ── Scores ─────────────────────────────────────────────────────────────
    score_lines = []
    if game.innings == 1:
        score_lines.append(
            f"{bat.upper():<10}: {game.current_runs}/{game.current_wickets} "
            f"({ov_str} ov)  CRR: {game.crr()}"
        )
        score_lines.append(f"{bowl.upper():<10}: YET TO BAT")
    else:
        score_lines.append(
            f"{bowl.upper():<10}: {game.runs[0]}/{game.wickets[0]} ({game.overs}.0 ov)"
        )
        score_lines.append(
            f"{bat.upper():<10}: {game.current_runs}/{game.current_wickets} "
            f"({ov_str} ov)  RRR: {game.rrr()}"
        )
    embed.add_field(
        name="\u200b", value="```\n" + "\n".join(score_lines) + "\n```", inline=False
    )

    # ── Batsman table ─────────────────────────────────────────────────────
    if game.striker or game.non_striker:
        header = f"{'BATSMAN':<19}{'R':>4} {'(B)':>4} {'4s':>3} {'6s':>3} {'SR':>7}"
        bat_rows = [header]
        if game.striker:
            sr = game.sr_for_player(game.striker)
            key = game._player_key(game.striker)
            s = game.batsman_stats.get(key, {"r": 0, "b": 0, "4s": 0, "6s": 0})
            bat_rows.append(
                _fmt_bat_row(game.striker.name, True, s["r"], s["b"], s["4s"], s["6s"], sr)
            )
        if game.non_striker:
            sr = game.sr_for_player(game.non_striker)
            key = game._player_key(game.non_striker)
            s = game.batsman_stats.get(key, {"r": 0, "b": 0, "4s": 0, "6s": 0})
            bat_rows.append(
                _fmt_bat_row(game.non_striker.name, False, s["r"], s["b"], s["4s"], s["6s"], sr)
            )
        embed.add_field(name="\u200b", value="```\n" + "\n".join(bat_rows) + "\n```", inline=False)

    # ── Bowler table ──────────────────────────────────────────────────────
    if game.current_bowler:
        bs = game.bowler_stats.get(game.current_bowler.name, {"r": 0, "w": 0, "m": 0})
        bc = game.bowler_ball_count.get(game.current_bowler.name, 0)
        overs = game.bowler_overs_str(game.current_bowler.name)
        maidens = bs.get("m", 0)
        econ = round((bs["r"] / bc) * 6, 2) if bc else 0.0
        header = f"{'BOWLER':<19}{'O':>5} {'M':>4} {'R':>4} {'W':>3} {'ECON':>7}"
        row = _fmt_bowl_row(game.current_bowler.name, overs, maidens, bs["r"], bs["w"], econ)
        embed.add_field(
            name="\u200b", value="```\n" + header + "\n" + row + "\n```", inline=False
        )

    # ── Timeline ──────────────────────────────────────────────────────────
    tl = getattr(game, "timeline", [])
    if tl:
        recent = tl[-TIMELINE_MAX_BALLS:]
        timeline_row = "".join(_tchar(str(o)) for o in recent)
        embed.add_field(name="Timeline", value=timeline_row, inline=False)

    # ── Chase line ────────────────────────────────────────────────────────
    if game.innings == 2:
        tgt = game.target() or 0
        need = max(tgt - game.current_runs, 0)
        embed.add_field(
            name="\u200b",
            value=f"**{bat}** need **{need}** runs in **{game.balls_remaining()}** balls.",
            inline=False,
        )

    return embed


def _team_ovr(team: list) -> int:
    if not team:
        return 0
    return round(sum(p.ovr for p in team[:11]) / 11)


def _xi_embed(team_name: str, team: list[MatchPlayer]) -> discord.Embed:
    ovr = _team_ovr(team)
    embed = discord.Embed(
        title=f"📋 {team_name}'s Playing XI — **{ovr}/100** OVR",
        color=discord.Color.blurple(),
    )
    all_rounders, batters, bowlers = [], [], []
    for p in team:
        has_bat = p.bat and p.bat >= 60
        has_bowl = p.bowl and p.bowl >= 60
        if has_bat and has_bowl:
            all_rounders.append(p)
        elif has_bat:
            batters.append(p)
        elif has_bowl:
            bowlers.append(p)
        else:
            batters.append(p)
    lines, counter = [], 1
    if all_rounders:
        lines.append("⭐ **All-Rounders:**")
        for p in all_rounders:
            lines.append(
                f"`{counter:>2}.` **{p.name}** | OVR `{p.ovr}` | Bat `{p.bat}` | Bowl `{p.bowl}`"
            )
            counter += 1
    if batters:
        lines.append("\n🏏 **Batters:**")
        for p in batters:
            lines.append(
                f"`{counter:>2}.` **{p.name}** | OVR `{p.ovr}` | Bat `{p.bat}`"
            )
            counter += 1
    if bowlers:
        lines.append("\n⚾ **Bowlers:**")
        for p in bowlers:
            lines.append(
                f"`{counter:>2}.` **{p.name}** | OVR `{p.ovr}` | Bowl `{p.bowl}` | {p.effective_bowling_type()}"
            )
            counter += 1
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"{team_name} • Playing XI")
    return embed


# ── Analytics Embeds ──────────────────────────────────────────────────────────


def _build_win_prob_embed(game: GameState) -> discord.Embed:
    bat_prob, bowl_prob = game.calculate_win_probability()
    bat_name = game.batting_user.display_name
    bowl_name = game.bowling_user.display_name
    inn = game.innings
    bat_bar = "█" * int(bat_prob * 20) + "░" * (20 - int(bat_prob * 20))
    bowl_bar = "█" * int(bowl_prob * 20) + "░" * (20 - int(bowl_prob * 20))
    desc = (
        f"**{bat_name}** `{int(bat_prob * 100)}%`\n`{bat_bar}`\n\n"
        f"**{bowl_name}** `{int(bowl_prob * 100)}%`\n`{bowl_bar}`"
    )
    if inn == 1:
        desc += "\n\n*(1st innings — baseline 50/50)*"
    else:
        need = game.target() - game.current_runs if game.target() else 0
        balls_left = game.balls_remaining()
        desc += f"\n\n🎯 Need **{max(0, need)}** in **{balls_left}** balls | RRR: **{game.rrr()}**"
    color = (
        discord.Color.green()
        if bat_prob > bowl_prob
        else discord.Color.red() if bowl_prob > bat_prob else discord.Color.gold()
    )
    embed = discord.Embed(title="📊 Win Probability", description=desc, color=color)
    embed.set_footer(text=f"Innings {inn} | Over {game.overs_str()}")
    return embed


def _build_partnership_embed(game: GameState) -> discord.Embed:
    ph = game.partnership_history
    current = game.current_partnership
    lines = []
    if current:
        s1 = game.striker.name if game.striker else "?"
        s2 = game.non_striker.name if game.non_striker else "?"
        lines.append("**Current Partnership** 🏏\n")
        lines.append(
            f"`{s1[:18].ljust(18)} {game.batsman_stats.get(game._player_key(game.striker), {}).get('r', 0)}*`"
        )
        lines.append(
            f"`{s2[:18].ljust(18)} {game.batsman_stats.get(game._player_key(game.non_striker), {}).get('r', 0)}*`"
        )
        lines.append(f"\n**Together:** {current.runs} runs ({current.balls} balls)")
        lines.append(
            f"**Run Rate:** {round(current.runs / max(1, current.balls) * 6, 2)}"
        )
    if ph:
        lines.append("\n**Previous Partnerships**")
        for i, p in enumerate(ph[-5:], 1):
            b1 = p.batsman1[:14] if hasattr(p, "batsman1") else "?"
            b2 = p.batsman2[:14] if hasattr(p, "batsman2") else "?"
            lines.append(f"`{str(i)}.` {b1} & {b2} — **{p.runs}** ({p.balls} balls)")
    embed = discord.Embed(
        title="🤝 Partnership Status",
        description="\n".join(lines) if lines else "No partnership data yet.",
        color=discord.Color.blue(),
    )
    embed.set_footer(text=f"Innings {game.innings} | {game.overs_str()} overs")
    return embed


def _build_manhattan_embed(game: GameState) -> discord.Embed:
    history = game.over_history
    if not history:
        return discord.Embed(
            title="📊 Manhattan Chart",
            description="No overs bowled yet.",
            color=discord.Color.orange(),
        )
    lines = ["**Over  | Runs | Wkts**"]
    max_runs = max((h["runs"] for h in history), default=1)
    scale = max(1, max_runs)
    for i, h in enumerate(history, 1):
        bar = "█" * int((h["runs"] / scale) * 8)
        wkt = " 💥" if h.get("wickets") else ""
        lines.append(f"`{str(i).rjust(2)}.` {bar.ljust(8)} **{h['runs']}**{wkt}")
    embed = discord.Embed(
        title="📊 Manhattan Chart (Runs per Over)",
        description="\n".join(lines),
        color=discord.Color.orange(),
    )
    embed.set_footer(
        text=f"Innings {game.innings} | Total: {sum(h['runs'] for h in history)} runs"
    )
    return embed


def _build_worm_embed(game: GameState) -> discord.Embed:
    lines = []
    inn1_runs = getattr(game, "runs", [0, 0])
    inn1_total = inn1_runs[0] if len(inn1_runs) > 0 else 0
    inn2_total = game.current_runs if game.innings == 2 else None

    if game.innings == 1:
        lines.append(
            f"**Innings 1 Progress:** `{inn1_total}/{game.current_wickets}` in `{game.overs_str()}/{game.overs}.0` overs"
        )
        lines.append("Innings 2 yet to begin.")
    else:
        lines.append(
            f"**Innings 1:** `{inn1_total}/{game.wickets[0]}` in `{game.overs}.0` overs"
        )
        lines.append(
            f"**Innings 2:** `{inn2_total}/{game.current_wickets}` in `{game.overs_str()}/{game.overs}.0` overs"
        )

    return discord.Embed(
        title="📈 Worm / Momentum",
        description="\n".join(lines),
        color=discord.Color.purple(),
    )


async def _record_ball_analytics(
    game: GameState, outcome_dict: dict, shot: str = "", channel=None, bot=None
) -> None:
    try:
        runs_scored = outcome_dict.get("runs_batter", 0) + outcome_dict.get(
            "overthrows", 0
        )
        delivery = getattr(game, "pending_delivery", "") or ""
        if not shot:
            shot = getattr(game, "_last_shot", "") or ""
        if runs_scored in (4, 6):
            game._do_record_boundary_direction(runs_scored, delivery, shot)
        game.record_win_prob(game.current_legal_balls)
        game._last_shot = shot
    except Exception:
        pass
    try:
        if channel is None or bot is None:
            return
        dismissal = outcome_dict.get("dismissal_type")
        is_direct_hit = bool(outcome_dict.get("is_direct_hit", False))
        milestone_runs = max(
            game.batsman_stats.get(game._player_key(game.striker), {}).get("r", 0), 0
        )

        # Pick media AFTER the outcome is decided (so keying on commentary/outcome is correct).
        url = await collect_match_event_media(
            delivery=delivery or None,
            shot=shot or None,
            dismissal=dismissal or None,
            runs_scored=runs_scored,
            milestone_runs=milestone_runs,
            is_direct_hit=is_direct_hit,
        )
        if url:
            await channel.send(url)
    except Exception:
        pass


# ── Prompts & Flow ────────────────────────────────────────────────────────────


async def send_bowling_prompt(
    channel: discord.abc.Messageable, game: GameState
) -> None:
    bowler = game.current_bowler
    if not bowler:
        return
    free_hit = (
        "\n🟡 **FREE HIT** — the batsman **cannot** be dismissed this ball!\n"
        if game.last_was_no_ball
        else ""
    )
    try:
        # Disable buttons on any previous bowling prompt to prevent duplicate UI
        last_msg_id = getattr(game, "_last_bowling_prompt_msg_id", None)
        if last_msg_id:
            try:
                old_msg = channel.get_partial_message(last_msg_id)
                await old_msg.edit(view=None)
            except (discord.NotFound, discord.HTTPException, AttributeError):
                pass
        view = BowlingView(game)
        message = await channel.send(
            content=f"{free_hit}{game.bowling_user.mention}\n**{bowler.name}** is bowling. Choose your delivery:",
            embed=_build_scoreboard_embed(game),
            view=view,
        )
        view.message = message
        game._last_bowling_prompt_msg_id = message.id
    except discord.HTTPException:
        pass


async def recover_current_prompt(
    channel: discord.abc.Messageable, game: GameState
) -> None:
    if game.is_innings_over():
        return

    # At the very start of an innings the batting side must pick openers and a striker
    # before the bowling side can choose a bowler. Without this guard recover_current_prompt
    # would jump straight to NextBowlerView and leave the bowling user stuck.
    if game.current_bowler is None and game.striker is None:
        if game.batting_user_id != BOT_USER_ID:
            team = game.teams.get(game.batting_user_id, [])
            if team:
                try:
                    if game.current_partnership is None:
                        view = OpenerSelectView(game, team)
                        message = await channel.send(
                            content=f"♻️ **Recovered** {game.batting_user.mention} select your **opening pair** (choose 2):",
                            embed=_xi_embed(game.batting_user.display_name, team),
                            view=view,
                        )
                        view.message = message
                    else:
                        p1 = next(
                            (
                                p
                                for p in team
                                if p.inst_pk == game.current_partnership.batsman1_pk
                            ),
                            None,
                        )
                        p2 = next(
                            (
                                p
                                for p in team
                                if p.inst_pk == game.current_partnership.batsman2_pk
                            ),
                            None,
                        )
                        if p1 and p2:
                            view = StrikerDesignateView(game, p1, p2)
                            message = await channel.send(
                                content=f"♻️ **Recovered** {game.batting_user.mention} who is on **strike**?",
                                view=view,
                            )
                            view.message = message
                except Exception:
                    pass
        return

    if game.current_bowler is None:
        if game.bowling_user_id != BOT_USER_ID:
            avail = game.get_available_bowlers()
            if avail:
                try:
                    await channel.send(
                        content=f"♻️ **Recovered** {game.bowling_user.mention}, choose the next bowler:",
                        view=NextBowlerView(game, avail),
                    )
                except Exception:
                    pass
        return
    if game.pending_delivery is None:
        await send_bowling_prompt(channel, game)
        return
    if game.batting_user_id != BOT_USER_ID:
        view = BattingView(game)
        try:
            message = await channel.send(
                content=f"♻️ **Recovered** {game.batting_user.mention}, please continue with the shot.",
                embed=_build_scoreboard_embed(game),
                view=view,
            )
        except Exception:
            pass


async def _do_innings_break(
    channel: discord.abc.Messageable, game: GameState, bot=None
) -> None:
    try:
        await _save_match_stats(game, bot=bot)
    except Exception as stats_exc:
        log.error(f"[views] _save_match_stats failed at innings break: {stats_exc}")
    game.populate_summary_stats()
    t, balls = game.runs[0], game.overs * 6
    await channel.send(
        embed=generate_tv_broadcast_summary(game, is_final=False, innings_summary=1)
    )
    await channel.send(
        f"☕ **INNINGS BREAK!**\n**{game.bowling_user.display_name}** need **{t + 1} runs** off "
        f"**{balls} balls** to win *(RRR: {round((t + 1) / (balls / 6), 1)})*"
    )
    game.start_second_innings()
    team = game.teams.get(game.batting_user_id, [])
    # Bot team: auto-pick openers silently (bot_match Runner handles its own flow)
    if game.batting_user_id == BOT_USER_ID:
        return
    view = OpenerSelectView(game, team)
    message = await channel.send(
        content=f"🏏 {game.batting_user.mention} select your **opening pair** (choose 2):",
        embed=_xi_embed(game.batting_user.display_name, team),
        view=view,
    )
    view.message = message


# ── Pre-Match & Setup Views ───────────────────────────────────────────────────


class BatBowlView(MatchView):
    def __init__(self, game: GameState):
        super().__init__()
        self.game = game

    async def _choose(self, interaction: discord.Interaction, bat_first: bool) -> None:
        if interaction.user.id != self.game.toss_winner_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Only the toss winner decides!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        other = (
            self.game.opponent.id
            if interaction.user.id == self.game.challenger.id
            else self.game.challenger.id
        )
        if bat_first:
            self.game.batting_user_id, self.game.bowling_user_id = (
                interaction.user.id,
                other,
            )
            label = "bat first"
        else:
            self.game.bowling_user_id, self.game.batting_user_id = (
                interaction.user.id,
                other,
            )
            label = "bowl first"
        self.game.innings1_batting_user_id = self.game.batting_user_id
        self.stop()
        for item in self.children:
            item.disabled = True
        # Edit the message to remove the view
        try:
            await interaction.response.edit_message(
                content=f"✅ **{interaction.user.display_name}** chose to **{label}**!",
                view=None,
            )
            # The prompt states to use followup.send for the next phase, not edit_message
        except (discord.NotFound, discord.HTTPException):
            pass
        team = self.game.teams.get(self.game.batting_user_id, [])
        view = OpenerSelectView(self.game, team)
        message = await interaction.channel.send(
            content=f"🏏 {self.game.batting_user.mention} select your **opening pair** (choose 2):",
            embed=_xi_embed(interaction.user.display_name, team),
            view=view,
        )
        view.message = message

    @discord.ui.button(label="Bat", style=discord.ButtonStyle.green)
    async def bat(self, i: discord.Interaction, _: discord.ui.Button):
        await self._choose(i, True)

    @discord.ui.button(label="Bowl", style=discord.ButtonStyle.red)
    async def bowl(self, i: discord.Interaction, _: discord.ui.Button):
        await self._choose(i, False)


class OpenerSelectView(MatchView):
    def __init__(self, game: GameState, team: list[MatchPlayer]):
        super().__init__()
        self.game, self._team = game, team
        options = [
            discord.SelectOption(
                label=p.name[:25],
                value=str(i),
                description=f"OVR {p.ovr} | Bat {p.bat} | Bowl {p.bowl}",
            )
            for i, p in enumerate(team)
        ]
        self._sel = discord.ui.Select(
            placeholder="🏏 Choose 2 openers...",
            min_values=2,
            max_values=2,
            options=options,
        )
        self._sel.callback = self._cb
        self.add_item(self._sel)

    async def _cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.batting_user_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Only the batting team selects openers!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        chosen = [self._team[int(v)] for v in self._sel.values]
        if len(chosen) < 2:
            return
        try:
            await interaction.response.edit_message(
                content=f"✅ **{chosen[0].name}** & **{chosen[1].name}** opening. Who takes strike?",
                view=None,
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        for p in chosen:
            self.game.reset_bat_stats_for_player(p)
        over_num = (
            self.game.current_legal_balls // 6 + 1
            if self.game.current_legal_balls
            else 1
        )
        self.game.current_partnership = Partnership(
            chosen[0].name,
            chosen[1].name,
            over_num,
            batsman1_pk=chosen[0].inst_pk,
            batsman2_pk=chosen[1].inst_pk,
        )
        self.stop()
        view = StrikerDesignateView(self.game, chosen[0], chosen[1])
        message = await interaction.channel.send(
            content=f"🏏 {self.game.batting_user.mention} who is on **strike**?",
            view=view,
        )
        view.message = message


class StrikerDesignateView(MatchView):
    def __init__(self, game: GameState, p1: MatchPlayer, p2: MatchPlayer):
        super().__init__()
        self.game = game
        self._p1, self._p2 = p1, p2
        b1 = discord.ui.Button(label=p1.name[:40], style=discord.ButtonStyle.green)
        b2 = discord.ui.Button(label=p2.name[:40], style=discord.ButtonStyle.green)
        b1.callback = lambda i: self._pick(i, p1, p2)
        b2.callback = lambda i: self._pick(i, p2, p1)
        self.add_item(b1)
        self.add_item(b2)

    async def _pick(
        self, inter: discord.Interaction, striker: MatchPlayer, non_striker: MatchPlayer
    ):
        if inter.user.id != self.game.batting_user_id:
            try:
                if not inter.response.is_done():
                    await inter.response.send_message(
                        "Only the batting team decides!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        self.game.striker, self.game.non_striker = striker, non_striker
        over_num = (
            self.game.current_legal_balls // 6 + 1
            if self.game.current_legal_balls
            else 1
        )
        self.game.current_partnership = Partnership(
            striker.name,
            non_striker.name,
            over_num,
            batsman1_pk=striker.inst_pk,
            batsman2_pk=non_striker.inst_pk,
        )
        self.stop()
        try:
            await inter.response.edit_message(
                content=f"✅ **{striker.name}** on strike, **{non_striker.name}** non-striker",
                view=None,
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        channel = inter.channel
        bowlers = [
            p
            for p in self.game.teams.get(self.game.bowling_user_id, [])
            if p.can_bowl()
        ]
        if not bowlers:
            await channel.send(
                "⚠️ No eligible bowler found. Use `/csrecover` or restart the match."
            )
            return
        try:
            view = BowlerSelectView(self.game, bowlers)
            message = await channel.send(
                content=f"⚾ {self.game.bowling_user.mention} choose your opening bowler:",
                embed=_xi_embed(
                    self.game.bowling_user.display_name,
                    self.game.teams.get(self.game.bowling_user_id, []),
                ),
                view=view,
            )
            view.message = message
        except Exception as exc:
            log.exception("[views] Failed to send bowler selection prompt: %s", exc)

        async def _fire_batter(mp: MatchPlayer, is_cap: bool):
            try:
                stats = await _get_card_stats(mp.inst_pk) if mp.inst_pk else None
                asyncio.create_task(
                    send_batter_intro_image(
                        channel, mp, card_stats=stats, is_captain=is_cap
                    )
                )
            except Exception as exc:
                log.error("[intro] Batter image failed: %s", exc)

        batting_captain = self.game.get_captain(self.game.batting_user_id)
        for mp in [striker, non_striker]:
            asyncio.create_task(_fire_batter(mp, mp == batting_captain))


class BowlerSelectView(MatchView):
    def __init__(self, game: GameState, bowlers: list[MatchPlayer]):
        super().__init__()
        self.game, self._bowlers = game, bowlers
        options = [
            discord.SelectOption(
                label=p.name[:25],
                value=str(i),
                description=f"🎯 {p.effective_bowling_type()} | Bowl {p.bowl} | Overs {game.bowler_overs_str(p.name)}",
            )
            for i, p in enumerate(bowlers)
        ]
        self._sel = discord.ui.Select(
            placeholder="⚾ Choose your bowler...", options=options
        )
        self._sel.callback = self._cb
        self.add_item(self._sel)

    async def _cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.bowling_user_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Only the bowling team selects the bowler!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        bowler = self._bowlers[int(self._sel.values[0])]
        self.game.current_bowler = bowler
        self.game._over_ended_flag = False
        self.game._ensure_bowl_stats(bowler.name)
        self.stop()
        try:
            await interaction.response.edit_message(
                content=f"✅ **{bowler.name}** will bowl.", view=None
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        await send_bowling_prompt(interaction.channel, self.game)
        asyncio.create_task(self._bowler_intro_bg(interaction.channel, bowler))

    async def _bowler_intro_bg(self, channel, bowler: MatchPlayer):
        try:
            stats = await _get_card_stats(bowler.inst_pk)
            is_cap = bowler == self.game.get_captain(self.game.bowling_user_id)
            await send_bowler_intro_image(
                channel, bowler, card_stats=stats, is_captain=is_cap
            )
        except Exception as exc:
            log.error("[intro] Bowler bg failed: %s", exc)


# ── In-Match Action Views ─────────────────────────────────────────────────────


class BowlingView(MatchView):

    STYLE_BTN = {
        "Fast": discord.ButtonStyle.danger,
        "InSwing": discord.ButtonStyle.secondary,
        "OutSwing": discord.ButtonStyle.primary,
        "Inswinger": discord.ButtonStyle.secondary,
        "Outswinger": discord.ButtonStyle.primary,
        "Reverse Swing": discord.ButtonStyle.primary,
        "Cutter": discord.ButtonStyle.success,
        "Slow": discord.ButtonStyle.secondary,
        "Slower Ball": discord.ButtonStyle.secondary,
    }
    LENGTH_BTN = {
        "Yorker": discord.ButtonStyle.secondary,
        "Bouncer": discord.ButtonStyle.danger,
        "Good Length": discord.ButtonStyle.success,
        "Full": discord.ButtonStyle.primary,
        "Slot": discord.ButtonStyle.secondary,
        "Short Ball": discord.ButtonStyle.danger,
        "Cutter": discord.ButtonStyle.primary,
    }

    def __init__(self, game: GameState):
        super().__init__()
        self.game = game
        btype = (
            game.current_bowler.effective_bowling_type()
            if game.current_bowler
            else "Fast"
        )
        if btype == "Fast":
            self._build_fast_rows()
        elif btype == "Fast-Medium Swing":
            self._build_fast_medium_swing_rows()
        elif btype == "Medium-Pace":
            self._build_medium_pace_rows()
        elif btype == "Orthodox":
            self._build_orthodox_rows()
        else:
            self._build_spin_rows(btype)

    ORTHODOX_STYLE = {
        "Stock": discord.ButtonStyle.secondary,
        "Arm Ball": discord.ButtonStyle.primary,
        "Topspinner": discord.ButtonStyle.success,
        "Slider": discord.ButtonStyle.danger,
        "Undercutter": discord.ButtonStyle.primary,
    }

    def _build_orthodox_rows(self) -> None:
        deliveries = BOWLING_DELIVERIES.get("Orthodox", ORTHODOX_DELIVERIES)
        for i, d in enumerate(deliveries):
            btn = discord.ui.Button(
                label=d,
                style=self.ORTHODOX_STYLE.get(d, discord.ButtonStyle.primary),
                row=i // 3,
            )
            btn.callback = self._orthodox_delivery_cb(d)
            self.add_item(btn)

    def _orthodox_delivery_cb(self, delivery: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            view = OrthodoxBowlingView(self.game)
            await view.start(interaction, delivery)

        return cb

    def _build_fast_rows(self) -> None:
        for i, style in enumerate(FAST_BOWLING_STYLES):
            btn = discord.ui.Button(
                label=style,
                style=self.STYLE_BTN.get(style, discord.ButtonStyle.secondary),
                row=i // 3,
            )
            btn.callback = self._style_cb(style)
            self.add_item(btn)
        for i, length in enumerate(FAST_BOWLING_LENGTHS):
            btn = discord.ui.Button(
                label=length,
                style=self.LENGTH_BTN.get(length, discord.ButtonStyle.secondary),
                row=2 + (i // 3),
                disabled=True,
            )
            btn.callback = self._length_cb(length)
            self.add_item(btn)

    def _style_cb(self, chosen_style: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            for item in self.children:
                if item.row in (0, 1):
                    item.disabled = True
                    if getattr(item, "label", "") == chosen_style:
                        item.style = discord.ButtonStyle.success
                elif item.row in (2, 3):
                    item.disabled = False
            try:
                await interaction.response.edit_message(
                    content=f"\n\n---\n\n✅ **{chosen_style}** locked in!\n**{self.game.bowling_user.mention} choose your delivery:**",
                    view=self,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

        return cb

    def _length_cb(self, chosen_length: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            chosen_style = next(
                (
                    getattr(i, "label", "Fast")
                    for i in self.children
                    if i.row in (0, 1)
                    and getattr(i, "style", None) == discord.ButtonStyle.success
                ),
                "Fast",
            )
            matrix_key, speed_mod = FAST_COMBO_MAP.get(
                (chosen_style, chosen_length), ("Good Length", 0.0)
            )
            combo_label = f"{chosen_style} {chosen_length}"
            self.game.pending_delivery = matrix_key
            self.game.pending_delivery_label = combo_label
            self.game.pending_delivery_speed_mod = speed_mod
            if self.game.batting_user_id != BOT_USER_ID:
                self.clear_items()
                for i, shot in enumerate(BATTING_SHOTS):
                    btn = discord.ui.Button(
                        label=shot,
                        style=BattingView.STYLES.get(
                            shot, discord.ButtonStyle.secondary
                        ),
                        row=i // 4,
                    )
                    btn.callback = self._make_shot_cb(shot)
                    self.add_item(btn)
                await interaction.response.edit_message(
                    content=(
                        f"\n\n---\n\n🎯 **{self.game.current_bowler.name}** bowls a **{combo_label}**!\n"
                        f"🏏 **{self.game.batting_user.mention}**\n"
                        f"**{self.game.striker.name if self.game.striker else 'Batter'}** — play your shot!"
                    ),
                    view=self,
                )
            else:
                await interaction.response.edit_message(
                    content=f"🎯 **{self.game.current_bowler.name}** bowls a **{combo_label}**!",
                    view=None,
                )

        return cb

    def _build_fast_medium_swing_rows(self) -> None:
        self._fms_length_deliveries = {}
        for i, style in enumerate(FAST_MEDIUM_SWING_STYLES):
            btn = discord.ui.Button(
                label=style,
                style=self.STYLE_BTN.get(style, discord.ButtonStyle.secondary),
                row=i // 3,
            )
            btn.callback = self._fms_style_cb(style)
            self.add_item(btn)
        length_map = {
            "Yorker": "Yorker",
            "Good Length": "Good Length",
            "Full": "Full",
            "Slot": "Slot",
        }
        for i, length in enumerate(FAST_MEDIUM_SWING_LENGTHS):
            cid = f"fms_len_{length.replace(' ', '_')}"
            btn = discord.ui.Button(
                label=length,
                custom_id=cid,
                style=discord.ButtonStyle.secondary,
                row=2 + (i // 3),
                disabled=True,
            )
            btn.callback = self._fms_length_cb
            self._fms_length_deliveries[cid] = length_map.get(length, length)
            self.add_item(btn)

    def _fms_style_cb(self, chosen_style: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            for item in self.children:
                if item.row in (0, 1):
                    item.disabled = True
                    if getattr(item, "label", "") == chosen_style:
                        item.style = discord.ButtonStyle.success
                elif item.row in (2, 3):
                    original = self._fms_length_deliveries.get(
                        getattr(item, "custom_id", ""), item.label
                    )
                    if chosen_style == "Cutter":
                        if original == "Yorker":
                            item.label = "Off Cutter"
                            self._fms_length_deliveries[item.custom_id] = "Off Cutter"
                            item.disabled = False
                        elif original == "Good Length":
                            item.label = "Leg Cutter"
                            self._fms_length_deliveries[item.custom_id] = "Leg Cutter"
                            item.disabled = False
                        else:
                            item.disabled = True
                    else:
                        item.label = original
                        item.disabled = False
                        self._fms_length_deliveries[item.custom_id] = original
            try:
                await interaction.response.edit_message(
                    content=f"\n\n---\n\n✅ **{chosen_style}** locked in!\n**{self.game.bowling_user.mention} choose your delivery:**",
                    view=self,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

        return cb

    async def _fms_length_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.bowling_user_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Not your turn!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        chosen_length = self._fms_length_deliveries.get(
            interaction.data.get("custom_id", ""), "Good Length"
        )
        chosen_style = next(
            (
                getattr(i, "label", "")
                for i in self.children
                if i.row in (0, 1)
                and getattr(i, "style", None) == discord.ButtonStyle.success
            ),
            "",
        )
        if chosen_style == "Cutter":
            matrix_key = chosen_length
            speed_mod = -5.0
        else:
            matrix_key, speed_mod = FAST_MEDIUM_SWING_COMBO_MAP.get(
                (chosen_style, chosen_length), (chosen_style, -3.0)
            )
        combo_label = (
            f"{chosen_style} {chosen_length}"
            if chosen_style != "Cutter"
            else chosen_length
        )
        self.game.pending_delivery = matrix_key
        self.game.pending_delivery_label = combo_label
        self.game.pending_delivery_speed_mod = speed_mod
        if self.game.batting_user_id != BOT_USER_ID:
            self.clear_items()
            for i, shot in enumerate(BATTING_SHOTS):
                btn = discord.ui.Button(
                    label=shot,
                    style=BattingView.STYLES.get(shot, discord.ButtonStyle.secondary),
                    row=i // 4,
                )
                btn.callback = self._make_shot_cb(shot)
                self.add_item(btn)
            await interaction.response.edit_message(
                content=(
                    f"\n\n---\n\n🎯 **{self.game.current_bowler.name}** bowls a **{combo_label}**!\n"
                    f"🏏 **{self.game.batting_user.mention}**\n"
                    f"**{self.game.striker.name if self.game.striker else 'Batter'}** — play your shot!"
                ),
                view=self,
            )
        else:
            await interaction.response.edit_message(
                content=f"🎯 **{self.game.current_bowler.name}** bowls a **{combo_label}**!",
                view=None,
            )

    def _build_medium_pace_rows(self) -> None:
        mp_style = {
            "Knuckle": discord.ButtonStyle.secondary,
            "Off Cutter": discord.ButtonStyle.success,
            "Leg Cutter": discord.ButtonStyle.primary,
            "Bouncer": discord.ButtonStyle.danger,
        }
        for i, delivery in enumerate(MEDIUM_PACE_DELIVERIES_LIST):
            btn = discord.ui.Button(
                label=delivery,
                style=mp_style.get(delivery, discord.ButtonStyle.primary),
                row=i // 3,
            )
            btn.callback = self._mp_delivery_cb(delivery)
            self.add_item(btn)
        bouncer_fast = discord.ui.Button(
            label="Bouncer (Fast)", style=discord.ButtonStyle.danger, row=2
        )
        bouncer_fast.callback = self._mp_delivery_cb("Bouncer (Fast)")
        self.add_item(bouncer_fast)
        bouncer_slow = discord.ui.Button(
            label="Bouncer (Slow)", style=discord.ButtonStyle.secondary, row=2
        )
        bouncer_slow.callback = self._mp_delivery_cb("Bouncer (Slow)")
        self.add_item(bouncer_slow)

    def _mp_delivery_cb(self, delivery: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            matrix_key, speed_mod = MEDIUM_PACE_COMBO_MAP.get(
                (delivery, delivery), (delivery, 0.0)
            )
            combo_label = delivery
            self.game.pending_delivery = matrix_key
            self.game.pending_delivery_label = combo_label
            self.game.pending_delivery_speed_mod = speed_mod
            if self.game.batting_user_id != BOT_USER_ID:
                self.clear_items()
                for i, shot in enumerate(BATTING_SHOTS):
                    btn = discord.ui.Button(
                        label=shot,
                        style=BattingView.STYLES.get(
                            shot, discord.ButtonStyle.secondary
                        ),
                        row=i // 4,
                    )
                    btn.callback = self._make_shot_cb(shot)
                    self.add_item(btn)
                await interaction.response.edit_message(
                    content=(
                        f"\n\n---\n\n🎯 **{self.game.current_bowler.name}** bowls a **{combo_label}**!\n"
                        f"🏏 **{self.game.batting_user.mention}**\n"
                        f"**{self.game.striker.name if self.game.striker else 'Batter'}** — play your shot!"
                    ),
                    view=self,
                )
            else:
                await interaction.response.edit_message(
                    content=f"🎯 **{self.game.current_bowler.name}** bowls a **{combo_label}**!",
                    view=None,
                )

        return cb

    def _make_shot_cb(self, shot: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            try:
                await interaction.response.edit_message(
                    content=f"🏏 **{shot}**", view=None
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            self.stop()
            try:
                await process_delivery(interaction, self.game, shot)
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[views] Shot error interaction expired: %s", e)
                try:
                    await interaction.channel.send(
                        "⏳ UI Expired. Use `/csrecover` to continue."
                    )
                except Exception:
                    pass
            except Exception as e:
                log.exception("[views] Shot error: %s", e)
                try:
                    await interaction.channel.send(
                        "⚠️ Something went wrong processing that ball. "
                        "The match is still active — use `/csrecover` if it doesn't continue."
                    )
                except Exception:
                    pass

        return cb

    SPIN_STYLE = {
        "Off Break": discord.ButtonStyle.primary,
        "Doosra": discord.ButtonStyle.danger,
        "Carrom Ball": discord.ButtonStyle.success,
        "Arm Ball": discord.ButtonStyle.secondary,
        "Top Spin": discord.ButtonStyle.primary,
        "Drift Ball": discord.ButtonStyle.secondary,
        "Leg Break": discord.ButtonStyle.primary,
        "Googly": discord.ButtonStyle.danger,
        "Flipper": discord.ButtonStyle.danger,
        "Top Spinner": discord.ButtonStyle.success,
        "Slider": discord.ButtonStyle.primary,
    }

    def _build_spin_rows(self, btype: str) -> None:
        deliveries = BOWLING_DELIVERIES.get(btype, BOWLING_DELIVERIES["Fast"])
        for i, d in enumerate(deliveries):
            btn = discord.ui.Button(
                label=d,
                style=self.SPIN_STYLE.get(d, discord.ButtonStyle.primary),
                row=i // 3,
            )
            btn.callback = self._spin_cb(d)
            self.add_item(btn)

    def _spin_cb(self, delivery: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            self.game.pending_delivery = delivery
            self.game.pending_delivery_label = delivery
            self.game.pending_delivery_speed_mod = 0.0
            if self.game.batting_user_id != BOT_USER_ID:
                self.clear_items()
                for i, shot in enumerate(BATTING_SHOTS):
                    btn = discord.ui.Button(
                        label=shot,
                        style=BattingView.STYLES.get(
                            shot, discord.ButtonStyle.secondary
                        ),
                        row=i // 4,
                    )
                    btn.callback = self._make_shot_cb(shot)
                    self.add_item(btn)
                await interaction.response.edit_message(
                    content=(
                        f"🎯 **{self.game.current_bowler.name}** bowls a **{delivery}**! "
                        f"🏏 {self.game.batting_user.mention} "
                        f"**{self.game.striker.name if self.game.striker else 'Batter'}**, play your shot!"
                    ),
                    view=self,
                )
            else:
                await interaction.response.edit_message(
                    content=f"🎯 **{self.game.current_bowler.name}** bowls a **{delivery}**!",
                    view=None,
                )

        return cb


class OrthodoxBowlingView(MatchView):
    LENGTH_BTN = {
        "Full Length": discord.ButtonStyle.danger,
        "Good Length": discord.ButtonStyle.secondary,
        "Short": discord.ButtonStyle.primary,
    }

    def __init__(self, game: GameState):
        super().__init__()
        self.game = game
        self._chosen_delivery: str | None = None
        for length in self.LENGTH_BTN:
            btn = discord.ui.Button(label=length, style=self.LENGTH_BTN[length])
            btn.callback = self._length_cb(length)
            self.add_item(btn)

    def _length_cb(self, chosen_length: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            delivery_label = f"{self._chosen_delivery} {chosen_length}"
            self.game.pending_delivery = self._chosen_delivery
            self.game.pending_delivery_label = delivery_label
            self.game.pending_delivery_speed_mod = 0.0
            if self.game.batting_user_id != BOT_USER_ID:
                self.clear_items()
                for i, shot in enumerate(BATTING_SHOTS):
                    btn = discord.ui.Button(
                        label=shot,
                        style=BattingView.STYLES.get(
                            shot, discord.ButtonStyle.secondary
                        ),
                        row=i // 4,
                    )
                    btn.callback = self._make_shot_cb(shot)
                    self.add_item(btn)
                await interaction.response.edit_message(
                    content=(
                        f"🎯 **{self.game.current_bowler.name}** bowls **{delivery_label}**! "
                        f"{self.game.batting_user.mention} choose your shot:"
                    ),
                    view=self,
                )
            else:
                await interaction.response.edit_message(
                    content=f"🎯 **{self.game.current_bowler.name}** bowls **{delivery_label}**!",
                    view=None,
                )

        return cb

    async def start(self, interaction: discord.Interaction, delivery: str):
        self._chosen_delivery = delivery
        for item in self.children:
            item.disabled = False
        try:
            await interaction.response.edit_message(
                content=f"\n\n---\n\n✅ **{delivery}** selected!\n**{self.game.bowling_user.mention} choose your length:**",
                view=self,
            )
        except (discord.NotFound, discord.HTTPException):
            pass

    def _make_shot_cb(self, shot: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            try:
                await interaction.response.edit_message(
                    content=f"🏏 **{shot}**", view=None
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            self.stop()
            try:
                await process_delivery(interaction, self.game, shot)
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[views] Shot error interaction expired: %s", e)
                try:
                    await interaction.channel.send(
                        "⏳ UI Expired. Use `/csrecover` to continue."
                    )
                except Exception:
                    pass
            except Exception as e:
                log.exception("[views] Shot error: %s", e)
                try:
                    await interaction.channel.send(
                        "⚠️ Something went wrong processing that ball. "
                        "The match is still active — use `/csrecover` if it doesn't continue."
                    )
                except Exception:
                    pass

        return cb


class OrthodoxBattingView(MatchView):
    STYLES = {
        "Drive": discord.ButtonStyle.success,
        "Pull": discord.ButtonStyle.danger,
        "Cut": discord.ButtonStyle.primary,
        "Sweep": discord.ButtonStyle.secondary,
        "Defend": discord.ButtonStyle.secondary,
        "Lofted": discord.ButtonStyle.danger,
        "Reverse-Sweep": discord.ButtonStyle.primary,
        "Back-foot": discord.ButtonStyle.secondary,
        "Front-foot": discord.ButtonStyle.success,
    }

    def __init__(self, game: GameState, delivery: str, length: str, shots: list[str]):
        super().__init__()
        self.game = game
        self.delivery = delivery
        self.length = length
        for shot in shots:
            btn = discord.ui.Button(
                label=shot, style=self.STYLES.get(shot, discord.ButtonStyle.secondary)
            )
            btn.callback = self._make_cb(shot)
            self.add_item(btn)

    def _make_cb(self, shot: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            # Edit the message to remove the view before processing the delivery
            try:
                await interaction.response.edit_message(
                    content=f"🏏 **{shot}**", view=None
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            self.stop()  # Stop the view's internal timer
            try:
                await process_delivery(interaction, self.game, shot)
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[views] Orthodox batting error interaction expired: %s", e)
                try:
                    await interaction.channel.send(
                        "⏳ UI Expired. Use `/csrecover` to continue."
                    )
                except Exception:
                    pass
            except Exception as e:
                log.exception("[views] Orthodox batting error: %s", e)
                try:
                    await interaction.channel.send(
                        "⚠️ Something went wrong processing that ball. "
                        "The match is still active — use `/csrecover` if it doesn't continue."
                    )
                except Exception:
                    pass

        return cb


class BattingView(MatchView):

    STYLES = {
        "Drive": discord.ButtonStyle.success,
        "Pull": discord.ButtonStyle.danger,
        "Cut": discord.ButtonStyle.primary,
        "Sweep": discord.ButtonStyle.secondary,
        "Lofted": discord.ButtonStyle.danger,
        "Flick": discord.ButtonStyle.success,
        "Defend": discord.ButtonStyle.secondary,
        "Reverse-Sweep": discord.ButtonStyle.primary,
        "Back-foot": discord.ButtonStyle.secondary,
        "Front-foot": discord.ButtonStyle.success,
    }

    def __init__(self, game: GameState):
        super().__init__()
        self.game = game
        for i, shot in enumerate(BATTING_SHOTS):
            btn = discord.ui.Button(
                label=shot,
                style=self.STYLES.get(shot, discord.ButtonStyle.secondary),
                row=i // 4,
            )
            btn.callback = self._make_cb(shot)
            self.add_item(btn)

    def _make_cb(self, shot: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Not your turn!", ephemeral=True
                        )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            # Edit the message to remove the view before processing the delivery
            try:
                await interaction.response.edit_message(
                    content=f"🏏 **{shot}**", view=None
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            self.stop()  # Stop the view's internal timer
            try:
                await process_delivery(interaction, self.game, shot)
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[views] Batting error interaction expired: %s", e)
                try:
                    await interaction.channel.send(
                        "⏳ UI Expired. Use `/csrecover` to continue."
                    )
                except Exception:
                    pass
            except Exception as e:
                log.exception("[views] Batting error: %s", e)
                try:
                    await interaction.channel.send(
                        "⚠️ Something went wrong processing that ball. "
                        "The match is still active — use `/csrecover` if it doesn't continue."
                    )
                except Exception:
                    pass

        return cb


# ── Engine Core ───────────────────────────────────────────────────────────────


async def process_delivery(
    interaction: discord.Interaction, game: GameState, shot: str
) -> None:
    """
    Called when a human player clicks a shot button.
    Resolves the ball, applies analytics, and advances the match state.
    """
    channel = interaction.channel
    delivery = game.pending_delivery
    bowler = game.current_bowler
    striker = game.striker

    if not delivery or not bowler or not striker:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Game state error.", ephemeral=True
                )
            else:
                await channel.send("Game state error — use `/csrecover`.")
        except (discord.NotFound, discord.HTTPException):
            pass
        return
    speed_mod = game.pending_delivery_speed_mod
    label = game.pending_delivery_label or delivery
    # Clear pending so the delivery can't be replayed
    game.pending_delivery = None
    game.pending_delivery_label = None
    # IMPORTANT: preserve speed_mod before clearing it.
    game.pending_delivery_speed_mod = 0.0

    # Delegate to the shared logic
    await _process_delivery_logic(
        interaction, channel, game, shot, delivery, bowler, striker, speed_mod, label
    )


async def _process_delivery_logic(
    interaction: discord.Interaction,
    channel: discord.abc.Messageable,
    game: GameState,
    shot: str,
    delivery: str,
    bowler: MatchPlayer,
    striker: MatchPlayer,
    speed_mod: float,
    delivery_label: str,
) -> None:
    """Helper function for the core delivery processing logic."""

    speed = delivery_speed(delivery, speed_mod)
    outcome_dict = calculate_outcome(
        delivery=delivery,
        shot=shot,
        bowler_ovr=getattr(bowler, "match_bowl", bowler.bowl),
        batsman_ovr=getattr(striker, "match_bat", striker.bat),
        bat_rarity=getattr(striker, "rarity", 1.0),
        bowl_rarity=getattr(bowler, "rarity", 1.0),
        bat_power=striker.bat,
        bowl_power=bowler.bowl,
        batter_runs=game.batsman_stats.get(game._player_key(striker), {}).get("r", 0),
        bat_style=getattr(striker, "bat_style", "balanced"),
        striker_role=getattr(striker, "role", "Bat"),
        bowler_role=getattr(bowler, "role", "Bowl"),
        memories_type=getattr(striker, "memories_type", None),
        fielder_grade=getattr(bowler, "fielder_grade", 1),
        narrative_attrs=getattr(striker, "narrative_attrs", []),
        free_hit=game.last_was_no_ball,
    )

    free_hit_note = (
        "\n🟡 **FREE HIT** — the wicket does NOT count!"
        if game.last_was_no_ball and not outcome_dict.get("free_hit_saved", False)
        else ""
    )

    if not hasattr(game, "bowling_team_reviews"):
        game.bowling_team_reviews = {}
    if not hasattr(game, "batting_team_reviews"):
        game.batting_team_reviews = {}

    outcome_dict = apply_momentum(outcome_dict, game, striker, delivery)
    commentary = build_ball_commentary(
        bowler_name=bowler.name,
        bowler_ovr=bowler.ovr,
        delivery=delivery,
        speed_kmph=speed,
        batsman_name=striker.name,
        shot=shot,
        outcome=outcome_dict,
        bowling_type=bowler.bowling_type,
        delivery_label=delivery_label,
    )
    if outcome_dict["timing_label"]:
        pass  # timing label suppressed from commentary text (kept in outcome_dict for logic)
    dismissal = outcome_dict["dismissal_type"]
    free_hit_saved = outcome_dict.get("free_hit_saved", False)
    is_direct_hit = outcome_dict.get("is_direct_hit", False)
    fielder_name = outcome_dict.get("fielder_name")
    bowling_team = game.teams.get(game.bowling_user_id, [])
    keeper = next((p for p in bowling_team if p.role and p.role.upper() == "WK"), None)
    if dismissal == "Caught" and not free_hit_saved:
        if not fielder_name or fielder_name == "Fielder":
            candidates = [
                p
                for p in bowling_team
                if p != game.current_bowler
                and p != keeper
                and (not p.role or p.role.upper() != "WK")
            ]
            if candidates:
                fielder_name = random.choice(candidates).name
            elif keeper:
                fielder_name = keeper.name
            else:
                fielder_name = "Fielder"
        commentary += f"\n❌ Dismissal: **{dismissal}** by **{fielder_name}**!"
    elif dismissal == "Stumped" and not free_hit_saved:
        fielder_name = keeper.name if keeper else "Keeper"
        commentary += f"\n❌ Dismissal: **{dismissal}** by **{fielder_name}**!"
    elif dismissal == "Run Out" and not free_hit_saved:
        if not fielder_name or fielder_name == "Fielder":
            candidates = [p for p in bowling_team if p != game.current_bowler]
            if candidates:
                fielder_name = random.choice(candidates).name
            else:
                fielder_name = "Fielder"
        if is_direct_hit:
            commentary += f"\n📺 **3rd Umpire Decision:** Direct hit by **{fielder_name}**! **OUT!**"
        else:
            commentary += f"\n📺 **3rd Umpire Decision:** **{fielder_name}** runs him out! **OUT!**"
    elif dismissal and not free_hit_saved:
        commentary += f"\n❌ Dismissal: **{dismissal}**"

    # ── DRS routing flags ──
    drs_eligible_wicket = outcome_dict.get("drs_eligible_wicket", False)
    drs_eligible_nb = outcome_dict.get("drs_eligible_nb", False)
    umpire_out = outcome_dict["umpire_given_out"]
    umpire_nb = outcome_dict["umpire_called_nb"]
    actual_out = outcome_dict["actual_is_out"]
    is_run_out = dismissal == "Run Out"
    is_lbw = dismissal == "LBW"
    is_caught = dismissal == "Caught"
    is_stumped = dismissal == "Stumped"
    is_hit_wicket = dismissal == "Hit Wicket"
    is_obstructing = dismissal in ("Obstructing", "Obstructing the Field")
    free_hit_saved = outcome_dict.get("free_hit_saved", False)

    # On a free hit, wicket is automatically nullified (except run out) - no DRS needed
    if free_hit_saved and not is_run_out:
        game.apply_ball_result(outcome_dict, batsman=striker)
        await _record_ball_analytics(game, outcome_dict, shot=shot)
        runs_ran = outcome_dict["runs_batter"] + outcome_dict.get("overthrows", 0)
        if outcome_dict.get("extra_type") in ("B", "LB"):
            runs_ran = outcome_dict.get("runs_extras", 0)
        if runs_ran % 2 != 0:
            game.rotate_strike()
        await channel.send(
            content=commentary + "\n🟡 **FREE HIT** — the wicket does NOT count!"
        )
        await _handle_dismissal_after_result(
            channel, game, striker, outcome_dict, free_hit_saved=True
        )
        return

    # Determine which team can review
    if is_run_out or is_hit_wicket or is_obstructing:
        # Run-out/Hit Wicket/Obstructing: either team can review
        # In real cricket, fielding team reviews NOT OUT, batting team reviews OUT
        is_wicket_appeal = umpire_out
        eligible_reviewing_team = (
            game.batting_user_id if umpire_out else game.bowling_user_id
        )
    elif is_caught or is_stumped:
        is_wicket_appeal = umpire_out
        eligible_reviewing_team = (
            game.batting_user_id if umpire_out else game.bowling_user_id
        )
    elif is_lbw:
        is_wicket_appeal = umpire_out or (
            drs_eligible_wicket and not umpire_out and actual_out is not None
        )
        eligible_reviewing_team = (
            game.batting_user_id if umpire_out else game.bowling_user_id
        )
    else:
        is_wicket_appeal = umpire_out
        eligible_reviewing_team = (
            game.batting_user_id if umpire_out else game.bowling_user_id
        )

    if drs_eligible_nb:
        is_nb_appeal = random.random() < (0.04 if umpire_out else 0.20)
    else:
        is_nb_appeal = False

    # ── Apply result directly if no DRS available or not eligible ──
    if (not drs_eligible_wicket or not is_wicket_appeal) and not is_nb_appeal:
        game.apply_ball_result(outcome_dict, batsman=striker)
        await _record_ball_analytics(game, outcome_dict, shot=shot)
        runs_ran = outcome_dict["runs_batter"] + outcome_dict.get("overthrows", 0)
        if outcome_dict.get("extra_type") in ("B", "LB"):
            runs_ran = outcome_dict.get("runs_extras", 0)
        if runs_ran % 2 != 0:
            game.rotate_strike()
        await channel.send(content=commentary + free_hit_note)
        await _handle_dismissal_after_result(
            channel, game, striker, outcome_dict, free_hit_saved
        )
        return

    # ── Check if reviews are available ──
    review_count = (
        game.batting_team_reviews.get(eligible_reviewing_team, 1)
        if eligible_reviewing_team == game.batting_user_id
        else game.bowling_team_reviews.get(eligible_reviewing_team, 1)
    )
    if review_count <= 0:
        # No reviews left - go with umpire's call
        game.apply_ball_result(outcome_dict, batsman=striker)
        await _record_ball_analytics(game, outcome_dict, shot=shot)
        runs_ran = outcome_dict["runs_batter"] + outcome_dict.get("overthrows", 0)
        if outcome_dict.get("extra_type") in ("B", "LB"):
            runs_ran = outcome_dict.get("runs_extras", 0)
        if runs_ran % 2 != 0:
            game.rotate_strike()
        await channel.send(content=commentary + free_hit_note)
        await _handle_dismissal_after_result(
            channel, game, striker, outcome_dict, free_hit_saved
        )
        return

    # ── DRS views ──
    await channel.send(content=commentary + free_hit_note)

    # Determine review type and embed
    if umpire_out and is_lbw:
        embed = discord.Embed(
            title="🟥 Umpire signals OUT!",
            description="Given out for LBW!\n*Will the Captain take a review?*",
            color=discord.Color.red(),
        )
        review_type = "WICKET"
    elif umpire_out and is_caught:
        embed = discord.Embed(
            title="🟥 Umpire signals OUT!",
            description="Given out Caught!\n*Will the Captain take a review?*",
            color=discord.Color.red(),
        )
        review_type = "WICKET"
    elif umpire_out and is_stumped:
        embed = discord.Embed(
            title="🟥 Umpire signals OUT!",
            description="Given out Stumped!\n*Will the Captain take a review?*",
            color=discord.Color.red(),
        )
        review_type = "WICKET"
    elif umpire_out and is_run_out:
        embed = discord.Embed(
            title="🟥 Umpire signals OUT!",
            description="Given out Run Out!\n*Will the Captain take a review?*",
            color=discord.Color.red(),
        )
        review_type = "WICKET"
    elif umpire_out and is_hit_wicket:
        embed = discord.Embed(
            title="🟥 Umpire signals OUT!",
            description="Given out Hit Wicket!\n*Will the Captain take a review?*",
            color=discord.Color.red(),
        )
        review_type = "WICKET"
    elif umpire_out and is_obstructing:
        embed = discord.Embed(
            title="🟥 Umpire signals OUT!",
            description="Given out Obstructing the Field!\n*Will the Captain take a review?*",
            color=discord.Color.red(),
        )
        review_type = "WICKET"
    elif not umpire_out and drs_eligible_wicket:
        if is_lbw:
            embed = discord.Embed(
                title="📢 LOUD APPEAL!",
                description="Umpire says NOT OUT for LBW!\n*Will the bowling team take a review?*",
                color=discord.Color.blue(),
            )
        elif is_caught:
            embed = discord.Embed(
                title="📢 LOUD APPEAL!",
                description="Umpire says NOT OUT Caught!\n*Will the bowling team take a review?*",
                color=discord.Color.blue(),
            )
        elif is_stumped:
            embed = discord.Embed(
                title="📢 LOUD APPEAL!",
                description="Umpire says NOT OUT Stumped!\n*Will the bowling team take a review?*",
                color=discord.Color.blue(),
            )
        elif is_run_out:
            embed = discord.Embed(
                title="📢 LOUD APPEAL!",
                description="Umpire says NOT OUT Run Out!\n*Will the bowling team take a review?*",
                color=discord.Color.blue(),
            )
        else:
            embed = discord.Embed(
                title="📢 LOUD APPEAL!",
                description="Umpire says NOT OUT!\n*Will the bowling team take a review?*",
                color=discord.Color.blue(),
            )
        review_type = "WICKET"
    elif umpire_nb:
        team_id = game.bowling_user_id
        if game.bowling_team_reviews.get(team_id, 1) > 0:
            embed = discord.Embed(
                title="🟧 Umpire signals NO BALL!",
                description="*Will the bowling team challenge?*",
                color=discord.Color.orange(),
            )
            view = DRSReviewView(game, outcome_dict, team_id, "NO_BALL")
            message = await channel.send(embed=embed, view=view)
            view.message = message
            return
        else:
            await _finalise_wicket(game, channel, umpire_out, outcome_dict)
            return
    elif is_nb_appeal:
        team_id = game.batting_user_id if not umpire_nb else game.bowling_user_id
        if (
            team_id == game.batting_user_id
            and game.batting_team_reviews.get(team_id, 1) > 0
        ):
            embed = discord.Embed(
                title="🟡 Suspicious Delivery...",
                description="*Will the batting team review for a No Ball?*",
                color=discord.Color.yellow(),
            )
            view = DRSReviewView(game, outcome_dict, team_id, "NO_BALL")
            message = await channel.send(embed=embed, view=view)
            view.message = message
            return
        elif (
            team_id == game.bowling_user_id
            and game.bowling_team_reviews.get(team_id, 1) > 0
        ):
            embed = discord.Embed(
                title="🟧 Umpire signals NO BALL!",
                description="*Will the bowling team challenge?*",
                color=discord.Color.orange(),
            )
            view = DRSReviewView(game, outcome_dict, team_id, "NO_BALL")
            message = await channel.send(embed=embed, view=view)
            view.message = message
            return
        else:
            await _finalise_wicket(game, channel, umpire_out, outcome_dict)
            return
    else:
        await _finalise_wicket(game, channel, umpire_out, outcome_dict)
        return

    view = DRSReviewView(game, outcome_dict, eligible_reviewing_team, review_type)
    message = await channel.send(embed=embed, view=view)
    view.message = message


async def resolve_delivery_for_bot(
    game: GameState,
    channel: discord.abc.Messageable,
    delivery: str,
    delivery_label: str,
    speed_mod: float,
    shot: str,
) -> None:
    """
    Identical resolution logic to process_delivery but for bot turns (no interaction).
    Called by bot_match.py Runner after both bot choices are made.
    """
    bowler = game.current_bowler
    striker = game.striker
    if not delivery or not bowler or not striker:
        return

    speed = delivery_speed(delivery, speed_mod)
    outcome_dict = calculate_outcome(
        delivery=delivery,
        shot=shot,
        bowler_ovr=getattr(bowler, "match_bowl", bowler.bowl),
        batsman_ovr=getattr(striker, "match_bat", striker.bat),
        bat_rarity=getattr(striker, "rarity", 1.0),
        bowl_rarity=getattr(bowler, "rarity", 1.0),
        bat_power=striker.bat,
        bowl_power=bowler.bowl,
        batter_runs=game.batsman_stats.get(game._player_key(striker), {}).get("r", 0),
        bat_style=getattr(striker, "bat_style", "balanced"),
        striker_role=getattr(striker, "role", "Bat"),
        bowler_role=getattr(bowler, "role", "Bowl"),
        memories_type=getattr(striker, "memories_type", None),
        fielder_grade=getattr(bowler, "fielder_grade", 1),
        narrative_attrs=getattr(striker, "narrative_attrs", []),
        free_hit=game.last_was_no_ball,
    )

    if not hasattr(game, "bowling_team_reviews"):
        game.bowling_team_reviews = {}
    if not hasattr(game, "batting_team_reviews"):
        game.batting_team_reviews = {}

    outcome_dict = apply_momentum(outcome_dict, game, striker, delivery)
    commentary = build_ball_commentary(
        bowler_name=bowler.name,
        bowler_ovr=bowler.ovr,
        delivery=delivery,
        speed_kmph=speed,
        batsman_name=striker.name,
        shot=shot,
        outcome=outcome_dict,
        bowling_type=bowler.bowling_type,
        delivery_label=delivery_label or delivery,
    )
    if outcome_dict["timing_label"]:
        pass  # timing label suppressed from commentary text (kept in outcome_dict for logic)
    dismissal = outcome_dict["dismissal_type"]
    free_hit_saved = outcome_dict.get("free_hit_saved", False)
    is_direct_hit = outcome_dict.get("is_direct_hit", False)
    fielder_name = outcome_dict.get("fielder_name")
    bowling_team = game.teams.get(game.bowling_user_id, [])
    keeper = next((p for p in bowling_team if p.role and p.role.upper() == "WK"), None)
    if dismissal == "Caught" and not free_hit_saved:
        if not fielder_name or fielder_name == "Fielder":
            candidates = [
                p
                for p in bowling_team
                if p != game.current_bowler
                and p != keeper
                and (not p.role or p.role.upper() != "WK")
            ]
            if candidates:
                fielder_name = random.choice(candidates).name
            elif keeper:
                fielder_name = keeper.name
            else:
                fielder_name = "Fielder"
        commentary += f"\n❌ Dismissal: **{dismissal}** by **{fielder_name}**!"
    elif dismissal == "Stumped" and not free_hit_saved:
        fielder_name = keeper.name if keeper else "Keeper"
        commentary += f"\n❌ Dismissal: **{dismissal}** by **{fielder_name}**!"
    elif dismissal == "Run Out" and not free_hit_saved:
        if not fielder_name or fielder_name == "Fielder":
            candidates = [p for p in bowling_team if p != game.current_bowler]
            if candidates:
                fielder_name = random.choice(candidates).name
            else:
                fielder_name = "Fielder"
        if is_direct_hit:
            commentary += f"\n📺 **3rd Umpire Decision:** Direct hit by **{fielder_name}**! **OUT!**"
        else:
            commentary += f"\n📺 **3rd Umpire Decision:** **{fielder_name}** runs him out! **OUT!**"
    elif dismissal and not free_hit_saved:
        commentary += f"\n❌ Dismissal: **{dismissal}**"

    game.apply_ball_result(outcome_dict, batsman=striker)
    await _record_ball_analytics(game, outcome_dict, shot=shot)

    runs_ran = outcome_dict["runs_batter"] + outcome_dict.get("overthrows", 0)
    if outcome_dict.get("extra_type") in ("B", "LB"):
        runs_ran = outcome_dict.get("runs_extras", 0)
    if runs_ran % 2 != 0:
        game.rotate_strike()

    await channel.send(content=commentary)

    # Show dismissal image for bot matches
    if (
        outcome_dict.get("dismissal_type")
        and outcome_dict.get("actual_is_out")
        and not (
            outcome_dict.get("free_hit_saved")
            and outcome_dict["dismissal_type"] != "Run Out"
        )
    ):
        asyncio.create_task(
            send_batter_dismissal_image(
                channel=channel,
                batter=striker,
                game=game,
                kind=outcome_dict["dismissal_type"],
                bowler=game.current_bowler.name if game.current_bowler else None,
                fielder=fielder_name,
                is_captain=(striker == game.get_captain(game.batting_user_id)),
            )
        )

    # Return dismissal info so Runner can handle next batsman
    return outcome_dict


async def _handle_dismissal_after_result(
    channel, game, striker, outcome_dict, free_hit_saved=False
):
    """Handle dismissal image and next batsman prompt after applying ball result."""
    dismissal_type = outcome_dict.get("dismissal_type")
    actual_out = outcome_dict.get("actual_is_out", False)
    is_run_out = dismissal_type == "Run Out"

    if (
        dismissal_type
        and actual_out
        and not (free_hit_saved and dismissal_type != "Run Out")
    ):
        fielder_name = outcome_dict.get("fielder_name")
        if is_run_out:
            bowling_team = game.teams.get(game.bowling_user_id, [])
            if not fielder_name or fielder_name == "Fielder":
                candidates = [p for p in bowling_team if p != game.current_bowler]
                if candidates:
                    fielder_name = random.choice(candidates).name
                else:
                    fielder_name = "Fielder"
        asyncio.create_task(
            send_batter_dismissal_image(
                channel=channel,
                batter=striker,
                game=game,
                kind=dismissal_type,
                bowler=game.current_bowler.name if game.current_bowler else None,
                fielder=fielder_name,
                is_captain=(striker == game.get_captain(game.batting_user_id)),
            )
        )

    needs_replacement = (
        dismissal_type
        and actual_out
        and not (free_hit_saved and dismissal_type != "Run Out")
    )
    if needs_replacement:
        if game.is_innings_over():
            await _check_end_of_over_or_innings(channel, game)
            return
        avail = game.get_available_batsmen()
        if avail:
            view = NextBatsmanView(game, avail)
            message = await channel.send(
                content=f"🏏 **Wicket!** {game.batting_user.mention} choose your next batsman:",
                view=view,
            )
            view.message = message
            return
    await _check_end_of_over_or_innings(channel, game)


async def _finalise_wicket(
    game: GameState, channel: discord.abc.Messageable, upheld: bool, outcome_dict: dict
) -> None:
    outcome_dict["actual_is_out"] = upheld
    outcome_dict["umpire_given_out"] = upheld
    if not upheld:
        # When OUT is overturned, dismissal is cancelled but runs off the bat still count!
        # The outcome_str and runs_batter should preserve what was actually scored
        outcome_dict["dismissal_type"] = None
    dismissed_player = game.striker
    game.apply_ball_result(outcome_dict, batsman=game.striker)
    await _record_ball_analytics(game, outcome_dict)
    runs_ran = outcome_dict["runs_batter"] + outcome_dict.get("overthrows", 0)
    if runs_ran % 2 != 0:
        game.rotate_strike()
    if upheld:
        await _handle_dismissal_after_result(
            channel, game, dismissed_player, outcome_dict
        )
        return
    await _next_prompt_after_advance(game, channel)


# ── Stats Persistence ─────────────────────────────────────────────────────────


async def _save_match_stats(game: GameState, bot=None) -> None:
    try:
        if bot is None:
            bot = getattr(game, "bot", None)
        if bot is None or getattr(bot, "career_stats", None) is None:
            return

        game._set_winner()
        stats_manager = bot.career_stats
        user_match_stats = {}  # {user_id: {runs: x, ...}}

        # 1. Aggregate stats for each card and user
        for team_user_id, team_players in game.teams.items():
            if team_user_id not in user_match_stats:
                user_match_stats[team_user_id] = {
                    "runs": 0,
                    "wickets": 0,
                    "matches_played": 0,
                    "matches_won": 0,
                    "matches_lost": 0,
                    "total_runs_conceded": 0,
                    "total_maiden_overs_bowled": 0,
                    "total_overs_bowled": 0,
                    "total_balls_faced": 0,
                    "total_fours": 0,
                    "total_sixes": 0,
                    "best_batting_innings_score": 0,
                    "best_bowling_wickets": 0,
                    "best_bowling_runs": 999,
                    "half_centuries": 0,
                    "centuries": 0,
                    "double_centuries": 0,
                    "five_wicket_hauls": 0,
                }

            is_winner = game.winner_id == team_user_id
            user_match_stats[team_user_id]["matches_played"] = 1
            if is_winner:
                user_match_stats[team_user_id]["matches_won"] = 1
            else:
                user_match_stats[team_user_id]["matches_lost"] = 1

            for mp in team_players:
                if not getattr(mp, "inst_pk", 0):
                    continue

                card_runs, card_hs, card_50s, card_100s, card_200s = 0, 0, 0, 0, 0
                card_wkts, card_runs_conceded, card_maidens, card_5w = 0, 0, 0, 0
                card_fours, card_sixes, card_balls, card_balls_bowled = 0, 0, 0, 0

                player_key = game._player_key(mp)
                batted = player_key in game.batsman_stats
                bowled = mp.name in game.bowler_stats

                # ---- Batting stats ----
                if batted:
                    b_stats = game.batsman_stats[player_key]
                    card_runs = b_stats.get("r", 0)
                    card_fours = b_stats.get("4s", 0)
                    card_sixes = b_stats.get("6s", 0)
                    card_balls = b_stats.get("b", 0)
                    card_hs = card_runs
                    if card_runs >= 200:
                        card_200s = 1
                    elif card_runs >= 100:
                        card_100s = 1
                    elif card_runs >= 50:
                        card_50s = 1

                    user_match_stats[team_user_id]["runs"] += card_runs
                    user_match_stats[team_user_id]["total_balls_faced"] += card_balls
                    user_match_stats[team_user_id]["total_fours"] += card_fours
                    user_match_stats[team_user_id]["total_sixes"] += card_sixes
                    user_match_stats[team_user_id]["best_batting_innings_score"] = max(
                        user_match_stats[team_user_id]["best_batting_innings_score"],
                        card_runs,
                    )
                    user_match_stats[team_user_id]["half_centuries"] += card_50s
                    user_match_stats[team_user_id]["centuries"] += card_100s
                    user_match_stats[team_user_id]["double_centuries"] += card_200s

                # ---- Bowling stats ----
                if bowled:
                    bw_stats = game.bowler_stats[mp.name]
                    card_wkts = bw_stats.get("w", 0)
                    card_runs_conceded = bw_stats.get("r", 0)
                    card_maidens = bw_stats.get("m", 0)
                    card_balls_bowled = bw_stats.get("b", 0)
                    card_overs_bowled = round(card_balls_bowled / 6.0, 1)
                    if card_wkts >= 5:
                        card_5w = 1

                    user_match_stats[team_user_id]["wickets"] += card_wkts
                    user_match_stats[team_user_id][
                        "total_runs_conceded"
                    ] += card_runs_conceded
                    user_match_stats[team_user_id][
                        "total_maiden_overs_bowled"
                    ] += card_maidens
                    user_match_stats[team_user_id][
                        "total_overs_bowled"
                    ] += card_overs_bowled
                    user_match_stats[team_user_id]["five_wicket_hauls"] += card_5w

                    # Update user best bowling for this match
                    if (
                        card_wkts
                        > user_match_stats[team_user_id]["best_bowling_wickets"]
                    ):
                        user_match_stats[team_user_id][
                            "best_bowling_wickets"
                        ] = card_wkts
                        user_match_stats[team_user_id][
                            "best_bowling_runs"
                        ] = card_runs_conceded
                    elif (
                        card_wkts
                        == user_match_stats[team_user_id]["best_bowling_wickets"]
                        and card_runs_conceded
                        < user_match_stats[team_user_id]["best_bowling_runs"]
                    ):
                        user_match_stats[team_user_id][
                            "best_bowling_runs"
                        ] = card_runs_conceded

                # Update card stats once with all data, only if they played
                if batted or bowled:
                    await stats_manager.update_card_stats(
                        card_instance_id=mp.inst_pk,
                        matches_played=1,
                        runs=card_runs,
                        wickets=card_wkts,
                        highest_score=card_hs,
                        half_centuries=card_50s,
                        centuries=card_100s,
                        double_centuries=card_200s,
                        five_wicket_hauls=card_5w,
                        best_bowling_wickets=card_wkts or None,
                        best_bowling_runs=card_runs_conceded if card_wkts else None,
                        best_bowling_overs=(
                            round(card_balls_bowled / 6.0, 1) if card_wkts else None
                        ),
                        maiden_overs_bowled=card_maidens,
                        overs_bowled=round(card_balls_bowled / 6.0, 1),
                        runs_conceded_total=card_runs_conceded,
                        fours=card_fours,
                        sixes=card_sixes,
                        total_balls_faced=card_balls,
                    )

        # 2. Update user career stats (skip bot user)
        for user_id, u_stats in user_match_stats.items():
            if user_id == BOT_USER_ID:
                continue
            if not any(
                u_stats.get(k, 0)
                for k in (
                    "runs",
                    "wickets",
                    "matches_played",
                    "total_runs_conceded",
                    "total_maiden_overs_bowled",
                    "total_overs_bowled",
                    "total_fours",
                    "total_sixes",
                    "half_centuries",
                    "centuries",
                    "double_centuries",
                    "five_wicket_hauls",
                )
            ) and not u_stats.get("best_bowling_wickets"):
                continue
            await stats_manager.update_user_stats(
                user_id=user_id,
                matches_played=u_stats["matches_played"],
                matches_won=u_stats["matches_won"],
                matches_lost=u_stats["matches_lost"],
                runs=u_stats["runs"],
                wickets=u_stats["wickets"],
                total_runs_conceded=u_stats["total_runs_conceded"],
                total_maiden_overs_bowled=u_stats["total_maiden_overs_bowled"],
                total_overs_bowled=u_stats["total_overs_bowled"],
                total_balls_faced=u_stats["total_balls_faced"],
                total_fours=u_stats["total_fours"],
                total_sixes=u_stats["total_sixes"],
                five_wicket_hauls=u_stats["five_wicket_hauls"],
                half_centuries=u_stats["half_centuries"],
                centuries=u_stats["centuries"],
                double_centuries=u_stats["double_centuries"],
                best_batting_innings_score=u_stats["best_batting_innings_score"],
                best_bowling_wickets=u_stats["best_bowling_wickets"] or None,
                best_bowling_runs=(
                    u_stats["best_bowling_runs"]
                    if u_stats["best_bowling_wickets"]
                    else None
                ),
            )

        # 3. Populate innings summary stats (for match summary and card intros)
        game.populate_summary_stats()

    except Exception as exc:
        log.error("[views] _save_match_stats failed: %s", exc)


async def _next_prompt_after_advance(
    game: GameState, channel: discord.abc.Messageable, bot=None
) -> None:
    await _check_end_of_over_or_innings(channel, game, bot=bot)


async def _check_end_of_over_or_innings(
    channel: discord.abc.Messageable, game: GameState, bot=None
) -> None:
    if game.is_innings_over():
        if game.innings == 1:
            await _do_innings_break(channel, game, bot=bot)
        else:
            await _save_match_stats(game, bot=bot)
            game.populate_summary_stats()
            game.match_finished = True
            active_games.pop(game.channel_id, None)
            await channel.send(
                embed=generate_tv_broadcast_summary(
                    game, is_final=True, innings_summary=None
                )
            )
        return

    # End of over: must check this BEFORE the striker-None guard,
    # because a wicket on the last ball sets striker=None but the over still ends.
    if game.current_over_balls >= 6:
        game.end_over()
        # After end_over(), if striker is still None (shouldn't happen with end_over fix),
        # wait for the new batsman instead of prompting for a bowler.
        if game.striker is None:
            return
        if game.non_striker is None:
            avail = game.get_available_batsmen()
            if avail:
                view = NextBatsmanView(game, avail)
                msg = await channel.send(
                    content=f"🏏 **New batsman needed!** {game.batting_user.mention} choose your next batsman:",
                    embed=_build_scoreboard_embed(game),
                    view=view,
                )
                view.message = msg
                return
        avail_bowlers = game.get_available_bowlers()
        if avail_bowlers and game.bowling_user_id != BOT_USER_ID:
            nview = NextBowlerView(game, avail_bowlers)
            msg = await channel.send(
                content=f"✅ **End of over!** {game.bowling_user.mention} choose your next bowler:",
                embed=_build_scoreboard_embed(game),
                view=nview,
            )
            nview.message = msg
            return
        if game.bowling_user_id != BOT_USER_ID:
            await channel.send(embed=_build_scoreboard_embed(game))
        return

    # If a batsman has not yet been chosen after a wicket, don't prompt anything else —
    # NextBatsmanView's own callback will resume the flow once a replacement is picked.
    if game.striker is None:
        return

    if game.pending_delivery is not None:
        if game.batting_user_id != BOT_USER_ID:
            await channel.send(
                content=(
                    f"🏏 {game.batting_user.mention} "
                    f"**{game.striker.name if game.striker else '?'}**, play your shot!\n"
                    f"*(Delivery: **{game.pending_delivery_label or game.pending_delivery}**)*"
                ),
                embed=_build_scoreboard_embed(game),
                view=BattingView(game),
            )
        return

    if game.current_bowler is not None:
        if game.batting_user_id != BOT_USER_ID and game.bowling_user_id != BOT_USER_ID:
            await send_bowling_prompt(channel, game)
        return

    if game.bowling_user_id != BOT_USER_ID:
        avail = game.get_available_bowlers()
        if avail:
            await channel.send(
                content=f"✅ {game.bowling_user.mention} choose your next bowler:",
                view=NextBowlerView(game, avail),
            )
        return


# ── Post-Ball Views ───────────────────────────────────────────────────────────


class NextBatsmanView(MatchView):
    def __init__(self, game: GameState, avail: list[MatchPlayer]):
        super().__init__()
        self.game, self._avail = game, avail
        options = [
            discord.SelectOption(
                label=p.name[:25],
                value=str(i),
                description=f"🏏 Bat {p.bat} | OVR {p.ovr}",
            )
            for i, p in enumerate(avail)
        ]
        self._sel = discord.ui.Select(
            placeholder="🏏 Choose next batsman...", options=options
        )
        self._sel.callback = self._cb
        self.add_item(self._sel)
        # Impact button only for human batting team after over 2
        if (
            game.current_legal_balls >= 12
            and not game.swap_used.get(game.batting_user_id, False)
            and game.batting_user_id != BOT_USER_ID
        ):
            self._impact_btn = discord.ui.Button(
                label="🔄 Impact", style=discord.ButtonStyle.green, row=1
            )
            self._impact_btn.callback = self._impact_cb
            self.add_item(self._impact_btn)

    async def _impact_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.batting_user_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Only batting team selects!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        if self.game.swap_used.get(self.game.batting_user_id, False):
            try:
                await interaction.response.send_message(
                    "⚠️ Impact already used.", ephemeral=True
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        bench = await sync_to_async(_build_bench_for_user)(
            self.game, self.game.batting_user_id
        )
        if not bench:
            try:
                await interaction.response.send_message(
                    "No substitutes available.", ephemeral=True
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        self.stop()
        dismissed_idx = None
        if (
            self.game.last_ball_was_wicket
            and self.game.last_wicket_dismissed_idx >= 0
            and self.game.batting_user_id in self.game.teams
            and self.game.last_wicket_dismissed_idx
            < len(self.game.teams[self.game.batting_user_id])
        ):
            dismissed_idx = self.game.last_wicket_dismissed_idx
        try:
            if dismissed_idx is not None:
                dismissed_name = self.game.teams[self.game.batting_user_id][
                    dismissed_idx
                ].name
                await interaction.response.edit_message(
                    content=f"🔄 **Impact Substitution** — {dismissed_name} was dismissed. Choose an XI player to remove from the match.",
                    view=SubstitutionView(
                        self.game,
                        self.game.batting_user_id,
                        bench,
                        interaction.channel,
                        auto_out_idx=dismissed_idx,
                        dismissed_idx=dismissed_idx,
                    ),
                )
            else:
                await interaction.response.edit_message(
                    content="🔄 **Impact Substitution**",
                    view=SubstitutionView(
                        self.game, self.game.batting_user_id, bench, interaction.channel
                    ),
                )
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.batting_user_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Only batting team selects!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        player = self._avail[int(self._sel.values[0])]
        if self.game.striker is None:
            self.game.striker = player
        elif self.game.non_striker is None:
            self.game.non_striker = player
        else:
            self.game.striker = player
        self.game.last_ball_was_wicket = False
        self.game.last_wicket_dismissed_name = ""
        self.game.last_wicket_dismissed_idx = -1
        self.game.reset_bat_stats_for_player(player)
        over_num = (
            self.game.current_legal_balls // 6 + 1
            if self.game.current_legal_balls
            else 1
        )
        self.game.current_partnership = Partnership(
            self.game.striker.name if self.game.striker else "",
            self.game.non_striker.name if self.game.non_striker else "",
            over_num,
            batsman1_pk=self.game.striker.inst_pk if self.game.striker else 0,
            batsman2_pk=self.game.non_striker.inst_pk if self.game.non_striker else 0,
        )
        self.stop()
        try:
            await interaction.response.edit_message(
                content=f"🏏 **{player.name}** walks to the crease!", view=None
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        try:
            asyncio.create_task(
                send_batter_intro_image(
                    interaction.channel,
                    player,
                    card_stats=await _get_card_stats(player.inst_pk),
                    is_captain=(
                        player == self.game.get_captain(self.game.batting_user_id)
                    ),
                )
            )
        except Exception as e:
            log.error(f"[intro] Batter image failed: {e}")
        await _check_end_of_over_or_innings(interaction.channel, self.game)


class NextBowlerView(MatchView):
    def __init__(self, game: GameState, bowlers: list[MatchPlayer]):
        super().__init__(timeout=None)

        self.game, self._bowlers = game, bowlers
        options = [
            discord.SelectOption(
                label=p.name[:25],
                value=str(i),
                description=f"⚾ {p.effective_bowling_type()} | Bowl {p.bowl} | {game.bowler_overs_str(p.name)}",
            )
            for i, p in enumerate(bowlers)
        ]
        self._sel = discord.ui.Select(
            placeholder="⚾ Choose next bowler...", options=options
        )
        self._sel.callback = self._cb
        self.add_item(self._sel)
        if (
            game.current_legal_balls >= 12
            and not game.swap_used.get(game.bowling_user_id, False)
            and game.bowling_user_id != BOT_USER_ID
        ):
            self._impact_btn = discord.ui.Button(
                label="🔄 Impact", style=discord.ButtonStyle.green, row=1
            )
            self._impact_btn.callback = self._impact_cb
            self.add_item(self._impact_btn)

    async def _impact_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.bowling_user_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Only bowling team can use Impact!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        if self.game.swap_used.get(self.game.bowling_user_id, False):
            try:
                await interaction.response.send_message(
                    "⚠️ Impact already used.", ephemeral=True
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        bench = await sync_to_async(_build_bench_for_user)(
            self.game, self.game.bowling_user_id
        )
        if not bench:
            try:
                await interaction.response.send_message(
                    "No substitutes available.", ephemeral=True
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        self.stop()
        try:
            await interaction.response.edit_message(
                content="🔄 **Impact Substitution**",
                view=SubstitutionView(
                    self.game, self.game.bowling_user_id, bench, interaction.channel
                ),
            )
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.bowling_user_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Only bowling team picks the bowler!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        bowler = self._bowlers[int(self._sel.values[0])]
        self.game.current_bowler = bowler
        self.game._over_ended_flag = False
        self.game._ensure_bowl_stats(bowler.name)
        self.stop()
        try:
            await interaction.response.edit_message(
                content=f"✅ **{bowler.name}** starts a new over.", view=None
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        if bowler.inst_pk not in self.game.bowlers_introduced:
            self.game.bowlers_introduced.add(bowler.inst_pk)
            try:
                asyncio.create_task(
                    send_bowler_intro_image(
                        interaction.channel,
                        bowler,
                        card_stats=await _get_card_stats(bowler.inst_pk),
                        is_captain=(
                            bowler == self.game.get_captain(self.game.bowling_user_id)
                        ),
                    )
                )
            except Exception as e:
                log.error(f"[intro] Bowler image failed: {e}")
        if not getattr(self.game, "is_bot_match", False):
            await send_bowling_prompt(interaction.channel, self.game)


class DRSReviewView(MatchView):
    def __init__(
        self,
        game: GameState,
        outcome_payload: dict,
        reviewing_team_id: int,
        review_type: str = "WICKET",
    ):
        super().__init__()
        self.game = game
        self.outcome = outcome_payload
        self.reviewing_team_id = reviewing_team_id
        self.review_type = review_type
        self.is_batting_review = reviewing_team_id == game.batting_user_id
        self._finished = False

    async def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.stop()
        for child in self.children:
            child.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

    def _use_review(self):
        """Use one review for the reviewing team. Returns True if review was available."""
        team_key = self.reviewing_team_id
        if self.is_batting_review:
            current = self.game.batting_team_reviews.get(team_key, 1)
            if current <= 0:
                return False
            self.game.batting_team_reviews[team_key] = current - 1
        else:
            current = self.game.bowling_team_reviews.get(team_key, 1)
            if current <= 0:
                return False
            self.game.bowling_team_reviews[team_key] = current - 1
        return True

    def _restore_review(self):
        """Restore one review for the reviewing team (successful review)."""
        team_key = self.reviewing_team_id
        if self.is_batting_review:
            self.game.batting_team_reviews[team_key] = (
                self.game.batting_team_reviews.get(team_key, 0) + 1
            )
        else:
            self.game.bowling_team_reviews[team_key] = (
                self.game.bowling_team_reviews.get(team_key, 0) + 1
            )

    @discord.ui.button(label="Take Review (T)", style=discord.ButtonStyle.green)
    async def take_review(self, interaction: discord.Interaction, _: discord.ui.Button):
        if self._finished:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "This review has already concluded.", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        if interaction.user.id != self.reviewing_team_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"Only the {'batting' if self.is_batting_review else 'bowling'} team can review this.",
                        ephemeral=True,
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        if not self._use_review():
            try:
                await interaction.response.send_message(
                    "⚠️ Your team has no reviews remaining this innings!",
                    ephemeral=True,
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        await self._finish()
        try:
            await interaction.response.defer()
        except (discord.NotFound, discord.HTTPException):
            pass

        await interaction.channel.send(
            "📺 The Captain makes the 'T' signal!\n*Going upstairs to the Third Umpire...*"
        )

        is_actual_nb = self.outcome.get("is_actual_nb", False)
        nb_reason = self.outcome.get("nb_review_reason", "Front Foot")
        dismissal_type = self.outcome.get("dismissal_type")
        msg = await interaction.channel.send(f"🔍 Checking {nb_reason}...")

        try:
            if self.review_type == "WICKET":
                await asyncio.sleep(1.5)
                if is_actual_nb:
                    self.outcome.update(
                        {
                            "is_legal": False,
                            "extra_type": "NB",
                            "runs_extras": 1,
                            "actual_is_out": False,
                            "umpire_given_out": False,
                            "dismissal_type": None,
                            "outcome_str": (
                                f"{self.outcome['runs_batter']}NB"
                                if self.outcome["runs_batter"] > 0
                                else "NB"
                            ),
                        }
                    )
                    self._restore_review()
                    await msg.edit(
                        content="🟡 OVERSTEPPED! No Ball! Wicket overturned!"
                    )
                    await _finalise_wicket(
                        self.game, interaction.channel, False, self.outcome
                    )
                    return

                await msg.edit(content="✅ Fair delivery. Checking ball tracking...")
                await asyncio.sleep(1.0)

                # Ball tracking for LBW
                if dismissal_type == "LBW":
                    pitch = random.choices(
                        ["In Line", "Outside Leg", "Outside Off"], weights=[60, 22, 18]
                    )[0]
                    await msg.edit(content=f"📡 **Pitching:** {pitch}")
                    await asyncio.sleep(1.2)

                    if pitch == "Outside Leg":
                        # Automatic NOT OUT - pitched outside leg
                        is_actually_out = False
                        umpire_call = False
                        await msg.edit(
                            content=f"📡 **Pitching:** {pitch}\n\n📺 **DRS Result:** 🟩 **NOT OUT** — Pitched outside leg stump!"
                        )
                    else:
                        impact = random.choices(
                            ["In Line", "Umpire's Call", "Outside"],
                            weights=[50, 30, 20],
                        )[0]
                        await msg.edit(
                            content=f"📡 **Pitching:** {pitch}\n📡 **Impact:** {impact}"
                        )
                        await asyncio.sleep(1.2)

                        if impact == "Outside":
                            # Ball hitting outside off - not out for LBW
                            is_actually_out = False
                            umpire_call = False
                            await msg.edit(
                                content=f"📡 **Pitching:** {pitch}\n📡 **Impact:** {impact}\n\n📺 **DRS Result:** 🟩 **NOT OUT** — Impact outside off stump!"
                            )
                        else:
                            wickets = random.choices(
                                ["Hitting", "Umpire's Call", "Missing"],
                                weights=[45, 35, 20],
                            )[0]
                            await msg.edit(
                                content=f"📡 **Pitching:** {pitch}\n📡 **Impact:** {impact}\n📡 **Wickets:** {wickets}"
                            )
                            await asyncio.sleep(1.0)

                            if wickets == "Hitting":
                                is_actually_out = True
                                umpire_call = False
                            elif wickets == "Missing":
                                is_actually_out = False
                                umpire_call = False
                            else:
                                # Umpire's Call - marginal case
                                umpire_call = True
                                is_actually_out = self.outcome["actual_is_out"]

                            if umpire_call:
                                umpire_text = (
                                    "Umpire's Call"
                                    if self.outcome["umpire_given_out"]
                                    else "Umpire's Call"
                                )
                                if self.is_batting_review:
                                    res_text = (
                                        f"🟥 **OUT** — Umpire's Call (Original Decision Stands)!"
                                        if self.outcome["umpire_given_out"]
                                        else f"🟩 **NOT OUT** — Umpire's Call (Original Decision Stands)!"
                                    )
                                else:
                                    res_text = (
                                        f"🟥 **OUT** — Umpire's Call (Decision Overturned)!"
                                        if not self.outcome["umpire_given_out"]
                                        else f"🟩 **NOT OUT** — Umpire's Call (Decision Overturned)!"
                                    )
                                await msg.edit(
                                    content=(
                                        f"📡 **Pitching:** {pitch}\n📡 **Impact:** {impact}\n"
                                        f"📡 **Wickets:** {wickets} ({umpire_text})\n\n📺 **DRS Result:** {res_text}"
                                    )
                                )
                            else:
                                if self.is_batting_review:
                                    res_text = (
                                        "🟥 **OUT** — Original Decision Stands!"
                                        if is_actually_out
                                        else "🟩 **NOT OUT** — Decision Overturned!"
                                    )
                                else:
                                    res_text = (
                                        "🟥 **OUT** — Decision Overturned!"
                                        if is_actually_out
                                        else "🟩 **NOT OUT** — Original Decision Stands!"
                                    )
                                await msg.edit(
                                    content=(
                                        f"📡 **Pitching:** {pitch}\n📡 **Impact:** {impact}\n"
                                        f"📡 **Wickets:** {wickets}\n\n📺 **DRS Result:** {res_text}"
                                    )
                                )

                # Ball tracking for Caught
                elif dismissal_type == "Caught":
                    # Caught reviews check: clean catch, no grass, no boundary contact
                    clean_catch = random.random() < 0.75
                    grass_contact = random.random() < 0.15
                    boundary_contact = random.random() < 0.10

                    if not clean_catch or grass_contact or boundary_contact:
                        is_actually_out = False
                        if boundary_contact:
                            reason = "ball touched the ground"
                        elif grass_contact:
                            reason = "ball touched the grass"
                        else:
                            reason = "catch was not clean"
                        if self.is_batting_review:
                            res_text = (
                                f"🟩 **NOT OUT** — Decision Overturned! ({reason})"
                            )
                        else:
                            res_text = (
                                f"🟥 **OUT** — Original Decision Stands! ({reason})"
                            )
                        await msg.edit(
                            content=f"📺 **Checking catch...**\n\n{res_text}"
                        )
                    else:
                        is_actually_out = True
                        if self.is_batting_review:
                            res_text = (
                                "🟥 **OUT** — Original Decision Stands! (Clean catch)"
                            )
                        else:
                            res_text = (
                                "🟩 **NOT OUT** — Decision Overturned! (Clean catch)"
                            )
                        await msg.edit(
                            content=f"📺 **Checking catch...**\n\n{res_text}"
                        )

                # Run out review
                elif dismissal_type == "Run Out":
                    # Check if batsman was in crease
                    in_crease = (
                        random.random() < 0.30
                    )  # 30% chance the umpire was wrong
                    is_actually_out = not in_crease
                    if self.is_batting_review:
                        res_text = (
                            "🟥 **OUT** — Original Decision Stands!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Decision Overturned! (Batsman was in crease)"
                        )
                    else:
                        res_text = (
                            "🟥 **OUT** — Decision Overturned!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Original Decision Stands! (Batsman was in crease)"
                        )
                    await msg.edit(content=f"📺 **Checking run out...**\n\n{res_text}")

                # Stumped review
                elif dismissal_type == "Stumped":
                    # Check if bails were removed before bat crossed crease
                    bat_in_crease = (
                        random.random() < 0.25
                    )  # 25% chance umpire was wrong
                    is_actually_out = not bat_in_crease
                    if self.is_batting_review:
                        res_text = (
                            "🟥 **OUT** — Original Decision Stands!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Decision Overturned! (Bat was in crease)"
                        )
                    else:
                        res_text = (
                            "🟥 **OUT** — Decision Overturned!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Original Decision Stands! (Bat was in crease)"
                        )
                    await msg.edit(content=f"📺 **Checking stumping...**\n\n{res_text}")

                # Hit Wicket review
                elif dismissal_type == "Hit Wicket":
                    # Check if batsman dislodged bails
                    hit_wicket = random.random() < 0.80  # 80% chance umpire was right
                    is_actually_out = hit_wicket
                    if self.is_batting_review:
                        res_text = (
                            "🟥 **OUT** — Original Decision Stands!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Decision Overturned! (Bails not dislodged by batsman)"
                        )
                    else:
                        res_text = (
                            "🟥 **OUT** — Decision Overturned!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Original Decision Stands! (Bails not dislodged by batsman)"
                        )
                    await msg.edit(
                        content=f"📺 **Checking hit wicket...**\n\n{res_text}"
                    )

                # Obstructing review
                elif dismissal_type in ("Obstructing", "Obstructing the Field"):
                    # Check if batsman deliberately obstructed
                    obstructed = random.random() < 0.70  # 70% chance umpire was right
                    is_actually_out = obstructed
                    if self.is_batting_review:
                        res_text = (
                            "🟥 **OUT** — Original Decision Stands!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Decision Overturned! (No deliberate obstruction)"
                        )
                    else:
                        res_text = (
                            "🟥 **OUT** — Decision Overturned!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Original Decision Stands! (No deliberate obstruction)"
                        )
                    await msg.edit(
                        content=f"📺 **Checking obstruction...**\n\n{res_text}"
                    )

                # Bowled review (only for no-ball, already handled above)
                else:
                    is_actually_out = self.outcome["actual_is_out"]
                    if self.is_batting_review:
                        res_text = (
                            "🟥 **OUT** — Original Decision Stands!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Decision Overturned!"
                        )
                    else:
                        res_text = (
                            "🟥 **OUT** — Decision Overturned!"
                            if is_actually_out
                            else "🟩 **NOT OUT** — Original Decision Stands!"
                        )
                    await msg.edit(content=f"📺 **DRS Result:** {res_text}")

                # Apply the result
                if is_actually_out != self.outcome["umpire_given_out"]:
                    # Decision changed - restore review
                    self._restore_review()
                    await asyncio.sleep(0.5)

                await _finalise_wicket(
                    self.game, interaction.channel, is_actually_out, self.outcome
                )

            elif self.review_type == "NO_BALL":
                await asyncio.sleep(2.0)
                is_actual_nb = self.outcome.get("is_actual_nb", False)
                nb_reason = self.outcome.get("nb_review_reason", "Front Foot")
                if is_actual_nb:
                    if self.is_batting_review:
                        res_text = "🟡 Decision Overturned! It's a No Ball!"
                        self.outcome.update(
                            {
                                "is_legal": False,
                                "extra_type": "NB",
                                "runs_extras": 1,
                                "outcome_str": (
                                    f"{self.outcome['runs_batter']}NB"
                                    if self.outcome["runs_batter"] > 0
                                    else "NB"
                                ),
                            }
                        )
                    else:
                        res_text = "✅ Original Decision Stands. It's a No Ball."
                    await msg.edit(content=f"🔍 Checking {nb_reason}...\n\n{res_text}")
                    self.outcome["umpire_given_out"] = False
                    self.outcome["actual_is_out"] = False
                    self.outcome["dismissal_type"] = None
                    await _finalise_wicket(
                        self.game, interaction.channel, False, self.outcome
                    )
                    return
                else:
                    if self.is_batting_review:
                        res_text = "✅ Fair delivery. Original Decision Stands."
                    else:
                        res_text = "🟩 Decision Overturned! Fair delivery."
                        total_runs = self.outcome.get(
                            "runs_batter", 0
                        ) + self.outcome.get("overthrows", 0)
                        if total_runs > 0:
                            self.outcome.update(
                                {
                                    "is_legal": True,
                                    "extra_type": None,
                                    "runs_extras": 0,
                                    "outcome_str": str(total_runs),
                                }
                            )
                        else:
                            self.outcome.update(
                                {
                                    "is_legal": True,
                                    "extra_type": None,
                                    "runs_extras": 0,
                                    "outcome_str": "0",
                                }
                            )
                        if (
                            self.outcome["actual_is_out"]
                            and self.outcome["dismissal_type"] != "Run Out"
                        ):
                            self.outcome["umpire_given_out"] = True
                    await msg.edit(content=f"🔍 Checking {nb_reason}...\n\n{res_text}")
                    await _finalise_wicket(
                        self.game,
                        interaction.channel,
                        self.outcome["umpire_given_out"],
                        self.outcome,
                    )
        except Exception:
            try:
                await msg.edit(
                    content="⚠️ Review interrupted. On-field decision stands."
                )
            except Exception:
                pass
            await _finalise_wicket(
                self.game,
                interaction.channel,
                self.outcome.get("umpire_given_out", False),
                self.outcome,
            )

    @discord.ui.button(label="No Review", style=discord.ButtonStyle.red)
    async def no_review(self, interaction: discord.Interaction, _: discord.ui.Button):
        if self._finished:
            return
        if interaction.user.id != self.reviewing_team_id:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Not your review!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        await self._finish()
        try:
            await interaction.response.defer()
        except (discord.NotFound, discord.HTTPException):
            pass
        await _finalise_wicket(
            self.game,
            interaction.channel,
            self.outcome.get("umpire_given_out", False),
            self.outcome,
        )

    async def on_timeout(self):
        await super().on_timeout()
        if self._finished:
            return
        self._finished = True
        self.stop()
        original_decision = self.outcome.get("umpire_given_out", False)
        channel = None
        if hasattr(self, "message") and self.message:
            channel = self.message.channel
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
        if channel:
            try:
                await channel.send(
                    f"⏱ Review time expired. On-field **{'OUT' if original_decision else 'NOT OUT'}** stands."
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            await _finalise_wicket(self.game, channel, original_decision, self.outcome)


# ── Substitution UI ───────────────────────────────────────────────────────────


def _build_innings_summary_embed(game: GameState) -> discord.Embed:
    return generate_tv_broadcast_summary(
        game, is_final=False, innings_summary=game.innings
    )


def _build_full_match_summary_embed(game: GameState) -> discord.Embed:
    team1_name = getattr(game, "team1_name", "TEAM 1")
    team2_name = getattr(game, "team2_name", "TEAM 2")
    is_final = bool(getattr(game, "match_finished", False))
    t1_runs = int(getattr(game, "t1_total_runs", 0) or 0)
    t1_wkts = int(getattr(game, "t1_total_wickets", 0) or 0)
    t2_runs = int(getattr(game, "t2_total_runs", 0) or 0)
    t2_wkts = int(getattr(game, "t2_total_wickets", 0) or 0)
    inn1_overs = getattr(game, "overs", 10)
    inn2_overs = inn1_overs

    embed = discord.Embed(
        title=f"📊 MATCH SUMMARY • {team1_name} vs {team2_name}",
        color=discord.Color.from_str("#1E90FF"),
    )

    if hasattr(game, "stadium_image_url") and getattr(game, "stadium_image_url"):
        embed.set_image(url=getattr(game, "stadium_image_url"))

    # ── Scorecard blocks ────────────────────────────────────────────────────
    def _inn_block(
        runs: int, wkts: int, overs: int, batting: list, bowling: list, label: str
    ) -> str:
        bat_lines = [
            f"{_safe_get(p, 'name', '—')[:16].ljust(16)} {_safe_get(p, 'runs', 0):>4} ({_safe_get(p, 'balls', 0):>3})"
            for p in sorted(
                batting, key=lambda x: _safe_get(x, "runs", 0), reverse=True
            )[:5]
        ]
        bowl_lines = [
            f"{_safe_get(p, 'name', '—')[:16].ljust(16)} {_safe_get(p, 'wickets', 0):>2}-{_safe_get(p, 'runs_conceded', 0):>3}"
            for p in sorted(
                bowling,
                key=lambda x: (
                    _safe_get(x, "wickets", 0),
                    -_safe_get(x, "runs_conceded", 0),
                ),
                reverse=True,
            )[:4]
        ]
        _pad_lines(bat_lines, 5, "—")
        _pad_lines(bowl_lines, 4, "—")
        batting_str = "```\n" + "\n".join(bat_lines) + "\n```"
        bowling_str = "```\n" + "\n".join(bowl_lines) + "\n```"
        return f"**{label} {runs}/{wkts} ({overs}.0)**\n{batting_str}{bowling_str}"

    # Collect per-innings data
    inn1_bat = getattr(game, "innings_batting_stats", [])
    inn2_bat = getattr(game, "innings_batting_stats", [])
    inn1_bowl = getattr(game, "innings_bowling_stats", [])
    inn2_bowl = getattr(game, "innings_bowling_stats", [])

    t1_batting = (
        inn1_bat[0] if len(inn1_bat) > 0 else getattr(game, "t1_batsmen", []) or []
    )
    t2_bowling = (
        inn1_bowl[0] if len(inn1_bowl) > 0 else getattr(game, "t2_bowlers", []) or []
    )
    t2_batting = (
        inn2_bat[1] if len(inn2_bat) > 1 else getattr(game, "t2_batsmen", []) or []
    )
    t1_bowling = (
        inn2_bowl[1] if len(inn2_bowl) > 1 else getattr(game, "t1_bowlers", []) or []
    )

    block1 = _inn_block(
        t1_runs, t1_wkts, inn1_overs, t1_batting, t2_bowling, f"{team1_name}"
    )
    embed.add_field(name="\u200b", value=block1, inline=False)

    if getattr(game, "innings", 1) >= 2 or is_final:
        block2 = _inn_block(
            t2_runs, t2_wkts, inn2_overs, t2_batting, t1_bowling, f"{team2_name}"
        )
        embed.add_field(name="\u200b", value=block2, inline=False)

    # ── Result / Margin ────────────────────────────────────────────────────
    if is_final:
        verdict = None
        if hasattr(game, "match_result") and callable(
            getattr(game, "match_result", None)
        ):
            try:
                verdict = game.match_result()
            except Exception:
                verdict = None
        if not verdict:
            if t2_runs > t1_runs:
                verdict = f"**{team2_name}** won by {t2_runs - t1_runs} runs"
            elif t1_runs > t2_runs:
                verdict = f"**{team1_name}** won by {t1_runs - t2_runs} runs"
            else:
                verdict = f"**{team2_name}** won by {max(0, 10 - t2_wkts)} wickets"
        embed.add_field(name="🏆 RESULT", value=f"**{verdict}**", inline=False)

    # ── Best performers ────────────────────────────────────────────────────
    all_batting = (t1_batting or []) + (t2_batting or [])
    all_bowling = (t2_bowling or []) + (t1_bowling or [])

    best_bat = (
        max(all_batting, key=lambda p: _safe_get(p, "runs", 0) or 0)
        if all_batting
        else None
    )
    best_bowl = (
        max(all_bowling, key=lambda p: _safe_get(p, "wickets", 0) or 0)
        if all_bowling
        else None
    )

    highlights: list[str] = []
    if best_bat and (_safe_get(best_bat, "runs", 0) or 0) > 0:
        highlights.append(
            f"🏏 **Best Batter:** {_safe_get(best_bat, 'name', '?')} — "
            f"{_safe_get(best_bat, 'runs', 0)} runs ({_safe_get(best_bat, 'balls', 0)} balls)"
        )
    if best_bowl and (_safe_get(best_bowl, "wickets", 0) or 0) > 0:
        highlights.append(
            f"⚾ **Best Bowler:** {_safe_get(best_bowl, 'name', '?')} — "
            f"{_safe_get(best_bowl, 'wickets', 0)}/{_safe_get(best_bowl, 'runs_conceded', 0)}"
        )

    # ── Key partnerships ───────────────────────────────────────────────────
    ph = getattr(game, "partnership_history", [])
    if ph:
        top_p = max(ph, key=lambda p: getattr(p, "runs", 0))
        if top_p and getattr(top_p, "runs", 0) > 0:
            b1 = getattr(top_p, "batsman1", "?")[:14]
            b2 = getattr(top_p, "batsman2", "?")[:14]
            highlights.append(
                f"🤝 **Best Partnership:** {b1} & {b2} — {getattr(top_p, 'runs', 0)} runs "
                f"({getattr(top_p, 'balls', 0)} balls)"
            )

    # ── Timeline highlights ────────────────────────────────────────────────
    tl = getattr(game, "timeline", [])
    if tl:
        milestones: list[str] = []
        total = 0
        wkts = 0
        for i, outcome in enumerate(tl):
            o = str(outcome)
            if o.startswith("W"):
                wkts += 1
                milestones.append(f"W{wkts}@b{i+1}")
            else:
                try:
                    r = int(o)
                except ValueError:
                    r = 0
                total += r
                if r >= 6:
                    milestones.append(f"6@{total}(b{i+1})")
                elif r >= 4:
                    milestones.append(f"4@{total}(b{i+1})")
        if milestones:
            embed.add_field(
                name="📍 KEY MOMENTS",
                value=" | ".join(milestones[:12]),
                inline=False,
            )

    if highlights:
        embed.add_field(name="⭐ HIGHLIGHTS", value="\n".join(highlights), inline=False)

    if not highlights and not ph and not tl:
        embed.add_field(
            name="STATUS",
            value="Match in progress — use `/cssummary` again at the end for full highlights.",
            inline=False,
        )

    return embed


class SubstitutionView(MatchView):
    def __init__(
        self,
        game: GameState,
        user_id: int,
        bench: list[tuple[MatchPlayer, int]],
        channel: discord.abc.Messageable,
        auto_out_idx: int | None = None,
        dismissed_idx: int | None = None,
    ):
        super().__init__()
        self.game = game
        self.user_id = user_id
        self.bench = bench
        self.channel = channel
        self._out_idx: int | None = auto_out_idx
        self._dismissed_idx: int | None = dismissed_idx
        self._in_idx: int | None = None
        self._confirmed = False

        if auto_out_idx is not None:
            out_name = self.game.teams[self.user_id][auto_out_idx].name
            self._auto_out_name = out_name
            self._build_in_selection()
        else:
            self._auto_out_name = None
            options = [
                discord.SelectOption(
                    label=p.name[:25], value=str(i), description=f"OVR {p.ovr}"
                )
                for i, p in enumerate(game.teams[user_id])
            ]
            self._out_sel = discord.ui.Select(
                placeholder="Player to come OFF...", options=options
            )
            self._out_sel.callback = self._out_cb
            self.add_item(self._out_sel)

            impact_btn = discord.ui.Button(
                label="🔼 Select Substitute →", style=discord.ButtonStyle.green, row=1
            )
            impact_btn.callback = self._impact_cb
            self.add_item(impact_btn)

            cancel_btn = discord.ui.Button(
                label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
            )
            cancel_btn.callback = self._cancel_cb
            self.add_item(cancel_btn)

    def _build_in_selection(self):
        options = [
            discord.SelectOption(
                label=p.name[:25], value=str(i), description=f"OVR {p.ovr}"
            )
            for i, (p, _) in enumerate(self.bench[:25])
        ]
        self._in_sel = discord.ui.Select(
            placeholder="Substitute to come ON...", options=options
        )
        self._in_sel.callback = self._in_cb
        self.add_item(self._in_sel)

        conf_btn = discord.ui.Button(
            label="✅ Confirm Impact Sub", style=discord.ButtonStyle.green, row=1
        )
        conf_btn.callback = self._confirm_cb
        self.add_item(conf_btn)

        cancel_btn = discord.ui.Button(
            label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
        )
        cancel_btn.callback = self._cancel_cb
        self.add_item(cancel_btn)

    def _check_user(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _out_cb(self, interaction: discord.Interaction) -> None:
        if not self._check_user(interaction):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Not your substitution!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        self._out_idx = int(self._out_sel.values[0])
        try:
            await interaction.response.defer()
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _impact_cb(self, interaction: discord.Interaction) -> None:
        if not self._check_user(interaction):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Not your substitution!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        if self.game.swap_used.get(self.user_id, False):
            try:
                await interaction.response.send_message(
                    "⚠️ You have already used your Impact substitution this match!",
                    ephemeral=True,
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        if self._out_idx is None:
            if hasattr(self, "_out_sel") and self._out_sel.values:
                self._out_idx = int(self._out_sel.values[0])
            if self._out_idx is None:
                try:
                    await interaction.response.send_message(
                        "⚠️ Please select a player to sub out first.", ephemeral=True
                    )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
        # Build in-player selector
        self.clear_items()
        options = [
            discord.SelectOption(
                label=p.name[:25], value=str(i), description=f"OVR {p.ovr}"
            )
            for i, (p, _) in enumerate(self.bench[:25])
        ]
        self._in_sel = discord.ui.Select(
            placeholder="Substitute to come ON...", options=options
        )
        self._in_sel.callback = self._in_cb
        self.add_item(self._in_sel)
        conf_btn = discord.ui.Button(
            label="✅ Confirm Impact Sub", style=discord.ButtonStyle.green, row=1
        )
        conf_btn.callback = self._confirm_cb
        self.add_item(conf_btn)
        cancel_btn = discord.ui.Button(
            label="❌ Cancel", style=discord.ButtonStyle.secondary, row=1
        )
        cancel_btn.callback = self._cancel_cb
        self.add_item(cancel_btn)
        out_name = self.game.teams[self.user_id][self._out_idx].name
        try:
            await interaction.response.edit_message(
                content=f"🔄 **{out_name}** comes off. Who replaces them?",
                view=self,
            )
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _in_cb(self, interaction: discord.Interaction) -> None:
        if not self._check_user(interaction):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Not your substitution!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        self._in_idx = int(self._in_sel.values[0])
        try:
            await interaction.response.defer()
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _confirm_cb(self, interaction: discord.Interaction) -> None:
        if not self._check_user(interaction):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Not your substitution!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        if self._out_idx is None or self._in_idx is None:
            try:
                await interaction.response.send_message(
                    "⚠️ Please select both players before confirming.", ephemeral=True
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        in_player, in_pk = self.bench[self._in_idx]
        out_player = self.game.teams[self.user_id][self._out_idx]
        subbed_in_name = in_player.name
        if subbed_in_name in self.game.sub_in_used.get(self.user_id, set()):
            try:
                await interaction.response.send_message(
                    f"⚠️ **{subbed_in_name}** has already been subbed in!",
                    ephemeral=True,
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        dismissed_player = None
        dismissed_on_strike = False
        dismissed_non_strike = False
        if self._dismissed_idx is not None:
            team_list = self.game.teams.get(self.user_id, [])
            if 0 <= self._dismissed_idx < len(team_list):
                dismissed_player = team_list[self._dismissed_idx]
                dismissed_on_strike = (
                    self.game.striker is not None
                    and self.game.striker.name == dismissed_player.name
                )
                dismissed_non_strike = (
                    self.game.non_striker is not None
                    and self.game.non_striker.name == dismissed_player.name
                )

        # Apply the substitution: remaining XI player OUT, bench Impact Player IN
        self.game.teams[self.user_id][self._out_idx] = in_player
        self.game.swap_used[self.user_id] = True
        self.game.sub_in_used.setdefault(self.user_id, set()).add(subbed_in_name)
        pks = self.game.team_pks.get(self.user_id, [])
        try:
            pks[pks.index(out_player.inst_pk)] = in_pk
        except ValueError:
            pks.append(in_pk)

        if self._dismissed_idx is not None:
            if (
                dismissed_player is not None
                and dismissed_player.name not in self.game.dismissed
            ):
                self.game.dismissed.append(dismissed_player.name)
            if out_player.name not in self.game.dismissed:
                self.game.dismissed.append(out_player.name)

            if dismissed_on_strike:
                self.game.striker = in_player
            elif dismissed_non_strike:
                self.game.non_striker = in_player
            else:
                self.game.striker = in_player

            surviving = (
                self.game.striker if dismissed_on_strike else self.game.non_striker
            )
            surviving_key = self.game._player_key(surviving) if surviving else None
            surviving_stats = (
                self.game.batsman_stats.get(surviving_key) if surviving_key else None
            )

            self.game.reset_bat_stats_for_player(in_player)

            if surviving_key and surviving_stats is not None:
                self.game.batsman_stats[surviving_key] = surviving_stats
            self.game.last_ball_was_wicket = False
            self.game.last_wicket_dismissed_name = ""
            self.game.last_wicket_dismissed_idx = -1
        self._confirmed = True
        self.stop()
        try:
            await interaction.response.edit_message(
                content=f"✅ **Impact Sub Complete!**\n**{out_player.name}** ➡ **{in_player.name}**",
                view=None,
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        await self._resume_match(interaction.channel)

    async def _cancel_cb(self, interaction: discord.Interaction) -> None:
        if not self._check_user(interaction):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Not your substitution!", ephemeral=True
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        self.stop()
        try:
            await interaction.response.edit_message(
                content="❌ Impact cancelled.", view=None
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        if self._dismissed_idx is not None:
            avail = self.game.get_available_batsmen()
            if avail:
                view = NextBatsmanView(self.game, avail)
                message = await interaction.channel.send(
                    content=f"🏏 **Wicket!** {self.game.batting_user.mention} choose your next batsman:",
                    embed=_build_scoreboard_embed(self.game),
                    view=view,
                )
                view.message = message
                return
        await self._resume_match(interaction.channel)

    async def _resume_match(self, channel: discord.abc.Messageable) -> None:
        """Re-send the appropriate prompt to continue the match after a sub."""
        try:
            await _check_end_of_over_or_innings(channel, self.game)
        except Exception as exc:
            log.error(f"[views] SubstitutionView._resume_match failed: {exc}")

    async def on_timeout(self) -> None:
        if not self._confirmed:
            self.stop()
            self.clear_items()
            try:
                if hasattr(self, "message") and self.message:
                    await self.message.edit(
                        content="⏱ *Impact substitution window expired. No changes made.*",
                        view=self,
                    )
            except (discord.NotFound, discord.HTTPException):
                pass
            try:
                if self._dismissed_idx is not None:
                    avail = self.game.get_available_batsmen()
                    if avail:
                        view = NextBatsmanView(self.game, avail)
                        message = await self.channel.send(
                            content=f"🏏 **Wicket!** {self.game.batting_user.mention} choose your next batsman:",
                            embed=_build_scoreboard_embed(self.game),
                            view=view,
                        )
                        view.message = message
                        return
                await self._resume_match(self.channel)
            except Exception:
                pass
