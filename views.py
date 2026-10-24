import asyncio
import io
import os
import random
import discord
import emojis
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
from commentary import build_ball_commentary, SIX_DISTANCES
from wagon_wheel import wagon_wheel_png, field_png, catch_wheel_png
import pitch_map
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

    async def _send():
        send_kwargs = dict(kwargs)
        if view is None:
            send_kwargs.pop("view")  # channel.send(view=None) is fine, but keep kwargs minimal
        if file is not None:
            send_kwargs["file"] = file
        await channel.send(**send_kwargs)

    # Ranked (mirrored) match: the old prompt's buttons are stripped from BOTH channels by
    # ranked_mirror._after_view as soon as that prompt's view stops. If we put the NEW buttons
    # onto the old message with an in-place edit, that cleanup lands afterwards and wipes them
    # ("striker buttons disappear"). So post the next prompt as a fresh message instead
    # (the mirror also shows it to both players) and just acknowledge the click.
    if view is not None and type(channel).__name__ == "MirrorChannel":
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass
        await _run_safely(_send, allowed_ids=allowed_ids, notify_channel=channel)
        return

    try:
        await interaction.response.edit_message(**kwargs)
        return
    except Exception as e:
        print(f"[match recovery] in-place edit failed ({type(e).__name__}: {e}) — sending a fresh message instead")

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
            for _u in (getattr(game, "batting_user_id", None), getattr(game, "bowling_user_id", None)):
                if _u is not None:
                    print(f"[quest] uid={_u} match finished through the 'Tap to continue' recovery path - "
                          f"quest tracking only counted if the lines for this match appeared earlier")
            result_embed = build_result_embed(game)
            await channel.send(embed=result_embed)
            await _send_match_summary(channel, game)
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
    is_two_stage, get_two_stage_buttons, get_spin_buttons, button_label,
    BATTING_BUTTONS, SHOT_BUTTON_MAP,
    get_delivery_speed, resolve_delivery, BOWLING_TYPE_ICON,
    DELIVERY_BUTTON_MAP,
    FAST_SHOT_GUIDE, SPIN_SHOT_GUIDE,
    NOT_OUT_EMOJI, LBW_EMOJI,
    DIRECTIONS, DIRECTIONS_BY_NAME, translate_shot, SHOT_BUTTON_MAP,
    LOFT_DIRECTION_RISK, USE_LOFT_DIRECTION_RISK,
)
from career_stats import save_innings as _save_innings
import field_logic


def save_game_innings_stats(game, batting_user_id=None, bowling_user_id=None, is_final=False):
    """Persist per-card stats for the innings that just ended.

    batting_user_id/bowling_user_id identify who was batting/bowling this
    innings, so the personal (per-user) card stats used by `csview` get
    credited to the right person, not just the shared all-owners totals.
    """
    try:
        _save_innings(
            game,
            is_final=is_final,
            batting_user_id=batting_user_id,
            bowling_user_id=bowling_user_id,
        )
    except Exception as e:
        print(f"[career_stats] save failed: {type(e).__name__}: {e}")


def get_career_stats(user_id, player_name):
    """ALL-TIME career totals of this user's card (zeros if never played)."""
    try:
        from career_stats import _key_for, get_career_personal
        key = _key_for(player_name)
        return get_career_personal(user_id, key) if key else {}
    except Exception as e:
        print(f"[stats] career lookup failed: {type(e).__name__}: {e}")
        return {}
from match_records import record_match_result as _record_match_result


async def _flush_quest_notes(channel, game):
    """Tell the players which daily quests they just finished (never raises)."""
    try:
        import quest
        await quest.flush_notes(channel, [game.batting_user_id, game.bowling_user_id])
    except Exception as e:
        print(f"[quest] announce failed: {type(e).__name__}: {e}")


def _ranked_finish(game, winner_id, loser_id):
    """Ranked match decided: +1 star winner / -1 star loser (once per game, never raises)."""
    if not getattr(game, "ranked", False) or game.__dict__.get("_ranked_done"):
        return
    game._ranked_done = True
    try:
        import ranked
        game.ranked_result = ranked.apply_result(winner_id, loser_id)
    except Exception as e:
        game.ranked_result = {}
        print(f"[ranked] result failed: {type(e).__name__}: {e}")


async def _ranked_announce(channel, game):
    """Post the star changes (and any tier reward) after a ranked match. Never raises."""
    try:
        if not getattr(game, "ranked", False) or game.__dict__.get("_ranked_announced"):
            return
        res = game.__dict__.get("ranked_result")
        if res is None:
            return
        game._ranked_announced = True
        import ranked
        if not res:
            await channel.send("🤝 Tied match — no stars changed.")
            return
        lines, pend = [], []
        for uid, r in res.items():
            who = game.challenger if game.challenger.id == uid else game.opponent
            if r["delta"] > 0:
                change = f"**+1** ⭐  ({r['old']} → **{r['new']}**)"
            elif r["delta"] < 0:
                change = f"**-1** ⭐  ({r['old']} → **{r['new']}**)"
            else:
                change = f"stays at **{r['new']}** ⭐"
            tier = ranked.tier_of(r["new"])
            line = f"{who.mention}: {change}  {ranked.tier_emoji(tier)} {tier}"
            if r["tier_new"] != r["tier_old"] and not r["won"]:
                line += f"\n⬇️ Dropped to {ranked.tier_emoji(tier)} **{tier}**"
            for text in r["rewards"]:
                line += f"\n🎉 {text}"
            if r.get("blacklist_until"):
                line += (f"\n🚫 Too many losses on Bronze. Blacklisted from ranked "
                         f"until <t:{int(r['blacklist_until'])}:D>.")
            lines.append(line)
            if r["pending"]:
                pend.append(uid)
        embed = discord.Embed(title="⭐ Ranked Result", description="\n\n".join(lines),
                              color=discord.Color.from_rgb(212, 175, 55))
        await channel.send(embed=embed)
        if pend:
            from economy_cog import send_next_pending
            for uid in pend:
                await send_next_pending(channel, uid)
    except Exception as e:
        print(f"[ranked] announce failed: {type(e).__name__}: {e}")


async def _ranked_credits_announce(channel, game):
    """Tell the players how many Star Pass credits the ranked match gave. Never raises."""
    try:
        res = game.__dict__.get("ranked_credits")
        if not res or game.__dict__.get("_ranked_credits_announced"):
            return
        game._ranked_credits_announced = True
        import starpass
        lines = []
        for who in (game.challenger, game.opponent):
            r = res.get(who.id)
            if r:
                lines.append(
                    f"{who.mention}: **+{r['credits']}** Star Pass credits "
                    f"({r['n']}/{starpass.STARPASS_RANKED_MATCHES_PER_DAY} ranked matches today)"
                )
        if lines:
            await channel.send(embed=discord.Embed(
                title="Star Pass", description="\n".join(lines), color=discord.Color.from_rgb(212, 175, 55)))
    except Exception as e:
        print(f"[starpass] ranked credits announce failed: {type(e).__name__}: {e}")


def _save_match_record(game):
    """Derive winner/loser and persist to match_records. Ignores ties."""
    t1 = game.runs[0]
    t2 = game.runs[1]
    # After start_second_innings():
    #   bowling_user_id  = innings-1 batter (defending team)
    #   batting_user_id  = innings-2 batter (chasing team)
    inn1_uid = game.bowling_user_id
    inn2_uid = game.batting_user_id
    # Daily quests (real matches only: practice never calls this). Ties count as played.
    try:
        import quest
        quest.on_match_end(game, inn1_uid, inn2_uid, t1, t2)
    except Exception as e:
        print(f"[quest] match tracking failed: {type(e).__name__}: {e}")
    # Star Pass credits for finished RANKED matches (once per game; ties count as played).
    if getattr(game, "ranked", False) and not game.__dict__.get("_ranked_credits_done"):
        game._ranked_credits_done = True
        try:
            import starpass
            if starpass.STARPASS_RANKED_VS_BOT or not getattr(game, "is_bot_match", False):
                game.ranked_credits = starpass.award_ranked_match_credits([inn1_uid, inn2_uid])
        except Exception as e:
            print(f"[starpass] ranked credits failed: {type(e).__name__}: {e}")
    if t1 == t2:
        if getattr(game, "ranked", False):   # ranked tie: no star change
            game.ranked_result = {}
            game._ranked_done = True
        return  # tie — don't record
    if t2 > t1:
        winner_id, loser_id = inn2_uid, inn1_uid
        winner_score, loser_score = t2, t1
    else:
        winner_id, loser_id = inn1_uid, inn2_uid
        winner_score, loser_score = t1, t2
    _ranked_finish(game, winner_id, loser_id)
    try:
        game.match_record_id = _record_match_result(winner_id, loser_id, winner_score, loser_score, game.overs)
    except Exception as e:
        print(f"[match_records] Failed to save result: {e}")
    # Economy: reward winner and loser with coins
    try:
        from economy import reward_match_winner
        reward_match_winner(str(winner_id), str(loser_id), game.overs)
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

from ranked_mirror import GameRegistry

# channel id -> game. A cross-server ranked match is stored under BOTH channel ids (see ranked_mirror.py).
active_games: dict[int, GameState] = GameRegistry()


def _ch(interaction, game):
    """The channel to post the next prompt in: the mirror (both players' channels) for a
    cross-server ranked match, otherwise the channel the button was pressed in (as before)."""
    return game.__dict__.get("mirror") or interaction.channel

VIEW_TIMEOUT = 300  # 5 minutes per action


