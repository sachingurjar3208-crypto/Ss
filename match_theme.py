"""One colour theme for every image of a match, taken from the players' rcsb summary theme.

The theme is picked ONCE when the first themed image is made (the challenger's turned-on rcsb theme,
else the opponent's) and then used for: batter / bowler stat banners, the end-of-over image,
both scorecards, the summary card and the Player of the Match embed. No theme = the normal look.

    style, img = theme_for(game)        # ("winter", jpeg bytes) or (None, None)
    pal = palette(style)                # colours for that theme (None = normal look)
    bg = backdrop(img, (w, h))          # the theme image, cropped, blurred and darkened (RGB)
"""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageEnhance, ImageFilter

# Colours per rcsb theme. bat / bowl = the two accents (batting side, bowling side),
# hi = highlight (top scorer, wickets), text / sub = main and secondary text, tint = overlay colour.
PALETTES = {
    "winter": dict(bat=(90, 200, 255), bowl=(185, 225, 255), hi=(255, 222, 130), text=(245, 252, 255),
                   sub=(170, 215, 245), tint=(6, 22, 58), line=(120, 210, 255), embed=0x5AC8FF),
    "halloween": dict(bat=(255, 142, 32), bowl=(170, 110, 255), hi=(160, 255, 90), text=(255, 244, 226),
                      sub=(255, 196, 128), tint=(28, 6, 46), line=(255, 142, 32), embed=0xFF8E20),
    "diwali": dict(bat=(255, 200, 70), bowl=(255, 128, 64), hi=(255, 236, 150), text=(255, 248, 232),
                   sub=(255, 222, 156), tint=(60, 16, 6), line=(255, 200, 70), embed=0xFFC846),
    "christmas": dict(bat=(255, 96, 96), bowl=(90, 220, 130), hi=(255, 226, 122), text=(255, 255, 255),
                      sub=(190, 255, 205), tint=(8, 40, 22), line=(255, 226, 122), embed=0xFF6060),
}


def theme_for(game):
    """(style, image) for this match, picked once and remembered on the game."""
    d = game.__dict__
    if "_mtheme" not in d:
        hit = None
        try:
            import summary_theme
            order = [getattr(getattr(game, "challenger", None), "id", None),
                     getattr(getattr(game, "opponent", None), "id", None)]
            hit = summary_theme.pick_for_match([u for u in order if u and int(u) > 0])
        except Exception as e:
            print(f"[match-theme] lookup skipped: {type(e).__name__}: {e}")
        d["_mtheme"] = hit if (hit and hit[0] in PALETTES) else (None, None)
    return d["_mtheme"]


def palette(style):
    return PALETTES.get(style) if style else None


@lru_cache(maxsize=24)
def _backdrop_cached(img: bytes, size: tuple, blur: float, dark: float, tint: tuple):
    im = Image.open(io.BytesIO(img)).convert("RGB")
    w, h = size
    sc = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * sc)), max(h, round(im.height * sc))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    im = im.crop((x, y, x + w, y + h))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    im = ImageEnhance.Color(im).enhance(1.1)
    return Image.blend(im, Image.new("RGB", size, tint), dark)


def backdrop(img: bytes, size, blur: float = 6, dark: float = 0.55, tint=(6, 12, 30)) -> Image.Image:
    """Theme image cover-cropped to size, blurred and darkened so text stays readable."""
    return _backdrop_cached(img, tuple(size), blur, dark, tuple(tint)).copy()
