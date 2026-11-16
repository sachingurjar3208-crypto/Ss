"""Undo.

Players
    rcundo                    take back your LAST sell (rcsell / rcms) within 5 minutes.
                              The cards come back to your bench and the coins that sell paid are taken
                              away. Not possible if you already spent those coins, or during an auction.

Owner only (main server)
    admin undo @user 2h       show everything that changed for that player in the last 2 hours
                              (30m, 2h, 1d ... max 7d), then Confirm to roll it all back:
                              cards that came in go out, cards that went out come back, coins,
                              vouchers and packs go back to what they were. Trades are NOT touched
                              (the other player is involved): they are listed so you can check them.
                              From the moment the preview shows until you confirm or cancel, the
                              player is LOCKED: no coin / card / voucher change can happen for them.
"""
from __future__ import annotations

import asyncio
import re
import time

import discord
from discord import ui
from discord.ext import commands

import card_db
import economy
import emojis
from admin_cog import owner_only
from security import esc

LOCK_SECONDS = 120
MAX_WINDOW = 7 * 86400
_UNIT = {"m": 60, "h": 3600, "d": 86400}


def _name(key: str) -> str:
    try:
        c = card_db.get_card(key)
        return c["playername"] if c else key
    except Exception:
        return key


def _parse_window(text: str) -> int | None:
    m = re.fullmatch(r"\s*(\d+)\s*([mhd])\s*", text.lower())
    if not m:
        return None
    secs = int(m.group(1)) * _UNIT[m.group(2)]
    return secs if 0 < secs <= MAX_WINDOW else None


def _list(d: dict[str, int], limit: int = 12) -> str:
    items = sorted(d.items(), key=lambda kv: -kv[1])
    out = [f"{esc(_name(k))}" + (f" x{n}" if n > 1 else "") for k, n in items[:limit]]
    if len(items) > limit:
        out.append(f"... and {len(items) - limit} more")
    return ", ".join(out) or "none"


