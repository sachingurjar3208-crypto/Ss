"""
practice_views.py
-----------------
Solo practice mode — the user plays a full match against a CPU bot.
No career stats are saved.  Everything else (delivery logic, commentary,
scoreboard, DRS, innings break) reuses the live-match code.
"""

import asyncio
import random
import discord
from discord import ui

from game import GameState, _pname
from logic import calculate_outcome, player_attributes
from commentary import build_ball_commentary
from embeds import build_scoreboard_embed, build_playing_xi_embed, build_result_embed
from data import (
    TIMELINE_EMOJIS,
    FAST_STAGE1, FAST_STAGE2,
    OFF_SPIN_BUTTONS, LEG_SPIN_BUTTONS,
    BATTING_BUTTONS, SHOT_BUTTON_MAP,
    get_delivery_speed, resolve_delivery,
    DELIVERY_BUTTON_MAP,
    FAST_SHOT_GUIDE, SPIN_SHOT_GUIDE,
    NOT_OUT_EMOJI, LBW_EMOJI,
)
from media import get_player_media, get_milestone_gif, get_umpire_gif

VIEW_TIMEOUT = 300

# ── Active practice sessions ───────────────────────────────────────────────
active_practice: dict[int, GameState] = {}   # channel_id → GameState


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _timeline_emoji(outcome: str) -> str:
    return TIMELINE_EMOJIS.get(outcome, "•")


# ── Difficulty constants ──────────────────────────────────────────────────────
# smart_bowl = chance CPU picks the "best" delivery against the batsman
# smart_bat  = chance CPU picks a recommended shot vs the delivery
CPU_DIFFICULTY = {
    "easy":   {"smart_bowl": 0.10, "smart_bat": 0.10},  # mostly random/weak
    "medium": {"smart_bowl": 0.20, "smart_bat": 0.20},  # 20% smart
    "hard":   {"smart_bowl": 0.70, "smart_bat": 0.70},  # 70% smart
}

# "Weak" deliveries the easy CPU favours (easier to hit)
_EASY_BOWL_FAST   = ["Full", "Good"]          # no bouncers/yorkers
_EASY_BOWL_SPIN   = ["Offspin", "Legspin"]    # basic deliveries only


def _get_difficulty(game: GameState) -> dict:
    diff = getattr(game, "cpu_difficulty", "medium")
    return CPU_DIFFICULTY.get(diff, CPU_DIFFICULTY["medium"])


def _bot_pick_delivery(game: GameState) -> tuple[str, str, str]:
    """
    CPU picks a delivery based on difficulty.
    - Easy:   mostly weak/easy deliveries (full, good length)
    - Medium: 20% chance of picking a smart dangerous delivery
    - Hard:   70% chance of picking a smart dangerous delivery
    Returns (stage1_choice, delivery_button, delivery_internal).
    """
    bowler = game.current_bowler
    btype  = bowler.get("bowling_type", "Fast") if bowler else "Fast"
    diff   = _get_difficulty(game)
    smart  = diff["smart_bowl"]

    if btype == "Fast":
        stage1 = random.choice(FAST_STAGE1)
        # Smart bowl = use full FAST_STAGE2 pool; dumb = only easy deliveries
        if random.random() < smart:
            stage2 = random.choice(FAST_STAGE2)            # any delivery
        else:
            stage2 = random.choice(_EASY_BOWL_FAST)        # easy/safe only
        internal = DELIVERY_BUTTON_MAP.get(stage2, stage2)
        return stage1, stage2, internal
    elif btype == "Off Spin":
        if random.random() < smart:
            btn = random.choice(OFF_SPIN_BUTTONS)           # full variety
        else:
            btn = random.choice(_EASY_BOWL_SPIN[:2])        # basic spin only
        internal = resolve_delivery(btn, "Off Spin")
        return "", btn, internal
    else:  # Leg Spin
        if random.random() < smart:
            btn = random.choice(LEG_SPIN_BUTTONS)
        else:
            btn = random.choice(["Legspin", "Topspin"])     # safest leg spin
        internal = resolve_delivery(btn, "Leg Spin")
        return "", btn, internal


# "Safe" batting shots for easy CPU (avoid attacking shots)
_EASY_BAT_SHOTS = ["Defend", "Block", "Nudge"]

def _bot_pick_shot(delivery_button: str, stage1: str = "", game: "GameState | None" = None) -> tuple[str, bool]:
    """
    CPU picks a batting shot based on difficulty.
    - Easy:   mostly defensive/safe shots, rarely hits recommended
    - Medium: 20% chance picks the correct recommended shot
    - Hard:   70% chance picks the correct recommended shot
    Returns (shot_button, is_recommended).
    """
    if stage1:
        recommended = FAST_SHOT_GUIDE.get((stage1, delivery_button), [])
    else:
        recommended = SPIN_SHOT_GUIDE.get(delivery_button, [])

    diff  = _get_difficulty(game) if game else CPU_DIFFICULTY["medium"]
    smart = diff["smart_bat"]

    # Check if easy shots exist in BATTING_BUTTONS (graceful fallback)
    easy_shots = [s for s in _EASY_BAT_SHOTS if s in BATTING_BUTTONS]
    if not easy_shots:
        easy_shots = BATTING_BUTTONS

    if recommended and random.random() < smart:
        shot = random.choice(recommended)       # picks correct shot
    elif random.random() < (1 - smart):
        shot = random.choice(easy_shots)        # safe/weak shot
    else:
        shot = random.choice(BATTING_BUTTONS)   # random

    is_rec = shot in recommended
    return shot, is_rec


