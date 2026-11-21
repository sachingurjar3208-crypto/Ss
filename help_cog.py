"""/help - the same help menu as `rchelp`, as a slash command (dropdown + buttons)."""
import discord
from discord import app_commands
from discord.ext import commands

import help_menu


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="All commands and how to play, with a menu and buttons")
    @app_commands.guild_only()
    async def help_slash(self, interaction: discord.Interaction):
        await help_menu.send_help_slash(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
