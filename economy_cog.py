"""Economy commands: rcbal, rcdaily, rcweekly, rcmonthly, rcpack, rcopen,
rcbuy, rcsell, rcms, rctrade, rcleaderboard.

Safety notes
- Anything that spends coins or gives a player away asks for a button
  confirmation first, and only the person it was made for can press it.
- The real check (enough coins? do you still own it?) happens again inside
  economy.py's single database transaction when the button is pressed, so
  spamming buttons or commands can never double-spend or copy a card.
- Squads are locked while a player is in a live match.
"""

from __future__ import annotations

import asyncio
import functools
import io
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import discord
from discord import ui
from discord.ext import commands

import card_db
import economy
import emojis
import pack_anim
import pack_gif
import quest
import security
import squad_logic as sl
import card_cache
from card_narratives import ROLES
from security import OwnedView, esc, fmt_wait
from squad_cog import _card_line, _in_match, _is_premium, _need_debut, squad_order

# ── One shared 5 second cooldown for ALL economy commands ────────────────────
# Each command used to have its own timer, so spamming rcbal/rcopen/rcbuy one
# after another still worked. Now every economy command shares ONE timer per
# user: after any of them, the user must wait ECONOMY_COOLDOWN seconds.
ECONOMY_COOLDOWN = 5

# Any command that opens a "spend coins" confirm button (rcpack, rcbuy,
# rcsell, rctrade), plus rcopen (opens a pack — same pacing, even though it
# has no confirm button of its own), uses this pair instead of/alongside
# ECONOMY_COOLDOWN:
# the buttons grey out at CONFIRM_TIMEOUT, and the user can't issue the same
# kind of command again for CONFIRM_COOLDOWN seconds — by which point the old
# buttons are already dead. Without this, a user could fire the command
# again during the old 5s window, get a SECOND live confirm button while the
# first was still active, and press both — paying twice but also farming
# quest counters (packs_bought/packs_opened) faster than intended.
CONFIRM_TIMEOUT = 10.0
CONFIRM_COOLDOWN = 15.0

# The free debut pack can only be opened in this server.
DEBUT_PACK_GUILD_ID = 1556644438917382150
# rcbuy / rcsell only need a 2 second gap. rcms (multi sell) has its own
# 15 second cooldown per user, the same in every server and channel.
BUYSELL_COOLDOWN = 2
MULTISELL_COOLDOWN = 15.0
MULTISELL_TIMEOUT = 20.0     # the Sell / Cancel buttons switch off after this

# When each user last used an economy command (monotonic seconds).
_last_econ: dict[int, float] = {}

# Users whose economy command is running RIGHT NOW (opening a pack, claiming...).
_running: set[int] = set()


def has_pending(uid: int) -> bool:
    """True while this user has a confirm button / trade waiting for an answer."""
    return uid in _pending_confirm or uid in _pending_trade


def is_busy(uid: int) -> bool:
    """True while ANY economy command or draft of this user is pending."""
    return has_pending(uid) or uid in _running


def economy_gate(seconds: float = ECONOMY_COOLDOWN):
    """Shared gate for every economy command (and rcdraft).

    1. If the user already has a command pending (confirm button open, trade
       waiting, pack opening, draft running) no other command can start. This
       is what stops farming by firing several commands at once.
    2. One timer per user shared by all economy commands: after any economy
       command the user waits `seconds` before the next one. seconds=0 only
       does the pending check (used by rcdraft, which has its own cooldown).
    """
    async def predicate(ctx: commands.Context) -> bool:
        uid = ctx.author.id
        if is_busy(uid):
            if security._notice_allowed(uid, 3):
                await ctx.send("You already have a command pending. Finish it first.")
            raise commands.CheckFailure("pending command")
        if seconds > 0:
            now = time.monotonic()
            wait = seconds - (now - _last_econ.get(uid, 0.0))
            if wait > 0:
                # security.py's error handler shows "is on cooldown. Try again in Xs".
                raise commands.CommandOnCooldown(commands.Cooldown(1, seconds), wait, commands.BucketType.user)
            if len(_last_econ) > 5000:   # keep the dict small
                for k in [k for k, t in _last_econ.items() if now - t > 600]:
                    _last_econ.pop(k, None)
            _last_econ[uid] = now
        return True
    return commands.check(predicate)


def economy_cooldown():
    """Old name, kept so nothing else breaks. Same as economy_gate(5)."""
    return economy_gate(ECONOMY_COOLDOWN)


def exclusive(fn):
    """Marks the user as busy while the command body runs, so no second
    economy command can start in the meantime (even if two messages race)."""
    @functools.wraps(fn)
    async def wrapper(self, ctx, *args, **kwargs):
        uid = ctx.author.id
        if uid in _running:
            if security._notice_allowed(uid, 3):
                await ctx.send("You already have a command pending. Finish it first.")
            return
        _running.add(uid)
        try:
            return await fn(self, ctx, *args, **kwargs)
        finally:
            _running.discard(uid)
    return wrapper


# Players with a trade waiting for an answer (each can only have one at a time).
_pending_trade: set[int] = set()

# Players with a rcpack/rcbuy/rcsell confirm button waiting for a press (each
# can only have ONE at a time — this is what actually blocks the farm, the
# cooldown above is just pacing on top of it).
_pending_confirm: set[int] = set()


# ── A reusable "are you sure?" button pair ───────────────────────────────────

class ConfirmView(OwnedView):
    def __init__(self, user_id: int, action: Callable[[], Awaitable[str]], timeout: float = CONFIRM_TIMEOUT,
                 locked: bool = False):
        super().__init__(user_id, timeout=timeout)
        self.user_id = user_id
        self.action = action
        self.used = False
        if locked:
            # Pack turned off by an admin: the buy (Confirm) button stays visible but grey and dead.
            self.confirm.disabled = True
            self.confirm.style = discord.ButtonStyle.secondary

    @ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:  # a second click can never run the action twice
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        _pending_confirm.discard(self.user_id)
        result = await self.action()
        await interaction.response.edit_message(content=result, embed=None, view=None)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self.used = True
        self.stop()
        _pending_confirm.discard(self.user_id)
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)

    async def on_timeout(self) -> None:
        _pending_confirm.discard(self.user_id)
        await super().on_timeout()


# ── rcpack: pack shop with a "Select a pack" menu ────────────────────────────
# `rcpack` shows every pack on sale (emoji, price, OVR range, how many you own) and a
# drop-down. Picking a pack asks Confirm / Cancel, the same safe buy flow as before.
# `rcpack bronze` (typing the name) still works as a shortcut.

SHOP_TIMEOUT = 60.0


