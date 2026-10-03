"""
Interactive Discord UI for /cs setxi and /cs myxi.
"""
import discord
from database import (
    get_user_cards, get_user_xi, set_user_xi, clear_user_xi,
    validate_xi, auto_build_xi, xi_total, get_role_category,
    BOWL_ROLES,
)

ROLE_CFG = {
    "BAT":  {"emoji": "🏏", "label": "Batsmen",        "min": 3, "max": 4},
    "WK":   {"emoji": "🧤", "label": "Wicket Keepers",  "min": 1, "max": 2},
    "AR":   {"emoji": "🔄", "label": "All Rounders",    "min": 2, "max": 3},
    "BOWL": {"emoji": "🎯", "label": "Bowlers",         "min": 3, "max": 4},
}

NUM_EMOJI = [
    "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
    "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟", "🔢",
]

NAVY   = discord.Color.from_rgb(8, 22, 60)
GREEN  = discord.Color.from_rgb(30, 160, 80)
GOLD   = discord.Color.from_rgb(212, 175, 55)
RED    = discord.Color.from_rgb(180, 30, 30)


def _bar(filled: int, total: int, width: int = 8) -> str:
    f = round(filled / total * width) if total else 0
    return "█" * f + "░" * (width - f)


def _cat_status(xi: dict, cat: str) -> str:
    cfg = ROLE_CFG[cat]
    n, mn, mx = len(xi.get(cat, [])), cfg["min"], cfg["max"]
    if n == 0:
        bar = "░" * 5
        tick = "⬜"
    elif n < mn:
        bar = _bar(n, mn, 5)
        tick = "🟡"
    elif n <= mx:
        bar = "█" * 5
        tick = "✅"
    else:
        bar = "█" * 5
        tick = "🔴"
    return f"{tick} {cfg['emoji']} **{cfg['label']}** [{n}/{mn}–{mx}] `{bar}`"


_COUNTRY_FLAG: dict[str, str] = {
    "india":         "🇮🇳",
    "australia":     "🇦🇺",
    "england":       "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "new zealand":   "🇳🇿",
    "south africa":  "🇿🇦",
    "pakistan":      "🇵🇰",
    "sri lanka":     "🇱🇰",
    "bangladesh":    "🇧🇩",
    "afghanistan":   "🇦🇫",
    "west indies":   "🏝️",
    "zimbabwe":      "🇿🇼",
    "ireland":       "🇮🇪",
    "scotland":      "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "netherlands":   "🇳🇱",
    "usa":           "🇺🇸",
    "united states": "🇺🇸",
}

_ROLE_LABEL: dict[str, str] = {
    "BAT":  "Batter",
    "WK":   "WK",
    "AR":   "All Rounder",
    "BOWL": "Bowler",
}


def _flag(country: str | None) -> str:
    if not country:
        return "🏴"
    return _COUNTRY_FLAG.get(country.lower().strip(), "🏴")


def _team_ovr(all_cards: list[dict]) -> int:
    if not all_cards:
        return 0
    return round(sum(p["ovr"] for p in all_cards) / len(all_cards))


