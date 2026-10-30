"""PACK SHOP preview image — shown when a user runs `rcpack` with no argument.

    buf  = build_pack_shop_image(user_id)        # io.BytesIO (PNG), sync/CPU work
    file = await pack_shop_file(ctx.author.id)    # discord.File, ready to send

Pulls live data: squad_logic.PACKS (label/price/OVR range) and
economy.get_packs(user_id) (how many of each the user already owns), so the
image always matches what `rcpack`/`rcopen` would actually do.

Pack art reuses pack_gif.py's thumbnails/colours so the shop, the open
animation and the classic cards' medal icon all look like the same game.
Fonts: label.ttf and name_font.ttf from the same folder.
"""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw

from pack_gif import _pack_layer, _font, PACK_COLORS, LABELS, CREAM, _LABEL, _NAME

W = 820
ROW_H = 256
TOP_PAD, BOTTOM_PAD, SIDE_PAD = 150, 26, 50
BG_TOP, BG_BOT = (18, 12, 22), (8, 6, 10)
COIN = (255, 221, 110)


def _fmt_coins(n: int) -> str:
    return f"{int(n):,}"


def _coin_icon(d: ImageDraw.ImageDraw, cx, cy, r):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=COIN, outline=(150, 110, 20), width=2)
    d.ellipse([cx - r * .6, cy - r * .6, cx + r * .6, cy + r * .6], outline=(150, 110, 20), width=1)


@lru_cache(maxsize=1)
def _bg_column(h: int) -> Image.Image:
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / h
        col.putpixel((0, y), tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))
    return col


def _row(img: Image.Image, d: ImageDraw.ImageDraw, ry: int, key: str, spec: dict, owned: int) -> None:
    colors = PACK_COLORS.get(key)
    if colors is None:
        return
    lo, hi = spec["range"]
    d.rounded_rectangle([SIDE_PAD, ry, W - SIDE_PAD, ry + ROW_H - 24], radius=20,
                         fill=(255, 255, 255, 10), outline=(*colors[1], 120), width=2)
    thumb = _pack_layer(key, 150, 218)
    img.paste(thumb, (SIDE_PAD + 26, ry + 18), thumb)

    tx = SIDE_PAD + 26 + 150 + 34
    label = LABELS.get(key, spec.get("label", key).upper())
    d.text((tx, ry + 34), label, font=_font(_NAME, 26), fill=(255, 255, 255), anchor="lm")
    d.text((tx, ry + 68), f"1 player  •  {lo}-{hi} OVR", font=_font(_LABEL, 15), fill=(*CREAM, 210), anchor="lm")

    _coin_icon(d, tx + 11, ry + 108, 11)
    d.text((tx + 30, ry + 108), _fmt_coins(spec["price"]), font=_font(_LABEL, 20), fill=COIN, anchor="lm")

    f_cmd = _font(_LABEL, 14)
    cmd = f"rcpack {key}"
    cw = d.textlength(cmd, font=f_cmd) + 24
    d.rounded_rectangle([tx, ry + 134, tx + cw, ry + 160], radius=13, fill=(255, 255, 255, 18))
    d.text((tx + 12, ry + 147), cmd, font=f_cmd, fill=(220, 215, 230), anchor="lm")

    badge_txt = f"YOU OWN {owned}"
    f_badge = _font(_LABEL, 14)
    bw = d.textlength(badge_txt, font=f_badge) + 26
    bx1, by0 = W - SIDE_PAD - 20, ry + 22
    bcol = (*colors[1], 230) if owned else (255, 255, 255, 30)
    tcol = (20, 14, 10) if owned else (190, 185, 200)
    d.rounded_rectangle([bx1 - bw, by0, bx1, by0 + 30], radius=15, fill=bcol)
    d.text((bx1 - bw / 2, by0 + 15), badge_txt, font=f_badge, fill=tcol, anchor="mm")


def build_pack_shop_image(user_id) -> io.BytesIO:
    """PNG (BytesIO) listing every buyable pack with its price, OVR range and
    how many the given user currently owns."""
    import economy
    import squad_logic as sl

    rows = [(k, v) for k, v in sl.PACKS.items() if v.get("shop") is not False]
    h = TOP_PAD + len(rows) * ROW_H + BOTTOM_PAD
    img = _bg_column(h).resize((W, h), Image.NEAREST).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")

    d.text((W / 2, 58), "PACK SHOP", font=_font(_NAME, 38), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, 96), "Buy with rcpack <type>  •  Open with rcopen <type>",
            font=_font(_LABEL, 16), fill=(190, 180, 200), anchor="mm")
    d.line([(60, 124), (W - 60, 124)], fill=(255, 255, 255, 40), width=2)

    try:
        owned_all = economy.get_packs(user_id) or {}
    except Exception:
        owned_all = {}

    for i, (key, spec) in enumerate(rows):
        _row(img, d, TOP_PAD + i * ROW_H, key, spec, owned_all.get(key, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def pack_shop_file(user_id):
    """discord.File for the pack shop preview. Returns None on any failure
    (caller should fall back to the plain text/embed listing)."""
    try:
        import asyncio
        import discord
        buf = await asyncio.to_thread(build_pack_shop_image, user_id)
        return discord.File(buf, filename="pack_shop.png")
    except Exception as e:
        print(f"[pack_shop_image] skipped ({type(e).__name__}: {e})")
        return None
