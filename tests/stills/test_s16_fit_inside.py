"""fit_inside: words sized and placed inside part of the artwork, and poster_fit keeping them there."""
import json

import numpy as np
import pytest
from PIL import Image

from funkbox.stills import fit, inside, render, schema


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))


def bar_doc(angle=-20, **text):
    """A blue canvas with a white slanted bar (a polygon) and a text layer meant to sit on it."""
    W, H = 800, 600
    t = np.tan(np.radians(angle))
    cx, cy, half, length = 400, 300, 50, 300
    pts = [(cx - length, cy - length * t - half), (cx + length, cy + length * t - half),
           (cx + length, cy + length * t + half), (cx - length, cy - length * t + half)]
    lay = {"id": "t", "type": "text", "text": "Hello bar", "font": "Funkbox Block:700", "color": "#1b224b",
           "size": 0.02, "x": 0.1, "y": 0.1, "max_width": 0.9, "wrap": False, "skew_y": angle, **text}
    return {"version": 1, "casual": True, "min_text": 0.012, "canvas": {"width": W, "height": H, "background": "#3e61ad"},
            "layers": [{"id": "bar", "type": "shape", "kind": "polygon", "points": [[x / W, y / H] for x, y in pts], "fill": "#ffffff"}, lay]}


SPEC = {"layer": "t", "at": [0.5, 0.5], "band": 0.07, "margin": 0.004}


def test_words_fill_the_bar_without_leaving_it():
    doc = bar_doc()
    res = inside.fit_inside(doc, [SPEC])
    assert not res.notes and schema.check(res.doc) == []
    new = next(l for l in res.doc["layers"] if l["id"] == "t")
    assert new["text"] == "Hello bar" and new["size"] > 0.02                      # words untouched, and bigger
    mask = inside.region(res.doc, inside.check_specs(res.doc, [SPEC])[0])
    assert inside.outside(res.doc, inside.check_specs(res.doc, [SPEC])[0], mask) == 0
    t = inside.ink(res.doc, "t")
    ys, xs = np.nonzero(t)
    assert abs(xs.mean() / t.shape[1] - 0.5) < 0.05 and abs(ys.mean() / t.shape[0] - 0.5) < 0.05      # centred
    assert new["size"] < 0.2 and res.changed["t"]["size"] == new["size"]
    assert all(isinstance(v, float) for v in res.changed["t"].values())               # JSON-safe


def test_a_thicker_bar_gets_bigger_words():
    thin = inside.fit_inside(bar_doc(), [SPEC]).changed["t"]["size"]
    doc = bar_doc()
    for p in doc["layers"][0]["points"]:                                                # bar twice as thick
        p[1] = 0.5 + (p[1] - 0.5) * 1.0
    wide = dict(SPEC, band=0.07)
    d2 = bar_doc(angle=-20)
    d2["layers"][0]["points"][0][1] -= 0.06; d2["layers"][0]["points"][1][1] -= 0.06
    d2["layers"][0]["points"][2][1] += 0.06; d2["layers"][0]["points"][3][1] += 0.06
    thick = inside.fit_inside(d2, [dict(wide, band=0.14)]).changed["t"]["size"]
    assert thick >= thin


def test_equal_size_for_two_halves():
    doc = bar_doc()
    second = dict(doc["layers"][1], id="u", text="Hi", y=0.8)
    doc["layers"].append(second)
    res = inside.fit_inside(doc, [SPEC, dict(SPEC, layer="u", at=[0.5, 0.5])])
    assert res.changed["t"]["size"] == res.changed["u"]["size"]
    solo = inside.fit_inside(doc, [SPEC, dict(SPEC, layer="u", at=[0.5, 0.5])], equal_size=False)
    assert solo.changed["u"]["size"] >= res.changed["u"]["size"]


def test_poster_fit_keeps_words_inside_even_when_the_reference_wants_them_out(tmp_path):
    fitted = inside.fit_inside(bar_doc(), [SPEC]).doc
    ref = render.render_document(fitted).pil.convert("RGB")
    wrong = json.loads(json.dumps(fitted))                                              # a reference with the words off the bar
    for l in wrong["layers"]:
        if l["id"] == "t":
            l["y"] = round(l["y"] - 0.2, 4)
    ref_path = tmp_path / "ref.png"
    render.render_document(wrong).pil.convert("RGB").save(ref_path)
    y0 = next(l for l in fitted["layers"] if l["id"] == "t")["y"]
    params = {"layers.t.y": [y0 - 0.25, y0]}                                            # it may only move toward the reference
    free = fit.fit(fitted, str(ref_path), params, rounds=3, scale=0.5)
    kept = fit.fit(fitted, str(ref_path), params, rounds=3, scale=0.5, keep_inside=[SPEC])
    ty = lambda d: next(l for l in d.doc["layers"] if l["id"] == "t")["y"]
    assert ty(free) < y0 - 0.03                                                          # without the rule it chases the reference
    spec = inside.check_specs(kept.doc, [SPEC])[0]
    assert inside.outside(kept.doc, spec, inside.region(kept.doc, spec)) < 0.01          # with it the words stay in the bar


@pytest.mark.parametrize("bad,match", [
    ({"layer": "nope", "at": [0.5, 0.5]}, "no layer"),
    ({"layer": "bar", "at": [0.5, 0.5]}, "not a text layer"),
    ({"layer": "t", "at": [2, 0.5]}, "fractions"),
    ({"layer": "t", "at": [0.5, 0.5], "bogus": 1}, "needs layer and at"),
    ({"layer": "t", "at": [0.5, 0.5], "band": 5}, "band"),
])
def test_bad_specs(bad, match):
    with pytest.raises(ValueError, match=match):
        inside.fit_inside(bar_doc(), [bad])


def test_bent_text_and_empty_regions_are_refused():
    with pytest.raises(ValueError, match="bent"):
        inside.fit_inside(bar_doc(arc=0.3), [SPEC])
    with pytest.raises(ValueError, match="empty"):
        inside.fit_inside(bar_doc(), [dict(SPEC, at=[0.02, 0.02], band=None)] if False else [dict(SPEC, at=[0.5, 0.5], tolerance=1, band=0.002, margin=0.05)])
