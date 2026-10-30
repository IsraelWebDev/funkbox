"""Shape layers (polygons, rects, ellipses, thick mitred strokes) and text bent along an arc, slanted or rotated."""
import numpy as np
import pytest

from funkbox.stills import render, schema
from funkbox.stills.shapes import bar_polygons

BG = (255, 255, 255)


def doc(layers, w=400, h=400):
    return {"version": 1, "canvas": {"width": w, "height": h, "background": "#ffffff"}, "casual": True, "layers": layers}


def ink(img):
    return (np.abs(img.astype(int) - BG).sum(-1) > 60)


def test_shapes_fill_stroke_and_stay_inside_their_points():
    r = render.render_document(doc([{"id": "p", "type": "shape", "kind": "polygon",
                                     "points": [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]],
                                     "fill": "#ff0000", "stroke": "#0000ff", "stroke_px": 8}]))
    im = r.image
    assert tuple(im[200, 200]) == (255, 0, 0)                           # filled
    assert tuple(im[200, 97]) == (0, 0, 255) and tuple(im[200, 303]) == (0, 0, 255)    # outline, outside the fill
    assert tuple(im[200, 80]) == BG and tuple(im[60, 200]) == BG           # nothing beyond the outline
    e = render.render_document(doc([{"id": "e", "type": "shape", "kind": "ellipse", "points": [[0.1, 0.1], [0.9, 0.9]],
                                     "fill": "#00ff00"}])).image
    assert tuple(e[200, 200]) == (0, 255, 0) and tuple(e[45, 45]) == BG    # a circle: the corner stays empty


def test_a_thick_stroke_has_a_full_mitred_corner():
    pts = [(100.0, 100.0), (300.0, 100.0), (300.0, 300.0)]
    polys = bar_polygons(pts, 20.0, "square")
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    assert max(xs) == pytest.approx(320) and min(ys) == pytest.approx(80)     # outer corner reaches (320, 80)
    r = render.render_document(doc([{"id": "l", "type": "shape", "kind": "polyline",
                                     "points": [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75]], "width": 0.1,
                                     "fill": "#000000"}])).image
    assert tuple(r[105, 315]) == (0, 0, 0)                               # the corner is square, not cut off
    assert tuple(r[200, 200]) == BG                                      # inside of the L is empty


def test_shape_validation_names_the_problem():
    bad = doc([{"id": "s", "type": "shape", "kind": "star", "points": []},
               {"id": "t", "type": "shape", "kind": "polygon", "points": [[0, 0], [1, 1]], "fill": "red"},
               {"id": "u", "type": "shape", "kind": "rect", "points": [[0, 0], [1, 1]]}])
    errs = " | ".join(schema.check(bad))
    assert "kind`: one of" in errs and "at least 3" in errs and "fill must be" in errs and "needs `fill`, `stroke`" in errs


def _rows_with_ink(img, x0, x1):
    m = ink(img)[:, x0:x1]
    rows = np.nonzero(m.any(1))[0]
    return rows.mean()


def _mid_and_end(img):
    """Mean ink row in the middle tenth of the text and in its first tenth."""
    cols = np.nonzero(ink(img).any(0))[0]
    lo, hi = cols.min(), cols.max()
    w = hi - lo
    return (_rows_with_ink(img, lo + int(w * 0.45), lo + int(w * 0.55)),
            _rows_with_ink(img, lo, lo + int(w * 0.1)))


def test_text_on_a_smile_arc_dips_in_the_middle_and_stays_exact():
    lay = {"id": "t", "type": "text", "text": "WE WANT MOSHIACH NOW", "size": 0.06, "x": 0.05, "y": 0.45,
           "max_width": 0.9, "align": "center", "color": "#000000"}
    flat = render.render_document(doc([lay])).image
    lay2 = dict(lay, arc=0.6)
    r = render.render_document(doc([lay2]))
    bent = r.image
    assert r.audit[0]["text"] == "WE WANT MOSHIACH NOW" and r.audit[0]["text_exact"]
    assert r.audit[0]["geometry"] == {"arc": 0.6}
    mid, end = _mid_and_end(bent)
    assert mid > end + 4                                                  # the middle sits lower than the ends
    fm, fe = _mid_and_end(flat)
    assert abs(fm - fe) < 3
    m2, e2 = _mid_and_end(render.render_document(doc([dict(lay, arc=-0.6)])).image)
    assert m2 < e2 - 4


def test_slanted_text_rises_to_the_right():
    lay = {"id": "t", "type": "text", "text": "Army of Hashem", "size": 0.07, "x": 0.1, "y": 0.45, "max_width": 0.8,
           "align": "center", "color": "#000000", "skew_y": -20}
    r = render.render_document(doc([lay]))
    cols = np.nonzero(ink(r.image).any(0))[0]
    left = _rows_with_ink(r.image, cols.min(), cols.min() + 40)
    right = _rows_with_ink(r.image, cols.max() - 40, cols.max())
    assert right < left - 20 and r.audit[0]["text_exact"]
    errs = " | ".join(schema.check(doc([dict(lay, skew_y=80), dict(lay, id="u", arc=0.01)])))
    assert "skew_y must be" in errs and "arc is the radius" in errs


def test_hebrew_on_an_arc_keeps_its_letters():
    lay = {"id": "t", "type": "text", "text": "צבאות ה׳ לישראל", "size": 0.07, "x": 0.05, "y": 0.4, "max_width": 0.9,
           "align": "center", "color": "#000000", "arc": -0.7}
    r = render.render_document(doc([lay]))
    assert r.audit[0]["text"] == "צבאות ה׳ לישראל" and r.audit[0]["text_exact"] and ink(r.image).sum() > 2000


def test_wrap_false_keeps_one_line_and_shrinks_to_fit():
    lay = {"id": "t", "type": "text", "text": "Hashem for Israel", "size": 0.08, "x": 0.3, "y": 0.4, "max_width": 0.3,
           "color": "#000000"}
    wrapped = render.render_document(doc([lay])).audit[0]
    one = render.render_document(doc([dict(lay, wrap=False)])).audit[0]
    assert len(wrapped["drawn_lines"]) > 1 and one["drawn_lines"] == ["Hashem for Israel"]
    assert one["px"] < wrapped["px"] and one["text_exact"]


def test_the_badge_example_renders_with_exact_text():
    import json
    from pathlib import Path
    d = json.loads((Path(__file__).resolve().parents[2] / "examples/stills/badge.poster.json").read_text("utf-8"))
    d["brand"].update(font_display="fonts/Rubik-Bold.ttf", font_caption="fonts/Oswald-SemiBold.ttf")   # offline
    r = render.render_document(d)
    assert [a["text"] for a in r.audit] == ["טקסט מדויק", "made with funkbox", "EXACT TEXT. EVERY TIME."]
    assert all(a["text_exact"] for a in r.audit)
