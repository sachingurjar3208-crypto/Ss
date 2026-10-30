"""Rewards overview image for `rcrankrewards` (1200x860 PNG, drawn at 2x then scaled down).

Lists every tier (Bronze -> Ruby) with its one-time tier-up reward (✅ once already claimed this
season), its season-end reward, and below: the player's tier, stars, stars to the next tier and when
the season ends. Reuses the look of rank_image.py (same fonts, background, colours).

generate_rewards_card(...) returns PNG bytes.
"""
from __future__ import annotations

import io
import time

from PIL import Image, ImageDraw, ImageFilter

import rank_image as ri
from rank_image import _f, _p, _mix, _NAME, _NUM, _LAB, TIER_COLORS

W, H = ri.W, 860
GREY = (150, 162, 196, 255)
DIM = (120, 132, 170, 255)
WHITE = (255, 255, 255, 255)
GREEN = (90, 220, 130, 255)


def _background(color):
    """Same look as rank_image._background, but for this card's height (rank_image's is 560 tall)."""
    from PIL import ImageChops
    img = Image.new("RGB", (_p(W), _p(H)), (8, 12, 28))
    px = ImageDraw.Draw(img)
    top, bot = (16, 24, 52), (5, 8, 20)
    for y in range(_p(H)):
        px.line([(0, y), (_p(W), y)], fill=_mix(top, bot, y / _p(H)))
    glow = Image.new("RGB", img.size, (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([_p(620), _p(-200), _p(1300), _p(420)], fill=tuple(int(c * 0.32) for c in color))
    g.ellipse([_p(-150), _p(300), _p(350), _p(760)], fill=tuple(int(c * 0.16) for c in color))
    glow = glow.filter(ImageFilter.GaussianBlur(_p(110)))
    img = ImageChops.add(img, glow)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for x in range(-_p(H), _p(W), _p(46)):
        od.line([(x, _p(H)), (x + _p(H), 0)], fill=(255, 255, 255, 7), width=_p(14))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def _wrap(d, parts: list[str], font, maxw: int, max_lines: int = 2) -> list[str]:
    """Join reward parts with ' + ' and wrap them into at most `max_lines` lines of width `maxw`."""
    lines: list[str] = []
    cur = ""
    for part in parts:
        cand = part if not cur else f"{cur} + {part}"
        if d.textlength(cand, font=font) <= _p(maxw):
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = part
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [" + ".join(lines[max_lines - 1:])]
    # a single line still too wide: shorten with an ellipsis
    out = []
    for ln in lines:
        while len(ln) > 4 and d.textlength(ln, font=font) > _p(maxw):
            ln = ln[:-2].rstrip() + "…"
        out.append(ln)
    return out


def _check(d, cx, cy, color):
    d.ellipse([_p(cx - 15), _p(cy - 15), _p(cx + 15), _p(cy + 15)], fill=color)
    d.line([(_p(cx - 7), _p(cy + 1)), (_p(cx - 2), _p(cy + 6)), (_p(cx + 8), _p(cy - 6))],
           fill=(8, 12, 28, 255), width=_p(4), joint="curve")


def _star(d, cx, cy, r, color):
    d.polygon(ri._poly(cx, cy, r, 5, inner=r * 0.45), fill=color)


def generate_rewards_card(*, tiers: list[dict], stars: int, tier: str, next_name: str | None,
                          next_min: int | None, next_needed: int | None, season_active: bool,
                          season_name: str, season_end: int, now: float | None = None) -> bytes:
    color = TIER_COLORS.get(tier, (255, 200, 60))
    img = _background(color)
    d = ImageDraw.Draw(img, "RGBA")
    now = now if now is not None else time.time()

    d.rounded_rectangle([_p(14), _p(14), _p(W - 14), _p(H - 14)], radius=_p(26),
                        outline=color + (150,), width=_p(3))

    # header
    d.text((_p(48), _p(44)), "RANK REWARDS", font=_f(_LAB, 26), fill=color + (255,), anchor="lm")
    d.line([(_p(48), _p(66)), (_p(230), _p(66))], fill=color + (255,), width=_p(3))
    right = season_name.upper() if season_active and season_name else "NO SEASON RUNNING"
    d.text((_p(W - 48), _p(44)), right, font=_f(_LAB, 24), fill=GREY, anchor="rm")

    # column headers
    C_TIER, C_UP, C_UP_END, C_SEA, C_SEA_END = 98, 282, 716, 770, 1130
    y_head = 118
    d.text((_p(C_TIER), _p(y_head)), "TIER", font=_f(_LAB, 22), fill=GREY, anchor="lm")
    d.text((_p(C_UP), _p(y_head)), "TIER-UP REWARD  (once per season)", font=_f(_LAB, 22), fill=GREY, anchor="lm")
    d.text((_p(C_SEA), _p(y_head)), "SEASON-END REWARD", font=_f(_LAB, 22), fill=GREY, anchor="lm")
    d.line([(_p(56), _p(136)), (_p(W - 56), _p(136))], fill=(255, 255, 255, 40), width=_p(2))

    # rows
    row_h, top = 84, 146
    for i, t in enumerate(tiers):
        y0 = top + i * row_h
        cy = y0 + row_h / 2 - 4
        tc = TIER_COLORS.get(t["name"], (255, 200, 60))
        is_cur = t["name"] == tier
        d.rounded_rectangle([_p(40), _p(y0 + 4), _p(W - 40), _p(y0 + row_h - 4)], radius=_p(16),
                            fill=(255, 255, 255, 22 if is_cur else 12),
                            outline=(tc + (255,)) if is_cur else (255, 255, 255, 25),
                            width=_p(3 if is_cur else 1))
        # tier name + marker
        d.ellipse([_p(62), _p(cy - 9), _p(80), _p(cy + 9)], fill=tc + (255,))
        d.text((_p(C_TIER), _p(cy - 6)), t["name"].upper(), font=_f(_NAME, 24), fill=tc + (255,), anchor="lm")
        # star + the stars needed for this tier (drawn shape, not an emoji character)
        _star(d, C_TIER + 9, cy + 22, 9, (255, 200, 60, 255))
        d.text((_p(C_TIER + 24), _p(cy + 22)), f"{t['min']} stars", font=_f(_LAB, 20),
               fill=(200, 208, 226, 255), anchor="lm")
        if is_cur:
            d.text((_p(C_TIER + 120), _p(cy + 22)), "YOU", font=_f(_LAB, 18), fill=color + (255,), anchor="lm")

        # tier-up reward (left column) + check if claimed
        if t["tier_up"]:
            lines = _wrap(d, t["tier_up"], _f(_LAB, 24), 390)
            col = WHITE if t["claimed"] else (200, 208, 226, 255)
            ly = cy - (len(lines) - 1) * 15
            for ln in lines:
                d.text((_p(C_UP), _p(ly)), ln, font=_f(_LAB, 24), fill=col, anchor="lm")
                ly += 30
            if t["claimed"]:
                _check(d, C_UP_END, cy, GREEN)
        else:
            d.text((_p(C_UP), _p(cy)), "Starting tier", font=_f(_LAB, 24), fill=DIM, anchor="lm")

        # season-end reward (right column)
        if t["season"]:
            lines = _wrap(d, t["season"], _f(_LAB, 24), 340)
            ly = cy - (len(lines) - 1) * 15
            for ln in lines:
                d.text((_p(C_SEA), _p(ly)), ln, font=_f(_LAB, 24), fill=(200, 210, 235, 255), anchor="lm")
                ly += 30
        else:
            d.text((_p(C_SEA), _p(cy)), "-", font=_f(_LAB, 24), fill=DIM, anchor="lm")

    # bottom panel: current tier, stars, next tier, season end
    py0, py1 = 690, 800
    d.rounded_rectangle([_p(40), _p(py0), _p(W - 40), _p(py1)], radius=_p(18),
                        fill=(255, 255, 255, 20), outline=color + (150,), width=_p(2))

    _star(d, 84, 735, 18, (255, 214, 102, 255))
    d.text((_p(110), _p(735)), f"{stars}", font=_f(_NUM, 58), fill=WHITE, anchor="lm")
    sw = d.textlength(f"{stars}", font=_f(_NUM, 58)) / ri.S
    d.text((_p(110 + sw + 10), _p(742)), "STARS", font=_f(_LAB, 24), fill=GREY, anchor="lm")

    d.text((_p(300), _p(722)), f"YOU ARE {tier.upper()}", font=_f(_NAME, 26), fill=color + (255,), anchor="lm")
    if next_name is not None and next_min is not None:
        cur_min = next(t["min"] for t in tiers if t["name"] == tier)
        span = max(1, next_min - cur_min)
        frac = max(0.0, min(1.0, (max(stars, 0) - cur_min) / span))
        bx0, bx1, by, bh = 300, 760, 752, 16
        d.rounded_rectangle([_p(bx0), _p(by), _p(bx1), _p(by + bh)], radius=_p(8), fill=(255, 255, 255, 30))
        d.rounded_rectangle([_p(bx0), _p(by), _p(bx0 + max(frac, 0.03) * (bx1 - bx0)), _p(by + bh)],
                            radius=_p(8), fill=_mix(color, (255, 255, 255), 0.15) + (255,))
        d.text((_p(300), _p(780)),
               f"{max(next_needed or 0, 0)} more stars to {next_name.upper()}  ({next_min} needed)",
               font=_f(_LAB, 22), fill=GREY, anchor="lm")
    else:
        d.text((_p(300), _p(760)), "MAX TIER REACHED", font=_f(_LAB, 26), fill=color + (255,), anchor="lm")

    if season_active and season_end:
        left = max(0, int(season_end - now))
        days, hours = left // 86400, (left % 86400) // 3600
        d.text((_p(1120), _p(722)), "SEASON ENDS IN", font=_f(_LAB, 22), fill=GREY, anchor="rm")
        d.text((_p(1120), _p(756)), f"{days}D {hours}H", font=_f(_NUM, 46), fill=WHITE, anchor="rm")
    else:
        d.text((_p(1120), _p(745)), "NO SEASON RUNNING", font=_f(_LAB, 24), fill=GREY, anchor="rm")

    d.text((_p(W // 2), _p(H - 36)),
           "Season reward = the tier you finish the season in (with more than 0 stars)",
           font=_f(_LAB, 20), fill=DIM, anchor="mm")

    out = img.convert("RGB").resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
