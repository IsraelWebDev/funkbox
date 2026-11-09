"""Bytes in, bytes out: put a cut-out picture into a photo with the engine's product compositor.

``compose_product(photo, cutout_png, params) -> (png_bytes, sidecar)`` is the entry point the hosted cat edit
(funkbox-online ``src/app/cat_edit.py``) calls instead of keeping its own copy: the same setting names and ranges,
the same refusals (``ComposeError``, which names the field and never the picture), and a sidecar that says what was
done. The picture itself is drawn by ``ProductCompositor.draw``, the code the poster and reel product layers use.
Nothing is written to disk and nothing is kept after the call; the caller decides how long the bytes live.

The cut-out is cleaned first: its largest blob is kept, holes are filled, and the edge is softened. The pixels of the
cat are only scaled and, when ``match`` > 0, moved toward the carpet beneath them. scipy is optional here.
"""

from __future__ import annotations

import hashlib
import io
import json
import numbers

import numpy as np
from PIL import Image, ImageFilter

from funkbox.reel.motion import Camera
from funkbox.reel.product import ProductCompositor
from funkbox.reel.schema import ProductSpec
from funkbox.stills.trace import _components

MAX_SIDE = 2048
MAX_PIXELS = 4_000_000
ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
TOOL = "funkbox-cat-composite"  # the name the hosted side already writes into its sidecars
VERSION = 2  # 1 was the hosted compositor; 2 draws through ProductCompositor
DEFAULTS = {"cx": 0.5, "cy": 0.9, "height": 0.5, "shadow": 0.6, "match": 0.0}
RANGES = {"cx": (0.0, 1.0), "cy": (0.0, 1.0), "height": (0.1, 0.7), "shadow": (0.0, 1.0), "match": (0.0, 1.0)}


class ComposeError(ValueError):
    """A refused input. The message names the field, never the picture or its bytes."""


def checked_params(params) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict) or not set(params) <= set(DEFAULTS):
        raise ComposeError("unknown placement setting")
    out = dict(DEFAULTS)
    for name, value in params.items():
        low, high = RANGES[name]
        if isinstance(value, bool) or not isinstance(value, numbers.Real) or not low <= value <= high:
            raise ComposeError(f"{name} must be a number from {low} to {high}")
        out[name] = float(value)
    return out


def _open_picture(data: bytes, *, need_alpha: bool) -> Image.Image:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ComposeError("not a picture")
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
    except Exception:
        raise ComposeError("not a picture") from None
    if need_alpha:
        has_alpha = probe.mode in ("RGBA", "LA", "PA") or "transparency" in probe.info
        if fmt != "PNG" or not has_alpha:
            raise ComposeError("the cut-out must be a PNG with alpha")
    elif fmt not in ALLOWED_FORMATS:
        raise ComposeError("the picture format is not supported")
    if probe.width > MAX_SIDE or probe.height > MAX_SIDE or probe.width * probe.height > MAX_PIXELS:
        raise ComposeError("the picture is too large")
    try:
        probe.load()
    except Exception:
        raise ComposeError("not a picture") from None
    return probe


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Background that does not touch the border is a hole: fill it."""
    lab, n = _components(~mask)
    if n == 0:
        return mask
    edge = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    touching = [int(v) for v in np.unique(edge) if v]
    return mask | ((lab > 0) & ~np.isin(lab, touching))


def _dilate(mask: np.ndarray, iterations: int) -> np.ndarray:
    out = mask
    for _ in range(iterations):
        p = np.pad(out, 1)
        out = out | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return out


def cutout_rgba(cutout_png: bytes) -> np.ndarray:
    """The cut-out as HxWx4 uint8: its largest blob, holes filled (a hole takes the picture's own colour and is
    opaque), the edge softened. Anything outside the blob is transparent, so a second blob never shows."""
    rgba = np.asarray(_open_picture(cutout_png, need_alpha=True).convert("RGBA"))
    alpha = rgba[..., 3].astype(np.float32) / 255.0
    labels, count = _components(alpha > 0.5)
    if count == 0:
        raise ComposeError("no cat found in the cut-out")
    sizes = np.bincount(labels.ravel())[1:]
    blob = labels == (int(np.argmax(sizes)) + 1)
    filled = _fill_holes(blob)
    holes = filled & ~blob
    coverage = np.maximum(alpha, holes.astype(np.float32))
    reach = _dilate(filled, 2)
    blur = np.asarray(Image.fromarray((reach * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0)))
    soft = np.clip(blur.astype(np.float32) / 255.0 * coverage * reach, 0.0, 1.0)
    ys, xs = np.where(soft > 0.05)
    if ys.size == 0:
        raise ComposeError("no cat found in the cut-out")
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    out = np.empty((y1 - y0, x1 - x0, 4), np.uint8)
    out[..., :3] = rgba[y0:y1, x0:x1, :3]
    out[..., 3] = np.round(soft[y0:y1, x0:x1] * 255).astype(np.uint8)
    return out


def compose_product(photo: bytes, cutout_png: bytes, params=None) -> tuple[bytes, dict]:
    """The photo with the cut-out added, as PNG, and its sidecar. Raises ComposeError for any refused input.
    An RGBA photo is drawn on its RGB only: the alpha channel of the photo is dropped."""
    settings = checked_params(params)
    pic = _open_picture(photo, need_alpha=False).convert("RGB")
    W, H = pic.size
    frame = np.array(pic)
    cut = cutout_rgba(cutout_png)

    spec = ProductSpec(
        image="",
        x=settings["cx"],
        y=settings["cy"],
        height=settings["height"],
        depth=0.5,
        shadow=settings["shadow"],
        match_color=settings["match"],
    )
    pc = ProductCompositor(None, spec, W, H, rgba=cut)
    cam = Camera(mode="kenburns", zoom=1.0)  # a still: no zoom, no pan
    pl = pc.place(cam)  # the size is known before anything is resized
    if pl.w > MAX_SIDE:
        raise ComposeError("the cat is too wide for the picture at this height")
    x0, y0, x1, y1 = pl.box
    L, T, R, B = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if R <= L or B <= T:
        raise ComposeError("the cat does not fit on the picture at this position")
    if spec.match_color > 0:
        pc.fit_transform(frame, cam)

    result = pc.draw(frame, cam)
    buffer = io.BytesIO()
    Image.fromarray(result).save(buffer, format="PNG")
    png = buffer.getvalue()
    sidecar = {
        "tool": TOOL,
        "version": VERSION,
        "params": settings,
        "photo_sha256": hashlib.sha256(bytes(photo)).hexdigest(),
        "cutout_sha256": hashlib.sha256(bytes(cutout_png)).hexdigest(),
        "cat_box": [int(L), int(T), int(R), int(B)],
        "output_sha256": hashlib.sha256(png).hexdigest(),
        "size": [int(W), int(H)],
    }
    return png, sidecar


def sidecar_json(sidecar: dict) -> str:
    return json.dumps(sidecar, indent=2, sort_keys=True)