def _shop_packs() -> list[tuple[str, dict]]:
    """Packs that can be bought right now (event packs only while their event has cards)."""
    return [(k, v) for k, v in sl.PACKS.items() if v.get("shop") is not False and sl.pack_available(k)]


def _buy_action(channel, uid: int, key: str) -> Callable[[], Awaitable[str]]:
    """The coroutine ConfirmView runs when ✅ is pressed. Money is only taken in here,
    inside economy.buy_pack's single database transaction."""
    spec = sl.PACKS[key]

    async def do_buy() -> str:
        if not sl.pack_available(key):
            return "❌ That pack isn't on sale right now."
        if economy.is_pack_off(key):
            return "🔒 This pack is turned off right now."
        err = economy.buy_pack(uid, key, spec["price"])
        if err:
            return f"❌ {err}"
        await quest.track_and_notify(channel, uid, {"packs_bought": 1})
        return (
            f"✅ Bought 1× {spec['emoji']} {spec['label']} for {economy.fmt_coins(spec['price'])}. "
            f"Open it with `rcopen {key}`."
        )
    return do_buy


def _shop_embed(uid: int) -> discord.Embed:
    owned = economy.get_packs(uid)
    off = economy.packs_off()
    lines = []
    for key, spec in _shop_packs():
        lo, hi = spec["range"]
        extra = " · event players only" if spec.get("event") else ""
        if key in off:
            extra += " · 🔒 not available right now"
        have = f" · you own **{owned[key]}**" if owned.get(key) else ""
        lines.append(
            f"{spec['emoji']} **{spec['label']}**\n"
            f"{economy.fmt_coins(spec['price'])} · 1 player · {lo}-{hi} OVR{extra}{have}"
        )
    embed = discord.Embed(title="📦 Pack Shop", description="\n\n".join(lines) or "No packs on sale right now.",
                          color=discord.Color.gold())
    embed.set_footer(text="Pick a pack below to buy it, then open it with rcopen <type>. Duplicates turn into coins.")
    return embed


