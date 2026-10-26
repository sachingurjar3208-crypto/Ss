"""Draft (voucher) system.

Players
    csdraft                      open the Draft screen (Pool A / B / C, 1 Draft / 10 Drafts)
    csdraft halloween            open a running EVENT draft by name (same as cshalloweendraft).
                                 While an event runs, a dropdown under the image switches drafts.
    cshalloweendraft             the Halloween Draft
    csbal                        (in economy_cog) shows coins + vouchers

Draft events (like the Star Pass seasons, see draft_event.py)  - slash commands, admins only
    /draftmaker   start an event draft: name, duration, theme, background image, Pool A chance,
                  guarantee, Pool C range. It runs NEXT TO the normal draft.
    /draftbg      change or remove the background image of an event (empty event = the normal draft)
    /draftend     end a running event right now
    /draftreopen  bring an ended event back for some days
    /pool add / remove / list  have an optional `event` option: empty = normal draft,
                  or the event name to fill THAT event's Pool A / B.

Owner / admin  (text commands, main server only)
    /pool add <A|B> <player> <chance>   put a card in Pool A or B with its chance (slash command)
    /pool remove <player>               take a card out of its pool
    /pool list <A|B>                    see a pool with the real % of each card
    csvoucher check @user        see someone's vouchers + draft counter   (also: voucher check)
    csvoucher give @user <n>     give vouchers
    csvoucher remove @user <n>   take vouchers (stops at 0)
    /pool animation pool:A pathname:<name> url:<link> [event:<name>]
                                        add a Pool A animation (plays on a Pool A pull);
                                        with `event` it plays ONLY in that event's draft
    /pool animation_remove pathname:<name>   delete an animation
    /pool animation_merge pathname:<name> url:<link>    replace that animation completely

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
import draft_event
import draft_image
import economy
import emojis
import pool_anim
import quest
import security
import squad_logic as sl
from bot_guard import ADMIN_GUILD_ID, admin_only, is_staff
from economy_cog import economy_cooldown, economy_gate, has_pending, send_next_pending, _running
from security import OwnedView, esc
from squad_cog import _card_line, _in_match, _is_premium, _need_debut

_HERE = Path(__file__).parent
_rng = secrets.SystemRandom()
POOLS = ("A", "B", "C")
MIN_GAP = 2.0                                   # seconds between two drafts of one user

_busy = _running                                # shared with economy_cog: a running draft blocks every other economy command
_last_draft: dict[int, float] = {}


# ── Pool contents (cached for a minute) ──────────────────────────────────────
# Pool A / B cards and their chances live in the database (managed with the
# `/pool` slash commands). Pool C = every card in the OVR range of draft_config.

_pool_cache: dict[str, dict] = {}                 # one entry per draft: "main" or an event key
MAIN = draft_event.MAIN


def _ckey(event: dict | None) -> str:
    return event["event_key"] if event else MAIN


def _c_range(event: dict | None) -> tuple[int, int]:
    if event:
        return int(event["c_min"]), int(event["c_max"])
    return cfg.POOL_C_OVR_MIN, cfg.POOL_C_OVR_MAX


def _odds(event: dict | None) -> dict:
    if event:                                     # the event sets Pool A; B / C keep the normal split
        return {"A": float(event["odds_a"]), "B": cfg.ODDS.get("B", 0), "C": cfg.ODDS.get("C", 0)}
    return dict(cfg.ODDS)


def _pity_at(event: dict | None) -> int:
    return int(event["pity_every"]) if event else cfg.PITY_A_EVERY


def drop_cache(key: str | None = None) -> None:
    if key is None:
        _pool_cache.clear()
    else:
        _pool_cache.pop(key, None)


def get_pools(force: bool = False, event: dict | None = None) -> dict[str, list]:
    """{"A": [rows], "B": [rows], "C": [rows]} of the normal draft (event=None) or of one event.
    Cached for 60 s (force=True refreshes)."""
    key = _ckey(event)
    now = time.time()
    c = _pool_cache.get(key)
    if not force and c is not None and c["data"] is not None and now - c["t"] < 60:
        return c["data"]
    saved = economy.pool_cards() if event is None else draft_event.pool_cards(key)
    by_key = card_db.get_cards([k for _, k, _ in saved]) if saved else {}
    pools: dict[str, list] = {"A": [], "B": [], "C": []}
    weights: dict[str, float] = {}
    for pool, pkey, weight in saved:
        row = by_key.get(pkey)
        if row is None:
            continue                                  # card was deleted from the game
        # Admin-picked Pool A / B cards are always allowed, even if the card is
        # unobtainable or belongs to an event (disabled or not).
        pools[pool].append(row)
        weights[pkey] = weight
    fixed = set(weights)
    lo, hi = _c_range(event)
    pools["C"] = [
        x for x in card_db.list_obtainable_cards()
        if lo <= int(x["ovr"]) <= hi and x["playername_key"] not in fixed
    ]
    pools["A"].sort(key=lambda x: -int(x["ovr"]))
    pools["B"].sort(key=lambda x: -int(x["ovr"]))
    _pool_cache[key] = {"t": now, "data": pools, "weights": weights}
    return pools


def get_weights(event: dict | None = None) -> dict[str, float]:
    return dict((_pool_cache.get(_ckey(event)) or {}).get("weights") or {})


def shares_for(pools: dict[str, list], pool: str, event: dict | None = None) -> dict[str, float]:
    """{player_key: % chance inside this pool} (the pool's weights scaled to 100)."""
    if pool not in ("A", "B"):
        return {}
    w = get_weights(event)
    rows = pools.get(pool, [])
    total = sum(w.get(r["playername_key"], 0) for r in rows)
    if total <= 0:
        return {}
    return {r["playername_key"]: round(100 * w.get(r["playername_key"], 0) / total, 1) for r in rows}


def _make_roll(pools: dict[str, list], weights: dict[str, float], odds: dict | None = None):
    avail = {p: rows for p, rows in pools.items() if rows}
    odds = odds if odds is not None else cfg.ODDS

    def roll(force_a: bool, force_b: bool = False):
        if not avail:
            return None
        if force_a and "A" in avail:
            pool = "A"
        elif force_b and "B" in avail:
            pool = "B"
        else:
            # Pool A is decided on its own: EXACTLY odds["A"]% per draft (3% normally), no matter
            # whether Pool B / C have cards. Only the pity draft can add more.
            a_chance = max(0.0, float(odds.get("A", 0)))
            if "A" in avail and _rng.random() * 100.0 < a_chance:
                pool = "A"
            else:
                others = [p for p in avail if p != "A"]
                if not others:
                    pool = "A"          # only Pool A has cards: nothing else to roll
                else:
                    w = [max(0.0, float(odds.get(p, 0))) for p in others]
                    if sum(w) <= 0:
                        w = [1.0] * len(others)
                    pool = _rng.choices(others, weights=w, k=1)[0]
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

def draft_color(pool: str, event: dict | None = None) -> discord.Color:
    theme = event["theme"] if event else "default"
    return discord.Color.from_rgb(*draft_image.pool_color(pool, theme))


def _screen_embed(pool: str, event: dict | None = None) -> discord.Embed:
    embed = discord.Embed(color=draft_color(pool, event))
    embed.set_image(url=f"attachment://{draft_image.FILENAME}")
    foot = f"Pool A is guaranteed on every {_pity_at(event)}th draft"
    if cfg.PITY_B_EVERY > 0:
        foot += f" • Pool B on every {cfg.PITY_B_EVERY}th"
    embed.set_footer(text=foot)
    return embed


async def _look(event: dict | None) -> dict | None:
    """What the image needs to draw an event (theme, title, background, time left).
    The normal draft only gets a look when an admin saved a custom background for it (/draftbg)."""
    bg = await asyncio.to_thread(draft_event.get_background, _ckey(event))
    if event is None:
        if bg is None:
            return None
        return {"theme": "default", "title": "", "bg": bg[0], "stamp": bg[1]}
    return {
        "theme": event["theme"], "title": event["name"], "left": draft_event.fmt_left(event["seconds_left"]),
        "bg": bg[0] if bg else None, "stamp": bg[1] if bg else None,
    }


async def _pity_of(uid: int, event: dict | None) -> int:
    if event is None:
        return await asyncio.to_thread(economy.get_draft_pity, uid)
    return await asyncio.to_thread(draft_event.get_pity, event["id"], uid)


async def _render_screen(uid: int, pool: str, page: int,
                         event: dict | None = None) -> tuple[discord.Embed, discord.File, int]:
    pools = await asyncio.to_thread(get_pools, False, event)
    vouchers = await asyncio.to_thread(economy.get_vouchers, uid)
    since = await _pity_of(uid, event)
    look = await _look(event)
    cards = pools.get(pool, []) if pool != "C" else []
    total_pages = draft_image.pages_for(len(cards)) if pool != "C" else 1
    page = max(0, min(page, total_pages - 1))
    buf = await asyncio.to_thread(
        draft_image.build_draft_screen,
        pool, page, cards, vouchers, since, _pity_at(event), _odds(event), _c_range(event),
        cfg.VOUCHERS_PER_DRAFT, cfg.VOUCHERS_PER_DRAFT * cfg.DRAFT_MULTI, cfg.DRAFT_MULTI,
        {}, look,
    )
    return _screen_embed(pool, event), discord.File(buf, filename=draft_image.FILENAME), page


DRAFT_VIEW_TIMEOUT = 60.0


class DraftView(OwnedView):
    def __init__(self, bot: commands.Bot, user_id: int, event: dict | None = None, actives: list | None = None):
        super().__init__(user_id, timeout=DRAFT_VIEW_TIMEOUT)
        self.bot = bot
        self.uid = user_id
        self.event = event                      # None = the normal draft
        self.actives = actives or []            # running events (a dropdown shows when there is one)
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
        if self.actives:                                  # an event is running: dropdown to switch
            cur = _ckey(self.event)
            opts = [discord.SelectOption(label="Normal Draft", value=MAIN, default=cur == MAIN)]
            opts += [discord.SelectOption(label=a["name"][:100], value=a["event_key"], default=a["event_key"] == cur)
                     for a in self.actives[:24]]
            sel = ui.Select(placeholder="Switch draft", options=opts, row=3)
            sel.callback = self._switch
            self.add_item(sel)

    def _make_pool_cb(self, pool: str):
        async def cb(interaction: discord.Interaction):
            self.pool, self.page = pool, 0
            await self._redraw(interaction)
        return cb

    def _page_cb(self, step: int):
        async def cb(interaction: discord.Interaction):
            self.page += step
            await self._redraw(interaction)
        return cb

    async def _switch(self, interaction: discord.Interaction):
        key = interaction.data["values"][0]
        self.actives = await asyncio.to_thread(draft_event.list_active)
        self.event = next((a for a in self.actives if a["event_key"] == key), None) if key != MAIN else None
        self.pool, self.page = "A", 0
        await self._redraw(interaction)

    async def _sync_event(self) -> bool:
        """Refresh the running event. Returns True when it just ended (we fall back to the normal draft)."""
        if self.event is None:
            return False
        fresh = await asyncio.to_thread(draft_event.get_active, self.event["event_key"])
        if fresh is None:
            self.event = None
            self.pool, self.page = "A", 0
            self.actives = await asyncio.to_thread(draft_event.list_active)
            return True
        self.event = fresh
        return False

    async def _redraw(self, interaction: discord.Interaction):
        await interaction.response.defer()
        ended = await self._sync_event()
        embed, file, self.page = await _render_screen(self.uid, self.pool, self.page, self.event)
        self._build()
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)
        if ended:
            await interaction.followup.send("That draft event has ended. Back to the normal draft.", ephemeral=True)

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
        if has_pending(uid):                            # a confirm button / trade is still waiting
            await interaction.response.send_message("You already have a command pending. Finish it first.", ephemeral=True)
            return
        _busy.add(uid)
        try:
            await interaction.response.defer()
            if await self._sync_event():                # the event ended while the screen was open: nothing is charged
                embed, file, self.page = await _render_screen(uid, self.pool, self.page, None)
                self._build()
                await interaction.edit_original_response(embed=embed, attachments=[file], view=self)
                await interaction.followup.send("❌ That draft event has ended. Nothing was charged.", ephemeral=True)
                return
            event = self.event
            pools = await asyncio.to_thread(get_pools, False, event)
            if not any(pools.values()):
                await interaction.followup.send("⚠️ No cards are available for the draft yet. Ask the owner.", ephemeral=True)
                return
            by_key = {c["playername_key"]: c for rows in pools.values() for c in rows}
            try:
                res = await asyncio.to_thread(
                    economy.draft_spin, uid, count, cfg.VOUCHERS_PER_DRAFT, _pity_at(event),
                    _make_roll(pools, get_weights(event), _odds(event)),
                    lambda k: sl.sell_value(by_key[k]) if k in by_key else 0,
                    event["id"] if event else None,
                    cfg.PITY_B_EVERY,
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
            await quest.track_and_notify(interaction.channel, uid, {"drafts": count})
        finally:
            _busy.discard(uid)

    async def _show_result(self, interaction: discord.Interaction, res: dict, by_key: dict, count: int):
        event = self.event
        rows = res["results"]
        items = [{"card": by_key.get(r["key"]) or card_db.get_card(r["key"]), "pool": r["pool"], "status": r["status"]} for r in rows]
        lines = []
        for r, it in zip(rows, items):
            if it["card"] is None:
                continue
            tag = {"added": "  🆕", "pending": "  ⚠️ squad full", "dupe": "  ♻️ duplicate"}[r["status"]]
            lines.append(f"**Pool {r['pool']}** · {_card_line(it['card'], tag)}")
        label = event["name"] if event else "Draft"
        embed = discord.Embed(
            title=f"{emojis.VOUCHER} {label} ×{count}",
            description="\n".join(lines)[:4000],
            color=draft_color("A" if any(r["pool"] == "A" for r in rows) else "C", event),
        )
        foot = f"Vouchers left: {res['vouchers']:,}"
        if res["refund"]:
            foot += f" · Duplicates gave back {res['refund']:,} coins"
        embed.set_footer(text=foot)
        embed.set_image(url="attachment://draft.jpg")

        look = await _look(event)
        buf = await asyncio.to_thread(draft_image.build_result_image, items, look)
        result_file = discord.File(buf, filename="draft.jpg")

        got_a = any(r["pool"] == "A" for r in rows)
        # The animation only plays when a Pool A player was pulled. Animations come from
        # `/pool animation` (one is picked at random); the old single file is the fallback.
        anim = None
        if got_a:
            anim = await asyncio.to_thread(pool_anim.pick, "A", event["event_key"] if event else "")
            if anim is None:
                legacy = _HERE / cfg.POOL_A_ANIMATION_FILE
                anim = legacy if legacy.exists() else None
        msg = None
        if got_a and anim is not None:
            try:
                ext = anim.suffix.lower() or ".gif"
                anim_embed = discord.Embed(color=draft_color("A", event))
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
            if got_a and anim is not None:                  # animation failed: rebuild the file, send plain
                result_file = discord.File(io.BytesIO(buf.getvalue()), filename="draft.jpg")
            await interaction.followup.send(embed=embed, file=result_file)

        # refresh the draft screen (new voucher count / counter)
        try:
            screen_embed, screen_file, self.page = await _render_screen(self.uid, self.pool, self.page, event)
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
    ev_name = getattr(interaction.namespace, "event", None)       # the `event` option typed so far
    event = await _event_ref(ev_name) if ev_name else None
    pools = await asyncio.to_thread(get_pools, False, event)
    names = [c["playername"] for p in ("A", "B") for c in pools[p] if cur in c["playername"].lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


async def _event_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower()
    items = await asyncio.to_thread(draft_event.list_keys)
    return [app_commands.Choice(name=f"{name} ({key})"[:100], value=key)
            for key, name in items if cur in key or cur in name.lower()][:25]


async def _event_ref(name: str | None) -> dict | None:
    """The event dict an admin means with the `event` option. None = the normal draft.
    A key that has no event yet gets a stand-in (cards can be prepared before the event starts)."""
    key = draft_event.norm_key(name)
    if key == MAIN:
        return None
    ev = await asyncio.to_thread(draft_event.get_latest, key)
    if ev is not None:
        return ev
    return {"id": 0, "event_key": key, "name": key.capitalize(), "theme": "default", "pity_every": cfg.PITY_A_EVERY,
            "odds_a": float(cfg.ODDS.get("A", 3)), "c_min": cfg.POOL_C_OVR_MIN, "c_max": cfg.POOL_C_OVR_MAX,
            "seconds_left": 0, "days_left": 0, "active": False}


async def _read_background(attachment: discord.Attachment | None):
    """(bytes | None, error | None): an uploaded image, ready to be stored."""
    if attachment is None:
        return None, None
    if not (attachment.content_type or "").startswith("image/"):
        return None, "The background must be an image (PNG or JPG)."
    if attachment.size > draft_image.MAX_BG_BYTES:
        return None, "That image is too big (max 8 MB)."
    try:
        raw = await attachment.read()
        return await asyncio.to_thread(draft_image.prepare_background, raw), None
    except ValueError as e:
        return None, str(e)
    except discord.HTTPException:
        return None, "I could not download that image. Please try again."


_open_cd = commands.CooldownMapping.from_cooldown(1, 2 * 60, commands.BucketType.user)


def _check_cooldown(ctx: commands.Context) -> None:
    """Normal users: 2 min between csdraft, Premium: none (checked AFTER the event name is valid,
    so a typo in the name doesn't use the cooldown up)."""
    if _is_premium(ctx.author):
        return
    bucket = _open_cd.get_bucket(ctx.message)
    retry = bucket.update_rate_limit() if bucket else None
    if retry:
        raise commands.CommandOnCooldown(bucket, retry, commands.BucketType.user)


async def _anim_autocomplete(interaction: discord.Interaction, current: str):
    found = await asyncio.to_thread(pool_anim.listing, current)
    return [app_commands.Choice(name=f"{n} ({ek or 'all drafts'})"[:100], value=n) for n, ek in found[:25]]


class DraftCog(commands.Cog, name="Draft"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await asyncio.to_thread(economy.ensure_draft_tables)
            await asyncio.to_thread(pool_anim.ensure_table)
        except Exception as e:
            print(f"[draft] could not create voucher tables: {e}")
        # warm the pool cache so the first csdraft is fast and typos show in the console
        try:
            await asyncio.to_thread(get_pools, True)
        except Exception as e:
            print(f"[draft] could not load pools: {e}")

    async def _open_draft(self, ctx: commands.Context, name: str | None):
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't draft during a match.")
            return
        actives = await asyncio.to_thread(draft_event.list_active)
        event = None
        key = draft_event.norm_key(name)
        if key != MAIN:
            event = next((a for a in actives if a["event_key"] == key), None)
            if event is None:
                running = ", ".join(f"`csdraft {a['event_key']}`" for a in actives) or "no event right now"
                await ctx.send(f"There is no running draft called **{esc(name)}**. Running now: {running}")
                return
        _check_cooldown(ctx)
        view = DraftView(self.bot, ctx.author.id, event, actives)
        embed, file, view.page = await _render_screen(ctx.author.id, view.pool, 0, event)
        view.message = await ctx.send(embed=embed, file=file, view=view)

    @commands.command(name="csdraft")
    @economy_gate(0)                    # blocked while another economy command is pending
    async def csdraft(self, ctx: commands.Context, *, event_name: str | None = None):
        """Open the Draft screen (or an event draft: csdraft halloween)."""
        await self._open_draft(ctx, event_name)

    @commands.command(name="cshalloweendraft", aliases=["halloweendraft", "cshdraft"])
    @economy_gate(0)
    async def cshalloweendraft(self, ctx: commands.Context):
        """Open the Halloween Draft."""
        await self._open_draft(ctx, "halloween")

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
        text = (
            f"{emojis.VOUCHER} **{esc(member.display_name)}** has **{have:,}** voucher(s).\n"
            f"Pool A guaranteed in **{left}** draft(s)."
        )
        for ev in await asyncio.to_thread(draft_event.list_active):
            n = await asyncio.to_thread(draft_event.get_pity, ev["id"], member.id)
            text += f"\n{esc(ev['name'])}: Pool A guaranteed in **{max(1, ev['pity_every'] - n)}** draft(s)."
        await ctx.send(text)

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
        event="Event draft (e.g. halloween). Empty = the normal draft",
    )
    @app_commands.choices(pool=[app_commands.Choice(name="Pool A", value="A"), app_commands.Choice(name="Pool B", value="B")])
    @app_commands.autocomplete(player=_card_autocomplete, event=_event_autocomplete)
    async def pool_add(self, interaction: discord.Interaction, pool: app_commands.Choice[str], player: str,
                       chance: app_commands.Range[float, 0.01, 100.0], event: str | None = None):
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
        ev = await _event_ref(event)
        if ev is None:
            await asyncio.to_thread(economy.pool_set, letter, card["playername_key"], float(chance))
        else:
            await asyncio.to_thread(draft_event.pool_set, ev["event_key"], letter, card["playername_key"], float(chance))
        pools = await asyncio.to_thread(get_pools, True, ev)
        share = shares_for(pools, letter, ev).get(card["playername_key"], 0)
        where = f"{esc(ev['name'])} · " if ev else ""
        await interaction.followup.send(
            f"✅ **{esc(card['playername'])}** ({card['ovr']} OVR) is in **{where}Pool {letter}** with chance **{chance:g}** "
            f"→ **{share:g}%** of Pool {letter} (Pool {letter} now has {len(pools[letter])} card(s)).",
            ephemeral=True,
        )

    @pool.command(name="remove", description="Take a card out of Pool A or B")
    @app_commands.describe(player="Which player card", event="Event draft (e.g. halloween). Empty = the normal draft")
    @app_commands.autocomplete(player=_pool_card_autocomplete, event=_event_autocomplete)
    async def pool_remove(self, interaction: discord.Interaction, player: str, event: str | None = None):
        if not await self._staff_only(interaction):
            return
        card = card_db.get_card(player)
        if card is None:
            await interaction.response.send_message("❌ Pick a player from the list.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ev = await _event_ref(event)
        if ev is None:
            was = await asyncio.to_thread(economy.pool_remove, card["playername_key"])
        else:
            was = await asyncio.to_thread(draft_event.pool_remove, ev["event_key"], card["playername_key"])
        if was is None:
            await interaction.followup.send(f"❌ **{esc(card['playername'])}** isn't in Pool A or B there.", ephemeral=True)
            return
        await asyncio.to_thread(get_pools, True, ev)
        where = f"{esc(ev['name'])} · " if ev else ""
        await interaction.followup.send(f"✅ Removed **{esc(card['playername'])}** from {where}Pool {was}.", ephemeral=True)

    @pool.command(name="list", description="Show the cards of Pool A or B with their real chance %")
    @app_commands.describe(pool="Pool A or Pool B", event="Event draft (e.g. halloween). Empty = the normal draft")
    @app_commands.choices(pool=[app_commands.Choice(name="Pool A", value="A"), app_commands.Choice(name="Pool B", value="B")])
    @app_commands.autocomplete(event=_event_autocomplete)
    async def pool_list(self, interaction: discord.Interaction, pool: app_commands.Choice[str], event: str | None = None):
        if not await self._staff_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        letter = pool.value
        ev = await _event_ref(event)
        pools = await asyncio.to_thread(get_pools, True, ev)
        rows = pools[letter]
        where = f"{esc(ev['name'])} · " if ev else ""
        if not rows:
            await interaction.followup.send(f"{where}Pool {letter} is empty. Add cards with `/pool add`.", ephemeral=True)
            return
        w, sh = get_weights(ev), shares_for(pools, letter, ev)
        lines = [
            f"{c['ovr']} OVR · **{esc(c['playername'])}** — chance {w.get(c['playername_key'], 0):g} → **{sh.get(c['playername_key'], 0):g}%**"
            for c in rows
        ]
        await interaction.followup.send((f"**{where}Pool {letter}** ({len(rows)} cards)\n" + "\n".join(lines))[:1900], ephemeral=True)

    # -- Draft events: /draftmaker /draftbg /draftend /draftreopen -------------
    @app_commands.command(name="draftmaker", description="Start an event draft (Halloween Draft ...) next to the normal draft")
    @app_commands.describe(
        event_name="Name of the event, e.g. Halloween Draft (players open it with csdraft halloween)",
        duration="How long the event runs",
        theme="Colors of the draft image (used when you don't upload a background)",
        background="Your own background image (PNG/JPG, best 1920x1080, 16:9)",
        pool_a_chance="Chance of Pool A per draft in % (empty = 3)",
        guarantee="Pool A is guaranteed every N drafts (empty = 50)",
        pool_c_min="Pool C random card: lowest OVR (empty = 60)",
        pool_c_max="Pool C random card: highest OVR (empty = 79)",
    )
    @app_commands.choices(
        duration=[app_commands.Choice(name=k, value=k) for k in draft_event.DURATIONS],
        theme=[app_commands.Choice(name=t.capitalize(), value=t) for t in draft_event.THEMES],
    )
    @admin_only()
    async def draftmaker(
        self,
        interaction: discord.Interaction,
        event_name: str,
        duration: app_commands.Choice[str],
        theme: app_commands.Choice[str] | None = None,
        background: discord.Attachment | None = None,
        pool_a_chance: app_commands.Range[float, 0.0, 100.0] | None = None,
        guarantee: app_commands.Range[int, 2, 1000] | None = None,
        pool_c_min: app_commands.Range[int, 1, 120] | None = None,
        pool_c_max: app_commands.Range[int, 1, 120] | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        bg_data, bg_err = await _read_background(background)          # check the image BEFORE the event starts
        if bg_err:
            await interaction.followup.send(bg_err, ephemeral=True)
            return
        ev = await asyncio.to_thread(
            draft_event.create_event, event_name, duration.value, interaction.user.id,
            theme.value if theme else "default",
            guarantee if guarantee is not None else cfg.PITY_A_EVERY,
            pool_a_chance if pool_a_chance is not None else float(cfg.ODDS.get("A", 3)),
            pool_c_min if pool_c_min is not None else cfg.POOL_C_OVR_MIN,
            pool_c_max if pool_c_max is not None else cfg.POOL_C_OVR_MAX,
        )
        if isinstance(ev, str):
            await interaction.followup.send(ev, ephemeral=True)
            return
        key = ev["event_key"]
        if bg_data is not None:
            await asyncio.to_thread(draft_event.set_background, key, bg_data)
        has_bg = bg_data is not None or await asyncio.to_thread(draft_event.get_background, key) is not None
        drop_cache(key)
        n_cards = len(await asyncio.to_thread(draft_event.pool_cards, key))

        embed = discord.Embed(title=f"{ev['name']} started", color=draft_color("A", ev))
        embed.add_field(name="Event key", value=key)
        embed.add_field(name="Ends", value=f"<t:{int(ev['end_ts'])}:R>")
        embed.add_field(name="Theme", value=ev["theme"].capitalize())
        embed.add_field(name="Background", value="Custom image" if has_bg else "Theme colors")
        embed.add_field(name="Pool A chance / guarantee", value=f"{ev['odds_a']:g}% / every {ev['pity_every']}")
        embed.add_field(name="Pool C", value=f"{ev['c_min']}-{ev['c_max']} OVR")
        embed.add_field(name="Players open it with", value=f"`csdraft {key}`", inline=False)
        notes = []
        if n_cards == 0:
            notes.append(f"Pool A / B are empty: add cards with /pool add (event: {key}).")
        if bg_data is None and has_bg:
            notes.append("Using the background saved earlier for this event (change it with /draftbg).")
        if notes:
            embed.set_footer(text="  ".join(notes))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="draftbg", description="Change or remove the background image of a draft")
    @app_commands.describe(
        image="New background (PNG/JPG, best 1920x1080, 16:9). Leave empty together with remove",
        remove="Remove the custom background (back to the theme / the default blue)",
        event="Which event (empty = the normal draft)",
    )
    @app_commands.autocomplete(event=_event_autocomplete)
    @admin_only()
    async def draftbg(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment | None = None,
        remove: bool = False,
        event: str | None = None,
    ):
        key = draft_event.norm_key(event)
        if (image is None) == (not remove):
            await interaction.response.send_message(
                "Upload an image, or set remove to True (not both, not neither).", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if remove:
            await asyncio.to_thread(draft_event.set_background, key, None)
            await interaction.followup.send(f"Background of `{key}` removed.", ephemeral=True)
            return
        data, err = await _read_background(image)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        await asyncio.to_thread(draft_event.set_background, key, data)
        await interaction.followup.send(
            f"New background saved for `{key}`. It shows the next time someone opens or refreshes the draft.",
            ephemeral=True)

    @app_commands.command(name="draftend", description="End a running event draft right now")
    @app_commands.describe(event="Which event to end")
    @app_commands.autocomplete(event=_event_autocomplete)
    @admin_only()
    async def draftend(self, interaction: discord.Interaction, event: str):
        key = draft_event.norm_key(event)
        if key == MAIN:
            await interaction.response.send_message("The normal draft never ends. Pick an event.", ephemeral=True)
            return
        ended = await asyncio.to_thread(draft_event.end_event, key)
        if ended is None:
            await interaction.response.send_message("That event is not running.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{ended['name']} has been ended.", ephemeral=True)

    @app_commands.command(name="draftreopen", description="Bring an ended event draft back for some days")
    @app_commands.describe(event="Which event", days="Run it for this many days from now")
    @app_commands.autocomplete(event=_event_autocomplete)
    @admin_only()
    async def draftreopen(self, interaction: discord.Interaction, event: str,
                          days: app_commands.Range[int, 1, 60] = 7):
        res = await asyncio.to_thread(draft_event.reopen_event, event, days)
        if isinstance(res, str):
            await interaction.response.send_message(res, ephemeral=True)
            return
        drop_cache(res["event_key"])
        await interaction.response.send_message(
            f"{res['name']} is back for {days} day(s). Players open it with `csdraft {res['event_key']}`.", ephemeral=True)

    # -- Pool A animation (shown when someone pulls a Pool A player) -------
    @pool.command(name="animation", description="Add a Pool animation: plays when someone pulls a Pool A player")
    @app_commands.describe(
        pool="Which pool (only Pool A for now)",
        pathname="A short name for this animation, e.g. golden_a (letters, numbers, - and _)",
        url="Link to the GIF (Giphy links work)",
        event="Event draft (e.g. halloween): plays ONLY there. Empty = all drafts",
    )
    @app_commands.choices(pool=[app_commands.Choice(name="Pool A", value="A")])
    @app_commands.autocomplete(event=_event_autocomplete)
    async def pool_animation(self, interaction: discord.Interaction, pool: app_commands.Choice[str],
                             pathname: str, url: str, event: str | None = None):
        if not await self._staff_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        ev = await _event_ref(event)
        ekey = ev["event_key"] if ev else ""
        try:
            res = await pool_anim.add(pool.value, pathname, url, ekey)
        except pool_anim.AnimError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        where = f" of **{esc(ev['name'])}**" if ev else ""
        await self._anim_done(
            interaction, f"✅ Animation **{res['pathname']}** saved for **Pool {res['pool']}**{where}.", res)

    @pool.command(name="animation_remove", description="Delete a Pool animation")
    @app_commands.describe(pathname="Name of the animation to delete")
    @app_commands.autocomplete(pathname=_anim_autocomplete)
    async def pool_animation_remove(self, interaction: discord.Interaction, pathname: str):
        if not await self._staff_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            old = await pool_anim.remove(pathname)
        except pool_anim.AnimError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        await interaction.followup.send(f"🗑️ Animation **{esc(old['pathname'])}** deleted.", ephemeral=True)

    @pool.command(name="animation_merge", description="Replace a Pool animation with a new one (the old one is deleted)")
    @app_commands.describe(
        pathname="Name of the animation to replace",
        url="Link to the NEW GIF (Giphy links work)",
    )
    @app_commands.autocomplete(pathname=_anim_autocomplete)
    async def pool_animation_merge(self, interaction: discord.Interaction, pathname: str, url: str):
        if not await self._staff_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            res = await pool_anim.merge(pathname, url)
        except pool_anim.AnimError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        await self._anim_done(
            interaction,
            f"✅ **{res['pathname']}** replaced. The old animation is deleted; Pool {res['pool']} now uses the new one.",
            res,
        )

    @staticmethod
    async def _anim_done(interaction: discord.Interaction, text: str, res: dict) -> None:
        """Confirm to the admin, with a preview of the saved animation."""
        path = res["file"]
        when = ("whenever someone pulls a Pool A player in that event"
                if res.get("event_key") else "whenever someone pulls a Pool A player")
        try:
            await interaction.followup.send(
                text + f"\nIt plays {when}.",
                file=discord.File(str(path), filename=f"preview{path.suffix}"),
                ephemeral=True,
            )
        except discord.HTTPException:
            await interaction.followup.send(text, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DraftCog(bot))
