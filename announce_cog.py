"""Owner-only: DM every debuted player about a new event.

    admin announce <message>            -> sends to everyone who debuted (not banned)
    admin announce test <message>       -> sends only to you (preview)

Install: copy this file next to main.py and add "announce_cog" to the cog list
(same way the other cogs are loaded), then restart the bot.
"""
from __future__ import annotations

import asyncio
import random

import discord
from discord.ext import commands

import economy
import security
from bot_guard import OWNER_ID

DELAY_MIN, DELAY_MAX = 5, 7  # random gap (seconds) between DMs - keeps spam flags away


class AnnounceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="announce")
    async def announce(self, ctx: commands.Context, *, message: str = ""):
        if ctx.author.id != OWNER_ID:
            return await ctx.send("❌ Only the bot owner can use this.")
        if not message.strip() or message.strip().lower() == "test":
            return await ctx.send("Usage: `announce <message>`  or  `announce test <message>`")

        # `economy.list_users()` = every player who used rcdebut (users table in economy.db)
        ids = [int(u["user_id"]) for u in economy.list_users()
               if not security.is_banned(u["user_id"])]

        if message.lower().startswith("test "):
            message = message[5:]
            ids = [ctx.author.id]

        status = await ctx.send(f"📨 Sending to **{len(ids)}** players...")
        sent = closed = failed = 0
        for uid in ids:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                await user.send(message)
                sent += 1
            except discord.Forbidden:      # DMs closed / bot blocked
                closed += 1
            except discord.HTTPException:
                failed += 1
            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        await status.edit(content=f"✅ Done. Sent: **{sent}** | DMs closed: **{closed}** | Failed: **{failed}**")


async def setup(bot: commands.Bot):
    await bot.add_cog(AnnounceCog(bot))
    print("[announce] ready: owner command `announce` loaded")
