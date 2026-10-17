"""Event logs + spam warnings + blacklist enforcement. No slash commands.

Everything goes to ONE channel (LOG_CHANNEL_ID):
  * Economy cog commands (trade, sell, buy, packs, daily ...) and Draft cog commands
  * every `admin ...` command and the owner tools (csgivecoins, csgivecard, csban, csunban)
  * every slash command that is actually used (cardmaker, pool, stadium, addgif ...)
  * ⚠️ spam warnings: same command used again and again in a short time
  * 🚫 blacklist add / remove (written by the `admin blacklist` command)

Logs are put in a small queue and sent in the background, so a slow Discord
never delays a command. A log failure only prints to the console.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

import discord
from discord.ext import commands

import security
from bot_guard import OWNER_ID

LOG_CHANNEL_ID = 1560951155838947388

# Cogs whose commands are all logged.
LOGGED_COGS = {"Economy", "Draft"}
# Extra text commands that are logged even though they live in another cog.
LOGGED_COMMANDS = {"admin", "csgivecoins", "csgivecard", "csban", "csunban"}
# Read-only commands: logging them would only be noise.
SKIP_COMMANDS = {"csbal", "cspending", "csleaderboard", "cslb", "csleaderboards"}

# Spam rules: (uses, seconds). Commands in SENSITIVE use the stricter rule.
SPAM_DEFAULT = (8, 15.0)
SPAM_SENSITIVE = (5, 30.0)
SENSITIVE = {"cstrade", "cssell", "csbuy", "cspack", "csopen", "csclaim", "csgivecoins", "csgivecard"}
ALERT_GAP = 60.0   # at most one alert per user+command per minute

_NO_PINGS = discord.AllowedMentions.none()


def _root_name(ctx: commands.Context) -> str:
    cmd = ctx.command
    while cmd is not None and getattr(cmd, "parent", None) is not None:
        cmd = cmd.parent
    return cmd.name if cmd else ""


def _clip(text: str, n: int = 350) -> str:
    text = (text or "").replace("```", "'''")
    return text if len(text) <= n else text[: n - 1] + "…"


async def post_log(bot: commands.Bot, embed: discord.Embed) -> None:
    """Queue an embed for the log channel (safe to call from anywhere)."""
    cog = bot.get_cog("EventLog")
    if cog is not None:
        cog.enqueue(embed)


async def blacklist_check(ctx: commands.Context) -> bool:
    """Global text-command check: blacklisted users can't run anything."""
    if ctx.author.id != OWNER_ID and security.is_banned(ctx.author.id):
        raise security.UserBanned()
    return True


