"""Career Mode commands.

  rccareer start <name>   make your own cricketer (pick Batter / Bowler / All-rounder)
  rccareer                your player: stats, stamina, stage, form, record
  rccareer play           career match: you play only your own player, ball by ball
  rccareer train          training (stamina 15): pick Batting / Bowling / Fitness, then the right drill
  rcdrink                 your drinks      rcdrink water|energy|power   drink one
  rcdrink buy <drink> [n] buy Water (100,000) / Energy Drink (500,000)
"""
from __future__ import annotations

import asyncio
import math
import random
import time

import discord
from discord import ui
from discord.ext import commands

import career as cr
import economy
import logic
import security
from security import OwnedView, esc

GREEN = discord.Color.from_rgb(46, 204, 113)
GOLD = discord.Color.from_rgb(240, 198, 104)

DELIVERIES = ["Fast", "Swing", "Good Length", "Full", "Bouncer", "Yorker", "Off Break", "Leg Break"]
DELIVERY_W = [16, 12, 18, 10, 10, 8, 13, 13]
SHOTS = ["Drive", "Cut", "Flick", "Pull", "Sweep", "Lofted", "Defend"]
SHOT_W = [20, 14, 16, 14, 8, 16, 12]
BAT_CHOICES = {"defend": ("Defend", 0.5), "rotate": ("Rotate strike", 1.0), "attack": ("Attack", 2.5)}
BOWL_CHOICES = {"Good Length": 1.0, "Yorker": 1.8, "Bouncer": 1.8, "Off Break": 1.2}
BOWL_LABEL = {"Good Length": "Good Length", "Yorker": "Yorker", "Bouncer": "Bouncer", "Off Break": "Slower ball"}

DRILLS = {
    "bat": ("Batting", [("short balls at the body", "Pull Shot"), ("full balls outside off", "Cover Drive"),
                        ("full balls at the stumps", "Straight Drive")]),
    "bowl": ("Bowling", [("a batter who keeps coming down the track", "Bouncer"),
                         ("death overs, big hitter on strike", "Yorker"), ("new ball on a green pitch", "Outswing")]),
    "fit": ("Fitness", [("a long innings tomorrow", "Endurance Run"), ("quick singles drill", "Sprints"),
                        ("sore legs after yesterday", "Recovery Swim")]),
}

_active: set[int] = set()


def bar(v: float, n: int = 10) -> str:
    full = int(round(v / 100 * n))
    return "▰" * full + "▱" * (n - full)


def _shot_for(delivery: str, choice: str) -> str:
    if choice == "defend":
        return "Defend"
    opts = [(s, logic.MATCHUP_MATRIX.get((delivery, s))) for s in SHOTS if s != "Defend"]
    opts = [(s, m) for s, m in opts if m]
    if not opts:
        return "Drive"
    if choice == "rotate":
        safe = [o for o in opts if o[0] != "Lofted"] or opts
        return min(safe, key=lambda o: (o[1]["wkt"], -o[1]["bat"], random.random()))[0]
    return max(opts, key=lambda o: (o[1]["bat"], random.random()))[0]


# Career ball model: the choice really matters (defend = safe and slow, attack = fast and risky).
# Ratings shift it: every point above the opponent adds ~2.5% runs and ~3.5% less wicket chance.
BAT_BASE = {
    "defend": {"0": 70, "1": 24, "2": 3, "4": 1.5, "W": 1.4},
    "rotate": {"0": 38, "1": 40, "2": 12, "3": 2, "4": 6, "6": 1, "W": 4.0},
    "attack": {"0": 30, "1": 15, "2": 6, "4": 22, "6": 13, "W": 11.0},
}
BATTER_AI = (["defend", "rotate", "attack"], [15, 50, 35])
# (runs x, wicket x) of your delivery against what the batter is trying to do -> read the batter!
COUNTER = {
    "Good Length": {"defend": (1.00, 1.00), "rotate": (0.75, 1.40), "attack": (1.15, 0.95)},
    "Yorker":      {"defend": (1.40, 0.60), "rotate": (1.30, 0.75), "attack": (0.65, 1.45)},
    "Bouncer":     {"defend": (1.00, 1.70), "rotate": (1.10, 0.90), "attack": (1.30, 1.00)},
    "Off Break":   {"defend": (1.10, 0.85), "rotate": (1.15, 0.90), "attack": (0.80, 1.30)},
}
BATTER_HINT = {"defend": "is blocking everything, playing safe", "rotate": "is looking for quick singles",
               "attack": "is shuffling across, looking to hit big"}
