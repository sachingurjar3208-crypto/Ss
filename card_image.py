"""Generates the final card PNG from a card DB row + background template.

LAYOUT CONFIG
=============
All pixel positions below are tuned for a 1037 x 1517 template (the size of
the reference template you supplied). If you use a different template size,
either resize your templates to 1037x1517 before adding them with
/bgadder, or scale CANVAS_W/CANVAS_H and re-check the positions below.

Fields drawn: displayname, the foreground cutout, ovr, bat, bowl, country,
role, batting hand and up to two playstyle logos. There is no data field for
things like a signature or a hand-written tagline, so those decorative extras
from the sample card are intentionally left out.

LAYERING: background -> player cutout -> bottom stats panel (restored from
the template so the player stands BEHIND it) -> optional custom panel
overlay image (added via /paneladder, put on a card via /editcard, moved
with /panelfixer) -> text and logos. The custom panel overlay is ALWAYS
drawn after the foreground cutout, so the foreground always sits behind it,
never in front.
"""

from __future__ import annotations

import io
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from card_db import get_playstyle_logo, get_panel
from card_narratives import ROLE_CARD_WORD, HAND_CARD_WORD

CANVAS_W, CANVAS_H = 1037, 1517

FONT_DIR = Path(__file__).parent / "fonts"
# Drop bold display TTFs in cardmaker/fonts/ with these exact names, or the
# code falls back to Pillow's built-in font (works, but looks plain).
FONT_DISPLAY_BOLD = FONT_DIR / "display_bold.ttf"     # big gold name / stat numbers
FONT_LABEL = FONT_DIR / "label.ttf"                   # small caps labels

GOLD = (222, 180, 90, 255)
WHITE = (240, 240, 245, 255)

# ── Foreground fit box: the cutout is scaled to fit inside this box, kept
# centered on the X axis, anchored near the bottom of the box, THEN the
# per-card offset_x/offset_y (from /foregroundfixer) is applied on top. ──
FG_BOX = {"x": 90, "y": 230, "w": 860, "h": 980}

DISPLAYNAME_POS = (55, 130)     # top-left corner, baseline-ish
DISPLAYNAME_SIZE = 120

STAT_LEFT_CENTER = (150, 1290)   # left hexagon number
STAT_RIGHT_CENTER = (890, 1290)  # right hexagon number
STAT_NUMBER_SIZE = 70
STAT_LABEL_SIZE = 24
STAT_LABEL_OFFSET_Y = 55         # label sits this many px below the number

ROLE_CENTER = (518, 1210)        # "BATTER" / "BOWLER" word, center band
ROLE_SIZE = 26

OVR_CENTER = (518, 1160)         # OVR number, above the role word
OVR_SIZE = 46

COUNTRY_CENTER = (518, 1455)     # bottom strip: "🇮🇳 India"
COUNTRY_SIZE = 30

# ── Bottom stats panel (the boxed strip with the two hexagons + centre band).
# Everything from this row downwards is copied back from the untouched
# template AFTER the cutout is pasted, so the player is hidden behind the
# panel instead of sitting on top of it. If your template's panel top border
# is at a different row, change this one number (border is ~row 1148-1155). ──
PANEL_TOP_Y = 1146

HAND_CENTER = (518, 1348)        # "RIGHT HAND BAT" / "LEFT HAND BAT", bottom of centre band
HAND_SIZE = 20

# ── Custom panel overlay image (added via /paneladder). Fits the same way
# the foreground cutout does: scaled to fit inside this box (keeping aspect
# ratio), centered on X, anchored near the bottom, THEN the per-card
# panelimg_dx/dy/scale (from /panelfixer) is applied on top. Full-width by
# default since a "stats panel" graphic usually spans the card. ──
PANEL_BOX = {"x": 0, "y": 1080, "w": CANVAS_W, "h": CANVAS_H - 1080}

