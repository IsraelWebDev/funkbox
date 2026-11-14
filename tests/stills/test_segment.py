"""MobileSAM click-to-mask: one point becomes the mask of the object under it; inpaint can take a point."""

import numpy as np
import pytest
from PIL import Image

from funkbox.stills import edit, inpaint, segment
from funkbox.stills.raster import RCtx
from stills_fixtures import work  # noqa: F401

W, H = 200, 100
SCALE = 1024 / max(W, H)


class FakeEncoder:
    def __init__(self):
        self.feeds = []

    def run(self, _names, feed):
        self.feeds.append(feed)
        return [np.zeros((1, 256, 64, 64), np.float32)]


class FakeDecoder:
    """Answers every click with the box (x0, y0, x1, y1) in picture pixels."""

    def __init__(self, box):
        self.box = box
        self.feeds = []

    def run(self, _names, feed):
        self.feeds.append(feed)
        h, w = (int(v) for v in feed["orig_im_size"])
        m = np.zeros((1, 1, h, w), np.float32)
        x0, y0, x1, y1 = self.box
        m[0, 0, y0:y1, x0:x1] = 1.0
        return [m, np.array([[0.93]], np.float32), np.zeros((1, 1, 256, 256), np.float32)]


class FakeLama:
    def run(self, _names, feed):
        return [np.full((1, 3, inpaint.NATIVE, inpaint.NATIVE), 200, np.float32)]


@pytest.fixture
def sessions(monkeypatch):
    enc, dec = FakeEncoder(), FakeDecoder((20, 10, 60, 40))
    monkeypatch.setattr(segment, "_SESSIONS", (enc, dec))
    return enc, dec


@pytest.fixture
def lama(monkeypatch, tmp_path):
    monkeypatch.setattr(inpaint, "_SESSION", FakeLama())
    monkeypatch.setattr(inpaint, "funk_out", lambda: tmp_path)


def test_click_mask_returns_the_object_under_the_point(sessions):
    m = segment.click_mask(np.zeros((H, W, 3), np.uint8), 50, 20)
    assert m.shape == (H, W)
    assert m.sum() == 30 * 40 and m[10:40, 20:60].all()


def test_encoder_gets_the_picture_padded_to_1024(sessions):
    enc, _ = sessions
    segment.click_mask(np.zeros((H, W, 3), np.uint8), 50, 20)
    x = enc.feeds[0]["input_image"]
    assert x.shape == (1024, 1024, 3) and x.dtype == np.float32
    assert x[round(H * SCALE) :].sum() == 0  # the padding below the picture is empty


def test_click_is_scaled_into_the_1024_frame(sessions):
    _, dec = sessions
    segment.click_mask(np.zeros((H, W, 3), np.uint8), 50, 20)
    feed = dec.feeds[0]
    assert np.allclose(feed["point_coords"][0, 0], [50 * SCALE, 20 * SCALE], atol=1e-3)
    assert feed["point_labels"][0, 0] == 1
    assert list(feed["orig_im_size"]) == [H, W]


def test_missing_models_give_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(segment, "_SESSIONS", None)
    monkeypatch.setattr(segment, "model_paths", lambda: (tmp_path / "a.onnx", tmp_path / "b.onnx"))
    with pytest.raises(segment.SegmentUnavailable, match=r"setup-models|onnxruntime"):
        segment.click_mask(np.zeros((10, 10, 3), np.uint8), 1, 1)


def test_inpaint_point_fills_the_clicked_object(sessions, lama):
    ctx = RCtx(w=W, h=H)
    out = edit.apply_pre(
        Image.new("RGBA", (W, H), (90, 90, 90, 255)), [{"op": "inpaint", "point": [50, 20], "grow": 0}], ctx
    )
    arr = np.asarray(out.convert("RGB"))
    assert (np.abs(arr[10:40, 20:60].astype(int) - 200) <= 1).all()
    assert (arr[:5] == 90).all()
    assert ctx.info["inpaint"]["point"] == [50, 20]


def test_point_outside_the_picture_is_refused(sessions, lama):
    with pytest.raises(edit.EditError, match="outside"):
        edit.apply_pre(Image.new("RGBA", (W, H)), [{"op": "inpaint", "point": [300, 20]}], RCtx(w=W, h=H))


@pytest.mark.parametrize("op", [{"op": "inpaint"}, {"op": "inpaint", "mask": "a.png", "point": [1, 1]}])
def test_inpaint_needs_exactly_one_of_mask_or_point(sessions, lama, op):
    with pytest.raises(edit.EditError, match="exactly one"):
        edit.apply_pre(Image.new("RGBA", (W, H)), [op], RCtx(w=W, h=H))


@pytest.mark.parametrize("bad", [[1], "1,2", [True, 2], [1, 2, 3]])
def test_bad_point_is_rejected(sessions, lama, bad):
    with pytest.raises(edit.EditError, match=r"\[x, y\]"):
        edit.apply_pre(Image.new("RGBA", (W, H)), [{"op": "inpaint", "point": bad}], RCtx(w=W, h=H))


def test_point_is_a_valid_inpaint_key_for_normalize():
    assert edit.normalize([{"op": "inpaint", "point": [1, 2]}])[0]["point"] == [1, 2]


@pytest.mark.skipif(bool(segment.unavailable_reason()), reason="MobileSAM or onnxruntime missing (setup-models)")
def test_real_mobilesam_finds_a_bright_square(work):
    """Runs the real ONNX models. Skipped in CI; run locally after setup-models."""
    rgb = np.zeros((200, 300, 3), np.uint8)
    rgb[50:150, 100:200] = 255
    m = segment.click_mask(rgb, 150, 100)
    box = np.zeros_like(m)
    box[50:150, 100:200] = True
    assert (m & box).sum() / (m | box).sum() > 0.7
