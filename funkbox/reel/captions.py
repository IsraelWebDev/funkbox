"""Caption styles, wrapping and reveal animations.

Text is laid out ONCE from the full copy line (wrap on spaces only, never inside a word, no
hyphenation, never below 0.032 x frame height, never truncated). Reveals then show a prefix of
that fixed layout, so a typewriter frame is exactly ``text[:n]`` drawn on the final layout.
Nothing here transforms case or touches characters.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from funkbox.config import ROOT as FUNKBOX

from .schema import HEX_RE, Brand

MIN_SIZE = 0.032                     # of frame height; below this the validator errors
SAFE_TOP, SAFE_BOTTOM = 0.14, 0.22   # reels safe area
LAND_TOP, LAND_BOTTOM = 0.07, 0.12   # landscape safe area (player controls)
CAPTION_DELAY = 0.1                  # seconds after the shot starts
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
TEMPLATES = FUNKBOX / "templates"

_STYLE_KEYS = {"font", "size", "color", "box", "box_alpha", "padding", "max_width", "line_height", "align",
               "scale_in", "stroke", "stroke_color", "shadow", "accent", "cursor", "cps", "x", "y", "gap"}


class CaptionFitError(ValueError):
    """The text cannot fit at the minimum size (carries the copy id)."""


@dataclass(frozen=True)
class Style:
    font: str = "caption"                # "caption" | "display" (brand fonts)
    size: float = 0.05                   # font px / frame height
    color: str = "text"                  # brand key (primary|secondary|text) or #rrggbb
    box: str | None = "primary"
    box_alpha: float = 1.0
    padding: float = 0.6                 # em
    max_width: float = 0.82              # of frame width
    line_height: float = 1.1
    align: str = "center"                # alignment of lines inside the block
    scale_in: float = 0.15               # seconds
    stroke: float = 0.0                  # em
    stroke_color: str = "secondary"
    shadow: float = 0.0                  # em offset
    accent: str | None = None            # left accent bar colour
    cursor: bool = False
    cps: float = 28.0
    x: float | None = None               # left edge (fraction of width); None = centred
    y: float | None = None               # top (fraction of height); None = anchored
    gap: float = 0.0


def parse_style(d: dict, where: str = "style") -> Style:
    if not isinstance(d, dict):
        raise ValueError(f"{where}: must be an object")
    if any(k in d for k in ("text_transform", "uppercase", "lowercase", "capitalize", "case")):
        raise ValueError(f"{where}: styles may not transform case; write the copy in its final case")
    bad = [k for k in d if k not in _STYLE_KEYS]
    if bad:
        raise ValueError(f"{where}: unknown keys {bad}")
    s = Style(**d)
    if s.font not in ("caption", "display"):
        raise ValueError(f"{where}: font must be 'caption' or 'display'")
    if s.align not in ("left", "center"):
        raise ValueError(f"{where}: align must be left|center")
    for k in ("color", "stroke_color", "box", "accent"):
        v = getattr(s, k)
        if v is not None and v not in ("primary", "secondary", "text") and not HEX_RE.match(str(v)):
            raise ValueError(f"{where}: {k}={v!r} must be primary|secondary|text or #rrggbb")
    return s


def list_styles() -> list[str]:
    return sorted(p.stem for p in (TEMPLATES / "captions").glob("*.json"))


def load_style(name: str) -> Style:
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad caption style name {name!r}")
    p = TEMPLATES / "captions" / f"{name}.json"
    if not p.exists():
        raise ValueError(f"unknown caption style {name!r}; have {list_styles()}")
    return parse_style(json.loads(p.read_text(encoding="utf-8")), f"templates/captions/{name}.json")


# --------------------------------------------------------------------------- #
class GlyphError(ValueError):
    """The text has characters neither the chosen font nor the fallback can draw."""


@dataclass
class Fonts:
    caption: Path
    display: Path
    fallback: Path | None = None

    def path_for(self, which: str, text: str | None = None) -> Path:
        """The font that draws ``text`` in slot ``which``: the slot's font if it has every glyph, else the fallback
        if IT has every glyph. Anything else raises GlyphError naming the characters, so text is never drawn as
        empty boxes. (The choice is per text block: a block is drawn in one font.)"""
        from .. import fonts as fontsmod
        main = self.caption if which == "caption" else self.display
        if text is None:
            return main
        miss = fontsmod.missing(main, text)
        if not miss:
            return main
        if self.fallback is not None:
            if not fontsmod.missing(self.fallback, text):
                return self.fallback
            miss = [c for c in miss if c in fontsmod.missing(self.fallback, text)]
            raise GlyphError(f"neither the {which} font ({main.name}) nor the fallback ({self.fallback.name}) has "
                             f"{fontsmod.describe(miss)}; pick a font that covers them")
        raise GlyphError(f"the {which} font ({main.name}) has no glyph for {fontsmod.describe(miss)}; set "
                         f"brand.font_fallback (e.g. 'Rubik:700' for Hebrew) or use a font that covers them")

    def get(self, which: str, px: int, text: str | None = None) -> ImageFont.FreeTypeFont:
        return _font(str(self.path_for(which, text)), px)


DEFAULT_FALLBACK = "fonts/Rubik-Bold.ttf"     # bundled: Latin, Hebrew, Cyrillic


def fonts_for(brand, base_dir) -> Fonts:
    """A brand's fonts: paths or names (funkbox.fonts), plus the optional fallback."""
    from .. import fonts as fontsmod
    fb = (getattr(brand, "font_fallback", DEFAULT_FALLBACK) if not isinstance(brand, dict)
          else brand.get("font_fallback", DEFAULT_FALLBACK))
    get = (lambda k: brand[k]) if isinstance(brand, dict) else (lambda k: getattr(brand, k))
    return Fonts(fontsmod.resolve(get("font_caption"), base_dir), fontsmod.resolve(get("font_display"), base_dir),
                 fontsmod.resolve(fb, base_dir) if fb else None)


