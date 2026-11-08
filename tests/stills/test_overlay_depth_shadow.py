"""Overlay ``depth`` (scale by how near the spot is) and ``shadow`` (a flattened shadow cast away from the light)."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from funkbox.config import funk_out
from funkbox.reel import depth as reel_depth
from funkbox.stills import edit
from stills_fixtures import work  # noqa: F401

W, H = 240, 160


@pytest.fixture
def base(work):
    p = work / "base.png"
    Image.new("RGB", (W, H), (90, 90, 90)).save(p)
    return p


@pytest.fixture
def cutout(work):
    # a 40 px cut-out whose opaque square sits at 10..30, so its silhouette is 20 px wide
    im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (10, 10, 30, 30))
    p = work / "cut.png"
    im.save(p)
    return p


def layers(res):
    return json.loads(res.paths["sidecar"].read_text())["layers"]


def shadow_of(res):
    return next(lay for lay in layers(res) if lay["id"].startswith("shadow"))


def silhouette(path):
    a = np.asarray(Image.open(path).convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(a)
    return int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1


def test_plain_overlay_has_no_shadow_layer(base, cutout, work):
    res = edit.edit_image(base, [{"op": "overlay", "path": str(cutout), "scale": 0.25}], out_dir=work / "plain")
    assert [lay["id"] for lay in layers(res)] == ["src", "overlay1"]


def test_shadow_falls_straight_down_under_a_light_from_above(base, cutout, work):
    shadow = {"opacity": 1, "light": 0, "distance": 0.1, "blur": 0, "squash": 0.3}
    res = edit.edit_image(
        base, [{"op": "overlay", "path": str(cutout), "scale": 0.25, "shadow": shadow}], out_dir=work / "up"
    )
    assert Image.open(res.paths["png"]).size == (W, H)
    assert [lay["id"] for lay in layers(res)] == ["src", "shadow2", "overlay1"]
    # the overlay's silhouette is x 110..130, y 70..90; the shadow lands 16 px lower, squashed to 6 px
    assert silhouette(shadow_of(res)["path"]) == (110, 130, 100, 106)


def test_light_from_the_right_throws_the_shadow_left(base, cutout, work):
    shadow = {"opacity": 1, "light": 90, "distance": 0.1, "blur": 0, "squash": 0.3}
    res = edit.edit_image(
        base, [{"op": "overlay", "path": str(cutout), "scale": 0.25, "shadow": shadow}], out_dir=work / "right"
    )
    assert silhouette(shadow_of(res)["path"]) == (94, 114, 84, 90)


def test_shadow_number_is_its_opacity(base, cutout, work):
    # a bare number is the opacity; every other setting takes its default, so both spellings give the same file
    def shadow_file(shadow, name):
        ops = [{"op": "overlay", "path": str(cutout), "scale": 0.25, "shadow": shadow}]
        return shadow_of(edit.edit_image(base, ops, out_dir=work / name))["path"]

    spelled = {"opacity": 0.5, "light": 45, "distance": 0.02, "blur": 0.01, "squash": 0.25}
    assert shadow_file(0.5, "num") == shadow_file(spelled, "dict")


def test_shadow_opacity_is_applied_to_the_shadow_pixels(base, cutout, work):
    res = edit.edit_image(
        base,
        [{"op": "overlay", "path": str(cutout), "scale": 0.25, "shadow": {"opacity": 0.5, "blur": 0}}],
        out_dir=work / "half",
    )
    alpha = np.asarray(Image.open(shadow_of(res)["path"]).convert("RGBA"))[..., 3]
    assert alpha.max() == 128  # round(255 * 0.5)


def test_shadow_fades_with_the_overlay_it_belongs_to(base, cutout, work):
    ops = [{"op": "overlay", "path": str(cutout), "scale": 0.25, "opacity": 0.5, "shadow": {"opacity": 1, "blur": 0}}]
    res = edit.edit_image(base, ops, out_dir=work / "fade")
    alpha = np.asarray(Image.open(shadow_of(res)["path"]).convert("RGBA"))[..., 3]
    assert alpha.max() == 128  # shadow 1.0 * overlay 0.5


def test_shadow_follows_a_geometry_op_before_the_overlay(base, cutout, work):
    ops = [{"op": "rotate", "deg": 90}, {"op": "overlay", "path": str(cutout), "scale": 0.25, "shadow": True}]
    res = edit.edit_image(base, ops, out_dir=work / "rot")
    assert Image.open(res.paths["png"]).size == (H, W)  # rotate 90 turns the 240x160 picture
    assert Image.open(shadow_of(res)["path"]).size == (H, W)


def test_depth_scales_the_overlay_by_how_near_its_spot_is(base, cutout, work, monkeypatch):
    def left_half_near(path):
        w, h = Image.open(path).size
        near = np.arange(w)[None, :] < w // 2
        return np.broadcast_to(near.astype(np.float32), (h, w)).copy()

    monkeypatch.setattr(reel_depth, "estimate_depth", left_half_near)

    def overlay_scale(x):
        ops = [{"op": "overlay", "path": str(cutout), "scale": 0.5, "x": x, "depth": 0.5}]
        res = edit.edit_image(base, ops, out_dir=work / f"depth-{x}")
        return next(lay["scale"] for lay in layers(res) if lay["id"] == "overlay1")

    assert overlay_scale(0.25) == pytest.approx(0.5 * 1.5)  # near: 1 + 0.5 * (2 * 1 - 1)
    assert overlay_scale(0.75) == pytest.approx(0.5 * 0.5)  # far:  1 + 0.5 * (2 * 0 - 1)


def test_depth_cache_tells_apart_pictures_with_the_same_bytes(work, cutout, monkeypatch):
    seen = []

    def record(path):
        seen.append(Path(path))
        w, h = Image.open(path).size
        return np.zeros((h, w), np.float32)

    monkeypatch.setattr(reel_depth, "estimate_depth", record)
    # a flat 240x160 and a flat 160x240 picture hold identical bytes; their depth maps must not be shared
    wide, tall = work / "wide.png", work / "tall.png"
    Image.new("RGB", (240, 160), (90, 90, 90)).save(wide)
    Image.new("RGB", (160, 240), (90, 90, 90)).save(tall)
    ops = [{"op": "overlay", "path": str(cutout), "depth": 0.5}]
    edit.edit_image(wide, ops, out_dir=work / "cw")
    edit.edit_image(tall, ops, out_dir=work / "ct")
    assert seen[0] != seen[1]


def test_depth_without_the_model_says_so(base, cutout, work, monkeypatch):
    def gone(path):
        raise reel_depth.DepthUnavailable("no model file")

    monkeypatch.setattr(reel_depth, "estimate_depth", gone)
    with pytest.raises(edit.EditError, match="depth model"):
        edit.edit_image(base, [{"op": "overlay", "path": str(cutout), "depth": 0.5}], out_dir=work / "gone")


@pytest.mark.parametrize(
    "bad",
    [
        {"depth": 2},
        {"depth": True},
        {"shadow": {"colour": 1}},
        {"shadow": {"opacity": 1.5}},
        {"shadow": "dark"},
        {"x": "left"},
        {"y": float("nan")},
        {"scale": float("inf")},
        {"scale": 0},
        {"opacity": 2},
    ],
)
def test_bad_overlay_values_are_refused_with_an_edit_error(base, cutout, work, bad):
    with pytest.raises(edit.EditError):
        edit.edit_image(base, [{"op": "overlay", "path": str(cutout), **bad}], out_dir=work / "bad")


def test_shadow_on_a_wide_canvas_with_the_overlay_hanging_off_the_edge(work, cutout):
    wide = work / "wide2.png"
    Image.new("RGB", (300, 100), (90, 90, 90)).save(wide)
    ops = [{"op": "overlay", "path": str(cutout), "scale": 0.5, "x": 0.9, "y": 1.1, "shadow": True}]
    res = edit.edit_image(wide, ops, out_dir=work / "wide")
    assert Image.open(res.paths["png"]).size == (300, 100)
    assert Image.open(shadow_of(res)["path"]).size == (300, 100)


def test_no_partial_files_are_left_behind(base, cutout, work):
    edit.edit_image(base, [{"op": "overlay", "path": str(cutout), "scale": 0.25, "shadow": True}], out_dir=work / "tmp")
    assert not list((funk_out() / "cache").rglob("*.part"))