def _bot_pick_opener(game: GameState) -> tuple[dict, dict]:
    """Pick the two highest-BAT players as openers."""
    team    = game.get_batting_team()
    players = sorted(team.get("players", []), key=lambda p: p.get("bat", 0), reverse=True)
    return players[0], players[1]


def _bot_pick_next_batsman(game: GameState) -> dict | None:
    available = game.get_available_batsmen()
    if not available:
        return None
    return sorted(available, key=lambda p: p.get("bat", 0), reverse=True)[0]


def _bot_pick_bowler(game: GameState) -> dict | None:
    available = game.get_available_bowlers()
    if not available:
        team      = game.get_bowling_team()
        available = [p for p in team.get("players", []) if p.get("bowling_type")]
    if not available:
        return None
    return sorted(available, key=lambda p: p.get("bowl", 0), reverse=True)[0]


# ─────────────────────────────────────────────────────────────────────────────
# Core delivery processor (practice — no stat saving)
# ─────────────────────────────────────────────────────────────────────────────

async def _practice_process_delivery(
    interaction: discord.Interaction,
    game: GameState,
    shot_button: str,
    *,
    stage1_choice: str = "",
    is_recommended_shot: bool = False,
):
    # Guard: match may have been cancelled
    ch = interaction.channel
    if ch and (ch.id not in active_practice or active_practice.get(ch.id) is not game):
        return
    try:
        await _practice_process_delivery_inner(
            interaction, game, shot_button,
            stage1_choice=stage1_choice,
            is_recommended_shot=is_recommended_shot,
        )
    except Exception as e:
        import traceback
        print(f"[practice] delivery error: {e}\n{traceback.format_exc()}")
        try:
            if ch and active_practice.get(ch.id) is game:
                await ch.send(
                    "⚠️ Something went wrong. Attempting to recover...\n"
                    "If the match is stuck, use `!cs cancel` to end it."
                )
                await _practice_send_bowling(ch, game)
        except Exception:
            pass


