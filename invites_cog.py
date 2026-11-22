"""Invite rewards (main server only).

When someone joins through a member's invite link, the join is recorded as PENDING. It only COUNTS when:
  * the new account is at least MIN_ACCOUNT_DAYS old (checked at join),
  * it is not a bot, not the inviter themself, and has never joined before (rejoins never count again),
  * it was NOT already a member before (everyone who was in the server when this started is saved as
    "existing", so leaving and coming back never counts), and
  * it made a team with rcdebut.
Leaving the server takes the invite away (rewards already given are kept, each milestone pays once).

Milestone rewards (MILESTONES) are given automatically and DM'd to the inviter.
Player command: rcinvites [@user]

Tables (economy.db, created automatically):
    invite_joins    member_id -> inviter_id, joined_ts, status (pending/counted/left/rejected), note
    invite_rewards  (user_id, milestone) rewards already paid
"""
from __future__ import annotations

import asyncio
import time

import discord
from discord.ext import commands, tasks

import economy
from bot_guard import ADMIN_GUILD_ID
from security import esc

MIN_ACCOUNT_DAYS = 30
STAY_DAYS = 0          # counts as soon as the new member has used rcdebut
CHECK_MINUTES = 2

# invites -> rewards. kind: "voucher" or a pack type (silver / gold / legendary)
MILESTONES: list[tuple[int, list[tuple[str, int]]]] = [
    (1,  [("voucher", 2),  ("silver", 2)]),
    (5,  [("voucher", 5),  ("silver", 2), ("gold", 1)]),
    (10, [("voucher", 10), ("silver", 3), ("gold", 2)]),
    (20, [("voucher", 20), ("silver", 10), ("gold", 5), ("legendary", 1)]),
]
_NAMES = {"voucher": "Draft Voucher", "silver": "Silver Pack", "gold": "Gold Pack", "legendary": "Legendary Pack"}


# ── database ─────────────────────────────────────────────────────────────────

