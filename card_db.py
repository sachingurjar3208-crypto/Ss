"""Storage layer for the /cardmaker system.

Three tables:
- cards            one row per player card (unique by playername, case-insensitive)
- backgrounds      templates added via /bgadder, referenced by pathname
- playstyle_logos  logos added via /logoadderofplaystyle, referenced by pathname
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

import emojis

DB_PATH = Path(__file__).parent / "cards_maker.db"
BACKGROUNDS_DIR = Path(__file__).parent / "backgrounds"
GENERATED_DIR = Path(__file__).parent / "generated"
LOGOS_DIR = Path(__file__).parent / "playstyle_logos"
PANELS_DIR = Path(__file__).parent / "panels"
COUNTRY_LOGOS_DIR = Path(__file__).parent / "country_logos"
# Every foreground cutout is saved here the first time its link is used, so
# cards keep working after Discord CDN links expire.
FOREGROUNDS_DIR = Path(__file__).parent / "foregrounds"

BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
LOGOS_DIR.mkdir(parents=True, exist_ok=True)
PANELS_DIR.mkdir(parents=True, exist_ok=True)
COUNTRY_LOGOS_DIR.mkdir(parents=True, exist_ok=True)
FOREGROUNDS_DIR.mkdir(parents=True, exist_ok=True)

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
    # ── Style 2 card design (set with /cardstats) ────────────────────────
    ("card_style",  "TEXT"),    # NULL or "classic" = old look, "style2" = new look
    ("extra_stats", "TEXT"),    # JSON, e.g. {"pwr": 94, "run": 99, "clu": 97}
    ("s2_bg",      "TEXT"),    # Style 2 only: backgrounds.pathname (NULL = built-in purple)
    ("s2_icon",    "TEXT"),    # Style 2 only: "gold" / "bronze" = medal icon in the left column, NULL = none
    ("s2_transparent", "INTEGER"),  # (old, no longer used - corners are always see-through now)
    ("s2_name_color", "TEXT"),      # Style 2 only: display-name colour (red/blue/yellow/pink/green/orange), NULL = default cream
    ("s2_line_color", "TEXT"),      # Style 2 only: colour of the line under the name (black/white/blue), NULL = default cream
    ("s2_fade_legs", "INTEGER"),    # Style 2 only: 1 = player image fades out above the name (legs/shorts hidden), NULL/0 = full image
    ("s2_theme",  "TEXT"),         # Style 2 only: NULL = normal purple card, "halloween" = Halloween frame + orange/bone text
    # ── Events (set with /cardmaker and /editcard, events made with /eventmaker) ──
    ("event_key",  "TEXT"),                              # NULL = normal card, else events.event_key
    ("obtainable", "INTEGER NOT NULL DEFAULT 1"),       # 1 = packs / claims / draft / shop, 0 = manual only (Star Pass, admin give)
    # Same real player, many cards: all versions share one player_group (e.g. "virat kohli").
    # NULL = the card is its own group (old cards keep working unchanged).
    ("player_group", "TEXT"),
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
        _ensure_events(conn)
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


def get_cards(keys: list[str]) -> dict[str, sqlite3.Row]:
    """Many cards in ONE query: {playername_key: row}. Missing keys are skipped."""
    keys = [k.strip().lower() for k in keys]
    out: dict[str, sqlite3.Row] = {}
    with _conn() as conn:
        for i in range(0, len(keys), 500):   # stay under SQLite's variable limit
            chunk = keys[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for row in conn.execute(f"SELECT * FROM cards WHERE playername_key IN ({marks})", chunk):
                out[row["playername_key"]] = row
    return out


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
    event_key: str | None = None,
    obtainable: bool = True,
    player_group: str | None = None,
) -> None:
    now = time.time()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO cards (
                playername_key, playername, displayname, foreground_link,
                background, ovr, bat, bowl, country, country_emoji,
                offset_x, offset_y, scale_pct, image_path, created_by, created_at, updated_at,
                role, batting_hand, playstyle1, playstyle2, bowling_type,
                event_key, obtainable, player_group
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 100, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playername.strip().lower(), playername.strip(), displayname.strip(),
                foreground_link.strip(), background.strip(), ovr, bat, bowl,
                country, country_emoji, created_by, now, now,
                role, batting_hand, playstyle1, playstyle2, bowling_type,
                (event_key.strip().lower() if event_key else None), 1 if obtainable else 0,
                (player_group.strip().lower() if player_group and player_group.strip() else None),
            ),
        )
        conn.commit()


# Columns that are NOT copied when a card is cloned with clone_card().
# extra_stats is left empty on purpose: the Style 2 stats are then worked out from the new ovr/bat/bowl.
_CLONE_SKIP = {
    "playername_key", "playername", "image_path",
    "created_by", "created_at", "updated_at", "extra_stats",
}


def clone_card(
    source: str,
    new_playername: str,
    *,
    ovr: int,
    bat: int,
    bowl: int,
    background: str,
    style: str,
    event_key: str,
    obtainable: bool,
    created_by: int,
    displayname: str | None = None,
    theme: str | None = None,
) -> str | None:
    """Make a new card from an existing one (used by /eventplayermaker).

    Copied from the source card: foreground link + position + size, country, role,
    batting hand, bowling type, playstyle logos and every layout setting.
    Set fresh: name, ovr / bat / bowl, background, design, event, obtainable.
    The new card joins the source's real-player group, so an XI can hold only one of them.
    Returns None when it worked, or "source" / "exists" / "style" when it did not."""
    if style not in CARD_STYLES:
        return "style"
    src = get_card(source)
    if src is None:
        return "source"
    if card_exists(new_playername):
        return "exists"
    now = time.time()
    with _conn() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(cards)")]
        values = {c: src[c] for c in cols if c not in _CLONE_SKIP}
        values.update({
            "playername_key": new_playername.strip().lower(),
            "playername": new_playername.strip(),
            "displayname": (displayname.strip() if displayname and displayname.strip() else src["displayname"]),
            "background": background.strip(),
            "ovr": ovr, "bat": bat, "bowl": bowl,
            "card_style": style,
            "event_key": event_key.strip().lower(),
            "obtainable": 1 if obtainable else 0,
            "player_group": src["player_group"] or src["playername_key"],
            "image_path": None, "extra_stats": None,
            "s2_theme": theme if theme in CARD_THEMES else None,   # never inherited from the source card
            **({"s2_bg": background.strip()} if theme in CARD_THEMES else {}),   # themed card: the picked background IS the frame
            "created_by": created_by, "created_at": now, "updated_at": now,
        })
        names = [n for n in values if n in cols]
        conn.execute(
            "INSERT INTO cards (" + ", ".join(names) + ") VALUES (" + ", ".join("?" for _ in names) + ")",
            [values[n] for n in names],
        )
        conn.commit()
    return None


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


# ── Events (event cards, event emoji, disabling an event) ────────────────
#
# An EVENT is just a name + an emoji. A card can belong to one event (cards.event_key).
#   - The event emoji is shown first in every XI row for that card (non-event cards
#     show emojis.DEFAULT_CARD_EMOJI instead).
#   - /eventdisable switches an event off: its cards can't be given to anybody any more
#     (packs, claims, draft, shop, trades, Star Pass, admin give - nothing). Cards players
#     already own stay with them. /eventenable switches it back on.
# Every card also has `obtainable`: 1 = can come out of packs / claims / draft / shop,
# 0 = never comes out of those; it can still be added by hand (Star Pass, admin give).

_EVENT_NAME_MAX = 40
_EVENT_CACHE: dict = {"t": 0.0, "data": {}}
_EVENT_TTL = 15.0
_EMOJI_RE = re.compile(r"^<(a?):([A-Za-z0-9_]{2,32}):(\d{15,25})>$")


def _ensure_events(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_key   TEXT PRIMARY KEY,          -- lowercase name
            eventname   TEXT NOT NULL,             -- name as typed
            emoji_id    TEXT NOT NULL,             -- Discord emoji id (digits)
            animated    INTEGER NOT NULL DEFAULT 0,
            disabled    INTEGER NOT NULL DEFAULT 0,
            created_by  INTEGER,
            created_at  REAL
        )
        """
    )


