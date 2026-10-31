"""Signature Box (pure logic + database, no Discord code).

How it works
- An admin opens a box with `/sigbox create`: one Signature card (the big prize), how many
  days it stays open, and an optional name. Only ONE box runs at a time. When its time is
  up it simply disappears (every function below treats an expired box as "no box").
- A box has 10 tiles: the Signature card + 9 other rewards (packs, vouchers, coins,
  player cards, summary themes). Every tile can be won only ONCE per user.
- Each user has 10 spins per box. The price of every spin is set per box
  (default: free, free, 30, 60, 120, 240, 480, 900, 1800, 3600 RC Points).
- Every spin has a chance to hit the Signature card (default 1% for spins 1-5,
  2% for spins 6-8, 10% for spin 9). If the Signature card is not hit, one of the
  remaining other tiles is picked at random. So by spin 10 only the Signature tile is
  left and it is GUARANTEED. After the Signature is won, the rest of the spins give
  the remaining tiles.
- Admins change rewards (`/sigbox reward`), prices (`/sigbox cost`), Signature chances
  per spin (`/sigbox chance`) and the chance of every reward slot (`/sigbox slotchance`)
  at any time. Players who already drew keep what they got.
- A draw is ONE database transaction: RC Points, the draw row and the reward are saved
  together or not at all. (box, user, spin) and (box, user, tile) are unique, so pressing
  the button many times can never pay twice or give a tile twice.

RC Points (RCP)
- Only admins add / remove them (`admin rcp give | remove | check`). Users see their
  balance in `rcbox` and with `rcp`.

Tables (created automatically):
    sigbox_box     one row per box (player, end time, prices, chances)
    sigbox_reward  the 9 other tiles of a box: (box, slot 1-9) -> reward
    sigbox_draw    what every user drew: (box, user, spin) -> slot (0 = Signature card)
    rcp_wallet     RC Points per user
    rcp_log        every RC Points change
"""
from __future__ import annotations

import json
import secrets
import time

import economy
from starpass import KIND_LABEL, PACK_KINDS, Reward

# ── Settings ─────────────────────────────────────────────────────────────────
SPINS = 10                                   # tiles = spins = 10 (Signature + 9 others)
OTHER_SLOTS = SPINS - 1
SIGNATURE_SLOT = 0
DEFAULT_COSTS = [0, 0, 30, 60, 120, 240, 480, 900, 1800, 3600]     # RCP for spin 1 .. 10
DEFAULT_CHANCES = [0.1] * 9                                        # % Signature on spin 1 .. 9 (10 = guaranteed)
DEFAULT_REWARDS = [                                                 # slot 1 .. 9
    ("coins", 5_000), ("bronze", 1), ("coins", 10_000), ("voucher", 1), ("silver", 1),
    ("coins", 25_000), ("voucher", 2), ("gold", 1), ("legendary", 1),
]
DEFAULT_WEIGHTS = [10] * 9                                          # chance of slot 1 .. 9 (relative, see draw())
DEFAULT_NAME = "Signature Box"
MAX_DAYS = 90
MAX_RCP = 1_000_000_000
KINDS = (*PACK_KINDS, "voucher", "coins", "player", "theme")
DAY = 86_400

_rng = secrets.SystemRandom()


# ── Database ─────────────────────────────────────────────────────────────────

def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sigbox_box ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, player_key TEXT NOT NULL, "
        "start_ts REAL NOT NULL, end_ts REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
        "costs TEXT NOT NULL, chances TEXT NOT NULL, created_by INTEGER)"
    )
    if "weights" not in {r[1] for r in conn.execute("PRAGMA table_info(sigbox_box)")}:
        conn.execute("ALTER TABLE sigbox_box ADD COLUMN weights TEXT")    # older boxes: every slot equal
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sigbox_reward ("
        "box_id INTEGER NOT NULL, slot INTEGER NOT NULL, kind TEXT NOT NULL, "
        "qty INTEGER NOT NULL DEFAULT 1, player_key TEXT, PRIMARY KEY (box_id, slot))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sigbox_draw ("
        "box_id INTEGER NOT NULL, user_id INTEGER NOT NULL, spin INTEGER NOT NULL, "
        "slot INTEGER NOT NULL, cost INTEGER NOT NULL DEFAULT 0, ts REAL NOT NULL, "
        "PRIMARY KEY (box_id, user_id, spin), UNIQUE (box_id, user_id, slot))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rcp_wallet ("
        "user_id INTEGER PRIMARY KEY, amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rcp_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, delta INTEGER NOT NULL, "
        "reason TEXT, balance_after INTEGER NOT NULL, ts REAL NOT NULL)"
    )


