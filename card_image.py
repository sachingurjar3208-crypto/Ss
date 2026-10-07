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

import asyncio
import hashlib
import io
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from card_db import get_playstyle_logo, get_panel, get_country_logo, FOREGROUNDS_DIR
from card_narratives import ROLE_CARD_WORD, HAND_CARD_WORD

CANVAS_W, CANVAS_H = 1037, 1517

_HERE = Path(__file__).parent


def _find_font(name: str) -> Path:
    """Look in ./fonts/ first, then next to this file (where the TTFs actually live)."""
    for folder in (_HERE / "fonts", _HERE):
        if (folder / name).is_file():
            return folder / name
    return _HERE / "fonts" / name


FONT_DIR = _HERE / "fonts"
# Drop bold display TTFs in cardmaker/fonts/ with these exact names, or the
# code falls back to Pillow's built-in font (works, but looks plain).
FONT_DISPLAY_BOLD = _find_font("display_bold.ttf")     # big gold name / stat numbers
FONT_LABEL = _find_font("label.ttf")                   # small caps labels

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

ROLE_CENTER = (518, 1200)        # role word (BATTER / BOWLER / WICKETKEEPER...) alone in the top centre strip
ROLE_SIZE = 26

# OVR now lives inside the big centre circle: number on top, small "OVR" word under it.
# Number uses the SAME font/size/colour as the BAT / BOWL numbers; the word uses the SAME
# font/size/colour as the BATTING / BOWLING labels and the same gap below the number.
OVR_CENTER = (518, 1320)         # OVR number centre (fixed px for every card)
OVR_SIZE = STAT_NUMBER_SIZE      # same size as the BAT / BOWL numbers
OVR_LABEL_TEXT = "OVR"

COUNTRY_CENTER = (518, 1465)     # bottom strip: "🇮🇳 India" (moved 10px down)
COUNTRY_SIZE = 30

# ── Bottom stats panel (the boxed strip with the two hexagons + centre band).
# Everything from this row downwards is copied back from the untouched
# template AFTER the cutout is pasted, so the player is hidden behind the
# panel instead of sitting on top of it. If your template's panel top border
# is at a different row, change this one number (border is ~row 1148-1155). ──
PANEL_TOP_Y = 1146
# When True, the cutout is hidden behind the bottom panel (old behaviour).
# When False (default), the cutout is never clipped by the panel.
FG_CLIP_TO_PANEL = False

HAND_CENTER = (518, 1357)        # "RIGHT HAND BAT" / "LEFT HAND BAT", bottom of centre band (moved 9px down)
HAND_SIZE = 20
# The centre circle now holds the OVR, so the "RIGHT HAND BAT" text is not drawn by default.
# Set True to draw it again (it would sit on top of the OVR, so move HAND_CENTER first).
SHOW_HAND_TEXT = False

# ── Country logo: sits just ABOVE the country name. It follows the country
# text (so /cardlayout countryup/down moves both), then the per-card
# /countrylogofixer dx/dy/scale is applied on top. ──
COUNTRY_LOGO_BOX = 46            # logo is fitted inside a square this big
COUNTRY_LOGO_OFFSET_Y = -44      # logo centre is this many px above the country text centre

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


def _foreground_file(url: str) -> Path:
    """Permanent local copy of a foreground link. Discord CDN links carry
    expiring ?ex=&is=&hm= tokens, so those are ignored: the same attachment
    always maps to the same file."""
    parts = urlsplit(url.strip())
    key = f"{parts.netloc}{parts.path}" if "discordapp" in parts.netloc else url.strip()
    return FOREGROUNDS_DIR / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.png"


async def _download_image(url: str) -> Image.Image:
    """Load the foreground from the permanent local folder if we already have
    it; otherwise download it once, save it forever, and use that."""
    local = _foreground_file(url)
    if local.exists():
        try:
            return Image.open(local).convert("RGBA")
        except Exception:
            local.unlink(missing_ok=True)   # damaged copy: fetch again
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            resp.raise_for_status()
            data = await resp.read()
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    try:
        tmp = local.with_suffix(".tmp")
        img.save(tmp, format="PNG")
        tmp.replace(local)
    except Exception as e:
        print(f"[card_image] could not save foreground copy: {e}")
    return img


