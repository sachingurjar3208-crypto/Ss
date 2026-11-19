"""My Player — a Premium member's OWN custom card.

* Premium only to create. One per user. Never universal: it is not listed, searched, spawned, put in
  packs / rewards / the shop, and it can't be sold, traded, gifted or auctioned. It only lives in its
  owner's squad / XI.
* Start: All-Rounder 60 (bat 60 / bowl 60), Batter or Wicketkeeper 60 (bat 60 / bowl 10),
  Bowler 60 (bat 10 / bowl 60).
* Upgrades cost Energy Drinks: rcdaily +1, rcweekly +20 (Premium only).
    60-68    5 drinks per +1 OVR   (very easy:  40 in total)
    68-75   15 per +1              (normal:     105)
    75-80   30 per +1              (hard:       150)
    80-88   60 per +1              (very hard:  480)
    88-95  175 per +1              (legendary: 1,225)   95 is the max. 60 -> 95 = 2,000 drinks.
* Premium runs out: the card stays and still plays, it just can't be upgraded.
* The photo: members only REQUEST one (rcmyp photo + image). Only the bot owner approves it or sets
  a photo (/myplayer ...). Until then the card shows a gold silhouette.
"""
from __future__ import annotations

import asyncio
import io
import re
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

import card_cache
import card_db
import card_image
import economy
from bot_guard import ADMIN_GUILD_ID, OWNER_ID
from card_countries import CRICKET_COUNTRIES
from squad_cog import _is_premium, _need_debut

HERE = Path(__file__).parent
PHOTO_DIR = HERE / "myplayer_photos"
SILHOUETTE = "local:myplayer_photos/silhouette.png"
MAX_OVR = 95
DAILY_DRINKS = 1
WEEKLY_DRINKS = 20
DRINK = "<:energydrink:1572435428928135230>"

# role -> (ovr, bat, bowl) at creation
START = {"AR": (60, 60, 60), "BAT": (60, 60, 10), "WK": (60, 60, 10), "BOWL": (60, 10, 60)}
ROLE_NAME = {"AR": "All-Rounder", "BAT": "Batter", "WK": "Wicketkeeper", "BOWL": "Bowler"}
# (from OVR, to OVR, drinks per +1 OVR)
TIERS = [(60, 68, 5), (68, 75, 15), (75, 80, 30), (80, 88, 60), (88, 95, 175)]   # 60 -> 95 = 2,000 drinks
TIER_NAME = {5: "Very easy", 15: "Normal", 30: "Hard", 60: "Very hard", 175: "Legendary"}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,19}$")


def cost_per_point(ovr: int) -> int | None:
    for lo, hi, c in TIERS:
        if lo <= ovr < hi:
            return c
    return None


def plan_upgrade(ovr: int, drinks: int, want: int | None) -> tuple[int, int]:
    """(points, drinks needed) for up to `want` points (None = as many as affordable)."""
    pts = cost = 0
    while ovr + pts < MAX_OVR and (want is None or pts < want):
        c = cost_per_point(ovr + pts)
        if c is None or cost + c > drinks:
            break
        cost += c
        pts += 1
    return pts, cost


def stats_after(card, pts: int) -> dict:
    role = str(card["role"] or "AR").upper()
    ovr, bat, bowl = int(card["ovr"]), int(card["bat"]), int(card["bowl"])
    ovr = min(MAX_OVR, ovr + pts)
    if role == "AR":
        bat, bowl = min(MAX_OVR, bat + pts), min(MAX_OVR, bowl + pts)
    elif role == "BOWL":
        bowl = min(MAX_OVR, bowl + pts)
    else:
        bat = min(MAX_OVR, bat + pts)
    return {"ovr": ovr, "bat": bat, "bowl": bowl}


async def render(user_id) -> str | None:
    """Draw the user's My Player card (Black & Gold, "MY PLAYER" tag). Returns the image path."""
    card = card_db.get_myplayer(user_id)
    if card is None:
        return None
    import card_full
    try:
        fg = await card_image._download_image(card["foreground_link"])
    except Exception as e:
        print(f"[myplayer] photo failed for {user_id}: {e}; using the silhouette")
        fg = await card_image._download_image(SILHOUETTE)
    role = card_db.effective_role(card)
    buf = await asyncio.to_thread(card_full.build, card, fg, role)
    out = card_db.GENERATED_DIR / f"{card['playername_key']}.png"
    out.write_bytes(buf.getvalue())
    card_db.set_card_image_path(card["playername_key"], str(out))
    await card_cache.warm(str(out))
    return str(out)


