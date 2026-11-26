"""Daily limit for rcautoplay in RANKED matches (normal matches stay unlimited).
One use = one ranked match where the player switched autoplay on (switching it off and on
again in the same match costs nothing extra). The day resets at midnight India time."""
from __future__ import annotations

import time

import economy

RANKED_AUTOPLAY_PER_DAY = 10


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 5.5 * 3600))   # IST date


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS autoplay_daily (user_id INTEGER NOT NULL, day TEXT NOT NULL, "
                 "used INTEGER NOT NULL, PRIMARY KEY (user_id, day))")


def used_today(user_id: int) -> int:
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT used FROM autoplay_daily WHERE user_id = ? AND day = ?",
                         (int(user_id), _day())).fetchone()
    return int(r[0]) if r else 0


def try_use(user_id: int) -> tuple[bool, int]:
    """Take one ranked use. (ok, uses left after this)."""
    day = _day()
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT used FROM autoplay_daily WHERE user_id = ? AND day = ?",
                         (int(user_id), day)).fetchone()
        used = int(r[0]) if r else 0
        if used >= RANKED_AUTOPLAY_PER_DAY:
            return False, 0
        conn.execute("INSERT INTO autoplay_daily (user_id, day, used) VALUES (?, ?, 1) "
                     "ON CONFLICT(user_id, day) DO UPDATE SET used = used + 1", (int(user_id), day))
    return True, RANKED_AUTOPLAY_PER_DAY - used - 1
