"""Top.gg vote rewards.

* `rcvote` shows a vote card: rewards, when the next vote is ready, and a "Vote" button.
* After a vote, Top.gg calls our webhook and the reward is paid AUTOMATICALLY and sent by DM.
  (If the webhook is not set up, `rcvote` after voting checks Top.gg and pays the reward instead.)
* `rcvotereminder` turns a DM reminder on/off when the next vote is ready.
Each vote is paid once.

Setup (.env on the VPS, never in the repo):
    TOPGG_TOKEN=xxxxxxxx          Top.gg > Integrations & API > Token
    TOPGG_WEBHOOK_SECRET=xxxxxx   any long password; the same text goes in Top.gg's webhook settings
    TOPGG_WEBHOOK_PORT=5005       optional (default 5005)
Top.gg webhook URL:  http://<VPS IP>:5005/topgg
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import time

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands, tasks

import card_cache
import card_db
import economy
import emojis

VOTE_VOUCHERS = 2             # reward per vote: 2 vouchers + 1 random card
VOTE_CARD_OVR = (78, 83)      # card OVR range (inclusive)
WEEKEND_MULTIPLIER = 2        # weekend votes: vouchers x2 (card stays 1)
VOTE_COOLDOWN = 12 * 3600     # Top.gg allows one vote every 12 hours
STREAK_BONUS_EVERY = 7        # every 7th vote in a row (within 36 h of the last one) = bonus
STREAK_BONUS_VOUCHERS = 3
STREAK_GAP = 36 * 3600

API = "https://top.gg/api"


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS vote_claims (user_id INTEGER PRIMARY KEY, last_ts REAL NOT NULL, "
                 "streak INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(vote_claims)")}
    if "remind" not in cols:
        conn.execute("ALTER TABLE vote_claims ADD COLUMN remind INTEGER NOT NULL DEFAULT 0")
    if "reminded" not in cols:
        conn.execute("ALTER TABLE vote_claims ADD COLUMN reminded INTEGER NOT NULL DEFAULT 1")


def _toggle_reminder(user_id: int) -> bool:
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT remind FROM vote_claims WHERE user_id = ?", (int(user_id),)).fetchone()
        new = 0 if (r and r[0]) else 1
        if r:
            conn.execute("UPDATE vote_claims SET remind = ? WHERE user_id = ?", (new, int(user_id)))
        else:
            conn.execute("INSERT INTO vote_claims (user_id, last_ts, remind, reminded) VALUES (?, 0, ?, 1)",
                         (int(user_id), new))
    return bool(new)


def _due_reminders() -> list[int]:
    now = time.time()
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT user_id FROM vote_claims WHERE remind = 1 AND reminded = 0 AND last_ts > 0 "
                            "AND last_ts + ? <= ?", (VOTE_COOLDOWN, now)).fetchall()
        for (uid,) in rows:
            conn.execute("UPDATE vote_claims SET reminded = 1 WHERE user_id = ?", (uid,))
    return [r[0] for r in rows]


def _last(user_id: int):
    with economy._tx() as conn:
        _ensure(conn)
        return conn.execute("SELECT last_ts, streak, total FROM vote_claims WHERE user_id = ?",
                            (int(user_id),)).fetchone()


def _claim(user_id: int, weekend: bool) -> dict | None:
    """Pay one vote. None = already claimed for this vote (or not debuted)."""
    uid, now = int(user_id), time.time()
    lo, hi = VOTE_CARD_OVR
    pool = [c["playername_key"] for c in card_db.list_obtainable_cards() if lo <= int(c["ovr"]) <= hi]
    with economy._tx() as conn:
        _ensure(conn)
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return None
        r = conn.execute("SELECT last_ts, streak, total FROM vote_claims WHERE user_id = ?", (uid,)).fetchone()
        if r and now - r[0] < VOTE_COOLDOWN - 600:     # 10 min grace: Top.gg's own 12 h timer
            return None
        streak = (r[1] + 1) if (r and now - r[0] <= STREAK_GAP) else 1
        total = (r[2] if r else 0) + 1
        conn.execute("INSERT INTO vote_claims (user_id, last_ts, streak, total, reminded) VALUES (?, ?, ?, ?, 0) "
                     "ON CONFLICT(user_id) DO UPDATE SET last_ts = excluded.last_ts, streak = excluded.streak, "
                     "total = excluded.total, reminded = 0", (uid, now, streak, total))
        card = None
        if pool:
            owned = {r[0] for r in conn.execute("SELECT player_key FROM owned WHERE user_id = ?", (uid,))}
            fresh = [k for k in pool if k not in owned]
            card = random.choice(fresh or pool)
            economy._ensure_pending_table(conn)
            if economy._grant(conn, uid, card, "Top.gg vote") == "blocked":
                card = None
    mult = WEEKEND_MULTIPLIER if weekend else 1
    vouchers = VOTE_VOUCHERS * mult
    bonus = STREAK_BONUS_VOUCHERS if streak % STREAK_BONUS_EVERY == 0 else 0
    economy.add_vouchers(uid, vouchers + bonus, "Top.gg vote")
    return {"vouchers": vouchers, "bonus": bonus, "streak": streak, "total": total, "card": card}


ACCENT = 0xF5B342


def _reward_line(weekend: bool = False) -> str:
    m = WEEKEND_MULTIPLIER if weekend else 1
    lo, hi = VOTE_CARD_OVR
    return (f"{emojis.VOUCHER} **{VOTE_VOUCHERS * m}** voucher{'s' if VOTE_VOUCHERS * m != 1 else ''}\n"
            f"**1** random card ({lo}-{hi} OVR)")


class VoteView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Vote", emoji="\N{UPWARDS BLACK ARROW}", url=url))


class VoteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.token = os.getenv("TOPGG_TOKEN", "").strip()
        self.secret = os.getenv("TOPGG_WEBHOOK_SECRET", "").strip()
        self.port = int(os.getenv("TOPGG_WEBHOOK_PORT", "5005") or 5005)
        self.runner: web.AppRunner | None = None

    async def cog_load(self):
        if self.secret:
            app = web.Application()
            app.router.add_post("/topgg", self._webhook)
            app.router.add_route("*", "/{tail:.*}", self._webhook)   # any path / method: log + handle
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            try:
                await web.TCPSite(self.runner, "0.0.0.0", self.port).start()
                print(f"[vote] Top.gg webhook listening on port {self.port}")
            except OSError as e:
                print(f"[vote] webhook port {self.port} not available: {e}")
        self.reminder_loop.start()

    async def cog_unload(self):
        self.reminder_loop.cancel()
        if self.runner:
            await self.runner.cleanup()

    def _url(self) -> str:
        return f"https://top.gg/bot/{self.bot.user.id}/vote"

    async def _api(self, path: str, **params):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(f"{API}{path}", params=params, headers={"Authorization": self.token}) as r:
                if r.status != 200:
                    raise RuntimeError(f"Top.gg answered {r.status}")
                return await r.json()

    # ── paying + DM ─────────────────────────────────────────────────────────
    def _reward_embed(self, res: dict, weekend: bool) -> tuple[discord.Embed, str | None]:
        """Clean reward message: the card first, then the vouchers. Returns (embed, card image path)."""
        card = card_db.get_card(res["card"]) if res.get("card") else None
        lines = ["**You got:**"]
        if card:
            lines.append(f"Player: **{card['playername']}** ({card['ovr']} OVR)")
        lines.append(f"Vouchers: **{res['vouchers']}**" + (" (weekend double)" if weekend else ""))
        if res["bonus"]:
            lines.append(f"Streak bonus: **{res['bonus']}** vouchers")
        total = res["vouchers"] + res["bonus"]
        if res["bonus"]:
            lines.append(f"Total vouchers: **{total}**")
        nxt = STREAK_BONUS_EVERY - res["streak"] % STREAK_BONUS_EVERY
        lines += ["", f"Vote streak: **{res['streak']}**"
                  + ("" if res["bonus"] else f" ({nxt} more for +{STREAK_BONUS_VOUCHERS} vouchers)"),
                  f"Next vote: <t:{int(time.time() + VOTE_COOLDOWN)}:R>"]
        e = discord.Embed(title="Thanks for voting!", description="\n".join(lines), color=ACCENT)
        e.set_footer(text="rcvotereminder = DM when you can vote again")
        return e, (card.get("image_path") if card else None)

    async def _send_reward(self, dest, embed: discord.Embed, path: str | None) -> None:
        file = await card_cache.card_file(path) if path else None
        if file:
            embed.set_image(url=f"attachment://{file.filename}")
            await dest.send(embed=embed, file=file)
        else:
            await dest.send(embed=embed)

    async def _pay_and_dm(self, user_id: int, weekend: bool, channel=None) -> bool:
        res = await asyncio.to_thread(_claim, user_id, weekend)
        if res is None:
            print(f"[vote] no reward for {user_id}: already claimed in last 12h or not debuted")
            return False
        embed, path = self._reward_embed(res, weekend)
        sent = False
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await self._send_reward(user, embed, path)
            sent = True
        except Exception as e:
            print(f"[vote] DM to {user_id} failed: {type(e).__name__}: {e}")
        if channel is not None:
            if sent:
                await channel.send(f"<@{user_id}> your vote reward was sent to your DMs.")
            else:
                await self._send_reward(channel, embed, path)
        print(f"[vote] paid {user_id}: {res}")
        return True

    # ── Top.gg webhook (automatic) ──────────────────────────────────────────
    def _authorised(self, request, body: bytes) -> bool:
        if hmac.compare_digest(request.headers.get("Authorization", ""), self.secret):
            return True
        sig = request.headers.get("x-topgg-signature", "")          # newer Top.gg webhooks
        if sig:
            parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
            mac = hmac.new(self.secret.encode(), f"{parts.get('t', '')}.".encode() + body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(mac, parts.get("v1", ""))
        return False

    async def _webhook(self, request: web.Request) -> web.Response:
        body = await request.read()
        if request.method != "POST":
            print(f"[vote] {request.method} {request.path} (not a webhook)")
            return web.Response(text="Rush Cricket vote webhook is up")
        print(f"[vote] webhook hit on {request.path} from {request.remote} "
              f"(sig={'yes' if request.headers.get('x-topgg-signature') else 'no'}, "
              f"auth={'yes' if request.headers.get('Authorization') else 'no'}, {len(body)} bytes)")
        if not self._authorised(request, body):
            print("[vote] webhook rejected: secret/signature did not match")
            return web.Response(status=401)
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            print("[vote] webhook rejected: body is not JSON")
            return web.Response(status=400)
        kind = str(data.get("type", ""))
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        user = data.get("user") or (inner.get("user") or {}).get("platform_id") or (inner.get("user") or {}).get("id")
        weekend = bool(data.get("isWeekend") or inner.get("weight", 1) > 1)
        if kind == "test" or kind.endswith("test"):
            print(f"[vote] Top.gg test webhook received (user {user})")
            return web.Response(status=200)
        try:
            uid = int(user)
        except (TypeError, ValueError):
            print(f"[vote] webhook had no user id: type={kind!r}")
            return web.Response(status=400)
        print(f"[vote] vote received from {uid} (type={kind!r}, weekend={weekend})")
        asyncio.create_task(self._pay_and_dm(uid, weekend))
        return web.Response(status=200)

    # ── reminder ────────────────────────────────────────────────────────────
    @tasks.loop(minutes=5)
    async def reminder_loop(self):
        for uid in await asyncio.to_thread(_due_reminders):
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                e = discord.Embed(title="You can vote again", color=ACCENT,
                                  description="Your next Rush Cricket vote is ready.\n\n" + _reward_line())
                await user.send(embed=e, view=VoteView(self._url()))
            except Exception as ex:
                print(f"[vote] reminder to {uid} failed: {type(ex).__name__}: {ex}")

    @reminder_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ── commands ────────────────────────────────────────────────────────────
    @commands.command(name="rcvote", aliases=["vote"])
    async def rcvote(self, ctx: commands.Context):
        last = await asyncio.to_thread(_last, ctx.author.id)
        last_ts = last[0] if last else 0
        ready_at = last_ts + VOTE_COOLDOWN
        # voted on Top.gg but the webhook didn't pay yet: check and pay now
        if self.token and time.time() >= ready_at - 600:
            try:
                voted = (await self._api(f"/bots/{self.bot.user.id}/check", userId=ctx.author.id)).get("voted") == 1
                if voted:
                    weekend = bool((await self._api("/weekend")).get("is_weekend"))
                    if await self._pay_and_dm(ctx.author.id, weekend, ctx.channel):
                        return
            except Exception as e:
                print(f"[vote] Top.gg check failed: {type(e).__name__}: {e}")
        e = discord.Embed(title="Rush Cricket · Vote", color=ACCENT,
                          description="Vote for Rush Cricket on Top.gg and the reward lands in your **DMs** "
                                      "automatically.")
        e.add_field(name="Every vote", value=_reward_line() + f"\n*x{WEEKEND_MULTIPLIER} on weekends*", inline=True)
        e.add_field(name="Streak bonus", value=f"Every **{STREAK_BONUS_EVERY}th** vote in a row\n"
                                                f"{emojis.VOUCHER} **+{STREAK_BONUS_VOUCHERS}** vouchers", inline=True)
        status = "**Ready now**" if time.time() >= ready_at else f"Ready <t:{int(ready_at)}:R>"
        streak = f"  ·  Streak **{last[1]}**" if last and last[1] else ""
        e.add_field(name="Top.gg", value=status + streak, inline=False)
        e.set_footer(text="rcvotereminder = DM reminder when your next vote is ready")
        await ctx.send(embed=e, view=VoteView(self._url()))

    @commands.command(name="rcvotereminder", aliases=["votereminder"])
    async def rcvotereminder(self, ctx: commands.Context):
        on = await asyncio.to_thread(_toggle_reminder, ctx.author.id)
        await ctx.send("Vote reminder **on**. I'll DM you when your next vote is ready." if on
                       else "Vote reminder **off**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(VoteCog(bot))
