# Spec: restyle as a function (E2)

Status: planned, before code. Effort: small. Source: owner list, item E2. Unblocks desktop gap item 6 (restyle) for the hosted image worker.

## Problem

img2img exists only behind FastSD's paths. `funkbox/studio/images.py` `restyle()` takes a file path, goes through `apps.fastsd.generate`, and saves to the inputs folder. A hosted worker that holds a picture in memory cannot call it. The worker needs a plain function: picture bytes in, PNG bytes out.

## Goal

Add `restyle_png` in `funkbox/studio/restyle.py`. It takes the picture as bytes, a prompt, a strength, a seed and an optional size, and returns PNG bytes. It applies the same strength and step rules as the existing img2img path (`funkbox/studio/sdcore.py`: `STRENGTH_MIN`/`STRENGTH_MAX`, `clamp_steps`). It does no file I/O and no network, and its errors contain no file path and no prompt text.

## Non-goals

- No change to `funkbox image restyle`, `images.restyle()`, the `image_restyle` MCP tool, or FastSD's CLI path. The existing behaviour stays as it is.
- No change to `sdcore.py`. It is copied into the FastSD folder by `sync_launcher`, so editing it would change what gets synced.
- The module does not load SD-Turbo. The caller passes the model in as a callable. Wiring a production model (on the hosted worker or on the laptop) is a separate item.
- No text-to-image or upscale function.
- No steps input. The rule fixes the steps.
- No product copy. The licence note below is a requirement for the copy, written here and not in code.

## Function signature

```python
RESTYLE_MIN_SIDE = 64
RESTYLE_MAX_SIDE = 2048

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

The model callable is `model(init, prompt, strength, steps, seed)`. `init` is an RGB Pillow image already at the model side (see Rule 2). It returns a Pillow image. Its size is not trusted and is fitted to the output size (Rule 4).

## Inputs

| Name | Type | Rule |
|---|---|---|
| `picture` | bytes | Any image Pillow can open (PNG, JPEG, ...). Not decodable gives `RestyleError`. Alpha is dropped by converting to RGB. |
| `prompt` | str | Non-empty after stripping whitespace. |
| `strength` | float | Finite, `0.05 <= strength <= 0.95`. Booleans and NaN are rejected. |
| `seed` | int | `0 <= seed <= 2**32 - 1`. Negative values are rejected (see open questions). |
| `size` | (int, int) or None | Each side `64..2048`. None means the input picture's own width and height, and that size is checked the same way. |
| `model` | callable | Keyword-only, required. |

## Outputs

PNG bytes, RGB mode. The decoded size is exactly `size`, or the input picture's size when `size` is None. The function writes no text chunks and no path into the PNG.

## Rules

1. **Step rule.** Steps are `sdcore.clamp_steps(1, strength)[0]`, so `steps * strength >= 1` always holds. At 0.05 that is 20 steps (`MAX_STEPS`). At 0.5 it is 2. The model is never called with fewer steps than the rule needs.
2. **Model side.** Each side of `size` is rounded down to a multiple of 64 with `sdcore.round64` (bounds 256 to 1024). The input is centre-cropped to that side with `sdcore.cover_resize`, then passed to the model.
3. **Checks before the model.** Every input check runs before the model is called. An invalid call never reaches the model.
4. **Output fit.** The model's result is centre-cropped and resized to exactly `size` with `sdcore.cover_resize`. So output size always equals the requested size, whatever the model returns.
5. **Errors.** Every error is `RestyleError`, a `ValueError`. Messages name the field and its allowed range. They never include the prompt, the picture bytes, a file path, or the model's exception text. A model failure is re-raised as `RestyleError("restyle model failed")` with `from None`, so the original text is dropped.
6. **No I/O.** No file access, no network, no module-level state. The function adds no randomness: the same model, inputs and seed give the same output.

## Licence note (product copy, not code)

Images made with SD-Turbo fall under the Stability AI Community License (`THIRD_PARTY_NOTICES.md`, the SD-Turbo row and the "SD-Turbo / Stability AI Community License" note). Commercial use is free for individuals and organisations under US$1M annual revenue. Stability asks commercial users to register. Above that revenue a separate licence from Stability AI is needed. funkbox does not bundle the model.

Before restyle is sold, the product copy must say this. The licence notice must appear with the restyle product. This spec is the only place it is recorded. The code does not carry it.

## Tests

`tests/test_e2_restyle.py`. A stub model stands in for SD-Turbo. Nothing is downloaded and nothing calls the network.

1. Step rule: strengths 0.05, 0.3, 0.5 and 0.95 each reach the stub with `steps * strength >= 1` and with steps equal to `clamp_steps(1, strength)[0]`. Strength 0.05 gives 20.
2. Strength range: 0.04, 0.96, NaN, infinity and a boolean raise `RestyleError`, and the stub is never called.
3. Output size: no size gives an output the same size as the input. An explicit size gives that size. A small input with a larger size works. The stub receives an init image whose sides are multiples of 64.
4. Error text: a prompt that contains a file path and a unique marker, with an invalid strength, gives an error that contains neither the marker nor the path. A model that raises with a path and the marker gives a `RestyleError` with neither in its text. Non-image bytes, an empty prompt, a negative seed and an out-of-range size each give `RestyleError`.
5. Output format: the result starts with the PNG signature and decodes as RGB.
6. Determinism: the same stub and inputs give identical bytes twice. The seed reaches the stub unchanged.

Run: `C:\dev\funkbox\.venv\Scripts\python.exe -m pytest -q tests/test_e2_restyle.py` from the worktree root.

## Out of scope

- The production model adapter and the hosted worker wiring.
- The 2 vCPU wall-clock measurement for a 512x512 restyle (owner's D8 CPU budget). It is measured on the hosted box, not in this change.
- Any change to `images.py`, `sdcore.py`, `apps.py`, the MCP tools, or the FastSD launcher.
- Any product copy for the licence.

## Done when

- `docs/specs/restyle-function.md` (this file) is committed before the code.
- `funkbox/studio/restyle.py` and `tests/test_e2_restyle.py` exist and the targeted test file passes.
- `images.py`, `sdcore.py` and the MCP tools are unchanged.
- The report states the 2 vCPU timing as not measured here, unless the hosted box measures it.

## Open questions

- Which side supplies the production `model` callable, and where does it run (hosted worker or laptop FastSD)?
- Should a negative seed mean random, as the CLI does with -1? The spec rejects it so hosted results can be reproduced.
- Is 2048 the right ceiling for `size`?