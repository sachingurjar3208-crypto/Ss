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
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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


# ── Reuse Discord's own copy of the image (no upload at all) ─────────────────
# After a card has been uploaded ONCE, Discord hosts it on its CDN. Later
# commands just point the embed at that link, so there is nothing to upload and
# the reply is as fast as a plain text message. Discord links expire (~24h), so
# we only reuse one while it has plenty of life left, then upload again.
URL_MAX_TTL = 6 * 3600     # never trust a link for longer than this
URL_MIN_LEFT = 30 * 60     # drop a link that expires within this time

_urls: dict[str, tuple[float, str, float]] = {}   # path -> (source mtime, url, trust-until)


def cached_url(path: str | None) -> str | None:
    """Discord CDN link for this card if we still trust it, else None."""
    if not path:
        return None
    hit = _urls.get(path)
    if not hit:
        return None
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None
    if hit[0] != mtime or hit[2] <= time.time():
        _urls.pop(path, None)
        return None
    return hit[1]


def image_url(path: str | None) -> str:
    """What an embed should use as its image: the CDN link, or the attachment."""
    return cached_url(path) or f"attachment://{FILENAME}"


def remember(path: str | None, message: discord.Message | None) -> None:
    """Call right after sending the card as a file: stores Discord's link for it."""
    if not path or message is None:
        return
    try:
        att = next((a for a in message.attachments if a.filename == FILENAME), None)
        if att is None:
            return
        url = att.url
        now = time.time()
        trust_until = now + URL_MAX_TTL
        ex = parse_qs(urlparse(url).query).get("ex")
        if ex:   # hex unix time when Discord stops serving this link
            trust_until = min(trust_until, int(ex[0], 16) - URL_MIN_LEFT)
        if trust_until <= now:
            return
        if len(_urls) >= 500:
            _urls.pop(next(iter(_urls)))
        _urls[path] = (Path(path).stat().st_mtime, url, trust_until)
    except Exception as e:   # never let caching break a command
        print(f"[card_cache] could not remember url: {e}")


async def send_embed(ctx, embed: discord.Embed, path: str | None, **kwargs) -> discord.Message:
    """ctx.send(embed) with the card image, using the fastest route available."""
    url = cached_url(path)
    if url:
        embed.set_image(url=url)
        return await ctx.send(embed=embed, **kwargs)
    file = await card_file(path)
    if file is None:
        return await ctx.send(embed=embed, **kwargs)
    embed.set_image(url=f"attachment://{FILENAME}")
    msg = await ctx.send(embed=embed, file=file, **kwargs)
    remember(path, msg)
    return msg
