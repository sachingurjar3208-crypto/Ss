"""Admin-only /cardstats command: set a card's extra stats and switch it to Style 2.

    /cardstats player:Virat Kohli pwr:94 run:99 tim:95 clu:97 fie:95

- Stats that do not belong to the card's role are ignored (with a note).
- `style` is optional. Default is Style 2 (new design); "Classic" switches back.
- /cardmaker and /editcard are NOT changed and keep working as before.
- Reminder: /refreshcards redraws EVERY card, so cards set to Style 2 are
  redrawn in the new style there too.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot_guard import MaintenanceActive
from card_db import (
    init_card_db, get_card, set_card_style, set_extra_stats, effective_role,
    set_style2_options, get_background, list_backgrounds,
)
from card_stats import allowed_extra_keys, card_stat_details
from cardmaker_cog import (
    MAX_STAT, is_owner, playername_autocomplete, background_autocomplete,
    _regenerate_and_send,
)

STYLE_CHOICES = [
    app_commands.Choice(name="Style 2 (new design)", value="style2"),
    app_commands.Choice(name="Classic (old design)", value="classic"),
]

ICON_CHOICES = [
    app_commands.Choice(name="Gold icon", value="gold"),
    app_commands.Choice(name="Bronze icon", value="bronze"),
    app_commands.Choice(name="No icon", value="none"),
]

TRANSPARENCY_CHOICES = [
    app_commands.Choice(name="On (hide legs - player fades out above the name)", value="on"),
    app_commands.Choice(name="Off (show full player image)", value="off"),
]

NAME_COLOR_CHOICES = [
    app_commands.Choice(name="Default (cream)", value="default"),
    app_commands.Choice(name="Red", value="red"),
    app_commands.Choice(name="Blue", value="blue"),
    app_commands.Choice(name="Yellow", value="yellow"),
    app_commands.Choice(name="Pink", value="pink"),
    app_commands.Choice(name="Green", value="green"),
    app_commands.Choice(name="Orange", value="orange"),
]

LINE_COLOR_CHOICES = [
    app_commands.Choice(name="Default (cream)", value="default"),
    app_commands.Choice(name="Black", value="black"),
    app_commands.Choice(name="White", value="white"),
    app_commands.Choice(name="Blue", value="blue"),
]

ROLE_NAMES = {"BAT": "Batter", "BOWL": "Bowler", "AR": "All-rounder", "WK": "Keeper"}

Stat = app_commands.Range[int, 1, MAX_STAT]


class CardStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_card_db()   # makes sure the card_style / extra_stats columns exist

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, MaintenanceActive):
            return   # the user was already told about maintenance
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ Only the bot owner or an authorised role can use this command."
        else:
            msg = f"⚠️ Something went wrong: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="cardstats",
        description="Set a card's extra stats and switch it to the new Style 2 design",
    )
    @app_commands.describe(
        player="Which card",
        style="Card design (default: Style 2)",
        background="Style 2 background (from /bgadder). Type 'default' for the built-in purple",
        icon="Gold / Bronze icon in the left column (Style 2)",
        transparency="Style 2: On = player image fades out above the name (legs/shorts hidden), Off = full image",
        namecolor="Style 2: colour of the display name (red/blue/yellow/pink/green/orange)",
        linecolor="Style 2: colour of the line under the display name (black/white/blue)",
        pwr="Power hitting (batter / all-rounder / keeper)",
        run="Running between wickets (batter / keeper)",
        tim="Timing (batter)",
        clu="Clutch (all roles)",
        fie="Fielding (batter / bowler / all-rounder)",
        acc="Bowling accuracy (bowler / all-rounder)",
        swi="Swing (bowler)",
        pac="Pace / spin (bowler)",
        kee="Wicketkeeping (keeper)",
        cat="Catching (keeper)",
        trn="Turn (spinner)",
        flt="Flight / drift (spinner)",
        var="Variations - googly, doosra, carrom ball (spinner)",
        ctl="Control (spinner)",
    )
    @app_commands.choices(style=STYLE_CHOICES, icon=ICON_CHOICES, transparency=TRANSPARENCY_CHOICES,
                      namecolor=NAME_COLOR_CHOICES, linecolor=LINE_COLOR_CHOICES)
    @app_commands.autocomplete(player=playername_autocomplete, background=background_autocomplete)
    @is_owner()
    async def cardstats(
        self,
        interaction: discord.Interaction,
        player: str,
        style: app_commands.Choice[str] | None = None,
        background: str | None = None,
        icon: app_commands.Choice[str] | None = None,
        transparency: app_commands.Choice[str] | None = None,
        namecolor: app_commands.Choice[str] | None = None,
        linecolor: app_commands.Choice[str] | None = None,
        pwr: Stat | None = None,
        run: Stat | None = None,
        tim: Stat | None = None,
        clu: Stat | None = None,
        fie: Stat | None = None,
        acc: Stat | None = None,
        swi: Stat | None = None,
        pac: Stat | None = None,
        kee: Stat | None = None,
        cat: Stat | None = None,
        trn: Stat | None = None,
        flt: Stat | None = None,
        var: Stat | None = None,
        ctl: Stat | None = None,
    ):
        await interaction.response.defer()

        card = get_card(player)
        if card is None:
            await interaction.followup.send(f"❌ No card found for **{player}**.")
            return

        # Which of the given stats belong to this card's role?
        role = effective_role(card)
        allowed = allowed_extra_keys(role)
        given = {
            "pwr": pwr, "run": run, "tim": tim, "clu": clu, "fie": fie,
            "acc": acc, "swi": swi, "pac": pac, "kee": kee, "cat": cat,
            "trn": trn, "flt": flt, "var": var, "ctl": ctl,
        }
        given = {k: v for k, v in given.items() if v is not None}
        to_save = {k: v for k, v in given.items() if k in allowed}
        ignored = [k.upper() for k in given if k not in allowed]

        # Style 2 background / icon (only touched when given).
        bg_note = ""
        if background is not None:
            if background.strip().lower() in ("default", "none", "purple"):
                set_style2_options(player, bg=None)
                bg_note = "Background: built-in purple"
            else:
                bg_row = get_background(background)
                if bg_row is None:
                    names = list_backgrounds()
                    hint = ", ".join(names) if names else "(none added yet)"
                    await interaction.followup.send(
                        f"❌ Unknown background `{background}`.\nAvailable: {hint}\n"
                        "(or type `default` for the built-in purple)"
                    )
                    return
                set_style2_options(player, bg=bg_row["pathname"])
                bg_note = f"Background: {bg_row['pathname']}"
        if icon is not None:
            set_style2_options(player, icon=None if icon.value == "none" else icon.value)
            bg_note = (bg_note + " • " if bg_note else "") + f"Icon: {icon.name}"

        if transparency is not None:
            set_style2_options(player, fade_legs=(transparency.value == "on"))
            bg_note = (bg_note + " • " if bg_note else "") + (
                "Transparency: ON (legs hidden)" if transparency.value == "on" else "Transparency: OFF (full image)"
            )

        if namecolor is not None:
            set_style2_options(player, name_color=None if namecolor.value == "default" else namecolor.value)
            bg_note = (bg_note + " • " if bg_note else "") + f"Name colour: {namecolor.name}"
        if linecolor is not None:
            set_style2_options(player, line_color=None if linecolor.value == "default" else linecolor.value)
            bg_note = (bg_note + " • " if bg_note else "") + f"Line colour: {linecolor.name}"

        if to_save:
            set_extra_stats(player, to_save)
        chosen_style = style.value if style is not None else "style2"
        set_card_style(player, chosen_style)

        # Redraw the card and post it.
        verb = "Switched to Style 2" if chosen_style == "style2" else "Switched to Classic"
        if to_save:
            verb = "Stats saved • " + verb
        if bg_note:
            verb += " • " + bg_note
        try:
            await _regenerate_and_send(interaction, player, verb)
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ The card was saved but the image could not be drawn: `{e}`\n"
                "Most often the **foreground link** is dead. Fix it with `/editcard`, "
                "then run this command again."
            )
            return

        # Small embed: the 6 stats the card shows now.
        details = card_stat_details(get_card(player))
        names = [label for label, _, _ in details]
        lines = []
        for label, value, derived in details:
            tag = " *(auto)*" if derived else ""
            lines.append(f"**{label}** {value}{tag}")
        embed = discord.Embed(
            title=f"📊 {card['playername']} — {ROLE_NAMES.get(role, role)}",
            color=discord.Color.purple(),
        )
        embed.add_field(name="Left", value="\n".join(lines[:3]), inline=True)
        embed.add_field(name="Right", value="\n".join(lines[3:]), inline=True)
        footer = "(auto) = not set yet, worked out from the card's other numbers."
        if chosen_style == "classic":
            footer = "Classic design is on, so these stats are not shown on the card."
        embed.set_footer(text=footer)
        if ignored:
            extra_names = ", ".join(n for n in names if n not in ("BAT", "BOW"))
            embed.add_field(
                name="Ignored",
                value=(
                    f"{ROLE_NAMES.get(role, role)} cards use {extra_names}.\n"
                    f"Ignored: {', '.join(ignored)}"
                ),
                inline=False,
            )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(CardStatsCog(bot))