def ensure_tables() -> None:
    conn = economy._conn()
    try:
        _ensure(conn)
        conn.commit()
    finally:
        conn.close()


# ── RC Points ────────────────────────────────────────────────────────────────

def _rcp(conn, uid: int) -> int:
    row = conn.execute("SELECT amount FROM rcp_wallet WHERE user_id = ?", (uid,)).fetchone()
    return int(row["amount"]) if row else 0


def _rcp_set(conn, uid: int, new: int, delta: int, reason: str) -> int:
    conn.execute(
        "INSERT INTO rcp_wallet (user_id, amount) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET amount = excluded.amount", (uid, new))
    conn.execute(
        "INSERT INTO rcp_log (user_id, delta, reason, balance_after, ts) VALUES (?, ?, ?, ?, ?)",
        (uid, delta, reason, new, time.time()))
    return new


def rcp_balance(user_id) -> int:
    conn = economy._conn()
    try:
        _ensure(conn)
        return _rcp(conn, int(user_id))
    finally:
        conn.close()


def rcp_change(user_id, delta: int, reason: str) -> tuple[int, int]:
    """Admin add (+) / remove (-). Never below 0. Returns (old balance, new balance)."""
    uid = int(user_id)
    with economy._tx() as conn:
        _ensure(conn)
        old = _rcp(conn, uid)
        new = max(0, min(MAX_RCP, old + int(delta)))
        if new != old:
            _rcp_set(conn, uid, new, new - old, reason)
        return old, new


# ── Boxes ────────────────────────────────────────────────────────────────────

def _active(conn):
    _ensure(conn)
    return conn.execute(
        "SELECT * FROM sigbox_box WHERE active = 1 AND end_ts > ? ORDER BY id DESC LIMIT 1",
        (time.time(),)).fetchone()


def _rewards(conn, box_id: int) -> list[Reward]:
    got = {int(r["slot"]): Reward(r["kind"], int(r["qty"]), r["player_key"], True)
           for r in conn.execute("SELECT * FROM sigbox_reward WHERE box_id = ?", (box_id,))}
    out = []
    for slot in range(1, OTHER_SLOTS + 1):
        if slot in got:
            out.append(got[slot])
        else:
            kind, qty = DEFAULT_REWARDS[slot - 1]
            out.append(Reward(kind, qty))
    return out


def _box_dict(conn, row) -> dict:
    costs = json.loads(row["costs"])
    chances = json.loads(row["chances"])
    weights = json.loads(row["weights"]) if "weights" in row.keys() and row["weights"] else []
    return {
        "id": int(row["id"]), "name": row["name"], "player_key": row["player_key"],
        "start_ts": float(row["start_ts"]), "end_ts": float(row["end_ts"]),
        "costs": [int(c) for c in costs][:SPINS] + DEFAULT_COSTS[len(costs):],
        "chances": [float(c) for c in chances][:OTHER_SLOTS] + DEFAULT_CHANCES[len(chances):],
        "weights": [float(w) for w in weights][:OTHER_SLOTS] + DEFAULT_WEIGHTS[len(weights):],
        "rewards": _rewards(conn, int(row["id"])),
    }


def get_active() -> dict | None:
    with economy._tx() as conn:
        row = _active(conn)
        return _box_dict(conn, row) if row else None


