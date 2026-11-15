"""RCP store: the premium currency (no real-money prices are shown in Discord).

Players
    rcstore                      the store: your RCP and what RCP buys
    rcstore buy <item>           spend RCP: premium | starpass | vouchers | legendary
    rcbal                        (economy_cog) also shows RCP

RCP is the SAME wallet as the Signature Box RC Points (sigbox.py). Staff adds or removes it with
the existing `admin rcp give | remove | check` (admin_cog), players see it with `rcp` and rcbal.

Premium members get RCP_PREMIUM_MONTHLY (500) free RCP every 30 days: given when the role
arrives, and again whenever they open rcstore / rcbal once 30 days have passed.
Premium bought with RCP lasts 30 days (stacks); a loop takes the role away when it ends.
Premium given by staff by hand has no end date here and is never removed by the bot.
"""
from __future__ import annotations

import asyncio

import discord
from discord import ui
from discord.ext import commands, tasks

import economy
import emojis
import security
import starpass
from bot_guard import ADMIN_GUILD_ID
from security import OwnedView, esc
from squad_cog import PREMIUM_ROLE_ID, _is_premium, _need_debut


# Real-money prices are never shown inside Discord: players get RCP through a ticket and staff
# adds it with `admin rcp give`.

# What RCP buys. give: "role:premium" | "role:starpass" | "vouchers" | "pack:<type>"
ITEMS = {
    "premium":   {"name": "Premium Membership (30 days)", "cost": 5_000, "give": "role:premium",
                  "info": "Claim every 35 min, weekly reward, no cooldowns, rcautoplay and rcdata, plus 500 RCP every month"},
    "starpass":  {"name": "Star Pass (this season)", "cost": 4_500, "give": "role:starpass",
                  "info": "Premium reward row and 1,000 bonus Star Pass credits"},
    "vouchers":  {"name": "10 Draft Vouchers", "cost": 1_500, "give": "vouchers", "qty": 10,
                  "info": "One full 10-draft pull in rcdraft"},
    "legendary": {"name": "Legendary Pack", "cost": 1_300, "give": "pack:legendary",
                  "info": "One 90 to 95 OVR card, open it with rcopen"},
}
ALIASES = {"prem": "premium", "membership": "premium", "pass": "starpass", "star": "starpass",
           "voucher": "vouchers", "10vouchers": "vouchers", "legend": "legendary", "pack": "legendary"}

RCP_COLOR = discord.Color.from_rgb(30, 170, 110)


def fmt_rcp(n: int) -> str:
    return f"{int(n):,} {emojis.RCP}"


async def premium_bonus(member) -> int:
    """Give the monthly Premium RCP if it's due. Returns how much was given."""
    if not _is_premium(member):
        return 0
    try:
        return await asyncio.to_thread(economy.rcp_premium_bonus, member.id)
    except Exception as e:
        print(f"[rcp] premium bonus failed for {member.id}: {type(e).__name__}: {e}")
        return 0


def _item_emoji(key: str) -> str:
    return {"premium": emojis.PREMIUM, "starpass": emojis.STAR_PASS,
            "vouchers": emojis.VOUCHER, "legendary": emojis.LEGENDARY_PACK}.get(key, emojis.RCP)


def _store_embed(balance: int, bonus: int = 0) -> discord.Embed:
    e = discord.Embed(title=f"{emojis.CART}  RUSH CRICKET STORE", color=RCP_COLOR)
    lines = [f"### {emojis.RCP} Your balance: {balance:,} RCP"]
    if bonus:
        lines.append(f"{emojis.GIFT} Premium monthly bonus added: **+{bonus:,}** {emojis.RCP}")
    lines.append("")
    for key, it in ITEMS.items():
        can = emojis.TICK if balance >= it["cost"] else emojis.LOCK
        lines.append(f"{_item_emoji(key)} **{it['name']}**\n"
                     f"{can} **{it['cost']:,}** {emojis.RCP}  {emojis.ARROW}  `rcstore buy {key}`\n"
                     f"-# {it['info']}")
        lines.append("")
    lines.append(f"{emojis.TICKET} **How to get RCP**\nOpen a ticket in the server.\n"
                 f"{emojis.GIFT} Premium members get **500** {emojis.RCP} free every month.")
    e.description = "\n".join(lines)
    e.set_footer(text="Green tick: you can buy it now  ·  Lock: you need more RCP")
    return e


class _BuyView(OwnedView):
    def __init__(self, cog: "RCPCog", member: discord.Member, key: str):
        super().__init__(member.id, timeout=60)
        self.cog, self.member, self.key, self.used = cog, member, key, False
        try:
            self.confirm.emoji = discord.PartialEmoji.from_str(emojis.CART)
        except Exception:
            pass

    @ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if self.used:
            await interaction.response.defer()
            return
        self.used = True
        self.stop()
        await interaction.response.defer()
        text = await self.cog.do_buy(self.member, self.key)
        await interaction.edit_original_response(content=text, embed=None, view=None)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self.used = True
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