@lru_cache(maxsize=64)
def _font(path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, px)


def hex_rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def colour(key: str | None, brand: Brand) -> tuple[int, int, int] | None:
    if key is None:
        return None
    if key in ("primary", "secondary", "text"):
        return hex_rgb(getattr(brand, key))
    return hex_rgb(key)


@dataclass
class Layout:
    copy_id: str
    text: str
    style: Style
    fonts: Fonts
    brand: Brand
    px: int
    pad: int
    line_h: int
    lines: list[tuple[int, int]]          # (start, end) offsets into text; end excludes the wrap space
    widths: list[float]                   # full width of each line
    x0: int
    y0: int
    box_w: int
    box_h: int
    cap_h: int
    frame: tuple[int, int]

    @property
    def font(self) -> ImageFont.FreeTypeFont:
        return self.fonts.get(self.style.font, self.px, self.text)

    @property
    def text_w(self) -> float:
        return max(self.widths) if self.widths else 0.0

    def line_x(self, i: int) -> float:
        """Left edge of line i relative to the block (centre alignment uses the FULL widths)."""
        if self.style.align == "center":
            return self.pad + (self.text_w - self.widths[i]) / 2
        return float(self.pad)

    def reconstruct(self, drawn: list[str | None]) -> str:
        """Rebuild the copy text from the drawn line strings plus the invisible separators."""
        out = [self.text[: self.lines[0][0]]]
        last = max((i for i, s in enumerate(drawn) if s is not None), default=-1)
        for i, s in enumerate(drawn):
            if s is None:
                break
            out.append(s)
            if i < last:
                out.append(self.text[self.lines[i][1]: self.lines[i + 1][0]])
        if last == len(self.lines) - 1:
            out.append(self.text[self.lines[-1][1]:])
        return "".join(out)