class _ConfirmUndo(ui.View):
    def __init__(self, cog: "UndoCog", owner_id: int, target: discord.Member, since: float):
        super().__init__(timeout=LOCK_SECONDS - 5)
        self.cog, self.owner_id, self.target, self.since = cog, owner_id, target, since
        self.message: discord.Message | None = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the owner who started this undo can use these buttons.",
                                                    ephemeral=True)
            return False
        return True

    def _finish(self):
        self.done = True
        self.stop()
        economy.UNDO_LOCK.pop(self.target.id, None)

    @ui.button(label="Confirm undo", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if self.done:
            await interaction.response.defer()
            return
        self.done = True
        await interaction.response.edit_message(content="Rolling back...", view=None)
        try:
            res = await asyncio.to_thread(economy.undo_apply, self.target.id, self.since, interaction.user.id)
        except Exception as e:
            self._finish()
            await interaction.edit_original_response(content=f"Undo failed, nothing was changed: {type(e).__name__}: {e}")
            return
        self._finish()
        lines = [f"{emojis.TICK} **Rolled back {esc(self.target.display_name)}**"]
        if res["removed"]:
            lines.append(f"Cards taken out: {_list(_count(res['removed']))}")
        if res["returned"]:
            lines.append(f"Cards given back: {_list(_count(res['returned']))}")
        if res["coins"]:
            lines.append(f"Coins: {'+' if res['coins'] > 0 else ''}{res['coins']:,}")
        if res["vouchers"]:
            lines.append(f"Vouchers: {'+' if res['vouchers'] > 0 else ''}{res['vouchers']:,}")
        if res["missing"]:
            lines.append(f"Not found any more (traded away?): {_list(_count(res['missing']))}")
        if res["trades"]:
            lines.append(f"Trades left as they were: {res['trades']}")
        lines.append(f"Purse now: **{res['purse']:,}**" + ("  (in debt: they can't spend until it's above 0)"
                                                           if res["purse"] < 0 else ""))
        await interaction.edit_original_response(content="\n".join(lines))
        print(f"[undo] owner {interaction.user.id} rolled back {self.target.id} since {int(self.since)}: {res}")

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self._finish()
        await interaction.response.edit_message(content="Undo cancelled. Nothing was changed.", embed=None, view=None)

    async def on_timeout(self):
        if not self.done:
            self._finish()
            if self.message:
                try:
                    await self.message.edit(content="Undo timed out. Nothing was changed.", embed=None, view=None)
                except discord.HTTPException:
                    pass


def _count(keys: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in keys:
        out[k] = out.get(k, 0) + 1
    return out


class UndoCog(commands.Cog, name="Undo"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._attached = False

    async def cog_load(self):
        self.bot.add_check(self._locked_check)
        admin = self.bot.get_command("admin")
        if isinstance(admin, commands.Group):
            cog = self

            async def undo_cmd(ctx: commands.Context, member: discord.Member = None, window: str = ""):
                await cog._admin_undo(ctx, member, window)
            admin.add_command(commands.Command(owner_only()(undo_cmd), name="undo"))
            self._attached = True
        else:
            print("[undo] admin group not found; load admin_cog before undo_cog")

    def cog_unload(self):
        self.bot.remove_check(self._locked_check)
        admin = self.bot.get_command("admin")
        if self._attached and isinstance(admin, commands.Group):
            admin.remove_command("undo")

    async def _locked_check(self, ctx: commands.Context) -> bool:
        if economy.is_undo_locked(ctx.author.id):
            await ctx.send("Your account is being checked by the owner. Try again in a minute.")
            raise commands.CheckFailure("undo lock")
        return True

    # ── players ──────────────────────────────────────────────────────────────
    @commands.command(name="rcundo")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rcundo(self, ctx: commands.Context):
        """Take back your last sell (within 5 minutes)."""
        res = await asyncio.to_thread(economy.undo_last_sell, ctx.author.id)
        if res["ok"]:
            names = _list(_count(res["cards"]))
            await ctx.send(f"{emojis.TICK} Undone. **{names}** {'is' if len(res['cards']) == 1 else 'are'} back on your bench "
                           f"and **{economy.fmt_coins(res['coins'])}** was taken back.\nUse `rcswap` to put them in your XI.")
            return
        reason = res["reason"]
        if reason == "auction":
            await ctx.send("❌ You can't undo during an auction.")
        elif reason == "coins":
            await ctx.send(f"❌ You already spent those coins. Undo needs **{economy.fmt_coins(res['need'])}**, "
                           f"you have **{economy.fmt_coins(res['have'])}**.")
        elif reason == "squad":
            await ctx.send(f"❌ Your squad is full ({res['limit']}). Sell something else first.")
        else:
            mins = economy.UNDO_SELL_SECONDS // 60
            await ctx.send(f"❌ Nothing to undo. Only your last sell from the last {mins} minutes can be undone.")

    # ── owner ────────────────────────────────────────────────────────────────
    async def _admin_undo(self, ctx: commands.Context, member: discord.Member = None, window: str = ""):
        from bot_guard import ADMIN_GUILD_ID
        if ctx.guild is None or ctx.guild.id != ADMIN_GUILD_ID:
            return
        secs = _parse_window(window) if member else None
        if member is None or secs is None:
            await ctx.send("Usage: `admin undo @user 2h`  (30m, 2h, 1d ... up to 7d)")
            return
        if member.bot or member.id == ctx.author.id:
            await ctx.send("Pick another player.")
            return
        if economy.is_undo_locked(member.id):
            await ctx.send("An undo for this player is already open.")
            return
        history = await asyncio.to_thread(economy.undo_history_since)
        since = time.time() - secs
        note = ""
        if since < history:
            since = history
            note = f"\n-# History only exists since <t:{int(history)}:f>, so the undo starts there."
        economy.UNDO_LOCK[member.id] = time.time() + LOCK_SECONDS     # lock BEFORE reading
        try:
            p = await asyncio.to_thread(economy.undo_preview, member.id, since)
        except Exception as e:
            economy.UNDO_LOCK.pop(member.id, None)
            await ctx.send(f"Couldn't read the history: {type(e).__name__}: {e}")
            return
        nothing = not (p["gained"] or p["lost"] or p["coins"] or p["vouchers"] or p["packs"])
        if nothing:
            economy.UNDO_LOCK.pop(member.id, None)
            extra = f"\n{p['trades']} trade(s) happened, but trades are never undone." if p["trades"] else ""
            await ctx.send(f"Nothing to undo for {esc(member.display_name)} since <t:{int(since)}:f>.{extra}{note}")
            return
        e = discord.Embed(title=f"Undo {member.display_name}?", color=discord.Color.orange())
        e.description = f"Everything since <t:{int(since)}:f> (<t:{int(since)}:R>) will be rolled back.{note}"
        if p["gained"]:
            e.add_field(name="Cards that came in (will be taken out)", value=_list(p["gained"]), inline=False)
        if p["lost"]:
            e.add_field(name="Cards that went out (will come back)", value=_list(p["lost"]), inline=False)
        if p["coins"]:
            after = p["purse"] - p["coins"]
            e.add_field(name="Coins", value=f"{'-' if p['coins'] > 0 else '+'}{abs(p['coins']):,}  "
                                            f"(purse {p['purse']:,} -> {after:,})", inline=False)
        if p["vouchers"]:
            e.add_field(name="Vouchers", value=f"{'-' if p['vouchers'] > 0 else '+'}{abs(p['vouchers']):,}", inline=True)
        if p["packs"]:
            e.add_field(name="Packs", value=", ".join(f"{k} {'-' if v > 0 else '+'}{abs(v)}" for k, v in p["packs"].items()),
                        inline=True)
        if p["trades"]:
            who = ", ".join(f"<@{u}>" for u in p["trade_partners"]) or "?"
            e.add_field(name="Trades (NOT undone)", value=f"{p['trades']} trade(s) with {who}. Check them by hand.",
                        inline=False)
        e.set_footer(text=f"{member.display_name} is locked until you confirm or cancel ({LOCK_SECONDS}s). "
                          "RCP and Star Pass credits are not changed.")
        view = _ConfirmUndo(self, ctx.author.id, member, since)
        view.message = await ctx.send(embed=e, view=view, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(UndoCog(bot))
