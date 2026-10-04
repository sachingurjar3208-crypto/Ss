import asyncio
import io
import os
import random
import discord
from discord import ui

_TOSS_IMG_PATH   = os.path.join(os.path.dirname(__file__), "toss_image.png")
_TOSS_HEADS_PATH = os.path.join(os.path.dirname(__file__), "toss_heads.png")
_TOSS_TAILS_PATH = os.path.join(os.path.dirname(__file__), "toss_tails.png")


def _toss_image_file() -> discord.File:
    return discord.File(_TOSS_IMG_PATH, filename="toss_image.png")


def _toss_result_file(result: str) -> tuple[discord.File, str]:
    if result == "Head" and os.path.exists(_TOSS_HEADS_PATH):
        return discord.File(_TOSS_HEADS_PATH, filename="toss_heads.png"), "toss_heads.png"
    if result == "Tail" and os.path.exists(_TOSS_TAILS_PATH):
        return discord.File(_TOSS_TAILS_PATH, filename="toss_tails.png"), "toss_tails.png"
    return discord.File(_TOSS_IMG_PATH, filename="toss_image.png"), "toss_image.png"

from game import GameState, _pname
from logic import calculate_outcome, player_attributes
from commentary import build_ball_commentary
from embeds import build_scoreboard_embed, build_playing_xi_embed, build_result_embed


def _delivery_announcement(bowler_label: str, speed: float, batter: discord.Member, *, free_hit: bool = False) -> str:
    """The single line shown right before the batter picks a shot, e.g.
    "**Inswing Yorker** is coming with **147.3 kmph** — @batter, pick your shot!"
    The bowler's name and the delivery type are NOT repeated in the result
    commentary afterwards — this line is the only place they're announced."""
    prefix = "**FREE HIT!**  " if free_hit else ""
    return f"{prefix}**{bowler_label}** is coming with **{speed:.1f} kmph** — {batter.mention}, pick your shot!"


# ─────────────────────────────────────────────────────────────────────────────
# Crash safety net — a match must NEVER get stuck because one Discord call
# (a slow network, an expired interaction, a rate limit) happened to fail.
#
# How it works:
#   1. Every step that decides something (toss result, bat/bowl choice,
#      openers, bowler, delivery type…) ALWAYS mutates game state first and
#      ONLY THEN tries to send the next prompt. Those state changes are plain
#      assignments (e.g. `game.current_bowler = bowler`), so re-running them
#      is harmless even if the send that follows had already failed and is
#      being retried.
#   2. `_run_safely` wraps that "send the next prompt" part. If it raises,
#      the error is logged and a small `_ContinueView` (one button) is
#      posted in the channel — pressing it just retries the exact same send,
#      no game data is touched again.
#   3. For steps deep inside ball-by-ball play (where the state change is
#      NOT safely repeatable — runs/wickets already added), the fallback
#      instead calls `_resume_prompt`, which never re-applies a game event;
#      it only LOOKS at the current state and re-sends whichever prompt
#      should be showing right now. That is always safe to call again.
# ─────────────────────────────────────────────────────────────────────────────

def _match_ids(game: GameState) -> set[int]:
    ids = set()
    for attr in ("challenger", "opponent", "batting_user", "bowling_user"):
        who = getattr(game, attr, None)
        if who is not None:
            ids.add(getattr(who, "id", who))
    return ids


class _ContinueView(ui.View):
    """Last-resort: one button that retries a failed send. Only used when
    BOTH the in-place edit AND a fresh channel message have already failed
    (e.g. a real outage) — normal hiccups never reach this."""

    def __init__(self, work, allowed_ids: set[int] | None = None, timeout: float = 600.0, attempt: int = 1):
        super().__init__(timeout=timeout)
        self.work = work
        self.allowed_ids = allowed_ids
        self.attempt = attempt

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.allowed_ids and interaction.user.id not in self.allowed_ids:
            await interaction.response.send_message("This match isn't yours to continue.", ephemeral=True)
            return False
        return True

    @ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def cont(self, interaction: discord.Interaction, button: ui.Button):
        for child in self.children:
            child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            pass
        self.stop()
        await _run_safely(
            self.work, allowed_ids=self.allowed_ids, notify_channel=interaction.channel,
            attempt=self.attempt + 1,
        )


_MAX_RECOVERY_ATTEMPTS = 3


