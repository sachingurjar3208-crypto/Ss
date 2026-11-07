"""Wild cricketer spawns (BallsDex-style, made for Rush Cricket).

How a spawn happens (per server, only in the channel set with /spawn channel):
  * every human message in the server adds points (counted at most once per 10 s, so spam is useless)
  * the points are halved for: very short messages (< 5 letters), fewer than 4 different people
    in the last 100 messages, or one person writing more than 40% of them
  * a little time bonus every minute, so a quiet server still gets one now and then
  * when the points reach a random target -> a card spawns, never sooner than MIN_GAP after the last
    one, never more than MAX_PER_DAY a day, and never while the last one is still uncaught
The spawn: "A wild cricketer appeared!" + the card with the NAME BLURRED + a "Catch me" button.
Button -> type the name. Right name = the card (squad full / duplicate -> the normal keep/sell/replace).
Event cards: "Virat Kohli (S1)" is caught with just "Virat Kohli" (the short card name works too).
Every SPAWN_VOUCHER_EVERY (30) catches = 1 Draft Voucher.
Unob cards (obtainable off) and cards of a disabled event never spawn. /spawn card turns any card off/on.

Slash (every server, everyone sees it):  /spawn channel   (needs Manage Server)
Slash (hidden, only your server):        /spawnadmin toggle  /spawnadmin status   (owner + admin role)
                                         /spawnadmin card <player> <on/off>  /spawnadmin blocked   (owner only)
                                         /admin cricketer spawn [playername] [count] [ovr] [event] (owner only)
Users:                       rcspawns
"""
from __future__ import annotations

import asyncio
import io
import re
import secrets
import string
import time
from collections import deque
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

import card_cache
import card_db
import economy
import spawn_blur
import spawn_db
from bot_guard import ADMIN_GUILD_ID, OWNER_ID, MaintenanceActive, is_staff

# ── settings (change freely) ──────────────────────────────────────────────────
SPAWN_POINTS = (30, 45)      # random target of message points for each spawn
MIN_GAP = 600                # seconds: at least 10 minutes between two spawns
MAX_PER_DAY = 30             # spawns per server per day (India time)
SPAWN_TIMEOUT = 300          # seconds the "Catch me" button stays (5 min), then the cricketer runs away
COUNT_EVERY = 10             # a server's points go up at most once per 10 seconds
HEADER = "**A wild cricketer appeared!**"

# OVR odds. The numbers are WEIGHTS (they add up to ~416, not 100), so the real chance of a band is
# weight / total. Bands with no spawnable card are skipped and the rest share their chance.
SPAWN_ODDS: list[tuple[int, int, float]] = [
    (99, 99, 0.001), (98, 98, 0.015), (97, 97, 0.050), (96, 96, 0.5), (95, 95, 1), (94, 94, 2),
    (93, 93, 3), (92, 92, 4), (91, 91, 5), (90, 90, 6), (89, 89, 7), (88, 88, 8), (87, 87, 10),
    (86, 86, 11), (85, 85, 13), (84, 84, 15), (83, 83, 16), (82, 82, 18), (81, 81, 20), (80, 80, 22),
    (79, 79, 24), (78, 78, 26),
    (72, 77, 40),            # 72-77: one random card of these OVRs
    (60, 71, 80),            # 60-71
    (0, 59, 85),             # 50-59 (and anything lower)
]

_rng = secrets.SystemRandom()


# ── helpers ───────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    t = str(text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(t.split())


def catch_names(card) -> set[str]:
    """Names that catch this card: full name, name without "(S1)"-style tags, player group, short name."""
    full = str(card["playername"])
    names = {_norm(full), _norm(re.sub(r"\([^)]*\)", " ", full))}
    group = card_db._rget(card, "player_group", None)
    if group:
        names.add(_norm(group))
    short = _norm(card_db._rget(card, "displayname", ""))
    if len(short) >= 3:
        names.add(short)
    return {n for n in names if n}


def pick_card(exclude: set[str] | None = None):
    """Random spawnable card using SPAWN_ODDS. None if there are none."""
    blocked = spawn_db.blocked_keys() | (exclude or set())
    pool = [c for c in card_db.list_obtainable_cards() if c["playername_key"] not in blocked]
    if not pool:
        return None
    bands, weights = [], []
    for lo, hi, w in SPAWN_ODDS:
        cards = [c for c in pool if lo <= int(c["ovr"]) <= hi]
        if cards and w > 0:
            bands.append(cards)
            weights.append(w)
    if not bands:
        return _rng.choice(pool)
    return _rng.choice(_rng.choices(bands, weights=weights, k=1)[0])


