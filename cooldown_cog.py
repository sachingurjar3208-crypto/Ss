"""/cooldown reset — owner-only tool to clear a user's reward cooldowns.

  /cooldown reset user:<member> starterpack:True/False weeklypack:True/False dailypack:True/False

True  = reset that cooldown for the user, False (default) = leave it alone.
  starterpack -> the user can use csstarterpack again
  weeklypack  -> csweekly is available again right away
  dailypack   -> csdaily is available again right away (their streak is kept)

Only the bot owner can run it, it exists only in the main server
(bot_guard.ADMIN_GUILD_ID) and is hidden from normal members.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import economy
from bot_guard import ADMIN_GUILD_ID, OWNER_ID


async def _owner_check(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID


class CooldownCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    cooldown = app_commands.Group(
        name="cooldown",
        description="Owner tools for cooldowns",
        guild_ids=[ADMIN_GUILD_ID],                       # only exists in your server
        guild_only=True,
        default_permissions=discord.Permissions(),        # hidden from normal members
    )

    @cooldown.command(name="reset", description="Reset a user's starter pack / weekly / daily cooldown")
    @app_commands.describe(
        user="Whose cooldown to reset",
        starterpack="True = they can claim csstarterpack again",
        weeklypack="True = csweekly is available again right now",
        dailypack="True = csdaily is available again right now (streak is kept)",
    )
    @app_commands.check(_owner_check)
    async def reset(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        starterpack: bool = False,
        weeklypack: bool = False,
        dailypack: bool = False,
    ):
        if not (starterpack or weeklypack or dailypack):
            await interaction.response.send_message(
                "ℹ️ Nothing selected. Set at least one of `starterpack`, `weeklypack`, `dailypack` to **True**.",
                ephemeral=True,
            )
            return
        if user.bot:
            await interaction.response.send_message("❌ Bots don't have cooldowns.", ephemeral=True)
            return

        done = await asyncio.to_thread(economy.reset_cooldowns, user.id, starterpack, weeklypack, dailypack)
        name = discord.utils.escape_markdown(user.display_name)
        if done is None:
            await interaction.response.send_message(f"❌ **{name}** hasn't debuted yet.", ephemeral=True)
            return

        lines = []
        if done["starter"]:
            lines.append("🎁 Starter pack: **reset** (they can use `csstarterpack` again)")
        if done["weekly"]:
            lines.append("📅 Weekly pack: **reset** (`csweekly` is ready)")
        if done["daily"]:
            lines.append("☀️ Daily pack: **reset** (`csdaily` is ready, streak kept)")
        await interaction.response.send_message(
            f"✅ Cooldown reset for **{name}**\n" + "\n".join(lines), ephemeral=True
        )

    @reset.error
    async def reset_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            msg = "🔒 Only the bot owner can use this command."
        else:
            msg = f"⚠️ Something went wrong: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CooldownCog(bot))
