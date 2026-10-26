"""Drop system: random 'Catch' drops posted in two channels.

Spawn flow:
  count -> target messages reached -> wait (10 min)
  wait  -> time passed             -> armed
  armed -> next human message      -> drop after 6 seconds
  no human message for 60 min while counting -> armed immediately

Catch: the first click wins. Other clicks are silently ignored.
Buttons grey out after the drop is caught or expires.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

import card_db
import drop_config as cfg
import economy
from bot_guard import OWNER_ID
from squad_logic import sell_value

log = logging.getLogger("drops")
DB_PATH = Path(__file__).parent / "drop_state.db"


# ── Persistence ──────────────────────────────────────────────────────────────

@contextmanager
def _db(path: Path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class DropStore:
    def __init__(self, path: Path):
        self.path = path
        with _db(path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS drop_log ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
                " user_id INTEGER NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL)"
            )

    def load_state(self) -> dict | None:
        with _db(self.path) as c:
            row = c.execute("SELECT v FROM kv WHERE k = 'state'").fetchone()
        return json.loads(row[0]) if row else None

    def save_state(self, state: dict) -> None:
        with _db(self.path) as c:
            c.execute(
                "INSERT INTO kv (k, v) VALUES ('state', ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (json.dumps(state),),
            )

    def log(self, user_id: int, outcome: str, detail: str) -> None:
        with _db(self.path) as c:
            c.execute(
                "INSERT INTO drop_log (ts, user_id, outcome, detail) VALUES (?, ?, ?, ?)",
                (time.time(), user_id, outcome, detail),
            )


# ── Drop object and messages ─────────────────────────────────────────────────

@dataclass
class Drop:
    drop_id: str
    outcome: str                       # card / voucher / gold / silver / bronze / legendary / weekly
    card_key: str | None = None
    messages: list[discord.Message] = field(default_factory=list)
    claimed_by: int | None = None
    done: bool = False
    closed_text: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    expire_task: asyncio.Task | None = None


def _drop_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎁 Drop aaya!",
        description="Sabse pehle **Catch** dabao, reward tumhara!",
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{cfg.DROP_EXPIRE_SECONDS} seconds me expire hoga")
    return embed


def _closed_embed(text: str) -> discord.Embed:
    return discord.Embed(title="🎁 Drop", description=text, color=discord.Color.greyple())


def _disabled_view(label: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=True))
    return view


class CatchView(discord.ui.View):
    """One view per posted message. All views point to the same Drop."""

    def __init__(self, cog: "DropCog", drop: Drop):
        super().__init__(timeout=None)
        self.cog = cog
        self.drop = drop
        button = discord.ui.Button(label="Catch", emoji="🎯", style=discord.ButtonStyle.primary)
        button.callback = self._on_click
        self.add_item(button)

    async def _on_click(self, interaction: discord.Interaction):
        try:
            # Silent acknowledgement: nothing visible to the clicker, no "interaction failed".
            await interaction.response.defer()
            await self.cog.try_claim(self.drop, interaction.user)
        except Exception:
            log.exception("catch click failed")


# ── Cog ──────────────────────────────────────────────────────────────────────

class DropCog(commands.Cog):
    # Groups live in the class body, same as cooldown_cog.py.
    admin = app_commands.Group(
        name="admin",
        description="Owner tools",
        guild_ids=[cfg.GUILD_ID],
        guild_only=True,
        default_permissions=discord.Permissions(),   # hidden from normal members
    )
    drop_group = app_commands.Group(name="drop", description="Drop system", parent=admin)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = DropStore(DB_PATH)
        self.state = self.store.load_state() or self._fresh_state()
        self.active: dict[str, Drop] = {}
        self.spawn_pending = False     # True while the 6-second arm delay is running
        self.state_loop.start()

    def cog_unload(self):
        self.state_loop.cancel()
        for drop in list(self.active.values()):
            if drop.expire_task:
                drop.expire_task.cancel()

    # ── state ────────────────────────────────────────────────────────────────

    @staticmethod
    def _new_target() -> int:
        return random.randint(cfg.SPAWN_MIN, cfg.SPAWN_MAX)

    def _fresh_state(self) -> dict:
        return {
            "phase": "count",
            "count": 0,
            "target": self._new_target(),
            "wait_until": 0.0,
            "last_activity": time.time(),
        }

    def _save(self) -> None:
        self.store.save_state(self.state)

    # ── message listener / spawn scheduling ──────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or message.guild.id != cfg.GUILD_ID:
            return

        now = time.time()
        self.state["last_activity"] = now

        if self.spawn_pending:
            self._save()
            return

        phase = self.state["phase"]
        if phase == "armed":
            self.spawn_pending = True
            asyncio.create_task(self._arm_then_spawn())
        elif phase == "count":
            self.state["count"] += 1
            if self.state["count"] >= self.state["target"]:
                self.state["phase"] = "wait"
                self.state["wait_until"] = now + cfg.WAIT_AFTER_TARGET
        # phase == "wait": messages do not count
        self._save()

    async def _arm_then_spawn(self) -> None:
        try:
            await asyncio.sleep(cfg.ARM_DELAY)
            self.state.update(
                phase="count",
                count=0,
                target=self._new_target(),
                wait_until=0.0,
                last_activity=time.time(),
            )
            self._save()
            await self.spawn_drop()
        except Exception:
            log.exception("auto spawn failed")
        finally:
            self.spawn_pending = False

    @tasks.loop(seconds=cfg.STATE_TICK_SECONDS)
    async def state_loop(self):
        now = time.time()
        phase = self.state["phase"]
        arm = (
            (phase == "wait" and now >= self.state["wait_until"])
            or (phase == "count" and now - self.state["last_activity"] >= cfg.INACTIVITY_LIMIT)
        )
        if arm:
            self.state["phase"] = "armed"
            self._save()
            log.info("drop system armed")

    @state_loop.before_loop
    async def _before_state_loop(self):
        await self.bot.wait_until_ready()

    # ── drop creation ────────────────────────────────────────────────────────

    def _pick_card(self) -> str | None:
        by_ovr: dict[int, list[str]] = {}
        for c in card_db.list_obtainable_cards():
            by_ovr.setdefault(int(c["ovr"]), []).append(c["playername_key"])
        if not by_ovr:
            return None
        ovrs = list(cfg.CARD_OVR_WEIGHTS)
        target = random.choices(ovrs, [cfg.CARD_OVR_WEIGHTS[o] for o in ovrs])[0]
        # No card at the rolled OVR? Use the nearest OVR that has cards.
        best = min(by_ovr, key=lambda o: (abs(o - target), o))
        return random.choice(by_ovr[best])

    def _roll_outcome(self) -> Drop:
        names = list(cfg.OUTCOME_WEIGHTS)
        outcome = random.choices(names, [cfg.OUTCOME_WEIGHTS[n] for n in names])[0]
        card_key = None
        if outcome == "card":
            card_key = self._pick_card()
            if card_key is None:          # no cards in the DB: fall back to a bronze pack
                outcome = "bronze"
        return Drop(drop_id=secrets.token_hex(4), outcome=outcome, card_key=card_key)

    async def _get_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException as e:
                log.error("cannot fetch drop channel %s: %s", channel_id, e)
                return None
        return channel

    async def spawn_drop(self) -> Drop | None:
        """Create one drop and post it in both drop channels. Does not touch the counter."""
        drop = await asyncio.to_thread(self._roll_outcome)
        self.active[drop.drop_id] = drop
        drop.expire_task = asyncio.create_task(self._expire(drop))

        posted = 0
        for channel_id in cfg.DROP_CHANNEL_IDS:
            channel = await self._get_channel(channel_id)
            if channel is None:
                continue
            try:
                msg = await channel.send(embed=_drop_embed(), view=CatchView(self, drop))
                drop.messages.append(msg)
                posted += 1
            except discord.HTTPException as e:
                log.error("drop post failed in %s: %s", channel_id, e)

        if not posted:
            drop.expire_task.cancel()
            self.active.pop(drop.drop_id, None)
            return None

        # Someone may have caught it while the second post was still going out.
        if drop.done:
            await self._close(drop)
        return drop

    # ── catch / expire ───────────────────────────────────────────────────────

    async def _expire(self, drop: Drop) -> None:
        await asyncio.sleep(cfg.DROP_EXPIRE_SECONDS)
        async with drop.lock:
            if drop.done:
                return
            drop.done = True
            drop.closed_text = "⌛ Ye drop expire ho gaya."
        self.active.pop(drop.drop_id, None)
        await self._close(drop)

    async def try_claim(self, drop: Drop, user: discord.abc.User) -> bool:
        async with drop.lock:                       # one winner, even on simultaneous clicks
            if drop.done:
                return False
            if not await asyncio.to_thread(economy.user_exists, user.id):
                return False                        # not registered: silent, no reward
            reward = await asyncio.to_thread(self._grant_reward, drop, user.id)
            drop.claimed_by = user.id
            drop.done = True
            drop.closed_text = f"✅ <@{user.id}> ne catch kiya!\n{reward}"

        if drop.expire_task:
            drop.expire_task.cancel()
        self.active.pop(drop.drop_id, None)
        await self._close(drop)
        return True

    async def _close(self, drop: Drop) -> None:
        label = "Caught" if drop.claimed_by else "Expired"
        for msg in list(drop.messages):
            try:
                await msg.edit(embed=_closed_embed(drop.closed_text), view=_disabled_view(label))
            except discord.HTTPException as e:
                log.warning("could not close drop message: %s", e)

    # ── rewards (sync, runs in a worker thread) ──────────────────────────────

    def _grant_reward(self, drop: Drop, uid: int) -> str:
        if drop.outcome == "card":
            card = card_db.get_card(drop.card_key)
            name = f"{card['playername']} ({card['ovr']} OVR)"
            if economy.owns(uid, drop.card_key):
                refund = sell_value(card)
                economy.add_coins(uid, refund, "Drop duplicate card")
                text = f"🎴 **{name}** (duplicate) → +{economy.fmt_coins(refund)}"
            elif economy.give_card(uid, drop.card_key):
                text = f"🎴 **{name}** — card mil gaya!"
            else:
                # Squad full (or card unavailable): pay the sell value instead.
                refund = sell_value(card)
                economy.add_coins(uid, refund, "Drop card (squad full)")
                text = f"🎴 **{name}** — squad full, isliye +{economy.fmt_coins(refund)} mile"

        elif drop.outcome == "voucher":
            economy.add_vouchers(uid, 1, "Drop catch")
            text = "🎟️ **1 Voucher**"

        else:  # gold / silver / bronze / legendary / weekly
            qty = cfg.PACK_QTY.get(drop.outcome, 1)
            economy.add_pack(uid, drop.outcome, qty)
            text = f"📦 **{qty}x {drop.outcome.title()} Pack**"

        self.store.log(uid, drop.outcome, text)
        return text

    # ── owner command: /admin drop spawn n ───────────────────────────────────

    @drop_group.command(name="spawn", description="Spawn n drops right now (owner only)")
    @app_commands.describe(n="How many drops to spawn (1-20)")
    async def spawn(self, interaction: discord.Interaction, n: app_commands.Range[int, 1, 20]):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Iske liye permission nahi hai.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        spawned = 0
        for _ in range(n):
            try:
                if await self.spawn_drop():
                    spawned += 1
            except Exception:
                log.exception("admin spawn failed")
        await interaction.followup.send(f"✅ {spawned}/{n} drops spawned.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DropCog(bot))
