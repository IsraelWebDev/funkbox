"""Trace a picture (a logo, an emblem, a flat illustration) into vector shapes: an SVG of a few flat colours that stays
sharp at any size and can be placed, clipped and recoloured as an ``svg`` layer.

    res = trace("logo.png", colors=6)           # or palette=["#1b224b", "#fdd94e", ...]
    res.svg      -> SVG text, one <path> per colour, stacked (big areas first, each extended under the later ones
                    so no hairline gaps show between colours)
    res.palette  -> the colours used with their share of the picture
    res.fidelity -> poster_compare's overall score of the traced picture against the original (0..100)

How: the picture is reduced to its main colours (k-means in Lab, or the palette given), small specks are smoothed
away (a mode filter), each colour's region is traced along the pixel edges into closed loops (holes wind the other way,
so the nonzero rule fills them right), the staircase is removed (edge midpoints), the loops are simplified
(Ramer-Douglas-Peucker, ``tolerance`` in pixels) and, with ``smooth`` > 0, the runs between corners become cubic
curves. Photos trace into posterised art; for a clean logo use a clean, large source.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

MAX_COLORS = 16


@dataclass
class Traced:
    svg: str
    palette: list[dict]
    size: tuple[int, int]
    loops: int
    fidelity: float | None = None
    preview: Image.Image | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- pixel edges -> loops
_DIRS = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}      # E, S, W, N (y down)


def loops_of(mask: np.ndarray) -> list[np.ndarray]:
    """Closed loops along the pixel edges of ``mask`` (vertex coordinates, y down). The filled side is on the right
    of travel: outer loops have positive shoelace area, holes negative."""
    m = np.pad(mask.astype(bool), 1)
    h, w = m.shape
    inner = m[1:-1, 1:-1]
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(sel, a, b):
        rs, cs = np.nonzero(sel)
        for r, c in zip(rs.tolist(), cs.tolist()):
            p, q = (c + a[0], r + a[1]), (c + b[0], r + b[1])
            edges.setdefault(p, []).append(q)
    add(inner & ~m[:-2, 1:-1], (0, 0), (1, 0))            # top: east
    add(inner & ~m[2:, 1:-1], (1, 1), (0, 1))             # bottom: west
    add(inner & ~m[1:-1, :-2], (0, 1), (0, 0))            # left: north
    add(inner & ~m[1:-1, 2:], (1, 0), (1, 1))             # right: south
    out = []
    while edges:
        start = next(iter(edges))
        pts = [start]
        cur, prev_d = start, None
        while True:
            nxts = edges.get(cur)
            if not nxts:
                break
            if len(nxts) == 1 or prev_d is None:
                q = nxts.pop(0)
            else:                                        # a saddle: turn right first (keeps diagonal pixels apart)
                def rank(q):
                    d = _DIRS[(q[0] - cur[0], q[1] - cur[1])]
                    return (d - prev_d) % 4                  # 1 = right turn (y down), 0 straight, 3 left
                q = min(nxts, key=lambda q: {1: 0, 0: 1, 3: 2}.get(rank(q), 3))
                nxts.remove(q)
            if not nxts:
                del edges[cur]
            prev_d = _DIRS[(q[0] - cur[0], q[1] - cur[1])]
            cur = q
            if cur == start:
                break
            pts.append(cur)
        if len(pts) >= 4:
            out.append(np.array(pts, np.float64))
    return out


def area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _rdp(p: np.ndarray, tol: float) -> np.ndarray:
    if len(p) < 3:
        return p
    a, b = p[0], p[-1]
    ab = b - a
    n = math.hypot(*ab)
    d = (np.abs(ab[0] * (p[:, 1] - a[1]) - ab[1] * (p[:, 0] - a[0])) / n if n > 1e-12
         else np.hypot(p[:, 0] - a[0], p[:, 1] - a[1]))
    i = int(np.argmax(d))
    if d[i] <= tol:
        return np.array([a, b])
    return np.vstack([_rdp(p[: i + 1], tol)[:-1], _rdp(p[i:], tol)])


def simplify(loop: np.ndarray, tol: float) -> np.ndarray:
    """Midpoints of the pixel edges (no staircase), then RDP on the closed loop."""
    mid = (loop + np.roll(loop, -1, axis=0)) / 2
    if tol <= 0 or len(mid) < 8:
        return mid
    far = int(np.argmax(np.hypot(*(mid - mid[0]).T)))
    a = _rdp(np.vstack([mid[: far + 1]]), tol)
    b = _rdp(np.vstack([mid[far:], mid[:1]]), tol)
    return np.vstack([a[:-1], b[:-1]])


def to_d(p: np.ndarray, smooth: float, k: float = 1.0, cap: float = 12.0) -> str:
    """SVG path data for one closed loop: straight segments, or with ``smooth`` > 0 cubic curves through the points
    (Catmull-Rom), keeping turns sharper than 35 degrees as corners; a handle is at most ``cap`` long, so long
    straight runs stay straight."""
    p = p * k
    f = lambda v: f"{v:.2f}".rstrip("0").rstrip(".")
    n = len(p)
    if smooth <= 0 or n < 4:
        return "M" + " L".join(f"{f(x)},{f(y)}" for x, y in p) + " Z"
    v_in = p - np.roll(p, 1, axis=0)
    v_out = np.roll(p, -1, axis=0) - p
    cosang = (v_in * v_out).sum(1) / np.maximum(np.hypot(*v_in.T) * np.hypot(*v_out.T), 1e-9)
    corner = cosang < math.cos(math.radians(35))
    parts = [f"M{f(p[0][0])},{f(p[0][1])}"]
    t = smooth / 3.0

    def tangent(i: int) -> np.ndarray:                  # unit direction through point i (Catmull-Rom, normalised)
        d = p[(i + 1) % n] - p[i - 1]
        return d / max(math.hypot(*d), 1e-9)
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        L = min(math.hypot(*(b - a)) * t, cap * k)
        c1 = a if corner[i] else a + tangent(i) * L
        c2 = b if corner[(i + 1) % n] else b - tangent((i + 1) % n) * L
        parts.append(f"C{f(c1[0])},{f(c1[1])} {f(c2[0])},{f(c2[1])} {f(b[0])},{f(b[1])}")
    return " ".join(parts) + " Z"


# --------------------------------------------------------------------------- picture -> colours -> SVG
def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in c[:3])


def _parse_hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) not in (6, 8):
        raise ValueError(f"palette colour {h!r}: use #rrggbb")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def trace(src, colors: int = 6, palette: list[str] | None = None, detail: int = 1024, smooth: float = 1.0,
          tolerance: float = 0.8, speckle: int = 3, min_area: float = 12.0, measure: bool = True) -> Traced:
    """See the module doc. ``detail`` is the longest side traced (the SVG keeps the picture's own size),
    ``speckle`` the mode-filter size (0 = off), ``min_area`` drops loops smaller than that many traced pixels."""
    from .compare import _lab, kmeans_palette
    if isinstance(src, Image.Image):
        im = src
    else:
        from funkbox.config import resolve_path
        im = Image.open(resolve_path(str(src)))
    im = ImageOps.exif_transpose(im).convert("RGBA")
    W, H = im.size
    k = min(1.0, detail / max(W, H))
    work = im.resize((max(8, round(W * k)), max(8, round(H * k))), Image.LANCZOS) if k < 1 else im
    arr = np.asarray(work, np.float64)
    solid = arr[..., 3] >= 128
    rgb = arr[..., :3]
    if palette:
        cols = np.array([_parse_hex(c) for c in palette], np.float64)
    else:
        if not 2 <= colors <= MAX_COLORS:
            raise ValueError(f"colors must be 2..{MAX_COLORS}")
        cols, _ = kmeans_palette(rgb[solid].reshape(-1, 1, 3).astype(np.uint8) if solid.any() else rgb, colors)
    lab_px, lab_c = _lab(rgb.astype(np.uint8)), _lab(np.clip(cols + 0.5, 0, 255).astype(np.uint8))
    label = np.argmin(((lab_px[..., None, :] - lab_c[None, None]) ** 2).sum(-1), axis=-1).astype(np.uint8)
    if speckle and speckle >= 3:
        size = speckle | 1
        label = np.asarray(Image.fromarray(label).filter(ImageFilter.ModeFilter(size)))
    label = np.where(solid, label, 255)
    counts = np.array([(label == i).sum() for i in range(len(cols))])
    order = [int(i) for i in np.argsort(-counts) if counts[i] > 0]
    sx, sy = W / work.width, H / work.height
    paths, nloops, pal = [], 0, []
    for n, ci in enumerate(order):
        mask = np.isin(label, order[n:])                   # this colour and everything drawn over it: no gaps
        loops = [l for l in loops_of(mask) if abs(area(l)) >= min_area]
        ds = []
        for l in loops:
            p = simplify(l, tolerance)
            if len(p) >= 3:
                p = p * [sx, sy]
                ds.append(to_d(p, smooth, cap=12.0 * max(sx, sy)))
        if ds:
            nloops += len(ds)
            paths.append(f'<path fill="{_hex(cols[ci])}" d="{" ".join(ds)}"/>')
        pal.append({"color": _hex(cols[ci]), "share": round(float(counts[ci] / max(1, solid.sum())), 4)})
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n'
           + "\n".join(paths) + "\n</svg>\n")
    res = Traced(svg, pal, (W, H), nloops)
    if measure:
        res.preview = render_svg(svg, (W, H))
        from .compare import compare
        res.fidelity = round(float(compare(res.preview, _over_white(im)).scores["overall"]), 1)
    return res


def _over_white(im: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, im.convert("RGBA")).convert("RGB")


def render_svg(svg: str, size: tuple[int, int]) -> Image.Image:
    """Rasterise a traced SVG with funkbox's own SVG reader (no browser), on white."""
    import tempfile
    from .raster import RCtx
    from .shapes import _Paint
    from .typeset import coverage
    from .vector import poly_cmds
    from .vpath import svg_items
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.svg"
        p.write_text(svg, encoding="utf-8")
        items, (vx, vy, vw, vh), _ = svg_items(p, tol=0.5)
    W, H = size
    ctx = RCtx(w=W, h=H, full=(W, H))
    paint = _Paint(ctx)
    for it in items:
        if it["fill"]:
            cmds = [c for pts, _ in it["subpaths"] for c in poly_cmds(pts)]
            paint.over(coverage(cmds, W, H), (*_parse_hex(it["fill"]), 255))
    return _over_white(Image.fromarray(paint.rgba(ctx), "RGBA"))


def write(res: Traced, out_dir, name: str) -> dict[str, Path]:
    from .render import NAME_RE, fence_out
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad output name {name!r}")
    d = fence_out(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths = {"svg": d / f"{name}.svg"}
    paths["svg"].write_text(res.svg, encoding="utf-8")
    if res.preview is not None:
        paths["preview"] = d / f"{name}.trace.png"
        res.preview.save(paths["preview"])
    return paths
