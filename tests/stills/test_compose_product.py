"""Bytes in, bytes out: put a cut-out picture into a photo through the engine's product compositor.

The contract matches the hosted cat compositor (funkbox-online src/app/cat_edit.py) so the hosted side can call the
engine instead of keeping its own copy: ``compose_product(photo, cutout_png, params) -> (png_bytes, sidecar)``, the
same setting names and ranges, and ``ComposeError`` for every refused input.
"""

import hashlib
import io

import numpy as np
import pytest
from PIL import Image

from funkbox.stills.compose import ComposeError, compose_product

W, H = 240, 160


def png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def photo() -> bytes:
    return png(Image.new("RGB", (W, H), (90, 120, 90)))


@pytest.fixture
def red_square() -> bytes:
    # a 40 px opaque red square on a transparent 60 px canvas
    im = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (10, 10, 50, 50))
    return png(im)


def decode(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)


def test_returns_a_png_of_the_photo_size_and_a_sidecar(photo, red_square):
    out, sidecar = compose_product(photo, red_square)
    im = Image.open(io.BytesIO(out))
    assert im.format == "PNG"
    assert im.size == (W, H)
    assert sidecar["size"] == [W, H]
    assert sidecar["output_sha256"] == hashlib.sha256(out).hexdigest()
    assert sidecar["photo_sha256"] == hashlib.sha256(photo).hexdigest()
    assert sidecar["cutout_sha256"] == hashlib.sha256(red_square).hexdigest()
    assert sidecar["params"] == {"cx": 0.5, "cy": 0.9, "height": 0.5, "shadow": 0.6, "match": 0.0}


def test_same_inputs_give_the_same_bytes(photo, red_square):
    a = compose_product(photo, red_square, {"cx": 0.3})
    b = compose_product(photo, red_square, {"cx": 0.3})
    assert a == b


