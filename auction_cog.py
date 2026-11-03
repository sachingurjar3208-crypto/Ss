"""Daily Live Auction (chat bidding).

Every day at 21:00 (India time) in AUCTION_CHANNEL_ID:
  - the bot picks 2 random 85-89 cards and 3 random 90-99 cards (no event / signature / EOAE cards)
  - pings the auction role, shows the 5 lots, and starts the first lot a minute later
  - each lot (2 minutes): base price = the card's sell price. People type in the channel:
        bid 2400000   bid 24L   bid 2.4m   bid 1.2cr   bid 500k   bid 24,00,000
    Every bid holds the coins at once; outbid = coins back. A bid in the last 10 seconds
    puts the timer back to 10 seconds. Highest bid when the timer ends wins the card.

Users:  rcauction  |  rcauction notify  |  rcauction history  |  rcauction won
Admin:  /auction start  /auction cancel  /auction pingrole  /auction status
"""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

import auction
import auction_image
import card_db
import squad_logic as sl
from bot_guard import ADMIN_GUILD_ID, is_staff

AUCTION_CHANNEL_ID = int(os.getenv("AUCTION_CHANNEL_ID", "1567238927709110373"))
IST = timezone(timedelta(hours=5, minutes=30))
AUCTION_TIME = dtime(21, 0, tzinfo=IST)            # every day 9 PM India time
LOT_PLAN = [((85, 89), 2), ((90, 99), 3)]          # (ovr range, how many cards)
LOT_SECONDS = 120           # time for each lot (2 minutes)
ANTI_SNIPE = 10             # a bid in the last N seconds sets the timer back to N seconds
FIRST_LOT_DELAY = 60        # seconds between the ping and lot 1
LOT_GAP = 8                 # seconds between lots
REMIND_EVERY = 12           # after this many bids, the bot repeats the top bid (the lot message scrolls up)
MIN_ACCOUNT_DAYS = 30       # Discord account age needed to bid (stops alt accounts)
MIN_MEMBER_DAYS = 3         # days in the server needed to bid
GOLD = discord.Color.from_rgb(240, 198, 104)

