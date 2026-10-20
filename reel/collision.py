"""Caption / product collision rule (v3.1, also a v3 validator rule).

The caption box and the product box must not overlap by more than 8% of the CAPTION area. Boxes are the
static layout boxes (caption: the full final block; product: x/y bottom-centre anchor, height fraction,
image aspect), before any camera movement.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

MAX_OVERLAP = 0.08


def product_box(x: float, y: float, height: float, aspect: float, W: int, H: int) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) in pixels. ``aspect`` = image width / height; (x, y) is the bottom-centre anchor."""
    h = height * H
    w = h * aspect
    return (x * W - w / 2, y * H - h, x * W + w / 2, y * H)


def image_aspect(path: str | Path) -> float:
    with Image.open(path) as im:
        w, h = im.size
    return w / max(1, h)


def caption_box(lay) -> tuple[float, float, float, float]:
    return (lay.x0, lay.y0, lay.x0 + lay.box_w, lay.y0 + lay.box_h)


def overlap_fraction(cap: tuple[float, float, float, float], prod: tuple[float, float, float, float]) -> float:
    """Intersection area as a fraction of the caption area."""
    iw = min(cap[2], prod[2]) - max(cap[0], prod[0])
    ih = min(cap[3], prod[3]) - max(cap[1], prod[1])
    area = max(1e-9, (cap[2] - cap[0]) * (cap[3] - cap[1]))
    return max(0.0, iw) * max(0.0, ih) / area