async def _practice_process_delivery_inner(
    interaction: discord.Interaction,
    game: GameState,
    shot_button: str,
    *,
    stage1_choice: str = "",
    is_recommended_shot: bool = False,
):
    shot_internal     = SHOT_BUTTON_MAP.get(shot_button, "Drive")
    delivery_button   = game.pending_delivery
    delivery_internal = game.pending_delivery_internal
    bowler            = game.current_bowler
    striker           = game.striker

    if not bowler or not striker:
        try:
            await interaction.response.send_message(
                "⚠️ Game state error — please wait for the next prompt.", ephemeral=True
            )
        except discord.InteractionResponded:
            pass
        return

    bname = _pname(bowler)
    sname = _pname(striker)

    speed_range  = get_delivery_speed(delivery_button)
    speed        = random.uniform(*speed_range)
    current_over = game.current_legal_balls // 6

    outcome, is_extra = calculate_outcome(
        delivery_internal, shot_internal,
        bowler.get("bowl", 80), striker.get("bat", 80),
        bowler_attrs  = player_attributes(bowler),
        batsman_attrs = player_attributes(striker),
        innings       = game.innings,
        current_over  = current_over,
        rrr           = game.rrr(),
        balls_since_wicket = game.balls_since_wicket,
        partnership_runs   = game.partnership_runs,
        is_recommended_shot= is_recommended_shot,
        delivery_button    = delivery_button,
        prev_delivery      = getattr(game, "last_delivery_internal", None),
        bowler_balls_done  = game.bowler_ball_count.get(bname, 0),
        bowler_max_balls   = game.max_bowler_balls(),
    )
    # Remember this delivery type for the next ball ("Mind Games" playstyle).
    game.last_delivery_internal = delivery_internal

    # Free-hit protection
    this_ball_is_free_hit = getattr(game, "pending_free_hit", False)
    if this_ball_is_free_hit and outcome == "W":
        outcome  = "0"
        is_extra = False
        commentary_suffix = "  *(Free Hit — NOT OUT!)*"
    else:
        commentary_suffix = ""

    commentary = build_ball_commentary(
        bowler_name      = bname,
        bowler_ovr       = bowler.get("ovr", 80),
        delivery_button  = delivery_button,
        delivery_internal= delivery_internal,
        speed            = speed,
        batsman_name     = sname,
        shot_button      = shot_button,
        shot_internal    = shot_internal,
        outcome          = outcome,
        bowling_type     = bowler.get("bowling_type", "Fast"),
        stage1_choice    = stage1_choice,
    ) + commentary_suffix

    striker_runs_before   = game.batsman_stats.get(sname, {}).get("runs", 0)
    bowler_wickets_before = game.bowler_stats.get(bname, {}).get("wickets", 0)

    # ── Update game state ─────────────────────────────────────────────────
    if outcome == "W":
        game.add_legal_ball()
        game.add_wicket()

    elif outcome == "Wd":
        game.add_extra_runs(1, charge_bowler=True)
        game.timeline.append(_timeline_emoji("Wd"))

    elif outcome == "NB":
        game.add_extra_runs(1, charge_bowler=True)
        game.timeline.append(_timeline_emoji("NB"))
        game.pending_free_hit = True

    elif outcome == "NB+1":
        game.add_extra_runs(1, charge_bowler=True)
        game.add_runs(1)
        game.timeline.append(_timeline_emoji("NB+1"))
        game.rotate_strike()
        game.pending_free_hit = True

    else:
        game.add_legal_ball()
        game.add_runs(int(outcome))
        game.timeline.append(_timeline_emoji(outcome))
        if outcome == "4" and sname in game.batsman_stats:
            game.batsman_stats[sname]["fours"] = game.batsman_stats[sname].get("fours", 0) + 1
        elif outcome == "6" and sname in game.batsman_stats:
            game.batsman_stats[sname]["sixes"] = game.batsman_stats[sname].get("sixes", 0) + 1
        if outcome in ("1", "3"):
            game.rotate_strike()

    # ── GIFs ─────────────────────────────────────────────────────────────
    gif_urls: list[str] = []
    if outcome in ("4", "6"):
        gif = get_player_media(sname, "four" if outcome == "4" else "six")
        if gif:
            gif_urls.append(gif)
    elif outcome == "W":
        wkts_now = game.bowler_stats.get(bname, {}).get("wickets", 0)
        gif = get_player_media(bname, "5wicket" if wkts_now >= 5 and bowler_wickets_before < 5 else "wicket")
        if gif:
            gif_urls.append(gif)

    ump_event = {"4": "four", "6": "six", "W": "out", "Wd": "wide", "NB": "noball", "NB+1": "noball"}.get(outcome)
    if ump_event:
        ump_gif = get_umpire_gif(ump_event)
        if ump_gif:
            gif_urls.append(ump_gif)

    if outcome in ("1", "2", "3", "4", "6"):
        runs_after = game.batsman_stats.get(sname, {}).get("runs", 0)
        for milestone in (50, 100, 150, 200, 250, 300):
            if striker_runs_before < milestone <= runs_after:
                gif = get_milestone_gif(sname, str(milestone)) or get_player_media(sname, str(milestone))
                if gif:
                    gif_urls.append(gif)
                break

    # Ack the interaction
    try:
        await interaction.response.edit_message(view=None)
    except discord.InteractionResponded:
        pass

    channel      = interaction.channel
    embed        = build_scoreboard_embed(game)
    innings_over = game.is_innings_over()

    # ── Wicket (not last) ─────────────────────────────────────────────────
    if outcome == "W" and not innings_over:
        await channel.send(content=commentary, embed=embed)
        for url in gif_urls:
            await channel.send(url)
        game.timeline.append(_timeline_emoji("W"))
        await _practice_handle_wicket(channel, game)
        return

    # ── Innings over ──────────────────────────────────────────────────────
    if innings_over:
        if outcome == "W":
            game.timeline.append(_timeline_emoji("W"))
        await channel.send(content=commentary, embed=embed)
        for url in gif_urls:
            await channel.send(url)
        if game.innings == 1:
            await _practice_start_second_innings(channel, game)
        else:
            result_embed = build_result_embed(game)
            await channel.send(embed=result_embed)
            await channel.send("🏋️ **Practice complete!** No stats were recorded — this was a practice session.")
            active_practice.pop(channel.id, None)
        return

    # ── Over ended ────────────────────────────────────────────────────────
    over_ended = (not is_extra) and (game.current_over_balls >= 6)
    if over_ended:
        if outcome not in ("1", "3"):
            game.rotate_strike()
        game.timeline.append("|")
        game.end_over()
        await channel.send(content=commentary, embed=embed)
        for url in gif_urls:
            await channel.send(url)
        await _practice_next_over(channel, game)
        return

    # ── Extras — re-bowl ─────────────────────────────────────────────────
    if is_extra:
        await channel.send(content=commentary, embed=embed)
        for url in gif_urls:
            await channel.send(url)
        await _practice_send_bowling(channel, game)
        return

    # ── Normal continue ───────────────────────────────────────────────────
    await channel.send(content=commentary, embed=embed)
    for url in gif_urls:
        await channel.send(url)
    await _practice_send_bowling(channel, game)


# ─────────────────────────────────────────────────────────────────────────────
# Flow helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _practice_send_bowling(channel: discord.TextChannel, game: GameState):
    """Send the bowling UI — bot bowls if it's the bot's turn, else show buttons."""
    # Guard: match may have been cancelled
    if channel.id not in active_practice or active_practice.get(channel.id) is not game:
        return

    # Guard: no bowler — auto-pick or abort
    if not game.current_bowler:
        bowler = _bot_pick_bowler(game) if game.bowling_user_id != game.challenger.id else None
        if bowler:
            game.current_bowler = bowler
            bname = _pname(bowler)
            if bname not in game.bowler_stats:
                game.bowler_stats[bname] = {"balls": 0, "runs": 0, "wickets": 0}
        else:
            await channel.send("❌ **Practice match ended** — no bowler available. Use `/cs practice` to start again.")
            active_practice.pop(channel.id, None)
            return

    # Guard: no striker — something went wrong, auto-recover
    if not game.striker:
        available = game.get_available_batsmen()
        if available:
            game.striker = available[0]
            sname = _pname(game.striker)
            game.batsman_stats[sname] = game.batsman_stats.get(sname, {"runs": 0, "balls": 0, "fours": 0, "sixes": 0})
        else:
            await channel.send("❌ **Practice match ended** — no batsmen available.")
            active_practice.pop(channel.id, None)
            return

    is_free_hit = getattr(game, "pending_free_hit", False)
    embed       = build_scoreboard_embed(game)

    if is_free_hit:
        embed.set_footer(text="⚡ FREE HIT — batsman cannot be dismissed (except run out)")
        game.pending_free_hit = False

    # Is the human the batter right now?
    human_is_batting = (game.batting_user_id == game.challenger.id)

    if human_is_batting:
        # Human bats → bot needs to bowl first, then human picks shot
        await _bot_bowl_and_wait(channel, game, is_free_hit=is_free_hit, embed=embed)
    else:
        # Bot bats → bot picks shot automatically after human bowls
        btype = game.current_bowler.get("bowling_type", "Fast")
        prefix = "🟡 **FREE HIT!**  " if is_free_hit else ""
        if btype == "Fast":
            view = PracticeFastBowlStage1View(game, is_free_hit=is_free_hit)
            view._channel = channel
            await channel.send(
                content=f"{prefix}Choose delivery  {game.bowling_user.mention}",
                embed=embed,
                view=view,
            )
        else:
            view = PracticeBowlingView(game, is_free_hit=is_free_hit)
            view._channel = channel
            await channel.send(
                content=f"{prefix}Choose delivery  {game.bowling_user.mention}",
                embed=embed,
                view=view,
            )


