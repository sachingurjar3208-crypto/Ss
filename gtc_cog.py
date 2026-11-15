"""Guess the Cricketer: 6 games a day in one channel, played in normal chat.

Owner (only the bot owner):
    admin gtc start          daily games run in THIS channel (09:00, 11:00, 14:00, 19:00, 20:30, 23:00 India time)
    admin gtc remove         daily games stop in this channel (a running game here stops too)
    admin gtc play           start one game right now, in any channel
    admin gtc stop           stop the running game
    admin set gtc @role      role pinged when a game starts (admin set gtc none = no ping)
    admin gtc status         channel, ping role, next game

Players:
    rcgtc                    how it works, next game, your points today
    rcgtc lb                 today's leaderboard (image)
    rcgtc top                all-time leaderboard (image)
    rcgtc notify             get / remove the game ping role

A game = 6 rounds. Each round shows a player photo; the first correct name typed in the channel gets
1 point (surname or the name on the card is enough, "(S1)"-style tags are not needed, one typo is ok).
Hints come after 12 and 22 seconds; after 30 seconds the answer is shown.
Most points wins 5 RCP (max 15 RCP a day from games). After the 11 PM game the day's top scorer
gets 5 RCP more and the day's leaderboard is posted.
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, time as dtime

import discord
from PIL import Image
from discord.ext import commands, tasks

import card_db
import card_image as ci
import economy
import emojis
import gtc
import gtc_image
import sigbox
from admin_cog import owner_only
from security import esc

ROUND_SECONDS = 30
HINT_1 = 12            # seconds: country and role
HINT_2 = 22            # seconds: letters of the name
START_DELAY = 30       # seconds between the ping and round 1
ROUND_GAP = 5          # seconds between rounds
MIN_ACCOUNT_DAYS = 7   # Discord account age needed to score (stops alt accounts)
RECENT_KEEP = 60       # a card shown in the last N rounds is not picked again
GREEN = discord.Color.from_rgb(30, 170, 110)
GOLD = discord.Color.from_rgb(240, 196, 80)

GAME_TIMES = [dtime(h, m, tzinfo=gtc.IST) for h, m in gtc.GAME_HOURS]
LAST_SLOT = gtc.GAME_HOURS[-1]


def _col(card, key, default=None):
    try:
        v = card[key]
        return default if v is None else v
    except (IndexError, KeyError):
        return default


def _role_word(card) -> str:
    r = str(_col(card, "role", "") or "").upper()
    return {"BAT": "Batter", "BOWL": "Bowler", "RBOWL": "Bowler", "AR": "All-rounder", "WK": "Wicket-keeper"}.get(r, "Player")


def _letters(card) -> str:
    """V _ _ _ _   K _ _ _ _  (first letter of each word, the rest hidden)."""
    name = gtc.norm(card["playername"]) or str(card["playername"]).lower()
    out = []
    for w in name.split():
        out.append(w[0].upper() + " " + " ".join("_" for _ in w[1:]))
    return "   ".join(out)


class Round:
    def __init__(self, card, fg):
        self.card, self.fg = card, fg
        self.names = gtc.answer_names(card)
        self.winner: discord.Member | None = None
        self.done = asyncio.Event()


class Game:
    def __init__(self, channel: discord.abc.Messageable, slot: str | None, last_of_day: bool):
        self.channel, self.slot, self.last_of_day = channel, slot, last_of_day
        self.scores: dict[int, int] = {}
        self.first_at: dict[int, float] = {}       # when a player reached their current score (tie-break)
        self.names: dict[int, str] = {}
        self.round: Round | None = None
        self.stop = False
        self.checked: dict[int, bool] = {}        # user -> may score (debuted, old enough account)


class GTCCog(commands.Cog, name="Guess the Cricketer"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.game: Game | None = None
        self.recent: list[str] = []
        self.lock = asyncio.Lock()
        self._admin_cmds: list[tuple[commands.Group, str]] = []

    async def cog_load(self):
        await asyncio.to_thread(gtc.ensure_tables)
        self._attach_admin_commands()
        self.daily.start()

    def cog_unload(self):
        self.daily.cancel()
        for parent, name in self._admin_cmds:
            parent.remove_command(name)
        if self.game:
            self.game.stop = True

    # ── picking cards ────────────────────────────────────────────────────────
    def _pick_cards(self, n: int) -> list[tuple]:
        lo, hi = gtc.OVR_RANGE
        pool = [c for c in card_db.list_all_cards()
                if lo <= int(c["ovr"] or 0) <= hi and _col(c, "foreground_link")
                and c["playername_key"] not in self.recent]
        random.shuffle(pool)
        out, groups = [], set()
        for c in pool:
            group = gtc.norm(_col(c, "player_group") or c["playername"])
            if group in groups:
                continue                      # never the same real player twice in one game
            path = ci._foreground_file(c["foreground_link"])
            if not path.exists():
                continue
            try:
                fg = Image.open(path).convert("RGBA")
            except Exception:
                continue
            if fg.getchannel("A").getbbox() is None:
                continue
            groups.add(group)
            out.append((c, fg))
            if len(out) >= n:
                break
        return out

    # ── the game ─────────────────────────────────────────────────────────────
    async def run_game(self, channel, slot: str | None = None, last_of_day: bool = False) -> str:
        async with self.lock:
            if self.game is not None:
                return "A game is already running."
            game = Game(channel, slot, last_of_day)
            self.game = game
        try:
            picks = await asyncio.to_thread(self._pick_cards, gtc.ROUNDS)
            if len(picks) < gtc.ROUNDS:
                return f"Not enough player photos found ({len(picks)}). Run the foreground cache first."
            role_id = await asyncio.to_thread(gtc.get_setting, "ping_role")
            ping = f"<@&{role_id}> " if role_id else ""
            start = int(time.time()) + START_DELAY
            intro = discord.Embed(
                title="Guess the Cricketer",
                color=GREEN,
                description=(f"{gtc.ROUNDS} rounds start <t:{start}:R>.\n\n"
                             f"{emojis.ARROW} A player photo appears. **Type the name in this channel.**\n"
                             f"{emojis.ARROW} First correct answer gets **1 point**. Surname is enough.\n"
                             f"{emojis.ARROW} Most points after {gtc.ROUNDS} rounds wins **{gtc.GAME_WIN_RCP}** {emojis.RCP}"))
            intro.set_footer(text=f"Up to {gtc.DAILY_RCP_CAP} RCP a day from games · Day's top scorer gets +{gtc.DAILY_TOP_RCP} RCP after the 11 PM game")
            await channel.send(ping, embed=intro,
                               allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False))
            await asyncio.sleep(START_DELAY)

            for i, (card, fg) in enumerate(picks, 1):
                if game.stop:
                    await channel.send("Game stopped.")
                    return "Stopped."
                self.recent.append(card["playername_key"])
                self.recent = self.recent[-RECENT_KEEP:]
                await self._play_round(game, i, card, fg)
                if i < gtc.ROUNDS and not game.stop:
                    await asyncio.sleep(ROUND_GAP)
            await self._finish(game)
            return "Done."
        except Exception as e:
            print(f"[gtc] game failed: {type(e).__name__}: {e}")
            try:
                await channel.send("Something went wrong, the game ended early.")
            except discord.HTTPException:
                pass
            return f"Game failed: {type(e).__name__}"
        finally:
            self.game = None

    async def _play_round(self, game: Game, i: int, card, fg):
        rnd = Round(card, fg)
        img = await asyncio.to_thread(gtc_image.round_image, fg, i, gtc.ROUNDS)
        e = discord.Embed(title=f"Round {i} of {gtc.ROUNDS}: who is this cricketer?", color=GREEN)
        e.set_image(url="attachment://gtc.jpg")
        e.set_footer(text=f"{ROUND_SECONDS} seconds · type the name in chat")
        game.round = rnd
        await game.channel.send(embed=e, file=discord.File(img, filename="gtc.jpg"))
        start = time.monotonic()
        for at, text in ((HINT_1, f"**Hint:** {esc(str(_col(card, 'country', '?')))} · {_role_word(card)}"),
                         (HINT_2, f"**Hint:** `{_letters(card)}`")):
            left = at - (time.monotonic() - start)
            try:
                await asyncio.wait_for(rnd.done.wait(), timeout=max(0.1, left))
                break
            except asyncio.TimeoutError:
                if game.stop:
                    break
                await game.channel.send(text)
        if not rnd.done.is_set() and not game.stop:
            try:
                await asyncio.wait_for(rnd.done.wait(), timeout=max(0.1, ROUND_SECONDS - (time.monotonic() - start)))
            except asyncio.TimeoutError:
                pass
        game.round = None
        name = esc(str(card["playername"]))
        if rnd.winner is None:
            await game.channel.send(f"Time's up! It was **{name}**.")

    async def _finish(self, game: Game):
        board = sorted(game.scores.items(), key=lambda kv: (-kv[1], game.first_at.get(kv[0], 0)))
        winner = board[0][0] if board else None
        day = gtc.today()
        res = await asyncio.to_thread(gtc.record_game, day, game.slot or "manual",
                                      getattr(game.channel, "id", 0), dict(game.scores), winner)
        if winner and res["rcp"]:
            await asyncio.to_thread(sigbox.rcp_change, winner, res["rcp"], "Guess the Cricketer win")
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for n, (uid, pts) in enumerate(board[:5]):
            mark = medals[n] if n < 3 else f"**{n + 1}.**"
            lines.append(f"{mark} <@{uid}>  ·  **{pts}** point{'s' if pts != 1 else ''}")
        e = discord.Embed(title="Game over", color=GOLD)
        if winner:
            prize = (f"wins **{res['rcp']}** {emojis.RCP}" if res["rcp"]
                     else "wins, but already has today's RCP limit")
            e.description = f"{emojis.GIFT} <@{winner}> {prize}!\n\n" + "\n".join(lines)
        else:
            e.description = "Nobody scored this time."
        e.set_footer(text=f"Next game: {self._next_text()}  ·  rcgtc lb for today's leaderboard")
        await game.channel.send(embed=e, allowed_mentions=discord.AllowedMentions(users=[discord.Object(winner)] if winner else []))

        if game.last_of_day:
            top = await asyncio.to_thread(gtc.close_day, day)
            if top:
                await asyncio.to_thread(sigbox.rcp_change, top["user_id"], gtc.DAILY_TOP_RCP, "Guess the Cricketer day top")
            await self._post_board(game.channel, day, top)

    async def _post_board(self, channel, day: str, top: dict | None = None):
        rows = await asyncio.to_thread(gtc.daily_board, day, 10)
        file = await self._board_file("Daily Leaderboard", f"Guess the Cricketer · {self._day_label(day)}", rows,
                                      f"Top scorer gets +{gtc.DAILY_TOP_RCP} RCP after the 11 PM game")
        text = ""
        if top:
            text = f"{emojis.PREMIUM} <@{top['user_id']}> is today's top scorer with **{top['points']}** points and gets **+{gtc.DAILY_TOP_RCP}** {emojis.RCP}!"
        await channel.send(text, file=file, allowed_mentions=discord.AllowedMentions(users=True))

    async def _board_file(self, title: str, subtitle: str, rows: list[dict], footer: str) -> discord.File:
        out = []
        for r in rows:
            uid = int(r["user_id"])
            member = None
            for g in self.bot.guilds:
                member = g.get_member(uid)
                if member:
                    break
            user = member or self.bot.get_user(uid)
            name = (member.display_name if member else (user.name if user else f"User {uid}"))[:24]
            avatar = None
            if user is not None:
                try:
                    avatar = await asyncio.wait_for(user.display_avatar.replace(size=128, format="png").read(), timeout=5)
                except Exception:
                    avatar = None
            out.append({"name": name, "avatar": avatar, "points": int(r["points"]), "wins": int(r.get("wins", 0)),
                        "rcp": int(r.get("rcp", 0))})
        buf = await asyncio.to_thread(gtc_image.board_image, title, subtitle, out, footer)
        return discord.File(buf, filename="gtc_leaderboard.png")

    @staticmethod
    def _day_label(day: str) -> str:
        try:
            return datetime.fromisoformat(day).strftime("%d %b")
        except ValueError:
            return day

    @staticmethod
    def _next_text() -> str:
        return f"<t:{gtc.next_game_ts()}:t>"

    # ── guesses in chat ──────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        game = self.game
        if game is None or message.author.bot or getattr(message.channel, "id", None) != getattr(game.channel, "id", -1):
            return
        rnd = game.round
        if rnd is None or rnd.done.is_set():
            return
        text = message.content.strip()
        if not text or len(text) > 60 or not gtc.is_correct(text, rnd.names):
            return
        uid = message.author.id
        ok = game.checked.get(uid)
        if ok is None:
            age_days = (discord.utils.utcnow() - message.author.created_at).days
            ok = age_days >= MIN_ACCOUNT_DAYS and await asyncio.to_thread(economy.user_exists, uid)
            game.checked[uid] = ok
            if not ok:
                try:
                    await message.reply("You need to debut first (`rcdebut`) to score in Guess the Cricketer.",
                                        mention_author=False, delete_after=8)
                except discord.HTTPException:
                    pass
                return
        if not ok or rnd.done.is_set():
            return
        rnd.winner = message.author
        rnd.done.set()
        game.scores[uid] = game.scores.get(uid, 0) + 1
        game.first_at[uid] = time.time()
        game.names[uid] = message.author.display_name
        try:
            await message.add_reaction(discord.PartialEmoji.from_str(emojis.TICK))
        except Exception:
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass
        pts = game.scores[uid]
        await message.channel.send(
            f"{emojis.TICK} {message.author.mention} got it! It was **{esc(str(rnd.card['playername']))}**  ·  "
            f"{pts} point{'s' if pts != 1 else ''}",
            allowed_mentions=discord.AllowedMentions(users=False))

    # ── daily schedule ───────────────────────────────────────────────────────
    @tasks.loop(time=GAME_TIMES)
    async def daily(self):
        ch_id = await asyncio.to_thread(gtc.get_setting, "channel")
        if not ch_id:
            return
        ch = self.bot.get_channel(int(ch_id))
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(int(ch_id))
            except discord.HTTPException:
                print(f"[gtc] channel {ch_id} not found")
                return
        now = datetime.now(gtc.IST)
        slot = min(gtc.GAME_HOURS, key=lambda hm: abs((hm[0] * 60 + hm[1]) - (now.hour * 60 + now.minute)))
        msg = await self.run_game(ch, f"{slot[0]:02d}:{slot[1]:02d}", last_of_day=(slot == LAST_SLOT))
        if msg not in ("Done.", "Stopped."):
            print(f"[gtc] {slot}: {msg}")

    @daily.before_loop
    async def _ready(self):
        await self.bot.wait_until_ready()

    # ── owner commands (added under the existing `admin` group) ──────────────
    def _attach_admin_commands(self):
        admin = self.bot.get_command("admin")
        if not isinstance(admin, commands.Group):
            print("[gtc] admin group not found; load admin_cog before gtc_cog")
            return
        cog = self

        async def gtc_root(ctx: commands.Context):
            await ctx.send("`admin gtc start` daily games in this channel\n"
                           "`admin gtc remove` stop daily games in this channel\n"
                           "`admin gtc play` start a game now, here\n"
                           "`admin gtc stop` stop the running game\n"
                           "`admin set gtc @role` ping role (`none` to remove)\n"
                           "`admin gtc status`")

        async def gtc_start(ctx: commands.Context):
            await asyncio.to_thread(gtc.set_setting, "channel", str(ctx.channel.id))
            times = ", ".join(f"{h % 12 or 12}{':%02d' % m if m else ''} {'AM' if h < 12 else 'PM'}" for h, m in gtc.GAME_HOURS)
            await ctx.send(f"{emojis.TICK} Guess the Cricketer will run **here** every day at {times} (India time).\n"
                           f"Next game: {cog._next_text()}. Set a ping with `admin set gtc @role`.")

        async def gtc_remove(ctx: commands.Context):
            cur = await asyncio.to_thread(gtc.get_setting, "channel")
            stopped = ""
            if cog.game and getattr(cog.game.channel, "id", None) == ctx.channel.id:
                cog.game.stop = True
                if cog.game.round:
                    cog.game.round.done.set()
                stopped = " The running game here was stopped."
            if cur and int(cur) == ctx.channel.id:
                await asyncio.to_thread(gtc.set_setting, "channel", None)
                await ctx.send(f"Daily Guess the Cricketer games are **off** in this channel.{stopped}")
            else:
                where = f"<#{cur}>" if cur else "no channel"
                await ctx.send(f"Daily games don't run here (they run in {where}).{stopped}")

        async def gtc_play(ctx: commands.Context):
            if cog.game is not None:
                await ctx.send("A game is already running.")
                return
            await ctx.message.add_reaction("✅")
            asyncio.create_task(cog.run_game(ctx.channel, None, False))

        async def gtc_stop(ctx: commands.Context):
            if cog.game is None:
                await ctx.send("No game is running.")
                return
            cog.game.stop = True
            if cog.game.round:
                cog.game.round.done.set()
            await ctx.send("Stopping the game.")

        async def gtc_status(ctx: commands.Context):
            ch = await asyncio.to_thread(gtc.get_setting, "channel")
            role = await asyncio.to_thread(gtc.get_setting, "ping_role")
            live = f"Running in <#{cog.game.channel.id}>" if cog.game else "No game running"
            await ctx.send(f"Channel: {f'<#{ch}>' if ch else 'not set'}\nPing role: {f'<@&{role}>' if role else 'not set'}\n"
                           f"Next game: {cog._next_text()}\n{live}", allowed_mentions=discord.AllowedMentions.none())

        async def set_gtc(ctx: commands.Context, role: str = ""):
            if role.lower() in ("none", "off", "remove", ""):
                await asyncio.to_thread(gtc.set_setting, "ping_role", None)
                await ctx.send("Guess the Cricketer ping removed." if role else "Usage: `admin set gtc @role` or `admin set gtc none`")
                return
            try:
                r = await commands.RoleConverter().convert(ctx, role)
            except commands.BadArgument:
                await ctx.send("Role not found. Use `admin set gtc @role`.")
                return
            await asyncio.to_thread(gtc.set_setting, "ping_role", str(r.id))
            await ctx.send(f"{emojis.TICK} {r.mention} will be pinged when a game starts. Players get it with `rcgtc notify`.",
                           allowed_mentions=discord.AllowedMentions.none())

        group = commands.Group(owner_only()(gtc_root), name="gtc", invoke_without_command=True)
        for fn, name in ((gtc_start, "start"), (gtc_remove, "remove"), (gtc_play, "play"), (gtc_stop, "stop"),
                         (gtc_status, "status")):
            group.add_command(commands.Command(owner_only()(fn), name=name))
        admin.add_command(group)
        self._admin_cmds.append((admin, "gtc"))

        set_group = admin.get_command("set")
        if set_group is None:
            async def set_root(ctx: commands.Context):
                await ctx.send("`admin set gtc @role`")
            set_group = commands.Group(owner_only()(set_root), name="set", invoke_without_command=True)
            admin.add_command(set_group)
            self._admin_cmds.append((admin, "set"))          # whole group goes on unload
        else:
            self._admin_cmds.append((set_group, "gtc"))
        set_group.add_command(commands.Command(owner_only()(set_gtc), name="gtc"))

    # ── player commands ──────────────────────────────────────────────────────
    @commands.group(name="rcgtc", aliases=["gtc"], invoke_without_command=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcgtc(self, ctx: commands.Context):
        st = await asyncio.to_thread(gtc.user_stats, ctx.author.id, gtc.today())
        ch = await asyncio.to_thread(gtc.get_setting, "channel")
        t, a = st["today"], st["all"]
        e = discord.Embed(title="Guess the Cricketer", color=GREEN)
        e.description = (
            f"{emojis.ARROW} 6 games a day: **9 AM, 11 AM, 2 PM, 7 PM, 8:30 PM, 11 PM** (India time)"
            + (f" in <#{ch}>" if ch else "") + "\n"
            f"{emojis.ARROW} {gtc.ROUNDS} rounds a game. A player photo appears, **type the name in chat**. "
            f"First correct answer gets 1 point. Surname is enough.\n"
            f"{emojis.ARROW} Most points wins **{gtc.GAME_WIN_RCP}** {emojis.RCP} (max **{gtc.DAILY_RCP_CAP}** a day)\n"
            f"{emojis.GIFT} Day's top scorer gets **+{gtc.DAILY_TOP_RCP}** {emojis.RCP} after the 11 PM game\n\n"
            f"**Next game:** <t:{gtc.next_game_ts()}:R>")
        rank = f" · rank **{st['rank']}**" if st["rank"] else ""
        e.add_field(name="Today", value=f"**{t['points']}** points · **{t['wins']}** wins · **{t['rcp']}** {emojis.RCP}{rank}", inline=False)
        e.add_field(name="All time", value=f"**{a['points']}** points · **{a['wins']}** wins · **{a['days_won']}** days won", inline=False)
        e.set_footer(text="rcgtc lb · rcgtc top · rcgtc notify")
        await ctx.send(embed=e)

    @rcgtc.command(name="lb", aliases=["leaderboard", "today"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rcgtc_lb(self, ctx: commands.Context):
        async with ctx.typing():
            day = gtc.today()
            rows = await asyncio.to_thread(gtc.daily_board, day, 10)
            file = await self._board_file("Daily Leaderboard", f"Guess the Cricketer · {self._day_label(day)}", rows,
                                          f"Top scorer gets +{gtc.DAILY_TOP_RCP} RCP after the 11 PM game")
        await ctx.send(file=file)

    @rcgtc.command(name="top", aliases=["alltime", "all"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rcgtc_top(self, ctx: commands.Context):
        async with ctx.typing():
            rows = await asyncio.to_thread(gtc.alltime_board, 10)
            file = await self._board_file("All Time Leaderboard", "Guess the Cricketer", rows, "Points from every game")
        await ctx.send(file=file)

    @rcgtc.command(name="notify", aliases=["ping"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcgtc_notify(self, ctx: commands.Context):
        role_id = await asyncio.to_thread(gtc.get_setting, "ping_role")
        role = ctx.guild.get_role(int(role_id)) if (role_id and ctx.guild) else None
        if role is None:
            await ctx.send("The Guess the Cricketer ping isn't set up in this server yet.")
            return
        try:
            if role in ctx.author.roles:
                await ctx.author.remove_roles(role, reason="rcgtc notify off")
                await ctx.send("Guess the Cricketer pings **off**.")
            else:
                await ctx.author.add_roles(role, reason="rcgtc notify on")
                await ctx.send(f"{emojis.TICK} Guess the Cricketer pings **on**. You'll be pinged before every game.")
        except discord.Forbidden:
            await ctx.send("I can't give that role. The bot needs Manage Roles and must be above the role.")


async def setup(bot: commands.Bot):
    await bot.add_cog(GTCCog(bot))
