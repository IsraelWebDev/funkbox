"""Keep words inside a part of the artwork: size and place text layers so they fit a bar, a plate or a label.

    res = fit_inside(doc, [{"layer": "eng2", "at": [0.63, 0.59], "band": 0.05},
                           {"layer": "eng1", "at": [0.37, 0.74], "band": 0.05}])
    res.doc       the poster with those layers' size, x and y set (their words are never touched)
    res.changed   {layer: {"size": .., "x": .., "y": ..}}

A spec names a text layer and a point ``at`` (fractions of the canvas) on the part of the artwork the words belong
in. The region is the connected area of that artwork colour around the point, taken from the layers drawn BELOW the
text layer; ``band`` (half-thickness, a fraction of the canvas height) cuts it to a strip along the layer's own
``skew_y`` angle, so a bar that runs into a bigger shape of the same colour stays a bar; ``margin`` keeps the words
that far from the edge. The text is made as large as fits, centred on the strip. ``equal_size`` gives every layer
named the smallest of their sizes (two halves of one phrase). The same specs can be passed to ``fit`` as
``keep_inside``: candidates whose words leave their region are rejected, so a fit to a flawed reference cannot push
words out of their bar.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageFilter

from . import render, schema

MAX_PX = 800                                    # regions and ink are measured at most this big
SPEC_KEYS = {"layer", "at", "band", "margin", "tolerance", "max_size"}


@dataclass
class InsideFit:
    doc: dict
    changed: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def check_specs(doc: dict, specs) -> list[dict]:
    if not isinstance(specs, list) or not specs:
        raise ValueError("specs: a non-empty list of {layer, at: [x, y], band, margin}")
    layers = {l.get("id"): i for i, l in enumerate(doc.get("layers", []))}
    out = []
    for s in specs:
        if not isinstance(s, dict) or set(s) - SPEC_KEYS or "layer" not in s or "at" not in s:
            raise ValueError(f"spec {s!r}: needs layer and at; other keys are {sorted(SPEC_KEYS - {'layer', 'at'})}")
        lid = s["layer"]
        if lid not in layers:
            raise ValueError(f"spec: no layer {lid!r} (have {sorted(map(str, layers))})")
        lay = doc["layers"][layers[lid]]
        if lay.get("type") != "text":
            raise ValueError(f"spec: layer {lid!r} is not a text layer")
        if lay.get("on_path") or lay.get("arc"):
            raise ValueError(f"spec: layer {lid!r} is bent (arc/on_path); only straight, optionally slanted text can be fitted inside")
        at = s["at"]
        if not (isinstance(at, (list, tuple)) and len(at) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in at)):
            raise ValueError(f"spec {lid!r}: at is [x, y] as fractions 0..1 of the canvas")
        for k, lo, hi in (("band", 0.002, 0.5), ("margin", 0.0, 0.1), ("tolerance", 1, 300), ("max_size", 0.01, 0.5)):
            if k in s and not (isinstance(s[k], (int, float)) and lo <= s[k] <= hi):
                raise ValueError(f"spec {lid!r}: {k} is a number in {lo}..{hi}")
        out.append({"layer": lid, "at": [float(at[0]), float(at[1])], "band": s.get("band"),
                    "margin": float(s.get("margin", 0.003)), "tolerance": float(s.get("tolerance", 60)),
                    "max_size": float(s.get("max_size", 0.2)), "index": layers[lid]})
    if len({s["layer"] for s in out}) != len(out):
        raise ValueError("specs: one spec per layer")
    return out


def _size(doc: dict) -> tuple[int, int]:
    d = schema.parse(doc)
    return d.width, d.height


def _scale(doc: dict) -> float:
    W, H = _size(doc)
    return min(1.0, MAX_PX / max(W, H))


def _erode(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask
    try:
        from scipy import ndimage
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        return ndimage.binary_erosion(mask, structure=(xx * xx + yy * yy) <= r * r)
    except ImportError:
        return np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(2 * r + 1))) > 127


def region(doc: dict, spec: dict, base_dir=None, scale: float | None = None, cache=None) -> np.ndarray:
    """The boolean mask (at ``scale``) the words of spec's layer must stay inside."""
    from .trace import _components
    s = scale or _scale(doc)
    below = dict(doc, layers=doc["layers"][:spec["index"]])
    if not below["layers"]:
        raise ValueError(f"layer {spec['layer']!r} has nothing under it to fit inside")
    art = np.asarray(render.render_document(below, base_dir, scale=s, cache=cache).pil.convert("RGB")).astype(int)
    H, W = art.shape[:2]
    cx, cy = int(round(spec["at"][0] * (W - 1))), int(round(spec["at"][1] * (H - 1)))
    m = np.abs(art - art[cy, cx]).sum(-1) < spec["tolerance"]
    lab, _ = _components(m)
    comp = lab == lab[cy, cx]
    if spec["band"]:
        ang = np.radians(float(doc["layers"][spec["index"]].get("skew_y", 0.0)))
        ys, xs = np.mgrid[0:H, 0:W]
        dist = -(xs - cx) * np.sin(ang) + (ys - cy) * np.cos(ang)
        comp = comp & (np.abs(dist) <= spec["band"] * H)
        lab, _ = _components(comp)
        comp = lab == lab[cy, cx]
    return _erode(comp, int(round(spec["margin"] * H)))


def _hex(c):
    if isinstance(c, str) and len(c) == 7 and c[0] == "#":
        try:
            return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return None
    return None


