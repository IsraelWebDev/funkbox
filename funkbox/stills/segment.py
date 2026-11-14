"""Click-to-mask: MobileSAM (ONNX, CPU) turns one point into a mask of the object under it.

Optional: needs ``onnxruntime`` and the two MobileSAM files from ``funkbox reel setup-models``.
Never downloads anything itself. The encoder runs once per picture; each click then needs only the decoder.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from funkbox.reel.depth import models_dir

SIDE = 1024  # SAM's input: the longest edge is scaled to this, then the picture is padded to a square
ENCODER = "mobile_sam_image_encoder.onnx"
DECODER = "sam_mask_decoder_single.onnx"

_SESSIONS = None


class SegmentUnavailable(RuntimeError):
    pass


def model_paths() -> tuple[Path, Path]:
    return models_dir() / ENCODER, models_dir() / DECODER


def unavailable_reason() -> str:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return "onnxruntime is not installed (pip install -r requirements-reel.txt)"
    missing = [p.name for p in model_paths() if not p.exists()]
    if missing:
        return f"MobileSAM model missing ({', '.join(missing)}; run: funkbox reel setup-models)"
    return ""


def _sessions():
    global _SESSIONS
    if _SESSIONS is None:
        reason = unavailable_reason()
        if reason:
            raise SegmentUnavailable(reason)
        import onnxruntime as ort

        enc, dec = model_paths()
        opts = {"providers": ["CPUExecutionProvider"]}
        _SESSIONS = (ort.InferenceSession(str(enc), **opts), ort.InferenceSession(str(dec), **opts))
    return _SESSIONS


def click_mask(rgb: np.ndarray, x: float, y: float) -> np.ndarray:
    """Boolean (H, W) mask of the object at pixel (x, y) of an RGB uint8 picture (x from the left, y from the top)."""
    H, W = rgb.shape[:2]
    enc, dec = _sessions()
    scale = SIDE / max(H, W)
    small = np.asarray(
        Image.fromarray(rgb).resize((round(W * scale), round(H * scale)), Image.BILINEAR), dtype=np.float32
    )
    padded = np.zeros((SIDE, SIDE, 3), np.float32)
    padded[: small.shape[0], : small.shape[1]] = small
    emb = enc.run(None, {"input_image": padded})[0]
    masks, iou = dec.run(
        None,
        {
            "image_embeddings": emb,
            "point_coords": np.array([[[x * scale, y * scale]]], np.float32),
            "point_labels": np.array([[1]], np.float32),
            "mask_input": np.zeros((1, 1, 256, 256), np.float32),
            "has_mask_input": np.array([0], np.float32),
            "orig_im_size": np.array([H, W], np.float32),
        },
    )[:2]
    best = int(np.argmax(iou.ravel()))
    return masks[0, best] > 0
