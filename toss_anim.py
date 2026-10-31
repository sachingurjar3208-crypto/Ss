"""
Animated coin toss GIF, made from the bot's own toss images (no external assets).

    toss_gif_bytes("Head") / toss_gif_bytes("Tail")  -> GIF bytes (cached after the first call)

Frames:
  1. the umpire's hand, coin resting on it
  2. the coin flies up spinning (squashes as it turns, light / dark side) and comes down
  3. cut to toss_heads.png / toss_tails.png with a small zoom-in pop
  4. a dark band slides up with HEADS / TAILS and stays

Only Pillow is needed. The first call takes ~1 s, then the result is cached in memory.
"""
import io
import math
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

_DIR  = os.path.dirname(__file__)
SIZE  = 480                       # output is SIZE x SIZE

# Where the original static coin + motion lines sit (in SIZE=480 space) -> painted white.
_ERASE_BOX = (108, 14, 152, 113)
_HAND      = (137, 121)           # coin resting point on the palm
_PEAK_RISE = 86                   # how high the coin flies (px)
_COIN_R    = 14                   # coin radius (px)

_FLIGHT_FRAMES = 20
_FRAME_MS      = 45
_HOLD_MS       = 4000             # last frame stays this long

_GOLD      = (232, 178, 46)
_GOLD_DARK = (176, 120, 20)
_RIM       = (120, 80, 10)
_BAND      = (18, 18, 20)
_BAND_TEXT = (240, 190, 60)


def _load(name: str) -> Image.Image:
    return Image.open(os.path.join(_DIR, name)).convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)


def _font(size: int):
    for f in ("display_bold.ttf", "label.ttf", "name_font.ttf"):
        p = os.path.join(_DIR, f)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _coin(img: Image.Image, cx: float, cy: float, spin: float) -> None:
    """Draw the spinning coin (4x supersampled for smooth edges)."""
    s = 4
    r = _COIN_R
    w = max(0.12, abs(math.cos(spin)))            # width shrinks as the coin turns edge-on
    front = math.cos(spin) >= 0
    box = int(r * 2 + 6)
    layer = Image.new("RGBA", (box * s, box * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    c = box * s / 2
    rx, ry = r * s * w, r * s
    d.ellipse((c - rx, c - ry, c + rx, c + ry), fill=_GOLD if front else _GOLD_DARK,
              outline=_RIM, width=max(2, int(1.5 * s)))
    if w > 0.45:                                   # inner ring only when the face is visible
        ir = 0.7
        d.ellipse((c - rx * ir, c - ry * ir, c + rx * ir, c + ry * ir),
                  outline=_RIM if front else (140, 95, 15), width=max(1, s))
    layer = layer.resize((box, box), Image.LANCZOS)
    img.paste(layer, (int(cx - box / 2), int(cy - box / 2)), layer)


def _streaks(img: Image.Image, cx: float, cy: float, up: bool) -> None:
    """Two short motion lines behind the coin."""
    d = ImageDraw.Draw(img)
    dy = 1 if up else -1
    for off in (-5, 5):
        y0 = cy + dy * (_COIN_R + 4)
        d.line((cx + off, y0, cx + off, y0 + dy * 12), fill=(150, 150, 150), width=1)


def _band(img: Image.Image, text: str, progress: float) -> Image.Image:
    """Dark band with HEADS / TAILS sliding up from the bottom (progress 0..1)."""
    out = img.copy()
    h = 74
    top = int(SIZE - h * progress)
    d = ImageDraw.Draw(out)
    d.rectangle((0, top, SIZE, SIZE), fill=_BAND)
    d.rectangle((0, top, SIZE, top + 3), fill=_BAND_TEXT)
    f = _font(40)
    spaced = " ".join(text)
    tw = d.textlength(spaced, font=f)
    d.text(((SIZE - tw) / 2, top + 16), spaced, font=f, fill=_BAND_TEXT)
    return out


def _zoom(img: Image.Image, scale: float) -> Image.Image:
    if scale <= 1.0:
        return img
    w = int(SIZE * scale)
    big = img.resize((w, w), Image.LANCZOS)
    o = (w - SIZE) // 2
    return big.crop((o, o, o + SIZE, o + SIZE))


TOSS_ANIMATION_ENABLED = True     # False = old static toss image

_REVEAL_MS: dict[bool, int] = {}


def reveal_seconds(result: str) -> float:
    """How long the GIF runs before the HEADS / TAILS band is fully up (text edit waits this long)."""
    head = str(result).lower().startswith("h")
    if head not in _REVEAL_MS:
        toss_gif_bytes(result)
    return _REVEAL_MS.get(head, 2200) / 1000.0


@lru_cache(maxsize=2)
def toss_gif_bytes(result: str) -> bytes:
    """result: 'Head' or 'Tail'."""
    head = str(result).lower().startswith("h")
    base = _load("toss_image.png")
    ImageDraw.Draw(base).rectangle(_ERASE_BOX, fill=(255, 255, 255))
    final = _load("toss_heads.png" if head else "toss_tails.png")

    frames: list[Image.Image] = []
    durations: list[int] = []

    def add(im, ms):
        frames.append(im)
        durations.append(ms)

    # 1) coin on the palm
    f = base.copy(); _coin(f, *_HAND, 0.0); add(f, 350)

    # 2) flight: parabola up and down, spinning
    # gold face = HEADS, darker face = TAILS: land on the right side
    spins = 8 * math.pi + (0 if head else math.pi)
    land = 0.0 if head else math.pi
    for i in range(1, _FLIGHT_FRAMES + 1):
        t = i / _FLIGHT_FRAMES
        x = _HAND[0] + 4 * math.sin(t * math.pi)
        y = _HAND[1] - _PEAK_RISE * 4 * t * (1 - t)
        f = base.copy()
        if 0.08 < t < 0.92:
            _streaks(f, x, y, up=t < 0.5)
        _coin(f, x, y, spins * t)
        add(f, _FRAME_MS + (25 if 0.4 < t < 0.6 else 0))   # hangs a bit at the top

    # 3) caught -> result picture with a small zoom pop
    f = base.copy(); _coin(f, *_HAND, land); add(f, 220)
    for sc in (1.08, 1.04, 1.0):
        add(_zoom(final, sc), 70)

    # 4) HEADS / TAILS band slides up and stays
    word = "HEADS" if head else "TAILS"
    for p in (0.35, 0.7, 1.0):
        add(_band(final, word, p), 60)
    durations[-1] = _HOLD_MS
    _REVEAL_MS[head] = sum(durations[:-1])

    # One shared palette keeps the gold steady and the file small.
    pal_src = Image.new("RGB", (SIZE, SIZE * 2))
    pal_src.paste(frames[1], (0, 0)); pal_src.paste(frames[-1], (0, SIZE))
    pal = pal_src.quantize(colors=128, method=Image.MEDIANCUT)
    q = [fr.quantize(palette=pal, dither=Image.NONE) for fr in frames]

    buf = io.BytesIO()
    q[0].save(buf, format="GIF", save_all=True, append_images=q[1:],
              duration=durations, optimize=True, disposal=1)
    return buf.getvalue()


if __name__ == "__main__":   # quick local preview: python toss_anim.py
    for r in ("Head", "Tail"):
        data = toss_gif_bytes(r)
        with open(f"toss_preview_{r.lower()}.gif", "wb") as fh:
            fh.write(data)
        print(r, len(data) // 1024, "KB")
