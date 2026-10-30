"""Pool animations: the GIF shown before the result when someone pulls a Pool A player.

Managed with the owner/admin slash commands in draft_cog:
    /pool animation pool:A pathname:<name> url:<link> [event:<name>]   add a new animation
                                       (event empty = every draft, event set = only THAT event)
    /pool animation_remove pathname:<name>               delete an animation
    /pool animation_merge pathname:<name> url:<link>     replace that animation completely

How it is stored
- The link is downloaded ONCE, checked (must really be a GIF / PNG / JPG / WEBP, max 8 MB)
  and saved in the `pool_animations/` folder, so it keeps working even if the link dies later.
- The name -> file mapping is in the `pool_animation` table of economy.db.
- merge puts the new file in place first and only then deletes the old file, so a failed
  download never leaves a pool without its animation.
- If a pool has several animations, one is picked at random each time.
- Event animations: an animation added with `event:<name>` plays ONLY in that event's draft.
  An event without an animation of its own uses the general ones (event empty). The normal
  draft only uses the general ones.

Safety (admin-only commands, but still): only http(s) links, the host must resolve to a public
address (no localhost / private network), at most 4 redirects (each one re-checked),
size and time limits, and the file name comes from a strict pathname (letters, numbers, - _).
"""
from __future__ import annotations

import asyncio
import io
import ipaddress
import re
import secrets
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import economy

_HERE = Path(__file__).parent
ANIM_DIR = _HERE / "pool_animations"

POOLS = ("A",)                      # only Pool A has animations for now
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MAX_BYTES = 8 * 1024 * 1024         # Discord upload limit friendly
TIMEOUT_SECONDS = 20
MAX_REDIRECTS = 4
_FORMATS = {"GIF": "gif", "PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
_rng = secrets.SystemRandom()


class AnimError(Exception):
    """A problem that can be shown to the admin as is."""


# ── Database ─────────────────────────────────────────────────────────────────

def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pool_animation ("
        "pathname TEXT PRIMARY KEY, pool TEXT NOT NULL, file TEXT NOT NULL, "
        "source_url TEXT, updated REAL NOT NULL)"
    )
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pool_animation)")}
    if "event_key" not in cols:                      # '' = general (all drafts), else one event
        conn.execute("ALTER TABLE pool_animation ADD COLUMN event_key TEXT NOT NULL DEFAULT ''")


def ensure_table() -> None:
    conn = economy._conn()
    try:
        _ensure(conn)
    finally:
        conn.close()


def get(pathname: str) -> dict | None:
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute("SELECT * FROM pool_animation WHERE pathname = ?", (pathname,)).fetchone()
        return dict(row) if row else None


def names(prefix: str = "", pool: str | None = None) -> list[str]:
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT pathname, pool FROM pool_animation ORDER BY pathname").fetchall()
    p = prefix.lower()
    return [r["pathname"] for r in rows if p in r["pathname"].lower() and (pool is None or r["pool"] == pool)]


def listing(prefix: str = "") -> list[tuple[str, str]]:
    """[(pathname, event_key)] ('' = general animation), for the admin autocomplete."""
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT pathname, event_key FROM pool_animation ORDER BY pathname").fetchall()
    p = prefix.lower()
    return [(r["pathname"], r["event_key"] or "") for r in rows if p in r["pathname"].lower()]


def _files(conn, pool: str, event_key: str) -> list[Path]:
    rows = conn.execute(
        "SELECT file FROM pool_animation WHERE pool = ? AND event_key = ?", (pool, event_key)
    ).fetchall()
    return [f for f in (ANIM_DIR / r["file"] for r in rows) if f.is_file()]


def pick(pool: str = "A", event_key: str = "") -> Path | None:
    """A random saved animation file for this pool (None if there is none).

    event_key '' (or 'main') = the normal draft: only general animations.
    An event key = that event's own animations; if it has none, the general ones are used."""
    ek = "" if (event_key or "").strip().lower() in ("", "main") else event_key.strip().lower()
    try:
        with economy._tx() as conn:
            _ensure(conn)
            files = _files(conn, pool, ek) if ek else []
            if not files:
                files = _files(conn, pool, "")
    except Exception as e:
        print(f"[pool_anim] could not read animations: {e}")
        return None
    return _rng.choice(files) if files else None


# ── Link -> checked image bytes ──────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """http(s) only. A normal Giphy page link is turned into the direct GIF link."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise AnimError("`url` must be a link starting with http:// or https://")
    m = re.match(r"^https?://(?:www\.)?giphy\.com/gifs/(?:[\w-]*-)?([A-Za-z0-9]+)/?(?:\?.*)?$", url)
    if m:
        return f"https://media.giphy.com/media/{m.group(1)}/giphy.gif"
    return url


def _ip_is_public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified)


async def _check_host(url: str) -> None:
    host = urlparse(url).hostname
    if not host:
        raise AnimError("That link has no website name in it.")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise AnimError("Couldn't find that website. Check the link.")
    if not infos or not all(_ip_is_public(i[4][0]) for i in infos):
        raise AnimError("That link points to a private or local address, so it isn't allowed.")


