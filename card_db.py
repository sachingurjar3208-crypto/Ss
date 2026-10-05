"""Storage layer for the /cardmaker system.

Three tables:
- cards            one row per player card (unique by playername, case-insensitive)
- backgrounds      templates added via /bgadder, referenced by pathname
- playstyle_logos  logos added via /logoadderofplaystyle, referenced by pathname
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "cards_maker.db"
BACKGROUNDS_DIR = Path(__file__).parent / "backgrounds"
GENERATED_DIR = Path(__file__).parent / "generated"
LOGOS_DIR = Path(__file__).parent / "playstyle_logos"
PANELS_DIR = Path(__file__).parent / "panels"
COUNTRY_LOGOS_DIR = Path(__file__).parent / "country_logos"

BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
LOGOS_DIR.mkdir(parents=True, exist_ok=True)
PANELS_DIR.mkdir(parents=True, exist_ok=True)
COUNTRY_LOGOS_DIR.mkdir(parents=True, exist_ok=True)

# Narratives of each card, cached briefly because the match engine asks once per ball.
_NARRATIVE_CACHE: dict[str, tuple[float, list[str]]] = {}
_NARRATIVE_TTL = 20.0

# Marker for "leave this slot as it is" (None means "clear this slot").
_UNSET = object()

# Columns added after the first version of the table. They are added to an
# existing cards_maker.db automatically on start-up, so old cards keep working.
_NEW_CARD_COLUMNS = [
    ("role",          "TEXT"),                              # BAT / BOWL / AR / WK
    ("batting_hand",  "TEXT"),                              # R / L
    ("playstyle1",    "TEXT"),                              # logo pathname, slot 1
    ("playstyle2",    "TEXT"),                              # logo pathname, slot 2
    ("logo1_dx",      "INTEGER NOT NULL DEFAULT 0"),
    ("logo1_dy",      "INTEGER NOT NULL DEFAULT 0"),
    ("logo1_scale",   "INTEGER NOT NULL DEFAULT 100"),
    ("logo2_dx",      "INTEGER NOT NULL DEFAULT 0"),
    ("logo2_dy",      "INTEGER NOT NULL DEFAULT 0"),
    ("logo2_scale",   "INTEGER NOT NULL DEFAULT 100"),
    ("bowling_type",  "TEXT"),                              # Fast / Off Spin / Leg Spin
    # ── Layout offsets, set via /cardlayout ──────────────────────────────
    ("name_dx",       "INTEGER NOT NULL DEFAULT 0"),
    ("name_dy",       "INTEGER NOT NULL DEFAULT 0"),
    ("name_size",     "INTEGER NOT NULL DEFAULT 120"),
    ("ovr_dx",        "INTEGER NOT NULL DEFAULT 0"),
    ("ovr_dy",        "INTEGER NOT NULL DEFAULT 0"),
    ("ovr_size",      "INTEGER NOT NULL DEFAULT 46"),
    ("role_dy",       "INTEGER NOT NULL DEFAULT 0"),
    ("role_dx",       "INTEGER NOT NULL DEFAULT 0"),
    ("role_size",     "INTEGER NOT NULL DEFAULT 26"),
    ("hand_dy",       "INTEGER NOT NULL DEFAULT 0"),
    ("hand_size",     "INTEGER NOT NULL DEFAULT 20"),
    ("hand_dx",       "INTEGER NOT NULL DEFAULT 0"),      # set via /playtypefixer
    ("stats_dx",      "INTEGER NOT NULL DEFAULT 0"),
    ("stats_dy",      "INTEGER NOT NULL DEFAULT 0"),
    ("stats_size",    "INTEGER NOT NULL DEFAULT 70"),
    ("panel_dy",      "INTEGER NOT NULL DEFAULT 0"),
    ("country_dy",    "INTEGER NOT NULL DEFAULT 0"),
    ("country_size",  "INTEGER NOT NULL DEFAULT 30"),
    # ── Optional custom stats-panel overlay image, added via /paneladder,
    # picked via /editcard's `panel` option, positioned via /panelfixer. ──
    ("panel_image",    "TEXT"),                              # panels.pathname, or NULL = none
    ("panelimg_dx",    "INTEGER NOT NULL DEFAULT 0"),
    ("panelimg_dy",    "INTEGER NOT NULL DEFAULT 0"),
    ("panelimg_scale", "INTEGER NOT NULL DEFAULT 100"),
    # ── Country logo (drawn just above the country name). The image itself is
    # per COUNTRY (/countrylogoadder); these are the per-CARD position/size
    # tweaks set with /countrylogofixer. ──
    ("countrylogo_dx",    "INTEGER NOT NULL DEFAULT 0"),
    ("countrylogo_dy",    "INTEGER NOT NULL DEFAULT 0"),
    ("countrylogo_scale", "INTEGER NOT NULL DEFAULT 100"),
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_card_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                playername_key   TEXT PRIMARY KEY,   -- lowercase, used for uniqueness
                playername       TEXT NOT NULL,       -- original casing, e.g. "Virat Kohli"
                displayname      TEXT NOT NULL,       -- shown big in the corner, e.g. "Kohli"
                foreground_link  TEXT NOT NULL,
                background       TEXT NOT NULL,       -- pathname referencing `backgrounds`
                ovr              INTEGER NOT NULL,
                bat              INTEGER NOT NULL,
                bowl             INTEGER NOT NULL,
                country          TEXT NOT NULL,
                country_emoji    TEXT NOT NULL,
                offset_x         INTEGER NOT NULL DEFAULT 0,
                offset_y         INTEGER NOT NULL DEFAULT 0,
                scale_pct        INTEGER NOT NULL DEFAULT 100,  -- 100 = normal size
                image_path       TEXT,
                created_by       INTEGER,
                created_at       REAL,
                updated_at       REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backgrounds (
                pathname_key  TEXT PRIMARY KEY,   -- lowercase
                pathname      TEXT NOT NULL,      -- original casing, shown in the picker
                link          TEXT NOT NULL,
                local_path    TEXT NOT NULL,
                added_by      INTEGER,
                added_at      REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS playstyle_logos (
                pathname_key  TEXT PRIMARY KEY,   -- lowercase
                pathname      TEXT NOT NULL,      -- original casing, shown in the picker
                link          TEXT NOT NULL,
                local_path    TEXT NOT NULL,
                added_by      INTEGER,
                added_at      REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS panels (
                pathname_key  TEXT PRIMARY KEY,   -- lowercase
                pathname      TEXT NOT NULL,      -- original casing, shown in the picker
                link          TEXT NOT NULL,
                local_path    TEXT NOT NULL,
                added_by      INTEGER,
                added_at      REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS country_logos (
                country_key   TEXT PRIMARY KEY,   -- lowercase country name
                country       TEXT NOT NULL,      -- canonical name, e.g. "India"
                link          TEXT NOT NULL,
                local_path    TEXT NOT NULL,
                added_by      INTEGER,
                added_at      REAL
            )
            """
        )
        # Migrate older databases: add any card columns that don't exist yet.
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(cards)")}
        for col, ddl in _NEW_CARD_COLUMNS:
            if col not in existing:
                conn.execute(f"ALTER TABLE cards ADD COLUMN {col} {ddl}")
        conn.commit()


