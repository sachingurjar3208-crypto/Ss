"""Owner / admin slash commands for pack opening animations (main server only).

    /pack animation add     url:<link>  pathname:<pack name>
    /pack animation remove  pathname:<name>

The animation is the GIF csopen shows for ANIM_SECONDS (pack_anim.py) before the player's real
card appears. pathname = the pack it is for: bronze, silver, gold, legendary, halloween, debut.
Want more than one for the same pack? Use `gold_2`, `gold_fire` ... (one is picked at random).
Adding a pathname that already exists replaces it.
"""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import pack_anim
import squad_logic as sl
from bot_guard import ADMIN_GUILD_ID, is_staff
from security import esc


async def _pathname_autocomplete(interaction: discord.Interaction, current: str):
    """Existing animation names first, then the pack names you can use."""
    cur = (current or "").lower()
    out: list[app_commands.Choice[str]] = []
    seen: set[str] = set()
    try:
        found = await asyncio.to_thread(pack_anim.listing, cur)
    except Exception:
        found = []
    for name, pack in found[:15]:
        seen.add(name)
        out.append(app_commands.Choice(name=f"{name} ({pack} pack)"[:100], value=name))
    for key in sl.PACKS:
        if key not in seen and cur in key:
            out.append(app_commands.Choice(name=f"{key} (new)"[:100], value=key))
    return out[:25]


async def _remove_autocomplete(interaction: discord.Interaction, current: str):
    try:
        found = await asyncio.to_thread(pack_anim.listing, (current or "").lower())
    except Exception:
        found = []
    return [app_commands.Choice(name=f"{n} ({p} pack)"[:100], value=n) for n, p in found[:25]]


# /pack ...  (exists ONLY in your server, hidden from normal members).
# The groups are plain module-level objects and get registered in setup() below with an
# explicit guild, so they can never end up as GLOBAL commands.
pack_group = app_commands.Group(
    name="pack",
    description="Pack opening animations (owner & admin)",
    guild_ids=[ADMIN_GUILD_ID],
    guild_only=True,
    default_permissions=discord.Permissions(),
)
# /pack animation ...
animation_group = app_commands.Group(
    name="animation",
    description="The animation shown before the card appears in csopen",
    parent=pack_group,
)


async def _staff_only(interaction: discord.Interaction) -> bool:
    if is_staff(interaction.user):
        return True
    await interaction.response.send_message("🔒 Only the bot owner or an admin can use this.", ephemeral=True)
    return False


def _label(pack: str) -> str:
    return sl.PACKS.get(pack, {}).get("label", pack)


@animation_group.command(name="add", description="Add a pack opening animation (a GIF link)")
@app_commands.describe(
    url="Link to the GIF (about 5 seconds long; Giphy links work)",
    pathname="Which pack: bronze, silver, gold, legendary, halloween, debut (gold_2 = a 2nd one for gold)",
)
@app_commands.autocomplete(pathname=_pathname_autocomplete)
async def animation_add(interaction: discord.Interaction, url: str, pathname: str):
    if not await _staff_only(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        res = await pack_anim.add(pathname, url)
    except pack_anim.AnimError as e:
        await interaction.followup.send(f"❌ {e}", ephemeral=True)
        return
    verb = "replaced" if res["replaced"] else "saved"
    text = (f"✅ Animation **{esc(res['pathname'])}** {verb} for the **{_label(res['pack'])}**.\n"
            f"`csopen` plays it for {pack_anim.ANIM_SECONDS:g} seconds, then shows the real card.")
    path = res["file"]
    try:
        await interaction.followup.send(
            text, file=discord.File(str(path), filename=f"preview{path.suffix}"), ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send(text, ephemeral=True)


@animation_group.command(name="remove", description="Delete a pack opening animation")
@app_commands.describe(pathname="Name of the animation to delete")
@app_commands.autocomplete(pathname=_remove_autocomplete)
async def animation_remove(interaction: discord.Interaction, pathname: str):
    if not await _staff_only(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        old = await pack_anim.remove(pathname)
    except pack_anim.AnimError as e:
        await interaction.followup.send(f"❌ {e}", ephemeral=True)
        return
    left = await asyncio.to_thread(pack_anim.pick, old["pack"])
    tail = ("" if left is not None else
            f"\nThe **{_label(old['pack'])}** has no animation now, so csopen uses the built-in pack intro.")
    await interaction.followup.send(f"🗑️ Animation **{esc(old['pathname'])}** deleted.{tail}", ephemeral=True)


async def setup(bot: commands.Bot):
    try:
        await asyncio.to_thread(pack_anim.ensure_table)
    except Exception as e:
        print(f"[pack] could not create the pack animation table: {e}")
    bot.tree.add_command(pack_group, guild=discord.Object(id=ADMIN_GUILD_ID), override=True)


async def teardown(bot: commands.Bot):
    bot.tree.remove_command("pack", guild=discord.Object(id=ADMIN_GUILD_ID))
