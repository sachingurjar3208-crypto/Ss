"""Market on / off (rcmarket off|on). While off, `rcbuy` still shows the cards but the buy
button is grey and does nothing, exactly like an unobtainable card. No "closed" message.
The setting is saved in the economy database, so it survives a restart."""
from __future__ import annotations

import economy

_open: bool | None = None          # cached; None = not loaded yet


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS market_switch (id INTEGER PRIMARY KEY CHECK (id = 1), open INTEGER NOT NULL)")


def is_open() -> bool:
    global _open
    if _open is None:
        try:
            with economy._tx() as conn:
                _ensure(conn)
                r = conn.execute("SELECT open FROM market_switch WHERE id = 1").fetchone()
            _open = bool(r[0]) if r else True
        except Exception as e:                    # a broken setting must never block the shop
            print(f"[market] could not read the switch: {type(e).__name__}: {e}")
            return True
    return _open


def set_open(value: bool) -> None:
    global _open
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute("INSERT OR REPLACE INTO market_switch (id, open) VALUES (1, ?)", (1 if value else 0,))
    _open = bool(value)
