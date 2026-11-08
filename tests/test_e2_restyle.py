"""E2: restyle as a function (picture bytes in, PNG bytes out). A stub model stands in for SD-Turbo:
no download, no network, no GPU."""

from __future__ import annotations

import io
from fractions import Fraction

import pytest
from PIL import Image

from funkbox.studio.restyle import PICTURE_MAX_BYTES, UNUSABLE_RESULT, RestyleError, restyle_png

MARKER = "PROMPT-MARKER-7431"
PATH = r"C:\dev\secret\inputs\cat.png"
BOUNDARY = 0.3333333332333  # 3 * BOUNDARY < 1, so 3 steps is one short


def _png(w: int, h: int, color=(10, 200, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _gif(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 20, 20)).save(buf, format="GIF")
    return buf.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


class StubModel:
    def __init__(self, fn=None):
        self.calls: list[dict] = []
        self.fn = fn

    def __call__(self, init, prompt, strength, steps, seed):
        self.calls.append({"init": init, "prompt": prompt, "strength": strength, "steps": steps, "seed": seed})
        if self.fn is not None:
            return self.fn(init, seed)
        return Image.new("RGB", init.size, (128, 64, 32))


class _BrokenConvert(Image.Image):
    """A result whose conversion to RGB fails."""

    def __init__(self):
        super().__init__()
        self._size = (16, 16)

    def convert(self, *args, **kwargs):
        raise ValueError("cannot convert")


def _steps_rule_holds(steps: int, strength: float) -> bool:
    """steps * strength >= 1 in exact arithmetic, and no smaller step count also satisfies it."""
    s = Fraction(strength)
    return steps * s >= 1 and (steps == 1 or (steps - 1) * s < 1)


@pytest.mark.parametrize(
    "strength, steps",
    [(0.05, 20), (0.25, 4), (0.3, 4), (0.5, 2), (1 / 3, 4), (BOUNDARY, 4), (0.95, 2)],
)
def test_step_rule_holds_exactly_and_is_minimal(strength, steps):
    m = StubModel()
    restyle_png(_png(256, 256), "a prompt", strength, 7, model=m)
    got = m.calls[0]["steps"]
    assert got == steps
    assert _steps_rule_holds(got, strength)


def test_lowest_strength_needs_20_steps():
    m = StubModel()
    restyle_png(_png(256, 256), "p", 0.05, 1, model=m)
    assert m.calls[0]["steps"] == 20


def test_strength_bounds_are_accepted_with_their_step_counts():
    m = StubModel()
    restyle_png(_png(256, 256), "p", 0.05, 1, model=m)
    restyle_png(_png(256, 256), "p", 0.95, 1, model=m)
    assert [c["steps"] for c in m.calls] == [20, 2]


@pytest.mark.parametrize("strength", [0.04, 0.96, float("nan"), float("inf"), True, "0.5", None])
def test_strength_outside_range_is_refused_before_the_model(strength):
    m = StubModel()
    with pytest.raises(RestyleError):
        restyle_png(_png(256, 256), "p", strength, 1, model=m)
    assert m.calls == []


def test_output_size_matches_input_when_size_omitted():
    out = restyle_png(_png(300, 200), "p", 0.5, 1, model=StubModel())
    assert _size(out) == (300, 200)


@pytest.mark.parametrize("side", [64, 2048])
def test_size_limits_are_accepted(side):
    out = restyle_png(_png(64, 64), "p", 0.5, 1, (side, side), model=StubModel())
    assert _size(out) == (side, side)


def test_explicit_size_is_the_output_size_and_model_side_is_multiple_of_64():
    m = StubModel()
    out = restyle_png(_png(100, 100), "p", 0.5, 1, (512, 768), model=m)
    assert _size(out) == (512, 768)
    w, h = m.calls[0]["init"].size
    assert w % 64 == 0 and h % 64 == 0


def test_model_side_rounds_down_to_64():
    m = StubModel()
    out = restyle_png(_png(100, 100), "p", 0.5, 1, (500, 700), model=m)
    assert m.calls[0]["init"].size == (448, 640)
    assert _size(out) == (500, 700)


def test_model_result_of_another_size_is_fitted_to_the_output_size():
    model = StubModel(lambda init, seed: Image.new("RGB", (50, 30)))
    out = restyle_png(_png(100, 100), "p", 0.5, 1, (512, 512), model=model)
    assert _size(out) == (512, 512)


@pytest.mark.parametrize(
    "size", [(32, 512), (63, 512), (512, 2049), (512, 4096), (512,), "512x512", (True, 512), (512.0, 512)]
)
def test_bad_size_is_refused(size):
    m = StubModel()
    with pytest.raises(RestyleError):
        restyle_png(_png(256, 256), "p", 0.5, 1, size, model=m)
    assert m.calls == []


def test_bad_size_is_named_as_size():
    with pytest.raises(RestyleError) as e:
        restyle_png(_png(256, 256), "p", 0.5, 1, (32, 512), model=StubModel())
    assert "each side of the size" in str(e.value)


@pytest.mark.parametrize("picture_size", [(30, 30), (4096, 100)])
def test_picture_outside_size_range_is_named_as_picture(picture_size):
    m = StubModel()
    with pytest.raises(RestyleError) as e:
        restyle_png(_png(*picture_size), "p", 0.5, 1, model=m)
    assert "each side of the picture" in str(e.value)
    assert m.calls == []


def test_gif_input_is_accepted_and_first_frame_used():
    out = restyle_png(_gif(64, 64), "p", 0.5, 1, model=StubModel())
    assert _size(out) == (64, 64)


def test_output_is_png_rgb():
    out = restyle_png(_png(256, 256), "p", 0.5, 1, model=StubModel())
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(out)).mode == "RGB"


