import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import funk
from funkgl import Engine, load_recipe, validate_loop
from funkgl.recipe import (Layer, PostPass, Recipe2, Settings2, TextSpec, recipe_from_v1,
                           resolve_path, save_recipe)

ROOT = Path(__file__).resolve().parent.parent


def sample_recipe() -> Recipe2:
    return Recipe2(
        settings=Settings2(width=64, height=64, fps=10, seconds=1.0, seed=7),
        palette="vapor", cycles=1,
        layers=[Layer(shader="shaders/warp.frag", params={"u_amp": 0.4}),
                Layer(type="image", path="examples/cat.png", fit="contain", blend="screen",
                      opacity=0.8, uv_warp=1.0, extra={"note": "keeps unknown keys"}),
                Layer(type="v1", effect="blobs", blend="add", opacity=0.5)],
        post=[PostPass("chroma", {"px": 4}), PostPass("grain", {"amount": 0.03})],
        text=TextSpec(text="FUNK", size=0.13, wobble=6.0, color="#ffffff", shadow="#ff2bd6"))


def test_v2_roundtrip(tmp_path):
    r = sample_recipe()
    saved = save_recipe(r, tmp_path / "r.json")
    loaded = load_recipe(tmp_path / "r.json")
    assert loaded == saved
    d = json.loads((tmp_path / "r.json").read_text())
    shader_layer = d["layers"][0]
    assert shader_layer["source"].startswith("// Port of funk v1 effect 'warp'")
    assert len(shader_layer["source_sha256"]) == 64
    assert set(shader_layer["params"]) == {"u_amp", "u_freq", "u_stripes", "u_iters"}   # resolved
    assert shader_layer["params"]["u_amp"] == 0.4                                      # override wins
    assert d["layers"][1]["note"] == "keeps unknown keys"       # extra round-trips
    assert d["layers"][2]["params"] is not None                                       # v1 params resolved
    # saved recipe renders identically to the original
    e = Engine(64, 64)
    try:
        assert np.array_equal(e.render(r, 2), e.render(loaded, 2))
    finally:
        e.release()


def test_inline_source_survives_frag_edit(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "shaders").mkdir(parents=True)
    monkeypatch.setenv("FUNK_HOME", str(home))
    frag = home / "shaders" / "a.frag"
    frag.write_text("vec4 effect(vec2 p){ return vec4(1.0, 0.0, 0.0, 1.0); }\n")
    r = Recipe2(settings=Settings2(width=32, height=32), layers=[Layer(shader="shaders/a.frag")])
    save_recipe(r, tmp_path / "s.json")
    frag.write_text("vec4 effect(vec2 p){ return vec4(0.0, 1.0, 0.0, 1.0); }\n")
    e = Engine(32, 32)
    try:
        arr = e.render(load_recipe(tmp_path / "s.json"), 0)
    finally:
        e.release()
    assert arr[..., 0].min() == 255 and arr[..., 1].max() == 0


def test_v1_sidecar_loads_and_renders(tmp_path):
    out = tmp_path / "v1out"
    subprocess.run([sys.executable, str(ROOT / "funk.py"), "render", "--preset", "hypno-tunnel",
                    "--seed", "2", "--seconds", "0.5", "--size", "64", "--no-gif", "--no-mp4",
                    "--out", str(out)], check=True, stdout=subprocess.DEVNULL, cwd=ROOT)
    side = next(out.glob("*.json"))
    r = load_recipe(side)
    assert len(r.layers) == 1 and r.layers[0].type == "v1" and r.layers[0].effect == "tunnel"
    assert [p.name for p in r.post] == ["chroma", "scanlines", "vignette"]
    e = Engine(64, 64)
    try:
        arr = e.render(r, 0, grain=False)
    finally:
        e.release()
    ref = np.asarray(Image.open(next(out.glob("*.png"))))
    assert arr.shape == ref.shape
    assert np.abs(arr.astype(int) - ref.astype(int)).mean() < 1.0     # same look as v1


def test_v1_adapter_maps_every_option():
    rc = dict(effect="plasma", palette="toxic", cycles=2, spin=1, zoom=0.1, kaleido=6, posterize=7,
              halftone=9, chroma=3, scanlines=True, vignette=0.4, grain=0.05, text="HI",
              text_size=0.2, text_wobble=3.0, text_color="#112233", shadow="#445566",
              params={"k": [4, 5, 6, 7]})
    r = recipe_from_v1({"recipe": rc, "settings": {"width": 80, "height": 60, "fps": 24,
                                                   "seconds": 2.0, "seed": 9}})
    assert [p.name for p in r.post] == ["posterize", "halftone", "chroma", "scanlines", "vignette", "grain"]
    assert r.palette == "toxic" and r.cycles == 2
    assert r.layers[0].extra["kaleido"] == 6 and r.layers[0].params == {"k": [4, 5, 6, 7]}
    assert r.text.text == "HI" and r.text.shadow == "#445566"
    assert (r.settings.width, r.settings.height, r.settings.seed) == (80, 60, 9)


def test_path_outside_roots_rejected(tmp_path, monkeypatch):
    outside = tmp_path / "secret.png"
    Image.new("RGB", (4, 4)).save(outside)
    monkeypatch.setenv("FUNK_HOME", str(ROOT))
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "o"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path / "i"))
    (tmp_path / "o").mkdir(), (tmp_path / "i").mkdir()
    with pytest.raises(ValueError, match="outside"):
        resolve_path(str(outside))
    with pytest.raises(ValueError, match="outside"):
        resolve_path("../../../../etc/passwd")
    r = Recipe2(settings=Settings2(width=16, height=16),
                layers=[Layer(type="image", path=str(outside))])
    e = Engine(16, 16)
    try:
        with pytest.raises(ValueError):
            e.render(r, 0)
    finally:
        e.release()
    # inside a root is fine
    good = tmp_path / "i" / "ok.png"
    Image.new("RGB", (4, 4)).save(good)
    assert resolve_path("ok.png") == good.resolve()
    # shader paths are fenced too
    with pytest.raises(ValueError):
        resolve_path("/etc/hostname")


def test_example_disco_cat_loops_and_renders(gl):
    r = load_recipe(ROOT / "examples" / "disco_cat.json")
    rep = validate_loop(r, size=128)
    assert rep.ok, rep.reason
    assert "static" not in rep.reason
    e = Engine(96, 96, gl)
    try:
        arr = e.render(r, 0)
    finally:
        e.release()
    assert arr.std() > 20


def test_bom_recipe_and_shader_load(gl, tmp_path, monkeypatch):
    """Windows PowerShell 5 `Set-Content -Encoding utf8` writes a BOM; both must still load."""
    import json
    from pathlib import Path
    from funkgl.recipe import load_recipe
    monkeypatch.setenv("FUNK_OUT", str(tmp_path))
    frag = tmp_path / "bom.frag"
    frag.write_bytes(b"\xef\xbb\xbf" + b"vec4 effect(vec2 p){ return vec4(palette(p.x), 1.0); }\n")
    rec = {"version": 2, "settings": {"width": 32, "height": 32, "fps": 10, "seconds": 0.2},
           "layers": [{"type": "shader", "shader": str(frag)}]}
    rp = tmp_path / "bom.json"
    rp.write_bytes(b"\xef\xbb\xbf" + json.dumps(rec).encode())
    r = load_recipe(rp)
    from funkgl.engine import Engine
    img = Engine(32, 32).render(r, 0)
    assert img.shape == (32, 32, 3)
