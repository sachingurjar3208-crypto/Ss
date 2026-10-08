"""Shared guard rules for the whole bot.

1. WHO is staff:      the bot owner, or anyone with ADMIN_ROLE_IDS in the main server.
2. admin_only():      decorator for admin slash commands. It
                        - hides the command from normal members (default permissions = Administrator only),
                        - registers it ONLY in ADMIN_GUILD_ID (it does not exist in any other server),
                        - and still checks the role when the command is run.
3. Maintenance mode:  /maintenance turns it on. While it is on, every command (slash AND text)
                      from non-staff users is blocked and they see the reason. The state is saved
                      in maintenance.json, so it survives a bot restart.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

OWNER_ID = 1317288099075850243
ADMIN_GUILD_ID = 1556644438917382150          # your server
ADMIN_ROLE_IDS = {1556644558929010769}        # the admin role


def is_staff(user) -> bool:
    """Owner, or a member holding the admin role."""
    if getattr(user, "id", None) == OWNER_ID:
        return True
    roles = getattr(user, "roles", None) or []
    return any(r.id in ADMIN_ROLE_IDS for r in roles)


# ── Admin-only slash commands ────────────────────────────────────────────

def admin_only():
    """Put this under @app_commands.command(...) on every admin slash command."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return is_staff(interaction.user)

    def decorator(func):
        func = app_commands.check(predicate)(func)
        func = app_commands.default_permissions()(func)       # hidden unless an admin allows a role
        func = app_commands.guild_only()(func)
        func = app_commands.guilds(ADMIN_GUILD_ID)(func)      # only exists in your server
        return func

    return decorator


# ── Maintenance state (saved to disk) ────────────────────────────────────

_STATE_FILE = Path(__file__).parent / "maintenance.json"
_state = {"enabled": False, "reason": "", "since": 0.0, "by": 0}


def _load() -> None:
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(data, dict):
        for key in _state:
            if key in data:
                _state[key] = data[key]


def _save() -> None:
    try:
        tmp = _STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state), encoding="utf-8")
        os.replace(tmp, _STATE_FILE)
    except OSError as e:
        print(f"[maintenance] could not save state: {e}")


_load()


def maintenance_on() -> bool:
    return bool(_state["enabled"])


def set_maintenance(enabled: bool, reason: str = "", by: int = 0) -> None:
    _state["enabled"] = bool(enabled)
    _state["reason"] = reason if enabled else ""
    _state["since"] = time.time() if enabled else 0.0
    _state["by"] = by
    _save()


def maintenance_text() -> str:
    reason = _state["reason"] or "The bot is being updated."
    return f"🛠️ **The bot is under maintenance.**\n**Reason:** {reason}\nPlease try again later."


class MaintenanceActive(app_commands.CheckFailure):
    """Raised for a blocked slash command (the user was already told why)."""


class MaintenancePrefixBlocked(commands.CheckFailure):
    """Raised for a blocked text command (the user was already told why)."""


class GuardedTree(app_commands.CommandTree):
    """Slash-command tree that blocks every command during maintenance."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if maintenance_on() and not is_staff(interaction.user):
            try:
                await interaction.response.send_message(maintenance_text(), ephemeral=True)
            except discord.HTTPException:
                pass
            raise MaintenanceActive()
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, MaintenanceActive):
            return
        await super().on_error(interaction, error)


_last_notice: dict[int, float] = {}


async def maintenance_prefix_check(ctx: commands.Context) -> bool:
    """Global check for text commands: register with bot.add_check(...)."""
    if not maintenance_on() or is_staff(ctx.author):
        return True
    now = time.monotonic()
    if now - _last_notice.get(ctx.author.id, 0.0) >= 10:      # don't spam if someone retries fast
        if len(_last_notice) > 2000:
            _last_notice.clear()
        _last_notice[ctx.author.id] = now
        try:
            await ctx.send(maintenance_text())
        except discord.HTTPException:
            pass
    raise MaintenancePrefixBlocked()
