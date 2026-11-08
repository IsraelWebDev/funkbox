"""v3.2 E1: ``poster render --layers`` writes each visible layer as its own PNG and a manifest; the composed PNG
does not change, and a failed or colliding export never leaves a mixed or overwritten set of files behind."""

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
from funkbox.stills import render, schema

ROOT = Path(__file__).resolve().parents[2]
VISIBLE = ["bg", "pic", "tint", "inv", "masked"]


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
            {"id": "inv", "type": "solid", "color": "#3366cc", "adjust": {"invert": True}},
            {
                "id": "masked",
                "type": "solid",
                "color": "#00aa00",
                "mask": {"type": "radial", "cx": 0.5, "cy": 0.5, "radius": 0.4, "feather": 0.2},
            },
        ],
    }


def other_poster(w=128, h=96):
    return {"version": 1, "canvas": {"width": w, "height": h}, "layers": [{"id": "x", "type": "solid", "color": "#ffffff"}]}


def run_cli(*args):
    r = subprocess.run(
        [sys.executable, "-m", "funkbox", "poster", "render", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return r


def write_poster(work, doc):
    pj = work / "demo.poster.json"
    pj.write_text(json.dumps(doc))
    return pj


def test_schema_refuses_layer_ids_that_differ_only_in_case():
    errs = schema.check(
        {
            "version": 1,
            "canvas": {"width": 128, "height": 96},
            "layers": [{"id": "Bg", "type": "solid", "color": "#000000"}, {"id": "bg", "type": "solid", "color": "#ffffff"}],
        }
    )
    assert any("duplicate layer id 'bg'" in e for e in errs), errs


def test_cli_layers_flag_leaves_the_composed_png_byte_identical(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    pj = write_poster(work, poster_doc(photo))
    run_cli(pj, "--out", work / "plain", "--name", "demo")
    run_cli(pj, "--out", work / "layered", "--name", "demo", "--layers")
    assert (work / "plain" / "demo.png").read_bytes() == (work / "layered" / "demo.png").read_bytes()
    assert not list((work / "plain").glob("demo-*.png"))  # no flag, no layer files


def test_cli_layers_flag_writes_one_png_per_visible_layer_and_a_manifest(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    pj = write_poster(work, poster_doc(photo))
    r = run_cli(pj, "--out", work / "layered", "--name", "demo", "--layers")
    assert "layers: " in r.stdout and "layer:tint: " in r.stdout
    out = work / "layered"
    man = json.loads((out / "demo.layers.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in man["layers"]] == VISIBLE
    assert [e["index"] for e in man["layers"]] == [0, 1, 2, 4, 5]  # the hidden layer at index 3 leaves a gap
    assert (man["width"], man["height"]) == (128, 96) and man["order"] == "bottom-to-top"
    for e in man["layers"]:
        assert e["file"] == f"demo-{e['id']}.png"
        im = Image.open(out / e["file"])
        assert im.mode == "RGBA" and im.size == (e["width"], e["height"]) == (128, 96)
    assert not (out / "demo-hidden.png").exists()
    assert (out / "demo.png").exists()


def test_cli_layer_pngs_equal_the_compositor_layers_with_mask_and_fx_applied(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    doc = poster_doc(photo)
    pj = write_poster(work, doc)
    run_cli(pj, "--out", work / "cli", "--name", "demo", "--layers")
    ref = {lay.id: lay.rgba for lay in render.render_document(copy.deepcopy(doc), work).layers}
    assert list(ref) == VISIBLE
    for lid in VISIBLE:
        arr = np.asarray(Image.open(work / "cli" / f"demo-{lid}.png"))
        assert arr.shape == (96, 128, 4)
        assert np.array_equal(arr, ref[lid]), lid
    inv = np.asarray(Image.open(work / "cli" / "demo-inv.png"))
    assert (inv[..., :3] == np.array([255 - 0x33, 255 - 0x66, 255 - 0xCC], np.uint8)).all()  # invert applied
    assert (inv[..., 3] == 255).all()
    masked = np.asarray(Image.open(work / "cli" / "demo-masked.png"))
    assert masked[48, 64, 3] == 255 and masked[0, 0, 3] == 0  # radial mask applied
    tint = np.asarray(Image.open(work / "cli" / "demo-tint.png"))
    assert (tint[..., 3] == 128).all()  # transparency kept


def test_layer_export_refuses_a_bad_name_or_folder_before_writing(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    r = render.render_document(poster_doc(photo), work)
    with pytest.raises(ValueError, match="bad output name"):
        render.write_layers(r, work / "x", "../x")
    with pytest.raises(ValueError, match="outside the allowed"):
        render.write_layers(r, "/tmp/stills-elsewhere", "demo")
    assert not (work / "x").exists()


def test_a_failed_layer_write_leaves_no_manifest_and_does_not_touch_the_composed_png(work, gl, monkeypatch):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    r = render.render_document(poster_doc(photo), work)
    out = work / "f"
    render.write_outputs(r, out, "demo", ["png"])
    render.write_layers(r, out, "demo")  # a complete earlier export, with its manifest
    assert (out / "demo.layers.json").exists()
    composed = (out / "demo.png").read_bytes()
    real = render._save_layer_png
    calls = {"n": 0}

    def fails_on_the_second_layer(im, path):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("file is locked by another program")
        real(im, path)

    monkeypatch.setattr(render, "_save_layer_png", fails_on_the_second_layer)
    with pytest.raises(OSError, match="locked"):
        render.write_layers(r, out, "demo")
    assert not (out / "demo.layers.json").exists()  # no manifest from the old run survives
    assert (out / "demo.png").read_bytes() == composed  # write_layers never touches the composed file
    assert not list(out.glob("*.part"))


def test_a_layer_hidden_on_the_next_render_loses_its_file(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    doc = poster_doc(photo)
    pj = write_poster(work, doc)
    out = work / "s"
    run_cli(pj, "--out", out, "--name", "demo", "--layers")
    assert (out / "demo-pic.png").exists()
    doc["layers"][1]["visible"] = False  # hide "pic"
    write_poster(work, doc)
    run_cli(pj, "--out", out, "--name", "demo", "--layers")
    assert not (out / "demo-pic.png").exists()
    man = json.loads((out / "demo.layers.json").read_text(encoding="utf-8"))
    assert "pic" not in [e["id"] for e in man["layers"]]
    assert [e["id"] for e in man["layers"]] == ["bg", "tint", "inv", "masked"]


def test_when_every_layer_is_hidden_the_manifest_lists_none(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    doc = poster_doc(photo)
    for lay in doc["layers"]:
        lay["visible"] = False
    pj = write_poster(work, doc)
    out = work / "none"
    run_cli(pj, "--out", out, "--name", "demo", "--layers")
    assert (out / "demo.png").exists()
    assert json.loads((out / "demo.layers.json").read_text(encoding="utf-8"))["layers"] == []
    assert not list(out.glob("demo-*.png"))


def test_a_layer_export_refuses_to_overwrite_another_posters_composed_file(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "c"
    render.write_outputs(render.render_document(other_poster(), work), out, "demo-bg", ["png"])
    before = (out / "demo-bg.png").read_bytes()
    r = render.render_document(poster_doc(photo), work)
    with pytest.raises(ValueError, match="not a layer file of this poster"):
        render.write_layers(r, out, "demo")
    assert (out / "demo-bg.png").read_bytes() == before
    assert not (out / "demo.layers.json").exists() and not (out / "demo-pic.png").exists()


def test_a_composed_file_refuses_to_overwrite_another_posters_layer_file(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "c2"
    render.write_layers(render.render_document(poster_doc(photo), work), out, "demo")
    before = (out / "demo-bg.png").read_bytes()
    with pytest.raises(ValueError, match="not overwritten"):
        render.write_outputs(render.render_document(other_poster(), work), out, "demo-bg", ["png"])
    assert (out / "demo-bg.png").read_bytes() == before


def test_a_layer_path_of_260_characters_or_more_is_refused_before_anything_is_written(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    r = render.render_document(poster_doc(photo), work)
    deep = work / ("d" * 200)
    with pytest.raises(ValueError, match="characters"):
        render.write_layers(r, deep, "demo")
    assert not deep.exists()
