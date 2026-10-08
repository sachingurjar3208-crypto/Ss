"""/maintenance — turn maintenance mode on or off (owner / admin role only).

  /maintenance reason:<text> universal:True    ON  — every non-staff command is blocked and
                                                    the user sees the reason
  /maintenance universal:False                 OFF — everyone can use commands again

The owner and the admin role are never blocked, so you can still test and switch it off.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot_guard import MaintenanceActive, admin_only, maintenance_on, set_maintenance

MAX_REASON = 300


class MaintenanceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="maintenance",
        description="Turn maintenance mode ON or OFF for the whole bot",
    )
    @app_commands.describe(
        reason="Why the bot is under maintenance (shown to every user who tries a command)",
        universal="True = block ALL commands for everyone, False = turn maintenance OFF",
    )
    @admin_only()
    async def maintenance(
        self,
        interaction: discord.Interaction,
        reason: str | None = None,
        universal: bool = True,
    ):
        if not universal:
            was_on = maintenance_on()
            set_maintenance(False, by=interaction.user.id)
            msg = (
                "✅ Maintenance is **OFF**. Everyone can use commands again."
                if was_on else "ℹ️ Maintenance was already off."
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        text = discord.utils.escape_mentions((reason or "").strip())[:MAX_REASON]
        text = text or "The bot is being updated."
        set_maintenance(True, text, interaction.user.id)
        await interaction.response.send_message(
            "🛠️ Maintenance is **ON**. All commands are blocked for everyone except the owner "
            "and the admin role.\n"
            f"**Reason shown to users:** {text}\n"
            "Turn it off with `/maintenance universal:False`.",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, MaintenanceActive):
            return
        if isinstance(error, app_commands.CheckFailure):
            msg = "🔒 Only the bot owner or the admin role can use this command."
        else:
            msg = f"⚠️ Something went wrong: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MaintenanceCog(bot))