def create_box(player_key: str, days: int, name: str | None = None, admin_id: int = 0) -> dict:
    """Open a new box (the running one, if any, ends). Fresh spins for everybody."""
    now = time.time()
    with economy._tx() as conn:
        _ensure(conn)
        conn.execute("UPDATE sigbox_box SET active = 0 WHERE active = 1")
        cur = conn.execute(
            "INSERT INTO sigbox_box (name, player_key, start_ts, end_ts, active, costs, chances, weights, created_by) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
            ((name or "").strip()[:40] or DEFAULT_NAME, player_key, now, now + int(days) * DAY,
             json.dumps(DEFAULT_COSTS), json.dumps(DEFAULT_CHANCES), json.dumps(DEFAULT_WEIGHTS), int(admin_id)))
        row = conn.execute("SELECT * FROM sigbox_box WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _box_dict(conn, row)


def end_box() -> dict | None:
    with economy._tx() as conn:
        row = _active(conn)
        if row is None:
            return None
        conn.execute("UPDATE sigbox_box SET active = 0 WHERE id = ?", (row["id"],))
        return _box_dict(conn, row)


def set_reward(slot: int, kind: str, qty: int = 1, player_key: str | None = None) -> dict | str:
    if kind not in KINDS:
        return "Invalid reward."
    if not 1 <= int(slot) <= OTHER_SLOTS:
        return f"Slot must be 1 to {OTHER_SLOTS}."
    qty = 1 if kind in ("player", "theme") else int(qty)
    if kind == "theme":
        import summary_theme
        player_key = summary_theme.norm(player_key)
        if player_key is None:
            return "Unknown summary theme."
    if qty < 1:
        return "The quantity must be at least 1."
    with economy._tx() as conn:
        row = _active(conn)
        if row is None:
            return "No Signature Box is open. Start one with /sigbox create first."
        conn.execute(
            "INSERT OR REPLACE INTO sigbox_reward (box_id, slot, kind, qty, player_key) VALUES (?, ?, ?, ?, ?)",
            (row["id"], int(slot), kind, qty, player_key))
        return _box_dict(conn, row)


def _set_list(column: str, index: int, value, length: int, default: list) -> dict | str:
    with economy._tx() as conn:
        row = _active(conn)
        if row is None:
            return "No Signature Box is open. Start one with /sigbox create first."
        vals = json.loads(row[column] or "[]")
        vals = (vals + default[len(vals):])[:length]
        vals[index] = value
        conn.execute(f"UPDATE sigbox_box SET {column} = ? WHERE id = ?", (json.dumps(vals), row["id"]))
        row = conn.execute("SELECT * FROM sigbox_box WHERE id = ?", (row["id"],)).fetchone()
        return _box_dict(conn, row)


def set_cost(spin: int, rcp: int) -> dict | str:
    if not 1 <= int(spin) <= SPINS:
        return f"Spin must be 1 to {SPINS}."
    if not 0 <= int(rcp) <= MAX_RCP:
        return "Invalid price."
    return _set_list("costs", int(spin) - 1, int(rcp), SPINS, DEFAULT_COSTS)


def set_chance(spin: int, percent: float) -> dict | str:
    if not 1 <= int(spin) <= OTHER_SLOTS:
        return f"Spin must be 1 to {OTHER_SLOTS} (spin {SPINS} is always the Signature card if not won yet)."
    if not 0 <= float(percent) <= 100:
        return "Chance must be 0 to 100."
    return _set_list("chances", int(spin) - 1, round(float(percent), 3), OTHER_SLOTS, DEFAULT_CHANCES)


def set_slot_chance(slot: int, chance: float) -> dict | str:
    """How likely reward slot 1-9 is to come up (relative: 20 is twice as likely as 10, 0 = only
    when nothing else is left, because every tile still drops once in 10 spins)."""
    if not 1 <= int(slot) <= OTHER_SLOTS:
        return f"Slot must be 1 to {OTHER_SLOTS}."
    if not 0 <= float(chance) <= 100:
        return "Chance must be 0 to 100."
    return _set_list("weights", int(slot) - 1, round(float(chance), 3), OTHER_SLOTS, DEFAULT_WEIGHTS)


def slot_percents(box: dict) -> list[float]:
    """The chance (%) of every slot on the FIRST spin that is not the Signature card."""
    w = box["weights"]
    total = sum(w)
    return [round(100 * x / total, 2) if total else round(100 / len(w), 2) for x in w]


def reset_user(user_id) -> dict | str:
    """Admin: wipe one user's spins in the open box, so they can open it again from spin 1
    (free spins too). Everything they already won stays with them."""
    uid = int(user_id)
    with economy._tx() as conn:
        row = _active(conn)
        if row is None:
            return "No Signature Box is open."
        n = conn.execute("DELETE FROM sigbox_draw WHERE box_id = ? AND user_id = ?", (row["id"], uid)).rowcount
        return {"box": _box_dict(conn, row), "removed": int(n)}


def player_keys(box: dict) -> list[str]:
    keys = [box["player_key"]]
    keys += [r.player_key for r in box["rewards"] if r.kind == "player" and r.player_key]
    return keys


# ── A user's board ───────────────────────────────────────────────────────────

def _drawn(conn, box_id: int, uid: int) -> dict[int, int]:
    """slot -> spin number it was won on."""
    return {int(r["slot"]): int(r["spin"]) for r in conn.execute(
        "SELECT slot, spin FROM sigbox_draw WHERE box_id = ? AND user_id = ?", (box_id, uid))}


def _board(conn, row, uid: int) -> dict:
    box = _box_dict(conn, row)
    drawn = _drawn(conn, box["id"], uid)
    spins = len(drawn)
    nxt = spins + 1
    done = spins >= SPINS
    sig_won = SIGNATURE_SLOT in drawn
    if done or sig_won:
        chance = None
    elif nxt >= SPINS:
        chance = 100.0
    else:
        chance = box["chances"][nxt - 1]
    return {
        "box": box, "drawn": drawn, "spins": spins, "done": done, "sig_won": sig_won,
        "next_spin": None if done else nxt,
        "next_cost": None if done else box["costs"][nxt - 1],
        "next_chance": chance,
        "balance": _rcp(conn, uid),
    }


def get_board(user_id) -> dict | None:
    with economy._tx() as conn:
        row = _active(conn)
        return _board(conn, row, int(user_id)) if row else None


# ── Drawing ──────────────────────────────────────────────────────────────────

def draw(user_id, player_values: dict[str, int] | None = None, expect_spin: int | None = None) -> dict | str:
    """One spin. Returns {"spin", "slot", "reward", "cost", "status", "refund", "board"} or an error.
    `expect_spin`: the spin number the button showed; if the user already drew it (double click
    / two open menus) nothing happens."""
    uid = int(user_id)
    values = player_values or {}
    with economy._tx() as conn:
        row = _active(conn)
        if row is None:
            return "The Signature Box is closed."
        if conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            return "You haven't debuted yet."
        b = _board(conn, row, uid)
        box = b["box"]
        if b["done"]:
            return "You have opened every tile of this box."
        n = b["next_spin"]
        if expect_spin is not None and int(expect_spin) != n:
            return "That spin was already done. Here is your box."
        for key in player_keys(box):
            if economy._event_disabled(key):
                return "This box is paused right now. Try again later."
        cost = int(box["costs"][n - 1])
        bal = b["balance"]
        if bal < cost:
            return f"You need **{cost:,} RCP** for this spin. You have **{bal:,} RCP**."

        drawn = b["drawn"]
        others = [s for s in range(1, OTHER_SLOTS + 1) if s not in drawn]
        if not b["sig_won"] and (not others or _rng.random() * 100 < float(b["next_chance"] or 0)):
            slot = SIGNATURE_SLOT
        else:
            slot = _pick(others, box["weights"])

        cur = conn.execute(
            "INSERT OR IGNORE INTO sigbox_draw (box_id, user_id, spin, slot, cost, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (box["id"], uid, n, slot, cost, time.time()))
        if cur.rowcount != 1:
            return "That spin was already done. Here is your box."
        if cost:
            _rcp_set(conn, uid, bal - cost, -cost, f"{box['name']} spin {n}")

        reward = Reward("player", 1, box["player_key"]) if slot == SIGNATURE_SLOT else box["rewards"][slot - 1]
        status, refund = _pay(conn, uid, reward, f"{box['name']} spin {n}", values)
        return {"spin": n, "slot": slot, "reward": reward, "cost": cost, "status": status,
                "refund": refund, "board": _board(conn, row, uid)}


def _pick(slots: list[int], weights: list[float]) -> int:
    """Weighted pick among the slots still closed. If all of them have chance 0, any of them."""
    ws = [max(0.0, float(weights[s - 1])) for s in slots]
    total = sum(ws)
    if total <= 0:
        return _rng.choice(slots)
    r = _rng.random() * total
    for s, w in zip(slots, ws):
        r -= w
        if r < 0:
            return s
    return slots[-1]


def _pay(conn, uid: int, rw: Reward, reason: str, values: dict) -> tuple[str, int]:
    """Give one reward inside the open transaction. Returns (status, coin refund)."""
    if rw.kind == "coins":
        economy._apply_coins(conn, uid, rw.qty, reason)
    elif rw.kind == "voucher":
        economy._voucher_change(conn, uid, rw.qty, reason)
    elif rw.kind in PACK_KINDS:
        conn.execute(
            "INSERT INTO packs (user_id, pack_type, qty) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, pack_type) DO UPDATE SET qty = qty + excluded.qty",
            (uid, rw.kind, rw.qty))
    elif rw.kind == "theme" and rw.player_key:
        import summary_theme
        return ("added" if summary_theme.grant_conn(conn, uid, rw.player_key, reason) else "dupe"), 0
    elif rw.kind == "player" and rw.player_key:
        status = economy._grant(conn, uid, rw.player_key, reason, allow_disabled=True)
        if status == "dupe":
            refund = int(values.get(rw.player_key, 0))
            if refund:
                economy._apply_coins(conn, uid, refund, f"Duplicate refund ({reason})")
            return "dupe", refund
        return status, 0
    return "added", 0


def reward_label(rw: Reward, card_names: dict[str, str] | None = None) -> str:
    if rw.kind == "player":
        return (card_names or {}).get(rw.player_key, rw.player_key or "Player Card")
    return rw.label()


__all__ = ["KIND_LABEL"]
