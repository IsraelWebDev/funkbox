"""v3.2 S4: OpenRaster export, variations and non-destructive edits."""
import copy
import hashlib
import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import assets
from stills import edit, ora, render, schema, variations


def doc(layers, w=128, h=96, bg="#202020", **kw):
    return {"version": 1, "canvas": {"width": w, "height": h, "background": bg}, "layers": layers, **kw}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def imgs(work):
    return (assets.save_noise(work / "a.png", 128, 96, 1), assets.save_noise(work / "b.png", 128, 96, 2, alpha=True),
            assets.save_noise(work / "c.png", 128, 96, 3))


# ------------------------------------------------------------------ OpenRaster
def ora_doc(imgs, post=None):
    a, b, c = imgs
    return doc([{"id": "photo", "type": "image", "path": str(a), "adjust": {"saturation": -0.3}},
                {"id": "mul", "type": "image", "path": str(b), "blend": "multiply", "opacity": 0.8},
                {"id": "scr", "type": "image", "path": str(c), "blend": "screen", "opacity": 0.5,
                 "mask": {"type": "radial", "feather": 0.4}},
                {"id": "add", "type": "solid", "color": "#203010", "blend": "add"},
                {"id": "dif", "type": "image", "path": str(b), "blend": "difference", "opacity": 0.6, "filters": {"blur": 1.5}},
                {"id": "grp", "type": "group", "opacity": 0.9, "layers": [{"id": "s1", "type": "solid", "color": "#ff00aa80"},
                                                                          {"id": "s2", "type": "gradient", "stops": ["#00000000", "#ffffffff"]}]},
                {"id": "txt", "type": "text", "text": "ORA", "x": 0.1, "y": 0.2, "size": 0.2}],
               casual=True, **({"post": post} if post else {}))


def test_ora_container_is_valid(imgs, work):
    p = render.render_poster({**ora_doc(imgs), "export": {"formats": ["png", "ora"]}}, work / "o", "ora1")["ora"]
    info = ora.validate_ora(p)
    with zipfile.ZipFile(p) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"image/openraster"
        names = set(z.namelist())
    assert (info["w"], info["h"]) == (128, 96)
    # stack.xml lists the TOP layer first; background is last
    assert [l["name"] for l in info["layers"]] == ["txt", "grp", "dif", "add", "scr", "mul", "photo", "background"]
    assert {l["src"] for l in info["layers"]} <= names
    assert "mergedimage.png" in names and "Thumbnails/thumbnail.png" in names
    ops = {l["name"]: l["op"] for l in info["layers"]}
    assert ops["mul"] == "svg:multiply" and ops["scr"] == "svg:screen" and ops["add"] == "svg:plus"
    assert ops["dif"] == "svg:difference" and ops["photo"] == "svg:src-over"
    assert {l["name"]: l["opacity"] for l in info["layers"]}["mul"] == pytest.approx(0.8)
    th = Image.open(zipfile.ZipFile(p).open("Thumbnails/thumbnail.png"))
    assert max(th.size) <= 256


def test_ora_op_names_cover_all_funkgl_blend_modes():
    from funkgl.recipe import BLEND_MODES
    assert ora.OP == {"normal": "svg:src-over", "add": "svg:plus", "screen": "svg:screen", "multiply": "svg:multiply",
                      "overlay": "svg:overlay", "difference": "svg:difference", "softlight": "svg:soft-light",
                      "hue": "svg:hue"}
    assert set(ora.OP) == set(BLEND_MODES)


def test_ora_recomposite_matches_the_merged_image(imgs, work):
    p = render.render_poster({**ora_doc(imgs), "export": {"formats": ["ora"]}}, work / "o", "ora2")["ora"]
    with zipfile.ZipFile(p) as z:
        merged = np.asarray(Image.open(z.open("mergedimage.png")).convert("RGBA"), np.int32)
    comp = ora.recomposite(p).astype(np.int32)
    diff = np.abs(comp - merged)
    assert (diff.max(axis=-1) <= 3).mean() >= 0.99 and diff.max() <= 6
    # ...and the layers are really pre-rendered: layer 'photo' has the desaturation baked in, no blend
    with zipfile.ZipFile(p) as z:
        photo = np.asarray(Image.open(z.open("data/photo.png")).convert("RGBA"))
    src = np.asarray(Image.open(imgs[0]).convert("RGB"), np.float32)
    lum = src @ np.array([0.299, 0.587, 0.114], np.float32)
    want = lum[..., None] + (src - lum[..., None]) * 0.7
    assert np.abs(photo[..., :3].astype(np.float32) - np.clip(want, 0, 255)).max() <= 3 and (photo[..., 3] == 255).all()


