"""Cricket Star end-of-match SUMMARY card (1536x1024), drawn on summary_base.png.

    png_bytes = generate_summary_card({
        "match_id": 194897, "venue": "National Stadium, Karachi",
        "team1": {"name": ..., "runs": 207, "wickets": 8, "overs": "20.0", "max_overs": "20",
                  "batters": [(name, runs, balls, not_out), ...],      # that team's top batters
                  "bowlers": [(name, wickets, runs, overs), ...]},     # that team's top bowlers
        "team2": {... same ...},
        "result": "TEAM X WON BY 1 RUN", "potm": "Jacques Kallis",
    })
team1 = batted first (blue section on top), team2 = batted second (red section).
Themes (Halloween / Diwali / Christmas): generate_summary_card(data, style="halloween", bg_bytes=<owner image>)
draws the SAME layout on the owner's own 1536x1024 image with a themed TEXT DESIGN (colours, outline, glow).
Without a style / image nothing changes: the normal blue summary is drawn exactly as before.
Fonts are the bot's own: name_font.ttf (Montserrat ExtraBold), display_bold.ttf (Jersey M54), label.ttf (Barlow Condensed).
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_HERE = Path(__file__).parent
_BASE = _HERE / "summary_base.png"
_NAME, _NUM, _LAB = _HERE / "name_font.ttf", _HERE / "display_bold.ttf", _HERE / "label.ttf"

WHITE = (255, 255, 255, 255)
GOLD = (255, 214, 102, 255)
SOFT = (200, 220, 255, 255)

# Row geometry measured on the template.  (top, bottom) of each of the 4 rows per section.
_ROWS_1 = [(262, 301), (308, 349), (357, 397), (405, 448)]
_ROWS_2 = [(546, 586), (593, 634), (641, 682), (689, 733)]
_LEFT_X = [172, 146, 118, 98]             # slanted left edge of the left name bars, per row
_L_NAME_END = 612
_R_NAME_X, _R_NAME_END = 832, 1310
_CELLS_L = [(635, 711), (717, 794)]      # left stat cells
_CELLS_R = [(1332, 1418), (1424, 1514)]  # right stat cells

# ── Themed text designs (colours, outline, glow). "default" = the normal blue summary. ──────────
# name = player / team names, num = numbers, sub = small labels, potm = Player of the Match line,
# result = result line, stroke = dark outline that keeps text readable on ANY background image,
# glow = soft coloured light behind the text (None = off), title/foot = top-bar and footer texts.
STYLES = {
    "halloween": {
        "label": "Halloween", "title": "HALLOWEEN SUMMARY", "foot": "CRICKET STAR  -  HAPPY HALLOWEEN",
        "name": (255, 244, 226, 255), "num": (255, 142, 32, 255), "sub": (255, 196, 128, 255),
        "potm": (160, 255, 90, 255), "result": (255, 142, 32, 255), "foot_fill": (255, 142, 32, 255),
        "stroke": (28, 6, 46, 255), "stroke_w": 3, "glow": (255, 106, 10, 235), "glow_r": 7,
        "shadow": (0, 0, 0, 210),
    },
    "diwali": {
        "label": "Diwali", "title": "DIWALI SUMMARY", "foot": "CRICKET STAR  -  HAPPY DIWALI",
        "name": (255, 248, 232, 255), "num": (255, 200, 70, 255), "sub": (255, 222, 156, 255),
        "potm": (255, 128, 64, 255), "result": (255, 200, 70, 255), "foot_fill": (255, 200, 70, 255),
        "stroke": (72, 20, 2, 255), "stroke_w": 3, "glow": (255, 164, 26, 235), "glow_r": 7,
        "shadow": (0, 0, 0, 200),
    },
    "christmas": {
        "label": "Christmas", "title": "CHRISTMAS SUMMARY", "foot": "CRICKET STAR  -  MERRY CHRISTMAS",
        "name": (255, 255, 255, 255), "num": (255, 96, 96, 255), "sub": (190, 255, 205, 255),
        "potm": (255, 226, 122, 255), "result": (255, 226, 122, 255), "foot_fill": (190, 255, 205, 255),
        "stroke": (8, 48, 24, 255), "stroke_w": 3, "glow": (60, 220, 110, 225), "glow_r": 7,
        "shadow": (0, 0, 0, 200),
    },
}
STYLE_NAMES = tuple(STYLES)
SIZE = (1536, 1024)


def prepare_theme_image(raw: bytes) -> bytes:
    """Owner upload -> stored theme image: cover-cropped to 1536x1024, saved as high-quality JPEG.
    Raises ValueError with a readable message when the file is not a usable image."""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise ValueError("I could not read that file as an image (use PNG or JPG).")
    im = im.convert("RGB")
    w, h = im.size
    if w < 600 or h < 400:
        raise ValueError("That image is too small. Use 1536x1024 (3:2).")
    scale = max(SIZE[0] / w, SIZE[1] / h)
    nw, nh = max(SIZE[0], round(w * scale)), max(SIZE[1], round(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - SIZE[0]) // 2, (nh - SIZE[1]) // 2
    im = im.crop((x, y, x + SIZE[0], y + SIZE[1]))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=94, subsampling=0)
    return buf.getvalue()


class _Paint:
    """Draws text for one summary card. Normal look: straight onto the card (same as before).
    Themed look: outline + soft glow + shadow, so text stays readable on any background."""

    def __init__(self, im: Image.Image, st: dict | None):
        self.im, self.st = im, st
        if st:
            self.text_layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
            self.glow_layer = Image.new("RGBA", im.size, (0, 0, 0, 0)) if st.get("glow") else None
            self.d = ImageDraw.Draw(self.text_layer, "RGBA")
            self.gd = ImageDraw.Draw(self.glow_layer, "RGBA") if self.glow_layer else None
        else:
            self.d = ImageDraw.Draw(im, "RGBA")
            self.gd = self.glow_layer = self.text_layer = None

    def text(self, xy, text, font, fill, anchor="lm", shadow=True):
        st = self.st
        if not st:
            if shadow:
                self.d.text((xy[0] + 2, xy[1] + 2), text, font=font, fill=(0, 0, 0, 170), anchor=anchor)
            self.d.text(xy, text, font=font, fill=fill, anchor=anchor)
            return
        sw = int(st.get("stroke_w", 3))
        if self.gd is not None:
            self.gd.text(xy, text, font=font, fill=st["glow"], anchor=anchor, stroke_width=sw + 2, stroke_fill=st["glow"])
        if shadow:
            self.d.text((xy[0] + 3, xy[1] + 3), text, font=font, fill=st["shadow"], anchor=anchor,
                        stroke_width=sw, stroke_fill=st["shadow"])
        self.d.text(xy, text, font=font, fill=fill, anchor=anchor, stroke_width=sw, stroke_fill=st["stroke"])

    def layers(self) -> tuple[Image.Image | None, Image.Image]:
        """(blurred glow layer or None, text layer) of a THEMED card. The animated summary keeps them apart
        so it can flicker the glow and let bats / embers fly BEHIND the text."""
        glow = None
        if self.glow_layer is not None:
            glow = self.glow_layer.filter(ImageFilter.GaussianBlur(int(self.st.get("glow_r", 7))))
        return glow, self.text_layer

    def finish(self) -> Image.Image:
        if not self.st:
            return self.im
        glow, text = self.layers()
        if glow is not None:
            self.im.alpha_composite(glow)
        self.im.alpha_composite(text)
        return self.im


def _col(st: dict | None, key: str, default):
    return st[key] if st else default


def _f(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _fit(d, text, path, size, maxw, min_size=14):
    f = _f(path, size)
    while d.textlength(text, font=f) > maxw and size > min_size:
        size -= 2
        f = _f(path, size)
    return f


def _num(P, cx, cy, text, size=36, fill=WHITE, maxw=70):
    """Value centred in a cell. Jersey lacks '-', '/', '*' so those use Montserrat."""
    d = P.d
    while True:
        fj, fo = _f(_NUM, size), _f(_NAME, int(size * 0.62))
        parts = [(c, fj if (c.isdigit() or c == ".") else fo) for c in text]
        w = sum(d.textlength(c, font=f) for c, f in parts)
        if w <= maxw or size <= 18:
            break
        size -= 2
    x = cx - w / 2
    for c, f in parts:
        P.text((x, cy), c, f, fill, anchor="lm", shadow=P.st is not None)
        x += d.textlength(c, font=f)


def _row_text(P, rows, entries, left: bool, kind: str):
    st, d = P.st, P.d
    for i, (top, bot) in enumerate(rows):
        if i >= len(entries):
            break
        cy = (top + bot) // 2
        e = entries[i]
        if kind == "bat":
            name, a, b = e[0], str(e[1]) + ("*" if len(e) > 3 and e[3] else ""), str(e[2])
        else:
            name, a, b = e[0], f"{e[1]}-{e[2]}", str(e[3])
        x0 = (_LEFT_X[i] + 34) if left else _R_NAME_X
        end = _L_NAME_END if left else _R_NAME_END
        f = _fit(d, name.upper(), _NAME, 27, end - x0 - 8)
        P.text((x0, cy), name.upper(), f, _col(st, "name", WHITE))
        cells = _CELLS_L if left else _CELLS_R
        _num(P, (cells[0][0] + cells[0][1]) // 2, cy - 1, a, 36, _col(st, "num", WHITE), cells[0][1] - cells[0][0] - 8)
        _num(P, (cells[1][0] + cells[1][1]) // 2, cy - 1, b, 36, _col(st, "sub", SOFT), cells[1][1] - cells[1][0] - 8)


def _header(P, team: dict, cy: int):
    st, d = P.st, P.d
    name = (team.get("name") or "TEAM").upper()
    f = _fit(d, name, _NAME, 40, 880)
    P.text((215, cy), name, f, _col(st, "name", WHITE))
    score = f"{team.get('runs', 0)}/{team.get('wickets', 0)}"
    _num(P, 1410, cy - 2, score, 58, _col(st, "num", WHITE), 200)
    P.text((1250, cy - 14), "OVERS", _f(_LAB, 22), _col(st, "sub", SOFT), anchor="lm")
    P.text((1250, cy + 10), f"{team.get('overs', '0.0')}", _f(_NUM, 34), _col(st, "name", WHITE), anchor="lm")


def _draw(data: dict, style: str | None, bg_bytes: bytes | None):
    """Draws every text of the card. Returns (st, im, P): st is None for the normal blue card."""
    st = STYLES.get(style or "")
    im = None
    if st and bg_bytes:
        try:
            im = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
            if im.size != SIZE:
                im = im.resize(SIZE, Image.LANCZOS)
        except Exception:
            im = None
    if im is None:
        st = None
        im = Image.open(_BASE).convert("RGBA")
    P = _Paint(im, st)
    d = P.d
    t1, t2 = data["team1"], data["team2"]

    # top bar
    title = st["title"] if st else "SUMMARY"
    P.text((430, 58), title, _fit(d, title, _NAME, 44, 360), _col(st, "name", WHITE), anchor="mm")
    venue = (data.get("venue") or "").upper()
    if venue:
        P.text((430, 98), venue, _fit(d, venue, _LAB, 30, 340), _col(st, "sub", SOFT), anchor="mm")
    mid = f"MATCH #{data.get('match_id', '')}"
    P.text((1105, 58), mid, _fit(d, mid, _NAME, 44, 330), _col(st, "name", WHITE), anchor="mm")
    mo = str(t1.get("max_overs", "20"))
    fmt = f"{mo} {'OVER' if mo == '1' else 'OVERS'} MATCH"
    P.text((1105, 98), fmt, _f(_LAB, 30), _col(st, "sub", SOFT), anchor="mm")

    # section 1 (team1 batting, blue) + section 2 (team2 batting, red)
    _header(P, t1, 224)
    _row_text(P, _ROWS_1, t1.get("batters", []), True, "bat")
    _row_text(P, _ROWS_1, t2.get("bowlers", []), False, "bowl")
    _header(P, t2, 510)
    _row_text(P, _ROWS_2, t2.get("batters", []), True, "bat")
    _row_text(P, _ROWS_2, t1.get("bowlers", []), False, "bowl")

    # result bar
    res = (data.get("result") or "").upper()
    P.text((768, 812), res, _fit(d, res, _NAME, 46, 1250), _col(st, "result", WHITE), anchor="mm")
    potm = data.get("potm")
    if potm:
        txt = f"PLAYER OF THE MATCH:  {potm.upper()}"
        P.text((768, 856), txt, _fit(d, txt, _LAB, 32, 1100), _col(st, "potm", GOLD), anchor="mm")

    # footer
    if st:
        P.text((768, 935), st["foot"], _f(_LAB, 34), st["foot_fill"], anchor="mm")
    return st, im, P


def generate_summary_layers(data: dict, style: str, bg_bytes: bytes):
    """For the animated summary: (background RGBA, blurred glow layer or None, text layer), all 1536x1024.
    Returns None when the style / image is not usable (the caller then sends the normal static card)."""
    st, im, P = _draw(data, style, bg_bytes)
    if not st:
        return None
    glow, text = P.layers()
    return im, glow, text


def generate_summary_card(data: dict, style: str | None = None, bg_bytes: bytes | None = None) -> bytes:
    """style = "halloween" | "diwali" | "christmas" | None.  bg_bytes = the owner's theme image.
    A style without an image (or an image that can't be read) falls back to the normal card."""
    st, im, P = _draw(data, style, bg_bytes)
    if st:
        im = P.finish()
    else:
        # cover the blurry area of the template with a clean dark band
        band = Image.new("RGBA", (1536, 70), (2, 8, 24, 235))
        im.alpha_composite(band, (0, 900))
        d = ImageDraw.Draw(im, "RGBA")
        d.text((768, 935), "CRICKET STAR", font=_f(_LAB, 34), fill=(140, 180, 255, 255), anchor="mm")

    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()
