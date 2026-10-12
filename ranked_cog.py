"""Ranked matchmaking cog.

Commands:
  csmp ranked   join the ranked queue (5 overs, same-tier opponents only)
  csrank        your tier / stars / season record     (csrank @user for someone else)
  csranktop     global top 10 by stars

The rules and numbers live in ranked.py. The `csmp` command itself is in squad_cog.py:
it hands "csmp ranked" over to RankedCog.start_ranked().
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import ui
from discord.ext import commands, tasks

import economy
import ranked
import squad_logic as sl
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

    # ── monthly reset ─────────────────────────────────────────────────────
    @tasks.loop(minutes=5)
    async def season_loop(self):
        try:
            res = await asyncio.to_thread(ranked.rollover_if_needed)
            if res:
                print(f"[ranked] season {res['season']} ended: rewards sent to {res['paid']} players, stars reset")
        except Exception as e:
            print(f"[ranked] season rollover failed: {type(e).__name__}: {e}")

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

    # ── csmp ranked ───────────────────────────────────────────────────────
    async def start_ranked(self, ctx: commands.Context):
        me = ctx.author
        if ctx.guild is None:
            await ctx.send("❌ Ranked matches can only be played in a server.")
            return
        if not await _need_debut(ctx):
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

        partner: Optional[_Entry] = None
        async with self._lock:
            for e in sorted(_queue.values(), key=lambda x: x.joined):
                if e.uid == me.id or e.tier != tier or e.guild_id != entry.guild_id:
                    continue
                if _in_match(self.bot, e.uid):
                    _queue.pop(e.uid, None)   # that player started something else meanwhile
                    continue
                partner = e
                break
            if partner is not None:
                _queue.pop(partner.uid, None)
            else:
                _queue[me.id] = entry

        if partner is not None:
            self._stop_tasks(partner)
            ok = await self._start_pvp(partner, entry, ctx.channel)
            if not ok:   # no free channel: put the waiting player back, tell the new one
                async with self._lock:
                    _queue[partner.uid] = partner
                partner.task = asyncio.create_task(self._wait_task(partner))
                partner.anim = asyncio.create_task(self._anim_task(partner))
                await ctx.send("❌ No free channel to start the match right now. Try again in a moment.")
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
                        await self._edit_search(entry, "No player found.\nTry `csmp ranked` again.")
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
    async def _start_pvp(self, first: _Entry, second: _Entry, new_channel) -> bool:
        """first = the player who waited, second = the one who just typed the command."""
        from views import active_games

        def can_use(ch, member) -> bool:
            try:
                p = ch.permissions_for(member)
                return p.view_channel and p.send_messages
            except Exception:
                return True

        channel = None
        for ch in (first.channel, new_channel):
            if ch.id in active_games:
                continue
            if can_use(ch, first.member) and can_use(ch, second.member):
                channel = ch
                break
        if channel is None:
            return False
        await self._launch(channel, first.member, second.member, first.tier, search_msgs=[first, second])
        return True

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
            autoplay_ids(game).add(opponent.id)   # the existing csautoplay machinery plays for the bot

        cond = random_match_conditions()
        game.conditions = cond
        game.pitch_type = cond["pitch_type"]
        active_games[channel.id] = game

        # 1) the searching card turns into "Player found" (the waiting player may be in another channel)
        names = {challenger.id: opponent.display_name, opponent.id: challenger.display_name}
        flashed = False
        for e in search_msgs or []:
            opp_name = names.get(e.uid, "")
            text = f"**Player found!**\n\n**{opp_name}**\n**Rank:**  {tier}"
            if e.channel.id != channel.id:
                text += f"\n\nGo to {channel.mention}"
            if e.message is not None:
                flashed = True
            await self._edit_search(e, text)
        if flashed:
            await asyncio.sleep(2)

        # 2) the match card: same layout as a normal csmp challenge, titled RANK UP MODE
        embed = build_ranked_match_embed(challenger, opponent, ranked.RANKED_OVERS, cond,
                                         gif_url=cond.get("gif_url"))
        mentions = " ".join(m.mention for m in humans)
        await channel.send(content=mentions, embed=embed)
        await start_toss(channel, game)

    # ── csrank ────────────────────────────────────────────────────────────
    @commands.command(name="csrank")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csrank(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Your ranked tier and stars: `csrank` or `csrank @user`."""
        who = member or ctx.author
        if not await asyncio.to_thread(economy.user_exists, who.id):
            await ctx.send("❌ That player hasn't debuted yet." if member else "❌ You must debut first. Use `csdebut`.")
            return
        if who.id == ctx.author.id:
            await self._show_notices(ctx, who.id)
        info = await asyncio.to_thread(ranked.get_player, who.id)
        pos = await asyncio.to_thread(ranked.global_position, who.id)
        tier, stars = info["tier"], info["stars"]
        t = ranked.TIER_BY_NAME[tier]
        nxt = ranked.next_tier(stars)
        embed = discord.Embed(
            title=f"{t['emoji']} {tier}  •  {stars} ⭐",
            description=f"**{esc(who.display_name)}**",
            color=discord.Color.from_rgb(212, 175, 55),
        )
        if nxt:
            nt, need = nxt
            embed.add_field(name="Next tier", value=f"{nt['emoji']} **{nt['name']}** in **{need}** ⭐", inline=False)
        else:
            embed.add_field(name="Top tier", value="You are **Ruby**. Keep collecting stars for the global top!", inline=False)
        embed.add_field(name="This season",
                        value=f"{info['wins']}W – {info['losses']}L  ({info['played']} played)", inline=True)
        if pos:
            embed.add_field(name="Global place", value=f"#{pos}", inline=True)
        embed.add_field(
            name="Tiers",
            value="\n".join(f"{x['emoji']} {x['name']} — {x['min']} ⭐" for x in ranked.TIERS),
            inline=False,
        )
        embed.set_footer(text="Season resets monthly • Play with `csmp ranked`")
        embed.description += f"\nSeason resets <t:{ranked.next_reset_ts()}:R>"
        await ctx.send(embed=embed)

    @commands.command(name="csranktop")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def csranktop(self, ctx: commands.Context):
        """Global top 10 by stars this season."""
        top = await asyncio.to_thread(ranked.top_players, 10)
        if not top:
            await ctx.send("No one has any stars yet this season. Be the first: `csmp ranked`")
            return
        lines = []
        for i, p in enumerate(top, 1):
            m = ctx.guild.get_member(p["user_id"]) if ctx.guild else None
            name = esc(m.display_name) if m else f"<@{p['user_id']}>"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i}.`")
            lines.append(f"{medal} {ranked.tier_emoji(p['tier'])} **{name}** — **{p['stars']}** ⭐  ({p['wins']}W/{p['losses']}L)")
        embed = discord.Embed(title="🏆 Global Ranked Top 10", description="\n".join(lines),
                              color=discord.Color.from_rgb(212, 175, 55))
        embed.set_footer(text=f"Resets {time.strftime('%d %b', time.gmtime(ranked.next_reset_ts()))} (monthly)")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(RankedCog(bot))