def _invalidate_events() -> None:
    _EVENT_CACHE["t"] = 0.0


def _events_map() -> dict:
    """{event_key: row-as-dict}, cached for a few seconds (the XI code asks for it a lot)."""
    now = time.time()
    if _EVENT_CACHE["t"] and now - _EVENT_CACHE["t"] < _EVENT_TTL:
        return _EVENT_CACHE["data"]
    data: dict = {}
    try:
        with _conn() as conn:
            _ensure_events(conn)
            for r in conn.execute("SELECT * FROM events"):
                data[r["event_key"]] = dict(r)
    except sqlite3.Error as e:
        print(f"[events] could not read events: {type(e).__name__}: {e}")
        return _EVENT_CACHE["data"]
    _EVENT_CACHE["t"] = now
    _EVENT_CACHE["data"] = data
    return data


def parse_emoji_input(text: str):
    """Accepts a bare emoji id (1562083735879295057) or a pasted emoji (<:name:id> / <a:name:id>).
    Returns (emoji_id, animated_or_None) or None if it isn't an emoji id."""
    t = (text or "").strip()
    m = _EMOJI_RE.match(t)
    if m:
        return m.group(3), m.group(1) == "a"
    if t.isdigit() and 15 <= len(t) <= 25:
        return t, None
    return None