async def _bot_bowl_and_wait(
    channel: discord.TextChannel,
    game: GameState,
    *,
    is_free_hit: bool = False,
    embed: discord.Embed | None = None,
):
    """Bot picks a delivery, then shows the batting view to the human."""
    if channel.id not in active_practice or active_practice.get(channel.id) is not game:
        return
    stage1, delivery_btn, delivery_internal = _bot_pick_delivery(game)
    game.pending_delivery          = delivery_btn
    game.pending_delivery_internal = delivery_internal
    game.phase                     = "bat_select"

    speed      = random.uniform(*get_delivery_speed(delivery_btn))
    bname      = _pname(game.current_bowler)
    combined   = f"{stage1} {delivery_btn}".strip() if stage1 else delivery_btn
    free_prefix= "🟡 **FREE HIT!**  " if is_free_hit else ""

    batting_view = PracticeBattingView(game, stage1_choice=stage1, is_free_hit=is_free_hit)
    batting_view._channel = channel

    if embed:
        await channel.send(
            content=(
                f"{free_prefix}**{bname}** : **{combined}** at {speed:.1f} km/h  ·  "
                f"{game.batting_user.mention} — pick your shot!"
            ),
            embed=embed,
            view=batting_view,
        )
    else:
        await channel.send(
            content=(
                f"{free_prefix}**{bname}** : **{combined}** at {speed:.1f} km/h  ·  "
                f"{game.batting_user.mention} — pick your shot!"
            ),
            view=batting_view,
        )


async def _practice_next_over(channel: discord.TextChannel, game: GameState):
    """Auto-select or prompt next bowler depending on whose turn it is."""
    if channel.id not in active_practice or active_practice.get(channel.id) is not game:
        return
    human_is_bowling = (game.bowling_user_id == game.challenger.id)

    if human_is_bowling:
        xi_embed = build_playing_xi_embed(game.get_bowling_team())
        view     = PracticeNextBowlerView(game)
        view._channel = channel
        await channel.send(
            content=f"Over is up — choose your next bowler {game.bowling_user.mention}",
            embed=xi_embed,
            view=view,
        )
    else:
        # Bot picks automatically
        bowler = _bot_pick_bowler(game)
        if not bowler:
            await channel.send("❌ Bot has no bowlers left!")
            active_practice.pop(channel.id, None)
            return
        bname = _pname(bowler)
        game.current_bowler = bowler
        if bname not in game.bowler_stats:
            game.bowler_stats[bname] = {"balls": 0, "runs": 0, "wickets": 0}
        await channel.send(f"🤖 **{bname}** comes into the attack (CPU)")
        await _practice_send_bowling(channel, game)


async def _practice_handle_wicket(channel: discord.TextChannel, game: GameState):
    """Handle wicket fall — bot or human picks next batter."""
    available = game.get_available_batsmen()
    if not available:
        if game.innings == 1:
            team_name  = game.get_batting_team().get("name", "The batting team")
            total_runs = game.current_runs
            overs_used = game.overs_str()
            await channel.send(
                f"🏏 **ALL OUT!**  {team_name} are bowled out for **{total_runs}** in {overs_used} overs!"
            )
            await _practice_start_second_innings(channel, game)
        else:
            result_embed = build_result_embed(game)
            await channel.send(embed=result_embed)
            await channel.send("🏋️ **Practice complete!** No stats were recorded — this was a practice session.")
            active_practice.pop(channel.id, None)
        return

    game.striker = None
    human_is_batting = (game.batting_user_id == game.challenger.id)

    if human_is_batting:
        xi_embed = build_playing_xi_embed(game.get_batting_team())
        view     = PracticeNextBatsmanView(game)
        view._channel = channel
        await channel.send(
            content=f"Wicket! Choose your next batter {game.batting_user.mention}",
            embed=xi_embed,
            view=view,
        )
    else:
        # Bot picks automatically
        next_bat = _bot_pick_next_batsman(game)
        if not next_bat:
            return
        nname = _pname(next_bat)
        game.striker              = next_bat
        game.batsman_stats[nname] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
        await channel.send(f"🤖 **{nname}** comes to the crease (CPU)")

        if game.current_over_balls >= 6:
            game.rotate_strike()
            game.timeline.append("|")
            game.end_over()
            await _practice_next_over(channel, game)
        else:
            await _practice_send_bowling(channel, game)


async def _practice_start_second_innings(channel: discord.TextChannel, game: GameState):
    game.start_second_innings()
    target  = game.target()
    balls   = game.overs * 6
    rrr_val = round((target / balls) * 6, 1) if balls > 0 else 0

    score_to_beat = game.runs[0]
    await channel.send(
        content=(
            f"**⏸ Innings Break!**\n"
            f"**{game.get_batting_team()['name']} require {score_to_beat + 1} to win "
            f"off {balls} balls  (RRR: {rrr_val})**"
        )
    )
    await _practice_select_openers(channel, game)


