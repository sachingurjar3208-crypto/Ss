"""Signature Edition look for Style 2 cards (theme "signature").

The normal Style 2 card is drawn first (purple shield, player, OVR / role / country), then:
  * the player fades into an ivory "autograph paper" lower half,
  * YOUR signature PNG is placed big on that paper,
  * under it: NAME | ROLE, the 6 stats in dark ink, and "SIGNATURE EDITION".

Signature files
---------------
    signatures/<playername with _ for spaces, lowercase>.png       e.g. signatures/virat_kohli.png

Add one with /signatureadd (Discord) or just put the PNG in the folder.
  * transparent PNG works best; a white / light background is made see-through automatically
  * empty space around the signature is trimmed, then it is fitted into SIG_BOX and centred
  * a very light (white / gold) signature is re-coloured to dark ink so it shows on the paper
No file for a card -> the player's name is written big in its place (the card never looks empty).
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import card_image as ci
from card_stats import card_stat_rows

SIGNATURES_DIR = Path(__file__).parent / "signatures"
SIGNATURES_DIR.mkdir(parents=True, exist_ok=True)

W, H = ci.CANVAS_W, ci.CANVAS_H          # 1037 x 1517 (same as every card)

# ── Look (tweak here) ─────────────────────────────────────────────────────
IVORY = (246, 241, 229)
INK = (16, 14, 22)
NAVY = (34, 26, 70)
GOLD = (196, 150, 58)
BORDER_GOLD = (234, 205, 120)
FADE_TOP, FADE_FULL = 600, 870           # player fades into the paper between these y values
SIG_BOX = (170, 800, 870, 1115)          # where the signature is fitted (x1, y1, x2, y2)
ROLE_WORDS = {"BAT": "BATTER", "BOWL": "BOWLER", "AR": "ALL-ROUNDER", "WK": "KEEPER"}
EDITION_TEXT = "S I G N A T U R E   E D I T I O N"


def signature_path(playername: str) -> Path:
    return SIGNATURES_DIR / f"{str(playername).strip().lower().replace(' ', '_')}.png"


# ── Uploads ───────────────────────────────────────────────────────────────

def prepare_signature(data: bytes) -> Image.Image:
    """Clean an uploaded signature: light background -> see-through, trimmed. Raises on bad files."""
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    alpha = im.getchannel("A")
    if alpha.getextrema()[0] > 250:                     # no transparency at all: remove the paper
        lum = im.convert("L")
        # dark = ink (opaque), light = paper (clear), soft edge in between
        alpha = lum.point(lambda v: 255 if v < 150 else (0 if v > 215 else int((215 - v) * 255 / 65)))
        im.putalpha(alpha)
    box = im.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    if not box:
        raise ValueError("the image has no visible signature")
    im = im.crop(box)
    if max(im.size) > 2000:                             # keep files small
        im.thumbnail((2000, 2000), Image.LANCZOS)
    return im


def save_signature(playername: str, data: bytes) -> Path:
    path = signature_path(playername)
    prepare_signature(data).save(path, "PNG", optimize=True)
    return path


def remove_signature(playername: str) -> bool:
    path = signature_path(playername)
    if path.exists():
        path.unlink()
        return True
    return False


# ── Drawing ───────────────────────────────────────────────────────────────

def _font(path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _fit(draw, text: str, path, size: int, max_w: int):
    f = _font(path, size)
    while size > 14 and draw.textlength(text, font=f) > max_w:
        size -= 2
        f = _font(path, size)
    return f


def _load_signature(playername: str) -> Image.Image | None:
    path = signature_path(playername)
    if not path.exists():
        return None
    try:
        sig = Image.open(path).convert("RGBA")
    except Exception:
        return None
    # a light signature would vanish on the ivory paper -> dark ink
    a = sig.getchannel("A")
    lum = sig.convert("L")
    visible = [l for l, al in zip(lum.getdata(), a.getdata()) if al > 128]
    if visible and sum(visible) / len(visible) > 175:
        ink = Image.new("RGBA", sig.size, INK + (255,))
        ink.putalpha(a)
        sig = ink
    return sig


def _paste_signature(card: Image.Image, sig: Image.Image) -> None:
    x1, y1, x2, y2 = SIG_BOX
    bw, bh = x2 - x1, y2 - y1
    scale = min(bw / sig.width, bh / sig.height)
    if scale < 0.75:
        # a big signature gets shrunk a lot: thicken the pen first so thin lines stay visible
        grow = 3 if scale > 0.4 else 5
        r, g, b, a = sig.split()
        a = a.filter(ImageFilter.MaxFilter(grow))
        rgb = Image.merge("RGB", (r, g, b)).filter(ImageFilter.MinFilter(grow))   # spread the ink colour too
        sig = Image.merge("RGBA", (*rgb.split(), a))
    sig = sig.resize((max(1, int(sig.width * scale)), max(1, int(sig.height * scale))), Image.LANCZOS)
    pos = (x1 + (bw - sig.width) // 2, y1 + (bh - sig.height) // 2)
    card.alpha_composite(sig, pos)


def _name_instead(card: Image.Image, name: str) -> None:
    d = ImageDraw.Draw(card)
    x1, y1, x2, y2 = SIG_BOX
    f = _fit(d, name, ci.FONT_NAME if Path(ci.FONT_NAME).exists() else ci.FONT_DISPLAY_BOLD, 120, x2 - x1)
    d.text(((x1 + x2) / 2, (y1 + y2) / 2), name, font=f, fill=INK, anchor="mm")


def apply_signature_look(base: Image.Image, card_row, role: str) -> Image.Image:
    """base = the finished normal Style 2 card (RGBA, 1037 x 1517) drawn WITHOUT a name."""
    base = base.convert("RGBA")
    if base.size != (W, H):
        base = base.resize((W, H), Image.LANCZOS)
    alpha = base.getchannel("A")
    card = base.copy()

    # 1) player fades into ivory paper
    fade = Image.new("L", (W, H), 0)
    fd = ImageDraw.Draw(fade)
    for y in range(FADE_TOP, H):
        v = 255 if y >= FADE_FULL else int(255 * ((y - FADE_TOP) / (FADE_FULL - FADE_TOP)) ** 1.6)
        fd.line((0, y, W, y), fill=v)
    card = Image.composite(Image.new("RGBA", (W, H), IVORY + (255,)), card, fade)
    tex = Image.new("L", (W, H), 0)
    td = ImageDraw.Draw(tex)
    for y in range(FADE_FULL, H, 7):
        td.line((0, y, W, y), fill=10)
    card = Image.composite(Image.new("RGBA", (W, H), (210, 200, 180, 255)), card, tex)

    # 2) the signature (or the name, big, when there is no signature file)
    playername = str(ci._col(card_row, "playername", "") or ci._col(card_row, "displayname", ""))
    display = str(ci._col(card_row, "displayname", playername) or playername).upper()
    sig = _load_signature(playername)
    if sig is not None:
        _paste_signature(card, sig)
    else:
        _name_instead(card, display)

    # 3) NAME | ROLE, stats, edition line
    d = ImageDraw.Draw(card)
    role_word = ROLE_WORDS.get(role, role)
    f_row = _fit(d, display, ci.FONT_LABEL, 38, 330)
    d.text((W / 2 - 40, 1160), display, font=f_row, fill=NAVY, anchor="rm")
    d.line((W / 2, 1138, W / 2, 1182), fill=GOLD, width=4)
    d.text((W / 2 + 40, 1160), role_word, font=_font(ci.FONT_LABEL, 38), fill=NAVY, anchor="lm")
    try:
        rows = card_stat_rows(card_row)[:6]
    except Exception:
        rows = []
    f_num, f_lab = _font(ci.FONT_DISPLAY_BOLD, 64), _font(ci.FONT_LABEL, 40)
    cols = [250, 518, 786]
    for i, (label, value) in enumerate(rows):
        x, y = cols[i % 3], 1222 + (i // 3) * 72
        d.text((x - 6, y), str(value), font=f_num, fill=INK, anchor="rm")
        d.text((x + 6, y), str(label), font=f_lab, fill=NAVY, anchor="lm")
    d.line((360, 1340, 677, 1340), fill=GOLD, width=3)
    d.text((W / 2, 1388), EDITION_TEXT, font=_font(ci.FONT_LABEL, 30), fill=GOLD, anchor="mm")

    # 4) the gold border again (the paper covered its lower part)
    inner = alpha.filter(ImageFilter.MinFilter(25))
    ring = Image.composite(Image.new("L", (W, H), 255), Image.new("L", (W, H), 0),
                           Image.eval(inner, lambda v: 255 - v))
    ring = Image.composite(ring, Image.new("L", (W, H), 0), alpha)
    card = Image.composite(Image.new("RGBA", (W, H), BORDER_GOLD + (255,)), card, ring)
    card.putalpha(alpha)
    return card


class _NoName:
    """Wraps a card row so the normal renderer draws it without the name and without a theme."""

    def __init__(self, row):
        self._row = row

    def __getitem__(self, key):
        if key == "displayname":
            return " "
        if key == "s2_theme":
            return None
        return self._row[key]

    def keys(self):
        return self._row.keys()

    def get(self, key, default=None):
        try:
            v = self[key]
        except (IndexError, KeyError):
            return default
        return default if v is None else v
