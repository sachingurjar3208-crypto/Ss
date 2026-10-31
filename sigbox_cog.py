"""Signature Box.

    rcbox      the Signature Box image with Spin + Prices buttons (aliases: rcsigbox, sigbox)
               (Signature chances are never shown to players, only in /sigbox info)
    rcp        your RC Points (alias: rcpoints)

Admin slash commands (main server only), all under /sigbox:
    /sigbox create   open a box: Signature card + how many days (+ optional name)
    /sigbox reward   change one of the 9 other tiles (pack, voucher, coins, player card, summary theme)
    /sigbox cost     change the RCP price of one spin (1-10)
    /sigbox chance   change the Signature chance of one spin (1-9; spin 10 is always guaranteed)
    /sigbox slotchance  change how likely one reward slot (1-9) is to come up
    /sigbox reset    reset one user's box: they can open it again from spin 1 (keeps what they won)
    /sigbox info     prices, chances and rewards of the open box (+ preview image)
    /sigbox end      close the box now
RC Points: `admin rcp give | remove | check` (admin_cog.py).
Logic + database: sigbox.py. Image + draw animation: sigbox_image.py.
"""
from __future__ import annotations

import asyncio
import io

import discord
from discord import app_commands, ui
from discord.ext import commands

import card_db
import sigbox
import sigbox_image
import squad_logic as sl
from bot_guard import ADMIN_GUILD_ID, MaintenanceActive, is_staff
from economy_cog import send_next_pending
from security import OwnedView, esc
from squad_cog import _in_match, _need_debut

COLOR = 0xE8BE50
CONFIRM_FROM = 480        # spins that cost this many RCP or more ask "are you sure?" first


async def _cards_for(box: dict) -> dict:
    keys = sigbox.player_keys(box)
    return await asyncio.to_thread(card_db.get_cards, keys) if keys else {}


def _card_name(cards: dict, key: str | None) -> str:
    c = cards.get(key) if key else None
    return c["playername"] if c is not None else (key or "Player")


def _embed(board: dict, cards: dict, image: str = sigbox_image.FILENAME) -> discord.Embed:
    box = board["box"]
    e = discord.Embed(color=COLOR)
    e.set_author(name=box["name"])
    e.description = f"Closes <t:{int(box['end_ts'])}:R>"
    e.set_image(url=f"attachment://{image}")
    return e


def _spin_label(board: dict) -> str:
    if board["done"]:
        return "Box complete"
    c = board["next_cost"]
    return f"Spin {board['next_spin']}  ·  FREE" if c == 0 else f"Spin {board['next_spin']}  ·  {c:,} RCP"


async def _render(user_id: int):
    board = await asyncio.to_thread(sigbox.get_board, user_id)
    if board is None:
        return None, None, None
    cards = await _cards_for(board["box"])
    png = await asyncio.to_thread(sigbox_image.board_png, board, cards)
    return board, cards, discord.File(io.BytesIO(png), filename=sigbox_image.FILENAME)


def _result_embed(user, res: dict, cards: dict, box: dict) -> discord.Embed:
    rw = res["reward"]
    if res["slot"] == sigbox.SIGNATURE_SLOT:
        name = esc(_card_name(cards, rw.player_key))
        e = discord.Embed(title="SIGNATURE CARD", color=0xF6D06A,
                          description=f"{esc(user.display_name)} pulled **{name}** (Signature Edition) on spin {res['spin']}.")
    else:
        what = esc(_card_name(cards, rw.player_key)) if rw.kind == "player" else esc(rw.label())
        e = discord.Embed(color=COLOR, description=f"Spin {res['spin']}: **{what}**")
    notes = []
    if res["status"] == "pending":
        notes.append("Your squad is full, decide with `rcpending`.")
    elif res["status"] == "dupe" and rw.kind == "player":
        notes.append(f"Already owned" + (f", refunded **{res['refund']:,}** coins." if res["refund"] else "."))
    elif res["status"] == "dupe" and rw.kind == "theme":
        notes.append("Summary theme already owned.")
    elif rw.kind == "theme":
        notes.append(f"Turn it on with `rcsb use {rw.player_key}`.")
    if res["cost"]:
        notes.append(f"-{res['cost']:,} RCP  ·  {res['board']['balance']:,} RCP left")
    if notes:
        e.set_footer(text="  |  ".join(n.replace("`", "").replace("**", "") for n in notes))
    return e


