"""Owner-only card-creation slash commands.

    /cardmaker      create a new player card
    /editcard       edit an existing player card (only pass fields you want to change)
    /bgadder        add a new background template
    /foregroundfix  nudge position and/or resize a card's foreground cutout
    /removecard     delete a player card
    /logoadderofplaystyle  add a playstyle logo to the logo list
    /setplaystylelogo      put up to 2 playstyle logos on a card
    /logofixer             move / resize a card's playstyle logos
    /countrylogoadder      add (or replace) a country's logo - shown just above the country name
    /countrylogofixer      move / resize a card's country logo
    /playtypefixer         move / resize a card's batting-hand text (RIGHT / LEFT HAND BAT)
    /paneladder     add a new stats-panel overlay image (sits IN FRONT of the foreground)
    /panelremover   remove a stats-panel overlay from the panel list
    /panelfixer     move / resize a card's stats-panel overlay

Only OWNER_ID may run any of these — everyone else gets a plain refusal.
"""

from __future__ import annotations

import io
import os

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

from card_countries import CRICKET_COUNTRIES, resolve_country
from card_db import (
    init_card_db,
    card_exists, get_card, create_card, update_card, update_card_offset,
    set_card_image_path, list_playernames, delete_card,
    background_exists, get_background, add_background, list_backgrounds,
    set_playstyles, update_logo_layout, update_card_layout,
    playstyle_logo_exists, get_playstyle_logo, add_playstyle_logo, list_playstyle_logos,
    panel_exists, get_panel, add_panel, list_panels, remove_panel,
    update_panel_layout, set_card_panel,
    get_country_logo, add_country_logo, list_country_logos, update_country_logo_layout,
    GENERATED_DIR, BACKGROUNDS_DIR, LOGOS_DIR, PANELS_DIR, COUNTRY_LOGOS_DIR,
)
from card_image import generate_card_image
import card_cache
from card_narratives import NARRATIVES, NARRATIVE_NAMES, ROLES, HANDS, BOWLING_TYPES

import aiohttp

OWNER_ID = 1317288099075850243
MAX_STAT = 150
MIN_SCALE_PCT = 10
MAX_SCALE_PCT = 400


def is_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == OWNER_ID
    return app_commands.check(predicate)


ROLE_CHOICES = [app_commands.Choice(name=label, value=code) for code, label in ROLES.items()]
HAND_CHOICES = [app_commands.Choice(name=label, value=code) for code, label in HANDS.items()]
BOWLTYPE_CHOICES = [app_commands.Choice(name=label, value=code) for code, label in BOWLING_TYPES.items()]
NARRATIVE_CHOICES = [app_commands.Choice(name=n, value=n) for n in NARRATIVE_NAMES]
# /editcard only: lets you clear a narrative slot.
NARRATIVE_CHOICES_EDIT = NARRATIVE_CHOICES + [app_commands.Choice(name="None (remove)", value="NONE")]

TARGET_CHOICES = [
    app_commands.Choice(name="Both logos", value="both"),
    app_commands.Choice(name="Logo 1 only", value="1"),
    app_commands.Choice(name="Logo 2 only", value="2"),
]


# ── Autocomplete providers ──────────────────────────────────────────────

