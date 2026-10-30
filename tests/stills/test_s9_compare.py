"""Compare a render with a reference: scores, worst regions, palette, sheet."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from funkbox.stills import compare


def poster(color=(220, 30, 60), shift=0, w=400, h=500, bar=(30, 40, 200)):
    im = Image.new("RGB", (w, h), (245, 240, 230))
    d = ImageDraw.Draw(im)
    d.rectangle((60 + shift, 60, 340 + shift, 300), fill=color)
    d.ellipse((150, 330, 250, 430), fill=(20, 20, 20))
    d.rectangle((0, 460, w, 500), fill=bar)
    return im


def test_identical_pictures_score_100():
    res = compare.compare(poster(), poster())
    assert res.scores["overall"] == pytest.approx(100, abs=0.2)
    assert all(c["score"] > 99 for c in res.cells) and all(p["delta_e"] < 1 for p in res.palette)
    assert "most different" not in res.summary()


def test_a_colour_change_is_found_where_it_is_and_called_colour():
    res = compare.compare(poster(), poster(bar=(240, 200, 30)))
    worst = res.cells[0]
    assert worst["row"] == 3 and worst["differs"] == "colour"
    assert res.scores["colour"] < 99 and res.scores["overall"] < 99
    assert any(p["color"] and p["delta_e"] > 12 for p in res.palette)            # the blue bar has no match
    assert "reference colours not matched" in res.summary()


def test_a_moved_shape_scores_lower_on_layout_than_a_small_shift():
    near = compare.compare(poster(shift=3), poster())
    far = compare.compare(poster(shift=50), poster())
    assert far.scores["layout"] < near.scores["layout"] and far.scores["overall"] < near.scores["overall"]
    assert far.cells[0]["differs"] == "shape" and far.cells[0]["row"] in (0, 1)


def test_the_reference_is_fitted_and_noted():
    ref = poster().resize((800, 1000))
    res = compare.compare(poster(), ref)
    assert res.scores["overall"] > 95 and not res.notes
    wide = Image.new("RGB", (1000, 500), (245, 240, 230))
    wide.paste(poster(), (300, 0))
    res = compare.compare(poster(), wide)
    assert res.notes and "cropped" in res.notes[0] and res.scores["overall"] > 90
    assert compare.compare(poster(), wide, "stretch").scores["overall"] < res.scores["overall"]


def test_sheet_and_report_are_written(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path))
    res = compare.compare(poster(), poster(shift=40))
    assert res.sheet.width > 2 * res.sheet.height
    paths = compare.write(res, tmp_path, "t")
    import json
    rep = json.loads(paths["report"].read_text())
    assert set(rep) == {"scores", "cells", "palette", "notes"} and len(rep["cells"]) == 16
    # the heat map (third panel) is red somewhere
    a = np.asarray(Image.open(paths["sheet"]))
    third = a[:, 2 * a.shape[1] // 3:]
    assert ((third[..., 0] > 200) & (third[..., 1] < 80)).sum() > 50