async def cache_all_foregrounds(cards) -> tuple[int, int, list[str]]:
    """Save every card's foreground into the permanent folder. Cards that are
    already saved cost nothing. Returns (newly_saved, already_saved, failed_names)."""
    new = have = 0
    failed: list[str] = []
    for card in cards:
        try:
            if _foreground_file(card["foreground_link"]).exists():
                have += 1
                continue
            await _download_image(card["foreground_link"])
            new += 1
        except Exception:
            failed.append(card["playername"])
    return new, have, failed


def _fit_foreground(
    fg: Image.Image, box: dict, offset_x: int, offset_y: int, scale_pct: int = 100
) -> tuple[Image.Image, tuple[int, int]]:
    """Scale `fg` to fit inside `box` (preserving aspect ratio), apply the
    manual /foregroundfix scale_pct on top (100 = box-fit size, 120 = 20%
    bigger, 80 = 20% smaller), and return the resized image plus the
    (x, y) paste position on the full canvas with the manual offset applied.
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
    return fg_resized, (paste_x, paste_y)


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


def _load_country_logo(country) -> Image.Image | None:
    """Load the logo added for this country via /countrylogoadder; None if
    there isn't one or it can't be read (card just renders without it)."""
    row = get_country_logo(country)
    if row is None:
        return None
    try:
        return Image.open(row["local_path"]).convert("RGBA")
    except Exception:
        return None


def _draw_logo(canvas: Image.Image, logo: Image.Image, center, dx: int, dy: int, scale_pct: int,
               box: int = LOGO_BOX) -> None:
    """Fit `logo` in a `box` x `box` square (LOGO_BOX by default), apply the
    /logofixer size, centre it on `center` shifted by (dx, dy) and paste it
    with its transparency."""
    base = min(box / logo.width, box / logo.height)
    scale = base * (scale_pct / 100)
    w, h = max(1, int(logo.width * scale)), max(1, int(logo.height * scale))
    resized = logo.resize((w, h), Image.LANCZOS)
    x = int(center[0] + dx - w / 2)
    y = int(center[1] + dy - h / 2)
    canvas.paste(resized, (x, y), resized)


# How a background template that is NOT exactly CANVAS_W x CANVAS_H gets fitted:
#   "stretch" - resize it to exactly the canvas, so the WHOLE background stays visible
#               (nothing is cut off). A 3:4 template such as 1098x1460 is squeezed ~10%.
#   "cover"   - keep its shape, fill the canvas and crop whatever overflows (the sides get cut).
# For no cut AND no squeezing, make the background exactly CANVAS_W x CANVAS_H (1037x1517).
BG_FIT_MODE = "stretch"
# "stretch" is only used while the template's shape is within this factor of the canvas's
# shape; a very different shape (e.g. a wide landscape image) would look badly squashed,
# so it falls back to "cover".
BG_MAX_STRETCH = 1.25


def _cover_to_canvas(img: Image.Image) -> Image.Image:
    """Fit the background template to the card canvas (see BG_FIT_MODE)."""
    if img.size == (CANVAS_W, CANVAS_H):
        return img
    if BG_FIT_MODE == "stretch":
        shape = (img.width / img.height) / (CANVAS_W / CANVAS_H)
        if 1 / BG_MAX_STRETCH <= shape <= BG_MAX_STRETCH:
            return img.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
    # "cover": scale to fill the canvas while keeping the aspect ratio, then
    # center-crop the overflow.
    scale = max(CANVAS_W / img.width, CANVAS_H / img.height)
    new_w = round(img.width * scale)
    new_h = round(img.height * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - CANVAS_W) // 2
    top = (new_h - CANVAS_H) // 2
    return img.crop((left, top, left + CANVAS_W, top + CANVAS_H))


async def generate_card_image(card_row, background_local_path: str) -> io.BytesIO:
    """Build the final PNG for a card DB row. Returns an in-memory PNG.

    Only the foreground download needs the event loop (network I/O). Every
    other step below is CPU-bound Pillow work (opening/resizing/compositing
    images, drawing several text fields with font rendering, saving the
    PNG) — running that inline on the event loop would block ALL other
    Discord interactions (other users' commands, gateway heartbeats) for
    as long as it takes, which is what was causing random
    "Unknown Message"/"Unknown interaction" 404s on followup.send() across
    the bot, not just on this command. So we download the foreground async,
    then hand the rest off to a worker thread via asyncio.to_thread.
    """
    fg = await _download_image(card_row["foreground_link"])
    return await asyncio.to_thread(_build_card_image_sync, card_row, background_local_path, fg)


def _build_card_image_sync(card_row, background_local_path: str, fg: Image.Image) -> io.BytesIO:
    """All the CPU-bound Pillow work — runs in a worker thread, never on the event loop."""
    background = Image.open(background_local_path).convert("RGBA")
    background = _cover_to_canvas(background)

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
    fg_resized, paste_pos = _fit_foreground(
        fg, FG_BOX, card_row["offset_x"], card_row["offset_y"],
        card_row["scale_pct"] if "scale_pct" in card_row.keys() else 100,
    )
    canvas.alpha_composite(fg_resized, dest=paste_pos)

    # ── Put the bottom stats panel IN FRONT of the cutout ──────────────
    # Copy the untouched template rows back over everything from the panel's
    # top border down, so the player appears to stand behind the box.
    # panel_dy (from /cardlayout) nudges that border up/down.
    if FG_CLIP_TO_PANEL:
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
        panel_resized, panel_pos = _fit_foreground(
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
    if role in ("BOWL", "LBOWL", "RBOWL"):
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

    # ── Role word alone in the top centre strip ────────────────────────
    role_dx    = _col(card_row, "role_dx", 0)
    role_dy    = _col(card_row, "role_dy", 0)
    role_size  = _col(card_row, "role_size", ROLE_SIZE)
    hand_dx    = _col(card_row, "hand_dx", 0)
    hand_dy    = _col(card_row, "hand_dy", 0)
    hand_size  = _col(card_row, "hand_size", HAND_SIZE)
    role_center = (ROLE_CENTER[0] + role_dx, ROLE_CENTER[1] + role_dy)
    role_font = _font(FONT_LABEL, role_size)
    _draw_centered(draw, role_center, role_word, role_font, WHITE)

    # ── OVR inside the big centre circle: number, then the word "OVR" under it ──
    ovr_center = (OVR_CENTER[0] + ovr_dx, OVR_CENTER[1] + ovr_dy)
    ovr_font = _font(FONT_DISPLAY_BOLD, ovr_size)
    _draw_centered(draw, ovr_center, str(card_row["ovr"]), ovr_font, GOLD)
    _draw_centered(
        draw,
        (ovr_center[0], ovr_center[1] + STAT_LABEL_OFFSET_Y),
        OVR_LABEL_TEXT, label_font, WHITE,
    )

    # ── Batting hand (off by default, see SHOW_HAND_TEXT) ──────────────
    hand_word = HAND_CARD_WORD.get(_col(card_row, "batting_hand"))
    if SHOW_HAND_TEXT and hand_word:
        hand_center = (HAND_CENTER[0] + hand_dx, HAND_CENTER[1] + hand_dy)
        _draw_centered(draw, hand_center, hand_word, _font(FONT_LABEL, hand_size), WHITE)

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

    # ── Country logo, just above the country name ──────────────────────
    country_logo = _load_country_logo(_col(card_row, "country"))
    if country_logo is not None:
        _draw_logo(
            canvas, country_logo,
            (country_center[0], country_center[1] + COUNTRY_LOGO_OFFSET_Y),
            _col(card_row, "countrylogo_dx", 0),
            _col(card_row, "countrylogo_dy", 0),
            _col(card_row, "countrylogo_scale", 100),
            box=COUNTRY_LOGO_BOX,
        )

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf
