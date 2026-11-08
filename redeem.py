"""Redeem codes (rcredeem CODE). Storage + giving the rewards, no Discord code here.

A code has a list of rewards, a total use limit (0 = unlimited), an optional expiry and an on/off
switch. Every player can redeem a code ONCE. Everything a redeem gives happens in ONE transaction,
so a code can never be used more times than its limit, even if many people type it together.

Reward kinds (stored as JSON):
  {"kind": "coins",    "amount": 50000}
  {"kind": "vouchers", "amount": 2}                 Draft Vouchers
  {"kind": "rcp",      "amount": 100}               RCP points
  {"kind": "pack",     "value": "gold", "amount": 1}
  {"kind": "card",     "value": "<playername_key>"} one exact card (duplicate / full squad -> rcpending)
  {"kind": "random",   "value": "85-89", "amount": 1}  random card(s) of that OVR range
  {"kind": "theme",    "value": "diwali"}           a match summary theme

Tables: redeem_codes (code PK, rewards JSON, max_uses, used, expires, active, created_by, created_ts)
        redeem_claims (code, user_id, ts)  PRIMARY KEY (code, user_id)
"""
from __future__ import annotations

import json
import re
import time

import economy

CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
KINDS = ("coins", "vouchers", "rcp", "pack", "card", "random", "theme")
MAX_AMOUNT = {"coins": 100_000_000, "vouchers": 1000, "rcp": 1_000_000, "pack": 20, "random": 5}


def norm_code(code: str) -> str:
    return str(code or "").strip().upper()


def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS redeem_codes (code TEXT PRIMARY KEY, rewards TEXT NOT NULL, "
        "max_uses INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0, expires REAL NOT NULL DEFAULT 0, "
        "active INTEGER NOT NULL DEFAULT 1, created_by INTEGER, created_ts REAL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS redeem_claims (code TEXT NOT NULL, user_id INTEGER NOT NULL, ts REAL, "
        "PRIMARY KEY (code, user_id))")


# ── checking a reward before it is saved ─────────────────────────────────────

def parse_range(text: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:-\s*(\d{1,2}))?\s*", str(text or ""))
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    lo, hi = min(lo, hi), max(lo, hi)
    return (lo, hi) if 1 <= lo <= hi <= 99 else None


def make_reward(kind: str, amount: int, value: str | None) -> tuple[dict | None, str]:
    """Build one reward dict. Returns (reward, "") or (None, error text)."""
    kind = str(kind).lower()
    if kind not in KINDS:
        return None, "Unknown reward."
    amount = int(amount or 1)
    if kind in MAX_AMOUNT and not 1 <= amount <= MAX_AMOUNT[kind]:
        return None, f"Amount for {kind} must be 1 to {MAX_AMOUNT[kind]:,}."
    if kind in ("coins", "vouchers", "rcp"):
        return {"kind": kind, "amount": amount}, ""
    if kind == "pack":
        import squad_logic as sl
        key = str(value or "").strip().lower()
        if key not in sl.PACKS or key == "debut":
            names = ", ".join(k for k in sl.PACKS if k != "debut")
            return None, f"Unknown pack. Use one of: {names} (e.g. `1 gold pack`)."
        return {"kind": "pack", "value": key, "amount": amount}, ""
    if kind == "card":
        import card_db
        card = card_db.get_card(str(value or ""))
        if card is None:
            return None, f"No card named `{value}`."
        return {"kind": "card", "value": card["playername_key"]}, ""
    if kind == "random":
        rng = parse_range(value or "")
        if rng is None:
            return None, "Write the OVR range like `random 85-89` or `random 90`."
        return {"kind": "random", "value": f"{rng[0]}-{rng[1]}", "amount": amount}, ""
    if kind == "theme":
        import summary_theme
        key = summary_theme.norm(value)
        if key is None:
            return None, f"Unknown theme. Use one of: {', '.join(summary_theme.NAMES)} (e.g. `diwali theme`)."
        return {"kind": "theme", "value": key}, ""
    return None, "Unknown reward."


