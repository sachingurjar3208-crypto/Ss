"""Match SUMMARY themes (Halloween / Diwali / Christmas) - see summary_theme.py.

Owner only (slash commands, hidden from everybody else, main server only)
    /rcsbset      themeimage:<image> pathname:<Halloween|Diwali|Christmas>
                  set (or replace) the background image of a theme. Image = same layout as the
                  normal summary card, best 1536x1024 (3:2). The text design is built in.
    /rcsbremove   pathname:<theme> [user:<@user>]
                  without `user`: delete the image of the theme (players keep owning it, the normal
                  summary shows until you set an image again). With `user`: take the theme from that player.
    /rcsbgive     user:<@user> pathname:<theme>
                  give a theme to a player (a player can also win one in a Star Pass).

Players (text commands, only for themes they own)
    rcsb                  theme menu: the theme that is ON, all themes (ON / OWNED / LOCKED), a preview,
                          and buttons  < Use this theme | Turn off >  to browse and switch
    rcsb use halloween    turn an owned theme on (used when YOU win, or when the other player has none)
    rcsb off              back to the normal summary
"""
from __future__ import annotations

import asyncio
import io
import time

import discord
from PIL import Image
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


# ── rcsb menu (embed + preview + buttons) ────────────────────────────────────

THEME_COLORS = {"halloween": 0xFF8C26, "diwali": 0xFFC442, "christmas": 0x50CD78}
NORMAL_COLOR = 0x5865F2
MENU_COOLDOWN = 60                                        # seconds between two `rcsb` menus per player
BUTTON_GAP = 1.0                                          # seconds between two button presses per player
_menu_cd = commands.CooldownMapping.from_cooldown(1, MENU_COOLDOWN, commands.BucketType.user)
ANIMATED = {"halloween"}                                  # themes that move (gif) at the end of a match
_PREVIEW_CACHE: dict = {}


