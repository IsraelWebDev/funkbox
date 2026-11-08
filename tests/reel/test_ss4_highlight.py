"""SS-4: the word highlight (a pill behind the word the typewriter has reached)."""

import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from conftest import make_plain_reel
from funkbox import bidi
from funkbox.reel import captions, render
from funkbox.reel import schema as S

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "fonts"
W, H = 1080, 1920


def fonts():
    return captions.Fonts(FONTS / "Oswald-SemiBold.ttf", FONTS / "DMSerifDisplay-Regular.ttf", FONTS / "Rubik-Bold.ttf")


def layout(text="ONE TWO THREE", hl=("#00ff00", 0.3)):
    style = captions.Style(font="caption", size=0.05, box="primary", highlight=hl)
    return captions.layout_block("c", text, style, fonts(), S.Brand(), W, H, anchor="center", safe_area="reels")


def painted(sprite, rgb):
    """True if some pixel of the sprite is exactly this opaque colour (a solid pill fill, not antialiased text)."""
    return sprite is not None and bool(np.any(np.all(sprite.rgba == np.array([*rgb, 255]), axis=-1)))


def reel_dict(caption_highlight):
    cap = {"copy_id": "hook"}
    if caption_highlight is not None:
        cap["highlight"] = caption_highlight
    return {
        "version": 3,
        "copy": "copy.json",
        "shots": [{"id": "s1", "source": {"color": "#202020"}, "duration": 1.0, "caption": cap}],
    }


def test_parse_highlight_defaults_and_bounds():
    assert S.parse_highlight(None, "x") is None
    assert S.parse_highlight({}, "x") == ("secondary", 0.3)
    assert S.parse_highlight({"color": "#ff0000", "radius": 0.5}, "x") == ("#ff0000", 0.5)
    for bad in ({"colour": "primary"}, {"color": "blue"}, {"radius": 0.9}, {"radius": True}, "primary"):
        with pytest.raises(ValueError):
            S.parse_highlight(bad, "x")


def test_parse_style_accepts_a_highlight_and_defaults_to_none():
    assert captions.parse_style({"size": 0.05}).highlight is None
    assert captions.parse_style({"size": 0.05, "highlight": {"color": "text"}}).highlight == ("text", 0.3)


def test_a_valid_caption_highlight_parses_and_an_absent_one_is_none():
    reel = S.parse_reel(reel_dict({"color": "#ffd400"}))
    assert reel.shots[0].caption.highlight == ("#ffd400", 0.3)
    assert S.parse_reel(reel_dict(None)).shots[0].caption.highlight is None


def test_a_bad_caption_highlight_is_a_reel_error_naming_the_value():
    with pytest.raises(S.ReelError, match="must be primary"):
        S.parse_reel(reel_dict({"color": "blue"}))


def test_no_pill_before_the_first_word():
    lay = layout()
    assert captions.highlight_box(lay, captions.revealed_lines(lay, 0)) is None


def test_the_pill_covers_the_last_word_typed_and_moves_on():
    lay = layout()
    first = captions.highlight_box(lay, captions.revealed_lines(lay, 3))  # "ONE"
    later = captions.highlight_box(lay, captions.revealed_lines(lay, 5))  # "ONE T"
    assert first and later and later[0] > first[0]


def test_the_pill_is_the_word_and_never_reaches_the_spaces():
    lay = layout("ONE TWO")
    x0, _, x1, _ = captions.highlight_box(lay, captions.revealed_lines(lay, 3))  # "ONE"
    word_end = math.ceil(lay.line_x(0) + lay.font.getlength("ONE"))
    assert x0 >= lay.line_x(0) - 1
    assert x1 <= word_end, "the pill runs past the end of ONE, into the space before TWO"


def test_a_pill_on_a_later_line_sits_on_its_own_line():
    lay = layout("HELLO WORLD\nHI")
    x0, y0, x1, _ = captions.highlight_box(lay, captions.revealed_lines(lay, len(lay.text)))  # "HI"
    assert y0 == 1 * lay.line_h + lay.pad
    assert x0 >= lay.line_x(1) - 1
    assert x1 <= math.ceil(lay.line_x(1) + lay.font.getlength("HI"))


def test_a_trailing_space_does_not_move_or_widen_the_pill():
    lay = layout()
    assert captions.highlight_box(lay, captions.revealed_lines(lay, 4)) == captions.highlight_box(
        lay, captions.revealed_lines(lay, 3)
    )


def test_the_pill_stays_inside_the_layout_box_at_every_reveal_step():
    lay = layout()
    boxes = 0
    for n in range(1, len(lay.text) + 1):
        box = captions.highlight_box(lay, captions.revealed_lines(lay, n))
        if box is None:
            continue
        boxes += 1
        x0, y0, x1, y1 = box
        assert 0 <= x0 < x1 <= lay.box_w, n
        assert 0 <= y0 < y1 <= lay.box_h, n
    assert boxes > 0


def test_a_blank_paragraph_keeps_the_pill_on_the_last_word():
    lay = layout("HELLO\n\nWORLD")
    for n in range(1, len(lay.text) + 1):
        strings = captions.revealed_lines(lay, n)
        typed = [i for i, s in enumerate(strings) if s and s.strip()]
        if typed:  # some word is typed: the pill sits on the last typed word's own line
            box = captions.highlight_box(lay, strings)
            assert box is not None and box[1] == typed[-1] * lay.line_h + lay.pad, n


