"""Top.gg vote rewards.

`rcvote` shows the vote link. After voting on Top.gg the player types `rcvote` again and the bot
checks Top.gg ("has this user voted in the last 12 hours?") and pays the reward once per vote.
No web server is needed: the bot asks Top.gg itself.

Setup: put the bot's Top.gg API token in .env on the VPS (never in the repo):
    TOPGG_TOKEN=xxxxxxxx
(Top.gg > your bot > Integrations & API > Token.)
"""
from __future__ import annotations

import asyncio
import os
import time

import aiohttp
import discord
from discord.ext import commands

import economy

VOTE_COINS = 25_000           # reward per vote
VOTE_VOUCHERS = 1
WEEKEND_MULTIPLIER = 2        # Top.gg counts weekend votes double, so do we
VOTE_COOLDOWN = 12 * 3600     # Top.gg allows one vote every 12 hours
STREAK_BONUS_EVERY = 7        # every 7th vote in a row (within 36 h of the last one) = bonus
STREAK_BONUS_VOUCHERS = 3
STREAK_GAP = 36 * 3600

API = "https://top.gg/api"


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS vote_claims (user_id INTEGER PRIMARY KEY, last_ts REAL NOT NULL, "
                 "streak INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0)")


def _last(user_id: int):
    with economy._tx() as conn:
        _ensure(conn)
        return conn.execute("SELECT last_ts, streak, total FROM vote_claims WHERE user_id = ?",
                            (int(user_id),)).fetchone()


def _claim(user_id: int, weekend: bool) -> dict | None:
    """Pay one vote. None = already claimed for this vote (or not debuted)."""
    uid, now = int(user_id), time.time()
    with economy._tx() as conn:
        _ensure(conn)
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return None
        r = conn.execute("SELECT last_ts, streak, total FROM vote_claims WHERE user_id = ?", (uid,)).fetchone()
        if r and now - r[0] < VOTE_COOLDOWN:
            return None
        streak = (r[1] + 1) if (r and now - r[0] <= STREAK_GAP) else 1
        total = (r[2] if r else 0) + 1
        conn.execute("INSERT OR REPLACE INTO vote_claims (user_id, last_ts, streak, total) VALUES (?, ?, ?, ?)",
                     (uid, now, streak, total))
    mult = WEEKEND_MULTIPLIER if weekend else 1
    coins, vouchers = VOTE_COINS * mult, VOTE_VOUCHERS * mult
    bonus = STREAK_BONUS_VOUCHERS if streak % STREAK_BONUS_EVERY == 0 else 0
    economy.add_coins(uid, coins, "Top.gg vote")
    economy.add_vouchers(uid, vouchers + bonus, "Top.gg vote")
    return {"coins": coins, "vouchers": vouchers, "bonus": bonus, "streak": streak, "total": total}


class VoteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.token = os.getenv("TOPGG_TOKEN", "").strip()

    def _url(self) -> str:
        return f"https://top.gg/bot/{self.bot.user.id}/vote"

    async def _api(self, path: str, **params):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(f"{API}{path}", params=params, headers={"Authorization": self.token}) as r:
                if r.status != 200:
                    raise RuntimeError(f"Top.gg answered {r.status}")
                return await r.json()

    def _button(self) -> discord.ui.View:
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label="Vote on Top.gg", url=self._url()))
        return v

    @commands.command(name="rcvote", aliases=["vote"])
    async def rcvote(self, ctx: commands.Context):
        if not self.token:
            await ctx.send("Voting rewards are not set up yet.", view=self._button())
            return
        last = await asyncio.to_thread(_last, ctx.author.id)
        if last and time.time() - last[0] < VOTE_COOLDOWN:
            left = int(VOTE_COOLDOWN - (time.time() - last[0]))
            await ctx.send(f"You already claimed your vote reward. Vote again in **{left // 3600}h "
                           f"{(left % 3600) // 60}m**.", view=self._button())
            return
        try:
            voted = (await self._api(f"/bots/{self.bot.user.id}/check", userId=ctx.author.id)).get("voted") == 1
            weekend = bool((await self._api("/weekend")).get("is_weekend")) if voted else False
        except Exception as e:
            print(f"[vote] Top.gg check failed: {type(e).__name__}: {e}")
            await ctx.send("Couldn't reach Top.gg right now. Try again in a minute.", view=self._button())
            return
        if not voted:
            mult = "(double on weekends)"
            await ctx.send(f"Vote for **Rush Cricket** on Top.gg, then type `rcvote` again to claim "
                           f"**{VOTE_COINS:,} coins + {VOTE_VOUCHERS} voucher** {mult}. "
                           f"Every {STREAK_BONUS_EVERY}th vote in a row gives **+{STREAK_BONUS_VOUCHERS} vouchers**.",
                           view=self._button())
            return
        res = await asyncio.to_thread(_claim, ctx.author.id, weekend)
        if res is None:
            await ctx.send("Use `rcdebut` first, then claim your vote reward.")
            return
        lines = [f"Thanks for voting! You got **{res['coins']:,} coins** and **{res['vouchers']} voucher"
                 f"{'s' if res['vouchers'] != 1 else ''}**" + (" (weekend double)" if weekend else "") + "."]
        if res["bonus"]:
            lines.append(f"Vote streak **{res['streak']}**: bonus **+{res['bonus']} vouchers**!")
        else:
            nxt = STREAK_BONUS_EVERY - res["streak"] % STREAK_BONUS_EVERY
            lines.append(f"Vote streak: **{res['streak']}** ({nxt} more for +{STREAK_BONUS_VOUCHERS} vouchers).")
        lines.append("You can vote again in 12 hours.")
        await ctx.send("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(VoteCog(bot))
