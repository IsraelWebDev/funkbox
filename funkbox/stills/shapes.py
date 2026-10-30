"""Vector shapes for posters, and the geometric transforms of text (slant, rotate, bend along an arc).

Shape layer (all coordinates are fractions of the canvas: x of the width, y of the height):

    {"type": "shape", "kind": "polygon", "points": [[x, y], ...], "fill": "#fdd94e", "stroke": "#1b224b", "stroke_px": 6}
    {"type": "shape", "kind": "rect", "points": [[x0, y0], [x1, y1]], ...}
    {"type": "shape", "kind": "ellipse", "points": [[x0, y0], [x1, y1]], ...}           # its bounding box
    {"type": "shape", "kind": "polyline", "points": [...], "width": 0.04, "fill": ..., "stroke": ..., "stroke_px": 4,
     "cap": "square"|"butt"}

    {"type": "shape", "kind": "path", "d": "M0.2,0.2 C0.4,0.0 0.6,0.4 0.8,0.2 L0.8,0.8 Z", "fill": ..., "fill_rule": "nonzero"}

A path takes SVG path data (M L H V C S Q T A Z, absolute or relative), in canvas fractions, or in canvas pixels
with "units": "px" (so arcs stay round on a non-square canvas). An svg layer places a whole SVG file:

    {"type": "svg", "path": "logo.svg", "x": 0.1, "y": 0.1, "width": 0.5}      # height follows its viewBox

A polyline is a thick stroke ``width`` (a fraction of the canvas width) with mitred corners, so straight-bar lettering
and ribbons can be drawn; its ``stroke`` outlines the whole bar. Shapes are drawn supersampled, so edges are smooth.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from .raster import RCtx, rgba_of, transparent

KINDS = ("polygon", "rect", "ellipse", "polyline", "path")
SS_MAX = 8192                     # largest supersampled side


def _ss(ctx: RCtx) -> int:
    return max(1, min(4, SS_MAX // max(ctx.w, ctx.h)))


def bar_polygons(pts: list[tuple[float, float]], half: float, cap: str = "square") -> list[list[tuple[float, float]]]:
    """Polygons whose union is a stroke of half-width ``half`` along ``pts`` with mitred joins."""
    polys: list[list[tuple[float, float]]] = []
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1) if pts[i] != pts[i + 1]]
    if not segs:
        return polys
    dirs = []
    for (ax, ay), (bx, by) in segs:
        L = math.hypot(bx - ax, by - ay)
        dirs.append(((bx - ax) / L, (by - ay) / L))
    for k, ((a, b), (ux, uy)) in enumerate(zip(segs, dirs)):
        nx, ny = -uy * half, ux * half
        ax, ay = a
        bx, by = b
        if cap == "square" and k == 0:
            ax, ay = ax - ux * half, ay - uy * half
        if cap == "square" and k == len(segs) - 1:
            bx, by = bx + ux * half, by + uy * half
        polys.append([(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)])
    for k in range(len(segs) - 1):                     # fill each corner with its mitre on both sides
        px, py = segs[k][1]
        (u1x, u1y), (u2x, u2y) = dirs[k], dirs[k + 1]
        for side in (1, -1):
            n1 = (-u1y * side, u1x * side)
            n2 = (-u2y * side, u2x * side)
            mx, my = n1[0] + n2[0], n1[1] + n2[1]
            ml = math.hypot(mx, my)
            if ml < 1e-9:
                continue
            cos_half = (mx * n1[0] + my * n1[1]) / ml
            length = min(half / max(cos_half, 1e-6), 4 * half)
            polys.append([(px, py), (px + n1[0] * half, py + n1[1] * half),
                          (px + mx / ml * length, py + my / ml * length), (px + n2[0] * half, py + n2[1] * half)])
    return polys


def _mask(ctx: RCtx, draw_fn) -> np.ndarray:
    s = _ss(ctx)
    im = Image.new("L", (ctx.w * s, ctx.h * s), 0)
    draw_fn(ImageDraw.Draw(im), s)
    if s > 1:
        im = im.resize((ctx.w, ctx.h), Image.BOX)
    return np.asarray(im, np.float32) / 255.0


def _geometry(lay: dict, ctx: RCtx, grow: float):
    """Draw function for the shape grown by ``grow`` canvas pixels (the outline is the shape grown by the stroke)."""
    kind = lay["kind"]
    if kind == "path":
        subs = path_px(lay, ctx)

        def draw_path(d: ImageDraw.ImageDraw, s: int) -> None:          # outline: a bar along every subpath
            for pts, closed in subs:
                S = [(x * s, y * s) for x, y in pts]
                for poly in bar_polygons(S + ([S[0], S[1]] if closed and len(S) > 2 else []), grow * s, "butt"):
                    d.polygon(poly, fill=255)
        return draw_path
    P = [(float(x) * ctx.w, float(y) * ctx.h) for x, y in lay["points"]]

    def draw(d: ImageDraw.ImageDraw, s: int) -> None:
        S = [(x * s, y * s) for x, y in P]
        g = grow * s
        if kind == "polyline":
            half = float(lay.get("width", 0.03)) * ctx.w * s / 2 + g
            for poly in bar_polygons(S, half, lay.get("cap", "square")):
                d.polygon(poly, fill=255)
        elif kind == "polygon":
            d.polygon(S, fill=255)
            if g > 0:
                for poly in bar_polygons(S + [S[0], S[1]], g, "butt"):
                    d.polygon(poly, fill=255)
        else:
            (x0, y0), (x1, y1) = S
            box = [min(x0, x1) - g, min(y0, y1) - g, max(x0, x1) + g, max(y0, y1) + g]
            (d.rectangle if kind == "rect" else d.ellipse)(box, fill=255)
    return draw


def path_px(lay: dict, ctx: RCtx) -> list[tuple[list[tuple[float, float]], bool]]:
    """A path layer's subpaths in render pixels."""
    from .vpath import parse
    px = lay.get("units", "fraction") == "px"
    sx, sy = (ctx.scale, ctx.scale) if px else (ctx.w, ctx.h)
    tol = 0.5 / max(sx, sy) if not px else 0.5 / max(ctx.scale, 1e-6)
    return [([(x * sx, y * sy) for x, y in pts], c) for pts, c in parse(lay["d"], tol)]


