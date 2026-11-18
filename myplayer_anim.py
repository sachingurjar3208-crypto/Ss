"""Animated My Player card (GIF): a gold leather ball spins behind the player, the sunburst rays turn,
a shine sweeps across, a light runs round the gold frame and stars twinkle. The player and the stats
stay still.

Built ONCE per change (create / upgrade / photo / rename / position) and saved next to the PNG, then
only re-sent. ~3-5 s of CPU per build.
"""
from __future__ import annotations

import io
import math
import threading

import numpy as np
from PIL import Image, ImageFilter

OUT_W = 420                       # GIF width (height follows the card)
FPS = 15
FRAMES = 36                       # 2.4 s loop
_LOCK = threading.Lock()          # one build at a time (keeps CPU / memory low)

GOLD = np.array([1.0, 0.80, 0.42], np.float32)


# ── 3D gold leather cricket ball ─────────────────────────────────────────
def _rot(ax, a):
    ax = np.array(ax, float); ax /= np.linalg.norm(ax); x, y, z = ax
    c, s = math.cos(a), math.sin(a); C = 1 - c
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def _grain():
    rng = np.random.default_rng(3)
    g = Image.fromarray(((rng.normal(0, 1, (128, 256)) * 40 + 128).clip(0, 255)).astype(np.uint8))
    g = g.filter(ImageFilter.GaussianBlur(1.2))
    return (np.asarray(g).astype(np.float32) - 128) / 40 * 2.2


_GRAIN = None


def _ball(R: int, phase: float) -> np.ndarray:
    global _GRAIN
    if _GRAIN is None:
        _GRAIN = _grain()
    n = 2 * R
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    x = (xx + 0.5 - R) / R; y = (yy + 0.5 - R) / R; d2 = x * x + y * y
    z = np.sqrt(np.clip(1 - d2, 0, 1))
    N = np.stack([x, -y, z], -1)
    M = _rot([0.25, 1, 0.15], 2 * math.pi * phase) @ _rot([1, 0, 0], math.radians(28))
    P = N @ M.astype(np.float32)
    sz = P[..., 2]
    lon = np.arctan2(P[..., 1], P[..., 0])
    seam = np.exp(-(sz / 0.035) ** 2)
    stitches = sum(np.exp(-((sz - off) / 0.018) ** 2) * (0.5 + 0.5 * np.cos(lon * 60)) ** 4 for off in (-0.075, 0.075))
    qs = np.exp(-(P[..., 0] / 0.012) ** 2) * 0.5
    lat = np.arcsin(np.clip(P[..., 1], -1, 1))
    gi = ((lat / math.pi + 0.5) * 127).astype(int).clip(0, 127)
    gj = ((lon / (2 * math.pi) + 0.5) * 255).astype(int).clip(0, 255)
    grain = _GRAIN[gi, gj] * 0.025
    L = np.array([-0.45, 0.55, 0.70], np.float32); L /= np.linalg.norm(L)
    lam = np.clip((N * L).sum(-1), 0, 1)
    Hh = L + np.array([0, 0, 1], np.float32); Hh /= np.linalg.norm(Hh)
    spec = np.clip((N * Hh).sum(-1), 0, 1) ** 70
    rim = (1 - z) ** 3
    gold = np.array([0.95, 0.70, 0.24], np.float32); dark = np.array([0.22, 0.12, 0.02], np.float32)
    light = np.array([1.0, 0.93, 0.70], np.float32)
    shade = np.clip(0.10 + 0.95 * lam ** 1.3 + grain, 0, 1.2)[..., None]
    col = dark * (1 - shade) + gold * shade
    ridge = np.exp(-((sz - 0.03) / 0.02) ** 2)
    col = col * (1 - 0.6 * seam[..., None] - 0.35 * qs[..., None]) + light * 0.35 * (ridge * lam)[..., None]
    col += light * 0.85 * (stitches * lam)[..., None] + 0.25 * stitches[..., None] * gold
    col += light * spec[..., None] * 0.9 + np.array([1, .8, .4], np.float32) * rim[..., None] * 0.35
    a = np.clip((1 - np.sqrt(d2)) * R, 0, 1) * (d2 <= 1)
    return np.dstack([np.clip(col, 0, 1), a]).astype(np.float32)


def _star(img, x, y, s, k, W, H):
    if k <= 0:
        return
    x0, x1 = int(max(0, x - s * 4)), int(min(W, x + s * 4)); y0, y1 = int(max(0, y - s * 4)), int(min(H, y + s * 4))
    X, Y = np.meshgrid(np.arange(x0, x1) - x, np.arange(y0, y1) - y)
    g = (np.exp(-(X ** 2 + Y ** 2) / (2 * (s * .45) ** 2))
         + 0.9 * np.exp(-np.abs(X) / (s * .18)) * np.exp(-np.abs(Y) / (s * 1.6))
         + 0.9 * np.exp(-np.abs(Y) / (s * .18)) * np.exp(-np.abs(X) / (s * 1.6)))
    img[y0:y1, x0:x1] += k * g[..., None] * np.array([1, .93, .75], np.float32)


def _arr(png_bytes, W, H):
    im = Image.open(io.BytesIO(png_bytes) if isinstance(png_bytes, (bytes, bytearray)) else png_bytes)
    return np.asarray(im.convert("RGBA").resize((W, H), Image.LANCZOS)).astype(np.float32) / 255


