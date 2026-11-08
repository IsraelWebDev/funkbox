"""v3.2 E1: ``poster render --layers`` writes each visible layer as its own PNG and a manifest; the composed PNG
does not change."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import stills_assets as assets
from stills_fixtures import gl, work  # noqa: F401
from funkbox.stills import render

ROOT = Path(__file__).resolve().parents[2]
VISIBLE = ["bg", "pic", "tint"]


def poster_doc(photo, w=128, h=96):
    return {
        "version": 1,
        "name": "demo",
        "canvas": {"width": w, "height": h, "background": "#202020"},
        "seed": 3,
        "layers": [
            {"id": "bg", "type": "solid", "color": "#334455"},
            {"id": "pic", "type": "image", "path": str(photo), "opacity": 0.6, "blend": "overlay"},
            {"id": "tint", "type": "solid", "color": "#ff000080", "blend": "screen"},
            {"id": "hidden", "type": "solid", "color": "#00ff00", "visible": False},
        ],
    }


def run_cli(*args):
    r = subprocess.run(
        [sys.executable, "-m", "funkbox", "poster", "render", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return r


def test_cli_layers_flag_leaves_the_composed_png_byte_identical(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    pj = work / "demo.poster.json"
    pj.write_text(json.dumps(poster_doc(photo)))
    run_cli(pj, "--out", work / "plain", "--name", "demo")
    run_cli(pj, "--out", work / "layered", "--name", "demo", "--layers")
    plain = (work / "plain" / "demo.png").read_bytes()
    layered = (work / "layered" / "demo.png").read_bytes()
    assert plain == layered
    assert not list((work / "plain").glob("demo-*.png"))  # no flag, no layer files


def test_cli_layers_flag_writes_one_png_per_visible_layer_and_a_manifest(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    pj = work / "demo.poster.json"
    pj.write_text(json.dumps(poster_doc(photo)))
    r = run_cli(pj, "--out", work / "layered", "--name", "demo", "--layers")
    assert "layers: " in r.stdout and "layer:tint: " in r.stdout
    out = work / "layered"
    man = json.loads((out / "demo.layers.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in man["layers"]] == VISIBLE
    assert [e["index"] for e in man["layers"]] == [0, 1, 2]
    assert (man["width"], man["height"]) == (128, 96) and man["order"] == "bottom-to-top"
    for e in man["layers"]:
        assert e["file"] == f"demo-{e['id']}.png"
        im = Image.open(out / e["file"])
        assert im.mode == "RGBA" and im.size == (e["width"], e["height"]) == (128, 96)
    assert not (out / "demo-hidden.png").exists()
    assert "hidden" not in [e["id"] for e in man["layers"]]
    assert (out / "demo.png").exists()


def test_layer_pngs_match_the_compositor_and_keep_transparency(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    d = poster_doc(photo)
    r = render.render_document(copy.deepcopy(d), work)
    assert [lay.id for lay in r.layers] == VISIBLE
    render.write_layers(r, work / "inproc", "demo")
    man = json.loads((work / "inproc" / "demo.layers.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in man["layers"]] == VISIBLE
    for e, lay in zip(man["layers"], r.layers):
        arr = np.asarray(Image.open(work / "inproc" / e["file"]))
        assert arr.shape == (96, 128, 4)
        assert np.array_equal(arr, lay.rgba)
    assert (np.asarray(Image.open(work / "inproc" / "demo-tint.png"))[..., 3] == 128).all()
    assert (np.asarray(Image.open(work / "inproc" / "demo-pic.png"))[..., 3] < 255).any()
    assert [e["blend"] for e in man["layers"]] == ["normal", "overlay", "screen"]
    assert [e["opacity"] for e in man["layers"]] == [1.0, 0.6, 1.0]


def test_layer_export_refuses_a_bad_name_or_folder_before_writing(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    r = render.render_document(poster_doc(photo), work)
    with pytest.raises(ValueError, match="bad output name"):
        render.write_layers(r, work / "x", "../x")
    with pytest.raises(ValueError, match="outside the allowed"):
        render.write_layers(r, "/tmp/stills-elsewhere", "demo")
    assert not (work / "x").exists()