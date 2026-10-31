"""ANIMATED Halloween match summary (GIF) - the same card, but alive:

    rising embers, bats flying across, a spider swinging on its thread, cobwebs in the corners and a
    gentle flicker in the glow of the text.

    out = build_animated_summary(data, bg_bytes)       # (gif_bytes, "match_summary.gif") or None

Only the Halloween theme is animated. Diwali / Christmas / normal cards stay the static PNG.

DISCORD SIZE LIMIT
  A 1536x1024 GIF can get big, so the file is built in a safe way:
    1. every GIF frame only stores the pixels that changed (small frames, same look),
    2. one shared 255-colour palette for all frames (no colour flicker between frames),
    3. if the file is still bigger than MAX_BYTES it is rebuilt smaller (fewer frames, then smaller
       picture) - see _LADDER. If even the smallest try does not fit, None is returned and the bot
       sends the normal static PNG, so the match end NEVER breaks.
"""
from __future__ import annotations

import io
import math
import random

from PIL import Image, ImageChops, ImageDraw

import summary_image

W, H = summary_image.SIZE
MAX_BYTES = 3_000_000            # small file = fast upload at match end (Discord allows 10 MB)
FILENAME = "match_summary.gif"

# (frames, width) tries from the best to the lightest. Height keeps the 3:2 shape.
# Lighter than before (was 20 frames at 1536px, up to 7.4 MB) so the summary arrives quickly.
_LADDER = [(14, 1152), (12, 1024), (10, 960), (8, 896)]
_DELAY_MS = 90
_LOOP_MS = 1800                  # one animation loop; fewer frames -> longer delay, same speed

_EMBER = [(255, 120, 20), (255, 168, 40), (255, 205, 90), (255, 236, 160)]


# ── static decoration (drawn once, costs nothing in the GIF) ─────────────────

def _cobweb(size: int, flip_x: bool) -> Image.Image:
    """A corner cobweb (RGBA). Corner is the top-left; flip_x mirrors it for the top-right."""
    S = 3                                              # draw big, shrink = smooth thin lines
    n = size * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    line = (238, 232, 255, 120)
    angles = [0, 17, 34, 52, 71, 90]                   # threads from the corner
    for a in angles:
        r = math.radians(a)
        d.line([(0, 0), (n * math.cos(r), n * math.sin(r))], fill=line, width=2 * S // 2 + 1)
    rings = [0.22, 0.42, 0.62, 0.84]
    for k in rings:
        pts = []
        for a in angles:
            r = math.radians(a)
            rad = n * k * (0.93 if a not in (0, 90) else 1.0)
            pts.append((rad * math.cos(r), rad * math.sin(r)))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):   # sagging arcs between the threads
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            sag = 0.88
            d.line([(x0, y0), (mx * sag, my * sag), (x1, y1)], fill=line, width=S)
    im = im.resize((size, size), Image.LANCZOS)
    return im.transpose(Image.FLIP_LEFT_RIGHT) if flip_x else im


def _decorate(bg: Image.Image) -> None:
    """Cobwebs in the top corners + a darker edge, so the effects pop and the text stays readable."""
    web = _cobweb(230, False)
    bg.alpha_composite(web, (0, 0))
    bg.alpha_composite(_cobweb(230, True), (W - 230, 0))
    web2 = _cobweb(150, False)
    bg.alpha_composite(web2.transpose(Image.FLIP_TOP_BOTTOM), (0, H - 150))
    bg.alpha_composite(web2.transpose(Image.ROTATE_180), (W - 150, H - 150))


# ── moving effects ───────────────────────────────────────────────────────────

class _Fx:
    """Deterministic effects. Everything is a function of u = frame / frames, and every motion is a
    whole number of cycles per loop, so the GIF loops with no jump."""

    def __init__(self, seed: int = 31):
        rnd = random.Random(seed)
        self.embers = []
        for _ in range(64):
            self.embers.append(dict(
                x=rnd.uniform(30, W - 30), y=rnd.uniform(420, H + 40),     # start point (bottom half)
                span=rnd.uniform(320, 620), ph=rnd.random(), r=rnd.uniform(2.6, 6.2),
                sway=rnd.uniform(8, 34), cyc=rnd.choice((1, 1, 2)), col=rnd.randrange(4),
            ))
        self.bats = [
            dict(y=rnd.uniform(100, 135), ph=0.00, size=1.35, flap=3, dy=20, dirn=1),
            dict(y=968, ph=0.52, size=1.0, flap=3, dy=14, dirn=-1),
        ]
        self._bat_cache: dict = {}

    # embers: little glowing sparks that float up, sway, fade in and out
    def draw_embers(self, layer: Image.Image, u: float, k: float) -> None:
        d = ImageDraw.Draw(layer, "RGBA")
        for e in self.embers:
            t = (u + e["ph"]) % 1.0
            a = math.sin(math.pi * t) ** 1.3                                   # 0 at both ends = no pop
            if a < 0.04:
                continue
            x = e["x"] + e["sway"] * math.sin(2 * math.pi * (t * e["cyc"]) + e["ph"] * 6.28)
            y = e["y"] - e["span"] * t
            r = e["r"] * (1.15 - 0.35 * t)
            c = _EMBER[min(3, int(e["col"] + t * 1.6))]
            d.ellipse((x - r * 2.6, y - r * 2.6, x + r * 2.6, y + r * 2.6), fill=(*c, int(46 * a)))
            d.ellipse((x - r * 1.6, y - r * 1.6, x + r * 1.6, y + r * 1.6), fill=(*c, int(110 * a)))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 240, 190, int(255 * a)))

    # bats: dark silhouettes with a purple rim, wings flap 3 times per loop
    def _bat_sprite(self, flap_phase: float, scale: float) -> Image.Image:
        key = (round(flap_phase, 2), round(scale, 2))
        if key in self._bat_cache:
            return self._bat_cache[key]
        S = 4
        bw, bh = int(120 * scale), int(60 * scale)
        im = Image.new("RGBA", (bw * S, bh * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        cx, cy = bw * S / 2, bh * S * 0.55
        lift = math.sin(flap_phase)                       # -1 wings down ... +1 wings up
        sx = bw * S / 2
        tip_y = cy - lift * bh * S * 0.46
        mid_y = cy - lift * bh * S * 0.20
        for sgn in (-1, 1):
            pts = [(cx, cy - 6 * S * scale),
                   (cx + sgn * sx * 0.30, mid_y - 10 * S * scale),
                   (cx + sgn * sx * 0.98, tip_y),
                   (cx + sgn * sx * 0.78, tip_y + 12 * S * scale),
                   (cx + sgn * sx * 0.62, mid_y + 12 * S * scale),
                   (cx + sgn * sx * 0.42, tip_y + 20 * S * scale + 4 * S),
                   (cx + sgn * sx * 0.22, mid_y + 16 * S * scale),
                   (cx, cy + 10 * S * scale)]
            d.polygon(pts, fill=(14, 6, 24, 255), outline=(120, 70, 170, 255), width=2 * S // 2)
        d.ellipse((cx - 7 * S * scale, cy - 14 * S * scale, cx + 7 * S * scale, cy + 14 * S * scale),
                  fill=(14, 6, 24, 255), outline=(120, 70, 170, 255), width=S)
        for sgn in (-1, 1):                               # ears
            d.polygon([(cx + sgn * 2 * S * scale, cy - 12 * S * scale), (cx + sgn * 7 * S * scale, cy - 22 * S * scale),
                       (cx + sgn * 6 * S * scale, cy - 10 * S * scale)], fill=(14, 6, 24, 255))
        im = im.resize((bw, bh), Image.LANCZOS)
        self._bat_cache[key] = im
        return im

    def draw_bats(self, layer: Image.Image, u: float) -> None:
        for b in self.bats:
            t = (u + b["ph"]) % 1.0
            x = (-90 + (W + 180) * t) if b["dirn"] > 0 else (W + 90 - (W + 180) * t)
            y = b["y"] + b["dy"] * math.sin(2 * math.pi * 2 * t + b["ph"] * 5)
            sprite = self._bat_sprite(2 * math.pi * b["flap"] * t, b["size"])
            if b["dirn"] < 0:
                sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)
            layer.alpha_composite(sprite, (int(x - sprite.width / 2), int(y - sprite.height / 2)))

    # spider swinging on a thread from the top-right corner
    def draw_spider(self, layer: Image.Image, u: float) -> None:
        d = ImageDraw.Draw(layer, "RGBA")
        x0 = 1452
        len_ = 96 + 20 * math.sin(2 * math.pi * u)
        sway = 7 * math.sin(2 * math.pi * u + 1.1)
        x, y = x0 + sway, len_
        d.line([(x0, 0), (x, y)], fill=(235, 230, 255, 150), width=2)
        d.ellipse((x - 12, y - 4, x + 12, y + 20), fill=(16, 8, 26, 255), outline=(120, 70, 170, 255), width=2)
        d.ellipse((x - 7, y - 14, x + 7, y - 2), fill=(16, 8, 26, 255), outline=(120, 70, 170, 255), width=2)
        for sgn in (-1, 1):
            for i, dy in enumerate((-2, 4, 10, 16)):
                bend = 4 * math.sin(2 * math.pi * (u * 2 + i * 0.2))
                d.line([(x + sgn * 8, y + dy), (x + sgn * 20, y + dy - 8 + bend), (x + sgn * 28, y + dy + 6)],
                       fill=(16, 8, 26, 255), width=2)
        d.ellipse((x - 4, y - 11, x - 1, y - 8), fill=(255, 90, 40, 255))      # red eyes
        d.ellipse((x + 1, y - 11, x + 4, y - 8), fill=(255, 90, 40, 255))

    @staticmethod
    def flicker(u: float) -> float:
        """Glow strength 0.62 .. 1.0, like a candle (whole cycles per loop = seamless)."""
        v = 0.81 + 0.13 * math.sin(2 * math.pi * 2 * u) + 0.06 * math.sin(2 * math.pi * 7 * u + 1.3)
        return max(0.55, min(1.0, v))


_FIRE = None


def _fire_layer() -> Image.Image:
    """Soft orange light along the bottom edge (like a pumpkin / bonfire). Flickers with the glow."""
    global _FIRE
    if _FIRE is None:
        g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(g)
        for i in range(130):
            a = int(78 * (1 - i / 130) ** 2)
            d.line([(0, H - 1 - i), (W, H - 1 - i)], fill=(255, 110, 14, a))
        _FIRE = g
    return _FIRE


def _scale_alpha(layer: Image.Image, f: float) -> Image.Image:
    r, g, b, a = layer.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: int(v * f))))


