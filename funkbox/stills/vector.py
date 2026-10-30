"""Vector export: a poster as SVG or PDF that stays sharp at any size.

Solids, borders, gradients, shapes, imported SVGs and text become real vector paths. Text is shaped with HarfBuzz
(the same shaping the pixel render uses, so kerning, ligatures and right-to-left order match) and written as glyph
outlines: the file needs no fonts and shows exactly the approved words. Arc, slant and rotate are applied to the
outlines. Anything that is only pixels (photos, products, shaders, groups) or that a filter, mask, shadow or glow
turned into pixels is embedded as a picture in its place, and the export report names those layers.

    export(rendered, "out/poster.svg")          # or .pdf
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from . import fx, raster
from .shapes import bar_polygons, path_px

Pt = tuple[float, float]
Cmd = tuple                                   # ("M", p) ("L", p) ("Q", c, p) ("C", c1, c2, p) ("Z",)

CSS_BLEND = {"normal": "normal", "add": "plus-lighter", "screen": "screen", "multiply": "multiply",
             "overlay": "overlay", "difference": "difference", "softlight": "soft-light", "hue": "hue"}
PDF_BLEND = {"normal": "Normal", "screen": "Screen", "multiply": "Multiply", "overlay": "Overlay",
             "difference": "Difference", "softlight": "SoftLight", "hue": "Hue"}
KAPPA = 0.5522847498


# --------------------------------------------------------------------------- display list
@dataclass
class Grad:
    kind: str                                 # linear | radial
    a: Pt                                     # linear: start; radial: centre
    b: Pt | float                             # linear: end;   radial: radius
    stops: list[tuple[float, tuple[int, int, int, int]]]


@dataclass
class PathItem:
    cmds: list[Cmd]
    fill: tuple | Grad | None = None
    rule: str = "nonzero"
    stroke: tuple | None = None
    stroke_w: float = 0.0
    join: str = "miter"
    cap: str = "butt"
    under: bool = True                        # stroke painted under the fill (outside outlines, text strokes)
    opacity: float = 1.0


@dataclass
class ImageItem:
    rgba: np.ndarray
    box: tuple[float, float, float, float]    # x, y, w, h in canvas px


@dataclass
class Group:
    id: str
    items: list = field(default_factory=list)
    opacity: float = 1.0
    blend: str = "normal"
    clip: list[PathItem] | None = None
    vector: bool = True


# --------------------------------------------------------------------------- geometry helpers
def poly_cmds(pts: list[Pt], closed: bool = True) -> list[Cmd]:
    if not pts:
        return []
    out: list[Cmd] = [("M", pts[0])] + [("L", p) for p in pts[1:]]
    return out + [("Z",)] if closed else out


def _positive(pts: list[Pt]) -> list[Pt]:
    """The same polygon wound so its signed area is positive (unions of polygons then fill right under nonzero)."""
    area = sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))
    return pts if area >= 0 else pts[::-1]


def ellipse_cmds(x0: float, y0: float, x1: float, y1: float) -> list[Cmd]:
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2
    kx, ky = rx * KAPPA, ry * KAPPA
    return [("M", (cx + rx, cy)),
            ("C", (cx + rx, cy + ky), (cx + kx, cy + ry), (cx, cy + ry)),
            ("C", (cx - kx, cy + ry), (cx - rx, cy + ky), (cx - rx, cy)),
            ("C", (cx - rx, cy - ky), (cx - kx, cy - ry), (cx, cy - ry)),
            ("C", (cx + kx, cy - ry), (cx + rx, cy - ky), (cx + rx, cy)), ("Z",)]


def map_points(cmds: list[Cmd], f: Callable[[Pt], Pt]) -> list[Cmd]:
    """Apply ``f`` to every point and control point (exact for affine maps)."""
    return [(c[0], *[f(p) for p in c[1:]]) for c in cmds]


def flatten(cmds: list[Cmd], tol: float = 0.25) -> list[Cmd]:
    """Curves -> short lines (before a non-affine map such as the arc)."""
    out: list[Cmd] = []
    cur: Pt = (0.0, 0.0)
    for c in cmds:
        op = c[0]
        if op in ("M", "L"):
            out.append(c)
            cur = c[1]
        elif op == "Z":
            out.append(c)
        else:
            if op == "Q":
                (qx, qy), p = c[1], c[2]
                c1 = (cur[0] + 2 / 3 * (qx - cur[0]), cur[1] + 2 / 3 * (qy - cur[1]))
                c2 = (p[0] + 2 / 3 * (qx - p[0]), p[1] + 2 / 3 * (qy - p[1]))
            else:
                c1, c2, p = c[1], c[2], c[3]
            n = max(2, min(48, int(math.ceil((math.dist(cur, c1) + math.dist(c1, c2) + math.dist(c2, p)) / (4 * tol)))))
            for i in range(1, n + 1):
                t = i / n
                a, b, cc, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                out.append(("L", (a * cur[0] + b * c1[0] + cc * c2[0] + d * p[0],
                                  a * cur[1] + b * c1[1] + cc * c2[1] + d * p[1])))
            cur = p
    return out


def text_geometry(centre: Pt, *, arc: float = 0.0, rotate_deg: float = 0.0, skew_x: float = 0.0,
                  skew_y: float = 0.0) -> Callable[[Pt], Pt]:
    """The forward map of shapes.warp_text: bend along the arc, then slant, then rotate, about ``centre``."""
    cx, cy = centre
    a, b = math.tan(math.radians(skew_x)), math.tan(math.radians(skew_y))
    ct, st = math.cos(math.radians(rotate_deg)), math.sin(math.radians(rotate_deg))
    R = abs(arc)

    def f(p: Pt) -> Pt:
        x, y = p[0] - cx, p[1] - cy
        if arc > 0:
            th, rad = x / R, y + R
            x, y = rad * math.sin(th), rad * math.cos(th) - R
        elif arc < 0:
            th, rad = x / R, R - y
            x, y = rad * math.sin(th), R - rad * math.cos(th)
        x, y = x + a * y, b * x + y
        x, y = x * ct + y * st, -x * st + y * ct
        return x + cx, y + cy
    return f


# --------------------------------------------------------------------------- text outlines
class Shaper:
    """HarfBuzz shaping + fontTools outlines for one font file, cached."""
    _cache: dict[str, "Shaper"] = {}

    def __init__(self, path: Path):
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont
        data = Path(path).read_bytes()
        self.hb = hb
        self.font = hb.Font(hb.Face(data))
        self.tt = TTFont(io.BytesIO(data), lazy=True)
        self.upem = self.tt["head"].unitsPerEm
        self.order = self.tt.getGlyphOrder()
        self.glyphs = self.tt.getGlyphSet()
        self.outlines: dict[int, list[Cmd]] = {}

    @classmethod
    def get(cls, path: Path) -> "Shaper":
        k = str(Path(path).resolve())
        if k not in cls._cache:
            cls._cache[k] = cls(Path(path))
        return cls._cache[k]

    def glyph(self, gid: int) -> list[Cmd]:
        """The glyph's outline in font units (y up)."""
        if gid not in self.outlines:
            from fontTools.pens.basePen import BasePen
            out: list[Cmd] = []

            class Pen(BasePen):
                def _moveTo(self, p): out.append(("M", p))
                def _lineTo(self, p): out.append(("L", p))
                def _curveToOne(self, a, b, c): out.append(("C", a, b, c))
                def _qCurveToOne(self, a, b): out.append(("Q", a, b))
                def _closePath(self): out.append(("Z",))
                def _endPath(self): pass

            self.glyphs[self.order[gid]].draw(Pen(self.glyphs))
            self.outlines[gid] = out
        return self.outlines[gid]

    def shape(self, text: str, px: float) -> tuple[list[tuple[int, float, float]], float]:
        """[(glyph id, x, y offset in px)] in visual order, and the advance width in px."""
        hb = self.hb
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, {})
        k = px / self.upem
        out, pen = [], 0.0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            out.append((info.codepoint, (pen + pos.x_offset) * k, pos.y_offset * k))
            pen += pos.x_advance
        return out, pen * k

    def line(self, text: str, px: float, x: float, baseline: float) -> list[Cmd]:
        """Outlines of a shaped line, left edge ``x`` and baseline ``baseline`` in canvas px (y down)."""
        k = px / self.upem
        cmds: list[Cmd] = []
        glyphs, _ = self.shape(text, px)
        for gid, gx, gy in glyphs:
            ox, oy = x + gx, baseline - gy
            cmds += map_points(self.glyph(gid), lambda p, ox=ox, oy=oy: (ox + p[0] * k, oy - p[1] * k))
        return cmds