def wrap(text: str, font: ImageFont.FreeTypeFont, avail: float) -> tuple[list[tuple[int, int]], bool]:
    """Greedy wrap on spaces only. Hard breaks at \\n. Returns (lines, every_word_fits)."""
    lines: list[tuple[int, int]] = []
    ok = True
    pos = 0
    for para in text.split("\n"):
        words = [(pos + m.start(), pos + m.end()) for m in re.finditer(r"[^\s]+", para)]
        if not words:
            lines.append((pos, pos))
        cur: tuple[int, int] | None = None
        for ws, we in words:
            if font.getlength(text[ws:we]) > avail:
                ok = False
            if cur is None:
                cur = (ws, we)
            elif font.getlength(text[cur[0]:we]) <= avail:
                cur = (cur[0], we)
            else:
                lines.append(cur)
                cur = (ws, we)
        if cur is not None:
            lines.append(cur)
        pos += len(para) + 1
    return lines, ok


def layout_block(copy_id: str, text: str, style: Style, fonts: Fonts, brand: Brand, W: int, H: int, *,
                 anchor: str = "bottom", safe_area: str = "reels") -> Layout:
    """Lay out a copy line. Raises CaptionFitError (naming the copy id) if it cannot fit."""
    min_px = max(8, math.ceil(MIN_SIZE * H))
    px = max(min_px, int(round(style.size * H)))
    if safe_area == "reels":
        top, bot = SAFE_TOP * H, H - SAFE_BOTTOM * H
    elif safe_area == "landscape":                 # 16:9 / square players: title-safe margins, controls at the bottom
        top, bot = LAND_TOP * H, H - LAND_BOTTOM * H
    else:
        top, bot = 0.04 * H, H - 0.04 * H
    while True:
        font = fonts.get(style.font, px, text)
        pad = int(round(style.padding * px))
        avail = style.max_width * W - 2 * pad
        lines, fits = wrap(text, font, avail)
        line_h = int(round(px * style.line_height))
        box_h = len(lines) * line_h + 2 * pad
        tall = style.y is None and box_h > (bot - top)
        if fits and not tall:
            break
        if px <= min_px:
            why = "a word is wider than the line" if not fits else "the block is taller than the safe area"
            raise CaptionFitError(f"copy {copy_id!r}: text does not fit at the minimum size "
                                  f"({MIN_SIZE} x frame height = {min_px}px): {why}")
        px = max(min_px, int(px * 0.94))
    widths = [font.getlength(text[s:e]) for s, e in lines]
    tw = max(widths) if widths else 0.0
    box_w = int(math.ceil(tw + 2 * pad))
    bb = font.getbbox("H", anchor="ls")
    cap_h = -bb[1]
    if style.x is not None:
        x0 = int(round(style.x * W))
    else:
        x0 = int(round((W - box_w) / 2))
    if style.y is not None:
        y0 = int(round(style.y * H))
    elif anchor == "top":
        y0 = int(round(top))
    elif anchor == "center":
        y0 = int(round((top + bot - box_h) / 2))
    else:
        y0 = int(round(bot - box_h))
    return Layout(copy_id, text, style, fonts, brand, px, pad, line_h, lines, widths, x0, y0, box_w, box_h,
                  cap_h, (W, H))


# --------------------------------------------------------------------------- #
# Reveals
# --------------------------------------------------------------------------- #
def reveal_count(lay: Layout, reveal: str, t: float, vo_dur: float | None = None) -> int:
    """Number of characters of the copy text visible ``t`` seconds after the caption starts."""
    n_total = len(lay.text)
    if t < 0:
        return 0
    if reveal in ("none", "pop"):
        return n_total
    if reveal == "typewriter":
        return min(n_total, int(math.floor(t * lay.style.cps)))
    if reveal == "words":
        words = [m.end() for m in re.finditer(r"[^\s]+", lay.text)]
        if not words:
            return n_total
        dur = vo_dur if vo_dur else 0.3 * len(words)
        k = min(len(words), int(math.floor(t / (dur / len(words)))) + 1)
        return words[k - 1]
    raise ValueError(f"unknown reveal {reveal!r}")


