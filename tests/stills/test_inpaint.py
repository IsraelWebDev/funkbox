"""Masked inpainting (LaMa): fills only the masked pixels, keeps the rest, honours the mask contract."""

import numpy as np
import pytest
from PIL import Image

from funkbox.stills import edit, inpaint
from funkbox.stills.raster import RCtx
from stills_fixtures import work  # noqa: F401

W, H = 240, 160
FILL = 200


class FakeLama:
    """Stands in for the ONNX session: returns a flat fill and records what it was given."""

    def __init__(self, value=FILL):
        self.value = value
        self.feeds = []

    def run(self, _names, feed):
        self.feeds.append(feed)
        return [np.full((1, 3, inpaint.NATIVE, inpaint.NATIVE), self.value, np.float32)]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """A fresh stub session and its own cache folder, so no test sees another test's cached fill."""
    f = FakeLama()
    monkeypatch.setattr(inpaint, "_SESSION", f)
    monkeypatch.setattr(inpaint, "funk_out", lambda: tmp_path)
    return f


@pytest.fixture
def scene(work):
    """A flat grey picture and a white rectangle mask at x 100..140, y 60..90."""
    img = work / "scene.png"
    Image.new("RGB", (W, H), (90, 90, 90)).save(img)
    mask = np.zeros((H, W), np.uint8)
    mask[60:90, 100:140] = 255
    mpath = work / "mask.png"
    Image.fromarray(mask).save(mpath)
    return img, mpath, mask > 0


def test_masked_pixels_filled_rest_untouched(fake, scene):
    img, _, m = scene
    out = np.asarray(inpaint.inpaint_image(Image.open(img), Image.fromarray(m.astype(np.uint8) * 255), grow=0))
    assert np.abs(out[m].astype(int) - FILL).max() <= 1
    assert (out[~m] == 90).all()


def test_feed_matches_the_lama_contract(fake, scene):
    img, _, m = scene
    inpaint.inpaint_image(Image.open(img), Image.fromarray(m.astype(np.uint8) * 255), grow=0)
    feed = fake.feeds[0]
    assert feed["image"].shape == (1, 3, 512, 512) and feed["image"].dtype == np.float32
    assert 0.0 <= feed["image"].min() and feed["image"].max() <= 1.0  # LaMa takes 0..1
    assert feed["mask"].shape == (1, 1, 512, 512)
    assert set(np.unique(feed["mask"])) <= {0.0, 1.0} and feed["mask"].sum() > 0  # 1 = hole


def test_grow_widens_the_fill(fake, scene):
    _, _, m = scene
    assert inpaint.grow_mask(m, 0).sum() == m.sum()
    assert inpaint.grow_mask(m, 3).sum() > m.sum()


def test_crop_box_is_square_inside_picture_and_holds_mask(scene):
    _, _, m = scene
    x0, y0, x1, y1 = inpaint.crop_box(m)
    assert x1 - x0 == y1 - y0
    assert 0 <= x0 and 0 <= y0 and x1 <= W and y1 <= H
    ys, xs = np.nonzero(m)
    assert x0 <= xs.min() and xs.max() < x1 and y0 <= ys.min() and ys.max() < y1
    edge = np.zeros((H, W), bool)
    edge[0:10, 0:10] = True  # mask in the top-left corner
    assert inpaint.crop_box(edge)[:2] == (0, 0)


def test_mask_larger_than_short_side_is_refused(fake):
    big = np.zeros((H, W), bool)
    big[10:30, 0:200] = True  # 200 px wide, picture short side is 160
    with pytest.raises(inpaint.MaskTooLarge):
        inpaint.fill_rgb(np.zeros((H, W, 3), np.uint8), big)


def test_second_call_is_served_from_cache(fake, scene):
    img, _, m = scene
    im = Image.open(img)
    mk = Image.fromarray(m.astype(np.uint8) * 255)
    a = inpaint.inpaint_image(im, mk, grow=2)
    b = inpaint.inpaint_image(im, mk, grow=2)
    assert len(fake.feeds) == 1
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_missing_model_gives_a_clear_error(monkeypatch, tmp_path, scene):
    monkeypatch.setattr(inpaint, "_SESSION", None)
    monkeypatch.setattr(inpaint, "funk_out", lambda: tmp_path)
    monkeypatch.setattr(inpaint, "model_path", lambda: tmp_path / "nope.onnx")
    img, _, m = scene
    with pytest.raises(inpaint.InpaintUnavailable, match="setup-models"):
        inpaint.inpaint_image(Image.open(img), Image.fromarray(m.astype(np.uint8) * 255), grow=0)


def test_op_runs_in_the_pipeline_and_reports_info(fake, scene):
    img, mpath, m = scene
    ctx = RCtx(w=W, h=H)
    out = edit.apply_pre(Image.open(img), [{"op": "inpaint", "mask": str(mpath)}], ctx)
    assert out.mode == "RGBA"
    grown = inpaint.grow_mask(m, 4)  # the default grow widens the mask
    assert (np.asarray(out.convert("RGB"))[~grown] == 90).all()
    assert ctx.info["inpaint"]["grow"] == 4  # default
    assert ctx.info["inpaint"]["mask"] == "mask.png"


def test_op_alpha_is_kept(fake, scene):
    _, mpath, m = scene
    rgba = Image.new("RGBA", (W, H), (90, 90, 90, 128))
    out = edit.apply_pre(rgba, [{"op": "inpaint", "mask": str(mpath), "grow": 0}], RCtx(w=W, h=H))
    assert set(np.unique(np.asarray(out.getchannel("A")))) == {128}


@pytest.mark.parametrize("bad", [99, -1, True, "4"])
def test_bad_grow_is_rejected(fake, scene, bad):
    _, mpath, _ = scene
    with pytest.raises(edit.EditError, match="grow"):
        edit.apply_pre(Image.new("RGB", (W, H)), [{"op": "inpaint", "mask": str(mpath), "grow": bad}], RCtx(w=W, h=H))


def test_missing_mask_path_is_rejected(fake):
    with pytest.raises(edit.EditError, match="mask"):
        edit.apply_pre(Image.new("RGB", (W, H)), [{"op": "inpaint", "mask": "no-such.png"}], RCtx(w=W, h=H))


def test_inpaint_must_come_before_layers():
    with pytest.raises(edit.EditError, match="put them first"):
        edit.normalize([{"op": "border", "px": 4}, {"op": "inpaint", "mask": "m.png"}])


@pytest.mark.skipif(not inpaint.available(), reason="LaMa model not installed (funkbox reel setup-models)")
def test_real_model_fills_the_hole(work):
    """Runs the real ONNX model. Skipped in CI; run locally after setup-models."""
    img = Image.new("RGB", (W, H), (120, 160, 200))
    mask = np.zeros((H, W), np.uint8)
    mask[50:110, 90:150] = 255
    out = np.asarray(inpaint.inpaint_image(img, Image.fromarray(mask), grow=0))
    assert not np.array_equal(out[mask > 0], np.full((int((mask > 0).sum()), 3), [120, 160, 200]))
