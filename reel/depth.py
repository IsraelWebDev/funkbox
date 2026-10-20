"""Monocular depth for 2.5D parallax. ONNX (CPU) Depth-Anything-V2-Small, cached as .npy.

Returns HxW float32 in [0, 1], 1 = near. Optional: needs ``onnxruntime`` and the model file from
``python reel_cli.py setup-models``. Never downloads anything itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from funkgl.recipe import FUNKBOX, funk_out

NATIVE = 518          # model input side (multiple of 14)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_SESSION = None


class DepthUnavailable(RuntimeError):
    pass


def models_manifest() -> dict:
    return json.loads((Path(__file__).with_name("models.json")).read_text(encoding="utf-8"))


def models_dir() -> Path:
    return Path(os.environ.get("FUNK_MODELS") or (FUNKBOX / "models")).expanduser().resolve()


def model_path() -> Path:
    return models_dir() / models_manifest()["depth"]["files"][0]["dest"]


def available() -> bool:
    """True when onnxruntime imports and the model file is present."""
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
        return f"depth model not found at {model_path()} (run: python reel_cli.py setup-models)"
    return ""


def stub_depth(h: int, w: int) -> np.ndarray:
    """Vertical gradient: top is far (0), bottom is near (1). Used by tests and REEL_DEPTH=stub."""
    return np.repeat(np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None], w, axis=1)


def _session():
    global _SESSION
    if _SESSION is None:
        if not available():
            raise DepthUnavailable(unavailable_reason())
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        _SESSION = ort.InferenceSession(str(model_path()), so, providers=["CPUExecutionProvider"])
    return _SESSION


def _cache_file(image_path: Path) -> Path:
    h = hashlib.sha1(image_path.read_bytes()).hexdigest()
    return funk_out() / "cache" / "depth" / f"{h}.npy"


def estimate_depth(image_path: str | Path) -> np.ndarray:
    """Depth for an image file: HxW float32 in [0, 1] (1 = near), same size as the image."""
    p = Path(image_path)
    cache = _cache_file(p)
    if cache.exists():
        return np.load(cache)
    sess = _session()
    im = Image.open(p).convert("RGB")
    w, h = im.size
    x = np.asarray(im.resize((NATIVE, NATIVE), Image.BICUBIC), dtype=np.float32) / 255.0
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    y = sess.run(None, {sess.get_inputs()[0].name: x})[0][0].astype(np.float32)   # larger = nearer
    up = np.asarray(Image.fromarray(y, mode="F").resize((w, h), Image.BILINEAR), dtype=np.float32)
    lo, hi = float(up.min()), float(up.max())
    out = ((up - lo) / (hi - lo)).astype(np.float32) if hi > lo else np.full_like(up, 0.5)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, out)
    return out


def get_depth(image_path: str | Path, mode: str = "auto") -> np.ndarray | None:
    """mode: "auto" (model if available else None), "off" (None), "stub", "model" (raises), "cached"."""
    mode = (os.environ.get("REEL_DEPTH") or mode).lower()
    if mode == "off":
        return None
    if mode == "stub":
        w, h = Image.open(image_path).size
        return stub_depth(h, w)
    if mode == "model":
        return estimate_depth(image_path)
    if mode == "cached":                    # fast previews: use real depth only if it is already computed
        c = _cache_file(Path(image_path))
        return np.load(c) if c.exists() else None
    if available():
        try:
            return estimate_depth(image_path)
        except Exception as e:  # noqa: BLE001 - never fail a render over depth
            print(f"reel: depth estimation failed ({e}); falling back to Ken Burns", file=sys.stderr)
    return None


def blur_depth(d: np.ndarray, passes: int = 2) -> np.ndarray:
    """Separable 9-tap blur (binomial) so parallax doesn't tear at depth discontinuities."""
    k = np.array([1, 8, 28, 56, 70, 56, 28, 8, 1], dtype=np.float32)
    k /= k.sum()
    out = d.astype(np.float32)
    for _ in range(passes):
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (4, 4)
            p = np.pad(out, pad, mode="edge")
            acc = np.zeros_like(out)
            for i, kv in enumerate(k):
                acc += kv * (p[i:i + out.shape[0], :] if axis == 0 else p[:, i:i + out.shape[1]])
            out = acc
    return out
