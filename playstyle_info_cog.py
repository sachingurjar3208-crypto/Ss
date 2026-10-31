"""Playstyle info: which badge is which playstyle (names only, no numbers).

    rcplaystyles            every playstyle badge with its name in one picture
    rcplaystyles <name>     one playstyle in all three badge colours (Silver / Gold / Golden Pink)

Aliases: rcplaystyle, rcps
"""
from __future__ import annotations

import asyncio
import io

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

import playstyle_badges as pb

BG = (24, 25, 31)
PANEL = (34, 36, 44)
TEXT = (236, 236, 242)
MUTED = (150, 154, 168)
GOLD = (232, 190, 92)
LEVEL_WORDS = {1: "SILVER", 2: "GOLD", 3: "GOLDEN PINK"}

_CACHE: dict = {}


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(str(pb.Path(__file__).with_name(name)), size)
    except Exception:
        return ImageFont.load_default()


def _fit(draw, text, fname, size, max_w):
    f = _font(fname, size)
    while size > 12 and draw.textlength(text, font=f) > max_w:
        size -= 1
        f = _font(fname, size)
    return f


def _all_image() -> bytes:
    names = list(pb.ICONS)
    cols, cell_w, cell_h, badge = 3, 300, 300, 200
    rows = (len(names) + cols - 1) // cols
    top, bottom = 120, 150
    W, H = cols * cell_w + 60, top + rows * cell_h + bottom
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W / 2, 58), "P L A Y S T Y L E S", font=_font("display_bold.ttf", 56), fill=GOLD, anchor="mm")
    for i, name in enumerate(names):
        x0 = 30 + (i % cols) * cell_w
        y0 = top + (i // cols) * cell_h
        d.rounded_rectangle((x0 + 10, y0 + 6, x0 + cell_w - 10, y0 + cell_h - 14), radius=22, fill=PANEL)
        b = pb.get_badge(name, 2)
        if b is not None:
            b = b.resize((badge, badge), Image.LANCZOS)
            img.paste(b, (int(x0 + (cell_w - badge) / 2), y0 + 22), b)
        label = name.upper()
        d.text((x0 + cell_w / 2, y0 + cell_h - 48), label,
               font=_fit(d, label, "label.ttf", 30, cell_w - 40), fill=TEXT, anchor="mm")
    # badge colours (names only)
    y = H - bottom + 30
    d.text((W / 2, y), "BADGE COLOURS", font=_font("label.ttf", 26), fill=MUTED, anchor="mm")
    sample = names[0]
    for k, lvl in enumerate((1, 2, 3)):
        cx = W / 2 + (k - 1) * 260
        b = pb.get_badge(sample, lvl)
        if b is not None:
            b = b.resize((64, 64), Image.LANCZOS)
            img.paste(b, (int(cx - 110), y + 30), b)
        d.text((cx - 36, y + 62), LEVEL_WORDS[lvl], font=_font("label.ttf", 26), fill=TEXT, anchor="lm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _one_image(name: str) -> bytes:
    W, H, badge = 1020, 520, 260
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    title = name.upper()
    d.text((W / 2, 64), title, font=_fit(d, title, "display_bold.ttf", 64, W - 80), fill=GOLD, anchor="mm")
    for k, lvl in enumerate((1, 2, 3)):
        x0 = 40 + k * 320
        d.rounded_rectangle((x0, 130, x0 + 300, 480), radius=24, fill=PANEL)
        b = pb.get_badge(name, lvl)
        if b is not None:
            b = b.resize((badge, badge), Image.LANCZOS)
            img.paste(b, (x0 + 20, 150), b)
        d.text((x0 + 150, 445), LEVEL_WORDS[lvl], font=_font("label.ttf", 32), fill=TEXT, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _find(query: str) -> tuple[str | None, list[str]]:
    """(exact playstyle or None, close matches to suggest). Spaces / dashes / case don't matter."""
    def norm(t):
        return "".join(ch for ch in t.lower() if ch.isalnum())
    q = norm(query)
    names = list(pb.ICONS)
    for n in names:
        if norm(n) == q:
            return n, []
    close = [n for n in names if norm(n).startswith(q)] or [n for n in names if q and q in norm(n)]
    if len(close) == 1:
        return close[0], []
    return None, close


def _image(name: str | None) -> bytes:
    key = name or "*all*"
    if key not in _CACHE:
        _CACHE[key] = _all_image() if name is None else _one_image(name)
    return _CACHE[key]


class PlaystyleInfoCog(commands.Cog, name="PlaystyleInfo"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="rcplaystyles", aliases=["rcplaystyle", "rcps"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rcplaystyles(self, ctx: commands.Context, *, name: str = ""):
        """Show the playstyle badges with their names."""
        found = None
        if name.strip():
            found, close = _find(name)
            if found is None:
                if close:
                    await ctx.send("Did you mean: " + ", ".join(f"**{c}**" for c in close[:6]) + "?")
                else:
                    await ctx.send("❌ No playstyle with that name. Use `rcplaystyles` to see all of them.")
                return
        async with ctx.typing():
            data = await asyncio.to_thread(_image, found)
        fname = "playstyles.png" if found is None else f"{found.lower().replace(' ', '_')}.png"
        tip = ("Use `rcplaystyles <name>` to see one playstyle in all badge colours."
               if found is None else f"**{found}** in Silver, Gold and Golden Pink.")
        await ctx.send(tip, file=discord.File(io.BytesIO(data), filename=fname))


async def setup(bot: commands.Bot):
    await bot.add_cog(PlaystyleInfoCog(bot))
