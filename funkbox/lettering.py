"""Lettering: letters drawn from strokes, built into a real font file, so hand-lettered text is still typed text.

Some lettering (emblems, badges, signage) is drawn, not set in a font: square block letters made of bars, slanted
into an isometric view. funkbox builds such letters as an actual TrueType font from stroke centrelines, so they go
through the same shaping, wrapping, right-to-left order, glyph check and text audit as any other font: the string
is typed, the shapes are drawn.

* ``"Funkbox Block"`` (weights 100..900, e.g. ``"Funkbox Block:800"``) is a built-in geometric block face: Hebrew
  (all letters and final forms, geresh and gershayim), Latin capitals (lowercase is drawn as capitals), digits and
  common punctuation. Use it anywhere a font name is accepted.
* A poster can define its own glyphs, or redraw some letters of the block face, in ``"glyphs"``:

      "glyphs": {"emblem": {"base": "block", "weight": 800,
                            "chars": {"צ": {"width": 0.95, "strokes": [[[0.1, 0.1], [0.1, 0.5], [0.85, 0.9]],
                                                                       [[0.85, 0.1], [0.85, 0.35], [0.5, 0.6]]]}}}}

  and a text layer uses them with ``"font": "glyphs:emblem"`` (or a run's ``"font"``). Coordinates are in a box one
  cap-height tall: x from 0 to the glyph's ``width``, y from 0 (top of capitals) to 1 (baseline), below 1 for
  descenders, below 0 for ascenders. ``strokes`` are centrelines drawn as square-ended bars ``weight`` thick with
  mitred corners (a stroke whose last point equals its first is closed); ``d`` is SVG path data for filled shapes.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

UPEM = 1000
CAP = 700  # font units per cap-height unit
GAP = 0.12  # space after each glyph, in cap heights
SPACE = 0.42


class LetteringError(ValueError):
    pass


def thickness(weight: int) -> float:
    """Bar thickness (cap heights) for a CSS-like weight: 100 -> 0.06, 400 -> 0.14, 700 -> 0.23, 900 -> 0.28."""
    w = max(100, min(900, int(weight)))
    return round(0.06 + (w - 100) / 800 * 0.22, 4)


# --------------------------------------------------------------------------- the block face
def block_glyphs(t: float) -> dict[str, dict]:
    """The built-in block face for bar thickness ``t``: {char: {"width", "strokes"}}."""
    h = t / 2
    T, B = h, 1 - h  # centrelines of top and bottom bars
    D = 1.42  # descender bottom
    A = -0.42  # ascender top (lamed)
    g: dict[str, dict] = {}

    def add(chars: str, w: float, *strokes):
        L, R = h, w - h
        res = []
        for s in strokes:
            pts = [(_v(x, w, L, R), _v(y, 1, T, B)) for x, y in s]
            res.append(pts)
        for c in chars:
            g[c] = {"width": w, "strokes": res}

    # Hebrew (y: "T" top bar, "B" base bar; x: "L" left, "R" right; plain numbers are positions in the box)
    add("א", 0.9, [("L", "T"), ("R", "B")], [("R", "T"), ("R", 0.42)], [("L", 0.58), ("L", "B")])
    add("ב", 0.85, [("L", "T"), (0.7, "T"), (0.7, "B")], [("L", "B"), ("R", "B")])
    add("ג", 0.62, [("L", "T"), ("R", "T"), ("R", "B")], [("R", 0.62), ("L", "B")])
    add("ד", 0.85, [("L", "T"), ("R", "T")], [(0.66, "T"), (0.66, "B")])
    add("ה", 0.85, [("L", "T"), ("R", "T"), ("R", "B")], [("L", 0.42), ("L", "B")])
    add("ו", 0.36, [("L", "T"), ("R", "T"), ("R", "B")])
    add("ז", 0.6, [("L", "T"), ("R", "T")], [(0.42, "T"), (0.42, "B")])
    add("ח", 0.85, [("L", "B"), ("L", "T"), ("R", "T"), ("R", "B")])
    add("ט", 0.85, [("L", 0.3), ("L", "B"), ("R", "B"), ("R", "T")], [("L", "T"), (0.5, 0.52)])
    add("י", 0.36, [("L", "T"), ("R", "T"), ("R", 0.46)])
    add("כ", 0.8, [("L", "T"), ("R", "T"), ("R", "B"), ("L", "B")])
    add("ך", 0.66, [("L", "T"), ("R", "T"), ("R", D)])
    add("ל", 0.8, [("L", A), ("L", 0.34), ("R", 0.34), ("R", 0.62), (0.3, "B")])
    add("מ", 0.88, [("L", "B"), ("L", "T"), ("R", "T"), ("R", "B"), (0.5, "B")])
    add("ם", 0.85, [("L", "T"), ("R", "T"), ("R", "B"), ("L", "B"), ("L", "T")])
    add("נ", 0.56, [("L", "T"), ("R", "T"), ("R", "B"), ("L", "B")])
    add("ן", 0.36, [("L", "T"), ("R", "T"), ("R", D)])
    add("ס", 0.85, [("L", "T"), ("R", "T"), ("R", 0.62), (0.5, "B"), ("L", 0.62), ("L", "T")])
    add("ע", 0.9, [("R", "T"), ("R", 0.52), ("L", "B")], [("L", "T"), ("L", 0.46), (0.52, 0.72)])
    add("פ", 0.85, [("L", 0.46), ("L", "T"), ("R", "T"), ("R", "B"), ("L", "B")])
    add("ף", 0.7, [("L", 0.46), ("L", "T"), ("R", "T"), ("R", D)])
    add(
        "צ",
        0.88,
        [("L", "T"), ("L", 0.46), ("R", "B")],
        [("R", "T"), ("R", 0.34), (0.55, 0.58)],
        [("L", "B"), ("R", "B")],
    )
    add("ץ", 0.8, [("L", "T"), ("L", 0.44), (0.5, 0.72), (0.5, D)], [("R", "T"), ("R", 0.34), (0.56, 0.56)])
    add("ק", 0.85, [("L", "T"), ("R", "T"), ("R", "B")], [("L", 0.42), ("L", D)])
    add("ר", 0.75, [("L", "T"), ("R", "T"), ("R", "B")])
    add("ש", 0.95, [("L", "T"), ("L", "B"), ("R", "B"), ("R", "T")], [(0.5, "T"), (0.5, 0.62)])
    add("ת", 0.9, [("L", "T"), ("R", "T"), ("R", "B")], [(0.26, "T"), (0.26, "B"), ("L", "B")])
    add("׳'’", 0.3, [("R", "T"), ("L", 0.36)])
    add('״"”“', 0.56, [(0.24, "T"), ("L", 0.36)], [("R", "T"), (0.32, 0.36)])

    # Latin capitals (lowercase maps to them), digits, punctuation
    add("A", 0.82, [("L", "B"), ("L", "T"), ("R", "T"), ("R", "B")], [("L", 0.52), ("R", 0.52)])
    add(
        "B",
        0.82,
        [("L", "B"), ("L", "T"), (0.68, "T"), (0.68, 0.5), ("L", 0.5)],
        [("L", 0.5), ("R", 0.5), ("R", "B"), ("L", "B")],
    )
    add("C", 0.78, [("R", "T"), ("L", "T"), ("L", "B"), ("R", "B")])
    add("D", 0.84, [("L", "T"), (0.6, "T"), ("R", 0.3), ("R", 0.7), (0.6, "B"), ("L", "B"), ("L", "T")])
    add("E", 0.74, [("R", "T"), ("L", "T"), ("L", "B"), ("R", "B")], [("L", 0.5), (0.62, 0.5)])
    add("F", 0.72, [("R", "T"), ("L", "T"), ("L", "B")], [("L", 0.5), (0.6, 0.5)])
    add("G", 0.82, [("R", "T"), ("L", "T"), ("L", "B"), ("R", "B"), ("R", 0.52), (0.48, 0.52)])
    add("H", 0.84, [("L", "T"), ("L", "B")], [("R", "T"), ("R", "B")], [("L", 0.5), ("R", 0.5)])
    add("I", 0.3, [(0.15, "T"), (0.15, "B")])
    add("J", 0.66, [("R", "T"), ("R", "B"), ("L", "B"), ("L", 0.64)])
    add("K", 0.82, [("L", "T"), ("L", "B")], [("R", "T"), ("L", 0.56)], [(0.4, 0.44), ("R", "B")])
    add("L", 0.7, [("L", "T"), ("L", "B"), ("R", "B")])
    add("M", 1.0, [("L", "B"), ("L", "T"), (0.5, 0.56), ("R", "T"), ("R", "B")])
    add("N", 0.86, [("L", "B"), ("L", "T"), ("R", "B"), ("R", "T")])
    add("O", 0.86, [("L", "T"), ("R", "T"), ("R", "B"), ("L", "B"), ("L", "T")])
    add("P", 0.8, [("L", "B"), ("L", "T"), ("R", "T"), ("R", 0.56), ("L", 0.56)])
    add("Q", 0.88, [("L", "T"), ("R", "T"), ("R", "B"), ("L", "B"), ("L", "T")], [(0.58, 0.7), ("R", 1.12)])
    add("R", 0.82, [("L", "B"), ("L", "T"), ("R", "T"), ("R", 0.54), ("L", 0.54)], [(0.46, 0.54), ("R", "B")])
    add("S", 0.8, [("R", "T"), ("L", "T"), ("L", 0.5), ("R", 0.5), ("R", "B"), ("L", "B")])
    add("T", 0.8, [("L", "T"), ("R", "T")], [(0.4, "T"), (0.4, "B")])
    add("U", 0.84, [("L", "T"), ("L", "B"), ("R", "B"), ("R", "T")])
    add("V", 0.86, [("L", "T"), (0.43, "B"), ("R", "T")])
    add("W", 1.12, [("L", "T"), (0.28, "B"), (0.56, 0.44), (0.84, "B"), ("R", "T")])
    add("X", 0.86, [("L", "T"), ("R", "B")], [("R", "T"), ("L", "B")])
    add("Y", 0.86, [("L", "T"), (0.43, 0.52), ("R", "T")], [(0.43, 0.52), (0.43, "B")])
    add("Z", 0.8, [("L", "T"), ("R", "T"), ("L", "B"), ("R", "B")])
    add("0", 0.8, [("L", "T"), ("R", "T"), ("R", "B"), ("L", "B"), ("L", "T")])
    add("1", 0.52, [("L", 0.24), (0.36, "T"), (0.36, "B")])
    add("2", 0.78, [("L", "T"), ("R", "T"), ("R", 0.5), ("L", 0.5), ("L", "B"), ("R", "B")])
    add("3", 0.78, [("L", "T"), ("R", "T"), ("R", "B"), ("L", "B")], [(0.3, 0.5), ("R", 0.5)])
    add("4", 0.8, [("L", "T"), ("L", 0.58), ("R", 0.58)], [(0.6, "T"), (0.6, "B")])
    add("5", 0.78, [("R", "T"), ("L", "T"), ("L", 0.5), ("R", 0.5), ("R", "B"), ("L", "B")])
    add("6", 0.78, [("R", "T"), ("L", "T"), ("L", "B"), ("R", "B"), ("R", 0.5), ("L", 0.5)])
    add("7", 0.76, [("L", "T"), ("R", "T"), (0.34, "B")])
    add("8", 0.8, [("L", "T"), ("R", "T"), ("R", "B"), ("L", "B"), ("L", "T")], [("L", 0.5), ("R", 0.5)])
    add("9", 0.78, [("R", 0.5), ("L", 0.5), ("L", "T"), ("R", "T"), ("R", "B"), ("L", "B")])
    add("!", 0.3, [(0.15, "T"), (0.15, 0.6)], [(0.15, 0.9), (0.15, "B")])
    add("?", 0.74, [("L", "T"), ("R", "T"), ("R", 0.46), (0.4, 0.46), (0.4, 0.68)], [(0.4, 0.86), (0.4, "B")])
    add(".", 0.3, [(0.15, 0.86), (0.15, "B")])
    add(",", 0.3, [(0.15, 0.86), (0.15, "B"), (0.04, 1.12)])
    add(":", 0.3, [(0.15, 0.3), (0.15, 0.44)], [(0.15, 0.86), (0.15, "B")])
    add(";", 0.3, [(0.15, 0.3), (0.15, 0.44)], [(0.15, 0.86), (0.15, "B"), (0.04, 1.12)])
    add("-־–", 0.6, [("L", 0.55), ("R", 0.55)])
    add("+", 0.7, [("L", 0.55), ("R", 0.55)], [(0.35, 0.3), (0.35, 0.8)])
    add("/", 0.6, [("R", "T"), ("L", "B")])
    add(
        "&",
        0.86,
        [("R", "B"), ("L", 0.3), ("L", "T"), (0.6, "T"), (0.6, 0.36), ("L", 0.66), ("L", "B"), (0.7, "B"), ("R", 0.62)],
    )
    add("(", 0.36, [("R", -0.06), ("L", 0.12), ("L", 0.88), ("R", 1.06)])
    add(")", 0.36, [("L", -0.06), ("R", 0.12), ("R", 0.88), ("L", 1.06)])
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        g[c.lower()] = g[c]
    return g


def _v(v, size: float, lo: float, hi: float) -> float:
    if v in ("L", "T"):
        return lo
    if v in ("R", "B"):
        return hi
    return float(v)


# --------------------------------------------------------------------------- outlines
def _polygons(glyph: dict, t: float) -> list[list[tuple[float, float]]]:
    """A glyph's filled polygons in the y-down cap box."""
    from funkbox.stills.shapes import bar_polygons

    out: list[list[tuple[float, float]]] = []
    for s in glyph.get("strokes") or []:
        pts = [tuple(map(float, p)) for p in s]
        closed = len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-6
        if closed:
            pts = pts[:-1] + [pts[0], pts[1]]
        else:  # square ends on straight bars; a diagonal ends flush, so it never pokes out
            pts[0] = _extend(pts[1], pts[0], t / 2)
            pts[-1] = _extend(pts[-2], pts[-1], t / 2)
        out += bar_polygons(pts, t / 2, "butt")
    if glyph.get("d"):
        from funkbox.stills.vpath import parse

        out += [pts for pts, _ in parse(glyph["d"], 0.004)]
    return out