def event_emoji_str(ev: dict) -> str:
    return f"<{'a' if ev.get('animated') else ''}:evt:{ev['emoji_id']}>"


def create_event(eventname: str, emoji_id: str, animated: bool, created_by: int) -> str:
    """Make an event. Returns "created", or "updated" if the name already existed
    (then only its emoji is changed)."""
    name = eventname.strip()[:_EVENT_NAME_MAX]
    key = name.lower()
    with _conn() as conn:
        _ensure_events(conn)
        exists = conn.execute("SELECT 1 FROM events WHERE event_key = ?", (key,)).fetchone()
        if exists:
            conn.execute(
                "UPDATE events SET emoji_id = ?, animated = ? WHERE event_key = ?",
                (emoji_id, 1 if animated else 0, key),
            )
            result = "updated"
        else:
            conn.execute(
                "INSERT INTO events (event_key, eventname, emoji_id, animated, disabled, created_by, created_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?)",
                (key, name, emoji_id, 1 if animated else 0, created_by, time.time()),
            )
            result = "created"
        conn.commit()
    _invalidate_events()
    return result


def get_event(eventname: str) -> dict | None:
    return _events_map().get((eventname or "").strip().lower())


def list_events() -> list[dict]:
    return sorted(_events_map().values(), key=lambda e: e["eventname"].lower())


def set_event_disabled(eventname: str, disabled: bool) -> bool:
    """False if there is no such event."""
    key = (eventname or "").strip().lower()
    with _conn() as conn:
        _ensure_events(conn)
        cur = conn.execute("UPDATE events SET disabled = ? WHERE event_key = ?", (1 if disabled else 0, key))
        conn.commit()
        ok = cur.rowcount > 0
    _invalidate_events()
    return ok


def rename_event(eventname: str, newname: str) -> tuple[str, dict | None]:
    """Rename an event. The event key is the lowercase name and cards point at it, so the
    events row AND every card of that event are moved to the new key in one transaction.
    Returns (status, event_row): "ok" | "missing" (no such event) | "empty" (blank new name)
    | "exists" (another event already has that name) | "same" (name is already exactly this)."""
    old_key = (eventname or "").strip().lower()
    name = (newname or "").strip()[:_EVENT_NAME_MAX].strip()
    if not name:
        return "empty", None
    new_key = name.lower()
    with _conn() as conn:
        _ensure_events(conn)
        row = conn.execute("SELECT * FROM events WHERE event_key = ?", (old_key,)).fetchone()
        if row is None:
            return "missing", None
        if row["eventname"] == name:
            return "same", dict(row)
        if new_key != old_key and conn.execute(
            "SELECT 1 FROM events WHERE event_key = ?", (new_key,)
        ).fetchone():
            return "exists", None
        conn.execute("UPDATE events SET event_key = ?, eventname = ? WHERE event_key = ?",
                     (new_key, name, old_key))
        if new_key != old_key:
            conn.execute("UPDATE cards SET event_key = ? WHERE event_key = ?", (new_key, old_key))
        conn.commit()
        updated = dict(conn.execute("SELECT * FROM events WHERE event_key = ?", (new_key,)).fetchone())
    _invalidate_events()
    return "ok", updated


def count_event_cards(event_key: str) -> int:
    with _conn() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM cards WHERE event_key = ?", ((event_key or "").strip().lower(),)
        ).fetchone()[0])


