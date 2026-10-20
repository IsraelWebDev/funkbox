import numpy as np
import pytest
from PIL import Image

from funkbox.gl import Engine
from funkbox.gl.recipe import Layer, PostPass, Recipe2, Settings2


def recipe(*layers, size=64, **kw):
    return Recipe2(settings=Settings2(width=size, height=size, fps=10, seconds=1.0, seed=3),
                   layers=list(layers), **kw)


@pytest.fixture()
def eng(gl):
    e = Engine(64, 64, gl)
    yield e
    e.release()


def test_shape_dtype_contiguous(eng):
    arr = eng.render(recipe(Layer(source="vec4 effect(vec2 p){ return vec4(p*0.5+0.5, 0.2, 1.0); }")), 0)
    assert arr.shape == (64, 64, 3) and arr.dtype == np.uint8
    assert arr.flags["C_CONTIGUOUS"]


def test_odd_size_rounds_down(gl):
    e = Engine(65, 63, gl)
    try:
        arr = e.render(recipe(Layer(source="vec4 effect(vec2 p){ return vec4(1.0); }"), size=65), 0)
        assert arr.shape == (62, 64, 3)
    finally:
        e.release()


def test_top_half_is_red(eng):
    """uv().y points up, so p.y > 0 is the TOP of the image; catches the Y-flip bug."""
    src = "vec4 effect(vec2 p){ return p.y > 0.0 ? vec4(1.0,0.0,0.0,1.0) : vec4(0.0,0.0,0.0,1.0); }"
    arr = eng.render(recipe(Layer(source=src)), 0)
    assert arr[:30, :, 0].min() == 255 and arr[:30, :, 1].max() == 0
    assert arr[34:, :, 0].max() == 0


def test_difference_of_identical_layers_is_black(eng):
    src = "vec4 effect(vec2 p){ return vec4(palette(p.x*0.7), 1.0); }"
    arr = eng.render(recipe(Layer(source=src), Layer(source=src, blend="difference")), 0)
    assert arr.max() <= 2


def test_image_layer_roundtrip(eng, tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    Image.new("RGB", (40, 24), (200, 100, 50)).save(tmp_path / "solid.png")
    arr = eng.render(recipe(Layer(type="image", path="solid.png", fit="stretch")), 0)
    assert np.abs(arr.astype(int) - np.array([200, 100, 50])).max() <= 2


def test_image_orientation_and_contain(eng, tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    im = Image.new("RGB", (32, 32), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 32, 16))            # red top half
    im.save(tmp_path / "tb.png")
    arr = eng.render(recipe(Layer(type="image", path="tb.png", fit="stretch")), 0)
    assert arr[8, 32, 0] > 200 and arr[56, 32, 2] > 200
    wide = Image.new("RGB", (64, 16), (0, 255, 0))
    wide.save(tmp_path / "wide.png")
    arr = eng.render(recipe(Layer(type="image", path="wide.png", fit="contain")), 0)
    assert arr[2, 32, 1] == 0 and arr[32, 32, 1] > 250     # letterboxed


def test_blend_modes_run(eng):
    base = Layer(source="vec4 effect(vec2 p){ return vec4(0.5, 0.25, 0.75, 1.0); }")
    for mode in ("normal", "add", "screen", "multiply", "overlay", "difference", "softlight", "hue"):
        top = Layer(source="vec4 effect(vec2 p){ return vec4(0.2, 0.6, 0.4, 1.0); }", blend=mode)
        arr = eng.render(recipe(base, top), 0)
        assert arr.shape == (64, 64, 3)
    mul = eng.render(recipe(base, Layer(source=top.source, blend="multiply")), 0)
    assert abs(int(mul[10, 10, 0]) - round(0.5 * 0.2 * 255)) <= 2


def test_post_passes_run_and_unknown_rejected(eng):
    src = "vec4 effect(vec2 p){ return vec4(palette(p.x), 1.0); }"
    names = ["chroma", "halftone", "posterize", "scanlines", "vignette", "grain"]
    r = recipe(Layer(source=src), post=[PostPass(n) for n in names])
    arr = eng.render(r, 2)
    assert arr.shape == (64, 64, 3) and arr.std() > 5
    with pytest.raises(ValueError, match="unknown post pass"):
        eng.render(recipe(Layer(source=src), post=[PostPass("nope")]), 0)
    with pytest.raises(ValueError, match="no parameter"):
        eng.render(recipe(Layer(source=src), post=[PostPass("chroma", {"zzz": 1})]), 0)


def test_v1_layer_matches_funk(eng):
    import funk
    r = recipe(Layer(type="v1", effect="plasma", params={"k": [5.0, 6.0, 7.0, 8.0]},
                     extra={"palette": "acid", "cycles": 1}))
    arr = eng.render(r, 3)
    st = funk.Settings(width=64, height=64, fps=10, seconds=1.0, seed=3)
    ref = funk.render_frame(3, st, funk.Recipe(effect="plasma", palette="acid", cycles=1,
                                               vignette=0.0, params={"k": [5.0, 6.0, 7.0, 8.0]}))
    assert np.abs(arr.astype(int) - ref.astype(int)).max() <= 2


def test_text_overlay(eng):
    from funkbox.gl.recipe import TextSpec
    src = "vec4 effect(vec2 p){ return vec4(0.0, 0.0, 0.0, 1.0); }"
    arr = eng.render(recipe(Layer(source=src), text=TextSpec(text="HI", size=0.4)), 0)
    assert arr.max() > 200
