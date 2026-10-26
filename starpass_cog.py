"""Star Pass.

    csstarpass              the Star Pass image with page arrows and a Collect button
                            (alias: starpass). 2 minute cooldown; Premium members and
                            members with the Star Pass role have none.
    csstarpass halloween    open one event pass by name (same as cshalloween). When more than
                            one pass is running, a dropdown under the image switches between them.
    cshalloween             the Halloween Pass

Admin slash commands (visible to admins only, exist only in the main server):
    /starpassmaker          start a season: duration 10D / 15D / 30D and the number of levels.
                            Optional: pass_name (a separate event pass such as "Halloween Pass"
                            that runs NEXT TO the normal Star Pass), theme, background image,
                            premium_role and bonus credits.
    /starpassbg             change or remove the background image of a pass at any time
    /starpass-rewardadd     change the reward of one level (Free or Premium row)
    /starpassend            end the running season of a pass right now
    /seasonreopen           bring an ended season back (for example Season 1 ended by mistake)
All admin commands have an optional pass_key (empty = the normal Star Pass).

Daily quest and ranked credits go to EVERY running pass. Credits come from daily quests (quest.py), levels and rewards are in starpass.py and the
image is drawn by starpass_image.py. Members with STARPASS_PREMIUM_ROLE_ID get 1000 credits
the moment the role is given (once per season, no refresh needed).
"""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands, ui
from discord.ext import commands

import card_db
import economy
import squad_logic as sl
import starpass
import starpass_image
from bot_guard import MaintenanceActive, admin_only
from economy_cog import send_next_pending
from security import OwnedView, esc
from squad_cog import _in_match, _is_premium, _need_debut

EMBED_COLOR = 0xF4C452
THEME_COLORS = {"halloween": 0xFF8C26, "diwali": 0xFFC442, "christmas": 0x50CD78}
THEME_CHOICES = [app_commands.Choice(name=n.capitalize(), value=n) for n in starpass.THEMES]


def _color(season: dict | None) -> int:
    return THEME_COLORS.get((season or {}).get("theme"), EMBED_COLOR)


def _open_hint(season: dict) -> str:
    return "csstarpass" if season["pass_key"] == starpass.DEFAULT_PASS else f"csstarpass {season['pass_key']}"
NO_PASS_TITLE = "No Star Pass is running"
NO_PASS_TEXT = (
    "There is no Star Pass running right now.\n"
    "A new season will start soon, so keep an eye on the announcements. "
    "Your daily quests will start earning Star Pass credits again as soon as it begins."
)

_cooldowns = commands.CooldownMapping.from_cooldown(1, starpass.CSSTARPASS_COOLDOWN, commands.BucketType.user)


def _has_star_role(member) -> bool:
    return any(r.id == starpass.STARPASS_PREMIUM_ROLE_ID for r in getattr(member, "roles", []))


def _pass_premium(member, season: dict | None = None) -> bool:
    """Star Pass Premium benefits: ONLY the Premium role of that pass (default: the Star Pass role)."""
    role_id = season["premium_role"] if season else starpass.STARPASS_PREMIUM_ROLE_ID
    return any(r.id == role_id for r in getattr(member, "roles", []))


def _no_cooldown(member) -> bool:
    """No csstarpass cooldown for bot Premium members and for the Star Pass role."""
    return _is_premium(member) or _has_star_role(member)


def _check_cooldown(ctx: commands.Context) -> None:
    if _no_cooldown(ctx.author):
        return
    bucket = _cooldowns.get_bucket(ctx.message)
    retry = bucket.update_rate_limit() if bucket else None
    if retry:
        raise commands.CommandOnCooldown(bucket, retry, commands.BucketType.user)


def _no_pass_embed() -> discord.Embed:
    return discord.Embed(title=NO_PASS_TITLE, description=NO_PASS_TEXT, color=0x5865F2)


def _start_page(board: dict) -> int:
    """Open on the page that holds the user's current level."""
    pages = starpass.page_ranges(board["season"]["levels"])
    for i, (a, b) in enumerate(pages):
        if a <= board["unlocked"] <= b:
            return i
    return 0