async def _practice_select_openers(channel: discord.TextChannel, game: GameState):
    """Bot or human picks their two openers."""
    if channel.id not in active_practice or active_practice.get(channel.id) is not game:
        return
    human_is_batting = (game.batting_user_id == game.challenger.id)

    if human_is_batting:
        xi_embed    = build_playing_xi_embed(game.get_batting_team())
        opener_view = PracticeOpenerSelectView(game)
        opener_view._channel = channel
        await channel.send(
            content=f"Select your openers {game.batting_user.mention}",
            embed=xi_embed,
            view=opener_view,
        )
    else:
        # Bot auto-picks top-2 BAT players
        p1, p2 = _bot_pick_opener(game)
        n1, n2 = _pname(p1), _pname(p2)
        game.striker     = p1
        game.non_striker = p2
        game.batsman_stats[n1] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
        game.batsman_stats[n2] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
        game.phase = "select_bowler"
        await channel.send(f"🤖 **{n1}** & **{n2}** opening for CPU")
        await _practice_select_bowler(channel, game)


async def _practice_select_bowler(channel: discord.TextChannel, game: GameState):
    """Bot or human picks the first bowler."""
    if channel.id not in active_practice or active_practice.get(channel.id) is not game:
        return
    human_is_bowling = (game.bowling_user_id == game.challenger.id)

    if human_is_bowling:
        xi_embed     = build_playing_xi_embed(game.get_bowling_team())
        bowler_view  = PracticeBowlerSelectView(game)
        bowler_view._channel = channel
        await channel.send(
            content=f"Select your opening bowler {game.bowling_user.mention}",
            embed=xi_embed,
            view=bowler_view,
        )
    else:
        bowler = _bot_pick_bowler(game)
        if not bowler:
            await channel.send("❌ Bot has no bowlers available!")
            active_practice.pop(channel.id, None)
            return
        bname = _pname(bowler)
        game.current_bowler = bowler
        if bname not in game.bowler_stats:
            game.bowler_stats[bname] = {"balls": 0, "runs": 0, "wickets": 0}
        await channel.send(f"🤖 **{bname}** will open the bowling (CPU)")
        await _practice_send_bowling(channel, game)


# ─────────────────────────────────────────────────────────────────────────────
# Timeout mixin (same pattern as main views)
# ─────────────────────────────────────────────────────────────────────────────

class _PracticeTimeoutMixin:
    _channel: discord.TextChannel | None = None

    async def on_timeout(self):
        ch   = self._channel
        game = getattr(self, "game", None)
        if not ch or not game:
            return
        if active_practice.get(ch.id) is not game:
            return
        if game.__dict__.get("_afk_sent"):
            return
        game._afk_sent = True
        active_practice.pop(ch.id, None)
        try:
            await ch.send(
                "⏰ **Practice match abandoned — no response received.**\n"
                "Use `/cs practice` to start a new session."
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Toss (human vs CPU)
# ─────────────────────────────────────────────────────────────────────────────

class PracticeTossView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=60)
        self.game = game
        self._add_buttons()

    def _add_buttons(self):
        head_btn = ui.Button(label="HEADS 🟡", style=discord.ButtonStyle.primary)
        tail_btn = ui.Button(label="TAILS ⚪", style=discord.ButtonStyle.secondary)
        head_btn.callback = self._make_callback("Head")
        tail_btn.callback = self._make_callback("Tail")
        self.add_item(head_btn)
        self.add_item(tail_btn)

    def _make_callback(self, call: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.challenger.id:
                await interaction.response.send_message("Only you can call the toss!", ephemeral=True)
                return
            self.stop()
            result  = random.choice(["Head", "Tail"])
            won     = (call == result)
            face    = "🟡 **HEADS**" if result == "Head" else "⚪ **TAILS**"
            outcome = "won" if won else "lost"

            await interaction.response.edit_message(
                content=(
                    f"🪙 You called **{call}** — coin landed {face}\n"
                    f"You **{outcome}** the toss!"
                ),
                view=None,
            )

            if won:
                # Human won — let them choose bat/bowl
                view = PracticeBatBowlView(self.game)
                view._channel = interaction.channel
                await interaction.followup.send(
                    content="What would you like to do?",
                    view=view,
                )
            else:
                # CPU won — randomly bat or bowl
                cpu_bats = random.random() < 0.5
                if cpu_bats:
                    self.game.batting_user_id  = self.game.opponent.id
                    self.game.bowling_user_id  = self.game.challenger.id
                    cpu_choice = "bat first"
                else:
                    self.game.batting_user_id  = self.game.challenger.id
                    self.game.bowling_user_id  = self.game.opponent.id
                    cpu_choice = "bowl first"

                self.game.drs_reviews[self.game.batting_user_id] = 2
                self.game.drs_reviews[self.game.bowling_user_id] = 2

                await interaction.followup.send(
                    content=f"🤖 CPU chose to **{cpu_choice}**."
                )
                await _practice_select_openers(interaction.channel, self.game)
        return callback


class PracticeBatBowlView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=60)
        self.game = game

    @ui.button(label="🏏  Bat First", style=discord.ButtonStyle.success)
    async def bat(self, interaction: discord.Interaction, button: ui.Button):
        await self._choose(interaction, bat=True)

    @ui.button(label="🎯  Bowl First", style=discord.ButtonStyle.danger)
    async def bowl(self, interaction: discord.Interaction, button: ui.Button):
        await self._choose(interaction, bat=False)

    async def _choose(self, interaction: discord.Interaction, bat: bool):
        if interaction.user.id != self.game.challenger.id:
            await interaction.response.send_message("Only you can choose!", ephemeral=True)
            return
        self.stop()
        if bat:
            self.game.batting_user_id = self.game.challenger.id
            self.game.bowling_user_id = self.game.opponent.id
            choice = "bat first"
        else:
            self.game.batting_user_id = self.game.opponent.id
            self.game.bowling_user_id = self.game.challenger.id
            choice = "bowl first"

        self.game.drs_reviews[self.game.batting_user_id] = 2
        self.game.drs_reviews[self.game.bowling_user_id] = 2

        await interaction.response.edit_message(
            content=f"✅ You chose to **{choice}**.",
            view=None,
        )
        await _practice_select_openers(interaction.channel, self.game)


