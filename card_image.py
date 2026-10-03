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
