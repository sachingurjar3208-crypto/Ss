"""Quest board image for `csquest` (pure Pillow, no Discord code).

    build_quest_board(board, team_name) -> PNG BytesIO

`board` is the dict from quest.get_board(). Portrait layout so it reads well on a phone:
header with the reset timer, a 5-segment meter, one card per quest (title, progress bar,
"2 / 3" counter) and a footer with the daily-streak voucher progress.
No emoji: everything is drawn with shapes and the bot's own fonts.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).parent
_NUM, _LAB, _NAME = _HERE / "display_bold.ttf", _HERE / "label.ttf", _HERE / "name_font.ttf"

W = 1000
FILENAME = "quest.png"                      # embeds must use attachment://quest.png

BG_TOP, BG_BOT = (13, 17, 29), (22, 29, 48)
CARD, CARD_LINE = (25, 32, 53), (42, 53, 82)
DONE_CARD, DONE_LINE = (18, 44, 50), (47, 191, 155)
TRACK = (38, 48, 76)
WHITE = (240, 244, 255)
DIM = (140, 155, 195)
TEAL = (47, 191, 155)
TIER_COLOR = {"easy": (96, 196, 130), "medium": (240, 184, 70), "hard": (240, 106, 106)}
TIER_LABEL = {"easy": "EASY", "medium": "MEDIUM", "hard": "HARD"}

HEADER_H = 150
ROW_H, ROW_GAP = 112, 14
SIDE = 40


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _spaced(d: ImageDraw.ImageDraw, xy, text: str, font, fill, spacing: int = 3, anchor: str = "l") -> int:
    """Draw text with extra letter spacing. anchor: 'l' (left) or 'r' (right). Returns the width."""
    widths = [d.textlength(ch, font=font) for ch in text]
    total = int(sum(widths) + spacing * max(0, len(text) - 1))
    x, y = xy
    if anchor == "r":
        x -= total
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=font, fill=fill)
        x += w + spacing
    return total


def _canvas(h: int) -> Image.Image:
    img = Image.new("RGB", (W, h), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))
    return img


def _fmt_wait(sec: int) -> str:
    h, m = sec // 3600, (sec % 3600) // 60
    return f"{h:02d}h {m:02d}m"


def _counter(d: ImageDraw.ImageDraw, rx: int, cy: int, a, b, font, fill, dim) -> None:
    """Draw  a / b  right-aligned at rx, centred on cy. The slash is drawn as a line
    because the display font has no '/' glyph."""
    sb, sa = str(b), str(a)
    d.text((rx, cy), sb, font=font, fill=dim, anchor="rm")
    x = rx - d.textlength(sb, font=font) - 16
    h = int(font.size * 0.62) if hasattr(font, "size") else 30
    d.line([(x + 7, cy - h // 2), (x - 7, cy + h // 2)], fill=dim, width=4)
    d.text((x - 16, cy), sa, font=font, fill=fill, anchor="rm")


def _check(d: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color, width: int = 5) -> None:
    d.line([(cx - size, cy), (cx - size // 3, cy + size * 2 // 3), (cx + size, cy - size * 2 // 3)],
           fill=color, width=width, joint="curve")


def build_quest_board(board: dict, team_name: str = "") -> io.BytesIO:
    quests = board["quests"]
    n = len(quests)
    rows_h = n * ROW_H + (n - 1) * ROW_GAP
    footer_h = 168
    top_of_rows = HEADER_H + 86
    H = top_of_rows + rows_h + 30 + footer_h + 30

    img = _canvas(H)
    d = ImageDraw.Draw(img)

    f_title = _f(_NUM, 74)
    f_small = _f(_LAB, 26)
    f_label = _f(_LAB, 22)
    f_big = _f(_NUM, 60)
    f_name = _f(_NAME, 29)
    f_count = _f(_NUM, 54)
    f_idx = _f(_NUM, 58)
    f_chip = _f(_LAB, 21)

    # ── header ──────────────────────────────────────────────────────────────
    d.text((SIDE, 30), "DAILY QUESTS", font=f_title, fill=WHITE)
    sub = (team_name or "").upper()[:28]
    if sub:
        _spaced(d, (SIDE + 2, 108), sub, f_small, DIM, spacing=3)
    _spaced(d, (W - SIDE, 36), "RESETS IN", f_label, DIM, spacing=4, anchor="r")
    d.text((W - SIDE, 62), _fmt_wait(int(board["reset_in"])), font=f_big, fill=TEAL, anchor="ra")

    # ── overall meter: one segment per quest ────────────────────────────────
    done = board["done_count"]
    y0 = HEADER_H
    d.line([(SIDE, y0 - 8), (W - SIDE, y0 - 8)], fill=CARD_LINE, width=2)
    seg_gap = 10
    bar_x0, bar_x1 = SIDE, W - SIDE - 250
    seg_w = (bar_x1 - bar_x0 - seg_gap * (n - 1)) / n
    for i in range(n):
        x = bar_x0 + i * (seg_w + seg_gap)
        d.rounded_rectangle([x, y0 + 18, x + seg_w, y0 + 38], radius=10, fill=TEAL if i < done else TRACK)
    f_meter = _f(_NUM, 52)
    _counter(d, W - SIDE, y0 + 28, done, n, f_meter, WHITE, DIM)
    _spaced(d, (W - SIDE - 128, y0 + 18), "DONE", f_label, DIM, spacing=4, anchor="r")

    # ── quest cards ─────────────────────────────────────────────────────────
    for i, q in enumerate(quests):
        y = top_of_rows + i * (ROW_H + ROW_GAP)
        tier_c = TIER_COLOR.get(q["tier"], WHITE)
        fin = q["done"]
        d.rounded_rectangle([SIDE, y, W - SIDE, y + ROW_H], radius=20,
                            fill=DONE_CARD if fin else CARD, outline=DONE_LINE if fin else CARD_LINE, width=2)
        # tier stripe on the left edge
        d.rounded_rectangle([SIDE, y + 14, SIDE + 8, y + ROW_H - 14], radius=4, fill=tier_c)
        # index
        d.text((SIDE + 44, y + ROW_H // 2 + 2), str(i + 1), font=f_idx, fill=TEAL if fin else tier_c, anchor="mm")

        tx = SIDE + 90
        d.text((tx, y + 14), q["title"], font=f_name, fill=(190, 205, 215) if fin else WHITE)
        tw = int(d.textlength(q["title"], font=f_name))
        # tier chip right after the title
        label = TIER_LABEL.get(q["tier"], q["tier"].upper())
        cw = int(sum(d.textlength(c, font=f_chip) for c in label) + 3 * (len(label) - 1)) + 24
        cx0 = tx + tw + 16
        if cx0 + cw < W - SIDE - 190:
            d.rounded_rectangle([cx0, y + 20, cx0 + cw, y + 48], radius=14, outline=tier_c, width=2)
            _spaced(d, (cx0 + 12, y + 22), label, f_chip, tier_c, spacing=3)
        else:   # long title: show the tier under the bar instead
            _spaced(d, (tx, y + 82), label, f_chip, tier_c, spacing=3)

        # progress bar
        bx0, bx1 = tx, W - SIDE - 200
        by0, by1 = y + 62, y + 80
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=9, fill=TRACK)
        frac = 1.0 if fin else (q["progress"] / q["target"] if q["target"] else 0)
        if frac > 0:
            fx = bx0 + max(18, int((bx1 - bx0) * frac))
            d.rounded_rectangle([bx0, by0, min(bx1, fx), by1], radius=9, fill=TEAL if fin else tier_c)

        # right side: counter or DONE
        rx = W - SIDE - 24
        if fin:
            _spaced(d, (rx, y + 26), "+1 VOUCHER", f_label, DIM, spacing=3, anchor="r")
            tw2 = _spaced(d, (rx, y + 64), "COMPLETE", f_label, TEAL, spacing=3, anchor="r")
            _check(d, rx - tw2 - 26, y + 76, 11, TEAL, 5)
        else:
            _counter(d, rx, y + ROW_H // 2 - 8, q["progress"], q["target"], f_count, WHITE, DIM)
            left = q["target"] - q["progress"]
            _spaced(d, (rx, y + ROW_H // 2 + 28), f"{left} TO GO", f_label, DIM, spacing=3, anchor="r")

    # ── footer: streak ──────────────────────────────────────────────────────
    fy = top_of_rows + rows_h + 30
    d.rounded_rectangle([SIDE, fy, W - SIDE, fy + footer_h], radius=20, fill=CARD, outline=CARD_LINE, width=2)
    streak = int(board.get("streak", 0))
    cyc = streak % 7 if streak % 7 else (7 if streak > 0 else 0)
    _spaced(d, (SIDE + 28, fy + 24), "DAILY STREAK", f_label, DIM, spacing=4)
    d.text((SIDE + 28, fy + 54), f"{streak}", font=_f(_NUM, 64), fill=WHITE)
    sw = int(d.textlength(str(streak), font=_f(_NUM, 64)))
    d.text((SIDE + 28 + sw + 10, fy + 78), "day(s)", font=f_small, fill=DIM)
    # 7 dots = one streak cycle
    dot_r, dot_gap = 15, 18
    dx0 = W - SIDE - 28 - (7 * dot_r * 2 + 6 * dot_gap)
    for k in range(7):
        cx = dx0 + k * (dot_r * 2 + dot_gap) + dot_r
        cy = fy + 52
        if k < cyc:
            d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=TEAL)
            if k == 6:
                _check(d, cx, cy, 7, BG_TOP, 4)
        else:
            d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], outline=CARD_LINE, width=3, fill=TRACK)
    to_go = 7 - (streak % 7) if streak % 7 else (0 if streak > 0 else 7)
    if to_go:
        msg = f"Claim csdaily {to_go} more day(s) in a row to earn 3 vouchers"
    else:
        msg = "7 day streak reached: 3 vouchers paid"
    _spaced(d, (SIDE + 28, fy + 126), msg.upper(), f_label, DIM, spacing=2)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