# ─────────────────────────────────────────────────────────────────────────────
# Autoplay (premium) — `csautoplay`
# ─────────────────────────────────────────────────────────────────────────────
# A premium player can type `csautoplay` during a match and the bot then makes
# THEIR choices (bowler, batter, delivery, shot, toss, openers ...) on their
# behalf. If only one side has it on, only that side is automatic; if both
# sides have it on, the whole match runs by itself.

AUTOPLAY_DELAY = 3.0   # seconds before the bot "clicks" for the player


def autoplay_ids(game) -> set:
    """Ids of the players in this match who have autoplay switched on."""
    ids = getattr(game, "autoplay_ids", None)
    if ids is None:
        ids = set()
        game.autoplay_ids = ids
    return ids


class _AutoResponse:
    """Stands in for `interaction.response` when the bot clicks for a player."""

    def __init__(self, owner):
        self._owner = owner
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, *args, **kwargs):
        # Only ever warnings meant for a human ("not your turn" ...). Skip.
        self._done = True

    async def defer(self, *args, **kwargs):
        self._done = True

    async def edit_message(self, **kwargs):
        msg = self._owner.message
        if msg is None or self._done:
            # Callers already fall back to posting a fresh message.
            raise RuntimeError("autoplay: nothing to edit")
        self._done = True
        await msg.edit(**kwargs)


class _AutoInteraction:
    """Just enough of discord.Interaction for the match views to run."""

    def __init__(self, user, channel, message, values=None):
        self.user = user
        self.channel = channel
        self.channel_id = channel.id
        self.message = message
        self.data = {"values": list(values or [])}
        self.response = _AutoResponse(self)

    async def edit_original_response(self, **kwargs):
        if self.message is None:
            raise RuntimeError("autoplay: nothing to edit")
        await self.message.edit(**kwargs)


def _first_select(view):
    return next((c for c in view.children if isinstance(c, ui.Select)), None)


def _acting_skill(view):
    """0..1 skill if the player being auto-played is a ranked BOT, else None
    (None = normal csautoplay behaviour for real premium players)."""
    uid = view.__dict__.get("_autoplay_uid")
    game = getattr(view, "game", None)
    if uid is None or game is None or uid not in getattr(game, "bot_ids", ()):
        return None
    return float(getattr(game, "bot_skill", 0.5))


async def _auto_pick_from_select(view, make, n: int = 1) -> None:
    select = _first_select(view)
    if select is None:
        return
    all_values = [o.value for o in select.options]
    if not all_values or all_values[0] == "none":
        return
    values = all_values[:n]   # the dropdown is already sorted best-first
    skill = _acting_skill(view)
    if skill is not None and len(all_values) > n and random.random() > skill:
        values = random.sample(all_values, n)   # a weaker bot sometimes picks a worse player
    await select.callback(make(values))


async def _auto_toss(view, make):
    btns = [c for c in view.children if isinstance(c, ui.Button)]
    if btns:
        await random.choice(btns).callback(make())


async def _auto_bat_bowl(view, make):
    await view._choose(make(), bat=True)


async def _auto_openers(view, make):
    await _auto_pick_from_select(view, make, n=2)


async def _auto_striker(view, make):
    btns = [c for c in view.children if isinstance(c, ui.Button)]
    if btns:
        await btns[0].callback(make())


async def _auto_select_one(view, make):
    await _auto_pick_from_select(view, make, n=1)


async def _auto_field(view, make):
    """Autoplay / ranked bot: lock in the default symmetric field."""
    await view._finalize(view._channel, view._default_positions(), auto=True)


async def _auto_fast_bowl(view, make):
    await random.choice(view._stage1_btns).callback(make())
    await random.choice(view._stage2_btns).callback(make())


async def _auto_spin_bowl(view, make):
    btns = [c for c in view.children if isinstance(c, ui.Button)]
    if btns:
        await random.choice(btns).callback(make())


async def _auto_bat(view, make):
    """Two-step BattingView: press a stage-1 button (Ground most of the
    time, Loft sometimes, Leave only rarely), then — once that callback has
    revealed the direction grid — press a direction. If view.recommended is
    non-empty, prefer a direction whose translate_shot() result lands in it,
    with the same skill-based chance ranked bots already use elsewhere."""
    stage1_btns = getattr(view, "_stage1_btns", None)
    if not stage1_btns:
        return
    btn_by_label = {b.label: b for b in stage1_btns}

    r = random.random()
    if r < 0.03:
        height_choice = "Leave"
    elif r < 0.03 + 0.25:
        height_choice = "Loft"
    else:
        height_choice = "Ground"

    stage1_btn = btn_by_label.get(height_choice) or random.choice(stage1_btns)
    await stage1_btn.callback(make())

    if height_choice == "Leave" or view.is_finished():
        return   # Leave ends the ball immediately — no direction step

    direction_btns = getattr(view, "_direction_btns", [])
    direction_keys = getattr(view, "_direction_keys", [])
    if not direction_btns:
        return

    delivery_internal = getattr(view.game, "pending_delivery_internal", "") or ""
    good = [
        b for b, key in zip(direction_btns, direction_keys)
        if translate_shot(view.height, key, delivery_internal) in (view.recommended or [])
    ]
    skill = _acting_skill(view)
    if skill is not None and random.random() > skill:
        good = []   # a weaker bot often ignores the recommended shot
    pool = good or direction_btns
    await random.choice(pool).callback(make())


_AUTOPLAY_HANDLERS = {
    "TossView": _auto_toss,
    "BatBowlView": _auto_bat_bowl,
    "OpenerSelectView": _auto_openers,
    "StrikerDesignateView": _auto_striker,
    "BowlerSelectView": _auto_select_one,
    "NextBowlerView": _auto_select_one,
    "FieldSetupView": _auto_field,
    "NextBatsmanView": _auto_select_one,
    "FastBowlStage1View": _auto_fast_bowl,
    "BowlingView": _auto_spin_bowl,
    "BattingView": _auto_bat,
}


def _autoplay_member(game, uid: int):
    for who in (getattr(game, "challenger", None), getattr(game, "opponent", None)):
        if who is not None and getattr(who, "id", None) == uid:
            return who
    return None


def autoplay_schedule(view) -> None:
    """Called for every new match view: if the player who has to act has
    autoplay on, click for them after a short pause."""
    game = getattr(view, "game", None)
    if game is None:
        return
    game.__dict__["_live_view"] = view
    if type(view).__name__ not in _AUTOPLAY_HANDLERS:
        return
    try:
        uid = view._get_leaver_id(game)
    except Exception:
        return
    if uid is None or uid not in autoplay_ids(game):
        return
    if view.__dict__.get("_autoplay_pending"):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    view.__dict__["_autoplay_pending"] = True
    loop.create_task(_autoplay_act(view, uid))


async def _autoplay_boot(view) -> None:
    await asyncio.sleep(0)
    try:
        autoplay_schedule(view)
    except Exception as e:
        print(f"[autoplay] schedule failed ({type(e).__name__}: {e})")


def autoplay_kick(game) -> None:
    """Right after `csautoplay` is switched on: handle the prompt that is
    already on screen."""
    view = game.__dict__.get("_live_view")
    if view is not None and not view.is_finished():
        autoplay_schedule(view)


async def _autoplay_act(view, uid: int) -> None:
    try:
        # A ranked bot "thinks" for a random, human-like time; real players' autoplay uses the fixed delay.
        _g = getattr(view, "game", None)
        if uid in getattr(_g, "bot_ids", ()):
            await asyncio.sleep(random.uniform(3.0, 8.0))
        else:
            await asyncio.sleep(AUTOPLAY_DELAY)
        view.__dict__["_autoplay_uid"] = uid
        game = view.game
        message = getattr(view, "message", None)
        channel = getattr(view, "_channel", None) or getattr(message, "channel", None)
        if channel is None or view.is_finished():
            return
        if active_games.get(channel.id) is not game:
            return
        if uid not in autoplay_ids(game) or view._get_leaver_id(game) != uid:
            return
        member = _autoplay_member(game, uid)
        handler = _AUTOPLAY_HANDLERS.get(type(view).__name__)
        if member is None or handler is None:
            return

        def make(values=None):
            return _AutoInteraction(member, channel, message, values)

        await handler(view, make)
    except Exception as e:
        print(f"[autoplay] failed ({type(e).__name__}: {e})")
        import traceback
        traceback.print_exc()
    finally:
        view.__dict__["_autoplay_pending"] = False


# ─────────────────────────────────────────────────────────────────────────────
# Timeout mixin
# ─────────────────────────────────────────────────────────────────────────────