def build_gif(card_row, fg_image: Image.Image, role) -> bytes:
    """Return the animated card as GIF bytes. Blocking: run it in a thread."""
    import card_full
    with _LOCK:
        fg = fg_image.convert("RGBA")
        blank = Image.new("RGBA", fg.size, (0, 0, 0, 0))
        mag = Image.new("RGBA", fg.size, (255, 0, 255, 255)); mag.putalpha(fg.getchannel("A"))
        full_w = card_full.OUT_W
        W = OUT_W; H = int(round(card_full.OUT_H * OUT_W / full_w))
        a = _arr(card_full.build(card_row, fg, role), W, H)       # with the player
        b = _arr(card_full.build(card_row, blank, role), W, H)    # without the player
        m = _arr(card_full.build(card_row, mag, role), W, H)      # player in magenta = exact cut-out
        del blank, mag

        v = np.array([1, 0, 1], np.float32) - b[..., :3]
        player = np.clip(((m[..., :3] - b[..., :3]) * v).sum(-1) / np.maximum((v * v).sum(-1), 1e-4), 0, 1)
        player = np.where(player > 0.97, 1, player).astype(np.float32)
        glowadd = np.clip(a[..., :3] - b[..., :3], 0, 1) * (1 - player[..., None])
        alpha = a[..., 3]
        base = b[..., :3]; playerrgb = a[..., :3]
        del m

        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        cx, cy = W * 0.5, H * 0.31
        theta = np.arctan2(yy - cy, xx - cx); r = np.hypot(xx - cx, yy - cy)
        top = np.clip((H * 0.70 - yy) / (H * 0.05), 0, 1)
        inner = ((xx > W * 0.035) & (xx < W * 0.965) & (yy > H * 0.025)).astype(np.float32)
        artzone = top * inner
        bgzone = artzone * (1 - player)
        lum = base.mean(-1)
        BR = int(W * 0.34); BX, BY = int(cx), int(H * 0.31)
        border = ((xx < W * 0.03) | (xx > W * 0.97) | (yy < H * 0.022) | (yy > H * 0.978)).astype(np.float32)
        per = 2 * (W + H)
        sarc = np.where(yy < H * 0.02, xx, np.where(xx > W * 0.98, W + yy,
                        np.where(yy > H * 0.98, W + H + (W - xx), 2 * W + H + (H - yy))))
        rng = np.random.default_rng(7)
        sp = [(rng.uniform(W * .08, W * .92), rng.uniform(H * .06, H * .66), rng.uniform(0, 1), rng.uniform(2.5, 5.5))
              for _ in range(26)]
        sp = [s for s in sp if player[int(s[1]), int(s[0])] < 0.2]
        halo = (np.exp(-(r / (BR * 1.35)) ** 2) * 0.22)[..., None] * GOLD * artzone[..., None]

        frames = []
        for i in range(FRAMES):
            t = i / FRAMES
            out = base.copy()
            rays = (0.5 + 0.5 * np.cos(12 * theta - 2 * math.pi * t)) ** 6
            out += (bgzone * rays * (0.10 + 0.55 * np.clip(lum - 0.12, 0, 1)))[..., None] * GOLD
            glow = np.exp(-(r / (W * 0.42)) ** 2) * (0.06 + 0.05 * math.sin(2 * math.pi * t))
            out += (top * (1 - player) * glow)[..., None] * GOLD
            out += halo
            bl = _ball(BR, t)
            y0, x0 = BY - BR, BX - BR
            ba = bl[..., 3:4] * artzone[y0:y0 + 2 * BR, x0:x0 + 2 * BR, None]
            reg = out[y0:y0 + 2 * BR, x0:x0 + 2 * BR]
            out[y0:y0 + 2 * BR, x0:x0 + 2 * BR] = reg * (1 - ba) + bl[..., :3] * ba
            out = out + glowadd * 0.8
            out = out * (1 - player[..., None]) + playerrgb * player[..., None]
            d = (xx / W * 0.55 + yy / H * 0.45) - (t * 1.6 - 0.3)
            band = np.exp(-(d / 0.022) ** 2) * 0.42 + np.exp(-(d / 0.07) ** 2) * 0.07
            out += ((1 - player * 0.85) * inner * band)[..., None] * np.array([1, .95, .82], np.float32)
            dd = np.abs(sarc - t * per); dd = np.minimum(dd, per - dd)
            out += (border * np.exp(-(dd / (W * 0.12)) ** 2) * 0.9)[..., None] * np.array([1, .9, .6], np.float32)
            for (x, y, ph, s) in sp:
                _star(out, x, y, s, max(0, math.sin(2 * math.pi * (t + ph))) ** 8 * 0.9, W, H)
            frames.append((np.clip(out, 0, 1) * 255).astype(np.uint8))

        # One shared 255-colour palette + 1 transparent index for the cut corners.
        sample = Image.fromarray(np.concatenate(frames[::6], axis=0), "RGB")
        pal = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        hole = alpha < 0.5
        gif = []
        for f in frames:
            q = Image.fromarray(f, "RGB").quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
            qa = np.asarray(q).copy(); qa[hole] = 255
            p = Image.fromarray(qa, "P"); p.putpalette(pal.getpalette()[:765] + [0, 0, 0])
            gif.append(p)
        buf = io.BytesIO()
        gif[0].save(buf, "GIF", save_all=True, append_images=gif[1:], duration=int(1000 / FPS),
                    loop=0, transparency=255, disposal=2, optimize=False)
        return buf.getvalue()
