"""Draft (voucher) system.

Players
    csdraft                      open the Draft screen (Pool A / B / C, 1 Draft / 10 Drafts)
    csbal                        (in economy_cog) shows coins + vouchers

Owner / admin  (text commands, main server only)
    /pool add <A|B> <player> <chance>   put a card in Pool A or B with its chance (slash command)
    /pool remove <player>               take a card out of its pool
    /pool list <A|B>                    see a pool with the real % of each card
    csvoucher check @user        see someone's vouchers + draft counter   (also: voucher check)
    csvoucher give @user <n>     give vouchers
    csvoucher remove @user <n>   take vouchers (stops at 0)

Safety
- The whole draft (take vouchers, roll cards, give cards, move the Pool A
  counter) is ONE database transaction in economy.draft_spin: you can never
  get cards without paying, or pay without getting cards.
- Rolls use the OS random source (secrets), not the normal predictable one.
- One draft per user at a time + a short gap between drafts, so button spam
  can't double-run anything.
- Only the person the screen was made for can press its buttons.
- Voucher give/remove: owner / full-admin role only, main server only,
  1..VOUCHER_ADMIN_MAX per command, every change is logged in voucher_log.
- Drafts are blocked during a live match (squads are locked).
"""
from __future__ import annotations

import asyncio
import io
import secrets
import time
from pathlib import Path

import discord
from discord import app_commands, ui
from discord.ext import commands

import card_db
import draft_config as cfg
import draft_image
import economy
import emojis
import security
import squad_logic as sl
from bot_guard import ADMIN_GUILD_ID, is_staff
from economy_cog import economy_cooldown, send_next_pending
from security import OwnedView, esc
from squad_cog import _card_line, _in_match, _need_debut

_HERE = Path(__file__).parent
_rng = secrets.SystemRandom()
POOLS = ("A", "B", "C")
MIN_GAP = 2.0                                   # seconds between two drafts of one user

_busy: set[int] = set()                         # users with a draft running right now
_last_draft: dict[int, float] = {}


# ── Pool contents (cached for a minute) ──────────────────────────────────────
# Pool A / B cards and their chances live in the database (managed with the
# `/pool` slash commands). Pool C = every card in the OVR range of draft_config.

_pool_cache: dict = {"t": 0.0, "data": None, "weights": {}}


def get_pools(force: bool = False) -> dict[str, list]:
    """{"A": [rows], "B": [rows], "C": [rows]}. Cached for 60 s (force=True refreshes)."""
    now = time.time()
    if not force and _pool_cache["data"] is not None and now - _pool_cache["t"] < 60:
        return _pool_cache["data"]
    saved = economy.pool_cards()
    by_key = card_db.get_cards([k for _, k, _ in saved]) if saved else {}
    pools: dict[str, list] = {"A": [], "B": [], "C": []}
    weights: dict[str, float] = {}
    for pool, key, weight in saved:
        row = by_key.get(key)
        if row is None:
            continue                                  # card was deleted from the game
        pools[pool].append(row)
        weights[key] = weight
    fixed = set(weights)
    pools["C"] = [
        x for x in card_db.list_all_cards()
        if cfg.POOL_C_OVR_MIN <= int(x["ovr"]) <= cfg.POOL_C_OVR_MAX and x["playername_key"] not in fixed
    ]
    pools["A"].sort(key=lambda x: -int(x["ovr"]))
    pools["B"].sort(key=lambda x: -int(x["ovr"]))
    _pool_cache.update(t=now, data=pools, weights=weights)
    return pools


def get_weights() -> dict[str, float]:
    return dict(_pool_cache.get("weights") or {})


def shares_for(pools: dict[str, list], pool: str) -> dict[str, float]:
    """{player_key: % chance inside this pool} (the pool's weights scaled to 100)."""
    if pool not in ("A", "B"):
        return {}
    w = _pool_cache.get("weights") or {}
    rows = pools.get(pool, [])
    total = sum(w.get(r["playername_key"], 0) for r in rows)
    if total <= 0:
        return {}
    return {r["playername_key"]: round(100 * w.get(r["playername_key"], 0) / total, 1) for r in rows}