def test_right_to_left_lines_get_no_pill_and_never_ask_the_pillow_build(monkeypatch):
    lays = [layout("שלום עולם"), layout("HELLO שלום")]  # built first; the build check is armed after

    def no_build_check():
        raise AssertionError("the RTL check must not depend on the Pillow build")

    monkeypatch.setattr(bidi, "pillow_has_raqm", no_build_check)
    monkeypatch.setattr(captions, "pillow_has_raqm", no_build_check, raising=False)
    for lay in lays:
        for n in range(1, len(lay.text) + 1):
            assert captions.highlight_box(lay, captions.revealed_lines(lay, n)) is None, (lay.text, n)


def test_the_pill_is_painted_in_its_colour():
    lay = layout(hl=("#00ff00", 0.3))
    with_pill = captions.draw_block(lay, captions.revealed_lines(lay, 5))
    plain_lay = layout(hl=None)
    plain = captions.draw_block(plain_lay, captions.revealed_lines(plain_lay, 5))
    assert painted(with_pill, (0, 255, 0))
    assert not painted(plain, (0, 255, 0))


def test_the_default_pill_shows_on_the_default_box():
    lay = layout(hl=S.parse_highlight({}, "x"))  # the default: secondary, #1a1a1a
    assert painted(captions.draw_block(lay, captions.revealed_lines(lay, 5)), (26, 26, 26))


def record_boxes(monkeypatch):
    """Spy on highlight_box: the list gets (highlight the layout was drawn with, box returned)."""
    seen = []
    real = captions.highlight_box

    def spy(lay, strings):
        box = real(lay, strings)
        seen.append((lay.style.highlight, box))
        return box

    monkeypatch.setattr(captions, "highlight_box", spy)
    return seen


def record_renders(monkeypatch):
    """Spy on render_caption, which the renderer calls for every caption and hook title: (layout, sprite)."""
    seen = []
    real = captions.render_caption

    def spy(lay, *a, **k):
        spr = real(lay, *a, **k)
        seen.append((lay, spr))
        return spr

    monkeypatch.setattr(captions, "render_caption", spy)
    return seen


def render_caption_reel(demo, work, monkeypatch, name, *, caption_hl=None, style_hl=None, hook=False):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    if style_hl is not None:
        real_load = captions.load_style
        monkeypatch.setattr(captions, "load_style", lambda n: replace(real_load(n), highlight=style_hl))
    d = make_plain_reel(demo)
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    for sh in d["shots"]:
        sh["duration"] = 2.5
    cap = dict(next(sh for sh in d["shots"] if "caption" in sh)["caption"])
    if hook:  # the hook title is shot 1's caption, so move the caption onto shot 1
        for sh in d["shots"]:
            sh.pop("caption", None)
        d["shots"][0]["caption"] = cap
        d["hook"] = True
    if caption_hl is not None:  # every caption in the reel, not just the first
        for sh in d["shots"]:
            if "caption" in sh:
                sh["caption"]["highlight"] = caption_hl
    render.render_reel(d, name, base_dir=demo, out_dir=work, audit=True, with_audio=False)
    return work / f"{name}.text_audit.json"


def test_a_caption_highlight_is_painted_without_a_style_highlight(demo, work, gl, monkeypatch):
    renders = record_renders(monkeypatch)
    render_caption_reel(demo, work, monkeypatch, "cap_on", caption_hl={"color": "#00ff00"})
    assert any(painted(spr, (0, 255, 0)) for _, spr in renders)


def test_no_highlight_anywhere_never_asks_for_a_pill(demo, work, gl, monkeypatch):
    seen = record_boxes(monkeypatch)
    render_caption_reel(demo, work, monkeypatch, "cap_off")
    assert seen == []


def test_a_style_highlight_is_painted(demo, work, gl, monkeypatch):
    renders = record_renders(monkeypatch)
    render_caption_reel(demo, work, monkeypatch, "style_on", style_hl=("#00ff00", 0.3))
    assert any(painted(spr, (0, 255, 0)) for _, spr in renders)


def test_a_caption_highlight_overrides_the_style_highlight(demo, work, gl, monkeypatch):
    renders = record_renders(monkeypatch)
    render_caption_reel(demo, work, monkeypatch, "override", style_hl=("#ff0000", 0.3), caption_hl={"color": "#00ff00"})
    assert any(painted(spr, (0, 255, 0)) for _, spr in renders)
    assert not any(painted(spr, (255, 0, 0)) for _, spr in renders), "the style's red pill was painted"


def test_the_hook_title_paints_the_caption_highlight(demo, work, gl, monkeypatch):
    renders = record_renders(monkeypatch)
    render_caption_reel(demo, work, monkeypatch, "hook_hl", caption_hl={"color": "#ffd400"}, hook=True)
    # the hook title is the display font (hook_style); its sprite must carry the caption's pill colour
    assert any(lay.style.font == "display" and painted(spr, (255, 212, 0)) for lay, spr in renders)


def test_a_highlighted_caption_renders_and_its_audit_is_text_only(demo, work, gl, monkeypatch):
    audit = render_caption_reel(demo, work, monkeypatch, "highlit", caption_hl={"color": "#00ff00"})
    text = audit.read_text(encoding="utf-8")
    assert json.loads(text)["ok"]
    assert "highlight" not in text