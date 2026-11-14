"""Foreground report: size and placement data of every card's cutout.

Run on the VPS from the bot folder:
    python3 tools/fg_report.py

Writes fg_report.txt (one line per card) and fg_sheet_1.jpg, fg_sheet_2.jpg ... (thumbnails of
every cutout on a grey checker, with the name and the card's centre line in red).
Nothing in the database is changed.
"""
import sqlite3
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("aiohttp", type(sys)("aiohttp"))     # card_image imports it; not needed here
import card_image as ci  # noqa: E402

TH_W, TH_H, COLS, ROWS = 220, 300, 6, 5


def main():
    conn = sqlite3.connect(ROOT / "cards_maker.db")
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    want = [c for c in ("playername", "foreground_link", "full_scale", "full_dx", "full_dy") if c in cols]
    cards = conn.execute(f"SELECT {', '.join(want)} FROM cards ORDER BY playername COLLATE NOCASE").fetchall()

    lines = ["name | file WxH | player WxH | empty L/R/T/B px | cut edges | size% dx dy"]
    thumbs = []
    for c in cards:
        name = c["playername"]
        link = c["foreground_link"] if "foreground_link" in c.keys() else None
        path = ci._foreground_file(link) if link else None
        if not path or not path.exists():
            lines.append(f"{name} | MISSING")
            continue
        try:
            im = Image.open(path).convert("RGBA")
        except Exception:
            lines.append(f"{name} | BROKEN FILE")
            continue
        a = im.getchannel("A").point(lambda v: 255 if v > 24 else 0)
        bb = a.getbbox()
        if not bb:
            lines.append(f"{name} | EMPTY (fully transparent)")
            continue
        W, H = im.size
        l, t, r, b = bb[0], bb[1], W - bb[2], H - bb[3]
        cut = [s for s, v in (("left", l), ("right", r), ("top", t), ("bottom", b)) if v <= 2]
        lines.append(f"{name} | {W}x{H} | {bb[2]-bb[0]}x{bb[3]-bb[1]} | {l}/{r}/{t}/{b} | "
                     f"{','.join(cut) or '-'} | {c['full_scale'] if 'full_scale' in c.keys() else ''} "
                     f"{c['full_dx'] if 'full_dx' in c.keys() else ''} {c['full_dy'] if 'full_dy' in c.keys() else ''}")
        crop = im.crop(bb)
        crop.thumbnail((TH_W - 10, TH_H - 40))
        thumbs.append((name, crop))

    (ROOT / "fg_report.txt").write_text("\n".join(lines), encoding="utf-8")

    font = ImageFont.load_default()
    per = COLS * ROWS
    for n in range(0, len(thumbs), per):
        sheet = Image.new("RGB", (COLS * TH_W, ROWS * TH_H), (40, 40, 48))
        d = ImageDraw.Draw(sheet)
        for i, (name, crop) in enumerate(thumbs[n:n + per]):
            x, y = (i % COLS) * TH_W, (i // COLS) * TH_H
            d.rectangle((x + 2, y + 2, x + TH_W - 3, y + TH_H - 3), fill=(70, 70, 80))
            sheet.paste(crop, (x + (TH_W - crop.width) // 2, y + TH_H - 36 - crop.height), crop)
            d.line((x + TH_W // 2, y + 4, x + TH_W // 2, y + TH_H - 36), fill=(230, 40, 40), width=1)
            d.text((x + 6, y + TH_H - 30), name[:34], fill=(255, 255, 255), font=font)
        sheet.save(ROOT / f"fg_sheet_{n // per + 1}.jpg", quality=85)
    print(f"{len(cards)} cards, {len(thumbs)} cutouts. Wrote fg_report.txt and "
          f"{(len(thumbs) + per - 1) // per} sheet image(s).")


if __name__ == "__main__":
    main()
