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
FADE_TOP, FADE_FULL = 600, 870           # player fades into deep purple from about these y values
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


GOLD_TOP, GOLD_BOT = (255, 238, 170), (214, 150, 40)
PURPLE_DEEP = (16, 6, 40)


def _gold_fill(size) -> Image.Image:
    """Vertical bright-to-deep gold gradient."""
    g = Image.linear_gradient("L").resize(size)
    top = Image.new("RGB", size, GOLD_TOP)
    bot = Image.new("RGB", size, GOLD_BOT)
    return Image.composite(bot, top, g)


def _gold_text(card, xy, text, font, anchor="mm"):
    m = Image.new("L", card.size, 0)
    ImageDraw.Draw(m).text(xy, text, font=font, fill=255, anchor=anchor)
    bb = m.getbbox()
    if not bb:
        return
    sh = m.filter(ImageFilter.GaussianBlur(4))
    card.paste((0, 0, 0, 255), (2, 3), sh.point(lambda v: int(v * 0.6)))
    card.paste(_gold_fill((bb[2] - bb[0], bb[3] - bb[1])), bb[:2], m.crop(bb))


def _gold_bar(card, x1, x2, y, h, fade_left: bool):
    """A thick gold bar that fades out toward the card edge."""
    w = int(x2 - x1)
    bar = _gold_fill((w, h)).convert("RGBA")
    ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))   # 255 left -> 0 right
    if fade_left:
        ramp = ramp.transpose(Image.FLIP_LEFT_RIGHT)
    a = ramp.point(lambda v: int(min(255, 60 + v * 0.9)))
    bar.putalpha(a)
    card.alpha_composite(bar, (int(x1), int(y - h / 2)))


def _laurel_sprig(card, x, y, flip=False, size=46):
    """Small gold laurel branch (5 leaf pairs along a curved stem)."""
    import math
    lay = Image.new("L", (size * 3, size * 3), 0)
    cx, cy = size * 1.5, size * 2.6
    R = size * 1.6
    stem = [(cx + R * math.cos(math.radians(a)), cy + R * math.sin(math.radians(a))) for a in range(200, 296, 4)]
    ImageDraw.Draw(lay).line(stem, fill=255, width=3)
    leaf = Image.new("L", (26, 11), 0)
    ImageDraw.Draw(leaf).ellipse((0, 0, 25, 10), fill=255)
    for i in range(6):
        a = math.radians(205 + i * 17)
        px, py = cx + R * math.cos(a), cy + R * math.sin(a)
        tang = a + math.pi / 2
        for side in (-1, 1):
            ang = tang + side * 0.75 + math.pi
            lx, ly = px + math.cos(ang) * 12, py + math.sin(ang) * 12
            lf = leaf.rotate(-math.degrees(ang), expand=True, resample=Image.BICUBIC)
            lay.paste(255, (int(lx - lf.width / 2), int(ly - lf.height / 2)), lf)
    if flip:
        lay = lay.transpose(Image.FLIP_LEFT_RIGHT)
    gold = _gold_fill(lay.size).convert("RGBA")
    gold.putalpha(lay)
    card.alpha_composite(gold, (int(x - lay.width / 2), int(y - lay.height / 2)))


