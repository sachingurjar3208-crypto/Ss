"""Redeem codes.

Players:   rcredeem HAPPYDIWALI        (each player once per code)

Owner / admins (slash, only in your server, hidden from everyone else):
  /redeem create code reward1 [reward2] [reward3] [quantity]
        code      what players type (A-Z, 0-9, _ -), e.g. HAPPYDIWALI
        reward1-3 written like: 50000 coins | 2 vouchers | 100 rcp | 1 gold pack | card Virat Kohli
                  | random 85-89 | diwali theme      (suggestions appear while typing)
        quantity  how many players can redeem it in total (0 or empty = unlimited)
  /redeem status code active
        active True  = the code works (also turns a stopped code back on)
        active False = the code is fully off, nobody can redeem it
"""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import economy
import redeem
from bot_guard import ADMIN_GUILD_ID, MaintenanceActive, is_staff

REASON_TEXT = {
    "invalid": "❌ That code doesn't exist. Check the spelling.",
    "off": "❌ This code is not active.",
    "expired": "⌛ This code has expired.",
    "used_up": "❌ This code has been fully redeemed. Better luck next time!",
    "already": "✅ You already redeemed this code.",
    "not_debuted": "You haven't debuted yet. Type `rcdebut` first.",
    "banned": "❌ You can't redeem codes.",
}

EXAMPLES = ["50000 coins", "100000 coins", "2 vouchers", "5 vouchers", "100 rcp", "1 bronze pack", "1 silver pack",
            "1 gold pack", "1 legendary pack", "random 80-84", "random 85-89", "random 90-93", "card ",
            "diwali theme", "halloween theme", "christmas theme"]


async def _reward_autocomplete(interaction: discord.Interaction, current: str):
    cur = (current or "").strip().lower()
    if cur.startswith("card "):                      # card <player>: search the players
        import card_db
        q = cur[5:]
        out = []
        for c in card_db.list_all_cards():
            if q in c["playername"].lower():
                txt = f"card {c['playername']}"
                out.append(app_commands.Choice(name=f"{txt} · {c['ovr']} OVR"[:100], value=txt[:100]))
                if len(out) >= 25:
                    break
        return out
    picks = [e for e in EXAMPLES if not cur or cur in e] or ([current] if current else [])
    if current and current not in picks:
        picks.insert(0, current)                     # keep exactly what was typed (e.g. "75000 coins")
    return [app_commands.Choice(name=p.strip() or p, value=p.strip() or p) for p in picks][:25]


async def _code_autocomplete(interaction: discord.Interaction, current: str):
    cur = (current or "").upper()
    codes = await asyncio.to_thread(redeem.list_codes)
    return [app_commands.Choice(name=f"{d['code']} ({redeem.status(d)}, {d['used']} used)"[:100], value=d["code"])
            for d in codes if cur in d["code"]][:25]


class RedeemCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        def init():
            with economy._tx() as conn:
                redeem._ensure(conn)
        await asyncio.to_thread(init)

    # ── players ──────────────────────────────────────────────────────────────
    @commands.command(name="rcredeem")
    async def rcredeem(self, ctx: commands.Context, code: str | None = None):
        """Redeem a code: rcredeem HAPPYDIWALI"""
        if not code:
            await ctx.send("Type the code like this: `rcredeem HAPPYDIWALI`")
            return
        res = await asyncio.to_thread(redeem.redeem, ctx.author.id, code)
        if not res["ok"]:
            if res["reason"] == "used_up":
                n = res.get("max", 0)
                await ctx.send(f"❌ **Maximum users reached.** This code could be redeemed by only **{n:,}** "
                               f"player{'s' if n != 1 else ''} and all of them have claimed it. "
                               "It can't be claimed anymore.")
                return
            await ctx.send(REASON_TEXT.get(res["reason"], "❌ Couldn't redeem that code."))
            return
        embed = discord.Embed(title=f"🎁 Code {res['code']} redeemed!",
                              description="\n".join(res["lines"]) or "Nothing to give.",
                              color=discord.Color.from_rgb(240, 198, 104))
        embed.set_footer(text=f"Redeemed by {ctx.author.display_name}")
        await ctx.send(embed=embed)
        print(f"[redeem] {ctx.author.id} redeemed {res['code']}")
        if res["pending"]:
            try:
                from economy_cog import send_next_pending
                await send_next_pending(ctx, ctx.author.id)
            except Exception as e:
                print(f"[redeem] pending prompt failed: {type(e).__name__}: {e}")

    # ── owner / admins ───────────────────────────────────────────────────────
    group = app_commands.Group(name="redeem", description="Redeem codes (owner)", guild_only=True,
                               default_permissions=discord.Permissions())
    app_commands.guilds(ADMIN_GUILD_ID)(group)

    @group.command(name="create", description="Make a redeem code")
    @app_commands.describe(
        code="What players type, e.g. HAPPYDIWALI",
        reward1="e.g. 50000 coins / 2 vouchers / 1 gold pack / card Virat Kohli / random 85-89 / diwali theme",
        reward2="(optional) a second reward",
        reward3="(optional) a third reward",
        quantity="How many players can redeem it (0 or empty = unlimited)",
    )
    @app_commands.autocomplete(reward1=_reward_autocomplete, reward2=_reward_autocomplete,
                               reward3=_reward_autocomplete)
    @app_commands.check(lambda i: is_staff(i.user))
    async def r_create(self, interaction: discord.Interaction, code: str, reward1: str,
                       reward2: str | None = None, reward3: str | None = None,
                       quantity: app_commands.Range[int, 0, 1_000_000] = 0):
        rewards = []
        for text in (reward1, reward2, reward3):
            if not text or not text.strip():
                continue
            r, err = await asyncio.to_thread(redeem.parse_reward_text, text)
            if r is None:
                await interaction.response.send_message(f"❌ {err}", ephemeral=True)
                return
            rewards.append(r)
        err = await asyncio.to_thread(redeem.create, code, rewards, quantity, 0, interaction.user.id)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        c = redeem.norm_code(code)
        lim = f"{quantity} players" if quantity else "unlimited players"
        await interaction.response.send_message(
            f"✅ Code **{c}** is live.\nRewards: " + " + ".join(redeem.describe(r) for r in rewards)
            + f"\nLimit: {lim}\nPlayers type: `rcredeem {c}`", ephemeral=True)

    @group.command(name="status", description="Turn a code on (True) or fully off (False)")
    @app_commands.describe(code="The code", active="True = works / turn back on · False = fully off")
    @app_commands.autocomplete(code=_code_autocomplete)
    @app_commands.check(lambda i: is_staff(i.user))
    async def r_status(self, interaction: discord.Interaction, code: str, active: bool):
        d = await asyncio.to_thread(redeem.get, code)
        if d is None:
            await interaction.response.send_message(f"❌ No code `{redeem.norm_code(code)}`.", ephemeral=True)
            return
        await asyncio.to_thread(redeem.set_active, code, active)
        lim = f"{d['used']}/{d['max_uses']}" if d["max_uses"] else f"{d['used']} (no limit)"
        txt = "▶️ is **on**, players can redeem it." if active else "⛔ is **off**, nobody can redeem it."
        await interaction.response.send_message(f"**{d['code']}** {txt}\nRedeemed so far: {lim}", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, MaintenanceActive):
            return
        msg = "Only admins can use this." if isinstance(error, app_commands.CheckFailure) else "Something went wrong."
        if not isinstance(error, app_commands.CheckFailure):
            print(f"[redeem] command error: {type(error).__name__}: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RedeemCog(bot))