def _make_roll(pools: dict[str, list], weights: dict[str, float]):
    avail = {p: rows for p, rows in pools.items() if rows}

    def roll(force_a: bool):
        if not avail:
            return None
        if force_a and "A" in avail:
            pool = "A"
        else:
            names = list(avail)
            w = [max(0, float(cfg.ODDS.get(p, 0))) for p in names]
            if sum(w) <= 0:
                w = [1.0] * len(names)
            pool = _rng.choices(names, weights=w, k=1)[0]
        rows = avail[pool]
        if pool in ("A", "B"):                       # fixed pools: each card has its own chance
            cw = [max(0.0, float(weights.get(r["playername_key"], 1.0))) for r in rows]
            if sum(cw) <= 0:
                cw = [1.0] * len(rows)
            card = _rng.choices(rows, weights=cw, k=1)[0]
        else:
            card = _rng.choice(rows)
        return pool, card["playername_key"]

    return roll


# ── The draft screen ─────────────────────────────────────────────────────────

def _screen_embed(pool: str) -> discord.Embed:
    embed = discord.Embed(color=draft_color(pool))
    embed.set_image(url=f"attachment://{draft_image.FILENAME}")
    embed.set_footer(
        text=f"Pool A {cfg.ODDS['A']}% · Pool B {cfg.ODDS['B']}% · Pool C {cfg.ODDS['C']}%  |  "
             f"Pool A is guaranteed on every {cfg.PITY_A_EVERY}th draft"
    )
    return embed


def draft_color(pool: str) -> discord.Color:
    return discord.Color.from_rgb(*draft_image.POOL_COLORS.get(pool, (70, 140, 230)))


async def _render_screen(uid: int, pool: str, page: int) -> tuple[discord.Embed, discord.File, int]:
    pools = await asyncio.to_thread(get_pools)
    vouchers = await asyncio.to_thread(economy.get_vouchers, uid)
    since = await asyncio.to_thread(economy.get_draft_pity, uid)
    cards = pools.get(pool, []) if pool != "C" else []
    total_pages = draft_image.pages_for(len(cards)) if pool != "C" else 1
    page = max(0, min(page, total_pages - 1))
    buf = await asyncio.to_thread(
        draft_image.build_draft_screen,
        pool, page, cards, vouchers, since, cfg.PITY_A_EVERY, dict(cfg.ODDS),
        (cfg.POOL_C_OVR_MIN, cfg.POOL_C_OVR_MAX),
        cfg.VOUCHERS_PER_DRAFT, cfg.VOUCHERS_PER_DRAFT * cfg.DRAFT_MULTI, cfg.DRAFT_MULTI,
        shares_for(pools, pool),
    )
    return _screen_embed(pool), discord.File(buf, filename=draft_image.FILENAME), page


