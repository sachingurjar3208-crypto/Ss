"""`ping` - shows message latency directly, with no placeholder message."""
import datetime

from discord.ext import commands


class PingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ping(self, ctx: commands.Context):
        """Time from the user's message being created to the bot handling it."""
        now = datetime.datetime.now(datetime.timezone.utc)
        ms = max(0, round((now - ctx.message.created_at).total_seconds() * 1000))
        await ctx.send(f"Message latency: **{ms} ms**")


async def setup(bot: commands.Bot):
    await bot.add_cog(PingCog(bot))