class PackShopSelect(ui.Select):
    def __init__(self):
        options = []
        off = economy.packs_off()
        for key, spec in _shop_packs():
            lo, hi = spec["range"]
            options.append(discord.SelectOption(
                label=spec["label"], value=key,
                description=(("🔒 Off · " if key in off else "") + f"{spec['price']:,} coins · {lo}-{hi} OVR")[:100],
                emoji=discord.PartialEmoji.from_str(spec["emoji"]) if spec.get("emoji") else None,
            ))
        super().__init__(placeholder="Select a pack", min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        await self.view.picked(interaction, self.values[0])


class PackShopView(OwnedView):
    def __init__(self, user_id: int):
        super().__init__(user_id, timeout=SHOP_TIMEOUT)
        self.user_id = user_id
        self.used = False
        self.add_item(PackShopSelect())

    async def picked(self, interaction: discord.Interaction, key: str):
        spec = sl.PACKS.get(key)
        if spec is None or spec.get("shop") is False or not sl.pack_available(key):
            await interaction.response.send_message("❌ That pack isn't on sale right now.", ephemeral=True)
            return
        uid = self.user_id
        if self.used or uid in _pending_confirm:
            await interaction.response.send_message(
                "❌ You already have a confirmation waiting — finish that first.", ephemeral=True)
            return
        self.used = True
        self.stop()
        _pending_confirm.add(uid)
        try:
            off = economy.is_pack_off(key)
            confirm = ConfirmView(uid, _buy_action(interaction.channel, uid, key), locked=off)
            confirm.message = interaction.message
            text = f"Buy 1× {spec['emoji']} **{spec['label']}** for **{economy.fmt_coins(spec['price'])}**?"
            if off:
                text += "\n🔒 This pack isn't available right now."
            await interaction.response.edit_message(content=text, embed=None, view=confirm)
        except Exception:
            _pending_confirm.discard(uid)
            raise


# ── rcopen: pack animation, then the REAL card ───────────────────────────────

async def _pack_intro(key: str):
    """(discord.File, seconds to wait) for the opening animation of this pack:
    an animation added with `/pack animation add` if there is one, else the built-in
    pack intro (pack_gif.py, with NO card in it). (None, 0) = no animation at all."""
    path = await asyncio.to_thread(pack_anim.pick, key)
    if path is not None:
        try:
            return discord.File(str(path), filename=f"pack_open{path.suffix.lower()}"), pack_anim.ANIM_SECONDS
        except OSError as e:
            print(f"[rcopen] could not read animation {path}: {e}")
    return await pack_gif.intro_file(key)


# ── rcbuy / rcsell: card image + two emoji buttons ───────────────────────────
# The message shows the card image with a green check and a red cross under it.
# After BUYSELL_TIMEOUT seconds both buttons switch off (greyed out), so
# pressing them later does nothing.
BUYSELL_TIMEOUT = CONFIRM_TIMEOUT


def _plain_coins(n: int) -> str:
    return f"{int(n):,} coins"


def _buysell_embed(card, text: str) -> discord.Embed:
    embed = discord.Embed(description=text, color=discord.Color.gold())
    path = card["image_path"]
    if path and Path(path).exists():
        embed.set_image(url=card_cache.image_url(path))
    return embed


async def _send_buysell(ctx: commands.Context, card, text: str, action: Callable[[], Awaitable[str]],
                        buy_locked: bool = False):
    """Send the card image + text with the check / cross buttons.
    Refuses (politely) if this user already has a confirm button waiting —
    see _pending_confirm above."""
    if ctx.author.id in _pending_confirm:
        await ctx.send("❌ You already have a confirmation waiting — finish that first.")
        return
    _pending_confirm.add(ctx.author.id)
    try:
        import time as _t
        view = BuySellView(ctx.author.id, card, action, buy_locked=buy_locked)
        t0 = _t.perf_counter()
        embed = _buysell_embed(card, text)
        path = card["image_path"]
        url = card_cache.cached_url(path)
        file = None if url else await card_cache.card_file(path)   # cached link = no upload
        if not url and file is None:
            embed = discord.Embed(description=text, color=discord.Color.gold())
        t1 = _t.perf_counter()
        if file:
            view.message = await ctx.send(embed=embed, file=file, view=view)
            card_cache.remember(path, view.message)
        else:
            view.message = await ctx.send(embed=embed, view=view)
        t2 = _t.perf_counter()
        how = "cdn-link" if url else ("upload" if file else "no-image")
        print(f"[timing buy/sell] image={t1 - t0:.2f}s  discord_send={t2 - t1:.2f}s  via={how}")
    except Exception:
        # The view never made it to Discord (or never got far enough to be
        # tracked), so nothing will ever release the flag for us — release it
        # here instead of leaving this user permanently "pending".
        _pending_confirm.discard(ctx.author.id)
        raise


LOCKED_CARD_TEXT = "🔒 This card isn't sold in the shop"


class BuySellView(OwnedView):
    def __init__(self, user_id: int, card, action: Callable[[], Awaitable[str]], buy_locked: bool = False):
        super().__init__(user_id, timeout=BUYSELL_TIMEOUT)
        self.user_id = user_id
        self.card = card
        self.action = action
        self.used = False
        if buy_locked:
            # Unobtainable card: the buy (tick) button stays visible but grey and dead.
            self.yes.disabled = True
            self.yes.style = discord.ButtonStyle.secondary

    @ui.button(emoji="✅", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:  # a second click can never run the action twice
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        _pending_confirm.discard(self.user_id)
        result = await self.action()
        await interaction.response.edit_message(embed=_buysell_embed(self.card, result), view=None)

    @ui.button(emoji="❌", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        _pending_confirm.discard(self.user_id)
        await interaction.response.edit_message(embed=_buysell_embed(self.card, "Cancelled."), view=None)

    async def on_timeout(self) -> None:
        _pending_confirm.discard(self.user_id)
        await super().on_timeout()


# ── rcbuy browser: same name, many cards (◀ ▶) ─────────────────────────────
# `rcbuy virat kohli` shows every Virat Kohli card (normal, event, renamed ...).
# ✅ buys the card on screen, ❌ cancels, ◀ ▶ flip through the cards.

def _buy_text(card) -> str:
    text = f"**Price:** `{sl.buy_price(card):,}` {emojis.COIN}"
    if not card_db.is_obtainable(card):
        text += "\n" + LOCKED_CARD_TEXT
    return text


def find_buy_cards(text: str) -> list:
    """Every card that matches the typed name, exact name first, then the rest of the
    same real player (group), then other name matches. No duplicates."""
    found = list(card_db.find_cards(text, limit=24))
    out, seen = [], set()

    def add(c):
        k = c["playername_key"]
        if k not in seen:
            seen.add(k)
            out.append(c)

    for c in found:
        add(c)
    if found:
        for c in card_db.list_group_cards(card_db.group_key(found[0])):
            add(c)
    return out[:25]


class BuyBrowseView(OwnedView):
    def __init__(self, user_id: int, cards: list, index: int = 0):
        super().__init__(user_id, timeout=BUYSELL_TIMEOUT)
        self.user_id = user_id
        self.cards = cards
        self.index = index
        self.used = False
        if len(cards) < 2:
            self.remove_item(self.prev_b)
            self.remove_item(self.next_b)
        self._sync_buttons()

    @property
    def card(self):
        return self.cards[self.index]

    def _sync_buttons(self) -> None:
        # Cards nobody may buy: the tick stays visible but grey and dead.
        locked = not card_db.is_obtainable(self.card)
        self.yes.disabled = locked
        self.yes.style = discord.ButtonStyle.secondary if locked else discord.ButtonStyle.success

    def _embed(self) -> discord.Embed:
        return _buysell_embed(self.card, _buy_text(self.card))

    async def _show(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        embed, path = self._embed(), self.card["image_path"]
        if card_cache.cached_url(path) or not path:
            await interaction.response.edit_message(embed=embed, view=self)
            return
        await interaction.response.defer()
        file = await card_cache.card_file(path)
        if file is None:
            await interaction.edit_original_response(
                embed=discord.Embed(description=_buy_text(self.card), color=discord.Color.gold()),
                attachments=[], view=self)
            return
        msg = await interaction.edit_original_response(embed=embed, attachments=[file], view=self)
        card_cache.remember(path, msg)

    @ui.button(emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def yes(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        _pending_confirm.discard(self.user_id)
        uid, card = self.user_id, self.card
        key = card["playername_key"]
        if _in_match(interaction.client, uid):
            result = "You can't buy players during a match."
        elif not card_db.is_obtainable(card):
            result = LOCKED_CARD_TEXT
        elif economy.owns(uid, key):
            result = "You already own this player."
        elif economy.squad_count(uid) >= economy.SQUAD_MAX:
            result = (f"❌ Your squad is full (**{economy.SQUAD_MAX}/{economy.SQUAD_MAX}**). "
                      f"Sell a player first with `rcsell`.")
        else:
            price = sl.buy_price(card)
            e = economy.buy_card(uid, key, price)
            result = str(e) if e else f"Bought {esc(card['playername'])} for {_plain_coins(price)}."
        await interaction.response.edit_message(embed=_buysell_embed(card, result), view=None)

    @ui.button(emoji="❌", style=discord.ButtonStyle.danger, row=0)
    async def no(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        _pending_confirm.discard(self.user_id)
        await interaction.response.edit_message(embed=_buysell_embed(self.card, "Cancelled."), view=None)

    @ui.button(emoji="◀️", style=discord.ButtonStyle.primary, row=0)
    async def prev_b(self, interaction: discord.Interaction, button: ui.Button):
        self.index = (self.index - 1) % len(self.cards)
        await self._show(interaction)

    @ui.button(emoji="▶️", style=discord.ButtonStyle.primary, row=0)
    async def next_b(self, interaction: discord.Interaction, button: ui.Button):
        self.index = (self.index + 1) % len(self.cards)
        await self._show(interaction)

    async def on_timeout(self) -> None:
        _pending_confirm.discard(self.user_id)
        await super().on_timeout()


async def _send_buy_browser(ctx: commands.Context, cards: list):
    if ctx.author.id in _pending_confirm:
        await ctx.send("❌ You already have a confirmation waiting — finish that first.")
        return
    _pending_confirm.add(ctx.author.id)
    try:
        view = BuyBrowseView(ctx.author.id, cards)
        card = view.card
        embed, path = view._embed(), card["image_path"]
        url = card_cache.cached_url(path)
        file = None if url else await card_cache.card_file(path)
        if not url and file is None:
            embed = discord.Embed(description=_buy_text(card), color=discord.Color.gold())
        if file:
            view.message = await ctx.send(embed=embed, file=file, view=view)
            card_cache.remember(path, view.message)
        else:
            view.message = await ctx.send(embed=embed, view=view)
    except Exception:
        _pending_confirm.discard(ctx.author.id)
        raise


# ── rcms: multi sell ─────────────────────────────────────────────────────────

class MultiSellView(OwnedView):
    """Sell / Cancel buttons for rcms. Nothing is sold until Sell is pressed."""

    def __init__(self, bot, user_id: int, items: list, bench_order: list):
        super().__init__(user_id, timeout=MULTISELL_TIMEOUT)
        self.bot = bot
        self.user_id = user_id
        self.items = items
        self.bench_order = bench_order
        self.total = sum(v for _, v in items)
        self.used = False

    @ui.button(label="Sell", style=discord.ButtonStyle.danger)
    async def sell(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:  # a second click can never sell twice
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        _pending_confirm.discard(self.user_id)
        if _in_match(self.bot, self.user_id):
            await interaction.response.edit_message(content="You can't sell players during a match.", embed=None, view=None)
            return
        err, total = await asyncio.to_thread(economy.sell_cards, self.user_id, self.items, self.bench_order)
        if err:
            await interaction.response.edit_message(content=err, embed=None, view=None)
            return
        await interaction.response.edit_message(
            content=f"Sold {len(self.items)} players for {_plain_coins(total)}.", embed=None, view=None
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        _pending_confirm.discard(self.user_id)
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)

    async def on_timeout(self) -> None:
        _pending_confirm.discard(self.user_id)
        await super().on_timeout()


# ── Squad full (25 players): sell the new card or replace one of your players ─

PENDING_TIMEOUT = 120.0


class ReplaceSelect(ui.Select):
    def __init__(self, parent: "PendingCardView", options: list[discord.SelectOption]):
        super().__init__(placeholder="Pick the player to replace…", min_values=1, max_values=1, options=options)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.finish(interaction, "replace", self.values[0])


class PendingCardView(OwnedView):
    """Shown when a card arrives while the squad is full (25/25)."""

    def __init__(self, user_id: int, card):
        super().__init__(user_id, timeout=PENDING_TIMEOUT)
        self.uid = user_id
        self.card = card
        self.key = card["playername_key"]
        self.used = False
        self.sell.label = f"Sell it (+{sl.sell_value(card):,})"

    @ui.button(label="Sell it", style=discord.ButtonStyle.success, emoji="💰")
    async def sell(self, interaction: discord.Interaction, button: ui.Button):
        await self.finish(interaction, "sell")

    @ui.button(label="Replace a player", style=discord.ButtonStyle.primary, emoji="🔁")
    async def replace(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:
            await interaction.response.defer()
            return
        cards = sorted(sl.owned_cards(self.uid), key=lambda c: (int(c["ovr"]), c["playername"]))[: economy.SQUAD_MAX]
        in_xi = set(economy.get_xi(self.uid).values())
        options = []
        for c in cards:
            role = ROLES.get(card_db.effective_role(c), "")
            desc = f"{role} · sells for {sl.sell_value(c):,}" + (" · in your XI" if c["playername_key"] in in_xi else "")
            options.append(discord.SelectOption(
                label=f"{c['playername']} · {c['ovr']} OVR"[:100], value=c["playername_key"], description=desc[:100],
            ))
        if not options:
            await interaction.response.send_message("You have no players to replace.", ephemeral=True)
            return
        self.clear_items()
        self.add_item(ReplaceSelect(self, options))
        self.add_item(self.later)
        await interaction.response.edit_message(
            embed=self._embed("Pick which player this card should replace. The replaced player is sold at their value."),
            view=self,
        )

    @ui.button(label="Decide later", style=discord.ButtonStyle.secondary)
    async def later(self, interaction: discord.Interaction, button: ui.Button):
        self.used = True
        self.stop()
        await interaction.response.edit_message(
            embed=self._embed("Saved. Use `rcpending` whenever you want to decide."), view=None
        )

    def _embed(self, text: str) -> discord.Embed:
        embed = _buysell_embed(self.card, text)
        return embed

    async def finish(self, interaction: discord.Interaction, action: str, replace_key: str | None = None):
        if self.used:
            await interaction.response.defer()
            return
        if _in_match(interaction.client, self.uid):
            await interaction.response.send_message("❌ You can't change your squad during a match.", ephemeral=True)
            return
        self.used = True
        self.stop()
        new_value = sl.sell_value(self.card)
        old_card = card_db.get_card(replace_key) if replace_key else None
        old_value = sl.sell_value(old_card) if old_card else 0
        err = await asyncio.to_thread(
            economy.resolve_pending, self.uid, self.key, action, replace_key, new_value, old_value
        )
        if err:
            result = f"❌ {err}"
        elif action == "sell":
            result = f"💰 Sold **{esc(self.card['playername'])}** for **{_plain_coins(new_value)}**."
        else:
            result = (
                f"🔁 **{esc(self.card['playername'])}** joined your squad and replaced "
                f"**{esc(old_card['playername'])}** (sold for **{_plain_coins(old_value)}**)."
            )
        await interaction.response.edit_message(embed=self._embed(result), view=None)
        await send_next_pending(interaction.channel, self.uid)


async def send_next_pending(dest, user_id: int) -> bool:
    """Send the prompt for the user's oldest waiting card (if any). One at a time:
    the next one appears when this one is decided. dest = ctx or a channel."""
    keys = await asyncio.to_thread(economy.get_pending, user_id)
    for key in keys:
        card = card_db.get_card(key)
        if card is None:
            continue
        view = PendingCardView(user_id, card)
        n = economy.squad_count(user_id)
        embed = discord.Embed(
            description=(
                f"⚠️ **Your squad is full ({n}/{economy.SQUAD_MAX})!**\n"
                f"New card: **{esc(card['playername'])}** ({card['ovr']} OVR)\n"
                f"Sell it for **{_plain_coins(sl.sell_value(card))}**, or replace one of your players with it."
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Not now? It stays saved — use rcpending later.")
        view.message = await card_cache.send_embed(dest, embed, card["image_path"], view=view)
        return True
    return False


# ── Trade offer buttons ──────────────────────────────────────────────────────

class TradeView(OwnedView):
    def __init__(self, proposer: discord.Member, target: discord.Member, give_key: str, get_key: str):
        super().__init__({proposer.id, target.id}, timeout=60)
        self.proposer, self.target = proposer, target
        self.give_key, self.get_key = give_key, get_key
        self.done = False

    def _finish(self):
        self.done = True
        _pending_trade.discard(self.proposer.id)
        _pending_trade.discard(self.target.id)
        self.stop()

    async def on_timeout(self) -> None:
        _pending_trade.discard(self.proposer.id)
        _pending_trade.discard(self.target.id)
        await super().on_timeout()

    @ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="🤝")
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("Only the player who received the offer can accept.", ephemeral=True)
            return
        if self.done:
            await interaction.response.defer()
            return
        self._finish()
        if _in_match(interaction.client, self.proposer.id) or _in_match(interaction.client, self.target.id):
            await interaction.response.edit_message(content="❌ Trade cancelled — a player is in a match.", embed=None, view=None)
            return
        a_card, b_card = card_db.get_card(self.give_key), card_db.get_card(self.get_key)
        if a_card is None or b_card is None or int(a_card["ovr"]) != int(b_card["ovr"]):
            await interaction.response.edit_message(
                content="❌ Trade cancelled — the two players no longer have the same OVR.", embed=None, view=None
            )
            return
        err = economy.execute_trade(self.proposer.id, self.give_key, self.target.id, self.get_key)
        if err:
            await interaction.response.edit_message(content=f"❌ Trade failed: {err}", embed=None, view=None)
            return
        gave, got = card_db.get_card(self.give_key), card_db.get_card(self.get_key)
        await interaction.response.edit_message(
            content=(
                f"🤝 Trade complete! {esc(self.proposer.display_name)} gave **{esc(gave['playername'])}** "
                f"and received **{esc(got['playername'])}** from {esc(self.target.display_name)}.\n"
                f"💸 Trade fee: **{economy.TRADE_FEE:,} coins** paid by each of you."
            ),
            embed=None, view=None,
        )

    @ui.button(label="Decline / Cancel", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if self.done:
            await interaction.response.defer()
            return
        self._finish()
        who = "declined" if interaction.user.id == self.target.id else "cancelled"
        await interaction.response.edit_message(content=f"❌ Trade {who}.", embed=None, view=None)


# ── The cog ──────────────────────────────────────────────────────────────────

# Custom emojis for the reward messages (developer-portal emojis).
REWARD_EMOJI = {
    "daily":   "<:Dailypack:1558139740786528368>",
    "weekly":  "<:Weeklypack:1558138384675905536>",
    "monthly": "<:Monthlypack:1558138440208486440>",
}


class EconomyCog(commands.Cog, name="Economy"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- purse -----------------------------------------------------------
    @commands.command(name="rcbal")
    @economy_cooldown()
    async def rcbal(self, ctx: commands.Context):
        """Show your coins and vouchers."""
        if not await _need_debut(ctx):
            return
        coins = economy.get_balance(ctx.author.id)
        vouchers = economy.get_vouchers(ctx.author.id)
        await ctx.send(
            f"Purse: **{economy.fmt_coins(coins)}**\n"
            f"{emojis.VOUCHER} Vouchers: **{vouchers:,}**"
        )

    # -- rewards ---------------------------------------------------------
    @exclusive
    async def _claim(self, ctx: commands.Context, kind: str):
        if not await _need_debut(ctx):
            return
        if kind == "weekly" and not _is_premium(ctx.author):
            await ctx.send("💎 The weekly reward is for **Premium members** only.")
            return
        lo, hi = economy.REWARD_CARD_OVR[kind]
        pool = [c for c in card_db.list_obtainable_cards() if lo <= int(c["ovr"]) <= hi]
        res = economy.claim_reward(
            ctx.author.id, kind,
            [c["playername_key"] for c in pool],
            {c["playername_key"]: sl.sell_value(c) for c in pool},
        )
        if not res["ok"]:
            if res.get("locked"):
                await ctx.send(
                    f"🔒 The monthly reward unlocks **1 month after your debut**. "
                    f"Come back in **{fmt_wait(res['wait'])}**."
                )
                return
            await ctx.send(f"⏳ You already claimed your {kind} reward. Come back in **{fmt_wait(res['wait'])}**.")
            return
        lines = [f"{REWARD_EMOJI.get(kind, '🎁')} {kind.title()} reward: **+{economy.fmt_coins(res['coins'])}**"]
        if kind == "weekly" and res.get("vouchers"):
            lines.append(f"{emojis.VOUCHER} Weekly vouchers: **+{res['vouchers']}**")
        if kind == "daily":
            streak = res["streak"]
            if res["bonus"]:
                lines.append(
                    f"🎉 Streak bonus: **+{economy.fmt_coins(res['bonus'])}** "
                    f"(daily {economy.fmt_coins(res['base'])} + streak {economy.fmt_coins(res['bonus'])})"
                )
            if res.get("vouchers"):
                lines.append(f"{emojis.VOUCHER} {streak}-day streak reward: **+{res['vouchers']} vouchers**")
            nxt = economy.STREAK_EVERY - streak % economy.STREAK_EVERY
            lines.append(
                f"<:Streak:1558139736617390292> Streak: **{streak}** day(s) — next {economy.STREAK_VOUCHERS} vouchers in **{nxt}** day(s). "
                f"Miss a day and it restarts from 1!"
            )
        if res["card"]:
            card = card_db.get_card(res["card"])
            if res["dupe"]:
                lines.append(f"🎴 {_card_line(card, '  ♻️ duplicate')} → **+{economy.fmt_coins(res['refund'])}**")
            elif res.get("pending"):
                lines.append(f"🎴 {_card_line(card, '  ⚠️ squad full')} — choose below: sell it or replace a player.")
            else:
                lines.append(f"🎴 New player: {_card_line(card, '  🆕')}")
        else:
            lines.append(f"🎴 No player cards with rating {lo}-{hi} are available yet.")
        lines.append(f"Balance: **{economy.fmt_coins(res['balance'])}**")
        text = "\n".join(lines)
        shown = card_db.get_card(res["card"]) if res["card"] else None
        if shown is not None and shown["image_path"]:
            # Reward text + the card image in ONE message (daily, weekly and monthly).
            embed = discord.Embed(
                description=text,
                color={"daily": discord.Color.blue(), "weekly": discord.Color.purple(),
                       "monthly": discord.Color.gold()}.get(kind, discord.Color.blue()),
            )
            await card_cache.send_embed(ctx, embed, shown["image_path"])
        else:
            await ctx.send(text)
        if res.get("pending"):
            await send_next_pending(ctx, ctx.author.id)
        if kind == "daily":
            await quest.track_and_notify(ctx, ctx.author.id, {"daily_claim": 1})

    @commands.command(name="rcdaily")
    @economy_cooldown()
    async def rcdaily(self, ctx: commands.Context):
        """Claim daily coins + a random 60-78 player (streak bonus every 7th day)."""
        await self._claim(ctx, "daily")

    @commands.command(name="rcweekly")
    @economy_cooldown()
    async def rcweekly(self, ctx: commands.Context):
        """(Premium) Claim weekly coins + a random 80-83 player."""
        await self._claim(ctx, "weekly")

    @commands.command(name="rcmonthly")
    @economy_cooldown()
    async def rcmonthly(self, ctx: commands.Context):
        """Claim monthly coins + a random 85-88 player."""
        await self._claim(ctx, "monthly")

    # -- rcclaim: free card every hour (Premium: every 35 min) -------------
    @commands.command(name="rcclaim")
    @economy_cooldown()
    @exclusive
    async def rcclaim(self, ctx: commands.Context):
        """Claim a free random player card every hour (Premium: every 35 min)."""
        if not await _need_debut(ctx):
            return
        premium = _is_premium(ctx.author)
        cooldown = economy.CLAIM_COOLDOWN_PREMIUM if premium else economy.CLAIM_COOLDOWN
        by_ovr: dict[int, list[str]] = {}
        values: dict[str, int] = {}
        for c in card_db.list_obtainable_cards():
            by_ovr.setdefault(int(c["ovr"]), []).append(c["playername_key"])
            values[c["playername_key"]] = sl.sell_value(c)
        res = await asyncio.to_thread(economy.claim_card, ctx.author.id, cooldown, by_ovr, values)
        if not res["ok"]:
            if res.get("empty"):
                await ctx.send("🎴 No player cards are available to claim yet. Try again later.")
                return
            msg = f"⏳ Your next card is ready in **{fmt_wait(res['wait'])}**."
            if not premium:
                msg += "\n💎 Premium members can claim every **35 min**."
            await ctx.send(msg)
            return

        card = card_db.get_card(res["card"])
        ovr = int(card["ovr"])
        if res["dupe"]:
            body = f"{_card_line(card, '  ♻️ duplicate')}\n💰 Sold automatically for **+{economy.fmt_coins(res['refund'])}**"
        elif res["pending"]:
            body = f"{_card_line(card, '  ⚠️ squad full')}\nChoose below: sell it or replace a player."
        else:
            body = _card_line(card, "  🆕")
        if ovr >= 84:
            title, color = "🌟 Rare Claim!", discord.Color.purple()
        elif ovr >= 76:
            title, color = "🎴 Hourly Claim", discord.Color.blue()
        else:
            title, color = "🎴 Hourly Claim", discord.Color.light_grey()
        embed = discord.Embed(title=title, description=body, color=color)
        embed.set_footer(text=f"Next claim in {fmt_wait(cooldown)}" + (" • 💎 Premium" if premium else ""))
        await card_cache.send_embed(ctx, embed, card["image_path"])   # card image comes with the message
        if res["pending"]:
            await send_next_pending(ctx, ctx.author.id)

    # -- rccd: when is every claim command ready again? --------------------
    @commands.command(name="rccd")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rccd(self, ctx: commands.Context):
        """Show when rcdaily/rcweekly/rcmonthly/rcclaim are next ready."""
        if not await _need_debut(ctx):
            return
        premium = _is_premium(ctx.author)
        status = await asyncio.to_thread(economy.reward_cooldowns, ctx.author.id, premium)
        if status is None:
            await ctx.send("❌ You haven't debuted yet. Use `rcdebut`.")
            return

        def short(sec: int) -> str:
            d, rem = divmod(int(sec), 86400)
            h, rem = divmod(rem, 3600)
            m, s_ = divmod(rem, 60)
            if d:
                return f"{d}d {h}h"
            if h:
                return f"{h}h {m}m"
            if m:
                return f"{m}m {s_}s"
            return f"{s_}s"

        def row(label: str, cmd: str, value: str) -> str:
            return f"**{label}** `{cmd}`\n> `{value}`"

        def state(wait: int) -> str:
            return "Ready" if wait <= 0 else short(wait)

        rows = [row("Daily", "rcdaily", state(status["daily"]))]
        rows.append(row("Weekly", "rcweekly", state(status["weekly"]) if premium else "Premium only"))
        if status["monthly_locked"] > 0:
            rows.append(row("Monthly", "rcmonthly", f"Unlocks in {short(status['monthly_locked'])}"))
        else:
            rows.append(row("Monthly", "rcmonthly", state(status["monthly"])))
        rows.append(row("Claim", "rcclaim", state(status["claim"])))

        embed = discord.Embed(title="Cooldowns", description="\n\n".join(rows), color=discord.Color.teal())
        embed.set_footer(text=f"Requested by: {ctx.author.name}")
        await ctx.send(embed=embed)

    # -- packs -----------------------------------------------------------
    @commands.command(name="rcpack")
    @economy_cooldown()
    @commands.cooldown(1, CONFIRM_COOLDOWN, commands.BucketType.user)
    async def rcpack(self, ctx: commands.Context, pack: Optional[str] = None):
        """See the pack shop (pick a pack from the menu), or buy one directly: `rcpack bronze`."""
        if not await _need_debut(ctx):
            return
        if pack is None:
            if not _shop_packs():
                await ctx.send("No packs are on sale right now.")
                return
            view = PackShopView(ctx.author.id)
            view.message = await ctx.send(embed=_shop_embed(ctx.author.id), view=view)
            return
        key = security.clean_input(pack, 12).lower()
        if key not in sl.PACKS or sl.PACKS[key].get("shop") is False or not sl.pack_available(key):
            await ctx.send("❌ Pick " + ", ".join(f"`{k}`" for k, _ in _shop_packs()) + ".")
            return
        spec = sl.PACKS[key]
        uid = ctx.author.id

        if uid in _pending_confirm:
            await ctx.send("❌ You already have a confirmation waiting — finish that first.")
            return

        _pending_confirm.add(uid)
        try:
            off = economy.is_pack_off(key)
            view = ConfirmView(uid, _buy_action(ctx.channel, uid, key), locked=off)
            text = f"Buy 1× {spec['emoji']} **{spec['label']}** for **{economy.fmt_coins(spec['price'])}**?"
            if off:
                text += "\n🔒 This pack isn't available right now."
            view.message = await ctx.send(text, view=view)
        except Exception:
            _pending_confirm.discard(uid)
            raise

    @commands.command(name="rcopen")
    @economy_cooldown()
    @commands.cooldown(1, CONFIRM_COOLDOWN, commands.BucketType.user)
    @exclusive
    async def rcopen(self, ctx: commands.Context, pack: Optional[str] = None):
        """Open one of your packs: `rcopen bronze`. A short animation plays, then your real card appears."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't open packs during a match.")
            return
        have = economy.get_packs(ctx.author.id)
        have = {k: v for k, v in have.items() if k in sl.PACKS}
        if not have:
            await ctx.send("You have no packs. Buy them with `rcpack`.")
            return
        if pack is None:
            if len(have) == 1:
                pack = next(iter(have))
            else:
                await ctx.send("You have: " + ", ".join(f"{n}× {k}" for k, n in have.items()) + ". Use `rcopen <type>`.")
                return
        key = security.clean_input(pack, 12).lower()
        if key not in have:
            await ctx.send("❌ You don't have that pack.")
            return
        if key == "debut" and (ctx.guild is None or ctx.guild.id != DEBUT_PACK_GUILD_ID):
            await ctx.send("🔒 The `debut pack` can only be opened in the main server.")
            return
        spec = sl.PACKS[key]
        rolled = sl.roll_pack(key)
        if not rolled:
            if spec.get("event"):
                await ctx.send(f"⚠️ The {spec['label']} has no player cards right now (its event is switched off). "
                               "Your pack is kept — open it when the event is back.")
            else:
                await ctx.send("⚠️ There are no player cards in the game yet, so packs can't open. Ask the owner to add cards.")
            return
        # The pack is used up and the card handed over in ONE database transaction (as before).
        result = economy.open_pack(
            ctx.author.id, key,
            [c["playername_key"] for c in rolled],
            {c["playername_key"]: sl.sell_value(c) for c in rolled},
        )
        if isinstance(result, str):
            await ctx.send(f"❌ {result}")
            return
        by_key = {c["playername_key"]: c for c in rolled}
        lines = [_card_line(by_key[k], "  🆕") for k in result["new"]]
        lines += [_card_line(by_key[k], "  ⚠️ squad full") for k in result.get("pending", [])]
        lines += [_card_line(by_key[k], "  ♻️ duplicate") for k in result["dupes"]]
        if key == "debut":
            embed = discord.Embed(description="`Debut Pack` opened!\n\n" + "\n".join(lines), color=discord.Color.gold())
        else:
            embed = discord.Embed(title=f"{spec['emoji']} {spec['label']} opened!", description="\n".join(lines), color=discord.Color.gold())
        if result["refund"]:
            embed.set_footer(text=f"Duplicates gave you back {result['refund']:,} coins")

        # The REAL card image (the same one rcview / rcbuy show) is prepared BEFORE the animation
        # starts, so it can appear the moment the animation ends.
        path = rolled[0]["image_path"]
        has_img = bool(path) and Path(path).exists()
        card_url = card_cache.cached_url(path) if has_img else None
        card_file = None if (card_url or not has_img) else await card_cache.card_file(path)
        if card_url:
            embed.set_image(url=card_url)
        elif card_file is not None:
            embed.set_image(url=f"attachment://{card_file.filename}")

        # 1) the opening animation (nothing about the card is shown yet)
        msg = None
        intro, wait = await _pack_intro(key)
        if intro is not None:
            intro_embed = discord.Embed(title=f"{spec['emoji']} Opening {spec['label']}…", color=discord.Color.gold())
            intro_embed.set_image(url=f"attachment://{intro.filename}")
            try:
                msg = await ctx.send(embed=intro_embed, file=intro)
                await asyncio.sleep(wait)
            except discord.HTTPException as e:
                print(f"[rcopen] animation step failed: {e}")

        # 2) the real card replaces the animation in the same message
        shown = None
        if msg is not None:
            try:
                shown = await msg.edit(embed=embed, attachments=[card_file] if card_file is not None else [])
            except discord.HTTPException as e:       # e.g. the message was deleted meanwhile
                print(f"[rcopen] reveal edit failed: {e}")
                if card_file is not None:            # a discord.File can only be sent once
                    card_file = await card_cache.card_file(path)
        if shown is None:
            if card_file is not None:
                shown = await ctx.send(embed=embed, file=card_file)
            else:
                shown = await ctx.send(embed=embed)
        if card_file is not None:
            card_cache.remember(path, shown)

        if result.get("pending"):
            await send_next_pending(ctx, ctx.author.id)
        await quest.track_and_notify(ctx, ctx.author.id, {"packs_opened": 1})

    # -- buy / sell ------------------------------------------------------
    @commands.command(name="rcbuy")
    @economy_gate(BUYSELL_COOLDOWN)
    async def rcbuy(self, ctx: commands.Context, *, player: str):
        """Buy a player: `rcbuy Virat Kohli`."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("You can't buy players during a match.")
            return
        text = security.clean_input(player)
        cards = await asyncio.to_thread(find_buy_cards, text)
        if not cards:
            await ctx.send(f"No player found matching `{sl.discord_safe(text)}`.")
            return
        # Buy / owned / squad-full checks happen when ✅ is pressed, for the card on screen.
        await _send_buy_browser(ctx, cards)

    @commands.command(name="rcsell")
    @economy_gate(BUYSELL_COOLDOWN)
    async def rcsell(self, ctx: commands.Context, *, player: str):
        """Sell one of your players for coins."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("You can't sell players during a match.")
            return
        card, err = await asyncio.to_thread(sl.resolve_owned, ctx.author.id, security.clean_input(player))
        if card is None:
            await ctx.send(str(err))
            return
        uid, key = ctx.author.id, card["playername_key"]
        value = sl.sell_value(card)

        async def do_sell() -> str:
            # If this player is in the XI, the best bench player takes their place.
            xi_keys = set(economy.get_xi(uid).values())
            replacement = None
            if key in xi_keys:
                bench = [c for c in sl.owned_cards(uid) if c["playername_key"] not in xi_keys]
                if bench:
                    replacement = max(bench, key=lambda c: c["ovr"])["playername_key"]
            e = economy.sell_card(uid, key, value, replacement)
            return str(e) if e else f"Sold {esc(card['playername'])} for {_plain_coins(value)}."

        await _send_buysell(
            ctx, card,
            f"Sell {esc(card['playername'])} ({card['ovr']} OVR) for {_plain_coins(value)}?",
            do_sell,
        )

    @commands.command(name="rcms", cooldown_after_parsing=True)
    @economy_gate(ECONOMY_COOLDOWN)
    @commands.cooldown(1, MULTISELL_COOLDOWN, commands.BucketType.user)
    async def rcms(self, ctx: commands.Context, start: int, end: Optional[int] = None):
        """Multi sell by squad number: `rcms 1 20` sells players 1 to 20 of `rcsquad`."""
        if not await _need_debut(ctx):
            return
        uid = ctx.author.id

        async def refuse(text: str):
            self.rcms.reset_cooldown(ctx)   # a refused command shouldn't cost the 15 seconds
            await ctx.send(text)

        if _in_match(self.bot, uid):
            await refuse("You can't sell players during a match.")
            return
        if end is None:
            end = start
        if start < 1 or end < start:
            await refuse("Usage: `rcms <from> <to>`, for example `rcms 1 20`. Numbers are the ones shown in `rcsquad`.")
            return
        cards = squad_order(await asyncio.to_thread(sl.owned_cards, uid))
        n = len(cards)
        if end > n:
            await refuse(f"Your squad has only {n} players. Check the numbers in `rcsquad`.")
            return
        chosen = cards[start - 1:end]
        if n - len(chosen) < economy.MIN_SQUAD:
            can = max(0, n - economy.MIN_SQUAD)
            await refuse(f"You must keep at least {economy.MIN_SQUAD} players. Right now you can sell at most {can}.")
            return
        if uid in _pending_confirm:
            await refuse("You already have a confirmation waiting. Finish that first.")
            return

        items = [(c["playername_key"], sl.sell_value(c)) for c in chosen]
        total = sum(v for _, v in items)
        chosen_keys = {k for k, _ in items}
        bench_order = [c["playername_key"] for c in cards if c["playername_key"] not in chosen_keys]
        xi_keys = set(economy.get_xi(uid).values())
        in_xi = sum(1 for k in chosen_keys if k in xi_keys)

        lines = [
            f"{start + i}. {esc(c['playername'])} ({c['ovr']} OVR) - {_plain_coins(v)}"
            for i, (c, (_, v)) in enumerate(zip(chosen, items))
        ]
        text = (
            "Are you sure you want to sell these cards?\n\n"
            + "\n".join(lines)
            + f"\n\nPlayers: {len(items)}\nTotal coins: {_plain_coins(total)}"
            + f"\nYour coins now: {_plain_coins(economy.get_balance(uid))}"
        )
        if in_xi:
            text += f"\n\n{in_xi} of these players are in your XI. The best bench players will take their place."
        embed = discord.Embed(description=text[:4000], color=discord.Color.gold())
        embed.set_footer(text=f"Press Sell within {int(MULTISELL_TIMEOUT)} seconds, or it is cancelled.")

        _pending_confirm.add(uid)
        try:
            view = MultiSellView(self.bot, uid, items, bench_order)
            view.message = await ctx.send(embed=embed, view=view)
        except Exception:
            _pending_confirm.discard(uid)
            raise

    # -- cards waiting because the squad was full ---------------------------
    @commands.command(name="rcpending")
    @economy_cooldown()
    async def rcpending(self, ctx: commands.Context):
        """Cards that arrived while your squad was full (25): sell them or replace a player."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't change your squad during a match.")
            return
        if not await send_next_pending(ctx, ctx.author.id):
            await ctx.send(f"✅ No cards are waiting. Squad: **{economy.squad_count(ctx.author.id)}/{economy.SQUAD_MAX}**.")

    # -- trade -----------------------------------------------------------
    @commands.command(name="rctrade")
    @economy_cooldown()
    @commands.cooldown(1, CONFIRM_COOLDOWN, commands.BucketType.user)
    async def rctrade(self, ctx: commands.Context, member: discord.Member, *, players: str):
        """Trade players of the SAME OVR (5,000 coins fee each): `rctrade @user my player | their player`."""
        if not await _need_debut(ctx):
            return
        if member.bot or member.id == ctx.author.id:
            await ctx.send("❌ Pick another real player to trade with.")
            return
        if security.is_banned(member.id):
            await ctx.send("❌ You can't trade with that user.")
            return
        if not economy.user_exists(member.id):
            await ctx.send(f"❌ {esc(member.display_name)} hasn't debuted yet.")
            return
        if ctx.author.id in _pending_trade or member.id in _pending_trade:
            await ctx.send("❌ One of you already has a trade waiting. Finish that first.")
            return
        if _in_match(self.bot, ctx.author.id) or _in_match(self.bot, member.id):
            await ctx.send("❌ You can't trade while someone is in a match.")
            return
        players = security.clean_input(players, 120)
        if players.count("|") != 1:
            await ctx.send("Usage: `rctrade @user Your Player | Their Player`")
            return
        mine_txt, theirs_txt = (p.strip() for p in players.split("|"))
        mine, err = sl.resolve_owned(ctx.author.id, mine_txt)
        if mine is None:
            await ctx.send(f"❌ {err}")
            return
        theirs, err = sl.resolve_owned(member.id, theirs_txt)
        if theirs is None:
            await ctx.send(f"❌ {esc(member.display_name)} doesn't own a player like that.")
            return
        if economy.owns(member.id, mine["playername_key"]) or economy.owns(ctx.author.id, theirs["playername_key"]):
            await ctx.send("❌ One of you already owns the player you'd receive.")
            return
        if int(mine["ovr"]) != int(theirs["ovr"]):
            await ctx.send(
                f"❌ Trades are only allowed between players with the **same OVR**.\n"
                f"{esc(mine['playername'])} is **{mine['ovr']}** OVR, "
                f"{esc(theirs['playername'])} is **{theirs['ovr']}** OVR."
            )
            return
        fee = economy.TRADE_FEE
        for who in (ctx.author, member):
            if economy.get_balance(who.id) < fee:
                await ctx.send(f"❌ {esc(who.display_name)} needs **{fee:,} coins** for the trade fee (every trade costs {fee:,} coins each).")
                return
        _pending_trade.update({ctx.author.id, member.id})
        view = TradeView(ctx.author, member, mine["playername_key"], theirs["playername_key"])
        embed = discord.Embed(
            title="🤝 Trade offer",
            description=(
                f"**{esc(ctx.author.display_name)}** gives:\n{_card_line(mine)}\n\n"
                f"**{esc(member.display_name)}** gives:\n{_card_line(theirs)}"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Same OVR ✔ · Fee: {fee:,} coins each (paid when accepted) · {member.display_name}: press Accept within 60 seconds")
        view.message = await ctx.send(content=member.mention, embed=embed, view=view)

    # -- leaderboard -----------------------------------------------------
    @commands.command(name="rcleaderboard", aliases=["rcleaderboards", "rclb"])
    @commands.cooldown(1, 8, commands.BucketType.user)
    async def rcleaderboard(self, ctx: commands.Context, board: str = "ovr"):
        """Top teams: `rcleaderboard ovr` or `rcleaderboard coins`."""
        board = security.clean_input(board, 10).lower()
        if board not in ("ovr", "coins"):
            await ctx.send("Use `rcleaderboard ovr` or `rcleaderboard coins`.")
            return
        users = [u for u in economy.list_users() if not security.is_banned(u["user_id"])]
        rows = []
        if board == "coins":
            for u in sorted(users, key=lambda u: -u["purse"])[:10]:
                rows.append((u["team_name"], economy.fmt_coins(u["purse"])))
            title = "<:CSCoin:1558139683702055062> Richest teams"
        else:
            scored = []
            for u in users:
                cards = sl.xi_cards(u["user_id"])
                if len(cards) == economy.XI_SIZE:
                    scored.append((sl.average_ovr(cards), u["team_name"]))
            for ovr, name in sorted(scored, reverse=True)[:10]:
                rows.append((name, f"OVR {ovr}"))
            title = "<:OVR:1558139691482751066> Strongest XIs"
        if not rows:
            await ctx.send("No teams on the leaderboard yet.")
            return
        lines = [f"`{i + 1}.` **{esc(n)}** — {v}" for i, (n, v) in enumerate(rows)]
        await ctx.send(embed=discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold()))
        await quest.track_and_notify(ctx, ctx.author.id, {"lb_view": 1})


async def setup(bot: commands.Bot):
    await bot.add_cog(EconomyCog(bot))