def fill_mask(subs, ctx: RCtx, rule: str = "nonzero") -> np.ndarray:
    """Coverage of filled subpaths. nonzero: each subpath adds its winding (+1 or -1); evenodd: overlaps cancel."""
    s = _ss(ctx)
    acc = np.zeros((ctx.h * s, ctx.w * s), np.int16)
    for pts, _ in subs:
        if len(pts) < 3:
            continue
        im = Image.new("L", (ctx.w * s, ctx.h * s), 0)
        ImageDraw.Draw(im).polygon([(x * s, y * s) for x, y in pts], fill=1)
        m = np.asarray(im, np.int16)
        area = sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))
        acc += m if (rule == "evenodd" or area >= 0) else -m
    cov = ((acc % 2) != 0) if rule == "evenodd" else (acc != 0)
    im = Image.fromarray((cov * 255).astype(np.uint8))
    if s > 1:
        im = im.resize((ctx.w, ctx.h), Image.BOX)
    return np.asarray(im, np.float32) / 255.0


def stroke_mask(subs, half: float, ctx: RCtx) -> np.ndarray:
    def draw(d: ImageDraw.ImageDraw, s: int) -> None:
        for pts, closed in subs:
            S = [(x * s, y * s) for x, y in pts]
            for poly in bar_polygons(S + ([S[0], S[1]] if closed and len(S) > 2 else []), half * s, "butt"):
                d.polygon(poly, fill=255)
    return _mask(ctx, draw)


class _Paint:
    """Premultiplied 'over' compositing of coloured coverage masks."""

    def __init__(self, ctx: RCtx):
        self.P = np.zeros((ctx.h, ctx.w, 3), np.float32)
        self.A = np.zeros((ctx.h, ctx.w), np.float32)

    def over(self, mask: np.ndarray, rgba: tuple[int, int, int, int], opacity: float = 1.0) -> None:
        a = mask * (rgba[3] / 255.0) * opacity
        self.P = self.P * (1 - a[..., None]) + np.array(rgba[:3], np.float32) * a[..., None]
        self.A = self.A * (1 - a) + a

    def rgba(self, ctx: RCtx) -> np.ndarray:
        out = transparent(ctx.w, ctx.h)
        A = self.A[..., None]
        out[..., :3] = np.clip(np.where(A > 0, self.P / np.maximum(A, 1e-9), 0) + 0.5, 0, 255).astype(np.uint8)
        out[..., 3] = np.clip(self.A * 255 + 0.5, 0, 255).astype(np.uint8)
        return out


