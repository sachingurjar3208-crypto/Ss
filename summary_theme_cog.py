"""Match SUMMARY themes (Halloween / Diwali / Christmas) - see summary_theme.py.

Owner only (slash commands, hidden from everybody else, main server only)
    /cssbset      themeimage:<image> pathname:<Halloween|Diwali|Christmas>
                  set (or replace) the background image of a theme. Image = same layout as the
                  normal summary card, best 1536x1024 (3:2). The text design is built in.
    /cssbremove   pathname:<theme> [user:<@user>]
                  without `user`: delete the image of the theme (players keep owning it, the normal
                  summary shows until you set an image again). With `user`: take the theme from that player.
    /cssbgive     user:<@user> pathname:<theme>
                  give a theme to a player (a player can also win one in a Star Pass).

Players (text commands, only for themes they own)
    cssb                  see your themes and which one is on
    cssb use halloween    turn an owned theme on (used when YOU win, or when the other player has none)
    cssb off              back to the normal summary
"""
from __future__ import annotations

import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands

import summary_image
import summary_theme as st
from bot_guard import ADMIN_GUILD_ID, OWNER_ID, MaintenanceActive
from security import esc

THEME_CHOICES = [app_commands.Choice(name=st.label(n), value=n) for n in st.NAMES]


def owner_slash():
    """Slash command that only the bot OWNER can run: hidden from members, exists only in the main server."""
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == OWNER_ID

    def decorator(func):
        func = app_commands.check(predicate)(func)
        func = app_commands.default_permissions()(func)
        func = app_commands.guild_only()(func)
        func = app_commands.guilds(ADMIN_GUILD_ID)(func)
        return func

    return decorator


