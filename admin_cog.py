"""Admin-only commands, gated by a single role.

    admin card give @user <card name>
    admin coin give @user <coins>
    admin coin remove @user <coins>
    admin player reset @user all      (cards only)
    admin reset all @user             (coins -> 0 AND all cards removed)
    admin user top [20-50] [coins|cards]   (owner / admin role only)
"""
from __future__ import annotations

import discord
from discord.ext import commands

import card_db
import economy
import security
import squad_logic as sl
from security import esc

ADMIN_ROLE_IDS = {1556267277249155156, *security.FULL_ADMIN_ROLE_IDS}


def _has_admin_role(ctx: commands.Context) -> bool:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return False
    return any(r.id in ADMIN_ROLE_IDS for r in ctx.author.roles)


def admin_only():
    return commands.check(_has_admin_role)


def owner_or_admin():
    """Bot owner OR a member with an admin role."""
    async def predicate(ctx: commands.Context) -> bool:
        if await ctx.bot.is_owner(ctx.author) or _has_admin_role(ctx):
            return True
        raise commands.NotOwner("Only the bot owner or an admin can use this.")
    return commands.check(predicate)


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
            "`admin player reset @user all`\n"
            "`admin reset all @user`\n"
            "`admin user top [20-50] [coins|cards]`"
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
                "They haven't debuted yet, already own this player, or their squad is full (25)."
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

    # -- full reset ------------------------------------------------------
    @admin.group(name="reset", invoke_without_command=True)
    @owner_or_admin()
    async def reset(self, ctx: commands.Context):
        await ctx.send("Usage: `admin reset all @user`")

    @reset.command(name="all")
    @owner_or_admin()
    async def reset_all(self, ctx: commands.Context, member: discord.Member):
        if member.bot:
            await ctx.send("❌ Bots don't have an economy.")
            return
        if not economy.user_exists(member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        res = economy.reset_everything(member.id)
        await ctx.send(
            f"✅ Full reset for {esc(member.display_name)}: removed **{res['cards']}** card(s) "
            f"and **{economy.fmt_coins(res['coins'])}**. Their packs, XI and captain were cleared too."
        )

    # -- user stats ------------------------------------------------------
    @admin.group(name="user", invoke_without_command=True)
    @owner_or_admin()
    async def user(self, ctx: commands.Context):
        await ctx.send("Usage: `admin user top [20-50] [coins|cards]`")

    @user.command(name="top")
    @owner_or_admin()
    async def user_top(self, ctx: commands.Context, count: int = 20, by: str = "coins"):
        by = by.lower()
        if by not in ("coins", "cards"):
            await ctx.send("Usage: `admin user top [20-50] [coins|cards]`")
            return
        count = max(1, min(50, count))
        rows = economy.top_users(count, by)
        if not rows:
            await ctx.send("No users yet.")
            return
        lines = []
        for i, r in enumerate(rows, 1):
            lines.append(
                f"`{i:>2}.` <@{r['user_id']}> · **{esc(r['team_name'])}**\n"
                f"      🃏 **{r['cards']}** cards · {economy.fmt_coins(r['purse'])}"
            )
        title = f"Top {len(rows)} users by {by}"
        # 25 users per embed keeps us well under Discord's 4096-char limit.
        for start in range(0, len(lines), 25):
            embed = discord.Embed(
                title=title if start == 0 else f"{title} (cont.)",
                description="\n".join(lines[start:start + 25]),
                color=discord.Color.gold(),
            )
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
