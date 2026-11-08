"""Restyle (img2img) as a plain function for the hosted image worker: picture bytes in, PNG bytes out.

No file paths, no network, no FastSD. The caller passes the model in as a callable (see
``docs/specs/restyle-function.md``). The strength range and the step rule are the ones in ``sdcore``.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable

from PIL import Image

from funkbox.studio import sdcore

RESTYLE_MIN_SIDE = 64
RESTYLE_MAX_SIDE = 2048
SEED_MAX = 2**32 - 1

#: ``model(init, prompt, strength, steps, seed) -> PIL.Image``; ``init`` is RGB at the model side
Model = Callable[[Image.Image, str, float, int, int], Image.Image]


class RestyleError(ValueError):
    """A restyle request that must not run. The message never carries the prompt, the picture or a path."""


def _check_prompt(prompt: object) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise RestyleError("prompt must be a non-empty string")
    return prompt


def _check_strength(strength: object) -> float:
    if isinstance(strength, bool) or not isinstance(strength, (int, float)):
        raise RestyleError(f"strength must be a number from {sdcore.STRENGTH_MIN} to {sdcore.STRENGTH_MAX}")
    s = float(strength)
    if not math.isfinite(s) or not sdcore.STRENGTH_MIN <= s <= sdcore.STRENGTH_MAX:
        raise RestyleError(f"strength must be {sdcore.STRENGTH_MIN}..{sdcore.STRENGTH_MAX} (got {s!r})")
    return s


def _check_seed(seed: object) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX:
        raise RestyleError(f"seed must be an integer from 0 to {SEED_MAX}")
    return seed


def _check_size(size: object) -> tuple[int, int]:
    try:
        w, h = size  # type: ignore[misc]
    except (TypeError, ValueError):
        raise RestyleError("size must be a (width, height) pair") from None
    for v in (w, h):
        if isinstance(v, bool) or not isinstance(v, int) or not RESTYLE_MIN_SIDE <= v <= RESTYLE_MAX_SIDE:
            raise RestyleError(
                f"each side of size must be an integer from {RESTYLE_MIN_SIDE} to {RESTYLE_MAX_SIDE}"
            )
    return w, h


def _open(picture: object) -> Image.Image:
    if not isinstance(picture, (bytes, bytearray)) or not picture:
        raise RestyleError("picture must be non-empty image bytes")
    try:
        with Image.open(io.BytesIO(picture)) as im:
            im.load()
            return im.convert("RGB")
    except Exception:
        raise RestyleError("picture is not a readable image") from None


def restyle_png(
    picture: bytes,
    prompt: str,
    strength: float,
    seed: int,
    size: tuple[int, int] | None = None,
    *,
    model: Model,
) -> bytes:
    """Re-render ``picture`` following ``prompt`` and return a PNG of exactly ``size``.

    ``size`` None means the picture's own width and height. Steps are set by the rule
    ``steps * strength >= 1`` (``sdcore.clamp_steps``). Every check runs before ``model`` is called.
    """
    if not callable(model):
        raise RestyleError("model must be a callable")
    prompt = _check_prompt(prompt)
    s = _check_strength(strength)
    sd = _check_seed(seed)
    requested = None if size is None else _check_size(size)
    src = _open(picture)
    target = _check_size(src.size if requested is None else requested)

    steps, _note = sdcore.clamp_steps(1, s)
    mw, mh = sdcore.round64(target[0]), sdcore.round64(target[1])
    init = sdcore.cover_resize(src, mw, mh)
    try:
        result = model(init, prompt, s, steps, sd)
    except Exception:
        raise RestyleError("restyle model failed") from None
    if not isinstance(result, Image.Image):
        raise RestyleError("restyle model returned no image")

    fitted = sdcore.cover_resize(result, target[0], target[1])
    buf = io.BytesIO()
    fitted.save(buf, format="PNG")
    return buf.getvalue()