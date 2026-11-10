"""Masks in two more forms, and the bevel that makes a masked shape read as embossed.

* ``bevel``: a layer with a mask is lit from its mask's edges, so the shape reads as raised. Only colour changes,
  never alpha; the interior of the shape is untouched.
* ``mask`` as ``{"text": ...}``: the mask is typed text, drawn by the same poster text pipeline as a still (shaping,
  wrapping, glyph check, text audit). A typo or a missing glyph is an error, not a silently different mask.
"""

import numpy as np
import pytest
from PIL import Image

from funkbox.gl import Engine
from funkbox.gl.recipe import Layer, Recipe2, Settings2, load_recipe, resolve_recipe

GREY = "vec4 effect(vec2 p){ return vec4(0.5, 0.5, 0.5, 1.0); }"


def recipe(*layers, size=64):
    return Recipe2(settings=Settings2(width=size, height=size, fps=10, seconds=1.0, seed=3), layers=list(layers))


def write_mask(folder, name, alpha):
    h, w = alpha.shape
    arr = np.full((h, w, 4), 255, np.uint8)
    arr[..., 3] = alpha
    Image.fromarray(arr, "RGBA").save(folder / name)
    return name


def block_alpha(n=64):
    alpha = np.zeros((n, n), np.uint8)
    alpha[16:48, 16:48] = 255  # a 32 px square in the middle
    return alpha


@pytest.fixture()
def inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    return tmp_path


@pytest.fixture()
def eng(gl):
    e = Engine(64, 64, gl)
    yield e
    e.release()


# --- recipe fields and validation ------------------------------------------------------------------------------ #


def test_bevel_and_text_mask_survive_a_round_trip():
    lay = Layer.from_dict(
        {
            "type": "shader",
            "source": GREY,
            "mask": {"text": "FUNK", "size": 0.3},
            "bevel": {"width": 4, "strength": 0.6},
        }
    )
    again = Layer.from_dict(lay.to_dict())
    assert again.mask == {"text": "FUNK", "size": 0.3} and again.bevel == {"width": 4, "strength": 0.6}


def test_an_unbevelled_layer_writes_no_bevel_key():
    assert "bevel" not in Layer(source=GREY).to_dict()


def test_bevel_needs_a_mask(inputs):
    with pytest.raises(ValueError, match="bevel needs a mask"):
        resolve_recipe(recipe(Layer(source=GREY, bevel={"width": 4})))


@pytest.mark.parametrize(
    "bevel", [{"width": 0}, {"width": 65}, {"strength": 1.5}, {"strength": -0.1}, {"colour": "red"}]
)
def test_bad_bevel_settings_are_refused(inputs, bevel):
    write_mask(inputs, "b.png", block_alpha())
    with pytest.raises(ValueError):
        resolve_recipe(recipe(Layer(source=GREY, mask="b.png", bevel=bevel)))


def test_a_text_mask_needs_text(inputs):
    with pytest.raises(ValueError, match="text"):
        resolve_recipe(recipe(Layer(source=GREY, mask={"size": 0.3})))


def test_a_text_mask_refuses_unknown_keys(inputs):
    with pytest.raises(ValueError, match="unknown"):
        resolve_recipe(recipe(Layer(source=GREY, mask={"text": "FUNK", "colour": "red"})))


# --- the bevel (GL) --------------------------------------------------------------------------------------------- #


def test_zero_strength_bevel_changes_nothing(eng, inputs):
    write_mask(inputs, "b.png", block_alpha())
    plain = eng.render(recipe(Layer(source=GREY, mask="b.png")), 0)
    flat = eng.render(recipe(Layer(source=GREY, mask="b.png", bevel={"width": 4, "strength": 0.0})), 0)
    assert np.abs(plain.astype(int) - flat.astype(int)).max() <= 1