# ─────────────────────────────────────────────────────────────────────────────
# Opener selection (human only — bot is auto-handled)
# ─────────────────────────────────────────────────────────────────────────────

class PracticeOpenerSelectView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self._add_dropdown()

    def _add_dropdown(self):
        team    = self.game.get_batting_team()
        players = sorted(team.get("players", []), key=lambda p: p.get("bat", 0), reverse=True)
        options = [
            discord.SelectOption(
                label=_pname(p),
                description=f"BAT:{p.get('bat','?')} OVR:{p.get('ovr','?')}",
                value=_pname(p),
            )
            for p in players
        ]
        select          = ui.Select(placeholder="Select 2 openers…", min_values=2, max_values=2, options=options[:25])
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.batting_user_id:
            await interaction.response.send_message("Only you pick your openers!", ephemeral=True)
            return
        self.stop()
        names   = interaction.data["values"]
        team    = self.game.get_batting_team()
        by_name = {_pname(p): p for p in team.get("players", [])}
        p1, p2  = by_name.get(names[0]), by_name.get(names[1])
        if not p1 or not p2:
            await interaction.response.send_message("Player not found!", ephemeral=True)
            return

        striker_view = PracticeStrikerDesignateView(self.game, p1, p2)
        striker_view._channel = interaction.channel
        await interaction.response.edit_message(
            content=f"**{_pname(p1)}** & **{_pname(p2)}** — who takes strike?",
            view=striker_view,
            embed=None,
        )


class PracticeStrikerDesignateView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState, p1: dict, p2: dict):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self.p1   = p1
        self.p2   = p2
        b1 = ui.Button(label=_pname(p1), style=discord.ButtonStyle.primary)
        b2 = ui.Button(label=_pname(p2), style=discord.ButtonStyle.secondary)
        b1.callback = self._make_cb(p1, p2)
        b2.callback = self._make_cb(p2, p1)
        self.add_item(b1)
        self.add_item(b2)

    def _make_cb(self, striker: dict, non_striker: dict):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                await interaction.response.send_message("Only you select!", ephemeral=True)
                return
            self.stop()
            sn = _pname(striker)
            nn = _pname(non_striker)
            self.game.striker     = striker
            self.game.non_striker = non_striker
            self.game.batsman_stats[sn] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
            self.game.batsman_stats[nn] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
            self.game.phase = "select_bowler"
            await interaction.response.edit_message(
                content=f"🔴 **{sn}** on strike · **{nn}** at non-striker end",
                view=None,
            )
            await _practice_select_bowler(interaction.channel, self.game)
        return callback


# ─────────────────────────────────────────────────────────────────────────────
# Bowler selection (human only)
# ─────────────────────────────────────────────────────────────────────────────

class PracticeBowlerSelectView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self._add_dropdown()

    def _add_dropdown(self):
        bowlers = self.game.get_available_bowlers()
        if not bowlers:
            team    = self.game.get_bowling_team()
            bowlers = [p for p in team.get("players", []) if p.get("bowling_type")]
        bowlers = sorted(bowlers, key=lambda p: p.get("bowl", 0), reverse=True)
        options = [
            discord.SelectOption(
                label=_pname(p),
                description=f"{p.get('bowling_type','?')} | BOWL:{p.get('bowl','?')} | {self.game.bowler_overs_str(_pname(p))} ov",
                value=_pname(p),
            )
            for p in bowlers[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="No bowlers available", value="none")]
        select          = ui.Select(placeholder="Click to select bowler", options=options)
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.bowling_user_id:
            await interaction.response.send_message("Only you select the bowler!", ephemeral=True)
            return
        name = interaction.data["values"][0]
        if name == "none":
            await interaction.response.send_message("No bowlers available!", ephemeral=True)
            return
        team   = self.game.get_bowling_team()
        bowler = next((p for p in team.get("players", []) if _pname(p) == name), None)
        if not bowler:
            await interaction.response.send_message("Bowler not found!", ephemeral=True)
            return
        self.stop()
        bname = _pname(bowler)
        game  = self.game
        game.current_bowler = bowler
        if bname not in game.bowler_stats:
            game.bowler_stats[bname] = {"balls": 0, "runs": 0, "wickets": 0}
        await interaction.response.edit_message(content=f"🎯 **{bname}** will bowl.", view=None, embed=None)
        await _practice_send_bowling(interaction.channel, game)


# ─────────────────────────────────────────────────────────────────────────────
# Next bowler (human over prompt)
# ─────────────────────────────────────────────────────────────────────────────