class _TimeoutMixin:
    """
    Attach to every in-match View.
    Set view._channel = channel after creating the view.
    Call self.stop() in every successful callback so the timer cancels.
    """
    _message: discord.Message | None = None

    # Every prompt sets `view._channel = channel` right after it is created. We
    # use that moment to mark this view as the game's CURRENT prompt, so an
    # older / leftover view that times out later can never abandon a match
    # that has already moved on.
    @property
    def _channel(self):
        return self.__dict__.get("_channel_val")

    @_channel.setter
    def _channel(self, ch):
        self.__dict__["_channel_val"] = ch
        g = getattr(self, "game", None)
        if g is not None:
            try:
                g._live_view = self
            except Exception:
                pass

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Premium `csautoplay`: if the player who must act has it on, the bot
        # makes the choice for them.
        # (self.game etc. are set by the subclass AFTER this runs, so look at
        # the view on the next event-loop tick, not right now.)
        try:
            asyncio.get_running_loop().create_task(_autoplay_boot(self))
        except RuntimeError:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Block every button/select on this view once the match is no
        longer registered in active_games. Without this, `cscancel` only
        removed the game from that dict — the views already on-screen kept
        calling straight into `self.game` and the match carried on as if
        nothing happened."""
        game = getattr(self, "game", None)
        if game is not None and active_games.get(interaction.channel_id) is not game:
            try:
                await interaction.response.send_message(
                    "This match has been cancelled.", ephemeral=True
                )
            except discord.InteractionResponded:
                pass
            return False
        try:
            import security as _sec
            if _sec.is_banned(interaction.user.id):
                try:
                    await interaction.response.send_message("🚫 You are blacklisted from using this bot.", ephemeral=True)
                except discord.InteractionResponded:
                    pass
                return False
        except ImportError:
            pass
        # Stale-prompt guard: after `csresume` a NEW prompt is sent and becomes
        # the game's live view. Buttons on any older message must do nothing.
        if game is not None:
            live = game.__dict__.get("_live_view")
            if live is not None and live is not self and not live.is_finished():
                try:
                    await interaction.response.send_message(
                        "⚠️ This prompt is outdated — please use the buttons on the newest message.",
                        ephemeral=True,
                    )
                except discord.InteractionResponded:
                    pass
                return False
        return True

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
            "FieldSetupView",
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
        # A view that is no longer the game's current prompt (already answered,
        # replaced, or left over) must NEVER abandon the match or fine anyone.
        live = game.__dict__.get("_live_view")
        if live is not None and live is not self:
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

        for _u in (getattr(game, "batting_user_id", None), getattr(game, "bowling_user_id", None)):
            if _u is not None:
                print(f"[quest] uid={_u} NOT COUNTED: match abandoned (a player went AFK), no quest tracking for abandoned matches")
        try:
            await ch.send(
                "**Match abandoned — a player went AFK.**\n"
                "No response was received in time. Use `csmp @user <overs>` to start a fresh match."
                + penalty_msg
            )
        except Exception:
            pass
        if getattr(game, "ranked", False) and leaver_id is not None:
            # Ranked: leaving counts as a loss for the leaver and a win for the other player.
            other = game.opponent if getattr(game.challenger, "id", None) == leaver_id else game.challenger
            _ranked_finish(game, other.id, leaver_id)
            await _ranked_announce(ch, game)


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


async def _send_match_summary(channel, game) -> None:
    """Last message of a finished match: the Cricket Star SUMMARY card (never breaks the match end)."""
    try:
        from match_summary import build_summary_png
        png = await asyncio.to_thread(build_summary_png, game)
        await channel.send(file=discord.File(io.BytesIO(png), filename="match_summary.png"))
    except Exception as e:
        print(f"[summary] card failed: {type(e).__name__}: {e}")


async def _get_card_image_bytes(player: dict) -> bytes | None:
    try:
        bg_pathname = player.get("background_pathname") or ""
        bg_rec      = get_background(bg_pathname) if bg_pathname else None
        bg_url      = bg_rec["url"] if bg_rec else None
        return await generate_card_image(player, bg_url=bg_url)
    except Exception:
        return None


async def _bat_stats_file(user_id: int, player: dict, status: str = "", filename: str = "bat_stats.png") -> discord.File | None:
    try:
        name  = _pname(player)
        stats = get_career_stats(str(user_id), name)
        img   = await asyncio.to_thread(generate_bat_card, name, stats, None, status)
        return discord.File(fp=io.BytesIO(img), filename=filename)
    except Exception as e:
        print(f"[stats] bat card error: {e}")
        return None


async def _bowl_stats_file(user_id: int, player: dict) -> discord.File | None:
    try:
        name  = _pname(player)
        stats = get_career_stats(str(user_id), name)
        img   = await asyncio.to_thread(generate_bowl_card, name, stats, None, "CURRENT BOWLER")
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
    wheel: bytes | None = None,
    pitch: bytes | None = None,
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

    if wheel:   # wagon wheel for a four / six
        try:
            await channel.send(file=discord.File(io.BytesIO(wheel), filename="wagon_wheel.png"))
        except Exception:
            pass

    if pitch:   # where the ball landed vs the shot
        try:
            await channel.send(file=discord.File(io.BytesIO(pitch), filename="pitch_map.png"))
        except Exception:
            pass

    for url in gif_urls:
        try:
            await _send_gif_item(channel, url)
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

    if is_two_stage(btype):   # Fast + Medium Pacer
        view = FastBowlStage1View(game, is_free_hit=is_free_hit)
    else:
        view = BowlingView(game, is_free_hit=is_free_hit)
    view._channel = channel
    prefix = "**FREE HIT!**  " if is_free_hit else ""
    content = f"{prefix}Choose delivery  {game.bowling_user.mention}"
    await channel.send(content=content, embed=embed, view=view, files=files or [])


async def _send_next_bowler_prompt(channel: discord.TextChannel, game: GameState):
    """End-of-over hook: first the field-placement prompt (Field Placement
    feature), which then hands over to the next-bowler prompt. Skipped for
    the first over of an innings, when the feature is off, or when a field is
    already locked in for the coming over (e.g. `csresume`)."""
    over_idx = game.current_legal_balls // 6
    if (
        field_logic.FIELD_FEATURE_ENABLED
        and over_idx > 0
        and getattr(game, "field_over_idx", None) != over_idx
    ):
        try:
            await _send_field_prompt(channel, game, over_idx)
            return
        except Exception as e:   # never let the new feature stall a match
            print(f"[field] prompt failed ({type(e).__name__}: {e}) — skipping field setup")
    await _send_bowler_prompt_only(channel, game)


async def _send_bowler_prompt_only(channel: discord.TextChannel, game: GameState):
    """Prompt the bowling captain to choose the next over's bowler."""
    xi_embed = build_playing_xi_embed(game.get_bowling_team())
    nb_view  = NextBowlerView(game)
    nb_view._channel = channel
    msg = await channel.send(
        content=f"Over is up — choose your next bowler {game.bowling_user.mention}",
        embed=xi_embed,
        view=nb_view,
    )
    nb_view._message = msg


async def _send_next_batsman_prompt(
    channel: discord.TextChannel, game: GameState, *, content: str | None = None
):
    """Prompt the batting captain to choose the next batter."""
    try:
        xi_embed = build_playing_xi_embed(game.get_batting_team())
    except Exception as e:
        print(f"[wicket] XI embed error: {e}")
        xi_embed = None
    nb_view = NextBatsmanView(game)
    nb_view._channel = channel
    msg = await channel.send(
        content=content or f"Choose your next batter {game.batting_user.mention}",
        embed=xi_embed,
        view=nb_view,
    )
    nb_view._message = msg


# ─────────────────────────────────────────────────────────────────────────────
# Core delivery processor
# ─────────────────────────────────────────────────────────────────────────────

async def _process_delivery(interaction: discord.Interaction, game: GameState, shot_button: str, **kwargs):
    """Per-game lock around one ball. A second press for the SAME ball (double
    tap / lag) waits for the first to finish, then sees pending_delivery is
    already consumed and is told so, without touching the score."""
    lock = game.__dict__.get("_ball_lock")
    if lock is None:
        lock = game.__dict__.setdefault("_ball_lock", asyncio.Lock())
    async with lock:
        if game.pending_delivery is None:
            try:
                await interaction.response.send_message(
                    "This ball has already been played.", ephemeral=True
                )
            except Exception:
                pass
            return
        await _process_delivery_locked(interaction, game, shot_button, **kwargs)


