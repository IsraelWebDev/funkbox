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

import json
import re
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
    text_boxes: list[dict] = field(default_factory=list)   # lifted text lines: colour, box [x, y, w, h], angle


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
          tolerance: float = 0.8, speckle: int = 3, min_area: float = 12.0, measure: bool = True,
          lift_text: list[str] | str | None = None, max_letter: float = 0.12) -> Traced:
    """See the module doc. ``detail`` is the longest side traced (the SVG keeps the picture's own size),
    ``speckle`` the mode-filter size (0 = off), ``min_area`` drops loops smaller than that many traced pixels.
    ``lift_text`` (a list of text colours, or "auto" for every colour) takes lettering out of the artwork: small,
    letter-sized shapes of those colours (no side longer than ``max_letter`` of the picture) are filled in from what
    surrounds them before tracing, and their lines are returned in ``text_boxes`` so live text can go on top."""
    from .compare import _lab, kmeans_palette
    if not 0.01 <= max_letter <= 0.5:
        raise ValueError("max_letter: 0.01..0.5 of the picture's longest side")
    if lift_text is not None and lift_text != "auto":
        if not isinstance(lift_text, list) or not lift_text:
            raise ValueError('lift_text: "auto" or a list of colours ("#rrggbb" or {"color", "within": [x, y, w, h]})')
        for it in lift_text:
            c = it if isinstance(it, str) else (it.get("color") if isinstance(it, dict) else None)
            w = None if isinstance(it, str) else it.get("within")
            if not isinstance(c, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
                raise ValueError(f"lift_text: {it!r} needs a #rrggbb colour")
            if w is not None and not (isinstance(w, list) and len(w) == 4 and all(isinstance(v, (int, float)) for v in w)
                                      and w[2] > 0 and w[3] > 0):
                raise ValueError(f"lift_text: within is [x, y, w, h] as fractions of the picture, got {w!r}")
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
    boxes: list[dict] = []
    if lift_text:
        hole, boxes = _lift(label, cols, lift_text, max_letter, W / work.width)
        if hole.any():                                  # paint over the lettering smoothly, then re-colour just there
            filled = _inpaint(rgb, hole)
            ys, xs = np.nonzero(hole)
            near = np.argmin(((_lab(filled[ys, xs][None].astype(np.uint8))[0][:, None, :] - lab_c[None]) ** 2).sum(-1), 1)
            label = label.copy()
            label[ys, xs] = np.where(solid[ys, xs], near, 255)
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
    res = Traced(svg, pal, (W, H), nloops, text_boxes=boxes)
    if measure:
        res.preview = render_svg(svg, (W, H))
        from .compare import compare
        res.fidelity = round(float(compare(res.preview, _over_white(im)).scores["overall"]), 1)
    return res


# --------------------------------------------------------------------------- lifting text out of artwork
def _components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """8-connected components of a boolean mask: (labels, count). scipy when present, else flood fills."""
    try:
        from scipy import ndimage
        lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
        return lab, int(n)
    except ImportError:
        lab = np.zeros(mask.shape, np.int32)
        n = 0
        ys, xs = np.nonzero(mask)
        for y, x in zip(ys.tolist(), xs.tolist()):
            if lab[y, x]:
                continue
            n += 1
            stack = [(y, x)]
            lab[y, x] = n
            while stack:
                cy, cx = stack.pop()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        yy, xx = cy + dy, cx + dx
                        if 0 <= yy < mask.shape[0] and 0 <= xx < mask.shape[1] and mask[yy, xx] and not lab[yy, xx]:
                            lab[yy, xx] = n
                            stack.append((yy, xx))
        return lab, n


def _lift(label: np.ndarray, cols: np.ndarray, which, max_letter: float, scale: float):
    """(label map with the lettering filled in from its surroundings, the text lines found)."""
    H, W = label.shape
    jobs = []                                   # (palette entries, area allowed) per requested text colour
    full = (slice(0, H), slice(0, W))
    for item in (["auto"] if which == "auto" else which):
        if item == "auto":
            jobs.append((list(range(len(cols))), full))
            continue
        c, within = (item, None) if isinstance(item, str) else (item["color"], item.get("within"))
        d = np.sqrt(((cols - np.array(_parse_hex(c), float)) ** 2).sum(1))
        near = [int(i) for i in np.nonzero(d <= max(48.0, d.min() + 1))[0]]   # its shades, or at least the closest
        area = full
        if within:
            x, y, w, h = within
            area = (slice(int(y * H), int(np.ceil((y + h) * H))), slice(int(x * W), int(np.ceil((x + w) * W))))
        jobs.append((near, area))
    lim = max_letter * max(H, W)
    text = np.zeros((H, W), bool)
    sizes = []
    for idx, area in jobs:
        allowed = np.zeros((H, W), bool)
        allowed[area] = True
        lab, n = _components(np.isin(label, idx) & allowed)
        if not n:
            continue
        sl = _objects(lab, n)
        for j, (ys, xs) in enumerate(sl, start=1):
            if ys.stop - ys.start > lim or xs.stop - xs.start > lim:
                continue
            if ys.start <= area[0].start or xs.start <= area[1].start or ys.stop >= area[0].stop or xs.stop >= area[1].stop:
                continue
            text[ys, xs] |= lab[ys, xs] == j
            sizes.append(max(ys.stop - ys.start, xs.stop - xs.start))
    if not sizes:
        return label, []
    # lines: letters closer than about half a letter merge into one box (a gap between lines is wider than that)
    r = max(2, int(0.3 * float(np.median([z for z in sizes if z > 4] or sizes))))
    try:
        from scipy import ndimage
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        grown = ndimage.binary_dilation(text, structure=(xx * xx + yy * yy) <= r * r)
    except ImportError:
        grown = np.asarray(Image.fromarray((text * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * r + 1))) > 0
    lab, n = _components(grown)
    boxes = []
    for j, (ys, xs) in enumerate(_objects(lab, n), start=1):
        part = text[ys, xs] & (lab[ys, xs] == j)
        if part.sum() < 30:
            continue
        py, px = np.nonzero(part)
        colour = np.bincount(label[ys, xs][part].ravel(), minlength=len(cols)).argmax()
        ang = 0.0
        cov = np.cov(np.stack([px, py]).astype(float)) if len(px) > 10 else np.eye(2)
        ev, vec = np.linalg.eigh(cov)
        if ev[-1] > 4 * max(ev[0], 1e-9):                       # a line has a direction; a lone letter has none
            v = vec[:, -1]
            ang = float(np.degrees(np.arctan2(v[1], v[0])))
            ang = (ang + 90) % 180 - 90
        boxes.append({"color": _hex(cols[colour]), "angle": round(ang, 1), "letters": int(_components(part)[1]),
                      "box": [round((xs.start + px.min()) * scale), round((ys.start + py.min()) * scale),
                              round((px.max() + 1 - px.min()) * scale), round((py.max() + 1 - py.min()) * scale)]})
    boxes.sort(key=lambda b: (b["box"][1], b["box"][0]))
    # the lettering and its anti-aliased fringe
    hole = np.asarray(Image.fromarray((text * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    return hole, boxes


def _inpaint(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Fill ``hole`` from the pixels around it, smoothly (gradients carry on through): a coarse-to-fine average."""
    def fill(img, known):
        h, w = known.shape
        if known.all() or not known.any():
            return img if known.any() else np.broadcast_to(img.mean((0, 1)), img.shape).copy()
        if min(h, w) <= 4:
            out = img.copy()
            out[~known] = img[known].mean(0)
            return out
        H2, W2 = (h + 1) // 2, (w + 1) // 2
        pad_i = np.zeros((H2 * 2, W2 * 2, 3))
        pad_k = np.zeros((H2 * 2, W2 * 2))
        pad_i[:h, :w] = img * known[..., None]
        pad_k[:h, :w] = known
        s_i = pad_i.reshape(H2, 2, W2, 2, 3).sum((1, 3))
        s_k = pad_k.reshape(H2, 2, W2, 2).sum((1, 3))
        small = fill(np.where(s_k[..., None] > 0, s_i / np.maximum(s_k, 1)[..., None], 0), s_k > 0)
        up = small.repeat(2, 0).repeat(2, 1)[:h, :w]
        out = np.where(known[..., None], img, up)
        for _ in range(3):                              # smooth the filled part only
            p = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
            avg = sum(p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] for dy in (-1, 0, 1) for dx in (-1, 0, 1)) / 9
            out = np.where(known[..., None], img, avg)
        return out
    return fill(rgb.astype(np.float64), ~hole)


def _objects(lab: np.ndarray, n: int):
    try:
        from scipy import ndimage
        return [s if s is not None else (slice(0, 0), slice(0, 0)) for s in ndimage.find_objects(lab, n)]
    except ImportError:
        out = []
        for j in range(1, n + 1):
            ys, xs = np.nonzero(lab == j)
            out.append((slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1)))
        return out


def poster_with_text(res: Traced, art_path: str, texts: list[str], font: str = "Rubik:700") -> dict:
    """A starter poster: the traced artwork as an svg layer and ``texts`` (in reading order: top to bottom) set in
    the lifted text boxes, in their colours and angles. Fit it with poster_fit to line the words up."""
    W, H = res.size
    layers: list[dict] = [{"id": "art", "type": "svg", "path": art_path, "x": 0, "y": 0, "width": 1.0}]
    for i, (t, b) in enumerate(zip(texts, res.text_boxes)):
        x, y, w, h = b["box"]
        lay = {"id": f"text{i + 1}", "type": "text", "text": t, "font": font, "color": b["color"],
               "x": round(max(0.0, x / W - 0.01), 4), "y": round(y / H, 4), "max_width": round(min(1.0, w / W + 0.02), 4),
               "size": round(min(0.5, max(0.012, h / H * (0.75 if abs(b["angle"]) < 8 else 0.45))), 4),
               "align": "center", "wrap": False}
        if abs(b["angle"]) >= 8:
            lay["skew_y"] = b["angle"]
        layers.append(lay)
    doc = {"version": 1, "casual": True, "canvas": {"width": W, "height": H, "background": "#ffffff"}, "layers": layers}
    small = min([l["size"] for l in layers[1:]] or [0.032])
    if small < 0.032:                                   # emblems carry small print; keep it as small as the original
        doc["min_text"] = round(max(0.012, small), 4)
    return doc


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


def write(res: Traced, out_dir, name: str, texts: list[str] | None = None, font: str = "Rubik:700") -> dict[str, Path]:
    """Write <name>.svg and <name>.trace.png; with lifted text also <name>.text.json, and with ``texts`` (the words
    of the lifted lines, top to bottom) a starter poster <name>.poster.json to fit with poster_fit."""
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
    if res.text_boxes:
        paths["text"] = d / f"{name}.text.json"
        paths["text"].write_text(json.dumps({"size": list(res.size), "lines": res.text_boxes}, indent=1), encoding="utf-8")
    if texts:
        if len(texts) > len(res.text_boxes):
            raise ValueError(f"texts: {len(texts)} given but {len(res.text_boxes)} text lines were found")
        doc = poster_with_text(res, paths["svg"].name, texts, font)
        doc["name"] = name
        paths["poster"] = d / f"{name}.poster.json"
        paths["poster"].write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return paths
