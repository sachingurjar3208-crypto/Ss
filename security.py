"""Bot-wide protection so nobody can spam, exploit or break the bot.

What is covered
1. BANS            banned users (csban) can't run any command.
2. RATE LIMITS     one global check for every command: too many commands in a
                   short time -> "slow down"; keep doing it and you're muted
                   for a few minutes. This also protects the empty-prefix
                   commands from message floods.
3. SERVER ONLY     commands don't run in DMs.
4. CLEAN TEXT      team names (and any typed text) are stripped of mentions,
                   markdown tricks, invite links and invisible characters.
5. SAFE MENTIONS   SAFE_MENTIONS stops @everyone / @here / role pings from
                   ever being sent by the bot, even if someone tricks a
                   command into echoing them.
6. OWNER-ONLY      admin commands use commands.is_owner().
7. SAFE BUTTONS    OwnedView: only the person a menu was made for can press
                   its buttons, and the buttons switch off when it times out.
8. QUIET ERRORS    one error handler: users get a short friendly message,
                   never a traceback; unknown commands (every normal chat
                   message, since the prefix is empty) are ignored silently.
"""

from __future__ import annotations

import re
import time
import traceback
import unicodedata
from collections import defaultdict, deque

import discord
from discord import ui
from discord.ext import commands

import economy

# Allows normal user mentions (needed for match pings) but never @everyone,
# @here or role mentions.
SAFE_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False)

# ── Tunable limits ───────────────────────────────────────────────────────────
RATE_MAX_COMMANDS = 6        # commands allowed ...
RATE_WINDOW       = 10.0     # ... per this many seconds
STRIKES_TO_MUTE   = 4        # rate-limit hits in a minute before a temporary mute
MUTE_SECONDS      = 300
MAX_INPUT_LEN     = 60       # longest typed player name we accept


# ── Exceptions used by the global check ──────────────────────────────────────

class UserBanned(commands.CheckFailure):
    pass


class RateLimited(commands.CheckFailure):
    def __init__(self, wait: float, muted: bool = False):
        super().__init__("rate limited")
        self.wait = wait
        self.muted = muted


# ── Ban list (kept in memory, saved in economy.db) ───────────────────────────

_banned: set[int] = set()


def load_bans() -> None:
    _banned.clear()
    _banned.update(economy.banned_ids())


def ban_user(user_id: int, reason: str | None = None) -> None:
    economy.set_banned(user_id, True, reason)
    _banned.add(int(user_id))


def unban_user(user_id: int) -> None:
    economy.set_banned(user_id, False)
    _banned.discard(int(user_id))


def is_banned(user_id: int) -> bool:
    return int(user_id) in _banned


# ── Rate limiter ─────────────────────────────────────────────────────────────

_hits: dict[int, deque] = defaultdict(deque)
_strikes: dict[int, deque] = defaultdict(deque)
_muted_until: dict[int, float] = {}
_last_notice: dict[int, float] = {}


def _prune(now: float) -> None:
    """Forget users who have been quiet, so these dictionaries never grow forever."""
    for store in (_hits, _strikes):
        for uid in [u for u, dq in store.items() if not dq or now - dq[-1] > 120]:
            del store[uid]
    for uid in [u for u, t in _muted_until.items() if t < now]:
        del _muted_until[uid]
    for uid in [u for u, t in _last_notice.items() if now - t > 300]:
        del _last_notice[uid]


def _check_rate(user_id: int) -> None:
    now = time.monotonic()
    if len(_hits) > 2000:
        _prune(now)
    until = _muted_until.get(user_id, 0.0)
    if until > now:
        raise RateLimited(until - now, muted=True)

    hits = _hits[user_id]
    while hits and now - hits[0] > RATE_WINDOW:
        hits.popleft()
    if len(hits) >= RATE_MAX_COMMANDS:
        strikes = _strikes[user_id]
        strikes.append(now)
        while strikes and now - strikes[0] > 60:
            strikes.popleft()
        if len(strikes) >= STRIKES_TO_MUTE:
            _muted_until[user_id] = now + MUTE_SECONDS
            strikes.clear()
            raise RateLimited(MUTE_SECONDS, muted=True)
        raise RateLimited(RATE_WINDOW - (now - hits[0]))
    hits.append(now)


def _notice_allowed(user_id: int, gap: float = 6.0) -> bool:
    """Only answer a blocked user every few seconds so we don't spam back."""
    now = time.monotonic()
    if now - _last_notice.get(user_id, 0.0) >= gap:
        _last_notice[user_id] = now
        return True
    return False


