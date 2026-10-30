"""Typography beyond one font per block: letter spacing, styled runs (another font, colour or size for some words),
and text set along any path. Text is shaped with HarfBuzz and drawn from the glyph outlines, so the pixel render
and the SVG/PDF export are the same geometry.

Text layer keys (all optional; a layer without them is drawn by the plain text path in text.py):

    "letter_spacing": 0.08                 extra space after every letter, in ems (-0.2 .. 1)
    "runs": [{"match": "Hashem", "font": "display" | "caption" | "Frank Ruhl Libre:700" | "fonts/X.ttf",
              "color": "#d4af37", "scale": 1.2, "all": true}]
                                           style the words that match (every occurrence unless "all": false);
                                           the text itself never changes, so the audit still proves it exact
    "on_path": "M0.1,0.8 C0.3,0.5 0.7,0.5 0.9,0.8"
                                           set the line along this SVG path (canvas fractions; "path_units": "px"
                                           for pixels). "align" places it on the path (left = from the start),
                                           "path_offset" (0..1) moves the start, "path_shift" (ems) lifts the
                                           baseline off the path (positive = to the left of the direction of travel)

Mixed right-to-left and left-to-right inside one line keeps the paragraph's direction for the order of the styled
pieces (each piece is shaped correctly); keep such lines in one run.
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from funkbox.reel import captions

from .raster import RCtx, rgba_of
from .schema import PosterError

ADVANCED = ("letter_spacing", "runs", "on_path")
SS = 4                                   # supersampling of the outline rasteriser


def is_advanced(lay: dict) -> bool:
    return any(lay.get(k) for k in ADVANCED)


@dataclass
class Piece:
    start: int
    end: int
    font: object                         # vector.Shaper
    px: float
    color: tuple[int, int, int, int]


@dataclass
class Set:
    """The typeset layer: outlines grouped by colour (canvas px, full resolution), and what was drawn."""
    by_color: list[tuple[tuple[int, int, int, int], list]] = field(default_factory=list)
    lines: list[tuple[int, int]] = field(default_factory=list)
    drawn: list[str] = field(default_factory=list)
    px: int = 0
    box: tuple[int, int, int, int] = (0, 0, 0, 0)
    text: str = ""

    def reconstruct(self) -> str:
        t = self.text
        out = [t[: self.lines[0][0]]] if self.lines else [t]
        for i, s in enumerate(self.drawn):
            out.append(s)
            if i + 1 < len(self.lines):
                out.append(t[self.lines[i][1]: self.lines[i + 1][0]])
        if self.lines:
            out.append(t[self.lines[-1][1]:])
        return "".join(out)


# --------------------------------------------------------------------------- runs
def _attrs(lay: dict, text: str, ctx: RCtx, fonts: captions.Fonts, label: str):
    """Per character: (font path, scale, colour). Raises PosterError for a run that matches nothing or a font
    that lacks glyphs."""
    from funkbox import fonts as fontsmod
    which = lay.get("style", "caption")
    try:
        base = fonts.path_for(which, text)
    except captions.GlyphError as e:
        raise PosterError([f"{label}: {e}"]) from e
    col = rgba_of(lay.get("color", "brand.text"), ctx.brand)
    attrs = [(base, 1.0, col)] * len(text)
    for n, run in enumerate(lay.get("runs") or []):
        m = run["match"]
        hits, i = [], text.find(m)
        while i >= 0:
            hits.append(i)
            if run.get("all", True) is False:
                break
            i = text.find(m, i + len(m))
        if not hits:
            raise PosterError([f"{label}: runs[{n}] matches nothing: {m!r} is not in the text"])
        f = run.get("font")
        try:
            if f in (None, ""):
                path = None
            elif f in ("display", "caption"):
                path = fonts.path_for(f, m)
            else:
                path = fontsmod.resolve(f, ctx.base_dir)
                miss = fontsmod.missing(path, m)
                if miss:
                    raise captions.GlyphError(f"runs[{n}] font {f!r} has no glyph for {fontsmod.describe(miss)}")
        except (captions.GlyphError, FileNotFoundError, ValueError) as e:
            raise PosterError([f"{label}: {e}"]) from e
        rc = rgba_of(run["color"], ctx.brand) if run.get("color") else None
        for h in hits:
            for j in range(h, h + len(m)):
                p0, s0, c0 = attrs[j]
                attrs[j] = (path or p0, float(run.get("scale", s0)), rc or c0)
    return base, attrs


def _rtl(text: str) -> bool:
    for ch in text:
        b = unicodedata.bidirectional(ch)
        if b in ("R", "AL"):
            return True
        if b == "L":
            return False
    return False


# --------------------------------------------------------------------------- measuring and shaping
class _Line:
    def __init__(self, text: str, attrs, px: float, spacing: float):
        from .vector import Shaper
        self.text, self.attrs, self.px, self.spacing = text, attrs, px, spacing
        self.Shaper = Shaper

    def pieces(self, s: int, e: int) -> list[Piece]:
        out, i = [], s
        while i < e:
            j = i
            while j < e and self.attrs[j] == self.attrs[i]:
                j += 1
            path, scale, col = self.attrs[i]
            out.append(Piece(i, j, self.Shaper.get(path), self.px * scale, col))
            i = j
        return out

    def shaped(self, p: Piece):
        """[(gid, x, y, advance, cluster)] for a piece, letter spacing included (after every cluster)."""
        hb = p.font.hb
        buf = hb.Buffer()
        buf.add_str(self.text[p.start:p.end])
        buf.guess_segment_properties()
        hb.shape(p.font.font, buf, {})
        k = p.px / p.font.upem
        sp = self.spacing * p.px
        out, pen, last = [], 0.0, None
        infos = list(zip(buf.glyph_infos, buf.glyph_positions))
        for n, (info, pos) in enumerate(infos):
            adv = pos.x_advance * k
            nxt = infos[n + 1][0].cluster if n + 1 < len(infos) else None
            if sp and nxt != info.cluster:                     # the last glyph of a cluster carries the spacing
                adv += sp
            out.append((info.codepoint, pen + pos.x_offset * k, pos.y_offset * k, adv, info.cluster))
            pen += adv
        return out, pen

    def width(self, s: int, e: int) -> float:
        w = sum(self.shaped(p)[1] for p in self.pieces(s, e))
        return w - (self.spacing * self.px if self.spacing and e > s else 0.0)   # no tracking after the last letter

    def glyphs(self, s: int, e: int, rtl: bool):
        """[(Piece, gid, x, y_off, advance)] laid out left to right from x = 0."""
        ps = self.pieces(s, e)
        if rtl:
            ps = ps[::-1]
        out, x = [], 0.0
        for p in ps:
            g, w = self.shaped(p)
            out += [(p, gid, x + gx, gy, adv) for gid, gx, gy, adv, _ in g]
            x += w
        return out


def _wrap(line: _Line, text: str, frame_w: float, wrap: bool) -> tuple[list[tuple[int, int]], bool]:
    """Greedy wrap on spaces (hard breaks at newlines), like captions.wrap. Returns (lines, every word fits)."""
    if not wrap:
        return [(0, len(text))], line.width(0, len(text)) <= frame_w
    lines, ok, pos = [], True, 0
    for para in text.split("\n"):
        words, i = [], 0
        while i < len(para):
            j = para.find(" ", i)
            j = len(para) if j < 0 else j
            if j > i:
                words.append((pos + i, pos + j))
            i = j + 1
        if not words:
            lines.append((pos, pos))
        cur = None
        for ws, we in words:
            if line.width(ws, we) > frame_w:
                ok = False
            if cur is None:
                cur = (ws, we)
            elif line.width(cur[0], we) <= frame_w:
                cur = (cur[0], we)
            else:
                lines.append(cur)
                cur = (ws, we)
        if cur is not None:
            lines.append(cur)
        pos += len(para) + 1
    return lines, ok


# --------------------------------------------------------------------------- path
def _polyline(lay: dict, W: int, H: int) -> tuple[np.ndarray, np.ndarray]:
    from .vpath import parse
    px = lay.get("path_units", "fraction") == "px"
    sx, sy = (1.0, 1.0) if px else (W, H)
    tol = 0.25 / (1.0 if px else max(W, H))
    pts, _ = parse(lay["on_path"], tol)[0]
    P = np.array([(x * sx, y * sy) for x, y in pts], np.float64)
    seg = np.hypot(*np.diff(P, axis=0).T)
    return P, np.concatenate([[0.0], np.cumsum(seg)])


def _at(P: np.ndarray, L: np.ndarray, s: float) -> tuple[float, float, float]:
    """Point and tangent angle at arc length ``s`` (extended straight past the ends)."""
    i = int(np.clip(np.searchsorted(L, s) - 1, 0, len(P) - 2))
    while i < len(P) - 2 and L[i + 1] - L[i] < 1e-9:
        i += 1
    a, b = P[i], P[i + 1]
    seg = max(L[i + 1] - L[i], 1e-9)
    t = (s - L[i]) / seg
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, ang


# --------------------------------------------------------------------------- main
def typeset(lay: dict, text: str, ctx: RCtx, fonts: captions.Fonts) -> Set:
    """Lay out and outline ``text`` at full resolution (see the module doc). Raises PosterError when it cannot fit."""
    from .text import TEXT_SIZE
    from .vector import flatten, map_points, text_geometry
    W, H = ctx.full
    label = f"text layer {lay['id']!r}" + (f" (copy {lay['copy_id']!r})" if "copy_id" in lay else "")
    base, attrs = _attrs(lay, text, ctx, fonts, label)
    rtl = _rtl(text)
    spacing = float(lay.get("letter_spacing", 0.0))
    on_path = bool(lay.get("on_path"))
    max_w = float(lay.get("max_width", 0.84))
    x0 = int(round(float(lay.get("x", (1.0 - max_w) / 2)) * W))
    y0 = int(round(float(lay.get("y", 0.1)) * H))
    frame_w = int(round(max_w * W))
    if on_path:
        P, L = _polyline(lay, W, H)
        frame_w = int(L[-1] * (1.0 - float(lay.get("path_offset", 0.0))))
    lh = float(lay.get("line_height", 1.1))
    min_px = max(8, math.ceil(captions.MIN_SIZE * H))
    px = max(min_px, int(round(float(lay.get("size", TEXT_SIZE)) * H)))
    align = lay.get("align", "left")
    from PIL import ImageFont
    while True:
        line = _Line(text, attrs, px, spacing)
        lines, fits = _wrap(line, text, frame_w, lay.get("wrap", True) and not on_path)
        widths = [line.width(s, e) for s, e in lines]
        line_h = int(round(px * lh))
        block_h = len(lines) * line_h
        offs = [0.0 if align == "left" else (frame_w - w_) / (2 if align == "center" else 1) for w_ in widths]
        inside = on_path or (x0 + min(offs, default=0) >= 0 and y0 >= 0 and
                             x0 + max((o + w_ for o, w_ in zip(offs, widths)), default=0) <= W + 0.5
                             and y0 + block_h <= H)
        if fits and inside:
            break
        if px <= min_px:
            why = ("it is longer than the path" if on_path else "a word is wider than the line" if not fits else
                   "the block runs outside the canvas; move x/y, widen max_width or shorten the text")
            raise PosterError([f"{label}: text does not fit at the minimum size "
                               f"({captions.MIN_SIZE} x canvas height = {min_px}px): {why}"])
        px = max(min_px, int(px * 0.94))
    bb = ImageFont.truetype(str(base), px).getbbox("H", anchor="ls")
    cap_h = -bb[1]
    out = Set(lines=lines, drawn=[text[s:e] for s, e in lines], px=px, text=text,
              box=(x0, y0, frame_w, block_h))
    cmds_by: dict[tuple, list] = {}

    def add(piece: Piece, gid: int, f) -> None:
        k = piece.px / piece.font.upem
        cmds_by.setdefault(piece.color, []).extend(
            map_points(piece.font.glyph(gid), lambda p: f((p[0] * k, -p[1] * k))))

    if on_path:
        start = float(lay.get("path_offset", 0.0)) * L[-1] + offs[0]
        shift = float(lay.get("path_shift", 0.0)) * px
        for piece, gid, gx, gy, adv in line.glyphs(*lines[0], rtl):
            cx, cy, ang = _at(P, L, start + gx + adv / 2)
            ca, sa = math.cos(ang), math.sin(ang)
            # glyph frame: x along the path from the glyph's advance centre, y down from the baseline
            nx, ny = sa, -ca                                    # left of the direction of travel (screen coords)
            ox, oy = cx + nx * shift, cy + ny * shift

            def f(p, gy=gy, adv=adv, ox=ox, oy=oy, ca=ca, sa=sa):
                u, v = p[0] - adv / 2, p[1] - gy                  # from the glyph's centre on the baseline
                return ox + u * ca - v * sa, oy + u * sa + v * ca
            add(piece, gid, f)
    else:
        for i, (s, e) in enumerate(lines):
            base_y = y0 + i * line_h + (line_h + cap_h) / 2
            lx = x0 + offs[i]
            for piece, gid, gx, gy, adv in line.glyphs(s, e, rtl):
                add(piece, gid, lambda p, ox=lx + gx, oy=base_y - gy: (ox + p[0], oy + p[1]))
        geo = {k: float(lay[k]) for k in ("rotate_deg", "skew_x", "skew_y") if lay.get(k)}
        if lay.get("arc"):
            geo["arc"] = float(lay["arc"]) * W
        if geo:
            f = text_geometry((x0 + frame_w / 2, y0 + block_h / 2), **geo)
            cmds_by = {c: map_points(flatten(v) if "arc" in geo else v, f) for c, v in cmds_by.items()}
    base_col = rgba_of(lay.get("color", "brand.text"), ctx.brand)
    order = sorted(cmds_by, key=lambda c: c != base_col)            # the base colour first, runs over it
    out.by_color = [(c, cmds_by[c]) for c in order]
    return out


# --------------------------------------------------------------------------- rasterising outlines
def coverage(cmds: list, w: int, h: int, k: float = 1.0) -> np.ndarray:
    """Anti-aliased nonzero-winding coverage (0..1, h x w) of outline commands scaled by ``k``."""
    from .vector import flatten
    subs, cur = [], []
    for c in flatten(cmds, 0.2 / max(k, 1e-6)):
        if c[0] == "M":
            if len(cur) > 2:
                subs.append(cur)
            cur = [c[1]]
        elif c[0] == "L":
            cur.append(c[1])
        elif c[0] == "Z":
            if len(cur) > 2:
                subs.append(cur)
            cur = []
    if len(cur) > 2:
        subs.append(cur)
    cov = np.zeros((h, w), np.float32)
    if not subs:
        return cov
    allp = np.concatenate([np.array(s) for s in subs]) * k
    bx0, by0 = max(0, int(allp[:, 0].min()) - 1), max(0, int(allp[:, 1].min()) - 1)
    bx1, by1 = min(w, int(allp[:, 0].max()) + 2), min(h, int(allp[:, 1].max()) + 2)
    if bx1 <= bx0 or by1 <= by0:
        return cov
    bw, bh = (bx1 - bx0) * SS, (by1 - by0) * SS
    acc = np.zeros((bh, bw), np.int16)
    for s in subs:
        q = (np.array(s) * k - [bx0, by0]) * SS
        area = float(np.sum(q[:-1, 0] * q[1:, 1] - q[1:, 0] * q[:-1, 1]) + q[-1, 0] * q[0, 1] - q[0, 0] * q[-1, 1])
        if abs(area) < 1e-9:
            continue
        x0, y0 = max(0, int(q[:, 0].min())), max(0, int(q[:, 1].min()))
        x1, y1 = min(bw, int(q[:, 0].max()) + 2), min(bh, int(q[:, 1].max()) + 2)
        if x1 <= x0 or y1 <= y0:
            continue
        im = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(im).polygon([(x - x0, y - y0) for x, y in q], fill=1)
        acc[y0:y1, x0:x1] += np.asarray(im, np.int16) * (1 if area > 0 else -1)
    m = (acc != 0).astype(np.float32).reshape(by1 - by0, SS, bx1 - bx0, SS).mean((1, 3))
    cov[by0:by1, bx0:bx1] = m
    return cov
