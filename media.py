import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "cards.db")

BATTER_EVENTS = {"six", "four", "50", "100", "150", "200", "250", "300"}
BOWLER_EVENTS = {"wicket", "5wicket"}
UMPIRE_EVENTS = {"out", "notout", "wide", "six", "four", "noball"}


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_media_db():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS role_logos (
            pathname    TEXT PRIMARY KEY,
            url         TEXT NOT NULL,
            added_by    TEXT,
            added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_media (
            player_name TEXT NOT NULL,
            category    TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            gif_url     TEXT NOT NULL,
            PRIMARY KEY (player_name, event_type)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stadium_media (
            venue_name  TEXT PRIMARY KEY,
            gif_url     TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS milestone_media (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            universal   INTEGER NOT NULL DEFAULT 0,
            player_name TEXT,
            milestone   TEXT NOT NULL,
            gif_url     TEXT NOT NULL,
            UNIQUE(universal, player_name, milestone)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS umpire_media (
            event_type  TEXT PRIMARY KEY,
            gif_url     TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attribute_emojis (
            attribute   TEXT PRIMARY KEY,
            emoji_id    TEXT NOT NULL,
            pathname    TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# ── Umpire GIF functions (global — fire for every player) ──────────────────────

def set_umpire_gif(event_type: str, gif_url: str):
    conn = _conn()
    conn.execute("""
        INSERT INTO umpire_media (event_type, gif_url) VALUES (?, ?)
        ON CONFLICT(event_type) DO UPDATE SET gif_url=excluded.gif_url
    """, (event_type, gif_url))
    conn.commit()
    conn.close()


def get_umpire_gif(event_type: str) -> str | None:
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT gif_url FROM umpire_media WHERE event_type=?", (event_type,))
    row = c.fetchone()
    conn.close()
    return row["gif_url"] if row else None


def list_umpire_gifs() -> list[dict]:
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT * FROM umpire_media ORDER BY event_type")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def delete_umpire_gif(event_type: str) -> bool:
    conn = _conn()
    c = conn.cursor()
    c.execute("DELETE FROM umpire_media WHERE event_type=?", (event_type,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ── Milestone GIF functions ───────────────────────────────────────────────────

def set_milestone_gif(universal: bool, player_name: str | None, milestone: str, gif_url: str):
    conn = _conn()
    conn.execute("""
        INSERT INTO milestone_media (universal, player_name, milestone, gif_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(universal, player_name, milestone)
        DO UPDATE SET gif_url=excluded.gif_url
    """, (1 if universal else 0, player_name if not universal else None, milestone, gif_url))
    conn.commit()
    conn.close()


def get_milestone_gif(player_name: str, milestone: str) -> str | None:
    """
    Priority: individual player GIF → universal GIF → None.
    """
    conn = _conn()
    c = conn.cursor()
    # 1. Individual
    c.execute(
        "SELECT gif_url FROM milestone_media WHERE universal=0 AND LOWER(player_name)=LOWER(?) AND milestone=?",
        (player_name, milestone),
    )
    row = c.fetchone()
    if row:
        conn.close()
        return row["gif_url"]
    # 2. Universal
    c.execute(
        "SELECT gif_url FROM milestone_media WHERE universal=1 AND milestone=?",
        (milestone,),
    )
    row = c.fetchone()
    conn.close()
    return row["gif_url"] if row else None


def list_milestone_gifs() -> list[dict]:
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT * FROM milestone_media ORDER BY milestone, universal DESC, player_name")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def delete_milestone_gif(universal: bool, player_name: str | None, milestone: str) -> bool:
    conn = _conn()
    c = conn.cursor()
    if universal:
        c.execute(
            "DELETE FROM milestone_media WHERE universal=1 AND milestone=?",
            (milestone,),
        )
    else:
        c.execute(
            "DELETE FROM milestone_media WHERE universal=0 AND LOWER(player_name)=LOWER(?) AND milestone=?",
            (player_name, milestone),
        )
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ── Stadium GIF functions ─────────────────────────────────────────────────────

def set_stadium_gif(venue_name: str, gif_url: str):
    conn = _conn()
    conn.execute("""
        INSERT INTO stadium_media (venue_name, gif_url) VALUES (?, ?)
        ON CONFLICT(venue_name) DO UPDATE SET gif_url=excluded.gif_url
    """, (venue_name, gif_url))
    conn.commit()
    conn.close()


def get_stadium_gif(venue_name: str) -> str | None:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "SELECT gif_url FROM stadium_media WHERE LOWER(venue_name)=LOWER(?)",
        (venue_name,),
    )
    row = c.fetchone()
    conn.close()
    return row["gif_url"] if row else None


def delete_stadium_gif(venue_name: str) -> bool:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "DELETE FROM stadium_media WHERE LOWER(venue_name)=LOWER(?)",
        (venue_name,),
    )
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def list_stadium_gifs() -> list[dict]:
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT * FROM stadium_media ORDER BY venue_name")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def set_player_media(player_name: str, category: str, event_type: str, gif_url: str):
    conn = _conn()
    conn.execute("""
        INSERT INTO player_media (player_name, category, event_type, gif_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(player_name, event_type) DO UPDATE SET
            category=excluded.category, gif_url=excluded.gif_url
    """, (player_name, category, event_type, gif_url))
    conn.commit()
    conn.close()


def get_player_media(player_name: str, event_type: str) -> str | None:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "SELECT gif_url FROM player_media WHERE LOWER(player_name)=LOWER(?) AND event_type=?",
        (player_name, event_type),
    )
    row = c.fetchone()
    conn.close()
    return row["gif_url"] if row else None


def list_player_media(player_name: str) -> list[dict]:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM player_media WHERE LOWER(player_name)=LOWER(?) ORDER BY event_type",
        (player_name,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def delete_player_media(player_name: str, event_type: str) -> bool:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "DELETE FROM player_media WHERE LOWER(player_name)=LOWER(?) AND event_type=?",
        (player_name, event_type),
    )
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ── Role logo functions ───────────────────────────────────────────────────────

ROLE_LOGO_PATHNAMES = [
    "Batsman",
    "Fast Bowler",
    "Leg Spinner",
    "Off Spinner",
    "All Rounder",
    "Wicket Keeper",
]


def set_role_logo(pathname: str, url: str, added_by: str = None) -> bool:
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO role_logos (pathname, url, added_by) VALUES (?, ?, ?)
               ON CONFLICT(pathname) DO UPDATE SET url=excluded.url, added_by=excluded.added_by""",
            (pathname, url, added_by),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_role_logo(pathname: str) -> str | None:
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT url FROM role_logos WHERE pathname=?", (pathname,))
    row = c.fetchone()
    conn.close()
    return row["url"] if row else None


def list_role_logos() -> list[dict]:
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT pathname, url, added_at FROM role_logos ORDER BY pathname")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def delete_role_logo(pathname: str) -> bool:
    conn = _conn()
    c = conn.cursor()
    c.execute("DELETE FROM role_logos WHERE pathname=?", (pathname,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ── Attribute Emoji functions ─────────────────────────────────────────────────

def set_attribute_emoji(attribute: str, emoji_id: str, pathname: str):
    conn = _conn()
    conn.execute("""
        INSERT INTO attribute_emojis (attribute, emoji_id, pathname) VALUES (?, ?, ?)
        ON CONFLICT(attribute) DO UPDATE SET emoji_id=excluded.emoji_id, pathname=excluded.pathname
    """, (attribute, emoji_id, pathname))
    conn.commit()
    conn.close()


def get_attribute_emoji(attribute: str) -> dict | None:
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT * FROM attribute_emojis WHERE LOWER(attribute)=LOWER(?)", (attribute,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_attribute_emojis() -> dict[str, dict]:
    """Returns {attribute_name: {emoji_id, pathname}} for all stored emojis."""
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT * FROM attribute_emojis ORDER BY attribute")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return {r["attribute"]: r for r in rows}


def delete_attribute_emoji(attribute: str) -> bool:
    conn = _conn()
    c = conn.cursor()
    c.execute("DELETE FROM attribute_emojis WHERE LOWER(attribute)=LOWER(?)", (attribute,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0