def _db():
    conn = economy._conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS invite_joins (
        member_id INTEGER PRIMARY KEY, inviter_id INTEGER, joined_ts REAL,
        status TEXT NOT NULL DEFAULT 'pending', note TEXT, counted_ts REAL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_invite_joins_inviter ON invite_joins(inviter_id, status)")
    conn.execute("""CREATE TABLE IF NOT EXISTS invite_rewards (
        user_id INTEGER, milestone INTEGER, ts REAL, PRIMARY KEY (user_id, milestone))""")
    return conn


def record_join(member_id: int, inviter_id: int | None, status: str, note: str) -> bool:
    """False if this member already joined before (a rejoin never counts again)."""
    conn = _db()
    try:
        cur = conn.execute("INSERT OR IGNORE INTO invite_joins (member_id, inviter_id, joined_ts, status, note) "
                           "VALUES (?, ?, ?, ?, ?)", (member_id, inviter_id, time.time(), status, note))
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_left(member_id: int) -> int | None:
    """Member left: a pending / counted invite stops counting. Returns the inviter, if any."""
    conn = _db()
    try:
        row = conn.execute("SELECT inviter_id, status FROM invite_joins WHERE member_id = ?", (member_id,)).fetchone()
        if row is None or row["status"] not in ("pending", "counted"):
            return None
        conn.execute("UPDATE invite_joins SET status = 'left' WHERE member_id = ?", (member_id,))
        return row["inviter_id"]
    finally:
        conn.close()


def pending_ready() -> list:
    conn = _db()
    try:
        return conn.execute("SELECT member_id, inviter_id FROM invite_joins WHERE status = 'pending' AND joined_ts <= ?",
                            (time.time() - STAY_DAYS * 86400,)).fetchall()
    finally:
        conn.close()


def set_counted(member_id: int) -> None:
    conn = _db()
    try:
        conn.execute("UPDATE invite_joins SET status = 'counted', counted_ts = ? WHERE member_id = ? AND status = 'pending'",
                     (time.time(), member_id))
    finally:
        conn.close()


def stats(user_id: int) -> dict:
    conn = _db()
    try:
        rows = conn.execute("SELECT status, COUNT(*) n FROM invite_joins WHERE inviter_id = ? GROUP BY status",
                            (user_id,)).fetchall()
        paid = {r["milestone"] for r in conn.execute("SELECT milestone FROM invite_rewards WHERE user_id = ?", (user_id,))}
    finally:
        conn.close()
    by = {r["status"]: r["n"] for r in rows}
    return {"counted": by.get("counted", 0), "pending": by.get("pending", 0), "left": by.get("left", 0),
            "rejected": by.get("rejected", 0), "paid": paid}


def claim_milestones(user_id: int) -> list[int]:
    """Milestones reached and not paid yet; marks them paid (inside one transaction) and gives the items."""
    st = stats(user_id)
    due = [m for m, _ in MILESTONES if st["counted"] >= m and m not in st["paid"]]
    if not due or economy.get_user(user_id) is None:
        return []
    given = []
    conn = _db()
    try:
        for m in due:
            cur = conn.execute("INSERT OR IGNORE INTO invite_rewards (user_id, milestone, ts) VALUES (?, ?, ?)",
                               (user_id, m, time.time()))
            if cur.rowcount:
                given.append(m)
    finally:
        conn.close()
    for m in given:
        for kind, qty in dict(MILESTONES)[m]:
            if kind == "voucher":
                economy.add_vouchers(user_id, qty, f"Invite reward ({m} invites)")
            else:
                economy.add_pack(user_id, kind, qty)
    return given


def _reward_text(m: int) -> str:
    return "\n".join(f"• {q} {_NAMES[k]}{'s' if q > 1 else ''}" for k, q in dict(MILESTONES)[m])


# ── cog ──────────────────────────────────────────────────────────────────────

class InvitesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.uses: dict[str, int] = {}
        self.inviter_of: dict[str, int] = {}
        self._seeded = False

    async def cog_load(self):
        await asyncio.to_thread(lambda: _db().close())
        self.loop.start()

    async def cog_unload(self):
        self.loop.cancel()

    def _guild(self):
        return self.bot.get_guild(ADMIN_GUILD_ID)

    async def _snapshot(self) -> dict[str, int]:
        g = self._guild()
        if g is None:
            return {}
        try:
            invs = await g.invites()
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[invites] can't read invites (needs Manage Server): {e}")
            return {}
        for i in invs:
            if i.inviter:
                self.inviter_of[i.code] = i.inviter.id
        return {i.code: i.uses or 0 for i in invs}

    @commands.Cog.listener()
    async def on_ready(self):
        self.uses = await self._snapshot()
        if not self._seeded:
            self._seeded = True
            await self._seed_existing()

    async def _seed_existing(self):
        """Save every current member as 'existing' (once), so leave + rejoin never earns an invite."""
        g = self._guild()
        if g is None:
            return
        ids = []
        try:
            async for m in g.fetch_members(limit=None):
                ids.append(m.id)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[invites] could not list members: {e}")
            return

        def save():
            conn = _db()
            try:
                conn.executemany("INSERT OR IGNORE INTO invite_joins (member_id, inviter_id, joined_ts, status, note) "
                                 "VALUES (?, NULL, ?, 'existing', 'member before invite tracking')",
                                 [(i, time.time()) for i in ids])
            finally:
                conn.close()
        await asyncio.to_thread(save)
        print(f"[invites] {len(ids)} existing members saved (they can never count as invites)")

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild and invite.guild.id == ADMIN_GUILD_ID:
            self.uses[invite.code] = invite.uses or 0
            if invite.inviter:
                self.inviter_of[invite.code] = invite.inviter.id

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild and invite.guild.id == ADMIN_GUILD_ID:
            self.uses.pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild.id != ADMIN_GUILD_ID:
            return
        before = self.uses
        after = await self._snapshot()
        self.uses = after or before
        used = [c for c, n in after.items() if n > before.get(c, 0)]
        if not used:                                        # single-use invite that vanished after use
            used = [c for c in before if c not in after and c in self.inviter_of]
        inviter = self.inviter_of.get(used[0]) if len(used) == 1 else None
        if inviter is None:
            await asyncio.to_thread(record_join, member.id, None, "rejected", "inviter unknown")
            return
        age_days = (discord.utils.utcnow() - member.created_at).total_seconds() / 86400
        if member.bot:
            status, note = "rejected", "bot"
        elif inviter == member.id:
            status, note = "rejected", "self invite"
        elif age_days < MIN_ACCOUNT_DAYS:
            status, note = "rejected", f"account {age_days:.0f} days old"
        else:
            status, note = "pending", ""
        new = await asyncio.to_thread(record_join, member.id, inviter, status, note)
        print(f"[invites] {member.id} joined via {inviter}: {status if new else 'rejoin (ignored)'} {note}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.guild.id == ADMIN_GUILD_ID:
            await asyncio.to_thread(mark_left, member.id)

    @tasks.loop(minutes=CHECK_MINUTES)
    async def loop(self):
        g = self._guild()
        if g is None:
            return
        try:
            rows = await asyncio.to_thread(pending_ready)
            touched = set()
            for r in rows:
                if g.get_member(r["member_id"]) is None:
                    try:
                        await g.fetch_member(r["member_id"])
                    except discord.NotFound:
                        await asyncio.to_thread(mark_left, r["member_id"])
                        continue
                    except discord.HTTPException:
                        continue
                if await asyncio.to_thread(economy.get_user, r["member_id"]) is None:
                    continue                                 # not debuted yet: stays pending
                await asyncio.to_thread(set_counted, r["member_id"])
                touched.add(r["inviter_id"])
            # also retry inviters who reached a milestone before they had debuted themselves
            for uid in touched:
                await self._pay(uid)
        except Exception as e:
            print(f"[invites] check failed: {type(e).__name__}: {e}")

    @loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    async def _pay(self, user_id: int):
        given = await asyncio.to_thread(claim_milestones, user_id)
        if not given:
            return
        st = await asyncio.to_thread(stats, user_id)
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            for m in given:
                embed = discord.Embed(
                    title=f"🎉 {m} Invite{'s' if m > 1 else ''} Reward!",
                    description=(f"Thanks for growing Rush Cricket! You now have **{st['counted']}** invites.\n\n"
                                 f"**You received:**\n{_reward_text(m)}\n\nCheck your invites with `rcinvites`."),
                    color=discord.Color.gold())
                await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

    @commands.command(name="rcinvites", aliases=["rcinvite", "rcinv"])
    async def rcinvites(self, ctx: commands.Context, member: discord.Member | None = None):
        """Your invites and invite rewards."""
        member = member or ctx.author
        await self._pay(member.id)                           # safety: pays anything still due
        st = await asyncio.to_thread(stats, member.id)
        nxt = next((m for m, _ in MILESTONES if m > st["counted"]), None)
        lines = [f"✅ **Counted:** {st['counted']}",
                 f"⏳ **Pending:** {st['pending']}  (they still need to use `rcdebut`)",
                 f"🚪 **Left:** {st['left']}", ""]
        for m, _ in MILESTONES:
            mark = "✅" if m in st["paid"] else ("🔓" if st["counted"] >= m else "🔒")
            lines.append(f"{mark} **{m} invite{'s' if m > 1 else ''}:** " +
                         ", ".join(f"{q} {_NAMES[k]}" for k, q in dict(MILESTONES)[m]))
        if nxt:
            lines.append(f"\nNext reward in **{nxt - st['counted']}** more invite{'s' if nxt - st['counted'] > 1 else ''}.")
        embed = discord.Embed(title=f"{esc(member.display_name)}'s Invites", description="\n".join(lines),
                              color=discord.Color.blurple())
        embed.set_footer(text=f"New members only, accounts {MIN_ACCOUNT_DAYS}+ days old. Rejoins don't count. Each reward is given once.")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(InvitesCog(bot))
