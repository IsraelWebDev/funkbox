"""One poster, many sizes: backgrounds stretch, the design keeps its proportions, text stays exact."""
import numpy as np
import pytest

from funkbox.stills import render, resize, schema, vpath

BRAND = {"font_display": "fonts/Rubik-Bold.ttf", "font_caption": "fonts/Oswald-SemiBold.ttf"}


def doc(layers, w=600, h=600):
    return {"version": 1, "canvas": {"width": w, "height": h, "background": "#ffffff"}, "casual": True,
            "brand": BRAND, "layers": layers}


LAYERS = [
    {"id": "bg", "type": "solid", "color": "#203040"},
    {"id": "dot", "type": "shape", "kind": "ellipse", "points": [[0.3, 0.3], [0.7, 0.7]], "fill": "#ffcc00"},
    {"id": "ring", "type": "shape", "kind": "path", "units": "px", "fill": "#ff0000",
     "d": "M250,300 a50,50 0 1,0 100,0 a50,50 0 1,0 -100,0 Z"},
    {"id": "title", "type": "text", "text": "SAME WORDS", "size": 0.08, "x": 0.1, "y": 0.05, "max_width": 0.8,
     "align": "center", "color": "#ffffff"},
    {"id": "foot", "type": "shape", "kind": "rect", "points": [[0.0, 0.9], [1.0, 1.0]], "fill": "#00ff88",
     "resize": "bottom"},
]


def _bbox(mask):
    ys, xs = np.nonzero(mask)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def test_the_frame_maps_the_design_into_the_middle():
    f = resize.frame_for("fit", 600, 600, 1080, 1920)
    assert f.x(0) == pytest.approx(0) and f.x(1) == pytest.approx(1)
    assert f.y(0) == pytest.approx((1920 - 1080) / 2 / 1920) and f.s == pytest.approx(1.8)
    b = resize.frame_for("bottom", 600, 600, 1080, 1920)
    assert b.y(1) == pytest.approx(1.0)
    c = resize.frame_for("fit", 100, 100, 400, 200, box=(0.25, 0.25, 0.75, 0.75))
    assert (c.x(0.25), c.x(0.75), c.y(0.25), c.y(0.75)) == pytest.approx((0.25, 0.75, 0.0, 1.0))


def test_a_story_keeps_circles_round_and_the_footer_at_the_bottom():
    d, _ = resize.adapt(doc(LAYERS), 1080, 1920)
    assert not schema.check(d) and "resize" not in d["layers"][4]
    r = render.render_document(d)
    im = r.image.astype(int)
    assert tuple(im[5, 5]) == (0x20, 0x30, 0x40)                     # the background fills the new canvas
    yellow = (im[..., 0] > 240) & (im[..., 1] > 180) & (im[..., 2] < 60)
    x0, y0, x1, y1 = _bbox(yellow)
    assert abs((x1 - x0) - (y1 - y0)) <= 2 and abs((x0 + x1) / 2 - 540) <= 2 and abs((y0 + y1) / 2 - 960) <= 2
    red = (im[..., 0] > 240) & (im[..., 1] < 40)
    rx0, ry0, rx1, ry1 = _bbox(red)
    assert abs((rx1 - rx0) - 180) <= 3 and abs((ry0 + ry1) / 2 - 960) <= 2   # the px path scaled 1.8x, centred
    green = (im[..., 1] > 240) & (im[..., 0] < 40)
    gx0, gy0, gx1, gy1 = _bbox(green)
    assert gy1 == 1920 and gy0 == pytest.approx(1920 - 108, abs=2)       # pinned to the bottom edge
    assert r.audit[0]["text"] == "SAME WORDS" and r.audit[0]["text_exact"]


def test_fit_to_content_fills_a_banner():
    base = doc(LAYERS[:2])
    box = resize.content_box(base)
    assert box == pytest.approx((0.3, 0.3, 0.7, 0.7), abs=0.01)
    d, _ = resize.adapt(base, 1500, 500, box, margin=0.1)
    im = render.render_document(d).image.astype(int)
    x0, y0, x1, y1 = _bbox((im[..., 0] > 240) & (im[..., 2] < 60))
    assert y0 == pytest.approx(50, abs=2) and y1 == pytest.approx(450, abs=2) and abs((x1 - x0) - 400) <= 3


def test_poster_sizes_writes_each_and_reports_the_ones_that_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path))
    sizes = ["story", "landscape", {"name": "fixed", "width": 800, "height": 400, "set": {"layers.title.y": 0.02}},
             {"name": "tiny", "width": 64, "height": 640}]
    res = resize.poster_sizes(doc(LAYERS), sizes, tmp_path, "p", formats=["png", "svg"])
    assert set(res.files) == {"story", "landscape", "fixed"} and "tiny" in res.errors
    assert "title" in res.errors["tiny"]                              # its text would be too small
    assert res.files["story"]["png"].name == "p-story.png" and res.files["fixed"]["svg"].exists()
    assert res.docs[2]["layers"][3]["y"] == 0.02
    assert all(a["text_exact"] for au in res.audits.values() for a in au)
    assert res.sheet.exists()
    with pytest.raises(ValueError, match="use one of square"):
        resize.poster_sizes(doc(LAYERS), ["poster"], tmp_path, "p")


def test_bad_resize_rule_is_rejected():
    errs = schema.check(doc([dict(LAYERS[1], resize="middle")]))
    assert any("resize must be one of" in e for e in errs)


def test_transform_d_keeps_curves_and_arcs():
    d = "M10,10 l10,0 c5,0 5,10 0,10 q-5,0 -5,-5 a5,5 0 0 1 5,-5 h2 v3 Z"
    t = vpath.transform_d(d, 2, 2, 100, 50)
    a = vpath.parse(d, 0.05)
    b = vpath.parse(t, 0.1)
    pa = np.array(a[0][0]) * 2 + [100, 50]
    pb = np.array(b[0][0])
    assert np.abs(pa[[0, 1, -1]] - pb[[0, 1, -1]]).max() < 1e-6
    assert "C" in t and "Q" in t and "A10,10" in t
