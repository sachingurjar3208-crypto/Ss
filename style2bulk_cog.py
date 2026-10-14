"""Admin-only /style2bulk: move up to 10 existing cards to Style 2 in one go.

    /style2bulk background:bronze icon:Bronze icon transparency:On
      -> a box opens: type the player names, one per line (max 10), press Submit.

For every card it
  - switches it to Style 2 with the chosen background / icon / colours,
  - fills the role's stats with random numbers just below the card's OVR
    (OVR 78 -> each stat 70..77). BAT / BOW stay as they are.
        Batter   BAT PWR RUN TIM CLU FIE
        Pacer    BOW ACC SWI PAC CTL RUN
        Spinner  BOW TRN FLT VAR PAC RUN
        Keeper   BAT KEE CAT RUN FIE CLU
        All-rounder (unchanged)  BAT BOW PWR ACC CLU FIE
  - redraws the card and posts the images.
Stats that are already saved are kept; reroll:True makes new random numbers.
"""

from __future__ import annotations

import io

import discord
from discord import app_commands
from discord.ext import commands

import card_db
from bot_guard import MaintenanceActive
from card_db import get_card, get_background, init_card_db
from cardmaker_cog import (
    GENERATED_DIR, is_owner, background_autocomplete, _regenerate_silently,
)
from cardstats_cog import (
    ICON_CHOICES, TRANSPARENCY_CHOICES, NAME_COLOR_CHOICES, LINE_COLOR_CHOICES,
)
from style2bulk_logic import apply_style2_bulk

MAX_CARDS = 10
FILES_PER_MESSAGE = 3          # keeps every Discord message well under the upload size limit


class _NamesModal(discord.ui.Modal, title="Style 2 - player names"):
    names = discord.ui.TextInput(
        label=f"Player names, one per line (max {MAX_CARDS})",
        style=discord.TextStyle.paragraph,
        placeholder="Virat Kohli\nJasprit Bumrah\nRashid Khan",
        required=True,
        max_length=1500,
    )

    def __init__(self, opts: dict):
        super().__init__()
        self.opts = opts

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        seen, wanted = set(), []
        for line in str(self.names.value).splitlines():
            n = line.strip()
            if n and n.lower() not in seen:
                seen.add(n.lower())
                wanted.append(n)
        if not wanted:
            await interaction.followup.send("❌ No player names given.")
            return
        if len(wanted) > MAX_CARDS:
            await interaction.followup.send(
                f"❌ {len(wanted)} names given, the limit is {MAX_CARDS} per run. Send the rest in a second run."
            )
            return

        done, missing, failed = [], [], []
        for name in wanted:
            try:
                info = apply_style2_bulk(name, **self.opts)
            except Exception as e:
                failed.append(f"{name} (`{e}`)")
                continue
            if info is None:
                missing.append(name)
                continue
            if await _regenerate_silently(info["playername"]):
                done.append(info)
            else:
                failed.append(f"{info['playername']} (image not drawn - check its foreground link / background)")

        # Summary text
        lines = []
        for info in done:
            stats = " · ".join(f"{label} {value}" for label, value in info["rows"])
            lines.append(f"**{info['playername']}** ({info['set']}, OVR {info['ovr']}) — {stats}")
        header = f"✅ **{len(done)}** card(s) moved to Style 2"
        if missing:
            header += f"\n❌ Not found: {', '.join(missing)}"
        if failed:
            header += "\n⚠️ Problem: " + "; ".join(failed)
        text = header + ("\n\n" + "\n".join(lines) if lines else "")
        if len(text) > 1900:
            text = text[:1900] + "…"
        await interaction.followup.send(text)

        # Images, a few per message
        batch: list[discord.File] = []
        for info in done:
            card = get_card(info["playername"])
            path = (card["image_path"] if card is not None else None) or str(
                GENERATED_DIR / f"{info['playername'].strip().lower().replace(' ', '_')}.png"
            )
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            batch.append(discord.File(io.BytesIO(data), filename=f"{info['playername']}.png"))
            if len(batch) == FILES_PER_MESSAGE:
                await interaction.followup.send(files=batch)
                batch = []
        if batch:
            await interaction.followup.send(files=batch)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        msg = f"⚠️ Something went wrong: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


class Style2BulkCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_card_db()

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, MaintenanceActive):
            return
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ Only the bot owner or an authorised role can use this command."
        else:
            msg = f"⚠️ Something went wrong: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="style2bulk",
        description="Move up to 10 cards to Style 2 at once (background + random stats just below OVR)",
    )
    @app_commands.describe(
        background="Style 2 background for ALL these cards (from /bgadder). 'default' = built-in purple",
        icon="Gold / Bronze icon in the left column",
        transparency="On = player image fades out above the name (legs hidden), Off = full image. Default: On",
        namecolor="Colour of the display name",
        linecolor="Colour of the line under the display name",
        reroll="Yes = new random stats even if the card already has saved stats",
    )
    @app_commands.choices(
        icon=ICON_CHOICES, transparency=TRANSPARENCY_CHOICES,
        namecolor=NAME_COLOR_CHOICES, linecolor=LINE_COLOR_CHOICES,
    )
    @app_commands.autocomplete(background=background_autocomplete)
    @is_owner()
    async def style2bulk(
        self,
        interaction: discord.Interaction,
        background: str | None = None,
        icon: app_commands.Choice[str] | None = None,
        transparency: app_commands.Choice[str] | None = None,
        namecolor: app_commands.Choice[str] | None = None,
        linecolor: app_commands.Choice[str] | None = None,
        reroll: bool = False,
    ):
        opts: dict = {"reroll": reroll}

        if background is not None:
            if background.strip().lower() in ("default", "none", "purple"):
                opts["bg"] = None
            else:
                row = get_background(background)
                if row is None:
                    names = card_db.list_backgrounds()
                    hint = ", ".join(names) if names else "(none added yet)"
                    await interaction.response.send_message(
                        f"❌ Unknown background `{background}`.\nAvailable: {hint}\n"
                        "(or type `default` for the built-in purple)",
                        ephemeral=True,
                    )
                    return
                opts["bg"] = row["pathname"]
        if icon is not None:
            opts["icon"] = None if icon.value == "none" else icon.value
        # Legs fade is ON unless you pick Off.
        opts["fade_legs"] = not (transparency is not None and transparency.value == "off")
        if namecolor is not None:
            opts["name_color"] = None if namecolor.value == "default" else namecolor.value
        if linecolor is not None:
            opts["line_color"] = None if linecolor.value == "default" else linecolor.value

        await interaction.response.send_modal(_NamesModal(opts))


async def setup(bot: commands.Bot):
    await bot.add_cog(Style2BulkCog(bot))
