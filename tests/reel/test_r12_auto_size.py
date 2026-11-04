"""R12: caption styles may set "size": "auto" and "cps": "auto" (auto-box uses both). Fixed numbers stay
the default for every other style, so reels that do not opt in render exactly as before."""

from pathlib import Path

import numpy as np
import pytest

from funkbox.reel import captions as C
from funkbox.reel.schema import Brand

W, H = 540, 960


def fonts():
    return C.Fonts(Path("fonts/Oswald-SemiBold.ttf").resolve(), Path("fonts/DMSerifDisplay-Regular.ttf").resolve())


def auto_style():
    return C.parse_style({"size": "auto", "cps": "auto"})


def test_parse_style_accepts_auto_only_for_size_and_cps():
    assert C.parse_style({"size": "auto"}).size == "auto"
    assert C.parse_style({"cps": "auto"}).cps == "auto"
    assert C.parse_style({"size": 0.05}).size == 0.05
    assert C.parse_style({"cps": 28}).cps == 28
    for bad in ("AUTO", "none", "0.05", "28", None, [0.05]):
        with pytest.raises(ValueError, match="size"):
            C.parse_style({"size": bad})
    for bad in ("AUTO", "fast", "28", None, [28]):
        with pytest.raises(ValueError, match="cps"):
            C.parse_style({"cps": bad})


def test_auto_box_template_uses_auto_size_and_cps():
    st = C.load_style("auto-box")
    assert st.size == "auto" and st.cps == "auto"


def test_auto_size_follows_the_line_length_and_never_undercuts_min_size():
    st = auto_style()
    s10 = C.effective_size(st, "0123456789")  # 10 chars -> the big end
    s45 = C.effective_size(st, "X" * 45)  # 45 chars -> the small end
    assert s10 == 0.062 and s45 == 0.046
    assert 0.046 <= s45 < s10 <= 0.062
    mid = C.effective_size(st, "X" * 27)  # 27 chars: halfway between 14 and 40
    assert mid == pytest.approx((0.062 + 0.046) / 2)
    lay_short = C.layout_block("t", "0123456789", st, fonts(), Brand(), W, H)
    lay_long = C.layout_block("t", "word " * 8 + "words", st, fonts(), Brand(), W, H)  # 45 chars, wrappable
    assert lay_short.px > lay_long.px
    min_px = int(np.ceil(C.MIN_SIZE * H))
    assert lay_short.px >= min_px and lay_long.px >= min_px
    assert (
        0.046 * H - 1 <= lay_long.px <= 0.062 * H + 1 and 0.046 * H - 1 <= lay_short.px <= 0.062 * H + 1
    )  # px is rounded


def test_auto_cps_finishes_in_about_55_percent_of_the_shot():
    st = auto_style()
    text = "abcde fghij klmno pqrst uvwxyz"  # 30 chars with spaces so it can wrap
    assert len(text) == 30
    lay = C.layout_block("t", text, st, fonts(), Brand(), W, H)
    cps = C.effective_cps(st, text, 2.0)  # 30 chars on a 2 s shot
    assert cps == pytest.approx(30 / (0.55 * 2.0))
    assert 14 <= cps <= 40
    assert C.reveal_seconds(lay, "typewriter", span=2.0) == pytest.approx(1.1)
    assert C.reveal_count(lay, "typewriter", 1.1, span=2.0) == 30
    assert C.reveal_count(lay, "typewriter", 1.09, span=2.0) < 30
    # the 14..40 clamp
    assert C.effective_cps(st, "X" * 200, 1.0) == 40.0
    assert C.effective_cps(st, "HI", 10.0) == 14.0
    # no shot duration known -> the old fixed 28
    assert C.effective_cps(st, text, None) == 28.0
    assert C.reveal_seconds(lay, "typewriter") == pytest.approx(30 / 28.0)
    assert C.reveal_seconds(lay, "typewriter", None, None) == pytest.approx(30 / 28.0)


def test_fixed_styles_ignore_auto_and_the_span():
    st = C.load_style("red-box")  # numeric size and cps: unchanged behaviour
    assert st.size != "auto" and st.cps != "auto"
    text = "TACKLE CARPET CLEANING WITH GLOWMOP."
    assert C.effective_size(st, text) == st.size
    assert C.effective_cps(st, text, 2.0) == st.cps == 28.0
    assert C.effective_cps(st, text, None) == 28.0
    lay = C.layout_block("t", text, st, fonts(), Brand(), W, H)
    assert lay.px == max(int(np.ceil(C.MIN_SIZE * H)), int(round(st.size * H)))
    for t in (0.0, 0.3, 0.9, 1.7, 3.0):  # a span must not change a fixed-cps reveal
        a = C.render_caption(lay, "typewriter", t)
        b = C.render_caption(lay, "typewriter", t, None, 2.0)
        assert (a is None) == (b is None)
        if a is not None:
            assert a.n == b.n and (a.x, a.y) == (b.x, b.y) and np.array_equal(a.rgba, b.rgba)
