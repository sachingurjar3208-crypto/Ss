"""Career Mode commands. The career player grows only from real matches (rcmp ranked / rcmp @user).

  rccareer start <name>   make your own cricketer (pick Batter / Bowler / All-rounder)
  rccareer [@user]        the career player card: OVR, stats, XP, stamina, stage
  rccareer top            top 10 career players
  rcdrink                 your drinks      rcdrink water|energy|power   drink one
  rcdrink buy <drink> [n] buy Water (100,000) / Energy Drink (500,000)
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import discord
from discord import ui
from discord.ext import commands

import career as cr
import career_card
import economy
import security
from security import OwnedView, esc

GREEN = discord.Color.from_rgb(46, 204, 113)
CARD_COOLDOWN = 20                   # seconds between career card images per user
_card_used: dict[int, float] = {}
NAMES = {"bat": "BAT", "bowl": "BOWL", "fit": "FITNESS"}


MANNEQUIN = Path(__file__).parent / "career_mannequin.png"


async def refresh_card(uid: int, force: bool = False) -> dict | None:
    """Create / update the career player's card and redraw its image when the stats changed.
    Never raises. Returns career.sync_card()'s result."""
    try:
        res = await asyncio.to_thread(cr.sync_card, uid)
        if not res or not (res["render"] or force):
            return res
        import card_cache
        import card_db
        import card_image
        from card_style2 import build_card_style2
        from PIL import Image

        def draw():
            card = card_db.get_card(res["key"])
            fg = Image.open(MANNEQUIN).convert("RGBA")
            try:                                     # so any other redraw of this card finds the photo
                local = card_image._foreground_file(cr.FOREGROUND_LINK)
                if not local.exists():
                    local.parent.mkdir(parents=True, exist_ok=True)
                    fg.save(local, "PNG")
            except Exception:
                pass
            buf = build_card_style2(card, fg)
            card_db.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
            out = card_db.GENERATED_DIR / f"career_{uid}.png"
            out.write_bytes(buf.getvalue())
            card_db.set_card_image_path(card["playername"], str(out))
            return str(out)

        path = await asyncio.to_thread(draw)
        await card_cache.warm(path)
        return res
    except Exception as e:
        print(f"[career] card refresh failed for {uid}: {type(e).__name__}: {e}")
        return None


def bar(v: float, n: int = 10) -> str:
    full = int(round(max(0.0, min(100.0, v)) / 100 * n))
    return "▰" * full + "▱" * (n - full)


class RolePick(OwnedView):
    def __init__(self, uid: int, name: str):
        super().__init__(uid, timeout=90)
        self.name = name
        for key, label in cr.ROLES.items():
            b = ui.Button(label=label, style=discord.ButtonStyle.primary)
            b.callback = self._mk(key)
            self.add_item(b)

    def _mk(self, role):
        async def cb(interaction: discord.Interaction):
            err = await asyncio.to_thread(cr.create, interaction.user.id, self.name, role)
            self.stop()
            if err:
                return await interaction.response.edit_message(content=f"❌ {err}", embed=None, view=None)
            p = await asyncio.to_thread(cr.get, interaction.user.id)
            e = profile_embed(p, interaction.user)
            e.title = f"{esc(self.name)} starts at Club Cricket"
            await interaction.response.edit_message(
                content="He gets stronger every time you play `rcmp ranked` or `rcmp @user`. "
                        "You got 3 Water and 1 Energy Drink to start.\n"
                        "His card is now in your squad (`rcsquad`). Put him in your XI with `rcswap`.",
                embed=e, view=None)
            await refresh_card(interaction.user.id)
        return cb


