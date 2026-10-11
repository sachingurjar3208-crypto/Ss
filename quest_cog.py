"""Daily quests.

    csquest      today's 5 quests with your progress (aliases: csquests, quests)

Every finished quest pays 1 voucher automatically. Rules, quest list and
settings are in quest.py. The image is drawn by quest_image.py.
"""
from __future__ import annotations

import asyncio

import discord
from discord import ui
from discord.ext import commands

import economy
import quest
import quest_image
from economy_cog import economy_cooldown
from security import OwnedView
from squad_cog import _need_debut


async def _render(user_id: int, team_name: str):
    board = await asyncio.to_thread(quest.get_board, user_id)
    buf = await asyncio.to_thread(quest_image.build_quest_board, board, team_name)
    embed = discord.Embed(color=0x2FBF9B)
    embed.set_image(url=f"attachment://{quest_image.FILENAME}")
    left = len(board["quests"]) - board["done_count"]
    if left == 0:
        embed.set_footer(text="All quests done for today. New quests after the reset.")
    else:
        embed.set_footer(text=f"{quest.QUEST_VOUCHER} voucher for every quest you finish  |  {left} left today")
    return embed, discord.File(buf, filename=quest_image.FILENAME)


class QuestView(OwnedView):
    def __init__(self, user_id: int, team_name: str):
        super().__init__(user_id, timeout=120)
        self.user_id = user_id
        self.team_name = team_name

    @ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        embed, file = await _render(self.user_id, self.team_name)
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)


class QuestCog(commands.Cog, name="Quest"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await asyncio.to_thread(quest.ensure_tables)
        except Exception as e:
            print(f"[quest] could not create quest tables: {e}")

    @commands.command(name="csquest", aliases=["csquests", "quests"])
    @economy_cooldown()
    async def csquest(self, ctx: commands.Context):
        """Show today's quests and your progress."""
        if not await _need_debut(ctx):
            return
        user = economy.get_user(ctx.author.id)
        team = user["team_name"] if user else ""
        embed, file = await _render(ctx.author.id, team)
        view = QuestView(ctx.author.id, team)
        view.message = await ctx.send(embed=embed, file=file, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(QuestCog(bot))
