"""Entry point for the CricStar Discord bot.

Wires the cricket match flow plus the card maker, the squad/economy system
(csdebut, csxi, cspack, ...) and the bot-wide security layer (security.py).
"""

import discord
from discord.ext import commands

import economy
import security
import squad_logic
from embeds import build_match_invite_embed
from data import random_match_conditions
from game import GameState
from match_records import init_match_records_db
from practice_views import active_practice
from views import AcceptDeclineView, active_games


intents = discord.Intents.default()
intents.message_content = True
intents.members = True


def _new_game(challenger: discord.Member, opponent: discord.Member, overs: int) -> GameState:
    """Both teams are built fresh from each player's own playing XI, so a
    match can never change anybody's saved squad."""
    game = GameState(challenger, opponent, overs)
    game.teams[challenger.id] = squad_logic.build_match_team(challenger.id)
    game.teams[opponent.id] = squad_logic.build_match_team(opponent.id)
    return game


def _user_in_match(user_id: int) -> bool:
    for store in (active_games, active_practice):
        for obj in list(store.values()):
            for attr in ("challenger", "opponent", "user", "player", "author", "owner"):
                who = getattr(obj, attr, None)
                if who is not None and getattr(who, "id", who) == user_id:
                    return True
    return False


class CricStarBot(commands.Bot):
    async def setup_hook(self):
        init_match_records_db()
        economy.init_economy_db()
        security.load_bans()
        # One check that every text command passes through: ban list,
        # rate limit, server-only. See security.py.
        self.add_check(security.global_check)
        await self.load_extension("cardmaker_cog")
        await self.load_extension("squad_cog")
        await self.load_extension("economy_cog")
        # Slash commands (/cardmaker, /editcard, /bgadder, /foregroundfix)
        # need a tree sync before Discord will show them. Syncing on every
        # boot is fine at this bot's size; if command changes stop showing
        # up, restart the bot once — Discord can take a few minutes to
        # propagate a sync globally.
        await self.tree.sync()

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        # Friendly one-line errors; never a traceback in chat; unknown
        # commands (any normal message, as the prefix is empty) are ignored.
        await security.handle_command_error(ctx, error)


# An empty prefix allows natural chat commands such as `csmp @user 5`.
# Keep `!` as an alias so existing users can continue using `!csmp`.
# allowed_mentions: the bot can ping single users but never @everyone, @here or roles.
bot = CricStarBot(
    command_prefix=("", "!"),
    intents=intents,
    allowed_mentions=security.SAFE_MENTIONS,
)


@bot.group(name="cs", invoke_without_command=True)
async def cs(ctx: commands.Context):
    """Match controls.  Use ``!cs cancel`` to cancel your active match."""
    if ctx.invoked_subcommand is None:
        await ctx.send("Use `!csmp @user <overs>` to challenge another player.")


@cs.command(name="cancel")
async def cs_cancel(ctx: commands.Context):
    """Cancel the active match in this channel (only its players or a moderator)."""
    game = active_games.get(ctx.channel.id)
    practice = active_practice.get(ctx.channel.id)
    if game is None and practice is None:
        await ctx.send("There is no active match in this channel.")
        return
    is_player = False
    for obj in (game, practice):
        for attr in ("challenger", "opponent", "user", "player", "author", "owner"):
            who = getattr(obj, attr, None)
            if who is not None and getattr(who, "id", who) == ctx.author.id:
                is_player = True
    is_mod = ctx.author.guild_permissions.manage_messages or await ctx.bot.is_owner(ctx.author)
    if not (is_player or is_mod):
        await ctx.send("🔒 Only the players in this match (or a moderator) can cancel it.")
        return
    active_games.pop(ctx.channel.id, None)
    active_practice.pop(ctx.channel.id, None)
    await ctx.send("🛑 Match cancelled.")


@cs.command(name="resume")
@commands.cooldown(1, 10, commands.BucketType.user)
async def cs_resume(ctx: commands.Context):
    """Manually re-send whatever prompt should currently be showing, if a
    match ever looks stuck. Safe to use any time — never repeats a run,
    wicket or over; it only re-displays the current step."""
    game = active_games.get(ctx.channel.id)
    if game is None:
        await ctx.send("There is no active match in this channel to resume.")
        return
    is_player = False
    for attr in ("challenger", "opponent"):
        who = getattr(game, attr, None)
        if who is not None and getattr(who, "id", who) == ctx.author.id:
            is_player = True
    is_mod = ctx.author.guild_permissions.manage_messages or await ctx.bot.is_owner(ctx.author)
    if not (is_player or is_mod):
        await ctx.send("🔒 Only the players in this match (or a moderator) can resume it.")
        return
    from views import _resume_prompt
    await ctx.send("🔄 Resuming your match…")
    await _resume_prompt(ctx.channel, game)


@bot.command(name="csmp")
@commands.cooldown(1, 10, commands.BucketType.user)
async def csmp(ctx: commands.Context, opponent: discord.Member, overs: int = 20):
    """Challenge a member to a match: ``!csmp @user [overs]``."""
    if opponent.id == ctx.author.id:
        await ctx.send("You cannot challenge yourself.")
        return
    if opponent.bot:
        await ctx.send("You cannot challenge a bot.")
        return
    if not 1 <= overs <= 50:
        await ctx.send("Overs must be between 1 and 50.")
        return
    if ctx.channel.id in active_games or ctx.channel.id in active_practice:
        await ctx.send("A match is already active in this channel. Use `!cs cancel` first.")
        return
    # Both players need a team: debuted, not banned, full XI, not already in a match.
    for member, who in ((ctx.author, "You"), (opponent, opponent.display_name)):
        if security.is_banned(member.id):
            await ctx.send("❌ That player can't play matches.")
            return
        if not economy.user_exists(member.id):
            await ctx.send(f"❌ {security.esc(who)} hasn't debuted yet. Type `csdebut` first.")
            return
        problem = squad_logic.xi_problem(member.id)
        if problem:
            await ctx.send(f"❌ {security.esc(who)} {problem}. Fix it with `csxi`, `csswap` or `csautoxi`.")
            return
        if _user_in_match(member.id):
            await ctx.send(f"❌ {security.esc(who)} is already in another match.")
            return

    game = _new_game(ctx.author, opponent, overs)
    active_games[ctx.channel.id] = game
    embed = build_match_invite_embed(
        ctx.author, opponent, overs, random_match_conditions()
    )
    view = AcceptDeclineView(game)
    message = await ctx.send(embed=embed, view=view)
    view.message = message


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


if __name__ == "__main__":
    token = __import__("os").environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN before starting the bot.")
    bot.run(token)
