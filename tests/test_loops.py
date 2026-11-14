"""funkbox.loops: the nine presets are v2 recipes; each renders, loops seamlessly and is deterministic."""

import numpy as np
import pytest

from funkbox.gl import Engine, validate_loop
from funkbox.gl.recipe import Recipe2, resample_params
from funkbox.loops import presets

SZ = 64


def small(name, seed=1, **kw):
    return presets.preset_recipe(name, seed, width=SZ, fps=10, seconds=1.0, **kw)


def test_nine_presets_are_v2_recipes():
    assert sorted(presets.LOOKS) == sorted(
        [
            "acid-plasma",
            "pop-moire",
            "liquid-disco",
            "hypno-tunnel",
            "lava-lamp",
            "op-art",
            "frozen-kaleido",
            "party-poster",
            "liquid-metal",
        ]
    )
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
    assert np.array_equal(a, b)  # same seed, same pixels
    assert not np.array_equal(a, c) or name == "op-art"  # another seed changes the look (op-art is seed-light)


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
    assert rec.layers[0].params == {"u_kaleido": 6, "k_spin": 1}  # zoom 0 is the default


def _sidecar(res):
    import json
    from pathlib import Path

    return json.loads(Path(res.paths["json"]).read_text(encoding="utf-8"))


def test_sidecar_records_checked_loop_and_still_loads(gl, tmp_path):
    from funkbox.gl import render_recipe
    from funkbox.gl.loopcheck import loop_check_entry
    from funkbox.gl.recipe import load_recipe

    rec = small("acid-plasma")
    rep = validate_loop(rec, size=SZ)
    res = render_recipe(rec, tmp_path, "chk", mp4=False, gif=False, loop_check=loop_check_entry(rep))
    side = _sidecar(res)
    assert side["loop_check"]["status"] == "ok"
    assert {"max_diff", "bad_pixel_frac", "seam_step", "typical_step"} <= set(side["loop_check"])
    assert load_recipe(res.paths["json"]).to_dict() == res.recipe.to_dict()  # the new key is ignored on load


def test_sidecar_without_loop_check_has_no_entry(gl, tmp_path):
    from funkbox.gl import render_recipe

    res = render_recipe(small("acid-plasma"), tmp_path, "plain", mp4=False, gif=False)
    assert "loop_check" not in _sidecar(res)


def test_mcp_render_path_records_ok_and_skipped_for_audio(gl, tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    import wave

    from funkbox.gl.recipe import load_recipe
    from funkbox.loops import tools

    res, rep = tools._render_gl(small("acid-plasma"), tmp_path, "mcp-ok")
    assert rep is not None and _sidecar(res)["loop_check"]["status"] == "ok"

    wav = tmp_path / "s.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000)
    rec = small("acid-plasma")
    rec.settings.audio = str(wav)
    res, rep = tools._render_gl(rec, tmp_path, "mcp-audio")
    assert rep is None
    side = _sidecar(res)
    assert side["loop_check"] == {"status": "skipped", "reason": "audio"}
    assert load_recipe(res.paths["json"]).settings.audio  # still loads
