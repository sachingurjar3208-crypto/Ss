"""Daily database backup + small clean-up.

Every day at 04:00 IST (and once a few minutes after each start, if today's backup is missing) a
safe copy of every database is written to backups/<date>/ with SQLite's own backup API, so it
works while the bot is running. The last KEEP_DAYS days are kept, older folders are deleted.

These copies are on the SAME server. If that server's disk dies, they die too, so also download
the backups folder to your own PC (or another server) from time to time.

Also: coin-history rows (ledger) older than LEDGER_KEEP_DAYS are removed, so economy.db doesn't grow
forever. Nothing else is deleted.
"""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

from discord.ext import commands, tasks

IST = timezone(timedelta(hours=5, minutes=30))
HERE = Path(__file__).parent
BACKUP_DIR = HERE / "backups"
DATABASES = ("economy.db", "cards_maker.db", "cards.db")
KEEP_DAYS = 7
LEDGER_KEEP_DAYS = 180
RUN_AT = dtime(hour=4, minute=0, tzinfo=IST)


def _copy(src: Path, dst: Path) -> None:
    s = sqlite3.connect(str(src), timeout=30)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def make_backup(force: bool = False) -> str:
    day = datetime.now(IST).strftime("%Y-%m-%d")
    folder = BACKUP_DIR / day
    if folder.exists() and not force:
        return f"already done today ({folder.name})"
    tmp = BACKUP_DIR / f".{day}.part"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    done = []
    for name in DATABASES:
        src = HERE / name
        if src.exists():
            _copy(src, tmp / name)
            done.append(name)
    shutil.rmtree(folder, ignore_errors=True)
    tmp.rename(folder)                                    # a half-written backup never looks finished
    # keep only the last KEEP_DAYS folders
    for old in sorted(p for p in BACKUP_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))[:-KEEP_DAYS]:
        shutil.rmtree(old, ignore_errors=True)
    return f"{folder.name}: {', '.join(done) or 'no databases found'}"


def trim_ledger() -> int:
    path = HERE / "economy.db"
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        cur = conn.execute("DELETE FROM ledger WHERE ts < ?", (time.time() - LEDGER_KEEP_DAYS * 86400,))
        conn.commit()
        return cur.rowcount or 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


class BackupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.daily.start()
        self._first = asyncio.create_task(self._first_run())

    async def cog_unload(self):
        self.daily.cancel()
        if getattr(self, "_first", None):
            self._first.cancel()

    async def _run(self):
        try:
            msg = await asyncio.to_thread(make_backup)
            n = await asyncio.to_thread(trim_ledger)
            print(f"✓ Backup {msg}" + (f" · removed {n} old coin-history rows" if n else ""))
        except Exception as e:
            print(f"✗ Backup failed: {type(e).__name__}: {e}")

    async def _first_run(self):
        await asyncio.sleep(300)                        # let the bot finish starting first
        await self._run()

    @tasks.loop(time=RUN_AT)
    async def daily(self):
        await self._run()


async def setup(bot):
    await bot.add_cog(BackupCog(bot))
