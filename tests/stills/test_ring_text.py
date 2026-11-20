"""Ring text: a text layer's ``ring`` sets one line on a circle (a round badge's rim), centred and bent for it."""

import xml.etree.ElementTree as ET

import numpy as np

from funkbox.stills import render, resize, schema, vector

BG = (255, 255, 255)


def doc(layers, w=400, h=400):
    return {
        "version": 1,
        "canvas": {"width": w, "height": h, "background": "#ffffff"},
        "casual": True,
        "min_text": 0.02,
        "layers": layers,
    }


def ring_layer(side, text="ROUND CLUB BADGE", r=0.3, cx=0.5, cy=0.5):
    return {
        "id": side,
        "type": "text",
        "text": text,
        "size": 0.07,
        "color": "#000000",
        "ring": {"cx": cx, "cy": cy, "r": r, "side": side},
    }


def ink(img):
    return np.abs(np.asarray(img, int)[..., :3] - BG).sum(-1) > 60


def _rows(m, x0, x1):
    return np.nonzero(m[:, x0:x1].any(1))[0].mean()


def _shape(img):
    """(ink centre x, mean ink row in the middle tenth, mean ink row in the first tenth)."""
    m = ink(img)
    cols = np.nonzero(m.any(0))[0]
    lo, hi = cols.min(), cols.max()
    w = hi - lo
    return (lo + hi) / 2, _rows(m, lo + int(w * 0.45), lo + int(w * 0.55)), _rows(m, lo, lo + int(w * 0.1))


def test_top_ring_text_is_centred_on_the_circle_as_a_frown_and_stays_exact():
    r = render.render_document(doc([ring_layer("top")]))
    cx, mid, end = _shape(r.image)
    assert abs(cx - 200) < 6  # centred on the circle's cx
    assert abs(mid - (200 - 0.3 * 400)) < 8  # the line's middle sits on the circle, above the centre
    assert mid < end - 4  # the ends bend down round the circle
    e = r.audit[0]
    assert e["text"] == "ROUND CLUB BADGE" and e["text_exact"]
    assert e["geometry"]["ring"] == {"cx": 0.5, "cy": 0.5, "r": 0.3, "side": "top"} and e["geometry"]["arc"] == -0.3


def test_bottom_ring_text_is_a_smile_below_the_centre():
    r = render.render_document(doc([ring_layer("bottom", "AFTER DARK SINCE 2026")]))
    cx, mid, end = _shape(r.image)
    assert abs(cx - 200) < 6
    assert abs(mid - (200 + 0.3 * 400)) < 8
    assert mid > end + 4  # the ends bend up round the circle
    assert r.audit[0]["text_exact"] and r.audit[0]["geometry"]["arc"] == 0.3


def test_ring_follows_the_circle_off_centre_on_a_tall_canvas():
    r = render.render_document(doc([ring_layer("top", r=0.25, cx=0.45, cy=0.3)], w=400, h=700))
    cx, mid, _ = _shape(r.image)
    assert abs(cx - 0.45 * 400) < 6
    assert abs(mid - (0.3 * 700 - 0.25 * 400)) < 8  # r is in canvas widths, cy a fraction of the height


def test_ring_text_exports_as_vector_outlines(tmp_path):
    r = render.render_document(doc([ring_layer("top"), ring_layer("bottom", "AFTER DARK")]))
    rep = vector.export(r, tmp_path / "ring.svg")
    assert rep["vector"] == ["top", "bottom"] and not rep["pixels"]
    svg = ET.parse(tmp_path / "ring.svg").getroot()
    assert not list(svg.iter("{http://www.w3.org/2000/svg}text"))


def test_resize_moves_and_scales_the_ring_with_the_canvas():
    d, _ = resize.adapt(doc([ring_layer("top")]), 400, 700)
    lay = d["layers"][0]
    assert "x" not in lay and "y" not in lay  # the ring still places the line
    ring = lay["ring"]
    assert abs(ring["cx"] - 0.5) < 1e-6 and abs(ring["cy"] - 0.5) < 1e-6  # the circle stays centred
    r = render.render_document(d)
    assert r.audit[0]["text_exact"]
    cx, mid, _ = _shape(r.image)
    assert abs(cx - 200) < 6 and abs(mid - (0.5 * 700 - ring["r"] * 400)) < 8


def test_ring_validation_names_the_problem():
    bad = doc(
        [
            dict(ring_layer("top"), id="a", arc=0.5),
            dict(ring_layer("top"), id="b", ring={"cx": 0.5, "cy": 0.5, "r": 0.3, "side": "left"}),
            dict(ring_layer("top"), id="c", ring={"cx": 0.5, "r": 0.3}),
            dict(ring_layer("top"), id="d", ring={"cx": 0.5, "cy": 0.5, "r": 0.01}),
            dict(ring_layer("top"), id="e", text="TWO\nLINES"),
        ]
    )
    errs = " | ".join(schema.check(bad))
    assert "drop arc" in errs
    assert "ring.side is top" in errs
    assert "ring is {cx, cy, r, side}" in errs
    assert "ring.r must be a number in 0.05..20" in errs
    assert "ring text is one line" in errs
    assert not schema.check(doc([ring_layer("top"), ring_layer("bottom")]))