HINT_TRUE = 0.80
EXTRA_CHANCE = {"Good Length": 0.03, "Yorker": 0.05, "Bouncer": 0.07, "Off Break": 0.03}


def career_ball(intent: str, my_bat: float, opp_bowl: float, delivery: str | None = None,
                shot: str | None = None, bowl_mod=(1.0, 1.0), extra=0.04) -> str:
    """One ball -> '0'..'6', 'W', 'Wd', 'NB'."""
    if random.random() < extra:
        return random.choice(("Wd", "Wd", "NB"))
    diff = my_bat - opp_bowl
    fb = math.exp(diff * 0.025)
    fw = math.exp(-diff * 0.035)
    m = logic.MATCHUP_MATRIX.get((delivery, shot), {"bat": 1.0, "wkt": 1.0}) if delivery and shot else \
        {"bat": 1.0, "wkt": 1.0}
    mb, mw = math.sqrt(m["bat"]), math.sqrt(m["wkt"])
    w = dict(BAT_BASE[intent])
    for k in ("1", "2", "3"):
        if k in w:
            w[k] *= fb * mb * bowl_mod[0]
    for k in ("4", "6"):
        if k in w:
            w[k] *= (fb * mb * bowl_mod[0]) ** 1.25
    w["0"] /= fb
    w["W"] *= fw * mw * bowl_mod[1]
    keys = list(w)
    return random.choices(keys, [w[k] for k in keys])[0]


def _parse(o: str) -> tuple[int, bool, bool]:
    """-> (runs, legal ball, wicket). Extras: runs go to the team, ball not counted."""
    if o == "W":
        return 0, True, True
    if o == "Wd":
        return 1, False, False
    if o == "NB":
        return 1, False, False
    if o.startswith("NB+"):
        return 1 + int(o[3:] or 0), False, False
    return int(o), True, False


WORDS = {0: "dot ball", 1: "single", 2: "two runs", 3: "three runs", 4: "FOUR!", 6: "SIX!"}