def _gold_signature(card: Image.Image, sig: Image.Image) -> None:
    """Paste the signature as glowing gold ink."""
    x1, y1, x2, y2 = SIG_BOX
    bw, bh = x2 - x1, y2 - y1
    scale = min(bw / sig.width, bh / sig.height)
    a = sig.getchannel("A")
    if scale < 0.75:
        a = a.filter(ImageFilter.MaxFilter(3 if scale > 0.4 else 5))
    a = a.resize((max(1, int(sig.width * scale)), max(1, int(sig.height * scale))), Image.LANCZOS)
    pos = (x1 + (bw - a.width) // 2, y1 + (bh - a.height) // 2)
    full = Image.new("L", card.size, 0)
    full.paste(a, pos)
    glow = full.filter(ImageFilter.GaussianBlur(10))
    card.paste((255, 170, 60, 255), (0, 0), glow.point(lambda v: min(255, int(v * 1.1))))
    card.paste((60, 30, 0, 255), (2, 3), full.filter(ImageFilter.GaussianBlur(2)).point(lambda v: int(v * 0.6)))
    card.paste(_gold_fill(card.size), (0, 0), full)
    hi = full.filter(ImageFilter.MinFilter(3))
    card.paste((255, 250, 225, 255), (-1, -1), hi.point(lambda v: int(v * 0.35)))


def apply_signature_look(base: Image.Image, card_row, role: str) -> Image.Image:
    """base = the finished normal card (RGBA, 1037 x 1517) drawn WITHOUT a name, on the purple
    Signature backdrop. Adds: player fading into deep purple, the GOLD signature, NAME | ROLE with
    gold bars, the 6 stats in gold, and "SIGNATURE EDITION" between two laurel sprigs."""
    base = base.convert("RGBA")
    if base.size != (W, H):
        base = base.resize((W, H), Image.LANCZOS)
    alpha = base.getchannel("A")
    card = base.copy()

    # Panel style Clear / Glass with the card's own background (/cardbg): keep THAT background
    # under the signature and stats instead of the purple Signature backdrop.
    mode = str(ci._col(card_row, "panel_mode", "") or "").lower()
    own_back = None
    if mode in ("clear", "glass"):
        try:
            import card_full
            ob = card_full._card_bg(card_row)
            if ob is not None:
                ob = ob.convert("RGB").resize((W, H), Image.LANCZOS)
                if mode == "glass":
                    ob = ob.filter(ImageFilter.GaussianBlur(18))
                own_back = Image.blend(ob, Image.new("RGB", (W, H), (0, 0, 0)), 0.35).convert("RGBA")
        except Exception as e:
            print(f"[signature] own background skipped: {type(e).__name__}: {e}")

    # 1) the player fades into deep purple (the backdrop's light swirls still show through)
    fade = Image.new("L", (W, H), 0)
    fd = ImageDraw.Draw(fade)
    for y in range(FADE_TOP + 120, H):
        v = int(150 * min(1.0, ((y - FADE_TOP - 120) / 330)) ** 1.4)
        fd.line((0, y, W, y), fill=v)
    fade_to = own_back if own_back is not None else Image.new("RGBA", (W, H), PURPLE_DEEP + (255,))
    card = Image.composite(fade_to, card, fade)
    # the normal stats panel of the base card is covered with the backdrop again
    if own_back is not None:
        back = own_back
    else:
        try:
            import card_full
            back = card_full._sig_icon_bg().resize((W, H), Image.LANCZOS).convert("RGBA")
            back = Image.blend(back, Image.new("RGBA", (W, H), PURPLE_DEEP + (255,)), 0.25)
        except Exception:
            back = Image.new("RGBA", (W, H), PURPLE_DEEP + (255,))
    cover = Image.new("L", (W, H), 0)
    cd = ImageDraw.Draw(cover)
    if own_back is not None:      # bright own backgrounds: hide the old stats panel completely
        for y in range(900, H):
            cd.line((0, y, W, y), fill=min(255, int(255 * ((y - 900) / 90))))
    else:
        for y in range(985, H):
            cd.line((0, y, W, y), fill=min(255, int(255 * ((y - 985) / 130) ** 1.3)))
    card = Image.composite(back, card, cover)

    # 2) the signature in gold (or the name, big, when there is no signature file)
    playername = str(ci._col(card_row, "playername", "") or ci._col(card_row, "displayname", ""))
    display = str(ci._col(card_row, "displayname", playername) or playername).upper()
    sig = _load_signature(playername)
    if sig is not None:
        _gold_signature(card, sig)
    else:
        d = ImageDraw.Draw(card)
        x1, y1, x2, y2 = SIG_BOX
        f = _fit(d, display, ci.FONT_NAME if Path(ci.FONT_NAME).exists() else ci.FONT_DISPLAY_BOLD, 120, x2 - x1)
        _gold_text(card, ((x1 + x2) / 2, (y1 + y2) / 2), display, f)

    # glowing light line under the signature
    line = Image.new("L", (W, H), 0)
    ImageDraw.Draw(line).line((200, 1122, 837, 1122), fill=255, width=3)
    card.paste((255, 196, 90, 255), (0, 0), line.filter(ImageFilter.GaussianBlur(7)).point(lambda v: min(255, v * 2)))
    card.paste((255, 236, 190, 255), (0, 0), line.point(lambda v: int(v * 0.7)))

    # 3) NAME | ROLE between two gold bars
    d = ImageDraw.Draw(card)
    role_word = ROLE_WORDS.get(role, role)
    f_row = _fit(d, display, ci.FONT_LABEL, 38, 300)
    f_role = _font(ci.FONT_LABEL, 38)
    left_w = d.textlength(display, font=f_row)
    right_w = d.textlength(role_word, font=f_role)
    _gold_bar(card, 52, W / 2 - 40 - left_w - 14, 1160, 16, fade_left=True)
    _gold_bar(card, W / 2 + 40 + right_w + 14, W - 52, 1160, 16, fade_left=False)
    d.text((W / 2 - 40, 1160), display, font=f_row, fill=(255, 255, 255), anchor="rm")
    d.line((W / 2, 1138, W / 2, 1182), fill=GOLD_BOT, width=4)
    d.text((W / 2 + 40, 1160), role_word, font=f_role, fill=(255, 255, 255), anchor="lm")

    # 4) stats: gold numbers, white labels
    try:
        rows = card_stat_rows(card_row)[:6]
    except Exception:
        rows = []
    f_num, f_lab = _font(ci.FONT_DISPLAY_BOLD, 64), _font(ci.FONT_LABEL, 40)
    cols = [250, 518, 786]
    for i, (label, value) in enumerate(rows):
        x, y = cols[i % 3], 1222 + (i // 3) * 72
        _gold_text(card, (x - 6, y), str(value), f_num, anchor="rm")
        d.text((x + 6, y), str(label), font=f_lab, fill=(255, 255, 255), anchor="lm")

    # 5) edition line with laurels
    line = Image.new("L", (W, H), 0)
    ImageDraw.Draw(line).line((340, 1340, 697, 1340), fill=255, width=2)
    card.paste((255, 200, 100, 255), (0, 0), line.filter(ImageFilter.GaussianBlur(4)).point(lambda v: min(255, v * 2)))
    card.paste((255, 226, 150, 255), (0, 0), line)
    _gold_text(card, (W / 2, 1390), EDITION_TEXT, _font(ci.FONT_LABEL, 32))
    _laurel_sprig(card, 268, 1392, flip=True)
    _laurel_sprig(card, W - 268, 1392)

    # the two cut corners (top-right, bottom-left) are filled with the purple backdrop
    # instead of being see-through (they looked white in Discord / the gallery)
    full = back.copy()
    full.alpha_composite(card if card.mode == "RGBA" else card.convert("RGBA"))
    card = Image.composite(card, full, alpha)
    try:
        import card_full
        r = card_full.RADIUS
    except Exception:
        r = 34
    m = Image.new("L", (W * 2, H * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, W * 2 - 1, H * 2 - 1), radius=r * 2, fill=255)
    card.putalpha(m.resize((W, H), Image.LANCZOS))
    return card


class _NoName:
    """Wraps a card row so the normal renderer draws it without the name, on the Signature backdrop."""

    def __init__(self, row):
        self._row = row

    def __getitem__(self, key):
        if key == "displayname":
            return " "
        if key == "s2_theme":
            return "sig_base"      # charcoal + gold backdrop (card_full.THEMES["sig_base"])
        return self._row[key]

    def keys(self):
        return self._row.keys()

    def get(self, key, default=None):
        try:
            v = self[key]
        except (IndexError, KeyError):
            return default
        return default if v is None else v