# ── Playstyle logos: two circles in the centre band, below the role word. ──
LOGO_CENTER_1 = (400, 1280)      # left circle  (used for slot 1 when there are 2 logos)
LOGO_CENTER_2 = (636, 1280)      # right circle (slot 2)
LOGO_CENTER_SINGLE = (518, 1280) # a lone logo sits in the middle
LOGO_BOX = 110                   # logo is fitted inside a LOGO_BOX x LOGO_BOX square


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        pass
    try:
        # Pillow >= 10.1 supports a `size` arg on the built-in default font,
        # which looks far better than the tiny fixed-size fallback below.
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_centered(draw: ImageDraw.ImageDraw, xy, text: str, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((xy[0] - w / 2, xy[1] - h / 2), text, font=font, fill=fill)


async def _download_image(url: str) -> Image.Image:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            resp.raise_for_status()
            data = await resp.read()
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _fit_foreground(
    fg: Image.Image, box: dict, offset_x: int, offset_y: int, scale_pct: int = 100
) -> tuple[Image.Image, tuple[int, int], bool]:
    """Scale `fg` to fit inside `box` (preserving aspect ratio), apply the
    manual /foregroundfix scale_pct on top (100 = box-fit size, 120 = 20%
    bigger, 80 = 20% smaller), then clamp the final paste position to stay
    fully within the canvas bounds. Returns the resized image, the clamped
    paste position, and whether any clamping was required.
    """
    box_w, box_h = box["w"], box["h"]
    base_scale = min(box_w / fg.width, box_h / fg.height)
    scale = base_scale * (scale_pct / 100)
    new_w, new_h = max(1, int(fg.width * scale)), max(1, int(fg.height * scale))
    fg_resized = fg.resize((new_w, new_h), Image.LANCZOS)

    # Center horizontally in the box, anchor to the bottom of the box —
    # anchoring stays box-relative even as size changes, so growing/shrinking
    # keeps the subject's feet roughly in place instead of drifting.
    paste_x = box["x"] + (box_w - new_w) // 2
    paste_y = box["y"] + box_h - new_h

    # /foregroundfix offsets: up = negative Y, down = positive Y,
    # left = negative X, right = positive X.
    paste_x += offset_x
    paste_y += offset_y

    # Clamp to the canvas so Pillow never gets a partially/out-of-bounds
    # alpha_composite destination. The image is allowed to sit flush with the
    # canvas edge, but never any further outside.
    min_x = 0
    max_x = max(0, CANVAS_W - new_w)
    min_y = 0
    max_y = max(0, CANVAS_H - new_h)
    clamped_x = max(min_x, min(paste_x, max_x))
    clamped_y = max(min_y, min(paste_y, max_y))
    was_clamped = (clamped_x != paste_x) or (clamped_y != paste_y)
    return fg_resized, (clamped_x, clamped_y), was_clamped


def _col(row, key, default=None):
    """Read a column from a sqlite3.Row, falling back if it is missing/NULL."""
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _load_logo(pathname) -> Image.Image | None:
    """Load a playstyle logo by name; None if unset, unknown or unreadable."""
    if not pathname:
        return None
    row = get_playstyle_logo(pathname)
    if row is None:
        return None
    try:
        return Image.open(row["local_path"]).convert("RGBA")
    except Exception:
        return None


def _load_panel(pathname) -> Image.Image | None:
    """Load a stats-panel overlay image by name; None if unset, unknown or
    unreadable (card just renders without the overlay in that case)."""
    if not pathname:
        return None
    row = get_panel(pathname)
    if row is None:
        return None
    try:
        return Image.open(row["local_path"]).convert("RGBA")
    except Exception:
        return None


def _draw_logo(canvas: Image.Image, logo: Image.Image, center, dx: int, dy: int, scale_pct: int) -> None:
    """Fit `logo` in LOGO_BOX, apply the /logofixer size, centre it on
    `center` shifted by (dx, dy) and paste it with its transparency."""
    base = min(LOGO_BOX / logo.width, LOGO_BOX / logo.height)
    scale = base * (scale_pct / 100)
    w, h = max(1, int(logo.width * scale)), max(1, int(logo.height * scale))
    resized = logo.resize((w, h), Image.LANCZOS)
    x = int(center[0] + dx - w / 2)
    y = int(center[1] + dy - h / 2)
    canvas.paste(resized, (x, y), resized)


async def generate_card_image(card_row, background_local_path: str) -> io.BytesIO:
    """Build the final PNG for a card DB row. Returns an in-memory PNG."""
    background = Image.open(background_local_path).convert("RGBA")
    if background.size != (CANVAS_W, CANVAS_H):
        background = background.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)

    canvas = background.copy()

    # ── Layout offsets from /cardlayout (0 / default for cards that never
    # used it, so old cards render exactly as before). ─────────────────
    name_dx    = _col(card_row, "name_dx", 0)
    name_dy    = _col(card_row, "name_dy", 0)
    name_size  = _col(card_row, "name_size", DISPLAYNAME_SIZE)
    ovr_dx     = _col(card_row, "ovr_dx", 0)
    ovr_dy     = _col(card_row, "ovr_dy", 0)
    ovr_size   = _col(card_row, "ovr_size", OVR_SIZE)
    stats_dx   = _col(card_row, "stats_dx", 0)
    stats_dy   = _col(card_row, "stats_dy", 0)
    stats_size = _col(card_row, "stats_size", STAT_NUMBER_SIZE)
    panel_dy   = _col(card_row, "panel_dy", 0)
    country_dy = _col(card_row, "country_dy", 0)
    country_size = _col(card_row, "country_size", COUNTRY_SIZE)

    # ── Foreground cutout ──────────────────────────────────────────────
    fg = await _download_image(card_row["foreground_link"])
    fg_resized, paste_pos, _ = _fit_foreground(
        fg, FG_BOX, card_row["offset_x"], card_row["offset_y"],
        card_row["scale_pct"] if "scale_pct" in card_row.keys() else 100,
    )
    canvas.alpha_composite(fg_resized, dest=paste_pos)

    # ── Put the bottom stats panel IN FRONT of the cutout ──────────────
    # Copy the untouched template rows back over everything from the panel's
    # top border down, so the player appears to stand behind the box.
    # panel_dy (from /cardlayout) nudges that border up/down.
    panel_top = PANEL_TOP_Y + panel_dy
    canvas.paste(
        background.crop((0, panel_top, CANVAS_W, CANVAS_H)), (0, panel_top)
    )

    # ── Optional custom stats-panel overlay image ───────────────────────
    # Added via /paneladder, assigned to a card via /editcard, positioned
    # via /panelfixer. Drawn AFTER the foreground cutout (and after the
    # template's own baked-in panel strip above), so it always sits in
    # FRONT of the player — the foreground never ends up on top of it.
    panel_pathname = _col(card_row, "panel_image")
    panel_img = _load_panel(panel_pathname)
    if panel_img is not None:
        panelimg_dx = _col(card_row, "panelimg_dx", 0)
        panelimg_dy = _col(card_row, "panelimg_dy", 0)
        panelimg_scale = _col(card_row, "panelimg_scale", 100)
        panel_resized, panel_pos, _ = _fit_foreground(
            panel_img, PANEL_BOX, panelimg_dx, panelimg_dy, panelimg_scale
        )
        canvas.alpha_composite(panel_resized, dest=panel_pos)

    draw = ImageDraw.Draw(canvas)

    # ── Display name (top-left corner, big gold) ──────────────────────
    name_font = _font(FONT_DISPLAY_BOLD, name_size)
    name_pos = (DISPLAYNAME_POS[0] + name_dx, DISPLAYNAME_POS[1] + name_dy)
    draw.text(name_pos, card_row["displayname"].upper(), font=name_font, fill=GOLD)

    # ── Role + side ordering ───────────────────────────────────────────
    # Higher stat becomes the "primary" side (shown on the LEFT), matching
    # "jo batsmen hoga uski batting aage" / "jo bowler hoga uski bowling aage".
    bat, bowl = card_row["bat"], card_row["bowl"]
    role = _col(card_row, "role")
    if role == "BOWL":
        is_batter = False
    elif role in ("BAT", "WK"):
        is_batter = True
    else:  # all-rounder, or an old card with no role saved: higher stat first
        is_batter = bat >= bowl
    role_word = ROLE_CARD_WORD.get(role) or ("BATTER" if is_batter else "BOWLER")
    left_value, left_label = (bat, "BATTING") if is_batter else (bowl, "BOWLING")
    right_value, right_label = (bowl, "BOWLING") if is_batter else (bat, "BATTING")

    num_font = _font(FONT_DISPLAY_BOLD, stats_size)
    label_font = _font(FONT_LABEL, STAT_LABEL_SIZE)

    left_center = (STAT_LEFT_CENTER[0] + stats_dx, STAT_LEFT_CENTER[1] + stats_dy)
    right_center = (STAT_RIGHT_CENTER[0] + stats_dx, STAT_RIGHT_CENTER[1] + stats_dy)

    _draw_centered(draw, left_center, str(left_value), num_font, GOLD)
    _draw_centered(
        draw,
        (left_center[0], left_center[1] + STAT_LABEL_OFFSET_Y),
        left_label, label_font, WHITE,
    )
    _draw_centered(draw, right_center, str(right_value), num_font, GOLD)
    _draw_centered(
        draw,
        (right_center[0], right_center[1] + STAT_LABEL_OFFSET_Y),
        right_label, label_font, WHITE,
    )

    # ── OVR + role word, center band ───────────────────────────────────
    ovr_center = (OVR_CENTER[0] + ovr_dx, OVR_CENTER[1] + ovr_dy)
    role_center = (ROLE_CENTER[0] + ovr_dx, ROLE_CENTER[1] + ovr_dy)
    ovr_font = _font(FONT_DISPLAY_BOLD, ovr_size)
    role_font = _font(FONT_LABEL, ROLE_SIZE)
    _draw_centered(draw, ovr_center, str(card_row["ovr"]), ovr_font, GOLD)
    _draw_centered(draw, role_center, role_word, role_font, WHITE)

    # ── Batting hand, bottom of the centre band ────────────────────────
    hand_word = HAND_CARD_WORD.get(_col(card_row, "batting_hand"))
    if hand_word:
        _draw_centered(draw, HAND_CENTER, hand_word, _font(FONT_LABEL, HAND_SIZE), WHITE)

    # ── Playstyle logos (max 2): one sits in the middle, two fill the circles ──
    slots = []
    for slot in (1, 2):
        logo = _load_logo(_col(card_row, f"playstyle{slot}"))
        if logo is not None:
            slots.append((slot, logo))
    if len(slots) == 2:
        centers = {1: LOGO_CENTER_1, 2: LOGO_CENTER_2}
    else:
        centers = {s: LOGO_CENTER_SINGLE for s, _ in slots}
    for slot, logo in slots:
        _draw_logo(
            canvas, logo, centers[slot],
            _col(card_row, f"logo{slot}_dx", 0),
            _col(card_row, f"logo{slot}_dy", 0),
            _col(card_row, f"logo{slot}_scale", 100),
        )

    # ── Country (emoji glyphs don't render via truetype fonts in Pillow,
    # so we draw the country name; the emoji is still stored in the DB and
    # used wherever Discord itself renders text, e.g. in embeds). ───────
    country_center = (COUNTRY_CENTER[0], COUNTRY_CENTER[1] + country_dy)
    country_font = _font(FONT_LABEL, country_size)
    _draw_centered(draw, country_center, card_row["country"].upper(), country_font, WHITE)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf

"""Owner-only card-creation slash commands.

    /cardmaker      create a new player card
    /editcard       edit an existing player card (only pass fields you want to change)
    /bgadder        add a new background template
    /foregroundfix  nudge position and/or resize a card's foreground cutout
    /removecard     delete a player card
    /logoadderofplaystyle  add a playstyle logo to the logo list
    /setplaystylelogo      put up to 2 playstyle logos on a card
    /logofixer             move / resize a card's playstyle logos
    /paneladder     add a new stats-panel overlay image (sits IN FRONT of the foreground)
    /panelremover   remove a stats-panel overlay from the panel list
    /panelfixer     move / resize a card's stats-panel overlay

Only OWNER_ID may run any of these — everyone else gets a plain refusal.
"""

from __future__ import annotations

import io
import os

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

from card_countries import CRICKET_COUNTRIES, resolve_country
from card_db import (
    init_card_db,
    card_exists, get_card, create_card, update_card, update_card_offset,
    set_card_image_path, list_playernames, delete_card,
    background_exists, get_background, add_background, list_backgrounds,
    set_playstyles, update_logo_layout, update_card_layout,
    playstyle_logo_exists, get_playstyle_logo, add_playstyle_logo, list_playstyle_logos,
    panel_exists, get_panel, add_panel, list_panels, remove_panel,
    update_panel_layout, set_card_panel,
    GENERATED_DIR, BACKGROUNDS_DIR, LOGOS_DIR, PANELS_DIR,
)
from card_image import generate_card_image, _fit_foreground, FG_BOX, _download_image
from card_narratives import NARRATIVES, NARRATIVE_NAMES, ROLES, HANDS, BOWLING_TYPES

import aiohttp

OWNER_ID = 1317288099075850243
MAX_STAT = 150
MIN_SCALE_PCT = 10
MAX_SCALE_PCT = 400


def is_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == OWNER_ID
    return app_commands.check(predicate)


ROLE_CHOICES = [app_commands.Choice(name=label, value=code) for code, label in ROLES.items()]
HAND_CHOICES = [app_commands.Choice(name=label, value=code) for code, label in HANDS.items()]
BOWLTYPE_CHOICES = [app_commands.Choice(name=label, value=code) for code, label in BOWLING_TYPES.items()]
NARRATIVE_CHOICES = [app_commands.Choice(name=n, value=n) for n in NARRATIVE_NAMES]
# /editcard only: lets you clear a narrative slot.
NARRATIVE_CHOICES_EDIT = NARRATIVE_CHOICES + [app_commands.Choice(name="None (remove)", value="NONE")]

TARGET_CHOICES = [
    app_commands.Choice(name="Both logos", value="both"),
    app_commands.Choice(name="Logo 1 only", value="1"),
    app_commands.Choice(name="Logo 2 only", value="2"),
]


# ── Autocomplete providers ──────────────────────────────────────────────

