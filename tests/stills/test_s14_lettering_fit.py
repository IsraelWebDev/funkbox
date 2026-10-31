"""Lettering (a block face and poster glyphs as real fonts, still exact text) and fitting a poster to a reference."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from funkbox import fonts, lettering
from funkbox.stills import fit, render, schema
from funkbox.stills.variations import get_path, set_path


def doc(layers, w=600, h=400, **kw):
    return {"version": 1, "casual": True, "canvas": {"width": w, "height": h, "background": "#ffffff"},
            "layers": layers, **kw}


def ink(im):
    return np.asarray(im)[..., :3].astype(int).sum(-1) < 300


def test_block_face_covers_hebrew_latin_digits_and_builds_once(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    p = fonts.resolve("Funkbox Block:800")
    assert p.name.startswith("FunkboxBlock") and p.is_file()
    text = "אבגדהוזחטיכךלמםנןסעפףצץקרשת ׳״ ABCXYZ abc 0123456789 !?.,:-+/&()"
    assert fonts.missing(p, text) == []
    mtime = p.stat().st_mtime_ns
    assert fonts.resolve("Funkbox Block:800").stat().st_mtime_ns == mtime          # cached
    assert fonts.resolve("Funkbox Block:300") != p
    assert lettering.thickness(100) < lettering.thickness(900)


def test_block_text_is_exact_and_right_to_left():
    r = render.render_document(doc([{"id": "t", "type": "text", "text": "צהל WE WANT", "font": "Funkbox Block:700",
                                     "size": 0.15, "x": 0.05, "y": 0.3, "max_width": 0.9, "color": "#000000",
                                     "wrap": False}]))
    a = r.audit[0]
    assert a["text"] == "צהל WE WANT" and a["text_exact"] and ink(r.image).sum() > 4000


def test_poster_glyphs_draw_the_shapes_you_give(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    box = {"width": 1.0, "d": "M0,0 L1,0 L1,1 L0,1 Z"}                               # a filled square
    bar = {"width": 0.6, "strokes": [[[0.3, 0.0], [0.3, 1.0]]]}
    lay = {"id": "t", "type": "text", "text": "AB", "font": "glyphs:mine", "size": 0.3, "x": 0.1, "y": 0.2,
           "max_width": 0.8, "color": "#000000", "wrap": False}
    d = doc([lay], glyphs={"mine": {"weight": 400, "chars": {"A": box, "B": bar}}})
    assert not schema.check(d)
    r = render.render_document(d)
    m = ink(r.image)
    ys, xs = np.nonzero(m)
    cap = 0.7 * 0.3 * 400                                                           # cap height in px
    first = m[:, xs.min():xs.min() + int(cap * 0.9)]
    assert first.mean(1).max() > 0.85 * cap / cap * 0.9                            # the square is solid
    assert r.audit[0]["text_exact"]
    with pytest.raises(schema.PosterError, match="has no glyph"):
        render.render_document(doc([dict(lay, text="ABC")], glyphs={"mine": {"chars": {"A": box, "B": bar}}}))
    errs = " | ".join(schema.check(doc([lay], glyphs={"mine": {"chars": {"AB": box, "C": {"width": 9}}}})))
    assert "one character per glyph" in errs and "strokes" in errs
    errs = " | ".join(schema.check(doc([dict(lay, font="glyphs:other")], glyphs={"mine": {"chars": {"A": box}}})))
    assert "names no entry" in errs


def test_glyphs_on_top_of_the_block_face(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    spec = {"base": "block", "weight": 700, "chars": {"צ": {"width": 0.9, "d": "M0,0 L0.9,0 L0.9,1 L0,1 Z"}}}
    p = lettering.glyphset_font(spec)
    assert fonts.missing(p, "צהל ABC") == [] and lettering.glyphset_font(spec) == p


def test_set_and_get_reach_list_items():
    d = doc([{"id": "s", "type": "shape", "kind": "polygon", "points": [[0, 0], [1, 0], [1, 1]], "fill": "#000000"}])
    set_path(d, "layers.s.points.2.1", 0.5)
    assert get_path(d, "layers.s.points.2.1") == 0.5 and d["layers"][0]["points"][2] == [1, 0.5]
    from funkbox.stills.variations import VaryError
    with pytest.raises(VaryError, match="not an index"):
        set_path(d, "layers.s.points.9.0", 1)


def test_fit_moves_a_shape_onto_the_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    ref = Image.new("RGB", (300, 200), "white")
    ImageDraw.Draw(ref).rectangle((120, 60, 200, 140), fill=(200, 30, 40))
    ref.save(tmp_path / "ref.png")
    rect = {"id": "r", "type": "shape", "kind": "rect", "points": [[0.25, 0.2], [0.52, 0.6]], "fill": "#c81e28"}
    d = doc([rect], 300, 200)
    res = fit.fit(d, tmp_path / "ref.png", {"layers.r.points.0.0": [0.2, 0.6], "layers.r.points.1.0": [0.4, 0.8],
                                             "layers.r.points.0.1": 0.05, "layers.r.points.1.1": 0.05}, rounds=5)
    assert res.score["overall"] > res.start["overall"] + 5 and res.changed
    (x0, y0), (x1, y1) = res.doc["layers"][0]["points"]
    assert abs(x0 * 300 - 120) < 15 and abs(x1 * 300 - 200) < 15
    with pytest.raises(ValueError, match="needs a number"):
        fit.fit(d, tmp_path / "ref.png", {"layers.r.fill": 1})
    with pytest.raises(ValueError, match="only numbers"):
        fit.fit(d, tmp_path / "ref.png", {"layers.r.d": 1})
    with pytest.raises(ValueError, match="outside"):
        fit.fit(d, tmp_path / "ref.png", {"layers.r.points.0.0": [0.5, 0.9]})
