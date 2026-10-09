"""Squad commands: csdebut, csstarterpack, cssquad, csshow, csxi, csswap,
cscaptain, csautoxi, csteamname, csprofile, cshelp + owner tools.

Commands are plain text (no slash) like csmp: type `csdebut`, `csxi`, ...
Every command passes through security.global_check (ban list, rate limit,
server-only) which main.py installs.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Optional

import discord
from discord import ui
from discord.ext import commands

import card_cache
import card_db
import economy
import security
import squad_logic as sl
from card_narratives import ROLES, HANDS
from security import OwnedView, esc

PAGE_SIZE = 10

# Emoji shown in the csdebut welcome message.
DEBUT_EMOJI = "<:24693:1554672842694729899>"

# Only members with this role see the player emojis in csstarterpack.
PREMIUM_ROLE_ID = 1556957776415035423


def _is_premium(member) -> bool:
    return any(r.id == PREMIUM_ROLE_ID for r in getattr(member, "roles", []))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _in_match(bot, user_id: int) -> bool:
    """True if the user is in a live match (their squad is locked meanwhile)."""
    try:
        from views import active_games
    except Exception:
        return False
    for store in (active_games,):
        for obj in list(store.values()):
            for attr in ("challenger", "opponent", "user", "player", "author", "owner"):
                who = getattr(obj, attr, None)
                if who is not None and getattr(who, "id", who) == user_id:
                    return True
    return False


async def _need_debut(ctx: commands.Context) -> bool:
    if economy.user_exists(ctx.author.id):
        return True
    await ctx.send("❌ You haven't debuted yet. Type `csdebut` first.")
    return False


def _card_line(card, extra: str = "", emojis: bool = True) -> str:
    role = ROLES.get(card_db.effective_role(card), "?")
    if emojis:
        prefix = f"{sl.card_emoji(card)} {sl.role_emoji(card)} "
    else:
        prefix = ""   # plain text for non-premium members
    return f"{prefix}**{esc(card['playername'])}** · {card['ovr']} · {role}{extra}"


def _xi_embed(member: discord.abc.User, user_row) -> discord.Embed:
    xi = economy.get_xi(member.id)
    lines = []
    cap_key = user_row["captain_key"]
    for slot in range(1, economy.XI_SIZE + 1):
        key = xi.get(slot)
        card = card_db.get_card(key) if key else None
        if card is None:
            lines.append(f"`{slot:>2}.` — empty —")
            continue
        badge = " <:Captain:1558139695177932951>" if card["playername_key"] == cap_key else ""
        lines.append(
            f"`{slot:>2}.` {sl.card_emoji(card)} **{esc(card['playername'])}** · "
            f"{card['ovr']} · {ROLES.get(card_db.effective_role(card), '?')}{badge}"
        )
    cards = sl.xi_cards(member.id)
    embed = discord.Embed(
        title=f"🏏 {esc(user_row['team_name'])} — Playing XI",
        description="\n".join(lines),
        color=discord.Color.blue(),
    )
    problem = sl.xi_problem(member.id)
    embed.set_footer(text=(
        f"Team OVR {sl.average_ovr(cards)}"
        + (f" • ⚠️ {problem}" if problem else " • ✅ Ready to play")
    ))
    return embed


# ── Squad list with page buttons ─────────────────────────────────────────────

class SquadView(OwnedView):
    def __init__(self, viewer_id: int, owner_name: str, cards: list, in_xi: set[str]):
        super().__init__(viewer_id, timeout=120)
        self.owner_name = owner_name
        self.cards = cards
        self.in_xi = in_xi
        self.page = 0
        self.pages = max(1, -(-len(cards) // PAGE_SIZE))
        self._sync()

    def build(self) -> discord.Embed:
        start = self.page * PAGE_SIZE
        chunk = self.cards[start:start + PAGE_SIZE]
        lines = [
            _card_line(c, "  ✅ XI" if c["playername_key"] in self.in_xi else "")
            for c in chunk
        ] or ["No players yet."]
        embed = discord.Embed(
            title=f"<:Squademoji:1558139700156571801> {esc(self.owner_name)} — Squad ({len(self.cards)} players)",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self.pages}")
        return embed

    def _sync(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.pages - 1

    @ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.build(), view=self)

    @ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.page = min(self.pages - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.build(), view=self)


# ── The cog ──────────────────────────────────────────────────────────────────


def card_db_owner(player_key: str) -> int | None:
    """User id that currently owns this card, or None."""
    from economy import _conn
    with _conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM owned WHERE player_key = ? LIMIT 1", (player_key,)
        ).fetchone()
    return int(row[0]) if row else None


def _apply_auto_xi(user_id: int) -> str | None:
    """Pick and save the best XI (up to 11) from everything the user owns.
    Returns None on success, otherwise a message explaining what went wrong."""
    cards = sl.owned_cards(user_id)
    if not cards:
        return "❌ You don't own any players yet. Try `csstarterpack`."
    # With fewer than 11 players ALL of them still go into the XI (best_xi
    # returns every card when there are 11 or fewer); the XI just shows
    # "x/11" until the squad grows.
    try:
        xi = sl.best_xi(cards)
    except Exception as e:
        print(f"[autoxi] best_xi failed for {user_id}: {type(e).__name__}: {e}")
        return "❌ Couldn't build your XI. Try again."
    if not economy.set_full_xi(user_id, [c["playername_key"] for c in xi]):
        return "❌ Couldn't set your XI. Try again."
    return None


class AutoXiView(ui.View):
    """Button under your XI: one tap picks the best XI automatically."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = owner_id

    @ui.button(label="Auto XI", style=discord.ButtonStyle.success, emoji=None)
    async def auto_xi(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the owner of this XI can use this.", ephemeral=True)
            return
        if _in_match(interaction.client, self.owner_id):
            await interaction.response.send_message("❌ You can't change your XI during a match.", ephemeral=True)
            return
        err = _apply_auto_xi(self.owner_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=_xi_embed(interaction.user, economy.get_user(self.owner_id)), view=self
        )


class SquadCog(commands.Cog, name="Squad"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- challenge a player (csmp @user <overs>) ---------------------------
    @commands.command(name="csmp")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def csmp(self, ctx: commands.Context, opponent: discord.Member, overs: int = 5):
        """Challenge another player to a match: `csmp @user 5`."""
        from views import AcceptDeclineView, active_games
        from game import GameState
        from embeds import build_match_invite_embed
        from data import random_match_conditions

        challenger = ctx.author
        if opponent.bot:
            await ctx.send("❌ You can't challenge a bot.")
            return
        if opponent.id == challenger.id:
            await ctx.send("❌ You can't challenge yourself.")
            return
        if not 1 <= overs <= 20:
            await ctx.send("❌ Overs must be between 1 and 20. Example: `csmp @user 5`")
            return
        if ctx.channel.id in active_games:
            await ctx.send("❌ A match is already running in this channel. Use `cscancel` to end it.")
            return
        if _in_match(self.bot, challenger.id):
            await ctx.send("❌ You are already in a match.")
            return
        if _in_match(self.bot, opponent.id):
            await ctx.send(f"❌ {opponent.display_name} is already in a match.")
            return
        for who, label in ((challenger, "You"), (opponent, opponent.display_name)):
            if not economy.user_exists(who.id):
                await ctx.send(f"❌ {label} must debut first. Use `csdebut`.")
                return
            # Checks both the general XI rules AND whether this player has
            # enough distinct bowling options to legally cover a match of
            # THIS length (a 4-bowler XI is fine for 1 over, not for 20).
            problem = sl.xi_problem_for_match(who.id, overs)
            if problem:
                await ctx.send(f"❌ {label} {problem}.")
                return

        game = GameState(ctx.channel.id, challenger, opponent, overs)
        # The match screens (embeds / dropdowns) work with the "team dict" form:
        # {"name", "ovr", "chem", "players": [ {name, ovr, bat, bowl, ...}, ... ]}
        for uid in (challenger.id, opponent.id):
            game.teams[uid] = sl.build_match_team(uid)
            game.team_pks[uid] = []
            game.impact_subs_pool[uid] = sl.build_impact_subs_players(uid)
            game.impact_used[uid] = False

        active_games[ctx.channel.id] = game
        conditions = random_match_conditions()
        game.conditions = conditions
        game.pitch_type = conditions["pitch_type"]
        embed = build_match_invite_embed(
            challenger, opponent, overs, conditions, gif_url=conditions.get("gif_url")
        )
        await ctx.send(
            content=opponent.mention,
            embed=embed,
            view=AcceptDeclineView(game),
        )

    # -- end / resume a stuck match ----------------------------------------
    @commands.command(name="cscancel")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def cscancel(self, ctx: commands.Context):
        """End the match running in this channel (either player, or a moderator)."""
        from views import active_games
        game = active_games.get(ctx.channel.id)
        if game is None:
            await ctx.send("❌ There is no match running in this channel.")
            return
        is_player = ctx.author.id in (game.challenger.id, game.opponent.id)
        is_mod = getattr(ctx.author.guild_permissions, "manage_messages", False) if ctx.guild else False
        if not (is_player or is_mod):
            await ctx.send("🔒 Only the two players (or a moderator) can cancel this match.")
            return
        active_games.pop(ctx.channel.id, None)
        game._afk_sent = True   # stops any pending timeout from fining anyone
        await ctx.send("Match cancelled. No result was recorded.")

    @commands.command(name="csresume")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csresume(self, ctx: commands.Context):
        """Re-send the current match prompt if the match looks stuck."""
        from views import active_games, _resume_prompt
        game = active_games.get(ctx.channel.id)
        if game is None:
            await ctx.send("❌ There is no match running in this channel.")
            return
        if ctx.author.id not in (game.challenger.id, game.opponent.id):
            await ctx.send("🔒 Only the two players can resume this match.")
            return
        if game.batting_user_id is None:
            await ctx.send("The match hasn't started yet — finish the toss first.")
            return
        await _resume_prompt(ctx.channel, game)

    # -- debut -----------------------------------------------------------
    @commands.command(name="csdebut")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def csdebut(self, ctx: commands.Context):
        """Create your team."""
        default = security.clean_team_name(f"{ctx.author.display_name} XI")[0] or f"Team {ctx.author.id % 10000}"
        if not economy.create_user(ctx.author.id, default):
            await ctx.send("You have already debuted. Use `csprofile` to see your team.")
            return
        await ctx.send(
            f"{DEBUT_EMOJI} Welcome, **{esc(ctx.author.display_name)}**! Your team **{esc(default)}** is created "
            f"with **{economy.fmt_coins(economy.START_PURSE)}**.\n"
            f"Next: `csstarterpack` to get your first players, then `csxi` to see your XI.\n"
            f"Change your team name with `csteamname <name>`."
        )

    # -- starter pack ----------------------------------------------------
    @commands.command(name="csstarterpack")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def csstarterpack(self, ctx: commands.Context):
        """Claim your one-time starter squad."""
        if not await _need_debut(ctx):
            return
        user = economy.get_user(ctx.author.id)
        if user["starter_claimed"]:
            await ctx.send("You already claimed your starter pack. Try `cspack` or `csdaily`.")
            return
        squad = sl.pick_starter_squad()
        if squad is None:
            await ctx.send("⚠️ There aren't enough player cards in the game yet. Ask the owner to add more with `/cardmaker`.")
            return
        xi = sl.best_xi(squad)
        err = economy.claim_starter(
            ctx.author.id, [c["playername_key"] for c in squad], [c["playername_key"] for c in xi]
        )
        if err:
            await ctx.send(f"❌ {err}")
            return
        show_emoji = _is_premium(ctx.author)   # premium role only
        lines = [_card_line(c, emojis=show_emoji) for c in xi]
        bench = [c for c in squad if c not in xi]
        embed = discord.Embed(title="🎁 Starter Pack opened!", description="\n".join(lines), color=discord.Color.gold())
        if bench:
            embed.add_field(name="Bench", value="\n".join(_card_line(c, emojis=show_emoji) for c in bench), inline=False)
        embed.set_footer(text="See your team: csxi • Get coins: csdaily • Buy packs: cspack")
        await ctx.send(embed=embed)

    # -- squad list ------------------------------------------------------
    @commands.command(name="cssquad")
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def cssquad(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show your squad (or another player's)."""
        target = member or ctx.author
        if target.bot:
            await ctx.send("Bots don't have squads.")
            return
        user = economy.get_user(target.id)
        if user is None:
            await ctx.send(f"**{esc(target.display_name)}** hasn't debuted yet.")
            return
        cards = sorted(sl.owned_cards(target.id), key=lambda c: -c["ovr"])
        view = SquadView(ctx.author.id, user["team_name"], cards, set(economy.get_xi(target.id).values()))
        view.message = await ctx.send(embed=view.build(), view=view)

    # -- show a player ---------------------------------------------------
    @commands.command(name="csshow")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csshow(self, ctx: commands.Context, *, player: str):
        """Show a player's card and stats."""
        card, err = sl.resolve_any(security.clean_input(player))
        if card is None:
            await ctx.send(f"❌ {err}")
            return
        from card_narratives import NARRATIVES
        styles = [s for s in (card["playstyle1"], card["playstyle2"]) if s]
        desc = (
            f"{sl.card_emoji(card)} OVR **{card['ovr']}** · BAT **{card['bat']}** · BOWL **{card['bowl']}**\n"
            f"{ROLES.get(card_db.effective_role(card), '?')}"
            + (f" · {HANDS.get(card['batting_hand'], '')} bat" if card["batting_hand"] else "")
            + (f" · {card_db.effective_bowling_type(card)}" if card_db.effective_bowling_type(card) else "")
            + f"\n{card['country_emoji']} {esc(card['country'])}"
        )
        if styles:
            desc += "\n\n**Playstyles**\n" + "\n".join(
                f"• **{esc(s)}** — {esc(NARRATIVES.get(s, ''))}" for s in styles
            )
        desc += f"\n\nBuy **{economy.fmt_coins(sl.buy_price(card))}** · Sell **{economy.fmt_coins(sl.sell_value(card))}**"
        embed = discord.Embed(title=esc(card["playername"]), description=desc, color=discord.Color.gold())
        await card_cache.send_embed(ctx, embed, card["image_path"])   # cached link = no upload

    # -- player stats card (personal — only your own cards) --------------
    @commands.command(name="csview")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csview(self, ctx: commands.Context, *, player: str):
        """Show a card you own, with YOUR career batting/bowling stats on it."""
        from career_stats import get_career_personal, batting_figures, bowling_figures

        card, err = await asyncio.to_thread(sl.resolve_any, security.clean_input(player))
        if card is None:
            await ctx.send(f"❌ {err}")
            return

        # csview is for cards you currently own only. Selling a card doesn't
        # erase your stats with it (they're kept in case you buy it back) —
        # it just means csview won't show them to anyone while you don't
        # own it.
        owns_it = await asyncio.to_thread(economy.owns, ctx.author.id, card["playername_key"])
        if not owns_it:
            await ctx.send(
                f"❌ You don't own **{esc(card['playername'])}**, so `csview` can't show it. "
                f"(Only cards you currently own can be viewed here.)"
            )
            return

        career = await asyncio.to_thread(get_career_personal, ctx.author.id, card["playername_key"])
        bat = batting_figures(career)
        bowl = bowling_figures(career)

        width = 24
        rows = ["Batting".ljust(width) + "Bowling"]
        for (bl, bv), (wl, wv) in zip(bat, bowl):
            rows.append(f"{bl}: {bv}".ljust(width) + f"{wl}: {wv}")
        table = "\n".join(rows)

        header = f"OVR {card['ovr']}  BAT {card['bat']}  BOWL {card['bowl']}"
        lines = [header]
        lines.append(f"Owner: {esc(ctx.author.display_name)}")
        lines.append(f"Value: {economy.fmt_coins(sl.sell_value(card))}")
        styles = await asyncio.to_thread(card_db.get_player_narratives, card["playername_key"])
        if styles:   # only cards that have a playstyle/narrative
            lines.append("Playstyle: " + " · ".join(styles))
        embed = discord.Embed(
            title=f"Player Stats: {esc(card['playername'])}",
            description="```\n" + "\n".join(lines) + "\n\n" + table + "\n```",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Your personal stats with this card.")
        await card_cache.send_embed(ctx, embed, card["image_path"])   # cached link = no upload

    # -- player stats card (universal — premium only) ---------------------
    @commands.command(name="csdata")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csdata(self, ctx: commands.Context, *, player: str):
        """Premium-only: a player's ALL-TIME stats across every owner ever."""
        from career_stats import get_career, batting_figures, bowling_figures

        if not _is_premium(ctx.author):
            await ctx.send(
                "❌ `csdata` (universal, all-owners stats) is a premium-only command. "
                "Use `csview <player>` for your own cards instead."
            )
            return

        card, err = await asyncio.to_thread(sl.resolve_any, security.clean_input(player))
        if card is None:
            await ctx.send(f"❌ {err}")
            return
        career = await asyncio.to_thread(get_career, card["playername_key"])
        bat = batting_figures(career)
        bowl = bowling_figures(career)

        width = 24
        rows = ["Batting".ljust(width) + "Bowling"]
        for (bl, bv), (wl, wv) in zip(bat, bowl):
            rows.append(f"{bl}: {bv}".ljust(width) + f"{wl}: {wv}")
        table = "\n".join(rows)

        owner = None
        owner_id = await asyncio.to_thread(card_db_owner, card["playername_key"])
        if owner_id:
            member = ctx.guild.get_member(owner_id) if ctx.guild else None
            if member:
                owner = member.display_name
            else:   # not in this server / not cached: ask Discord for the name
                try:
                    user = self.bot.get_user(owner_id) or await self.bot.fetch_user(owner_id)
                    owner = user.display_name
                except discord.HTTPException:
                    owner = "Unknown"

        header = f"OVR {card['ovr']}  BAT {card['bat']}  BOWL {card['bowl']}"
        lines = [header]
        if owner:
            lines.append(f"Current owner: {esc(owner)}")
        lines.append(f"Value: {economy.fmt_coins(sl.sell_value(card))}")
        styles = await asyncio.to_thread(card_db.get_player_narratives, card["playername_key"])
        if styles:   # only cards that have a playstyle/narrative
            lines.append("Playstyle: " + " · ".join(styles))
        embed = discord.Embed(
            title=f"Universal Stats: {esc(card['playername'])}",
            description="```\n" + "\n".join(lines) + "\n\n" + table + "\n```",
            color=discord.Color.gold(),
        )
        embed.set_footer(text="All-time stats across every owner (premium).")
        await card_cache.send_embed(ctx, embed, card["image_path"])   # cached link = no upload

    # -- XI --------------------------------------------------------------
    @commands.command(name="csxi", aliases=["csbattingorder"])
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def csxi(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show your playing XI in batting order."""
        target = member or ctx.author
        if target.id != ctx.author.id and not _is_premium(ctx.author):
            await ctx.send("💎 Only **Premium members** can see another player's XI. You can still use `csxi` for your own XI.")
            return
        user = economy.get_user(target.id)
        if user is None:
            await ctx.send(f"**{esc(target.display_name)}** hasn't debuted yet.")
            return
        view = AutoXiView(ctx.author.id) if target.id == ctx.author.id else None
        await ctx.send(embed=_xi_embed(target, user), view=view)

    @commands.command(name="csautoxi")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def csautoxi(self, ctx: commands.Context):
        """Pick your best XI automatically."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't change your XI during a match.")
            return
        err = _apply_auto_xi(ctx.author.id)
        if err:
            await ctx.send(err)
            return
        await ctx.send(embed=_xi_embed(ctx.author, economy.get_user(ctx.author.id)))

    # -- autoplay (premium) ----------------------------------------------
    @commands.command(name="csautoplay")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def csautoplay(self, ctx: commands.Context):
        """(Premium) Let the bot play your side of the match for you. Type again to switch off."""
        if not _is_premium(ctx.author):
            await ctx.send("💎 `csautoplay` is for **Premium members** only.")
            return
        from views import active_games, autoplay_ids, autoplay_kick
        game = active_games.get(ctx.channel.id)
        players = {getattr(game, "challenger", None), getattr(game, "opponent", None)} if game else set()
        if game is None or ctx.author.id not in {getattr(p, "id", None) for p in players}:
            await ctx.send("❌ You are not playing a match in this channel.")
            return
        ids = autoplay_ids(game)
        if ctx.author.id in ids:
            ids.discard(ctx.author.id)
            await ctx.send(f"🛑 Autoplay **OFF** for {ctx.author.mention}. You are back in control.")
            return
        ids.add(ctx.author.id)
        both = all(getattr(p, "id", None) in ids for p in players if p is not None)
        note = "Both sides are on autoplay — the match will run by itself." if both else "The other side still plays manually."
        await ctx.send(
            f"🤖 Autoplay **ON** for {ctx.author.mention}. Bowler, batter, deliveries and shots are chosen for you. "
            f"{note}\nType `csautoplay` again to take control back."
        )
        autoplay_kick(game)

    # -- swap ------------------------------------------------------------
    @commands.command(name="csswap")
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def csswap(self, ctx: commands.Context, *, players: str):
        """Swap two players: `csswap 3 5` (XI spots) or `csswap Virat Kohli | Rohit Sharma`."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't change your XI during a match.")
            return
        players = security.clean_input(players, 120)
        parts = [p.strip() for p in players.split("|")] if "|" in players else players.split()
        if len(parts) != 2:
            await ctx.send("Usage: `csswap 3 5` or `csswap Player One | Player Two`")
            return
        xi = economy.get_xi(ctx.author.id)
        keys = []
        for part in parts:
            if part.isdigit():
                slot = int(part)
                if not 1 <= slot <= economy.XI_SIZE or slot not in xi:
                    await ctx.send(f"❌ XI spot `{slot}` is empty or not valid (1-{economy.XI_SIZE}).")
                    return
                keys.append(xi[slot])
            else:
                card, err = sl.resolve_owned(ctx.author.id, part)
                if card is None:
                    await ctx.send(f"❌ {err}")
                    return
                keys.append(card["playername_key"])
        err = economy.swap_players(ctx.author.id, keys[0], keys[1])
        if err:
            await ctx.send(f"❌ {err}")
            return
        await ctx.send(embed=_xi_embed(ctx.author, economy.get_user(ctx.author.id)))

    # -- impact player subs ------------------------------------------------
    @commands.command(name="cssubs", aliases=["csimpact"])
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def cssubs(
        self,
        ctx: commands.Context,
        action: Optional[str] = None,
        *,
        player: Optional[str] = None,
    ):
        """Manage your Impact Player subs (bench players, max 4).
        `cssubs` to view, `cssubs add <player>`, `cssubs remove <player>`, `cssubs clear`."""
        if not await _need_debut(ctx):
            return
        action = (action or "list").strip().lower()

        if action in ("list", "show", "view"):
            subs = economy.get_impact_subs(ctx.author.id)
            if not subs:
                await ctx.send(
                    "🔁 You have no Impact Player subs set.\n"
                    "Add one with `cssubs add <player name>` "
                    f"(up to {economy.IMPACT_SUBS_MAX}, must be a bench player — not already in your XI)."
                )
                return
            lines = []
            for slot in sorted(subs):
                card = card_db.get_card(subs[slot])
                name = card["playername"] if card else subs[slot]
                lines.append(f"**{slot}.** {esc(name)}")
            await ctx.send("🔁 **Your Impact Player subs:**\n" + "\n".join(lines))
            return

        if action in ("add", "set"):
            if _in_match(self.bot, ctx.author.id):
                await ctx.send("❌ You can't change your subs during a match.")
                return
            if not player:
                await ctx.send("Usage: `cssubs add <player name>`")
                return
            card, err = sl.resolve_owned(ctx.author.id, security.clean_input(player))
            if card is None:
                await ctx.send(f"❌ {err}")
                return
            err = economy.add_impact_sub(ctx.author.id, card["playername_key"])
            if err:
                await ctx.send(f"❌ {err}")
                return
            await ctx.send(f"✅ **{esc(card['playername'])}** added to your Impact Player subs.")
            return

        if action in ("remove", "rem", "delete", "del"):
            if _in_match(self.bot, ctx.author.id):
                await ctx.send("❌ You can't change your subs during a match.")
                return
            if not player:
                await ctx.send("Usage: `cssubs remove <player name>`")
                return
            card, err = sl.resolve_owned(ctx.author.id, security.clean_input(player))
            if card is None:
                await ctx.send(f"❌ {err}")
                return
            err = economy.remove_impact_sub(ctx.author.id, card["playername_key"])
            if err:
                await ctx.send(f"❌ {err}")
                return
            await ctx.send(f"✅ **{esc(card['playername'])}** removed from your Impact Player subs.")
            return

        if action == "clear":
            if _in_match(self.bot, ctx.author.id):
                await ctx.send("❌ You can't change your subs during a match.")
                return
            economy.clear_impact_subs(ctx.author.id)
            await ctx.send("✅ Your Impact Player subs list is now empty.")
            return

        await ctx.send(
            "Usage: `cssubs` (view), `cssubs add <player>`, `cssubs remove <player>`, `cssubs clear`."
        )

    # -- captain ---------------------------------------------------------
    @commands.command(name="cscaptain")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def cscaptain(self, ctx: commands.Context, *, player: str):
        """Set your captain (must be in your XI)."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't change your captain during a match.")
            return
        card, err = sl.resolve_owned(ctx.author.id, security.clean_input(player))
        if card is None:
            await ctx.send(f"❌ {err}")
            return
        err = economy.set_captain(ctx.author.id, card["playername_key"])
        if err:
            await ctx.send(f"❌ {err}")
            return
        await ctx.send(f"<:Captain:1558139695177932951> **{esc(card['playername'])}** is now your captain.")

    # -- team name -------------------------------------------------------
    @commands.command(name="csteamname")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def csteamname(self, ctx: commands.Context, *, name: str):
        """Change your team name."""
        if not await _need_debut(ctx):
            return
        clean, reason = security.clean_team_name(name)
        if clean is None:
            await ctx.send(f"❌ {reason}")
            return
        economy.set_team_name(ctx.author.id, clean)
        await ctx.send(f"✅ Team name changed to **{esc(clean)}**.")

    # -- profile ---------------------------------------------------------
    @commands.command(name="csprofile")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def csprofile(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show your team profile."""
        target = member or ctx.author
        if target.bot:
            await ctx.send("Bots don't have profiles.")
            return
        user = economy.get_user(target.id)
        if user is None:
            await ctx.send(f"**{esc(target.display_name)}** hasn't debuted yet.")
            return
        cards = sl.xi_cards(target.id)
        cap = card_db.get_card(user["captain_key"]) if user["captain_key"] else None
        try:
            from match_records import get_form
            form = get_form(target.id, 5)
        except Exception:
            form = []
        wins = sum(1 for m in form if m["won"])
        form_txt = " ".join(
            "<:Wonprofile:1558139744008020088>" if m["won"] else "<:Lossprofile:1558139747275243643>"
            for m in form
        ) or "No matches yet"
        embed = discord.Embed(title=f"👤 {esc(user['team_name'])}", color=discord.Color.blurple())
        embed.set_author(name=esc(target.display_name), icon_url=target.display_avatar.url)
        embed.add_field(name="Purse", value=economy.fmt_coins(user["purse"]))
        embed.add_field(name="<:OVR:1558139691482751066> XI OVR", value=str(sl.average_ovr(cards)))
        embed.add_field(name="<:Captain:1558139695177932951> Captain", value=esc(cap["playername"]) if cap else "Not set")
        embed.add_field(name="<:Squad:1558139688231903322> Players", value=str(len(economy.owned_keys(target.id))))
        embed.add_field(name="<:Streak:1558139736617390292> Daily streak", value=str(user["daily_streak"]))
        embed.add_field(name=f"Form (last {len(form)})", value=f"{form_txt}\n{wins} win(s)" if form else form_txt)
        await ctx.send(embed=embed)

    # -- help ------------------------------------------------------------
    @commands.command(name="cshelp")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def cshelp(self, ctx: commands.Context):
        """List the commands."""
        embed = discord.Embed(title="🏏 CricStar Help", color=discord.Color.orange())
        embed.add_field(name="Start", value=(
            "`csdebut` create team\n`csstarterpack` first players\n`csdaily` `csweekly` (Premium) `csmonthly` free coins + player"
        ), inline=False)
        embed.add_field(name="Squad", value=(
            "`cssquad` `csshow <player>` `csxi` `csautoxi` `csautoplay` (Premium) · `csxi @user` (Premium)\n"
            "`csswap 3 5` or `csswap A | B`\n`cscaptain <player>` `csteamname <name>` `csprofile`\n"
            "`csview <player>` your stats on a card you own · `csdata <player>` all-owners stats (Premium)\n"
            "`cssubs` view Impact Player subs · `cssubs add/remove <player>` · `cssubs clear`"
        ), inline=False)
        embed.add_field(name="Economy", value=(
            "`cspurse` `cspack` `csopen <type>`\n`csbuy <player>` `cssell <player>`\n"
            "`cstrade @user my player | their player` `csleaderboard`"
        ), inline=False)
        embed.add_field(name="Play", value="`csmp @user <overs>` challenge • `cscancel` cancel match", inline=False)
        await ctx.send(embed=embed)

    # ── Owner-only tools ─────────────────────────────────────────────────
    @commands.command(name="csban")
    @security.owner_or_admin_role()
    async def csban(self, ctx: commands.Context, member: discord.User, *, reason: str = "No reason"):
        """(Owner) Ban someone from using the bot."""
        if member.id == ctx.author.id or member.bot:
            await ctx.send("❌ Can't ban that user.")
            return
        security.ban_user(member.id, security.clean_input(reason, 150))
        await ctx.send(f"🚫 Banned **{esc(member.display_name)}**.")

    @commands.command(name="csunban")
    @security.owner_or_admin_role()
    async def csunban(self, ctx: commands.Context, member: discord.User):
        """(Owner) Let someone use the bot again."""
        security.unban_user(member.id)
        await ctx.send(f"✅ Unbanned **{esc(member.display_name)}**.")

    @commands.command(name="csgivecoins")
    @security.owner_or_admin_role()
    async def csgivecoins(self, ctx: commands.Context, member: discord.Member, amount: int):
        """(Owner) Give (or take with a negative number) coins."""
        if not economy.user_exists(member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        if not -1_000_000_000 <= amount <= 1_000_000_000:
            await ctx.send("❌ Amount too large.")
            return
        bal = economy.add_coins(member.id, amount, f"Owner adjustment by {ctx.author.id}")
        await ctx.send(f"✅ {esc(member.display_name)} now has **{economy.fmt_coins(bal)}**.")

    @commands.command(name="csgivecard")
    @security.owner_or_admin_role()
    async def csgivecard(self, ctx: commands.Context, member: discord.Member, *, player: str):
        """(Owner) Give someone a player card."""
        card, err = sl.resolve_any(security.clean_input(player))
        if card is None:
            await ctx.send(f"❌ {err}")
            return
        if economy.give_card(member.id, card["playername_key"]):
            await ctx.send(f"✅ Gave **{esc(card['playername'])}** to {esc(member.display_name)}.")
        else:
            await ctx.send("❌ They haven't debuted, or already own that player.")


async def setup(bot: commands.Bot):
    await bot.add_cog(SquadCog(bot))
