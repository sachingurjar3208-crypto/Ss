"""Contact sheets of the FINISHED card images (what players see), to check size and centring.

Run on the VPS from the bot folder, after /refreshcards:
    venv/bin/python tools/card_sheet.py

Writes card_sheet_1.jpg, card_sheet_2.jpg ... in the bot folder: 24 cards per sheet, each with its
name and a thin red line down the card's centre. Nothing is changed.
"""
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
TW, TH, COLS, ROWS = 260, 380, 6, 4


def main():
    conn = sqlite3.connect(ROOT / "cards_maker.db")
    rows = conn.execute("SELECT playername, image_path FROM cards ORDER BY playername COLLATE NOCASE").fetchall()
    font = ImageFont.load_default()
    items, missing = [], []
    for name, path in rows:
        p = Path(path) if path else None
        if p and not p.is_absolute():
            p = ROOT / p
        if not p or not p.exists():
            missing.append(name)
            continue
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            missing.append(name)
            continue
        im.thumbnail((TW - 8, TH - 26))
        items.append((name, im))

    per = COLS * ROWS
    for n in range(0, len(items), per):
        sheet = Image.new("RGB", (COLS * TW, ROWS * TH), (30, 30, 36))
        d = ImageDraw.Draw(sheet)
        for i, (name, im) in enumerate(items[n:n + per]):
            x, y = (i % COLS) * TW, (i // COLS) * TH
            ox = x + (TW - im.width) // 2
            sheet.paste(im, (ox, y + 4))
            cx = ox + im.width // 2
            d.line((cx, y + 4, cx, y + 4 + im.height), fill=(255, 40, 40), width=1)
            d.text((x + 6, y + TH - 20), name[:40], fill=(255, 255, 255), font=font)
        sheet.save(ROOT / f"card_sheet_{n // per + 1}.jpg", quality=82)
    print(f"{len(items)} cards on {(len(items) + per - 1) // per} sheet(s). Missing image: {len(missing)}")
    if missing:
        print("Missing:", ", ".join(missing[:30]))


if __name__ == "__main__":
    main()