# --------------------------------------------------------------------------- layers -> display list
def _rgba(c, brand) -> tuple[int, int, int, int]:
    return raster.rgba_of(c, brand)


def _rect(x0, y0, x1, y1) -> list[Cmd]:
    return poly_cmds([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def _shape_items(lay: dict, ctx) -> list[PathItem]:
    brand, kind = ctx.brand, lay["kind"]
    sw = ctx.px(float(lay.get("stroke_px", 0))) if lay.get("stroke") else 0.0
    stroke = _rgba(lay["stroke"], brand) if sw > 0 else None
    fill = _rgba(lay["fill"], brand) if lay.get("fill") else None
    if kind == "polyline":
        P = [(float(x) * ctx.w, float(y) * ctx.h) for x, y in lay["points"]]
        half, cap = float(lay.get("width", 0.03)) * ctx.w / 2, lay.get("cap", "square")
        out = []
        for col, h in ((stroke, half + sw), (fill, half)):
            if col is not None:
                cmds = [c for poly in bar_polygons(P, h, cap) for c in poly_cmds(_positive(poly))]
                out.append(PathItem(cmds, fill=col))
        return out
    if kind == "path":
        cmds = [c for pts, closed in path_px(lay, ctx) for c in poly_cmds(pts, closed)]
        rule = lay.get("fill_rule", "nonzero")
    else:
        P = [(float(x) * ctx.w, float(y) * ctx.h) for x, y in lay["points"]]
        rule = "nonzero"
        if kind == "polygon":
            cmds = poly_cmds(P)
        else:
            (x0, y0), (x1, y1) = P
            x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
            cmds = _rect(x0, y0, x1, y1) if kind == "rect" else ellipse_cmds(x0, y0, x1, y1)
    return [PathItem(cmds, fill=fill, rule=rule, stroke=stroke, stroke_w=2 * sw, join="miter")]


def _svg_items(lay: dict, ctx) -> list[PathItem]:
    from ..config import resolve_path
    from .vpath import svg_items
    items, (vx, vy, vw, vh), _ = svg_items(resolve_path(lay["path"], ctx.base_dir), tol=0.05)
    tw = float(lay.get("width", 0.5)) * ctx.w
    th = float(lay["height"]) * ctx.h if "height" in lay else tw * vh / vw
    tx, ty = float(lay.get("x", 0.0)) * ctx.w, float(lay.get("y", 0.0)) * ctx.h
    kx, ky = tw / vw, th / vh
    out = []
    for it in items:
        cmds = [c for pts, closed in it["subpaths"]
                for c in poly_cmds([(tx + (x - vx) * kx, ty + (y - vy) * ky) for x, y in pts], closed)]
        has_stroke = it["stroke"] and it["stroke_width"] > 0
        out.append(PathItem(cmds, fill=_rgba(it["fill"], ctx.brand) if it["fill"] else None,
                            rule="evenodd" if it["evenodd"] else "nonzero",
                            stroke=_rgba(it["stroke"], ctx.brand) if has_stroke else None,
                            stroke_w=it["stroke_width"] * (kx + ky) / 2 if has_stroke else 0.0,
                            under=False, opacity=float(it["opacity"])))
    return out


def _gradient_item(lay: dict, ctx) -> PathItem:
    w, h = ctx.w, ctx.h
    raw = lay["stops"]
    n = len(raw)
    stops = sorted(((float(s["pos"]) if isinstance(s, dict) and "pos" in s else i / (n - 1),
                     _rgba(s["color"] if isinstance(s, dict) else s, ctx.brand)) for i, s in enumerate(raw)),
                   key=lambda t: t[0])
    if lay.get("kind", "linear") == "radial":
        g = Grad("radial", (lay.get("cx", 0.5) * w, lay.get("cy", 0.5) * h),
                 lay.get("radius", 0.5) * float(math.hypot(w, h)), stops)
    else:
        a = math.radians(lay.get("angle", 90.0))
        dx, dy = math.cos(a), math.sin(a)
        L = abs(w * dx) + abs(h * dy)
        g = Grad("linear", (w / 2 - dx * L / 2, h / 2 - dy * L / 2), (w / 2 + dx * L / 2, h / 2 + dy * L / 2), stops)
    return PathItem(_rect(0, 0, w, h), fill=g)


def _text_items(lay: dict, ctx, text: str) -> list[PathItem]:
    from funkbox.reel import captions
    from .text import _fx, _line_x, layout_text
    from .typeset import is_advanced, typeset
    fonts = captions.fonts_for(ctx.brand, ctx.base_dir)
    if is_advanced(lay):
        ts = typeset(lay, text, ctx, fonts)
        base = _rgba(lay.get("color", "brand.text"), ctx.brand)
        st = _fx(lay, "stroke", ctx, ts.px, base[:3], {"width": 0.0, "opacity": 1.0, "color": "#000000"})
        stroke = (*st["rgb"], int(round(255 * st.get("opacity", 1.0)))) if st else None
        sw = 2 * round(st["width"] * ts.px) if st else 0.0
        return [PathItem(c, fill=col, stroke=stroke, stroke_w=sw, join="round", cap="round") for col, c in ts.by_color]
    lo, frame_w, _ = layout_text(lay, text, ctx, fonts)
    shaper = Shaper.get(fonts.path_for(lay.get("style", "caption"), text))
    cmds: list[Cmd] = []
    for i, (s, e) in enumerate(lo.lines):
        base = lo.y0 + i * lo.line_h + (lo.line_h + lo.cap_h) / 2
        cmds += shaper.line(lo.text[s:e], lo.px, lo.x0 + _line_x(lay, lo, i, frame_w), base)
    geo = {k: float(lay[k]) for k in ("rotate_deg", "skew_x", "skew_y") if lay.get(k)}
    if lay.get("arc"):
        geo["arc"] = float(lay["arc"]) * ctx.full[0]
    if geo:
        f = text_geometry((lo.x0 + frame_w / 2, lo.y0 + lo.box_h / 2), **geo)
        cmds = map_points(flatten(cmds) if "arc" in geo else cmds, f)
    fill = _rgba(lay.get("color", "brand.text"), ctx.brand)
    st = _fx(lay, "stroke", ctx, lo.px, fill[:3], {"width": 0.0, "opacity": 1.0, "color": "#000000"})
    sw = st["width"] * lo.px if st else 0.0
    stroke = (*st["rgb"], int(round(255 * st.get("opacity", 1.0)))) if st else None
    return [PathItem(cmds, fill=fill, stroke=stroke, stroke_w=2 * round(sw), join="round", cap="round")]


def _vector_ok(lay: dict) -> str | None:
    """Why this layer has to stay pixels (None = it can be drawn as vectors)."""
    t = lay["type"]
    if t not in ("solid", "border", "gradient", "shape", "svg", "text"):
        return f"a {t} layer is pixels"
    if fx.steps(lay.get("adjust"), lay.get("filters")):
        return "adjust/filters"
    if lay.get("mask"):
        return "mask"
    if t == "text" and any((lay.get("effects") or {}).get(k) for k in ("shadow", "glow")):
        return "text shadow/glow"
    return None


def _crop(rgba: np.ndarray, k: float) -> ImageItem | None:
    a = rgba[..., 3]
    ys, xs = np.nonzero(a)
    if not len(xs):
        return None
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    return ImageItem(rgba[y0:y1, x0:x1], (x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k))


def build(r, base_dir: str | Path | None = None) -> tuple[list[Group], dict]:
    """The poster as a display list of layer groups, plus a report: which layers are vectors, which are pixels."""
    from .render import make_context
    doc = r.doc
    W, H = doc.width, doc.height
    ctx = make_context(doc, base_dir if base_dir is not None else r.base_dir, W, H, 1.0)
    texts = {a["layer"]: a["text"] for a in r.audit}
    by_id = {}

    def collect(ls):
        for l in ls:
            by_id[l["id"]] = l
            if l["type"] == "group":
                collect(l.get("layers", []))
    collect(doc.layers)
    outs = {o.id: o for o in r.layers}
    k = W / r.width
    groups: list[Group] = []
    report = {"vector": [], "pixels": {}, "warnings": []}
    if doc.background != "transparent":
        groups.append(Group("background", [PathItem(_rect(0, 0, W, H), fill=_rgba(doc.background, ctx.brand))]))
    for lay in doc.layers:
        if lay.get("visible", True) is False or lay["id"] not in outs:
            continue
        g = Group(lay["id"], opacity=float(lay.get("opacity", 1.0)), blend=lay.get("blend", "normal"))
        why = _vector_ok(lay)
        if why is None:
            t = lay["type"]
            if t == "solid":
                g.items = [PathItem(_rect(0, 0, W, H), fill=_rgba(lay["color"], ctx.brand))]
            elif t == "border":
                b = max(1, round(ctx.px(lay["px"])))
                g.items = [PathItem(_rect(0, 0, W, H) + _rect(b, b, W - b, H - b), rule="evenodd",
                                    fill=_rgba(lay.get("color", "#000000"), ctx.brand))]
            elif t == "gradient":
                g.items = [_gradient_item(lay, ctx)]
            elif t == "shape":
                g.items = _shape_items(lay, ctx)
            elif t == "svg":
                g.items = _svg_items(lay, ctx)
            else:
                g.items = _text_items(lay, ctx, texts[lay["id"]])
            if lay.get("clip"):
                tgt = by_id[lay["clip"]]
                g.clip = _shape_items(tgt, ctx) if tgt["type"] == "shape" else _svg_items(tgt, ctx)
            report["vector"].append(lay["id"])
        else:
            img = _crop(outs[lay["id"]].rgba, k)
            g.items = [img] if img else []
            g.vector = False
            report["pixels"][lay["id"]] = why
        groups.append(g)
    if doc.post:
        report["warnings"].append("poster-level effects (" + ", ".join(p.get("type", "?") for p in doc.post)
                                  + ") are only in the pixel exports")
    return groups, report


# --------------------------------------------------------------------------- SVG
def _n(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def d_of(cmds: list[Cmd]) -> str:
    parts = []
    for c in cmds:
        parts.append(c[0] + (" ".join(f"{_n(p[0])},{_n(p[1])}" for p in c[1:])))
    return " ".join(parts)


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(c[:3])


def _png_b64(rgba: np.ndarray) -> str:
    b = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(b, format="PNG", optimize=True)
    return base64.b64encode(b.getvalue()).decode("ascii")


def _paint_attrs(prefix: str, col, defs: list[str], ids: list[int]) -> str:
    if isinstance(col, Grad):
        ids[0] += 1
        gid = f"g{ids[0]}"
        stops = "".join(f'<stop offset="{_n(p)}" stop-color="{_hex(c)}"'
                        + (f' stop-opacity="{_n(c[3] / 255)}"' if c[3] < 255 else "") + "/>" for p, c in col.stops)
        if col.kind == "linear":
            defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{_n(col.a[0])}" '
                        f'y1="{_n(col.a[1])}" x2="{_n(col.b[0])}" y2="{_n(col.b[1])}">{stops}</linearGradient>')
        else:
            defs.append(f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{_n(col.a[0])}" '
                        f'cy="{_n(col.a[1])}" r="{_n(col.b)}">{stops}</radialGradient>')
        return f' {prefix}="url(#{gid})"'
    s = f' {prefix}="{_hex(col)}"'
    return s + (f' {prefix}-opacity="{_n(col[3] / 255)}"' if col[3] < 255 else "")


def to_svg(groups: list[Group], W: int, H: int) -> str:
    defs: list[str] = []
    body: list[str] = []
    ids = [0]
    for g in groups:
        attrs = f' id="{_xml_id(g.id)}"'
        if g.opacity < 1:
            attrs += f' opacity="{_n(g.opacity)}"'
        if g.blend != "normal":
            attrs += f' style="mix-blend-mode:{CSS_BLEND[g.blend]}"'
        if g.clip:
            ids[0] += 1
            cid = f"c{ids[0]}"
            defs.append(f'<clipPath id="{cid}">' + "".join(
                f'<path d="{d_of(p.cmds)}" clip-rule="{p.rule}"/>' for p in g.clip) + "</clipPath>")
            attrs += f' clip-path="url(#{cid})"'
        inner = []
        for it in g.items:
            if isinstance(it, ImageItem):
                x, y, w, h = it.box
                inner.append(f'<image x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
                             f'preserveAspectRatio="none" href="data:image/png;base64,{_png_b64(it.rgba)}"/>')
                continue
            a = f'<path d="{d_of(it.cmds)}"'
            a += _paint_attrs("fill", it.fill, defs, ids) if it.fill is not None else ' fill="none"'
            if it.rule != "nonzero":
                a += f' fill-rule="{it.rule}"'
            if it.stroke is not None and it.stroke_w > 0:
                a += _paint_attrs("stroke", it.stroke, defs, ids)
                a += f' stroke-width="{_n(it.stroke_w)}" stroke-linejoin="{it.join}" stroke-linecap="{it.cap}"'
                if it.join == "miter":
                    a += ' stroke-miterlimit="4"'
                if it.under:
                    a += ' paint-order="stroke"'
            if it.opacity < 1:
                a += f' opacity="{_n(it.opacity)}"'
            inner.append(a + "/>")
        body.append(f"<g{attrs}>" + "".join(inner) + "</g>")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            + (f"<defs>{''.join(defs)}</defs>" if defs else "") + "\n" + "\n".join(body) + "\n</svg>\n")


def _xml_id(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s) or "layer"


# --------------------------------------------------------------------------- PDF
def to_pdf(groups: list[Group], W: int, H: int, path: Path, dpi: float = 96.0) -> list[str]:
    """Draw the display list into a PDF page of W x H pixels at ``dpi`` (96 = CSS pixels). Returns warnings."""
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas
    warns: list[str] = []
    k = 72.0 / dpi
    c = canvas.Canvas(str(path), pagesize=(W * k, H * k))
    c.setTitle(path.stem)
    c.transform(k, 0, 0, -k, 0, H * k)                # y down, canvas pixels

    def rl_path(cmds):
        p = c.beginPath()
        cur = (0.0, 0.0)
        for cm in cmds:
            op = cm[0]
            if op == "M":
                p.moveTo(*cm[1]); cur = cm[1]
            elif op == "L":
                p.lineTo(*cm[1]); cur = cm[1]
            elif op == "Q":
                (qx, qy), e = cm[1], cm[2]
                p.curveTo(cur[0] + 2 / 3 * (qx - cur[0]), cur[1] + 2 / 3 * (qy - cur[1]),
                          e[0] + 2 / 3 * (qx - e[0]), e[1] + 2 / 3 * (qy - e[1]), *e); cur = e
            elif op == "C":
                p.curveTo(*cm[1], *cm[2], *cm[3]); cur = cm[3]
            else:
                p.close()
        return p

    def col(rgba, alpha):
        return Color(rgba[0] / 255, rgba[1] / 255, rgba[2] / 255, alpha=rgba[3] / 255 * alpha)

    def fill_rule(rule):
        return 0 if rule == "evenodd" else 1          # reportlab: FILL_EVEN_ODD=0, FILL_NON_ZERO=1

    for g in groups:
        c.saveState()
        if g.blend != "normal":
            if g.blend in PDF_BLEND:
                c.setBlendMode(PDF_BLEND[g.blend])
            else:
                warns.append(f"layer {g.id!r}: blend {g.blend!r} has no PDF equivalent; drawn normally")
        if g.clip:
            cm_all = [cm for it in g.clip for cm in it.cmds]
            c.clipPath(rl_path(cm_all), stroke=0, fill=0, fillMode=1)
        for it in g.items:
            if isinstance(it, ImageItem):
                from reportlab.lib.utils import ImageReader
                x, y, w, h = it.box
                c.saveState()
                c.transform(1, 0, 0, -1, 0, 2 * y + h)       # images draw upright in a y-down space
                rgba = it.rgba
                if g.opacity < 1:
                    rgba = rgba.copy()
                    rgba[..., 3] = (rgba[..., 3] * g.opacity + 0.5).astype(np.uint8)
                c.drawImage(ImageReader(Image.fromarray(rgba, "RGBA")), x, y, w, h, mask="auto")
                c.restoreState()
                continue
            alpha = g.opacity * it.opacity
            p = rl_path(it.cmds)
            stroke = it.stroke is not None and it.stroke_w > 0
            if stroke and it.under:
                c.setStrokeColor(col(it.stroke, alpha))
                c.setLineWidth(it.stroke_w)
                c.setLineJoin(0 if it.join == "miter" else 1)
                c.setLineCap(0 if it.cap == "butt" else 1)
                c.setMiterLimit(4)
                c.drawPath(p, stroke=1, fill=0)
            if isinstance(it.fill, Grad):
                c.saveState()
                c.clipPath(p, stroke=0, fill=0, fillMode=fill_rule(it.rule))
                cols = [col(s[1], alpha) for s in it.fill.stops]
                pos = [s[0] for s in it.fill.stops]
                if it.fill.kind == "linear":
                    c.linearGradient(*it.fill.a, *it.fill.b, cols, pos, extend=True)
                else:
                    c.radialGradient(*it.fill.a, it.fill.b, cols, pos, extend=True)
                c.restoreState()
            elif it.fill is not None:
                c.setFillColor(col(it.fill, alpha))
                c.drawPath(p, stroke=0, fill=1, fillMode=fill_rule(it.rule))
            if stroke and not it.under:
                c.setStrokeColor(col(it.stroke, alpha))
                c.setLineWidth(it.stroke_w)
                c.setLineJoin(0 if it.join == "miter" else 1)
                c.drawPath(p, stroke=1, fill=0)
        c.restoreState()
    c.showPage()
    c.save()
    return warns


# --------------------------------------------------------------------------- entry point
def export(r, path: str | Path, base_dir: str | Path | None = None) -> dict:
    """Write the rendered poster ``r`` as .svg or .pdf (by the suffix). Returns the report
    {"vector": [layer ids], "pixels": {layer id: why}, "warnings": [...]}."""
    path = Path(path)
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in ("svg", "pdf"):
        raise ValueError(f"vector export writes .svg or .pdf, not {path.suffix!r}")
    groups, report = build(r, base_dir)
    W, H = r.doc.width, r.doc.height
    if fmt == "svg":
        tmp = path.with_suffix(".svg.tmp")
        tmp.write_text(to_svg(groups, W, H), encoding="utf-8")
        tmp.replace(path)
    else:
        report["warnings"] += to_pdf(groups, W, H, path)
    if report["pixels"]:
        report["warnings"].append("kept as pictures inside the " + fmt + ": "
                                  + ", ".join(f"{k} ({v})" for k, v in report["pixels"].items()))
    return report