def ink(doc: dict, layer_id: str, base_dir=None, scale: float | None = None, cache=None) -> np.ndarray:
    """Where the text layer's letters are. It is rendered alone, on white or black (whichever contrasts with its
    colour), so the artwork's colours cannot hide it."""
    s = scale or _scale(doc)
    lay = next(l for l in doc["layers"] if l.get("id") == layer_id)
    col = _hex(lay.get("color"))
    bgs = [(255, 255, 255)] if col and sum(col) < 480 else [(0, 0, 0)] if col else [(255, 255, 255), (0, 0, 0)]
    shots = []
    for bg in bgs:
        d = dict(doc, layers=[{"id": "_bg", "type": "solid", "color": "#%02x%02x%02x" % bg}, copy.deepcopy(lay)])
        shots.append(np.asarray(render.render_document(d, base_dir, scale=s, cache=cache).pil.convert("RGB")).astype(int))
    if len(shots) == 2:
        return np.abs(shots[0] - shots[1]).sum(-1) < 3 * 255 * 0.6
    full = float(np.abs(np.array(col) - np.array(bgs[0])).sum())
    return np.abs(shots[0] - np.array(bgs[0])).sum(-1) > 0.4 * full


def outside(doc: dict, spec: dict, mask: np.ndarray, base_dir=None, scale: float | None = None, cache=None,
            t: np.ndarray | None = None) -> float:
    """Fraction of the words' ink that falls outside ``mask`` (0 = all inside). ``t`` is the ink if already measured."""
    t = ink(doc, spec["layer"], base_dir, scale, cache) if t is None else t
    if t.shape != mask.shape or not t.any():
        return 1.0
    return float((t & ~mask).sum() / t.sum())


def _centre(doc, lid, mask, base_dir, s, rounds=2):
    """Shift the layer so its ink is centred on the region; returns the ink at the final position (None: no ink)."""
    ys, xs = np.nonzero(mask)
    cx, cy = xs.mean() / mask.shape[1], ys.mean() / mask.shape[0]
    lay = next(l for l in doc["layers"] if l.get("id") == lid)
    for _ in range(rounds):
        t = ink(doc, lid, base_dir, s)
        if not t.any():
            return None
        ty, tx = np.nonzero(t)
        lay["x"] = round(float(lay.get("x", 0.0) + (cx - tx.mean() / t.shape[1])), 4)
        lay["y"] = round(float(lay.get("y", 0.0) + (cy - ty.mean() / t.shape[0])), 4)
    return ink(doc, lid, base_dir, s)


def _growth(t: np.ndarray, mask: np.ndarray) -> float:
    """The largest factor the ink can be scaled by (about the region's centre) and stay inside the region."""
    ys, xs = np.nonzero(mask)
    cx, cy = xs.mean(), ys.mean()
    ty, tx = np.nonzero(t)
    H, W = mask.shape
    lo, hi = 0.05, 12.0
    for _ in range(30):
        k = (lo + hi) / 2
        px = np.rint(cx + k * (tx - cx)).astype(int)
        py = np.rint(cy + k * (ty - cy)).astype(int)
        ok = (px >= 0).all() and (py >= 0).all() and (px < W).all() and (py < H).all() and mask[py, px].all()
        lo, hi = (k, hi) if ok else (lo, k)
    return lo


def fit_inside(doc, specs, base_dir=None, equal_size: bool = True) -> InsideFit:
    doc = copy.deepcopy(schema.parse(doc).raw if not isinstance(doc, schema.Doc) else doc.raw)
    sp = check_specs(doc, specs)
    s = _scale(doc)
    res = InsideFit(doc)
    masks = {x["layer"]: region(doc, x, base_dir, s) for x in sp}
    for x in sp:
        if masks[x["layer"]].sum() < 50:
            raise ValueError(f"layer {x['layer']!r}: the region around {x['at']} is empty; point `at` at the artwork "
                             f"colour the words belong on (tolerance {x['tolerance']})")
    best: dict[str, float] = {}
    for x in sp:
        lid = x["layer"]
        lay = next(l for l in doc["layers"] if l["id"] == lid)
        lay.update(align="left", wrap=False, max_width=0.95, x=0.0, y=0.0)
        lay.pop("on_path", None)
        start = min(0.02, x["max_size"])
        lay["size"] = start
        t = _centre(doc, lid, masks[lid], base_dir, s)
        if t is None:
            raise ValueError(f"layer {lid!r}: its words did not render")
        k = _growth(t, masks[lid])
        size = min(x["max_size"], start * k * 0.99)
        ok = None
        for _ in range(8):                                  # ink grows linearly with size: verify, shrink if needed
            lay["size"] = round(size, 4)
            lay["x"] = lay["y"] = 0.0
            try:
                t = _centre(doc, lid, masks[lid], base_dir, s)
                fine = t is not None and outside(doc, x, masks[lid], base_dir, s, t=t) == 0
            except schema.PosterError:
                fine = False                                # too big for the line: smaller
            if fine:
                ok = size
                break
            size *= 0.95
        if ok is None:
            raise ValueError(f"layer {lid!r}: its words do not fit inside the region even at the smallest size")
        best[lid] = round(ok * 0.98, 4)                     # a little air
    size = min(best.values()) if equal_size and len(best) > 1 else None
    for x in sp:
        lid = x["layer"]
        lay = next(l for l in doc["layers"] if l["id"] == lid)
        lay["size"] = size if size else best[lid]
        lay["x"] = lay["y"] = 0.0
        t = _centre(doc, lid, masks[lid], base_dir, s, rounds=3)
        miss = outside(doc, x, masks[lid], base_dir, s, t=t) if t is not None else 1.0
        if miss > 0.002:
            res.notes.append(f"{lid}: {miss:.1%} of the words still touch the edge margin; raise margin or lower size")
        res.changed[lid] = {"size": lay["size"], "x": lay["x"], "y": lay["y"]}
    if schema.check(doc):
        raise ValueError("the fitted poster is invalid: " + "; ".join(schema.check(doc)[:3]))
    res.doc = doc
    return res