class DraftView(OwnedView):
    def __init__(self, bot: commands.Bot, user_id: int):
        super().__init__(user_id, timeout=180.0)
        self.bot = bot
        self.uid = user_id
        self.pool = "A"
        self.page = 0
        self._build()

    # buttons are rebuilt so the selected pool is highlighted
    def _build(self):
        self.clear_items()
        for p in POOLS:
            b = ui.Button(
                label=f"Pool {p}", row=0,
                style=discord.ButtonStyle.primary if p == self.pool else discord.ButtonStyle.secondary,
            )
            b.callback = self._make_pool_cb(p)
            self.add_item(b)
        prev_b = ui.Button(emoji="◀", row=1, style=discord.ButtonStyle.secondary)
        next_b = ui.Button(emoji="▶", row=1, style=discord.ButtonStyle.secondary)
        prev_b.callback = self._page_cb(-1)
        next_b.callback = self._page_cb(+1)
        self.add_item(prev_b)
        self.add_item(next_b)
        one = ui.Button(
            label="1 Draft", emoji=emojis.VOUCHER, row=2, style=discord.ButtonStyle.success,
        )
        many = ui.Button(
            label=f"{cfg.DRAFT_MULTI} Drafts", emoji=emojis.VOUCHER, row=2, style=discord.ButtonStyle.success,
        )
        one.callback = self._draft_cb(1)
        many.callback = self._draft_cb(cfg.DRAFT_MULTI)
        self.add_item(one)
        self.add_item(many)

    def _make_pool_cb(self, pool: str):
        async def cb(interaction: discord.Interaction):
            self.pool, self.page = pool, 0
            await self._refresh(interaction)
        return cb

    def _page_cb(self, step: int):
        async def cb(interaction: discord.Interaction):
            self.page += step
            await self._refresh(interaction)
        return cb

    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, file, self.page = await _render_screen(self.uid, self.pool, self.page)
        self._build()
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    def _draft_cb(self, count: int):
        async def cb(interaction: discord.Interaction):
            await self._run_draft(interaction, count)
        return cb

    # ── the actual draft ─────────────────────────────────────────────────
    async def _run_draft(self, interaction: discord.Interaction, count: int):
        uid = self.uid
        if _in_match(self.bot, uid):
            await interaction.response.send_message("❌ You can't draft during a match.", ephemeral=True)
            return
        now = time.monotonic()
        if uid in _busy or now - _last_draft.get(uid, 0.0) < MIN_GAP:
            await interaction.response.defer()          # ignore spam clicks silently
            return
        _busy.add(uid)
        try:
            await interaction.response.defer()
            pools = await asyncio.to_thread(get_pools)
            if not any(pools.values()):
                await interaction.followup.send("⚠️ No cards are available for the draft yet. Ask the owner.", ephemeral=True)
                return
            by_key = {c["playername_key"]: c for rows in pools.values() for c in rows}
            try:
                res = await asyncio.to_thread(
                    economy.draft_spin, uid, count, cfg.VOUCHERS_PER_DRAFT, cfg.PITY_A_EVERY,
                    _make_roll(pools, get_weights()), lambda k: sl.sell_value(by_key[k]) if k in by_key else 0,
                )
            except Exception as e:                      # transaction rolled back: nothing was charged
                print(f"[draft] spin failed for {uid}: {e}")
                await interaction.followup.send("⚠️ The draft failed. Nothing was charged. Try again.", ephemeral=True)
                return
            if isinstance(res, str):
                await interaction.followup.send(f"❌ {res}", ephemeral=True)
                return
            _last_draft[uid] = time.monotonic()
            await self._show_result(interaction, res, by_key, count)
        finally:
            _busy.discard(uid)

    async def _show_result(self, interaction: discord.Interaction, res: dict, by_key: dict, count: int):
        rows = res["results"]
        items = [{"card": by_key.get(r["key"]) or card_db.get_card(r["key"]), "pool": r["pool"], "status": r["status"]} for r in rows]
        lines = []
        for r, it in zip(rows, items):
            if it["card"] is None:
                continue
            tag = {"added": "  🆕", "pending": "  ⚠️ squad full", "dupe": "  ♻️ duplicate"}[r["status"]]
            lines.append(f"**Pool {r['pool']}** · {_card_line(it['card'], tag)}")
        embed = discord.Embed(
            title=f"{emojis.VOUCHER} Draft ×{count}",
            description="\n".join(lines)[:4000],
            color=draft_color("A" if any(r["pool"] == "A" for r in rows) else "C"),
        )
        foot = f"Vouchers left: {res['vouchers']:,}"
        if res["refund"]:
            foot += f" · Duplicates gave back {res['refund']:,} coins"
        embed.set_footer(text=foot)
        embed.set_image(url="attachment://draft.jpg")

        buf = await asyncio.to_thread(draft_image.build_result_image, items)
        result_file = discord.File(buf, filename="draft.jpg")

        got_a = any(r["pool"] == "A" for r in rows)
        anim = _HERE / cfg.POOL_A_ANIMATION_FILE
        msg = None
        if got_a and anim.exists():
            try:
                ext = anim.suffix.lower() or ".gif"
                anim_embed = discord.Embed(color=draft_color("A"))
                anim_embed.set_image(url=f"attachment://pool_a{ext}")
                msg = await interaction.followup.send(
                    embed=anim_embed, file=discord.File(str(anim), filename=f"pool_a{ext}"), wait=True,
                )
                await asyncio.sleep(max(1, cfg.POOL_A_ANIMATION_SECONDS))
                await msg.edit(embed=embed, attachments=[result_file])
            except discord.HTTPException as e:
                print(f"[draft] animation step failed: {e}")
                msg = None
        if msg is None:
            if got_a and anim.exists():                     # animation failed: rebuild the file, send plain
                result_file = discord.File(io.BytesIO(buf.getvalue()), filename="draft.jpg")
            await interaction.followup.send(embed=embed, file=result_file)

        # refresh the draft screen (new voucher count / counter)
        try:
            screen_embed, screen_file, self.page = await _render_screen(self.uid, self.pool, self.page)
            await interaction.edit_original_response(embed=screen_embed, attachments=[screen_file], view=self)
        except discord.HTTPException:
            pass
        if any(r["status"] == "pending" for r in rows):
            await send_next_pending(interaction.channel, self.uid)


