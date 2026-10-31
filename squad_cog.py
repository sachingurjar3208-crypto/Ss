"""Squad commands: rcdebut, rcstarterpack, rcsquad, rcshow, rcxi, rcswap,
rccaptain, rcautoxi, rcteamname, rcprofile, rchelp + owner tools.

Commands are plain text (no slash) like rcmp: type `rcdebut`, `rcxi`, ...
Every command passes through security.global_check (ban list, rate limit,
server-only) which main.py installs.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Optional, Union

import discord
from discord import ui
from discord.ext import commands

import card_cache
import card_db
import economy
import quest
import security
import squad_logic as sl
from squad_image import build_team_image, FILENAME as TEAM_IMG_NAME
from debut_image import build_debut_image, FILENAME as DEBUT_IMG_NAME
from card_narratives import ROLES, HANDS
from security import OwnedView, esc

PAGE_SIZE = 10

# Main server invite shown in the rcdebut welcome message (clickable blue link).
MAIN_SERVER_INVITE = "https://discord.gg/UjgyV56tkT"
JOIN_MAIN_LINK = f"[Join Main server]({MAIN_SERVER_INVITE})"

# Only members with this role see the player emojis in rcstarterpack.
PREMIUM_ROLE_ID = 1556957776415035423


def _is_premium(member) -> bool:
    return any(r.id == PREMIUM_ROLE_ID for r in getattr(member, "roles", []))


def premium_cooldown(seconds: int):
    """Per-command cooldown for normal users. Premium members have NO cooldown.
    Each use of this decorator has its own timer (not shared with other commands)."""
    mapping = commands.CooldownMapping.from_cooldown(1, seconds, commands.BucketType.user)

    async def predicate(ctx: commands.Context) -> bool:
        if _is_premium(ctx.author):
            return True
        bucket = mapping.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit() if bucket else None
        if retry_after:
            # security.py's error handler shows "Wait Xm Ys before using this again".
            raise commands.CommandOnCooldown(bucket, retry_after, commands.BucketType.user)
        return True
    return commands.check(predicate)


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
    await ctx.send("❌ You haven't debuted yet. Type `rcdebut` first.")
    return False


def squad_order(cards: list) -> list:
    """The one squad order used everywhere: best OVR first, ties by name.
    `rcsquad` numbers and `rcms <from> <to>` both use this, so numbers match."""
    return sorted(cards, key=lambda c: (-int(c["ovr"]), c["playername_key"]))


def _card_line(card, extra: str = "", emojis: bool = True) -> str:
    role = ROLES.get(card_db.effective_role(card), "?")
    if emojis:
        prefix = f"{sl.role_emoji(card)} "
    else:
        prefix = ""   # plain text for non-premium members
    return f"{prefix}**{esc(card['playername'])}** · {card['ovr']} · {role}{extra}"


def _xi_embed(member: discord.abc.User, user_row) -> discord.Embed:
    """rcxi: the XI in batting order, one monospace row per player (see xi_display.py)."""
    import xi_display as xd
    xi = economy.get_xi(member.id)
    cap_key = user_row["captain_key"]
    cards = sl.xi_cards(member.id)
    lines = [f"Team OVR **{sl.average_ovr(cards)}**  ·  CHEM **{sl.chem_of(cards)}**", ""]
    for slot in range(1, economy.XI_SIZE + 1):
        key = xi.get(slot)
        card = card_db.get_card(key) if key else None
        if card is None:
            lines.append(xd.empty_row(slot))
            continue
        lines.append(xd.player_row(
            xd.entry_from_card(card), slot=slot, captain=card["playername_key"] == cap_key,
        ))
    embed = discord.Embed(
        title=f"{esc(user_row['team_name'])} — Playing XI",
        description="\n".join(lines),
        color=discord.Color.blue(),
    )
    problem = sl.xi_problem(member.id)
    embed.set_footer(text=(
        (f"⚠️ {problem}" if problem else "✅ Ready to play") + f" • {xd.COLUMNS_NOTE}"
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
            f"`{start + i + 1}.` " + _card_line(c, "  ✅ XI" if c["playername_key"] in self.in_xi else "")
            for i, c in enumerate(chunk)
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
        return "❌ You don't own any players yet. Try `rcstarterpack`."
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


def _show_embed(card) -> discord.Embed:
    """The embed body `rcshow` displays for one card (pulled out so the
    picker view below can rebuild it for whichever card is on screen)."""
    styles = [s for s in (card["playstyle1"], card["playstyle2"]) if s]
    desc = (
        f"OVR **{card['ovr']}** · BAT **{card['bat']}** · BOWL **{card['bowl']}**\n"
        f"{ROLES.get(card_db.effective_role(card), '?')}"
        + (f" · {HANDS.get(card['batting_hand'], '')} bat" if card["batting_hand"] else "")
        + (f" · {card_db.effective_bowling_type(card)}" if card_db.effective_bowling_type(card) else "")
        + f"\n{card['country_emoji']} {esc(card['country'])}"
    )
    if styles:
        desc += "\n\n**Playstyles**\n" + "\n".join(f"• **{esc(s)}**" for s in styles)
    desc += f"\n\nBuy **{economy.fmt_coins(sl.buy_price(card))}** · Sell **{economy.fmt_coins(sl.sell_value(card))}**"
    embed = discord.Embed(title=esc(card["playername"]), description=desc, color=discord.Color.gold())
    return embed


class ShowPickerView(OwnedView):
    """rcshow with 2+ name matches (e.g. "Virat Kohli" and "Virat Kohli
    (HW)"): ◀ ▶ flips between them instead of forcing a longer / exact
    search string."""

    def __init__(self, viewer_id: int, cards: list):
        super().__init__(viewer_id, timeout=90)
        self.cards = cards
        self.idx = 0
        self._sync()

    def _sync(self):
        self.prev_btn.disabled = self.idx <= 0
        self.next_btn.disabled = self.idx >= len(self.cards) - 1

    def build(self) -> discord.Embed:
        card = self.cards[self.idx]
        embed = _show_embed(card)
        embed.set_footer(text=f"Match {self.idx + 1}/{len(self.cards)} — ◀ ▶ to browse other cards with this name")
        return embed

    async def _render(self, interaction: discord.Interaction):
        await interaction.response.defer()
        card = self.cards[self.idx]
        embed = self.build()
        url = card_cache.cached_url(card["image_path"])
        if url:
            embed.set_image(url=url)
            await interaction.edit_original_response(embed=embed, attachments=[], view=self)
            return
        file = await card_cache.card_file(card["image_path"])
        if file:
            embed.set_image(url=f"attachment://{file.filename}")
            msg = await interaction.edit_original_response(embed=embed, attachments=[file], view=self)
            card_cache.remember(card["image_path"], msg)
        else:
            await interaction.edit_original_response(embed=embed, attachments=[], view=self)

    @ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.idx = max(0, self.idx - 1)
        self._sync()
        await self._render(interaction)

    @ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.idx = min(len(self.cards) - 1, self.idx + 1)
        self._sync()
        await self._render(interaction)


class ViewPickerView(OwnedView):
    """rcview with 2+ owned matches: same ◀ ▶ browsing as ShowPickerView,
    but each card's content (image or text fallback) comes from the cog's
    `_rcview_content`, since it needs the caller's personal career stats."""

    def __init__(self, viewer_id: int, cards: list, cog: "SquadCog"):
        super().__init__(viewer_id, timeout=90)
        self.cards = cards
        self.idx = 0
        self.cog = cog
        self._sync()

    def _sync(self):
        self.prev_btn.disabled = self.idx <= 0
        self.next_btn.disabled = self.idx >= len(self.cards) - 1

    async def _render(self, interaction: discord.Interaction):
        await interaction.response.defer()
        card = self.cards[self.idx]
        kind, content = await self.cog._rcview_content(interaction.user, card)
        if kind == "image":
            file = discord.File(io.BytesIO(content), filename="rcview.png")
            await interaction.edit_original_response(embed=None, attachments=[file], view=self)
        else:
            await interaction.edit_original_response(embed=content, attachments=[], view=self)

    @ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.idx = max(0, self.idx - 1)
        self._sync()
        await self._render(interaction)

    @ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.idx = min(len(self.cards) - 1, self.idx + 1)
        self._sync()
        await self._render(interaction)


class SquadCog(commands.Cog, name="Squad"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -- challenge a player (rcmp @user <overs>) ---------------------------
    # security.LOCKED_CHANNEL_IDS blocks this in 2 channels; also covers "rcmp ranked":
    # both paths go through this one command, so one check guards both.
    @commands.command(name="rcmp")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @security.match_channel_check()
    async def rcmp(self, ctx: commands.Context, opponent: Union[discord.Member, str], overs: int = 5):
        """Challenge another player: `rcmp @user 5`. Ranked matchmaking: `rcmp ranked`."""
        from views import AcceptDeclineView, active_games
        from game import GameState
        from embeds import build_match_invite_embed
        from data import random_match_conditions

        if isinstance(opponent, str):
            if opponent.strip().lower() == "ranked":
                cog = self.bot.get_cog("Ranked")
                if cog is None:
                    await ctx.send("❌ Ranked mode is not available right now.")
                    return
                await cog.start_ranked(ctx)
                return
            await ctx.send("❌ Use `rcmp @user 5` to challenge someone, or `rcmp ranked` for ranked matchmaking.")
            return

        challenger = ctx.author
        try:
            from ranked_cog import in_queue
            for who, label in ((challenger, "You are"), (opponent, f"{opponent.display_name} is")):
                if in_queue(who.id):
                    await ctx.send(f"❌ {label} searching for a ranked match right now.")
                    return
        except ImportError:
            pass
        if opponent.bot:
            await ctx.send("❌ You can't challenge a bot.")
            return
        if opponent.id == challenger.id:
            await ctx.send("❌ You can't challenge yourself.")
            return
        if not 1 <= overs <= 20:
            await ctx.send("❌ Overs must be between 1 and 20. Example: `rcmp @user 5`")
            return
        if ctx.channel.id in active_games:
            await ctx.send("❌ A match is already running in this channel. Use `rccancel` to end it.")
            return
        if _in_match(self.bot, challenger.id):
            await ctx.send("❌ You are already in a match.")
            return
        if _in_match(self.bot, opponent.id):
            await ctx.send(f"❌ {opponent.display_name} is already in a match.")
            return
        for who, label in ((challenger, "You"), (opponent, opponent.display_name)):
            if not economy.user_exists(who.id):
                await ctx.send(f"❌ {label} must debut first. Use `rcdebut`.")
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
        import time as _time
        from embeds import INVITE_SECONDS
        embed = build_match_invite_embed(
            challenger, opponent, overs, conditions, gif_url=conditions.get("gif_url"),
            teams=(game.teams.get(challenger.id), game.teams.get(opponent.id)),
            expires_at=int(_time.time()) + INVITE_SECONDS,
        )
        invite_view = AcceptDeclineView(game)
        # keep the message so the card can show "Expired" when nobody answers
        invite_view.message = await ctx.send(
            content=opponent.mention,
            embed=embed,
            view=invite_view,
        )

    # -- end / resume a stuck match ----------------------------------------
    @commands.command(name="rccancel")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rccancel(self, ctx: commands.Context):
        """End the match running in this channel (either player, or a moderator)."""
        from views import active_games
        game = active_games.get(ctx.channel.id)
        if game is None:
            await ctx.send("❌ There is no match running in this channel.")
            return
        is_player = ctx.author.id in (game.challenger.id, game.opponent.id)
        is_mod = getattr(ctx.author.guild_permissions, "manage_messages", False) if ctx.guild else False
        if getattr(game, "ranked", False):
            is_mod = False   # ranked: only the two players can end it (a mod of one server can't void a result)
        if not (is_player or is_mod):
            await ctx.send("🔒 Only the two players (or a moderator) can cancel this match.")
            return
        active_games.pop(ctx.channel.id, None)
        game._afk_sent = True   # stops any pending timeout from fining anyone
        if getattr(game, "ranked", False) and is_player:
            # Ranked: cancelling = forfeit (-1 star for the one who cancelled, +1 for the other).
            from views import _ranked_finish, _ranked_announce
            other = game.opponent if game.challenger.id == ctx.author.id else game.challenger
            out = game.__dict__.get("mirror") or ctx.channel   # cross-server: tell both channels
            await out.send("🏳️ Ranked match forfeited.")
            _ranked_finish(game, other.id, ctx.author.id)
            await _ranked_announce(out, game)
            return
        await ctx.send("Match cancelled. No result was recorded.")

    @commands.command(name="rcresume")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcresume(self, ctx: commands.Context):
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
        await _resume_prompt(game.__dict__.get("mirror") or ctx.channel, game)   # cross-server: both channels

    # -- debut -----------------------------------------------------------
    @commands.command(name="rcdebut")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rcdebut(self, ctx: commands.Context):
        """Create your team."""
        default = security.clean_team_name(f"{ctx.author.display_name} XI")[0] or f"Team {ctx.author.id % 10000}"
        if not economy.create_user(ctx.author.id, default):
            await ctx.send("You have already debuted. Use `rcprofile` to see your team.")
            return
        economy.add_pack(ctx.author.id, "debut", 1)   # one-time: create_user only succeeds once per user
        # Welcome image (falls back to the plain text message below if it can't be drawn)
        buf = None
        try:
            buf = await asyncio.to_thread(
                build_debut_image, ctx.author.display_name, default, f"{economy.START_PURSE:,}"
            )
        except Exception as e:
            print(f"[rcdebut] image failed: {e}")
        if buf is not None:
            await ctx.send(
                f"{ctx.author.mention} your team **{esc(default)}** is created with "
                f"**{economy.fmt_coins(economy.START_PURSE)}** {JOIN_MAIN_LINK}\n"
                f"You also got 1 `debut pack`. Open it with `rcopen debut` in the main server.",
                file=discord.File(buf, filename=DEBUT_IMG_NAME),
                allowed_mentions=discord.AllowedMentions(users=[ctx.author]),
            )
            return
        await ctx.send(
            f"Welcome, **{esc(ctx.author.display_name)}**! Your team **{esc(default)}** is created "
            f"with **{economy.fmt_coins(economy.START_PURSE)}** {JOIN_MAIN_LINK}\n"
            f"You also got 1 `debut pack`. Open it with `rcopen debut` in the main server.\n"
            f"Next: `rcstarterpack` to get your first players, then `rcxi` to see your XI.\n"
            f"Change your team name with `rcteamname <name>`."
        )

    # -- starter pack ----------------------------------------------------
    @commands.command(name="rcstarterpack")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rcstarterpack(self, ctx: commands.Context):
        """Claim your one-time starter squad."""
        if not await _need_debut(ctx):
            return
        user = economy.get_user(ctx.author.id)
        if user["starter_claimed"]:
            await ctx.send("You already claimed your starter pack. Try `rcpack` or `rcdaily`.")
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
        team_ovr = sl.average_ovr(xi)
        embed = discord.Embed(
            title="🎁 Starter Pack opened!",
            description=f"Team **{esc(user['team_name'])}** · Team OVR **{team_ovr}** · {len(xi)} players",
            color=discord.Color.gold(),
        )
        embed.set_footer(text="See your team: rcxi • Get coins: rcdaily • Buy packs: rcpack")
        buf = None
        try:
            buf = await asyncio.to_thread(build_team_image, xi, user["team_name"], team_ovr)
        except Exception as e:
            print(f"[rcstarterpack] collage failed: {e}")
        if buf is not None:
            embed.set_image(url=f"attachment://{TEAM_IMG_NAME}")
            await ctx.send(embed=embed, file=discord.File(buf, filename=TEAM_IMG_NAME))
        else:   # image failed -> still show the players as text
            show_emoji = _is_premium(ctx.author)   # premium role only
            embed.description += "\n\n" + "\n".join(_card_line(c, emojis=show_emoji) for c in xi)
            await ctx.send(embed=embed)

    # -- squad list ------------------------------------------------------
    @commands.command(name="rcsquad")
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def rcsquad(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show your squad (or another player's)."""
        target = member or ctx.author
        if target.bot:
            await ctx.send("Bots don't have squads.")
            return
        user = economy.get_user(target.id)
        if user is None:
            await ctx.send(f"**{esc(target.display_name)}** hasn't debuted yet.")
            return
        cards = squad_order(sl.owned_cards(target.id))
        view = SquadView(ctx.author.id, user["team_name"], cards, set(economy.get_xi(target.id).values()))
        view.message = await ctx.send(embed=view.build(), view=view)

    # -- show a player ---------------------------------------------------
    @commands.command(name="rcshow")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcshow(self, ctx: commands.Context, *, player: str):
        """Show a player's card and stats."""
        query = security.clean_input(player)
        found = await asyncio.to_thread(card_db.find_cards, query, 8)
        if not found:
            await ctx.send(f"❌ No player found matching `{sl.discord_safe(query)}`.")
            return
        # Every card whose name contains the typed text, exact match first
        # (find_cards already orders it that way). More than one -> ◀ ▶ picker,
        # so "Virat Kohli" also lets you flip to "Virat Kohli (S1)", "(HW)"...
        cards = found

        if len(cards) == 1:
            embed = _show_embed(cards[0])
            await card_cache.send_embed(ctx, embed, cards[0]["image_path"])   # cached link = no upload
            return

        view = ShowPickerView(ctx.author.id, cards)
        embed = view.build()
        url = card_cache.cached_url(cards[0]["image_path"])
        if url:
            embed.set_image(url=url)
            view.message = await ctx.send(embed=embed, view=view)
        else:
            file = await card_cache.card_file(cards[0]["image_path"])
            if file:
                embed.set_image(url=f"attachment://{file.filename}")
                view.message = await ctx.send(embed=embed, file=file, view=view)
                card_cache.remember(cards[0]["image_path"], view.message)
            else:
                view.message = await ctx.send(embed=embed, view=view)

    async def _rcview_content(self, author, card):
        """Build what `rcview` shows for ONE card: ('image', png_bytes) if the
        drawn card worked, else ('embed', discord.Embed) as the text fallback.
        Shared by the plain command and ViewPickerView's ◀ ▶ so every card in
        a multi-match picker renders the same way a single match would."""
        from career_stats import get_career_personal, batting_figures, bowling_figures

        career = await asyncio.to_thread(get_career_personal, author.id, card["playername_key"])
        bat = batting_figures(career)
        bowl = bowling_figures(career)

        width = 24
        rows = ["Batting".ljust(width) + "Bowling"]
        for (bl, bv), (wl, wv) in zip(bat, bowl):
            rows.append(f"{bl}: {bv}".ljust(width) + f"{wl}: {wv}")
        table = "\n".join(rows)

        header = f"OVR {card['ovr']}  BAT {card['bat']}  BOWL {card['bowl']}"
        lines = [header]
        lines.append(f"Owner: {esc(author.display_name)}")
        lines.append(f"Value: {economy.fmt_coins(sl.sell_value(card))}")
        styles = await asyncio.to_thread(card_db.get_player_narratives, card["playername_key"])
        if styles:   # only cards that have a playstyle/narrative
            lines.append("Playstyle: " + " · ".join(styles))
        # Preferred look: one image card. If anything goes wrong while drawing it,
        # fall back to the old text embed below so rcview never breaks.
        try:
            from rcview_image import generate_view_card
            png = await asyncio.to_thread(
                generate_view_card,
                card["playername"], card["ovr"], card["bat"], card["bowl"],
                author.display_name, f"{sl.sell_value(card):,} coins",
                bat, bowl, card["image_path"], styles,
            )
            return "image", png
        except Exception as e:
            print(f"[rcview] image card failed, using text embed: {e}")

        embed = discord.Embed(
            title=f"Player Stats: {esc(card['playername'])}",
            description="```\n" + "\n".join(lines) + "\n\n" + table + "\n```",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Your personal stats with this card.")
        return "embed", embed

    # -- player stats card (personal — only your own cards) --------------
    @commands.command(name="rcview")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcview(self, ctx: commands.Context, *, player: str):
        """Show a card you own, with YOUR career batting/bowling stats on it."""
        query = security.clean_input(player)
        found = await asyncio.to_thread(card_db.find_cards, query, 8)
        if not found:
            await ctx.send(f"❌ No player found matching `{sl.discord_safe(query)}`.")
            return

        # rcview is for cards you currently own only. Selling a card doesn't
        # erase your stats with it (they're kept in case you buy it back) —
        # it just means rcview won't show them to anyone while you don't
        # own it.
        owned = await asyncio.to_thread(sl.owned_cards, ctx.author.id)
        owned_keys = {c["playername_key"] for c in owned}
        owned_matches = [c for c in found if c["playername_key"] in owned_keys]
        if not owned_matches:
            await ctx.send(
                f"❌ You don't own **{esc(found[0]['playername'])}**, so `rcview` can't show it. "
                f"(Only cards you currently own can be viewed here.)"
            )
            return

        # Same as rcshow: every owned card matching the name, exact match first.
        # More than one -> ◀ ▶ picker (e.g. a normal + a Halloween card).
        cards = owned_matches

        if len(cards) == 1:
            kind, content = await self._rcview_content(ctx.author, cards[0])
            if kind == "image":
                await ctx.send(file=discord.File(io.BytesIO(content), filename="rcview.png"))
            else:
                await card_cache.send_embed(ctx, content, cards[0]["image_path"])   # cached link = no upload
            return

        view = ViewPickerView(ctx.author.id, cards, self)
        kind, content = await self._rcview_content(ctx.author, cards[0])
        if kind == "image":
            view.message = await ctx.send(file=discord.File(io.BytesIO(content), filename="rcview.png"), view=view)
        else:
            view.message = await ctx.send(embed=content, view=view)

    # -- player stats card (universal — premium only) ---------------------
    @commands.command(name="rcdata")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcdata(self, ctx: commands.Context, *, player: str):
        """Premium-only: a player's ALL-TIME stats across every owner ever."""
        from career_stats import get_career, batting_figures, bowling_figures

        if not _is_premium(ctx.author):
            await ctx.send(
                "❌ `rcdata` (universal, all-owners stats) is a premium-only command. "
                "Use `rcview <player>` for your own cards instead."
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
    @commands.command(name="rcxi", aliases=["rcbattingorder"])
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def rcxi(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show your playing XI in batting order."""
        target = member or ctx.author
        if target.id != ctx.author.id and not _is_premium(ctx.author):
            await ctx.send("💎 Only **Premium members** can see another player's XI. You can still use `rcxi` for your own XI.")
            return
        user = economy.get_user(target.id)
        if user is None:
            await ctx.send(f"**{esc(target.display_name)}** hasn't debuted yet.")
            return
        view = AutoXiView(ctx.author.id) if target.id == ctx.author.id else None
        await ctx.send(embed=_xi_embed(target, user), view=view)
        if target.id == ctx.author.id:
            await quest.track_and_notify(ctx, ctx.author.id, {"xi_view": 1})

    # -- XI image + team OVR ----------------------------------------------
    @commands.command(name="rcovr")
    @premium_cooldown(30 * 60)          # normal users: 30 min, Premium: none
    async def rcovr(self, ctx: commands.Context):
        """Your real playing XI as an image, with the team OVR."""
        if not await _need_debut(ctx):
            return
        user = economy.get_user(ctx.author.id)
        cards = await asyncio.to_thread(sl.xi_cards, ctx.author.id)   # batting order, current card images
        if not cards:
            await ctx.send("❌ Your XI is empty. Use `rcstarterpack` for your first players, or `rcautoxi`.")
            return
        team_ovr = sl.average_ovr(cards)
        problem = sl.xi_problem(ctx.author.id)
        embed = discord.Embed(
            title="🏏 Playing XI",
            description=f"Team **{esc(user['team_name'])}** · Team OVR **{team_ovr}** · {len(cards)} players",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=(f"⚠️ {problem}" if problem else "✅ Ready to play") + " • Edit XI: rcswap • rcautoxi")
        buf = None
        try:
            buf = await asyncio.to_thread(build_team_image, cards, user["team_name"], team_ovr)
        except Exception as e:
            print(f"[rcovr] collage failed: {e}")
        if buf is not None:
            embed.set_image(url=f"attachment://{TEAM_IMG_NAME}")
            await ctx.send(embed=embed, file=discord.File(buf, filename=TEAM_IMG_NAME))
        else:   # image failed -> show the normal text XI instead
            await ctx.send(embed=_xi_embed(ctx.author, user))

    @commands.command(name="rcautoxi")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rcautoxi(self, ctx: commands.Context):
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
    @commands.command(name="rcautoplay")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def rcautoplay(self, ctx: commands.Context):
        """(Premium) Let the bot play your side of the match for you. Type again to switch off."""
        if not _is_premium(ctx.author):
            await ctx.send("💎 `rcautoplay` is for **Premium members** only.")
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
            f"{note}\nType `rcautoplay` again to take control back."
        )
        autoplay_kick(game)

    @commands.command(name="rcswap")
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def rcswap(self, ctx: commands.Context, *, players: str):
        """Swap two players: `rcswap 3 5` (XI spots) or `rcswap Virat Kohli | Rohit Sharma`."""
        if not await _need_debut(ctx):
            return
        if _in_match(self.bot, ctx.author.id):
            await ctx.send("❌ You can't change your XI during a match.")
            return
        players = security.clean_input(players, 120)
        parts = [p.strip() for p in players.split("|")] if "|" in players else players.split()
        if len(parts) != 2:
            await ctx.send("Usage: `rcswap 3 5` or `rcswap Player One | Player Two`")
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
    @commands.command(name="rcsubs", aliases=["rcimpact"])
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def rcsubs(
        self,
        ctx: commands.Context,
        action: Optional[str] = None,
        *,
        player: Optional[str] = None,
    ):
        """Manage your Impact Player subs (bench players, max 4).
        `rcsubs` to view, `rcsubs add <player>`, `rcsubs remove <player>`, `rcsubs clear`."""
        if not await _need_debut(ctx):
            return
        action = (action or "list").strip().lower()

        if action in ("list", "show", "view"):
            subs = economy.get_impact_subs(ctx.author.id)
            if not subs:
                await ctx.send(
                    "🔁 You have no Impact Player subs set.\n"
                    "Add one with `rcsubs add <player name>` "
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
                await ctx.send("Usage: `rcsubs add <player name>`")
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
                await ctx.send("Usage: `rcsubs remove <player name>`")
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
            "Usage: `rcsubs` (view), `rcsubs add <player>`, `rcsubs remove <player>`, `rcsubs clear`."
        )

    # -- captain ---------------------------------------------------------
    @commands.command(name="rccaptain")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rccaptain(self, ctx: commands.Context, *, player: str):
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
        await quest.track_and_notify(ctx, ctx.author.id, {"captain_set": 1})

    # -- team name -------------------------------------------------------
    @commands.command(name="rcteamname")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def rcteamname(self, ctx: commands.Context, *, name: str):
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
    @commands.command(name="rcprofile")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def rcprofile(self, ctx: commands.Context, member: Optional[discord.Member] = None):
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
        if target.id == ctx.author.id:
            await quest.track_and_notify(ctx, ctx.author.id, {"profile_view": 1})

    # -- help ------------------------------------------------------------
    # Normal users: once every 2 minutes. Premium members: no cooldown at all.
    @commands.command(name="rchelp")
    @premium_cooldown(120)
    async def rchelp(self, ctx: commands.Context):
        """List the commands (text menu with a topic selector)."""
        import help_menu
        await help_menu.send_help(ctx)

    # ── Owner-only tools ─────────────────────────────────────────────────
    @commands.command(name="rcban")
    @security.owner_or_admin_role()
    async def rcban(self, ctx: commands.Context, member: discord.User, *, reason: str = "No reason"):
        """(Owner) Ban someone from using the bot."""
        if member.id == ctx.author.id or member.bot:
            await ctx.send("❌ Can't ban that user.")
            return
        security.ban_user(member.id, security.clean_input(reason, 150))
        await ctx.send(f"🚫 Banned **{esc(member.display_name)}**.")

    @commands.command(name="rcunban")
    @security.owner_or_admin_role()
    async def rcunban(self, ctx: commands.Context, member: discord.User):
        """(Owner) Let someone use the bot again."""
        security.unban_user(member.id)
        await ctx.send(f"✅ Unbanned **{esc(member.display_name)}**.")

    @commands.command(name="rcgivecoins")
    @security.owner_or_admin_role()
    async def rcgivecoins(self, ctx: commands.Context, member: discord.Member, amount: int):
        """(Owner) Give (or take with a negative number) coins."""
        if not economy.user_exists(member.id):
            await ctx.send("❌ That user hasn't debuted.")
            return
        if not -1_000_000_000 <= amount <= 1_000_000_000:
            await ctx.send("❌ Amount too large.")
            return
        bal = economy.add_coins(member.id, amount, f"Owner adjustment by {ctx.author.id}")
        await ctx.send(f"✅ {esc(member.display_name)} now has **{economy.fmt_coins(bal)}**.")

    @commands.command(name="rcgivecard")
    @security.owner_or_admin_role()
    async def rcgivecard(self, ctx: commands.Context, member: discord.Member, *, player: str):
        """(Owner) Give someone a player card."""
        card, err = sl.resolve_any(security.clean_input(player))
        if card is None:
            await ctx.send(f"❌ {err}")
            return
        if card_db.is_event_disabled(card):
            await ctx.send("❌ That card's event is disabled, so nobody can get it. Use `/eventenable` first.")
            return
        if economy.give_card(member.id, card["playername_key"]):
            await ctx.send(f"✅ Gave **{esc(card['playername'])}** to {esc(member.display_name)}.")
        else:
            await ctx.send("❌ They haven't debuted, already own that player, or their squad is full (25).")


async def setup(bot: commands.Bot):
    await bot.add_cog(SquadCog(bot))
