"""E2: restyle as a function (picture bytes in, PNG bytes out). A stub model stands in for SD-Turbo:
no download, no network, no GPU."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from funkbox.studio import sdcore
from funkbox.studio.restyle import RestyleError, restyle_png

MARKER = "PROMPT-MARKER-7431"
PATH = r"C:\dev\secret\inputs\cat.png"


def _png(w: int, h: int, color=(10, 200, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


class StubModel:
    def __init__(self, fn=None):
        self.calls: list[dict] = []
        self.fn = fn

    def __call__(self, init, prompt, strength, steps, seed):
        self.calls.append({"init": init, "prompt": prompt, "strength": strength, "steps": steps, "seed": seed})
        if self.fn is not None:
            return self.fn(init, seed)
        return Image.new("RGB", init.size, (128, 64, 32))


@pytest.mark.parametrize("strength", [0.05, 0.25, 0.3, 0.5, 0.95])
def test_step_rule_steps_times_strength_at_least_one(strength):
    m = StubModel()
    restyle_png(_png(512, 512), "a prompt", strength, 7, model=m)
    call = m.calls[0]
    assert call["steps"] == sdcore.clamp_steps(1, strength)[0]
    assert call["steps"] * strength >= 1
    assert 1 <= call["steps"] <= sdcore.MAX_STEPS


def test_lowest_strength_needs_max_steps():
    m = StubModel()
    restyle_png(_png(256, 256), "p", sdcore.STRENGTH_MIN, 1, model=m)
    assert m.calls[0]["steps"] == sdcore.MAX_STEPS


@pytest.mark.parametrize("strength", [0.04, 0.96, float("nan"), float("inf"), True, "0.5", None])
def test_strength_outside_range_is_refused_before_the_model(strength):
    m = StubModel()
    with pytest.raises(RestyleError):
        restyle_png(_png(256, 256), "p", strength, 1, model=m)
    assert m.calls == []


def test_strength_bounds_are_accepted():
    for s in (sdcore.STRENGTH_MIN, sdcore.STRENGTH_MAX):
        restyle_png(_png(256, 256), "p", s, 1, model=StubModel())


def test_output_size_matches_input_when_size_omitted():
    out = restyle_png(_png(300, 200), "p", 0.5, 1, model=StubModel())
    assert Image.open(io.BytesIO(out)).size == (300, 200)


def test_explicit_size_is_the_output_size_and_model_side_is_multiple_of_64():
    m = StubModel()
    out = restyle_png(_png(100, 100), "p", 0.5, 1, (512, 768), model=m)
    assert Image.open(io.BytesIO(out)).size == (512, 768)
    w, h = m.calls[0]["init"].size
    assert w % 64 == 0 and h % 64 == 0


def test_model_side_rounds_down_to_64():
    m = StubModel()
    out = restyle_png(_png(100, 100), "p", 0.5, 1, (500, 700), model=m)
    assert m.calls[0]["init"].size == (448, 640)
    assert Image.open(io.BytesIO(out)).size == (500, 700)


def test_model_result_of_another_size_is_fitted_to_the_output_size():
    out = restyle_png(_png(100, 100), "p", 0.5, 1, (512, 512), model=StubModel(lambda init, seed: Image.new("RGB", (50, 30))))
    assert Image.open(io.BytesIO(out)).size == (512, 512)


@pytest.mark.parametrize("size", [(32, 512), (512, 4096), (512,), "512x512", (True, 512), (512.0, 512)])
def test_bad_size_is_refused(size):
    m = StubModel()
    with pytest.raises(RestyleError):
        restyle_png(_png(256, 256), "p", 0.5, 1, size, model=m)
    assert m.calls == []


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


def test_refusals_carry_no_path_or_prompt_text():
    text_prompt = f"{MARKER} at {PATH}"
    cases = [
        lambda: restyle_png(_png(256, 256), text_prompt, 2.0, 1, model=StubModel()),
        lambda: restyle_png(_png(256, 256), text_prompt, 0.5, -1, model=StubModel()),
        lambda: restyle_png(_png(256, 256), text_prompt, 0.5, 1, (8, 8), model=StubModel()),
        lambda: restyle_png(f"{PATH} {MARKER}".encode(), text_prompt, 0.5, 1, model=StubModel()),
    ]
    for case in cases:
        with pytest.raises(RestyleError) as e:
            case()
        msg = str(e.value)
        assert MARKER not in msg
        assert PATH not in msg
        assert "secret" not in msg


def test_model_failure_is_reported_without_its_text():
    def boom(init, seed):
        raise OSError(f"{PATH} failed for {MARKER}")

    with pytest.raises(RestyleError) as e:
        restyle_png(_png(256, 256), f"{MARKER} at {PATH}", 0.5, 1, model=StubModel(boom))
    assert str(e.value) == "restyle model failed"
    assert e.value.__cause__ is None
    assert MARKER not in str(e.value) and PATH not in str(e.value)