def _rget(row, key, default=None):
    """row[key] that doesn't blow up on an old row without the column."""
    try:
        v = row[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if v is None else v


def event_of(card) -> dict | None:
    k = _rget(card, "event_key")
    return _events_map().get(str(k).strip().lower()) if k else None


def card_emoji(card) -> str:
    """The emoji shown first in XI rows: the card's event emoji, or the default card emoji."""
    ev = event_of(card)
    return event_emoji_str(ev) if ev else emojis.DEFAULT_CARD_EMOJI


def is_event_disabled(card) -> bool:
    ev = event_of(card)
    return bool(ev and int(ev.get("disabled") or 0))


def is_obtainable(card) -> bool:
    """True if the card may come out of packs / claims / draft / shop / rewards."""
    return int(_rget(card, "obtainable", 1)) != 0 and not is_event_disabled(card)


def key_event_disabled(player_key: str) -> bool:
    """True if this card belongs to a disabled event (so nobody may be given it)."""
    row = get_card(player_key)
    return bool(row is not None and is_event_disabled(row))


def group_key(card) -> str:
    """The real-player identity of a card. Several cards can share it (Kohli RCB, Kohli India...).
    Cards with no player_group are their own group."""
    g = _rget(card, "player_group", None)
    return str(g).strip().lower() if g and str(g).strip() else str(card["playername_key"]).strip().lower()


def group_key_of(player_key: str) -> str:
    """Same as group_key() but from a card key. Unknown card -> the key itself."""
    row = get_card(player_key)
    return group_key(row) if row is not None else str(player_key).strip().lower()


def group_keys(keys: list[str]) -> dict[str, str]:
    """{card_key: group_key} for many cards in one query."""
    found = get_cards(list(keys))
    return {k: (group_key(found[k.strip().lower()]) if k.strip().lower() in found
                else k.strip().lower()) for k in keys}


def list_group_cards(group: str) -> list[sqlite3.Row]:
    """Every card of one real player, best OVR first."""
    g = group.strip().lower()
    return [c for c in list_all_cards() if group_key(c) == g]


def set_card_group(playername: str, group: str | None) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE cards SET player_group = ?, updated_at = ? WHERE playername_key = ?",
            ((group.strip().lower() if group and group.strip() else None), time.time(),
             playername.strip().lower()),
        )
        conn.commit()
        return cur.rowcount > 0


def list_obtainable_cards() -> list[sqlite3.Row]:
    """Cards that random sources (packs, claims, rewards, starter, draft pool C, shop) may use."""
    return [c for c in list_all_cards() if is_obtainable(c)]


def list_enabled_cards() -> list[sqlite3.Row]:
    """All cards except those of a disabled event (for pools the admin fills by hand)."""
    return [c for c in list_all_cards() if not is_event_disabled(c)]


def set_card_event(playername: str, event_key: str | None) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE cards SET event_key = ?, updated_at = ? WHERE playername_key = ?",
            ((event_key.strip().lower() if event_key else None), time.time(), playername.strip().lower()),
        )
        conn.commit()
        return cur.rowcount > 0


def set_card_obtainable(playername: str, obtainable: bool) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE cards SET obtainable = ?, updated_at = ? WHERE playername_key = ?",
            (1 if obtainable else 0, time.time(), playername.strip().lower()),
        )
        conn.commit()
        return cur.rowcount > 0


# ── Style 2 card design: style switch + extra stats ──────────────────────

CARD_STYLES = ("classic", "style2")
CARD_THEMES = ("halloween",)      # Style 2 themes (NULL in the database = normal card)


def set_card_style(playername: str, style: str) -> bool:
    """Switch a card between the old look ("classic") and the new one ("style2")."""
    if style not in CARD_STYLES or get_card(playername) is None:
        return False
    with _conn() as conn:
        conn.execute(
            "UPDATE cards SET card_style = ?, updated_at = ? WHERE playername_key = ?",
            (style, time.time(), playername.strip().lower()),
        )
        conn.commit()
    return True


def set_style2_options(playername: str, bg=_UNSET, icon=_UNSET, transparent=_UNSET, fade_legs=_UNSET,
                       name_color=_UNSET, line_color=_UNSET, theme=_UNSET) -> bool:
    """Style 2 extras. bg: backgrounds.pathname or None (= built-in purple).
    icon: "gold", "bronze" or None. fade_legs: True = the player cutout fades out above
    the name so legs / shorts do not show behind the name and stats, False/None = full image.
    (transparent is the old corner option and is ignored.) Arguments not given are left unchanged."""
    if get_card(playername) is None:
        return False
    sets, vals = [], []
    if theme is not _UNSET:
        sets.append("s2_theme = ?")
        vals.append(theme if theme in CARD_THEMES else None)    # None / "default" = normal card
    if name_color is not _UNSET:
        sets.append("s2_name_color = ?")
        vals.append(name_color)
    if line_color is not _UNSET:
        sets.append("s2_line_color = ?")
        vals.append(line_color)
    if fade_legs is not _UNSET:
        sets.append("s2_fade_legs = ?")
        vals.append(1 if fade_legs else 0)
    if bg is not _UNSET:
        sets.append("s2_bg = ?")
        vals.append(bg)
    if icon is not _UNSET:
        sets.append("s2_icon = ?")
        vals.append(icon)
    if not sets:
        return True
    sets.append("updated_at = ?")
    vals.append(time.time())
    vals.append(playername.strip().lower())
    with _conn() as conn:
        conn.execute(f"UPDATE cards SET {', '.join(sets)} WHERE playername_key = ?", vals)
        conn.commit()
    return True


