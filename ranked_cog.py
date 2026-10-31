"""Ranked matchmaking cog.

Commands:
  rcmp ranked   join the ranked queue (2 overs, same-tier opponents only)
  rcrank        your tier / stars / season record     (rcrank @user for someone else)
  rcrankrewards  every tier's tier-up + season-end reward, your progress, season end time
  rcranktop     global top 10 by stars
  rcseason                    current season and when it ends
  rcseasonstart [days]        (owner) start a season now; it ends by itself after 30 days (or `days`)
  rcseasonend                 (owner) end the season now: rewards + DMs to everyone with stars > 0
  rcrankgive @user <n>        (staff) add / remove stars (negative n removes)
  rcrankreset @user | all     (staff / owner) reset stars + record
  rcrankban @user [days]      (staff) ranked ban (default 30 days)
  rcrankunban @user           (staff) lift a ranked ban

The rules and numbers live in ranked.py. The `rcmp` command itself is in squad_cog.py:
it hands "rcmp ranked" over to RankedCog.start_ranked().
"""

from __future__ import annotations

import asyncio
import io
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import ui
from discord.ext import commands, tasks

import card_db
import economy
import player_buffs
import ranked
import rank_image
import rank_rewards_image
import rank_top_image
import squad_logic as sl
from bot_guard import ADMIN_GUILD_ID, OWNER_ID, is_staff
from security import OwnedView, esc
from squad_cog import _in_match, _need_debut


@dataclass
class _Entry:
    uid: int
    member: discord.Member
    channel: discord.abc.Messageable
    guild_id: int
    tier: str
    joined: float = field(default_factory=time.monotonic)
    message: Optional[discord.Message] = None
    task: Optional[asyncio.Task] = None
    anim: Optional[asyncio.Task] = None


# Season-end DMs already tried this run (a failed one is not retried until the bot restarts).
_dm_tried: set[int] = set()

# Everyone currently searching, by user id.
_queue: dict[int, _Entry] = {}


def in_queue(user_id: int) -> bool:
    """squad_cog uses this so a searching player can't also start a casual match."""
    return int(user_id) in _queue


def _search_embed(entry: _Entry, dots: int = 1, text: str | None = None) -> discord.Embed:
    """The 'searching for a player' card (dots + timer move) or, with `text`, a plain status card."""
    embed = discord.Embed(title="RANK UP MODE", color=discord.Color.from_rgb(8, 22, 60))
    if text:
        embed.description = f"{_sep()}\n{text}\n{_sep()}"
        return embed
    secs = int(time.monotonic() - entry.joined)
    embed.description = (
        f"{_sep()}\n"
        f"**Searching for a player{'.' * dots}**\n"
        f"{_sep()}\n\n"
        f"**Rank:**   {entry.tier}\n"
        f"**Match:**  {ranked.RANKED_OVERS} overs\n"
        f"**Time:**   {secs // 60}:{secs % 60:02d}"
    )
    return embed


