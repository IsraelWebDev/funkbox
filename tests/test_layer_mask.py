"""A layer can carry a ``mask``: an image whose alpha multiplies the layer's own alpha.

The mask is fitted to the frame with the same cover / contain / stretch rules as an image layer (``mask_fit``), so a
logo mask lands where the image layer would put the logo.
"""

import numpy as np
import pytest
from PIL import Image

from funkbox.gl import Engine
from funkbox.gl.recipe import Layer, Recipe2, Settings2, resolve_recipe

WHITE = "vec4 effect(vec2 p){ return vec4(1.0); }"


def recipe(*layers, size=64):
    return Recipe2(settings=Settings2(width=size, height=size, fps=10, seconds=1.0, seed=3), layers=list(layers))


def write_mask(folder, name, alpha, rgb=255):
    """Save an RGBA PNG whose alpha is ``alpha`` (2-D uint8 array)."""
    h, w = alpha.shape
    arr = np.full((h, w, 4), rgb, np.uint8)
    arr[..., 3] = alpha
    Image.fromarray(arr, "RGBA").save(folder / name)
    return name


@pytest.fixture()
def inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    return tmp_path


@pytest.fixture()
def eng(gl):
    e = Engine(64, 64, gl)
    yield e
    e.release()


# --- the recipe: fields, validation, round trip ---------------------------------------------------------------- #


def test_mask_fields_survive_a_round_trip():
    lay = Layer.from_dict({"type": "shader", "source": WHITE, "mask": "logo.png", "mask_fit": "contain"})
    assert lay.mask == "logo.png" and lay.mask_fit == "contain"
    again = Layer.from_dict(lay.to_dict())
    assert again.mask == "logo.png" and again.mask_fit == "contain"


def test_an_unmasked_layer_writes_no_mask_keys():
    d = Layer(source=WHITE).to_dict()
    assert "mask" not in d and "mask_fit" not in d


def test_mask_fit_defaults_to_cover():
    assert Layer(source=WHITE, mask="logo.png").mask_fit == "cover"


def test_a_bad_mask_fit_is_refused(inputs):
    write_mask(inputs, "m.png", np.full((8, 8), 255, np.uint8))
    with pytest.raises(ValueError, match="mask_fit"):
        resolve_recipe(recipe(Layer(source=WHITE, mask="m.png", mask_fit="fill")))


def test_a_missing_mask_file_is_refused(inputs):
    with pytest.raises(FileNotFoundError):
        resolve_recipe(recipe(Layer(source=WHITE, mask="nope.png")))


def test_a_mask_outside_the_roots_is_refused(inputs, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "m.png"
    write_mask(outside.parent, outside.name, np.full((8, 8), 255, np.uint8))
    with pytest.raises(ValueError, match="outside"):
        resolve_recipe(recipe(Layer(source=WHITE, mask=str(outside))))


# --- what the mask does to the pixels (GL) --------------------------------------------------------------------- #


def test_the_mask_keeps_the_layer_where_it_is_opaque(eng, inputs):
    alpha = np.zeros((64, 64), np.uint8)
    alpha[:, :32] = 255  # left half opaque
    eng_recipe = recipe(Layer(source=WHITE, mask=write_mask(inputs, "left.png", alpha)))
    arr = eng.render(eng_recipe, 0)
    assert arr[:, :28].mean() > 200 and arr[:, 36:].mean() < 30


def test_the_mask_is_not_mirrored_top_stays_top(eng, inputs):
    alpha = np.zeros((64, 64), np.uint8)
    alpha[:32] = 255  # top half opaque
    arr = eng.render(recipe(Layer(source=WHITE, mask=write_mask(inputs, "top.png", alpha))), 0)
    assert arr[:28].mean() > 200 and arr[36:].mean() < 30


def test_only_the_mask_alpha_counts_not_its_colour(eng, inputs):
    alpha = np.full((64, 64), 255, np.uint8)
    arr = eng.render(recipe(Layer(source=WHITE, mask=write_mask(inputs, "black.png", alpha, rgb=0))), 0)
    assert arr.mean() > 200


def test_mask_fit_contain_leaves_the_bands_outside_the_mask_clear(eng, inputs):
    alpha = np.full((64, 128), 255, np.uint8)  # 2:1 mask into a square frame
    arr = eng.render(recipe(Layer(source=WHITE, mask=write_mask(inputs, "wide.png", alpha), mask_fit="contain")), 0)
    assert arr[:12].mean() < 30 and arr[-12:].mean() < 30
    assert arr[20:44].mean() > 200


def test_the_layer_opacity_still_applies_under_a_mask(eng, inputs):
    alpha = np.full((64, 64), 255, np.uint8)
    arr = eng.render(recipe(Layer(source=WHITE, mask=write_mask(inputs, "full.png", alpha), opacity=0.5)), 0)
    assert 110 < arr.mean() < 145


def test_an_unmasked_layer_is_unchanged(eng):
    arr = eng.render(recipe(Layer(source=WHITE)), 0)
    assert arr.mean() > 250


# --- the catalog shows the mask (tool schema) ------------------------------------------------------------------- #


def test_the_recipe_schema_lists_mask_and_mask_fit():
    from funkbox import catalog

    tools = {t["name"]: t["input_schema"] for t in catalog.build()["tools"]}
    recipe_schema = tools["loop_render"]["properties"]["recipe"]
    for alt in recipe_schema.get("anyOf", [recipe_schema]):
        if alt.get("type") == "object":
            layer = alt["properties"]["layers"]["items"]["properties"]
            break
    else:
        raise AssertionError("no recipe object schema")
    assert {"type": "string"} in layer["mask"]["anyOf"]  # an image path ...
    assert any(o.get("type") == "object" for o in layer["mask"]["anyOf"])  # ... or typed text
    assert layer["mask_fit"]["enum"] == ["cover", "contain", "stretch"]


def test_the_example_recipe_loads_and_its_mask_resolves():
    from pathlib import Path

    from funkbox.gl.recipe import load_recipe

    path = Path(__file__).resolve().parents[1] / "examples" / "layer_mask" / "funkbox-chrome.json"
    rec = resolve_recipe(load_recipe(path))
    assert rec.layers[0].mask == "funkbox-mask.png" and rec.layers[0].mask_fit == "contain"
