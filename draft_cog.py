"""Draft (voucher) system.

Players
    csdraft                      open the Draft screen (Pool A / B / C, 1 Draft / 10 Drafts)
    csbal                        (in economy_cog) shows coins + vouchers

Owner / admin  (text commands, main server only)
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
from discord import ui
from discord.ext import commands

import card_db
import draft_config as cfg
import draft_image
import economy
import emojis
import security
import squad_logic as sl
from bot_guard import ADMIN_GUILD_ID
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

_pool_cache: dict = {"t": 0.0, "data": None}
_warned: set[str] = set()


def _resolve(names: list[str], label: str) -> list:
    out, seen = [], set()
    for name in names:
        card, err = sl.resolve_any(str(name))
        if card is None:
            tag = f"{label}:{name}"
            if tag not in _warned:
                _warned.add(tag)
                print(f"[draft] Pool {label}: could not use '{name}' — {err}")
            continue
        if card["playername_key"] in seen:
            continue
        seen.add(card["playername_key"])
        out.append(card)
    return out


def get_pools(force: bool = False) -> dict[str, list]:
    """{"A": [rows], "B": [rows], "C": [rows]}. Cached for 60 s (call with force=True to refresh)."""
    now = time.time()
    if not force and _pool_cache["data"] is not None and now - _pool_cache["t"] < 60:
        return _pool_cache["data"]
    a = _resolve(cfg.POOL_A_CARDS, "A")
    a_keys = {c["playername_key"] for c in a}
    b = [c for c in _resolve(cfg.POOL_B_CARDS, "B") if c["playername_key"] not in a_keys]
    fixed = a_keys | {c["playername_key"] for c in b}
    c = [
        x for x in card_db.list_all_cards()
        if cfg.POOL_C_OVR_MIN <= int(x["ovr"]) <= cfg.POOL_C_OVR_MAX and x["playername_key"] not in fixed
    ]
    a.sort(key=lambda x: -int(x["ovr"]))
    b.sort(key=lambda x: -int(x["ovr"]))
    data = {"A": a, "B": b, "C": c}
    _pool_cache.update(t=now, data=data)
    return data


def _make_roll(pools: dict[str, list]):
    avail = {p: rows for p, rows in pools.items() if rows}

    def roll(force_a: bool):
        if not avail:
            return None
        if force_a and "A" in avail:
            pool = "A"
        else:
            names = list(avail)
            weights = [max(0, float(cfg.ODDS.get(p, 0))) for p in names]
            if sum(weights) <= 0:
                weights = [1.0] * len(names)
            pool = _rng.choices(names, weights=weights, k=1)[0]
        return pool, _rng.choice(avail[pool])["playername_key"]

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
                    _make_roll(pools), lambda k: sl.sell_value(by_key[k]) if k in by_key else 0,
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


class DraftCog(commands.Cog, name="Draft"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
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


async def setup(bot: commands.Bot):
    await bot.add_cog(DraftCog(bot))
