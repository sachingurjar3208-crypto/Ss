"""Admin-only commands, gated by a single role.

    admin card give @user <card name>
    admin coin give @user <coins>
    admin coin remove @user <coins>
    admin player reset @user all      (cards only)
    admin reset all @user             (coins -> 0 AND all cards removed)
    admin user top [20-50] [coins|cards]   (owner / admin role only)
    admin voucher give @user <n>
    admin voucher remove @user <n>
    admin voucher check @user
    admin quest reset @user      (clears today's quest progress for that user)
"""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

import card_db
import draft_config as cfg
import economy
import emojis
import quest
import security
import squad_logic as sl
from bot_guard import ADMIN_GUILD_ID, OWNER_ID
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


def owner_only():
    """Only the bot owner (OWNER_ID)."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id == OWNER_ID:
            return True
        raise commands.NotOwner("Only the bot owner can use this.")
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
            "`admin user top [20-50] [coins|cards]`\n"
            "`admin economy leaderboard [1-50] [networth|coins|cards]`\n"
            "`admin voucher give @user <number>`\n"
            "`admin voucher remove @user <number>`\n"
            "`admin voucher check @user`\n"
            "`admin quest reset @user`\n"
            "`admin blacklist add @user <reason>`  (owner)\n"
            "`admin blacklist remove @user <reason>`  (owner)"
        )

    # -- blacklist (owner only) -------------------------------------------
    @admin.group(name="blacklist", invoke_without_command=True)
    @owner_only()
    async def blacklist(self, ctx: commands.Context):
        await ctx.send("Usage: `admin blacklist add @user <reason>` or `admin blacklist remove @user <reason>`")

    @blacklist.command(name="add")
    @owner_only()
    async def blacklist_add(self, ctx: commands.Context, member: discord.User, *, reason: str):
        if member.bot or member.id == OWNER_ID or member.id == ctx.author.id:
            await ctx.send("❌ Can't blacklist that user.")
            return
        reason = security.clean_input(reason, 150)
        if security.is_banned(member.id):
            await ctx.send(f"ℹ️ **{esc(member.display_name)}** is already blacklisted.")
            return
        security.ban_user(member.id, reason)
        await ctx.send(f"🚫 Blacklisted **{esc(member.display_name)}**.\n**Reason:** {esc(reason)}")
        await self._blacklist_log(ctx, member, "🚫 Blacklist ADD", discord.Color.dark_red(), reason)

    @blacklist.command(name="remove")
    @owner_only()
    async def blacklist_remove(self, ctx: commands.Context, member: discord.User, *, reason: str):
        reason = security.clean_input(reason, 150)
        if not security.is_banned(member.id):
            await ctx.send(f"ℹ️ **{esc(member.display_name)}** is not blacklisted.")
            return
        security.unban_user(member.id)
        await ctx.send(f"✅ Removed **{esc(member.display_name)}** from the blacklist.\n**Reason:** {esc(reason)}")
        await self._blacklist_log(ctx, member, "✅ Blacklist REMOVE", discord.Color.green(), reason)

    async def _blacklist_log(self, ctx, member, title, color, reason):
        try:
            from log_cog import post_log
            e = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
            e.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
            e.add_field(name="By", value=f"{ctx.author.mention}", inline=True)
            e.add_field(name="Reason", value=reason or "-", inline=False)
            await post_log(self.bot, e)
        except Exception as ex:
            print(f"[log] blacklist log failed: {ex}")

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

    # -- vouchers --------------------------------------------------------
    # Same rules as csvoucher (draft_cog): owner / full-admin only, main server
    # only, 1..VOUCHER_ADMIN_MAX per command, logged in voucher_log.
    @admin.group(name="voucher", invoke_without_command=True)
    @security.owner_or_admin_role()
    async def voucher(self, ctx: commands.Context):
        await ctx.send(
            "`admin voucher give @user <number>`\n"
            "`admin voucher remove @user <number>`\n"
            "`admin voucher check @user`"
        )

    @staticmethod
    def _main_guild(ctx: commands.Context) -> bool:
        return ctx.guild is not None and ctx.guild.id == ADMIN_GUILD_ID

    @voucher.command(name="give")
    @security.owner_or_admin_role()
    async def voucher_give(self, ctx: commands.Context, member: discord.Member, amount: int):
        if not self._main_guild(ctx):
            return
        if member.bot:
            await ctx.send("❌ Bots can't have vouchers.")
            return
        if not 1 <= amount <= cfg.VOUCHER_ADMIN_MAX:
            await ctx.send(f"❌ Give between 1 and {cfg.VOUCHER_ADMIN_MAX:,} vouchers at a time.")
            return
        new = economy.add_vouchers(member.id, amount, f"Admin give by {ctx.author.id}")
        if new is None:
            await ctx.send("❌ That user hasn't debuted.")
            return
        await ctx.send(
            f"✅ Gave **{amount:,}** {emojis.VOUCHER} to {esc(member.display_name)}. "
            f"They now have **{new:,}**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @voucher.command(name="remove")
    @security.owner_or_admin_role()
    async def voucher_remove(self, ctx: commands.Context, member: discord.Member, amount: int):
        if not self._main_guild(ctx):
            return
        if not 1 <= amount <= cfg.VOUCHER_ADMIN_MAX:
            await ctx.send(f"❌ Remove between 1 and {cfg.VOUCHER_ADMIN_MAX:,} vouchers at a time.")
            return
        out = economy.remove_vouchers(member.id, amount, f"Admin remove by {ctx.author.id}")
        if out is None:
            await ctx.send("❌ That user hasn't debuted.")
            return
        removed, new = out
        await ctx.send(
            f"✅ Removed **{removed:,}** {emojis.VOUCHER} from {esc(member.display_name)}. "
            f"They now have **{new:,}**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @voucher.command(name="check")
    @security.owner_or_admin_role()
    async def voucher_check(self, ctx: commands.Context, member: discord.Member):
        if not self._main_guild(ctx):
            return
        if not economy.user_exists(member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        have = economy.get_vouchers(member.id)
        since = economy.get_draft_pity(member.id)
        left = max(1, cfg.PITY_A_EVERY - since)
        await ctx.send(
            f"{emojis.VOUCHER} **{esc(member.display_name)}** has **{have:,}** voucher(s).\n"
            f"Pool A guaranteed in **{left}** draft(s).",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- quests ----------------------------------------------------------
    @admin.group(name="quest", invoke_without_command=True)
    @security.owner_or_admin_role()
    async def quest_group(self, ctx: commands.Context):
        await ctx.send("Usage: `admin quest reset @user`")

    @quest_group.command(name="reset")
    @security.owner_or_admin_role()
    async def quest_reset(self, ctx: commands.Context, member: discord.Member):
        if not self._main_guild(ctx):
            return
        if not economy.user_exists(member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        n = quest.reset_user_today(member.id)
        await ctx.send(
            f"✅ Cleared today's quest progress for {esc(member.display_name)} "
            f"({n} finished quest(s) reset). Vouchers already paid were not taken back.",
            allowed_mentions=discord.AllowedMentions.none(),
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

    # -- economy leaderboard ---------------------------------------------
    @admin.group(name="economy", invoke_without_command=True)
    @owner_or_admin()
    async def economy_group(self, ctx: commands.Context):
        await ctx.send("Usage: `admin economy leaderboard [1-50] [networth|coins|cards]`")

    @economy_group.command(name="leaderboard", aliases=["lb", "top"])
    @owner_or_admin()
    async def economy_leaderboard(self, ctx: commands.Context, count: int = 10, by: str = "networth"):
        """Richest users. Net worth = coins + sell value of every card they own."""
        by = by.lower()
        if by not in ("networth", "coins", "cards"):
            await ctx.send("Usage: `admin economy leaderboard [1-50] [networth|coins|cards]`")
            return
        count = max(1, min(50, count))
        snap = await asyncio.to_thread(economy.economy_snapshot)
        if not snap:
            await ctx.send("No users yet.")
            return
        values = {c["playername_key"].strip().lower(): sl.sell_value(c) for c in card_db.list_all_cards()}
        rows = []
        for u in snap:
            card_value = sum(values.get(k, 0) for k in u["keys"])
            rows.append({
                **u, "cards": len(u["keys"]), "card_value": card_value,
                "networth": u["purse"] + card_value,
            })
        key = {"networth": "networth", "coins": "purse", "cards": "cards"}[by]
        rows.sort(key=lambda r: (-r[key], -r["networth"]))
        total_coins = sum(r["purse"] for r in rows)
        total_cards = sum(r["cards"] for r in rows)
        total_value = sum(r["card_value"] for r in rows)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, r in enumerate(rows[:count], 1):
            lines.append(
                f"{medals.get(i, f'`{i:>2}.`')} <@{r['user_id']}> · **{esc(r['team_name'])}**\n"
                f"      💎 Net worth **{economy.fmt_coins(r['networth'])}**\n"
                f"      🪙 {economy.fmt_coins(r['purse'])} · 🃏 {r['cards']} cards "
                f"({economy.fmt_coins(r['card_value'])})"
            )
        label = {"networth": "net worth", "coins": "coins", "cards": "cards"}[by]
        title = f"💰 Economy leaderboard — top {min(count, len(rows))} by {label}"
        first = True
        for start in range(0, len(lines), 10):   # 10 users per embed stays far below 4096 chars
            embed = discord.Embed(
                title=title if first else f"{title} (cont.)",
                description="\n".join(lines[start:start + 10]),
                color=discord.Color.gold(),
            )
            if first:
                embed.add_field(
                    name="Whole economy",
                    value=(
                        f"👥 Users: **{len(rows):,}**\n"
                        f"🪙 Coins in circulation: **{economy.fmt_coins(total_coins)}**\n"
                        f"🃏 Cards owned: **{total_cards:,}** (worth {economy.fmt_coins(total_value)})\n"
                        f"💎 Total: **{economy.fmt_coins(total_coins + total_value)}**"
                    ),
                    inline=False,
                )
                first = False
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