# ── match ────────────────────────────────────────────────────────────────────
class CareerMatch(OwnedView):
    def __init__(self, uid: int, p: dict):
        super().__init__(uid, timeout=150)
        self.uid, self.p = uid, p
        st = cr.STAGES[p["stage"]]
        self.stage = st
        self.opp = random.randint(*st["opp"])
        self.stamina = float(p["stamina"])
        self.tired_start = p["stamina_start"] < cr.LOW_STAMINA
        bonus = cr.form_bonus(p)
        boost = 1 + (p.get("boost") or 0) / 100
        self.my_bat = (p["bat"] + bonus) * boost
        self.my_bowl = (p["bowl"] + bonus) * boost
        if p["role"] == "bat":
            self.phases = [("bat", st["bat_balls"])]
        elif p["role"] == "bowl":
            self.phases = [("bowl", st["bowl_balls"])]
        else:
            self.phases = [("bat", st["bat_balls"] * 2 // 3), ("bowl", st["bowl_balls"])]
        self.pi = 0
        self.bat = {"runs": 0, "balls": 0, "out": False, "fours": 0, "sixes": 0} if any(
            ph == "bat" for ph, _ in self.phases) else None
        self.bowl = {"runs": 0, "balls": 0, "wkts": 0} if any(ph == "bowl" for ph, _ in self.phases) else None
        self.log: list[str] = []
        self.next_delivery = random.choices(DELIVERIES, DELIVERY_W)[0]
        self._new_batter_intent()
        self.done = False
        self._build()

    def _new_batter_intent(self):
        self.batter_intent = random.choices(*BATTER_AI)[0]
        shown = self.batter_intent if random.random() < HINT_TRUE else \
            random.choice([i for i in BATTER_HINT if i != self.batter_intent])
        self.batter_hint = BATTER_HINT[shown]

    # helpers
    @property
    def phase(self):
        return self.phases[self.pi] if self.pi < len(self.phases) else (None, 0)

    def _mult(self) -> float:
        if self.stamina <= 0:
            return 0.75
        return 0.9 if self.stamina < cr.LOW_STAMINA else 1.0

    def _build(self):
        self.clear_items()
        ph, _ = self.phase
        if self.done or ph is None:
            return
        if ph == "bat":
            for key, (label, cost) in BAT_CHOICES.items():
                b = ui.Button(label=f"{label} (-{cost:g})", style=discord.ButtonStyle.danger if key == "attack"
                              else (discord.ButtonStyle.secondary if key == "defend" else discord.ButtonStyle.primary))
                b.callback = self._mk(key)
                self.add_item(b)
        else:
            for key, cost in BOWL_CHOICES.items():
                b = ui.Button(label=f"{BOWL_LABEL[key]} (-{cost:g})", style=discord.ButtonStyle.primary)
                b.callback = self._mk(key)
                self.add_item(b)

    def _mk(self, key):
        async def cb(interaction: discord.Interaction):
            if self.done:
                return await interaction.response.defer()
            self._ball(key)
            if self.done:
                self.stop()
                summary = await asyncio.to_thread(self._finish)
                await interaction.response.edit_message(embed=summary, view=None)
            else:
                self._build()
                await interaction.response.edit_message(embed=self.embed(), view=self)
        return cb

    def _ball(self, key):
        ph, limit = self.phase
        if ph == "bat":
            label, cost = BAT_CHOICES[key]
            d = self.next_delivery
            shot = _shot_for(d, key)
            o = career_ball(key, self.my_bat * self._mult(), self.opp, d, shot)
            runs, legal, wkt = _parse(o)
            self.stamina = max(0.0, self.stamina - cost)
            if not legal:
                self.log.append(f"{d}: {'wide' if o == 'Wd' else 'no ball'} (+{runs})")
            elif wkt:
                self.bat["balls"] += 1
                self.bat["out"] = True
                self.log.append(f"{d} → {label}: **OUT!**")
            else:
                self.bat["balls"] += 1
                self.bat["runs"] += runs
                self.bat["fours"] += runs == 4
                self.bat["sixes"] += runs == 6
                self.log.append(f"{d} → {label}: {WORDS.get(runs, f'{runs} runs')}")
            self.next_delivery = random.choices(DELIVERIES, DELIVERY_W)[0]
            if self.bat["out"] or self.bat["balls"] >= limit:
                self.pi += 1
                self.log.append("──── innings over ────" if self.pi < len(self.phases) else "")
        else:
            cost = BOWL_CHOICES[key]
            intent = self.batter_intent
            o = career_ball(intent, self.opp, self.my_bowl * self._mult(), None, None,
                            COUNTER[key][intent], EXTRA_CHANCE[key])
            self._new_batter_intent()
            shot = {"defend": "a blocker", "rotate": "a single-taker", "attack": "a big hitter"}[intent]
            runs, legal, wkt = _parse(o)
            self.stamina = max(0.0, self.stamina - cost)
            self.bowl["runs"] += runs
            if not legal:
                self.log.append(f"{BOWL_LABEL[key]}: {'wide' if o == 'Wd' else 'no ball'} (+{runs})")
            else:
                self.bowl["balls"] += 1
                if wkt:
                    self.bowl["wkts"] += 1
                    self.log.append(f"{BOWL_LABEL[key]} vs {shot}: **WICKET!**")
                else:
                    self.log.append(f"{BOWL_LABEL[key]} vs {shot}: {WORDS.get(runs, f'{runs} runs')}")
            if self.bowl["balls"] >= limit:
                self.pi += 1
        self.log = [x for x in self.log if x][-6:]
        if self.pi >= len(self.phases):
            self.done = True

    def embed(self) -> discord.Embed:
        p = self.p
        ph, limit = self.phase
        e = discord.Embed(title=f"{esc(p['name'])} · {self.stage['name']}", color=GREEN)
        lines = []
        if self.bat:
            b = self.bat
            lines.append(f"**Batting:** {b['runs']}{'' if b['out'] else '*'} ({b['balls']})"
                         + (f"  ·  ball {b['balls'] + 1}/{self.phases[0][1]}" if ph == "bat" else ""))
        if self.bowl:
            w = self.bowl
            ov = f"{w['balls'] // 6}.{w['balls'] % 6}"
            lines.append(f"**Bowling:** {w['wkts']}/{w['runs']} ({ov} ov)"
                         + (f"  ·  ball {w['balls'] + 1}/{limit}" if ph == "bowl" else ""))
        lines.append(f"**Stamina:** {bar(self.stamina)} {self.stamina:.0f}"
                     + ("  ·  tired, -10%" if 0 < self.stamina < cr.LOW_STAMINA else "")
                     + ("  ·  exhausted, -25%" if self.stamina <= 0 else ""))
        lines.append(f"**Opponent:** {self.opp} rated")
        e.description = "\n".join(lines)
        if self.log:
            e.add_field(name="Last balls", value="\n".join(self.log), inline=False)
        if ph == "bat":
            e.add_field(name="Next ball", value=f"Bowler is running in: **{self.next_delivery}**\n"
                        "Attack scores more but costs stamina and is riskier.", inline=False)
        elif ph == "bowl":
            e.add_field(name="Your ball", value=f"The batter **{self.batter_hint}**.\n"
                        "Read him: Yorker stops big hitters, Good Length beats singles, Bouncer gets blockers "
                        "out, Slower ball fools hitters too. (He may be bluffing.)", inline=False)
        e.set_footer(text="Buttons stop after 2.5 min of no play (the rest is played safely for you).")
        return e

    def _finish(self) -> discord.Embed:
        r = cr.rating(self.bat, self.bowl)
        won = random.random() < max(0.25, min(0.90, 0.30 + r * 0.06))
        base = random.randint(128, 168) + ((self.bat["runs"] - 20) // 2 if self.bat else 0)
        theirs = base - random.randint(1, 35) if won else base + random.randint(1, 35)
        res = {"bat": self.bat, "bowl": self.bowl, "won": won, "rating": r,
               "stamina_left": self.stamina, "tired_start": self.tired_start}
        out = cr.finish_match(self.uid, res)
        _active.discard(self.uid)
        e = discord.Embed(title=f"{'WON' if won else 'LOST'}  ·  {esc(self.p['name'])}", color=GOLD if won else
                          discord.Color.dark_grey())
        lines = [f"Your team **{base}** · Opponent **{theirs}**",
                 f"Match rating: **{r}/10**"]
        if self.bat:
            b = self.bat
            sr = b["runs"] * 100 / b["balls"] if b["balls"] else 0
            lines.append(f"Batting: **{b['runs']}{'' if b['out'] else '*'}** ({b['balls']}) · 4s {b['fours']} · "
                         f"6s {b['sixes']} · SR {sr:.0f}")
        if self.bowl:
            w = self.bowl
            lines.append(f"Bowling: **{w['wkts']}/{w['runs']}** ({w['balls'] // 6}.{w['balls'] % 6} ov)")
        lines.append(f"Stamina left: {self.stamina:.0f}")
        lines.append(f"\n**+{out['coins']:,} coins**")
        names = {"bat": "BAT", "bowl": "BOWL", "fit": "FITNESS"}
        for stat, n in out["ups"].items():
            lines.append(f"{names[stat]} +{n}")
        if out["water"]:
            lines.append("Found 1 Water in the dressing room.")
        lines += out["milestones"]
        if out["stage_up"]:
            lines.append(f"\n**PROMOTED to {out['stage_up']}!**")
        if out["injured"]:
            lines.append("\nYou played tired and got **injured**. Out for 24 hours.")
        e.description = "\n".join(lines)
        e.set_footer(text="rccareer · rccareer play · rccareer train · rcdrink")
        return e

    async def on_timeout(self):
        if self.done:
            return
        while not self.done:
            ph, _ = self.phase
            self._ball("rotate" if ph == "bat" else "Good Length")
        summary = await asyncio.to_thread(self._finish)
        if self.message:
            try:
                await self.message.edit(embed=summary, view=None)
            except discord.HTTPException:
                pass


# ── start / train views ──────────────────────────────────────────────────────
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
            e.title = f"Welcome to Club Cricket, {esc(self.name)}!"
            await interaction.response.edit_message(
                content="You got 3 Water and 1 Energy Drink to start. Play your first match: `rccareer play`",
                embed=e, view=None)
        return cb


class TrainPick(OwnedView):
    def __init__(self, uid: int):
        super().__init__(uid, timeout=90)
        self.stat = None
        self._stats()

    def _stats(self):
        self.clear_items()
        for key, (label, _) in DRILLS.items():
            b = ui.Button(label=label, style=discord.ButtonStyle.primary)
            b.callback = self._pick_stat(key)
            self.add_item(b)

    def _pick_stat(self, key):
        async def cb(interaction: discord.Interaction):
            self.stat = key
            hint, right = random.choice(DRILLS[key][1])
            self.right = right
            self.clear_items()
            opts = [d for _, d in DRILLS[key][1]]
            random.shuffle(opts)
            for o in opts:
                b = ui.Button(label=o, style=discord.ButtonStyle.secondary)
                b.callback = self._drill(o)
                self.add_item(b)
            e = discord.Embed(title=f"{DRILLS[key][0]} training",
                              description=f"Coach: \"Today we work on **{hint}**.\"\nPick the right drill. "
                                          "The right one gives double XP.", color=GREEN)
            await interaction.response.edit_message(embed=e, view=self)
        return cb

    def _drill(self, choice):
        async def cb(interaction: discord.Interaction):
            self.stop()
            perfect = choice == self.right
            err, r = await asyncio.to_thread(cr.train, interaction.user.id, self.stat, perfect)
            if err:
                return await interaction.response.edit_message(content=f"❌ {err}", embed=None, view=None)
            name = DRILLS[self.stat][0]
            txt = ("**Perfect drill!** " if perfect else f"Not the best drill (it was {self.right}). ") + \
                  f"+{r['xp']} {name} XP"
            if r["gained"]:
                txt += f"\n**{name} +{r['gained']}** → {r['value']}"
            txt += f"\nXP {r['xp_now']}/{r['need']} to next point · Stamina -{cr.TRAIN_COST}"
            e = discord.Embed(title=f"{name} training done", description=txt, color=GREEN)
            await interaction.response.edit_message(embed=e, view=None)
        return cb


def profile_embed(p: dict, user) -> discord.Embed:
    s = cr.stamina_now(p)
    st = cr.STAGES[p["stage"]]
    e = discord.Embed(title=f"{esc(p['name'])} · {cr.ROLES[p['role']]}", color=GREEN)
    e.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    fb = cr.form_bonus(p)
    fa = cr.form_avg(p)
    lines = [f"**OVR {cr.ovr(p)}**  ·  {st['name']}",
             f"BAT **{p['bat']}**  ·  BOWL **{p['bowl']}**  ·  FITNESS **{p['fit']}**",
             f"Stamina {bar(s)} **{s:.0f}/100**  (+{cr.regen_rate(p['fit']):.1f}/hour)",
             f"Form: {f'{fa:.1f}' if fa is not None else '-'}"
             + (f" (+{fb} to stats)" if fb > 0 else (f" ({fb} to stats)" if fb < 0 else ""))]
    if p["injured_until"] and p["injured_until"] > time.time():
        lines.append(f"**Injured** for {(p['injured_until'] - time.time()) / 3600:.0f}h more")
    if p.get("boost"):
        lines.append(f"Power Shot active: +{p['boost']}% next match")
    up = st["up"]
    if up and p["stage"] < cr.LAST_OPEN_STAGE:
        lines.append(f"Next stage **{cr.STAGES[p['stage'] + 1]['name']}**: OVR {up[0]} and "
                     f"{p['stage_matches']}/{up[1]} matches")
    elif p["stage"] >= cr.LAST_OPEN_STAGE:
        lines.append(f"Next stage **{cr.STAGES[p['stage'] + 1]['name']}**: coming soon")
    e.description = "\n".join(lines)
    avg = p["runs"] / p["outs"] if p["outs"] else float(p["runs"])
    sr = p["runs"] * 100 / p["balls"] if p["balls"] else 0
    e.add_field(name="Batting", value=f"{p['runs']} runs · avg {avg:.1f} · SR {sr:.0f}\nBest {p['best_bat'] or '-'}")
    econ = p["conceded"] * 6 / p["bowled"] if p["bowled"] else 0
    e.add_field(name="Bowling", value=f"{p['wickets']} wkts · econ {econ:.1f}\nBest {p['best_bowl'] or '-'}")
    e.add_field(name="Record", value=f"{p['matches']} matches · {p['wins']} won")
    dr = p.get("drinks", {})
    e.add_field(name=f"Drinks (today {p.get('drinks_today', 0)}/{cr.DRINKS_PER_DAY})",
                value=" · ".join(f"{d['label']} {dr.get(k, 0)}" for k, d in cr.DRINKS.items()), inline=False)
    e.set_footer(text="rccareer play · rccareer train · rcdrink")
    return e


# ── cog ──────────────────────────────────────────────────────────────────────
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
        """Career Mode: rccareer [start <name> | play | train]"""
        if not await self._need_debut(ctx):
            return
        sub = (sub or "").lower()
        uid = ctx.author.id
        if sub == "start":
            name, err = security.clean_team_name(rest or "")
            if err:
                return await ctx.send("Give your cricketer a name: `rccareer start <name>` (3-24 letters).")
            if await asyncio.to_thread(cr.get, uid):
                return await ctx.send("You already have a career player. Type `rccareer`.")
            v = RolePick(uid, name)
            e = discord.Embed(title=f"Create {esc(name)}", color=GREEN, description=(
                "Pick his role:\n**Batter**: BAT 62 · BOWL 35\n**Bowler**: BAT 35 · BOWL 62\n"
                "**All-rounder**: BAT 55 · BOWL 55 (bats and bowls every match)\nEveryone starts with FITNESS 55."))
            v.message = await ctx.send(embed=e, view=v)
            return
        p = await asyncio.to_thread(cr.get, uid)
        if not p:
            return await ctx.send(
                "**Career Mode**: make your own cricketer and take him from Club cricket to the top.\n"
                "Start: `rccareer start <name>`")
        if sub in ("play", "match"):
            if uid in _active:
                return await ctx.send("You are already in a career match.")
            err, p = await asyncio.to_thread(cr.start_match, uid)
            if err:
                return await ctx.send(f"❌ {err}")
            _active.add(uid)
            v = CareerMatch(uid, p)
            v.message = await ctx.send(embed=v.embed(), view=v)
            return
        if sub in ("train", "training"):
            err = cr.can_act(p, cr.TRAIN_COST)
            if err:
                return await ctx.send(f"❌ {err}")
            v = TrainPick(uid)
            e = discord.Embed(title="Training", color=GREEN,
                              description=f"What do you want to train? (stamina -{cr.TRAIN_COST})")
            v.message = await ctx.send(embed=e, view=v)
            return
        await ctx.send(embed=profile_embed(p, ctx.author))

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
            n = max(1, min(20, n))
            err = await asyncio.to_thread(cr.buy_drink, uid, drink, n)
            if err:
                return await ctx.send(f"❌ {err}")
            return await ctx.send(f"Bought **{n} x {cr.DRINKS[drink]['label']}**.")
        if a in cr.DRINKS:
            err, new = await asyncio.to_thread(cr.use_drink, uid, a)
            if err:
                return await ctx.send(f"❌ {err}")
            d = cr.DRINKS[a]
            extra = f" · next match +{d['boost']}%" if d["boost"] else ""
            return await ctx.send(f"You had a **{d['label']}**. Stamina {bar(new)} **{new:.0f}/100**{extra}")
        p = await asyncio.to_thread(cr.get, uid)
        if not p:
            return await ctx.send("Make your career player first: `rccareer start <name>`.")
        dr = p.get("drinks", {})
        lines = [f"**{d['label']}** x{dr.get(k, 0)}: +{d['stamina']} stamina"
                 + (f", +{d['boost']}% next match" if d["boost"] else "")
                 + (f" · buy {economy.fmt_coins(d['price'])}" if d["price"] else " · events and codes only")
                 + f"  → `rcdrink {k}`" for k, d in cr.DRINKS.items()]
        lines.append(f"\nToday: {p.get('drinks_today', 0)}/{cr.DRINKS_PER_DAY} drinks · "
                     f"Stamina {cr.stamina_now(p):.0f}/100")
        lines.append("Buy: `rcdrink buy water 2`")
        await ctx.send(embed=discord.Embed(title="Drinks", description="\n".join(lines), color=GREEN))


async def setup(bot):
    await bot.add_cog(CareerCog(bot))