_rng = secrets.SystemRandom()
_BID_RE = re.compile(r"^bid\s+([\d.,]+)\s*(k|l|lac|lakh|lakhs|m|mil|cr|crore|crores)?\s*$", re.I)
_MULT = {None: 1, "k": 1_000, "l": 100_000, "lac": 100_000, "lakh": 100_000, "lakhs": 100_000,
         "m": 1_000_000, "mil": 1_000_000, "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000}


def parse_bid(text: str) -> int | None:
    m = _BID_RE.match(text.strip())
    if not m:
        return None
    num = m.group(1).replace(",", "")
    try:
        val = float(num) * _MULT[(m.group(2) or "").lower() or None]
    except (ValueError, KeyError):
        return None
    return int(val) if 0 < val < 10**12 else None


def short(n: int) -> str:
    if n >= 10_000_000:
        return f"{n / 10_000_000:.2f}".rstrip("0").rstrip(".") + "Cr"
    if n >= 100_000:
        return f"{n / 100_000:.2f}".rstrip("0").rstrip(".") + "L"
    if n >= 1_000:
        return f"{n / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(n)


def money(n: int) -> str:
    return f"{n:,} ({short(n)})"


def _col(row, key, default=None):
    try:
        return row[key] if key in row.keys() else default
    except Exception:
        return default


def pick_lots() -> list:
    """2 random 85-89 + 3 random 90-99 cards, no event / signature / EOAE card. Lowest OVR first."""
    cards = []
    for c in card_db.list_obtainable_cards():
        if _col(c, "event_key"):
            continue
        if str(_col(c, "s2_theme", "") or "").strip().lower() in ("signature", "eoae"):
            continue
        cards.append(c)
    out = []
    for (lo, hi), n in LOT_PLAN:
        pool = [c for c in cards if lo <= int(c["ovr"]) <= hi]
        out += _rng.sample(pool, min(n, len(pool)))
    return sorted(out, key=lambda c: int(c["ovr"]))


class Lot:
    def __init__(self, run_id: int, idx: int, total: int, card):
        self.run_id, self.idx, self.total, self.card = run_id, idx, total, card
        self.key = card["playername_key"]
        self.base = sl.sell_value(card)
        self.end = time.time() + LOT_SECONDS
        self.top: tuple[int, int, str] | None = None     # (user_id, amount, display name)
        self.message: discord.Message | None = None
        self.dirty = False
        self.bids_since_remind = 0
        self.reminded = False
        self.bids = 0

    def left(self) -> int:
        return max(0, int(round(self.end - time.time())))

    def embed(self, final: str | None = None) -> discord.Embed:
        c = self.card
        top = f"{money(self.top[1])}  by {self.top[2]}" if self.top else "no bid yet"
        nxt = auction.min_next_bid(self.top[1] if self.top else None, self.base)
        lines = [
            f"{c['playername'].upper()}  |  {c['ovr']} OVR",
            "",
            f"Base price   {money(self.base)}",
            f"Top bid      {top}",
        ]
        if final:
            lines.append(f"Result       {final}")
        else:
            lines.append(f"Next min     {money(nxt)}")
            lines.append(f"Time left    {self.left() // 60}:{self.left() % 60:02d}")
        e = discord.Embed(title=f"LIVE AUCTION  |  LOT {self.idx} / {self.total}",
                          description="```\n" + "\n".join(lines).replace("`", "'") + "\n```", color=GOLD)
        if self.has_image:
            e.set_image(url="attachment://lot.png")
        e.set_footer(text="Type in this channel:  bid 30L   bid 2.5m   bid 1.2cr   bid 500k" if not final else "Auction")
        return e

    @property
    def has_image(self) -> bool:
        p = _col(self.card, "image_path")
        return bool(p) and Path(p).is_file()


class AuctionCog(commands.Cog, name="Auction"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lot: Lot | None = None
        self.lock = asyncio.Lock()
        self.running = False
        self.cancel = False

    async def cog_load(self):
        try:
            await asyncio.to_thread(auction.ensure_tables)
            n = await asyncio.to_thread(auction.recover)
            if n:
                print(f"[auction] restart: refunded {n} held bid(s)")
        except Exception as e:
            print(f"[auction] setup failed: {e}")
        self.daily.start()

    async def cog_unload(self):
        self.daily.cancel()

    # ── schedule ─────────────────────────────────────────────────────────────
    @tasks.loop(time=AUCTION_TIME)
    async def daily(self):
        day = datetime.now(IST).date().isoformat()
        try:
            await self.run_auction(day)
        except Exception as e:
            print(f"[auction] daily run failed: {type(e).__name__}: {e}")

    @daily.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def _channel(self):
        ch = self.bot.get_channel(AUCTION_CHANNEL_ID)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(AUCTION_CHANNEL_ID)
            except discord.HTTPException:
                return None
        return ch

    async def run_auction(self, day: str | None) -> str:
        if self.running:
            return "An auction is already running."
        ch = await self._channel()
        if ch is None:
            return f"Auction channel {AUCTION_CHANNEL_ID} not found (wrong id, or the bot can't see it)."
        cards = await asyncio.to_thread(pick_lots)
        if not cards:
            return "No 85-99 cards (non-event) found for the auction."
        run_id = await asyncio.to_thread(auction.start_run, day)
        if run_id is None:
            return "Today's auction already ran."
        self.running, self.cancel = True, False
        sold = []
        try:
            role_id = await asyncio.to_thread(auction.get_setting, "ping_role")
            ping = f"<@&{role_id}> " if role_id else ""
            rows = [f"{i}. {c['playername'][:22]:<22} {c['ovr']} OVR   base {short(sl.sell_value(c))}"
                    for i, c in enumerate(cards, 1)]
            lineup = None
            try:
                lineup = await asyncio.to_thread(
                    auction_image.build_lineup,
                    [(_col(c, "image_path"), c["playername"], int(c["ovr"]), sl.sell_value(c)) for c in cards],
                    max(1, LOT_SECONDS // 60))
            except Exception as e:
                print(f"[auction] lineup image failed: {e}")
            extra = {"file": discord.File(lineup, filename="auction.png")} if lineup else {}
            await ch.send(
                f"{ping}**LIVE AUCTION starts in {FIRST_LOT_DELAY} seconds**\n"
                "```\n" + "\n".join(rows) + "\n```\n"
                "How to bid: type `bid 30L` / `bid 2.5m` / `bid 1.2cr` in this channel.\n"
                f"Coins are held when you bid, and come back if someone bids higher. "
                f"Max {auction.MAX_WINS_PER_RUN} wins per person.",
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
                **extra,
            )
            if await self._sleep(FIRST_LOT_DELAY):
                return "Cancelled."
            for i, card in enumerate(cards, 1):
                res = await self._run_lot(ch, Lot(run_id, i, len(cards), card))
                if res:
                    sold.append((card, res))
                if self.cancel:
                    break
                if i < len(cards) and await self._sleep(LOT_GAP):
                    break
            await self._summary(ch, sold, cancelled=self.cancel)
            return "Done."
        finally:
            await asyncio.to_thread(auction.end_run, run_id, "cancelled" if self.cancel else "done")
            self.running, self.lot = False, None

    async def _sleep(self, seconds: float) -> bool:
        """Sleep, but wake up on cancel. True = cancelled."""
        end = time.time() + seconds
        while time.time() < end:
            if self.cancel:
                return True
            await asyncio.sleep(min(1, end - time.time()))
        return self.cancel

    async def _run_lot(self, ch, lot: Lot):
        kwargs = {}
        if lot.has_image:
            kwargs["file"] = discord.File(lot.card["image_path"], filename="lot.png")
        lot.message = await ch.send(embed=lot.embed(), **kwargs)
        lot.end = time.time() + LOT_SECONDS
        async with self.lock:
            self.lot = lot
        last_edit = 0.0
        while not self.cancel:
            now = time.time()
            if now >= lot.end:
                break
            left = lot.end - now
            if left <= ANTI_SNIPE and not lot.reminded and lot.top:
                lot.reminded = True
                await ch.send(f"`{lot.card['playername']}  |  Top: {short(lot.top[1])} by {lot.top[2]}  |  {int(left)}s left`",
                              allowed_mentions=discord.AllowedMentions.none())
            if lot.dirty or now - last_edit >= 5:
                lot.dirty = False
                last_edit = now
                try:
                    await lot.message.edit(embed=lot.embed())
                except discord.HTTPException:
                    pass
            await asyncio.sleep(1)
        async with self.lock:            # no bid can slip in after this point
            self.lot = None
        if self.cancel:
            await asyncio.to_thread(auction.refund_lot, lot.run_id, lot.idx)
            try:
                await lot.message.edit(embed=lot.embed(final="CANCELLED, coins returned"))
            except discord.HTTPException:
                pass
            return None
        res = await asyncio.to_thread(auction.settle_lot, lot.run_id, lot.idx, lot.key)
        name = lot.card["playername"]
        if res:
            final = f"SOLD for {short(res['amount'])}"
            text = (f"**SOLD!** {name} ({lot.card['ovr']} OVR) to <@{res['user_id']}> for **{res['amount']:,}** coins.")
            if res["pending"]:
                text += " Squad full or duplicate: decide with `rcpending`."
        else:
            final = "UNSOLD"
            text = f"**UNSOLD.** Nobody bid for {name}."
        try:
            await lot.message.edit(embed=lot.embed(final=final))
        except discord.HTTPException:
            pass
        await ch.send(text, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        return res

    async def _summary(self, ch, sold, cancelled: bool):
        if not sold:
            await ch.send("Auction over. Nothing was sold today." + (" (cancelled)" if cancelled else "")
                          + " Next auction: tomorrow 9 PM.")
            return
        rows = []
        for card, res in sold:
            member = ch.guild.get_member(res["user_id"]) if getattr(ch, "guild", None) else None
            who = member.display_name if member else str(res["user_id"])
            rows.append(f"{card['playername'][:20]:<20} {card['ovr']}  {short(res['amount']):>8}  {who[:16]}")
        best = max(sold, key=lambda x: x[1]["amount"])
        await ch.send(
            "**AUCTION RESULTS**" + (" (cancelled early)" if cancelled else "") + "\n"
            "```\n" + "\n".join(rows) + "\n```\n"
            f"Most expensive: **{best[0]['playername']}** for **{best[1]['amount']:,}** coins. Next auction: tomorrow 9 PM.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ── chat bids ────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id != AUCTION_CHANNEL_ID:
            return
        if not message.content.lower().startswith("bid"):
            return
        lot = self.lot
        if lot is None:
            if self.running:
                await message.reply("Wait for the next lot.", delete_after=5, mention_author=False)
            return
        amount = parse_bid(message.content)
        if amount is None:
            await message.reply("Write it like `bid 30L`, `bid 2.5m`, `bid 1.2cr` or `bid 2500000`.",
                                delete_after=6, mention_author=False)
            return
        member = message.author
        now = datetime.now(timezone.utc)
        if (now - member.created_at).days < MIN_ACCOUNT_DAYS:
            await message.reply(f"Your Discord account must be at least {MIN_ACCOUNT_DAYS} days old to bid.",
                                delete_after=6, mention_author=False)
            return
        joined = getattr(member, "joined_at", None)
        if joined and (now - joined).days < MIN_MEMBER_DAYS:
            await message.reply(f"You must be in this server for {MIN_MEMBER_DAYS} days to bid.",
                                delete_after=6, mention_author=False)
            return
        async with self.lock:
            if self.lot is not lot or time.time() >= lot.end:
                return
            res = await asyncio.to_thread(auction.place_bid, lot.run_id, lot.idx, lot.key, member.id, amount, lot.base)
            if res["ok"]:
                lot.top = (member.id, amount, member.display_name[:20])
                lot.dirty = True
                lot.bids += 1
                lot.bids_since_remind += 1
                if lot.end - time.time() < ANTI_SNIPE:
                    lot.end = time.time() + ANTI_SNIPE
                    lot.reminded = False
        if not res["ok"]:
            why = {
                "no_user": "You haven't debuted yet. Type `rcdebut` first.",
                "banned": "You can't bid.",
                "already_top": "You are already the top bidder.",
                "max_wins": f"You already won {auction.MAX_WINS_PER_RUN} lots today.",
                "low": f"Too low. Minimum bid now: **{res.get('min', 0):,}** ({short(res.get('min', 0))}).",
                "no_coins": f"Not enough coins. You have **{res.get('balance', 0):,}**.",
            }.get(res["reason"], "Bid not accepted.")
            await message.reply(why, delete_after=6, mention_author=False)
            return
        try:
            await message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
        except discord.HTTPException:
            pass
        if lot.bids_since_remind >= REMIND_EVERY:
            lot.bids_since_remind = 0
            await message.channel.send(
                f"`{lot.card['playername']}  |  Top: {short(amount)} by {member.display_name[:20]}  |  {lot.left()}s left`",
                allowed_mentions=discord.AllowedMentions.none())

    # ── user commands ────────────────────────────────────────────────────────
    @commands.group(name="rcauction", invoke_without_command=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcauction(self, ctx: commands.Context):
        lot = self.lot
        if lot:
            top = f"{short(lot.top[1])} by {lot.top[2]}" if lot.top else "no bid yet"
            await ctx.send(f"Live now in <#{AUCTION_CHANNEL_ID}>: **{lot.card['playername']}** ({lot.card['ovr']} OVR), "
                           f"top {top}, {lot.left()}s left.", allowed_mentions=discord.AllowedMentions.none())
            return
        now = datetime.now(IST)
        nxt = now.replace(hour=AUCTION_TIME.hour, minute=AUCTION_TIME.minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await ctx.send(
            f"**Live Auction** every day at **9 PM** in <#{AUCTION_CHANNEL_ID}>: 2 players (85-89) and 3 players (90-99).\n"
            f"Next one <t:{int(nxt.timestamp())}:R>. Base price = sell price. Bid by typing `bid 30L` in the channel.\n"
            "`rcauction notify` = get pinged  |  `rcauction history` = last results  |  `rcauction won` = your wins",
            allowed_mentions=discord.AllowedMentions.none())

    @rcauction.command(name="notify")
    async def rcauction_notify(self, ctx: commands.Context):
        role_id = await asyncio.to_thread(auction.get_setting, "ping_role")
        role = ctx.guild.get_role(int(role_id)) if (role_id and ctx.guild) else None
        if role is None:
            await ctx.send("The auction ping role isn't set up yet.")
            return
        try:
            if role in ctx.author.roles:
                await ctx.author.remove_roles(role, reason="rcauction notify off")
                await ctx.send("Auction pings **off**.")
            else:
                await ctx.author.add_roles(role, reason="rcauction notify on")
                await ctx.send("Auction pings **on**. You'll be pinged at 9 PM.")
        except discord.Forbidden:
            await ctx.send("I can't give that role. (The bot needs Manage Roles, and its role must be above the auction role.)")

    @rcauction.command(name="history")
    async def rcauction_history(self, ctx: commands.Context):
        rows = await asyncio.to_thread(auction.last_results, 10)
        if not rows:
            await ctx.send("No auction results yet.")
            return
        out = []
        for r in rows:
            c = card_db.get_card(r["player_key"])
            name = c["playername"] if c else r["player_key"]
            m = ctx.guild.get_member(int(r["user_id"])) if ctx.guild else None
            out.append(f"{name[:20]:<20} {short(int(r['amount'])):>8}  {(m.display_name if m else r['user_id'])}")
        await ctx.send("**Last auction results**\n```\n" + "\n".join(out) + "\n```",
                       allowed_mentions=discord.AllowedMentions.none())

    @rcauction.command(name="won")
    async def rcauction_won(self, ctx: commands.Context):
        rows = await asyncio.to_thread(auction.user_wins, ctx.author.id, 15)
        if not rows:
            await ctx.send("You haven't won anything in an auction yet.")
            return
        out = []
        for r in rows:
            c = card_db.get_card(r["player_key"])
            out.append(f"{(c['playername'] if c else r['player_key'])[:20]:<20} {short(int(r['amount'])):>8}")
        await ctx.send("**Your auction wins**\n```\n" + "\n".join(out) + "\n```")

    # ── /auction (admin) ─────────────────────────────────────────────────────
    group = app_commands.Group(name="auction", description="Live Auction (admin)",
                               guild_only=True, default_permissions=discord.Permissions())
    app_commands.guilds(ADMIN_GUILD_ID)(group)

    @group.command(name="start", description="Start an auction right now (extra one, not the daily)")
    @app_commands.check(lambda i: is_staff(i.user))
    async def a_start(self, interaction: discord.Interaction):
        if self.running:
            await interaction.response.send_message("An auction is already running.", ephemeral=True)
            return
        await interaction.response.send_message(f"Starting in <#{AUCTION_CHANNEL_ID}>.", ephemeral=True)

        async def go():
            try:
                msg = await self.run_auction(None)
            except Exception as e:
                msg = f"Auction failed: {type(e).__name__}: {e}"
                print(f"[auction] {msg}")
            if msg not in ("Done.", "Cancelled."):
                try:
                    await interaction.followup.send(msg, ephemeral=True)
                except discord.HTTPException:
                    pass
        asyncio.create_task(go())

    @group.command(name="cancel", description="Stop the running auction (the live lot's coins go back)")
    @app_commands.check(lambda i: is_staff(i.user))
    async def a_cancel(self, interaction: discord.Interaction):
        if not self.running:
            await interaction.response.send_message("No auction is running.", ephemeral=True)
            return
        self.cancel = True
        await interaction.response.send_message("Stopping. Coins of the live lot will be returned.", ephemeral=True)

    @group.command(name="pingrole", description="Role pinged when the auction starts (empty = no ping)")
    @app_commands.describe(role="The role to ping, e.g. @Auction")
    @app_commands.check(lambda i: is_staff(i.user))
    async def a_pingrole(self, interaction: discord.Interaction, role: discord.Role | None = None):
        await asyncio.to_thread(auction.set_setting, "ping_role", str(role.id) if role else None)
        await interaction.response.send_message(
            f"Auction ping role: {role.mention}." if role else "Auction ping removed.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="status", description="Auction channel, time, ping role and the live lot")
    @app_commands.check(lambda i: is_staff(i.user))
    async def a_status(self, interaction: discord.Interaction):
        role_id = await asyncio.to_thread(auction.get_setting, "ping_role")
        lot = self.lot
        live = (f"Live: lot {lot.idx}/{lot.total} {lot.card['playername']}, top "
                f"{short(lot.top[1]) if lot.top else 'none'}, {lot.left()}s left") if lot else "Nothing live."
        await interaction.response.send_message(
            f"Channel: <#{AUCTION_CHANNEL_ID}>\nTime: every day 21:00 IST\n"
            f"Ping role: {('<@&' + role_id + '>') if role_id else 'not set'}\n{live}",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionCog(bot))