async def _render(member, page: int | None, pass_key: str):
    """(embed, file, board, page) for one page of one pass, or (no-pass embed, None, None, 0)
    when that pass is not running. page None = open on the page of the user's current level."""
    season = await asyncio.to_thread(starpass.get_active_season, pass_key)
    if season is None:
        return _no_pass_embed(), None, None, 0
    premium = _pass_premium(member, season)
    board = await asyncio.to_thread(starpass.get_board, member.id, premium, pass_key)
    if board is None:
        return _no_pass_embed(), None, None, 0
    if page is None:
        page = _start_page(board)
    page = max(0, min(int(page), len(starpass.page_ranges(board["season"]["levels"])) - 1))
    buf = await asyncio.to_thread(starpass_image.build_starpass_image, board, page)
    embed = discord.Embed(color=_color(board["season"]))
    embed.set_image(url=f"attachment://{starpass_image.FILENAME}")
    if board["ready"]:
        embed.set_footer(text=f"{board['ready']} reward(s) ready to collect")
    else:
        embed.set_footer(text="Finish daily quests to earn Star Pass credits  |  csquest")
    return embed, discord.File(buf, filename=starpass_image.FILENAME), board, page


class StarPassView(OwnedView):
    def __init__(self, bot: commands.Bot, user_id: int, page: int, board: dict,
                 pass_key: str = starpass.DEFAULT_PASS, actives: list | None = None):
        super().__init__(user_id, timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.page = page
        self.pass_key = pass_key
        self.pages = len(starpass.page_ranges(board["season"]["levels"]))
        self._busy = asyncio.Lock()
        self.select: ui.Select | None = None
        if actives and len(actives) > 1:              # more than one pass running: dropdown to switch
            self.select = ui.Select(
                placeholder="Switch pass",
                options=[discord.SelectOption(label=a["name"][:100], value=a["pass_key"],
                                              default=a["pass_key"] == pass_key) for a in actives[:25]],
                row=1,
            )
            self.select.callback = self._switch
            self.add_item(self.select)
        self._sync(board)

    async def _switch(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.pass_key = self.select.values[0]
        self.page = None                              # open on the page of the user's level
        await self._redraw(interaction)

    def _sync(self, board: dict | None) -> None:
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.pages - 1
        ready = board["ready"] if board else 0
        self.collect_btn.label = "Collect All" if ready >= 2 else "Collect"
        self.collect_btn.disabled = ready <= 0

    async def _redraw(self, interaction: discord.Interaction) -> None:
        embed, file, board, page = await _render(interaction.user, self.page, self.pass_key)
        if board is None:                              # this pass ended: fall back to another running pass
            others = await asyncio.to_thread(starpass.list_active_seasons)
            if others:
                self.pass_key = others[0]["pass_key"]
                embed, file, board, page = await _render(interaction.user, None, self.pass_key)
        if board is None:
            self.stop()
            await interaction.edit_original_response(embed=embed, attachments=[], view=None)
            return
        self.page = page
        self.pages = len(starpass.page_ranges(board["season"]["levels"]))
        if self.select is not None:
            for opt in self.select.options:
                opt.default = opt.value == self.pass_key
        self._sync(board)
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    @ui.button(label="<", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        self.page = max(0, (self.page or 0) - 1)
        await self._redraw(interaction)

    @ui.button(label="Collect", style=discord.ButtonStyle.success, row=0)
    async def collect_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        if self._busy.locked():
            return                                    # a collect is already running: ignore the extra press
        async with self._busy:
            await self._collect(interaction)

    @ui.button(label=">", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        self.page = min(self.pages - 1, (self.page or 0) + 1)
        await self._redraw(interaction)

    async def _collect(self, interaction: discord.Interaction) -> None:
        uid = self.user_id
        if _in_match(self.bot, uid):
            await interaction.followup.send("Finish your current match before collecting Star Pass rewards.", ephemeral=True)
            return
        key = self.pass_key
        season = await asyncio.to_thread(starpass.get_active_season, key)
        if season is None:                            # ended a moment ago
            await interaction.followup.send("This Star Pass has ended.", ephemeral=True)
            await self._redraw(interaction)
            return
        premium = _pass_premium(interaction.user, season)
        if premium:
            await asyncio.to_thread(starpass.grant_premium_bonus, uid, key)

        # coin value of cards the user may already own (duplicates are paid out as coins)
        keys = await asyncio.to_thread(starpass.player_keys_in_pass, key)
        cards = await asyncio.to_thread(card_db.get_cards, keys) if keys else {}
        values = {k: sl.sell_value(c) for k, c in cards.items()}

        result = await asyncio.to_thread(starpass.collect, uid, premium, values, key)
        if isinstance(result, str):
            await interaction.followup.send(result, ephemeral=True)
            await self._redraw(interaction)
            return

        await interaction.followup.send(
            embed=await _summary_embed(interaction.user, result, season),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._redraw(interaction)
        if result["pending"]:
            await send_next_pending(interaction.channel, uid)


async def _summary_embed(user, result: dict, season: dict | None = None) -> discord.Embed:
    lines = []
    if result["coins"]:
        lines.append(f"Coins: {result['coins']:,}")
    if result["vouchers"]:
        lines.append(f"Vouchers: {result['vouchers']:,}")
    for kind, qty in result["packs"].items():
        lines.append(f"{sl.PACKS[kind]['label']}: x{qty}")
    for tkey, is_new in result.get("themes", []):
        import summary_theme
        lines.append(f"Summary theme: {summary_theme.label(tkey)}" +
                     (f" (turn on with cssb use {tkey})" if is_new else " (already owned)"))
    keys = result["new"] + result["pending"] + result["dupes"]
    cards = await asyncio.to_thread(card_db.get_cards, keys) if keys else {}
    name = lambda k: esc(cards[k]["playername"]) if k in cards else esc(k)
    for k in result["new"]:
        lines.append(f"Player card: {name(k)} (added to your squad)")
    for k in result["pending"]:
        lines.append(f"Player card: {name(k)} (your squad is full, decide with cspending)")
    for k in result["dupes"]:
        lines.append(f"Player card: {name(k)} (already owned)")
    if result["refund"]:
        lines.append(f"Duplicate refund: {result['refund']:,} coins")
    n = result["count"]
    embed = discord.Embed(
        title=("Star Pass rewards collected" if season is None or season["pass_key"] == starpass.DEFAULT_PASS
               else f"{esc(season['name'])} rewards collected"),
        description=f"{esc(user.display_name)} collected {n} reward{'s' if n != 1 else ''}.\n\n" + "\n".join(lines),
        color=_color(season),
    )
    return embed


# ── Admin slash command helpers ──────────────────────────────────────────────

DURATION_CHOICES = [app_commands.Choice(name=k, value=k) for k in starpass.DURATIONS]
ROW_CHOICES = [app_commands.Choice(name="Free row", value=starpass.FREE),
               app_commands.Choice(name="Premium row", value=starpass.PREMIUM)]
REWARD_CHOICES = [
    app_commands.Choice(name="LEGENDARY PACK", value="legendary"),
    app_commands.Choice(name="GOLD PACK", value="gold"),
    app_commands.Choice(name="SILVER PACK", value="silver"),
    app_commands.Choice(name="BRONZE PACK", value="bronze"),
    app_commands.Choice(name="VOUCHER", value="voucher"),
    app_commands.Choice(name="COINS", value="coins"),
]
import summary_theme as _sb_theme
THEME_CHOICES_SB = [app_commands.Choice(name=_sb_theme.label(n), value=n) for n in _sb_theme.NAMES]
QTY_CAP = {"voucher": 10_000, "coins": 100_000_000}      # packs: PACK_QTY_CAP


async def player_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    names = [n for n in await asyncio.to_thread(card_db.list_playernames) if cur in n.lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


async def pass_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    items = await asyncio.to_thread(starpass.list_pass_keys)
    return [app_commands.Choice(name=f"{name} ({key})"[:100], value=key)
            for key, name in items if cur in key or cur in name.lower()][:25]


async def _read_background(attachment: discord.Attachment | None):
    """(bytes | None, error | None): an uploaded image, ready to be stored."""
    if attachment is None:
        return None, None
    if not (attachment.content_type or "").startswith("image/"):
        return None, "The background must be an image (PNG or JPG)."
    if attachment.size > starpass.MAX_BG_BYTES:
        return None, "That image is too big (max 8 MB)."
    try:
        raw = await attachment.read()
        return await asyncio.to_thread(starpass_image.prepare_background, raw), None
    except ValueError as e:
        return None, str(e)
    except discord.HTTPException:
        return None, "I could not download that image. Please try again."


class StarPassCog(commands.Cog, name="StarPass"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await asyncio.to_thread(starpass.ensure_tables)
        except Exception as e:
            print(f"[starpass] could not create tables: {e}")

    # ── csstarpass ───────────────────────────────────────────────────────────
    async def _open_pass(self, ctx: commands.Context, name: str | None):
        if not await _need_debut(ctx):
            return
        uid = ctx.author.id
        actives = await asyncio.to_thread(starpass.list_active_seasons)     # normal Star Pass first
        if not actives:
            await ctx.send(embed=_no_pass_embed())              # no cooldown is used up for this
            return
        if name:
            key = starpass.norm_key(name)
            chosen = next((a for a in actives if a["pass_key"] == key), None)
            if chosen is None:
                running = ", ".join(f"`{_open_hint(a)}`" for a in actives)
                await ctx.send(f"There is no running pass called **{esc(name)}**. Running now: {running}")
                return
        else:
            chosen = actives[0]
        key = chosen["pass_key"]
        _check_cooldown(ctx)

        if _pass_premium(ctx.author, chosen):                    # role given earlier: bonus is paid now at the latest
            await asyncio.to_thread(starpass.grant_premium_bonus, uid, key)
        embed, file, board, page = await _render(ctx.author, None, key)
        if board is None:                                        # ended a moment ago
            await ctx.send(embed=_no_pass_embed())
            return
        view = StarPassView(self.bot, uid, page, board, key, actives)
        view.message = await ctx.send(embed=embed, file=file, view=view)

    @commands.command(name="csstarpass", aliases=["starpass"])
    async def csstarpass(self, ctx: commands.Context, *, pass_name: str | None = None):
        """Show the Star Pass (or an event pass: csstarpass halloween)."""
        await self._open_pass(ctx, pass_name)

    @commands.command(name="cshalloween", aliases=["halloween"])
    async def cshalloween(self, ctx: commands.Context):
        """Show the Halloween Pass."""
        await self._open_pass(ctx, "halloween")

    # ── Premium role bonus ───────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return
        gained = {r.id for r in after.roles} - {r.id for r in before.roles}
        if not gained:
            return
        try:
            actives = await asyncio.to_thread(starpass.list_active_seasons)
            lines = []
            for a in actives:
                if a["premium_role"] in gained:
                    bonus = await asyncio.to_thread(starpass.grant_premium_bonus, after.id, a["pass_key"])
                    if bonus:
                        lines.append(f"{a['name']}: {bonus:,} credits  (type {_open_hint(a)})")
        except Exception as e:
            print(f"[starpass] premium bonus failed for {after.id}: {type(e).__name__}: {e}")
            return
        if lines:
            try:
                await after.send("Your Star Pass Premium is active. You received Star Pass credits:\n" + "\n".join(lines))
            except discord.HTTPException:
                pass                                              # DMs closed: the credits are still saved

    # ── Admin: /starpassmaker ────────────────────────────────────────────────
    @app_commands.command(name="starpassmaker", description="Start a new Star Pass season or an event pass (Halloween...)")
    @app_commands.describe(
        duration="How long the pass runs",
        level="Number of levels (for example 25 or 30)",
        pass_name="Empty = normal Star Pass. A name like Halloween Pass starts a SEPARATE pass next to it",
        theme="Colors of the pass image",
        background="Your own background image (PNG/JPG, best 1400x930)",
        premium_role="Role that unlocks the Premium row (empty = the normal Star Pass role)",
        bonus="Premium bonus credits for this pass (empty = 1000, 0 = none)",
    )
    @app_commands.choices(duration=DURATION_CHOICES, theme=THEME_CHOICES)
    @admin_only()
    async def starpassmaker(
        self,
        interaction: discord.Interaction,
        duration: app_commands.Choice[str],
        level: app_commands.Range[int, 1, starpass.MAX_LEVELS],
        pass_name: str | None = None,
        theme: app_commands.Choice[str] | None = None,
        background: discord.Attachment | None = None,
        premium_role: discord.Role | None = None,
        bonus: app_commands.Range[int, 0, 1_000_000] | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        bg_data, bg_err = await _read_background(background)          # check the image BEFORE the pass starts
        if bg_err:
            await interaction.followup.send(bg_err, ephemeral=True)
            return
        season = await asyncio.to_thread(
            starpass.create_season, level, duration.value, interaction.user.id,
            pass_name, theme.value if theme else "default",
            premium_role.id if premium_role else None, bonus)
        if isinstance(season, str):
            await interaction.followup.send(season, ephemeral=True)
            return
        key = season["pass_key"]
        if bg_data is not None:
            await asyncio.to_thread(starpass.set_background, key, bg_data)
        has_bg = bg_data is not None or await asyncio.to_thread(starpass.get_background, key) is not None

        # members who already hold the Premium role get their bonus for the new season
        given = 0
        role = interaction.guild.get_role(season["premium_role"]) if interaction.guild else None
        if role is not None:
            for m in list(role.members):
                if not m.bot and await asyncio.to_thread(starpass.grant_premium_bonus, m.id, key):
                    given += 1

        pages = starpass.page_ranges(season["levels"])
        embed = discord.Embed(title=f"{season['name']} started", color=_color(season))
        embed.add_field(name="Pass key", value=key)
        embed.add_field(name="Levels", value=f"{season['levels']} ({len(pages)} pages)")
        embed.add_field(name="Ends", value=f"<t:{int(season['end_ts'])}:R>")
        embed.add_field(name="Theme", value=season["theme"].capitalize())
        embed.add_field(name="Background", value="Custom image" if has_bg else "Theme colors")
        embed.add_field(name="Players open it with", value=f"`{_open_hint(season)}`")
        notes = []
        if given:
            notes.append(f"{given} Premium member(s) received the {season['bonus']} credit bonus.")
        if bg_data is None and has_bg:
            notes.append("Using the background saved earlier for this pass (change it with /starpassbg).")
        if notes:
            embed.set_footer(text="  ".join(notes))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Admin: /starpassbg ───────────────────────────────────────────────────
    @app_commands.command(name="starpassbg", description="Change or remove the background image of a Star Pass")
    @app_commands.describe(
        image="New background (PNG/JPG, best 1400x930). Leave empty together with remove",
        remove="Remove the custom background (back to the theme colors)",
        pass_key="Which pass (empty = the normal Star Pass)",
    )
    @app_commands.autocomplete(pass_key=pass_autocomplete)
    @admin_only()
    async def starpassbg(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment | None = None,
        remove: bool = False,
        pass_key: str | None = None,
    ):
        key = starpass.norm_key(pass_key)
        if (image is None) == (not remove):
            await interaction.response.send_message(
                "Upload an image, or set remove to True (not both, not neither).", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if remove:
            await asyncio.to_thread(starpass.set_background, key, None)
            await interaction.followup.send(f"Background of `{key}` removed.", ephemeral=True)
            return
        data, err = await _read_background(image)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        await asyncio.to_thread(starpass.set_background, key, data)
        await interaction.followup.send(
            f"New background saved for `{key}`. It shows the next time someone opens the pass.", ephemeral=True)

    # ── Admin: /starpassend ──────────────────────────────────────────────────
    @app_commands.command(name="starpassend", description="End the running Star Pass right now")
    @app_commands.describe(pass_key="Which pass to end (empty = the normal Star Pass)")
    @app_commands.autocomplete(pass_key=pass_autocomplete)
    @admin_only()
    async def starpassend(self, interaction: discord.Interaction, pass_key: str | None = None):
        ended = await asyncio.to_thread(starpass.end_season, starpass.norm_key(pass_key))
        if ended is None:
            await interaction.response.send_message("That pass is not running.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{ended['name']} has been ended.", ephemeral=True)

    # ── Admin: /seasonreopen ─────────────────────────────────────────────────
    @app_commands.command(name="seasonreopen", description="Bring an ended Star Pass season back")
    @app_commands.describe(
        season="Season number inside the pass (1 = Season 1). Empty = the latest ended season",
        days="Run it for this many days from now (needed if its time is already over)",
        replace_running="Another season is running (e.g. Season 2): pause it and bring this one back",
        pass_key="Which pass (empty = the normal Star Pass)",
    )
    @app_commands.autocomplete(pass_key=pass_autocomplete)
    @admin_only()
    async def seasonreopen(
        self,
        interaction: discord.Interaction,
        season: int | None = None,
        days: int | None = None,
        replace_running: bool = False,
        pass_key: str | None = None,
    ):
        if season is not None and not 1 <= season <= 1000:
            await interaction.response.send_message("Season must be between 1 and 1000.", ephemeral=True)
            return
        if days is not None and not 1 <= days <= 60:
            await interaction.response.send_message("Days must be between 1 and 60.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        res = await asyncio.to_thread(starpass.reopen_season, season, days, replace_running,
                                      starpass.norm_key(pass_key))
        if isinstance(res, str):
            await interaction.followup.send(res, ephemeral=True)
            return
        embed = discord.Embed(title=f"{res['name']} reopened", color=_color(res))
        embed.add_field(name="Season", value=res["name"])
        embed.add_field(name="Levels", value=str(res["levels"]))
        embed.add_field(name="Ends", value=f"<t:{int(res['end_ts'])}:R>")
        embed.set_footer(text="Everything players earned and collected in this season is still saved.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Admin: /starpass-rewardadd ───────────────────────────────────────────
    @app_commands.command(name="starpass-rewardadd", description="Change the reward of one Star Pass level")
    @app_commands.describe(
        level="Level to change",
        row="Free row or Premium row",
        reward="Pack, voucher or coins (leave empty if you pick a player)",
        quantity="How many (for example 2000 coins)",
        playername="Give a player card as the reward (leave reward empty)",
        pass_key="Which pass (empty = the normal Star Pass)",
        summary_theme="Give a match summary theme as the reward (leave reward and player empty)",
    )
    @app_commands.choices(row=ROW_CHOICES, reward=REWARD_CHOICES, summary_theme=THEME_CHOICES_SB)
    @app_commands.autocomplete(playername=player_autocomplete, pass_key=pass_autocomplete)
    @admin_only()
    async def starpass_rewardadd(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, starpass.MAX_LEVELS],
        row: app_commands.Choice[str],
        reward: app_commands.Choice[str] | None = None,
        quantity: app_commands.Range[int, 1, 100_000_000] = 1,
        playername: str | None = None,
        pass_key: str | None = None,
        summary_theme: app_commands.Choice[str] | None = None,
    ):
        if sum(x is not None and x != "" for x in (reward, playername, summary_theme)) > 1:
            await interaction.response.send_message(
                "Choose only one: a reward type, a player name or a summary theme.", ephemeral=True)
            return
        if reward is None and not playername and summary_theme is None:
            await interaction.response.send_message(
                "Pick a reward type, a summary theme, or leave them empty and enter a player name.", ephemeral=True)
            return

        if summary_theme is not None:
            kind, qty, key = "theme", 1, summary_theme.value
            what = f"{summary_theme.name} Summary (summary theme)"
        elif playername:
            card = await asyncio.to_thread(card_db.get_card, playername)
            if card is None:
                await interaction.response.send_message(
                    f"I could not find a player named {esc(playername)}. Pick one from the list.", ephemeral=True)
                return
            kind, qty, key = "player", 1, card["playername_key"]
            what = f"{card['playername']} (player card)"
        else:
            kind, qty, key = reward.value, int(quantity), None
            cap = QTY_CAP.get(kind, 100)
            if qty > cap:
                await interaction.response.send_message(f"The most you can give of this reward is {cap:,}.", ephemeral=True)
                return
            what = starpass.Reward(kind, qty).label()

        res = await asyncio.to_thread(starpass.set_reward, level, row.value, kind, qty, key,
                                      starpass.norm_key(pass_key))
        if isinstance(res, str):
            await interaction.response.send_message(res, ephemeral=True)
            return
        await interaction.response.send_message(
            f"Level {level} ({row.name.lower()}) of {res['name']} now gives: {esc(what)}.", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MaintenanceActive):
            return
        if isinstance(error, app_commands.CheckFailure):
            msg = "Only admins can use this command."
        else:
            print(f"[starpass] command error: {type(error).__name__}: {error}")
            msg = "Something went wrong. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(StarPassCog(bot))
