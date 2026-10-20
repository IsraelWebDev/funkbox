"""PIL text overlay: port of v1 `_text` (auto-fit, \\n handling, per-letter wobble)."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

import funk

from .recipe import TextSpec


def draw_text(arr: np.ndarray, theta: float, spec: TextSpec) -> np.ndarray:
    """Draw spec onto an HxWx3 uint8 array (returns a new array)."""
    if not spec.text:
        return arr
    im = Image.fromarray(arr)
    w, h = im.size
    draw = ImageDraw.Draw(im)
    lines = spec.text.split("\\n") if "\\n" in spec.text else spec.text.split("\n")
    px = max(8, int(h * spec.size))
    widest = max(draw.textlength(ln, font=funk._font(px)) for ln in lines)
    if widest > 0.86 * w:  # auto-fit long text instead of clipping it
        px = max(8, int(px * 0.86 * w / widest))
    font = funk._font(px)
    line_h = font.size * 1.15
    y0 = h / 2 - line_h * len(lines) / 2
    off = max(2, font.size // 12)
    for li, line in enumerate(lines):
        total = draw.textlength(line, font=font)
        x = (w - total) / 2
        for i, ch in enumerate(line):
            dy = spec.wobble * math.sin(2 * theta + i * 0.7 + li)
            y = y0 + li * line_h + dy
            draw.text((x + off, y + off), ch, font=font, fill=spec.shadow)
            draw.text((x, y), ch, font=font, fill=spec.color,
                      stroke_width=max(1, font.size // 22), stroke_fill="#000000")
            x += draw.textlength(ch, font=font)
    return np.asarray(im)