class _SearchView(OwnedView):
    def __init__(self, cog: "RankedCog", entry: _Entry):
        super().__init__(entry.uid, timeout=ranked.SEARCH_TIMEOUT + 30)
        self.cog, self.entry = cog, entry

    @ui.button(label="Cancel search", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        removed = await self.cog._remove(self.entry)
        self.stop()
        text = "Search cancelled." if removed else "Match is starting."
        try:
            await interaction.response.edit_message(embed=_search_embed(self.entry, text=text), view=None)
        except discord.HTTPException:
            pass


def _sep() -> str:
    return "──────────────────────"


_PERMS_TEXT = ("❌ I can't run ranked in this channel. I need **View Channel, Send Messages, "
               "Embed Links** and **Attach Files** here.")


class RankedCog(commands.Cog, name="Ranked"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()

    async def cog_load(self):
        self.season_loop.start()

    async def cog_unload(self):
        self.season_loop.cancel()
        for e in list(_queue.values()):
            self._stop_tasks(e)
        _queue.clear()

    # ── season end (auto after the season's days, or by rcseasonend) ───────
    async def _deliver_season_dms(self) -> tuple[int, int]:
        """DM every waiting season-end message. A message that was DMed is deleted; one that couldn't be
        sent (DMs closed) stays and is shown the next time the player uses rcrank / rcmp ranked."""
        sent = failed = 0
        for n in await asyncio.to_thread(ranked.list_notices):
            if n["id"] in _dm_tried:
                continue
            _dm_tried.add(n["id"])
            try:
                user = self.bot.get_user(n["user_id"]) or await self.bot.fetch_user(n["user_id"])
                await user.send(n["text"])
                await asyncio.to_thread(ranked.delete_notice, n["id"])
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(1)   # stay well under Discord's DM rate limits
        return sent, failed

    @tasks.loop(minutes=2)
    async def season_loop(self):
        try:
            res = await asyncio.to_thread(ranked.rollover_if_needed)
            if res:
                print(f"[ranked] {res['season']} ended: rewards for {res['paid']} players, stars reset")
            await self._deliver_season_dms()
        except Exception as e:
            print(f"[ranked] season loop failed: {type(e).__name__}: {e}")

    @season_loop.before_loop
    async def _before_season(self):
        await self.bot.wait_until_ready()

    # ── queue helpers ─────────────────────────────────────────────────────
    async def _remove(self, entry: _Entry) -> bool:
        """Take an entry out of the queue. False if it was already taken (match starting)."""
        async with self._lock:
            if _queue.get(entry.uid) is not entry:
                return False
            _queue.pop(entry.uid, None)
        self._stop_tasks(entry)
        return True

    @staticmethod
    def _stop_tasks(entry: _Entry) -> None:
        for t in (entry.task, entry.anim):
            if t and t is not asyncio.current_task():
                t.cancel()

    async def _show_notices(self, ctx_or_channel, user_id: int):
        notes = await asyncio.to_thread(ranked.pop_notices, user_id)
        for text in notes:
            await ctx_or_channel.send(text)
        if notes:
            from economy_cog import send_next_pending
            await send_next_pending(ctx_or_channel, user_id)

    # ── rcmp ranked ───────────────────────────────────────────────────────
    async def start_ranked(self, ctx: commands.Context):
        me = ctx.author
        if ctx.guild is None:
            await ctx.send("❌ Ranked matches can only be played in a server.")
            return
        if not await _need_debut(ctx):
            return
        if not await asyncio.to_thread(ranked.season_active):
            await ctx.send("⏸️ No ranked season is running right now. Wait for the next season to start.")
            return
        left = await asyncio.to_thread(ranked.blacklist_remaining, me.id)
        if left > 0:
            await ctx.send(f"🚫 You are blacklisted from ranked matches. You can play again <t:{int(time.time()) + left}:R>.")
            return
        await self._show_notices(ctx, me.id)
        if me.id in _queue:
            await ctx.send("❌ You are already searching for a player.")
            return
        if _in_match(self.bot, me.id):
            await ctx.send("❌ You are already in a match.")
            return
        problem = await asyncio.to_thread(sl.xi_problem_for_match, me.id, ranked.RANKED_OVERS)
        if problem:
            await ctx.send(f"❌ You {problem}.")
            return

        info = await asyncio.to_thread(ranked.get_player, me.id)
        tier = info["tier"]
        entry = _Entry(uid=me.id, member=me, channel=ctx.channel, guild_id=ctx.guild.id, tier=tier)

        from views import active_games
        partner: Optional[_Entry] = None
        channel_busy = False
        async with self._lock:
            # one ranked search / match per channel
            if ctx.channel.id in active_games or any(e.channel.id == ctx.channel.id for e in _queue.values()):
                channel_busy = True
            else:
                for e in sorted(_queue.values(), key=lambda x: x.joined):
                    if e.uid == me.id or e.tier != tier or (not ranked.CROSS_SERVER and e.guild_id != entry.guild_id):
                        continue
                    if _in_match(self.bot, e.uid):
                        _queue.pop(e.uid, None)   # that player started something else meanwhile
                        continue
                    # alt-farm guard: not the same opponent again within the next 3 matches
                    if await asyncio.to_thread(ranked.recently_faced, me.id, e.uid):
                        continue
                    partner = e
                    break
                if partner is not None:
                    _queue.pop(partner.uid, None)
                else:
                    _queue[me.id] = entry
        if channel_busy:
            await ctx.send("❌ A ranked search or match is already running in this channel. "
                           "Go to another channel (or another server) to search.")
            return

        if partner is not None:
            self._stop_tasks(partner)
            res = await self._start_pvp(partner, entry)
            if res == "ok":
                return
            if res == "first_bad":
                # the waiting player's channel is unusable: cancel THEIR search, the newcomer keeps searching
                await self._edit_search(partner, _PERMS_TEXT + "\nSearch cancelled.")
                async with self._lock:
                    _queue[me.id] = entry
            else:
                # put the waiting player back and tell the newcomer
                async with self._lock:
                    _queue[partner.uid] = partner
                partner.task = asyncio.create_task(self._wait_task(partner))
                partner.anim = asyncio.create_task(self._anim_task(partner))
                if res == "second_bad":
                    await ctx.send(_PERMS_TEXT)
                else:
                    await ctx.send("❌ Couldn't start the match right now. Try again in a moment.")
                return

        entry.message = await ctx.send(embed=_search_embed(entry, 1), view=_SearchView(self, entry))
        entry.task = asyncio.create_task(self._wait_task(entry))
        entry.anim = asyncio.create_task(self._anim_task(entry))

    async def _anim_task(self, entry: _Entry):
        """Moves the dots and the timer so it looks like a live player search."""
        dots = 1
        try:
            while True:
                await asyncio.sleep(3)
                if _queue.get(entry.uid) is not entry or entry.message is None:
                    return
                dots = dots % 3 + 1
                try:
                    await entry.message.edit(embed=_search_embed(entry, dots))
                except discord.NotFound:
                    return
                except discord.HTTPException:
                    pass
        except asyncio.CancelledError:
            return

    # ── waiting for a bot / timeout ───────────────────────────────────────
    async def _wait_task(self, entry: _Entry):
        try:
            await asyncio.sleep(ranked.BOT_WAIT_SECONDS)
            while True:
                if _queue.get(entry.uid) is not entry:
                    return
                if time.monotonic() - entry.joined >= ranked.SEARCH_TIMEOUT:
                    if await self._remove(entry):
                        await self._edit_search(entry, "No player found.\nTry `rcmp ranked` again.")
                    return
                if await self._try_bot(entry):
                    return
                left = ranked.SEARCH_TIMEOUT - (time.monotonic() - entry.joined)
                await asyncio.sleep(max(0.5, min(10, left)))
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[ranked] wait task failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            await self._remove(entry)

    async def _edit_search(self, entry: _Entry, text: str):
        if entry.message is not None:
            try:
                await entry.message.edit(content=None, embed=_search_embed(entry, text=text), view=None)
                return
            except discord.HTTPException:
                pass
        try:
            await entry.channel.send(embed=_search_embed(entry, text=text))
        except discord.HTTPException:
            pass

    async def _try_bot(self, entry: _Entry) -> bool:
        from views import active_games
        if _in_match(self.bot, entry.uid):
            await self._remove(entry)
            return True
        if entry.channel.id in active_games:
            return False   # channel busy: try again in a few seconds
        name = ranked.random_bot_name(avoid=[entry.member.display_name])
        team = await asyncio.to_thread(ranked.build_bot_team, entry.tier, name)
        if team is None:
            return False
        if not await self._remove(entry):
            return True    # a real player grabbed this entry in the meantime
        fake = ranked.FakeMember(name)
        await self._launch(entry.channel, entry.member, fake, entry.tier, bot_team=team,
                           search_msgs=[entry])
        return True

    # ── starting matches ──────────────────────────────────────────────────
    async def _start_pvp(self, first: _Entry, second: _Entry) -> str:
        """first = the player who waited, second = the one who just typed the command.
        Each player plays in their OWN channel (they may be in different servers); the match is
        mirrored into both. Returns "ok", "retry", "first_bad" or "second_bad"."""
        from views import active_games
        from ranked_mirror import MirrorChannel

        def bot_can_post(ch) -> bool:
            try:
                p = ch.permissions_for(ch.guild.me)
                return bool(p.view_channel and p.send_messages and p.embed_links and p.attach_files)
            except Exception:
                return True

        if first.channel.id in active_games or second.channel.id in active_games:
            return "retry"
        if not bot_can_post(first.channel):
            return "first_bad"
        if not bot_can_post(second.channel):
            return "second_bad"
        channels = [first.channel] if first.channel.id == second.channel.id else [first.channel, second.channel]
        mirror = MirrorChannel(channels, members=[first.member, second.member])
        await self._launch(mirror, first.member, second.member, first.tier, search_msgs=[first, second])
        return "ok"

    async def _launch(self, channel, challenger, opponent, tier: str, bot_team: dict | None = None,
                      search_msgs: list | None = None):
        """Create the game, announce it and start the toss. opponent may be a ranked.FakeMember."""
        from views import active_games, autoplay_ids, start_toss
        from game import GameState
        from data import random_match_conditions
        from embeds import build_ranked_match_embed

        game = GameState(channel.id, challenger, opponent, ranked.RANKED_OVERS)
        humans = [m for m in (challenger, opponent) if not getattr(m, "is_fake", False)]
        for m in humans:
            game.teams[m.id] = sl.build_match_team(m.id)
            game.team_pks[m.id] = []
            game.impact_subs_pool[m.id] = sl.build_impact_subs_players(m.id)
            game.impact_used[m.id] = False
        game.ranked = True
        game.ranked_tier = tier
        if bot_team is not None:   # opponent is the bot
            game.teams[opponent.id] = bot_team
            game.team_pks[opponent.id] = []
            game.impact_subs_pool[opponent.id] = []
            game.impact_used[opponent.id] = True
            game.is_bot_match = True
            game.bot_ids = {opponent.id}
            game.bot_skill = ranked.BOT_PROFILES[tier]["skill"]
            autoplay_ids(game).add(opponent.id)   # the existing rcautoplay machinery plays for the bot

        cond = random_match_conditions()
        game.conditions = cond
        game.pitch_type = cond["pitch_type"]
        # a mirrored (cross-server) match is registered under BOTH channel ids
        chans = list(getattr(channel, "channels", None) or [channel])
        ids = {ch.id for ch in chans}
        if len(chans) > 1:
            game.mirror_ids = ids
            game.mirror = channel
        for cid in ids:
            active_games[cid] = game
        try:   # anti-alt: remember who faced whom (bot = 0)
            await asyncio.to_thread(ranked.record_match, challenger.id, opponent.id)
        except Exception as e:
            print(f"[ranked] record_match failed: {type(e).__name__}: {e}")

        # 1) the searching card turns into "Player found" (the waiting player may be in another channel)
        names = {challenger.id: opponent.display_name, opponent.id: challenger.display_name}
        flashed = False
        for e in search_msgs or []:
            opp_name = names.get(e.uid, "")
            text = f"**Player found!**\n\n**{opp_name}**\n**Rank:**  {tier}"
            if e.channel.id not in ids:
                text += f"\n\nGo to {channel.mention}"
            if e.message is not None:
                flashed = True
            await self._edit_search(e, text)
        if flashed:
            await asyncio.sleep(2)

        # 2) the match card: same layout as a normal rcmp challenge, titled RANK UP MODE
        embed = build_ranked_match_embed(challenger, opponent, ranked.RANKED_OVERS, cond,
                                         gif_url=cond.get("gif_url"),
                                         teams=(game.teams.get(challenger.id), game.teams.get(opponent.id)))
        mentions = " ".join(m.mention for m in humans)
        await channel.send(content=mentions, embed=embed)
        await start_toss(channel, game)

    # ── rcrank ────────────────────────────────────────────────────────────
    @commands.command(name="rcrank")
    @commands.cooldown(1, 30, commands.BucketType.user)   # image command: 30 s cooldown
    async def rcrank(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Your ranked card (image): `rcrank` or `rcrank @user`."""
        who = member or ctx.author
        if not await asyncio.to_thread(economy.user_exists, who.id):
            await ctx.send("❌ That player hasn't debuted yet." if member else "❌ You must debut first. Use `rcdebut`.")
            return
        if who.id == ctx.author.id:
            await self._show_notices(ctx, who.id)
        info = await asyncio.to_thread(ranked.get_player, who.id)
        pos = await asyncio.to_thread(ranked.global_position, who.id)
        tier, stars = info["tier"], info["stars"]
        nxt = ranked.next_tier(stars)
        try:
            avatar = await who.display_avatar.replace(size=256, format="png").read()
        except Exception:
            avatar = None
        end_ts = await asyncio.to_thread(ranked.next_reset_ts)
        left_days = max(0, math.ceil((end_ts - time.time()) / 86400)) if end_ts else None
        png = await asyncio.to_thread(
            rank_image.generate_rank_card,
            name=who.display_name, tier=tier, stars=stars,
            wins=info["wins"], losses=info["losses"], played=info["played"], position=pos,
            tier_min=ranked.TIER_BY_NAME[tier]["min"],
            next_name=nxt[0]["name"] if nxt else None,
            next_min=nxt[0]["min"] if nxt else None,
            reset_days=left_days, avatar_bytes=avatar,
        )
        note = None
        if info.get("blacklist_until", 0) > time.time():
            note = f"🚫 Ranked blacklist ends <t:{int(info['blacklist_until'])}:R>"
        await ctx.send(content=note, file=discord.File(io.BytesIO(png), filename="rank.png"))

    @commands.command(name="rcrankrewards")
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def rcrankrewards(self, ctx: commands.Context):
        """Rewards for every tier (Bronze -> Ruby), what you already claimed, and when the season ends."""
        if not await asyncio.to_thread(economy.user_exists, ctx.author.id):
            await ctx.send("❌ You must debut first. Use `rcdebut`.")
            return
        info = await asyncio.to_thread(ranked.reward_summary, ctx.author.id)
        png = await asyncio.to_thread(rank_rewards_image.generate_rewards_card, **info)
        note = None
        if info["season_active"] and info["season_end"]:
            end = int(info["season_end"])
            note = f"⏳ Season ends <t:{end}:F> (<t:{end}:R>)"
        await ctx.send(content=note, file=discord.File(io.BytesIO(png), filename="rank_rewards.png"))

    @staticmethod
    def _top_name(text: str) -> str:
        """Keep only letters the leaderboard font can draw (Latin). Emoji / other scripts would show as boxes."""
        out = "".join(ch for ch in (text or "") if 32 <= ord(ch) < 0x250).strip()
        return out[:24]

    @commands.command(name="rcranktop")
    @commands.cooldown(1, 20, commands.BucketType.user)   # image command
    async def rcranktop(self, ctx: commands.Context):
        """Global top 10 by stars this season (image)."""
        top = await asyncio.to_thread(ranked.top_players, 10)
        if not top:
            await ctx.send("No one has any stars yet this season. Be the first: `rcmp ranked`")
            return

        async def one(p: dict) -> dict:
            uid = p["user_id"]
            user = ctx.guild.get_member(uid) if ctx.guild else None
            if user is None:
                user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await asyncio.wait_for(self.bot.fetch_user(uid), timeout=4)
                except Exception:
                    user = None
            name = self._top_name(getattr(user, "display_name", None) or getattr(user, "name", "")) if user else ""
            if not name and user is not None:
                name = self._top_name(getattr(user, "name", ""))
            avatar = None
            if user is not None:
                try:
                    avatar = await asyncio.wait_for(user.display_avatar.replace(size=128, format="png").read(), timeout=5)
                except Exception:
                    avatar = None
            return {"name": name or f"Player {str(uid)[-4:]}", "tier": p["tier"], "stars": p["stars"],
                    "wins": p["wins"], "losses": p["losses"], "avatar": avatar}

        async with ctx.typing():
            players = await asyncio.gather(*(one(p) for p in top))
            end_ts = await asyncio.to_thread(ranked.next_reset_ts)
            season = f"Season ends {time.strftime('%d %b', time.gmtime(end_ts))}" if end_ts else "No season running"
            png = await asyncio.to_thread(rank_top_image.generate_rank_top, list(players), season)
        await ctx.send(file=discord.File(io.BytesIO(png), filename=rank_top_image.FILENAME))

    # ── seasons ───────────────────────────────────────────────────────────
    @commands.command(name="rcseason")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcseason(self, ctx: commands.Context):
        """Current ranked season and when it ends."""
        info = await asyncio.to_thread(ranked.season_info)
        if not info["active"]:
            await ctx.send("⏸️ No ranked season is running right now.")
            return
        await ctx.send(f"🏆 **{info['name']}** is running.\nStarted <t:{info['start']}:D> · ends <t:{info['end']}:f> (<t:{info['end']}:R>)")

    @commands.command(name="rcseasonstart")
    async def rcseasonstart(self, ctx: commands.Context, days: int = 0):
        """Owner: `rcseasonstart` starts a season now that ends by itself after 30 days (`rcseasonstart 45` = 45 days)."""
        await self.cog_check_staff(ctx)
        if ctx.author.id != OWNER_ID:
            await ctx.send("❌ Only the bot owner can start a season.")
            return
        days = days or ranked.SEASON_DAYS
        if days < 1 or days > 120:
            await ctx.send("❌ Days must be between 1 and 120.")
            return
        r = await asyncio.to_thread(ranked.start_season, ctx.author.id, days)
        if not r["ok"]:
            await ctx.send(f"❌ **{r['name']}** is already running (ends <t:{r['end']}:R>). End it first with `rcseasonend`.")
            return
        await ctx.send(
            f"✅ **{r['name']}** started! Everyone starts at 0 ⭐.\n"
            f"Ends automatically <t:{r['end']}:f> (<t:{r['end']}:R>), after **{r['days']}** days."
        )

    @commands.command(name="rcseasonend")
    async def rcseasonend(self, ctx: commands.Context):
        """Owner: end the season now. Everyone with more than 0 stars gets the reward for their tier by DM."""
        await self.cog_check_staff(ctx)
        if ctx.author.id != OWNER_ID:
            await ctx.send("❌ Only the bot owner can end a season.")
            return
        info = await asyncio.to_thread(ranked.season_info)
        if not info["active"]:
            await ctx.send("ℹ️ No season is running.")
            return
        view = _ConfirmEndSeason(ctx.author.id, self)
        view.message = await ctx.send(
            f"⚠️ **End {info['name']} now?** Everyone with more than 0 ⭐ gets their tier reward by DM, "
            f"then all stars reset to 0. This can't be undone.",
            view=view,
        )


    # ── staff commands (message commands, main server only) ───────────────
    async def cog_check_staff(self, ctx: commands.Context) -> bool:
        if ctx.guild is None or ctx.guild.id != ADMIN_GUILD_ID or not is_staff(ctx.author):
            raise commands.NotOwner("Only the bot owner or an admin can use this.")
        return True

    @commands.command(name="rcrankgive")
    async def rcrankgive(self, ctx: commands.Context, member: discord.Member, amount: int):
        """`rcrankgive @user 5` adds 5 stars, `rcrankgive @user -3` removes 3. No rewards are paid."""
        await self.cog_check_staff(ctx)
        if amount == 0 or abs(amount) > 500:
            await ctx.send("❌ Amount must be between -500 and 500 (not 0). Example: `rcrankgive @user 5`")
            return
        if member.bot or not await asyncio.to_thread(economy.user_exists, member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        r = await asyncio.to_thread(ranked.admin_give_stars, ctx.author.id, member.id, amount)
        await ctx.send(
            f"✅ **{esc(member.display_name)}**: **{r['old']}** → **{r['new']}** ⭐ "
            f"({ranked.tier_emoji(r['tier_new'])} {r['tier_new']})\n"
            f"`{amount:+d}` stars by {esc(ctx.author.display_name)}. No tier rewards were paid.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ── hidden buff / nerf (staff). Replies go to the admin's DM; nothing is shown to players ──
    async def _admin_reply(self, ctx: commands.Context, text: str) -> None:
        try:
            await ctx.message.delete()          # keep the channel clean
        except Exception:
            pass
        try:
            await ctx.author.send(text)
        except Exception:
            await ctx.send("✅ Done. (I couldn't DM you the details, so turn on DMs from this server.)",
                           allowed_mentions=discord.AllowedMentions.none())

    async def _find_card_name(self, query: str) -> str | None:
        card = await asyncio.to_thread(card_db.get_card, query)
        if card is not None:
            return card["playername"]
        hits = await asyncio.to_thread(card_db.find_cards, query, 2)
        return hits[0]["playername"] if len(hits) == 1 else None

    @commands.command(name="rcbuff")
    async def rcbuff(self, ctx: commands.Context, *, arg: str = ""):
        """`rcbuff <player> <+/-percent>` e.g. `rcbuff Virat Kohli +10`. Hidden from players.
        Batter/WK -> batting, Bowler -> bowling, All-rounder -> both."""
        await self.cog_check_staff(ctx)
        parts = arg.strip().rsplit(None, 1)
        usage = "Usage: `rcbuff <player> <+/-percent>`  e.g. `rcbuff Virat Kohli +10` or `rcbuff Virat Kohli -15`"
        if len(parts) != 2:
            await self._admin_reply(ctx, usage)
            return
        query, raw = parts
        try:
            pct = float(raw.replace("%", "").replace("+", ""))
        except ValueError:
            await self._admin_reply(ctx, usage)
            return
        name = await self._find_card_name(query)
        if name is None:
            await self._admin_reply(ctx, f"❌ No single card matches **{query}**.")
            return
        try:
            await asyncio.to_thread(player_buffs.set_buff, ctx.author.id, name, pct)
        except ValueError as e:
            await self._admin_reply(ctx, f"❌ {e}")
            return
        if pct == 0:
            await self._admin_reply(ctx, f"✅ Buff for **{name}** cleared.")
        else:
            await self._admin_reply(ctx, f"✅ **{name}**: `{pct:+g}%` saved. Hidden from players.")

    @commands.command(name="rcbuffoff")
    async def rcbuffoff(self, ctx: commands.Context, *, player: str = ""):
        """`rcbuffoff <player>` removes a hidden buff/nerf."""
        await self.cog_check_staff(ctx)
        if not player.strip():
            await self._admin_reply(ctx, "Usage: `rcbuffoff <player>`")
            return
        name = await self._find_card_name(player.strip())
        if name is None:
            await self._admin_reply(ctx, f"❌ No single card matches **{player.strip()}**.")
            return
        had = await asyncio.to_thread(player_buffs.clear_buff, ctx.author.id, name)
        await self._admin_reply(ctx, f"✅ Buff for **{name}** removed." if had else f"ℹ️ **{name}** had no buff.")

    @commands.command(name="rcbufflist")
    async def rcbufflist(self, ctx: commands.Context):
        """Lists every hidden buff/nerf (sent to your DM)."""
        await self.cog_check_staff(ctx)
        rows = await asyncio.to_thread(player_buffs.list_buffs)
        if not rows:
            await self._admin_reply(ctx, "No hidden buffs/nerfs are set.")
            return
        body = "\n".join(f"`{pct:+g}%`  {key}" for key, pct in rows)
        await self._admin_reply(ctx, f"**Hidden buffs / nerfs ({len(rows)})**\n{body}"[:1900])

    @commands.command(name="rcrankreset")
    async def rcrankreset(self, ctx: commands.Context, member: Optional[discord.Member] = None, *, arg: str = ""):
        """`rcrankreset @user` resets one player. `rcrankreset all` resets everyone (owner only)."""
        await self.cog_check_staff(ctx)
        if member is None:
            if arg.strip().lower() != "all":
                await ctx.send("Usage: `rcrankreset @user`  or  `rcrankreset all` (owner only)")
                return
            if ctx.author.id != OWNER_ID:
                await ctx.send("❌ Only the bot owner can reset everyone.")
                return
            view = _ConfirmResetAll(ctx.author.id)
            view.message = await ctx.send(
                "⚠️ **Reset ranked for EVERY player?** Stars, wins and losses go to 0. This can't be undone.",
                view=view,
            )
            return
        if member.bot or not await asyncio.to_thread(economy.user_exists, member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        old = await asyncio.to_thread(ranked.admin_reset_player, ctx.author.id, member.id)
        await ctx.send(
            f"✅ **{esc(member.display_name)}** ranked reset: was **{old['stars']}** ⭐, "
            f"{old['wins']}W/{old['losses']}L → now **0** ⭐, 0W/0L.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="rcrankban")
    async def rcrankban(self, ctx: commands.Context, member: discord.Member, days: int = 30, *, reason: str = ""):
        """`rcrankban @user [days] [reason]` bans from ranked (default 30 days)."""
        await self.cog_check_staff(ctx)
        if member.bot or member.id == OWNER_ID or member.id == ctx.author.id:
            await ctx.send("❌ Can't ban that user from ranked.")
            return
        if days < 1 or days > 365:
            await ctx.send("❌ Days must be between 1 and 365.")
            return
        if not await asyncio.to_thread(economy.user_exists, member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        until = await asyncio.to_thread(ranked.admin_ban, ctx.author.id, member.id, days, reason[:100])
        await ctx.send(
            f"🚫 **{esc(member.display_name)}** is banned from ranked until <t:{until}:f> (<t:{until}:R>).",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="rcrankunban")
    async def rcrankunban(self, ctx: commands.Context, member: discord.Member):
        """`rcrankunban @user` lifts a ranked ban."""
        await self.cog_check_staff(ctx)
        had = await asyncio.to_thread(ranked.admin_unban, ctx.author.id, member.id)
        if had:
            await ctx.send(f"✅ **{esc(member.display_name)}** can play ranked again.",
                           allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send(f"ℹ️ **{esc(member.display_name)}** had no ranked ban.",
                           allowed_mentions=discord.AllowedMentions.none())


class _ConfirmEndSeason(OwnedView):
    def __init__(self, owner_id: int, cog: "RankedCog"):
        super().__init__(owner_id, timeout=30)
        self.cog = cog

    @ui.button(label="End season now", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="⏳ Ending the season and sending rewards...", view=None)
        self.stop()
        res = await asyncio.to_thread(ranked.end_season_now, interaction.user.id)
        if res is None:
            await interaction.edit_original_response(content="ℹ️ No season is running.")
            return
        sent, failed = await self.cog._deliver_season_dms()
        await interaction.edit_original_response(
            content=(f"✅ **{res['season']}** ended. Rewards paid to **{res['paid']}** player(s): "
                     f"**{sent}** DM(s) sent, **{failed}** could not be DMed "
                     f"(they will see it next time they use `rcrank`). Stars are reset; start the next one with `rcseasonstart`.")
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


class _ConfirmResetAll(OwnedView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id, timeout=30)

    @ui.button(label="Confirm reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        n = await asyncio.to_thread(ranked.admin_reset_all, interaction.user.id)
        self.stop()
        await interaction.response.edit_message(content=f"✅ Ranked reset for **{n}** player(s).", view=None)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)



async def setup(bot: commands.Bot):
    await bot.add_cog(RankedCog(bot))
