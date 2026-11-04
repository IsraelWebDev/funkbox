import numpy as np
import pytest

from funkbox.reel import captions as C
from funkbox.reel import render
from funkbox.reel.brand import contrast
from funkbox.reel.schema import Brand
from funkbox.reel.validate import validate

W, H, FPS = 270, 480, 30


def test_parse_style_accepts_box_auto_and_rejects_auto_elsewhere():
    assert C.parse_style({"box": "auto"}).box == "auto"
    for k in ("color", "stroke_color", "accent"):
        with pytest.raises(ValueError, match="primary\\|secondary\\|text or #rrggbb"):
            C.parse_style({k: "auto"})
    with pytest.raises(ValueError, match="auto"):
        C.parse_style({"box": "nope"})


def test_colour_auto_is_the_brand_primary():
    brand = Brand(primary="#12ab34")
    assert C.colour("auto", brand) == C.hex_rgb("#12ab34") == C.hex_rgb(brand.primary)


def test_auto_box_picks_a_bluish_box_with_enough_contrast():
    brand = Brand()
    frame = np.full((120, 200, 3), (30, 60, 220), np.uint8)
    box, text = C.auto_box(frame, brand, (255, 255, 255))
    assert box[2] > box[0] and box[2] > box[1]
    assert contrast(box, (255, 255, 255)) >= 4.5
    assert text == (255, 255, 255)


def test_auto_box_keeps_a_bright_yellow_and_switches_the_text_to_dark():
    brand = Brand()
    frame = np.full((120, 200, 3), (250, 215, 60), np.uint8)
    box, text = C.auto_box(frame, brand, (255, 255, 255))
    assert box[0] > 200 and box[1] > 170  # still yellow, not darkened to olive
    assert text == (17, 17, 17) and contrast(box, text) >= 4.5


def test_auto_box_falls_back_to_primary_on_grey_black_and_white_frames():
    brand = Brand(primary="#c8102e")
    prim = C.hex_rgb(brand.primary)
    for col in ((128, 128, 128), (0, 0, 0), (250, 250, 250)):
        frame = np.full((100, 80, 3), col, np.uint8)
        assert C.auto_box(frame, brand, (255, 255, 255))[0] == prim


def test_auto_box_is_deterministic():
    brand = Brand()
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:300, 0:500]
    frame = np.stack([xx % 256, (xx + yy) % 256, yy % 256], -1).astype(np.uint8)
    frame = np.clip(frame.astype(np.int16) + rng.normal(0, 30, frame.shape), 0, 255).astype(np.uint8)
    a = C.auto_box(frame, brand, (255, 255, 255))
    b = C.auto_box(frame.copy(), brand, (255, 255, 255))
    assert a == b


def test_load_style_auto_box():
    style = C.load_style("auto-box")
    assert style.box == "auto"
    assert "auto-box" in C.list_styles()


def green_reel():
    return {
        "copy": "inputs/brand/copy.json",
        "settings": {"width": W, "height": H, "fps": FPS},
        "shots": [
            {
                "id": "c",
                "source": {"color": "#1d7a1d"},
                "duration": 1.0,
                "caption": {"copy_id": "tackle", "style": "auto-box", "reveal": "none", "anchor": "bottom"},
            }
        ],
        "end_card": {
            "template": "brand-split",
            "duration": 2.0,
            "copy": {"headline": "end_head", "sub": "end_sub", "url": "url"},
            "product": "inputs/brand/bottle.png",
        },
    }


def test_render_picks_a_green_box_from_a_solid_green_frame(demo, gl):
    rep = validate(green_reel(), demo)
    assert rep.ok, rep.errors
    r = render.Renderer(rep.reel, rep.deck, rep.spans, width=W, height=H, depth_mode="off")
    try:
        span = r.span_for_shot("c")
        lay = r.caption_layout(span)
        assert lay.style.box.startswith("#") and lay.style.box != "auto"
        box_rgb = C.hex_rgb(lay.style.box)
        assert box_rgb[1] > box_rgb[0] and box_rgb[1] > box_rgb[2]
        fr = r.shot_frame(span, FPS // 2)  # caption fully revealed
        px = fr[lay.y0 + 1, lay.x0 + 1]  # inside the box padding
        assert int(px[1]) > int(px[0]) and int(px[1]) > int(px[2])
    finally:
        r.release()
