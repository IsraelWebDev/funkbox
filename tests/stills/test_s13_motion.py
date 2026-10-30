"""Animated posters: tracks, entrance effects, every frame audited, the last frame is the poster."""
import copy
import json

import numpy as np
import pytest

from funkbox.stills import motion, render, schema

BRAND = {"font_display": "fonts/Rubik-Bold.ttf", "font_caption": "fonts/Oswald-SemiBold.ttf"}


def doc(m=None):
    d = {"version": 1, "canvas": {"width": 320, "height": 240, "background": "#ffffff"}, "casual": True,
         "brand": BRAND, "layers": [
             {"id": "bg", "type": "gradient", "stops": ["#202040", "#402020"]},
             {"id": "dot", "type": "shape", "kind": "ellipse", "points": [[0.4, 0.3], [0.6, 0.57]], "fill": "#ffcc00"},
             {"id": "t", "type": "text", "text": "MOVING, EXACT", "size": 0.1, "x": 0.1, "y": 0.7, "max_width": 0.8,
              "align": "center", "color": "#ffffff"}]}
    if m is not None:
        d["motion"] = m
    return d


def test_keyframes_ease_and_interpolate_colours_and_lists():
    keys = [[0, 0.0], [1, 10.0, "linear"], [2, 0.0, "out"]]
    assert motion.value_at(keys, 0.5) == pytest.approx(5) and motion.value_at(keys, 3) == 0
    assert motion.value_at(keys, 1.5) < 5                                    # ease-out falls fast first
    assert motion.value_at([[0, "#000000"], [1, "#ff8000"]], 0.5) == "#804000"
    assert motion.value_at([[0, [0, 0]], [1, [2, 4]]], 0.5) == [1, 2]


def test_motion_validation_names_the_problem():
    bad = doc({"seconds": 99, "enter": [{"layer": "nope", "effect": "fade"}, {"layer": "bg", "effect": "rise"}],
               "tracks": {"layers.t.size": [[2, 0.1], [1, 0.2]], "layers.bg.dx": [[0, 0]], "layers.zz.x": [[0, 1]],
                          "layers.t.y": [[0, 0.5, "bounce"]]}})
    errs = " | ".join(schema.check(bad))
    for frag in ("motion.seconds", "'nope' is not a layer id", "can only fade", "must rise",
                 "dx, dy and scale move", "no layer with id 'zz'", "ease is one of"):
        assert frag in errs, frag


def test_entrances_start_hidden_and_end_on_the_poster():
    d = doc({"seconds": 2, "enter": [{"layer": "dot", "effect": "pop", "at": 0.5, "dur": 0.5},
                                     {"layer": "t", "effect": "rise", "at": 1.0}]})
    tracks = motion._expand(d)
    f0 = motion.frame_doc(d, tracks, 0.0, 320, 240)
    assert f0["layers"][1]["opacity"] == 0 and f0["layers"][2]["opacity"] == 0
    assert f0["layers"][2]["y"] == pytest.approx(0.76)
    pts = np.array(f0["layers"][1]["points"])
    assert (pts[1] - pts[0]) == pytest.approx(np.array([0.2, 0.27]) * 0.6)    # popped from 60 % about its centre
    end = motion.frame_doc(d, tracks, 2.0, 320, 240)
    base = copy.deepcopy(d)
    base.pop("motion")
    def rounded(x):
        return json.loads(json.dumps(x), parse_float=lambda f: round(float(f), 9))
    for lay in end["layers"]:
        if lay.get("opacity") == 1.0:
            del lay["opacity"]                                              # faded in to the default
    assert rounded(end["layers"]) == rounded(base["layers"])


def test_layer_cache_gives_the_same_pixels():
    cache = {}
    a = render.render_document(doc(), cache=cache).image
    b = render.render_document(doc(), cache=cache)
    assert np.array_equal(a, b.image) and len(cache) == 3 and b.audit[0]["text_exact"]


def test_animate_writes_a_video_the_poster_and_the_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path))
    d = doc({"seconds": 1, "fps": 8, "enter": [{"layer": "t", "effect": "slide-left", "at": 0.2, "dur": 0.5}],
             "tracks": {"layers.dot.dy": [[0, -0.2], [1, 0, "inout"]], "layers.t.letter_spacing": [[0, 0.3], [1, 0.0001]]}})
    res = motion.animate(d, tmp_path, "m")
    assert res.frames == 8 and res.all_exact and res.texts == ["MOVING, EXACT"]
    import imageio_ffmpeg
    n, secs = imageio_ffmpeg.count_frames_and_secs(str(res.paths["mp4"]))
    assert n == 8 and secs == pytest.approx(1.0, abs=0.15)
    still = render.render_document(doc()).image
    from PIL import Image
    assert np.array_equal(np.asarray(Image.open(res.paths["png"]).convert("RGB")), still)
    au = json.loads(res.paths["audit"].read_text())
    assert au["texts"] == [{"text": "MOVING, EXACT", "exact_every_frame": True}]
    assert res.paths["frames"].exists()