async def _run_safely(work, *, allowed_ids: set[int] | None = None, notify_channel=None, attempt: int = 1) -> None:
    """Run `work()` (a no-arg async callable that only touches `channel` /
    `game`, never a specific interaction). If it raises, log it and — only
    as a last resort — post a manual Continue button so the match can
    never be silently stuck.

    If the SAME step fails `_MAX_RECOVERY_ATTEMPTS` times in a row, it's not
    a transient network hiccup — it's a real bug (bad asset, bad payload,
    etc). Stop offering a Continue button that will just fail identically
    forever, and say so plainly instead so it gets reported/fixed."""
    try:
        await work()
    except Exception as e:
        print(f"[match recovery] step failed (attempt {attempt}): {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        if notify_channel is None:
            return
        try:
            if attempt >= _MAX_RECOVERY_ATTEMPTS:
                await notify_channel.send(
                    content=(
                        "This step keeps failing the same way — that's not a network blip, "
                        "it's a bug. Please report this to the bot admins; your match progress "
                        "is still saved, but it can't continue on its own from here."
                    ),
                )
            else:
                await notify_channel.send(
                    content="A small hiccup sending that step — nothing was lost. Tap to continue.",
                    view=_ContinueView(work, allowed_ids, attempt=attempt),
                )
        except discord.HTTPException:
            pass


async def _advance(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: ui.View | None = None,
    file: discord.File | None = None,
    allowed_ids: set[int] | None = None,
) -> None:
    """Show the next step of the match. This is THE fix for "interaction
    failed": it never depends on a single fragile Discord call succeeding.

    1. Try to edit the message the button/select lives on (tidiest look).
    2. If that fails for ANY reason (expired token, already acknowledged,
       a network hiccup — exactly the situations that show the user
       "This interaction failed") — instead of giving up, just POST A NEW
       MESSAGE with the same content. This alone fixes the vast majority
       of cases, silently, with no visible error to the players.
    3. Only if that fresh message ALSO fails does a manual Continue button
       appear, as a final safety net.
    """
    kwargs = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    kwargs["view"] = view  # explicit, even if None — clears old buttons on edit

    try:
        await interaction.response.edit_message(**kwargs)
        return
    except Exception as e:
        print(f"[match recovery] in-place edit failed ({type(e).__name__}: {e}) — sending a fresh message instead")

    async def _send():
        send_kwargs = dict(kwargs)
        if view is None:
            send_kwargs.pop("view")  # channel.send(view=None) is fine, but keep kwargs minimal
        if file is not None:
            send_kwargs["file"] = file
        await channel.send(**send_kwargs)

    await _run_safely(_send, allowed_ids=allowed_ids, notify_channel=channel)


async def _send_opener_prompt(channel: discord.TextChannel, game: GameState) -> None:
    xi_embed    = build_playing_xi_embed(game.get_batting_team())
    opener_view = OpenerSelectView(game)
    opener_view._channel = channel
    await channel.send(
        content=f"Select your openers, {game.batting_user.mention}",
        embed=xi_embed,
        view=opener_view,
    )


async def _resume_prompt(channel: discord.TextChannel, game: GameState) -> bool:
    """Re-send whatever prompt SHOULD currently be showing, purely by
    reading the current game state — it never repeats a game event (no
    runs/wickets/overs are touched here). Safe to call as many times as
    needed. Returns False if there's nothing to resume (match not live).

    Order matters: openers are checked before "current bowler" / "next
    batsman", because right after `start_second_innings()` runs, EVERY one
    of those is empty at once (striker, non_striker, current_bowler are all
    reset together) — openers must be picked first, same as at the very
    start of the match."""
    if game.is_innings_over():
        if game.innings == 1:
            await _start_second_innings(channel, game)
        else:
            result_embed = build_result_embed(game)
            await channel.send(embed=result_embed)
            active_games.pop(channel.id, None)
        return True

    if game.striker is None and game.non_striker is None:
        # Either the match/innings just started and openers were never
        # confirmed sent, or start_second_innings() already ran (which
        # resets both to None) but its own openers prompt failed to send.
        await _send_opener_prompt(channel, game)
        return True

    if game.current_bowler is None:
        await _send_next_bowler_prompt(channel, game)
        return True

    if game.striker is None or game.non_striker is None:
        available = game.get_available_batsmen()
        if available:
            try:
                xi_embed = build_playing_xi_embed(game.get_batting_team())
            except Exception:
                xi_embed = None
            nb_view = NextBatsmanView(game)
            nb_view._channel = channel
            await channel.send(
                content=f"Choose your next batter {game.batting_user.mention}",
                embed=xi_embed, view=nb_view,
            )
            return True

    if game.pending_delivery:
        # A delivery was already decided — resend the shot-selection prompt
        # with the same announcement (speed is cached when the delivery is set).
        speed = getattr(game, "pending_delivery_speed", None)
        stage1_choice = getattr(game, "pending_stage1_choice", "")
        is_free_hit = getattr(game, "current_ball_free_hit", False)
        label = f"{stage1_choice} {game.pending_delivery}".strip()
        batting_view = BattingView(game, stage1_choice=stage1_choice, is_free_hit=is_free_hit)
        batting_view._channel = channel
        content = (
            _delivery_announcement(label, speed, game.batting_user, free_hit=is_free_hit)
            if speed is not None
            else f"**{label}** — {game.batting_user.mention}, pick your shot!"
        )
        await channel.send(content=content, view=batting_view)
        return True

    await _send_bowling_prompt(channel, game)
    return True


async def _safe_next(channel: discord.TextChannel, game: GameState, work) -> None:
    """Run `work()` (a no-arg async callable that only sends/edits — no game
    event is repeated by calling it). If it fails, fall back to a Continue
    button that calls `_resume_prompt`, which re-derives the right prompt
    from state instead of retrying `work` itself."""
    try:
        await work()
    except Exception as e:
        print(f"[match recovery] step failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        try:
            await channel.send(
                content="A small hiccup — your match is safe. Tap to continue.",
                view=_ContinueView(lambda: _resume_prompt(channel, game), _match_ids(game)),
            )
        except discord.HTTPException:
            pass
from data import (
    TOSS_HEAD_EMOJI, TOSS_TAIL_EMOJI, TIMELINE_EMOJIS,
    FAST_STAGE1, FAST_STAGE2,
    OFF_SPIN_BUTTONS, LEG_SPIN_BUTTONS,
    BATTING_BUTTONS, SHOT_BUTTON_MAP,
    get_delivery_speed, resolve_delivery, BOWLING_TYPE_ICON,
    DELIVERY_BUTTON_MAP,
    FAST_SHOT_GUIDE, SPIN_SHOT_GUIDE,
    NOT_OUT_EMOJI, LBW_EMOJI,
)
from career_stats import save_innings as _save_innings


def save_game_innings_stats(game, batting_user_id=None, bowling_user_id=None, is_final=False):
    """Persist per-card stats for the innings that just ended."""
    try:
        _save_innings(game, is_final=is_final)
    except Exception as e:
        print(f"[career_stats] save failed: {type(e).__name__}: {e}")


def get_career_stats(user_id, player_name):
    return {}
from match_records import record_match_result as _record_match_result


def _save_match_record(game):
    """Derive winner/loser and persist to match_records. Ignores ties."""
    t1 = game.runs[0]
    t2 = game.runs[1]
    if t1 == t2:
        return  # tie — don't record
    # After start_second_innings():
    #   bowling_user_id  = innings-1 batter (defending team)
    #   batting_user_id  = innings-2 batter (chasing team)
    inn1_uid = game.bowling_user_id
    inn2_uid = game.batting_user_id
    if t2 > t1:
        winner_id, loser_id = inn2_uid, inn1_uid
        winner_score, loser_score = t2, t1
    else:
        winner_id, loser_id = inn1_uid, inn2_uid
        winner_score, loser_score = t1, t2
    try:
        _record_match_result(winner_id, loser_id, winner_score, loser_score, game.overs)
    except Exception as e:
        print(f"[match_records] Failed to save result: {e}")
    # Economy: reward winner and loser with coins
    try:
        from economy import reward_match_winner
        reward_match_winner(str(winner_id), str(loser_id))
    except Exception as e:
        print(f"[economy] Failed to reward match coins: {e}")
try:
    from stats_image import generate_bat_card, generate_bowl_card
except ImportError:
    generate_bat_card = generate_bowl_card = None
try:
    from database import get_background
except ImportError:
    def get_background(*args, **kwargs):
        return None
try:
    from card_generator import generate_card_image
except ImportError:
    generate_card_image = None
try:
    from media import get_player_media, get_milestone_gif, get_umpire_gif
except ImportError:
    def get_player_media(*args, **kwargs):
        return None

    def get_milestone_gif(*args, **kwargs):
        return None

    def get_umpire_gif(*args, **kwargs):
        return None

active_games: dict[int, GameState] = {}

VIEW_TIMEOUT = 300  # 5 minutes per action


# ─────────────────────────────────────────────────────────────────────────────
# Timeout mixin
# ─────────────────────────────────────────────────────────────────────────────

class _TimeoutMixin:
    """
    Attach to every in-match View.
    Set view._channel = channel after creating the view.
    Call self.stop() in every successful callback so the timer cancels.
    """
    _channel: discord.TextChannel | None = None

    def _get_leaver_id(self, game) -> int | None:
        """
        Identify whose turn it was when this view timed out.
        Each view class is expected by a specific user — return their Discord user ID.
        """
        class_name = type(self).__name__
        # Bowling views — bowling_user_id is responsible
        if class_name in (
            "FastBowlStage1View", "FastBowlStage2View",
            "BowlingView", "BowlerSelectView", "NextBowlerView",
        ):
            return getattr(game, "bowling_user_id", None)
        # Batting views — batting_user_id is responsible
        if class_name in (
            "BattingView", "OpenerSelectView", "StrikerDesignateView",
            "NextBatsmanView", "DRSView",
        ):
            return getattr(game, "batting_user_id", None)
        # Toss / bat-bowl choice — challenger calls toss, toss winner picks bat/bowl
        if class_name == "TossView":
            return getattr(game.challenger, "id", None) if hasattr(game, "challenger") else None
        if class_name == "BatBowlView":
            return getattr(game, "toss_winner_id", None)
        # Fallback — unknown view, can't tell who left
        return None

    async def on_timeout(self):
        ch   = self._channel
        game = getattr(self, "game", None)
        if not ch or not game:
            return
        if active_games.get(ch.id) is not game:
            return
        if game.__dict__.get("_afk_sent"):
            return
        game._afk_sent = True
        active_games.pop(ch.id, None)

        # ── Identify and penalise the leaver ────────────────────────────────
        leaver_id   = self._get_leaver_id(game)
        penalty     = 20_000
        penalty_msg = ""
        leaver_mention = ""

        if leaver_id is not None:
            try:
                from economy import add_coins, get_balance, fmt_coins
                # add_coins with negative amount — balance CAN go below 0
                old_bal  = get_balance(str(leaver_id))
                new_bal  = add_coins(str(leaver_id), -penalty, "Left a match midway")
                # Resolve a display name for the message
                leaver_obj = getattr(game, "challenger", None)
                if leaver_obj and getattr(leaver_obj, "id", None) == leaver_id:
                    leaver_mention = leaver_obj.mention
                else:
                    opp = getattr(game, "opponent", None)
                    if opp and getattr(opp, "id", None) == leaver_id:
                        leaver_mention = opp.mention
                    else:
                        leaver_mention = f"<@{leaver_id}>"
                penalty_msg = (
                    f"\n{leaver_mention} left the match midway and was fined "
                    f"**{fmt_coins(penalty)}**!\n"
                    f"New balance: **{fmt_coins(new_bal)}**"
                    + (" (negative balance)" if new_bal < 0 else "")
                )
            except Exception as e:
                print(f"[economy] penalty deduction failed: {e}")

        try:
            await ch.send(
                "**Match abandoned — a player went AFK.**\n"
                "No response was received in time. Use `csmp @user <overs>` to start a fresh match."
                + penalty_msg
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dismissal_type(text: str) -> str:
    """Guess the dismissal from the commentary line (used for bowler credit)."""
    t = (text or "").upper()
    if "LBW" in t or "PLUMB" in t or "IN FRONT" in t:
        return "LBW"
    if "STUMP" in t:
        return "Stumped"
    if "CAUGHT" in t or "CATCH" in t or "HOLES OUT" in t:
        return "Caught"
    return "Bowled"


def _timeline_emoji(outcome: str) -> str:
    return TIMELINE_EMOJIS.get(outcome, "•")


def _drs_overturn_chance(delivery_internal: str, shot_internal: str) -> float:
    HIGH_RISK = {
        ("Yorker",  "Drive"), ("Yorker",  "Pull"),  ("Yorker",  "Lofted"),
        ("Bouncer", "Drive"), ("Bouncer", "Sweep"),  ("Bouncer", "Flick"),
        ("Swing",   "Drive"), ("Swing",   "Lofted"), ("Swing",   "Reverse-Sweep"),
        ("Googly",  "Sweep"), ("Flipper", "Sweep"),  ("Doosra",  "Sweep"),
    }
    LOW_RISK = {
        ("Yorker",      "Defend"),  ("Full",       "Defend"),
        ("Good Length", "Defend"),  ("Arm Ball",   "Flick"),
        ("Drift Ball",  "Sweep"),   ("Leg Break",  "Cut"),
        ("Off Break",   "Sweep"),   ("Googly",     "Reverse-Sweep"),
        ("Slider",      "Sweep"),   ("Carrom Ball","Flick"),
    }
    key = (delivery_internal, shot_internal)
    if key in HIGH_RISK: return 0.15
    if key in LOW_RISK:  return 0.60
    return 0.40


async def _get_card_image_bytes(player: dict) -> bytes | None:
    try:
        bg_pathname = player.get("background_pathname") or ""
        bg_rec      = get_background(bg_pathname) if bg_pathname else None
        bg_url      = bg_rec["url"] if bg_rec else None
        return await generate_card_image(player, bg_url=bg_url)
    except Exception:
        return None


async def _bat_stats_file(user_id: int, player: dict) -> discord.File | None:
    try:
        name     = _pname(player)
        stats    = get_career_stats(str(user_id), name)
        card_img = await _get_card_image_bytes(player)
        img      = generate_bat_card(name, stats, card_image_bytes=card_img)
        return discord.File(fp=io.BytesIO(img), filename="bat_stats.png")
    except Exception as e:
        print(f"[stats] bat card error: {e}")
        return None


async def _bowl_stats_file(user_id: int, player: dict) -> discord.File | None:
    try:
        name     = _pname(player)
        stats    = get_career_stats(str(user_id), name)
        card_img = await _get_card_image_bytes(player)
        img      = generate_bowl_card(name, stats, card_image_bytes=card_img)
        return discord.File(fp=io.BytesIO(img), filename="bowl_stats.png")
    except Exception as e:
        print(f"[stats] bowl card error: {e}")
        return None


async def _send_commentary_with_gifs(
    channel: discord.TextChannel,
    commentary: str,
    embed: discord.Embed,
    gif_urls: list[str],
    interaction: discord.Interaction | None = None,
):
    """
    Show ball commentary + scoreboard embed.
    If `interaction` is given, edit the batting-prompt message in-place (keeps
    the channel clean).  Otherwise send a fresh message.
    """
    if interaction is not None:
        try:
            await interaction.edit_original_response(content=commentary, embed=embed)
        except Exception:
            await channel.send(content=commentary, embed=embed)
    else:
        await channel.send(content=commentary, embed=embed)

    for url in gif_urls:
        try:
            await channel.send(url)
        except Exception:
            pass


async def _post_delivery_prompt(interaction, channel, game, *, content, view, allowed_ids=None):
    """Show the next delivery the Glenn McGrath way:
    1) the old bowling message loses its buttons,
    2) a fresh scoreboard is posted with the current state,
    3) the "X is coming with N kmph" line + shot buttons go BELOW it.
    So the next-bowl line is always the last thing under the score box."""
    try:
        await interaction.response.edit_message(view=None)
    except Exception:
        pass  # cosmetic only

    async def _send():
        await channel.send(embed=build_scoreboard_embed(game))
        await channel.send(content=content, view=view)

    await _run_safely(_send, allowed_ids=allowed_ids, notify_channel=channel)


async def _send_bowling_prompt(
    channel: discord.TextChannel,
    game: GameState,
    *,
    intro: str | None = None,
    files: list | None = None,
):
    """Send the bowling UI for the current bowler.

    `intro` (e.g. "X starts a new over") is rendered as the last line INSIDE
    the scoreboard box, so the next-bowler info sits directly under the score.
    """
    # Guard – if bowler was cleared (innings ended) don't crash
    if not game.current_bowler:
        return
    embed = build_scoreboard_embed(game)
    if intro:
        embed.set_footer(text=intro)
    btype = game.current_bowler.get("bowling_type", "Fast")

    # Show FREE HIT banner if the previous ball was a no-ball
    is_free_hit = getattr(game, "pending_free_hit", False)
    if is_free_hit:
        embed.set_footer(text="FREE HIT — batsman cannot be dismissed (except run out)")
        game.pending_free_hit = False  # consume the flag
    # Remember that THIS ball is a free hit — the shot handler reads it from here
    # (the flag above is already cleared by the time the batter plays).
    game.current_ball_free_hit = is_free_hit

    if btype == "Fast":
        view = FastBowlStage1View(game, is_free_hit=is_free_hit)
    else:
        view = BowlingView(game, is_free_hit=is_free_hit)
    view._channel = channel
    prefix = "**FREE HIT!**  " if is_free_hit else ""
    content = f"{prefix}Choose delivery  {game.bowling_user.mention}"
    await channel.send(content=content, embed=embed, view=view, files=files or [])


async def _send_next_bowler_prompt(channel: discord.TextChannel, game: GameState):
    """Prompt the bowling captain to choose the next over's bowler."""
    xi_embed = build_playing_xi_embed(game.get_bowling_team())
    nb_view  = NextBowlerView(game)
    nb_view._channel = channel
    await channel.send(
        content=f"Over is up — choose your next bowler {game.bowling_user.mention}",
        embed=xi_embed,
        view=nb_view,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Core delivery processor
# ─────────────────────────────────────────────────────────────────────────────

async def _process_delivery(
    interaction: discord.Interaction,
    game: GameState,
    shot_button: str,
    *,
    stage1_choice: str = "",
    is_recommended_shot: bool = False,
    guide_entry_exists: bool = False,
    is_free_hit: bool = False,
):
    shot_internal     = SHOT_BUTTON_MAP.get(shot_button, "Drive")
    delivery_button   = game.pending_delivery
    delivery_internal = game.pending_delivery_internal
    bowler            = game.current_bowler
    striker           = game.striker

    # The shot is being played now, so this delivery is no longer "pending" —
    # clear it so match-recovery (_resume_prompt) doesn't think a shot is
    # still waiting to be picked once this ball has actually been bowled.
    game.pending_delivery          = None
    game.pending_delivery_internal = None
    game.pending_delivery_speed    = None
    game.pending_stage1_choice     = ""

    # BUG FIX #1: Guard against race-condition where game state was reset
    # (timeout, innings ended) between button render and user click.
    if not bowler or not striker:
        try:
            await interaction.response.send_message(
                "Game state error — the match may have ended or timed out. "
                "Please wait for the next prompt.",
                ephemeral=True,
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
        bowler_attrs=player_attributes(bowler),
        batsman_attrs=player_attributes(striker),
        innings=game.innings,
        current_over=current_over,
        rrr=game.rrr(),
        balls_since_wicket=game.balls_since_wicket,
        partnership_runs=game.partnership_runs,
        is_recommended_shot=is_recommended_shot,
        guide_entry_exists=guide_entry_exists,
        total_overs=game.overs,
    )

    # ── Free-hit: batsman cannot be dismissed (except run out) ───────────
    # If the previous delivery was a no-ball, this is a free-hit.
    # We consume the flag here (before _send_bowling_prompt clears it) so
    # the flag is accurate for this ball's processing.
    this_ball_is_free_hit = bool(is_free_hit or getattr(game, "current_ball_free_hit", False))
    game.current_ball_free_hit = False
    if this_ball_is_free_hit and outcome == "W":
        # Downgrade to a dot ball — batsman survives
        outcome  = "0"
        is_extra = False
        commentary_suffix = "  *(Free Hit — NOT OUT!)*"
    else:
        commentary_suffix = ""

    commentary = build_ball_commentary(
        bowler_name=bname,
        bowler_ovr=bowler.get("ovr", 80),
        delivery_button=delivery_button,
        delivery_internal=delivery_internal,
        speed=speed,
        batsman_name=sname,
        shot_button=shot_button,
        shot_internal=shot_internal,
        outcome=outcome,
        bowling_type=bowler.get("bowling_type", "Fast"),
        stage1_choice=stage1_choice,
    ) + commentary_suffix

    # BUG FIX #8: bowler_stats[bname] is initialised inside BowlerSelectView
    # when the bowler is first chosen, but the snapshot below is taken before
    # any guard.  If the initialization order ever shifts this would silently
    # return 0 via .get({}) — correct by luck, not by design.
    # Ensure the entry exists here so the snapshot is always reading real data.
    if bname not in game.bowler_stats:
        game.bowler_stats[bname] = {"balls": 0, "runs": 0, "wickets": 0}

    # Snapshot pre-delivery state for milestone / GIF detection
    striker_runs_before   = game.batsman_stats.get(sname, {}).get("runs", 0)
    bowler_wickets_before = game.bowler_stats[bname]["wickets"]

    # ── Update game state ─────────────────────────────────────────────────

    if outcome == "W":
        game.add_legal_ball()
        # Save partnership state BEFORE add_wicket() wipes it — DRS may restore it
        _pre_wicket_pship_runs  = game.partnership_runs
        _pre_wicket_pship_balls = game.partnership_balls
        _pre_wicket_balls_since = game.balls_since_wicket
        _pre_wicket_striker     = game.striker
        game.add_wicket(_dismissal_type(commentary))
        # Timeline emoji deferred until DRS decision (or added immediately if no DRS)

    elif outcome == "Wd":
        game.add_extra_runs(1, charge_bowler=True)
        game.timeline.append(_timeline_emoji("Wd"))
        # BUG FIX #6: a wide concedes 1 penalty run but the batsman faces no
        # ball, so it should still count as a dot for consecutive_dots (the
        # batter got nothing off the bat).  add_extra_runs() doesn't touch
        # consecutive_dots, so we increment it manually here.
        game.consecutive_dots += 1
        if this_ball_is_free_hit:
            game.pending_free_hit = True   # a wide does not use up the free hit
        # Wide: re-bowled; no legal ball, no batsman credit, no strike rotation

    elif outcome == "NB":
        game.add_extra_runs(1, charge_bowler=True)
        game.timeline.append(_timeline_emoji("NB"))
        # No-ball: re-bowled; no legal ball. Mark free-hit pending.
        game.pending_free_hit = True

    elif outcome == "NB+1":
        # NB+1: penalty run (not credited to batsman) + batsman's single.
        game.add_extra_runs(1, charge_bowler=True)
        game.add_runs(1)
        # BUG FIX #4: partnership_runs was incremented by add_runs() but
        # partnership_balls was not (no add_legal_ball() call on a no-ball).
        # This caused the momentum modifier to trigger too early.
        # Manually sync partnership_balls so runs and balls stay in step.
        game.partnership_balls += 1
        # BUG FIX: balls_since_wicket was not updated on NB+1, keeping
        # the new batter in the vulnerability window (×1.4 wicket risk)
        # indefinitely against illegal deliveries. Mirror partnership_balls.
        game.balls_since_wicket += 1
        game.timeline.append(_timeline_emoji("NB+1"))
        # Batsmen crossed on the single — rotate strike
        game.rotate_strike()
        # NB+1 is NOT a legal ball — do NOT call add_legal_ball().
        # However, if current_over_balls was already at 5 before this no-ball,
        # the over still hasn't ended (this ball doesn't count), so we just
        # re-bowl. Free-hit still applies.
        game.pending_free_hit = True

    else:
        # Normal outcomes: 0, 1, 2, 3, 4, 6
        game.add_legal_ball()
        game.add_runs(int(outcome))
        game.timeline.append(_timeline_emoji(outcome))
        if outcome == "4" and sname in game.batsman_stats:
            game.batsman_stats[sname]["fours"] = game.batsman_stats[sname].get("fours", 0) + 1
        elif outcome == "6" and sname in game.batsman_stats:
            game.batsman_stats[sname]["sixes"] = game.batsman_stats[sname].get("sixes", 0) + 1
        # Odd runs → rotate strike
        if outcome in ("1", "3"):
            game.rotate_strike()

    # ── GIF collection ────────────────────────────────────────────────────
    gif_urls: list[str] = []

    if outcome in ("4", "6"):
        event = "four" if outcome == "4" else "six"
        gif = get_player_media(sname, event)
        if gif:
            gif_urls.append(gif)
    elif outcome == "W":
        wkts_now = game.bowler_stats.get(bname, {}).get("wickets", 0)
        if wkts_now >= 5 and bowler_wickets_before < 5:
            gif = get_player_media(bname, "5wicket")
        else:
            gif = get_player_media(bname, "wicket")
        if gif:
            gif_urls.append(gif)

    # Umpire GIFs (global — fire for every player on top of player GIFs)
    _umpire_event = {
        "4": "four", "6": "six", "W": "out",
        "Wd": "wide", "NB": "noball", "NB+1": "noball",
    }.get(outcome)
    if _umpire_event:
        ump_gif = get_umpire_gif(_umpire_event)
        if ump_gif:
            gif_urls.append(ump_gif)

    # Batting milestone GIFs (50, 100, 150, …)
    if outcome in ("1", "2", "3", "4", "6"):
        runs_after = game.batsman_stats.get(sname, {}).get("runs", 0)
        for milestone in (50, 100, 150, 200, 250, 300):
            if striker_runs_before < milestone <= runs_after:
                gif = get_milestone_gif(sname, str(milestone))
                if not gif:
                    gif = get_player_media(sname, str(milestone))
                if gif:
                    gif_urls.append(gif)
                break

    # ── Acknowledge the shot button (remove it from the message) ─────────
    # BUG FIX #4: always ack the interaction immediately to avoid
    # "This interaction failed" errors. We edit in-place to remove the view.
    try:
        await interaction.response.edit_message(view=None)
    except discord.InteractionResponded:
        pass

    channel      = interaction.channel
    embed        = build_scoreboard_embed(game)
    innings_over = game.is_innings_over()

    # Everything below only SENDS messages — every run/wicket/over update for
    # this ball is already committed above. So if anything here fails (a
    # network hiccup, an expired interaction reference…), it is always safe
    # to fall back to _resume_prompt: it never repeats a game event, it just
    # looks at the state we already have and shows whichever prompt belongs
    # next — the match can never get stuck on a botched send.
    try:
        # ── Wicket (not the last one) → show DRS ─────────────────────────
        if outcome == "W" and not innings_over:
            await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction)

            batting_uid = game.batting_user_id
            reviews     = game.drs_reviews.get(batting_uid, 0)
            if reviews > 0:
                drs_view = DRSView(
                    game, channel, delivery_internal, shot_internal,
                    commentary=commentary,
                    pre_wicket_pship_runs=_pre_wicket_pship_runs,
                    pre_wicket_pship_balls=_pre_wicket_pship_balls,
                    pre_wicket_balls_since=_pre_wicket_balls_since,
                    pre_wicket_striker=_pre_wicket_striker,
                )
                drs_view.message = await channel.send(
                    content=(
                        f"**DECISION REVIEW SYSTEM**\n"
                        f"{game.batting_user.mention} — On-field decision: **OUT**\n"
                        f"Reviews remaining: **{reviews}**\n"
                        f"Do you want to challenge the umpire's decision?"
                    ),
                    view=drs_view,
                )
                return
            # No reviews left — stamp W on timeline and proceed
            game.timeline.append(_timeline_emoji("W"))
            await _handle_wicket_fall(channel, game)
            return

        # ── Innings over ──────────────────────────────────────────────────
        if innings_over:
            if outcome == "W":
                game.timeline.append(_timeline_emoji("W"))
            if game.innings == 1:
                await _do_innings_break(channel, game, commentary, embed, gif_urls, interaction)
            else:
                save_game_innings_stats(game, game.batting_user_id, game.bowling_user_id, is_final=True)
                _save_match_record(game)
                await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction)
                result_embed = build_result_embed(game)
                await channel.send(embed=result_embed)
                active_games.pop(channel.id, None)
            return

        # ── Over ended (no wicket at this point) ────────────────────────
        # BUG FIX #5: "over ended" check must use current_over_balls, NOT
        # is_extra.  Wides and no-balls don't increment current_over_balls so
        # they can never trigger this branch, but the original double-checked
        # `not is_extra` which could mask bugs — kept here as explicit safety.
        over_ended = (not is_extra) and (game.current_over_balls >= 6)
        if over_ended:
            # Rotate strike at end of over ONLY if runs were even (odd already rotated)
            if outcome not in ("1", "3"):
                game.rotate_strike()
            game.timeline.append("|")
            game.end_over()
            await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction)
            await _send_next_bowler_prompt(channel, game)
            return

        # ── Extras (wide / no-ball) — re-bowl ────────────────────────────
        if is_extra:
            await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction)
            # NB outcomes set pending_free_hit; wides do not.
            # _send_bowling_prompt reads and clears the flag to show FREE HIT label.
            await _send_bowling_prompt(channel, game)
            return

        # ── Normal continue ───────────────────────────────────────────────
        await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction)
        await _send_bowling_prompt(channel, game)

    except Exception as e:
        print(f"[match recovery] _process_delivery tail failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        try:
            await channel.send(
                content="A small hiccup showing that result — your score is safe. Tap to continue.",
                view=_ContinueView(lambda: _resume_prompt(channel, game), _match_ids(game)),
            )
        except discord.HTTPException:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Wicket fall handler
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_wicket_fall(channel, game: GameState):
    """
    Show next-batsman UI.  Commentary has already been sent before this runs.
    Handles all-out case (innings transition) without resending commentary.
    """
    available = game.get_available_batsmen()

    if not available:
        # All out
        if game.innings == 1:
            await _do_innings_break_allout(channel, game)
        else:
            save_game_innings_stats(game, game.batting_user_id, game.bowling_user_id, is_final=True)
            _save_match_record(game)
            result_embed = build_result_embed(game)
            await channel.send(embed=result_embed)
            active_games.pop(channel.id, None)
        return

    # BUG FIX #6: clear game.striker before showing the dropdown so that
    # get_available_batsmen() doesn't accidentally include the dismissed
    # batsman (who is still referenced in game.striker at this point because
    # add_wicket() only appends to dismissed[], it doesn't clear game.striker).
    game.striker = None

    try:
        xi_embed = build_playing_xi_embed(game.get_batting_team())
    except Exception as e:
        print(f"[wicket] XI embed error: {e}")
        xi_embed = None

    nb_view = NextBatsmanView(game)
    nb_view._channel = channel
    await channel.send(
        content=f"Wicket! Choose your next batter {game.batting_user.mention}",
        embed=xi_embed,
        view=nb_view,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Innings transition helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _do_innings_break(channel, game: GameState, commentary, embed, gif_urls=None, interaction=None):
    """End of innings via overs exhausted: save stats, send commentary, start break."""
    save_game_innings_stats(game, game.batting_user_id, game.bowling_user_id)
    await _send_commentary_with_gifs(channel, commentary, embed, gif_urls or [], interaction)
    await _start_second_innings(channel, game)


async def _do_innings_break_allout(channel, game: GameState):
    """End of innings via all-out: commentary already sent, just announce, save & start break."""
    save_game_innings_stats(game, game.batting_user_id, game.bowling_user_id)
    team_name = game.get_batting_team().get("name", "The batting team")
    total_runs = game.current_runs
    overs_used = game.overs_str()
    try:
        await channel.send(
            f"**ALL OUT!**  {team_name} are bowled out for **{total_runs}** "
            f"in {overs_used} overs!"
        )
    except Exception:
        pass
    await _start_second_innings(channel, game)


async def _start_second_innings(channel, game: GameState):
    game.start_second_innings()
    target  = game.target()
    balls   = game.overs * 6
    rrr_val = round((target / balls) * 6, 1) if balls > 0 else 0

    # BUG FIX #7: target() returns runs[0]+1 (i.e. the runs needed to WIN),
    # but the innings-break message should show the score to BEAT, not the
    # winning target.  Display as "<team> require <target> to win".
    score_to_beat = game.runs[0]
    await channel.send(
        content=(
            f"**Innings Break!**\n"
            f"**{game.get_batting_team()['name']} require {score_to_beat + 1} to win "
            f"off {balls} balls  (RRR: {rrr_val})**"
        )
    )
    await _send_opener_prompt(channel, game)


# ─────────────────────────────────────────────────────────────────────────────
# Accept / Decline
# ─────────────────────────────────────────────────────────────────────────────

class AcceptDeclineView(ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=30)
        self.game  = game
        self._done = False

    @ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.game.opponent.id:
            await interaction.response.send_message(
                "Only the challenged player can accept!", ephemeral=True
            )
            return
        if self._done:
            return
        self._done = True
        self.stop()

        channel = interaction.channel
        allowed = _match_ids(self.game)

        try:
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.set_footer(text="Challenge accepted! Toss coming up…")
                embed.color = discord.Color.from_rgb(30, 160, 80)
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception:
            pass  # cosmetic only — the toss below is sent regardless

        async def _send_toss():
            toss_embed = _build_toss_embed(self.game)
            toss_view  = TossView(self.game)
            toss_view._channel = channel
            await channel.send(embed=toss_embed, view=toss_view, file=_toss_image_file())
        await _run_safely(_send_toss, allowed_ids=allowed, notify_channel=channel)

    @ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        both_ids = (self.game.challenger.id, self.game.opponent.id)
        if interaction.user.id not in both_ids:
            await interaction.response.send_message(
                "Only the two players can decline this match.", ephemeral=True
            )
            return
        if self._done:
            return
        self._done = True
        self.stop()
        active_games.pop(interaction.channel_id, None)

        who   = interaction.user.display_name
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.set_footer(text=f"{who} declined the match.")
            embed.color = discord.Color.from_rgb(180, 30, 30)
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self):
        if self._done:
            return
        self._done = True
        ch = next((c for c, g in active_games.items() if g is self.game), None)
        if ch is not None:
            active_games.pop(ch, None)
        try:
            if self.message:
                embed = self.message.embeds[0] if self.message.embeds else None
                if embed:
                    embed.set_footer(text="Match cancelled — no response within 30 seconds.")
                    embed.color = discord.Color.from_rgb(100, 100, 100)
                await self.message.edit(embed=embed, view=None)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Toss
# ─────────────────────────────────────────────────────────────────────────────

def _build_toss_embed(game: GameState) -> discord.Embed:
    sep = "──────────────────────"
    embed = discord.Embed(title="COIN TOSS", color=discord.Color.from_rgb(212, 175, 55))
    embed.description = (
        f"{sep}\n"
        f"**Challenger:**  {game.challenger.mention}\n"
        f"**Opponent:**    {game.opponent.mention}\n"
        f"{sep}\n\n"
        f"{game.challenger.mention}, it's your call!\n"
        f"Choose **Heads** or **Tails** below."
    )
    embed.set_footer(text="The toss winner chooses to bat or bowl.")
    embed.set_image(url="attachment://toss_image.png")
    return embed


class TossView(_TimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self._add_buttons()

    def _add_buttons(self):
        head_btn = ui.Button(label="HEADS", emoji="🪙", style=discord.ButtonStyle.primary,   row=0)
        tail_btn = ui.Button(label="TAILS", emoji="🪙", style=discord.ButtonStyle.secondary, row=0)
        head_btn.callback = self._make_callback("Head")
        tail_btn.callback = self._make_callback("Tail")
        self.add_item(head_btn)
        self.add_item(tail_btn)

    def _make_callback(self, call: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.challenger.id:
                await interaction.response.send_message("Only the challenger calls the toss!", ephemeral=True)
                return
            self.stop()
            result = random.choice(["Head", "Tail"])
            won    = (call == result)
            winner = self.game.challenger if won else self.game.opponent
            loser  = self.game.opponent   if won else self.game.challenger
            self.game.toss_winner_id = winner.id

            channel = interaction.channel
            allowed = _match_ids(self.game)
            coin_face = "**HEADS**" if result == "Head" else "**TAILS**"
            sep       = "──────────────────────"

            # Best-effort only: the actual result/next-step below never
            # depends on this succeeding, so a failure here is harmless.
            try:
                await interaction.response.edit_message(view=None, embed=None, content="Coin tossed!")
            except Exception:
                pass

            async def _send_result():
                result_file, result_fname = _toss_result_file(result)
                result_embed = discord.Embed(title="COIN TOSS", color=discord.Color.from_rgb(212, 175, 55))
                result_embed.description = (
                    f"{sep}\nThe coin spins in the air…\n\n"
                    f"{coin_face}\n\n"
                    f"**{winner.display_name}** won the toss!\n{sep}"
                )
                result_embed.set_footer(text=f"{loser.display_name} called {call.lower()} — coin landed {result.lower()}.")
                result_embed.set_image(url=f"attachment://{result_fname}")
                await channel.send(embed=result_embed, file=result_file)
            await _run_safely(_send_result, allowed_ids=allowed, notify_channel=channel)

            async def _send_choice():
                bb_embed = discord.Embed(title="CHOOSE YOUR STRATEGY", color=discord.Color.from_rgb(8, 22, 60))
                bb_embed.description = (
                    f"{sep}\n**{winner.display_name}** won the toss!\n\n"
                    f"What would you like to do, {winner.mention}?"
                )
                bat_bowl_view = BatBowlView(self.game)
                bat_bowl_view._channel = channel
                await channel.send(embed=bb_embed, view=bat_bowl_view)
            await _run_safely(_send_choice, allowed_ids=allowed, notify_channel=channel)
        return callback


# ─────────────────────────────────────────────────────────────────────────────
# Bat / Bowl choice
# ─────────────────────────────────────────────────────────────────────────────

class BatBowlView(_TimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game

    @ui.button(label="Bat First",  style=discord.ButtonStyle.success)
    async def bat(self, interaction: discord.Interaction, button: ui.Button):
        await self._choose(interaction, bat=True)

    @ui.button(label="Bowl First", style=discord.ButtonStyle.danger)
    async def bowl(self, interaction: discord.Interaction, button: ui.Button):
        await self._choose(interaction, bat=False)

    async def _choose(self, interaction: discord.Interaction, bat: bool):
        if interaction.user.id != self.game.toss_winner_id:
            await interaction.response.send_message("Only the toss winner decides!", ephemeral=True)
            return
        self.stop()
        winner = interaction.user
        other  = self.game.challenger if winner.id == self.game.opponent.id else self.game.opponent
        if bat:
            self.game.batting_user_id = winner.id
            self.game.bowling_user_id = other.id
            choice_text = "opted to **BAT FIRST**"
        else:
            self.game.bowling_user_id = winner.id
            self.game.batting_user_id = other.id
            choice_text = "opted to **BOWL FIRST**"

        # Initialise DRS reviews for both teams
        self.game.drs_reviews[self.game.batting_user_id] = 2
        self.game.drs_reviews[self.game.bowling_user_id] = 2

        bat_team_name = self.game.get_batting_team().get("name", "Batting side")
        self.game.toss_note = f"{bat_team_name} chose to bat first" if bat else f"{bat_team_name} will bat first (opponent chose to bowl)"

        sep = "──────────────────────"
        channel = interaction.channel
        allowed = _match_ids(self.game)
        choice_embed = discord.Embed(title="MATCH IS SET", color=discord.Color.from_rgb(30, 160, 80))
        choice_embed.description = (
            f"{sep}\n{winner.mention} {choice_text}\n\n"
            f"**Batting:**  {self.game.batting_user.mention}\n"
            f"**Bowling:**  {self.game.bowling_user.mention}\n{sep}"
        )
        try:
            await interaction.response.edit_message(embed=choice_embed, view=None)
        except Exception:
            pass  # cosmetic only

        await _run_safely(lambda: _send_opener_prompt(channel, self.game), allowed_ids=allowed, notify_channel=channel)


# ─────────────────────────────────────────────────────────────────────────────
# Opener selection
# ─────────────────────────────────────────────────────────────────────────────

class OpenerSelectView(_TimeoutMixin, ui.View):
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
            await interaction.response.send_message("Only the batting team selects openers!", ephemeral=True)
            return
        self.stop()
        names = interaction.data["values"]
        team  = self.game.get_batting_team()

        players_by_name = {_pname(p): p for p in team.get("players", [])}
        p1 = players_by_name.get(names[0])
        p2 = players_by_name.get(names[1])
        if not p1 or not p2:
            await interaction.response.send_message("Player not found!", ephemeral=True)
            return

        n1, n2 = _pname(p1), _pname(p2)
        channel = interaction.channel
        striker_view = StrikerDesignateView(self.game, p1, p2)
        striker_view._channel = channel
        await _advance(
            interaction, channel,
            content=f"**{n1}** & **{n2}** selected — who takes strike?",
            view=striker_view,
            allowed_ids=_match_ids(self.game),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Strike designation
# ─────────────────────────────────────────────────────────────────────────────

class StrikerDesignateView(_TimeoutMixin, ui.View):
    def __init__(self, game: GameState, p1: dict, p2: dict):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self.p1   = p1
        self.p2   = p2
        self._add_buttons()

    def _add_buttons(self):
        b1 = ui.Button(label=_pname(self.p1), style=discord.ButtonStyle.primary)
        b2 = ui.Button(label=_pname(self.p2), style=discord.ButtonStyle.secondary)
        b1.callback = self._make_cb(self.p1, self.p2)
        b2.callback = self._make_cb(self.p2, self.p1)
        self.add_item(b1)
        self.add_item(b2)

    def _make_cb(self, striker: dict, non_striker: dict):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                await interaction.response.send_message("Only the batting team selects!", ephemeral=True)
                return
            self.stop()
            self.game.striker     = striker
            self.game.non_striker = non_striker
            sn = _pname(striker)
            nn = _pname(non_striker)
            # BUG FIX #8: initialise batsman_stats with fours/sixes so all
            # code that does .get("fours", 0) never hits a missing-key situation.
            self.game.batsman_stats[sn] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
            self.game.batsman_stats[nn] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
            self.game.phase = "select_bowler"

            channel = interaction.channel
            allowed = _match_ids(self.game)
            try:
                await interaction.response.edit_message(
                    content=f"**{sn}** on strike · **{nn}** at non-striker end",
                    view=None,
                )
            except Exception:
                pass  # cosmetic only

            async def _send_openers_info():
                f1, f2 = await asyncio.gather(
                    _bat_stats_file(self.game.batting_user_id, striker),
                    _bat_stats_file(self.game.batting_user_id, non_striker),
                )
                files = [f for f in (f1, f2) if f]
                await channel.send(
                    content=f"**{sn}** ({striker.get('ovr','?')}) and **{nn}** ({non_striker.get('ovr','?')}) are opening the batting",
                    files=files,
                )
            await _run_safely(_send_openers_info, allowed_ids=allowed, notify_channel=channel)

            async def _send_bowler_select():
                xi_embed    = build_playing_xi_embed(self.game.get_bowling_team())
                bowler_view = BowlerSelectView(self.game)
                bowler_view._channel = channel
                await channel.send(
                    content=f"Select your bowler {self.game.bowling_user.mention}",
                    embed=xi_embed,
                    view=bowler_view,
                )
            await _run_safely(_send_bowler_select, allowed_ids=allowed, notify_channel=channel)
        return callback


# ─────────────────────────────────────────────────────────────────────────────
# Bowler selection (start of innings)
# ─────────────────────────────────────────────────────────────────────────────

class BowlerSelectView(_TimeoutMixin, ui.View):
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
            await interaction.response.send_message("Only the bowling team selects the bowler!", ephemeral=True)
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
        self.game.current_bowler = bowler
        bname = _pname(bowler)
        if bname not in self.game.bowler_stats:
            self.game.bowler_stats[bname] = {"balls": 0, "runs": 0, "wickets": 0}

        channel = interaction.channel
        allowed = _match_ids(self.game)
        try:
            await interaction.response.edit_message(content=f"**{bname}** will bowl.", view=None, embed=None)
        except Exception:
            pass  # cosmetic only

        async def _send_prompt():
            bowl_file = await _bowl_stats_file(self.game.bowling_user_id, bowler)
            files = [bowl_file] if bowl_file else []
            await _send_bowling_prompt(
                channel, self.game,
                intro=f"{bname} ({bowler.get('ovr','?')}) comes into the attack",
                files=files,
            )
        await _run_safely(_send_prompt, allowed_ids=allowed, notify_channel=channel)


# ─────────────────────────────────────────────────────────────────────────────
# Fast bowling — two-stage
# ─────────────────────────────────────────────────────────────────────────────

_FAST_STAGE1_STYLES = {
    "Outswing": discord.ButtonStyle.primary,
    "Inswing":  discord.ButtonStyle.secondary,
    "Fast":     discord.ButtonStyle.danger,
    "Slow":     discord.ButtonStyle.success,
}

_FAST_STAGE2_STYLES = {
    "Bouncer": discord.ButtonStyle.danger,
    "Full":    discord.ButtonStyle.primary,
    "Good":    discord.ButtonStyle.success,
    "Yorker":  discord.ButtonStyle.secondary,
}


class FastBowlStage1View(_TimeoutMixin, ui.View):
    """Step 1 of fast bowling: choose swing / speed type."""

    def __init__(self, game: GameState, is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game        = game
        self.is_free_hit = is_free_hit
        for label in FAST_STAGE1:
            style = _FAST_STAGE1_STYLES.get(label, discord.ButtonStyle.secondary)
            btn   = ui.Button(label=label, style=style, row=0)
            btn.callback = self._make_callback(label)
            self.add_item(btn)

    def _make_callback(self, stage1_label: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only the bowling team bowls!", ephemeral=True)
                return
            self.stop()
            bname = _pname(self.game.current_bowler)
            channel = interaction.channel
            stage2_view = FastBowlStage2View(self.game, stage1_choice=stage1_label, is_free_hit=self.is_free_hit)
            stage2_view._channel = channel
            await _advance(
                interaction, channel,
                content=f"**{bname}** — **{stage1_label}**. Now pick line/length:",
                view=stage2_view,
                allowed_ids=_match_ids(self.game),
            )
        return callback


class FastBowlStage2View(_TimeoutMixin, ui.View):
    """Step 2 of fast bowling: choose line/length. Triggers the actual delivery."""

    def __init__(self, game: GameState, stage1_choice: str, is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game          = game
        self.stage1_choice = stage1_choice
        self.is_free_hit   = is_free_hit
        for label in FAST_STAGE2:
            style = _FAST_STAGE2_STYLES.get(label, discord.ButtonStyle.secondary)
            btn   = ui.Button(label=label, style=style, row=0)
            btn.callback = self._make_callback(label)
            self.add_item(btn)

    def _make_callback(self, delivery_button: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only the bowling team bowls!", ephemeral=True)
                return
            self.stop()
            delivery_internal = DELIVERY_BUTTON_MAP.get(delivery_button, delivery_button)
            self.game.pending_delivery          = delivery_button
            self.game.pending_delivery_internal = delivery_internal
            self.game.phase = "bat_select"

            speed    = random.uniform(*get_delivery_speed(delivery_button))
            combined = f"{self.stage1_choice} {delivery_button}"
            # Cached so a failed send can be retried (or _resume_prompt used)
            # with the exact same speed/label instead of losing it.
            self.game.pending_delivery_speed  = speed
            self.game.pending_stage1_choice   = self.stage1_choice

            channel = interaction.channel
            batting_view = BattingView(self.game, stage1_choice=self.stage1_choice, is_free_hit=self.is_free_hit)
            batting_view._channel = channel
            await _post_delivery_prompt(
                interaction, channel, self.game,
                content=_delivery_announcement(combined, speed, self.game.batting_user, free_hit=self.is_free_hit),
                view=batting_view,
                allowed_ids=_match_ids(self.game),
            )
        return callback


# ─────────────────────────────────────────────────────────────────────────────
# Spin bowling — single step
# ─────────────────────────────────────────────────────────────────────────────

_SPIN_STYLES = {
    "Offspin":  discord.ButtonStyle.primary,
    "Carrom":   discord.ButtonStyle.danger,
    "Arm Ball": discord.ButtonStyle.success,
    "Doosra":   discord.ButtonStyle.danger,
    "Topspin":  discord.ButtonStyle.success,
    "Legspin":  discord.ButtonStyle.primary,
    "Googly":   discord.ButtonStyle.danger,
    "Flipper":  discord.ButtonStyle.success,
    "Drifter":  discord.ButtonStyle.danger,
    "Slider":   discord.ButtonStyle.success,
}


class BowlingView(_TimeoutMixin, ui.View):
    """Single-step bowling view for Off Spin and Leg Spin."""

    def __init__(self, game: GameState, is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game        = game
        self.is_free_hit = is_free_hit
        self._add_buttons()

    def _add_buttons(self):
        btype   = self.game.current_bowler.get("bowling_type", "Off Spin") if self.game.current_bowler else "Off Spin"
        buttons = OFF_SPIN_BUTTONS if btype == "Off Spin" else LEG_SPIN_BUTTONS
        for i, label in enumerate(buttons):
            style = _SPIN_STYLES.get(label, discord.ButtonStyle.secondary)
            btn   = ui.Button(label=label, style=style, row=i // 3)
            btn.callback = self._make_callback(label)
            self.add_item(btn)

    def _make_callback(self, delivery_button: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only the bowling team bowls!", ephemeral=True)
                return
            self.stop()
            btype             = self.game.current_bowler.get("bowling_type", "Off Spin")
            delivery_internal = resolve_delivery(delivery_button, btype)
            self.game.pending_delivery          = delivery_button
            self.game.pending_delivery_internal = delivery_internal
            self.game.phase = "bat_select"

            speed = random.uniform(*get_delivery_speed(delivery_button))
            self.game.pending_delivery_speed = speed
            self.game.pending_stage1_choice  = ""

            channel = interaction.channel
            batting_view = BattingView(self.game, is_free_hit=self.is_free_hit)
            batting_view._channel = channel
            await _post_delivery_prompt(
                interaction, channel, self.game,
                content=_delivery_announcement(delivery_button, speed, self.game.batting_user, free_hit=self.is_free_hit),
                view=batting_view,
                allowed_ids=_match_ids(self.game),
            )
        return callback


# ─────────────────────────────────────────────────────────────────────────────
# Batting view
# ─────────────────────────────────────────────────────────────────────────────

class BattingView(_TimeoutMixin, ui.View):
    def __init__(self, game: GameState, *, stage1_choice: str = "", is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game          = game
        self.stage1_choice = stage1_choice
        self.is_free_hit   = is_free_hit

        delivery_btn = game.pending_delivery or ""
        if stage1_choice:
            guide_list = FAST_SHOT_GUIDE.get((stage1_choice, delivery_btn), [])
        else:
            guide_list = SPIN_SHOT_GUIDE.get(delivery_btn, [])

        self.recommended        = guide_list
        self.guide_entry_exists = bool(guide_list)  # False when delivery not in guide

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
            style   = STYLES.get(label, discord.ButtonStyle.secondary)
            row     = ROWS.get(label, 0)
            is_good = label in self.recommended
            btn     = ui.Button(label=label, style=style, row=row)
            btn.callback = self._make_callback(label, is_good, self.guide_entry_exists)
            self.add_item(btn)

    def _make_callback(self, shot: str, is_recommended: bool, guide_entry_exists: bool):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                await interaction.response.send_message("Only the batting team plays shots!", ephemeral=True)
                return
            self.stop()
            await _process_delivery(
                interaction, self.game, shot,
                stage1_choice=self.stage1_choice,
                is_recommended_shot=is_recommended,
                guide_entry_exists=guide_entry_exists,
                is_free_hit=self.is_free_hit,
            )
        return callback


# ─────────────────────────────────────────────────────────────────────────────
# DRS
# ─────────────────────────────────────────────────────────────────────────────

class DRSView(ui.View):
    """
    DRS prompt.  Commentary has already been sent before this view appears.
    Handles challenge / accept / timeout.
    """

    def __init__(
        self, game: GameState, channel,
        delivery_internal: str = "", shot_internal: str = "",
        commentary: str = "",
        pre_wicket_pship_runs: int = 0,
        pre_wicket_pship_balls: int = 0,
        pre_wicket_balls_since: int = 999,
        pre_wicket_striker=None,
    ):
        super().__init__(timeout=10)
        self._striker              = pre_wicket_striker
        self.game                  = game
        self.channel               = channel
        self.delivery_internal     = delivery_internal
        self.shot_internal         = shot_internal
        self._decided              = False
        self.message               = None
        text_upper                 = commentary.upper()
        self.is_lbw                = "LBW" in text_upper or "PLUMB" in text_upper or "IN FRONT" in text_upper
        self._out_emoji            = LBW_EMOJI if self.is_lbw else TIMELINE_EMOJIS.get("W", "")
        self._pship_runs           = pre_wicket_pship_runs
        self._pship_balls          = pre_wicket_pship_balls
        self._balls_since          = pre_wicket_balls_since

    @ui.button(label="Challenge", style=discord.ButtonStyle.danger)
    async def challenge(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.game.batting_user_id:
            await interaction.response.send_message("Only the batting team can challenge!", ephemeral=True)
            return
        if self._decided:
            await interaction.response.send_message("Decision has already been made.", ephemeral=True)
            return
        self._decided = True
        self.stop()

        uid = self.game.batting_user_id
        self.game.drs_reviews[uid] = max(0, self.game.drs_reviews.get(uid, 0) - 1)
        remaining = self.game.drs_reviews[uid]

        # Cosmetic status message only — the actual decision below does not
        # depend on this succeeding.
        try:
            await interaction.response.edit_message(
                content=(
                    f"**Review Referred to Third Umpire…**\n"
                    f"Reviews remaining after this: **{remaining}**"
                ),
                view=None,
            )
        except Exception:
            pass

        try:
            await asyncio.sleep(1.5)
            try:
                await interaction.edit_original_response(
                    content="**Checking…** Ball Tracking | Hot Spot | Ultra Edge"
                )
            except Exception:
                pass
            await asyncio.sleep(1.5)

            overturn_chance = _drs_overturn_chance(self.delivery_internal, self.shot_internal)
            overturned      = random.random() < overturn_chance

            if overturned:
                # Undo the wicket
                inn = self.game.innings - 1
                self.game.wickets[inn] = max(0, self.game.wickets[inn] - 1)
                if self.game.dismissed:
                    self.game.dismissed.pop()
                bowler_key = _pname(self.game.current_bowler) if self.game.current_bowler else None
                if bowler_key and bowler_key in self.game.bowler_stats:
                    _bs = self.game.bowler_stats[bowler_key]
                    for _k in ("wickets", "w"):
                        if _k in _bs:
                            _bs[_k] = max(0, _bs[_k] - 1)

                # add_wicket() cleared the striker — put the batter back at the crease
                if self._striker is not None:
                    self.game.striker = self._striker
                self.game.last_ball_was_wicket = False

                # Restore partnership state wiped by add_wicket()
                self.game.partnership_runs   = self._pship_runs
                self.game.partnership_balls  = self._pship_balls
                self.game.balls_since_wicket = self._balls_since

                # BUG FIX #9: also restore game.striker — add_wicket() appended the
                # name to dismissed[] but the caller (_process_delivery) didn't wipe
                # game.striker yet.  _handle_wicket_fall() does it, but we never
                # called that on NOT-OUT.  game.striker is still set at this point
                # so we just leave it (the batsman is back at the crease).
                # What we MUST do is remove the name from dismissed[] — already done above.

                self.game.timeline.append(NOT_OUT_EMOJI)

                try:
                    await interaction.edit_original_response(
                        content=(
                            f"**THIRD UMPIRE DECISION:**\n"
                            f"{NOT_OUT_EMOJI} **NOT OUT — Decision Overturned!**\n"
                            f"The batsman stays at the crease!"
                        )
                    )
                except Exception:
                    await self.channel.send(
                        content=f"{NOT_OUT_EMOJI} **NOT OUT — Decision Overturned!** The batsman stays at the crease!"
                    )

                _notout_gif = get_umpire_gif("notout")
                if _notout_gif:
                    await self.channel.send(_notout_gif)

                # BUG FIX #10: after NOT OUT, check if the over ended on that ball.
                # If current_over_balls >= 6 the over has finished — rotate & pick
                # new bowler.  If not, just send the bowling prompt.
                if self.game.current_over_balls >= 6:
                    self.game.rotate_strike()
                    self.game.timeline.append("|")
                    self.game.end_over()
                    await _send_next_bowler_prompt(self.channel, self.game)
                else:
                    await _send_bowling_prompt(self.channel, self.game)

            else:
                # OUT upheld
                self.game.timeline.append(_timeline_emoji("W"))
                try:
                    await interaction.edit_original_response(
                        content=(
                            f"**THIRD UMPIRE DECISION:**\n"
                            f"{self._out_emoji} **OUT — Decision Upheld!**\n"
                            f"The original decision stands."
                        )
                    )
                except Exception:
                    await self.channel.send(
                        content=f"{self._out_emoji} **OUT — Decision Upheld!** The batsman must walk."
                    )
                await _handle_wicket_fall(self.channel, self.game)
        except Exception as e:
            print(f"[match recovery] DRS challenge failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            try:
                await self.channel.send(
                    content="A small hiccup with that review — your match is safe. Tap to continue.",
                    view=_ContinueView(lambda: _resume_prompt(self.channel, self.game), _match_ids(self.game)),
                )
            except discord.HTTPException:
                pass

    @ui.button(label="Accept Decision", style=discord.ButtonStyle.secondary)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.game.batting_user_id:
            await interaction.response.send_message("Only the batting team can respond!", ephemeral=True)
            return
        if self._decided:
            await interaction.response.send_message("Decision has already been made.", ephemeral=True)
            return
        self._decided = True
        self.stop()

        self.game.timeline.append(_timeline_emoji("W"))
        try:
            await interaction.response.edit_message(
                content="Decision accepted. The batsman walks back to the pavilion.",
                view=None,
            )
        except Exception:
            pass  # cosmetic only
        await _safe_next(self.channel, self.game, lambda: _handle_wicket_fall(self.channel, self.game))

    async def on_timeout(self):
        if self._decided:
            return
        self._decided = True
        self.game.timeline.append(_timeline_emoji("W"))
        if self.message:
            try:
                await self.message.edit(
                    content=(
                        "**Review time expired — OUT decision stands.**\n"
                        "The batsman must walk."
                    ),
                    view=None,
                )
            except Exception:
                pass
        await _safe_next(self.channel, self.game, lambda: _handle_wicket_fall(self.channel, self.game))


# ─────────────────────────────────────────────────────────────────────────────
# Next batsman
# ─────────────────────────────────────────────────────────────────────────────

class NextBatsmanView(_TimeoutMixin, ui.View):
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
            await interaction.response.send_message("Only the batting team selects!", ephemeral=True)
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
        channel = interaction.channel

        try:
            await interaction.response.edit_message(
                content=f"**{name}** selected.", view=None, embed=None
            )
        except Exception:
            pass  # cosmetic only

        async def _work():
            bat_file = await _bat_stats_file(self.game.batting_user_id, player)
            files = [bat_file] if bat_file else []
            await channel.send(
                content=f"**{name}** ({player.get('ovr','?')}) comes to the crease",
                files=files,
            )

            # BUG FIX #11: if the wicket fell on ball 6 of the over, the over had
            # already ended when this view was shown.  We must rotate strike (new
            # batsman goes to non-striker end; existing non-striker faces) and then
            # prompt for a new bowler — NOT send a bowling prompt for the same over.
            if self.game.current_over_balls >= 6:
                self.game.rotate_strike()
                self.game.timeline.append("|")
                self.game.end_over()
                await _send_next_bowler_prompt(channel, self.game)
            else:
                await _send_bowling_prompt(channel, self.game)

        await _safe_next(channel, self.game, _work)


# ─────────────────────────────────────────────────────────────────────────────
# Next bowler
# ─────────────────────────────────────────────────────────────────────────────

class NextBowlerView(_TimeoutMixin, ui.View):
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

        options = [
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
            await interaction.response.send_message("Only the bowling team selects!", ephemeral=True)
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

        # BUG FIX #12: validate the bowler hasn't exceeded their over quota.
        # get_available_bowlers() already filters this but an admin / edge-case
        # could still produce a stale dropdown.  Re-check here and reject.
        bname     = _pname(bowler)
        max_balls = self.game.max_bowler_balls()
        balls_so_far = self.game.bowler_ball_count.get(bname, 0)
        if balls_so_far >= max_balls:
            await interaction.response.send_message(
                f"**{bname}** has already bowled their maximum of "
                f"{max_balls // 6} overs. Choose someone else.",
                ephemeral=True,
            )
            return

        self.stop()
        self.game.current_bowler = bowler
        if bname not in self.game.bowler_stats:
            self.game.bowler_stats[bname] = {"balls": 0, "runs": 0, "wickets": 0}
        channel = interaction.channel

        try:
            await interaction.response.edit_message(
                content=f"**{bname}** starts a new over.", view=None, embed=None
            )
        except Exception:
            pass  # cosmetic only

        async def _work():
            bowl_file = await _bowl_stats_file(self.game.bowling_user_id, bowler)
            files = [bowl_file] if bowl_file else []
            await _send_bowling_prompt(
                channel, self.game,
                intro=f"{bname} ({bowler.get('ovr','?')}) starts a new over",
                files=files,
            )

        await _safe_next(channel, self.game, _work)
