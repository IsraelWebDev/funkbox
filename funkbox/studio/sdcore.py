"""Pure helpers for the FastSD extensions (v3.2 S1). Standard library + Pillow + numpy only.

Used by three callers, so the rules live in exactly one place:
  * ``start_local_mcp.py`` (the localhost launcher, copied next to this file inside the FastSD folder),
  * ``funkbox/studio`` (request building, the size tracker, and the ``STUDIO_FAKE_SD`` test stub),
  * the tests (no FastSD, no network, no models).
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

#: aspect presets, every side a multiple of 64 (the OpenVINO model is compiled per size)
ASPECTS: dict[str, tuple[int, int]] = {
    "square": (512, 512),
    "portrait": (512, 768),
    "landscape": (768, 512),
    "story": (512, 896),
    "wide": (896, 512),
}
STRENGTH_MIN, STRENGTH_MAX = 0.05, 0.95
MAX_STEPS = 20                     # ceil(1 / 0.05)
SLOW_SIZE_MINUTES = "2-3"          # one OpenVINO recompile per new size on the laptop
NAME_RE = re.compile(r"[^A-Za-z0-9_\-]")
UPSCALE_FACTOR = 2


class SDError(ValueError):
    """A request FastSD must not be asked to run (bad aspect, path outside the roots, ...)."""


# --------------------------------------------------------------------------- #
# sizes
# --------------------------------------------------------------------------- #
def aspect_size(aspect: str) -> tuple[int, int]:
    key = (aspect or "square").strip().lower()
    if key not in ASPECTS:
        raise SDError(f"unknown aspect {aspect!r}; use one of {', '.join(ASPECTS)} "
                      f"({', '.join(f'{k} {w}x{h}' for k, (w, h) in ASPECTS.items())})")
    return ASPECTS[key]


def nearest_aspect(width: int, height: int) -> str:
    """The preset whose width/height ratio is closest to the image's (ties go to the first listed)."""
    r = math.log(max(1, width) / max(1, height))
    return min(ASPECTS, key=lambda k: abs(math.log(ASPECTS[k][0] / ASPECTS[k][1]) - r))


def round64(v: int, lo: int = 256, hi: int = 1024) -> int:
    return max(lo, min(hi, int(v) - int(v) % 64))


class SizeTracker:
    """Sizes the running FastSD has already compiled. A size not in the set costs a recompile."""

    def __init__(self) -> None:
        self.seen: set[tuple[int, int]] = set()

    def is_new(self, w: int, h: int) -> bool:
        # the very first image after a cold start loads the model (30-60 s at 512x512, no recompile)
        return (w, h) not in self.seen and not (not self.seen and (w, h) == ASPECTS["square"])

    def mark(self, w: int, h: int) -> None:
        self.seen.add((w, h))

    def clear(self) -> None:
        self.seen.clear()

    def warning(self, w: int, h: int) -> str | None:
        if not self.is_new(w, h):
            return None
        return (f"first image at {w}x{h} will take ~{SLOW_SIZE_MINUTES} min (the iGPU model is recompiled once "
                f"per size, then that size is fast until the size changes again)")


# --------------------------------------------------------------------------- #
# request building
# --------------------------------------------------------------------------- #
def clamp_steps(steps: int, strength: float) -> tuple[int, str | None]:
    """SD-Turbo img2img runs int(steps * strength) denoising steps, so it needs steps * strength >= 1."""
    need = max(1, math.ceil(1.0 / strength - 1e-9))
    steps = int(steps)
    if steps >= need:
        return steps, None
    return need, f"steps raised from {steps} to {need} (SD-Turbo img2img needs steps * strength >= 1)"


def fence_path(path: str | Path, roots: list[Path]) -> Path:
    """Resolve ``path`` (symlinks followed) and require it to be an existing file inside one of ``roots``."""
    p = Path(str(path)).expanduser()
    try:
        rp = p.resolve()
    except OSError as e:
        raise SDError(f"cannot resolve {path!s}: {e}") from None
    rr = [Path(r).expanduser().resolve() for r in roots]
    if not any(rp == r or rp.is_relative_to(r) for r in rr):
        raise SDError(f"path {path!s} resolves outside the allowed folders ({', '.join(str(r) for r in rr)}); "
                      "put the image in the inputs folder")
    if not rp.is_file():
        raise SDError(f"image not found: {path!s}")
    return rp


