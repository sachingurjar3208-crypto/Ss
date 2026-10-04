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

from typing import Awaitable, Callable, Optional

import discord
from discord import ui
from discord.ext import commands

import card_db
import economy
import security
import squad_logic as sl
from card_narratives import ROLES
from security import OwnedView, esc, fmt_wait
from squad_cog import _card_line, _in_match, _is_premium, _need_debut

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

# Custom emojis for the reward messages (developer-portal emojis).
REWARD_EMOJI = {
    "daily":   "<:Daily:1554697281855430666>",
    "weekly":  "<:Weeklypack:1554697293092094065>",
    "monthly": "<:Monthlypack:1554698034800099369>",
}


class EconomyCog(commands.Cog, name="Economy"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- purse -----------------------------------------------------------
    @commands.command(name="csbal")
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def csbal(self, ctx: commands.Context):
        """Show your coins and unopened packs."""
        if not await _need_debut(ctx):
            return
        packs = economy.get_packs(ctx.author.id)
        pack_txt = ", ".join(f"{sl.PACKS[t]['emoji']} {n}× {sl.PACKS[t]['label']}" for t, n in packs.items() if t in sl.PACKS)
        await ctx.send(
            f"💰 Purse: **{economy.fmt_coins(economy.get_balance(ctx.author.id))}**"
            + (f"\n📦 Unopened: {pack_txt}" if pack_txt else "")
        )

    # -- rewards ---------------------------------------------------------
    async def _claim(self, ctx: commands.Context, kind: str):
        if not await _need_debut(ctx):
            return
        if kind == "weekly" and not _is_premium(ctx.author):
            await ctx.send("💎 The weekly reward is for **Premium members** only.")
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
                    f"🔒 The monthly reward unlocks **1 month after your debut**. "
                    f"Come back in **{fmt_wait(res['wait'])}**."
                )
                return
            await ctx.send(f"⏳ You already claimed your {kind} reward. Come back in **{fmt_wait(res['wait'])}**.")
            return
        lines = [f"{REWARD_EMOJI.get(kind, '🎁')} {kind.title()} reward: **+{economy.fmt_coins(res['coins'])}**"]
        if kind == "daily":
            streak = res["streak"]
            if res["bonus"]:
                lines.append(
                    f"🎉 Streak bonus: **+{economy.fmt_coins(res['bonus'])}** "
                    f"(daily {economy.fmt_coins(res['base'])} + streak {economy.fmt_coins(res['bonus'])})"
                )
            nxt = economy.STREAK_EVERY - streak % economy.STREAK_EVERY
            lines.append(
                f"🔥 Streak: **{streak}** day(s) — next streak bonus in **{nxt}** day(s). "
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
        lines.append(f"Balance: **{economy.fmt_coins(res['balance'])}**")
        await ctx.send("\n".join(lines))

    @commands.command(name="csdaily")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csdaily(self, ctx: commands.Context):
        """Claim daily coins + a random 60-78 player (streak bonus every 7th day)."""
        await self._claim(ctx, "daily")

    @commands.command(name="csweekly")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csweekly(self, ctx: commands.Context):
        """(Premium) Claim weekly coins + a random 80-83 player."""
        await self._claim(ctx, "weekly")

    @commands.command(name="csmonthly")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csmonthly(self, ctx: commands.Context):
        """Claim monthly coins + a random 85-88 player."""
        await self._claim(ctx, "monthly")

    # -- packs -----------------------------------------------------------
    @commands.command(name="cspack")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def cspack(self, ctx: commands.Context, pack: Optional[str] = None):
        """See packs, or buy one: `cspack bronze`."""
        if not await _need_debut(ctx):
            return
        if pack is None:
            lines = []
            for key, spec in sl.PACKS.items():
                lines.append(f"{spec['emoji']} **{spec['label']}** — {economy.fmt_coins(spec['price'])} · {spec['cards']} players\n`cspack {key}`")
            embed = discord.Embed(title="📦 Pack Shop", description="\n\n".join(lines), color=discord.Color.gold())
            embed.set_footer(text="Buy with cspack <type>, then open with csopen <type>. Duplicates turn into coins.")
            await ctx.send(embed=embed)
            return
        key = security.clean_input(pack, 12).lower()
        if key not in sl.PACKS:
            await ctx.send("❌ Pick `bronze`, `silver` or `gold`.")
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
    @commands.cooldown(1, 4, commands.BucketType.user)
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
        embed = discord.Embed(title=f"{spec['emoji']} {spec['label']} opened!", description="\n".join(lines), color=discord.Color.gold())
        if result["refund"]:
            embed.set_footer(text=f"Duplicates gave you back {result['refund']:,} coins")
        await ctx.send(embed=embed)

    # -- buy / sell ------------------------------------------------------
    @commands.command(name="csbuy")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csbuy(self, ctx: commands.Context, *, player: str):
        """Buy a player: `csbuy Virat Kohli`."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't buy players during a match.")
            return
        card, err = sl.resolve_any(security.clean_input(player))
        if card is None:
            await ctx.send(f"❌ {err}")
            return
        if economy.owns(ctx.author.id, card["playername_key"]):
            await ctx.send("You already own this player.")
            return
        price = sl.buy_price(card)
        uid, key = ctx.author.id, card["playername_key"]

        async def do_buy() -> str:
            e = economy.buy_card(uid, key, price)
            return f"❌ {e}" if e else f"✅ Bought **{esc(card['playername'])}** for {economy.fmt_coins(price)}!"

        view = ConfirmView(uid, do_buy)
        view.message = await ctx.send(
            f"Buy {_card_line(card)} for **{economy.fmt_coins(price)}**?\n"
            f"Your purse: {economy.fmt_coins(economy.get_balance(uid))}", view=view
        )

    @commands.command(name="cssell")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def cssell(self, ctx: commands.Context, *, player: str):
        """Sell one of your players for coins."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't sell players during a match.")
            return
        card, err = sl.resolve_owned(ctx.author.id, security.clean_input(player))
        if card is None:
            await ctx.send(f"❌ {err}")
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
            return f"❌ {e}" if e else f"✅ Sold **{esc(card['playername'])}** for {economy.fmt_coins(value)}."

        view = ConfirmView(uid, do_sell)
        view.message = await ctx.send(f"Sell {_card_line(card)} for **{economy.fmt_coins(value)}**?", view=view)

    # -- trade -----------------------------------------------------------
    @commands.command(name="cstrade")
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
            title = "💰 Richest teams"
        else:
            scored = []
            for u in users:
                cards = sl.xi_cards(u["user_id"])
                if len(cards) == economy.XI_SIZE:
                    scored.append((sl.average_ovr(cards), u["team_name"]))
            for ovr, name in sorted(scored, reverse=True)[:10]:
                rows.append((name, f"OVR {ovr}"))
            title = "🏆 Strongest XIs"
        if not rows:
            await ctx.send("No teams on the leaderboard yet.")
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'`{i + 1}.`'} **{esc(n)}** — {v}" for i, (n, v) in enumerate(rows)]
        await ctx.send(embed=discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold()))


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
