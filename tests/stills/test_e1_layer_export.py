"""v3.2 E1: ``poster render --layers`` writes each visible layer as its own PNG and a manifest. The composed PNG never
changes, and a failed, refused or colliding export leaves no orphans and writes nothing it should not."""

import copy
import json
import os
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


def cli_command(*args):
    return [sys.executable, "-m", "funkbox", "poster", "render", *map(str, args)]


def run_cli(*args):
    r = subprocess.run(cli_command(*args), cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


def run_cli_failing(*args):
    r = subprocess.run(cli_command(*args), cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    return r.stdout + r.stderr


def write_poster(work, doc):
    pj = work / "demo.poster.json"
    pj.write_text(json.dumps(doc))
    return pj


def manifest(folder, name="demo"):
    return json.loads((folder / f"{name}.layers.json").read_text(encoding="utf-8"))


def failing_save_on_layer(monkeypatch, n):
    """Make the n-th layer PNG write fail the way a file held open by another program does."""
    real = render._save_layer_png
    calls = {"n": 0}

    def fails(im, path):
        calls["n"] += 1
        if calls["n"] == n:
            raise OSError("file is locked by another program")
        real(im, path)

    monkeypatch.setattr(render, "_save_layer_png", fails)

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
    man = manifest(out)
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

def test_a_failed_layer_write_leaves_no_layer_file_and_an_identical_rerun_succeeds(work, gl, monkeypatch):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "fresh"
    failing_save_on_layer(monkeypatch, 2)
    with pytest.raises(OSError, match="locked"):
        render.write_layers(render.render_document(poster_doc(photo), work), out, "demo")
    assert not list(out.glob("demo*"))  # no layer file, no manifest, no .part file
    monkeypatch.undo()
    render.write_layers(render.render_document(poster_doc(photo), work), out, "demo")  # the identical re-run
    assert [e["id"] for e in manifest(out)["layers"]] == VISIBLE
    assert all((out / e["file"]).exists() for e in manifest(out)["layers"])


def test_a_failed_layer_write_over_an_earlier_export_leaves_nothing_and_the_rerun_succeeds(  # noqa: F811
    work, gl, monkeypatch
):
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    r = render.render_document(poster_doc(photo), work)
    out = work / "over"
    render.write_outputs(r, out, "demo", ["png"])
    render.write_layers(r, out, "demo")  # a complete earlier export
    composed = (out / "demo.png").read_bytes()
    failing_save_on_layer(monkeypatch, 2)
    with pytest.raises(OSError, match="locked"):
        render.write_layers(r, out, "demo")
    assert not list(out.glob("demo-*.png")) and not (out / "demo.layers.json").exists()
    assert not list(out.glob("*.part"))
    assert (out / "demo.png").read_bytes() == composed  # write_layers never touches the composed file
    monkeypatch.undo()
    render.write_layers(r, out, "demo")
    assert [e["id"] for e in manifest(out)["layers"]] == VISIBLE


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
    assert [e["id"] for e in manifest(out)["layers"]] == ["bg", "tint", "inv", "masked"]


def test_when_every_layer_is_hidden_the_manifest_lists_none(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    doc = poster_doc(photo)
    for lay in doc["layers"]:
        lay["visible"] = False
    pj = write_poster(work, doc)
    out = work / "none"
    run_cli(pj, "--out", out, "--name", "demo", "--layers")
    assert (out / "demo.png").exists()
    assert manifest(out)["layers"] == []
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
    with pytest.raises(ValueError, match="characters"):
        render.check_layer_export(r, deep, "demo")
    assert not deep.exists()

def test_stale_layer_files_are_removed_after_the_manifest_and_a_failed_removal_leaves_nothing(  # noqa: F811
    work, gl, monkeypatch
):
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "stale"
    doc = poster_doc(photo)
    render.write_layers(render.render_document(copy.deepcopy(doc), work), out, "demo")
    doc["layers"][1]["visible"] = False  # hide "pic": its file is now stale
    r2 = render.render_document(copy.deepcopy(doc), work)
    real = render._unlink
    order: list[str] = []
    failed = {"once": False}

    def stub(p):
        order.append(p.name)
        if p.name == "demo-pic.png" and not failed["once"]:
            failed["once"] = True
            raise OSError("file is locked by another program")
        real(p)

    monkeypatch.setattr(render, "_unlink", stub)
    with pytest.raises(OSError, match="locked"):
        render.write_layers(r2, out, "demo")
    assert order.index("demo.layers.json") < order.index("demo-pic.png")  # the manifest goes first
    assert not list(out.glob("demo*"))  # the cleanup removed the manifest and every layer file
    monkeypatch.setattr(render, "_unlink", real)
    render.write_layers(r2, out, "demo")
    assert [e["id"] for e in manifest(out)["layers"]] == ["bg", "tint", "inv", "masked"]
    assert not (out / "demo-pic.png").exists()


def test_a_failed_cleanup_is_reported_in_the_error(work, gl, monkeypatch):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "stuck"
    doc = poster_doc(photo)
    render.write_layers(render.render_document(copy.deepcopy(doc), work), out, "demo")
    doc["layers"][1]["visible"] = False
    r2 = render.render_document(copy.deepcopy(doc), work)
    real = render._unlink

    def stuck(p):
        if p.name == "demo-pic.png":
            raise OSError("file is locked by another program")
        real(p)

    monkeypatch.setattr(render, "_unlink", stuck)
    with pytest.raises(OSError) as info:
        render.write_layers(r2, out, "demo")
    assert "could not be removed: demo-pic.png" in str(info.value)
    assert isinstance(info.value.__cause__, OSError)


def test_cli_refuses_a_collision_before_writing_the_composed_png(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "clash"
    render.write_outputs(render.render_document(other_poster(), work), out, "demo-bg", ["png"])
    pj = write_poster(work, poster_doc(photo))
    msg = run_cli_failing(pj, "--out", out, "--name", "demo", "--layers")
    assert "not a layer file of this poster" in msg
    for leftover in ("demo.png", "demo.poster.json", "demo.text_audit.json", "demo.layers.json"):
        assert not (out / leftover).exists(), leftover


def test_cli_refuses_a_too_long_path_before_creating_the_folder(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    pj = write_poster(work, poster_doc(photo))
    out = work / ("d" * 200)
    msg = run_cli_failing(pj, "--out", out, "--name", "demo", "--layers")
    assert "characters" in msg
    assert not out.exists()


def test_a_deeply_nested_or_oversized_manifest_does_not_break_a_plain_render(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "bad"
    out.mkdir(parents=True)
    (out / "deep.layers.json").write_text("[" * 200000, encoding="utf-8")  # RecursionError inside json
    (out / "big.layers.json").write_text("[" * 1_100_000, encoding="utf-8")  # over the 1 MB cap
    r = render.render_document(poster_doc(photo), work)
    paths = render.write_outputs(r, out, "demo", ["png"])
    assert paths["png"].exists()
    render.write_layers(r, out, "demo")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file names ignore case")
def test_a_name_that_differs_only_in_case_is_the_same_layer_file_on_windows(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "case"
    render.write_layers(render.render_document(poster_doc(photo), work), out, "demo")
    doc = poster_doc(photo)
    doc["layers"][0]["id"] = "BG"
    render.write_layers(render.render_document(doc, work), out, "demo")
    assert manifest(out)["layers"][0]["id"] == "BG"
    assert len([n for n in os.listdir(out) if n.casefold() == "demo-bg.png"]) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file names ignore case")
def test_a_case_only_clash_with_another_posters_file_says_so(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "clash2"
    render.write_outputs(render.render_document(other_poster(), work), out, "DEMO-BG", ["png"])
    with pytest.raises(ValueError, match="differs only in case"):
        render.write_layers(render.render_document(poster_doc(photo), work), out, "demo")
    assert not (out / "demo.layers.json").exists()

def test_cli_refuses_a_folder_named_like_the_manifest_before_writing_anything(work, gl):  # noqa: F811
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "dirman"
    (out / "demo.layers.json").mkdir(parents=True)
    before = sorted(os.listdir(out))
    pj = write_poster(work, poster_doc(photo))
    msg = run_cli_failing(pj, "--out", out, "--name", "demo", "--layers")
    assert "is a folder, not a manifest" in msg
    assert sorted(os.listdir(out)) == before  # no file created, none removed
    assert (out / "demo.layers.json").is_dir()
    for leftover in ("demo.png", "demo.poster.json", "demo.text_audit.json"):
        assert not (out / leftover).exists(), leftover


def test_when_the_manifest_cannot_be_removed_this_exports_part_files_are_still_removed(  # noqa: F811
    work, gl, monkeypatch
):
    photo = assets.save_noise(work / "photo.png", 128, 96, 1, alpha=True)
    out = work / "partkeep"
    render.write_layers(render.render_document(poster_doc(photo), work), out, "demo")  # a complete export
    leftover = out / "demo-bg.png.part"  # a partial file from an interrupted export of this name
    leftover.write_bytes(b"partial")
    real = render._unlink

    def locked_manifest(p):
        if p.name == "demo.layers.json":
            raise OSError("file is locked by another program")
        real(p)

    monkeypatch.setattr(render, "_unlink", locked_manifest)
    with pytest.raises(OSError) as info:
        render.write_layers(render.render_document(poster_doc(photo), work), out, "demo")
    assert "could not be removed: demo.layers.json" in str(info.value)
    assert isinstance(info.value.__cause__, OSError)
    assert not leftover.exists()  # the .part files go even though the manifest stayed
    assert (out / "demo.layers.json").exists()  # the manifest stayed, so the layer files stayed too
    assert (out / "demo-bg.png").exists() and (out / "demo-pic.png").exists()