# ── Cards ────────────────────────────────────────────────────────────────

def card_exists(playername: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM cards WHERE playername_key = ?",
            (playername.strip().lower(),),
        ).fetchone()
        return row is not None


def get_card(playername: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM cards WHERE playername_key = ?",
            (playername.strip().lower(),),
        ).fetchone()


def create_card(
    *,
    playername: str,
    displayname: str,
    foreground_link: str,
    background: str,
    ovr: int,
    bat: int,
    bowl: int,
    country: str,
    country_emoji: str,
    created_by: int,
    role: str | None = None,
    batting_hand: str | None = None,
    playstyle1: str | None = None,
    playstyle2: str | None = None,
    bowling_type: str | None = None,
) -> None:
    now = time.time()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO cards (
                playername_key, playername, displayname, foreground_link,
                background, ovr, bat, bowl, country, country_emoji,
                offset_x, offset_y, scale_pct, image_path, created_by, created_at, updated_at,
                role, batting_hand, playstyle1, playstyle2, bowling_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 100, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playername.strip().lower(), playername.strip(), displayname.strip(),
                foreground_link.strip(), background.strip(), ovr, bat, bowl,
                country, country_emoji, created_by, now, now,
                role, batting_hand, playstyle1, playstyle2, bowling_type,
            ),
        )
        conn.commit()