def test_ora_merged_image_is_the_stack_even_when_the_poster_has_post_passes(imgs, work):
    d = {**ora_doc(imgs, post=[{"name": "grain", "amount": 0.05}]), "export": {"formats": ["png", "ora"]}}
    p = render.render_poster(d, work / "o", "ora3")
    comp = ora.recomposite(p["ora"]).astype(np.int32)
    with zipfile.ZipFile(p["ora"]) as z:
        merged = np.asarray(Image.open(z.open("mergedimage.png")).convert("RGBA"), np.int32)
    assert (np.abs(comp - merged).max(axis=-1) <= 3).mean() >= 0.99
    png = np.asarray(Image.open(p["png"]).convert("RGB"), np.int32)
    assert np.abs(png - merged[..., :3]).mean() > 1.0            # grain is in the PNG, not in the .ora


def test_validate_ora_rejects_broken_archives(imgs, work):
    p = render.render_poster({**ora_doc(imgs), "export": {"formats": ["ora"]}}, work / "o", "ora4")["ora"]
    bad = work / "bad.ora"
    with zipfile.ZipFile(p) as zin, zipfile.ZipFile(bad, "w") as zout:           # mimetype compressed and not first
        for n in reversed(zin.namelist()):
            zout.writestr(n, zin.read(n), compress_type=zipfile.ZIP_DEFLATED)
    with pytest.raises(ora.OraError, match="first entry"):
        ora.validate_ora(bad)
    miss = work / "miss.ora"
    with zipfile.ZipFile(p) as zin, zipfile.ZipFile(miss, "w") as zout:
        zout.writestr(zipfile.ZipInfo("mimetype"), b"image/openraster", compress_type=zipfile.ZIP_STORED)
        for n in zin.namelist():
            if n != "mimetype" and n != "data/photo.png":
                zout.writestr(n, zin.read(n))
    with pytest.raises(ora.OraError, match="missing"):
        ora.validate_ora(miss)


def test_transparent_canvas_exports_rgba_png_and_ora_without_a_background_layer(work, imgs):
    d = doc([{"id": "sq", "type": "solid", "color": "#ff0000", "mask": {"type": "radial", "feather": 0.2}}], bg="transparent",
            export={"formats": ["png", "jpg", "ora"]})
    p = render.render_poster(d, work / "o", "tr")
    im = Image.open(p["png"])
    assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0 and im.getpixel((64, 48))[3] == 255
    assert Image.open(p["jpg"]).mode == "RGB"
    assert "background" not in [l["name"] for l in ora.validate_ora(p["ora"])["layers"]]


# ------------------------------------------------------------------ variations
def var_doc(imgs):
    a, b, c = imgs
    return doc([{"id": "bg", "type": "shader", "shader": "shaders/fbm_flow.frag", "t": 0.3, "palette": "vapor"},
                {"id": "photo", "type": "image", "path": str(b), "opacity": 0.5, "adjust": {"hue": 0}}], w=160, h=128, seed=3)


def test_variations_by_seed_make_files_and_a_sheet(imgs, work, gl):
    res = variations.poster_variations(var_doc(imgs), {"seed": [1, 2, 3]}, work / "v", "sd")
    assert len(res.files) == 3 and res.sheet.is_file()
    px = [np.asarray(Image.open(f["png"])) for f in res.files]
    assert all(p.shape == (128, 160, 3) for p in px)
    assert not np.array_equal(px[0], px[1]) and not np.array_equal(px[1], px[2])
    assert res.labels[0] == "1: seed 1"
    sheet = Image.open(res.sheet)
    assert sheet.width > 160 * 2 and sheet.height > 128
    # every variant has its own reproducible sidecar
    for f, seed in zip(res.files, (1, 2, 3)):
        side = json.loads(f["sidecar"].read_text())
        assert side["seed"] == seed and side["layers"][0]["seed"] == seed