class EventLog(commands.Cog, name="EventLog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=300)
        self._task: asyncio.Task | None = None
        self._hits: dict[tuple[int, str], deque] = defaultdict(deque)
        self._last_alert: dict[tuple[int, str], float] = {}

    # ── lifecycle ────────────────────────────────────────────────────
    async def cog_load(self):
        try:
            security.load_bans()          # blacklisted users stay blocked after a restart
        except Exception as e:
            print(f"[log] could not load blacklist: {e}")
        self.bot.add_check(blacklist_check)
        self._task = asyncio.create_task(self._worker())

    async def cog_unload(self):
        self.bot.remove_check(blacklist_check)
        if self._task:
            self._task.cancel()

    def enqueue(self, embed: discord.Embed) -> None:
        try:
            self._queue.put_nowait(embed)
        except asyncio.QueueFull:
            pass   # better to drop a log than to slow the bot

    async def _worker(self):
        while True:
            embed = await self._queue.get()
            try:
                ch = self.bot.get_channel(LOG_CHANNEL_ID) or await self.bot.fetch_channel(LOG_CHANNEL_ID)
                await ch.send(embed=embed, allowed_mentions=_NO_PINGS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[log] could not send to log channel: {type(e).__name__}: {e}")
            await asyncio.sleep(0.6)   # stay well inside Discord's rate limits

    # ── embeds ───────────────────────────────────────────────────────
    @staticmethod
    def _who(user) -> str:
        return f"{user.mention} (`{user.id}`)"

    @staticmethod
    def _where(guild, channel) -> str:
        g = guild.name if guild else "DM"
        c = f"<#{channel.id}>" if channel is not None else "?"
        return f"{g} · {c}"

    # ── logging commands ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        try:
            root = _root_name(ctx)
            if not root or root in SKIP_COMMANDS:
                return
            if ctx.command.qualified_name.startswith("admin blacklist"):
                return   # already logged by its own blacklist embed
            cog_name = getattr(ctx.cog, "qualified_name", "")
            if cog_name not in LOGGED_COGS and root not in LOGGED_COMMANDS:
                return
            if root in ("admin",) or root in LOGGED_COMMANDS:
                color, tag = discord.Color.red(), "Admin"
            elif cog_name == "Draft":
                color, tag = discord.Color.blue(), "Draft"
            else:
                color, tag = discord.Color.green(), "Economy"
            e = discord.Embed(title=f"{tag} · {ctx.command.qualified_name}", color=color,
                              timestamp=discord.utils.utcnow())
            e.add_field(name="User", value=self._who(ctx.author), inline=True)
            e.add_field(name="Where", value=self._where(ctx.guild, ctx.channel), inline=True)
            e.add_field(name="Message", value=f"```{_clip(ctx.message.content)}```", inline=False)
            self.enqueue(e)
        except Exception as ex:
            print(f"[log] command log failed: {type(ex).__name__}: {ex}")

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        try:
            opts = ""
            data = interaction.data or {}
            parts = []
            for o in data.get("options", []) or []:
                if "value" in o:
                    parts.append(f"{o.get('name')}: {o.get('value')}")
                else:                              # sub-command
                    for s in o.get("options", []) or []:
                        parts.append(f"{s.get('name')}: {s.get('value')}")
            opts = ", ".join(parts)
            e = discord.Embed(title=f"Slash · /{command.qualified_name}", color=discord.Color.purple(),
                              timestamp=discord.utils.utcnow())
            e.add_field(name="User", value=self._who(interaction.user), inline=True)
            e.add_field(name="Where", value=self._where(interaction.guild, interaction.channel), inline=True)
            if opts:
                e.add_field(name="Options", value=f"```{_clip(opts)}```", inline=False)
            self.enqueue(e)
        except Exception as ex:
            print(f"[log] slash log failed: {type(ex).__name__}: {ex}")

    # ── spam warnings ────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        """Fires for every real command attempt (even ones later blocked)."""
        try:
            root = _root_name(ctx)
            if not root:
                return
            key = (ctx.author.id, root)
            limit, window = SPAM_SENSITIVE if root in SENSITIVE else SPAM_DEFAULT
            now = time.monotonic()
            dq = self._hits[key]
            dq.append(now)
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(self._hits) > 3000:
                for k in [k for k, d in self._hits.items() if not d or now - d[-1] > 120]:
                    self._hits.pop(k, None)
                    self._last_alert.pop(k, None)
            if len(dq) >= limit and now - self._last_alert.get(key, -1e9) >= ALERT_GAP:
                self._last_alert[key] = now
                e = discord.Embed(
                    title="⚠️ Spam warning",
                    description=f"{self._who(ctx.author)} used `{root}` **{len(dq)} times in {int(window)}s**.",
                    color=discord.Color.orange(), timestamp=discord.utils.utcnow(),
                )
                e.add_field(name="Where", value=self._where(ctx.guild, ctx.channel), inline=True)
                e.add_field(name="Last message", value=f"```{_clip(ctx.message.content, 150)}```", inline=False)
                self.enqueue(e)
        except Exception as ex:
            print(f"[log] spam check failed: {type(ex).__name__}: {ex}")


async def setup(bot: commands.Bot):
    await bot.add_cog(EventLog(bot))