def inspect_image(data: bytes) -> str:
    """Check the bytes really are a GIF/PNG/JPG/WEBP image. Returns the file extension."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        img.verify()
    except Exception:
        raise AnimError("That link isn't a valid GIF / image file.")
    ext = _FORMATS.get(fmt or "")
    if ext is None:
        raise AnimError("Only GIF, PNG, JPG or WEBP files are supported.")
    return ext


async def fetch_image(url: str) -> tuple[bytes, str]:
    """Download `url` safely. Returns (bytes, extension)."""
    import aiohttp
    url = normalize_url(url)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (Rush Cricket bot)"}) as session:
        for _ in range(MAX_REDIRECTS + 1):
            await _check_host(url)
            try:
                async with session.get(url, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("Location")
                        if not loc:
                            raise AnimError("The link redirects nowhere.")
                        url = normalize_url(urljoin(url, loc))
                        continue
                    if resp.status != 200:
                        raise AnimError(f"The link answered with an error (HTTP {resp.status}).")
                    if (resp.content_length or 0) > MAX_BYTES:
                        raise AnimError(f"File is too big. Max {MAX_BYTES // (1024 * 1024)} MB.")
                    buf = bytearray()
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        buf.extend(chunk)
                        if len(buf) > MAX_BYTES:
                            raise AnimError(f"File is too big. Max {MAX_BYTES // (1024 * 1024)} MB.")
            except asyncio.TimeoutError:
                raise AnimError("The link took too long to download.")
            except aiohttp.ClientError:
                raise AnimError("Couldn't download that link.")
            data = bytes(buf)
            return data, await asyncio.to_thread(inspect_image, data)
    raise AnimError("Too many redirects.")


# ── Add / merge ──────────────────────────────────────────────────────────────

def clean_name(pathname: str) -> str:
    name = (pathname or "").strip()
    if not NAME_RE.match(name):
        raise AnimError("`pathname` can only have letters, numbers, `-` and `_` (max 32 characters), e.g. `golden_a`.")
    return name


def _write_file(pathname: str, data: bytes, ext: str) -> str:
    ANIM_DIR.mkdir(exist_ok=True)
    fname = f"{pathname}-{secrets.token_hex(4)}.{ext}"
    tmp = ANIM_DIR / (fname + ".part")
    tmp.write_bytes(data)
    tmp.replace(ANIM_DIR / fname)
    return fname


def _delete_file(fname: str | None) -> None:
    if not fname:
        return
    try:
        (ANIM_DIR / Path(fname).name).unlink(missing_ok=True)
    except OSError as e:
        print(f"[pool_anim] could not delete old animation {fname}: {e}")


async def add(pool: str, pathname: str, url: str, event_key: str = "") -> dict:
    """Save a NEW animation. Fails if the pathname already exists (use merge to replace)."""
    if pool not in POOLS:
        raise AnimError("Animations are only available for Pool A right now.")
    name = clean_name(pathname)
    if await asyncio.to_thread(get, name):
        raise AnimError(f"**{name}** already exists. Use `/pool animation_merge` to replace it.")
    data, ext = await fetch_image(url)
    fname = await asyncio.to_thread(_write_file, name, data, ext)
    try:
        def _insert():
            with economy._tx() as conn:
                _ensure(conn)
                conn.execute(
                    "INSERT INTO pool_animation (pathname, pool, file, source_url, updated, event_key) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, pool, fname, url.strip()[:500], time.time(), event_key),
                )
        await asyncio.to_thread(_insert)
    except Exception:
        _delete_file(fname)
        raise AnimError(f"Couldn't save **{name}**. Try again.")
    return {"pathname": name, "pool": pool, "file": ANIM_DIR / fname, "size": len(data), "event_key": event_key}


async def remove(pathname: str) -> dict:
    """Delete an animation (the DB row first, then the file)."""
    name = clean_name(pathname)
    old = await asyncio.to_thread(get, name)
    if old is None:
        raise AnimError(f"No animation named **{name}**.")

    def _drop():
        with economy._tx() as conn:
            _ensure(conn)
            conn.execute("DELETE FROM pool_animation WHERE pathname = ?", (name,))
    await asyncio.to_thread(_drop)
    await asyncio.to_thread(_delete_file, old["file"])
    return old


async def merge(pathname: str, url: str) -> dict:
    """Replace an existing animation completely: new file in, old file deleted."""
    name = clean_name(pathname)
    old = await asyncio.to_thread(get, name)
    if old is None:
        raise AnimError(f"No animation named **{name}**. Add it first with `/pool animation`.")
    data, ext = await fetch_image(url)
    fname = await asyncio.to_thread(_write_file, name, data, ext)
    try:
        def _swap():
            with economy._tx() as conn:
                _ensure(conn)
                cur = conn.execute(
                    "UPDATE pool_animation SET file = ?, source_url = ?, updated = ? WHERE pathname = ?",
                    (fname, url.strip()[:500], time.time(), name),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("animation vanished")
        await asyncio.to_thread(_swap)
    except Exception:
        _delete_file(fname)
        raise AnimError(f"Couldn't replace **{name}**. The old animation is still in place.")
    await asyncio.to_thread(_delete_file, old["file"])      # only after the new one is live
    return {"pathname": name, "pool": old["pool"], "file": ANIM_DIR / fname, "size": len(data),
            "event_key": old.get("event_key") or ""}
