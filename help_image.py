"""Help menu image for `cshelp` (pure Pillow, no Discord code).

    build_help_image() -> PNG BytesIO

Same dark-gradient / rounded-card look as quest_image.py and squad_image.py,
built from the bot's own fonts (display_bold.ttf for headings, label.ttf for
body text). Commands written as `backticked` text are drawn as small
rounded chips in the section's accent colour; everything else is plain body
text that wraps normally. This file is purely cosmetic — add/remove/edit a
command here and nothing else changes; the real commands still live in
squad_cog.py / economy_cog.py / ranked_cog.py.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).parent
_NUM, _LAB, _NAME = _HERE / "display_bold.ttf", _HERE / "label.ttf", _HERE / "name_font.ttf"

W = 1000
SIDE = 46
FILENAME = "cshelp.png"

BG_TOP, BG_BOT = (12, 16, 27), (20, 27, 45)
CARD, CARD_LINE = (24, 31, 52), (40, 51, 79)
WHITE = (240, 244, 255)
DIM = (150, 163, 198)
TEAL = (47, 191, 155)

CHIP_PAD_X = 10
TOKEN_GAP = 10
LINE_H = 34
ENTRY_GAP = 10
SECTION_GAP = 26
HEADER_PAD = 14

# (section name, accent colour, [(command markdown, description), ...])
SECTIONS = [
    ("START", (47, 191, 155), [
        ("`csdebut`", "Create your team"),
        ("`csstarterpack`", "Claim your first 11 players"),
        ("`csdaily` `csweekly` (Premium) `csmonthly`", "Free coins + a player card"),
        ("`csclaim`", "Free random card every hour — Premium: every 35 min"),
        ("`cscd`", "Check when your claim cooldowns are ready"),
        ("`csmp ranked`", "Rank-up mode queue (5 overs, same-tier opponents)"),
        ("`csrank`", "Your tier, stars and season record"),
        ("`csranktop`", "Global top 10 by stars"),
    ]),
    ("SQUAD", (90, 156, 248), [
        ("`cssquad` `csshow <player>`", "View your squad, or one card"),
        ("`csxi` `csovr`", "Your starting XI image + OVR"),
        ("`csautoxi` `csautoplay` (Premium)", "Auto-pick your XI / auto-play a match"),
        ("`csxi @user` (Premium)", "View someone else's XI"),
        ("`csswap 3 5`", "Swap two XI slots (also `csswap A | B`)"),
        ("`cscaptain <player>`", "Set your captain"),
        ("`csteamname <name>`", "Rename your team"),
        ("`csprofile`", "Your team profile card"),
        ("`csview <player>`", "Your stats on a card you own"),
        ("`csdata <player>` (Premium)", "That card's stats across every owner"),
        ("`cssubs`", "Impact Player subs — add / remove / clear"),
    ]),
    ("ECONOMY", (240, 184, 70), [
        ("`cspurse`", "Coins and vouchers"),
        ("`cspack` `csopen <type>`", "Buy and open packs"),
        ("`csbuy <player>` `cssell <player>`", "Buy or sell a card"),
        ("`cstrade @user my X | their Y`", "Trade cards — same OVR, 5,000 coin fee each"),
        ("`cspending` `csleaderboard`", "Cards waiting on a decision / top teams"),
        ("`csquest`", "5 daily quests — 1 voucher each"),
        ("`csdraft`", "Spend vouchers on a player draft"),
    ]),
    ("PLAY", (240, 106, 106), [
        ("`csmp @user <overs>`", "Challenge another player to a match"),
        ("`cscancel`", "Cancel the match running in this channel"),
        ("`csresume`", "Re-send a stuck match's current prompt"),
    ]),
]


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _tokenize(text: str):
    """Splits on whitespace, keeping `backticked phrases` (even with spaces
    inside) as one atomic 'code' token so a chip never breaks mid-command."""
    tokens = []
    for m in re.finditer(r"`[^`]+`|\S+", text):
        s = m.group(0)
        if s.startswith("`") and s.endswith("`") and len(s) >= 2:
            tokens.append(("code", s[1:-1]))
        else:
            tokens.append(("text", s))
    return tokens


def _wrap(d: ImageDraw.ImageDraw, tokens, font_code, font_text, max_w: int):
    """Greedy word-wrap. Returns a list of lines, each a list of
    (kind, text, width) ready to draw left-to-right."""
    lines, cur, x = [], [], 0
    for kind, text in tokens:
        font = font_code if kind == "code" else font_text
        w = d.textlength(text, font=font)
        if kind == "code":
            w += CHIP_PAD_X * 2
        if cur and x + w > max_w:
            lines.append(cur)
            cur, x = [], 0
        cur.append((kind, text, w))
        x += w + TOKEN_GAP
    if cur:
        lines.append(cur)
    return lines


def _draw_lines(d: ImageDraw.ImageDraw, lines, x0: int, y: int, font_code, font_text, accent) -> int:
    chip_fill = tuple(int(c * 0.20) for c in accent)
    for line in lines:
        x = x0
        for kind, text, w in line:
            if kind == "code":
                box = [x, y, x + w, y + LINE_H - 8]
                d.rounded_rectangle(box, radius=8, fill=chip_fill, outline=accent, width=1)
                d.text((x + CHIP_PAD_X, y + (LINE_H - 8) / 2), text, font=font_code, fill=accent, anchor="lm")
            else:
                d.text((x, y + (LINE_H - 8) / 2), text, font=font_text, fill=DIM, anchor="lm")
            x += w + TOKEN_GAP
        y += LINE_H
    return y


def _canvas(h: int) -> Image.Image:
    img = Image.new("RGB", (W, h), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))
    return img


def build_help_image() -> io.BytesIO:
    f_title = _f(_NUM, 58)
    f_sub = _f(_LAB, 24)
    f_section = _f(_LAB, 26)
    f_code = _f(_LAB, 22)
    f_text = _f(_LAB, 22)

    dummy = Image.new("RGB", (W, 10))
    dd = ImageDraw.Draw(dummy)
    content_w = W - SIDE * 2 - 36   # minus card inner padding

    # ── pass 1: wrap every entry once, remember the lines + each card's height ──
    section_layouts = []
    for name, accent, entries in SECTIONS:
        card_h = HEADER_PAD * 2 + 40  # header bar
        entry_lines = []
        for cmds, desc in entries:
            tokens = _tokenize(cmds) + [("text", "—")] + _tokenize(desc)
            lines = _wrap(dd, tokens, f_code, f_text, content_w)
            entry_lines.append(lines)
            card_h += len(lines) * LINE_H + ENTRY_GAP
        card_h += 18 - ENTRY_GAP  # trim the last gap, add bottom padding
        section_layouts.append((name, accent, entries, entry_lines, card_h))

    header_h = 128
    total_h = header_h
    for _, _, _, _, card_h in section_layouts:
        total_h += card_h + SECTION_GAP
    total_h += 30  # bottom margin

    img = _canvas(int(total_h))
    d = ImageDraw.Draw(img)

    # ── header ──────────────────────────────────────────────────────────────
    d.text((SIDE, 32), "CRICSTAR HELP", font=f_title, fill=WHITE)
    d.text((SIDE, 94), "Every command, one screen. Type any of these in chat.", font=f_sub, fill=DIM)
    d.line([(SIDE, header_h - 6), (W - SIDE, header_h - 6)], fill=CARD_LINE, width=2)

    # ── sections ────────────────────────────────────────────────────────────
    y = header_h + 10
    for name, accent, entries, entry_lines, card_h in section_layouts:
        d.rounded_rectangle([SIDE, y, W - SIDE, y + card_h], radius=18, fill=CARD, outline=CARD_LINE, width=2)
        d.rounded_rectangle([SIDE, y, SIDE + 8, y + card_h], radius=4, fill=accent)
        cy = y + HEADER_PAD
        d.text((SIDE + 28, cy), name, font=f_section, fill=accent)
        cy += 40
        for lines in entry_lines:
            cy = _draw_lines(d, lines, SIDE + 28, cy, f_code, f_text, accent)
            cy += ENTRY_GAP
        y += card_h + SECTION_GAP

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
