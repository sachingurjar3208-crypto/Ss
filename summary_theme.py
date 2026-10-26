"""Match SUMMARY themes (Halloween / Diwali / Christmas ...): the owner sets the image, players own the theme.

  * The OWNER sets the background image of a theme with /cssbset (the text design - colours, outline,
    glow - is fixed in summary_image.STYLES, only the image comes from the owner).
  * A player can use a theme only after it was GIVEN to them: by the owner with /cssbgive, or as a
    Star Pass reward (kind "theme").
  * A player turns an owned theme on / off with `cssb use <theme>` / `cssb off`.
  * At the end of a match the summary card uses the theme of the WINNER (if they have one turned on),
    otherwise the theme of the other player, otherwise the normal blue summary.

Tables (created automatically, nothing to run by hand):
    summary_theme_img    pathname -> image (JPEG bytes, 1536x1024)
    summary_theme_owned  (user_id, pathname) the themes a player owns
    summary_theme_equip  user_id -> the theme that player has turned on
"""
from __future__ import annotations

import time

import economy
from summary_image import STYLES

NAMES = tuple(STYLES)                                   # ("halloween", "diwali", "christmas")
LABELS = {k: v["label"] for k, v in STYLES.items()}
MAX_IMG_BYTES = 8 * 1024 * 1024


def norm(pathname) -> str | None:
    """'Halloween' / ' halloween ' -> 'halloween'. Unknown names -> None."""
    key = str(pathname or "").strip().lower()
    return key if key in STYLES else None


def label(pathname) -> str:
    return LABELS.get(str(pathname or "").lower(), str(pathname or "").title())


def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS summary_theme_img ("
        "pathname TEXT PRIMARY KEY, data BLOB NOT NULL, ts REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS summary_theme_owned ("
        "user_id INTEGER NOT NULL, pathname TEXT NOT NULL, ts REAL NOT NULL, "
        "source TEXT NOT NULL DEFAULT '', PRIMARY KEY (user_id, pathname))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS summary_theme_equip ("
        "user_id INTEGER PRIMARY KEY, pathname TEXT NOT NULL)"
    )


def ensure_tables() -> None:
    conn = economy._conn()
    try:
        _ensure(conn)
    finally:
        conn.close()


# ── Owner: the image of a theme ──────────────────────────────────────────────

def set_image(pathname: str, data: bytes) -> bool:
    key = norm(pathname)
    if key is None:
        return False
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute("INSERT OR REPLACE INTO summary_theme_img (pathname, data, ts) VALUES (?, ?, ?)",
                     (key, data, time.time()))
    return True


def remove_image(pathname: str) -> bool:
    """Delete the image of a theme. Players keep owning it; it just shows the normal summary until
    the owner sets an image again. Returns False when there was no image."""
    key = norm(pathname)
    if key is None:
        return False
    with economy._tx() as conn:
        _ensure(conn)
        return conn.execute("DELETE FROM summary_theme_img WHERE pathname = ?", (key,)).rowcount > 0


def has_image(pathname: str) -> bool:
    key = norm(pathname)
    if key is None:
        return False
    with economy._tx() as conn:
        _ensure(conn)
        return conn.execute("SELECT 1 FROM summary_theme_img WHERE pathname = ?", (key,)).fetchone() is not None


def get_image(pathname: str) -> bytes | None:
    key = norm(pathname)
    if key is None:
        return None
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute("SELECT data FROM summary_theme_img WHERE pathname = ?", (key,)).fetchone()
    return bytes(row["data"]) if row else None


def image_names() -> list[str]:
    """Themes that currently have an image."""
    with economy._tx() as conn:
        _ensure(conn)
        have = {r["pathname"] for r in conn.execute("SELECT pathname FROM summary_theme_img")}
    return [n for n in NAMES if n in have]


# ── Owning themes ────────────────────────────────────────────────────────────

def grant_conn(conn, user_id, pathname: str, source: str = "") -> bool:
    """Give a theme inside an open transaction (used by the Star Pass collect).
    True = new, False = the player already owned it."""
    key = norm(pathname)
    if key is None:
        return False
    _ensure(conn)
    cur = conn.execute(
        "INSERT OR IGNORE INTO summary_theme_owned (user_id, pathname, ts, source) VALUES (?, ?, ?, ?)",
        (int(user_id), key, time.time(), source[:40]),
    )
    return cur.rowcount == 1


def give(user_id, pathname: str, source: str = "owner") -> bool | None:
    """Owner gives a theme. True = given, False = already owned, None = unknown theme."""
    if norm(pathname) is None:
        return None
    with economy._tx() as conn:
        return grant_conn(conn, user_id, pathname, source)


def take(user_id, pathname: str) -> bool | None:
    """Owner takes a theme from one player (also turns it off for them). None = unknown theme."""
    key = norm(pathname)
    if key is None:
        return None
    uid = int(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        n = conn.execute("DELETE FROM summary_theme_owned WHERE user_id = ? AND pathname = ?", (uid, key)).rowcount
        conn.execute("DELETE FROM summary_theme_equip WHERE user_id = ? AND pathname = ?", (uid, key))
        return n > 0


def owned(user_id) -> list[str]:
    with economy._tx() as conn:
        _ensure(conn)
        have = {r["pathname"] for r in conn.execute(
            "SELECT pathname FROM summary_theme_owned WHERE user_id = ?", (int(user_id),))}
    return [n for n in NAMES if n in have]


def equipped(user_id) -> str | None:
    with economy._tx() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT e.pathname FROM summary_theme_equip e JOIN summary_theme_owned o "
            "ON o.user_id = e.user_id AND o.pathname = e.pathname WHERE e.user_id = ?", (int(user_id),)
        ).fetchone()
    return row["pathname"] if row else None


def equip(user_id, pathname: str) -> str:
    """Turn an OWNED theme on. Returns "ok", "unknown" or "not_owned"."""
    key = norm(pathname)
    if key is None:
        return "unknown"
    uid = int(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        if conn.execute("SELECT 1 FROM summary_theme_owned WHERE user_id = ? AND pathname = ?",
                        (uid, key)).fetchone() is None:
            return "not_owned"
        conn.execute("INSERT INTO summary_theme_equip (user_id, pathname) VALUES (?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET pathname = excluded.pathname", (uid, key))
    return "ok"


def unequip(user_id) -> None:
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute("DELETE FROM summary_theme_equip WHERE user_id = ?", (int(user_id),))


# ── Used by the match summary ────────────────────────────────────────────────

def pick_for_match(user_ids) -> tuple[str, bytes] | None:
    """user_ids in priority order (winner first). Returns (pathname, image) of the first player who has
    an owned + turned-on theme whose image exists, or None for the normal summary."""
    for uid in user_ids:
        if uid is None:
            continue
        try:
            key = equipped(uid)
            data = get_image(key) if key else None
        except Exception as e:                           # never break the match end
            print(f"[summary-theme] lookup failed for {uid}: {type(e).__name__}: {e}")
            continue
        if key and data:
            return key, data
    return None
