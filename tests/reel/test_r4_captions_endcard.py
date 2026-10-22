import json
import re

import numpy as np
import pytest
from PIL import Image

from funkbox.reel import captions as C
from funkbox.reel import render
from funkbox.reel.validate import validate

W, H, FPS = 540, 960, 30


def cap_reel(copy_id="tackle", reveal="typewriter", style="red-box", anchor="bottom", duration=3.0, deck_extra=None):
    return {"copy": "inputs/brand/copy.json", "settings": {"width": W, "height": H, "fps": FPS},
            "shots": [{"id": "c", "source": {"color": "#204060"}, "duration": duration,
                       "caption": {"copy_id": copy_id, "style": style, "reveal": reveal, "anchor": anchor}}],
            "end_card": {"template": "brand-split", "duration": 5.0,
                         "copy": {"headline": "end_head", "sub": "end_sub", "url": "url"},
                         "product": "inputs/brand/bottle.png"}}


def make_renderer(demo, reel):
    rep = validate(reel, demo)
    assert rep.ok, rep.errors
    return render.Renderer(rep.reel, rep.deck, rep.spans, width=W, height=H, depth_mode="off")


@pytest.mark.parametrize("copy_id", ["tackle", "hook", "care"])
def test_typewriter_frame_k_is_exactly_the_text_prefix(demo, gl, copy_id):
    r = make_renderer(demo, cap_reel(copy_id))
    try:
        span = r.span_for_shot("c")
        lay = r.caption_layout(span)
        text = lay.text
        cps = lay.style.cps
        for k in range(0, 60):
            t = k / FPS - C.CAPTION_DELAY
            if t < lay.style.scale_in:               # during the 0.15 s box scale-in the layer is animated
                continue
            n = min(len(text), int(np.floor(t * cps)))
            spr = C.render_caption(lay, "typewriter", t)
            # independent definition of "the first n characters on the final layout"
            strings = [text[s:min(e, n)].rstrip() if n > s else None for s, e in lay.lines]
            direct = C.draw_block(lay, strings)
            assert (spr is None) == (direct is None)
            if spr is None:
                continue
            a, b = spr.layer(W, H), direct.layer(W, H)
            assert np.array_equal(a, b), f"frame {k}: layer differs from direct prefix render"
            assert lay.reconstruct(spr.drawn) == text[:n].rstrip()      # drawn strings ARE the prefix
            # and the full pipeline composites exactly that sprite over the shot's source
            fr = r.shot_frame(span, k)
            base = np.full((H, W, 3), (0x20, 0x40, 0x60), np.uint8)
            assert np.array_equal(fr, spr.over(base))
    finally:
        r.release()


def test_typewriter_reveals_one_character_at_a_time_and_box_grows(demo, gl):
    r = make_renderer(demo, cap_reel("tackle"))
    try:
        lay = r.caption_layout(r.span_for_shot("c"))
        widths = []
        for n in range(1, len(lay.text) + 1):
            strings = [lay.text[s:min(e, n)].rstrip() if n > s else None for s, e in lay.lines]
            spr = C.draw_block(lay, strings)
            widths.append(spr.rgba.shape[1])
        assert widths == sorted(widths) and widths[-1] > widths[2]
        full = C.draw_block(lay, [lay.text[s:e] for s, e in lay.lines])
        assert full.rgba.shape[0] >= lay.box_h and full.rgba.shape[1] >= lay.box_w
    finally:
        r.release()


