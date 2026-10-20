"""Synthetic assets for the stills tests (no network, no models)."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "examples" / "reel_demo"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def noise_array(w, h, seed, alpha=False):
    """Smooth-ish colour noise (uint8 HxWx3, or HxWx4 with varied alpha)."""
    rng = np.random.default_rng(seed)
    c = 4 if alpha else 3
    a = rng.random((h, w, c)).astype(np.float32)
    im = Image.fromarray((a * 255).astype(np.uint8), "RGBA" if alpha else "RGB").filter(ImageFilter.GaussianBlur(2))
    arr = np.asarray(im).copy()
    if alpha:
        arr[..., 3] = np.clip(arr[..., 3].astype(np.int32) * 2 - 60, 0, 255).astype(np.uint8)
        arr[:8, :, 3] = 0
        arr[-8:, :, 3] = 255
    else:
        # use the full range so blend maths is exercised everywhere
        arr = np.clip((arr.astype(np.float32) - arr.min()) * 255.0 / max(1, arr.max() - arr.min()), 0, 255).astype(np.uint8)
    return arr


def save_noise(path, w, h, seed, alpha=False):
    Image.fromarray(noise_array(w, h, seed, alpha)).save(path)
    return path


def make_bottle(path, w=300, h=700):
    import make_assets
    make_assets.make_bottle(Path(path), w, h)
    return path


def make_kitchen(path, w=1080, h=1350):
    """A plausible 'photo': warm wall, counter, window light, a few props (no text)."""
    from PIL import ImageDraw
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = yy / h
    img = np.zeros((h, w, 3), np.float32)
    wall = np.array([226, 214, 196], np.float32)
    img[:] = wall * (1.0 - 0.18 * t[..., None])
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im, "RGBA")
    d.rectangle((0, int(h * 0.62), w, h), fill=(150, 118, 90, 255))                     # counter
    d.rectangle((0, int(h * 0.60), w, int(h * 0.63)), fill=(196, 170, 140, 255))
    d.rectangle((int(w * 0.08), int(h * 0.08), int(w * 0.46), int(h * 0.42)), fill=(255, 250, 235, 255))   # window
    for i in range(1, 3):
        d.line((int(w * 0.08) + i * int(w * 0.38) // 3, int(h * 0.08), int(w * 0.08) + i * int(w * 0.38) // 3,
                int(h * 0.42)), fill=(190, 180, 160, 255), width=8)
    d.rectangle((int(w * 0.08), int(h * 0.08), int(w * 0.46), int(h * 0.42)), outline=(170, 160, 140, 255), width=10)
    for cx, cy, r, col in ((0.62, 0.55, 0.05, (90, 140, 90)), (0.78, 0.57, 0.07, (200, 90, 70)),
                           (0.9, 0.58, 0.04, (80, 110, 170))):
        d.ellipse((int(w * (cx - r)), int(h * (cy - r)), int(w * (cx + r)), int(h * (cy + r))), fill=col + (255,))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    Image.fromarray(np.asarray(im)).save(path)
    return path