class _Confirm(ui.View):
    def __init__(self, owner: "SigBoxView", spin: int, cost: int):
        super().__init__(timeout=30)
        self.owner, self.spin = owner, spin
        self.yes.label = f"Spin for {cost:,} RCP"

    @ui.button(label="Spin", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Spinning...", view=None)
        await self.owner.run_spin(interaction, self.spin, from_confirm=True)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


class SigBoxView(OwnedView):
    def __init__(self, bot: commands.Bot, user_id: int, board: dict):
        super().__init__(user_id, timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.board = board
        self._busy = asyncio.Lock()
        self._sync()

    def _sync(self) -> None:
        self.spin_btn.label = _spin_label(self.board)
        self.spin_btn.disabled = self.board["done"]

    @ui.button(label="Spin", style=discord.ButtonStyle.success, row=0)
    async def spin_btn(self, interaction: discord.Interaction, button: ui.Button):
        fresh = await asyncio.to_thread(sigbox.get_board, self.user_id)   # balance may have changed (admin rcp give)
        if fresh is None:
            await interaction.response.send_message("The Signature Box is closed.", ephemeral=True)
            return
        if fresh["box"]["id"] != self.board["box"]["id"]:
            await interaction.response.send_message("A new box has opened. Type `rcbox` again.", ephemeral=True)
            return
        self.board = b = fresh
        self._sync()
        if b["done"]:
            await interaction.response.defer()
            return
        cost = int(b["next_cost"] or 0)
        if cost > b["balance"]:
            await interaction.response.send_message(
                f"You need **{cost:,} RCP** for spin {b['next_spin']}. You have **{b['balance']:,} RCP**.",
                ephemeral=True)
            return
        if cost >= CONFIRM_FROM:
            await interaction.response.send_message(
                f"Spin {b['next_spin']} costs **{cost:,} RCP**. You have **{b['balance']:,} RCP**.",
                view=_Confirm(self, b["next_spin"], cost), ephemeral=True)
            return
        await interaction.response.defer()
        await self.run_spin(interaction, b["next_spin"])

    @ui.button(label="Prices", style=discord.ButtonStyle.secondary, row=0)
    async def odds_btn(self, interaction: discord.Interaction, button: ui.Button):
        box = self.board["box"]
        lines = [f"Spin {i + 1}: **{'FREE' if c == 0 else f'{c:,} RCP'}**" for i, c in enumerate(box["costs"])]
        txt = ("\n".join(lines) + f"\n\nAll 10 spins: **{sum(box['costs']):,} RCP**"
               "\nEvery reward drops only once. If the Signature card has not come by spin 10, spin 10 gives it.")
        await interaction.response.send_message(txt, ephemeral=True)

    async def run_spin(self, interaction: discord.Interaction, spin: int, from_confirm: bool = False) -> None:
        if self._busy.locked():
            return
        async with self._busy:
            await self._spin(interaction, spin, from_confirm)

    async def _edit(self, **kw):
        if self.message is not None:
            try:
                await self.message.edit(**kw)
            except discord.HTTPException:
                pass

    async def _spin(self, interaction: discord.Interaction, spin: int, from_confirm: bool) -> None:
        uid = self.user_id
        if _in_match(self.bot, uid):
            await interaction.followup.send("Finish your current match before spinning.", ephemeral=True)
            return
        before = self.board
        cards = await _cards_for(before["box"])
        values = {k: sl.sell_value(c) for k, c in cards.items()}
        res = await asyncio.to_thread(sigbox.draw, uid, values, spin)
        if isinstance(res, str):
            await interaction.followup.send(res, ephemeral=True)
            board, cards, file = await _render(uid)
            if board is None:
                self.stop()
                await self._edit(embed=discord.Embed(description="The Signature Box is closed.", color=COLOR),
                                 attachments=[], view=None)
                return
            self.board = board
            self._sync()
            await self._edit(embed=_embed(board, cards), attachments=[file], view=self)
            return

        # 1) the light runs over the tiles and stops on the prize
        for c in self.children:
            c.disabled = True
        try:
            gif, land = await asyncio.to_thread(sigbox_image.draw_gif, before, res["slot"], cards)
            await self._edit(embed=_embed(before, cards, sigbox_image.GIF_NAME),
                             attachments=[discord.File(io.BytesIO(gif), filename=sigbox_image.GIF_NAME)], view=self)
            await asyncio.sleep(land)
        except Exception as e:
            print(f"[sigbox] draw animation skipped ({type(e).__name__}: {e})")

        # 2) the updated board + what they got
        self.board = res["board"]
        for c in self.children:
            c.disabled = False
        self._sync()
        png = await asyncio.to_thread(sigbox_image.board_png, self.board, cards)
        await self._edit(embed=_embed(self.board, cards),
                         attachments=[discord.File(io.BytesIO(png), filename=sigbox_image.FILENAME)], view=self)
        channel = self.message.channel if self.message is not None else interaction.channel
        file = None
        if res["slot"] == sigbox.SIGNATURE_SLOT:
            card = cards.get(res["reward"].player_key)
            if card is not None and card["image_path"]:
                try:
                    import card_cache
                    file = await card_cache.card_file(card["image_path"])
                except Exception:
                    file = None
        emb = _result_embed(interaction.user, res, cards, self.board["box"])
        if file is not None:
            emb.set_image(url=f"attachment://{file.filename}")
            await channel.send(embed=emb, file=file, allowed_mentions=discord.AllowedMentions.none())
        else:
            await channel.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())
        if res["status"] == "pending":
            await send_next_pending(channel, uid)


# ── Admin helpers ────────────────────────────────────────────────────────────

REWARD_CHOICES = [
    app_commands.Choice(name="LEGENDARY PACK", value="legendary"),
    app_commands.Choice(name="GOLD PACK", value="gold"),
    app_commands.Choice(name="SILVER PACK", value="silver"),
    app_commands.Choice(name="BRONZE PACK", value="bronze"),
    app_commands.Choice(name="VOUCHER", value="voucher"),
    app_commands.Choice(name="COINS", value="coins"),
]
QTY_CAP = {"voucher": 10_000, "coins": 100_000_000}


async def player_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    names = [n for n in await asyncio.to_thread(card_db.list_playernames) if cur in n.lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


def _theme_choices():
    try:
        import summary_theme as st
        return [app_commands.Choice(name=st.label(n), value=n) for n in st.NAMES]
    except Exception:
        return []


def _info_text(box: dict, cards: dict) -> str:
    rows = [f"{box['name']}  ·  Signature: {_card_name(cards, box['player_key'])}",
            f"Closes <t:{int(box['end_ts'])}:R>", "", "Tiles:"]
    pct = sigbox.slot_percents(box)
    for i, rw in enumerate(box["rewards"], 1):
        what = _card_name(cards, rw.player_key) if rw.kind == "player" else rw.label()
        rows.append(f"  slot {i}: {what}  ·  chance {box['weights'][i - 1]:g} (= {pct[i - 1]:g}% on spin 1)")
    rows += ["", "Spins (price · Signature chance):"]
    for i in range(sigbox.SPINS):
        ch = "guaranteed" if i == sigbox.SPINS - 1 else f"{box['chances'][i]:g}%"
        c = box["costs"][i]
        rows.append(f"  spin {i + 1}: {'FREE' if c == 0 else f'{c:,} RCP'} · {ch}")
    return "\n".join(rows)


class SigBoxCog(commands.Cog, name="SigBox"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await asyncio.to_thread(sigbox.ensure_tables)
        except Exception as e:
            print(f"[sigbox] could not create tables: {e}")

    # ── rcbox ────────────────────────────────────────────────────────────────
    @commands.command(name="rcbox", aliases=["rcsigbox", "sigbox"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcbox(self, ctx: commands.Context):
        if not await _need_debut(ctx):
            return
        async with ctx.typing():
            board, cards, file = await _render(ctx.author.id)
        if board is None:
            await ctx.send("No Signature Box is open right now.")
            return
        view = SigBoxView(self.bot, ctx.author.id, board)
        view.message = await ctx.send(embed=_embed(board, cards), file=file, view=view)

    @commands.command(name="rcp", aliases=["rcpoints"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def rcp(self, ctx: commands.Context):
        bal = await asyncio.to_thread(sigbox.rcp_balance, ctx.author.id)
        await ctx.send(f"{esc(ctx.author.display_name)}, you have **{bal:,} RCP**.",
                       allowed_mentions=discord.AllowedMentions.none())

    # ── /sigbox (admin) ──────────────────────────────────────────────────────
    group = app_commands.Group(name="sigbox", description="Signature Box (admin)",
                               guild_only=True, default_permissions=discord.Permissions())
    app_commands.guilds(ADMIN_GUILD_ID)(group)       # set on the GROUP, not the subcommands

    @group.command(name="create", description="Open a Signature Box (the open one, if any, closes)")
    @app_commands.describe(playername="The Signature card (the big prize)", days="How many days it stays open",
                           name="Box name shown on the image (default: Signature Box)")
    @app_commands.autocomplete(playername=player_autocomplete)
    @app_commands.check(lambda i: is_staff(i.user))
    async def create(self, interaction: discord.Interaction, playername: str,
                     days: app_commands.Range[int, 1, sigbox.MAX_DAYS], name: str | None = None):
        card = await asyncio.to_thread(card_db.get_card, playername)
        if card is None:
            await interaction.response.send_message(f"No card named {esc(playername)}. Pick one from the list.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        box = await asyncio.to_thread(sigbox.create_box, card["playername_key"], days, name, interaction.user.id)
        note = ""
        theme = str(card["s2_theme"] or "").lower() if "s2_theme" in card.keys() else ""
        if theme != "signature":
            note = "\nNote: this card is not a Signature Edition card yet (`/editcard theme:Signature Edition`)."
        try:
            if not int(card["obtainable"]):
                pass
            else:
                note += "\nTip: set this card to obtainable False so it only comes from the box."
        except Exception:
            pass
        await interaction.followup.send(
            f"**{esc(box['name'])}** is open for {days} days with **{esc(card['playername'])}**. "
            f"Change tiles with /sigbox reward, prices with /sigbox cost, chances with /sigbox chance.{note}",
            ephemeral=True)

    @group.command(name="reward", description="Change one of the 9 other tiles of the open box")
    @app_commands.describe(slot="Tile 1-9 (see /sigbox info)", reward="Pack, voucher or coins",
                           quantity="How many (for example 5000 coins)",
                           playername="Give a player card instead (leave reward empty)",
                           summary_theme="Give a summary theme instead (leave reward and player empty)")
    @app_commands.choices(reward=REWARD_CHOICES, summary_theme=_theme_choices())
    @app_commands.autocomplete(playername=player_autocomplete)
    @app_commands.check(lambda i: is_staff(i.user))
    async def reward(self, interaction: discord.Interaction,
                     slot: app_commands.Range[int, 1, sigbox.OTHER_SLOTS],
                     reward: app_commands.Choice[str] | None = None,
                     quantity: app_commands.Range[int, 1, 100_000_000] = 1,
                     playername: str | None = None,
                     summary_theme: app_commands.Choice[str] | None = None):
        if sum(x is not None and x != "" for x in (reward, playername, summary_theme)) != 1:
            await interaction.response.send_message(
                "Choose exactly one: a reward type, a player name or a summary theme.", ephemeral=True)
            return
        if summary_theme is not None:
            kind, qty, key, what = "theme", 1, summary_theme.value, f"{summary_theme.name} Summary"
        elif playername:
            card = await asyncio.to_thread(card_db.get_card, playername)
            if card is None:
                await interaction.response.send_message(f"No card named {esc(playername)}.", ephemeral=True)
                return
            kind, qty, key, what = "player", 1, card["playername_key"], f"{card['playername']} (player card)"
        else:
            kind, qty, key = reward.value, int(quantity), None
            cap = QTY_CAP.get(kind, 100)
            if qty > cap:
                await interaction.response.send_message(f"The most you can give of this reward is {cap:,}.", ephemeral=True)
                return
            what = sigbox.Reward(kind, qty).label()
        res = await asyncio.to_thread(sigbox.set_reward, slot, kind, qty, key)
        if isinstance(res, str):
            await interaction.response.send_message(res, ephemeral=True)
            return
        await interaction.response.send_message(f"Tile {slot} now gives: {esc(what)}.", ephemeral=True)

    @group.command(name="cost", description="Change the RCP price of one spin")
    @app_commands.describe(spin="Spin number 1-10", rcp="Price in RC Points (0 = free)")
    @app_commands.check(lambda i: is_staff(i.user))
    async def cost(self, interaction: discord.Interaction, spin: app_commands.Range[int, 1, sigbox.SPINS],
                   rcp: app_commands.Range[int, 0, 1_000_000]):
        res = await asyncio.to_thread(sigbox.set_cost, spin, rcp)
        if isinstance(res, str):
            await interaction.response.send_message(res, ephemeral=True)
            return
        await interaction.response.send_message(
            f"Spin {spin} now costs {'nothing (FREE)' if rcp == 0 else f'{rcp:,} RCP'}.", ephemeral=True)

    @group.command(name="chance", description="Change the Signature chance of one spin (spin 10 is always guaranteed)")
    @app_commands.describe(spin="Spin number 1-9", percent="Chance in % (for example 1, 2.5, 10)")
    @app_commands.check(lambda i: is_staff(i.user))
    async def chance(self, interaction: discord.Interaction, spin: app_commands.Range[int, 1, sigbox.OTHER_SLOTS],
                     percent: app_commands.Range[float, 0.0, 100.0]):
        res = await asyncio.to_thread(sigbox.set_chance, spin, percent)
        if isinstance(res, str):
            await interaction.response.send_message(res, ephemeral=True)
            return
        await interaction.response.send_message(f"Spin {spin} now has a {percent:g}% Signature chance.", ephemeral=True)

    @group.command(name="slotchance", description="Change how likely one reward slot (1-9) is to come up")
    @app_commands.describe(slot="Reward slot 1-9 (see /sigbox info)",
                           chance="Chance 0-100, compared with the rewards still left. Low = comes in the last spins. 0 = last")
    @app_commands.check(lambda i: is_staff(i.user))
    async def slotchance(self, interaction: discord.Interaction,
                         slot: app_commands.Range[int, 1, sigbox.OTHER_SLOTS],
                         chance: app_commands.Range[float, 0.0, 100.0]):
        res = await asyncio.to_thread(sigbox.set_slot_chance, slot, chance)
        if isinstance(res, str):
            await interaction.response.send_message(res, ephemeral=True)
            return
        cards = await _cards_for(res)
        rw = res["rewards"][slot - 1]
        what = _card_name(cards, rw.player_key) if rw.kind == "player" else rw.label()
        pct = sigbox.slot_percents(res)
        lines = [f"Slot {i}: {res['weights'][i - 1]:g}  ({pct[i - 1]:g}%)" for i in range(1, sigbox.OTHER_SLOTS + 1)]
        await interaction.response.send_message(
            f"Slot {slot} ({esc(what)}) now has chance **{chance:g}**.\n"
            "Chance of every slot on spin 1 (later spins: only the rewards left):\n"
            + "\n".join(lines), ephemeral=True)

    @group.command(name="reset", description="Reset a user's Signature Box so they can open it again from spin 1")
    @app_commands.describe(user="Whose box to reset (they keep everything they already won)")
    @app_commands.check(lambda i: is_staff(i.user))
    async def reset(self, interaction: discord.Interaction, user: discord.Member):
        res = await asyncio.to_thread(sigbox.reset_user, user.id)
        if isinstance(res, str):
            await interaction.response.send_message(res, ephemeral=True)
            return
        if res["removed"] == 0:
            await interaction.response.send_message(
                f"{esc(user.display_name)} has not spun this box yet, nothing to reset.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Reset **{esc(user.display_name)}**'s {esc(res['box']['name'])}: {res['removed']} spin(s) cleared. "
            "They can open it again from spin 1 (free spins too). What they already won stays with them.",
            ephemeral=True)

    @group.command(name="info", description="Prices, chances and tiles of the open box")
    @app_commands.check(lambda i: is_staff(i.user))
    async def info(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        board, cards, file = await _render(interaction.user.id)
        if board is None:
            await interaction.followup.send("No Signature Box is open.", ephemeral=True)
            return
        await interaction.followup.send(_info_text(board["box"], cards), file=file, ephemeral=True)

    @group.command(name="end", description="Close the Signature Box now")
    @app_commands.check(lambda i: is_staff(i.user))
    async def end(self, interaction: discord.Interaction):
        box = await asyncio.to_thread(sigbox.end_box)
        if box is None:
            await interaction.response.send_message("No Signature Box is open.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**{esc(box['name'])}** is closed. Everything players won stays with them.", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MaintenanceActive):
            return
        if isinstance(error, app_commands.CheckFailure):
            msg = "Only admins can use this command."
        else:
            print(f"[sigbox] command error: {type(error).__name__}: {error}")
            msg = "Something went wrong. Please try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(SigBoxCog(bot))
