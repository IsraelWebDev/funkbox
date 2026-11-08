"""Overlay ``depth`` (scale by how near the spot is) and ``shadow`` (a flattened shadow cast away from the light)."""

import json

import numpy as np
import pytest
from PIL import Image

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
    sh = next(lay for lay in layers(res) if lay["id"].startswith("shadow"))
    # the overlay's silhouette is x 110..130, y 70..90; the shadow lands 16 px lower, squashed to 6 px
    assert silhouette(sh["path"]) == (110, 130, 100, 106)


def test_light_from_the_right_throws_the_shadow_left(base, cutout, work):
    shadow = {"opacity": 1, "light": 90, "distance": 0.1, "blur": 0, "squash": 0.3}
    res = edit.edit_image(
        base, [{"op": "overlay", "path": str(cutout), "scale": 0.25, "shadow": shadow}], out_dir=work / "right"
    )
    sh = next(lay for lay in layers(res) if lay["id"].startswith("shadow"))
    assert silhouette(sh["path"]) == (94, 114, 84, 90)


def test_shadow_number_is_its_opacity(base, cutout, work):
    # a bare number is the opacity; every other setting takes its default, so both spellings give the same file
    def shadow_file(shadow, name):
        ops = [{"op": "overlay", "path": str(cutout), "scale": 0.25, "shadow": shadow}]
        res = edit.edit_image(base, ops, out_dir=work / name)
        return next(lay for lay in layers(res) if lay["id"].startswith("shadow"))["path"]

    spelled = {"opacity": 0.5, "light": 45, "distance": 0.02, "blur": 0.01, "squash": 0.25}
    assert shadow_file(0.5, "num") == shadow_file(spelled, "dict")


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


def test_depth_without_the_model_says_so(base, cutout, work, monkeypatch):
    def gone(path):
        raise reel_depth.DepthUnavailable("no model file")

    monkeypatch.setattr(reel_depth, "estimate_depth", gone)
    with pytest.raises(edit.EditError, match="depth model"):
        edit.edit_image(base, [{"op": "overlay", "path": str(cutout), "depth": 0.5}], out_dir=work / "gone")


@pytest.mark.parametrize(
    "bad",
    [{"depth": 2}, {"depth": True}, {"shadow": {"colour": 1}}, {"shadow": {"opacity": 1.5}}, {"shadow": "dark"}],
)
def test_bad_depth_or_shadow_values_are_refused(base, cutout, work, bad):
    with pytest.raises(edit.EditError):
        edit.edit_image(base, [{"op": "overlay", "path": str(cutout), **bad}], out_dir=work / "bad")