def _extend(a, b, d: float):
    """``b`` moved ``d`` further from ``a`` when a->b is horizontal or vertical."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-9 or min(abs(dx), abs(dy)) > 1e-6:
        return b
    return (b[0] + dx / L * d, b[1] + dy / L * d)


def _clockwise_font(poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """In font units (y up) TrueType outer contours run clockwise: negative signed area."""
    a = sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly)))
    return poly if a < 0 else poly[::-1]


def build_font(glyphs: dict[str, dict], t: float, family: str, out: Path) -> Path:
    """Write a TrueType font of ``glyphs`` (strokes ``t`` thick) to ``out``."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", "space"]
    cmap = {0x20: "space", 0xA0: "space"}
    shapes: dict[str, tuple[float, list]] = {}
    for ch, gl in sorted(glyphs.items()):
        if len(ch) != 1:
            raise LetteringError(f"glyph key {ch!r}: one character per glyph")
        name = f"uni{ord(ch):04X}"
        if name not in shapes:
            shapes[name] = (float(gl.get("width", 0.8)), _polygons(gl, t))
            order.append(name)
        cmap[ord(ch)] = name
    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    glyf, metrics = {}, {}
    for name in order:
        pen = TTGlyphPen(None)
        if name in shapes:
            w, polys = shapes[name]
            for poly in polys:
                pts = _clockwise_font([(round(x * CAP), round((1 - y) * CAP)) for x, y in poly])
                pen.moveTo(pts[0])
                for p in pts[1:]:
                    pen.lineTo(p)
                pen.closePath()
            adv = round((w + GAP) * CAP)
        else:
            adv = round((SPACE if name == "space" else 0.6) * CAP)
        glyf[name] = pen.glyph()
        metrics[name] = (adv, 0)
    fb.setupGlyf(glyf)
    for name in order:  # left side bearing = xMin of the glyph
        g = glyf[name]
        metrics[name] = (metrics[name][0], getattr(g, "xMin", 0) if g.numberOfContours else 0)
    fb.setupHorizontalMetrics(metrics)
    asc, desc = round(1.5 * CAP), -round(0.5 * CAP)
    fb.setupHorizontalHeader(ascent=asc, descent=desc)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(
        sTypoAscender=asc, sTypoDescender=desc, usWinAscent=asc, usWinDescent=-desc, sxHeight=CAP, sCapHeight=CAP
    )
    fb.setupPost()
    try:  # one clean outline per glyph (bars and mitres merged): no seams when filled
        from fontTools.ttLib.removeOverlaps import removeOverlaps

        removeOverlaps(fb.font)
    except ImportError:  # skia-pathops missing: overlapping contours still fill correctly (nonzero)
        pass
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    fb.save(str(tmp))
    tmp.replace(out)
    return out


