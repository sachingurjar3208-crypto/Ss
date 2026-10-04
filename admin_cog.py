"""Admin-only commands, gated by a single role.

    admin card give @user <card name>
    admin coin give @user <coins>
    admin coin remove @user <coins>
    admin player reset @user all
"""
from __future__ import annotations

import discord
from discord.ext import commands

import card_db
import economy
import security
import squad_logic as sl
from security import esc

ADMIN_ROLE_ID = 1556267277249155156


def _has_admin_role(ctx: commands.Context) -> bool:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return False
    return any(r.id == ADMIN_ROLE_ID for r in ctx.author.roles)


def admin_only():
    return commands.check(_has_admin_role)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.group(name="admin", invoke_without_command=True)
    @admin_only()
    async def admin(self, ctx: commands.Context):
        await ctx.send(
            "`admin card give @user <card name>`\n"
            "`admin coin give @user <coins>`\n"
            "`admin coin remove @user <coins>`\n"
            "`admin player reset @user all`"
        )

    # -- cards -----------------------------------------------------------
    @admin.group(name="card", invoke_without_command=True)
    @admin_only()
    async def card(self, ctx: commands.Context):
        await ctx.send("Usage: `admin card give @user <card name>`")

    @card.command(name="give")
    @admin_only()
    async def card_give(self, ctx: commands.Context, member: discord.Member, *, player: str):
        if member.bot:
            await ctx.send("❌ Bots can't own players.")
            return
        found, err = sl.resolve_any(security.clean_input(player))
        if found is None:
            await ctx.send(f"❌ {err}")
            return
        if economy.give_card(member.id, found["playername_key"]):
            await ctx.send(f"✅ Gave **{esc(found['playername'])}** to {esc(member.display_name)}.")
        else:
            await ctx.send(
                f"❌ Couldn't give **{esc(found['playername'])}**. "
                "They haven't debuted yet, or already own this player."
            )

    # -- coins -----------------------------------------------------------
    @admin.group(name="coin", invoke_without_command=True)
    @admin_only()
    async def coin(self, ctx: commands.Context):
        await ctx.send("Usage: `admin coin give @user <coins>` or `admin coin remove @user <coins>`")

    @coin.command(name="give")
    @admin_only()
    async def coin_give(self, ctx: commands.Context, member: discord.Member, amount: int):
        await self._adjust(ctx, member, amount)

    @coin.command(name="remove")
    @admin_only()
    async def coin_remove(self, ctx: commands.Context, member: discord.Member, amount: int):
        await self._adjust(ctx, member, -abs(amount))

    async def _adjust(self, ctx: commands.Context, member: discord.Member, amount: int):
        if amount == 0:
            await ctx.send("❌ Amount can't be zero.")
            return
        if not -1_000_000_000 <= amount <= 1_000_000_000:
            await ctx.send("❌ Amount too large.")
            return
        if not economy.user_exists(member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        bal = economy.add_coins(member.id, amount, f"Admin adjustment by {ctx.author.id}")
        verb = "received" if amount > 0 else "lost"
        await ctx.send(
            f"✅ {esc(member.display_name)} {verb} **{economy.fmt_coins(abs(amount))}**. "
            f"Balance: **{economy.fmt_coins(bal)}**."
        )

    # -- players ---------------------------------------------------------
    @admin.group(name="player", invoke_without_command=True)
    @admin_only()
    async def player(self, ctx: commands.Context):
        await ctx.send("Usage: `admin player reset @user all`")

    @player.command(name="reset")
    @admin_only()
    async def player_reset(self, ctx: commands.Context, member: discord.Member, scope: str):
        if scope.lower() != "all":
            await ctx.send("Usage: `admin player reset @user all`")
            return
        if not economy.user_exists(member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        removed = economy.reset_all_cards(member.id)
        await ctx.send(
            f"✅ Removed **{removed}** card(s) from {esc(member.display_name)}. "
            "Their XI and captain were cleared too."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
