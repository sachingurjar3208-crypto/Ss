"""Text help menu for `cshelp` (replaces the old help image).

    cshelp  ->  first page lists EVERY command, grouped by topic.
                A "Select a help topic" menu under it opens one topic
                (Rewards, Squad, Economy, Packs, Match, Ranked ...) with a short
                explanation of each command.

Look is the same as `cscd`: a bold name, the command in monospace, and the
explanation as a quote line under it. No emojis.

To change the help text, edit TOPICS below (nothing else needs to change).
The Packs page is built live from squad_logic.PACKS, so prices / OVR ranges /
packs switched off with `admin pack off` are always correct.
"""
from __future__ import annotations

import discord
from discord import ui

from security import OwnedView

MENU_TIMEOUT = 180.0
OVERVIEW_KEY = "all"

# Each topic: key -> (menu label, menu description, [(name, command, explanation[, overview text])])
# The optional 4th item is what the first page shows for that row (default: first word of command).
TOPICS: dict[str, tuple[str, str, list[tuple]]] = {
    "start": ("Start", "Create your team and first players", [
        ("Debut", "csdebut", "Create your team. Do this first."),
        ("Starter Pack", "csstarterpack", "Claim your one-time starter squad."),
        ("Coins", "csbal", "See your coins and vouchers."),
        ("Profile", "csprofile", "Your team profile: OVR, captain, streak and recent form."),
        ("Team Name", "csteamname <name>", "Change your team name.", "csteamname"),
    ]),
    "rewards": ("Rewards", "Daily, weekly, monthly and claim", [
        ("Daily", "csdaily", "Daily coins and a random player (60-78 OVR). Every 7th day gives a streak bonus."),
        ("Weekly", "csweekly", "Premium only. Weekly coins and a random player (80-83 OVR)."),
        ("Monthly", "csmonthly", "Monthly coins and a random player (85-88 OVR)."),
        ("Claim", "csclaim", "A free random player every hour. Premium members can claim every 35 minutes."),
        ("Cooldowns", "cscd", "Shows when daily, weekly, monthly and claim are ready again."),
        ("Quests", "csquest", "Your daily quests and progress. Finishing a quest gives a voucher."),
    ]),
    "squad": ("Squad", "Your players, XI, captain and subs", [
        ("Squad", "cssquad [@user]", "Show your squad, or another player's squad.", "cssquad"),
        ("Player Card", "csshow <player>", "Show any player's card and stats.", "csshow"),
        ("Your Card Stats", "csview <player>", "A card you own, with your own career batting and bowling stats.", "csview"),
        ("All-Time Stats", "csdata <player>", "Premium only. That card's stats across every owner.", "csdata"),
        ("Playing XI", "csxi", "Your playing XI in batting order. Premium members can use csxi @user to see another XI."),
        ("XI Image", "csovr", "Your playing XI as an image with the team OVR."),
        ("Auto XI", "csautoxi", "Pick your best XI automatically."),
        ("Swap", "csswap 3 5", "Swap two XI spots. You can also swap by name: csswap Player A | Player B.", "csswap"),
        ("Impact Subs", "cssubs", "View your Impact Player subs (max 4). Use cssubs add <player>, cssubs remove <player> or cssubs clear."),
        ("Captain", "cscaptain <player>", "Set your captain. The player must be in your XI.", "cscaptain"),
    ]),
    "economy": ("Economy", "Buy, sell, trade and leaderboard", [
        ("Coins", "csbal", "See your coins and vouchers."),
        ("Buy a Player", "csbuy <player>", "Buy a player card with coins.", "csbuy"),
        ("Sell a Player", "cssell <player>", "Sell one of your players. You get 60% of the buy price.", "cssell"),
        ("Multi Sell", "csms 1 20", "Sell players by squad number. This sells players 1 to 20 of cssquad.", "csms"),
        ("Pending Cards", "cspending", "Cards that arrived while your squad was full. Sell them or replace a player."),
        ("Trade", "cstrade @user my player | their player", "Trade players of the same OVR. Each side pays a 5,000 coin fee.", "cstrade"),
        ("Leaderboard", "csleaderboard ovr", "Top teams. Use csleaderboard ovr or csleaderboard coins.", "csleaderboard"),
    ]),
    # "packs" page is built live, see _pack_rows()
    "packs": ("Packs", "Pack shop, prices and opening", []),
    "match": ("Match", "Play against other players", [
        ("Challenge", "csmp @user <overs>", "Challenge a player. Overs can be 1 to 20, default is 5.", "csmp"),
        ("Cancel", "cscancel", "End the match running in this channel. Either player or a moderator can use it."),
        ("Resume", "csresume", "Re-send the current prompt if your match looks stuck."),
        ("Auto Play", "csautoplay", "Premium only. The bot plays your side for you. Use it again to switch it off."),
    ]),
    "ranked": ("Ranked", "Ranked mode, tiers and rewards", [
        ("Find a Match", "csmp ranked", "Join the ranked queue. Every ranked match is 2 overs against a player of a similar tier.", "csmp ranked"),
        ("Your Rank", "csrank [@user]", "Your tier, stars and season record as a card.", "csrank"),
        ("Top Players", "csranktop", "Global top 10 by stars this season."),
        ("Rank Rewards", "csrankrewards", "Rewards for every tier, what you already claimed, and when the season ends."),
        ("Season", "csseason", "The current ranked season and when it ends."),
    ]),
    "events": ("Events", "Passes, drafts and themes", [
        ("Star Pass", "csstarpass", "Show the Star Pass. Use csstarpass halloween to open an event pass by name."),
        ("Halloween Pass", "cshalloween", "Show the Halloween Pass."),
        ("Draft", "csdraft", "Spend vouchers on a player draft. Use csdraft halloween for an event draft."),
        ("Halloween Draft", "cshalloweendraft", "Open the Halloween Draft directly."),
        ("Summary Theme", "cssb", "Open the match summary theme menu. Use cssb use <name> to equip a theme and cssb off to go back to normal."),
    ]),
    "other": ("Other", "Bot info and card list", [
        ("Bot Info", "cs about", "Bot latency, uptime and usage statistics.", "cs about"),
        ("All Cards", "/csrarity", "Slash command. Lists all cards by OVR with BAT, BOWL and price. Only you can see it."),
        ("Help", "cshelp", "This menu."),
    ]),
    "premium": ("Premium", "What Premium members get", [
        ("Weekly Reward", "csweekly", "Weekly coins and a random 80-83 OVR player."),
        ("Faster Claim", "csclaim", "Claim every 35 minutes instead of every hour."),
        ("All-Time Stats", "csdata <player>", "A card's stats across every owner.", "csdata"),
        ("View Other XI", "csxi @user", "See another player's playing XI."),
        ("Auto Play", "csautoplay", "The bot plays your side of the match for you."),
    ]),
}

