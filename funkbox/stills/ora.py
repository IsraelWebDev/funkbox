"""OpenRaster (.ora) export: a zip GIMP, Krita and MyPaint open with the layers intact.

    mimetype                 first entry, stored (not compressed): image/openraster
    stack.xml                the layer stack, TOP layer first, with composite-op, opacity and visibility
    data/<layer>.png         each layer pre-rendered with its own adjustments, filters, effects and mask, WITHOUT its
                             blend mode or opacity (those are stack attributes)
    Thumbnails/thumbnail.png
    mergedimage.png          the stack composited: what compositing data/*.png with the stack gives

Poster-level ``post`` passes (grain, vignette, ...) cannot be expressed as layers, so they are NOT part of the .ora;
they are in the PNG/JPG exports. A group is exported as one flattened layer carrying the group's blend and opacity.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

from funkbox.gl.recipe import BLEND_MODES

#: funkgl blend mode -> OpenRaster composite-op
OP = {"normal": "svg:src-over", "add": "svg:plus", "screen": "svg:screen", "multiply": "svg:multiply",
      "overlay": "svg:overlay", "difference": "svg:difference", "softlight": "svg:soft-light", "hue": "svg:hue"}
assert set(OP) == set(BLEND_MODES)
OP_TO_MODE = {v: k for k, v in OP.items()}
MIMETYPE = b"image/openraster"
THUMB = 256


def _png(arr_or_im) -> bytes:
    im = arr_or_im if isinstance(arr_or_im, Image.Image) else Image.fromarray(arr_or_im)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def write_ora(r, path: str | Path) -> Path:
    """Write the layers of a ``render.Rendered`` as an OpenRaster file."""
    path = Path(path)
    w, h = r.width, r.height
    root = ET.Element("image", {"version": "0.0.3", "w": str(w), "h": str(h)})
    stack = ET.SubElement(root, "stack")
    used: set[str] = set()
    entries: list[tuple[str, bytes]] = []

    def uniq(name: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_\-]", "_", name) or "layer"
        n, k = base, 2
        while n.lower() in used:
            n, k = f"{base}_{k}", k + 1
        used.add(n.lower())
        return n

    for lay in reversed(r.layers):                          # stack.xml lists the TOP layer first
        fn = uniq(lay.id)
        ET.SubElement(stack, "layer", {"name": lay.id, "src": f"data/{fn}.png", "x": "0", "y": "0",
                                       "opacity": f"{float(lay.opacity):.4f}".rstrip("0").rstrip(".") or "0",
                                       "visibility": "visible", "composite-op": OP[lay.blend]})
        entries.append((f"data/{fn}.png", _png(lay.rgba)))
    if r.alpha is None:                                     # an opaque canvas has a background layer at the bottom
        fn = uniq("background")
        ET.SubElement(stack, "layer", {"name": "background", "src": f"data/{fn}.png", "x": "0", "y": "0",
                                       "opacity": "1", "visibility": "visible", "composite-op": OP["normal"]})
        bg = np.empty((h, w, 4), np.uint8)
        bg[:] = (*r.background, 255)
        entries.append((f"data/{fn}.png", _png(bg)))
    merged = r.flat_pil
    thumb = merged.copy()
    thumb.thumbnail((THUMB, THUMB), Image.LANCZOS)
    xml = b"<?xml version='1.0' encoding='UTF-8'?>\n" + ET.tostring(root, encoding="utf-8")
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)
        z.writestr("stack.xml", xml, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in entries:
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED)       # PNG is already compressed
        z.writestr("Thumbnails/thumbnail.png", _png(thumb), compress_type=zipfile.ZIP_STORED)
        z.writestr("mergedimage.png", _png(merged), compress_type=zipfile.ZIP_STORED)
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------- #
# reading / validating (used by the tests and by `funkbox poster ora-check`)
# --------------------------------------------------------------------------- #
class OraError(ValueError):
    pass


def validate_ora(path: str | Path) -> dict:
    """Check the container rules; returns {'w', 'h', 'layers': [{name, src, opacity, op, x, y, visibility}]}."""
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise OraError("mimetype must be the first entry of the zip")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise OraError("mimetype must be stored uncompressed")
        if z.read("mimetype") != MIMETYPE:
            raise OraError("mimetype content must be image/openraster")
        names = set(z.namelist())
        try:
            root = ET.fromstring(z.read("stack.xml"))
        except ET.ParseError as e:
            raise OraError(f"stack.xml does not parse: {e}") from None
        if root.tag != "image" or not root.get("w") or not root.get("h"):
            raise OraError("stack.xml needs <image w= h=>")
        layers = []
        for el in root.iter("layer"):
            src = el.get("src", "")
            if src not in names:
                raise OraError(f"layer {el.get('name')!r}: file {src!r} is missing from the archive")
            op = el.get("composite-op", "svg:src-over")
            if op not in OP_TO_MODE:
                raise OraError(f"layer {el.get('name')!r}: unsupported composite-op {op!r}")
            layers.append({"name": el.get("name"), "src": src, "opacity": float(el.get("opacity", "1")), "op": op,
                           "x": int(el.get("x", "0")), "y": int(el.get("y", "0")),
                           "visibility": el.get("visibility", "visible")})
        for need in ("mergedimage.png", "Thumbnails/thumbnail.png"):
            if need not in names:
                raise OraError(f"{need} is missing")
        return {"w": int(root.get("w")), "h": int(root.get("h")), "layers": layers}


def recomposite(path: str | Path) -> np.ndarray:
    """Composite data/*.png bottom-up with the stack's ops in float (exact formulas: normal, multiply, screen, add,
    difference); returns HxWx4 uint8. Used to prove mergedimage.png equals what the stack adds up to."""
    info = validate_ora(path)
    w, h = info["w"], info["h"]
    base = np.zeros((h, w, 4), np.float64)
    with zipfile.ZipFile(path) as z:
        for lay in reversed(info["layers"]):            # stack.xml is top first
            if lay["visibility"] != "visible":
                continue
            src = np.asarray(Image.open(io.BytesIO(z.read(lay["src"]))).convert("RGBA"), np.float64) / 255.0
            canvas = np.zeros((h, w, 4))
            canvas[lay["y"]:lay["y"] + src.shape[0], lay["x"]:lay["x"] + src.shape[1]] = src[:h - lay["y"], :w - lay["x"]]
            base = _blend_over(base, canvas, OP_TO_MODE[lay["op"]], lay["opacity"])
    return np.clip(np.floor(base * 255.0 + 0.5), 0, 255).astype(np.uint8)


def _blend_fn(mode: str, b: np.ndarray, s: np.ndarray) -> np.ndarray:
    if mode == "normal":
        return s
    if mode == "multiply":
        return b * s
    if mode == "screen":
        return 1 - (1 - b) * (1 - s)
    if mode == "add":
        return np.minimum(b + s, 1.0)
    if mode == "difference":
        return np.abs(b - s)
    raise OraError(f"recomposite has no exact formula for {mode!r}")


def _blend_over(base: np.ndarray, src: np.ndarray, mode: str, opacity: float) -> np.ndarray:
    """W3C compositing: straight-alpha RGBA, backdrop ``base``, source ``src``."""
    cb, ab = base[..., :3], base[..., 3:4]
    cs, a_s = src[..., :3], src[..., 3:4] * opacity
    mixed = (1 - ab) * cs + ab * _blend_fn(mode, cb, cs)
    ar = a_s + ab * (1 - a_s)
    num = a_s * mixed + (1 - a_s) * ab * cb
    rgb = np.where(ar > 0, num / np.maximum(ar, 1e-12), 0.0)
    return np.concatenate([rgb, ar], axis=-1)