async def logo_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [n for n in list_playstyle_logos() if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def narrative_name_autocomplete(interaction: discord.Interaction, current: str):
    """Suggests the narrative names for /logoadderofplaystyle's pathname (you can still type your own)."""
    current_l = current.lower()
    names = [n for n in NARRATIVE_NAMES if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def background_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [n for n in list_backgrounds() if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def panel_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [n for n in list_panels() if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def country_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [name for name, _ in CRICKET_COUNTRIES if current_l in name.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def playername_autocomplete(interaction: discord.Interaction, current: str):
    current_l = current.lower()
    names = [n for n in list_playernames() if current_l in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


async def _regenerate_and_send(interaction: discord.Interaction, playername: str, verb: str) -> None:
    card = get_card(playername)
    bg = get_background(card["background"])
    if bg is None:
        await interaction.followup.send(
            f"⚠️ Card saved, but background template `{card['background']}` "
            f"no longer exists — image not regenerated."
        )
        return
    buf = await generate_card_image(card, bg["local_path"])
    out_path = GENERATED_DIR / f"{playername.strip().lower().replace(' ', '_')}.png"
    with open(out_path, "wb") as f:
        f.write(buf.getvalue())
    set_card_image_path(playername, str(out_path))
    buf.seek(0)
    file = discord.File(buf, filename=f"{card['displayname']}.png")
    extras = ""
    if card["role"]:
        extras += f" • {ROLES.get(card['role'], card['role'])}"
    if card["batting_hand"]:
        extras += f" • {HANDS.get(card['batting_hand'], card['batting_hand'])} bat"
    styles = [s for s in (card["playstyle1"], card["playstyle2"]) if s]
    if styles:
        extras += " • " + " + ".join(styles)
    embed = discord.Embed(
        title=f"🏏 {card['playername']}",
        description=(
            f"{verb} • OVR **{card['ovr']}** • BAT **{card['bat']}** • "
            f"BOWL **{card['bowl']}** • {card['country_emoji']} {card['country']} • "
            f"Size **{card['scale_pct']}%**{extras}"
        ),
        color=discord.Color.gold(),
    )
    embed.set_image(url=f"attachment://{card['displayname']}.png")
    await interaction.followup.send(embed=embed, file=file)


def _missing_logo_note(names) -> str:
    """Note to append when a chosen narrative has no logo in the logo list yet."""
    missing = [n for n in names if n and not playstyle_logo_exists(n)]
    if not missing:
        return ""
    return (
        "\nℹ️ No logo yet for: " + ", ".join(f"**{m}**" for m in missing)
        + " — add one with `/logoadderofplaystyle` using exactly that name as `pathname`."
    )


class CardMakerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_card_db()

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ Only the bot owner can use this command."
        else:
            msg = f"⚠️ Something went wrong: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ── /cardmaker ───────────────────────────────────────────────────
    @app_commands.command(name="cardmaker", description="Create a new player card")
    @app_commands.describe(
        playername="Full player name — the unique ID for this card, e.g. Virat Kohli",
        displayname="Short name shown big in the card corner, e.g. Kohli",
        foregroundlink="Direct link to the player cutout image (PNG with transparent background works best)",
        background="Card background template (added via /bgadder)",
        ovr="Overall rating (1–150)",
        bat="Batting power (1–150)",
        bowl="Bowling power (1–150)",
        countryflag="Country",
        role="Player role (shown on the card)",
        battinghand="Right hand or left hand batter",
        narrative1="Playstyle 1 (optional) — its logo shows on the card",
        narrative2="Playstyle 2 (optional) — its logo shows on the card",
        bowlingtype="Bowling type for bowlers/all-rounders (default Fast)",
    )
    @app_commands.choices(
        role=ROLE_CHOICES, battinghand=HAND_CHOICES,
        narrative1=NARRATIVE_CHOICES, narrative2=NARRATIVE_CHOICES,
        bowlingtype=BOWLTYPE_CHOICES,
    )
    @app_commands.autocomplete(background=background_autocomplete, countryflag=country_autocomplete)
    @is_owner()
    async def cardmaker(
        self,
        interaction: discord.Interaction,
        playername: str,
        displayname: str,
        foregroundlink: str,
        background: str,
        ovr: app_commands.Range[int, 1, MAX_STAT],
        bat: app_commands.Range[int, 1, MAX_STAT],
        bowl: app_commands.Range[int, 1, MAX_STAT],
        countryflag: str,
        role: str,
        battinghand: str,
        narrative1: str | None = None,
        narrative2: str | None = None,
        bowlingtype: str | None = None,
    ):
        await interaction.response.defer()

        if narrative1 and narrative2 and narrative1 == narrative2:
            await interaction.followup.send("❌ narrative1 and narrative2 can't be the same — pick two different playstyles.")
            return

        if card_exists(playername):
            await interaction.followup.send(
                f"❌ A card for **{playername}** already exists. "
                f"Use `/editcard` to change it instead."
            )
            return

        country = resolve_country(countryflag)
        if country is None:
            await interaction.followup.send(
                f"❌ Unknown country `{countryflag}` — pick one from the autocomplete list."
            )
            return
        country_name, country_emoji = country

        bg = get_background(background)
        if bg is None:
            names = list_backgrounds()
            hint = ", ".join(names) if names else "(none added yet — use `/bgadder` first)"
            await interaction.followup.send(f"❌ Unknown background `{background}`.\nAvailable: {hint}")
            return

        create_card(
            playername=playername,
            displayname=displayname,
            foreground_link=foregroundlink,
            background=bg["pathname"],
            ovr=ovr, bat=bat, bowl=bowl,
            country=country_name, country_emoji=country_emoji,
            created_by=interaction.user.id,
            role=role, batting_hand=battinghand,
            playstyle1=narrative1, playstyle2=narrative2,
            bowling_type=bowlingtype,
        )

        try:
            await _regenerate_and_send(interaction, playername, "✅ Card created")
            note = _missing_logo_note([narrative1, narrative2])
            if note:
                await interaction.followup.send(note.strip())
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Card saved to the database, but image generation failed: `{e}`\n"
                f"This is usually the foreground image link — check it loads directly as "
                f"an image (not a webpage), then try `/editcard` with a new link, or "
                f"nudge it with `/foregroundfix`."
            )

    # ── /editcard ────────────────────────────────────────────────────
    @app_commands.command(name="editcard", description="Edit an existing player card")
    @app_commands.describe(
        playername="Which card to edit",
        displayname="New short display name (leave blank to keep current)",
        foregroundlink="New cutout image link (leave blank to keep current)",
        background="New background template (leave blank to keep current)",
        ovr="New overall rating (1–150)",
        bat="New batting power (1–150)",
        bowl="New bowling power (1–150)",
        countryflag="New country",
        role="New role",
        battinghand="New batting hand",
        narrative1="New playstyle 1 (choose None to remove)",
        narrative2="New playstyle 2 (choose None to remove)",
        bowlingtype="New bowling type (Fast / Off Spin / Leg Spin)",
        panel="Stats-panel overlay image (added via /paneladder) — type none to remove",
    )
    @app_commands.choices(
        role=ROLE_CHOICES, battinghand=HAND_CHOICES,
        narrative1=NARRATIVE_CHOICES_EDIT, narrative2=NARRATIVE_CHOICES_EDIT,
        bowlingtype=BOWLTYPE_CHOICES,
    )
    @app_commands.autocomplete(
        playername=playername_autocomplete,
        background=background_autocomplete,
        countryflag=country_autocomplete,
        panel=panel_autocomplete,
    )
    @is_owner()
    async def editcard(
        self,
        interaction: discord.Interaction,
        playername: str,
        displayname: str | None = None,
        foregroundlink: str | None = None,
        background: str | None = None,
        ovr: app_commands.Range[int, 1, MAX_STAT] | None = None,
        bat: app_commands.Range[int, 1, MAX_STAT] | None = None,
        bowl: app_commands.Range[int, 1, MAX_STAT] | None = None,
        countryflag: str | None = None,
        role: str | None = None,
        battinghand: str | None = None,
        narrative1: str | None = None,
        narrative2: str | None = None,
        bowlingtype: str | None = None,
        panel: str | None = None,
    ):
        await interaction.response.defer()

        if not card_exists(playername):
            await interaction.followup.send(
                f"❌ No card exists for **{playername}** yet. Use `/cardmaker` to create it first."
            )
            return

        updates: dict = {}
        if displayname is not None:
            updates["displayname"] = displayname
        if foregroundlink is not None:
            updates["foreground_link"] = foregroundlink
        if background is not None:
            bg = get_background(background)
            if bg is None:
                names = list_backgrounds()
                hint = ", ".join(names) if names else "(none added yet)"
                await interaction.followup.send(f"❌ Unknown background `{background}`.\nAvailable: {hint}")
                return
            updates["background"] = bg["pathname"]
        if countryflag is not None:
            country = resolve_country(countryflag)
            if country is None:
                await interaction.followup.send(
                    f"❌ Unknown country `{countryflag}` — pick one from the autocomplete list."
                )
                return
            updates["country"], updates["country_emoji"] = country
        if ovr is not None:
            updates["ovr"] = ovr
        if bat is not None:
            updates["bat"] = bat
        if bowl is not None:
            updates["bowl"] = bowl
        if role is not None:
            updates["role"] = role
        if battinghand is not None:
            updates["batting_hand"] = battinghand
        if bowlingtype is not None:
            updates["bowling_type"] = bowlingtype

        # Playstyle slots: only touch the slot(s) that were given ("NONE" clears one).
        slot_updates: dict = {}
        if narrative1 is not None:
            slot_updates["playstyle1"] = None if narrative1 == "NONE" else narrative1
        if narrative2 is not None:
            slot_updates["playstyle2"] = None if narrative2 == "NONE" else narrative2

        # Panel overlay: "none" (any case) clears it; otherwise it must be a
        # known /paneladder pathname.
        panel_update_pending = False
        new_panel_value: str | None = None
        if panel is not None:
            if panel.strip().lower() == "none":
                panel_update_pending = True
                new_panel_value = None
            else:
                prow = get_panel(panel)
                if prow is None:
                    names = list_panels()
                    hint = ", ".join(names) if names else "(none added yet — use `/paneladder` first)"
                    await interaction.followup.send(f"❌ Unknown panel `{panel}`.\nAvailable: {hint}")
                    return
                panel_update_pending = True
                new_panel_value = prow["pathname"]

        if not updates and not slot_updates and not panel_update_pending:
            await interaction.followup.send("❌ Nothing to change — give at least one field besides playername.")
            return

        if slot_updates:
            current = get_card(playername)
            final1 = slot_updates.get("playstyle1", current["playstyle1"])
            final2 = slot_updates.get("playstyle2", current["playstyle2"])
            if final1 and final2 and final1.lower() == final2.lower():
                await interaction.followup.send("❌ A card can't have the same playstyle twice — pick two different ones.")
                return

        if updates:
            update_card(playername, **updates)
        if slot_updates:
            set_playstyles(playername, **slot_updates)
        if panel_update_pending:
            set_card_panel(playername, new_panel_value)

        try:
            await _regenerate_and_send(interaction, playername, "✏️ Card updated")
            note = _missing_logo_note(list(slot_updates.values()))
            if note:
                await interaction.followup.send(note.strip())
        except Exception as e:
            await interaction.followup.send(f"⚠️ Card updated in the database, but image regeneration failed: `{e}`")

    # ── /bgadder ─────────────────────────────────────────────────────
    @app_commands.command(name="bgadder", description="Add a new card background template")
    @app_commands.describe(
        link="Direct link to the background template image",
        pathname="Name to pick this background by in /cardmaker and /editcard",
    )
    @is_owner()
    async def bgadder(self, interaction: discord.Interaction, link: str, pathname: str):
        await interaction.response.defer()

        if background_exists(pathname):
            await interaction.followup.send(f"❌ A background named `{pathname}` already exists — pick a different name.")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(link, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
        except Exception as e:
            await interaction.followup.send(f"❌ Couldn't download that link: `{e}`")
            return

        local_path = BACKGROUNDS_DIR / f"{pathname.strip().lower().replace(' ', '_')}.png"
        with open(local_path, "wb") as f:
            f.write(data)

        add_background(pathname, link, str(local_path), interaction.user.id)
        await interaction.followup.send(f"✅ Background **{pathname}** added. Pick it in `/cardmaker`'s `background` option.")

    # ── /paneladder ──────────────────────────────────────────────────
    @app_commands.command(
        name="paneladder",
        description="Add a new stats panel overlay image (always drawn in front of the foreground)",
    )
    @app_commands.describe(
        url="Direct link to the panel image (transparent PNG works best)",
        pathname="Name to pick this panel by in /editcard's `panel` option",
    )
    @is_owner()
    async def paneladder(self, interaction: discord.Interaction, url: str, pathname: str):
        await interaction.response.defer()

        if panel_exists(pathname):
            await interaction.followup.send(f"❌ A panel named `{pathname}` already exists — pick a different name.")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
            # Decode it now so a link that isn't a real image fails here, not later on a card.
            Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception as e:
            await interaction.followup.send(f"❌ Couldn't download that link as an image: `{e}`")
            return

        local_path = PANELS_DIR / f"{pathname.strip().lower().replace(' ', '_')}.png"
        with open(local_path, "wb") as f:
            f.write(data)

        add_panel(pathname, url, str(local_path), interaction.user.id)
        await interaction.followup.send(
            f"✅ Panel **{pathname}** added. Put it on a card with "
            f"`/editcard playername:<card> panel:{pathname}`, then nudge it with `/panelfixer`.\n"
            f"ℹ️ The panel always renders IN FRONT of the foreground — the foreground never "
            f"covers it, whatever position/size you set."
        )

    # ── /panelremover ────────────────────────────────────────────────
    @app_commands.command(name="panelremover", description="Remove a stats panel overlay from the panel list")
    @app_commands.describe(pathname="Which panel to remove")
    @app_commands.autocomplete(pathname=panel_autocomplete)
    @is_owner()
    async def panelremover(self, interaction: discord.Interaction, pathname: str):
        await interaction.response.defer()

        removed = remove_panel(pathname)
        if removed is None:
            names = list_panels()
            hint = ", ".join(names) if names else "(none added yet)"
            await interaction.followup.send(f"❌ No panel found named `{pathname}`.\nAvailable: {hint}")
            return

        # Best-effort cleanup of the local file — the DB rows are already
        # gone either way, so a missing/locked file here is not an error.
        try:
            os.remove(removed["local_path"])
        except OSError:
            pass

        await interaction.followup.send(
            f"🗑️ Removed panel **{removed['pathname']}**. "
            f"Any cards that had it set now render without a panel overlay."
        )

    # ── /foregroundfix ───────────────────────────────────────────────
    @app_commands.command(name="foregroundfix", description="Nudge position and/or resize a card's foreground cutout")
    @app_commands.describe(
        playername="Which card to adjust",
        foregroundup="Pixels to move the cutout up",
        foregrounddown="Pixels to move the cutout down",
        foregroundleft="Pixels to move the cutout left",
        foregroundright="Pixels to move the cutout right",
        foregroundsizebig="Percent to make the cutout bigger, e.g. 10 = +10% size",
        foregroundsizesmall="Percent to make the cutout smaller, e.g. 10 = -10% size",
    )
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def foregroundfix(
        self,
        interaction: discord.Interaction,
        playername: str,
        foregroundup: app_commands.Range[int, 0, 2000] = 0,
        foregrounddown: app_commands.Range[int, 0, 2000] = 0,
        foregroundleft: app_commands.Range[int, 0, 2000] = 0,
        foregroundright: app_commands.Range[int, 0, 2000] = 0,
        foregroundsizebig: app_commands.Range[int, 0, 500] = 0,
        foregroundsizesmall: app_commands.Range[int, 0, 90] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        new_offset_x = card["offset_x"] - foregroundleft + foregroundright
        new_offset_y = card["offset_y"] - foregroundup + foregrounddown
        new_scale_pct = card["scale_pct"] + foregroundsizebig - foregroundsizesmall
        new_scale_pct = max(MIN_SCALE_PCT, min(MAX_SCALE_PCT, new_scale_pct))

        clamp_notice = ""
        try:
            fg = await _download_image(card["foreground_link"])
            base_scale = min(FG_BOX["w"] / fg.width, FG_BOX["h"] / fg.height)
            scale = base_scale * (new_scale_pct / 100)
            new_w, new_h = max(1, int(fg.width * scale)), max(1, int(fg.height * scale))
            anchor_x = FG_BOX["x"] + (FG_BOX["w"] - new_w) // 2
            anchor_y = FG_BOX["y"] + FG_BOX["h"] - new_h

            _, clamped_pos, was_clamped = _fit_foreground(fg, FG_BOX, new_offset_x, new_offset_y, new_scale_pct)
            if was_clamped:
                new_offset_x = clamped_pos[0] - anchor_x
                new_offset_y = clamped_pos[1] - anchor_y
                clamp_notice = "⚠️ Clamped to the canvas edge — went as far as possible."
        except Exception:
            pass

        update_card_offset(playername, new_offset_x, new_offset_y, new_scale_pct)

        try:
            await _regenerate_and_send(
                interaction, playername,
                f"🔧 Adjusted (offset x={new_offset_x}, y={new_offset_y}, size={new_scale_pct}%)",
            )
            if clamp_notice:
                await interaction.followup.send(clamp_notice)
        except Exception as e:
            msg = f"⚠️ Offset/size saved, but image regeneration failed: `{e}`"
            if clamp_notice:
                msg = f"{clamp_notice}\n{msg}"
            await interaction.followup.send(msg)

    # ── /logoadderofplaystyle ────────────────────────────────────────
    @app_commands.command(name="logoadderofplaystyle", description="Add a playstyle logo to the logo list")
    @app_commands.describe(
        url="Direct link to the logo image (transparent PNG works best)",
        pathname="Name of this logo, e.g. Swing King. Use the narrative's exact name to link it automatically",
    )
    @app_commands.autocomplete(pathname=narrative_name_autocomplete)
    @is_owner()
    async def logoadderofplaystyle(self, interaction: discord.Interaction, url: str, pathname: str):
        await interaction.response.defer()

        if playstyle_logo_exists(pathname):
            await interaction.followup.send(f"❌ A logo named `{pathname}` already exists — pick a different name.")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
            # Decode it now so a link that isn't a real image fails here, not later on a card.
            logo = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception as e:
            await interaction.followup.send(f"❌ Couldn't download that link as an image: `{e}`")
            return

        local_path = LOGOS_DIR / f"{pathname.strip().lower().replace(' ', '_')}.png"
        logo.save(local_path, format="PNG")

        add_playstyle_logo(pathname, url, str(local_path), interaction.user.id)
        await interaction.followup.send(
            f"✅ Playstyle logo **{pathname}** added. Pick it with `/setplaystylelogo`."
        )

    # ── /setplaystylelogo ────────────────────────────────────────────
    @app_commands.command(name="setplaystylelogo", description="Put up to 2 playstyle logos on a player card")
    @app_commands.describe(
        playername="Which card to put the logo(s) on",
        logo1="First playstyle logo",
        logo2="Second playstyle logo (optional — leave empty for just one logo)",
    )
    @app_commands.autocomplete(
        playername=playername_autocomplete, logo1=logo_autocomplete, logo2=logo_autocomplete,
    )
    @is_owner()
    async def setplaystylelogo(
        self,
        interaction: discord.Interaction,
        playername: str,
        logo1: str,
        logo2: str | None = None,
    ):
        await interaction.response.defer()

        if not card_exists(playername):
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        chosen = []
        for name in (logo1, logo2):
            if name is None:
                chosen.append(None)
                continue
            row = get_playstyle_logo(name)
            if row is None:
                names = list_playstyle_logos()
                hint = ", ".join(names) if names else "(none added yet — use `/logoadderofplaystyle` first)"
                await interaction.followup.send(f"❌ Unknown logo `{name}`.\nAvailable: {hint}")
                return
            chosen.append(row["pathname"])

        if chosen[0] and chosen[1] and chosen[0].lower() == chosen[1].lower():
            await interaction.followup.send("❌ logo1 and logo2 can't be the same logo.")
            return

        # Replaces both slots: leaving logo2 empty removes any second logo.
        set_playstyles(playername, chosen[0], chosen[1])

        try:
            await _regenerate_and_send(interaction, playername, "🎯 Playstyle logo(s) set")
        except Exception as e:
            await interaction.followup.send(f"⚠️ Logos saved, but image regeneration failed: `{e}`")

    # ── /logofixer ───────────────────────────────────────────────────
    @app_commands.command(name="logofixer", description="Move and/or resize a card's playstyle logos")
    @app_commands.describe(
        playername="Which card to adjust",
        target="Adjust both logos together, or just logo 1 / logo 2 (default: both)",
        logoup="Pixels to move the logo up",
        logodown="Pixels to move the logo down",
        logoleft="Pixels to move the logo left",
        logoright="Pixels to move the logo right",
        logosizebig="Percent to make the logo bigger, e.g. 10 = +10% size",
        logosizesmall="Percent to make the logo smaller, e.g. 10 = -10% size",
    )
    @app_commands.choices(target=TARGET_CHOICES)
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def logofixer(
        self,
        interaction: discord.Interaction,
        playername: str,
        target: str = "both",
        logoup: app_commands.Range[int, 0, 2000] = 0,
        logodown: app_commands.Range[int, 0, 2000] = 0,
        logoleft: app_commands.Range[int, 0, 2000] = 0,
        logoright: app_commands.Range[int, 0, 2000] = 0,
        logosizebig: app_commands.Range[int, 0, 500] = 0,
        logosizesmall: app_commands.Range[int, 0, 90] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        slots = (1, 2) if target == "both" else (int(target),)
        slots = [s for s in slots if card[f"playstyle{s}"]]
        if not slots:
            await interaction.followup.send(
                f"❌ **{card['playername']}** has no logo in that slot — set one with `/setplaystylelogo` first."
            )
            return

        summary = []
        for s in slots:
            new_dx = card[f"logo{s}_dx"] - logoleft + logoright
            new_dy = card[f"logo{s}_dy"] - logoup + logodown
            new_scale = card[f"logo{s}_scale"] + logosizebig - logosizesmall
            new_scale = max(MIN_SCALE_PCT, min(MAX_SCALE_PCT, new_scale))
            update_logo_layout(playername, s, new_dx, new_dy, new_scale)
            summary.append(f"logo {s}: x={new_dx}, y={new_dy}, size={new_scale}%")

        try:
            await _regenerate_and_send(interaction, playername, "🔧 Logo adjusted (" + "; ".join(summary) + ")")
        except Exception as e:
            await interaction.followup.send(f"⚠️ Logo position/size saved, but image regeneration failed: `{e}`")

    # ── /panelfixer ──────────────────────────────────────────────────
    @app_commands.command(name="panelfixer", description="Move and/or resize a card's stats panel overlay")
    @app_commands.describe(
        playername="Which card to adjust",
        panelup="Pixels to move the panel up",
        paneldown="Pixels to move the panel down",
        panelleft="Pixels to move the panel left",
        panelright="Pixels to move the panel right",
        panelsizebig="Percent to make the panel bigger, e.g. 10 = +10% size",
        panelsizesmall="Percent to make the panel smaller, e.g. 10 = -10% size",
    )
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def panelfixer(
        self,
        interaction: discord.Interaction,
        playername: str,
        panelup: app_commands.Range[int, 0, 2000] = 0,
        paneldown: app_commands.Range[int, 0, 2000] = 0,
        panelleft: app_commands.Range[int, 0, 2000] = 0,
        panelright: app_commands.Range[int, 0, 2000] = 0,
        panelsizebig: app_commands.Range[int, 0, 500] = 0,
        panelsizesmall: app_commands.Range[int, 0, 90] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        if not card["panel_image"]:
            await interaction.followup.send(
                f"❌ **{card['playername']}** has no panel set yet — pick one first with "
                f"`/editcard playername:{card['playername']} panel:<name>`."
            )
            return

        new_dx = card["panelimg_dx"] - panelleft + panelright
        new_dy = card["panelimg_dy"] - panelup + paneldown
        new_scale = card["panelimg_scale"] + panelsizebig - panelsizesmall
        new_scale = max(MIN_SCALE_PCT, min(MAX_SCALE_PCT, new_scale))

        update_panel_layout(playername, new_dx, new_dy, new_scale)

        try:
            await _regenerate_and_send(
                interaction, playername,
                f"🔧 Panel adjusted (x={new_dx}, y={new_dy}, size={new_scale}%)",
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Panel position/size saved, but image regeneration failed: `{e}`")

    # ── /cardlayout ──────────────────────────────────────────────────
    @app_commands.command(
        name="cardlayout",
        description="Move/resize the name, OVR, stats, or country text on a card",
    )
    @app_commands.describe(
        playername="Which card to adjust",
        nameup="Move the display name up (px)",
        namedown="Move the display name down (px)",
        nameleft="Move the display name left (px)",
        nameright="Move the display name right (px)",
        namesizebig="Make the display name bigger, e.g. 10 = +10px",
        namesizesmall="Make the display name smaller, e.g. 10 = -10px",
        ovrup="Move the OVR number + role word up (px)",
        ovrdown="Move the OVR number + role word down (px)",
        ovrsizebig="Make the OVR number bigger, e.g. 10 = +10px",
        ovrsizesmall="Make the OVR number smaller, e.g. 10 = -10px",
        statsup="Move the BAT/BOWL hexagon numbers up (px)",
        statsdown="Move the BAT/BOWL hexagon numbers down (px)",
        statsleft="Move the BAT/BOWL hexagon numbers left (px)",
        statsright="Move the BAT/BOWL hexagon numbers right (px)",
        statssizebig="Make the BAT/BOWL numbers bigger, e.g. 10 = +10px",
        statssizesmall="Make the BAT/BOWL numbers smaller, e.g. 10 = -10px",
        countryup="Move the country text up (px)",
        countrydown="Move the country text down (px)",
    )
    @app_commands.autocomplete(playername=playername_autocomplete)
    @is_owner()
    async def cardlayout(
        self,
        interaction: discord.Interaction,
        playername: str,
        nameup: app_commands.Range[int, 0, 2000] = 0,
        namedown: app_commands.Range[int, 0, 2000] = 0,
        nameleft: app_commands.Range[int, 0, 2000] = 0,
        nameright: app_commands.Range[int, 0, 2000] = 0,
        namesizebig: app_commands.Range[int, 0, 300] = 0,
        namesizesmall: app_commands.Range[int, 0, 110] = 0,
        ovrup: app_commands.Range[int, 0, 2000] = 0,
        ovrdown: app_commands.Range[int, 0, 2000] = 0,
        ovrsizebig: app_commands.Range[int, 0, 300] = 0,
        ovrsizesmall: app_commands.Range[int, 0, 40] = 0,
        statsup: app_commands.Range[int, 0, 2000] = 0,
        statsdown: app_commands.Range[int, 0, 2000] = 0,
        statsleft: app_commands.Range[int, 0, 2000] = 0,
        statsright: app_commands.Range[int, 0, 2000] = 0,
        statssizebig: app_commands.Range[int, 0, 300] = 0,
        statssizesmall: app_commands.Range[int, 0, 60] = 0,
        countryup: app_commands.Range[int, 0, 500] = 0,
        countrydown: app_commands.Range[int, 0, 500] = 0,
    ):
        await interaction.response.defer()

        card = get_card(playername)
        if card is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{playername}**.\nExisting cards: {hint}")
            return

        new_name_dx = card["name_dx"] - nameleft + nameright
        new_name_dy = card["name_dy"] - nameup + namedown
        new_name_size = max(20, card["name_size"] + namesizebig - namesizesmall)

        new_ovr_dx = card["ovr_dx"]
        new_ovr_dy = card["ovr_dy"] - ovrup + ovrdown
        new_ovr_size = max(10, card["ovr_size"] + ovrsizebig - ovrsizesmall)

        new_stats_dx = card["stats_dx"] - statsleft + statsright
        new_stats_dy = card["stats_dy"] - statsup + statsdown
        new_stats_size = max(15, card["stats_size"] + statssizebig - statssizesmall)

        new_country_dy = card["country_dy"] - countryup + countrydown

        update_card_layout(
            playername,
            name_dx=new_name_dx, name_dy=new_name_dy, name_size=new_name_size,
            ovr_dx=new_ovr_dx, ovr_dy=new_ovr_dy, ovr_size=new_ovr_size,
            stats_dx=new_stats_dx, stats_dy=new_stats_dy, stats_size=new_stats_size,
            country_dy=new_country_dy,
        )

        try:
            await _regenerate_and_send(
                interaction, playername,
                f"🔧 Layout adjusted (name size={new_name_size}, ovr size={new_ovr_size}, "
                f"stats size={new_stats_size})",
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Layout saved, but image regeneration failed: `{e}`")

    # ── /removecard ──────────────────────────────────────────────────
    @app_commands.command(name="removecard", description="Delete a player card from the database")
    @app_commands.describe(cardname="Which card to remove")
    @app_commands.autocomplete(cardname=playername_autocomplete)
    @is_owner()
    async def removecard(self, interaction: discord.Interaction, cardname: str):
        await interaction.response.defer()

        deleted = delete_card(cardname)
        if deleted is None:
            names = list_playernames()
            hint = ", ".join(names) if names else "(no cards yet)"
            await interaction.followup.send(f"❌ No card found for **{cardname}**.\nExisting cards: {hint}")
            return

        # Best-effort cleanup of the generated image file — the DB row is
        # already gone either way, so a missing/locked file here is not an error.
        if deleted["image_path"]:
            try:
                os.remove(deleted["image_path"])
            except OSError:
                pass

        await interaction.followup.send(
            f"🗑️ Removed **{deleted['playername']}**'s card from the database."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(CardMakerCog(bot))





















































































































































































































































































































































































































































































































































































































































































































































