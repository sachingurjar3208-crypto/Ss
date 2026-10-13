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
FILENAME = "card.jpg"    # opaque (classic) cards
FILENAME_PNG = "card.png"   # cards with transparent corners (Style 2) - JPEG would turn them black

# New folder name on purpose: old display copies were all flattened JPEGs (transparent
# corners turned black), so they must not be reused.
_DISPLAY_DIR = Path(__file__).parent / "generated" / "display_v2"
_mem: dict[str, tuple[float, bytes]] = {}   # source path -> (source mtime, jpeg bytes)


def _has_transparency(img: Image.Image) -> bool:
    if img.mode not in ("RGBA", "LA") and "transparency" not in img.info:
        return False
    alpha = img.convert("RGBA").getchannel("A")
    return alpha.getextrema()[0] < 250


def _build(src: str, dst_base: Path) -> bytes:
    """Small display copy. Cards with transparent corners stay PNG (alpha kept);
    opaque cards become JPEG as before."""
    img = Image.open(src)
    alpha = _has_transparency(img)
    img = img.convert("RGBA" if alpha else "RGB")
    if img.width > DISPLAY_W:
        h = round(img.height * DISPLAY_W / img.width)
        img = img.resize((DISPLAY_W, h), Image.LANCZOS)
    buf = io.BytesIO()
    if alpha:
        img.save(buf, format="PNG", optimize=True)
        dst = dst_base.with_suffix(".png")
    else:
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        dst = dst_base.with_suffix(".jpg")
    data = buf.getvalue()
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        other = dst_base.with_suffix(".jpg" if alpha else ".png")
        other.unlink(missing_ok=True)      # a card that changed type: drop the stale copy
    except OSError:
        pass  # disk cache is only a bonus
    return data


def is_png(data: bytes | None) -> bool:
    return bool(data) and data[:4] == b"\x89PNG"


def _get_sync(path: str) -> bytes | None:
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    hit = _mem.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    base = _DISPLAY_DIR / p.stem
    data = None
    for ext in (".png", ".jpg"):
        cand = base.with_suffix(ext)
        try:
            if cand.exists() and cand.stat().st_mtime >= mtime:
                data = cand.read_bytes()
                break
        except OSError:
            data = None
    if data is None:
        try:
            data = _build(path, base)
        except Exception as e:
            print(f"[card_cache] could not build display copy for {path}: {e}")
            return None
    if len(_mem) >= MAX_MEMORY_ITEMS:
        _mem.pop(next(iter(_mem)))
    _mem[path] = (mtime, data)
    return data


def filename_for(path: str | None) -> str:
    """card.png for cards with transparency, card.jpg otherwise."""
    return FILENAME_PNG if is_png(_get_sync(path)) else FILENAME if path else FILENAME


def load_rgba(path: str | None) -> Image.Image | None:
    """The card's display copy as RGBA (alpha = 255 everywhere for opaque cards).
    Use this to paste cards onto other images so the transparent corners stay see-through."""
    if not path:
        return None
    try:
        data = _get_sync(path)
        if data:
            return Image.open(io.BytesIO(data)).convert("RGBA")
        return Image.open(path).convert("RGBA")
    except Exception as e:
        print(f"[card_cache] could not load {path}: {e}")
        return None


async def card_file(path: str | None) -> discord.File | None:
    """Ready-to-send discord.File for a card, or None if there is no image.
    Heavy work runs in a thread so the bot never freezes."""
    if not path:
        return None
    data = await asyncio.to_thread(_get_sync, path)
    if data is None:
        return None
    return discord.File(io.BytesIO(data), filename=FILENAME_PNG if is_png(data) else FILENAME)


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
    return cached_url(path) or f"attachment://{filename_for(path)}"


def remember(path: str | None, message: discord.Message | None) -> None:
    """Call right after sending the card as a file: stores Discord's link for it."""
    if not path or message is None:
        return
    try:
        att = next((a for a in message.attachments if a.filename in (FILENAME, FILENAME_PNG)), None)
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
    embed.set_image(url=f"attachment://{file.filename}")
    msg = await ctx.send(embed=embed, file=file, **kwargs)
    remember(path, msg)
    return msg