async def _process_delivery_locked(
    interaction: discord.Interaction,
    game: GameState,
    shot_button: str,
    *,
    stage1_choice: str = "",
    is_recommended_shot: bool = False,
    guide_entry_exists: bool = False,
    is_free_hit: bool = False,
    direction: str | None = None,
    height: str | None = None,
):
    # BUG FIX: `cscancel` only removed the game from active_games — it never
    # stopped the match itself, because this function (and every other
    # button callback) kept acting on `game` directly regardless of whether
    # it was still registered. That let players keep playing balls on a
    # "cancelled" match. Bail out here if the game is no longer live.
    channel_id = interaction.channel_id
    if active_games.get(channel_id) is not game:
        try:
            await interaction.response.send_message(
                "This match has been cancelled.", ephemeral=True
            )
        except discord.InteractionResponded:
            pass
        return

    shot_internal     = SHOT_BUTTON_MAP.get(shot_button, "Drive")
    delivery_button   = game.pending_delivery
    delivery_internal = game.pending_delivery_internal
    bowler            = game.current_bowler
    striker           = game.striker

    # The shot is being played now, so this delivery is no longer "pending" —
    # clear it so match-recovery (_resume_prompt) doesn't think a shot is
    # still waiting to be picked once this ball has actually been bowled.
    _announced_speed               = game.pending_delivery_speed
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

    # Two-step batting UI: remember exactly where/how the shot was played
    # (None for Leave, the CPU batter, or an old resumed game).
    game.last_shot_direction = direction
    game.last_shot_height    = height

    # Same speed that was announced ("is coming with X kmph"); fall back to a fresh roll.
    speed_range  = get_delivery_speed(delivery_button, bowler.get("bowling_type"))
    speed        = _announced_speed if _announced_speed is not None else random.uniform(*speed_range)
    current_over = game.current_legal_balls // 6
    # Field Placement: the zone that would take a LOFTED shot in this direction
    # (outfield fielder, else an inner fielder covering it). None = no catch zone.
    catch_zone_key = None
    _fp_now = getattr(game, "field_positions", None)
    if height == "Loft" and direction and _fp_now is not None:
        catch_zone_key = field_logic.catch_zone_for(DIRECTIONS_BY_NAME.get(direction), _fp_now)

    # Pitch map: where the ball landed (random within the delivery's zone)
    # against where the shot was aimed. Leave is not a shot, so no timing.
    timing = None
    if shot_button != "Leave":
        timing = pitch_map.timing_for(
            delivery_button,
            SHOT_BUTTON_MAP.get(shot_button, shot_button),
            DIRECTIONS_BY_NAME.get(direction) if direction else None,
            pitch_map.batter_hand(striker),
        )

    if shot_button == "Leave":
        # BUG FIX: "Leave" was mapped to the "Defend" internal shot purely
        # for matchup/narrative lookups, but that meant it also went through
        # the normal weighted outcome roll — so leaving the ball could
        # occasionally come back as a 4 or even a 6, which makes no sense
        # (nobody scores runs off a ball they didn't play at). A genuine
        # leave is always a dot ball: no runs, no wicket.
        outcome, is_extra = "0", False
    else:
        # Loft direction risk (optional, section 7): an off-angle lofted
        # shot is harder to clear than a straight one — fewer sixes, a
        # little more wicket risk. Only ever set when height == "Loft".
        loft_zone_mod = None
        if USE_LOFT_DIRECTION_RISK and height == "Loft" and direction:
            zone_key = DIRECTIONS_BY_NAME.get(direction)
            loft_zone_mod = LOFT_DIRECTION_RISK.get(zone_key)

        # Field placement: ground shots only (loft keeps its own risk table
        # above). None when no field was set -> behaviour unchanged.
        # Lofted shot hit towards a fielder: fixed six / wicket chances from
        # the bat (and bowl) rating (field_logic.loft_field_chances); the
        # playstyle six boost is applied inside calculate_outcome. Gaps /
        # no field -> unchanged.
        loft_field = None
        _fp = getattr(game, "field_positions", None)
        if height == "Loft" and direction and _fp is not None:
            loft_field = field_logic.loft_field_for(
                DIRECTIONS_BY_NAME.get(direction), _fp,
                striker.get("bat", 80), bowler.get("bowl", 80))

        # How far the batter can clear the rope with a lofted ball that is not
        # into a fielder: low bat = short, high bat = stadium. Applied only
        # when no fielder is in the way (loft_field covers that case).
        loft_pow = None
        if height == "Loft" and loft_field is None:
            loft_pow = field_logic.loft_power(striker.get("bat", 80))

        ground_zone_mod = None
        if height == "Ground" and direction:
            ground_zone_mod = field_logic.get_field_modifier(
                DIRECTIONS_BY_NAME.get(direction),
                getattr(game, "field_positions", None),
            )

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
            ground_type=getattr(game, "pitch_type", None),
            delivery_button=delivery_button,
            prev_delivery=getattr(game, "last_delivery_internal", None),
            bowler_balls_done=game.bowler_ball_count.get(bname, 0),
            bowler_max_balls=game.max_bowler_balls(),
            loft_zone_mod=loft_zone_mod,
            ground_zone_mod=ground_zone_mod,
            loft_field_chances=loft_field,
            loft_power=loft_pow,
            timing_mod=timing["mod"] if timing else None,
        )
    # Remember this delivery type for the next ball ("Mind Games" playstyle).
    game.last_delivery_internal = delivery_internal

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

    # A wicket off a lofted shot into a fielded zone is a catch (a free hit
    # cannot be a catch, so this runs after the free-hit downgrade above).
    caught_zone_key = catch_zone_key if outcome == "W" else None

    # One six distance, used by both the commentary and the wagon-wheel picture.
    six_distance = random.choice(SIX_DISTANCES) if outcome == "6" else None

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
        direction=direction,
        six_distance=six_distance,
        catch_zone=field_logic.zone_name(caught_zone_key) if caught_zone_key else None,
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
        if outcome in ("4", "6"):
            game._do_record_boundary_direction(int(outcome), delivery_button, shot_button)
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

    # Commentary GIFs set with /addgif (player-specific first, else universal, random pick)
    try:
        from gif_store import collect_ball_gifs
        gif_urls += collect_ball_gifs(
            outcome=outcome, shot_button=shot_button, batter=sname, bowler=bname,
            runs_before=striker_runs_before,
            runs_after=game.batsman_stats.get(sname, {}).get("runs", 0),
            bowler_wkts_before=bowler_wickets_before,
            bowler_wkts_after=game.bowler_stats.get(bname, {}).get("wickets", 0),
        )
    except Exception as _ge:
        print(f"[gif] skipped ({type(_ge).__name__}: {_ge})")

    # Umpire GIFs (global — fire for every player on top of player GIFs)
    _umpire_event = {
        "4": "four", "6": "six", "W": "out",
        "Wd": "wide", "NB": "noball", "NB+1": "noball",
    }.get(outcome)
    if _umpire_event:
        if UMPIRE_BANNER:
            gif_urls.append(f"umpire:{_umpire_event}")
        else:
            ump_gif = get_umpire_gif(_umpire_event)
            if ump_gif:
                gif_urls.append(ump_gif)

    # Batting milestone GIFs (50, 100, 150, …)
    if outcome in ("1", "2", "3", "4", "6"):
        runs_after = game.batsman_stats.get(sname, {}).get("runs", 0)
        for milestone in (50, 100, 150, 200, 250, 300):
            if striker_runs_before < milestone <= runs_after:
                if milestone in (50, 100):
                    _m_emoji = emojis.CENTURY if milestone == 100 else emojis.HALFCENTURY
                    _m_word = "CENTURY" if milestone == 100 else "HALF-CENTURY"
                    commentary = f"{commentary}\n{_m_emoji} **{sname}** reaches a {_m_word}! ({runs_after})"
                gif = get_milestone_gif(sname, str(milestone))
                if not gif:
                    gif = get_player_media(sname, str(milestone))
                if gif:
                    gif_urls.append(gif)
                break

    # Pitch picture: landing spot vs the shot line, with the result. Sent with
    # the ball; None when there was no timing for this ball.
    pitch = None
    if timing is not None:
        try:
            _txt = {"6": "SIX", "4": "FOUR", "W": "OUT"}.get(outcome, f"{outcome} run" + ("" if outcome == "1" else "s"))
            pitch = await asyncio.to_thread(
                pitch_map.pitch_png, sname, pitch_map.batter_hand(striker),
                SHOT_BUTTON_MAP.get(shot_button, shot_button),
                DIRECTIONS_BY_NAME.get(direction) if direction else None,
                timing, delivery_button,
                field_logic.catch_zone_for(DIRECTIONS_BY_NAME.get(direction) if direction else None,
                                           getattr(game, "field_positions", None)),
                _txt,
            )
        except Exception:
            pitch = None

    # Wagon wheel: only for a four or a six (None for everything else).
    wheel = None
    if outcome in ("4", "6"):
        try:
            wheel = await asyncio.to_thread(
                wagon_wheel_png, direction, outcome, six_distance, sname, shot_internal,
                field_positions=getattr(game, "field_positions", None),
            )
        except Exception:
            wheel = None
    elif caught_zone_key:
        # Lofted catch: red curved line from the batter to the fielder, ending in OUT.
        try:
            wheel = await asyncio.to_thread(
                catch_wheel_png, direction, caught_zone_key, sname,
                getattr(game, "field_positions", None), shot_internal,
            )
        except Exception:
            wheel = None

    # ── Acknowledge the shot button (remove it from the message) ─────────
    # BUG FIX #4: always ack the interaction immediately to avoid
    # "This interaction failed" errors. We edit in-place to remove the view.
    try:
        await interaction.response.edit_message(view=None)
    except (discord.InteractionResponded, RuntimeError):
        # RuntimeError: autoplay's stand-in interaction has no message to
        # edit. This ack is cosmetic, so skip it and let the ball finish.
        pass

    channel      = _ch(interaction, game)
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
            batting_uid = game.batting_user_id
            reviews     = game.drs_reviews.get(batting_uid, 0)
            if batting_uid in getattr(game, "bot_ids", ()):
                reviews = 0   # a ranked bot never asks for a review
            # While a review is possible the wicket is NOT final yet, so the
            # umpire / wicket GIFs are held back and only shown once the
            # decision is confirmed (see DRSView._flush_held_gifs).
            await _send_commentary_with_gifs(
                channel, commentary, embed,
                [] if reviews > 0 else gif_urls, interaction,
            )
            if reviews > 0:
                drs_view = DRSView(
                    game, channel, delivery_internal, shot_internal,
                    commentary=commentary,
                    held_gifs=gif_urls,
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
                await _do_innings_break(channel, game, commentary, embed, gif_urls, interaction, wheel=wheel, pitch=pitch)
            else:
                save_game_innings_stats(game, game.batting_user_id, game.bowling_user_id, is_final=True)
                _save_match_record(game)
                await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction, wheel=wheel, pitch=pitch)
                result_embed = build_result_embed(game)
                await channel.send(embed=result_embed)
                await _ranked_announce(channel, game)
                await _ranked_credits_announce(channel, game)
                await _send_match_summary(channel, game)
                await _flush_quest_notes(channel, game)
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
            await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction, wheel=wheel, pitch=pitch)
            await _send_next_bowler_prompt(channel, game)
            return

        # ── Extras (wide / no-ball) — re-bowl ────────────────────────────
        if is_extra:
            await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction, wheel=wheel, pitch=pitch)
            # NB outcomes set pending_free_hit; wides do not.
            # _send_bowling_prompt reads and clears the flag to show FREE HIT label.
            await _send_bowling_prompt(channel, game)
            return

        # ── Normal continue ───────────────────────────────────────────────
        await _send_commentary_with_gifs(channel, commentary, embed, gif_urls, interaction, wheel=wheel, pitch=pitch)
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
            await _ranked_announce(channel, game)
            await _ranked_credits_announce(channel, game)
            await _flush_quest_notes(channel, game)
            active_games.pop(channel.id, None)
        return

    # BUG FIX #6: clear game.striker before showing the dropdown so that
    # get_available_batsmen() doesn't accidentally include the dismissed
    # batsman (who is still referenced in game.striker at this point because
    # add_wicket() only appends to dismissed[], it doesn't clear game.striker).
    game.striker = None

    await _send_next_batsman_prompt(
        channel, game, content=f"Wicket! Choose your next batter {game.batting_user.mention}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Innings transition helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _do_innings_break(channel, game: GameState, commentary, embed, gif_urls=None, interaction=None, wheel=None, pitch=None):
    """End of innings via overs exhausted: save stats, send commentary, start break."""
    save_game_innings_stats(game, game.batting_user_id, game.bowling_user_id)
    await _send_commentary_with_gifs(channel, commentary, embed, gif_urls or [], interaction, wheel=wheel, pitch=pitch)
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


# ── Umpire banner (generated GIF for four / six / out / wide / noball) ───────
UMPIRE_BANNER = True   # False = go back to the GIF links saved in the umpire_media table


async def _send_gif_item(channel, item: str) -> None:
    """gif_urls holds plain URLs, or "umpire:<event>" for the generated umpire banner."""
    if item.startswith("umpire:"):
        try:
            from umpire_gif import umpire_file
            f = await umpire_file(item[7:])
            if f is not None:
                await channel.send(file=f)
        except Exception as e:
            print(f"[umpire_gif] skipped ({type(e).__name__}: {e})")
        return
    await channel.send(item)


# ── MATCH STARTING banner (animated GIF, made on the fly) ────────────────────
def _banner_names(game: GameState) -> tuple[str, str]:
    """Team names for the banner: the XI's team name, else the player's display name."""
    out = []
    for m in (game.challenger, game.opponent):
        t = game.teams.get(m.id) or {}
        out.append(str(t.get("name") or getattr(m, "display_name", "") or "Team"))
    return out[0], out[1]


async def _send_match_banner(channel, game: GameState) -> None:
    """Posts the MATCH STARTING GIF. Cosmetic only: any failure is skipped, the match goes on."""
    try:
        from match_start_gif import match_start_file
        a, b = _banner_names(game)
        f = await match_start_file(a, b)
        if f is not None:
            await channel.send(file=f)
        if UMPIRE_BANNER:   # pre-render the umpire banners in the background
            from umpire_gif import warm
            asyncio.create_task(warm())
    except Exception as e:
        print(f"[match_banner] skipped ({type(e).__name__}: {e})")


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
            await _send_match_banner(channel, self.game)
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

            channel = _ch(interaction, self.game)
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


async def start_toss(channel, game: GameState) -> None:
    """Send the coin-toss prompt (used by ranked matches, which skip Accept / Decline)."""
    allowed = _match_ids(game)

    async def _send_toss():
        await _send_match_banner(channel, game)
        toss_embed = _build_toss_embed(game)
        toss_view  = TossView(game)
        toss_view._channel = channel
        await channel.send(embed=toss_embed, view=toss_view, file=_toss_image_file())
    await _run_safely(_send_toss, allowed_ids=allowed, notify_channel=channel)


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

        # Record who is batting in innings 1 — match_result(), team1_name/
        # team2_name, and the t1_/t2_ batsmen/bowlers properties all key off
        # this. It was never being set, so it stayed None forever and the
        # winner/team labelling was effectively random (always fell back to
        # the "else" branch), which could show the losing side as the winner.
        self.game.innings1_batting_user_id = self.game.batting_user_id

        # Initialise DRS reviews for both teams
        self.game.drs_reviews[self.game.batting_user_id] = 2
        self.game.drs_reviews[self.game.bowling_user_id] = 2

        bat_team_name = self.game.get_batting_team().get("name", "Batting side")
        self.game.toss_note = f"{bat_team_name} chose to bat first" if bat else f"{bat_team_name} will bat first (opponent chose to bowl)"

        sep = "──────────────────────"
        channel = _ch(interaction, self.game)
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
        channel = _ch(interaction, self.game)
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

            channel = _ch(interaction, self.game)
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
                    _bat_stats_file(self.game.batting_user_id, striker, "ON STRIKE", "bat_striker.png"),
                    _bat_stats_file(self.game.batting_user_id, non_striker, "NON-STRIKER", "bat_nonstriker.png"),
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

        channel = _ch(interaction, self.game)
        allowed = _match_ids(self.game)
        try:
            await interaction.response.edit_message(content=f"**{bname}** will bowl.", view=None, embed=None)
        except Exception:
            pass  # cosmetic only

        async def _send_prompt():
            bowl_file = await _bowl_stats_file(self.game.bowling_user_id, bowler)
            files = [bowl_file] if bowl_file else []

            async def _first_delivery():
                await _send_bowling_prompt(
                    channel, self.game,
                    intro=f"{bname} ({bowler.get('ovr','?')}) comes into the attack",
                    files=files,
                )

            # Field Placement also happens at the start: once the first bowler
            # is picked, the captain sets the field for over 1 (powerplay cap
            # applies) before the first delivery. Skipped if a field is already
            # locked in for this over (e.g. a resumed match).
            if (field_logic.FIELD_FEATURE_ENABLED
                    and getattr(self.game, "field_over_idx", None) != 0):
                try:
                    await _send_field_prompt(
                        channel, self.game, 0, after=_first_delivery, kind="start")
                    return
                except Exception as e:   # never let the new feature stall a match
                    print(f"[field] start prompt failed ({type(e).__name__}: {e}) — skipping field setup")
            await _first_delivery()
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

# Medium pacer — deliberately different colours from the fast bowler's set
_MEDIUM_STAGE1_STYLES = {
    "Outswing": discord.ButtonStyle.success,
    "Inswing":  discord.ButtonStyle.primary,
    "Cutter":   discord.ButtonStyle.danger,
    "Slower":   discord.ButtonStyle.success,
}
_MEDIUM_STAGE2_STYLES = {
    "Bouncer":     discord.ButtonStyle.primary,
    "Yorker":      discord.ButtonStyle.danger,
    "Good Length": discord.ButtonStyle.success,
    "Full":        discord.ButtonStyle.primary,
}


class FastBowlStage1View(_TimeoutMixin, ui.View):
    """Fast bowling — ONE message, two rows.

    Row 0 (swing/speed) is active first; row 1 (line/length) is greyed out.
    Once row 0 is picked, row 0 turns grey/disabled and row 1 becomes active.
    Picking row 1 triggers the actual delivery.
    """

    def __init__(self, game: GameState, is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game          = game
        self.is_free_hit   = is_free_hit
        self.stage1_choice = ""
        self._stage1_btns: list[ui.Button] = []
        self._stage2_btns: list[ui.Button] = []
        self._stage2_keys: list[str] = []   # original (logic) names, same order as _stage2_btns

        # Fast bowler and Medium pacer share this view; only the buttons / colours differ.
        self.btype = (game.current_bowler or {}).get("bowling_type", "Fast")
        stage1_labels, stage2_labels = get_two_stage_buttons(self.btype)
        if self.btype == "Medium Pacer":
            self._s1_styles, self._s2_styles = _MEDIUM_STAGE1_STYLES, _MEDIUM_STAGE2_STYLES
        else:
            self._s1_styles, self._s2_styles = _FAST_STAGE1_STYLES, _FAST_STAGE2_STYLES

        for label in stage1_labels:
            style = self._s1_styles.get(label, discord.ButtonStyle.secondary)
            btn   = ui.Button(label=button_label(self.btype, label), style=style, row=0)
            btn.callback = self._make_stage1_callback(label)
            self._stage1_btns.append(btn)
            self.add_item(btn)

        for label in stage2_labels:
            # Locked + grey until step 1 is chosen
            btn = ui.Button(label=button_label(self.btype, label), style=discord.ButtonStyle.secondary, row=1, disabled=True)
            btn.callback = self._make_stage2_callback(label)
            self._stage2_btns.append(btn)
            self._stage2_keys.append(label)
            self.add_item(btn)

    def _make_stage1_callback(self, stage1_label: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only the bowling team bowls!", ephemeral=True)
                return
            if self.stage1_choice:  # already picked — ignore double taps
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                return
            self.stage1_choice = stage1_label

            # Row 0 -> grey + disabled
            for b in self._stage1_btns:
                b.disabled = True
                b.style    = discord.ButtonStyle.secondary
            # Row 1 -> unlock with the normal colours
            for b, key in zip(self._stage2_btns, self._stage2_keys):
                b.disabled = False
                b.style    = self._s2_styles.get(key, discord.ButtonStyle.secondary)

            try:
                # view only — keeps the existing message text/embed as it is
                await interaction.response.edit_message(view=self)
            except Exception as e:
                print(f"[fast bowl] stage1 edit failed ({type(e).__name__}: {e})")
        return callback

    def _make_stage2_callback(self, delivery_button: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only the bowling team bowls!", ephemeral=True)
                return
            if not self.stage1_choice:
                await interaction.response.send_message("Pehle swing/speed choose karo.", ephemeral=True)
                return
            if self.is_finished():   # delivery already chosen (double tap)
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                return
            self.stop()
            delivery_internal = DELIVERY_BUTTON_MAP.get(delivery_button, delivery_button)
            self.game.pending_delivery          = delivery_button
            self.game.pending_delivery_internal = delivery_internal
            self.game.phase = "bat_select"

            speed    = random.uniform(*get_delivery_speed(delivery_button, self.btype))
            combined = f"{self.stage1_choice} {delivery_button}"
            # Cached so a failed send can be retried (or _resume_prompt used)
            # with the exact same speed/label instead of losing it.
            self.game.pending_delivery_speed  = speed
            self.game.pending_stage1_choice   = self.stage1_choice

            channel = _ch(interaction, self.game)
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

# Orthodox spinner — own colour set
_ORTHODOX_STYLES = {
    "Arm Ball":    discord.ButtonStyle.primary,
    "Top Spinner": discord.ButtonStyle.danger,
    "Doosra":      discord.ButtonStyle.success,
    "Carrom":      discord.ButtonStyle.primary,
    "Slider":      discord.ButtonStyle.success,
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
        buttons = get_spin_buttons(btype)
        styles  = _ORTHODOX_STYLES if btype == "Orthodox" else _SPIN_STYLES
        for i, label in enumerate(buttons):
            style = styles.get(label, discord.ButtonStyle.secondary)
            btn   = ui.Button(label=button_label(btype, label), style=style, row=i // 3)
            btn.callback = self._make_callback(label)
            self.add_item(btn)

    def _make_callback(self, delivery_button: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.bowling_user_id:
                await interaction.response.send_message("Only the bowling team bowls!", ephemeral=True)
                return
            if self.is_finished():   # delivery already chosen (double tap)
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                return
            self.stop()
            btype             = self.game.current_bowler.get("bowling_type", "Off Spin")
            delivery_internal = resolve_delivery(delivery_button, btype)
            self.game.pending_delivery          = delivery_button
            self.game.pending_delivery_internal = delivery_internal
            self.game.phase = "bat_select"

            speed = random.uniform(*get_delivery_speed(delivery_button, btype))
            self.game.pending_delivery_speed = speed
            self.game.pending_stage1_choice  = ""

            channel = _ch(interaction, self.game)
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

_STAGE1_STYLES = {
    "Loft":   discord.ButtonStyle.danger,
    "Ground": discord.ButtonStyle.success,
    "Leave":  discord.ButtonStyle.secondary,
}
# (row, key) order for the 3x3 direction grid — matches the camera-behind-
# bowler, right-handed-batter orientation in the design doc exactly.
_DIRECTION_GRID = [
    ("UL", 1), ("U", 1), ("UR", 1),
    ("L",  2), ("C", 2), ("R",  2),
    ("DL", 3), ("D", 3), ("DR", 3),
]


class BattingView(_TimeoutMixin, ui.View):
    """Two-step batting: [Loft] [Ground] [Leave] first; after Loft/Ground is
    tapped those three grey out and a 3x3 direction grid appears on the same
    message. Leave ends the ball immediately (no direction grid)."""

    def __init__(self, game: GameState, *, stage1_choice: str = "", is_free_hit: bool = False):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game          = game
        self.stage1_choice = stage1_choice   # the FAST bowler's swing choice (unrelated to height)
        self.is_free_hit   = is_free_hit
        self.height        = ""              # "Loft" | "Ground" once stage 1 is picked

        delivery_btn = game.pending_delivery or ""
        if stage1_choice:
            guide_list = FAST_SHOT_GUIDE.get((stage1_choice, delivery_btn), [])
        else:
            guide_list = SPIN_SHOT_GUIDE.get(delivery_btn, [])

        self.recommended        = guide_list
        self.guide_entry_exists = bool(guide_list)  # False when delivery not in guide

        self._stage1_btns: list[ui.Button] = []
        self._direction_btns: list[ui.Button] = []
        self._direction_keys: list[str] = []   # parallel to _direction_btns
        self._add_stage1_buttons()

    def _add_stage1_buttons(self):
        for label in ("Loft", "Ground", "Leave"):
            btn = ui.Button(label=label, style=_STAGE1_STYLES[label], row=0)
            btn.callback = self._make_stage1_callback(label)
            self._stage1_btns.append(btn)
            self.add_item(btn)

    def _add_direction_buttons(self):
        for key, row in _DIRECTION_GRID:
            arrow, name = DIRECTIONS[key]
            style = discord.ButtonStyle.primary if key == "C" else discord.ButtonStyle.secondary
            btn   = ui.Button(label=f"{arrow} {name}", style=style, row=row)
            btn.callback = self._make_direction_callback(key)
            self._direction_btns.append(btn)
            self._direction_keys.append(key)
            self.add_item(btn)

    def _make_stage1_callback(self, label: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                await interaction.response.send_message("Only the batting team plays shots!", ephemeral=True)
                return
            if self.height or self.is_finished():   # already picked — ignore double taps
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                return

            if label == "Leave":
                if self.is_finished():
                    try:
                        await interaction.response.defer()
                    except Exception:
                        pass
                    return
                self.stop()
                await _process_delivery(
                    interaction, self.game, "Leave",
                    stage1_choice=self.stage1_choice,
                    is_recommended_shot=False,
                    guide_entry_exists=self.guide_entry_exists,
                    is_free_hit=self.is_free_hit,
                )
                return

            self.height = label
            # Row 0 -> grey + disabled
            for b in self._stage1_btns:
                b.disabled = True
                b.style    = discord.ButtonStyle.secondary
            # Reveal the direction grid on the same message
            self._add_direction_buttons()
            try:
                await interaction.response.edit_message(view=self)
            except Exception as e:
                print(f"[batting] stage1 edit failed ({type(e).__name__}: {e})")
        return callback

    def _make_direction_callback(self, direction_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.game.batting_user_id:
                await interaction.response.send_message("Only the batting team plays shots!", ephemeral=True)
                return
            if not self.height:
                await interaction.response.send_message("Pehle Loft ya Ground choose karo.", ephemeral=True)
                return
            if self.is_finished():   # shot already chosen (double tap)
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                return
            self.stop()

            delivery_internal = self.game.pending_delivery_internal or ""
            shot_button = translate_shot(self.height, direction_key, delivery_internal)
            is_recommended = shot_button in self.recommended
            _, direction_name = DIRECTIONS[direction_key]

            await _process_delivery(
                interaction, self.game, shot_button,
                stage1_choice=self.stage1_choice,
                is_recommended_shot=is_recommended,
                guide_entry_exists=self.guide_entry_exists,
                is_free_hit=self.is_free_hit,
                direction=direction_name,
                height=self.height,
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
        held_gifs: list[str] | None = None,
    ):
        super().__init__(timeout=10)
        self._held_gifs            = list(held_gifs or [])
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

    async def _flush_held_gifs(self):
        """Send the wicket GIFs that were held back. Called ONLY once the OUT
        decision is final (upheld / accepted / timed out) — never on NOT OUT."""
        gifs, self._held_gifs = self._held_gifs, []
        for url in gifs:
            try:
                await _send_gif_item(self.channel, url)
            except Exception:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if active_games.get(interaction.channel_id) is not self.game:
            try:
                await interaction.response.send_message(
                    "This match has been cancelled.", ephemeral=True
                )
            except discord.InteractionResponded:
                pass
            return False
        return True

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
        reviews_before = self.game.drs_reviews.get(uid, 0)
        # BUG FIX (cricket rule): a successful review does NOT cost you a
        # review in real DRS — only an unsuccessful one does. The review is
        # only actually deducted below once we know whether it was upheld or
        # overturned; this provisional number is just for the "pending"
        # message so the player sees what it would drop to if unsuccessful.
        provisional_remaining = max(0, reviews_before - 1)

        # Cosmetic status message only — the actual decision below does not
        # depend on this succeeding.
        try:
            await interaction.response.edit_message(
                content=(
                    f"**Review Referred to Third Umpire…**\n"
                    f"Reviews remaining if unsuccessful: **{provisional_remaining}**"
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
                # Review succeeded — real DRS does not cost a review in this
                # case, so drs_reviews is left untouched (still at
                # reviews_before).
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
                # OUT upheld — unsuccessful review, so it actually costs
                # them a review now.
                self.game.drs_reviews[uid] = provisional_remaining
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
                await self._flush_held_gifs()
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
        await self._flush_held_gifs()
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
        await self._flush_held_gifs()
        await _safe_next(self.channel, self.game, lambda: _handle_wicket_fall(self.channel, self.game))


# ─────────────────────────────────────────────────────────────────────────────
# Next batsman
# ─────────────────────────────────────────────────────────────────────────────

async def _close_swap_views(swaps) -> None:
    """Grey out / close Impact Player lists whose origin prompt is over."""
    for sv in swaps:
        if sv.is_finished():
            continue            # already used (or timed out) - leave its message alone
        sv.stop()
        inter = getattr(sv, "_interaction", None)
        if inter is not None:
            try:
                await inter.edit_original_response(
                    content="⛔ Impact Player list closed — the selection was already made.",
                    view=None,
                )
            except Exception:
                pass


class _ImpactButtonMixin:
    """Adds an optional Impact Player button next to a next-bowler / next-
    batsman prompt. One tap opens an ephemeral ImpactPlayerSwapView; the
    button goes grey and disabled the moment that team's one sub is used."""

    def stop(self):
        """When the normal bowler / batter list is answered (or replaced), the
        Impact Player list that was opened from it is closed and greyed out, so
        it can never pick a bowler / batter afterwards."""
        super().stop()
        swaps = list(self.__dict__.get("_swap_views", []))
        if swaps:
            try:
                asyncio.get_running_loop().create_task(_close_swap_views(swaps))
            except RuntimeError:
                pass

    def _add_impact_button(self, uid: int, kind: str):
        game = self.game
        used = game.impact_used.get(uid, False)
        if not used and not game.impact_subs_pool.get(uid):
            return  # team never nominated any subs — keep the UI clean
        btn = ui.Button(
            label="Impact Player used" if used else "🔁 Impact Player",
            style=discord.ButtonStyle.secondary if used else discord.ButtonStyle.primary,
            disabled=used,
            row=1,
        )

        async def callback(interaction: discord.Interaction):
            if interaction.user.id != uid:
                await interaction.response.send_message(
                    "Only that team's captain can make this substitution!", ephemeral=True
                )
                return
            if not game.impact_available(uid):
                await interaction.response.send_message(
                    "Your Impact Player sub has already been used this match.", ephemeral=True
                )
                return
            if self.is_finished():
                await interaction.response.send_message(
                    "This selection is already done — Impact Player is closed for it.", ephemeral=True
                )
                return
            swap_view = ImpactPlayerSwapView(game, uid, origin_view=self, origin_kind=kind)
            swap_view._interaction = interaction
            self.__dict__.setdefault("_swap_views", []).append(swap_view)
            await interaction.response.send_message(
                content="🔁 **Impact Player** — choose your substitute.",
                view=swap_view,
                ephemeral=True,
            )

        btn.callback = callback
        self.add_item(btn)


class ImpactPlayerSwapView(ui.View):
    """Ephemeral 2-step flow: pick the bench sub coming IN, then the XI
    player going OUT. Only the captain who opened it can use it. Confirming
    uses up that team's one Impact Player swap for the whole match."""

    def __init__(self, game: GameState, uid: int, origin_view, origin_kind: str):
        super().__init__(timeout=120)
        self.game = game
        self.uid = uid
        self.origin_view = origin_view
        self.origin_kind = origin_kind  # "bowler" or "batsman"
        self.incoming: dict | None = None
        self._add_incoming_select()

    def _add_incoming_select(self):
        pool = self.game.impact_subs_pool.get(self.uid, [])
        options = [
            discord.SelectOption(
                label=_pname(p),
                description=f"{p.get('role', '?')} | OVR:{p.get('ovr', '?')}",
                value=_pname(p),
            )
            for p in pool[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="No subs available", value="none")]
        select = ui.Select(placeholder="Choose your Impact Player substitute", options=options)
        select.callback = self._incoming_callback
        self.add_item(select)

    async def _origin_closed(self, interaction: discord.Interaction) -> bool:
        """True (and the user is told) if the normal list this was opened from is already answered."""
        if self.origin_view is not None and self.origin_view.is_finished():
            self.stop()
            try:
                await interaction.response.edit_message(
                    content="⛔ Impact Player list closed — the selection was already made.", view=None
                )
            except Exception:
                pass
            return True
        return False

    async def _incoming_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("This isn't your substitution.", ephemeral=True)
            return
        if await self._origin_closed(interaction):
            return
        name = interaction.data["values"][0]
        if name == "none":
            await interaction.response.send_message("You have no subs left to bring on.", ephemeral=True)
            return
        pool = self.game.impact_subs_pool.get(self.uid, [])
        incoming = next((p for p in pool if _pname(p) == name), None)
        if incoming is None:
            await interaction.response.send_message("That sub is no longer available.", ephemeral=True)
            return
        self.incoming = incoming
        self.clear_items()
        self._add_outgoing_select()
        await interaction.response.edit_message(
            content=f"🔁 Bringing on **{name}** — who comes off?",
            view=self,
        )

    def _add_outgoing_select(self):
        team = self.game.teams.get(self.uid, {})
        players = self.game._team_players(team)
        on_field = {
            _pname(self.game.striker),
            _pname(self.game.non_striker),
            _pname(self.game.current_bowler),
        }
        options = [
            discord.SelectOption(
                label=_pname(p),
                description=f"{p.get('role', '?')} | OVR:{p.get('ovr', '?')}",
                value=_pname(p),
            )
            for p in players
            if _pname(p) and _pname(p) not in on_field
        ][:25]
        if not options:
            options = [discord.SelectOption(label="No eligible player to replace", value="none")]
        select = ui.Select(placeholder="Choose who comes off", options=options)
        select.callback = self._outgoing_callback
        self.add_item(select)

    async def _outgoing_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("This isn't your substitution.", ephemeral=True)
            return
        if await self._origin_closed(interaction):
            return
        outgoing_name = interaction.data["values"][0]
        if outgoing_name == "none":
            await interaction.response.send_message(
                "No one on your team can be replaced right now.", ephemeral=True
            )
            return
        ok = self.game.apply_impact_sub(self.uid, self.incoming, outgoing_name)
        if not ok:
            await interaction.response.send_message("That player couldn't be found — try again.", ephemeral=True)
            return
        self.stop()
        incoming_name = _pname(self.incoming)
        await interaction.response.edit_message(
            content=f"✅ **{incoming_name}** is on in place of **{outgoing_name}**.",
            view=None,
        )

        channel = _ch(interaction, self.game)
        team_name = self.game.teams.get(self.uid, {}).get("name", "Team")
        await channel.send(
            f"🔁 **Impact Player!** {team_name} bring on **{incoming_name}** "
            f"in place of **{outgoing_name}**."
        )

        # The original prompt's dropdown was built before this swap, so it
        # won't offer the new player — retire it and send a fresh one.
        old_view = self.origin_view
        old_msg  = getattr(old_view, "_message", None)
        if old_view is not None:
            old_view.stop()
        if old_msg is not None:
            try:
                await old_msg.edit(view=None)
            except Exception:
                pass

        if self.origin_kind == "bowler":
            await _send_next_bowler_prompt(channel, self.game)
        else:
            await _send_next_batsman_prompt(channel, self.game)


class NextBatsmanView(_ImpactButtonMixin, _TimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self._add_dropdown()
        self._add_impact_button(game.batting_user_id, "batsman")

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
        channel = _ch(interaction, self.game)

        try:
            await interaction.response.edit_message(
                content=f"**{name}** selected.", view=None, embed=None
            )
        except Exception:
            pass  # cosmetic only

        async def _work():
            bat_file = await _bat_stats_file(self.game.batting_user_id, player, "NEW BATTER")
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
# Field placement (end of every over, before the next bowler is chosen)
# ─────────────────────────────────────────────────────────────────────────────

async def _send_field_prompt(channel, game: GameState, over_idx: int, after=None, kind: str = "over"):
    """Public field prompt. `after` is the no-arg coroutine run once the field
    is locked in (default: the next-bowler prompt). kind is "over" at the end
    of an over, "start" when the first bowler of an innings has just been picked."""
    view = FieldSetupView(game, over_idx, after=after, kind=kind)
    view._channel = channel
    msg = await channel.send(
        content=view.prompt_text(),
        view=view,
    )
    view._message = msg


def _formation_options(max_allowed: int) -> list[discord.SelectOption]:
    """Formations that are legal for the current cap (powerplay = 2 outside)."""
    opts: list[discord.SelectOption] = []
    for name, counts in field_logic.FORMATIONS.items():
        if not field_logic.formation_fits(name, max_allowed):
            continue
        out = field_logic.outside_count(counts)
        opts.append(discord.SelectOption(
            label=name,
            description=f"{out} outside the circle, {field_logic.PLAYERS_TOTAL - out} inside"[:100],
            value=name,
        ))
    return opts


class _FieldEditorView(ui.View):
    """The private field editor. Sent as an EPHEMERAL reply, so only the
    bowling captain sees it. Eleven zone buttons laid out like the ground,
    an Add / Remove mode, Reset, Confirm, and a formation dropdown. The state
    lives on the parent FieldSetupView."""

    def __init__(self, parent: "FieldSetupView"):
        super().__init__(timeout=field_logic.FIELD_VIEW_TIMEOUT)
        self.parent = parent
        self._zone_btns: dict[str, ui.Button] = {}

        for row, keys in enumerate(field_logic.GRID_LAYOUT):
            for key in keys:
                btn = ui.Button(label=field_logic.zone_name(key), row=row)
                btn.callback = self._make_zone(key)
                self._zone_btns[key] = btn
                self.add_item(btn)

        self._mode_btn = ui.Button(label="Mode: Add", style=discord.ButtonStyle.success, row=3)
        self._mode_btn.callback = self._toggle_mode
        self.add_item(self._mode_btn)

        reset_btn = ui.Button(label="Reset", style=discord.ButtonStyle.secondary, row=3)
        reset_btn.callback = self._reset
        self.add_item(reset_btn)

        self._confirm_btn = ui.Button(label="Confirm", style=discord.ButtonStyle.primary, emoji="✅", row=3)
        self._confirm_btn.callback = self._confirm
        self.add_item(self._confirm_btn)

        self._formation_sel = ui.Select(
            placeholder="Load a formation",
            options=_formation_options(parent.max_allowed),
            row=4,
        )
        self._formation_sel.callback = self._pick_formation
        self.add_item(self._formation_sel)

        self._mode = "add"
        self._refresh()

    # -- state ---------------------------------------------------------
    def _refresh(self):
        p = self.parent
        for key, btn in self._zone_btns.items():
            n = int(p.counts.get(key, 0))
            btn.label = f"{field_logic.zone_name(key)} · {n}"
            btn.style = discord.ButtonStyle.success if n else discord.ButtonStyle.secondary
        if self._mode == "add":
            self._mode_btn.label = "Mode: Add"
            self._mode_btn.style = discord.ButtonStyle.success
        else:
            self._mode_btn.label = "Mode: Remove"
            self._mode_btn.style = discord.ButtonStyle.danger
        self._confirm_btn.disabled = not p.is_valid()

    async def _show(self, interaction: discord.Interaction, *, note: str | None = None):
        """Re-render the editor in place (text, picture, buttons)."""
        self._refresh()
        p = self.parent
        f = await p.field_file(p.counts)
        text = p.editor_text() + (f"\n{note}" if note else "")
        await interaction.response.edit_message(
            content=text, view=self, attachments=[f] if f else [])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.parent.game.bowling_user_id:
            await interaction.response.send_message("Only the bowling captain sets the field!", ephemeral=True)
            return False
        if self.parent._done:
            await interaction.response.send_message("The field is already locked in.", ephemeral=True)
            return False
        return True

    # -- callbacks -----------------------------------------------------
    def _make_zone(self, key: str):
        async def callback(interaction: discord.Interaction):
            p = self.parent
            n = int(p.counts.get(key, 0))
            if self._mode == "add":
                if p.total() >= field_logic.PLAYERS_TOTAL:
                    await interaction.response.send_message(
                        "All 11 fielders are already placed. Switch to Remove to move one.", ephemeral=True)
                    return
                if key in field_logic.FIELD_POSITIONS and p.outside() >= p.max_allowed:
                    await interaction.response.send_message(
                        f"Max {p.max_allowed} fielders outside the circle right now.", ephemeral=True)
                    return
                if key in field_logic.INNER_ZONES and n >= field_logic.MAX_PER_INNER_ZONE:
                    await interaction.response.send_message("That zone is full.", ephemeral=True)
                    return
                p.counts[key] = n + 1
            else:
                if n <= 0:
                    await interaction.response.send_message("No fielder there to remove.", ephemeral=True)
                    return
                if n == 1:
                    p.counts.pop(key, None)
                else:
                    p.counts[key] = n - 1
            await self._show(interaction)
        return callback

    async def _toggle_mode(self, interaction: discord.Interaction):
        self._mode = "remove" if self._mode == "add" else "add"
        await self._show(interaction)

    async def _reset(self, interaction: discord.Interaction):
        p = self.parent
        p.counts = field_logic.default_field(p.max_allowed)
        await self._show(interaction, note="*Reset to the default formation.*")

    async def _pick_formation(self, interaction: discord.Interaction):
        p = self.parent
        name = self._formation_sel.values[0]
        p.counts = field_logic.formation_counts(name)
        await self._show(interaction, note=f"*Formation: {name}*")

    async def _confirm(self, interaction: discord.Interaction):
        p = self.parent
        if p._done:
            await interaction.response.send_message("The field is already locked in.", ephemeral=True)
            return
        if not p.is_valid():
            await interaction.response.send_message(p.invalid_reason(), ephemeral=True)
            return
        try:
            await interaction.response.edit_message(content="✅ Field locked in.", view=None, attachments=[])
        except Exception:
            pass
        self.stop()
        await p._finalize(p._channel or _ch(interaction, p.game), dict(p.counts))


class FieldSetupView(_TimeoutMixin, ui.View):
    """Public prompt for the field: shown at the end of every over, and at the
    start of an innings once the first bowler is picked. The bowling captain
    picks a formation from the dropdown, presses Customise for the private
    editor, or Confirm to lock in the current field. Timeout -> the default
    formation is applied (NOT the usual AFK abandon) so the match never stalls."""

    def __init__(self, game: GameState, over_idx: int, after=None, kind: str = "over"):
        super().__init__(timeout=field_logic.FIELD_VIEW_TIMEOUT)
        self.game = game
        self.over_idx = over_idx                       # 0-based over this field is FOR
        self.max_allowed = field_logic.max_fielders_allowed(over_idx, game.overs)
        self.in_powerplay = field_logic.is_powerplay(over_idx, game.overs)
        self.kind = kind
        self._after = after
        # Start from the previous field when it is still legal (one tap to
        # keep it); otherwise the default formation for this phase.
        prev = dict(getattr(game, "field_positions", None) or {})
        if field_logic.counts_valid(prev, self.max_allowed):
            self.counts: dict[str, int] = field_logic.clean_counts(prev)
        else:
            self.counts = field_logic.default_field(self.max_allowed)
        self._done = False

        self._formation_sel = ui.Select(
            placeholder="Pick a formation",
            options=_formation_options(self.max_allowed),
            row=0,
        )
        self._formation_sel.callback = self._pick_formation
        self.add_item(self._formation_sel)

        cust_btn = ui.Button(label="Customise", emoji="🧤", style=discord.ButtonStyle.primary, row=1)
        cust_btn.callback = self._open_editor
        self.add_item(cust_btn)

        self._confirm_btn = ui.Button(label="Confirm field", style=discord.ButtonStyle.success, row=1)
        self._confirm_btn.callback = self._confirm_now
        self.add_item(self._confirm_btn)

    # -- state -----------------------------------------------------------
    def total(self) -> int:
        return field_logic.total_count(self.counts)

    def outside(self) -> int:
        return field_logic.outside_count(self.counts)

    def is_valid(self) -> bool:
        return field_logic.counts_valid(self.counts, self.max_allowed)

    def invalid_reason(self) -> str:
        if self.total() != field_logic.PLAYERS_TOTAL:
            return f"You must place all {field_logic.PLAYERS_TOTAL} fielders ({self.total()} placed)."
        return f"Only {self.max_allowed} fielders can be outside the circle in this phase ({self.outside()} now)."

    def _phase(self) -> str:
        return "Powerplay" if self.in_powerplay else "Non-restriction"

    # -- text ------------------------------------------------------------
    def prompt_text(self) -> str:
        if self.kind == "start":
            head = f"🧤 Innings start — set your field for over **{self.over_idx + 1}**"
        else:
            head = f"🧤 Over complete — set your field for over **{self.over_idx + 1}**"
        return (
            f"{head} {self.game.bowling_user.mention}\n"
            f"{self._phase()}: max **{self.max_allowed}** fielders outside the circle, "
            f"all **{field_logic.PLAYERS_TOTAL}** players must be placed. "
            f"*(Only you can see the field.)*"
        )

    def editor_text(self) -> str:
        sel = field_logic.describe_field(self.counts)
        return (
            f"🧤 **Over {self.over_idx + 1} — {self._phase()}**\n"
            f"Placed: **{self.total()} / {field_logic.PLAYERS_TOTAL}** · "
            f"Outside circle: **{self.outside()} / {self.max_allowed}**\n"
            f"Selected: {sel}"
        )

    def _default_positions(self) -> dict[str, int]:
        return field_logic.default_field(self.max_allowed)

    async def field_file(self, counts) -> discord.File | None:
        """Picture of the 11 fielders (None if drawing failed)."""
        try:
            png = await asyncio.to_thread(
                field_png, dict(counts), self.over_idx + 1,
                self._phase(), self.max_allowed,
            )
        except Exception:
            png = None
        return discord.File(io.BytesIO(png), filename="field.png") if png else None

    # -- callbacks -------------------------------------------------------
    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.bowling_user_id:
            await interaction.response.send_message("Only the bowling captain sets the field!", ephemeral=True)
            return False
        if self._done:
            await interaction.response.send_message("The field is already locked in.", ephemeral=True)
            return False
        return True

    async def _pick_formation(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        name = self._formation_sel.values[0]
        self.counts = field_logic.formation_counts(name)
        await interaction.response.send_message(
            f"Formation set: **{name}**. Press **Confirm field**, or **Customise** to change positions.",
            ephemeral=True)

    async def _open_editor(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        editor = _FieldEditorView(self)
        f = await self.field_file(self.counts)
        kwargs = {"file": f} if f else {}
        await interaction.response.send_message(self.editor_text(), view=editor, ephemeral=True, **kwargs)

    async def _confirm_now(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if not self.is_valid():
            await interaction.response.send_message(self.invalid_reason(), ephemeral=True)
            return
        try:
            await interaction.response.defer()
        except Exception:
            pass
        await self._finalize(_ch(interaction, self.game), dict(self.counts))

    # -- finish ----------------------------------------------------------
    async def _finalize(self, channel, counts, *, auto: bool = False):
        """Lock the field in, tidy the prompt, then carry on (next-bowler prompt,
        or the first bowler's delivery prompt at the start of an innings)."""
        if self._done:
            return
        self._done = True
        self.stop()
        self.game.set_field(counts)
        # `set_field` stamps the over index from the ball count; keep it exact.
        self.game.field_over_idx = self.over_idx
        if field_logic.FIELD_VISIBLE_TO_BATTER:
            summary = f"🧤 Field set for over **{self.over_idx + 1}**: {field_logic.describe_field(counts)}"
        else:
            summary = f"🧤 Field set for over **{self.over_idx + 1}** (11 fielders placed)."
        if auto:
            summary += " *(default formation applied)*"
        try:
            if self._message is not None:
                await self._message.edit(content=summary, view=None)
        except Exception:
            pass   # cosmetic only
        if field_logic.FIELD_VISIBLE_TO_BATTER:
            # Visible mode only: post the field picture for everyone.
            async def _show():
                f = await self.field_file(counts)
                if f:
                    await channel.send(file=f)
            await _safe_next(channel, self.game, _show)
        nxt = self._after or (lambda: _send_bowler_prompt_only(channel, self.game))
        await _safe_next(channel, self.game, nxt)

    async def on_timeout(self):
        """Override of _TimeoutMixin.on_timeout: NEVER abandon the match here."""
        if self._done:
            return
        ch, game = self._channel, self.game
        if not ch or active_games.get(ch.id) is not game:
            return
        live = game.__dict__.get("_live_view")
        if live is not None and live is not self:
            return
        await self._finalize(ch, self._default_positions(), auto=True)


# ─────────────────────────────────────────────────────────────────────────────
# Next bowler
# ─────────────────────────────────────────────────────────────────────────────

class NextBowlerView(_ImpactButtonMixin, _TimeoutMixin, ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self._add_dropdown()
        self._add_impact_button(game.bowling_user_id, "bowler")

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
        channel = _ch(interaction, self.game)

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