class SummaryThemeCog(commands.Cog, name="SummaryTheme"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await asyncio.to_thread(st.ensure_tables)
        except Exception as e:
            print(f"[summary-theme] could not create tables: {e}")

    # ── Owner: /cssbset ──────────────────────────────────────────────────────
    @app_commands.command(name="cssbset", description="Set the image of a summary theme (Halloween, Diwali, Christmas)")
    @app_commands.describe(
        themeimage="Background image for the summary card (same layout as the normal summary, best 1536x1024)",
        pathname="Which theme this image is for",
    )
    @app_commands.choices(pathname=THEME_CHOICES)
    @owner_slash()
    async def cssbset(self, interaction: discord.Interaction, themeimage: discord.Attachment,
                      pathname: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        if not (themeimage.content_type or "").startswith("image/"):
            await interaction.followup.send("The theme image must be a PNG or JPG file.", ephemeral=True)
            return
        if themeimage.size > st.MAX_IMG_BYTES:
            await interaction.followup.send("That image is too big (max 8 MB).", ephemeral=True)
            return
        try:
            raw = await themeimage.read()
            data = await asyncio.to_thread(summary_image.prepare_theme_image, raw)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("I could not download that image. Please try again.", ephemeral=True)
            return
        await asyncio.to_thread(st.set_image, pathname.value, data)

        # preview with sample numbers, so you see the text design on your image right away
        try:
            png = await asyncio.to_thread(summary_image.generate_summary_card, _SAMPLE, pathname.value, data)
            file = discord.File(io.BytesIO(png), filename="summary_theme_preview.png")
        except Exception as e:
            print(f"[summary-theme] preview failed: {type(e).__name__}: {e}")
            file = None
        msg = (f"**{esc(pathname.name)}** summary theme saved. Players who own it and turn it on "
               f"(`cssb use {pathname.value}`) get it at the end of their matches.")
        if file is not None:
            await interaction.followup.send(msg + "\nPreview with sample numbers:", file=file, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)

    # ── Owner: /cssbremove ───────────────────────────────────────────────────
    @app_commands.command(name="cssbremove", description="Remove a summary theme image (or take a theme from one player)")
    @app_commands.describe(
        pathname="Which theme",
        user="Optional: take this theme away from ONE player instead of deleting the image",
    )
    @app_commands.choices(pathname=THEME_CHOICES)
    @owner_slash()
    async def cssbremove(self, interaction: discord.Interaction, pathname: app_commands.Choice[str],
                         user: discord.Member | None = None):
        if user is not None:
            res = await asyncio.to_thread(st.take, user.id, pathname.value)
            if res:
                text = f"Took the **{esc(pathname.name)}** theme from {user.mention}."
            else:
                text = f"{user.mention} doesn't own the **{esc(pathname.name)}** theme."
        else:
            res = await asyncio.to_thread(st.remove_image, pathname.value)
            if res:
                text = (f"The **{esc(pathname.name)}** image was deleted. Players keep owning the theme, "
                        f"but they get the normal summary until you set a new image with /cssbset.")
            else:
                text = f"There is no image set for **{esc(pathname.name)}**."
        await interaction.response.send_message(text, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    # ── Owner: /cssbgive ─────────────────────────────────────────────────────
    @app_commands.command(name="cssbgive", description="Give a summary theme to a player")
    @app_commands.describe(user="Who gets the theme", pathname="Which theme")
    @app_commands.choices(pathname=THEME_CHOICES)
    @owner_slash()
    async def cssbgive(self, interaction: discord.Interaction, user: discord.Member,
                       pathname: app_commands.Choice[str]):
        if user.bot:
            await interaction.response.send_message("Bots can't own a theme.", ephemeral=True)
            return
        res = await asyncio.to_thread(st.give, user.id, pathname.value, "owner")
        has_img = await asyncio.to_thread(st.has_image, pathname.value)
        if res:
            text = f"Gave the **{esc(pathname.name)}** summary theme to {user.mention}."
        else:
            text = f"{user.mention} already owns the **{esc(pathname.name)}** summary theme."
        if not has_img:
            text += f"\nNote: no image is set for {esc(pathname.name)} yet. Use /cssbset first, until then the normal summary shows."
        await interaction.response.send_message(text, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
        if res:
            try:
                await user.send(f"You received the **{pathname.name}** match summary theme! "
                                f"Turn it on with `cssb use {pathname.value}`.")
            except discord.HTTPException:
                pass                                              # DMs closed: the theme is still saved

    # ── Players: cssb ────────────────────────────────────────────────────────
    @commands.group(name="cssb", aliases=["summarytheme"], invoke_without_command=True)
    async def cssb(self, ctx: commands.Context):
        """Your summary themes."""
        mine = await asyncio.to_thread(st.owned, ctx.author.id)
        on = await asyncio.to_thread(st.equipped, ctx.author.id)
        if not mine:
            await ctx.send("You don't own any summary theme yet. You can win one in a Star Pass event.")
            return
        lines = [f"{st.label(n)}{'  (ON)' if n == on else ''}" for n in mine]
        await ctx.send("```\nYour summary themes\n" + "\n".join(lines) +
                       "\n\nTurn on: cssb use <theme>   |   Turn off: cssb off\n```")

    @cssb.command(name="use", aliases=["on", "equip"])
    async def cssb_use(self, ctx: commands.Context, *, pathname: str):
        """cssb use halloween"""
        res = await asyncio.to_thread(st.equip, ctx.author.id, pathname)
        if res == "unknown":
            await ctx.send("Unknown theme. Themes: " + ", ".join(st.label(n) for n in st.NAMES) + ".")
        elif res == "not_owned":
            await ctx.send(f"You don't own the {esc(pathname.strip().title())} theme.")
        else:
            key = st.norm(pathname)
            extra = "" if await asyncio.to_thread(st.has_image, key) else " (its image isn't set yet, so the normal summary shows for now)"
            await ctx.send(f"{st.label(key)} summary theme is ON{extra}.")

    @cssb.command(name="off", aliases=["default", "normal", "remove"])
    async def cssb_off(self, ctx: commands.Context):
        """Back to the normal summary."""
        await asyncio.to_thread(st.unequip, ctx.author.id)
        await ctx.send("Summary theme is OFF. Your matches use the normal summary.")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MaintenanceActive):
            return
        if isinstance(error, app_commands.CheckFailure):
            msg = "Only the bot owner can use this command."
        else:
            print(f"[summary-theme] command error: {type(error).__name__}: {error}")
            msg = "Something went wrong. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


_SAMPLE = {
    "match_id": 100001, "venue": "Cricket Star Stadium", "result": "TEAM A WON BY 12 RUNS", "potm": "Sample Player",
    "team1": {"name": "Team A", "runs": 187, "wickets": 6, "overs": "20.0", "max_overs": "20",
              "batters": [("Player One", 82, 47, False), ("Player Two", 46, 28, True), ("Player Three", 31, 15, False), ("Player Four", 14, 9, False)],
              "bowlers": [("Bowler One", 3, 24, "4.0"), ("Bowler Two", 2, 31, "4.0"), ("Bowler Three", 1, 28, "4.0"), ("Bowler Four", 0, 39, "4.0")]},
    "team2": {"name": "Team B", "runs": 175, "wickets": 9, "overs": "20.0", "max_overs": "20",
              "batters": [("Player Five", 64, 40, False), ("Player Six", 51, 33, False), ("Player Seven", 28, 18, False), ("Player Eight", 12, 10, False)],
              "bowlers": [("Bowler Five", 3, 22, "4.0"), ("Bowler Six", 2, 30, "4.0"), ("Bowler Seven", 1, 35, "4.0"), ("Bowler Eight", 0, 41, "4.0")]},
}


async def setup(bot: commands.Bot):
    await bot.add_cog(SummaryThemeCog(bot))
