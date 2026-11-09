"""Masked inpainting: LaMa (ONNX, CPU) fills the white part of a mask from the pixels around it.

Optional: needs ``onnxruntime`` and the model from ``funkbox reel setup-models``. Never downloads anything itself.
LaMa takes a fixed 512x512 input, so the fill runs on a square crop around the mask (with some context) and
only the masked pixels of that crop are replaced. Results are cached by content hash, like depth and shadows.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from funkbox.config import funk_out
from funkbox.reel.depth import models_dir, models_manifest

NATIVE = 512  # the exported LaMa graph takes 512 x 512
CONTEXT = 0.5  # extra context around the mask box, as a fraction of the box side
VERSION = "lama-crop-v1"  # bump when the crop or blend changes, so old cache entries are not reused

_SESSION = None


class InpaintUnavailable(RuntimeError):
    pass


class MaskTooLarge(ValueError):
    pass


def model_path() -> Path:
    return models_dir() / models_manifest()["lama"]["files"][0]["dest"]


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return model_path().exists()


def unavailable_reason() -> str:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return "onnxruntime is not installed (pip install -r requirements-reel.txt)"
    if not model_path().exists():
        return f"LaMa model not found at {model_path()} (run: funkbox reel setup-models)"
    return ""


def _session():
    global _SESSION
    if _SESSION is None:
        reason = unavailable_reason()
        if reason:
            raise InpaintUnavailable(reason)
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("FUNKBOX_ORT_THREADS", "0") or 0)
        _SESSION = ort.InferenceSession(str(model_path()), so, providers=["CPUExecutionProvider"])
    return _SESSION


def grow_mask(mask: np.ndarray, grow: int) -> np.ndarray:
    """Dilate a boolean mask by ``grow`` px so the fill also covers the soft edge of the thing removed."""
    if grow <= 0:
        return mask
    im = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(2 * grow + 1))
    return np.asarray(im) > 127


def crop_box(mask: np.ndarray) -> tuple[int, int, int, int]:
    """A square (x0, y0, x1, y1) inside the picture that holds the mask box plus CONTEXT on each side."""
    H, W = mask.shape
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    bw, bh = x1 - x0, y1 - y0
    limit = min(W, H)
    if max(bw, bh) > limit:
        raise MaskTooLarge(
            f"the mask is {bw}x{bh} px but the picture's short side is {limit} px; use a smaller mask "
            "or fill it in parts"
        )
    side = min(limit, int(round(max(bw, bh) * (1 + 2 * CONTEXT))))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left = min(max(int(round(cx - side / 2)), 0), W - side)
    top = min(max(int(round(cy - side / 2)), 0), H - side)
    return left, top, left + side, top + side


def fill_rgb(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """RGB (H, W, 3) uint8 with the masked pixels replaced by LaMa's fill. Unmasked pixels are untouched."""
    if not mask.any():
        return rgb
    x0, y0, x1, y1 = crop_box(mask)
    side = x1 - x0
    crop = Image.fromarray(rgb[y0:y1, x0:x1]).resize((NATIVE, NATIVE), Image.BICUBIC)
    m = Image.fromarray(mask[y0:y1, x0:x1].astype(np.uint8) * 255).resize((NATIVE, NATIVE), Image.NEAREST)
    x = np.asarray(crop, np.float32).transpose(2, 0, 1)[None] / 255.0
    hole = (np.asarray(m, np.float32) > 127).astype(np.float32)[None, None]
    out = _session().run(None, {"image": x, "mask": hole})[0][0]  # 3 x 512 x 512, already 0..255
    out = np.clip(out.transpose(1, 2, 0), 0, 255).astype(np.uint8)
    back = np.asarray(Image.fromarray(out).resize((side, side), Image.BICUBIC))
    res = rgb.copy()
    sub_mask = mask[y0:y1, x0:x1]
    res[y0:y1, x0:x1][sub_mask] = back[sub_mask]
    return res


def inpaint_image(im: Image.Image, mask: Image.Image, grow: int = 4) -> Image.Image:
    """Fill ``im`` where ``mask`` is white. The mask is resized to the picture; alpha is kept as it was."""
    alpha = im.getchannel("A") if "A" in im.getbands() else None
    rgb = np.asarray(im.convert("RGB"))
    m = np.asarray(mask.convert("L").resize(im.size, Image.NEAREST)) > 127
    m = grow_mask(m, grow)
    key = hashlib.sha1(VERSION.encode() + bytes([grow]) + rgb.tobytes() + np.packbits(m).tobytes()).hexdigest()
    cache = funk_out() / "cache" / "inpaint" / f"{key}.png"
    if cache.exists():
        out = Image.open(cache).convert("RGB")
    else:
        out = Image.fromarray(fill_rgb(rgb, m))
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f"{cache.stem}.{uuid.uuid4().hex}.part")
        out.save(tmp, format="PNG")
        os.replace(tmp, cache)
    if alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return out