async def logo_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [n for n in list_playstyle_logos() if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def narrative_name_autocomplete(interaction: discord.Interaction, current: str):
    """Suggests the narrative names for /logoadderofplaystyle's pathname (you can still type your own)."""
    current_l = current.lower()
    names = [n for n in NARRATIVE_NAMES if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def background_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [n for n in list_backgrounds() if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def panel_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [n for n in list_panels() if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def country_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [name for name, _ in CRICKET_COUNTRIES if current_l in name.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def playername_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [n for n in list_playernames() if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def _regenerate_and_send(interaction: discord.Interaction, playername: str, verb: str) -> None:
    card = get_card(playername)
    bg = get_background(card["background"])
    if bg is None:
        await interaction.followup.send(
            f"⚠️ Card saved, but background template `{card['background']}` "
            f"no longer exists — image not regenerated."
        )
        return
    buf = await generate_card_image(card, bg["local_path"])
    out_path = GENERATED_DIR / f"{playername.strip().lower().replace(' ', '_')}.png"
    with open(out_path, "wb") as f:
        f.write(buf.getvalue())
    set_card_image_path(playername, str(out_path))
    await card_cache.warm(str(out_path))   # so csview/csbuy/cssell are instant
    buf.seek(0)
    file = discord.File(buf, filename=f"{card['displayname']}.png")
    extras = ""
    if card["role"]:
        extras += f" • {ROLES.get(card['role'], card['role'])}"
    if card["batting_hand"]:
        extras += f" • {HANDS.get(card['batting_hand'], card['batting_hand'])} bat"
    styles = [s for s in (card["playstyle1"], card["playstyle2"]) if s]
    if styles:
        extras += " • " + " + ".join(styles)
    embed = discord.Embed(
        title=f"🏏 {card['playername']}",
        description=(
            f"{verb} • OVR **{card['ovr']}** • BAT **{card['bat']}** • "
            f"BOWL **{card['bowl']}** • {card['country_emoji']} {card['country']} • "
            f"Size **{card['scale_pct']}%**{extras}"
        ),
        color=discord.Color.gold(),
    )
    embed.set_image(url=f"attachment://{card['displayname']}.png")
    await interaction.followup.send(embed=embed, file=file)


def _missing_logo_note(names) -> str:
    """Note to append when a chosen narrative has no logo in the logo list yet."""
    missing = [n for n in names if n and not playstyle_logo_exists(n)]
    if not missing:
        return ""
    return (
        "\nℹ️ No logo yet for: " + ", ".join(f"**{m}**" for m in missing)
        + " — add one with `/logoadderofplaystyle` using exactly that name as `pathname`."
    )


class CardMakerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_card_db()

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ Only the bot owner can use this command."
        else:
            msg = f"⚠️ Something went wrong: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ── /cardmaker ───────────────────────────────────────────────────
    @app_commands.command(name="cardmaker", description="Create a new player card")
    @app_commands.describe(
        playername="Full player name — the unique ID for this card, e.g. Virat Kohli",
        displayname="Short name shown big in the card corner, e.g. Kohli",
        foregroundlink="Direct link to the player cutout image (PNG with transparent background works best)",
        background="Card background template (added via /bgadder)",
        ovr="Overall rating (1–150)",
        bat="Batting power (1–150)",
        bowl="Bowling power (1–150)",
        countryflag="Country",
        role="Player role (shown on the card)",
        battinghand="Right hand or left hand batter",
        narrative1="Playstyle 1 (optional) — its logo shows on the card",
        narrative2="Playstyle 2 (optional) — its logo shows on the card",
        bowlingtype="Bowling type for bowlers/all-rounders (default Fast)",
    )
    @app_commands.choices(
        role=ROLE_CHOICES, battinghand=HAND_CHOICES,
        narrative1=NARRATIVE_CHOICES, narrative2=NARRATIVE_CHOICES,
        bowlingtype=BOWLTYPE_CHOICES,
    )
    @app_commands.autocomplete(background=background_autocomplete, countryflag=country_autocomplete)
    @is_owner()
    async def cardmaker(
        self,
        interaction: discord.Interaction,
        playername: str,
        displayname: str,
        foregroundlink: str,
        background: str,
        ovr: app_commands.Range[int, 1, MAX_STAT],
        bat: app_commands.Range[int, 1, MAX_STAT],
        bowl: app_commands.Range[int, 1, MAX_STAT],
        countryflag: str,
        role: str,
        battinghand: str,
        narrative1: str | None = None,
        narrative2: str | None = None,
        bowlingtype: str | None = None,
    ):
        await interaction.response.defer()

        if narrative1 and narrative2 and narrative1 == narrative2:
            await interaction.followup.send("❌ narrative1 and narrative2 can't be the same — pick two different playstyles.")
            return

        if card_exists(playername):
            await interaction.followup.send(
                f"❌ A card for **{playername}** already exists. "
                f"Use `/editcard` to change it instead."
            )
            return

        country = resolve_country(countryflag)
        if country is None:
            await interaction.followup.send(
                f"❌ Unknown country `{countryflag}` — pick one from the autocomplete list."
            )
            return
        country_name, country_emoji = country

        bg = get_background(background)
        if bg is None:
            names = list_backgrounds()
            hint = ", ".join(names) if names else "(none added yet — use `/bgadder` first)"
            await interaction.followup.send(f"❌ Unknown background `{background}`.\nAvailable: {hint}")
            return

        create_card(
            playername=playername,
            displayname=displayname,
            foreground_link=foregroundlink,
            background=bg["pathname"],
            ovr=ovr, bat=bat, bowl=bowl,
            country=country_name, country_emoji=country_emoji,
            created_by=interaction.user.id,
            role=role, batting_hand=battinghand,
            playstyle1=narrative1, playstyle2=narrative2,
            bowling_type=bowlingtype,
        )

        try:
            await _regenerate_and_send(interaction, playername, "✅ Card created")
            note = _missing_logo_note([narrative1, narrative2])
            if note:
                await interaction.followup.send(note.strip())
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Card saved to the database, but image generation failed: `{e}`\n"
                f"This is usually the foreground image link — check it loads directly as "
                f"an image (not a webpage), then try `/editcard` with a new link, or "
                f"nudge it with `/foregroundfix`."
            )

    # ── /editcard ────────────────────────────────────────────────────
    @app_commands.command(name="editcard", description="Edit an existing player card")
    @app_commands.describe(
        playername="Which card to edit",
        displayname="New short display name (leave blank to keep current)",
        foregroundlink="New cutout image link (leave blank to keep current)",
        background="New background template (leave blank to keep current)",
        ovr="New overall rating (1–150)",
        bat="New batting power (1–150)",
        bowl="New bowling power (1–150)",
        countryflag="New country",
        role="New role",
        battinghand="New batting hand",
        narrative1="New playstyle 1 (choose None to remove)",
        narrative2="New playstyle 2 (choose None to remove)",
        bowlingtype="New bowling type (Fast / Off Spin / Leg Spin)",
        panel="Stats-panel overlay image (added via /paneladder) — type none to remove",
    )
    @app_commands.choices(
        role=ROLE_CHOICES, battinghand=HAND_CHOICES,
        narrative1=NARRATIVE_CHOICES_EDIT, narrative2=NARRATIVE_CHOICES_EDIT,
        bowlingtype=BOWLTYPE_CHOICES,
    )
    @app_commands.autocomplete(
        playername=playername_autocomplete,
        background=background_autocomplete,
        countryflag=country_autocomplete,
        panel=panel_autocomplete,
    )
    @is_owner()
    async def editcard(
        self,
        interaction: discord.Interaction,
        playername: str,
        displayname: str | None = None,
        foregroundlink: str | None = None,
        background: str | None = None,
        ovr: app_commands.Range[int, 1, MAX_STAT] | None = None,
        bat: app_commands.Range[int, 1, MAX_STAT] | None = None,
        bowl: app_commands.Range[int, 1, MAX_STAT] | None = None,
        countryflag: str | None = None,
        role: str | None = None,
        battinghand: str | None = None,
        narrative1: str | None = None,
        narrative2: str | None = None,
        bowlingtype: str | None = None,
        panel: str | None = None,
    ):
        await interaction.response.defer()

        if not card_exists(playername):
            await interaction.followup.send(
                f"❌ No card exists for **{playername}** yet. Use `/cardmaker` to create it first."
            )
            return

        updates: dict = {}
        if displayname is not None:
            updates["displayname"] = displayname
        if foregroundlink is not None:
            updates["foreground_link"] = foregroundlink
        if background is not None:
            bg = get_background(background)
            if bg is None:
                names = list_backgrounds()
                hint = ", ".join(names) if names else "(none added yet)"
                await interaction.followup.send(f"❌ Unknown background `{background}`.\nAvailable: {hint}")
                return
            updates["background"] = bg["pathname"]
        if countryflag is not None:
            country = resolve_country(countryflag)
            if country is None:
                await interaction.followup.send(
                    f"❌ Unknown country `{countryflag}` — pick one from the autocomplete list."
                )
                return
            updates["country"], updates["country_emoji"] = country
        if ovr is not None:
            updates["ovr"] = ovr
        if bat is not None:
            updates["bat"] = bat
        if bowl is not None:
            updates["bowl"] = bowl
        if role is not None:
            updates["role"] = role
        if battinghand is not None:
            updates["batting_hand"] = battinghand
        if bowlingtype is not None:
            updates["bowling_type"] = bowlingtype

        # Playstyle slots: only touch the slot(s) that were given ("NONE" clears one).
        slot_updates: dict = {}
        if narrative1 is not None:
            slot_updates["playstyle1"] = None if narrative1 == "NONE" else narrative1
        if narrative2 is not None:
            slot_updates["playstyle2"] = None if narrative2 == "NONE" else narrative2

        # Panel overlay: "none" (any case) clears it; otherwise it must be a
        # known /paneladder pathname.
        panel_update_pending = False
        new_panel_value: str | None = None
        if panel is not None:
            if panel.strip().lower() == "none":
                panel_update_pending = True
                new_panel_value = None
            else:
                prow = get_panel(panel)
                if prow is None:
                    names = list_panels()
                    hint = ", ".join(names) if names else "(none added yet — use `/paneladder` first)"
                    await interaction.followup.send(f"❌ Unknown panel `{panel}`.\nAvailable: {hint}")
                    return
                panel_update_pending = True
                new_panel_value = prow["pathname"]

        if not updates and not slot_updates and not panel_update_pending:
            await interaction.followup.send("❌ Nothing to change — give at least one field besides playername.")
            return

        if slot_updates:
            current = get_card(playername)
            final1 = slot_updates.get("playstyle1", current["playstyle1"])
            final2 = slot_updates.get("playstyle2", current["playstyle2"])
            if final1 and final2 and final1.lower() == final2.lower():
                await interaction.followup.send("❌ A card can't have the same playstyle twice — pick two different ones.")
                return

        if updates:
            update_card(playername, **updates)
        if slot_updates:
            set_playstyles(playername, **slot_updates)
        if panel_update_pending:
            set_card_panel(playername, new_panel_value)

        try:
            await _regenerate_and_send(interaction, playername, "✏️ Card updated")
            note = _missing_logo_note(list(slot_updates.values()))
            if note:
                await interaction.followup.send(note.strip())
        except Exception as e:
            await interaction.followup.send(f"⚠️ Card updated in the database, but image regeneration failed: `{e}`")

    # ── /bulkbackground ─────────────────────────────────────────────
    @app_commands.command(
        name="bulkbackground",
        description="Set the background for many cards at once",
    )
    @app_commands.describe(
        pathname="Background template to apply (added via /bgadder)",
        universal="True = apply to every card. False = only the cards listed in playername",
        playername="Comma-separated list of cards (ignored if universal is True)",
    )
    @app_commands.autocomplete(pathname=background_autocomplete)
    @is_owner()
    async def bulkbackground(
        self,
        interaction: discord.Interaction,
        pathname: str,
        universal: bool,
        playername: str | None = None,
    ):
        await interaction.response.defer()

        bg = get_background(pathname)
        if bg is None:
            names = list_backgrounds()
            hint = ", ".join(names) if names else "(none added yet)"
            await interaction.followup.send(f"❌ Unknown background `{pathname}`.\nAvailable: {hint}")
            return

        if universal:
            targets = list_playernames()
        else:
            if not playername:
                await interaction.followup.send(
                    "❌ `playername` is required when `universal` is False — "
                    "give a comma-separated list, e.g. `Virat Kohli, Glenn Maxwell`."
                )
                return
            targets = [p.strip() for p in playername.split(",") if p.strip()]

        if not targets:
            await interaction.followup.send("❌ No cards to update.")
            return

        updated: list[str] = []
        missing: list[str] = []
        failed: list[str] = []

        for name in targets:
            if not card_exists(name):
                missing.append(name)
                continue
            update_card(name, background=bg["pathname"])
            try:
                card = get_card(name)
                buf = await generate_card_image(card, bg["local_path"])
                out_path = GENERATED_DIR / f"{name.strip().lower().replace(' ', '_')}.png"
                with open(out_path, "wb") as f:
                    f.write(buf.getvalue())
                set_card_image_path(name, str(out_path))
                await card_cache.warm(str(out_path))
                updated.append(card["playername"])
            except Exception as e:
                failed.append(f"{name} (`{e}`)")

        summary = [f"🖼️ Background set to `{bg['pathname']}` for **{len(updated)}** card(s)."]
        if failed:
            summary.append(f"⚠️ Image regeneration failed for **{len(failed)}**: " + ", ".join(failed)[:800])
        if missing:
            summary.append(f"❌ No card found for **{len(missing)}**: " + ", ".join(missing)[:800])

        # Discord message content is capped at 2000 chars. With many cards the
        # "updated" list alone can blow past that, so keep the summary short
        # and attach the full name list as a text file instead of inlining it.
        full_list = "\n".join(updated) if updated else "(none)"
        file = discord.File(io.BytesIO(full_list.encode()), filename="updated_cards.txt")

        text = "\n".join(summary)
        if len(text) > 1900:
            text = text[:1900] + "…"

        await interaction.followup.send(content=text, file=file)

    # ── /bgadder ─────────────────────────────────────────────────────
    @app_commands.command(name="bgadder", description="Add a new card background template")
    @app_commands.describe(
        link="Direct link to the background template image",
        pathname="Name to pick this background by in /cardmaker and /editcard",
    )
    @is_owner()
    async def bgadder(self, interaction: discord.Interaction, link: str, pathname: str):
        await interaction.response.defer()

        if background_exists(pathname):
            await interaction.followup.send(f"❌ A background named `{pathname}` already exists — pick a different name.")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(link, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
        except Exception as e:
            await interaction.followup.send(f"❌ Couldn't download that link: `{e}`")
            return

        local_path = BACKGROUNDS_DIR / f"{pathname.strip().lower().replace(' ', '_')}.png"
        with open(local_path, "wb") as f:
            f.write(data)

        add_background(pathname, link, str(local_path), interaction.user.id)
        await interaction.followup.send(f"✅ Background **{pathname}** added. Pick it in `/cardmaker`'s `background` option.")

    # ── /paneladder ──────────────────────────────────────────────────
    @app_commands.command(
        name="paneladder",
        description="Add a new stats panel overlay image (always drawn in front of the foreground)",
    )
    @app_commands.describe(
        url="Direct link to the panel image (transparent PNG works best)",
        pathname="Name to pick this panel by in /editcard's `panel` option",
    )
    @is_owner()
    async def paneladder(self, interaction: discord.Interaction, url: str, pathname: str):
        await interaction.response.defer()

        if panel_exists(pathname):
            await interaction.followup.send(f"❌ A panel named `{pathname}` already exists — pick a different name.")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
            # Decode it now so a link that isn't a real image fails here, not later on a card.
            Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception as e:
            await interaction.followup.send(f"❌ Couldn't download that link as an image: `{e}`")
            return

        local_path = PANELS_DIR / f"{pathname.strip().lower().replace(' ', '_')}.png"
        with open(local_path, "wb") as f:
            f.write(data)

        add_panel(pathname, url, str(local_path), interaction.user.id)
        await interaction.followup.send(
            f"✅ Panel **{pathname}** added. Put it on a card with "
            f"`/editcard playername:<card> panel:{pathname}`, then nudge it with `/panelfixer`.\n"
            f"ℹ️ The panel always renders IN FRONT of the foreground — the foreground never "
            f"covers it, whatever position/size you set."
        )

    # ── /panelremover ────────────────────────────────────────────────
    @app_commands.command(name="panelremover", description="Remove a stats panel overlay from the panel list")
    @app_commands.describe(pathname="Which panel to remove")
    @app_commands.autocomplete(pathname=panel_autocomplete)
    @is_owner()
    async def panelremover(self, interaction: discord.Interaction, pathname: str):
        await interaction.response.defer()

        removed = remove_panel(pathname)
        if removed is None:
            names = list_panels()
            hint = ", ".join(names) if names else "(none added yet)"
            await interaction.followup.send(f"❌ No panel found named `{pathname}`.\nAvailable: {hint}")
            return

        # Best-effort cleanup of the local file — the DB rows are already
        # gone either way, so a missing/locked file here is not an error.
        try:
            os.remove(removed["local_path"])
        except OSError:
            pass

        await interaction.followup.send(
            f"🗑️ Removed panel **{removed['pathname']}**. "
            f"Any cards that had it set now render without a panel overlay."
        )

    # ── /foregroundfix ───────────────────────────────────────────────
    @app_commands.command(name="foregroundfix", description="Nudge position and/or resize a card's foreground cutout")
    @app_commands.describe(
        playername="Which card to adjust",
        foregroundup="Pixels to move the cutout up",
        foregrounddown="Pixels to move the cutout down",
        foregroundleft="Pixels to move the cutout left",
        foregroundright="Pixels to move the cutout right",
        foregroundsizebig="Percent to make the cutout bigger, e.g. 10 = +10% size",
        foregroundsizesmall="Percent to make the cutout smaller, e.g. 10 = -10% size",
    )
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def foregroundfix(
        self,
        interaction: discord.Interaction,
        playername: str,
        foregroundup: app_commands.Range[int, 0, 2000] = 0,
        foregrounddown: app_commands.Range[int, 0, 2000] = 0,
        foregroundleft: app_commands.Range[int, 0, 2000] = 0,
        foregroundright: app_commands.Range[int, 0, 2000] = 0,
        foregroundsizebig: app_commands.Range[int, 0, 500] = 0,
        foregroundsizesmall: app_commands.Range[int, 0, 90] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        new_offset_x = card["offset_x"] - foregroundleft + foregroundright
        new_offset_y = card["offset_y"] - foregroundup + foregrounddown
        new_scale_pct = card["scale_pct"] + foregroundsizebig - foregroundsizesmall
        new_scale_pct = max(MIN_SCALE_PCT, min(MAX_SCALE_PCT, new_scale_pct))

        update_card_offset(playername, new_offset_x, new_offset_y, new_scale_pct)

        try:
            await _regenerate_and_send(
                interaction, playername,
                f"🔧 Adjusted (offset x={new_offset_x}, y={new_offset_y}, size={new_scale_pct}%)",
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Offset/size saved, but image regeneration failed: `{e}`")

    # ── /logoadderofplaystyle ────────────────────────────────────────
    @app_commands.command(name="logoadderofplaystyle", description="Add a playstyle logo to the logo list")
    @app_commands.describe(
        url="Direct link to the logo image (transparent PNG works best)",
        pathname="Name of this logo, e.g. Swing King. Use the narrative's exact name to link it automatically",
    )
    @app_commands.autocomplete(pathname=narrative_name_autocomplete)
    @is_owner()
    async def logoadderofplaystyle(self, interaction: discord.Interaction, url: str, pathname: str):
        await interaction.response.defer()

        if playstyle_logo_exists(pathname):
            await interaction.followup.send(f"❌ A logo named `{pathname}` already exists — pick a different name.")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
            # Decode it now so a link that isn't a real image fails here, not later on a card.
            logo = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception as e:
            await interaction.followup.send(f"❌ Couldn't download that link as an image: `{e}`")
            return

        local_path = LOGOS_DIR / f"{pathname.strip().lower().replace(' ', '_')}.png"
        logo.save(local_path, format="PNG")

        add_playstyle_logo(pathname, url, str(local_path), interaction.user.id)
        await interaction.followup.send(
            f"✅ Playstyle logo **{pathname}** added. Pick it with `/setplaystylelogo`."
        )

    # ── /setplaystylelogo ────────────────────────────────────────────
    @app_commands.command(name="setplaystylelogo", description="Put up to 2 playstyle logos on a player card")
    @app_commands.describe(
        playername="Which card to put the logo(s) on",
        logo1="First playstyle logo",
        logo2="Second playstyle logo (optional — leave empty for just one logo)",
    )
    @app_commands.autocomplete(
        playername=playername_autocomplete, logo1=logo_autocomplete, logo2=logo_autocomplete,
    )
    @is_owner()
    async def setplaystylelogo(
        self,
        interaction: discord.Interaction,
        playername: str,
        logo1: str,
        logo2: str | None = None,
    ):
        await interaction.response.defer()

        if not card_exists(playername):
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        chosen = []
        for name in (logo1, logo2):
            if name is None:
                chosen.append(None)
                continue
            row = get_playstyle_logo(name)
            if row is None:
                names = list_playstyle_logos()
                hint = ", ".join(names) if names else "(none added yet — use `/logoadderofplaystyle` first)"
                await interaction.followup.send(f"❌ Unknown logo `{name}`.\nAvailable: {hint}")
                return
            chosen.append(row["pathname"])

        if chosen[0] and chosen[1] and chosen[0].lower() == chosen[1].lower():
            await interaction.followup.send("❌ logo1 and logo2 can't be the same logo.")
            return

        # Replaces both slots: leaving logo2 empty removes any second logo.
        set_playstyles(playername, chosen[0], chosen[1])

        try:
            await _regenerate_and_send(interaction, playername, "🎯 Playstyle logo(s) set")
        except Exception as e:
            await interaction.followup.send(f"⚠️ Logos saved, but image regeneration failed: `{e}`")

    # ── /logofixer ───────────────────────────────────────────────────
    @app_commands.command(name="logofixer", description="Move and/or resize a card's playstyle logos")
    @app_commands.describe(
        playername="Which card to adjust",
        target="Adjust both logos together, or just logo 1 / logo 2 (default: both)",
        logoup="Pixels to move the logo up",
        logodown="Pixels to move the logo down",
        logoleft="Pixels to move the logo left",
        logoright="Pixels to move the logo right",
        logosizebig="Percent to make the logo bigger, e.g. 10 = +10% size",
        logosizesmall="Percent to make the logo smaller, e.g. 10 = -10% size",
    )
    @app_commands.choices(target=TARGET_CHOICES)
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def logofixer(
        self,
        interaction: discord.Interaction,
        playername: str,
        target: str = "both",
        logoup: app_commands.Range[int, 0, 2000] = 0,
        logodown: app_commands.Range[int, 0, 2000] = 0,
        logoleft: app_commands.Range[int, 0, 2000] = 0,
        logoright: app_commands.Range[int, 0, 2000] = 0,
        logosizebig: app_commands.Range[int, 0, 500] = 0,
        logosizesmall: app_commands.Range[int, 0, 90] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        slots = (1, 2) if target == "both" else (int(target),)
        slots = [s for s in slots if card[f"playstyle{s}"]]
        if not slots:
            await interaction.followup.send(
                f"❌ **{card['playername']}** has no logo in that slot — set one with `/setplaystylelogo` first."
            )
            return

        summary = []
        for s in slots:
            new_dx = card[f"logo{s}_dx"] - logoleft + logoright
            new_dy = card[f"logo{s}_dy"] - logoup + logodown
            new_scale = card[f"logo{s}_scale"] + logosizebig - logosizesmall
            new_scale = max(MIN_SCALE_PCT, min(MAX_SCALE_PCT, new_scale))
            update_logo_layout(playername, s, new_dx, new_dy, new_scale)
            summary.append(f"logo {s}: x={new_dx}, y={new_dy}, size={new_scale}%")

        try:
            await _regenerate_and_send(interaction, playername, "🔧 Logo adjusted (" + "; ".join(summary) + ")")
        except Exception as e:
            await interaction.followup.send(f"⚠️ Logo position/size saved, but image regeneration failed: `{e}`")

    # ── /countrylogoadder ────────────────────────────────────────────
    @app_commands.command(
        name="countrylogoadder",
        description="Add a country logo - it shows just above the country name on every card of that country",
    )
    @app_commands.describe(
        country="Which country this logo is for",
        url="Direct link to the logo image (transparent PNG works best)",
    )
    @app_commands.autocomplete(country=country_autocomplete)
    @is_owner()
    async def countrylogoadder(self, interaction: discord.Interaction, country: str, url: str):
        await interaction.response.defer()

        resolved = resolve_country(country)
        if resolved is None:
            await interaction.followup.send(
                f"❌ Unknown country `{country}` — pick one from the autocomplete list."
            )
            return
        country_name, _emoji = resolved

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
            if len(data) > 15_000_000:
                raise ValueError("image is bigger than 15 MB")
            # Decode it now so a link that isn't a real image fails here, not later on a card.
            logo = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception as e:
            await interaction.followup.send(f"❌ Couldn't download that link as an image: `{e}`")
            return

        # country_name comes from the fixed country list, so this file name is always safe.
        local_path = COUNTRY_LOGOS_DIR / f"{country_name.strip().lower().replace(' ', '_')}.png"
        logo.save(local_path, format="PNG")
        replaced = add_country_logo(country_name, url, str(local_path), interaction.user.id)

        verb = "replaced" if replaced else "added"
        await interaction.followup.send(
            f"✅ Country logo for **{country_name}** {verb}. It now shows above the country name on every "
            f"{country_name} card (cards update the next time they are generated — e.g. with "
            f"`/countrylogofixer` or `/editcard`). Fine-tune one card with `/countrylogofixer`."
        )

    # ── /countrylogofixer ────────────────────────────────────────────
    @app_commands.command(name="countrylogofixer", description="Move and/or resize a card's country logo")
    @app_commands.describe(
        playername="Which card to adjust",
        logoup="Pixels to move the country logo up",
        logodown="Pixels to move the country logo down",
        logoleft="Pixels to move the country logo left",
        logoright="Pixels to move the country logo right",
        logosizebig="Percent to make the logo bigger, e.g. 10 = +10% size",
        logosizesmall="Percent to make the logo smaller, e.g. 10 = -10% size",
    )
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def countrylogofixer(
        self,
        interaction: discord.Interaction,
        playername: str,
        logoup: app_commands.Range[int, 0, 2000] = 0,
        logodown: app_commands.Range[int, 0, 2000] = 0,
        logoleft: app_commands.Range[int, 0, 2000] = 0,
        logoright: app_commands.Range[int, 0, 2000] = 0,
        logosizebig: app_commands.Range[int, 0, 500] = 0,
        logosizesmall: app_commands.Range[int, 0, 90] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        if get_country_logo(card["country"]) is None:
            have = list_country_logos()
            hint = ", ".join(have) if have else "(none added yet)"
            await interaction.followup.send(
                f"❌ There is no logo for **{card['country']}** yet — add one with "
                f"`/countrylogoadder country:{card['country']}` first.\nCountries with a logo: {hint}"
            )
            return

        new_dx = card["countrylogo_dx"] - logoleft + logoright
        new_dy = card["countrylogo_dy"] - logoup + logodown
        new_scale = card["countrylogo_scale"] + logosizebig - logosizesmall
        new_scale = max(MIN_SCALE_PCT, min(MAX_SCALE_PCT, new_scale))

        update_country_logo_layout(playername, new_dx, new_dy, new_scale)

        try:
            await _regenerate_and_send(
                interaction, playername,
                f"🔧 Country logo adjusted (x={new_dx}, y={new_dy}, size={new_scale}%)",
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Country logo position/size saved, but image regeneration failed: `{e}`")

    # ── /playtypefixer ───────────────────────────────────────────────
    @app_commands.command(
        name="playtypefixer",
        description="Move and/or resize the batting-hand text (RIGHT / LEFT HAND BAT) on a card",
    )
    @app_commands.describe(
        playername="Which card to adjust",
        playtypeup="Pixels to move the text up",
        playtypedown="Pixels to move the text down",
        playtypeleft="Pixels to move the text left",
        playtyperight="Pixels to move the text right",
        playtypesizebig="Make the text bigger, e.g. 4 = +4px",
        playtypesizesmall="Make the text smaller, e.g. 4 = -4px",
    )
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def playtypefixer(
        self,
        interaction: discord.Interaction,
        playername: str,
        playtypeup: app_commands.Range[int, 0, 500] = 0,
        playtypedown: app_commands.Range[int, 0, 500] = 0,
        playtypeleft: app_commands.Range[int, 0, 500] = 0,
        playtyperight: app_commands.Range[int, 0, 500] = 0,
        playtypesizebig: app_commands.Range[int, 0, 60] = 0,
        playtypesizesmall: app_commands.Range[int, 0, 15] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        if not card["batting_hand"]:
            await interaction.followup.send(
                f"❌ **{card['playername']}** has no batting hand set, so there is no text to move — "
                f"set one with `/editcard playername:{card['playername']} battinghand:<Right/Left>`."
            )
            return

        new_dx = card["hand_dx"] - playtypeleft + playtyperight
        new_dy = card["hand_dy"] - playtypeup + playtypedown
        new_size = max(8, min(80, card["hand_size"] + playtypesizebig - playtypesizesmall))

        update_card_layout(playername, hand_dx=new_dx, hand_dy=new_dy, hand_size=new_size)

        try:
            await _regenerate_and_send(
                interaction, playername,
                f"🔧 Play type text adjusted (x={new_dx}, y={new_dy}, size={new_size}px)",
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Play type position/size saved, but image regeneration failed: `{e}`")

    # ── /panelfixer ──────────────────────────────────────────────────
    @app_commands.command(name="panelfixer", description="Move and/or resize a card's stats panel overlay")
    @app_commands.describe(
        playername="Which card to adjust",
        panelup="Pixels to move the panel up",
        paneldown="Pixels to move the panel down",
        panelleft="Pixels to move the panel left",
        panelright="Pixels to move the panel right",
        panelsizebig="Percent to make the panel bigger, e.g. 10 = +10% size",
        panelsizesmall="Percent to make the panel smaller, e.g. 10 = -10% size",
    )
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def panelfixer(
        self,
        interaction: discord.Interaction,
        playername: str,
        panelup: app_commands.Range[int, 0, 2000] = 0,
        paneldown: app_commands.Range[int, 0, 2000] = 0,
        panelleft: app_commands.Range[int, 0, 2000] = 0,
        panelright: app_commands.Range[int, 0, 2000] = 0,
        panelsizebig: app_commands.Range[int, 0, 500] = 0,
        panelsizesmall: app_commands.Range[int, 0, 90] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        if not card["panel_image"]:
            await interaction.followup.send(
                f"❌ **{card['playername']}** has no panel set yet — pick one first with "
                f"`/editcard playername:{card['playername']} panel:<name>`."
            )
            return

        new_dx = card["panelimg_dx"] - panelleft + panelright
        new_dy = card["panelimg_dy"] - panelup + paneldown
        new_scale = card["panelimg_scale"] + panelsizebig - panelsizesmall
        new_scale = max(MIN_SCALE_PCT, min(MAX_SCALE_PCT, new_scale))

        update_panel_layout(playername, new_dx, new_dy, new_scale)

        try:
            await _regenerate_and_send(
                interaction, playername,
                f"🔧 Panel adjusted (x={new_dx}, y={new_dy}, size={new_scale}%)",
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Panel position/size saved, but image regeneration failed: `{e}`")

    # ── /cardlayout ──────────────────────────────────────────────────
    @app_commands.command(
        name="cardlayout",
        description="Move/resize the name, OVR, stats, or country text on a card",
    )
    @app_commands.describe(
        playername="Which card to adjust",
        nameup="Move the display name up (px)",
        namedown="Move the display name down (px)",
        nameleft="Move the display name left (px)",
        nameright="Move the display name right (px)",
        namesizebig="Make the display name bigger, e.g. 10 = +10px",
        namesizesmall="Make the display name smaller, e.g. 10 = -10px",
        ovrup="Move the OVR number + role word up (px)",
        ovrdown="Move the OVR number + role word down (px)",
        ovrsizebig="Make the OVR number bigger, e.g. 10 = +10px",
        ovrsizesmall="Make the OVR number smaller, e.g. 10 = -10px",
        statsup="Move the BAT/BOWL hexagon numbers up (px)",
        statsdown="Move the BAT/BOWL hexagon numbers down (px)",
        statsleft="Move the BAT/BOWL hexagon numbers left (px)",
        statsright="Move the BAT/BOWL hexagon numbers right (px)",
        statssizebig="Make the BAT/BOWL numbers bigger, e.g. 10 = +10px",
        statssizesmall="Make the BAT/BOWL numbers smaller, e.g. 10 = -10px",
        countryup="Move the country text up (px)",
        countrydown="Move the country text down (px)",
        roleup="Move the batting role word (BATTER/BOWLER...) up (px)",
        roledown="Move the batting role word down (px)",
        handup="Move the batting-hand text (RIGHT/LEFT HAND BAT) up (px)",
        handdown="Move the batting-hand text down (px)",
    )
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def cardlayout(
        self,
        interaction: discord.Interaction,
        playername: str,
        nameup: app_commands.Range[int, 0, 2000] = 0,
        namedown: app_commands.Range[int, 0, 2000] = 0,
        nameleft: app_commands.Range[int, 0, 2000] = 0,
        nameright: app_commands.Range[int, 0, 2000] = 0,
        namesizebig: app_commands.Range[int, 0, 300] = 0,
        namesizesmall: app_commands.Range[int, 0, 110] = 0,
        ovrup: app_commands.Range[int, 0, 2000] = 0,
        ovrdown: app_commands.Range[int, 0, 2000] = 0,
        ovrsizebig: app_commands.Range[int, 0, 300] = 0,
        ovrsizesmall: app_commands.Range[int, 0, 40] = 0,
        statsup: app_commands.Range[int, 0, 2000] = 0,
        statsdown: app_commands.Range[int, 0, 2000] = 0,
        statsleft: app_commands.Range[int, 0, 2000] = 0,
        statsright: app_commands.Range[int, 0, 2000] = 0,
        statssizebig: app_commands.Range[int, 0, 300] = 0,
        statssizesmall: app_commands.Range[int, 0, 60] = 0,
        countryup: app_commands.Range[int, 0, 500] = 0,
        countrydown: app_commands.Range[int, 0, 500] = 0,
        roleup: app_commands.Range[int, 0, 500] = 0,
        roledown: app_commands.Range[int, 0, 500] = 0,
        handup: app_commands.Range[int, 0, 500] = 0,
        handdown: app_commands.Range[int, 0, 500] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        new_name_dx = card["name_dx"] - nameleft + nameright
        new_name_dy = card["name_dy"] - nameup + namedown
        new_name_size = max(20, card["name_size"] + namesizebig - namesizesmall)

        new_ovr_dx = card["ovr_dx"]
        new_ovr_dy = card["ovr_dy"] - ovrup + ovrdown
        new_ovr_size = max(10, card["ovr_size"] + ovrsizebig - ovrsizesmall)

        new_stats_dx = card["stats_dx"] - statsleft + statsright
        new_stats_dy = card["stats_dy"] - statsup + statsdown
        new_stats_size = max(15, card["stats_size"] + statssizebig - statssizesmall)

        new_country_dy = card["country_dy"] - countryup + countrydown

        new_role_dy = card["role_dy"] - roleup + roledown
        new_hand_dy = card["hand_dy"] - handup + handdown

        update_card_layout(
            playername,
            name_dx=new_name_dx, name_dy=new_name_dy, name_size=new_name_size,
            ovr_dx=new_ovr_dx, ovr_dy=new_ovr_dy, ovr_size=new_ovr_size,
            stats_dx=new_stats_dx, stats_dy=new_stats_dy, stats_size=new_stats_size,
            country_dy=new_country_dy,
            role_dy=new_role_dy, hand_dy=new_hand_dy,
        )

        try:
            await _regenerate_and_send(
                interaction, playername,
                f"🔧 Layout adjusted (name size={new_name_size}, ovr size={new_ovr_size}, "
                f"stats size={new_stats_size})",
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Layout saved, but image regeneration failed: `{e}`")

    # ── /removecard ──────────────────────────────────────────────────
    @app_commands.command(name="removecard", description="Delete a player card from the database")
    @app_commands.describe(cardname="Which card to remove")
    @app_commands.autocomplete(cardname=playername_autocomplete)
    @is_owner()
    async def removecard(self, interaction: discord.Interaction, cardname: str):
        await interaction.response.defer()

        deleted = delete_card(cardname)
        if deleted is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{cardname}**.\nExisting cards: {hint}")
            return

        # Best-effort cleanup of the generated image file �� the DB row is
        # already gone either way, so a missing/locked file here is not an error.
        if deleted["image_path"]:
            try:
                os.remove(deleted["image_path"])
            except OSError:
                pass

        await interaction.followup.send(
            f"🗑️ Removed **{deleted['playername']}**'s card from the database."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(CardMakerCog(bot))