def get_extra_stats(card_row) -> dict:
    """Extra stats of a card as a dict. Always safe: {} if missing or bad JSON."""
    try:
        raw = card_row["extra_stats"]
    except (IndexError, KeyError):
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def set_extra_stats(playername: str, stats: dict) -> bool:
    """Merge `stats` into the card's extra_stats JSON (other keys are kept)."""
    row = get_card(playername)
    if row is None:
        return False
    merged = get_extra_stats(row)
    for key, value in stats.items():
        if value is not None:
            merged[str(key)] = int(value)
    with _conn() as conn:
        conn.execute(
            "UPDATE cards SET extra_stats = ?, updated_at = ? WHERE playername_key = ?",
            (json.dumps(merged), time.time(), playername.strip().lower()),
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


def cards_using_background(pathname: str) -> list[str]:
    """Playernames of the cards currently set to this background."""
    key = pathname.strip().lower()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT playername FROM cards WHERE LOWER(background) = ?", (key,)
        ).fetchall()
        return [r["playername"] for r in rows]


def cards_on_background_any(pathname: str) -> list[str]:
    """Playernames of cards that use this background in EITHER design:
    Classic (`background`) or Style 2 (`s2_bg`)."""
    key = pathname.strip().lower()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT playername FROM cards WHERE LOWER(background) = ? OR LOWER(s2_bg) = ?",
            (key, key),
        ).fetchall()
        return [r["playername"] for r in rows]


def reassign_cards_background(old_pathname: str, new_pathname: str) -> int:
    """Point every card using old_pathname at new_pathname instead (Classic `background`
    and Style 2 `s2_bg`). Returns how many cards changed."""
    old_key = old_pathname.strip().lower()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE cards SET background = ?, updated_at = ? WHERE LOWER(background) = ?",
            (new_pathname.strip(), time.time(), old_key),
        )
        n = cur.rowcount
        cur2 = conn.execute(
            "UPDATE cards SET s2_bg = ?, updated_at = ? WHERE LOWER(s2_bg) = ?",
            (new_pathname.strip(), time.time(), old_key),
        )
        conn.commit()
        return max(n, cur2.rowcount)


def delete_background(pathname: str) -> sqlite3.Row | None:
    """Remove a background template from the library and return the deleted row
    (or None if it didn't exist) so the caller can also remove its local file."""
    row = get_background(pathname)
    if row is None:
        return None
    with _conn() as conn:
        conn.execute("DELETE FROM backgrounds WHERE pathname_key = ?", (pathname.strip().lower(),))
        conn.commit()
    return row


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


def cards_using_panel(pathname: str) -> list[str]:
    """Playernames of the cards currently set to this panel overlay."""
    key = pathname.strip().lower()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT playername FROM cards WHERE LOWER(panel_image) = ?", (key,)
        ).fetchall()
        return [r["playername"] for r in rows]


def reassign_cards_panel(old_pathname: str, new_pathname: str) -> int:
    """Point every card using old_pathname's panel at new_pathname instead.
    Returns how many rows changed. Position/size offsets are left as-is,
    same as reassign_cards_background, since the artwork underneath keeps
    the same shape (it's physically copied onto new_pathname's file)."""
    old_key = old_pathname.strip().lower()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE cards SET panel_image = ?, updated_at = ? WHERE LOWER(panel_image) = ?",
            (new_pathname.strip(), time.time(), old_key),
        )
        conn.commit()
        return cur.rowcount


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


def remove_playstyle_logo(pathname: str) -> sqlite3.Row | None:
    """Delete a playstyle logo from the logo list (used by /playstylelogoremover)
    and return the deleted row (or None if it didn't exist) so the caller can
    also remove its local file. Cards keep their playstyle NAME (the match
    effect stays); they just draw no logo until a logo with that name is added
    again with /logoadderofplaystyle."""
    row = get_playstyle_logo(pathname)
    if row is None:
        return None
    with _conn() as conn:
        conn.execute("DELETE FROM playstyle_logos WHERE pathname_key = ?", (pathname.strip().lower(),))
        conn.commit()
    return row


def cards_using_playstyle_logo(pathname: str) -> list[str]:
    """Playernames of the cards that currently carry this playstyle logo."""
    key = pathname.strip().lower()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT playername FROM cards "
            "WHERE LOWER(COALESCE(playstyle1, '')) = ? OR LOWER(COALESCE(playstyle2, '')) = ?",
            (key, key),
        ).fetchall()
        return [r["playername"] for r in rows]


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