class PracticeNextBowlerView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self._add_dropdown()

    def _add_dropdown(self):
        available = self.game.get_available_bowlers()
        if not available:
            team      = self.game.get_bowling_team()
            available = [p for p in team.get("players", []) if p.get("bowling_type")]
        available = sorted(available, key=lambda p: p.get("bowl", 0), reverse=True)
        options   = [
            discord.SelectOption(
                label=_pname(p),
                description=f"{p.get('bowling_type','?')} | BOWL:{p.get('bowl','?')} | {self.game.bowler_overs_str(_pname(p))} ov",
                value=_pname(p),
            )
            for p in available[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="No bowlers available", value="none")]
        select          = ui.Select(placeholder="Click to select bowler", options=options)
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.bowling_user_id:
            await interaction.response.send_message("Only you select!", ephemeral=True)
            return
        name = interaction.data["values"][0]
        if name == "none":
            await interaction.response.send_message("No bowlers available!", ephemeral=True)
            return
        team   = self.game.get_bowling_team()
        bowler = next((p for p in team.get("players", []) if _pname(p) == name), None)
        if not bowler:
            await interaction.response.send_message("Bowler not found!", ephemeral=True)
            return

        bname    = _pname(bowler)
        max_balls= self.game.max_bowler_balls()
        if self.game.bowler_ball_count.get(bname, 0) >= max_balls:
            await interaction.response.send_message(
                f"❌ **{bname}** has already bowled their max overs.", ephemeral=True
            )
            return
        self.stop()
        self.game.current_bowler = bowler
        if bname not in self.game.bowler_stats:
            self.game.bowler_stats[bname] = {"balls": 0, "runs": 0, "wickets": 0}
        await interaction.response.edit_message(content=f"🎯 **{bname}** starts a new over.", view=None, embed=None)
        await _practice_send_bowling(interaction.channel, self.game)


# ─────────────────────────────────────────────────────────────────────────────
# Next batsman (human only)
# ─────────────────────────────────────────────────────────────────────────────

class PracticeNextBatsmanView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self._add_dropdown()

    def _add_dropdown(self):
        available = sorted(self.game.get_available_batsmen(), key=lambda p: p.get("bat", 0), reverse=True)
        options   = [
            discord.SelectOption(
                label=_pname(p),
                description=f"BAT:{p.get('bat','?')} OVR:{p.get('ovr','?')}",
                value=_pname(p),
            )
            for p in available[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="No batsmen available", value="none")]
        select          = ui.Select(placeholder="Click to select batter", options=options)
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.batting_user_id:
            await interaction.response.send_message("Only you select!", ephemeral=True)
            return
        name = interaction.data["values"][0]
        if name == "none":
            await interaction.response.send_message("No batsmen left!", ephemeral=True)
            return
        team   = self.game.get_batting_team()
        player = next((p for p in team.get("players", []) if _pname(p) == name), None)
        if not player:
            await interaction.response.send_message("Player not found!", ephemeral=True)
            return
        self.stop()
        self.game.striker             = player
        self.game.batsman_stats[name] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
        await interaction.response.edit_message(
            content=f"🏏 **{name}** selected.", view=None, embed=None
        )
        if self.game.current_over_balls >= 6:
            self.game.rotate_strike()
            self.game.timeline.append("|")
            self.game.end_over()
            await _practice_next_over(interaction.channel, self.game)
        else:
            await _practice_send_bowling(interaction.channel, self.game)


# ─────────────────────────────────────────────────────────────────────────────
# Human bowling views (fast & spin) — bot bats automatically
# ─────────────────────────────────────────────────────────────────────────────

