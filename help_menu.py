"""Text help menu for `rchelp` (replaces the old help image).

    rchelp  ->  first page lists EVERY command, grouped by topic.
                A "Select a help topic" menu under it opens one topic
                (Rewards, Squad, Economy, Packs, Match, Ranked ...) with a short
                explanation of each command.

Look is the same as `rccd`: a bold name, the command in monospace, and the
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
        ("Debut", "rcdebut", "Create your team. Do this first."),
        ("Starter Pack", "rcstarterpack", "Claim your one-time starter squad."),
        ("Coins", "rcbal", "See your coins and vouchers."),
        ("Profile", "rcprofile", "Your team profile: OVR, captain, streak and recent form."),
        ("Team Name", "rcteamname <name>", "Change your team name.", "rcteamname"),
    ]),
    "rewards": ("Rewards", "Daily, weekly, monthly and claim", [
        ("Daily", "rcdaily", "Daily coins and a random player (60-78 OVR). Every 7th day gives a streak bonus."),
        ("Weekly", "rcweekly", "Premium only. Weekly coins and a random player (80-83 OVR)."),
        ("Monthly", "rcmonthly", "Monthly coins and a random player (85-88 OVR)."),
        ("Claim", "rcclaim", "A free random player every hour. Premium members can claim every 35 minutes."),
        ("Cooldowns", "rccd", "Shows when daily, weekly, monthly and claim are ready again."),
        ("Quests", "rcquest", "Your daily quests and progress. Finishing a quest gives a voucher."),
    ]),
    "squad": ("Squad", "Your players, XI, captain and subs", [
        ("Squad", "rcsquad [@user]", "Show your squad, or another player's squad.", "rcsquad"),
        ("Player Card", "rcshow <player>", "Show any player's card and stats.", "rcshow"),
        ("Your Card Stats", "rcview <player>", "A card you own, with your own career batting and bowling stats.", "rcview"),
        ("All-Time Stats", "rcdata <player>", "Premium only. That card's stats across every owner.", "rcdata"),
        ("Playing XI", "rcxi", "Your playing XI in batting order. Premium members can use rcxi @user to see another XI."),
        ("XI Image", "rcovr", "Your playing XI as an image with the team OVR."),
        ("Auto XI", "rcautoxi", "Pick your best XI automatically."),
        ("Swap", "rcswap 3 5", "Swap two XI spots. You can also swap by name: rcswap Player A | Player B.", "rcswap"),
        ("Impact Subs", "rcsubs", "View your Impact Player subs (max 4). Use rcsubs add <player>, rcsubs remove <player> or rcsubs clear."),
        ("Captain", "rccaptain <player>", "Set your captain. The player must be in your XI.", "rccaptain"),
    ]),
    "economy": ("Economy", "Buy, sell, trade and leaderboard", [
        ("Coins", "rcbal", "See your coins and vouchers."),
        ("Buy a Player", "rcbuy <player>", "Buy a player card with coins.", "rcbuy"),
        ("Sell a Player", "rcsell <player>", "Sell one of your players. You get 60% of the buy price.", "rcsell"),
        ("Multi Sell", "rcms 1 20", "Sell players by squad number. This sells players 1 to 20 of rcsquad.", "rcms"),
        ("Pending Cards", "rcpending", "Cards that arrived while your squad was full. Sell them or replace a player."),
        ("Trade", "rctrade @user my player | their player", "Trade players of the same OVR. Each side pays a 5,000 coin fee.", "rctrade"),
        ("Leaderboard", "rcleaderboard ovr", "Top teams. Use rcleaderboard ovr or rcleaderboard coins.", "rcleaderboard"),
    ]),
    # "packs" page is built live, see _pack_rows()
    "packs": ("Packs", "Pack shop, prices and opening", []),
    "match": ("Match", "Play against other players", [
        ("Challenge", "rcmp @user <overs>", "Challenge a player. Overs can be 1 to 20, default is 5.", "rcmp"),
        ("Cancel", "rccancel", "End the match running in this channel. Either player or a moderator can use it."),
        ("Resume", "rcresume", "Re-send the current prompt if your match looks stuck."),
        ("Auto Play", "rcautoplay", "Premium only. The bot plays your side for you. Use it again to switch it off."),
    ]),
    "ranked": ("Ranked", "Ranked mode, tiers and rewards", [
        ("Find a Match", "rcmp ranked", "Join the ranked queue. Every ranked match is 2 overs against a player of a similar tier.", "rcmp ranked"),
        ("Your Rank", "rcrank [@user]", "Your tier, stars and season record as a card.", "rcrank"),
        ("Top Players", "rcranktop", "Global top 10 by stars this season."),
        ("Rank Rewards", "rcrankrewards", "Rewards for every tier, what you already claimed, and when the season ends."),
        ("Season", "rcseason", "The current ranked season and when it ends."),
    ]),
    "events": ("Events", "Passes, drafts and themes", [
        ("Star Pass", "rcstarpass", "Show the Star Pass. Use rcstarpass halloween to open an event pass by name."),
        ("Halloween Pass", "rchalloween", "Show the Halloween Pass."),
        ("Draft", "rcdraft", "Spend vouchers on a player draft. Use rcdraft halloween for an event draft."),
        ("Halloween Draft", "rchalloweendraft", "Open the Halloween Draft directly."),
        ("Summary Theme", "rcsb", "Open the match summary theme menu. Use rcsb use <name> to equip a theme and rcsb off to go back to normal."),
    ]),
    "other": ("Other", "Bot info and card list", [
        ("Bot Info", "rc about", "Bot latency, uptime and usage statistics.", "rc about"),
        ("All Cards", "/rcrarity", "Slash command. Lists all cards by OVR with BAT, BOWL and price. Only you can see it."),
        ("Signature Box", "rcbox", "Spin for the Signature card and 9 other rewards. Every reward drops once, spin 10 always gives the Signature card. `rcp` shows your RC Points.", "rcbox"),
        ("Playstyles", "rcplaystyles [name]", "Every playstyle badge with its name. Add a name to see that badge in Silver, Gold and Golden Pink.", "rcplaystyles"),
        ("Help", "rchelp", "This menu."),
    ]),
    "premium": ("Premium", "What Premium members get", [
        ("Weekly Reward", "rcweekly", "Weekly coins and a random 80-83 OVR player."),
        ("Faster Claim", "rcclaim", "Claim every 35 minutes instead of every hour."),
        ("All-Time Stats", "rcdata <player>", "A card's stats across every owner.", "rcdata"),
        ("View Other XI", "rcxi @user", "See another player's playing XI."),
        ("Auto Play", "rcautoplay", "The bot plays your side of the match for you."),
    ]),
}

ORDER = ["start", "rewards", "squad", "economy", "packs", "match", "ranked", "events", "other", "premium"]


# ── Packs page (live from squad_logic.PACKS) ────────────────────────────────

def _pack_rows() -> list[tuple]:
    import squad_logic as sl
    rows: list[tuple] = [
        ("Pack Shop", "rcpack", "Open the pack shop, pick a pack from the menu and press Confirm to buy."),
        ("Buy Directly", "rcpack <type>", "Buy a pack by name, for example rcpack gold.", "rcpack"),
        ("Open a Pack", "rcopen <type>", "Open a pack you own. A short animation plays, then your player appears.", "rcopen"),
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
        rows.append((spec["label"], f"rcpack {key}", text, ""))   # "" = not repeated on the first page
    rows.append(("Duplicates", "rcopen", "A player you already own is turned into coins automatically.", ""))
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
    """Called by the rchelp command."""
    name = ctx.author.display_name
    view = HelpView(ctx.author.id, name)
    view.message = await ctx.send(embed=overview_embed(name), view=view)