def _preview_jpeg(name: str, data: bytes) -> bytes | None:
    """Small preview of a theme with sample numbers (static, so the menu opens fast). Cached."""
    key = (name, len(data), hash(data))
    if key in _PREVIEW_CACHE:
        return _PREVIEW_CACHE[key]
    try:
        png = summary_image.generate_summary_card(_SAMPLE, name, data)
        im = Image.open(io.BytesIO(png)).convert("RGB").resize((960, 640), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        out = buf.getvalue()
    except Exception as e:
        print(f"[summary-theme] menu preview failed: {type(e).__name__}: {e}")
        return None
    if len(_PREVIEW_CACHE) >= 8:
        _PREVIEW_CACHE.pop(next(iter(_PREVIEW_CACHE)))
    _PREVIEW_CACHE[key] = out
    return out


def menu_text(idx: int, mine: list[str], on: str | None, has_img: bool) -> str:
    """The monospace block of the menu. '>' marks the theme you are looking at."""
    rows = []
    for i, n in enumerate(st.NAMES):
        state = "ON" if n == on else ("OWNED" if n in mine else "LOCKED")
        rows.append(f"{'>' if i == idx else ' '} {st.label(n).ljust(10)} {state}")
    name = st.NAMES[idx]
    owned = name in mine
    lines = [
        f"Current    {st.label(on) + ' (ON)' if on else 'Normal summary'}",
        "",
        *rows,
        "",
        f"Viewing    {st.label(name)}",
        f"Card       {'Animated' if name in ANIMATED else 'Static'}",
        f"Status     {'ON' if name == on else ('OWNED' if owned else 'LOCKED')}",
    ]
    if owned and not has_img:
        lines.append("Image      not set yet (normal summary shows)")
    return "```\n" + "\n".join(lines) + "\n```"


class ThemeMenu(discord.ui.View):
    """rcsb menu. Only the person who opened it can press the buttons."""

    def __init__(self, user_id: int, idx: int = 0):
        super().__init__(timeout=180)
        self.user_id, self.idx = user_id, idx
        self.message: discord.Message | None = None
        self._last = 0.0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This menu is not yours. Type rcsb to open your own.", ephemeral=True)
            return False
        now = time.monotonic()
        if now - self._last < BUTTON_GAP:                  # pressed too fast: ignore quietly
            await interaction.response.defer()
            return False
        self._last = now
        return True

    async def render(self):
        """(embed, preview file or None). Also sets which buttons are usable."""
        mine = await asyncio.to_thread(st.owned, self.user_id)
        on = await asyncio.to_thread(st.equipped, self.user_id)
        name = st.NAMES[self.idx]
        data = await asyncio.to_thread(st.get_image, name)
        self.use_btn.disabled = name not in mine or name == on
        self.use_btn.label = "In use" if name == on else "Use this theme"
        self.off_btn.disabled = on is None
        embed = discord.Embed(title="SUMMARY THEMES", description=menu_text(self.idx, mine, on, bool(data)),
                              color=THEME_COLORS.get(name, NORMAL_COLOR))
        embed.set_footer(text=f"{self.idx + 1}/{len(st.NAMES)}  |  Themes come from the Star Pass and owner gifts")
        file = None
        if data:
            jpg = await asyncio.to_thread(_preview_jpeg, name, data)
            if jpg:
                file = discord.File(io.BytesIO(jpg), filename="theme_preview.jpg")
                embed.set_image(url="attachment://theme_preview.jpg")
        return embed, file

    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, file = await self.render()
        await interaction.edit_original_response(embed=embed, attachments=[file] if file else [], view=self)

    @discord.ui.button(label="<", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx = (self.idx - 1) % len(st.NAMES)
        await self._refresh(interaction)

    @discord.ui.button(label="Use this theme", style=discord.ButtonStyle.success, row=0)
    async def use_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await asyncio.to_thread(st.equip, self.user_id, st.NAMES[self.idx])
        await self._refresh(interaction)

    @discord.ui.button(label="Turn off", style=discord.ButtonStyle.danger, row=0)
    async def off_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await asyncio.to_thread(st.unequip, self.user_id)
        await self._refresh(interaction)

    @discord.ui.button(label=">", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx = (self.idx + 1) % len(st.NAMES)
        await self._refresh(interaction)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SummaryThemeCog(commands.Cog, name="SummaryTheme"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await asyncio.to_thread(st.ensure_tables)
        except Exception as e:
            print(f"[summary-theme] could not create tables: {e}")

    # ── Owner: /rcsbset ──────────────────────────────────────────────────────
    @app_commands.command(name="rcsbset", description="Set the image of a summary theme (Halloween, Diwali, Christmas)")
    @app_commands.describe(
        themeimage="Background image for the summary card (same layout as the normal summary, best 1536x1024)",
        pathname="Which theme this image is for",
    )
    @app_commands.choices(pathname=THEME_CHOICES)
    @owner_slash()
    async def rcsbset(self, interaction: discord.Interaction, themeimage: discord.Attachment,
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
            anim = None
            if pathname.value == "halloween":                 # Halloween is animated (gif), others stay a PNG
                import summary_anim
                anim = await asyncio.to_thread(summary_anim.build_animated_summary, _SAMPLE, data, "halloween")
            if anim:
                file = discord.File(io.BytesIO(anim[0]), filename="summary_theme_preview.gif")
            else:
                png = await asyncio.to_thread(summary_image.generate_summary_card, _SAMPLE, pathname.value, data)
                file = discord.File(io.BytesIO(png), filename="summary_theme_preview.png")
        except Exception as e:
            print(f"[summary-theme] preview failed: {type(e).__name__}: {e}")
            file = None
        msg = (f"**{esc(pathname.name)}** summary theme saved. Players who own it and turn it on "
               f"(`rcsb use {pathname.value}`) get it at the end of their matches.")
        if file is not None:
            await interaction.followup.send(msg + "\nPreview with sample numbers:", file=file, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)

    # ── Owner: /rcsbremove ───────────────────────────────────────────────────
    @app_commands.command(name="rcsbremove", description="Remove a summary theme image (or take a theme from one player)")
    @app_commands.describe(
        pathname="Which theme",
        user="Optional: take this theme away from ONE player instead of deleting the image",
    )
    @app_commands.choices(pathname=THEME_CHOICES)
    @owner_slash()
    async def rcsbremove(self, interaction: discord.Interaction, pathname: app_commands.Choice[str],
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
                        f"but they get the normal summary until you set a new image with /rcsbset.")
            else:
                text = f"There is no image set for **{esc(pathname.name)}**."
        await interaction.response.send_message(text, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    # ── Owner: /rcsbgive ─────────────────────────────────────────────────────
    @app_commands.command(name="rcsbgive", description="Give a summary theme to a player")
    @app_commands.describe(user="Who gets the theme", pathname="Which theme")
    @app_commands.choices(pathname=THEME_CHOICES)
    @owner_slash()
    async def rcsbgive(self, interaction: discord.Interaction, user: discord.Member,
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
            text += f"\nNote: no image is set for {esc(pathname.name)} yet. Use /rcsbset first, until then the normal summary shows."
        await interaction.response.send_message(text, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
        if res:
            try:
                await user.send(f"You received the **{pathname.name}** match summary theme! "
                                f"Turn it on with `rcsb use {pathname.value}`.")
            except discord.HTTPException:
                pass                                              # DMs closed: the theme is still saved

    # ── Players: rcsb ────────────────────────────────────────────────────────
    @commands.group(name="rcsb", aliases=["summarytheme"], invoke_without_command=True)
    async def rcsb(self, ctx: commands.Context):
        """Theme menu: current theme, all themes, preview and buttons."""
        bucket = _menu_cd.get_bucket(ctx.message)
        retry = bucket.update_rate_limit() if bucket else None
        if retry:
            raise commands.CommandOnCooldown(bucket, retry, commands.BucketType.user)
        on = await asyncio.to_thread(st.equipped, ctx.author.id)
        idx = st.NAMES.index(on) if on in st.NAMES else 0           # open on the theme that is ON
        view = ThemeMenu(ctx.author.id, idx)
        embed, file = await view.render()
        view.message = await ctx.send(embed=embed, file=file, view=view) if file else \
            await ctx.send(embed=embed, view=view)

    @rcsb.command(name="use", aliases=["on", "equip"])
    async def rcsb_use(self, ctx: commands.Context, *, pathname: str):
        """rcsb use halloween"""
        res = await asyncio.to_thread(st.equip, ctx.author.id, pathname)
        if res == "unknown":
            await ctx.send("Unknown theme. Themes: " + ", ".join(st.label(n) for n in st.NAMES) + ".")
        elif res == "not_owned":
            await ctx.send(f"You don't own the {esc(pathname.strip().title())} theme.")
        else:
            key = st.norm(pathname)
            extra = "" if await asyncio.to_thread(st.has_image, key) else " (its image isn't set yet, so the normal summary shows for now)"
            await ctx.send(f"{st.label(key)} summary theme is ON{extra}.")

    @rcsb.command(name="off", aliases=["default", "normal", "remove"])
    async def rcsb_off(self, ctx: commands.Context):
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
    "match_id": 100001, "venue": "Rush Cricket Stadium", "result": "TEAM A WON BY 12 RUNS", "potm": "Sample Player",
    "team1": {"name": "Team A", "runs": 187, "wickets": 6, "overs": "20.0", "max_overs": "20",
              "batters": [("Player One", 82, 47, False), ("Player Two", 46, 28, True), ("Player Three", 31, 15, False), ("Player Four", 14, 9, False)],
              "bowlers": [("Bowler One", 3, 24, "4.0"), ("Bowler Two", 2, 31, "4.0"), ("Bowler Three", 1, 28, "4.0"), ("Bowler Four", 0, 39, "4.0")]},
    "team2": {"name": "Team B", "runs": 175, "wickets": 9, "overs": "20.0", "max_overs": "20",
              "batters": [("Player Five", 64, 40, False), ("Player Six", 51, 33, False), ("Player Seven", 28, 18, False), ("Player Eight", 12, 10, False)],
              "bowlers": [("Bowler Five", 3, 22, "4.0"), ("Bowler Six", 2, 30, "4.0"), ("Bowler Seven", 1, 35, "4.0"), ("Bowler Eight", 0, 41, "4.0")]},
}


async def setup(bot: commands.Bot):
    await bot.add_cog(SummaryThemeCog(bot))