def parse_ovrs(text: str) -> set[int] | None:
    """ "88, 91" -> {88, 91};  "85-89" -> {85..89};  mixed works too. None if nothing valid."""
    out: set[int] = set()
    for part in re.split(r"[,\s]+", str(text or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", part)
        if m:
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            out.update(range(a, b + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            return None
    out = {o for o in out if 1 <= o <= 99}
    return out or None


def admin_pool(ovrs: set[int] | None, event_key: str | None) -> list:
    """Cards for /admin cricketer spawn: never unob, never turned-off cards."""
    blocked = spawn_db.blocked_keys()
    pool = [c for c in card_db.list_obtainable_cards() if c["playername_key"] not in blocked]
    if ovrs:
        pool = [c for c in pool if int(c["ovr"]) in ovrs]
    if event_key == "*":
        pool = [c for c in pool if card_db._rget(c, "event_key", None)]
    elif event_key:
        pool = [c for c in pool if str(card_db._rget(c, "event_key", "") or "").lower() == event_key]
    return pool


def pick_from(pool: list, tried: set[str] | None = None):
    """Random card from `pool`: first a random OVR (each OVR asked for has the same chance), then a card."""
    cards = [c for c in pool if c["playername_key"] not in (tried or set())]
    if not cards:
        return None
    by_ovr: dict[int, list] = {}
    for c in cards:
        by_ovr.setdefault(int(c["ovr"]), []).append(c)
    return _rng.choice(by_ovr[_rng.choice(list(by_ovr))])


def odds_table() -> str:
    total = sum(w for _, _, w in SPAWN_ODDS)
    rows = []
    for lo, hi, w in SPAWN_ODDS:
        name = str(lo) if lo == hi else (f"{lo}-{hi}" if lo else f"<= {hi}")
        rows.append(f"{name:<7} {w / total * 100:8.4f}%")
    return "\n".join(rows)


def _random_filename() -> str:
    return "nt_" + "".join(_rng.choice(string.ascii_letters) for _ in range(15)) + ".png"


@dataclass
class _Counter:
    start: float = field(default_factory=time.time)
    points: float = SPAWN_POINTS[0] / 2          # start half-way so a restart doesn't mean a long wait
    target: int = field(default_factory=lambda: _rng.randint(*SPAWN_POINTS))
    last_count: float = 0.0
    cache: deque = field(default_factory=lambda: deque(maxlen=100))   # (author_id, short?)

    def reset(self):
        self.start = time.time()
        self.points = 1.0
        self.target = _rng.randint(*SPAWN_POINTS)


def _time_mult(members: int) -> float:
    if members < 5:
        return 0.1
    if members < 100:
        return 0.8
    if members < 1000:
        return 0.5
    return 0.2


# ── catch UI ──────────────────────────────────────────────────────────────────

class CatchModal(discord.ui.Modal, title="Catch this cricketer!"):
    name = discord.ui.TextInput(label="Player name", placeholder="Type the player's name", max_length=60)

    def __init__(self, view: "SpawnView"):
        super().__init__(timeout=SPAWN_TIMEOUT)
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        await self.view.try_catch(interaction, str(self.name.value))


class SpawnView(discord.ui.View):
    def __init__(self, cog: "SpawnCog", guild_id: int, card, log_id: int):
        super().__init__(timeout=SPAWN_TIMEOUT)
        self.cog, self.guild_id, self.card, self.log_id = cog, guild_id, card, log_id
        self.names = catch_names(card)
        self.caught_by: int | None = None
        self.lock = asyncio.Lock()
        self.message: discord.Message | None = None

    @discord.ui.button(label="Catch me", style=discord.ButtonStyle.success)
    async def catch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.caught_by is not None or self.is_finished():
            await interaction.response.send_message("This cricketer was already caught.", ephemeral=True)
            return
        row = await asyncio.to_thread(economy.get_user, interaction.user.id)
        if row is None:
            await interaction.response.send_message("You haven't debuted yet. Type `rcdebut` first.", ephemeral=True)
            return
        if int(row["banned"] or 0):
            await interaction.response.send_message("You can't catch cricketers.", ephemeral=True)
            return
        await interaction.response.send_modal(CatchModal(self))

    async def try_catch(self, interaction: discord.Interaction, guess: str):
        async with self.lock:
            if self.caught_by is not None:
                await interaction.response.send_message(f"Too late, <@{self.caught_by}> already caught it.",
                                                        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
                return
            if _norm(guess) not in self.names:
                shown = discord.utils.escape_markdown(guess.strip()[:60]) or "nothing"
                await interaction.response.send_message(f"{interaction.user.mention} Wrong name: `{shown}`",
                                                        allowed_mentions=discord.AllowedMentions(users=False))
                return
            res = await asyncio.to_thread(spawn_db.catch, interaction.user.id, self.card["playername_key"], self.log_id)
            if not res["ok"]:
                msg = {"missing": "You haven't debuted yet. Type `rcdebut` first.",
                       "banned": "You can't catch cricketers."}.get(res["reason"], "This card can't be caught right now.")
                await interaction.response.send_message(msg, ephemeral=True)
                return
            self.caught_by = interaction.user.id
            self.stop()
        self.cog.drop(self)

        card = self.card
        left = spawn_db.SPAWN_VOUCHER_EVERY - res["catches"] % spawn_db.SPAWN_VOUCHER_EVERY
        lines = [f"{interaction.user.mention} caught **{discord.utils.escape_markdown(card['playername'])}**!  "
                 f"`{card['ovr']} OVR`"]
        if res["voucher"]:
            lines.append(f"**+1 Draft Voucher** for `{res['catches']}` catches!")
        lines.append(f"`Catches: {res['catches']}  |  Next Draft Voucher in {left}`")
        if res["pending"]:
            lines.append("Squad full or duplicate: choose below to keep, sell or replace.")
        await interaction.response.send_message("\n".join(lines))

        # the spawn message: real card (no blur) + button off
        self.catch_btn.disabled = True
        self.catch_btn.label = f"Caught by {interaction.user.display_name}"[:80]
        self.catch_btn.style = discord.ButtonStyle.secondary
        if self.message is not None:
            try:
                f = await card_cache.card_file(card["image_path"])
                if f is not None:
                    await self.message.edit(attachments=[f], view=self)
                else:
                    await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        if res["pending"]:
            try:
                from economy_cog import send_next_pending
                await send_next_pending(interaction.channel, interaction.user.id)
            except Exception as e:
                print(f"[spawn] pending prompt failed: {type(e).__name__}: {e}")

    async def on_timeout(self):
        self.cog.drop(self)
        if self.caught_by is not None or self.message is None:
            return
        self.catch_btn.disabled = True
        self.catch_btn.label = "Ran away"
        self.catch_btn.style = discord.ButtonStyle.secondary
        try:
            await self.message.edit(view=self)
            await self.message.reply("The wild cricketer ran away.", mention_author=False)
        except discord.HTTPException:
            pass


# ── cog ───────────────────────────────────────────────────────────────────────

async def _player_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    out = []
    for c in card_db.list_all_cards():
        if cur in c["playername"].lower():
            out.append(app_commands.Choice(name=f"{c['playername']} · {c['ovr']} OVR"[:100], value=c["playername"][:100]))
            if len(out) >= 25:
                break
    return out


async def _event_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    out = [app_commands.Choice(name="Any event", value="any")] if "any".startswith(cur) or not cur else []
    for e in card_db.list_events():
        if cur in e["eventname"].lower():
            out.append(app_commands.Choice(name=e["eventname"][:100], value=e["event_key"][:100]))
    return out[:25]


def _owner_check(i: discord.Interaction) -> bool:
    return i.user.id == OWNER_ID


def _manager_check(i: discord.Interaction) -> bool:
    """Owner / bot admin, or anyone with Manage Server in this server."""
    perms = getattr(i.user, "guild_permissions", None)
    return is_staff(i.user) or bool(perms and (perms.manage_guild or perms.administrator))


class SpawnCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.configs: dict[int, dict] = {}
        self.counters: dict[int, _Counter] = {}
        self.active: dict[int, set] = {}          # guild -> live (uncaught) spawn views
        self.busy: set[int] = set()

    async def cog_load(self):
        await asyncio.to_thread(spawn_db.init)
        self.configs = await asyncio.to_thread(spawn_db.all_configs)

    # ── counting messages ────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        g = message.guild
        if g is None or message.author.bot or message.webhook_id:
            return
        cfg = self.configs.get(g.id)
        if not cfg or not cfg["enabled"] or not cfg["channel_id"]:
            return
        c = self.counters.setdefault(g.id, _Counter())
        c.cache.append((message.author.id, len(message.content or "") < 5))
        now = time.time()
        if now - c.last_count < COUNT_EVERY:
            return
        c.last_count = now
        members = g.member_count or 0
        mult = 1.0
        if members < 5 or members > 1000:
            mult /= 2
        if len(message.content or "") < 5:
            mult /= 2
        authors = [a for a, _ in c.cache]
        if len(set(authors)) < 4 or authors.count(message.author.id) / c.cache.maxlen > 0.4:
            mult /= 2
        c.points += mult
        if c.points + _time_mult(members) * ((now - c.start) // 60) <= c.target:
            return
        if now - c.start < MIN_GAP or self.active.get(g.id) or g.id in self.busy:
            return
        if await asyncio.to_thread(spawn_db.spawns_today, g.id) >= MAX_PER_DAY:
            return
        channel = g.get_channel(int(cfg["channel_id"]))
        if channel is None:
            return
        c.reset()
        asyncio.create_task(self.spawn(channel))

    # ── spawning ─────────────────────────────────────────────────────────────
    def drop(self, view: "SpawnView") -> None:
        live = self.active.get(view.guild_id)
        if live:
            live.discard(view)
            if not live:
                self.active.pop(view.guild_id, None)

    async def spawn(self, channel: discord.abc.Messageable, card=None, chooser=None, manual: bool = False) -> str:
        g = channel.guild
        if g.id in self.busy:
            return "A spawn is already being prepared."
        self.busy.add(g.id)
        try:
            data, tried = None, set()
            for _ in range(4):                      # a card that can't be drawn -> try another
                if card is None:
                    card = await asyncio.to_thread(chooser or pick_card, tried)
                if card is None:
                    return "No card can spawn (all unob / turned off)."
                data = await spawn_blur.blurred_card(card)
                if data:
                    break
                tried.add(card["playername_key"])
                card = None
            if not data:
                return "Could not draw the spawn image."
            log_id = await asyncio.to_thread(spawn_db.log_spawn, g.id, card["playername_key"], manual)
            view = SpawnView(self, g.id, card, log_id)
            view.message = await channel.send(HEADER, file=discord.File(io.BytesIO(data), filename=_random_filename()),
                                              view=view)
            self.active.setdefault(g.id, set()).add(view)
            print(f"[spawn] {g.id}: {card['playername']} ({card['ovr']})")
            return f"Spawned in {channel.mention}."
        except discord.HTTPException as e:
            print(f"[spawn] send failed: {e}")
            return f"Could not send the spawn: {e}"
        finally:
            self.busy.discard(g.id)

    # ── user command ─────────────────────────────────────────────────────────
    @commands.command(name="rcspawns")
    async def rcspawns(self, ctx: commands.Context):
        """How many wild cricketers you caught."""
        n = await asyncio.to_thread(spawn_db.get_catches, ctx.author.id)
        left = spawn_db.SPAWN_VOUCHER_EVERY - n % spawn_db.SPAWN_VOUCHER_EVERY
        await ctx.send(f"**{discord.utils.escape_markdown(ctx.author.display_name)}**\n"
                       f"```\nCatches            {n}\nNext Draft Voucher {left} more catch{'es' if left != 1 else ''}\n```")

    # ── /spawn ───────────────────────────────────────────────────────────────
    # /spawn: visible to everyone in every server. Changing it needs "Manage Server" in that server
    # (otherwise any member could move or switch off the spawns).
    group = app_commands.Group(name="spawn", description="Wild cricketer spawns in this server", guild_only=True)
    # /spawnadmin: owner tools, only in your server and hidden from everyone else
    admin_group = app_commands.Group(name="spawnadmin", description="Spawn owner tools",
                                     guild_only=True, default_permissions=discord.Permissions())
    app_commands.guilds(ADMIN_GUILD_ID)(admin_group)

    @group.command(name="channel", description="Set the ONE channel where cricketers spawn in this server")
    @app_commands.describe(channel="The spawn channel")
    @app_commands.check(_manager_check)
    async def s_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.attach_files and perms.view_channel):
            await interaction.response.send_message(
                f"I need View Channel, Send Messages and Attach Files in {channel.mention}.", ephemeral=True)
            return
        await asyncio.to_thread(spawn_db.set_channel, interaction.guild.id, channel.id)
        self.configs = await asyncio.to_thread(spawn_db.all_configs)
        on = self.configs.get(interaction.guild.id, {}).get("enabled", True)
        await interaction.response.send_message(
            f"Spawn channel: {channel.mention}. Spawning is **{'on' if on else 'off'}**.", ephemeral=True)

    @admin_group.command(name="toggle", description="Turn spawning on or off in this server")
    @app_commands.describe(enabled="True = on, False = off")
    @app_commands.check(lambda i: is_staff(i.user))
    async def s_toggle(self, interaction: discord.Interaction, enabled: bool):
        await asyncio.to_thread(spawn_db.set_enabled, interaction.guild.id, enabled)
        self.configs = await asyncio.to_thread(spawn_db.all_configs)
        ch = self.configs.get(interaction.guild.id, {}).get("channel_id")
        extra = "" if ch else " No channel set yet: use `/spawn channel`."
        await interaction.response.send_message(f"Spawning is now **{'on' if enabled else 'off'}**.{extra}", ephemeral=True)

    @admin_group.command(name="status", description="Spawn channel, on/off, today's spawns and progress")
    @app_commands.check(lambda i: is_staff(i.user))
    async def s_status(self, interaction: discord.Interaction):
        g = interaction.guild
        cfg = self.configs.get(g.id) or {}
        today = await asyncio.to_thread(spawn_db.spawns_today, g.id)
        last = await asyncio.to_thread(spawn_db.last_spawn, g.id)
        c = self.counters.get(g.id)
        if last:
            lc = card_db.get_card(last["player_key"])
            lname = lc["playername"] if lc else last["player_key"]
            if not last["caught_by"] and self.active.get(g.id):
                lname = "hidden (still waiting to be caught)"      # never give the live answer away
            who = f"caught by <@{last['caught_by']}>" if last["caught_by"] else "not caught"
            last_txt = f"{lname}, <t:{int(last['ts'])}:R>, {who}"
        else:
            last_txt = "none yet"
        prog = "no messages counted since the restart"
        if c:
            now = time.time()
            pts = c.points + _time_mult(g.member_count or 0) * ((now - c.start) // 60)
            gap = max(0, int(MIN_GAP - (now - c.start)))
            prog = f"{pts:.1f} / {c.target} points" + (f", gap {gap // 60}m {gap % 60}s left" if gap else "")
        await interaction.response.send_message(
            f"Channel: {('<#' + str(cfg['channel_id']) + '>') if cfg.get('channel_id') else 'not set'}\n"
            f"Spawning: **{'on' if cfg.get('enabled') else 'off'}**\n"
            f"Today: {today} / {MAX_PER_DAY} (admin spawns not counted)\nLive now: {len(self.active.get(g.id, ()))}\n"
            f"Last: {last_txt}\nProgress: {prog}",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    # /admin cricketer spawn (owner only, hidden, your server)
    admin_root = app_commands.Group(name="admin", description="Owner tools",
                                    guild_only=True, default_permissions=discord.Permissions())
    app_commands.guilds(ADMIN_GUILD_ID)(admin_root)
    cricketer = app_commands.Group(name="cricketer", description="Wild cricketer tools", parent=admin_root)

    @cricketer.command(name="spawn", description="(Owner) Spawn cricketers in this channel now")
    @app_commands.describe(
        playername="(optional) this exact card spawns",
        count="How many spawns, one after another (1-10, default 1)",
        ovr="(optional) only these OVRs, e.g. 88, 91  or a range 85-89",
        event="(optional) a random card of this event (Any event = any event card)",
    )
    @app_commands.autocomplete(playername=_player_autocomplete, event=_event_autocomplete)
    @app_commands.check(_owner_check)
    async def a_spawn(self, interaction: discord.Interaction, playername: str | None = None,
                      count: app_commands.Range[int, 1, 10] = 1, ovr: str | None = None, event: str | None = None):
        ch = interaction.channel
        perms = ch.permissions_for(interaction.guild.me) if hasattr(ch, "permissions_for") else None
        if perms is None or not (perms.send_messages and perms.attach_files):
            await interaction.response.send_message("I need Send Messages and Attach Files in this channel.", ephemeral=True)
            return
        chooser, label = None, "random card"
        if playername:
            card = card_db.get_card(playername)
            if card is None:
                await interaction.response.send_message(f"No card named `{playername}`.", ephemeral=True)
                return
            if not card_db.is_obtainable(card):
                await interaction.response.send_message(
                    f"**{card['playername']}** is unob (or its event is disabled). Unob cards never spawn.", ephemeral=True)
                return
            chooser, label = (lambda tried, k=card["playername_key"]: card_db.get_card(k)), card["playername"]
        else:
            ovrs = None
            if ovr:
                ovrs = parse_ovrs(ovr)
                if not ovrs:
                    await interaction.response.send_message("OVR not understood. Examples: `88`  `88, 91`  `85-89`.",
                                                            ephemeral=True)
                    return
            ev_key = None
            if event:
                ev_key = "*" if event.strip().lower() in ("*", "any", "any event") else event.strip().lower()
                if ev_key != "*" and ev_key not in {e["event_key"] for e in card_db.list_events()}:
                    await interaction.response.send_message(f"No event named `{event}`.", ephemeral=True)
                    return
            pool = await asyncio.to_thread(admin_pool, ovrs, ev_key)
            if not pool:
                await interaction.response.send_message(
                    "No card matches (unob cards never spawn, cards turned off with /spawnadmin card don't either).",
                    ephemeral=True)
                return
            chooser = lambda tried, p=pool: pick_from(p, tried)
            parts = []
            if ovrs:
                parts.append("OVR " + ", ".join(str(o) for o in sorted(ovrs)))
            if ev_key:
                parts.append("any event" if ev_key == "*" else f"event {event}")
            label = " | ".join(parts) or label
        await interaction.response.send_message(f"Spawning **{count}** ({label}) here.", ephemeral=True)

        async def run():
            ok = 0
            for i in range(count):
                msg = await self.spawn(ch, None, chooser, manual=True)
                if msg.startswith("Spawned"):
                    ok += 1
                elif msg.startswith("A spawn is already"):
                    await asyncio.sleep(3)
                    msg = await self.spawn(ch, None, chooser, manual=True)
                    ok += msg.startswith("Spawned")
                if i < count - 1:
                    await asyncio.sleep(3)
            if ok < count:
                try:
                    await interaction.followup.send(f"Spawned {ok} of {count}.", ephemeral=True)
                except discord.HTTPException:
                    pass
        asyncio.create_task(run())

    @admin_group.command(name="card", description="(Owner) Turn spawning on/off for one card")
    @app_commands.describe(player="The card", enabled="True = can spawn, False = never spawns")
    @app_commands.autocomplete(player=_player_autocomplete)
    @app_commands.check(_owner_check)
    async def s_card(self, interaction: discord.Interaction, player: str, enabled: bool):
        card = card_db.get_card(player)
        if card is None:
            await interaction.response.send_message(f"No card named `{player}`.", ephemeral=True)
            return
        await asyncio.to_thread(spawn_db.set_card_spawn, card["playername_key"], enabled, interaction.user.id)
        note = ""
        if enabled and not card_db.is_obtainable(card):
            note = "\nNote: this card is unob (obtainable off) or its event is disabled, so it still never spawns."
        await interaction.response.send_message(
            f"**{card['playername']}** ({card['ovr']} OVR): spawn **{'on' if enabled else 'off'}**.{note}", ephemeral=True)

    @admin_group.command(name="blocked", description="(Owner) Cards turned off for spawning + the OVR odds")
    @app_commands.check(_owner_check)
    async def s_blocked(self, interaction: discord.Interaction):
        keys = sorted(await asyncio.to_thread(spawn_db.blocked_keys))
        names = []
        for k in keys:
            c = card_db.get_card(k)
            names.append(f"{c['playername']} ({c['ovr']})" if c else k)
        listing = "\n".join(names) if names else "none"
        text = f"**Turned off ({len(names)})**\n```\n{listing[:1500]}\n```\n**Real spawn odds**\n```\n{odds_table()}\n```"
        await interaction.response.send_message(text[:2000], ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, MaintenanceActive):
            return
        if isinstance(error, app_commands.CheckFailure):
            msg = "Only the owner can use this." if interaction.command and interaction.command.name in (
                "spawn", "card", "blocked") else "You need the Manage Server permission to change spawns."
        else:
            print(f"[spawn] command error: {type(error).__name__}: {error}")
            msg = "Something went wrong. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(SpawnCog(bot))
