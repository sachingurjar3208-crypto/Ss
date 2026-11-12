"""Spawn image: the card with ONLY the name blurred.

How the name is found (works for every theme and every /cardlayout offset):
  the card is drawn twice, once normally and once with an empty name. The pixels that differ
  are exactly the name. That box (+ padding) is pixelated + blurred on the normal card.
The result is saved in spawn_cache/, so each card is only worked out ONCE (later spawns of the
same card just read the file, a few ms). Changing the card (updated_at) makes a new file.
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

import card_db
from card_image import generate_card_image

CACHE_DIR = Path(__file__).parent / "spawn_cache"
PAD = 34                 # px of extra blur around the name (bigger than the soft edge)


class _NoName:
    """The card row with an empty name (one space, so no renderer falls back to playername)."""

    def __init__(self, row):
        self._row = row

    def __getitem__(self, key):
        if key == "displayname":
            return " "
        return self._row[key]

    def keys(self):
        return self._row.keys()

    def __iter__(self):
        return iter(self._row)


def _design_tag() -> str:
    """Changes when the card SHAPE (/cardshape) or the card design code changes (git pull),
    so spawns never show an image drawn with an old design."""
    try:
        import card_style2
        shape = card_style2.CARD_SHAPE
    except Exception:
        shape = "x"
    ver = 0
    for f in ("card_full.py", "card_style2.py", "card_image.py"):
        try:
            ver = max(ver, int((Path(__file__).parent / f).stat().st_mtime))
        except OSError:
            pass
    return f"{shape}{ver}"


def _cache_path(card) -> Path:
    stamp = int(float(card["updated_at"] or 0))
    return CACHE_DIR / f"{card['playername_key'].replace(' ', '_').replace('/', '_')}_{stamp}_{_design_tag()}.png"


def _blur(full: bytes, nameless: bytes) -> bytes:
    a = Image.open(io.BytesIO(full)).convert("RGBA")
    b = Image.open(io.BytesIO(nameless)).convert("RGBA")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 24 else 0)
    box = diff.getbbox()
    w, h = a.size
    if box is None or (box[2] - box[0]) * (box[3] - box[1]) > 0.35 * w * h:
        # nothing found / renderer not repeatable: the nameless card hides the name too
        out = b
    else:
        x0, y0 = max(0, box[0] - PAD), max(0, box[1] - PAD)
        x1, y1 = min(w, box[2] + PAD), min(h, box[3] + PAD)
        region = a.crop((x0, y0, x1, y1))
        rw, rh = region.size
        # shrink to a handful of pixels (letters disappear), grow back, soften
        flat = Image.new("RGBA", region.size, (0, 0, 0, 255))
        flat.alpha_composite(region)                        # see-through outlines would show the letters
        tiny = flat.resize((max(1, rw // 80), max(1, rh // 40)), Image.BOX)
        cover = tiny.resize((rw, rh), Image.BICUBIC).filter(ImageFilter.GaussianBlur(18))
        # soft edges so it doesn't look like a pasted box
        m = Image.new("L", (rw, rh), 0)
        e = min(14, rw // 6, rh // 4)
        ImageDraw.Draw(m).rounded_rectangle((e, e, rw - 1 - e, rh - 1 - e), max(1, e), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(max(1, e / 2)))
        m = ImageChops.multiply(m, region.getchannel("A").point(lambda v: 255 if v > 0 else 0))   # keep card corners see-through
        out = a.copy()
        out.paste(cover, (x0, y0), m)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def blurred_card(card) -> bytes | None:
    """PNG bytes of the card with the name blurred, or None if it can't be drawn."""
    try:
        path = _cache_path(card)
        if path.exists():
            return await asyncio.to_thread(path.read_bytes)
        bg = card_db.get_background(card["background"])
        if bg is None and card_db._rget(card, "card_style") != "style2":
            return None
        bg_path = bg["local_path"] if bg is not None else ""
        full = (await generate_card_image(card, bg_path)).getvalue()
        nameless = (await generate_card_image(_NoName(card), bg_path)).getvalue()
        data = await asyncio.to_thread(_blur, full, nameless)
        CACHE_DIR.mkdir(exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        # old versions of this card are not needed any more
        prefix = card['playername_key'].replace(' ', '_').replace('/', '_') + "_"
        for old in CACHE_DIR.glob(f"{prefix}*.png"):
            rest = old.name[len(prefix):-4]
            parts = rest.split("_")
            same_card = parts[0].isdigit() and len(parts) <= 2   # not "virat_kohli_(s1)_..."
            if old != path and same_card:
                try:
                    old.unlink()
                except OSError:
                    pass
        return data
    except Exception as e:
        print(f"[spawn] blur failed for {card['playername_key']}: {type(e).__name__}: {e}")
        return None
