"""Signature Box image (PNG board) and the short draw animation (GIF).

    board_png(board, cards)            -> PNG bytes
    draw_gif(board_before, slot, cards) -> (GIF bytes, seconds until the pick lands)

Layout: the Signature card big in the MIDDLE, the 9 other rewards around it
(3 on the left, 3 on the right, 3 under the card). No chances are shown.
`cards` = {player_key: card row} for the Signature card and any player rewards.
"""
from __future__ import annotations

import io
import random
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import sigbox
import starpass_image as spi

_HERE = Path(__file__).parent
_NUM, _LAB = _HERE / "display_bold.ttf", _HERE / "label.ttf"
FILENAME = "sigbox.png"
GIF_NAME = "sigbox_draw.gif"

W, H = 1400, 960
GOLD = (236, 198, 112)
GOLD_HI = (255, 232, 160)
WHITE = (240, 236, 250)
DIM = (168, 158, 200)
TILE = (36, 28, 64)
TILE_LINE = (98, 80, 150)
GREEN = (60, 190, 120)

TW, TH, GAP = 206, 152, 14
TOP = 118                                   # first row of tiles
SIDE_X = 100                                # left column x (right column is mirrored)
ROWS_Y = [TOP + r * (TH + GAP) for r in range(4)]          # 118, 284, 450, 616
CARD_H = ROWS_Y[2] + TH - TOP              # the card is as tall as the 3 side tiles
PANEL_Y = ROWS_Y[3] + TH + 22              # 790
LADDER_Y = PANEL_Y + 96


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _slot_rects() -> dict[int, tuple[int, int, int, int]]:
    """slot -> rectangle. Slot 0 (Signature) = the big card in the middle."""
    out = {}
    rx = W - SIDE_X - TW
    for i in range(3):                       # slots 1-3 left, 4-6 right
        out[1 + i] = (SIDE_X, ROWS_Y[i], SIDE_X + TW, ROWS_Y[i] + TH)
        out[4 + i] = (rx, ROWS_Y[i], rx + TW, ROWS_Y[i] + TH)
    row_w = 3 * TW + 2 * GAP                 # slots 7-9 under the card
    x0 = (W - row_w) // 2
    for i in range(3):
        xa = x0 + i * (TW + GAP)
        out[7 + i] = (xa, ROWS_Y[3], xa + TW, ROWS_Y[3] + TH)
    cw = round(CARD_H * 1037 / 1517)         # normal card ratio (exact size is set when drawn)
    out[sigbox.SIGNATURE_SLOT] = ((W - cw) // 2, TOP, (W + cw) // 2, TOP + CARD_H)
    return out


RECTS = _slot_rects()


def _card_img(card, h: int):
    if card is None:
        return None
    try:
        import card_cache
        src = card_cache.load_rgba(card["image_path"])
    except Exception:
        src = None
    if src is None:
        return None
    return src.resize((max(1, round(src.width * h / src.height)), h), Image.LANCZOS)


def _background() -> Image.Image:
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        d.line((0, y, W, y), fill=(int(56 - 32 * t), int(28 - 12 * t), int(108 - 52 * t)))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for k in range(13):
        x = k * 130 - 200
        od.polygon([(x, H), (x + 36, H), (x + 520, 0), (x + 484, 0)], fill=(255, 255, 255, 9))
    od.ellipse((W / 2 - 330, 60, W / 2 + 330, 700), fill=(190, 120, 255, 60))      # glow behind the card
    return Image.alpha_composite(bg.convert("RGBA"), ov.filter(ImageFilter.GaussianBlur(40)))


def _label(rw, cards) -> str:
    if rw.kind == "player":
        c = cards.get(rw.player_key)
        return (c["playername"] if c is not None else (rw.player_key or "PLAYER")).upper()
    if rw.kind == "coins":
        return f"{rw.qty:,}"
    if rw.kind == "voucher":
        return f"x{rw.qty} VOUCHER"
    if rw.kind == "theme":
        import summary_theme
        return summary_theme.label(rw.player_key).upper()
    base = spi.PACKS[rw.kind][3] + " PACK"
    return base if rw.qty == 1 else f"x{rw.qty} {base}"


def _fit(d, text, path, size, max_w):
    f = _f(path, size)
    while size > 12 and d.textlength(text, font=f) > max_w:
        size -= 1
        f = _f(path, size)
    return f


def _check(d, cx, cy, r=30):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GREEN)
    k = r / 30
    d.line((cx - 14 * k, cy + 1 * k, cx - 3 * k, cy + 12 * k, cx + 16 * k, cy - 10 * k),
           fill=(255, 255, 255), width=max(4, int(7 * k)), joint="curve")


def _shade(img, rect, radius):
    x0, y0, x1, y1 = rect
    sh = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius, fill=(8, 6, 16, 175))
    img.alpha_composite(sh, (x0, y0))


