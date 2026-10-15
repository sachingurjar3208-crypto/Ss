"""`ping` - shows only the message round-trip latency."""
import time

from discord.ext import commands


class PingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ping(self, ctx: commands.Context):
        """Message latency: time to send a message and get it back."""
        t0 = time.perf_counter()
        msg = await ctx.send("Pinging...")
        ms = round((time.perf_counter() - t0) * 1000)
        await msg.edit(content=f"Pong! Message latency: **{ms} ms**")


async def setup(bot: commands.Bot):
    await bot.add_cog(PingCog(bot))