class PracticeFastBowlStage1View(_PracticeTimeoutMixin, ui.View):
    """Fast bowling (practice) — ONE message, two rows.
    Row 0 active first, row 1 greyed. After row 0 pick: row 0 grey, row 1 active."""

    _S1_STYLES = {
        "Outswing": discord.ButtonStyle.primary,
        "Inswing":  discord.ButtonStyle.secondary,
        "Fast":     discord.ButtonStyle.danger,
        "Slow":     discord.ButtonStyle.success,
    }
    _S2_STYLES = {
        "Bouncer": discord.ButtonStyle.danger,
        "Full":    discord.ButtonStyle.primary,
        "Good":    discord.ButtonStyle.success,
        "Yorker":  discord.ButtonStyle.secondary,
    }

    def __init__(self, game: GameState, is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game          = game
        self.is_free_hit   = is_free_hit
        self.stage1_choice = ""
        self._stage1_btns: list = []
        self._stage2_btns: list = []

        for label in FAST_STAGE1:
            btn = ui.Button(label=label, style=self._S1_STYLES.get(label, discord.ButtonStyle.secondary), row=0)
            btn.callback = self._make_stage1_callback(label)
            self._stage1_btns.append(btn)
            self.add_item(btn)

        for label in FAST_STAGE2:
            btn = ui.Button(label=label, style=discord.ButtonStyle.secondary, row=1, disabled=True)
            btn.callback = self._make_stage2_callback(label)
            self._stage2_btns.append(btn)
            self.add_item(btn)

    def _make_stage1_callback(self, stage1: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only you bowl!", ephemeral=True)
                return
            if self.stage1_choice:
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                return
            self.stage1_choice = stage1
            for b in self._stage1_btns:
                b.disabled = True
                b.style    = discord.ButtonStyle.secondary
            for b in self._stage2_btns:
                b.disabled = False
                b.style    = self._S2_STYLES.get(b.label, discord.ButtonStyle.secondary)
            try:
                await interaction.response.edit_message(view=self)
            except Exception as e:
                print(f"[practice fast bowl] stage1 edit failed ({type(e).__name__}: {e})")
        return callback

    def _make_stage2_callback(self, delivery_button: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only you bowl!", ephemeral=True)
                return
            if not self.stage1_choice:
                await interaction.response.send_message("Pehle swing/speed choose karo.", ephemeral=True)
                return
            self.stop()
            delivery_internal = DELIVERY_BUTTON_MAP.get(delivery_button, delivery_button)
            self.game.pending_delivery          = delivery_button
            self.game.pending_delivery_internal = delivery_internal
            self.game.phase = "bat_select"

            speed    = random.uniform(*get_delivery_speed(delivery_button))
            combined = f"{self.stage1_choice} {delivery_button}"

            # Bot picks shot automatically
            shot_button, is_rec = _bot_pick_shot(delivery_button, self.stage1_choice, self.game)
            free_prefix = "🟡 **FREE HIT!**  " if self.is_free_hit else ""
            await interaction.response.edit_message(
                content=(
                    f"{free_prefix}**{_pname(self.game.current_bowler)}** : **{combined}** at {speed:.1f} km/h  ·  "
                    f"🤖 CPU plays **{shot_button}**"
                ),
                view=None,
                embed=None,
            )
            await _practice_process_delivery(
                interaction, self.game, shot_button,
                stage1_choice=self.stage1_choice,
                is_recommended_shot=is_rec,
            )
        return callback


class PracticeBowlingView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState, is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game        = game
        self.is_free_hit = is_free_hit
        self._add_buttons()

    def _add_buttons(self):
        btype   = self.game.current_bowler.get("bowling_type", "Off Spin") if self.game.current_bowler else "Off Spin"
        buttons = OFF_SPIN_BUTTONS if btype == "Off Spin" else LEG_SPIN_BUTTONS
        _STYLES = {
            "Offspin": discord.ButtonStyle.primary,  "Carrom": discord.ButtonStyle.danger,
            "Arm Ball":discord.ButtonStyle.success,   "Doosra": discord.ButtonStyle.danger,
            "Topspin": discord.ButtonStyle.success,   "Legspin":discord.ButtonStyle.primary,
            "Googly":  discord.ButtonStyle.danger,    "Flipper":discord.ButtonStyle.success,
            "Drifter": discord.ButtonStyle.danger,    "Slider": discord.ButtonStyle.success,
        }
        for i, label in enumerate(buttons):
            btn          = ui.Button(label=label, style=_STYLES.get(label, discord.ButtonStyle.secondary), row=i // 3)
            btn.callback = self._make_callback(label)
            self.add_item(btn)

    def _make_callback(self, delivery_button: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only you bowl!", ephemeral=True)
                return
            self.stop()
            btype             = self.game.current_bowler.get("bowling_type", "Off Spin")
            delivery_internal = resolve_delivery(delivery_button, btype)
            self.game.pending_delivery          = delivery_button
            self.game.pending_delivery_internal = delivery_internal
            self.game.phase = "bat_select"

            speed = random.uniform(*get_delivery_speed(delivery_button))
            shot_button, is_rec = _bot_pick_shot(delivery_button, game=self.game)
            free_prefix = "🟡 **FREE HIT!**  " if self.is_free_hit else ""
            await interaction.response.edit_message(
                content=(
                    f"{free_prefix}**{_pname(self.game.current_bowler)}** : **{delivery_button}** at {speed:.1f} km/h  ·  "
                    f"🤖 CPU plays **{shot_button}**"
                ),
                view=None,
                embed=None,
            )
            await _practice_process_delivery(
                interaction, self.game, shot_button,
                is_recommended_shot=is_rec,
            )
        return callback


# ─────────────────────────────────────────────────────────────────────────────
# Human batting view — bot bowled already, human picks shot
# ─────────────────────────────────────────────────────────────────────────────

class PracticeBattingView(_PracticeTimeoutMixin, ui.View):
    def __init__(self, game: GameState, *, stage1_choice: str = "", is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game          = game
        self.stage1_choice = stage1_choice
        self.is_free_hit   = is_free_hit

        delivery_btn = game.pending_delivery or ""
        if stage1_choice:
            self.recommended = FAST_SHOT_GUIDE.get((stage1_choice, delivery_btn), [])
        else:
            self.recommended = SPIN_SHOT_GUIDE.get(delivery_btn, [])

        self._add_buttons()

    def _add_buttons(self):
        STYLES = {
            "Drive":  discord.ButtonStyle.success,
            "Loft":   discord.ButtonStyle.danger,
            "Flick":  discord.ButtonStyle.success,
            "Pull":   discord.ButtonStyle.danger,
            "Cut":    discord.ButtonStyle.primary,
            "Sweep":  discord.ButtonStyle.primary,
            "Scoop":  discord.ButtonStyle.secondary,
            "Defend": discord.ButtonStyle.secondary,
            "Leave":  discord.ButtonStyle.secondary,
        }
        ROWS = {
            "Drive": 0, "Loft": 0, "Flick": 0, "Pull": 0, "Cut": 0,
            "Sweep": 1, "Scoop": 1, "Defend": 1, "Leave": 1,
        }
        for label in BATTING_BUTTONS:
            btn          = ui.Button(label=label, style=STYLES.get(label, discord.ButtonStyle.secondary), row=ROWS.get(label, 0))
            btn.callback = self._make_callback(label, label in self.recommended)
            self.add_item(btn)

    def _make_callback(self, shot: str, is_recommended: bool):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                await interaction.response.send_message("Only you bat!", ephemeral=True)
                return
            self.stop()
            await _practice_process_delivery(
                interaction, self.game, shot,
                stage1_choice=self.stage1_choice,
                is_recommended_shot=is_recommended,
            )
        return callback
