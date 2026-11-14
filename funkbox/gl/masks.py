"""Masks drawn from text: a ``mask`` of ``{"text": ...}`` is the text, drawn by the poster text pipeline.

The text is laid out and shaped exactly as a poster's text layer is, and the text audit applies: if the drawn text is
not the text asked for, the mask is refused rather than drawn differently. The stills code is imported lazily, because
it depends on this package's GL module.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _draw_text_alpha(spec: dict, w: int, h: int, base_dir: str | Path | None) -> tuple[np.ndarray, dict]:
    """White text on black at w x h: its red channel is the coverage. Returns (alpha, the text's audit entry)."""
    from funkbox.stills import render

    layer = {
        "id": "mask",
        "type": "text",
        "text": spec["text"],
        "size": float(spec.get("size", 0.2)),
        "align": spec.get("align", "center"),
        "color": "#ffffff",
    }
    if spec.get("font"):
        layer["font"] = spec["font"]
    doc = {
        "version": 1,
        "casual": True,
        "canvas": {"width": int(w), "height": int(h), "background": "#000000"},
        "layers": [layer],
    }
    r = render.render_document(doc, base_dir=base_dir)
    alpha = np.ascontiguousarray(r.image[..., 0])
    audit = r.audit[0] if r.audit else {"text": None, "text_exact": False}
    return alpha, audit


def text_mask(spec: dict, w: int, h: int, base_dir: str | Path | None = None) -> tuple[np.ndarray, dict]:
    """The coverage (uint8, h x w) of ``spec``'s text, and its audit entry. Refused unless the text is drawn exactly."""
    alpha, audit = _draw_text_alpha(spec, w, h, base_dir)
    if not audit.get("text_exact", False):
        raise ValueError(
            f"text mask: the text was not drawn exactly (asked for {spec['text']!r}, drawn {audit.get('text')!r})"
        )
    return alpha, audit
