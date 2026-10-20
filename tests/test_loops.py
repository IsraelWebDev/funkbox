"""funkbox.loops: the eight presets are v2 recipes; each renders, loops seamlessly and is deterministic."""
import numpy as np
import pytest

from funkbox.gl import Engine, validate_loop
from funkbox.gl.recipe import Recipe2, resample_params
from funkbox.loops import presets

SZ = 64


def small(name, seed=1, **kw):
    return presets.preset_recipe(name, seed, width=SZ, fps=10, seconds=1.0, **kw)


def test_eight_presets_are_v2_recipes():
    assert sorted(presets.LOOKS) == sorted(["acid-plasma", "pop-moire", "liquid-disco", "hypno-tunnel",
                                            "lava-lamp", "op-art", "frozen-kaleido", "party-poster"])
    assert presets.DEFAULT_PRESET in presets.LOOKS
    for name, d in presets.PRESETS.items():
        rec = Recipe2.from_dict(d)
        assert rec.layers and all(lay.type == "shader" for lay in rec.layers), name


@pytest.mark.parametrize("name", sorted(presets.LOOKS))
def test_preset_renders_loops_and_is_deterministic(gl, name):
    rec = small(name)
    rep = validate_loop(rec, size=SZ)
    assert rep.ok, (name, rep.reason)
    e = Engine(SZ, SZ, gl)
    try:
        a = e.render(rec, 3)
        b = e.render(small(name), 3)
        c = e.render(small(name, seed=2), 3)
    finally:
        e.release()
    assert a.shape == (SZ, SZ, 3) and a.std() > 3
    assert np.array_equal(a, b)                       # same seed, same pixels
    assert not np.array_equal(a, c) or name == "op-art"   # another seed changes the look (op-art is seed-light)


def test_overrides_and_random_looks():
    d = presets.look("liquid-disco", overrides=["kaleido=4", "chroma=0"])
    assert d["kaleido"] == 4 and d["chroma"] == 0
    assert presets.random_look(5) == presets.random_look(5) and presets.random_look(5) != presets.random_look(6)
    with pytest.raises(ValueError):
        presets.look("nope")
    assert presets.parse_seeds("1-3,7") == [1, 2, 3, 7]


def test_resample_keeps_look_controls():
    rec = small("liquid-disco")
    assert rec.layers[0].params["u_kaleido"] == 6
    resample_params(rec)
    assert rec.layers[0].params == {"u_kaleido": 6, "k_spin": 1}   # zoom 0 is the default