def test_the_cat_stands_with_its_feet_at_cx_cy(photo, red_square):
    out, sidecar = compose_product(photo, red_square, {"cx": 0.5, "cy": 0.9, "height": 0.5, "shadow": 0, "match": 0})
    L, T, R, B = sidecar["cat_box"]
    # feet on the base line, centred on cx, about height * H tall
    assert B == pytest.approx(0.9 * H, abs=2)
    assert (L + R) / 2 == pytest.approx(0.5 * W, abs=2)
    assert (B - T) == pytest.approx(0.5 * H, abs=3)
    centre = decode(out)[(T + B) // 2, (L + R) // 2]
    assert centre[0] > 200 and centre[1] < 60 and centre[2] < 60  # the red cat itself


def test_outside_the_cat_and_with_no_shadow_or_colour_match_nothing_changes(photo, red_square):
    out, sidecar = compose_product(photo, red_square, {"shadow": 0, "match": 0})
    L, T, R, B = sidecar["cat_box"]
    before, after = decode(photo), decode(out)
    mask = np.ones((H, W), bool)
    mask[T:B, L:R] = False
    assert np.array_equal(before[mask], after[mask])


def test_the_shadow_darkens_the_floor_under_the_cat(photo, red_square):
    flat, sidecar = compose_product(photo, red_square, {"shadow": 0, "match": 0})
    shaded, _ = compose_product(photo, red_square, {"shadow": 1.0, "match": 0})
    L, T, R, B = sidecar["cat_box"]
    # on the floor just below the feet, the shaded picture is darker than the same spot without a shadow
    y, x = B + 3, (L + R) // 2
    assert decode(shaded)[y, x].sum() < decode(flat)[y, x].sum() - 30


def test_only_the_largest_blob_of_the_cut_out_is_kept(photo):
    im = Image.new("RGBA", (120, 60), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (10, 10, 50, 50))  # the cat, 40 px square
    im.paste((0, 0, 255, 255), (100, 40, 106, 46))  # a speck of a second blob, 6 px square
    out, sidecar = compose_product(photo, png(im), {"shadow": 0, "match": 0})
    L, T, R, B = sidecar["cat_box"]
    assert R - L == pytest.approx(B - T, abs=3)  # the speck does not widen the box
    assert not np.any(decode(out)[..., 2] > 200)  # no blue anywhere


def test_a_cut_out_with_no_cat_is_refused():
    empty = png(Image.new("RGBA", (30, 30), (0, 0, 0, 0)))
    with pytest.raises(ComposeError, match="no cat"):
        compose_product(png(Image.new("RGB", (W, H))), empty)


def test_a_cut_out_that_is_not_a_png_with_alpha_is_refused(photo):
    rgb = io.BytesIO()
    Image.new("RGB", (30, 30), (255, 0, 0)).save(rgb, format="JPEG")
    with pytest.raises(ComposeError, match="PNG with alpha"):
        compose_product(photo, rgb.getvalue())


def test_bytes_that_are_not_a_picture_are_refused(photo, red_square):
    with pytest.raises(ComposeError, match="not a picture"):
        compose_product(b"", red_square)
    with pytest.raises(ComposeError, match="not a picture"):
        compose_product(b"hello", red_square)
    with pytest.raises(ComposeError, match="not a picture"):
        compose_product(photo, b"hello")


def test_an_unsupported_photo_format_is_refused(red_square):
    bmp = io.BytesIO()
    Image.new("RGB", (W, H)).save(bmp, format="BMP")
    with pytest.raises(ComposeError, match="format is not supported"):
        compose_product(bmp.getvalue(), red_square)


def test_an_oversized_photo_is_refused(red_square):
    big = png(Image.new("RGB", (2049, 10)))
    with pytest.raises(ComposeError, match="too large"):
        compose_product(big, red_square)


@pytest.mark.parametrize(
    "params",
    [
        {"cx": 1.5},
        {"cy": -0.1},
        {"height": 0.05},
        {"height": 0.9},
        {"shadow": 2},
        {"match": True},
        {"match": "0.5"},
        {"scale": 0.5},  # not a setting name
    ],
)
def test_bad_settings_are_refused_by_name(photo, red_square, params):
    with pytest.raises(ComposeError):
        compose_product(photo, red_square, params)


def test_a_bad_setting_names_the_field(photo, red_square):
    with pytest.raises(ComposeError, match="height must be a number from 0.1 to 0.7"):
        compose_product(photo, red_square, {"height": 0.9})


def test_the_picture_is_drawn_by_the_reel_product_compositor(photo, red_square, monkeypatch):
    # one code path: the engine's ProductCompositor draws the cat, not a second compositor
    from funkbox.reel.product import ProductCompositor

    drawn = []
    real = ProductCompositor.draw

    def spy(self, frame, cam):
        drawn.append(frame.shape)
        return real(self, frame, cam)

    monkeypatch.setattr(ProductCompositor, "draw", spy)
    compose_product(photo, red_square)
    assert drawn == [(H, W, 3)]


def test_a_hole_in_the_cut_out_is_filled_with_the_cat(photo):
    # an opaque red ring with a transparent hole in its middle: the hole is cat, so it must be drawn as cat
    im = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (10, 10, 50, 50))
    im.paste((255, 0, 0, 0), (20, 20, 40, 40))  # the hole: colour kept, alpha zero
    out, sidecar = compose_product(photo, png(im), {"shadow": 0, "match": 0})
    L, T, R, B = sidecar["cat_box"]
    centre = decode(out)[(T + B) // 2, (L + R) // 2]
    assert centre[0] > 200 and centre[1] < 60 and centre[2] < 60


def test_a_speck_near_the_cat_is_not_drawn(photo):
    # a blue 2 px speck, 3 px from the cat's edge: it is a second blob, so it must not appear or widen the box
    im = Image.new("RGBA", (60, 40), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (5, 5, 29, 29))
    im.paste((0, 0, 255, 255), (32, 5, 34, 7))
    out, sidecar = compose_product(photo, png(im), {"shadow": 0, "match": 0})
    assert not np.any(decode(out)[..., 2] > 200)
    L, T, R, B = sidecar["cat_box"]
    assert R - L == pytest.approx(B - T, abs=3)


def test_an_rgb_cut_out_without_alpha_is_refused(photo):
    solid = io.BytesIO()
    Image.new("RGB", (30, 30), (255, 0, 0)).save(solid, format="PNG")
    with pytest.raises(ComposeError, match="PNG with alpha"):
        compose_product(photo, solid.getvalue())


def test_a_cut_out_too_wide_for_the_height_is_refused_before_any_resize(photo):
    # 2048 x 1 at the default height would be a sprite about two million pixels wide
    with pytest.raises(ComposeError, match="too wide"):
        compose_product(photo, png(Image.new("RGBA", (2048, 1), (255, 0, 0, 255))))


def test_a_cut_out_thin_and_tall_on_a_narrow_photo_is_refused(photo):
    narrow = png(Image.new("RGB", (1, 2048)))
    with pytest.raises(ComposeError, match="too wide"):
        compose_product(narrow, png(Image.new("RGBA", (40, 1), (255, 0, 0, 255))), {"cy": 1, "height": 0.7})


def test_colour_match_pulls_the_cat_toward_the_carpet(photo, red_square):
    carpet = np.array([90, 120, 90])
    plain, sidecar = compose_product(photo, red_square, {"shadow": 0, "match": 0})
    matched, _ = compose_product(photo, red_square, {"shadow": 0, "match": 1.0})
    L, T, R, B = sidecar["cat_box"]
    y, x = (T + B) // 2, (L + R) // 2
    a, b = decode(plain)[y, x], decode(matched)[y, x]
    assert not np.array_equal(a, b)
    assert np.linalg.norm(b - carpet) < np.linalg.norm(a - carpet)


def test_the_drawn_cat_matches_the_sidecar_box(photo, red_square):
    out, sidecar = compose_product(photo, red_square, {"shadow": 0, "match": 0})
    pix = decode(out)
    red = (pix[..., 0] > 200) & (pix[..., 1] < 60) & (pix[..., 2] < 60)
    ys, xs = np.where(red)
    L, T, R, B = sidecar["cat_box"]
    assert xs.min() == pytest.approx(L, abs=2) and xs.max() + 1 == pytest.approx(R, abs=2)
    assert ys.min() == pytest.approx(T, abs=2) and ys.max() + 1 == pytest.approx(B, abs=2)