def _team_chem(all_cards: list[dict]) -> int:
    """Chemistry = number of same-country player pairs in the XI."""
    from collections import Counter
    counts = Counter(
        (p.get("country") or "Unknown").strip().lower()
        for p in all_cards
    )
    return sum(n * (n - 1) // 2 for n in counts.values())


def build_myxi_embed(user: discord.Member | discord.User, xi: dict) -> discord.Embed:
    valid, errors = validate_xi(xi)
    total = xi_total(xi)
    all_cards = [p for pl in xi.values() for p in pl]

    colour = GREEN if valid else (GOLD if total > 0 else RED)

    team_ovr  = _team_ovr(all_cards)
    team_chem = _team_chem(all_cards)

    embed = discord.Embed(
        title=f"Team: {user.display_name}  [OVR: {team_ovr} | CHEM: {team_chem}]",
        color=colour,
    )

    for cat, label in _ROLE_LABEL.items():
        players = xi.get(cat, [])
        if not players:
            embed.add_field(name=f"*{label}*", value="*— empty —*", inline=False)
            continue

        name_w = max(len(p["player_name"]) for p in players)
        header = f"`{'':>{name_w}}  OVR  BAT  BWL`"
        lines  = [header]
        for p in players:
            name = p["player_name"].ljust(name_w)
            ovr  = str(p["ovr"]).rjust(3)
            bat  = str(p["bat"]).rjust(3)
            bowl = str(p["bowl"]).rjust(3)
            flag = _flag(p.get("country"))
            lines.append(f"`{name}  {ovr}  {bat}  {bowl}`  {flag}")

        embed.add_field(
            name=f"*{label}*",
            value="\n".join(lines),
            inline=False,
        )

    status = "✅ XI Complete" if valid else ("⚠️ Incomplete" if total > 0 else "❌ Empty")
    embed.set_footer(text=f"{status}  •  {total}/11 players")

    if errors:
        embed.add_field(
            name="⚠️ Issues",
            value="\n".join(f"• {e}" for e in errors),
            inline=False,
        )
    return embed


def build_builder_embed(
    user: discord.Member | discord.User,
    xi: dict,
    stage_label: str = "XI Builder",
) -> discord.Embed:
    valid, errors = validate_xi(xi)
    total = xi_total(xi)
    colour = GREEN if valid else NAVY

    lines = [_cat_status(xi, cat) for cat in ROLE_CFG]
    progress = f"`{'█' * total}{'░' * (11 - total)}` **{total}/11**"

    embed = discord.Embed(
        title=f"🏏  PLAYING XI BUILDER  —  {user.display_name}",
        color=colour,
    )
    embed.description = "\n".join(lines) + "\n\n**Overall progress**\n" + progress

    if valid:
        embed.add_field(
            name="✅  XI is complete!",
            value="Click **Confirm & Save** to lock it in.",
            inline=False,
        )
    elif errors:
        embed.add_field(
            name="⚠️  Issues",
            value="\n".join(f"• {e}" for e in errors),
            inline=False,
        )

    embed.set_footer(text=stage_label)
    return embed


class RoleSelect(discord.ui.Select):
    def __init__(self, cat: str, pool: list[dict], current_ids: list[int]):
        cfg = ROLE_CFG[cat]
        self._cat = cat

        options = []
        for card in pool[:25]:
            desc = f"BAT {card['bat']} | BOWL {card['bowl']} | {card['role']}"
            options.append(discord.SelectOption(
                label=card["player_name"][:25],
                value=str(card["id"]),
                description=desc[:50],
                default=(card["id"] in current_ids),
            ))

        if not options:
            options = [discord.SelectOption(label="No cards of this type", value="none")]

        super().__init__(
            placeholder=f"Pick {cfg['min']}–{cfg['max']} {cfg['label']}…",
            min_values=cfg["min"],
            max_values=min(cfg["max"], len(options)),
            options=options,
            custom_id=f"role_select_{cat}",
        )

    async def callback(self, interaction: discord.Interaction):
        view: RoleSelectorView = self.view
        selected_ids = [int(v) for v in self.values if v != "none"]
        view.selected_ids = selected_ids
        view.confirmed = True
        view.stop()
        await interaction.response.defer()


class BackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="← Back", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view: RoleSelectorView = self.view
        view.confirmed = False
        view.stop()
        await interaction.response.defer()


class RoleSelectorView(discord.ui.View):
    def __init__(self, cat: str, pool: list[dict], current_ids: list[int]):
        super().__init__(timeout=120)
        self.confirmed    = False
        self.selected_ids: list[int] = []
        self.add_item(RoleSelect(cat, pool, current_ids))
        self.add_item(BackButton())


class ConfirmRemoveView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        self.confirmed = False

    @discord.ui.button(label="🗑️  Yes, delete it", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="✖ Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


class SetXIView(discord.ui.View):
    def __init__(self, user_id: str, saved_xi: dict, user_cards: list[dict]):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.user_cards = user_cards
        self.pending: dict[str, list[int]] = {
            cat: [p["id"] for p in players]
            for cat, players in saved_xi.items()
        }
        self._update_confirm_state()

    def _xi_as_dicts(self) -> dict:
        id_map = {c["id"]: c for c in self.user_cards}
        return {
            cat: [id_map[i] for i in ids if i in id_map]
            for cat, ids in self.pending.items()
        }

    def _cards_for_cat(self, cat: str) -> list[dict]:
        return sorted(
            [c for c in self.user_cards if get_role_category(c["role"]) == cat],
            key=lambda c: c.get("ovr", 0), reverse=True,
        )

    def _update_confirm_state(self):
        xi = self._xi_as_dicts()
        valid, _ = validate_xi(xi)
        for child in self.children:
            if getattr(child, "custom_id", None) == "confirm_xi":
                child.disabled = not valid
                child.style = (
                    discord.ButtonStyle.success if valid
                    else discord.ButtonStyle.secondary
                )

    async def _run_role_selector(self, interaction: discord.Interaction, cat: str) -> bool:
        cfg  = ROLE_CFG[cat]
        pool = self._cards_for_cat(cat)
        if not pool:
            await interaction.response.send_message(
                f"❌ You don't own any {cfg['label']}! Get cards first via `/admin give`.",
                ephemeral=True,
            )
            return False

        current  = self.pending.get(cat, [])
        sel_view = RoleSelectorView(cat, pool, current)

        await interaction.response.edit_message(
            content=f"{cfg['emoji']} **Choose your {cfg['label']}** (pick {cfg['min']}–{cfg['max']})",
            embed=None,
            view=sel_view,
        )
        timed_out = await sel_view.wait()
        if timed_out or not sel_view.confirmed:
            # Restore the main builder embed so the user can try again
            self._update_confirm_state()
            xi = self._xi_as_dicts()
            embed = build_builder_embed(interaction.user, xi)
            try:
                await interaction.edit_original_response(content=None, embed=embed, view=self)
            except Exception:
                pass
            return False
        self.pending[cat] = sel_view.selected_ids
        return True

    async def _refresh(self, interaction: discord.Interaction):
        self._update_confirm_state()
        xi = self._xi_as_dicts()
        embed = build_builder_embed(interaction.user, xi)
        try:
            await interaction.response.edit_message(content=None, embed=embed, view=self)
        except discord.InteractionResponded:
            await interaction.edit_original_response(content=None, embed=embed, view=self)

    @discord.ui.button(label="⚡ Auto-Fill", style=discord.ButtonStyle.primary, row=0)
    async def auto_fill(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = auto_build_xi(self.user_id)
        if not result:
            await interaction.response.send_message(
                "❌ You don't have enough cards to auto-fill a valid XI.\n"
                "You need at least **3 Batsmen, 1 WK, 2 All Rounders, 3 Bowlers** in your collection.",
                ephemeral=True,
            )
            return
        self.pending = result
        await self._refresh(interaction)

    @discord.ui.button(label="🏏 Batsmen", style=discord.ButtonStyle.secondary, row=0)
    async def set_bat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._run_role_selector(interaction, "BAT"):
            await self._refresh(interaction)

    @discord.ui.button(label="🧤 Wicket Keeper", style=discord.ButtonStyle.secondary, row=0)
    async def set_wk(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._run_role_selector(interaction, "WK"):
            await self._refresh(interaction)

    @discord.ui.button(label="🔄 All Rounders", style=discord.ButtonStyle.secondary, row=1)
    async def set_ar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._run_role_selector(interaction, "AR"):
            await self._refresh(interaction)

    @discord.ui.button(label="🎯 Bowlers", style=discord.ButtonStyle.secondary, row=1)
    async def set_bowl(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._run_role_selector(interaction, "BOWL"):
            await self._refresh(interaction)

    @discord.ui.button(label="🗑️ Clear XI", style=discord.ButtonStyle.danger, row=2)
    async def clear_xi(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pending = {"BAT": [], "WK": [], "AR": [], "BOWL": []}
        await self._refresh(interaction)

    @discord.ui.button(
        label="✅ Confirm & Save",
        style=discord.ButtonStyle.secondary,
        row=2,
        custom_id="confirm_xi",
        disabled=True,
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        xi = self._xi_as_dicts()
        valid, errors = validate_xi(xi)
        if not valid:
            await interaction.response.send_message(
                "❌ XI is still invalid:\n" + "\n".join(f"• {e}" for e in errors),
                ephemeral=True,
            )
            return
        set_user_xi(self.user_id, self.pending)

        xi_saved = self._xi_as_dicts()
        embed = build_myxi_embed(interaction.user, xi_saved)
        embed.title = "✅  XI SAVED — " + embed.title.replace("🏏  ", "")
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=None, embed=embed, view=None)