def update_card(
    playername: str,
    *,
    displayname: str | None = None,
    foreground_link: str | None = None,
    background: str | None = None,
    ovr: int | None = None,
    bat: int | None = None,
    bowl: int | None = None,
    country: str | None = None,
    country_emoji: str | None = None,
    role: str | None = None,
    batting_hand: str | None = None,
    bowling_type: str | None = None,
) -> bool:
    row = get_card(playername)
    if row is None:
        return False
    fields = {
        "displayname": displayname,
        "foreground_link": foreground_link,
        "background": background,
        "ovr": ovr,
        "bat": bat,
        "bowl": bowl,
        "country": country,
        "country_emoji": country_emoji,
        "role": role,
        "batting_hand": batting_hand,
        "bowling_type": bowling_type,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return True
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with _conn() as conn:
        conn.execute(
            f"UPDATE cards SET {set_clause} WHERE playername_key = ?",
            (*updates.values(), playername.strip().lower()),
        )
        conn.commit()
    return True


def update_card_offset(
    playername: str, offset_x: int, offset_y: int, scale_pct: int | None = None
) -> bool:
    if get_card(playername) is None:
        return False
    with _conn() as conn:
        if scale_pct is None:
            conn.execute(
                "UPDATE cards SET offset_x = ?, offset_y = ?, updated_at = ? "
                "WHERE playername_key = ?",
                (offset_x, offset_y, time.time(), playername.strip().lower()),
            )
        else:
            conn.execute(
                "UPDATE cards SET offset_x = ?, offset_y = ?, scale_pct = ?, updated_at = ? "
                "WHERE playername_key = ?",
                (offset_x, offset_y, scale_pct, time.time(), playername.strip().lower()),
            )
        conn.commit()
    return True


def effective_role(card) -> str:
    """BAT / BOWL / AR / WK for a card. Old cards with no role saved are
    guessed from their stats."""
    role = card["role"] if "role" in card.keys() else None
    if role in ("LBOWL", "RBOWL"):   # left/right arm bowler = a normal bowler for the game
        return "BOWL"
    if role in ("BAT", "BOWL", "AR", "WK"):
        return role
    bat, bowl = card["bat"], card["bowl"]
    if min(bat, bowl) >= 60:
        return "AR"
    return "BAT" if bat >= bowl else "BOWL"


def effective_bowling_type(card) -> str | None:
    """Bowling type the match engine should use, or None for non-bowlers."""
    bt = card["bowling_type"] if "bowling_type" in card.keys() else None
    if bt:
        return bt
    return "Fast" if effective_role(card) in ("BOWL", "AR") else None


def list_all_cards() -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute("SELECT * FROM cards ORDER BY ovr DESC, playername").fetchall()


def find_cards(query: str, limit: int = 10) -> list[sqlite3.Row]:
    """Cards whose name contains `query` (case-insensitive), best match first."""
    q = query.strip().lower()
    if not q:
        return []
    with _conn() as conn:
        exact = conn.execute("SELECT * FROM cards WHERE playername_key = ?", (q,)).fetchall()
        like = conn.execute(
            "SELECT * FROM cards WHERE playername_key LIKE ? ESCAPE '\\' AND playername_key != ? "
            "ORDER BY ovr DESC LIMIT ?",
            ("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", q, limit),
        ).fetchall()
    return list(exact) + list(like)


def set_playstyles(playername: str, playstyle1=_UNSET, playstyle2=_UNSET) -> bool:
    """Set the card's playstyle logo slots. Pass a logo pathname to fill a
    slot, None to clear it, or leave the argument out to keep it unchanged."""
    if get_card(playername) is None:
        return False
    updates: dict = {}
    if playstyle1 is not _UNSET:
        updates["playstyle1"] = playstyle1
    if playstyle2 is not _UNSET:
        updates["playstyle2"] = playstyle2
    if not updates:
        return True
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with _conn() as conn:
        conn.execute(
            f"UPDATE cards SET {set_clause} WHERE playername_key = ?",
            (*updates.values(), playername.strip().lower()),
        )
        conn.commit()
    _NARRATIVE_CACHE.clear()
    return True


def update_logo_layout(playername: str, slot: int, dx: int, dy: int, scale: int) -> bool:
    """Save the position offset and size (percent) of logo slot 1 or 2."""
    if slot not in (1, 2) or get_card(playername) is None:
        return False
    with _conn() as conn:
        conn.execute(
            f"UPDATE cards SET logo{slot}_dx = ?, logo{slot}_dy = ?, logo{slot}_scale = ?, "
            f"updated_at = ? WHERE playername_key = ?",
            (dx, dy, scale, time.time(), playername.strip().lower()),
        )
        conn.commit()
    return True


def update_panel_layout(playername: str, dx: int, dy: int, scale: int) -> bool:
    """Save the position offset and size (percent) of a card's panel overlay,
    set via /panelfixer."""
    if get_card(playername) is None:
        return False
    with _conn() as conn:
        conn.execute(
            "UPDATE cards SET panelimg_dx = ?, panelimg_dy = ?, panelimg_scale = ?, "
            "updated_at = ? WHERE playername_key = ?",
            (dx, dy, scale, time.time(), playername.strip().lower()),
        )
        conn.commit()
    return True


def set_card_panel(playername: str, panel_image: str | None) -> bool:
    """Set (or clear, with None) which panel overlay a card uses. Resets the
    position/size back to default whenever the panel image itself changes,
    so a new panel doesn't inherit a stale offset from a differently-shaped
    old one."""
    if get_card(playername) is None:
        return False
    with _conn() as conn:
        conn.execute(
            "UPDATE cards SET panel_image = ?, panelimg_dx = 0, panelimg_dy = 0, "
            "panelimg_scale = 100, updated_at = ? WHERE playername_key = ?",
            (panel_image, time.time(), playername.strip().lower()),
        )
        conn.commit()
    return True


def update_card_layout(playername: str, **kwargs) -> bool:
    """Save layout offsets/sizes for a card, set via /cardlayout. Pass only
    the keys you want to change — e.g. update_card_layout("Virat Kohli",
    name_dy=20, panel_dy=-50). Unknown keys are silently ignored."""
    if get_card(playername) is None:
        return False
    allowed = {
        "name_dx", "name_dy", "name_size",
        "ovr_dx", "ovr_dy", "ovr_size",
        "role_dx", "role_dy", "role_size",
        "hand_dx", "hand_dy", "hand_size",
        "stats_dx", "stats_dy", "stats_size",
        "panel_dy", "country_dy", "country_size",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return True
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with _conn() as conn:
        conn.execute(
            f"UPDATE cards SET {set_clause} WHERE playername_key = ?",
            (*updates.values(), playername.strip().lower()),
        )
        conn.commit()
    return True


def get_player_narratives(playername: str) -> list[str]:
    """Narratives (playstyles) on a player's card, as canonical narrative names.
    Used by the match engine; returns [] if the player has no card. Cached for
    a few seconds so a ball doesn't cost a database read."""
    from card_narratives import NARRATIVE_NAMES
    key = playername.strip().lower()
    now = time.time()
    hit = _NARRATIVE_CACHE.get(key)
    if hit and now - hit[0] < _NARRATIVE_TTL:
        return list(hit[1])
    canon = {n.lower(): n for n in NARRATIVE_NAMES}
    names: list[str] = []
    row = get_card(playername)
    if row is not None:
        for col in ("playstyle1", "playstyle2"):
            value = row[col] if col in row.keys() else None
            if value and value.strip().lower() in canon:
                names.append(canon[value.strip().lower()])
    _NARRATIVE_CACHE[key] = (now, names)
    return list(names)


def set_card_image_path(playername: str, image_path: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE cards SET image_path = ? WHERE playername_key = ?",
            (image_path, playername.strip().lower()),
        )
        conn.commit()


def list_playernames() -> list[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT playername FROM cards ORDER BY playername").fetchall()
        return [r["playername"] for r in rows]


def delete_card(playername: str) -> sqlite3.Row | None:
    """Delete a card and return the row that was deleted (or None if it
    didn't exist), so the caller can also clean up its generated image file.
    """
    row = get_card(playername)
    if row is None:
        return None
    with _conn() as conn:
        conn.execute(
            "DELETE FROM cards WHERE playername_key = ?",
            (playername.strip().lower(),),
        )
        conn.commit()
    _NARRATIVE_CACHE.clear()
    return row


# ── Backgrounds ──────────────────────────────────────────────────────────

def background_exists(pathname: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM backgrounds WHERE pathname_key = ?",
            (pathname.strip().lower(),),
        ).fetchone()
        return row is not None


def get_background(pathname: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM backgrounds WHERE pathname_key = ?",
            (pathname.strip().lower(),),
        ).fetchone()


def add_background(pathname: str, link: str, local_path: str, added_by: int) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO backgrounds (pathname_key, pathname, link, local_path, added_by, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pathname.strip().lower(), pathname.strip(), link.strip(), local_path, added_by, time.time()),
        )
        conn.commit()


def list_backgrounds() -> list[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT pathname FROM backgrounds ORDER BY pathname").fetchall()
        return [r["pathname"] for r in rows]


# ── Panels (stats-panel overlay images, added via /paneladder) ────────────

def panel_exists(pathname: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM panels WHERE pathname_key = ?",
            (pathname.strip().lower(),),
        ).fetchone()
        return row is not None


def get_panel(pathname: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM panels WHERE pathname_key = ?",
            (pathname.strip().lower(),),
        ).fetchone()


def add_panel(pathname: str, link: str, local_path: str, added_by: int) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO panels (pathname_key, pathname, link, local_path, added_by, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pathname.strip().lower(), pathname.strip(), link.strip(), local_path, added_by, time.time()),
        )
        conn.commit()


def list_panels() -> list[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT pathname FROM panels ORDER BY pathname").fetchall()
        return [r["pathname"] for r in rows]


def remove_panel(pathname: str) -> sqlite3.Row | None:
    """Delete a panel asset (used by /panelremover) and return the row that
    was deleted (or None if it didn't exist), so the caller can also clean
    up its local file. Any cards currently using this panel have it cleared
    automatically, so they fall back to having no panel overlay instead of
    pointing at a deleted file."""
    row = get_panel(pathname)
    if row is None:
        return None
    with _conn() as conn:
        conn.execute("DELETE FROM panels WHERE pathname_key = ?", (pathname.strip().lower(),))
        conn.execute(
            "UPDATE cards SET panel_image = NULL, panelimg_dx = 0, panelimg_dy = 0, "
            "panelimg_scale = 100 WHERE panel_image IS NOT NULL AND LOWER(panel_image) = ?",
            (pathname.strip().lower(),),
        )
        conn.commit()
    return row


# ── Playstyle logos ──────────────────────────────────────────────────────

def playstyle_logo_exists(pathname: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM playstyle_logos WHERE pathname_key = ?",
            (pathname.strip().lower(),),
        ).fetchone()
        return row is not None


def get_playstyle_logo(pathname: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM playstyle_logos WHERE pathname_key = ?",
            (pathname.strip().lower(),),
        ).fetchone()


def add_playstyle_logo(pathname: str, link: str, local_path: str, added_by: int) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO playstyle_logos (pathname_key, pathname, link, local_path, added_by, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pathname.strip().lower(), pathname.strip(), link.strip(), local_path, added_by, time.time()),
        )
        conn.commit()


def list_playstyle_logos() -> list[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT pathname FROM playstyle_logos ORDER BY pathname").fetchall()
        return [r["pathname"] for r in rows]


# ── Country logos (one image per country, shown above the country name) ───

def get_country_logo(country: str) -> sqlite3.Row | None:
    if not country:
        return None
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM country_logos WHERE country_key = ?",
            (country.strip().lower(),),
        ).fetchone()


def add_country_logo(country: str, link: str, local_path: str, added_by: int) -> bool:
    """Add a country's logo, or replace the existing one. Returns True if it replaced one."""
    key = country.strip().lower()
    with _conn() as conn:
        existed = conn.execute("SELECT 1 FROM country_logos WHERE country_key = ?", (key,)).fetchone() is not None
        conn.execute(
            """
            INSERT OR REPLACE INTO country_logos (country_key, country, link, local_path, added_by, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, country.strip(), link.strip(), local_path, added_by, time.time()),
        )
        conn.commit()
    return existed


def list_country_logos() -> list[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT country FROM country_logos ORDER BY country").fetchall()
        return [r["country"] for r in rows]


def update_country_logo_layout(playername: str, dx: int, dy: int, scale: int) -> bool:
    """Save the position offset and size (percent) of a card's country logo."""
    if get_card(playername) is None:
        return False
    with _conn() as conn:
        conn.execute(
            "UPDATE cards SET countrylogo_dx = ?, countrylogo_dy = ?, countrylogo_scale = ?, "
            "updated_at = ? WHERE playername_key = ?",
            (dx, dy, scale, time.time(), playername.strip().lower()),
        )
        conn.commit()
    return True
