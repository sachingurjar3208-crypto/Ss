"""Star Pass.

    csstarpass              the Star Pass image with page arrows and a Collect button
                            (alias: starpass). 2 minute cooldown; Premium members and
                            members with the Star Pass role have none.

Admin slash commands (visible to admins only, exist only in the main server):
    /starpassmaker          start a season: duration 10D / 15D / 30D and the number of levels
    /starpass-rewardadd     change the reward of one level (Free or Premium row)
    /starpassend            end the running season right now
    /seasonreopen           bring an ended season back (for example Season 1 ended by mistake)

Credits come from daily quests (quest.py), levels and rewards are in starpass.py and the
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
NO_PASS_TITLE = "No Star Pass is running"
NO_PASS_TEXT = (
    "There is no Star Pass running right now.\n"
    "A new season will start soon, so keep an eye on the announcements. "
    "Your daily quests will start earning Star Pass credits again as soon as it begins."
)

_cooldowns = commands.CooldownMapping.from_cooldown(1, starpass.CSSTARPASS_COOLDOWN, commands.BucketType.user)


def _has_star_role(member) -> bool:
    return any(r.id == starpass.STARPASS_PREMIUM_ROLE_ID for r in getattr(member, "roles", []))


def _pass_premium(member) -> bool:
    """Star Pass Premium benefits: ONLY the Star Pass role."""
    return _has_star_role(member)


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


async def _render(user_id: int, premium: bool, page: int):
    """(embed, file, board) for one page, or (no-pass embed, None, None) when nothing runs."""
    board = await asyncio.to_thread(starpass.get_board, user_id, premium)
    if board is None:
        return _no_pass_embed(), None, None
    buf = await asyncio.to_thread(starpass_image.build_starpass_image, board, page)
    embed = discord.Embed(color=EMBED_COLOR)
    embed.set_image(url=f"attachment://{starpass_image.FILENAME}")
    if board["ready"]:
        embed.set_footer(text=f"{board['ready']} reward(s) ready to collect")
    else:
        embed.set_footer(text="Finish daily quests to earn Star Pass credits  |  csquest")
    return embed, discord.File(buf, filename=starpass_image.FILENAME), board


class StarPassView(OwnedView):
    def __init__(self, bot: commands.Bot, user_id: int, page: int, board: dict):
        super().__init__(user_id, timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.page = page
        self.pages = len(starpass.page_ranges(board["season"]["levels"]))
        self._busy = asyncio.Lock()
        self._sync(board)

    def _sync(self, board: dict | None) -> None:
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.pages - 1
        ready = board["ready"] if board else 0
        self.collect_btn.label = "Collect All" if ready >= 2 else "Collect"
        self.collect_btn.disabled = ready <= 0

    async def _refresh(self, interaction: discord.Interaction) -> None:
        premium = _pass_premium(interaction.user)
        embed, file, board = await _render(self.user_id, premium, self.page)
        if board is None:
            self.stop()
            await interaction.edit_original_response(embed=embed, attachments=[], view=None)
            return
        self.pages = len(starpass.page_ranges(board["season"]["levels"]))
        self.page = max(0, min(self.page, self.pages - 1))
        self._sync(board)
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    @ui.button(label="<", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        await self._refresh(interaction)

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
        self.page = min(self.pages - 1, self.page + 1)
        await self._refresh(interaction)

    async def _collect(self, interaction: discord.Interaction) -> None:
        uid = self.user_id
        if _in_match(self.bot, uid):
            await interaction.followup.send("Finish your current match before collecting Star Pass rewards.", ephemeral=True)
            return
        premium = _pass_premium(interaction.user)
        if premium:
            await asyncio.to_thread(starpass.grant_premium_bonus, uid)

        # coin value of cards the user may already own (duplicates are paid out as coins)
        keys = await asyncio.to_thread(starpass.player_keys_in_pass)
        cards = await asyncio.to_thread(card_db.get_cards, keys) if keys else {}
        values = {k: sl.sell_value(c) for k, c in cards.items()}

        result = await asyncio.to_thread(starpass.collect, uid, premium, values)
        if isinstance(result, str):
            await interaction.followup.send(result, ephemeral=True)
            await self._refresh(interaction)
            return

        await interaction.followup.send(
            embed=await _summary_embed(interaction.user, result),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._refresh(interaction)
        if result["pending"]:
            await send_next_pending(interaction.channel, uid)


async def _summary_embed(user, result: dict) -> discord.Embed:
    lines = []
    if result["coins"]:
        lines.append(f"Coins: {result['coins']:,}")
    if result["vouchers"]:
        lines.append(f"Vouchers: {result['vouchers']:,}")
    for kind, qty in result["packs"].items():
        lines.append(f"{sl.PACKS[kind]['label']}: x{qty}")
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
        title="Star Pass rewards collected",
        description=f"{esc(user.display_name)} collected {n} reward{'s' if n != 1 else ''}.\n\n" + "\n".join(lines),
        color=EMBED_COLOR,
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
QTY_CAP = {"voucher": 10_000, "coins": 100_000_000}      # packs: PACK_QTY_CAP


async def player_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    names = [n for n in await asyncio.to_thread(card_db.list_playernames) if cur in n.lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


class StarPassCog(commands.Cog, name="StarPass"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await asyncio.to_thread(starpass.ensure_tables)
        except Exception as e:
            print(f"[starpass] could not create tables: {e}")

    # ── csstarpass ───────────────────────────────────────────────────────────
    @commands.command(name="csstarpass", aliases=["starpass"])
    async def csstarpass(self, ctx: commands.Context):
        """Show the Star Pass."""
        if not await _need_debut(ctx):
            return
        uid = ctx.author.id
        premium = _pass_premium(ctx.author)

        season = await asyncio.to_thread(starpass.get_active_season)
        if season is None:
            await ctx.send(embed=_no_pass_embed())              # no cooldown is used up for this
            return
        _check_cooldown(ctx)

        if premium:                                              # role given earlier: bonus is paid now at the latest
            await asyncio.to_thread(starpass.grant_premium_bonus, uid)
        board = await asyncio.to_thread(starpass.get_board, uid, premium)
        if board is None:                                        # ended a moment ago
            await ctx.send(embed=_no_pass_embed())
            return
        page = _start_page(board)
        embed, file, board = await _render(uid, premium, page)
        view = StarPassView(self.bot, uid, page, board)
        view.message = await ctx.send(embed=embed, file=file, view=view)

    # ── Premium role bonus ───────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        had = any(r.id == starpass.STARPASS_PREMIUM_ROLE_ID for r in before.roles)
        has = any(r.id == starpass.STARPASS_PREMIUM_ROLE_ID for r in after.roles)
        if had or not has or after.bot:
            return
        try:
            bonus = await asyncio.to_thread(starpass.grant_premium_bonus, after.id)
        except Exception as e:
            print(f"[starpass] premium bonus failed for {after.id}: {type(e).__name__}: {e}")
            return
        if bonus:
            try:
                await after.send(
                    f"Your Star Pass Premium is active. You received {bonus:,} Star Pass credits. "
                    f"Type csstarpass to see your rewards."
                )
            except discord.HTTPException:
                pass                                              # DMs closed: the credits are still saved

    # ── Admin: /starpassmaker ────────────────────────────────────────────────
    @app_commands.command(name="starpassmaker", description="Start a new Star Pass season")
    @app_commands.describe(duration="How long the pass runs", level="Number of levels (for example 25 or 30)")
    @app_commands.choices(duration=DURATION_CHOICES)
    @admin_only()
    async def starpassmaker(
        self,
        interaction: discord.Interaction,
        duration: app_commands.Choice[str],
        level: app_commands.Range[int, 1, starpass.MAX_LEVELS],
    ):
        await interaction.response.defer(ephemeral=True)
        season = await asyncio.to_thread(starpass.create_season, level, duration.value, interaction.user.id)
        if isinstance(season, str):
            await interaction.followup.send(season, ephemeral=True)
            return

        # members who already hold the Premium role get their bonus for the new season
        given = 0
        role = interaction.guild.get_role(starpass.STARPASS_PREMIUM_ROLE_ID) if interaction.guild else None
        if role is not None:
            for m in list(role.members):
                if not m.bot and await asyncio.to_thread(starpass.grant_premium_bonus, m.id):
                    given += 1

        pages = starpass.page_ranges(season["levels"])
        embed = discord.Embed(title="Star Pass started", color=EMBED_COLOR)
        embed.add_field(name="Season", value=season["name"])
        embed.add_field(name="Levels", value=f"{season['levels']} ({len(pages)} pages)")
        embed.add_field(name="Ends", value=f"<t:{int(season['end_ts'])}:R>")
        if given:
            embed.set_footer(text=f"{given} Premium member(s) received the {starpass.PREMIUM_BONUS_CREDITS} credit bonus.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Admin: /starpassend ──────────────────────────────────────────────────
    @app_commands.command(name="starpassend", description="End the running Star Pass right now")
    @admin_only()
    async def starpassend(self, interaction: discord.Interaction):
        ended = await asyncio.to_thread(starpass.end_season)
        if ended is None:
            await interaction.response.send_message("No Star Pass is running.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{ended['name']} has been ended.", ephemeral=True)

    # ── Admin: /seasonreopen ─────────────────────────────────────────────────
    @app_commands.command(name="seasonreopen", description="Bring an ended Star Pass season back")
    @app_commands.describe(
        season="Season number to reopen (1 = Season 1). Empty = the latest ended season",
        days="Run it for this many days from now (needed if its time is already over)",
        replace_running="Another season is running (e.g. Season 2): pause it and bring this one back",
    )
    @admin_only()
    async def seasonreopen(
        self,
        interaction: discord.Interaction,
        season: int | None = None,
        days: int | None = None,
        replace_running: bool = False,
    ):
        if season is not None and not 1 <= season <= 1000:
            await interaction.response.send_message("Season must be between 1 and 1000.", ephemeral=True)
            return
        if days is not None and not 1 <= days <= 60:
            await interaction.response.send_message("Days must be between 1 and 60.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        res = await asyncio.to_thread(starpass.reopen_season, season, days, replace_running)
        if isinstance(res, str):
            await interaction.followup.send(res, ephemeral=True)
            return
        embed = discord.Embed(title="Star Pass reopened", color=EMBED_COLOR)
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
    )
    @app_commands.choices(row=ROW_CHOICES, reward=REWARD_CHOICES)
    @app_commands.autocomplete(playername=player_autocomplete)
    @admin_only()
    async def starpass_rewardadd(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, starpass.MAX_LEVELS],
        row: app_commands.Choice[str],
        reward: app_commands.Choice[str] | None = None,
        quantity: app_commands.Range[int, 1, 100_000_000] = 1,
        playername: str | None = None,
    ):
        if reward is not None and playername:
            await interaction.response.send_message(
                "Choose either a reward type or a player name, not both.", ephemeral=True)
            return
        if reward is None and not playername:
            await interaction.response.send_message(
                "Pick a reward type, or leave it empty and enter a player name.", ephemeral=True)
            return

        if playername:
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

        res = await asyncio.to_thread(starpass.set_reward, level, row.value, kind, qty, key)
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