def test_variations_by_layer_property(imgs, work, gl):
    res = variations.poster_variations(var_doc(imgs), {"layers.bg.palette": ["vapor", "sunset"], "layers.photo.adjust.hue": [0, 120]},
                                       work / "v", "pal")
    assert res.labels == ["1: palette=vapor, hue=0", "2: palette=sunset, hue=120"]
    a, b = (np.asarray(Image.open(f["png"])) for f in res.files)
    assert not np.array_equal(a, b)
    assert res.docs[1]["layers"][1]["adjust"]["hue"] == 120 and res.docs[0]["layers"][1]["adjust"]["hue"] == 0


def test_variation_errors_are_clear(imgs, work):
    d = var_doc(imgs)
    for vary, frag in (({}, "vary must be"), ({"seed": []}, "non-empty"), ({"seed": [1, 2], "layers.bg.t": [0.1]}, "same length"),
                       ({"layers.nope.t": [0.1]}, "no layer with id 'nope'"), ({"bogus": [1]}, "use 'seed'"),
                       ({"seed": list(range(13))}, "at most 12")):
        with pytest.raises(variations.VaryError, match=frag):
            variations.poster_variations(d, vary, work / "v", "x")
    with pytest.raises(schema.PosterError, match=r"variant 2 .*opacity must be 0..1"):
        variations.poster_variations(d, {"layers.photo.opacity": [0.5, 7]}, work / "v", "x")
    assert d["seed"] == 3 and d["layers"][1]["opacity"] == 0.5               # the input doc is never modified


# ------------------------------------------------------------------ edits
@pytest.fixture
def photo(work):
    return assets.make_kitchen(work / "photo.png", 640, 800)


def test_edit_never_touches_the_source_and_writes_a_new_file(photo, work):
    before = (sha(photo), os.stat(photo).st_mtime_ns)
    res = edit.edit_image(photo, [{"op": "crop", "aspect": "1:1"}, {"op": "adjust", "contrast": 0.2},
                                  {"op": "border", "px": 10, "color": "#ffffff"}], out_dir=work / "edits")
    assert (sha(photo), os.stat(photo).st_mtime_ns) == before
    out = res.paths["png"]
    assert out.name == "photo-1.png" and out != photo and Image.open(out).size == (640, 640)
    assert res.paths["sidecar"].name == "photo-1.poster.json" and res.compare.name == "photo-1.compare.png"
    again = edit.edit_image(photo, [{"op": "flip", "axis": "h"}], out_dir=work / "edits")
    assert again.paths["png"].name == "photo-2.png" and out.is_file()            # a second edit never replaces the first
    named = edit.edit_image(photo, [{"op": "flip", "axis": "v"}], name="upside", out_dir=work / "edits")
    assert named.paths["png"].name == "photo-upside.png"
    assert sha(photo) == before[0]
    cmp = Image.open(res.compare)
    assert cmp.width > 640 and cmp.height > 300


def test_edit_sidecar_rerenders_to_the_same_pixels(photo, work):
    ops = [{"op": "crop", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}, {"op": "resize", "width": 400},
           {"op": "adjust", "saturation": -0.4, "brightness": 0.05}, {"op": "filter", "blur": 1.2},
           {"op": "adjust", "contrast": 0.3},                                   # forces a nested group
           {"op": "overlay", "path": str(photo), "scale": 0.3, "x": 0.8, "y": 0.2, "blend": "screen", "opacity": 0.6},
           {"op": "text", "text": "Edited", "x": 0.1, "y": 0.1, "size": 0.08},
           {"op": "vignette", "amount": 0.8}, {"op": "border", "px": 6, "color": "#202020"}]
    res = edit.edit_image(photo, ops, casual=True, out_dir=work / "edits")
    a = np.asarray(Image.open(res.paths["png"]))
    side = json.loads(res.paths["sidecar"].read_text())
    assert side["casual"] is True and side["canvas"]["width"] == 400
    r2 = render.render_poster(side, work / "again", "again", base_dir=res.paths["sidecar"].parent)
    b = np.asarray(Image.open(r2["png"]))
    assert np.array_equal(a, b)
    # ...also through a file on disk, from another working folder
    r3 = render.render_poster(json.loads(res.paths["sidecar"].read_text()), work / "again", "again2")
    assert np.array_equal(np.asarray(Image.open(r3["png"])), a)