def _cache() -> Path:
    from funkbox.fonts import cache_dir

    d = cache_dir() / "lettering"
    d.mkdir(parents=True, exist_ok=True)
    return d


VERSION = 2  # bump when the built-in face changes, so cached fonts are rebuilt


def block_font(weight: int = 700) -> Path:
    """The built-in "Funkbox Block" face at ``weight`` (built once, cached)."""
    out = _cache() / f"FunkboxBlock-v{VERSION}-{int(weight)}.ttf"
    if not out.is_file():
        t = thickness(weight)
        build_font(block_glyphs(t), t, "Funkbox Block", out)
    return out


def check_glyphset(name: str, spec, errs: list[str]) -> None:
    w = f"glyphs.{name}"
    if not isinstance(spec, dict) or set(spec) - {"base", "weight", "chars"}:
        errs.append(f'{w}: {{base: "block" or none, weight: 100..900, chars: {{char: glyph}}}}')
        return
    if spec.get("base") not in (None, "block"):
        errs.append(f'{w}.base: "block" (the built-in face) or leave it out')
    wt = spec.get("weight", 700)
    if not (isinstance(wt, int) and 100 <= wt <= 900):
        errs.append(f"{w}.weight: 100..900")
    chars = spec.get("chars", {})
    if not isinstance(chars, dict) or (not chars and not spec.get("base")):
        errs.append(f'{w}.chars: an object of {{"char": {{width, strokes | d}}}}')
        return
    for ch, gl in chars.items():
        wc = f"{w}.chars[{ch!r}]"
        if len(ch) != 1:
            errs.append(f"{wc}: one character per glyph")
            continue
        if not isinstance(gl, dict) or set(gl) - {"width", "strokes", "d"} or not (gl.get("strokes") or gl.get("d")):
            errs.append(f'{wc}: {{width, strokes: [[[x, y], ...], ...]}} and/or {{d: "svg path"}}')
            continue
        if not (isinstance(gl.get("width", 0.8), (int, float)) and 0.05 <= gl.get("width", 0.8) <= 3):
            errs.append(f"{wc}.width: 0.05..3 cap heights")
        for s in gl.get("strokes") or []:
            if not (
                isinstance(s, list)
                and len(s) >= 2
                and all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p) for p in s)
            ):
                errs.append(f"{wc}.strokes: each stroke is a list of at least two [x, y] points")
                break
        if gl.get("d"):
            from funkbox.stills.vpath import PathError, parse

            try:
                parse(gl["d"])
            except PathError as e:
                errs.append(f"{wc}.d: {e}")


def glyphset_font(spec: dict) -> Path:
    """A poster's glyph set (optionally on top of the block face) as a font file, cached by its content."""
    weight = int(spec.get("weight", 700))
    t = thickness(weight)
    glyphs = block_glyphs(t) if spec.get("base") == "block" else {}
    glyphs.update(spec.get("chars") or {})
    key = hashlib.sha1(json.dumps([VERSION, spec], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    out = _cache() / f"glyphs-{key}.ttf"
    if not out.is_file():
        build_font(glyphs, t, "Funkbox Glyphs", out)
    return out
