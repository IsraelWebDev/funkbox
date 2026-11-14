"""The liquid-metal look: the `metal` effect, its four palettes and the look entry."""

from pathlib import Path

import pytest

from funkbox.gl import validate_loop
from funkbox.gl.recipe import PALETTES, V1_EFFECTS, Recipe2, resolve_recipe
from funkbox.gl.shader import ShaderSource
from funkbox.loops import presets

ROOT = Path(__file__).resolve().parents[2]
METAL_PALETTES = ("chrome", "gold", "mercury", "iridescent")
EXISTING_LOOKS = {
    "acid-plasma",
    "pop-moire",
    "liquid-disco",
    "hypno-tunnel",
    "lava-lamp",
    "op-art",
    "frozen-kaleido",
    "party-poster",
}


def small(seed=1):
    return presets.preset_recipe("liquid-metal", seed, width=64, fps=10, seconds=1.0)


def test_look_exists_in_looks_and_presets():
    assert presets.LOOKS["liquid-metal"] == dict(effect="metal", palette="chrome", cycles=1, vignette=0.3)
    assert "liquid-metal" in presets.PRESETS
    assert EXISTING_LOOKS <= set(presets.LOOKS) and len(presets.LOOKS) == 9
    assert presets.DEFAULT_PRESET == "liquid-disco"


def test_metal_is_a_v1_effect_with_a_shader_file():
    assert "metal" in V1_EFFECTS
    assert (ROOT / "shaders" / "metal.frag").is_file()


@pytest.mark.parametrize("name", METAL_PALETTES)
def test_metal_palettes_exist_in_the_cosine_format(name):
    assert name in PALETTES
    pal = PALETTES[name]
    assert len(pal) == 4 and all(len(v) == 3 and all(isinstance(x, float) for x in v) for v in pal)


def test_recipe_from_liquid_metal_validates():
    rec = Recipe2.from_dict(presets.PRESETS["liquid-metal"])
    assert rec.palette == "chrome" and rec.layers[0].shader == "shaders/metal.frag"
    r = resolve_recipe(small())  # loads the .frag and samples its params
    assert r.layers[0].source and "effect(" in r.layers[0].source


@pytest.mark.parametrize("name", METAL_PALETTES)
def test_every_metal_palette_makes_a_valid_recipe(name):
    d = presets.look("liquid-metal", overrides={"palette": name})
    rec = presets.look_recipe(d)
    assert rec.palette == name
    resolve_recipe(rec)


def test_metal_shader_compiles(gl):
    res = ShaderSource.from_file(ROOT / "shaders" / "metal.frag").compile(gl)
    assert res.ok, res.errors


def test_liquid_metal_loop_check_passes(gl):
    rep = validate_loop(small(), size=64)
    assert rep.ok, rep.reason


@pytest.mark.parametrize("name", METAL_PALETTES)
def test_each_palette_loops(gl, name):
    rec = presets.look_recipe(presets.look("liquid-metal", overrides={"palette": name}))
    rec.settings.width = rec.settings.height = 64
    rec.settings.fps, rec.settings.seconds = 10, 1.0
    assert validate_loop(rec, size=64).ok
