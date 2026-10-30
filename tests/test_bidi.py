"""Display order of right-to-left lines without libraqm."""
import pytest

from funkbox import bidi


@pytest.mark.parametrize("logical, shown", [
    ("צהל", "להצ"),
    ("hello", "hello"),
    ("צבאות ה׳ לישראל", "לארשיל ׳ה תואבצ"),
    ("Army of Hashem צהל!", "Army of Hashem להצ!"),
    ("צהל 2026 now", "now 2026 להצ"),
    ("שלום (עולם)", "(םלוע) םולש"),
    ("מחיר 120 ₪", "₪ 120 ריחמ"),
    ("WE WANT משיח NOW", "WE WANT חישמ NOW"),
])
def test_visual_order(logical, shown):
    assert bidi.visual(logical) == shown


def test_levels_and_direction():
    assert bidi.is_rtl("  צהל abc") and not bidi.is_rtl("abc צהל") and not bidi.is_rtl("123")
    assert bidi.levels("אב cd") == [1, 1, 1, 2, 2]
    assert bidi.levels("ab גד") == [0, 0, 0, 1, 1]


def test_a_hebrew_poster_reads_the_same_with_and_without_raqm(monkeypatch):
    from PIL import ImageFont, features
    import numpy as np
    from funkbox.reel import captions
    from funkbox.stills import render
    if not features.check("raqm"):
        pytest.skip("needs libraqm for the reference render")
    doc = {"version": 1, "casual": True, "canvas": {"width": 600, "height": 300, "background": "#ffffff"},
           "brand": {"font_display": "fonts/Rubik-Bold.ttf", "font_caption": "fonts/Rubik-Bold.ttf"},
           "layers": [{"id": "t", "type": "text", "text": "צבאות ה׳ לישראל 2026", "size": 0.15, "x": 0.05, "y": 0.3,
                       "max_width": 0.9, "align": "center", "color": "#000000"}]}
    ref = render.render_document(doc).image
    captions._font.cache_clear()
    monkeypatch.setattr(captions, "_font", lambda p, px: ImageFont.truetype(p, px, layout_engine=ImageFont.Layout.BASIC))
    monkeypatch.setattr(bidi, "pillow_has_raqm", lambda: False)
    basic = render.render_document(doc).image
    d = np.abs(ref.astype(int) - basic.astype(int)).sum(-1)
    assert (d > 200).mean() < 0.03                   # same order; only kerning moves a few edges by a pixel


def test_without_raqm_posters_use_harfbuzz_outlines(monkeypatch):
    from funkbox.stills import render, typeset
    monkeypatch.setattr(bidi, "pillow_has_raqm", lambda: False)
    lay = {"id": "t", "type": "text", "text": "צהל"}
    assert typeset.use_outlines(lay)
    doc = {"version": 1, "casual": True, "canvas": {"width": 400, "height": 200, "background": "#ffffff"},
           "brand": {"font_display": "fonts/Rubik-Bold.ttf", "font_caption": "fonts/Rubik-Bold.ttf"},
           "layers": [dict(lay, size=0.3, x=0.1, y=0.2, max_width=0.8, color="#000000")]}
    r = render.render_document(doc)
    assert r.audit[0]["text_exact"] and "typography" not in r.audit[0]
    import numpy as np
    a = r.image.astype(int).sum(-1) < 300
    cols = np.nonzero(a.any(0))[0]
    top = np.array([np.argmax(a[:, c]) for c in cols])
    assert cols[np.argmin(top)] < (cols.min() + cols.max()) / 2          # the lamed (last letter) is at the left