def test_the_bevel_lights_one_edge_and_shades_the_other(eng, inputs):
    write_mask(inputs, "b.png", block_alpha())
    arr = eng.render(recipe(Layer(source=GREY, mask="b.png", bevel={"width": 4, "strength": 0.8, "angle": 0})), 0)
    left = arr[32, 16:20, 0].mean()  # the 4 px band just inside the left edge (the bevel's width)
    right = arr[32, 44:48, 0].mean()  # the 4 px band just inside the right edge
    assert abs(right - left) > 12


def test_the_bevel_leaves_the_interior_as_it_was(eng, inputs):
    write_mask(inputs, "b.png", block_alpha())
    flat = eng.render(recipe(Layer(source=GREY, mask="b.png")), 0)
    lit = eng.render(recipe(Layer(source=GREY, mask="b.png", bevel={"width": 4, "strength": 0.8})), 0)
    assert abs(int(flat[32, 32, 0]) - int(lit[32, 32, 0])) <= 1


def test_the_bevel_never_changes_coverage(eng, inputs):
    write_mask(inputs, "b.png", block_alpha())
    flat = eng.render(recipe(Layer(source=GREY, mask="b.png")), 0)
    lit = eng.render(recipe(Layer(source=GREY, mask="b.png", bevel={"width": 6, "strength": 1.0})), 0)
    assert ((flat.sum(axis=2) > 0) == (lit.sum(axis=2) > 0)).all()


# --- the text mask (GL and the text audit) ----------------------------------------------------------------------- #


def test_a_text_mask_is_text_drawn_with_the_poster_pipeline():
    from funkbox.gl.masks import text_mask

    alpha, audit = text_mask({"text": "FUNK"}, 128, 128, None)
    assert alpha.shape == (128, 128) and alpha.dtype == np.uint8
    coverage = (alpha > 127).mean()
    assert 0.02 < coverage < 0.6
    assert audit["text"] == "FUNK" and audit["text_exact"] is True


def test_a_text_mask_shows_the_layer_only_where_the_letters_are(eng):
    arr = eng.render(recipe(Layer(source=GREY, mask={"text": "I", "size": 0.5})), 0)
    assert arr.max() > 100  # letter ink, at the layer's own brightness
    assert arr[:3].mean() < 5 and arr[-3:].mean() < 5  # the margins are clear


def test_a_text_mask_refuses_text_that_is_not_drawn_exactly(monkeypatch):
    from funkbox.gl import masks

    def drawn_wrong(*a, **k):
        return np.zeros((8, 8), np.uint8), {"text": "FUNX", "text_exact": False}

    monkeypatch.setattr(masks, "_draw_text_alpha", drawn_wrong)
    with pytest.raises(ValueError, match="not drawn exactly"):
        masks.text_mask({"text": "FUNK"}, 8, 8, None)


# --- the catalog and the example ------------------------------------------------------------------------------- #


def _layer_schema():
    from funkbox import catalog

    tools = {t["name"]: t["input_schema"] for t in catalog.build()["tools"]}
    rs = tools["loop_render"]["properties"]["recipe"]
    for alt in rs.get("anyOf", [rs]):
        if alt.get("type") == "object":
            return alt["properties"]["layers"]["items"]["properties"]
    raise AssertionError("no recipe object schema")


def test_the_catalog_shows_bevel_and_the_two_mask_forms():
    layer = _layer_schema()
    mask_options = layer["mask"]["anyOf"]
    assert {"type": "string"} in mask_options
    text_form = next(o for o in mask_options if o.get("type") == "object")
    assert text_form["required"] == ["text"]
    assert layer["bevel"]["type"] == "object"


def test_the_text_chrome_example_loads_and_resolves():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "examples" / "layer_mask" / "funkbox-text-chrome.json"
    rec = resolve_recipe(load_recipe(path))
    assert rec.layers[0].mask == {"text": "FUNK BOX", "size": 0.22, "font": "Funkbox Block:800"}
    assert rec.layers[0].bevel["strength"] > 0
