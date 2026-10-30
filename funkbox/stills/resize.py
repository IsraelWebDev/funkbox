"""One poster, many sizes: adapt a design to other canvases (story, landscape, square, A4 ...) without distorting it.

Backgrounds (solid, gradient, shader, border, image) stretch to fill the new canvas. Everything else (text, shapes,
SVGs, products, groups' contents) keeps its proportions: the original design is scaled to fit the new canvas and
centred, so circles stay round and text keeps its relative size. A layer can choose its own rule with ``"resize"``:

    "fill"      stretch with the canvas (default for backgrounds)
    "fit"       keep proportions, centred (default for the rest)
    "top" / "bottom" / "left" / "right"     keep proportions, pinned to that edge (a band, a footer, a logo corner)
    "top-left", "top-right", "bottom-left", "bottom-right"

After adapting, ``set`` patches per size use the variations keys (``layers.<id>.<prop>``, ``canvas.<k>``, ...), in the
new canvas's fractions, e.g. {"story": {"layers.title.y": 0.12, "layers.title.size": 0.07}}.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import render, schema
from .sheet import contact_sheet
from .text import TEXT_SIZE
from .variations import VaryError, set_path
from .vpath import transform_d

RULES = schema.RESIZE_RULES
FILL_TYPES = {"solid", "gradient", "shader", "border", "image"}
MAX_SIZES = 12


@dataclass
class Frame:
    """Where the original design lands in the new canvas, for one rule: old fraction v -> o + v * s_."""
    sx: float          # new fraction per old fraction, x  (s * W0 / W1)
    sy: float          # y                                (s * H0 / H1)
    ox: float          # where old x = 0 lands, new fraction of width
    oy: float
    s: float           # pixel scale

    def x(self, v: float) -> float:
        return self.ox + float(v) * self.sx

    def y(self, v: float) -> float:
        return self.oy + float(v) * self.sy


def frame_for(rule: str, W0: int, H0: int, W1: int, H1: int,
              box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0), margin: float = 0.0) -> Frame:
    """Fit ``box`` (x0, y0, x1, y1 in old fractions; the whole canvas by default) into the new canvas, ``margin``
    (a fraction of the new canvas's shorter side) kept clear, centred or pinned to the rule's edges."""
    bx0, by0, bx1, by1 = box
    bw, bh = (bx1 - bx0) * W0, (by1 - by0) * H0                # the box in old pixels
    m = margin * min(W1, H1)
    s = min((W1 - 2 * m) / bw, (H1 - 2 * m) / bh)
    sw, sh = s * bw / W1, s * bh / H1                        # the box's share of the new canvas
    mx, my = m / W1, m / H1
    hx = mx if "left" in rule else 1.0 - mx - sw if "right" in rule else (1.0 - sw) / 2
    hy = my if "top" in rule else 1.0 - my - sh if "bottom" in rule else (1.0 - sh) / 2
    sx, sy = s * W0 / W1, s * H0 / H1
    return Frame(sx, sy, hx - bx0 * sx, hy - by0 * sy, s)


def _canvas_size(size) -> tuple[str, int, int, dict]:
    if isinstance(size, str):
        if size not in schema.PRESETS:
            raise ValueError(f"size {size!r}: use one of {', '.join(schema.PRESETS)} or {{name, width, height}}")
        w, h = schema.PRESETS[size]
        return size, w, h, {}
    if not isinstance(size, dict) or "width" not in size or "height" not in size:
        raise ValueError("a size is a preset name or {\"name\", \"width\", \"height\", \"set\": {...}}")
    name = str(size.get("name") or f"{size['width']}x{size['height']}")
    return name, int(size["width"]), int(size["height"]), dict(size.get("set") or {})


def rule_of(lay: dict) -> str:
    return lay.get("resize") or ("fill" if lay["type"] in FILL_TYPES or lay["type"] == "group" else "fit")


def _adapt_layer(lay: dict, W0: int, H0: int, W1: int, H1: int, warn: list[str], box, margin) -> None:
    t = lay["type"]
    rule = rule_of(lay)
    lay.pop("resize", None)
    if t == "group":
        for sub in lay.get("layers", []):
            _adapt_layer(sub, W0, H0, W1, H1, warn, box, margin)
        return
    if rule == "fill":
        return
    f = frame_for(rule, W0, H0, W1, H1, box, margin)
    if t == "text":
        mw = float(lay.get("max_width", 0.84))
        lay["x"] = f.x(lay.get("x", (1 - mw) / 2))
        lay["y"] = f.y(lay.get("y", 0.1))
        lay["max_width"] = mw * f.sx
        lay["size"] = float(lay.get("size", TEXT_SIZE)) * f.sy
        if lay.get("arc"):
            lay["arc"] = float(lay["arc"]) * f.sx
        if lay.get("on_path"):
            lay["on_path"] = (transform_d(lay["on_path"], f.s, f.s, f.ox * W1, f.oy * H1)
                              if lay.get("path_units", "fraction") == "px"
                              else transform_d(lay["on_path"], f.sx, f.sy, f.ox, f.oy))
    elif t == "shape":
        if lay["kind"] == "path":
            if lay.get("units", "fraction") == "px":
                lay["d"] = transform_d(lay["d"], f.s, f.s, f.ox * W1, f.oy * H1)
            else:
                lay["d"] = transform_d(lay["d"], f.sx, f.sy, f.ox, f.oy)
        else:
            lay["points"] = [[f.x(x), f.y(y)] for x, y in lay["points"]]
        if "width" in lay:
            lay["width"] = float(lay["width"]) * f.sx
        if "stroke_px" in lay:
            lay["stroke_px"] = float(lay["stroke_px"]) * f.s
    elif t == "svg":
        lay["x"], lay["y"] = f.x(lay.get("x", 0.0)), f.y(lay.get("y", 0.0))
        lay["width"] = float(lay.get("width", 0.5)) * f.sx
        if "height" in lay:
            lay["height"] = float(lay["height"]) * f.sy
    elif t == "product":
        lay["x"], lay["y"] = f.x(lay.get("x", 0.5)), f.y(lay.get("y", 0.9))
        lay["height"] = float(lay.get("height", 0.5)) * f.sy
    elif t == "image":                                       # an image the author asked to keep in proportion
        lay["x"], lay["y"] = f.x(lay.get("x", 0.5)), f.y(lay.get("y", 0.5))
        lay["scale"] = float(lay.get("scale", 1.0)) * min(f.sx, f.sy)
        warn.append(f"image {lay['id']!r}: resize {rule!r} keeps its centre and scale; check the crop")
    else:
        return
    m = lay.get("mask")
    if isinstance(m, dict) and m.get("type") == "radial":
        m["cx"], m["cy"] = f.x(m.get("cx", 0.5)), f.y(m.get("cy", 0.5))


def content_box(doc, base_dir=None) -> tuple[float, float, float, float]:
    """The bounding box (old canvas fractions) of everything that is not a stretched background."""
    import numpy as np
    d = schema.parse(doc) if not isinstance(doc, schema.Doc) else doc
    r = render.render_document(d, base_dir, scale=min(1.0, 512 / max(d.width, d.height)))
    keep = {l["id"] for l in d.layers if rule_of(l) != "fill" or l["type"] == "group"}
    acc = np.zeros((r.height, r.width), bool)
    for o in r.layers:
        if o.id in keep:
            acc |= o.rgba[..., 3] > 8
    if not acc.any():
        return 0.0, 0.0, 1.0, 1.0
    ys, xs = np.nonzero(acc)
    return xs.min() / r.width, ys.min() / r.height, (xs.max() + 1) / r.width, (ys.max() + 1) / r.height


def adapt(doc: dict, width: int, height: int, box=(0.0, 0.0, 1.0, 1.0), margin: float = 0.0) -> tuple[dict, list[str]]:
    """The poster redrawn for a ``width`` x ``height`` canvas. ``box`` is the part of the old canvas to fit (see
    content_box), ``margin`` the clear border around it (fraction of the shorter side). Returns (poster, notes)."""
    d = schema.parse(doc) if not isinstance(doc, schema.Doc) else doc
    W0, H0 = d.width, d.height
    out = copy.deepcopy(d.raw)
    canvas = {k: v for k, v in out.get("canvas", {}).items() if k not in ("preset", "width", "height")}
    out["canvas"] = {**canvas, "width": int(width), "height": int(height)}
    warn: list[str] = []
    for lay in out["layers"]:
        _adapt_layer(lay, W0, H0, int(width), int(height), warn, box, margin)
    return out, warn


@dataclass
class Sizes:
    names: list[str]
    docs: list[dict] = field(default_factory=list)
    files: dict[str, dict[str, Path]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    audits: dict[str, list[dict]] = field(default_factory=dict)
    sheet: Path | None = None
    sheet_image: Image.Image | None = None


def poster_sizes(doc, sizes: list, out_dir: str | Path | None = None, name: str = "", base_dir=None,
                 formats: list[str] | None = None, scale: float = 1.0, write_files: bool = True,
                 fit_to: str = "canvas", margin: float = 0.06) -> Sizes:
    """Render the poster in every size. ``fit_to``: "canvas" keeps the original framing (its margins scale too);
    "content" fits just the drawn foreground (plus ``margin``), so a wide banner or tall story uses its space.
    A size that cannot be drawn (text no longer fits, ...) is reported in ``errors`` and the others still render.
    Files: ``<name>-<size>.png`` (+ sidecar, text audit, other ``formats``) and ``<name>-sizes.png``, a sheet."""
    if not sizes:
        raise ValueError("give at least one size, e.g. [\"story\", \"landscape\", \"square\"]")
    if len(sizes) > MAX_SIZES:
        raise ValueError(f"at most {MAX_SIZES} sizes")
    base = schema.parse(doc) if not isinstance(doc, schema.Doc) else doc
    name = name or base.name
    if fit_to not in ("canvas", "content"):
        raise ValueError("fit_to is 'canvas' or 'content'")
    box, mg = ((content_box(base, base_dir), margin) if fit_to == "content" else ((0.0, 0.0, 1.0, 1.0), 0.0))
    res = Sizes([])
    ims, labels = [], []
    for label, w, h, patch in [_canvas_size(sz) for sz in sizes]:       # bad size names fail before any render
        res.names.append(label)
        try:
            d, warn = adapt(base, w, h, box, mg)
            for k, v in patch.items():
                set_path(d, k, v)
            res.notes += [f"{label}: {x}" for x in warn]
            errs = schema.check(d)
            if errs:
                raise schema.PosterError(errs)
            res.docs.append(d)
            r = render.render_document(d, base_dir, scale=scale)
        except (schema.PosterError, VaryError, ValueError) as e:
            res.errors[label] = str(e)
            continue
        res.audits[label] = r.audit
        if write_files:
            res.files[label] = render.write_outputs(r, out_dir, f"{name}-{label}", formats or ["png"])
        ims.append(r.pil)
        labels.append(f"{label} {w}x{h}")
    if ims:
        res.sheet_image = contact_sheet([im.convert("RGB") for im in ims], labels, tile=360)
        if write_files:
            p = render.fence_out(out_dir) / f"{name}-sizes.png"
            res.sheet_image.save(p)
            res.sheet = p
    return res
