# Spec: restyle as a function (E2)

Status: merged as PR #3 (merge 321cabb); amended after two independent reviews (see Revision). Effort: small. Source: owner list, item E2. Unblocks desktop gap item 6 (restyle) for the hosted image worker.

## Problem

img2img exists only behind FastSD's paths. `funkbox/studio/images.py` `restyle()` takes a file path, goes through `apps.fastsd.generate`, and saves to the inputs folder. A hosted worker that holds a picture in memory cannot call it. The worker needs a plain function: picture bytes in, PNG bytes out.

## Goal

Add `restyle_png` in `funkbox/studio/restyle.py`. It takes the picture as bytes, a prompt, a strength, a seed and an optional size, and returns PNG bytes. It uses the strength range in `sdcore` (`STRENGTH_MIN`, `STRENGTH_MAX`) and the step rule `steps * strength >= 1`, which this module computes itself (Rule 1). It does no file I/O and no network, and its errors contain no file path and no prompt text.

## Non-goals

- No change to `funkbox image restyle`, `images.restyle()`, the `image_restyle` MCP tool, or FastSD's CLI path. The existing behaviour stays as it is.
- No change to `sdcore.py`. It is copied into the FastSD folder by `sync_launcher`, so editing it would change what gets synced. `sdcore.clamp_steps` is not called (Rule 1 explains why).
- The module does not load SD-Turbo. The caller passes the model in as a callable. Wiring a production model (on the hosted worker or on the laptop) is a separate item.
- No text-to-image or upscale function.
- No steps input. The rule fixes the steps.
- No product copy. The licence note below is a requirement for the copy, written here and not in code.

## Function signature

```python
RESTYLE_MIN_SIDE = 64
RESTYLE_MAX_SIDE = 2048
PICTURE_MAX_BYTES = 40 * 1024 * 1024  # 41,943,040 bytes
PICTURE_MAX_SIDE = 8192  # pixels, per side
PICTURE_MAX_PIXELS = 40_000_000  # total pixels
SEED_MAX = 2**32 - 1


class RestyleError(ValueError): ...


def restyle_png(
    picture: bytes,
    prompt: str,
    strength: float,
    seed: int,
    size: tuple[int, int] | None = None,
    *,
    model: Callable[[Image.Image, str, float, int, int], Image.Image],
) -> bytes: ...
```

The model callable is `model(init, prompt, strength, steps, seed)`. `init` is an RGB Pillow image already at the model side (Rule 2). It returns a Pillow image, which is then fitted to the output size (Rule 4).

## Inputs

| Name | Type | Rule |
|---|---|---|
| `picture` | bytes | Any image Pillow can open. That includes GIF (the first frame is used), PNG and JPEG, and this is deliberate. Three limits apply, in this order, and all are checked before any pixels are decoded: empty or non-bytes input is refused; input longer than `PICTURE_MAX_BYTES` (40 MiB) is refused; a declared size above `PICTURE_MAX_SIDE` (8192) on either side, or above `PICTURE_MAX_PIXELS` (40 million) in area, is refused. Input that does not decode is refused. Alpha is dropped by converting to RGB. |
| `prompt` | str | Non-empty after stripping whitespace. |
| `strength` | float | Finite, `0.05 <= strength <= 0.95` (`sdcore.STRENGTH_MIN` and `STRENGTH_MAX`). Booleans, strings, NaN, infinity and integers too large for a float are refused as `RestyleError`. |
| `seed` | int | `0 <= seed <= 2**32 - 1`, both ends accepted. Negative values are rejected (see open questions). |
| `size` | (int, int) or None | Each side `64..2048`. None means the picture's own width and height, and that picture size is checked against the same range. The error names "picture" in that case and "size" when the caller passed one. |
| `model` | callable | Keyword-only, required. |

## Outputs

PNG bytes, RGB mode. The decoded size is exactly `size`, or the picture's own size when `size` is None. The function writes no text chunks and no path into the PNG.

## Rules

1. **Step rule.** Steps are the fewest integers with `steps * strength >= 1`, checked in exact rational arithmetic on the float value (`fractions.Fraction`). This is `steps_for()` in `restyle.py`. `steps_for` first checks that the strength is in the sdcore range and raises `ValueError` otherwise, which covers zero, negatives, NaN, infinity and tiny values. `sdcore.clamp_steps` is not used: its 1e-9 tolerance can return one step too few. For strength 0.3333333332333 it returns 3, and `3 * 0.3333333332333 < 1`, so the model would run zero steps. Expected values: 0.05 gives 20, 0.25 gives 4, 0.3 gives 4, 0.5 gives 2, 0.95 gives 2, 1/3 gives 4, and 0.3333333332333 gives 4. The model is never called with fewer steps than the rule needs. Because the range keeps the count at most `sdcore.MAX_STEPS` (20), the internal check on that count is an `assert`, which is unreachable through `restyle_png`.
2. **Model side.** Each side of the output size is rounded down to a multiple of 64 with `sdcore.round64` (bounds 256 to 1024). The picture is centre-cropped to that side with `sdcore.cover_resize`, then passed to the model.
3. **Checks before the model.** Every input check runs before the model is called. An invalid call never reaches the model.
4. **Result.** The model's result must be a Pillow image with both sides at least 1 that can be converted to RGB. Other modes that convert (L, RGBA, P, F, I;16, CMYK and so on) are accepted and converted. A non-image, a zero-size image, a result whose size cannot be read, or a result whose conversion or resize fails gives `RestyleError("restyle model returned no usable image")`. Accepted results are centre-cropped and resized to exactly the output size with `sdcore.cover_resize`.
5. **Errors.** Every error is a `RestyleError`, a `ValueError`. Messages name the field and its allowed range. They never include the prompt, the picture bytes, a file path, or the model's exception text. A model failure gives `RestyleError("restyle model failed")`. This module raises its errors with no exception of its own in flight, so the model's exception is never kept as the error's `__cause__`, and the tests check that `__context__` is None when the caller is not itself inside an `except` block. Python sets `__context__` at raise time, so if a caller raises from inside its own `except` block, this module cannot prevent that.
6. **No I/O.** No file access, no network, no module-level state. The function adds no randomness: the same model, inputs and seed give the same output.