# ── The global check every command passes through ────────────────────────────

async def global_check(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    uid = ctx.author.id
    if is_banned(uid):
        raise UserBanned()
    if await ctx.bot.is_owner(ctx.author):
        return True  # the owner is never rate limited
    _check_rate(uid)
    return True


# ── Text cleaning ────────────────────────────────────────────────────────────

_NAME_PUNCT = set(" .'&-")
_BAD_FRAGMENTS = ("discord.gg", "discord.com", "http", "www.", ".com", ".gg", ".io", "@everyone", "@here")


def clean_input(text: str, max_len: int = MAX_INPUT_LEN) -> str:
    """For typed player names / search text: normalise, drop control and
    invisible characters, squash spaces, limit length."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", " ", text)  # newlines and tabs become a single space first
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    return text.strip()[:max_len]


def clean_team_name(raw: str) -> tuple[str | None, str | None]:
    """Returns (name, None) if fine, or (None, reason). Allows letters,
    numbers, spaces and . ' & - only, 3-24 characters."""
    name = clean_input(raw, 60)
    if not 3 <= len(name) <= 24:
        return None, "Team name must be 3-24 characters."
    # Letters, numbers and combining marks of any language (Hindi works), plus a few symbols.
    ok = all(unicodedata.category(ch)[0] in "LNM" or ch in _NAME_PUNCT for ch in name)
    if not ok or unicodedata.category(name[0])[0] not in "LN":
        return None, "Team name can only use letters, numbers, spaces and . ' & -"
    low = name.lower()
    if any(bad in low for bad in _BAD_FRAGMENTS):
        return None, "Team name can't contain links or mentions."
    return name, None


def esc(text: str) -> str:
    """Make any text safe to show in a message (no mentions, no markdown)."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(text)))


# ── Buttons that only their owner can press ──────────────────────────────────

class OwnedView(ui.View):
    """A view that only `allowed_ids` may use. Buttons switch off on timeout."""

    def __init__(self, allowed_ids, timeout: float = 90.0):
        super().__init__(timeout=timeout)
        if isinstance(allowed_ids, int):
            allowed_ids = {allowed_ids}
        self.allowed_ids = set(allowed_ids)
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_banned(interaction.user.id):
            return False
        if interaction.user.id not in self.allowed_ids:
            await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        traceback.print_exception(type(error), error, error.__traceback__)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)
            else:
                await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)
        except discord.HTTPException:
            pass


# ── One error handler for all commands ───────────────────────────────────────

def _fmt_wait(seconds: float) -> str:
    seconds = max(1, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


fmt_wait = _fmt_wait


async def handle_command_error(ctx: commands.Context, error: Exception) -> None:
    error = getattr(error, "original", error) if isinstance(error, commands.CommandInvokeError) else error

    # Empty prefix: every normal chat message lands here. Stay silent.
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, UserBanned):
        if _notice_allowed(ctx.author.id, 60):
            await ctx.send("🚫 You are banned from using this bot.")
        return
    if isinstance(error, RateLimited):
        if _notice_allowed(ctx.author.id):
            if error.muted:
                await ctx.send(f"⏳ Too many commands. You're muted for {_fmt_wait(error.wait)}.")
            else:
                await ctx.send(f"⏳ Slow down! Try again in {_fmt_wait(error.wait)}.")
        return
    if isinstance(error, commands.NoPrivateMessage):
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Wait {_fmt_wait(error.retry_after)} before using this again.")
        return
    if isinstance(error, commands.NotOwner):
        await ctx.send("🔒 Only the bot owner can use this command.")
        return
    if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions)):
        await ctx.send("🔒 Missing permissions for that.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}".strip()
        await ctx.send(f"❌ Missing input. Usage: `{usage}`")
        return
    if isinstance(error, (commands.BadArgument, commands.BadUnionArgument, commands.ConversionError,
                          commands.TooManyArguments, commands.UserInputError, commands.ArgumentParsingError)):
        await ctx.send("❌ That input isn't valid. Check the command and try again.")
        return
    if isinstance(error, commands.CheckFailure):
        return

    # Anything else is a real bug: log it for the owner, show users nothing scary.
    print(f"[error] command {getattr(ctx.command, 'qualified_name', '?')} failed:")
    traceback.print_exception(type(error), error, error.__traceback__)
    try:
        await ctx.send("⚠️ Something went wrong. Please try again in a moment.")
    except discord.HTTPException:
        pass