def raster_shape(lay: dict, ctx: RCtx) -> np.ndarray:
    sw = ctx.px(float(lay.get("stroke_px", 0))) if lay.get("stroke") else 0.0
    paint = _Paint(ctx)
    if sw > 0:
        paint.over(_mask(ctx, _geometry(lay, ctx, sw)) if lay["kind"] != "path"
                   else np.maximum(stroke_mask(path_px(lay, ctx), sw, ctx),
                                   fill_mask(path_px(lay, ctx), ctx, lay.get("fill_rule", "nonzero"))),
                   rgba_of(lay["stroke"], ctx.brand))
    if lay.get("fill"):
        m = (fill_mask(path_px(lay, ctx), ctx, lay.get("fill_rule", "nonzero")) if lay["kind"] == "path"
             else _mask(ctx, _geometry(lay, ctx, 0.0)))
        paint.over(m, rgba_of(lay["fill"], ctx.brand))
    return paint.rgba(ctx)


def raster_svg(lay: dict, ctx: RCtx) -> np.ndarray:
    """Place an SVG file: x, y (top-left) and width as canvas fractions; height from its viewBox unless given."""
    from ..config import resolve_path
    from .vpath import svg_items
    path = resolve_path(lay["path"], ctx.base_dir)
    items, (vx, vy, vw, vh), warns = svg_items(path, tol=0.25)
    ctx.warnings += [f"svg {lay['id']!r}: {w}" for w in warns]
    tw = float(lay.get("width", 0.5)) * ctx.w
    th = float(lay["height"]) * ctx.h if "height" in lay else tw * vh / vw
    tx, ty = float(lay.get("x", 0.0)) * ctx.w, float(lay.get("y", 0.0)) * ctx.h
    k = (tw / vw, th / vh)
    paint = _Paint(ctx)
    for it in items:
        subs = [([(tx + (x - vx) * k[0], ty + (y - vy) * k[1]) for x, y in pts], c) for pts, c in it["subpaths"]]
        if it["fill"]:
            paint.over(fill_mask(subs, ctx, "evenodd" if it["evenodd"] else "nonzero"),
                       rgba_of(it["fill"], ctx.brand), it["opacity"])
        if it["stroke"] and it["stroke_width"] > 0:
            paint.over(stroke_mask(subs, it["stroke_width"] * (k[0] + k[1]) / 4, ctx), rgba_of(it["stroke"], ctx.brand),
                       it["opacity"])
    return paint.rgba(ctx)


