"""Owner-only stadium slash commands.

    /stadium_edit    change a stadium (or add a new one):
                       soiltype      pick from a list of soil types
                       stadiumtype   Spinner / Medium Pace / Pacer / Batting
                       stadiumlocat  free text (where the stadium is)
                       gif           GIF link shown when a match starts
                     Only the fields you fill in are changed.
    /stadiumgif <stadium> <link>   add or replace a stadium's GIF
    /stadiumgif_remove <stadium>   remove a stadium's GIF
    /stadiumgif_list               which stadiums have a GIF and which don't
    /stadium_list    show every stadium and its details
    /stadium_remove  delete a stadium you added, or reset a built-in one

Only OWNER_ID may run these (same owner as /cardmaker).
"""

from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

import data
import media
import security
from bot_guard import MaintenanceActive, OWNER_ID, admin_only


def is_owner():
    """Admin-only command: hidden from normal members, main server only (see bot_guard.admin_only)."""
    return admin_only()


def normalize_gif_url(url: str) -> str | None:
    """Accept a normal Giphy page link and turn it into a direct GIF link.
    Returns None if it is not an http(s) link."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        return None
    m = re.match(r"^https?://(?:www\.)?giphy\.com/gifs/(?:[\w-]*-)?([A-Za-z0-9]+)/?(?:\?.*)?$", url)
    if m:
        return f"https://media.giphy.com/media/{m.group(1)}/giphy.gif"
    return url


SOIL_CHOICES = [app_commands.Choice(name=s, value=s) for s in data.SOIL_TYPES]
TYPE_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in data.STADIUM_TYPES.items()]


async def stadium_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    names = sorted(st["name"] for st in data.get_all_stadiums())
    return [app_commands.Choice(name=n, value=n) for n in names if cur in n.lower()][:25]


class StadiumCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /stadium_edit ────────────────────────────────────────────────
    @app_commands.command(name="stadium_edit", description="Edit a stadium (soil, type, location, GIF) or add a new one")
    @app_commands.describe(
        stadium="Stadium name (pick from the list, or type a new name to add one)",
        soiltype="Soil type of the pitch",
        stadiumtype="Which bowlers this stadium helps: Spinner / Medium Pace / Pacer / Batting",
        stadiumlocat="Where the stadium is (type anything, e.g. Ahmedabad, India)",
        gif="GIF link shown when a match starts (Giphy links work)",
    )
    @app_commands.autocomplete(stadium=stadium_autocomplete)
    @app_commands.choices(soiltype=SOIL_CHOICES, stadiumtype=TYPE_CHOICES)
    @is_owner()
    async def stadium_edit(
        self,
        interaction: discord.Interaction,
        stadium: str,
        soiltype: app_commands.Choice[str] | None = None,
        stadiumtype: app_commands.Choice[str] | None = None,
        stadiumlocat: str | None = None,
        gif: str | None = None,
    ):
        stadium = stadium.strip()
        if not stadium or len(stadium) > 80:
            await interaction.response.send_message("❌ Stadium name must be 1-80 characters.", ephemeral=True)
            return
        if soiltype is None and stadiumtype is None and stadiumlocat is None and gif is None:
            await interaction.response.send_message(
                "❌ Fill at least one of `soiltype`, `stadiumtype`, `stadiumlocat` or `gif`.", ephemeral=True
            )
            return

        existing = data.find_stadium(stadium)
        if existing:
            stadium = existing["name"]          # keep the exact stored spelling
        elif stadiumtype is None or not (stadiumlocat or "").strip():
            await interaction.response.send_message(
                f"❌ **{stadium}** is a new stadium. For a new stadium, `stadiumtype` and `stadiumlocat` are required.",
                ephemeral=True,
            )
            return

        gif_url = None
        if gif is not None:
            gif_url = normalize_gif_url(gif)
            if gif_url is None:
                await interaction.response.send_message("❌ `gif` must be a link starting with http:// or https://", ephemeral=True)
                return

        media.upsert_stadium(
            stadium,
            location=stadiumlocat.strip()[:100] if stadiumlocat else None,
            soil=soiltype.value if soiltype else None,
            stadium_type=stadiumtype.value if stadiumtype else None,
            gif_url=gif_url,
        )

        st = data.find_stadium(stadium)
        embed = self._stadium_embed(st, title=("✅ Stadium updated" if existing else "✅ New stadium added"))
        await interaction.response.send_message(embed=embed)

    # ── /stadiumgif ──────────────────────────────────────────────────
    @app_commands.command(name="stadiumgif", description="Add or replace a stadium's GIF")
    @app_commands.describe(stadium="Which stadium", link="GIF link (Giphy page links work too)")
    @app_commands.autocomplete(stadium=stadium_autocomplete)
    @is_owner()
    async def stadiumgif(self, interaction: discord.Interaction, stadium: str, link: str):
        st = data.find_stadium(stadium)
        if not st:
            await interaction.response.send_message(
                f"❌ No stadium named **{stadium}**. Pick one from the list, or add it first with `/stadium_edit`.",
                ephemeral=True,
            )
            return
        gif_url = normalize_gif_url(link)
        if gif_url is None:
            await interaction.response.send_message("❌ `link` must start with http:// or https://", ephemeral=True)
            return
        media.upsert_stadium(st["name"], gif_url=gif_url)
        embed = discord.Embed(title="✅ Stadium GIF saved", description=f"**{st['name']}**",
                              color=discord.Color.from_rgb(8, 22, 60))
        embed.set_image(url=gif_url)
        await interaction.response.send_message(embed=embed)

    # ── /stadiumgif_remove ───────────────────────────────────────────
    @app_commands.command(name="stadiumgif_remove", description="Remove a stadium's GIF")
    @app_commands.describe(stadium="Which stadium")
    @app_commands.autocomplete(stadium=stadium_autocomplete)
    @is_owner()
    async def stadiumgif_remove(self, interaction: discord.Interaction, stadium: str):
        st = data.find_stadium(stadium)
        if not st:
            await interaction.response.send_message(f"❌ No stadium named **{stadium}**.", ephemeral=True)
            return
        if not st["gif_url"]:
            await interaction.response.send_message(f"**{st['name']}** has no GIF to remove.", ephemeral=True)
            return
        # "" = removed on purpose, so the built-in default GIF doesn't come back.
        media.upsert_stadium(st["name"], gif_url="")
        media.delete_stadium_gif(st["name"])      # also clear an older-style saved GIF
        await interaction.response.send_message(f"🗑️ GIF removed from **{st['name']}**.")

    # ── /stadiumgif_list ─────────────────────────────────────────────
    @app_commands.command(name="stadiumgif_list", description="Show which stadiums have a GIF")
    @is_owner()
    async def stadiumgif_list(self, interaction: discord.Interaction):
        stadiums = sorted(data.get_all_stadiums(), key=lambda s: s["name"])
        with_gif = [s["name"] for s in stadiums if s["gif_url"]]
        without = [s["name"] for s in stadiums if not s["gif_url"]]
        embed = discord.Embed(title="🎞️ Stadium GIFs", color=discord.Color.from_rgb(8, 22, 60))
        embed.add_field(name=f"✅ GIF set ({len(with_gif)})",
                        value="\n".join(with_gif)[:1000] or "None", inline=False)
        embed.add_field(name=f"❌ No GIF ({len(without)})",
                        value="\n".join(without)[:1000] or "None", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /stadium_list ────────────────────────────────────────────────
    @app_commands.command(name="stadium_list", description="Show all stadiums and their details")
    @is_owner()
    async def stadium_list(self, interaction: discord.Interaction):
        lines = []
        for st in sorted(data.get_all_stadiums(), key=lambda s: s["name"]):
            lines.append(
                f"**{st['name']}** — {st['location'] or 'Unknown'}\n"
                f"└ {data.STADIUM_TYPES.get(st['type'], st['type'])} • "
                f"Soil: {st['soil'] or 'not set'} • GIF: {'✅' if st['gif_url'] else '❌'}"
            )
        # Discord embed description limit is 4096 characters — split into pages.
        pages, cur = [], ""
        for ln in lines:
            if len(cur) + len(ln) + 2 > 3800:
                pages.append(cur)
                cur = ""
            cur += ln + "\n\n"
        if cur:
            pages.append(cur)
        embeds = [
            discord.Embed(title=f"🏟️ Stadiums ({len(lines)})", description=p, color=discord.Color.from_rgb(8, 22, 60))
            for p in pages
        ]
        await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)

    # ── /stadium_remove ──────────────────────────────────────────────
    @app_commands.command(name="stadium_remove", description="Delete a stadium you added, or reset a built-in one to default")
    @app_commands.describe(stadium="Which stadium")
    @app_commands.autocomplete(stadium=stadium_autocomplete)
    @is_owner()
    async def stadium_remove(self, interaction: discord.Interaction, stadium: str):
        builtin = stadium.strip().lower() in {n.lower() for n, _ in data.VENUES}
        removed = media.delete_stadium_row(stadium.strip())
        if not removed:
            await interaction.response.send_message(f"❌ No saved changes found for **{stadium}**.", ephemeral=True)
            return
        if builtin:
            await interaction.response.send_message(f"♻️ **{stadium}** was reset to its default details.")
        else:
            await interaction.response.send_message(f"🗑️ Stadium **{stadium}** was removed.")

    # ── helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _stadium_embed(st: dict, title: str) -> discord.Embed:
        embed = discord.Embed(title=title, color=discord.Color.from_rgb(8, 22, 60))
        embed.add_field(name="Stadium", value=st["name"], inline=False)
        embed.add_field(name="Location", value=st["location"] or "Unknown", inline=True)
        embed.add_field(name="Soil", value=st["soil"] or "Not set", inline=True)
        embed.add_field(
            name="Stadium type",
            value=f"{data.STADIUM_TYPES.get(st['type'], st['type'])} (±{data.GROUND_EFFECT_PCT}% effect)",
            inline=True,
        )
        if st["gif_url"]:
            embed.set_image(url=st["gif_url"])
        else:
            embed.set_footer(text="No GIF set for this stadium yet.")
        return embed

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MaintenanceActive):
            return   # the user was already told about maintenance
        if isinstance(error, app_commands.CheckFailure):
            msg = "🔒 Only the bot owner or an admin can use this command."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(StadiumCog(bot))