def reveal_seconds(lay: Layout, reveal: str, vo_dur: float | None = None) -> float:
    """Time until the caption is fully visible."""
    if reveal == "typewriter":
        return len(lay.text) / lay.style.cps
    if reveal == "words":
        n = len(re.findall(r"[^\s]+", lay.text))
        return (vo_dur if vo_dur else 0.3 * n) * (n - 1) / max(1, n)
    return 0.0


def revealed_lines(lay: Layout, n: int) -> list[str | None]:
    """The strings to draw per line when ``n`` characters are visible (None = not yet visible)."""
    return [lay.text[s:min(e, n)].rstrip() if n > s else None for s, e in lay.lines]


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
@dataclass
class Sprite:
    rgba: np.ndarray                      # HxWx4 uint8
    x: int
    y: int
    drawn: list[str | None] = field(default_factory=list)
    n: int = 0

    def over(self, frame: np.ndarray) -> np.ndarray:
        """Composite onto an HxWx3 uint8 frame; returns a new array."""
        out = frame.copy()
        h, w = self.rgba.shape[:2]
        X0, Y0, X1, Y1 = max(0, self.x), max(0, self.y), min(frame.shape[1], self.x + w), min(frame.shape[0], self.y + h)
        if X1 <= X0 or Y1 <= Y0:
            return out
        sub = self.rgba[Y0 - self.y:Y1 - self.y, X0 - self.x:X1 - self.x].astype(np.float32)
        a = sub[..., 3:4] / 255.0
        out[Y0:Y1, X0:X1] = np.clip(out[Y0:Y1, X0:X1] * (1 - a) + sub[..., :3] * a + 0.5, 0, 255).astype(np.uint8)
        return out

    def layer(self, W: int, H: int) -> np.ndarray:
        """Full-frame RGBA layer (for pixel comparisons)."""
        out = np.zeros((H, W, 4), np.uint8)
        h, w = self.rgba.shape[:2]
        X0, Y0, X1, Y1 = max(0, self.x), max(0, self.y), min(W, self.x + w), min(H, self.y + h)
        if X1 > X0 and Y1 > Y0:
            out[Y0:Y1, X0:X1] = self.rgba[Y0 - self.y:Y1 - self.y, X0 - self.x:X1 - self.x]
        return out