def test_edit_geometry_ops(photo, work):
    def run(ops, **kw):
        return edit.edit_image(photo, ops, out_dir=work / "e", **kw)
    assert Image.open(run([{"op": "crop", "aspect": "4:5"}], name="c1").paths["png"]).size == (640, 800)
    assert Image.open(run([{"op": "crop", "aspect": "16:9", "anchor": "top"}], name="c2").paths["png"]).size == (640, 360)
    assert Image.open(run([{"crop": {"x": 0, "y": 0.5, "w": 0.5, "h": 0.5}}], name="c3").paths["png"]).size == (320, 400)
    assert Image.open(run([{"op": "resize", "height": 400}], name="r1").paths["png"]).size == (320, 400)
    assert Image.open(run([{"op": "resize", "width": 200, "height": 200}], name="r2").paths["png"]).size == (160, 200)
    assert Image.open(run([{"op": "rotate", "deg": 90}], name="ro").paths["png"]).size == (800, 640)
    a = np.asarray(Image.open(photo).convert("RGB"))
    f = np.asarray(Image.open(run([{"op": "flip", "axis": "h"}], name="fl").paths["png"]).convert("RGB"))
    assert np.array_equal(f, a[:, ::-1])                                          # exact: no resampling
    same = np.asarray(Image.open(run([{"op": "adjust", "brightness": 0.0}], name="id").paths["png"]).convert("RGB"))
    assert np.array_equal(same, a)                                                # no-op edit is byte-exact
    odd = assets.save_noise(work / "odd.png", 201, 133, 5)
    assert Image.open(edit.edit_image(odd, [{"op": "flip", "axis": "h"}], out_dir=work / "e").paths["png"]).size == (200, 132)


def test_edit_order_rules_and_errors(photo, work):
    with pytest.raises(edit.EditError, match="put them first"):
        edit.edit_image(photo, [{"op": "adjust", "contrast": 0.1}, {"op": "crop", "aspect": "1:1"}], out_dir=work / "e")
    for ops, frag in (([], "non-empty"), ([{"op": "dance"}], "must be"), ([{"op": "crop", "wobble": 1}], "unknown keys"),
                      ([{"op": "crop", "aspect": "banana"}], "W:H"), ([{"op": "crop", "x": 0.5, "y": 0, "w": 0.9, "h": 1}], "inside"),
                      ([{"op": "resize"}], "width and/or height"), ([{"op": "flip", "axis": "z"}], "h or v"),
                      ([{"op": "resize", "width": 10}], "minimum")):
        with pytest.raises(edit.EditError, match=frag):
            edit.edit_image(photo, ops, out_dir=work / "e")
    with pytest.raises(schema.PosterError, match="casual"):                       # literal text needs casual
        edit.edit_image(photo, [{"op": "text", "text": "hi"}], out_dir=work / "e")
    with pytest.raises(ValueError, match="outside the allowed"):
        edit.edit_image("/etc/hostname", [{"op": "flip", "axis": "h"}], out_dir=work / "e")
    with pytest.raises(ValueError, match="outside the allowed"):
        edit.edit_image(photo, [{"op": "flip", "axis": "h"}], out_dir="/tmp/elsewhere")
    with pytest.raises(edit.EditError, match="bad edit name"):
        edit.edit_image(photo, [{"op": "flip", "axis": "h"}], name="../x", out_dir=work / "e")


def test_edit_remove_background_reports_the_quality_score(work):
    arr = np.full((300, 300, 3), 245, np.uint8)
    arr[80:220, 100:200] = (30, 90, 160)                                          # a product on a white backdrop
    Image.fromarray(arr).save(work / "prod.png")
    res = edit.edit_image(work / "prod.png", [{"op": "remove_background"}], out_dir=work / "e")
    q = res.rendered.info["remove_background"]
    assert 0 < q["score"] <= 1 and q["method"] == "cutout"
    out = Image.open(res.paths["png"])
    assert out.mode == "RGBA" and out.size[0] < 130 and out.getpixel((0, 0))[3] < 40 \
        and out.getpixel((out.width // 2, out.height // 2))[3] == 255     # transparent PNG, cropped to the subject


def test_edit_text_ops_follow_the_copy_rules(photo, work, monkeypatch):
    deck = work / "copy.json"
    deck.write_text(json.dumps({"brand_name": "X", "lines": {"hook": {"text": "LOOK CLOSER."}}}))
    res = edit.edit_image(photo, [{"op": "text", "copy_id": "hook", "y": 0.1, "size": 0.06}], copy=str(deck), out_dir=work / "e")
    assert res.rendered.audit[0]["text"] == "LOOK CLOSER." and res.rendered.audit[0]["source"] == "copy_id"
    audit = json.loads(res.paths["text_audit"].read_text())
    assert audit[0]["layer"].startswith("text")
