"""Pack animations: the GIF shown for a few seconds when someone runs `rcopen`,
right BEFORE their real card appears.

Managed with the owner/admin slash commands in pack_cog:
    /pack animation add     url:<link>  pathname:<name>    add (or replace) an animation
    /pack animation remove  pathname:<name>                delete an animation

pathname  = which pack the animation is for.
            `bronze`, `silver`, `gold`, `legendary`, `halloween`, `debut`
            Want several animations for the same pack? Add a suffix after `_` or `-`:
            `gold`, `gold_2`, `gold-fire` ... all belong to the gold pack and one is
            picked at random each time.

How it is stored (same idea as pool_anim.py)
- The link is downloaded ONCE, checked (must really be an ANIMATED GIF / WEBP, max 8 MB)
  and saved in the `pack_animations/` folder, so it keeps working even if the link dies.
- The name -> file mapping is in the `pack_animation` table of economy.db.
- Adding a name that already exists REPLACES it: the new file is put in place first and
  the old one is deleted only after that, so a failed download never loses the old animation.

If a pack has NO animation here, rcopen uses the built-in pack intro (pack_gif.py, no card in it).

Safety: the download code is pool_anim.fetch_image (only http(s), public addresses only,
max 4 redirects, size + time limits) and the file name is built from a strict pathname.
"""
from __future__ import annotations

import asyncio
import io
import re
import secrets
import time
from pathlib import Path

import economy
import pool_anim
import squad_logic as sl
from pool_anim import AnimError

_HERE = Path(__file__).parent
ANIM_DIR = _HERE / "pack_animations"

# How long rcopen shows an animation added with /pack animation add before the real card
# appears. Change this one number if your animations are shorter / longer.
ANIM_SECONDS = 5.0

_rng = secrets.SystemRandom()
_SUFFIX_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")


# ── Database ─────────────────────────────────────────────────────────────────

def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pack_animation ("
        "pathname TEXT PRIMARY KEY, pack TEXT NOT NULL, file TEXT NOT NULL, "
        "source_url TEXT, updated REAL NOT NULL)"
    )


def ensure_table() -> None:
    conn = economy._conn()
    try:
        _ensure(conn)
        conn.commit()
    finally:
        conn.close()


def get(pathname: str) -> dict | None:
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute("SELECT * FROM pack_animation WHERE pathname = ?", (pathname,)).fetchone()
        return dict(row) if row else None


def listing(prefix: str = "") -> list[tuple[str, str]]:
    """[(pathname, pack)] for the admin autocomplete."""
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT pathname, pack FROM pack_animation ORDER BY pathname").fetchall()
    p = prefix.lower()
    return [(r["pathname"], r["pack"]) for r in rows if p in r["pathname"].lower()]


def pick(pack: str) -> Path | None:
    """A random saved animation file for this pack (None if there is none)."""
    try:
        with economy._tx() as conn:
            _ensure(conn)
            rows = conn.execute("SELECT file FROM pack_animation WHERE pack = ?", (pack,)).fetchall()
    except Exception as e:
        print(f"[pack_anim] could not read animations: {e}")
        return None
    files = [f for f in (ANIM_DIR / r["file"] for r in rows) if f.is_file()]
    return _rng.choice(files) if files else None


# ── pathname -> pack ─────────────────────────────────────────────────────────

def pack_of(pathname: str) -> tuple[str, str]:
    """Check the pathname and return (clean_pathname, pack_key).

    `gold` -> pack gold.   `gold_2` / `gold-fire` -> also pack gold (several per pack)."""
    name = pool_anim.clean_name(pathname).lower()
    m = re.match(r"^([a-z0-9]+)(?:[_-](.+))?$", name)
    key, suffix = (m.group(1), m.group(2)) if m else (name, None)
    if key not in sl.PACKS:
        packs = ", ".join(f"`{k}`" for k in sl.PACKS)
        raise AnimError(f"`pathname` must be a pack name ({packs}), or a pack name + `_something` "
                        f"like `gold_2`. You typed `{name}`.")
    if suffix is not None and not _SUFFIX_RE.match(suffix.replace("_", "").replace("-", "")):
        raise AnimError("After the pack name use only letters / numbers, e.g. `gold_2` or `gold-fire`.")
    return name, key


# ── Check the downloaded file ────────────────────────────────────────────────

def _check_animated(data: bytes, ext: str) -> None:
    """Pack animations must really be animated GIF / WEBP (a still picture is not an animation)."""
    if ext not in ("gif", "webp"):
        raise AnimError("A pack animation must be a GIF (or animated WEBP), not a still "
                        f"{ext.upper()} picture.")
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        frames = int(getattr(img, "n_frames", 1))
    except Exception:
        raise AnimError("That link isn't a valid GIF file.")
    if frames < 2:
        raise AnimError("That file has only one frame, so it isn't an animation.")


# ── Files ────────────────────────────────────────────────────────────────────

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
        print(f"[pack_anim] could not delete old animation {fname}: {e}")


# ── Add / remove ─────────────────────────────────────────────────────────────

async def add(pathname: str, url: str) -> dict:
    """Save an animation. If the pathname already exists it is REPLACED (old file deleted last).
    Returns {"pathname", "pack", "file", "size", "replaced"}."""
    name, pack = pack_of(pathname)
    data, ext = await pool_anim.fetch_image(url)
    await asyncio.to_thread(_check_animated, data, ext)
    old = await asyncio.to_thread(get, name)
    fname = await asyncio.to_thread(_write_file, name, data, ext)
    try:
        def _save():
            with economy._tx() as conn:
                _ensure(conn)
                conn.execute(
                    "INSERT INTO pack_animation (pathname, pack, file, source_url, updated) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(pathname) DO UPDATE SET pack = excluded.pack, file = excluded.file, "
                    "source_url = excluded.source_url, updated = excluded.updated",
                    (name, pack, fname, url.strip()[:500], time.time()),
                )
        await asyncio.to_thread(_save)
    except Exception as e:
        print(f"[pack_anim] save failed: {type(e).__name__}: {e}")
        _delete_file(fname)
        raise AnimError(f"Couldn't save **{name}**. Try again." +
                        (" The old animation is still in place." if old else ""))
    if old:
        await asyncio.to_thread(_delete_file, old["file"])        # only after the new one is live
    return {"pathname": name, "pack": pack, "file": ANIM_DIR / fname, "size": len(data),
            "replaced": old is not None}


async def remove(pathname: str) -> dict:
    """Delete an animation (the DB row first, then the file)."""
    name, _pack = pack_of(pathname)
    old = await asyncio.to_thread(get, name)
    if old is None:
        raise AnimError(f"No animation named **{name}**.")

    def _drop():
        with economy._tx() as conn:
            _ensure(conn)
            conn.execute("DELETE FROM pack_animation WHERE pathname = ?", (name,))
    await asyncio.to_thread(_drop)
    await asyncio.to_thread(_delete_file, old["file"])
    return old