def gif_path(card) -> Path:
    """Old animated copy (no longer made); only used to delete leftovers."""
    return card_db.GENERATED_DIR / f"{card['playername_key']}.gif"


async def send_card(dest, embed: discord.Embed, card, png_path: str | None = None) -> discord.Message:
    """Send the embed with the (still) card image."""
    return await card_cache.send_embed(dest, embed, png_path or card["image_path"])


async def give_claim_drinks(ctx: commands.Context, kind: str) -> None:
    """Called after a successful rcdaily / rcweekly by a Premium member."""
    n = DAILY_DRINKS if kind == "daily" else WEEKLY_DRINKS
    total = await asyncio.to_thread(economy.myp_add_drinks, ctx.author.id, n)
    has = card_db.get_myplayer(ctx.author.id) is not None
    tip = "Upgrade with `rcmyp upgrade`." if has else "Create your own card with `rcmyp create <name>`."
    await ctx.send(f"{DRINK} **+{n} Energy Drink{'s' if n > 1 else ''}** (you have **{total}**). {tip}")


def _progress_text(card, drinks: int, premium: bool) -> str:
    ovr = int(card["ovr"])
    lines = [
        f"**{ROLE_NAME.get(str(card['role']).upper(), card['role'])}** · {card['country_emoji']} {card['country']}",
        f"OVR **{ovr}** · BAT **{card['bat']}** · BOWL **{card['bowl']}**",
        f"{DRINK} Energy Drinks: **{drinks}**",
    ]
    c = cost_per_point(ovr)
    if c is None:
        lines.append("🏆 **MAX OVR reached (95).**")
    else:
        pts, _ = plan_upgrade(ovr, drinks, None)
        lines.append(f"Next: **{c * 2} drinks = +2 OVR** ({TIER_NAME[c]} tier)")
        if pts:
            lines.append(f"You can upgrade **+{pts} OVR** now → `rcmyp upgrade`")
    if not premium:
        lines.append("❄️ Premium ended: your card still plays, but upgrades are locked until you renew.")
    if str(card["foreground_link"]).startswith(SILHOUETTE):
        lines.append("📸 No photo yet: send `rcmyp photo` with an image attached (the owner approves it).")
    return "\n".join(lines)