def parse_reward_text(text: str) -> tuple[dict | None, str]:
    """One reward written as text (what the owner types in /redeem create):
         50000 coins | 2 vouchers | 100 rcp | 1 gold pack | gold pack | card Virat Kohli
         random 85-89 | 2 random 85-89 | diwali theme
    Returns (reward, "") or (None, error)."""
    t = " ".join(str(text or "").strip().split())
    low = t.lower()
    if not t:
        return None, "Empty reward."
    if low.startswith("card "):
        return make_reward("card", 1, t[5:].strip())
    m = re.fullmatch(r"(?:(\d[\d,]*)\s+)?random\s+(.+)", low)
    if m:
        return make_reward("random", int((m.group(1) or "1").replace(",", "")), m.group(2))
    m = re.fullmatch(r"(?:theme\s+(\w+)|(\w+)\s+(?:summary\s+)?theme)", low)
    if m:
        return make_reward("theme", 1, m.group(1) or m.group(2))
    m = re.fullmatch(r"(?:(\d[\d,]*)\s*x?\s+)?(\w+)\s+packs?", low)
    if m:
        return make_reward("pack", int((m.group(1) or "1").replace(",", "")), m.group(2))
    m = re.fullmatch(r"(\d[\d,]*)\s*(coins?|vouchers?|draft vouchers?|rcp)|(coins?|vouchers?|rcp)\s+(\d[\d,]*)", low)
    if m:
        num = int((m.group(1) or m.group(4)).replace(",", ""))
        word = (m.group(2) or m.group(3))
        kind = "coins" if word.startswith("coin") else ("rcp" if word == "rcp" else "vouchers")
        return make_reward(kind, num, None)
    return None, (f"Couldn't read `{t}`. Examples: `50000 coins`, `2 vouchers`, `100 rcp`, `1 gold pack`, "
                  "`card Virat Kohli`, `random 85-89`, `diwali theme`.")


def describe(r: dict) -> str:
    """Short text of one reward, for lists."""
    k, a, v = r["kind"], r.get("amount", 1), r.get("value")
    if k == "coins":
        return f"{a:,} coins"
    if k == "vouchers":
        return f"{a} Draft Voucher{'s' if a != 1 else ''}"
    if k == "rcp":
        return f"{a:,} RCP"
    if k == "pack":
        import squad_logic as sl
        return f"{a}x {sl.PACKS.get(v, {}).get('label', v)}"
    if k == "card":
        import card_db
        c = card_db.get_card(v)
        return f"card {c['playername'] if c else v}"
    if k == "random":
        return f"{a}x random card ({v} OVR)"
    if k == "theme":
        import summary_theme
        return f"{summary_theme.label(v)} summary theme"
    return k


# ── owner side ───────────────────────────────────────────────────────────────

def create(code: str, rewards: list[dict], max_uses: int, expires: float, by: int) -> str | None:
    """New code. Returns an error text, or None if it worked."""
    code = norm_code(code)
    if not CODE_RE.match(code):
        return "Code must be 3-32 letters/numbers (A-Z, 0-9, _ or -), no spaces."
    with economy._tx() as conn:
        _ensure(conn)
        if conn.execute("SELECT 1 FROM redeem_codes WHERE code = ?", (code,)).fetchone():
            return f"Code `{code}` already exists. Pick another name."
        conn.execute("INSERT INTO redeem_codes (code, rewards, max_uses, used, expires, active, created_by, created_ts) "
                     "VALUES (?, ?, ?, 0, ?, 1, ?, ?)",
                     (code, json.dumps(rewards), max(0, int(max_uses)), float(expires or 0), int(by), time.time()))
    return None


def add_reward(code: str, reward: dict) -> str | None:
    code = norm_code(code)
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT rewards FROM redeem_codes WHERE code = ?", (code,)).fetchone()
        if r is None:
            return f"No code `{code}`."
        rewards = json.loads(r["rewards"])
        if len(rewards) >= 10:
            return "A code can have at most 10 rewards."
        rewards.append(reward)
        conn.execute("UPDATE redeem_codes SET rewards = ? WHERE code = ?", (json.dumps(rewards), code))
    return None


def set_active(code: str, active: bool) -> bool:
    with economy._tx() as conn:
        _ensure(conn)
        cur = conn.execute("UPDATE redeem_codes SET active = ? WHERE code = ?", (1 if active else 0, norm_code(code)))
        return cur.rowcount == 1


def delete(code: str) -> bool:
    code = norm_code(code)
    with economy._tx() as conn:
        _ensure(conn)
        cur = conn.execute("DELETE FROM redeem_codes WHERE code = ?", (code,))
        conn.execute("DELETE FROM redeem_claims WHERE code = ?", (code,))
        return cur.rowcount == 1


