"""Typography: letter spacing, styled runs (font, colour, size for some words), text along any path."""
import math

import numpy as np
import pytest

from funkbox.stills import render, schema, vector

BRAND = {"font_display": "fonts/Rubik-Bold.ttf", "font_caption": "fonts/Oswald-SemiBold.ttf"}


def doc(layers, w=800, h=600):
    return {"version": 1, "canvas": {"width": w, "height": h, "background": "#ffffff"}, "casual": True,
            "brand": BRAND, "layers": layers}


def T(**kw):
    base = {"id": "t", "type": "text", "text": "Army of Hashem for Israel", "size": 0.08, "x": 0.05, "y": 0.4,
            "max_width": 0.9, "align": "center", "color": "#000000"}
    return {**base, **kw}


def ink(im):
    return np.abs(im.astype(int) - 255).sum(-1) > 120


def span(im):
    xs = np.nonzero(ink(im).any(0))[0]
    return xs.max() - xs.min()


def test_letter_spacing_adds_space_after_every_letter_but_the_last():
    plain = render.render_document(doc([T(wrap=False, letter_spacing=0.0001)]))
    wide = render.render_document(doc([T(wrap=False, letter_spacing=0.1)]))
    px = plain.audit[0]["px"]
    assert wide.audit[0]["px"] == px and wide.audit[0]["text_exact"]
    gaps = len("Army of Hashem for Israel") - 1
    assert span(wide.image) - span(plain.image) == pytest.approx(0.1 * px * gaps, rel=0.03)
    assert wide.audit[0]["typography"] == {"letter_spacing": 0.1}


def test_the_outline_engine_matches_the_plain_text_engine():
    a = render.render_document(doc([T()])).image
    b = render.render_document(doc([T(letter_spacing=1e-9)])).image          # forces the outline engine
    d = np.abs(a.astype(int) - b.astype(int)).sum(-1)
    assert d.mean() < 2 and (d > 200).mean() < 0.004


def test_runs_restyle_words_and_keep_the_text_exact():
    r = render.render_document(doc([T(runs=[{"match": "Hashem", "color": "#ff0000", "font": "display", "scale": 1.2}])]))
    a = r.audit[0]
    assert a["text"] == "Army of Hashem for Israel" and a["text_exact"] and a["typography"]["runs"] == ["Hashem"]
    im = r.image.astype(int)
    red = (im[..., 0] > 200) & (im[..., 1] < 60) & (im[..., 2] < 60)
    xs = np.nonzero(red.any(0))[0]
    assert red.sum() > 1500 and 280 < xs.mean() < 520                       # the middle word only
    blk = ink(r.image) & ~red
    assert np.nonzero(blk.any(0))[0].min() < xs.min() - 100


def test_run_problems_name_the_run():
    with pytest.raises(schema.PosterError, match="matches nothing"):
        render.render_document(doc([T(runs=[{"match": "Moshiach"}])]))
    with pytest.raises(schema.PosterError, match="no glyph"):
        render.render_document(doc([T(text="צבאות ה׳", runs=[{"match": "צבאות", "font": "fonts/Oswald-SemiBold.ttf"}])]))
    errs = " | ".join(schema.check(doc([T(runs=[{"match": "", "colour": "red"}]),
                                        T(id="u", runs=[{"match": "x", "scale": 9, "zz": 1}])])))
    assert "needs `match`" in errs and "scale must be" in errs and "unknown keys" in errs


def test_hebrew_runs_keep_right_to_left_order():
    r = render.render_document(doc([T(text="צבאות ה׳ לישראל", style="display",
                                       runs=[{"match": "ה׳", "color": "#ff0000"}])]))
    assert r.audit[0]["text_exact"]
    im = r.image.astype(int)
    red = (im[..., 0] > 200) & (im[..., 1] < 60)
    rx = np.nonzero(red.any(0))[0].mean()
    xs = np.nonzero(ink(r.image).any(0))[0]
    assert xs.min() + 0.3 * (xs.max() - xs.min()) < rx < xs.min() + 0.7 * (xs.max() - xs.min())   # in the middle


def test_text_follows_a_circle():
    R, cx, cy = 200.0, 400.0, 300.0
    lay = T(text="AROUND AND AROUND WE GO", size=0.05, on_path=f"M{cx},{cy + R} a{R},{R} 0 1,1 0.1,0",
            path_units="px")
    r = render.render_document(doc([lay]))
    assert r.audit[0]["text_exact"] and r.audit[0]["typography"]["on_path"]
    ys, xs = np.nonzero(ink(r.image))
    dist = np.hypot(xs - cx, ys - cy)
    assert dist.min() > R - 5 and dist.max() < R + 45                      # clockwise: letters stand outside it
    assert ys.min() < cy - R + 50                                          # centred: the top of the circle has text


def test_text_on_a_line_is_rotated_along_it_and_shrinks_to_fit():
    lay = T(text="SLOPE", size=0.1, on_path="M0.1,0.9 L0.9,0.1")
    r = render.render_document(doc([lay], 600, 600))
    ys, xs = np.nonzero(ink(r.image))
    ev, vec = np.linalg.eigh(np.cov(np.stack([xs, ys])))
    major = vec[:, -1]
    assert abs(math.degrees(math.atan2(major[1], major[0])) % 180 - 135) < 6         # along the 45-degree line
    long = T(text="THIS LINE IS TOO LONG", size=0.1, on_path="M0.15,0.5 L0.85,0.5")
    assert render.render_document(doc([long], 600, 600)).audit[0]["px"] < 60


def test_path_text_validation():
    errs = " | ".join(schema.check(doc([T(on_path="L1,1"), T(id="u", on_path="M0,0 L1,1", arc=0.5),
                                        T(id="v", letter_spacing=3), T(id="w", on_path="M0,0 L1,1", path_shift=9)])))
    assert "on_path: path data must start with M" in errs and "not both" in errs
    assert "letter_spacing is" in errs and "path_shift must be" in errs


def test_vector_export_of_runs_and_path_text_matches(tmp_path, monkeypatch):
    layers = [T(runs=[{"match": "Hashem", "color": "#ff0000"}], letter_spacing=0.05, y=0.1),
              T(id="p", text="ON THE CURVE", size=0.06, on_path="M0.1,0.8 C0.3,0.4 0.7,0.4 0.9,0.8")]
    r = render.render_document(doc(layers))
    vector.export(r, tmp_path / "t.svg")
    s = (tmp_path / "t.svg").read_text()
    assert 'fill="#ff0000"' in s and "<text" not in s
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    back = render.render_document(doc([{"id": "s", "type": "svg", "path": "t.svg", "x": 0, "y": 0, "width": 1.0}]),
                                  base_dir=tmp_path)
    d = np.abs(r.image.astype(int) - back.image.astype(int)).sum(-1)
    assert d.mean() < 3 and (d > 200).mean() < 0.005