def draw_block(lay: Layout, strings: list[str | None], t: float = 1e9, anim: str = "none",
               cursor_on: bool = False) -> Sprite | None:
    """Draw the revealed ``strings`` (one per layout line) at the layout's fixed geometry.

    ``anim``: "none" | "scale-in" | "pop" with ``t`` seconds since the caption started.
    Returns None when nothing is visible.
    """
    if all(s is None for s in strings):
        return None
    st, br = lay.style, lay.brand
    font = lay.font
    nvis = max(i for i, s in enumerate(strings) if s is not None) + 1
    wid = [font.getlength(s) if s is not None else 0.0 for s in strings]
    # revealed box: grows right/down from the fixed top-left
    right = max((lay.line_x(i) + wid[i]) for i in range(nvis) if strings[i] is not None)
    bw = int(math.ceil(right + lay.pad))
    bh = nvis * lay.line_h + 2 * lay.pad
    m = int(math.ceil(max(st.stroke, st.shadow) * lay.px)) + 4
    cw, ch = bw + 2 * m, bh + 2 * m
    text_c = colour(st.color, br) or (255, 255, 255)
    layer = Image.new("RGBA", (cw, ch), (*text_c, 0))
    d = ImageDraw.Draw(layer)
    box_c = colour(st.box, br)
    if box_c is not None:
        d.rectangle((m, m, m + bw - 1, m + bh - 1), fill=(*box_c, int(round(255 * st.box_alpha))))
    acc = colour(st.accent, br)
    if acc is not None:
        d.rectangle((m, m, m + max(4, lay.px // 8) - 1, m + bh - 1), fill=(*acc, 255))
    stroke = int(round(st.stroke * lay.px))
    sc = colour(st.stroke_color, br) or (0, 0, 0)
    for i, s in enumerate(strings):
        if s is None:
            continue
        base = m + lay.pad + i * lay.line_h + (lay.line_h + lay.cap_h) / 2
        x = m + lay.line_x(i)
        from funkbox.bidi import shown
        s = shown(s)                                  # right-to-left lines in display order without libraqm
        if st.shadow > 0:
            off = max(1, int(round(st.shadow * lay.px)))
            d.text((x + off, base + off), s, font=font, fill=(0, 0, 0, 120), anchor="ls",
                   stroke_width=stroke, stroke_fill=(0, 0, 0, 120))
        d.text((x, base), s, font=font, fill=(*text_c, 255), anchor="ls", stroke_width=stroke,
               stroke_fill=(*sc, 255))
    if cursor_on and st.cursor:
        li = nvis - 1
        cx = int(m + lay.line_x(li) + wid[li]) + max(2, lay.px // 10)
        cy = m + lay.pad + li * lay.line_h + int(lay.line_h * 0.1)
        d.rectangle((cx, cy, cx + max(2, lay.px // 10) - 1, cy + int(lay.line_h * 0.8)), fill=(*text_c, 255))
    arr = np.asarray(layer)
    x, y = lay.x0 - m, lay.y0 - m
    if anim != "none":
        arr, x, y = _animate(arr, x, y, lay, bw, bh, m, t, anim)
    return Sprite(np.ascontiguousarray(arr), x, y, list(strings), 0)


def _animate(arr, x, y, lay: Layout, bw: int, bh: int, m: int, t: float, anim: str):
    st = lay.style
    if anim == "scale-in":
        dur = st.scale_in
        if dur <= 0 or t >= dur:
            return arr, x, y
        p = t / dur
        s = 0.9 + 0.1 * (p * p * (3 - 2 * p))
        alpha = 1.0
    else:   # pop: ease-out-back over 0.22 s
        dur = 0.22
        if t >= dur:
            return arr, x, y
        p = t / dur
        c1, c3 = 1.70158, 2.70158
        s = 0.55 + 0.45 * (1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2)
        alpha = min(1.0, p / 0.4)
    h, w = arr.shape[:2]
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    im = Image.fromarray(arr).resize((nw, nh), Image.BICUBIC)
    a = np.asarray(im).copy()
    if alpha < 1.0:
        a[..., 3] = (a[..., 3] * alpha).astype(np.uint8)
    # scale about the centre of the box
    cx, cy = x + m + bw / 2, y + m + bh / 2
    return a, int(round(cx - (m + bw / 2) * s)), int(round(cy - (m + bh / 2) * s))


def render_caption(lay: Layout, reveal: str, t: float, vo_dur: float | None = None) -> Sprite | None:
    """Sprite for a caption ``t`` seconds after it started (t < 0: not yet visible)."""
    n = reveal_count(lay, reveal, t, vo_dur)
    strings = revealed_lines(lay, n)
    if reveal == "none" and t < 0:
        return None
    cursor_on = reveal == "typewriter" and n < len(lay.text) and int(t * 2.5 * 2) % 2 == 0
    anim = "pop" if reveal == "pop" else ("scale-in" if lay.style.scale_in > 0 else "none")
    spr = draw_block(lay, strings, t, anim, cursor_on)
    if spr is not None:
        spr.n = n
    return spr


# --------------------------------------------------------------------------- #
def check_caption(copy_id: str, text: str, style_name: str, anchor: str, fonts: Fonts, brand: Brand,
                  W: int, H: int, safe_area: str) -> str | None:
    """Validator hook: returns an error string (naming the copy id) or None."""
    try:
        layout_block(copy_id, text, load_style(style_name), fonts, brand, W, H, anchor=anchor, safe_area=safe_area)
    except (CaptionFitError, ValueError) as e:            # GlyphError is a ValueError
        return f"copy {copy_id!r}: {e}" if isinstance(e, GlyphError) else str(e)
    return None
