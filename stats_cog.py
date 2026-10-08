"""`cs about` — bot speed, uptime and usage statistics.

Also counts every successful command (per user) in economy.db so the
"how many users used the bot" numbers work. Counting starts from the day this
file is deployed.
"""
from __future__ import annotations

import asyncio
import time

import discord
from discord.ext import commands

import card_db
import economy

START_TIME = time.time()


def _init_table() -> None:
    conn = economy._conn()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS command_usage ("
            " user_id INTEGER PRIMARY KEY,"
            " uses    INTEGER NOT NULL DEFAULT 0,"
            " last_used REAL NOT NULL DEFAULT 0)"
        )
    finally:
        conn.close()


def _record(user_id: int) -> None:
    conn = economy._conn()
    try:
        conn.execute(
            "INSERT INTO command_usage (user_id, uses, last_used) VALUES (?, 1, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET uses = uses + 1, last_used = excluded.last_used",
            (int(user_id), time.time()),
        )
    finally:
        conn.close()


def _scalar(conn, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0] or 0) if row else 0


def _collect() -> dict:
    conn = economy._conn()
    try:
        return {
            "cards_made": len(card_db.list_all_cards()),
            "cards_owned": _scalar(conn, "SELECT COUNT(*) FROM owned"),
            "owners": _scalar(conn, "SELECT COUNT(DISTINCT user_id) FROM owned"),
            "debuted": _scalar(conn, "SELECT COUNT(*) FROM users"),
            "cmd_users": _scalar(conn, "SELECT COUNT(*) FROM command_usage"),
            "cmd_total": _scalar(conn, "SELECT SUM(uses) FROM command_usage"),
        }
    finally:
        conn.close()


def _fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = [f"{d}d"] if d else []
    parts += [f"{h}h", f"{m}m"] if (d or h) else [f"{m}m"]
    parts.append(f"{s}s")
    return " ".join(parts)


class StatsCog(commands.Cog, name="Stats"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _init_table()

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        if ctx.author.bot:
            return
        try:
            await asyncio.to_thread(_record, ctx.author.id)
        except Exception as e:
            print(f"[stats] couldn't record command use: {type(e).__name__}: {e}")

    @commands.group(name="cs", invoke_without_command=True)
    async def cs(self, ctx: commands.Context):
        await ctx.send("Usage: `cs about`")

    @cs.command(name="about", aliases=["info", "stats", "ping"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def about(self, ctx: commands.Context):
        """Bot latency, uptime and usage statistics."""
        ws_ms = round(self.bot.latency * 1000)

        t0 = time.perf_counter()
        msg = await ctx.send("Checking...")
        api_ms = round((time.perf_counter() - t0) * 1000)

        data = await asyncio.to_thread(_collect)

        embed = discord.Embed(title="Bot Info", color=discord.Color.blue())
        embed.add_field(
            name="Speed",
            value=f"Websocket: **{ws_ms} ms**\nMessage round-trip: **{api_ms} ms**",
            inline=False,
        )
        embed.add_field(
            name="Bot",
            value=(
                f"Uptime: **{_fmt_uptime(time.time() - START_TIME)}**\n"
                f"Servers: **{len(self.bot.guilds):,}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Cards",
            value=(
                f"Cards created: **{data['cards_made']:,}**\n"
                f"Cards owned by players: **{data['cards_owned']:,}**\n"
                f"Players holding cards: **{data['owners']:,}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Users",
            value=(
                f"Debuted players: **{data['debuted']:,}**\n"
                f"Users who used commands: **{data['cmd_users']:,}**\n"
                f"Total commands used: **{data['cmd_total']:,}**"
            ),
            inline=False,
        )
        embed.set_footer(text="Command counting started when this feature was added.")
        await msg.edit(content=None, embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))
