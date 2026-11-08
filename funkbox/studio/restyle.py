"""Restyle (img2img) as a plain function for the hosted image worker: picture bytes in, PNG bytes out.

No file paths, no network, no FastSD. The caller passes the model in as a callable (see
``docs/specs/restyle-function.md``). The strength range comes from ``sdcore``; the step count is worked out here.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from fractions import Fraction

from PIL import Image

from funkbox.studio import sdcore

RESTYLE_MIN_SIDE = 64
RESTYLE_MAX_SIDE = 2048
PICTURE_MAX_BYTES = 40 * 1024 * 1024
PICTURE_MAX_SIDE = 8192
PICTURE_MAX_PIXELS = 40_000_000
SEED_MAX = 2**32 - 1
UNUSABLE_RESULT = "restyle model returned no usable image"
NOT_READABLE = "picture is not a readable image"
TOO_LARGE = (
    f"picture is larger than {PICTURE_MAX_SIDE} pixels a side or {PICTURE_MAX_PIXELS // 1_000_000} megapixels"
)

#: ``model(init, prompt, strength, steps, seed) -> PIL.Image``; ``init`` is RGB at the model side
Model = Callable[[Image.Image, str, float, int, int], Image.Image]


class RestyleError(ValueError):
    """A restyle request that must not run. The message never carries the prompt, the picture or a path."""


def steps_for(strength: float) -> int:
    """The fewest steps with ``steps * strength >= 1``, checked in exact arithmetic on the float value.

    ``sdcore.clamp_steps`` is not used: its 1e-9 tolerance can return one step too few (0.3333333332333 gives 3,
    and 3 * 0.3333333332333 < 1). Strength outside the sdcore range (this includes zero, negatives, NaN, infinity
    and tiny values) raises ValueError.
    """
    if not (math.isfinite(strength) and sdcore.STRENGTH_MIN <= strength <= sdcore.STRENGTH_MAX):
        raise ValueError(
            f"steps_for needs a strength from {sdcore.STRENGTH_MIN} to {sdcore.STRENGTH_MAX} (got {strength!r})"
        )
    s = Fraction(strength)
    steps = max(1, math.ceil(1 / strength))
    while steps * s < 1:
        steps += 1
    while steps > 1 and (steps - 1) * s >= 1:
        steps -= 1
    # the range above keeps steps at most sdcore.MAX_STEPS (20); restyle_png checks the range before calling this
    assert steps <= sdcore.MAX_STEPS, "steps_for called outside the sdcore strength range"
    return steps


def _check_prompt(prompt: object) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise RestyleError("prompt must be a non-empty string")
    return prompt


def _check_strength(strength: object) -> float:
    if isinstance(strength, bool) or not isinstance(strength, (int, float)):
        raise RestyleError(f"strength must be a number from {sdcore.STRENGTH_MIN} to {sdcore.STRENGTH_MAX}")
    try:
        s = float(strength)
    except OverflowError:  # an int too large for a float: out of range, not a crash
        s = math.inf
    if not math.isfinite(s) or not sdcore.STRENGTH_MIN <= s <= sdcore.STRENGTH_MAX:
        raise RestyleError(f"strength must be {sdcore.STRENGTH_MIN}..{sdcore.STRENGTH_MAX} (got {s!r})")
    return s


def _check_seed(seed: object) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX:
        raise RestyleError(f"seed must be an integer from 0 to {SEED_MAX}")
    return seed


def _pair(size: object) -> tuple | None:
    try:
        w, h = size  # type: ignore[misc]
    except (TypeError, ValueError):
        return None
    return w, h


def _check_size(size: object, what: str) -> tuple[int, int]:
    """``what`` is "size" when the caller passed one, "picture" when the picture's own size is used."""
    pair = _pair(size)
    if pair is None:
        raise RestyleError(f"{what} must be a (width, height) pair")
    for v in pair:
        if isinstance(v, bool) or not isinstance(v, int) or not RESTYLE_MIN_SIDE <= v <= RESTYLE_MAX_SIDE:
            raise RestyleError(
                f"each side of the {what} must be an integer from {RESTYLE_MIN_SIDE} to {RESTYLE_MAX_SIDE}"
            )
    return int(pair[0]), int(pair[1])


def _probe_size(data: bytes) -> tuple[int, int] | None:
    """The declared size from the header only (no pixels are decoded here), or None."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return None


def _decode(data: bytes) -> Image.Image | None:
    """The first frame as RGB (GIF, PNG, JPEG and the rest of Pillow's formats), or None."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            return im.convert("RGB")
    except Exception:
        return None


def _open(picture: object) -> Image.Image:
    if not isinstance(picture, (bytes, bytearray)) or not picture:
        raise RestyleError("picture must be non-empty image bytes")
    if len(picture) > PICTURE_MAX_BYTES:
        raise RestyleError(f"picture is larger than {PICTURE_MAX_BYTES // (1024 * 1024)} MiB")
    data = bytes(picture)
    declared = _probe_size(data)
    if declared is None:
        raise RestyleError(NOT_READABLE)
    if max(declared) > PICTURE_MAX_SIDE or declared[0] * declared[1] > PICTURE_MAX_PIXELS:
        raise RestyleError(TOO_LARGE)
    im = _decode(data)
    if im is None:
        raise RestyleError(NOT_READABLE)
    return im


def _fit(result: object, target: tuple[int, int]) -> Image.Image | None:
    """The model's result as an RGB image of exactly ``target``, or None when it is not usable."""
    try:
        if not isinstance(result, Image.Image) or min(result.size) < 1:
            return None
        return sdcore.cover_resize(result, target[0], target[1])
    except Exception:
        return None


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

    ``size`` None means the picture's own width and height. Steps follow ``steps * strength >= 1``
    (``steps_for``). Every check runs before ``model`` is called. Errors are raised by this module with no
    exception of its own in flight, so the model's exception is not kept as the error's ``__cause__``.
    """
    if not callable(model):
        raise RestyleError("model must be a callable")
    prompt = _check_prompt(prompt)
    s = _check_strength(strength)
    sd = _check_seed(seed)
    requested = None if size is None else _check_size(size, "size")
    src = _open(picture)
    target = requested if requested is not None else _check_size(src.size, "picture")

    steps = steps_for(s)
    init = sdcore.cover_resize(src, sdcore.round64(target[0]), sdcore.round64(target[1]))
    model_failed = False
    result = None
    try:
        result = model(init, prompt, s, steps, sd)
    except Exception:
        model_failed = True
    if model_failed:
        raise RestyleError("restyle model failed")

    fitted = _fit(result, target)
    if fitted is None:
        raise RestyleError(UNUSABLE_RESULT)
    buf = io.BytesIO()
    fitted.save(buf, format="PNG")
    return buf.getvalue()