class RCPCog(commands.Cog, name="RCP"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._buying: set[int] = set()
        self.expire_loop.start()

    def cog_unload(self):
        self.expire_loop.cancel()

    # -- players ---------------------------------------------------------------
    @commands.group(name="rcstore", aliases=["rcshop", "store"], invoke_without_command=True)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcstore(self, ctx: commands.Context):
        """The RCP store."""
        if not await _need_debut(ctx):
            return
        bonus = await premium_bonus(ctx.author)
        bal = await asyncio.to_thread(economy.get_rcp, ctx.author.id)
        await ctx.send(embed=_store_embed(bal, bonus))

    @rcstore.command(name="buy")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcstore_buy(self, ctx: commands.Context, *, item: str = ""):
        if not await _need_debut(ctx):
            return
        key = item.strip().lower().replace(" ", "")
        key = ALIASES.get(key, key)
        it = ITEMS.get(key)
        if it is None:
            await ctx.send("❌ Pick one: `premium`, `starpass`, `vouchers` or `legendary`. Example: `rcstore buy vouchers`")
            return
        if ctx.guild is None or ctx.guild.id != ADMIN_GUILD_ID:
            if it["give"].startswith("role:"):
                await ctx.send("❌ Buy Premium and Star Pass in the main Rush Cricket server (the role lives there).")
                return
        if it["give"] == "role:starpass" and self._has_starpass(ctx.author):
            await ctx.send("✅ You already have this season's Star Pass.")
            return
        bal = await asyncio.to_thread(economy.get_rcp, ctx.author.id)
        if bal < it["cost"]:
            await ctx.send(f"❌ **{it['name']}** costs **{fmt_rcp(it['cost'])}**. You have **{fmt_rcp(bal)}**. "
                           f"Open a ticket to buy RCP.")
            return
        view = _BuyView(self, ctx.author, key)
        view.message = await ctx.send(
            f"{_item_emoji(key)} Buy **{it['name']}** for **{fmt_rcp(it['cost'])}**?\n"
            f"You have **{fmt_rcp(bal)}**, after this **{fmt_rcp(bal - it['cost'])}**.", view=view)

    def _starpass_role_id(self) -> int:
        try:
            season = starpass.get_active_season()
            if season and season.get("premium_role"):
                return int(season["premium_role"])
        except Exception:
            pass
        return starpass.STARPASS_PREMIUM_ROLE_ID

    def _has_starpass(self, member) -> bool:
        rid = self._starpass_role_id()
        return any(r.id == rid for r in getattr(member, "roles", []))

    async def do_buy(self, member: discord.Member, key: str) -> str:
        it = ITEMS[key]
        if member.id in self._buying:
            return "❌ Another purchase is still running."
        self._buying.add(member.id)
        try:
            give = it["give"]
            res = await asyncio.to_thread(economy.rcp_buy, member.id, it["cost"], it["name"],
                                          "none" if give.startswith("role:") else give, it.get("qty", 1))
            if not res["ok"]:
                if res["reason"] == "funds":
                    return f"❌ Not enough RCP. You have **{fmt_rcp(res['balance'])}**."
                return "❌ You need to debut first."
            if give.startswith("role:"):
                guild = self.bot.get_guild(ADMIN_GUILD_ID)
                rid = PREMIUM_ROLE_ID if give == "role:premium" else self._starpass_role_id()
                role = guild.get_role(rid) if guild else None
                gm = guild.get_member(member.id) if guild else None
                try:
                    if role is None or gm is None:
                        raise RuntimeError("role or member not found")
                    if role not in gm.roles:
                        await gm.add_roles(role, reason=f"RCP store: {it['name']}")
                except Exception as e:
                    print(f"[rcp] role give failed for {member.id}: {type(e).__name__}: {e}")
                    await asyncio.to_thread(economy.rcp_refund, member.id, it["cost"], it["name"])
                    return "❌ Couldn't give the role, so your RCP was refunded. Please open a ticket."
                if give == "role:premium":
                    end = await asyncio.to_thread(economy.rcp_premium_extend, member.id, 30)
                    bonus = await asyncio.to_thread(economy.rcp_premium_bonus, member.id)
                    extra = f"\n{emojis.GIFT} Monthly bonus: **+{bonus:,}** {emojis.RCP}" if bonus else ""
                    bal = await asyncio.to_thread(economy.get_rcp, member.id)
                    return (f"{emojis.PREMIUM} **Premium** is active until <t:{int(end)}:D>.{extra}\n"
                            f"RCP left: **{fmt_rcp(bal)}**")
            return f"{emojis.CART} Bought {_item_emoji(key)} **{it['name']}**. RCP left: **{fmt_rcp(res['balance'])}**"
        finally:
            self._buying.discard(member.id)

    # -- Premium: monthly RCP when the role arrives ----------------------------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot or _is_premium(before) or not _is_premium(after):
            return
        if not await asyncio.to_thread(economy.user_exists, after.id):
            return
        got = await premium_bonus(after)
        if got:
            try:
                await after.send(f"{emojis.PREMIUM} Your Premium is active. {emojis.GIFT} You received **{got:,} RCP** for this month.")
            except discord.HTTPException:
                pass

    # -- Premium bought with RCP ends after 30 days ----------------------------
    @tasks.loop(minutes=30)
    async def expire_loop(self):
        ids = await asyncio.to_thread(economy.rcp_premium_expired)
        if not ids:
            return
        guild = self.bot.get_guild(ADMIN_GUILD_ID)
        role = guild.get_role(PREMIUM_ROLE_ID) if guild else None
        for uid in ids:
            m = guild.get_member(uid) if guild else None
            if m is None or role is None or role not in m.roles:
                continue
            try:
                await m.remove_roles(role, reason="RCP Premium ended")
                await m.send("Your Premium has ended. Renew it any time with `rcstore buy premium`.")
            except discord.HTTPException as e:
                print(f"[rcp] premium end failed for {uid}: {e}")

    @expire_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(RCPCog(bot))
