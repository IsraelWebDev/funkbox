"""Tracing a picture into vector shapes."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from funkbox.stills import render, trace, vpath


def logo(w=400, h=300):
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((40, 40, 260, 260), fill=(20, 30, 120))
    d.ellipse((100, 100, 200, 200), fill=(255, 255, 255))            # a hole
    d.rectangle((280, 60, 360, 240), fill=(240, 200, 30))
    d.polygon([(300, 80), (340, 80), (320, 200)], fill=(220, 30, 50))
    return im


def test_pixel_edge_loops_wind_holes_the_other_way():
    m = np.zeros((10, 10), bool)
    m[2:8, 2:8] = True
    m[4:6, 4:6] = False
    loops = trace.loops_of(m)
    areas = sorted(trace.area(l) for l in loops)
    assert areas == [-4.0, 36.0]
    diag = np.array([[1, 0], [0, 1]], bool)                            # two pixels touching at a corner
    assert sorted(trace.area(l) for l in trace.loops_of(diag)) == [1.0, 1.0]


def test_a_logo_traces_with_its_colours_holes_and_high_fidelity():
    res = trace.trace(logo(), colors=4)
    assert res.fidelity > 95
    cols = {p["color"] for p in res.palette}
    assert {"#ffffff", "#141e78", "#f0c81e", "#dc1e32"} == cols
    im = np.asarray(res.preview).astype(int)
    assert tuple(im[150, 150]) == (255, 255, 255)                      # the hole is a hole
    assert tuple(im[60, 150]) == (20, 30, 120) and tuple(im[150, 320]) == (220, 30, 50)
    assert res.svg.count("<path") == 4 and "C" in res.svg            # smooth curves


def test_a_fixed_palette_and_straight_segments(tmp_path, monkeypatch):
    res = trace.trace(logo(), palette=["#ffffff", "#000080", "#ffff00", "#ff0000"], smooth=0)
    assert [p["color"] for p in res.palette][:1] == ["#ffffff"] and "C" not in res.svg
    assert {p["color"] for p in res.palette} == {"#ffffff", "#000080", "#ffff00", "#ff0000"}
    monkeypatch.setenv("FUNK_OUT", str(tmp_path))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    paths = trace.write(res, tmp_path, "logo")
    assert paths["svg"].exists() and paths["preview"].exists()
    # usable as an svg layer, at another size, still sharp
    r = render.render_document({"version": 1, "casual": True, "canvas": {"width": 800, "height": 600,
                                "background": "#ffffff"}, "layers": [{"id": "s", "type": "svg", "path": "logo.svg",
                                                                      "x": 0, "y": 0, "width": 1.0}]}, base_dir=tmp_path)
    assert tuple(r.image[300, 640]) == (255, 0, 0)
    with pytest.raises(ValueError, match="#rrggbb"):
        trace.trace(logo(), palette=["red"])


def test_curves_keep_long_edges_straight():
    pts = np.array([[0, 0], [300, 0], [300, 10], [0, 10]], float)
    d = trace.to_d(pts, 1.0)
    (poly, _), = vpath.parse(d, 0.1)
    ys = np.array(poly)[:, 1]
    assert ys.min() > -0.01 and ys.max() < 10.01                       # no bulge past the rectangle
