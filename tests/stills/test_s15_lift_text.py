"""image_trace lift_text: lettering comes out of the traced artwork, its lines come back as boxes, and a starter
poster puts live, exact text on top."""

import builtins
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from funkbox.stills import render, trace


def emblem(tmp_path):
    """A red badge on white with a row of navy 'letters' on it and a slanted row of blue ones."""
    im = Image.new("RGB", (600, 400), "#ffffff")
    d = ImageDraw.Draw(im)
    d.rectangle((60, 40, 540, 360), fill="#ec334b")
    for i in range(6):  # a straight line of six letters
        x = 120 + i * 60
        d.rectangle((x, 80, x + 36, 130), fill="#1b224b")
    for i in range(5):  # a slanted line (about -27 degrees)
        x, y = 150 + i * 55, 300 - i * 28
        d.polygon([(x, y), (x + 30, y - 15), (x + 30, y + 15), (x, y + 30)], fill="#3e61ad")
    return im


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))


def test_lift_finds_lines_and_paints_them_out(tmp_path):
    src = emblem(tmp_path)
    res = trace.trace(
        src, palette=["#ffffff", "#ec334b", "#1b224b", "#3e61ad"], lift_text=["#1b224b", "#3e61ad"], measure=False
    )
    assert len(res.text_boxes) == 2
    top, slant = res.text_boxes  # top to bottom
    assert top["color"] == "#1b224b" and top["letters"] == 6 and abs(top["angle"]) < 3
    x, y, w, h = top["box"]
    assert 110 <= x <= 125 and 75 <= y <= 85 and 330 <= w <= 360 and 45 <= h <= 60
    assert slant["color"] == "#3e61ad" and slant["letters"] == 5 and -35 < slant["angle"] < -20
    used = {p["color"] for p in res.palette}
    assert "#1b224b" not in used and "#3e61ad" not in used  # all the lettering is gone from the artwork
    assert "#ec334b" in used


def test_within_limits_where_text_is_looked_for(tmp_path):
    src = emblem(tmp_path)
    res = trace.trace(
        src,
        palette=["#ffffff", "#ec334b", "#1b224b", "#3e61ad"],
        measure=False,
        lift_text=[{"color": "#1b224b", "within": [0, 0, 1, 0.5]}, {"color": "#3e61ad", "within": [0, 0, 1, 0.5]}],
    )
    assert [b["color"] for b in res.text_boxes] == ["#1b224b"]
    assert "#3e61ad" in {p["color"] for p in res.palette}  # the slanted row stays in the artwork


def test_big_shapes_are_not_text(tmp_path):
    src = emblem(tmp_path)
    res = trace.trace(src, palette=["#ffffff", "#ec334b", "#1b224b", "#3e61ad"], lift_text="auto", measure=False)
    assert "#ec334b" in {p["color"] for p in res.palette}  # the badge is bigger than any letter
    assert all(b["color"] != "#ec334b" for b in res.text_boxes)


def test_without_scipy_the_same_lines(tmp_path, monkeypatch):
    src = emblem(tmp_path)
    kw = dict(
        palette=["#ffffff", "#ec334b", "#1b224b", "#3e61ad"],
        lift_text=["#1b224b", "#3e61ad"],
        measure=False,
        detail=300,
    )
    want = trace.trace(src, **kw).text_boxes
    real = builtins.__import__

    def no_scipy(name, *a, **k):
        if name.startswith("scipy"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_scipy)
    got = trace.trace(src, **kw).text_boxes
    assert [(b["color"], b["letters"]) for b in got] == [(b["color"], b["letters"]) for b in want]
    for a, b in zip(got, want):
        assert np.abs(np.array(a["box"]) - np.array(b["box"])).max() <= 6


def test_starter_poster_puts_exact_text_on_top(tmp_path):
    src = emblem(tmp_path)
    res = trace.trace(
        src, palette=["#ffffff", "#ec334b", "#1b224b", "#3e61ad"], lift_text=["#1b224b", "#3e61ad"], measure=False
    )
    paths = trace.write(res, tmp_path / "out", "badge", texts=["HELLO!", "slanted"])
    meta = json.loads(paths["text"].read_text())
    assert meta["size"] == [600, 400] and len(meta["lines"]) == 2
    doc = json.loads(paths["poster"].read_text())
    assert [l["type"] for l in doc["layers"]] == ["svg", "text", "text"]
    assert doc["layers"][2]["skew_y"] < -20 and doc["layers"][1]["color"] == "#1b224b"
    r = render.render_document(doc, base_dir=paths["poster"].parent)
    assert [(a["text"], a["text_exact"]) for a in r.audit] == [("HELLO!", True), ("slanted", True)]
    with pytest.raises(ValueError, match="text lines were found"):
        trace.write(res, tmp_path / "out", "badge2", texts=["a", "b", "c"])


def test_bad_lift_arguments(tmp_path):
    src = emblem(tmp_path)
    for bad in (["navy"], [{"color": "#1b224b", "within": [0, 0, 1]}], []):
        with pytest.raises(ValueError, match="lift_text"):
            trace.trace(src, lift_text=bad, measure=False)
    with pytest.raises(ValueError, match="max_letter"):
        trace.trace(src, lift_text="auto", max_letter=0.9, measure=False)


def test_min_text_lets_an_emblem_keep_its_small_print():
    from funkbox.stills import schema

    base = {
        "version": 1,
        "casual": True,
        "canvas": {"width": 600, "height": 600, "background": "#ffffff"},
        "layers": [
            {
                "id": "t",
                "type": "text",
                "text": "small print",
                "size": 0.02,
                "x": 0.1,
                "y": 0.1,
                "max_width": 0.8,
                "wrap": False,
            }
        ],
    }
    r = render.render_document(base)
    assert any("size raised to the minimum (0.032" in w for w in r.warnings)
    small = dict(base, min_text=0.016)
    r2 = render.render_document(small)
    assert not any("raised" in w for w in r2.warnings) and r2.audit[0]["text_exact"]
    assert schema.check(dict(base, min_text=0.005))