@pytest.mark.parametrize("text", [
    "TACKLE CARPET CLEANING WITH GLOWMOP.",
    "GIVE YOUR CARPET THE ATTENTION IT NEEDS, EVERY SINGLE DAY OF THE WEEK.",
    "LOOK CLOSER.\nTIME FOR A CLEAN?",
    "A B C D E F G H I J K L M N O P Q R S T U V W X Y Z",
    "Pneumonoultramicroscopic dust.",
])
@pytest.mark.parametrize("width", [540, 720, 1080])
def test_wrapping_never_splits_words(text, width):
    fonts = C.Fonts(__import__("pathlib").Path("fonts/Oswald-SemiBold.ttf").resolve(),
                    __import__("pathlib").Path("fonts/DMSerifDisplay-Regular.ttf").resolve())
    from funkbox.reel.schema import Brand
    lay = C.layout_block("t", text, C.load_style("red-box"), fonts, Brand(), width, width * 16 // 9)
    words = [text[s:e].split() for s, e in lay.lines]
    assert [w for ws in words for w in ws] == text.split()               # every word intact, in order
    for (s, e) in lay.lines:                                              # lines are contiguous slices of the text
        assert text[s:e] == text[s:e].strip()
    assert all(lay.font.getlength(text[s:e]) <= lay.style.max_width * width - 2 * lay.pad + 1e-6 for s, e in lay.lines)


def test_no_hyphenation_added():
    from pathlib import Path

    from funkbox.reel.schema import Brand
    fonts = C.Fonts(Path("fonts/Oswald-SemiBold.ttf").resolve(), Path("fonts/DMSerifDisplay-Regular.ttf").resolve())
    text = "EXTRAORDINARILY LONG WORDS WRAP ONLY ON SPACES"
    lay = C.layout_block("t", text, C.load_style("red-box"), fonts, Brand(), 540, 960)
    assert "".join(text[s:e] for s, e in lay.lines).replace(" ", "") == text.replace(" ", "")


def test_text_that_cannot_fit_is_a_validator_error_naming_the_copy_id(demo, work):
    import shutil
    d = work
    shutil.copytree(demo / "inputs", d / "inputs")
    deck = json.loads((d / "inputs/brand/copy.json").read_text())
    deck["lines"]["toolong"] = {"text": "SUPERCALIFRAGILISTICEXPIALIDOCIOUSNESS OF " + "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW"}
    (d / "inputs/brand/copy.json").write_text(json.dumps(deck))
    reel = cap_reel("toolong")
    rep = validate(reel, d)
    assert not rep.ok
    assert any("toolong" in e and "minimum size" in e and "shots[0] (c)" in e for e in rep.errors), rep.errors


def test_min_size_is_never_undercut():
    from pathlib import Path

    from funkbox.reel.schema import Brand
    fonts = C.Fonts(Path("fonts/Oswald-SemiBold.ttf").resolve(), Path("fonts/DMSerifDisplay-Regular.ttf").resolve())
    lay = C.layout_block("t", "GIVE YOUR CARPET THE ATTENTION IT NEEDS. " * 3, C.load_style("red-box"), fonts,
                         Brand(), 540, 960)
    assert lay.px >= int(np.ceil(C.MIN_SIZE * 960))


def test_styles_cannot_transform_case_or_hold_unknown_keys():
    with pytest.raises(ValueError, match="case"):
        C.parse_style({"text_transform": "uppercase"})
    with pytest.raises(ValueError, match="unknown keys"):
        C.parse_style({"label": "hello"})
    for name in C.list_styles():
        C.load_style(name)
    assert {"red-box", "lower-third", "bold-pop"} <= set(C.list_styles())


@pytest.mark.parametrize("reveal", ["none", "words", "pop", "typewriter"])
@pytest.mark.parametrize("style", ["red-box", "lower-third", "bold-pop"])
def test_every_style_and_reveal_ends_with_the_exact_text(demo, gl, style, reveal):
    r = make_renderer(demo, cap_reel("hook", reveal, style, anchor="center"))
    try:
        span = r.span_for_shot("c")
        lay = r.caption_layout(span)
        spr = C.render_caption(lay, reveal, 2.5, 0.8)
        assert spr is not None and lay.reconstruct(spr.drawn) == lay.text
        first = C.render_caption(lay, reveal, 0.0, 0.8)
        if reveal in ("typewriter", "words"):
            assert first is None or lay.reconstruct(first.drawn) != lay.text
    finally:
        r.release()


def test_safe_area_anchors(demo, gl):
    r = make_renderer(demo, cap_reel("tackle"))
    try:
        for anchor in ("top", "center", "bottom"):
            c = cap_reel("tackle", anchor=anchor)
            r2 = make_renderer(demo, c)
            lay = r2.caption_layout(r2.span_for_shot("c"))
            assert lay.y0 >= 0.14 * H - 1 and lay.y0 + lay.box_h <= H - 0.22 * H + 1
            r2.release()
    finally:
        r.release()


def test_brand_split_end_card_renders_every_slot(demo, gl, work):
    r = make_renderer(demo, cap_reel())
    try:
        span = r.spans[-1]
        assert span.kind == "end"
        drawn = {}
        r.audit = {}
        last = span.end_frame - span.start_frame - 1
        fr = r.shot_frame(span, 0)
        for k in range(0, last + 1, 3):
            fr = r.shot_frame(span, k)
        slots = {(e["slot"], e["instance"]): e for e in r.audit_report()}
        assert {k[0] for k in slots} == {"headline", "sub", "url"}
        assert ("url", "top") in slots and ("url", "bottom") in slots
        for key, e in slots.items():
            assert e["text"] == r.deck.text(e["copy_id"]) and e["complete_frame"] is not None, key
        Image.fromarray(fr).save(work / "endcard.png")
        # product and panel are really there (right third has the bottle, left two thirds the brand colour)
        assert tuple(fr[H // 2, 5]) == (0xC8, 0x10, 0x2E) or tuple(fr[H // 2, 5]) == tuple(fr[H // 2 + 40, 5])
        assert fr[int(H * 0.5), int(W * 0.83)].tolist() != r.endcard().static[int(H * 0.5), int(W * 0.83)].tolist()
    finally:
        r.release()


def test_end_card_slot_mapping_errors(demo):
    reel = cap_reel()
    del reel["end_card"]["copy"]["sub"]
    rep = validate(reel, demo)
    assert any("needs a copy id for slot 'sub'" in e for e in rep.errors)
    reel = cap_reel()
    reel["end_card"]["copy"]["bogus"] = "url"
    assert any("no slot 'bogus'" in e for e in validate(reel, demo).errors)
    reel = cap_reel()
    reel["end_card"]["template"] = "centered-logo"
    reel["end_card"]["copy"] = {"headline": "end_head", "url": "url"}
    assert any("needs brand.logo" in e for e in validate(reel, demo).errors)


def test_centered_logo_template_renders(demo, gl, work):
    reel = cap_reel()
    reel["brand"] = {"logo": "inputs/brand/logo.png"}
    reel["end_card"] = {"template": "centered-logo", "duration": 4.0, "copy": {"headline": "end_head", "url": "url"}}
    r = make_renderer(demo, reel)
    try:
        span = r.spans[-1]
        r.audit = {}
        fr = r.shot_frame(span, 100)
        Image.fromarray(fr).save(work / "centered.png")
        assert {e["slot"] for e in r.audit_report()} == {"headline", "url"}
    finally:
        r.release()


def test_caption_templates_hold_no_free_text():
    from funkbox.reel.schema import scan_forbidden
    from pathlib import Path
    for p in list((C.TEMPLATES / "captions").glob("*.json")) + list((C.TEMPLATES / "endcards").glob("*.json")):
        assert scan_forbidden(json.loads(p.read_text())) == [], p


def test_logo_clean_template_white_logo_and_url_once(demo, gl, work):
    reel = cap_reel()          # keeps end_card.product: a template without a "product" block must ignore it
    reel["brand"] = {"logo": "inputs/brand/logo.png"}
    reel["end_card"] = {"template": "logo-clean", "duration": 4.0, "product": "inputs/brand/bottle.png",
                        "copy": {"headline": "end_head", "sub": "end_sub", "url": "url"}}
    r = make_renderer(demo, reel)
    try:
        span = r.spans[-1]
        r.audit = {}
        for k in range(0, span.end_frame - span.start_frame, 3):
            fr = r.shot_frame(span, k)
        assert {(e["slot"], e["instance"]) for e in r.audit_report()} == {("headline", "main"), ("sub", "main"), ("url", "main")}
        ec = r.endcard()
        assert tuple(ec.static[2, 2]) == (255, 255, 255) and tuple(ec.static[H - 3, W - 3]) == (255, 255, 255)
        arr, x, y = ec.logo
        assert x == (W - arr.shape[1]) // 2 and y == round(0.30 * H)
        Image.fromarray(fr).save(work / "logo-clean.png")
    finally:
        r.release()