def test_seed_and_prompt_reach_the_model_and_output_is_deterministic():
    m = StubModel(lambda init, seed: Image.new("RGB", init.size, (seed % 256, 0, 0)))
    a = restyle_png(_png(256, 256), "a cat", 0.5, 7, model=m)
    b = restyle_png(_png(256, 256), "a cat", 0.5, 7, model=m)
    assert a == b
    assert m.calls[0]["seed"] == 7
    assert m.calls[0]["prompt"] == "a cat"
    assert m.calls[0]["strength"] == 0.5


@pytest.mark.parametrize("seed", [-1, 2**32])
def test_seed_out_of_range_is_refused(seed):
    with pytest.raises(RestyleError):
        restyle_png(_png(256, 256), "p", 0.5, seed, model=StubModel())


@pytest.mark.parametrize("prompt", ["", "   ", None, 42])
def test_empty_prompt_is_refused_before_the_model(prompt):
    m = StubModel()
    with pytest.raises(RestyleError):
        restyle_png(_png(256, 256), prompt, 0.5, 1, model=m)
    assert m.calls == []


@pytest.mark.parametrize("picture", [b"", b"junk", "not bytes", None])
def test_unreadable_picture_is_refused(picture):
    m = StubModel()
    with pytest.raises(RestyleError):
        restyle_png(picture, "p", 0.5, 1, model=m)
    assert m.calls == []


def test_unreadable_picture_has_no_chained_exception():
    with pytest.raises(RestyleError) as e:
        restyle_png(b"junk", "p", 0.5, 1, model=StubModel())
    assert e.value.__context__ is None
    assert e.value.__cause__ is None


def test_picture_over_byte_limit_is_refused_before_decode():
    m = StubModel()
    with pytest.raises(RestyleError, match="larger than 40 MiB"):
        restyle_png(b"\x00" * (PICTURE_MAX_BYTES + 1), "p", 0.5, 1, model=m)
    assert m.calls == []


def test_picture_at_byte_limit_still_goes_to_decode():
    with pytest.raises(RestyleError, match="not a readable image"):
        restyle_png(b"\x00" * PICTURE_MAX_BYTES, "p", 0.5, 1, model=StubModel())


@pytest.mark.parametrize(
    "make",
    [
        lambda init, seed: Image.new("RGB", (0, 0)),
        lambda init, seed: Image.new("RGB", (0, 16)),
        lambda init, seed: None,
        lambda init, seed: "not an image",
        lambda init, seed: _BrokenConvert(),
    ],
)
def test_unusable_model_result_is_refused(make):
    with pytest.raises(RestyleError) as e:
        restyle_png(_png(256, 256), "p", 0.5, 1, model=StubModel(make))
    assert str(e.value) == UNUSABLE_RESULT
    assert e.value.__context__ is None


@pytest.mark.parametrize("mode", ["L", "RGBA", "P", "F", "I;16", "CMYK"])
def test_result_in_another_mode_is_converted_to_rgb(mode):
    model = StubModel(lambda init, seed: Image.new(mode, init.size))
    out = restyle_png(_png(256, 256), "p", 0.5, 1, model=model)
    assert Image.open(io.BytesIO(out)).mode == "RGB"


def test_refusals_carry_no_path_or_prompt_text():
    text_prompt = f"{MARKER} at {PATH}"
    cases = [
        lambda: restyle_png(_png(256, 256), text_prompt, 2.0, 1, model=StubModel()),
        lambda: restyle_png(_png(256, 256), text_prompt, 0.5, -1, model=StubModel()),
        lambda: restyle_png(_png(256, 256), text_prompt, 0.5, 1, (8, 8), model=StubModel()),
        lambda: restyle_png(f"{PATH} {MARKER}".encode(), text_prompt, 0.5, 1, model=StubModel()),
        lambda: restyle_png(_png(256, 256), text_prompt, 0.5, 1, model=StubModel(lambda i, s: None)),
    ]
    for case in cases:
        with pytest.raises(RestyleError) as e:
            case()
        msg = str(e.value)
        assert MARKER not in msg
        assert PATH not in msg
        assert "secret" not in msg


def test_model_failure_is_reported_without_its_text_or_chain():
    def boom(init, seed):
        raise OSError(f"{PATH} failed for {MARKER}")

    with pytest.raises(RestyleError) as e:
        restyle_png(_png(256, 256), f"{MARKER} at {PATH}", 0.5, 1, model=StubModel(boom))
    err = e.value
    assert str(err) == "restyle model failed"
    assert err.__context__ is None
    assert err.__cause__ is None
    assert MARKER not in str(err) and PATH not in str(err)