class CreateView(discord.ui.View):
    def __init__(self, author: discord.abc.User, name: str):
        super().__init__(timeout=180)
        self.author, self.name = author, name
        self.role = self.hand = self.bowling = self.country = None
        self.message: discord.Message | None = None

        role = discord.ui.Select(placeholder="1. Role", options=[
            discord.SelectOption(label="All-Rounder (bat 60 / bowl 60)", value="AR"),
            discord.SelectOption(label="Batter (bat 60 / bowl 10)", value="BAT"),
            discord.SelectOption(label="Bowler (bat 10 / bowl 60)", value="BOWL"),
            discord.SelectOption(label="Wicketkeeper (bat 60 / bowl 10)", value="WK"),
        ])
        hand = discord.ui.Select(placeholder="2. Batting hand", options=[
            discord.SelectOption(label="Right hand bat", value="R"),
            discord.SelectOption(label="Left hand bat", value="L"),
        ])
        bowl = discord.ui.Select(placeholder="3. Bowling type", options=[
            discord.SelectOption(label=x, value=x) for x in ("Fast", "Medium Pacer", "Off Spin", "Leg Spin", "Orthodox")
        ])
        country = discord.ui.Select(placeholder="4. Country", options=[
            discord.SelectOption(label=n, value=n, emoji=e if len(e) <= 2 else None) for n, e in CRICKET_COUNTRIES[:25]
        ])
        for sel, attr in ((role, "role"), (hand, "hand"), (bowl, "bowling"), (country, "country")):
            sel.callback = self._picker(sel, attr)
            self.add_item(sel)

    def _picker(self, sel, attr):
        async def cb(interaction: discord.Interaction):
            setattr(self, attr, sel.values[0])
            for o in sel.options:
                o.default = o.value == sel.values[0]
            await interaction.response.edit_message(view=self)
        return cb

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This isn't your card.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="⌛ Timed out. Run `rcmyp create <name>` again.", view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, emoji="✨", row=4)
    async def create(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not all((self.role, self.hand, self.bowling, self.country)):
            await interaction.response.send_message("Pick all 4 first.", ephemeral=True)
            return
        uid = self.author.id
        if card_db.get_myplayer(uid) is not None:
            await interaction.response.edit_message(content="You already have a My Player.", view=None)
            return
        await interaction.response.edit_message(content="✨ Creating your player...", view=None)
        self.stop()
        ovr, bat, bowl = START[self.role]
        flag = dict(CRICKET_COUNTRIES).get(self.country, "")
        bgs = card_db.list_backgrounds()
        key = card_db.create_myplayer(
            user_id=uid, name=self.name, role=self.role, bat=bat, bowl=bowl, ovr=ovr,
            country=self.country, country_emoji=flag, batting_hand=self.hand,
            bowling_type=self.bowling, foreground_link=SILHOUETTE, background=bgs[0] if bgs else "",
        )
        await asyncio.to_thread(economy.myp_grant, uid, key)
        path = await render(uid)
        card = card_db.get_myplayer(uid)
        embed = discord.Embed(
            title=f"✨ {card['playername']} is born!",
            description=_progress_text(card, economy.myp_drinks(uid), True)
            + "\n\nYour player is in your squad now. Only **you** will ever have this card.",
            color=discord.Color.gold(),
        )
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass
        await send_card(interaction.channel, embed, card, path)


class MyPlayerCog(commands.Cog, name="My Player"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── member commands ─────────────────────────────────────────────────
    @commands.group(name="rcmyplayer", aliases=["rcmyp"], invoke_without_command=True)
    async def rcmyplayer(self, ctx: commands.Context):
        """(Premium) Your own custom card."""
        if not await _need_debut(ctx):
            return
        card = card_db.get_myplayer(ctx.author.id)
        premium = _is_premium(ctx.author)
        if card is None:
            if not premium:
                await ctx.send("💎 **My Player** is a Premium perk: create your OWN card (only you can ever "
                               "have it) and level it from 60 up to 95 OVR with Energy Drinks.")
            else:
                await ctx.send(f"✨ Create your own card: `rcmyp create <name>`\n{DRINK} You have "
                               f"**{economy.myp_drinks(ctx.author.id)}** Energy Drinks.")
            return
        if not card["image_path"] or not Path(card["image_path"]).exists():
            await render(ctx.author.id)
            card = card_db.get_myplayer(ctx.author.id)
        embed = discord.Embed(title=f"⭐ {card['playername']} · My Player",
                              description=_progress_text(card, economy.myp_drinks(ctx.author.id), premium),
                              color=discord.Color.gold())
        await send_card(ctx, embed, card)

    @rcmyplayer.command(name="create")
    async def mp_create(self, ctx: commands.Context, *, name: str = ""):
        """(Premium) rcmyp create <name>"""
        if not await _need_debut(ctx):
            return
        if not _is_premium(ctx.author):
            await ctx.send("💎 Creating a My Player is for **Premium members** only.")
            return
        if card_db.get_myplayer(ctx.author.id) is not None:
            await ctx.send("You already have a My Player. See it with `rcmyp`.")
            return
        name = " ".join(name.split())
        if not NAME_RE.match(name):
            await ctx.send("Name: 2-20 letters (spaces, . ' - allowed). Example: `rcmyp create Rahul Singh`")
            return
        if card_db.find_cards(name, limit=1) and card_db.find_cards(name, limit=1)[0]["playername_key"] == name.lower():
            await ctx.send("That's a real card's name. Pick your own name.")
            return
        view = CreateView(ctx.author, name)
        view.message = await ctx.send(f"✨ Creating **{discord.utils.escape_markdown(name)}**. Pick all 4, then **Create**:",
                                      view=view)

    @rcmyplayer.command(name="upgrade")
    async def mp_upgrade(self, ctx: commands.Context, amount: str = "max"):
        """(Premium) rcmyp upgrade [points | max]"""
        uid = ctx.author.id
        card = card_db.get_myplayer(uid)
        if card is None:
            await ctx.send("You don't have a My Player yet. `rcmyp create <name>`")
            return
        if not _is_premium(ctx.author):
            await ctx.send("❄️ Upgrades need **Premium**. Your card still plays; renew to keep levelling.")
            return
        ovr = int(card["ovr"])
        if ovr >= MAX_OVR:
            await ctx.send("🏆 Your player is already **95 OVR** (max).")
            return
        want = None if amount.lower() == "max" else (int(amount) if amount.isdigit() and int(amount) > 0 else -1)
        if want == -1:
            await ctx.send("Use `rcmyp upgrade` (as much as you can) or `rcmyp upgrade 2`.")
            return
        drinks = economy.myp_drinks(uid)
        pts, cost = plan_upgrade(ovr, drinks, want)
        if pts == 0:
            c = cost_per_point(ovr)
            await ctx.send(f"{DRINK} You need **{c} drinks** for +1 OVR ({c * 2} for +2). You have **{drinks}**.")
            return
        if not await asyncio.to_thread(economy.myp_spend_drinks, uid, cost):
            await ctx.send("Your drinks changed. Try again.")
            return
        new = stats_after(card, pts)
        if not card_db.update_myplayer(uid, **new):
            await asyncio.to_thread(economy.myp_add_drinks, uid, cost)   # refund
            await ctx.send("Something went wrong. Your drinks were given back.")
            return
        path = await render(uid)
        card = card_db.get_myplayer(uid)
        embed = discord.Embed(
            title=f"⬆️ {card['playername']}: {ovr} → {new['ovr']} OVR",
            description=f"Used **{cost}** {DRINK}\n\n" + _progress_text(card, economy.myp_drinks(uid), True),
            color=discord.Color.gold(),
        )
        await send_card(ctx, embed, card, path)

    @rcmyplayer.command(name="photo")
    async def mp_photo(self, ctx: commands.Context):
        """Request a photo for your player (attach an image). The bot owner approves it."""
        uid = ctx.author.id
        if card_db.get_myplayer(uid) is None:
            await ctx.send("You don't have a My Player yet.")
            return
        att = next((a for a in ctx.message.attachments if (a.content_type or "").startswith("image/")), None)
        if att is None:
            await ctx.send("Attach an image to the message: `rcmyp photo` + image. A clear photo with a plain "
                           "background works best.")
            return
        if att.size > 8 * 1024 * 1024:
            await ctx.send("Image too big (max 8 MB).")
            return
        try:
            data = await att.read()
            img = await asyncio.to_thread(lambda: Image.open(io.BytesIO(data)).convert("RGBA"))
            img.thumbnail((1400, 1400))
            PHOTO_DIR.mkdir(exist_ok=True)
            await asyncio.to_thread(img.save, PHOTO_DIR / f"pending_{uid}.png", "PNG")
        except Exception:
            await ctx.send("Couldn't read that image. Try a PNG or JPG.")
            return
        await ctx.send("📸 Photo sent to the owner for approval. You'll get a DM when it's done.")
        try:
            owner = self.bot.get_user(OWNER_ID) or await self.bot.fetch_user(OWNER_ID)
            await owner.send(
                f"📸 **My Player photo request** from {ctx.author} (`{uid}`) for "
                f"**{card_db.get_myplayer(uid)['playername']}**\n"
                f"Approve: `/myplayer approve user:{uid}` · Reject: `/myplayer reject user:{uid}`\n"
                f"(Or upload your own cut-out PNG with `/myplayer setphoto`.)",
                file=discord.File(PHOTO_DIR / f"pending_{uid}.png", filename="request.png"),
            )
        except Exception as e:
            print(f"[myplayer] could not DM owner: {e}")

    # ── owner slash commands (main server only, hidden) ──────────────────
    owner_group = app_commands.Group(name="myplayer", description="(Owner) My Player cards",
                                     guild_only=True, default_permissions=discord.Permissions())
    app_commands.guilds(ADMIN_GUILD_ID)(owner_group)

    async def _owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("Only the bot owner can do this.", ephemeral=True)
            return False
        return True

    async def _apply_photo(self, interaction, user: discord.User, src: Path) -> None:
        dest_name = f"{user.id}_{int(time.time())}.png"
        src.replace(PHOTO_DIR / dest_name)
        old = card_db.get_myplayer(user.id)["foreground_link"]
        card_db.update_myplayer(user.id, foreground_link=f"local:myplayer_photos/{dest_name}")
        if str(old).startswith("local:myplayer_photos/") and old != SILHOUETTE:
            (HERE / old[6:]).unlink(missing_ok=True)
        path = await render(user.id)
        await interaction.followup.send("✅ Photo set.", file=await card_cache.card_file(path))
        try:
            await user.send("📸 Your **My Player** photo was approved! See it with `rcmyp`.")
        except Exception:
            pass

    @owner_group.command(name="approve", description="Approve a member's requested photo")
    async def o_approve(self, interaction: discord.Interaction, user: discord.User):
        if not await self._owner(interaction):
            return
        src = PHOTO_DIR / f"pending_{user.id}.png"
        if card_db.get_myplayer(user.id) is None or not src.exists():
            await interaction.response.send_message("No pending photo for that user.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._apply_photo(interaction, user, src)

    @owner_group.command(name="reject", description="Reject a member's requested photo")
    async def o_reject(self, interaction: discord.Interaction, user: discord.User, reason: str = ""):
        if not await self._owner(interaction):
            return
        (PHOTO_DIR / f"pending_{user.id}.png").unlink(missing_ok=True)
        await interaction.response.send_message("❌ Rejected.", ephemeral=True)
        try:
            await user.send("📸 Your **My Player** photo was not approved." + (f" Reason: {reason}" if reason else "")
                            + " You can send another with `rcmyp photo`.")
        except Exception:
            pass

    @owner_group.command(name="setphoto", description="Upload the photo yourself (best: a cut-out PNG)")
    async def o_setphoto(self, interaction: discord.Interaction, user: discord.User, image: discord.Attachment):
        if not await self._owner(interaction):
            return
        if card_db.get_myplayer(user.id) is None:
            await interaction.response.send_message("That user has no My Player.", ephemeral=True)
            return
        await interaction.response.defer()
        data = await image.read()
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img.thumbnail((1400, 1400))
        PHOTO_DIR.mkdir(exist_ok=True)
        tmp = PHOTO_DIR / f"pending_{user.id}.png"
        img.save(tmp, "PNG")
        await self._apply_photo(interaction, user, tmp)

    @owner_group.command(name="position", description="Move / resize the photo on the card")
    @app_commands.describe(dx="left - / right +", dy="up - / down +", scale="size %, 100 = normal")
    async def o_position(self, interaction: discord.Interaction, user: discord.User,
                         dx: int = 0, dy: int = 0, scale: app_commands.Range[int, 30, 300] = 100):
        if not await self._owner(interaction):
            return
        if not card_db.update_myplayer(user.id, full_dx=dx, full_dy=dy, full_scale=scale):
            await interaction.response.send_message("That user has no My Player.", ephemeral=True)
            return
        await interaction.response.defer()
        path = await render(user.id)
        await interaction.followup.send("✅ Moved.", file=await card_cache.card_file(path))

    @owner_group.command(name="drinks", description="Give (or take with minus) Energy Drinks")
    async def o_drinks(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if not await self._owner(interaction):
            return
        total = await asyncio.to_thread(economy.myp_add_drinks, user.id, amount)
        await interaction.response.send_message(f"{DRINK} {user} now has **{total}** drinks.", ephemeral=True)

    @owner_group.command(name="rename", description="Rename a member's My Player")
    async def o_rename(self, interaction: discord.Interaction, user: discord.User, name: str):
        if not await self._owner(interaction):
            return
        name = " ".join(name.split())
        if not NAME_RE.match(name) or not card_db.update_myplayer(user.id, playername=name, displayname=name):
            await interaction.response.send_message("Bad name or no My Player.", ephemeral=True)
            return
        await interaction.response.defer()
        path = await render(user.id)
        await interaction.followup.send("✅ Renamed.", file=await card_cache.card_file(path))

    @owner_group.command(name="delete", description="Delete a member's My Player (removed from their squad)")
    async def o_delete(self, interaction: discord.Interaction, user: discord.User):
        if not await self._owner(interaction):
            return
        card = card_db.get_myplayer(user.id)
        if card is None:
            await interaction.response.send_message("That user has no My Player.", ephemeral=True)
            return
        key = card["playername_key"]
        with economy._tx() as conn:
            n = economy._own_qty(conn, user.id, key)
            if n > 0:
                economy._remove_copies(conn, user.id, key, n)
        card_db.delete_myplayer(user.id)
        if card["image_path"]:
            Path(card["image_path"]).unlink(missing_ok=True)
        gif_path(card).unlink(missing_ok=True)
        await interaction.response.send_message(f"🗑️ Deleted {card['playername']}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MyPlayerCog(bot))