## Licence note (product copy, not code)

Images made with SD-Turbo fall under the Stability AI Community License (`THIRD_PARTY_NOTICES.md`, the SD-Turbo row and the "SD-Turbo / Stability AI Community License" note). Commercial use is free for individuals and organisations under US$1M annual revenue. Stability asks commercial users to register. Above that revenue a separate licence from Stability AI is needed. funkbox does not bundle the model.

Before restyle is sold, the product copy must say this. The licence notice must appear with the restyle product. This spec is the only place it is recorded. The code does not carry it.

## Tests

`tests/test_e2_restyle.py`, 77 tests. A stub model stands in for SD-Turbo. Nothing is downloaded and nothing calls the network.

1. Step rule: the boundary value 0.3333333332333, plus 1/3, 0.05, 0.95, 0.25, 0.3 and 0.5, each with its literal expected step count. Each count is checked with exact arithmetic (`steps * strength >= 1`, and `(steps - 1) * strength < 1`). `steps_for` refuses 0, -0.5, NaN, infinity, 1e-300 and a value just below `STRENGTH_MIN`.
2. Strength range: `STRENGTH_MIN - 0.01`, `STRENGTH_MAX + 0.01`, NaN, infinity, `10**400`, `-(10**400)`, a boolean, a string and None are refused, and the stub is never called. `STRENGTH_MIN` and `STRENGTH_MAX` are accepted with step counts 20 and 2.
3. Output size: no size gives an output the same size as the picture. Sizes 64 and 2048 are accepted. An explicit size is the output size, and the model side is a multiple of 64 (500x700 gives a 448x640 model side). A model result of another size is fitted. Sizes 32, 63, 2049, 4096, a 1-tuple, a string, a boolean and a float side are refused. A picture out of range is reported as "picture", and a bad size as "size".
4. Seeds: 0 and 2**32 - 1 are accepted and reach the stub unchanged. -1 and 2**32 are refused.
5. Pictures: a GIF is accepted, an empty or non-image input is refused, and the stub is not called. A picture over `PICTURE_MAX_BYTES` is refused before decoding (the message names the 40 MiB limit). A picture of exactly that length still goes on to probing and decoding.
6. Pixel cap: a PNG header declaring 9000x9000, 8193x100 or 8000x6000 is refused before decoding, with a message naming 8192 pixels a side or 40 megapixels. The header is hand-built with no pixel data, so no large image is allocated. A header declaring 8192x4882 (under 40 megapixels) passes the cap and then fails to decode, because it has no pixels.
7. Model results: a zero-size result, a zero-height result, None, a string, an object whose conversion raises, and an object whose `size` property raises are all refused with the "no usable image" message. Results in L, RGBA, P, F, I;16 and CMYK are converted to RGB.
8. Error text: a prompt with a path and a unique marker, with a bad strength (including `10**400`), a bad seed, a bad size, a bad picture, or a model returning None, gives an error with neither the marker nor the path. A model that raises with both gives `"restyle model failed"` with `__context__` and `__cause__` both None.
9. Output format: the result starts with the PNG signature and decodes as RGB.
10. Determinism: the same stub and inputs give identical bytes twice. The seed, prompt and strength reach the stub unchanged.

Run: `C:\dev\funkbox\.venv\Scripts\python.exe -m pytest -q tests/test_e2_restyle.py` from the worktree root.

## Out of scope

- The production model adapter and the hosted worker wiring.
- The 2 vCPU wall-clock measurement for a 512x512 restyle (owner's D8 CPU budget). It is measured on the hosted box, not in this change.
- Any change to `images.py`, `sdcore.py`, `apps.py`, the MCP tools, or the FastSD launcher.
- Any product copy for the licence.

## Done when

- `docs/specs/restyle-function.md` (this file) is committed.
- `funkbox/studio/restyle.py` and `tests/test_e2_restyle.py` exist and the targeted test file passes.
- `images.py`, `sdcore.py` and the MCP tools are unchanged.
- The report states the 2 vCPU timing as not measured here, unless the hosted box measures it.

## Open questions

- Which side supplies the production `model` callable, and where does it run (hosted worker or laptop FastSD)?
- Should a negative seed mean random, as the CLI does with -1? The spec rejects it so hosted results can be reproduced.
- Is 2048 the right ceiling for `size`? Are 8192 per side and 40 megapixels the right picture caps?

## Revision

The first version of this spec was written before the code. The first review (commit 6a1791a) led to exact step arithmetic (Rule 1), a named error for unusable model results (Rule 4), the 40 MiB input limit, GIF stated as accepted, and stronger tests. The second review (commit 2eb0fd3) led to: overflow of large integers handled as a range error (Inputs, strength row); a `size` property that raises handled as an unusable result (Rule 4); `steps_for` guarded against values outside the sdcore range; the range-bound `steps` check made an `assert`; the picture pixel caps (Inputs, picture row); the chaining claim narrowed (Rule 5); seeds 0 and 2**32 - 1 tested; and the strength tests use `sdcore` bounds.