def safe_name(name: str, fallback: str) -> str:
    n = NAME_RE.sub("", (name or "").strip())[:60]
    return n or fallback


def build_request(prompt: str, aspect: str = "square", *, seed: int = -1, steps: int = 1, negative_prompt: str = "",
                  name: str = "", mode: str = "txt2img", init_image: str | Path | None = None,
                  strength: float = 0.5, roots: list[Path] | None = None,
                  size: tuple[int, int] | None = None) -> dict:
    """The body for POST /studio/generate. Raises SDError before anything is sent."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise SDError("prompt must be a non-empty string")
    if mode not in ("txt2img", "img2img"):
        raise SDError(f"mode must be txt2img or img2img (got {mode!r})")
    w, h = size if size else aspect_size(aspect)
    w, h = round64(w), round64(h)
    body: dict = {"prompt": prompt, "negative_prompt": negative_prompt or "", "width": w, "height": h,
                  "seed": int(seed), "name": name or "", "mode": mode}
    steps = int(steps)
    if not 1 <= steps <= MAX_STEPS:
        raise SDError(f"steps must be 1..{MAX_STEPS}")
    if mode == "img2img":
        if init_image is None:
            raise SDError("img2img needs init_image")
        if not STRENGTH_MIN <= float(strength) <= STRENGTH_MAX:
            raise SDError(f"strength must be {STRENGTH_MIN}..{STRENGTH_MAX} (got {strength})")
        body["init_image"] = str(fence_path(init_image, roots or []))
        body["strength"] = float(strength)
        steps, note = clamp_steps(steps, float(strength))
        if note:
            body["steps_note"] = note
    elif init_image is not None:
        raise SDError("init_image is only for mode img2img")
    body["steps"] = steps
    return body


def cover_resize(im: Image.Image, w: int, h: int) -> Image.Image:
    """Resize so the image covers w x h, centre-cropping the overflow (fit cover)."""
    iw, ih = im.size
    s = max(w / iw, h / ih)
    nw, nh = max(w, round(iw * s)), max(h, round(ih * s))
    r = im.convert("RGB").resize((nw, nh), Image.LANCZOS)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return r.crop((x0, y0, x0 + w, y0 + h))


# --------------------------------------------------------------------------- #
# upscale (the fallback FastSD-less path; the launcher tries FastSD's own upscaler first)
# --------------------------------------------------------------------------- #
def upscale_lanczos(im: Image.Image) -> Image.Image:
    """Exactly 2x with LANCZOS plus a mild unsharp mask."""
    im = im.convert("RGB")
    big = im.resize((im.width * UPSCALE_FACTOR, im.height * UPSCALE_FACTOR), Image.LANCZOS)
    return big.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))


# --------------------------------------------------------------------------- #
# the STUDIO_FAKE_SD stub: deterministic procedural pictures, no FastSD, no models
# --------------------------------------------------------------------------- #
def fake_image(prompt: str, w: int, h: int, seed: int = 0, init: Image.Image | None = None,
               strength: float = 0.5) -> Image.Image:
    """Smooth colour fields and soft blobs seeded from sha256(prompt) and ``seed``.

    img2img: blends toward the (cover-resized) init image, so strength 0.05 stays close to it and
    0.95 is almost entirely the generated picture.
    """
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    rng = np.random.default_rng([int.from_bytes(digest[:8], "big"), int(seed) & 0xFFFFFFFF])
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx /= max(1, w - 1)
    yy /= max(1, h - 1)
    c0, c1 = rng.uniform(0.05, 0.95, 3), rng.uniform(0.05, 0.95, 3)
    ang = rng.uniform(0, 2 * math.pi)
    t = np.clip((xx - 0.5) * math.cos(ang) + (yy - 0.5) * math.sin(ang) + 0.5, 0, 1)[..., None]
    img = c0 * (1 - t) + c1 * t
    for _ in range(int(rng.integers(4, 8))):
        cx, cy, r = rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0.12, 0.38)
        col = rng.uniform(0.05, 1.0, 3)
        a = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * (r * 0.5) ** 2)))[..., None] * rng.uniform(0.3, 0.8)
        img = img * (1 - a) + col * a
    gen = Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    if init is None:
        return gen
    base = np.asarray(cover_resize(init, w, h), np.float32)
    out = base * (1.0 - strength) + np.asarray(gen, np.float32) * strength
    return Image.fromarray((np.clip(out, 0, 255) + 0.5).astype(np.uint8), "RGB")