def _draw_tile(img: Image.Image, slot: int, board: dict, cards: dict, opened: dict) -> None:
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = RECTS[slot]
    d.rounded_rectangle((x0, y0, x1, y1), 16, fill=TILE, outline=TILE_LINE, width=2)
    rw = board["box"]["rewards"][slot - 1]
    card = cards.get(rw.player_key) if rw.kind == "player" else None
    icon = spi._make_icon(rw.kind, 100, card=card, extra=rw.player_key)
    if icon is not None:
        img.alpha_composite(icon, (int((x0 + x1 - icon.width) / 2), y0 + 12))
    label = _label(rw, cards)
    d = ImageDraw.Draw(img)
    if slot not in opened:
        d.text(((x0 + x1) / 2, y1 - 22), label, font=_fit(d, label, _LAB, 21, TW - 16), fill=WHITE, anchor="mm")
        return
    _shade(img, (x0, y0, x1, y1), 16)
    d = ImageDraw.Draw(img)
    _check(d, (x0 + x1) / 2, y0 + 64, 26)
    d.text(((x0 + x1) / 2, y1 - 22), label, font=_fit(d, label, _LAB, 21, TW - 16), fill=(150, 140, 175), anchor="mm")
    d.text((x1 - 10, y0 + 9), f"SPIN {opened[slot]}", font=_f(_LAB, 14), fill=DIM, anchor="rt")


def _draw_card(img: Image.Image, board: dict, cards: dict, opened: dict) -> None:
    box = board["box"]
    x0, y0, x1, y1 = RECTS[sigbox.SIGNATURE_SLOT]
    card = _card_img(cards.get(box["player_key"]), CARD_H)
    if card is None:                                  # no image yet: a gold placeholder
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((x0, y0, x1, y1), 22, fill=(70, 48, 20), outline=GOLD, width=4)
        d.text(((x0 + x1) / 2, (y0 + y1) / 2), "SIGNATURE", font=_f(_NUM, 40), fill=GOLD, anchor="mm")
    else:
        cx = (W - card.width) // 2
        sh = Image.new("RGBA", card.size, (0, 0, 0, 0))
        sh.putalpha(card.split()[3].point(lambda a: int(a * 0.6)))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)), (cx + 8, y0 + 16))
        img.alpha_composite(card, (cx, y0))
    if sigbox.SIGNATURE_SLOT in opened:
        d = ImageDraw.Draw(img)
        mx, my = (x0 + x1) / 2, y1 - 40
        d.rounded_rectangle((mx - 130, my - 24, mx + 130, my + 24), 24, fill=(20, 60, 40), outline=GREEN, width=2)
        d.text((mx, my), f"WON ON SPIN {opened[sigbox.SIGNATURE_SLOT]}", font=_f(_NUM, 24), fill=(220, 255, 230), anchor="mm")