ORDER = ["start", "rewards", "squad", "economy", "packs", "match", "ranked", "events", "other", "premium"]


# ── Packs page (live from squad_logic.PACKS) ────────────────────────────────

def _pack_rows() -> list[tuple]:
    import squad_logic as sl
    rows: list[tuple] = [
        ("Pack Shop", "cspack", "Open the pack shop, pick a pack from the menu and press Confirm to buy."),
        ("Buy Directly", "cspack <type>", "Buy a pack by name, for example cspack gold.", "cspack"),
        ("Open a Pack", "csopen <type>", "Open a pack you own. A short animation plays, then your player appears.", "csopen"),
    ]
    try:
        import economy
        off = economy.packs_off()
    except Exception:
        off = set()
    for key, spec in sl.PACKS.items():
        if spec.get("shop") is False:
            continue
        try:
            if not sl.pack_available(key):
                continue
        except Exception:
            continue
        lo, hi = spec["range"]
        text = f"{spec['price']:,} coins. One player of {lo}-{hi} OVR."
        if spec.get("event"):
            text += " Only event players."
        if key in off:
            text += " Not available right now."
        rows.append((spec["label"], f"cspack {key}", text, ""))   # "" = not repeated on the first page
    rows.append(("Duplicates", "csopen", "A player you already own is turned into coins automatically.", ""))
    return rows


def _rows(key: str) -> list[tuple]:
    return _pack_rows() if key == "packs" else TOPICS[key][2]


# ── Embeds ───────────────────────────────────────────────────────────────────

def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _overview_names(key: str) -> list[str]:
    seen: list[str] = []
    for row in _rows(key):
        if len(row) >= 4:
            short = row[3]
        else:
            short = row[1].split(" ")[0]
        if short and short not in seen:
            seen.append(short)
    return seen


def overview_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Help",
        description="Every command is listed below, grouped by topic.\n"
                    "Use the menu under this message to see how each command works.",
        color=discord.Color.teal(),
    )
    for key in ORDER:
        names = _overview_names(key)
        if names:
            embed.add_field(name=TOPICS[key][0], value=_fit(" ".join(f"`{n}`" for n in names), 1024), inline=False)
    embed.set_footer(text=f"Requested by: {user_name}")
    return embed


def topic_embed(key: str, user_name: str) -> discord.Embed:
    title = TOPICS[key][0]
    blocks = [f"**{row[0]}** `{row[1]}`\n> {row[2]}" for row in _rows(key)]
    embed = discord.Embed(title=f"Help: {title}", description=_fit("\n\n".join(blocks), 4000),
                          color=discord.Color.teal())
    embed.set_footer(text=f"Requested by: {user_name}  |  Pick another topic below, or All Commands to go back")
    return embed


def build_embed(key: str, user_name: str) -> discord.Embed:
    return overview_embed(user_name) if key == OVERVIEW_KEY else topic_embed(key, user_name)


# ── Menu ─────────────────────────────────────────────────────────────────────

class HelpSelect(ui.Select):
    def __init__(self, current: str = OVERVIEW_KEY):
        options = [discord.SelectOption(label="All Commands", value=OVERVIEW_KEY,
                                        description="Every command in one list", default=current == OVERVIEW_KEY)]
        for key in ORDER:
            label, desc, _ = TOPICS[key]
            options.append(discord.SelectOption(label=label, value=key, description=desc[:100],
                                                default=key == current))
        super().__init__(placeholder="Select a help topic", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        view: HelpView = self.view            # type: ignore[assignment]
        view.rebuild(key)
        await interaction.response.edit_message(embed=build_embed(key, view.user_name), view=view)


class HelpView(OwnedView):
    def __init__(self, user_id: int, user_name: str):
        super().__init__(user_id, timeout=MENU_TIMEOUT)
        self.user_name = user_name
        self.add_item(HelpSelect())

    def rebuild(self, current: str) -> None:
        self.clear_items()
        self.add_item(HelpSelect(current))


async def send_help(ctx) -> None:
    """Called by the cshelp command."""
    name = ctx.author.display_name
    view = HelpView(ctx.author.id, name)
    view.message = await ctx.send(embed=overview_embed(name), view=view)