# ── Cog ──────────────────────────────────────────────────────────────────────

def _admin_guild_only(ctx: commands.Context) -> bool:
    return ctx.guild is not None and ctx.guild.id == ADMIN_GUILD_ID


async def _card_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    names = [n for n in await asyncio.to_thread(card_db.list_playernames) if cur in n.lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


async def _pool_card_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    pools = await asyncio.to_thread(get_pools)
    names = [c["playername"] for p in ("A", "B") for c in pools[p] if cur in c["playername"].lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


class DraftCog(commands.Cog, name="Draft"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await asyncio.to_thread(economy.ensure_draft_tables)
        except Exception as e:
            print(f"[draft] could not create voucher tables: {e}")
        # warm the pool cache so the first csdraft is fast and typos show in the console
        try:
            await asyncio.to_thread(get_pools, True)
        except Exception as e:
            print(f"[draft] could not load pools: {e}")

    @commands.command(name="csdraft")
    @economy_cooldown()
    async def csdraft(self, ctx: commands.Context):
        """Open the Draft screen."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't draft during a match.")
            return
        view = DraftView(self.bot, ctx.author.id)
        embed, file, view.page = await _render_screen(ctx.author.id, view.pool, 0)
        view.message = await ctx.send(embed=embed, file=file, view=view)

    # -- owner / admin: vouchers -----------------------------------------
    @commands.group(name="csvoucher", aliases=["voucher"], invoke_without_command=True)
    @security.owner_or_admin_role()
    async def csvoucher(self, ctx: commands.Context):
        await ctx.send(
            "`csvoucher check @user`\n"
            "`csvoucher give @user <number>`\n"
            "`csvoucher remove @user <number>`"
        )

    @csvoucher.command(name="check")
    @security.owner_or_admin_role()
    async def voucher_check(self, ctx: commands.Context, member: discord.Member):
        if not _admin_guild_only(ctx):
            return
        if not economy.user_exists(member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        have = economy.get_vouchers(member.id)
        since = economy.get_draft_pity(member.id)
        left = max(1, cfg.PITY_A_EVERY - since)
        await ctx.send(
            f"{emojis.VOUCHER} **{esc(member.display_name)}** has **{have:,}** voucher(s).\n"
            f"Pool A guaranteed in **{left}** draft(s)."
        )

    @csvoucher.command(name="give")
    @security.owner_or_admin_role()
    async def voucher_give(self, ctx: commands.Context, member: discord.Member, amount: int):
        if not _admin_guild_only(ctx):
            return
        if not 1 <= amount <= cfg.VOUCHER_ADMIN_MAX:
            await ctx.send(f"❌ Give between 1 and {cfg.VOUCHER_ADMIN_MAX:,} vouchers at a time.")
            return
        new = economy.add_vouchers(member.id, amount, f"Admin give by {ctx.author.id}")
        if new is None:
            await ctx.send("❌ That user hasn't debuted.")
            return
        await ctx.send(f"✅ Gave **{amount:,}** {emojis.VOUCHER} to {esc(member.display_name)}. They now have **{new:,}**.")

    @csvoucher.command(name="remove")
    @security.owner_or_admin_role()
    async def voucher_remove(self, ctx: commands.Context, member: discord.Member, amount: int):
        if not _admin_guild_only(ctx):
            return
        if not 1 <= amount <= cfg.VOUCHER_ADMIN_MAX:
            await ctx.send(f"❌ Remove between 1 and {cfg.VOUCHER_ADMIN_MAX:,} vouchers at a time.")
            return
        out = economy.remove_vouchers(member.id, amount, f"Admin remove by {ctx.author.id}")
        if out is None:
            await ctx.send("❌ That user hasn't debuted.")
            return
        removed, new = out
        await ctx.send(f"✅ Removed **{removed:,}** {emojis.VOUCHER} from {esc(member.display_name)}. They now have **{new:,}**.")

    # -- owner / admin: pool cards + chances (SLASH commands, main server only) --
    pool = app_commands.Group(
        name="pool",
        description="Draft pools: add / remove cards and set their chance (owner & admin)",
        guild_ids=[ADMIN_GUILD_ID],                       # only exists in your server
        guild_only=True,
        default_permissions=discord.Permissions(),        # hidden from normal members
    )

    @staticmethod
    async def _staff_only(interaction: discord.Interaction) -> bool:
        if is_staff(interaction.user):
            return True
        await interaction.response.send_message("🔒 Only the bot owner or an admin can use this.", ephemeral=True)
        return False

    @pool.command(name="add", description="Put a card in Pool A or B with its chance (run again to change it)")
    @app_commands.describe(
        pool="Pool A or Pool B",
        player="Which player card",
        chance="Chance of this card inside the pool, e.g. 30 (cards in a pool share the pool)",
    )
    @app_commands.choices(pool=[app_commands.Choice(name="Pool A", value="A"), app_commands.Choice(name="Pool B", value="B")])
    @app_commands.autocomplete(player=_card_autocomplete)
    async def pool_add(self, interaction: discord.Interaction, pool: app_commands.Choice[str], player: str,
                       chance: app_commands.Range[float, 0.01, 100.0]):
        if not await self._staff_only(interaction):
            return
        card = card_db.get_card(player)
        if card is None:
            card, err = sl.resolve_any(security.clean_input(player, 80))
            if card is None:
                await interaction.response.send_message(f"❌ {err}", ephemeral=True)
                return
        await interaction.response.defer(ephemeral=True)
        letter = pool.value
        await asyncio.to_thread(economy.pool_set, letter, card["playername_key"], float(chance))
        pools = await asyncio.to_thread(get_pools, True)
        share = shares_for(pools, letter).get(card["playername_key"], 0)
        await interaction.followup.send(
            f"✅ **{esc(card['playername'])}** ({card['ovr']} OVR) is in **Pool {letter}** with chance **{chance:g}** "
            f"→ **{share:g}%** of Pool {letter} (Pool {letter} now has {len(pools[letter])} card(s)).",
            ephemeral=True,
        )

    @pool.command(name="remove", description="Take a card out of Pool A or B")
    @app_commands.describe(player="Which player card")
    @app_commands.autocomplete(player=_pool_card_autocomplete)
    async def pool_remove(self, interaction: discord.Interaction, player: str):
        if not await self._staff_only(interaction):
            return
        card = card_db.get_card(player)
        if card is None:
            await interaction.response.send_message("❌ Pick a player from the list.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        was = await asyncio.to_thread(economy.pool_remove, card["playername_key"])
        if was is None:
            await interaction.followup.send(f"❌ **{esc(card['playername'])}** isn't in Pool A or B.", ephemeral=True)
            return
        await asyncio.to_thread(get_pools, True)
        await interaction.followup.send(f"✅ Removed **{esc(card['playername'])}** from Pool {was}.", ephemeral=True)

    @pool.command(name="list", description="Show the cards of Pool A or B with their real chance %")
    @app_commands.describe(pool="Pool A or Pool B")
    @app_commands.choices(pool=[app_commands.Choice(name="Pool A", value="A"), app_commands.Choice(name="Pool B", value="B")])
    async def pool_list(self, interaction: discord.Interaction, pool: app_commands.Choice[str]):
        if not await self._staff_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        letter = pool.value
        pools = await asyncio.to_thread(get_pools, True)
        rows = pools[letter]
        if not rows:
            await interaction.followup.send(f"Pool {letter} is empty. Add cards with `/pool add`.", ephemeral=True)
            return
        w, sh = get_weights(), shares_for(pools, letter)
        lines = [
            f"{c['ovr']} OVR · **{esc(c['playername'])}** — chance {w.get(c['playername_key'], 0):g} → **{sh.get(c['playername_key'], 0):g}%**"
            for c in rows
        ]
        await interaction.followup.send((f"**Pool {letter}** ({len(rows)} cards)\n" + "\n".join(lines))[:1900], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DraftCog(bot))