def _ends_text(end_ts: float) -> str:
    left = max(0, end_ts - time.time())
    days, hours = int(left // 86400), int(left % 86400 // 3600)
    if days >= 1:
        return f"Closes in {days} day{'s' if days != 1 else ''}"
    return f"Closes in {max(1, hours)} hour{'s' if hours != 1 else ''}"


def _base(board: dict, cards: dict, opened: dict) -> Image.Image:
    img = _background()
    box = board["box"]
    sig_card = cards.get(box["player_key"])
    d = ImageDraw.Draw(img)

    # header: name in the middle, closing time left, RC Points right
    title = box["name"].upper()
    d.text((W / 2, 46), title, font=_fit(d, title, _NUM, 48, 700), fill=GOLD, anchor="mm")
    name = (sig_card["playername"] if sig_card is not None else box["player_key"]).upper()
    sub = f"{name}  ·  SIGNATURE EDITION"
    d.text((W / 2, 88), sub, font=_fit(d, sub, _LAB, 20, 700), fill=DIM, anchor="mm")
    d.text((SIDE_X, 50), _ends_text(box["end_ts"]), font=_f(_LAB, 20), fill=DIM, anchor="lm")
    bal = f"{board['balance']:,} RCP"
    f = _f(_NUM, 26)
    bw = d.textlength(bal, font=f) + 66
    bx1 = W - SIDE_X
    d.rounded_rectangle((bx1 - bw, 28, bx1, 72), 22, fill=(14, 10, 28), outline=TILE_LINE, width=2)
    gx, gy = bx1 - bw + 28, 50
    d.polygon([(gx, gy - 13), (gx + 11, gy - 3), (gx, gy + 13), (gx - 11, gy - 3)], fill=(70, 200, 220))
    d.text((gx + 20, gy), bal, font=f, fill=WHITE, anchor="lm")

    _draw_card(img, board, cards, opened)
    for slot in range(1, sigbox.OTHER_SLOTS + 1):
        _draw_tile(img, slot, board, cards, opened)
    d = ImageDraw.Draw(img)

    # info panel: progress + next spin
    px0, px1 = SIDE_X, W - SIDE_X
    d.rounded_rectangle((px0, PANEL_Y, px1, PANEL_Y + 80), 16, fill=(14, 10, 28), outline=TILE_LINE, width=2)
    d.text((px0 + 24, PANEL_Y + 24), "Every reward drops once  ·  Signature card guaranteed by spin 10",
           font=_f(_LAB, 20), fill=WHITE, anchor="lm")
    bx0, bx1_, by = px0 + 24, px0 + 640, PANEL_Y + 56
    spins = len(opened)
    d.rounded_rectangle((bx0, by - 7, bx1_, by + 7), 7, fill=(50, 40, 80))
    if spins:
        d.rounded_rectangle((bx0, by - 7, bx0 + (bx1_ - bx0) * spins / sigbox.SPINS, by + 7), 7, fill=GOLD)
    d.text((bx1_ + 16, by), f"{spins} / {sigbox.SPINS}", font=_f(_LAB, 19), fill=DIM, anchor="lm")
    nx1, nx0 = px1 - 12, px1 - 300
    if board["done"]:
        txt, fill, ink = "BOX COMPLETE", (60, 52, 90), WHITE
    else:
        cost = board["next_cost"]
        txt = f"SPIN {board['next_spin']}:  FREE" if cost == 0 else f"SPIN {board['next_spin']}:  {cost:,} RCP"
        fill, ink = (232, 190, 80), (24, 18, 30)
    d.rounded_rectangle((nx0, PANEL_Y + 11, nx1, PANEL_Y + 69), 29, fill=fill)
    d.text(((nx0 + nx1) / 2, PANEL_Y + 40), txt, font=_fit(d, txt, _NUM, 28, 270), fill=ink, anchor="mm")

    # price ladder
    n = sigbox.SPINS
    cw = (px1 - px0 - (n - 1) * 8) / n
    nxt = board["next_spin"]
    for i in range(n):
        x0 = px0 + i * (cw + 8)
        cost = box["costs"][i]
        done = i + 1 <= spins
        cur = nxt == i + 1
        d.rounded_rectangle((x0, LADDER_Y, x0 + cw, LADDER_Y + 52), 10,
                            fill=(70, 52, 22) if cur else (22, 16, 40),
                            outline=GOLD if cur else (60, 48, 96), width=2 if cur else 1)
        col = (110, 100, 140) if done else (GOLD if cur else WHITE)
        d.text((x0 + cw / 2, LADDER_Y + 16), f"SPIN {i + 1}", font=_f(_LAB, 14), fill=col if cur else DIM, anchor="mm")
        d.text((x0 + cw / 2, LADDER_Y + 37), "FREE" if cost == 0 else f"{cost:,}",
               font=_f(_NUM, 19), fill=col, anchor="mm")
    return img


def board_png(board: dict, cards: dict) -> bytes:
    img = _base(board, cards, board["drawn"]).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


GIF_SCALE = 0.6
GIF_HOLD_MS = 1600


def _glow(img: Image.Image, slot: int, strong: bool) -> Image.Image:
    out = img.copy()
    x0, y0, x1, y1 = RECTS[slot]
    r = 26 if slot == sigbox.SIGNATURE_SLOT else 22
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle((x0 - 8, y0 - 8, x1 + 8, y1 + 8), r,
                                          outline=(255, 226, 140, 255 if strong else 200), width=12 if strong else 8)
    out.alpha_composite(lay.filter(ImageFilter.GaussianBlur(7 if strong else 5)))
    ImageDraw.Draw(out).rounded_rectangle((x0 - 3, y0 - 3, x1 + 3, y1 + 3), r - 4, outline=GOLD_HI, width=5)
    if not strong:
        out.alpha_composite(Image.new("RGBA", (x1 - x0, y1 - y0), (255, 230, 150, 34)), (x0, y0))
    return out


def draw_gif(board_before: dict, slot: int, cards: dict) -> tuple[bytes, float]:
    """The light runs over the still-closed rewards (and the card), slows down and stops on the prize."""
    opened = board_before["drawn"]
    base = _base(board_before, cards, opened)
    closed = [s for s in range(sigbox.SPINS) if s not in opened]
    steps = 15 if len(closed) > 1 else 3
    seq, last = [], None
    for _ in range(steps - 1):
        last = random.choice([s for s in closed if s != last] or closed)
        seq.append(last)
    if seq and seq[-1] == slot and len(closed) > 1:
        seq[-1] = random.choice([s for s in closed if s != slot])
    seq.append(slot)
    size = (int(W * GIF_SCALE), int(H * GIF_SCALE))
    frames, durs = [], []
    for i, s in enumerate(seq):
        final = i == len(seq) - 1
        frames.append(_glow(base, s, final).convert("RGB").resize(size, Image.LANCZOS))
        durs.append(GIF_HOLD_MS if final else int(70 + 260 * (i / max(1, len(seq) - 1)) ** 2.2))
    pal = frames[-1].quantize(colors=160, method=Image.Quantize.MEDIANCUT)
    pics = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    buf = io.BytesIO()
    pics[0].save(buf, "GIF", save_all=True, append_images=pics[1:], duration=durs, disposal=1)
    return buf.getvalue(), sum(durs[:-1]) / 1000.0 + 0.5