def profile_embed(p: dict, user) -> discord.Embed:
    s = cr.stamina_now(p)
    o = cr.ovr(p)
    e = discord.Embed(title=f"{esc(p['name'])} · {cr.ROLES[p['role']]}", color=GREEN)
    e.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    lines = [f"**OVR {o}**  ·  {cr.STAGES[p['stage']][0]}"]
    stats = ["bat", "bowl", "fit"] if p["role"] == "ar" else [p["role"], "fit"]
    for st in stats:
        need = cr.xp_need(p[st])
        lines.append(f"{NAMES[st]} **{p[st]}**  {bar(p['xp_' + st] * 100 / need, 8)}  {p['xp_' + st]}/{need} XP")
    lines.append(f"Stamina {bar(s)} **{s:.0f}/100**  (+{cr.regen_rate(p['fit']):.1f}/hour)")
    lines.append(f"Career XP today: **{p.get('xp_today', 0)}/{cr.DAILY_XP_CAP}**")
    nxt = next(((n, need) for i, (n, need, _) in enumerate(cr.STAGES) if i > p["stage"]), None)
    if nxt:
        lines.append(f"Next stage **{nxt[0]}** at OVR {nxt[1]}")
    e.description = "\n".join(lines)
    e.add_field(name="Record", value=f"{p['matches']} matches · {p['wins']} won")
    dr = p.get("drinks", {})
    e.add_field(name=f"Drinks (today {p.get('drinks_today', 0)}/{cr.DRINKS_PER_DAY})",
                value=" · ".join(f"{d['label']} {dr.get(k, 0)}" for k, d in cr.DRINKS.items()), inline=False)
    e.set_footer(text=f"Each rcmp ranked / rcmp @user match: -{cr.MATCH_COST} stamina, "
                      f"+{cr.XP_PLAY} XP (+{cr.XP_WIN} for a win)")
    return e


async def announce(channel, game) -> None:
    """After a real match: one short line per player who has a career player. Never raises."""
    try:
        res = game.__dict__.get("career_results")
        if not res or game.__dict__.get("_career_announced"):
            return
        game._career_announced = True
        lines = []
        for who in (game.challenger, game.opponent):
            r = res.get(getattr(who, "id", None))
            if not r:
                continue
            sk = r.get("skipped")
            if sk == "tired":
                lines.append(f"{who.mention}: career player too tired ({r['stamina']:.0f}/100 stamina), "
                             f"no XP. `rcdrink`")
                continue
            if sk == "cap":
                lines.append(f"{who.mention}: daily career XP done ({cr.DAILY_XP_CAP}/{cr.DAILY_XP_CAP}).")
                continue
            if sk == "opp_cap":
                lines.append(f"{who.mention}: no career XP, already {cr.PER_OPP_CAP} matches vs this player today.")
                continue
            if r["ups"] or r["stage_up"]:
                await refresh_card(who.id)           # his card in the squad grows with him
            txt = f"{who.mention}: **{esc(r['name'])}** +{r['xp']} XP"
            if r.get("bonus"):
                txt += " (" + ", ".join(r["bonus"]) + ")"
            if r["ups"]:
                txt += "  ·  " + ", ".join(f"{NAMES[k]} +{v}" for k, v in r["ups"].items())
            if r["ovr_up"] > 0:
                txt += f"  ·  **OVR {r['ovr']}**"
            txt += f"  ·  stamina {r['stamina']:.0f}"
            if r["stage_up"]:
                txt += f"\n**Promoted to {r['stage_up']}!** +{r['coins']:,} coins"
            lines.append(txt)
        if lines:
            await channel.send(embed=discord.Embed(title="Career", description="\n".join(lines), color=GREEN))
    except Exception as e:
        print(f"[career] announce failed: {type(e).__name__}: {e}")


class CareerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(cr.init)

    async def _need_debut(self, ctx) -> bool:
        if await asyncio.to_thread(economy.user_exists, ctx.author.id):
            return True
        await ctx.send("❌ You haven't debuted yet. Type `rcdebut` first.")
        return False

    @commands.command(name="rccareer")
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def rccareer(self, ctx: commands.Context, sub: str | None = None, *, rest: str | None = None):
        """Career Mode: rccareer [start <name> | @user]"""
        if not await self._need_debut(ctx):
            return
        uid = ctx.author.id
        if (sub or "").lower() == "start":
            name, err = security.clean_team_name(rest or "")
            if err:
                return await ctx.send("Give your cricketer a name: `rccareer start <name>` (3-24 letters).")
            if await asyncio.to_thread(cr.get, uid):
                return await ctx.send("You already have a career player. Type `rccareer`.")
            import card_db
            if await asyncio.to_thread(card_db.get_card, name) is not None:
                return await ctx.send("❌ That name is already used by a card. Pick another name for your cricketer.")
            v = RolePick(uid, name)
            e = discord.Embed(title=f"Create {esc(name)}", color=GREEN, description=(
                "Pick his role:\n**Batter**: BAT 62 · BOWL 35\n**Bowler**: BAT 35 · BOWL 62\n"
                "**All-rounder**: BAT 55 · BOWL 55 (grows both, gets x1.5 XP)\n"
                "Everyone starts with FITNESS 55.\n\nHe grows only when you play `rcmp ranked` or `rcmp @user`. "
                "It is slow on purpose: a top player (OVR 85) takes months."))
            v.message = await ctx.send(embed=e, view=v)
            return
        if (sub or "").lower() in ("top", "lb", "leaderboard"):
            rows = await asyncio.to_thread(cr.top, 10)
            if not rows:
                return await ctx.send("No career players yet. Be the first: `rccareer start <name>`")
            lines = [f"`{i:>2}` **{esc(p['name'])}** · OVR **{cr.ovr(p)}** · {cr.STAGES[p['stage']][0]} · "
                     f"<@{p['user_id']}>" for i, p in enumerate(rows, 1)]
            me = await asyncio.to_thread(cr.rank_of, uid)
            e = discord.Embed(title="Career · Top players", description="\n".join(lines), color=GREEN)
            if me:
                e.set_footer(text=f"Your rank: #{me}")
            return await ctx.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
        target = ctx.author
        if ctx.message.mentions:
            target = ctx.message.mentions[0]
        p = await asyncio.to_thread(cr.get, target.id)
        if not p:
            if target.id != uid:
                return await ctx.send(f"**{esc(target.display_name)}** has no career player yet.")
            return await ctx.send(
                "**Career Mode**: make your own cricketer. He grows every time you play `rcmp ranked` "
                "or `rcmp @user`.\nStart: `rccareer start <name>`")
        if target.id == uid:
            await refresh_card(uid)                  # makes his squad card if he has none yet
        left = CARD_COOLDOWN - (time.monotonic() - _card_used.get(uid, -1e9))
        if left > 0:
            return await ctx.send(f"⏳ The career card is on cooldown. Try again in **{left:.0f}s**.",
                                  delete_after=8)
        _card_used[uid] = time.monotonic()
        if len(_card_used) > 5000:
            _card_used.clear()
        try:                                             # image card; falls back to the text embed
            try:
                avatar = await target.display_avatar.with_size(256).read()
            except Exception:
                avatar = None
            buf = await asyncio.to_thread(career_card.render, p, target.display_name, avatar)
            rank = await asyncio.to_thread(cr.rank_of, target.id)
            tip = (f"Rank #{rank} · " if rank else "") + \
                f"Each `rcmp ranked` / `rcmp @user` match: -{cr.MATCH_COST} stamina, +{cr.XP_PLAY} XP " \
                f"(+{cr.XP_WIN} win) · `rccareer top` · `rcdrink`"
            if target.id == uid:
                tip += f"\nHis card is in your squad: `rcview {p['name']}`. Put him in your XI with `rcswap`."
            await ctx.send(tip, file=discord.File(buf, "career.png"))
        except Exception as e:
            print(f"[career] card failed: {type(e).__name__}: {e}")
            await ctx.send(embed=profile_embed(p, target))

    @commands.command(name="rcdrink")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def rcdrink(self, ctx: commands.Context, a: str | None = None, b: str | None = None, n: int = 1):
        """rcdrink [water|energy|power] · rcdrink buy <drink> [amount]"""
        if not await self._need_debut(ctx):
            return
        uid = ctx.author.id
        a = (a or "").lower()
        if a == "buy":
            drink = (b or "").lower()
            n = max(1, min(10, n))
            err = await asyncio.to_thread(cr.buy_drink, uid, drink, n)
            if err:
                return await ctx.send(f"❌ {err}")
            return await ctx.send(f"Bought **{n} x {cr.DRINKS[drink]['label']}**.")
        if a in cr.DRINKS:
            err, new = await asyncio.to_thread(cr.use_drink, uid, a)
            if err:
                return await ctx.send(f"❌ {err}")
            return await ctx.send(f"You had a **{cr.DRINKS[a]['label']}**. Stamina {bar(new)} **{new:.0f}/100**")
        p = await asyncio.to_thread(cr.get, uid)
        if not p:
            return await ctx.send("Make your career player first: `rccareer start <name>`.")
        dr = p.get("drinks", {})
        lines = [f"**{d['label']}** x{dr.get(k, 0)}: +{d['stamina']} stamina"
                 + (f" · buy {economy.fmt_coins(d['price'])}" if d["price"] else " · events and codes only")
                 + f"  → `rcdrink {k}`" for k, d in cr.DRINKS.items()]
        lines.append(f"\nToday: {p.get('drinks_today', 0)}/{cr.DRINKS_PER_DAY} drinks · "
                     f"Stamina {cr.stamina_now(p):.0f}/100")
        lines.append("Buy: `rcdrink buy water`")
        await ctx.send(embed=discord.Embed(title="Drinks", description="\n".join(lines), color=GREEN))


async def setup(bot):
    await bot.add_cog(CareerCog(bot))