def check_shape(lay: dict, w: str, errs: list[str], is_color, num) -> None:
    kind = lay.get("kind")
    if kind not in KINDS:
        errs.append(f"{w}: shape needs `kind`: one of {list(KINDS)}")
        return
    if kind == "path":
        from .vpath import PathError, parse
        try:
            parse(lay.get("d") or "")
        except PathError as e:
            errs.append(f"{w}: {e}")
        if lay.get("fill_rule", "nonzero") not in ("nonzero", "evenodd"):
            errs.append(f"{w}: fill_rule must be nonzero or evenodd")
        if lay.get("units", "fraction") not in ("fraction", "px"):
            errs.append(f"{w}: units must be fraction or px")
        if "points" in lay:
            errs.append(f"{w}: a path takes `d` (SVG path data), not `points`")
        lay = {k: v for k, v in lay.items() if k != "kind"}
        for k in ("fill", "stroke"):
            if k in lay and not is_color(lay[k], alpha=True):
                errs.append(f"{w}: {k} must be #rrggbb[aa] or brand.primary|secondary|text")
        if not lay.get("fill") and not lay.get("stroke"):
            errs.append(f"{w}: a shape needs `fill`, `stroke` or both")
        if lay.get("stroke") and not num(lay.get("stroke_px", 0), 0.01, 200):
            errs.append(f"{w}: a stroke needs `stroke_px` (0.01..200)")
        return
    pts = lay.get("points")
    ok = isinstance(pts, list) and all(isinstance(p, (list, tuple)) and len(p) == 2 and all(num(v, -2, 3) for v in p)
                                       for p in pts)
    need = {"polygon": 3, "polyline": 2, "rect": 2, "ellipse": 2}[kind]
    if not ok or len(pts) < need or (kind in ("rect", "ellipse") and len(pts) != 2):
        errs.append(f"{w}: points must be {'exactly' if kind in ('rect', 'ellipse') else 'at least'} {need} [x, y] "
                    "pairs, as fractions of the canvas (-2..3)")
    if not lay.get("fill") and not lay.get("stroke"):
        errs.append(f"{w}: a shape needs `fill`, `stroke` or both")
    for k in ("fill", "stroke"):
        if k in lay and not is_color(lay[k], alpha=True):
            errs.append(f"{w}: {k} must be #rrggbb[aa] or brand.primary|secondary|text")
    if "stroke_px" in lay and not num(lay["stroke_px"], 0, 200):
        errs.append(f"{w}: stroke_px must be 0..200")
    if lay.get("stroke") and not num(lay.get("stroke_px", 0), 0.01, 200):
        errs.append(f"{w}: a stroke needs `stroke_px` (0.01..200)")
    if kind == "polyline":
        if "width" in lay and not num(lay["width"], 0.001, 1):
            errs.append(f"{w}: width must be 0.001..1 (a fraction of the canvas width)")
        if lay.get("cap", "square") not in ("square", "butt"):
            errs.append(f"{w}: cap must be square or butt")


# --------------------------------------------------------------------------- text geometry
def warp_text(rgba: np.ndarray, centre: tuple[float, float], *, arc: float = 0.0, rotate_deg: float = 0.0,
              skew_x: float = 0.0, skew_y: float = 0.0) -> np.ndarray:
    """Bend (arc, a radius in pixels: + bows the line down at the middle like a smile, - like a frown), then slant
    (skew_x/skew_y in degrees) and rotate (degrees, counter-clockwise) a rendered text layer about ``centre``.
    The line is laid out and shaped first, so letters, kerning and right-to-left order are untouched."""
    H, W = rgba.shape[:2]
    cx, cy = centre
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    # inverse map: output pixel -> where it comes from. Undo rotate, then skew, then the arc.
    x, y = xx - cx, yy - cy
    if rotate_deg:
        t = math.radians(rotate_deg)
        x, y = x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t)
    if skew_x or skew_y:
        a, b = math.tan(math.radians(skew_x)), math.tan(math.radians(skew_y))
        det = 1 - a * b
        x, y = (x - a * y) / det, (y - b * x) / det
    if arc:
        R = abs(arc)
        if arc > 0:                                     # centre of the circle above the line
            vx, vy = x, y + R
            theta, rad = np.arctan2(vx, vy), np.hypot(vx, vy)
            x, y = R * theta, rad - R
        else:
            vx, vy = x, y - R
            theta, rad = np.arctan2(vx, -vy), np.hypot(vx, vy)
            x, y = R * theta, R - rad
    sx, sy = x + cx, y + cy
    return _sample(rgba, sx, sy)


def _sample(rgba: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Bilinear sampling with premultiplied alpha (no dark fringes); outside the source is transparent."""
    H, W = rgba.shape[:2]
    src = rgba.astype(np.float32)
    a = src[..., 3:4] / 255.0
    pm = np.concatenate([src[..., :3] * a, a * 255.0], -1)
    x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
    acc = np.zeros(sx.shape + (4,), np.float32)
    for dx, dy, wgt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        xi, yi = x0 + dx, y0 + dy
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
        v = np.zeros(sx.shape + (4,), np.float32)
        v[ok] = pm[yi[ok], xi[ok]]
        acc += v * wgt
    alpha = acc[..., 3:4]
    out = np.zeros_like(rgba)
    out[..., :3] = np.clip(np.where(alpha > 0, acc[..., :3] / np.maximum(alpha / 255.0, 1e-6), 0) + 0.5, 0, 255)
    out[..., 3] = np.clip(alpha[..., 0] + 0.5, 0, 255)
    return out
