"""Economy commands: csbal, csdaily, csweekly, csmonthly, cspack, csopen,
csbuy, cssell, cstrade, csleaderboard.

Safety notes
- Anything that spends coins or gives a player away asks for a button
  confirmation first, and only the person it was made for can press it.
- The real check (enough coins? do you still own it?) happens again inside
  economy.py's single database transaction when the button is pressed, so
  spamming buttons or commands can never double-spend or copy a card.
- Squads are locked while a player is in a live match.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional

import discord
from discord import ui
from discord.ext import commands

import card_db
import economy
import security
import squad_logic as sl
import card_cache
import emojis
from card_narratives import ROLES
from security import OwnedView, esc, fmt_wait
from squad_cog import _card_line, _in_match, _is_premium, _need_debut

# ── One shared 5 second cooldown for ALL economy commands ────────────────────
# Each command used to have its own timer, so spamming csbal/csopen/csbuy one
# after another still worked. Now every economy command shares ONE timer per
# user: after any of them, the user must wait ECONOMY_COOLDOWN seconds.
ECONOMY_COOLDOWN = 5
_econ_bucket = commands.CooldownMapping.from_cooldown(1, ECONOMY_COOLDOWN, commands.BucketType.user)


def economy_cooldown():
    async def predicate(ctx: commands.Context) -> bool:
        bucket = _econ_bucket.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit() if bucket else None
        if retry_after:
            # security.py's error handler shows "Wait Xs before using this again".
            raise commands.CommandOnCooldown(bucket, retry_after, commands.BucketType.user)
        return True
    return commands.check(predicate)


# Players with a trade waiting for an answer (each can only have one at a time).
_pending_trade: set[int] = set()


# ── A reusable "are you sure?" button pair ───────────────────────────────────

class ConfirmView(OwnedView):
    def __init__(self, user_id: int, action: Callable[[], Awaitable[str]], timeout: float = 45.0):
        super().__init__(user_id, timeout=timeout)
        self.action = action
        self.used = False

    @ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:  # a second click can never run the action twice
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        result = await self.action()
        await interaction.response.edit_message(content=result, embed=None, view=None)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self.used = True
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


# ── csbuy / cssell: card image + two emoji buttons ───────────────────────────
# The message shows the card image with a green check and a red cross under it.
# After BUYSELL_TIMEOUT seconds both buttons switch off (greyed out), so
# pressing them later does nothing.
BUYSELL_TIMEOUT = 20.0


def _plain_coins(n: int) -> str:
    return emojis.coins(n)


def _buysell_embed(card, text: str) -> discord.Embed:
    embed = discord.Embed(description=text, color=discord.Color.gold())
    path = card["image_path"]
    if path and Path(path).exists():
        embed.set_image(url=card_cache.image_url(path))
    return embed


async def _send_buysell(ctx: commands.Context, card, text: str, action: Callable[[], Awaitable[str]]):
    """Send the card image + text with the check / cross buttons."""
    import time as _t
    view = BuySellView(ctx.author.id, card, action)
    t0 = _t.perf_counter()
    embed = _buysell_embed(card, text)
    path = card["image_path"]
    url = card_cache.cached_url(path)
    file = None if url else await card_cache.card_file(path)   # cached link = no upload
    if not url and file is None:
        embed = discord.Embed(description=text, color=discord.Color.gold())
    t1 = _t.perf_counter()
    if file:
        view.message = await ctx.send(embed=embed, file=file, view=view)
        card_cache.remember(path, view.message)
    else:
        view.message = await ctx.send(embed=embed, view=view)
    t2 = _t.perf_counter()
    how = "cdn-link" if url else ("upload" if file else "no-image")
    print(f"[timing buy/sell] image={t1 - t0:.2f}s  discord_send={t2 - t1:.2f}s  via={how}")


class BuySellView(OwnedView):
    def __init__(self, user_id: int, card, action: Callable[[], Awaitable[str]]):
        super().__init__(user_id, timeout=BUYSELL_TIMEOUT)
        self.card = card
        self.action = action
        self.used = False

    @ui.button(emoji="✅", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:  # a second click can never run the action twice
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        result = await self.action()
        await interaction.response.edit_message(embed=_buysell_embed(self.card, result), view=None)

    @ui.button(emoji="❌", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        await interaction.response.edit_message(embed=_buysell_embed(self.card, "Cancelled."), view=None)


# ── Trade offer buttons ──────────────────────────────────────────────────────

class TradeView(OwnedView):
    def __init__(self, proposer: discord.Member, target: discord.Member, give_key: str, get_key: str):
        super().__init__({proposer.id, target.id}, timeout=60)
        self.proposer, self.target = proposer, target
        self.give_key, self.get_key = give_key, get_key
        self.done = False

    def _finish(self):
        self.done = True
        _pending_trade.discard(self.proposer.id)
        _pending_trade.discard(self.target.id)
        self.stop()

    async def on_timeout(self) -> None:
        _pending_trade.discard(self.proposer.id)
        _pending_trade.discard(self.target.id)
        await super().on_timeout()

    @ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="🤝")
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("Only the player who received the offer can accept.", ephemeral=True)
            return
        if self.done:
            await interaction.response.defer()
            return
        self._finish()
        if _in_match(interaction.client, self.proposer.id) or _in_match(interaction.client, self.target.id):
            await interaction.response.edit_message(content="❌ Trade cancelled — a player is in a match.", embed=None, view=None)
            return
        err = economy.execute_trade(self.proposer.id, self.give_key, self.target.id, self.get_key)
        if err:
            await interaction.response.edit_message(content=f"❌ Trade failed: {err}", embed=None, view=None)
            return
        gave, got = card_db.get_card(self.give_key), card_db.get_card(self.get_key)
        await interaction.response.edit_message(
            content=(
                f"🤝 Trade complete! {esc(self.proposer.display_name)} gave **{esc(gave['playername'])}** "
                f"and received **{esc(got['playername'])}** from {esc(self.target.display_name)}."
            ),
            embed=None, view=None,
        )

    @ui.button(label="Decline / Cancel", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if self.done:
            await interaction.response.defer()
            return
        self._finish()
        who = "declined" if interaction.user.id == self.target.id else "cancelled"
        await interaction.response.edit_message(content=f"❌ Trade {who}.", embed=None, view=None)


# ── The cog ──────────────────────────────────────────────────────────────────

# Custom emojis for the reward messages live in emojis.py.


class EconomyCog(commands.Cog, name="Economy"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- purse -----------------------------------------------------------
    @commands.command(name="csbal")
    @economy_cooldown()
    async def csbal(self, ctx: commands.Context):
        """Show your coins and unopened packs."""
        if not await _need_debut(ctx):
            return
        packs = economy.get_packs(ctx.author.id)
        pack_txt = ", ".join(f"{sl.PACKS[t]['emoji']} {n}× {sl.PACKS[t]['label']}" for t, n in packs.items() if t in sl.PACKS)
        await ctx.send(
            f"{emojis.COIN} **Purse:** {economy.fmt_coins(economy.get_balance(ctx.author.id))}"
            + (f"\n📦 Unopened: {pack_txt}" if pack_txt else "")
        )

    # -- rewards ---------------------------------------------------------
    async def _claim(self, ctx: commands.Context, kind: str):
        if not await _need_debut(ctx):
            return
        if kind == "weekly" and not _is_premium(ctx.author):
            await ctx.send(f"{emojis.WEEKLY} The **Weekly** reward is for **Premium members** only.")
            return
        lo, hi = economy.REWARD_CARD_OVR[kind]
        pool = [c for c in card_db.list_all_cards() if lo <= int(c["ovr"]) <= hi]
        res = economy.claim_reward(
            ctx.author.id, kind,
            [c["playername_key"] for c in pool],
            {c["playername_key"]: sl.sell_value(c) for c in pool},
        )
        if not res["ok"]:
            if res.get("locked"):
                await ctx.send(
                    f"{emojis.MONTHLY} The **Monthly** reward unlocks **1 month after your debut**. "
                    f"Come back in **{fmt_wait(res['wait'])}**."
                )
                return
            await ctx.send(f"⏳ You already claimed your {emojis.reward_label(kind)} reward. Come back in **{fmt_wait(res['wait'])}**.")
            return
        lines = [f"{emojis.reward_label(kind)} reward: **+{economy.fmt_coins(res['coins'])}**"]
        if kind == "daily":
            streak = res["streak"]
            if res["bonus"]:
                lines.append(
                    f"{emojis.STREAK} **Streak bonus:** +{economy.fmt_coins(res['bonus'])} "
                    f"({emojis.DAILY} Daily {economy.fmt_coins(res['base'])} + {emojis.STREAK} Streak {economy.fmt_coins(res['bonus'])})"
                )
            nxt = economy.STREAK_EVERY - streak % economy.STREAK_EVERY
            lines.append(
                f"{emojis.STREAK} **Streak:** {streak} day(s) — next streak bonus in **{nxt}** day(s). "
                f"Miss a day and it restarts from 1!"
            )
        if res["card"]:
            card = card_db.get_card(res["card"])
            if res["dupe"]:
                lines.append(f"🎴 {_card_line(card, '  ♻️ duplicate')} → **+{economy.fmt_coins(res['refund'])}**")
            else:
                lines.append(f"🎴 New player: {_card_line(card, '  🆕')}")
        else:
            lines.append(f"🎴 No player cards with rating {lo}-{hi} are available yet.")
        lines.append(f"{emojis.COIN} **Balance:** {economy.fmt_coins(res['balance'])}")
        await ctx.send("\n".join(lines))

    @commands.command(name="csdaily")
    @economy_cooldown()
    async def csdaily(self, ctx: commands.Context):
        """Claim daily coins + a random 60-78 player (streak bonus every 7th day)."""
        await self._claim(ctx, "daily")

    @commands.command(name="csweekly")
    @economy_cooldown()
    async def csweekly(self, ctx: commands.Context):
        """(Premium) Claim weekly coins + a random 80-83 player."""
        await self._claim(ctx, "weekly")

    @commands.command(name="csmonthly")
    @economy_cooldown()
    async def csmonthly(self, ctx: commands.Context):
        """Claim monthly coins + a random 85-88 player."""
        await self._claim(ctx, "monthly")

    # -- packs -----------------------------------------------------------
    @commands.command(name="cspack")
    @economy_cooldown()
    async def cspack(self, ctx: commands.Context, pack: Optional[str] = None):
        """See packs, or buy one: `cspack bronze`."""
        if not await _need_debut(ctx):
            return
        if pack is None:
            lines = []
            for key, spec in sl.PACKS.items():
                lines.append(f"{spec['emoji']} **{spec['label']}** — {economy.fmt_coins(spec['price'])} · 1 player ({emojis.OVR} {spec['range'][0]}-{spec['range'][1]})\n`cspack {key}`")
            embed = discord.Embed(title="📦 Pack Shop", description="\n\n".join(lines), color=discord.Color.gold())
            embed.set_footer(text="Buy with cspack <type>, then open with csopen <type>. Duplicates turn into coins.")
            await ctx.send(embed=embed)
            return
        key = security.clean_input(pack, 12).lower()
        if key not in sl.PACKS:
            await ctx.send("❌ Pick " + ", ".join(f"`{k}`" for k in sl.PACKS) + ".")
            return
        spec = sl.PACKS[key]
        uid = ctx.author.id

        async def do_buy() -> str:
            err = economy.buy_pack(uid, key, spec["price"])
            if err:
                return f"❌ {err}"
            return (
                f"✅ Bought 1× {spec['emoji']} {spec['label']} for {economy.fmt_coins(spec['price'])}. "
                f"Open it with `csopen {key}`."
            )

        view = ConfirmView(uid, do_buy)
        view.message = await ctx.send(
            f"Buy 1× {spec['emoji']} **{spec['label']}** for **{economy.fmt_coins(spec['price'])}**?", view=view
        )

    @commands.command(name="csopen")
    @economy_cooldown()
    async def csopen(self, ctx: commands.Context, pack: Optional[str] = None):
        """Open one of your packs: `csopen bronze`."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't open packs during a match.")
            return
        have = economy.get_packs(ctx.author.id)
        have = {k: v for k, v in have.items() if k in sl.PACKS}
        if not have:
            await ctx.send("You have no packs. Buy them with `cspack`.")
            return
        if pack is None:
            if len(have) == 1:
                pack = next(iter(have))
            else:
                await ctx.send("You have: " + ", ".join(f"{n}× {k}" for k, n in have.items()) + ". Use `csopen <type>`.")
                return
        key = security.clean_input(pack, 12).lower()
        if key not in have:
            await ctx.send("❌ You don't have that pack.")
            return
        rolled = sl.roll_pack(key)
        if not rolled:
            await ctx.send("⚠️ There are no player cards in the game yet, so packs can't open. Ask the owner to add cards.")
            return
        result = economy.open_pack(
            ctx.author.id, key,
            [c["playername_key"] for c in rolled],
            {c["playername_key"]: sl.sell_value(c) for c in rolled},
        )
        if isinstance(result, str):
            await ctx.send(f"❌ {result}")
            return
        by_key = {c["playername_key"]: c for c in rolled}
        lines = [_card_line(by_key[k], "  🆕") for k in result["new"]]
        lines += [_card_line(by_key[k], "  ♻️ duplicate") for k in result["dupes"]]
        spec = sl.PACKS[key]
        if result["refund"]:
            lines.append(f"\n♻️ Duplicates gave you back {economy.fmt_coins(result['refund'])}")
        embed = discord.Embed(title=f"{spec['emoji']} {spec['label']} opened!", description="\n".join(lines), color=discord.Color.gold())
        await ctx.send(embed=embed)

    # -- buy / sell ------------------------------------------------------
    @commands.command(name="csbuy")
    @economy_cooldown()
    async def csbuy(self, ctx: commands.Context, *, player: str):
        """Buy a player: `csbuy Virat Kohli`."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("You can't buy players during a match.")
            return
        card, err = await asyncio.to_thread(sl.resolve_any, security.clean_input(player))
        if card is None:
            await ctx.send(str(err))
            return
        if economy.owns(ctx.author.id, card["playername_key"]):
            await ctx.send("You already own this player.")
            return
        price = sl.buy_price(card)
        uid, key = ctx.author.id, card["playername_key"]

        async def do_buy() -> str:
            e = economy.buy_card(uid, key, price)
            return str(e) if e else f"Bought {esc(card['playername'])} for {_plain_coins(price)}."

        await _send_buysell(
            ctx, card,
            f"Buy {esc(card['playername'])} ({emojis.ovr(card['ovr'])}) for {_plain_coins(price)}?\n"
            f"{emojis.COIN} Your coins: {_plain_coins(economy.get_balance(uid))}",
            do_buy,
        )

    @commands.command(name="cssell")
    @economy_cooldown()
    async def cssell(self, ctx: commands.Context, *, player: str):
        """Sell one of your players for coins."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("You can't sell players during a match.")
            return
        card, err = await asyncio.to_thread(sl.resolve_owned, ctx.author.id, security.clean_input(player))
        if card is None:
            await ctx.send(str(err))
            return
        uid, key = ctx.author.id, card["playername_key"]
        value = sl.sell_value(card)

        async def do_sell() -> str:
            # If this player is in the XI, the best bench player takes their place.
            xi_keys = set(economy.get_xi(uid).values())
            replacement = None
            if key in xi_keys:
                bench = [c for c in sl.owned_cards(uid) if c["playername_key"] not in xi_keys]
                if bench:
                    replacement = max(bench, key=lambda c: c["ovr"])["playername_key"]
            e = economy.sell_card(uid, key, value, replacement)
            return str(e) if e else f"Sold {esc(card['playername'])} for {_plain_coins(value)}."

        await _send_buysell(
            ctx, card,
            f"Sell {esc(card['playername'])} ({emojis.ovr(card['ovr'])}) for {_plain_coins(value)}?",
            do_sell,
        )

    # -- trade -----------------------------------------------------------
    @commands.command(name="cstrade")
    @economy_cooldown()
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def cstrade(self, ctx: commands.Context, member: discord.Member, *, players: str):
        """Trade players: `cstrade @user my player | their player`."""
        if not await _need_debut(ctx):
            return
        if member.bot or member.id == ctx.author.id:
            await ctx.send("❌ Pick another real player to trade with.")
            return
        if security.is_banned(member.id):
            await ctx.send("❌ You can't trade with that user.")
            return
        if not economy.user_exists(member.id):
            await ctx.send(f"❌ {esc(member.display_name)} hasn't debuted yet.")
            return
        if ctx.author.id in _pending_trade or member.id in _pending_trade:
            await ctx.send("❌ One of you already has a trade waiting. Finish that first.")
            return
        if _in_match(self.bot, ctx.author.id) or _in_match(self.bot, member.id):
            await ctx.send("❌ You can't trade while someone is in a match.")
            return
        players = security.clean_input(players, 120)
        if players.count("|") != 1:
            await ctx.send("Usage: `cstrade @user Your Player | Their Player`")
            return
        mine_txt, theirs_txt = (p.strip() for p in players.split("|"))
        mine, err = sl.resolve_owned(ctx.author.id, mine_txt)
        if mine is None:
            await ctx.send(f"❌ {err}")
            return
        theirs, err = sl.resolve_owned(member.id, theirs_txt)
        if theirs is None:
            await ctx.send(f"❌ {esc(member.display_name)} doesn't own a player like that.")
            return
        if economy.owns(member.id, mine["playername_key"]) or economy.owns(ctx.author.id, theirs["playername_key"]):
            await ctx.send("❌ One of you already owns the player you'd receive.")
            return
        _pending_trade.update({ctx.author.id, member.id})
        view = TradeView(ctx.author, member, mine["playername_key"], theirs["playername_key"])
        embed = discord.Embed(
            title="🤝 Trade offer",
            description=(
                f"**{esc(ctx.author.display_name)}** gives:\n{_card_line(mine)}\n\n"
                f"**{esc(member.display_name)}** gives:\n{_card_line(theirs)}"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"{member.display_name}: press Accept within 60 seconds")
        view.message = await ctx.send(content=member.mention, embed=embed, view=view)

    # -- leaderboard -----------------------------------------------------
    @commands.command(name="csleaderboard", aliases=["csleaderboards", "cslb"])
    @commands.cooldown(1, 8, commands.BucketType.user)
    async def csleaderboard(self, ctx: commands.Context, board: str = "ovr"):
        """Top teams: `csleaderboard ovr` or `csleaderboard coins`."""
        board = security.clean_input(board, 10).lower()
        if board not in ("ovr", "coins"):
            await ctx.send("Use `csleaderboard ovr` or `csleaderboard coins`.")
            return
        users = [u for u in economy.list_users() if not security.is_banned(u["user_id"])]
        rows = []
        if board == "coins":
            for u in sorted(users, key=lambda u: -u["purse"])[:10]:
                rows.append((u["team_name"], economy.fmt_coins(u["purse"])))
            title = f"{emojis.COIN} Richest teams"
        else:
            scored = []
            for u in users:
                cards = sl.xi_cards(u["user_id"])
                if len(cards) == economy.XI_SIZE:
                    scored.append((sl.average_ovr(cards), u["team_name"]))
            for ovr, name in sorted(scored, reverse=True)[:10]:
                rows.append((name, emojis.ovr(ovr)))
            title = "🏆 Strongest XIs"
        if not rows:
            await ctx.send("No teams on the leaderboard yet.")
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'`{i + 1}.`'} **{esc(n)}** — {v}" for i, (n, v) in enumerate(rows)]
        await ctx.send(embed=discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold()))


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
