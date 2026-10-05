"""Fast card images for csview / csbuy / cssell.

WHY IT WAS SLOW: every command re-uploaded the full-size card PNG
(1037x1517, several MB) to Discord, reading it from disk each time.
The upload alone took seconds.

WHAT THIS DOES: builds a smaller JPEG copy of each card ONCE (800px wide,
a few hundred KB), keeps it on disk (generated/display/) and in memory,
and sends that instead. The original PNG is never touched, so /cardmaker
and /editcard still work on full quality.
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

import discord
from PIL import Image

DISPLAY_W = 800          # width of the copy that gets sent in chat
JPEG_QUALITY = 90
MAX_MEMORY_ITEMS = 100   # how many cards stay in RAM (~300KB each, kept small for a 1GB server)
FILENAME = "card.jpg"    # embeds must use attachment://card.jpg

_DISPLAY_DIR = Path(__file__).parent / "generated" / "display"
_mem: dict[str, tuple[float, bytes]] = {}   # source path -> (source mtime, jpeg bytes)


def _build(src: str, dst: Path) -> bytes:
    img = Image.open(src).convert("RGB")
    if img.width > DISPLAY_W:
        h = round(img.height * DISPLAY_W / img.width)
        img = img.resize((DISPLAY_W, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    data = buf.getvalue()
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    except OSError:
        pass  # disk cache is only a bonus
    return data


def _get_sync(path: str) -> bytes | None:
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    hit = _mem.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    dst = _DISPLAY_DIR / (p.stem + ".jpg")
    data = None
    try:
        if dst.exists() and dst.stat().st_mtime >= mtime:
            data = dst.read_bytes()
    except OSError:
        data = None
    if data is None:
        try:
            data = _build(path, dst)
        except Exception as e:
            print(f"[card_cache] could not build display copy for {path}: {e}")
            return None
    if len(_mem) >= MAX_MEMORY_ITEMS:
        _mem.pop(next(iter(_mem)))
    _mem[path] = (mtime, data)
    return data


async def card_file(path: str | None) -> discord.File | None:
    """Ready-to-send discord.File for a card, or None if there is no image.
    Heavy work runs in a thread so the bot never freezes."""
    if not path:
        return None
    data = await asyncio.to_thread(_get_sync, path)
    if data is None:
        return None
    return discord.File(io.BytesIO(data), filename=FILENAME)


async def warm(path: str | None) -> None:
    """Pre-build the display copy (call right after a card image is made)."""
    if path:
        await asyncio.to_thread(_get_sync, path)
