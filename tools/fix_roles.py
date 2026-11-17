"""Fix wrong roles / countries on many cards at once.

    cd /root/ss-bot
    venv/bin/python tools/fix_roles.py            # shows what would change (nothing is written)
    venv/bin/python tools/fix_roles.py --apply    # writes the changes (makes a backup first)

A rule matches every card of that player: "Kuldeep Yadav" also fixes "Kuldeep Yadav (DW)" etc.
After --apply, run /refreshcards in Discord so the card images show the new role.
Edit FIXES below to add more players.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "cards_maker.db"

# player (without the "(DW)" style tag) -> columns to set
FIXES: dict[str, dict] = {
    # left-arm bowlers that were saved as right-arm / generic bowler
    "Kuldeep Yadav":     {"role": "LBOWL"},
    "Mitchell Starc":    {"role": "LBOWL"},
    "Wasim Akram":       {"role": "LBOWL"},
    "Mohammad Amir":     {"role": "LBOWL"},
    "Shaheen Afridi":    {"role": "LBOWL"},
    "Zaheer Khan":       {"role": "LBOWL"},
    "Michell Johnson":   {"role": "LBOWL"},
    "Mitchell Johnson":  {"role": "LBOWL"},
    "Fazalhaq Farooqi":  {"role": "LBOWL"},
    "Nandre Burger":     {"role": "LBOWL"},
    # right-arm bowler saved as left-arm
    "Ravi Bishnoi":      {"role": "RBOWL"},
    # bowlers / batters in the wrong group
    "Brian Masaba":      {"role": "RBOWL"},        # Uganda leg-spinner, was BAT
    "Alick Athanaze":    {"role": "BAT"},          # batter, was RBOWL
    "Shane Watson":      {"role": "AR"},           # all-rounder, was BAT
    "Jacob Bethell":     {"role": "AR"},           # spin all-rounder, was BAT
    # wrong country
    "Malcolm Marshall":  {"country": "West Indies"},
}


def base_name(name: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*", " ", str(name or "")).strip().lower()


def main(apply: bool) -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cards)")}
    rows = conn.execute("SELECT * FROM cards").fetchall()
    wanted = {k.lower(): v for k, v in FIXES.items()}

    # country emoji: copy it from another card of that country
    emoji_of = {}
    for r in rows:
        if r["country"] and r["country_emoji"]:
            emoji_of.setdefault(r["country"], r["country_emoji"])

    changes = []
    for r in rows:
        fix = wanted.get(base_name(r["playername"]))
        if not fix:
            continue
        upd = {}
        for col, val in fix.items():
            if col in cols and r[col] != val:
                upd[col] = val
        if "country" in upd and upd["country"] in emoji_of:
            upd["country_emoji"] = emoji_of[upd["country"]]
        if upd:
            changes.append((r["playername_key"], r["playername"], {c: r[c] for c in upd}, upd))

    if not changes:
        print("Nothing to change. Everything already matches.")
        return
    for _, name, old, new in changes:
        diff = ", ".join(f"{c}: {old[c]} -> {new[c]}" for c in new if c != "country_emoji")
        print(f"  {name:32s} {diff}")
    print(f"\n{len(changes)} card(s).")
    missing = [k for k in FIXES if k.lower() not in {base_name(r['playername']) for r in rows}]
    if missing:
        print("Not in the database (skipped):", ", ".join(missing))
    if not apply:
        print("\nDry run. Nothing written. Run again with --apply to save.")
        return
    backup = DB.with_name(f"cards_maker_before_rolefix_{int(time.time())}.db")
    shutil.copy2(DB, backup)
    with conn:
        for key, _, _, new in changes:
            sets = ", ".join(f"{c} = ?" for c in new)
            conn.execute(f"UPDATE cards SET {sets} WHERE playername_key = ?", (*new.values(), key))
    print(f"\nSaved. Backup: {backup.name}\nNow run /refreshcards in Discord.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