# ── GIF building ─────────────────────────────────────────────────────────────

def _palette_image(frames_rgb: list[Image.Image]) -> Image.Image:
    """One 255-colour palette that fits the card AND the effect colours (index 255 = transparent)."""
    fw, fh = frames_rgb[0].size
    sw = Image.new("RGB", (fw, 60))
    d = ImageDraw.Draw(sw)
    cols = _EMBER + [(255, 240, 190), (14, 6, 24), (120, 70, 170), (235, 230, 255), (255, 90, 40)]
    for i, c in enumerate(cols):
        d.rectangle((i * 60, 0, i * 60 + 59, 59), fill=c)
    sample = Image.new("RGB", (fw // 2, fh // 2 + 30))
    sample.paste(frames_rgb[0].resize((fw // 2, fh // 2), Image.LANCZOS), (0, 30))
    sample.paste(sw.resize((fw // 2, 30)), (0, 0))
    return sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def _to_index_frames(frames_rgb: list[Image.Image], pal: Image.Image) -> list[Image.Image]:
    """RGB frames -> P frames on the shared palette. Unchanged pixels keep EXACTLY the same index
    in every frame (the still picture is dithered once), so frame-to-frame differences stay small."""
    base_rgb = frames_rgb[0]
    still = base_rgb.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
    out = []
    for fr in frames_rgb:
        diff = ImageChops.difference(fr, base_rgb).convert("L").point(lambda v: 255 if v > 2 else 0)
        near = fr.quantize(palette=pal, dither=Image.Dither.NONE)
        idx = Image.composite(near, still, diff)
        idx.putpalette(pal.getpalette())
        out.append(idx)
    return out


def _encode(index_frames: list[Image.Image], delay_ms: int) -> bytes:
    """Write the GIF with Pillow. Every frame after the first is cut down to the box that changed, and
    pixels that did not change are made transparent (index 255) - that is what keeps the file small."""
    size = index_frames[0].size
    pal = index_frames[0].getpalette()[: 255 * 3]
    pal = pal + [0, 0, 0] * (256 - len(pal) // 3)
    frames: list[Image.Image] = []
    offsets: list[tuple[int, int]] = []
    prev = None
    for im in index_frames:
        raw = Image.frombytes("L", size, im.tobytes())               # indices as a plain grey image
        if prev is None:
            frames.append(im)
            offsets.append((0, 0))
        else:
            changed = ImageChops.difference(raw, prev).point(lambda v: 255 if v else 0)
            box = changed.getbbox()
            if box is None:
                box = (0, 0, 2, 2)                                   # identical frame: keep a tiny one
                changed = Image.new("L", size, 0)
            sub = Image.composite(raw, Image.new("L", size, 255), changed).crop(box)
            p = Image.frombytes("P", sub.size, sub.tobytes())
            p.putpalette(pal)
            frames.append(p)
            offsets.append((box[0], box[1]))
        prev = raw
    return _write_gif(frames, offsets, size, pal, delay_ms)


def _write_gif(frames, offsets, size, pal, delay_ms) -> bytes:
    """Small GIF89a writer: global palette, per-frame offset, transparent index 255, disposal 1.
    The image data of every frame is encoded by Pillow's own LZW coder (fast, C code)."""
    out = io.BytesIO()
    w, h = size
    out.write(b"GIF89a" + w.to_bytes(2, "little") + h.to_bytes(2, "little"))
    out.write(bytes([0xF7, 0, 0]))                                  # global palette, 256 colours
    out.write(bytes(pal[:768]))
    out.write(b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00")       # loop forever
    for i, (fr, (ox, oy)) in enumerate(zip(frames, offsets)):
        out.write(b"\x21\xf9\x04" + bytes([0x05]) + max(2, delay_ms // 10).to_bytes(2, "little")
                  + bytes([255]) + b"\x00")                         # disposal 1 (keep) + transparent 255
        out.write(b"\x2c" + ox.to_bytes(2, "little") + oy.to_bytes(2, "little")
                  + fr.width.to_bytes(2, "little") + fr.height.to_bytes(2, "little") + b"\x00")
        out.write(_lzw_block(fr))
    out.write(b"\x3b")
    return out.getvalue()


def _lzw_block(fr: Image.Image) -> bytes:
    """LZW image data of one P frame: let Pillow write a one-frame GIF and cut the data part out of it."""
    buf = io.BytesIO()
    fr.save(buf, "GIF", optimize=False, interlace=False)
    b = buf.getvalue()
    pos = 13
    if b[10] & 0x80:
        pos += 3 * (2 << (b[10] & 7))
    while b[pos] == 0x21:                                           # skip extension blocks
        pos += 2
        while b[pos]:
            pos += b[pos] + 1
        pos += 1
    assert b[pos] == 0x2C
    flags = b[pos + 9]
    pos += 10
    if flags & 0x80:
        pos += 3 * (2 << (flags & 7))
    start = pos                                                     # LZW min code size byte + sub-blocks
    pos += 1
    while b[pos]:
        pos += b[pos] + 1
    return b[start:pos + 1]


def _build_frames(layers, n: int, width: int) -> list[Image.Image]:
    bg, glow, text = layers
    fx = _Fx()
    base = bg.copy()
    _decorate(base)
    frames = []
    for i in range(n):
        u = i / n
        fr = base.copy()
        fxl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fx.draw_embers(fxl, u, 1.0)
        fx.draw_bats(fxl, u)
        fx.draw_spider(fxl, u)
        fr.alpha_composite(fxl)
        fr.alpha_composite(_scale_alpha(_fire_layer(), fx.flicker((u + 0.37) % 1.0)))
        if glow is not None:
            fr.alpha_composite(_scale_alpha(glow, fx.flicker(u)))
        fr.alpha_composite(text)
        rgb = fr.convert("RGB")
        if width != W:
            rgb = rgb.resize((width, round(H * width / W)), Image.LANCZOS)
        frames.append(rgb)
    return frames


def build_animated_summary(data: dict, bg_bytes: bytes | None, style: str = "halloween",
                           max_bytes: int = MAX_BYTES):
    """(gif_bytes, filename) or None. None = use the normal static PNG."""
    if style != "halloween" or not bg_bytes:
        return None
    layers = summary_image.generate_summary_layers(data, style, bg_bytes)
    if layers is None:
        return None
    for n, width in _LADDER:
        frames = _build_frames(layers, n, width)
        pal = _palette_image(frames)
        delay = max(_DELAY_MS, round(_LOOP_MS / n))
        gif = _encode(_to_index_frames(frames, pal), delay)
        if len(gif) <= max_bytes:
            return gif, FILENAME
    return None