def get(code: str) -> dict | None:
    with economy._tx() as conn:
        _ensure(conn)
        r = conn.execute("SELECT * FROM redeem_codes WHERE code = ?", (norm_code(code),)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["rewards"] = json.loads(d["rewards"])
    return d


def list_codes() -> list[dict]:
    with economy._tx() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT * FROM redeem_codes ORDER BY created_ts DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["rewards"] = json.loads(d["rewards"])
        out.append(d)
    return out


def status(d: dict) -> str:
    if not d["active"]:
        return "off"
    if d["expires"] and time.time() >= d["expires"]:
        return "expired"
    if d["max_uses"] and d["used"] >= d["max_uses"]:
        return "used up"
    return "live"


# ── player side ──────────────────────────────────────────────────────────────

def _give(conn, uid: int, r: dict, source: str, idx_cache: list) -> tuple[str, bool]:
    """Give one reward inside the open transaction. Returns (text, card_is_pending)."""
    import emojis
    k, a, v = r["kind"], int(r.get("amount", 1)), r.get("value")
    if k == "coins":
        economy._apply_coins(conn, uid, a, source)
        return f"💰 **{a:,}** coins", False
    if k == "vouchers":
        economy._voucher_change(conn, uid, a, source)
        return f"{emojis.VOUCHER} **{a}** Draft Voucher{'s' if a != 1 else ''}", False
    if k == "rcp":
        import sigbox
        sigbox._ensure(conn)
        old = sigbox._rcp(conn, uid)
        new = min(sigbox.MAX_RCP, old + a)
        if new != old:
            sigbox._rcp_set(conn, uid, new, new - old, source)
        return f"💎 **{a:,}** RCP", False
    if k == "pack":
        import squad_logic as sl
        conn.execute("INSERT INTO packs (user_id, pack_type, qty) VALUES (?, ?, ?) "
                     "ON CONFLICT(user_id, pack_type) DO UPDATE SET qty = qty + excluded.qty", (uid, v, a))
        return f"📦 **{a}x {sl.PACKS.get(v, {}).get('label', v)}** (open with `rcopen`)", False
    if k == "card":
        import card_db
        res = economy._grant(conn, uid, v, source)
        c = card_db.get_card(v)
        name = f"**{c['playername']}** ({c['ovr']} OVR)" if c else f"**{v}**"
        if res == "blocked":
            return f"🎴 {name} — not available right now", False
        if res == "pending":
            return f"🎴 {name} — waiting: keep, sell or replace with `rcpending`", True
        return f"🎴 {name}", False
    if k == "random":
        import ranked
        lo, hi = (int(x) for x in str(v).split("-"))
        if not idx_cache:
            idx_cache.append(ranked._cards_index())
        lines, pend = [], False
        for _ in range(a):
            res = ranked._grant_random_card(conn, uid, lo, hi, source, idx_cache[0])
            if res is None:
                continue
            c = idx_cache[0][1][res["key"]]
            t = f"🎴 **{c['playername']}** ({c['ovr']} OVR)"
            if res["status"] == "dupe":
                t += f" — you already had it, sold for **{res['refund']:,}** coins"
            elif res["status"] == "pending":
                t += " — waiting: keep, sell or replace with `rcpending`"
                pend = True
            lines.append(t)
        return "\n".join(lines) or "🎴 (no card in that range)", pend
    if k == "theme":
        import summary_theme
        new = summary_theme.grant_conn(conn, uid, v, source)
        t = f"🖼️ **{summary_theme.label(v)}** summary theme"
        return (t + " (turn it on with `rcsb use " + v + "`)") if new else (t + " — you already had it"), False
    return "", False


def redeem(user_id, code: str) -> dict:
    """{"ok": True, "lines": [...], "pending": bool} or {"ok": False, "reason": "..."}.
    reasons: invalid, off, expired, used_up, already, not_debuted, banned."""
    uid, code = int(user_id), norm_code(code)
    with economy._tx() as conn:
        _ensure(conn)
        c = conn.execute("SELECT * FROM redeem_codes WHERE code = ?", (code,)).fetchone()
        if c is None:
            return {"ok": False, "reason": "invalid"}
        u = conn.execute("SELECT banned FROM users WHERE user_id = ?", (uid,)).fetchone()
        if u is None:
            return {"ok": False, "reason": "not_debuted"}
        if int(u["banned"] or 0):
            return {"ok": False, "reason": "banned"}
        if not c["active"]:
            return {"ok": False, "reason": "off"}
        if c["expires"] and time.time() >= c["expires"]:
            return {"ok": False, "reason": "expired"}
        if conn.execute("SELECT 1 FROM redeem_claims WHERE code = ? AND user_id = ?", (code, uid)).fetchone():
            return {"ok": False, "reason": "already"}
        if c["max_uses"] and c["used"] >= c["max_uses"]:
            return {"ok": False, "reason": "used_up"}
        economy._ensure_pending_table(conn)
        lines, pending, cache = [], False, []
        for r in json.loads(c["rewards"]):
            text, pend = _give(conn, uid, r, f"Redeem {code}", cache)
            if text:
                lines.append(text)
            pending = pending or pend
        conn.execute("INSERT INTO redeem_claims (code, user_id, ts) VALUES (?, ?, ?)", (code, uid, time.time()))
        conn.execute("UPDATE redeem_codes SET used = used + 1 WHERE code = ?", (code,))
        left = (c["max_uses"] - c["used"] - 1) if c["max_uses"] else None
        return {"ok": True, "code": code, "lines": lines, "pending": pending, "left": left}
