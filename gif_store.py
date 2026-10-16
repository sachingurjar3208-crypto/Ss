"""Commentary GIFs — link-only storage (no files are ever saved).

One table, `commentary_gifs`:
    key         what triggers it (sweep, four, 50, wicket, ...)
    player_key  lowercase player name, or '' for a UNIVERSAL gif
    url         the GIF link (Giphy / Tenor / Imgur ...)

Lookup rule: the player's own gifs first; if he has none for that key, the
universal ones. A random one is picked each time.
"""
from __future__ import annotations

import os
import re
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "cards.db")

# ── Every key you can use ────────────────────────────────────────────────
SHOT_KEYS = ("drive", "loft", "flick", "defend", "sweep", "cut", "leave", "pull", "scoop")
RESULT_KEYS = ("four", "six", "dot", "out", "duck")           # batter
MILESTONE_KEYS = ("50", "100", "150", "200", "250", "300")    # batter
BOWLER_KEYS = ("wicket", "5wicket")                           # bowler

ALL_KEYS = SHOT_KEYS + RESULT_KEYS + MILESTONE_KEYS + BOWLER_KEYS

_ALIASES = {
    "lofted": "loft", "reverse-sweep": "scoop", "reversesweep": "scoop", "reverse sweep": "scoop",
    "half century": "50", "halfcentury": "50", "fifty": "50",
    "century": "100", "hundred": "100", "wickets": "wicket", "fifer": "5wicket",
    "4": "four", "6": "six", "sixer": "six", "boundary": "four",
}


def normalize_key(raw: str) -> str | None:
    """Clean what the owner typed. Returns the real key, or None if unknown."""
    k = (raw or "").strip().lstrip(":").strip().lower()
    k = _ALIASES.get(k, k)
    return k if k in ALL_KEYS else None


def keys_help() -> str:
    return (
        "**Shots:** " + ", ".join(SHOT_KEYS) + "\n"
        "**Batter results:** " + ", ".join(RESULT_KEYS) + "\n"
        "**Milestones:** " + ", ".join(MILESTONE_KEYS) + "\n"
        "**Bowler:** " + ", ".join(BOWLER_KEYS)
    )


def normalize_gif_url(url: str) -> str | None:
    """Only http(s) links. A Giphy *page* link is turned into the direct GIF link."""
    url = (url or "").strip()
    if not re.match(r"^https?://\S+$", url, re.I):
        return None
    m = re.match(r"^https?://(?:www\.)?giphy\.com/gifs/(?:[\w-]*-)?([A-Za-z0-9]+)/?(?:\?.*)?$", url)
    if m:
        return f"https://media.giphy.com/media/{m.group(1)}/giphy.gif"
    return url


# ── DB ───────────────────────────────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_gif_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS commentary_gifs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                key         TEXT NOT NULL,
                player_key  TEXT NOT NULL DEFAULT '',
                url         TEXT NOT NULL,
                UNIQUE(key, player_key, url)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_cgifs ON commentary_gifs(key, player_key)")


def add_gif(key: str, url: str, player: str | None = None) -> bool:
    """True if added, False if that exact link was already there."""
    pk = (player or "").strip().lower()
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO commentary_gifs (key, player_key, url) VALUES (?, ?, ?)",
            (key, pk, url),
        )
        return cur.rowcount > 0


def remove_gif(gif_id: int) -> sqlite3.Row | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM commentary_gifs WHERE id = ?", (gif_id,)).fetchone()
        if row:
            c.execute("DELETE FROM commentary_gifs WHERE id = ?", (gif_id,))
        return row


def list_gifs(key: str | None = None, player: str | None = None, universal_only: bool = False) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM commentary_gifs WHERE 1=1", []
    if key:
        sql += " AND key = ?"; args.append(key)
    if universal_only:
        sql += " AND player_key = ''"
    elif player:
        sql += " AND player_key = ?"; args.append(player.strip().lower())
    sql += " ORDER BY key, player_key, id"
    with _conn() as c:
        return c.execute(sql, args).fetchall()


def pick_gif(key: str, player_name: str | None) -> str | None:
    """Random gif: the player's own first, else a universal one, else None.
    Never raises — a GIF problem must not break a match."""
    try:
        with _conn() as c:
            if player_name:
                row = c.execute(
                    "SELECT url FROM commentary_gifs WHERE key = ? AND player_key = ? "
                    "ORDER BY RANDOM() LIMIT 1",
                    (key, player_name.strip().lower()),
                ).fetchone()
                if row:
                    return row["url"]
            row = c.execute(
                "SELECT url FROM commentary_gifs WHERE key = ? AND player_key = '' "
                "ORDER BY RANDOM() LIMIT 1",
                (key,),
            ).fetchone()
            return row["url"] if row else None
    except Exception as e:  # pragma: no cover
        print(f"[gif] lookup failed ({type(e).__name__}: {e})")
        return None


def collect_ball_gifs(
    *, outcome: str, shot_button: str, batter: str, bowler: str,
    runs_before: int, runs_after: int, bowler_wkts_before: int, bowler_wkts_after: int,
) -> list[str]:
    """Gifs for ONE ball (batter's side + bowler's wicket). At most one batter gif
    and one bowler gif, so the channel never gets spammed.

    Batter priority:  milestone > four/six > shot (only if runs scored) > dot
    On a wicket:      out (or duck if he was on 0) for the batter, wicket/5wicket for the bowler.
    """
    out: list[str] = []
    try:
        if outcome == "W":
            g = (pick_gif("duck", batter) if runs_before == 0 else None) or pick_gif("out", batter)
            if g:
                out.append(g)
            fifer = bowler_wkts_after >= 5 and bowler_wkts_before < 5
            g = (pick_gif("5wicket", bowler) if fifer else None) or pick_gif("wicket", bowler)
            if g:
                out.append(g)
            return out

        if outcome in ("0", "1", "2", "3", "4", "6"):
            g = None
            if outcome in ("1", "2", "3", "4", "6"):
                for m in (50, 100, 150, 200, 250, 300):
                    if runs_before < m <= runs_after:
                        g = pick_gif(str(m), batter)
                        break
                if not g and outcome in ("4", "6"):
                    g = pick_gif("four" if outcome == "4" else "six", batter)
                if not g:
                    sk = _ALIASES.get(shot_button.lower(), shot_button.lower())
                    if sk in SHOT_KEYS:
                        g = pick_gif(sk, batter)
            else:  # dot ball
                g = pick_gif("dot", batter)
            if g:
                out.append(g)
    except Exception as e:  # pragma: no cover
        print(f"[gif] collect failed ({type(e).__name__}: {e})")
    return out


try:
    init_gif_db()
except Exception as _e:  # pragma: no cover
    print(f"[gif] could not initialise gif table: {_e}")
