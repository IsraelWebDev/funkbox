"""SS-6 nit: a bad annotation start reports that start only, not a second 'end must be after start' error."""

from conftest import make_plain_reel
from funkbox.reel.validate import validate


def annotated(demo, an):
    d = make_plain_reel(demo)
    d["shots"][0].pop("caption", None)
    d["shots"][0]["source"] = {"color": "#202020"}
    d["shots"][0].pop("motion", None)
    d["shots"][0]["annotations"] = [an]
    return d


def test_bad_start_with_valid_end_reports_only_the_start(demo):
    rep = validate(annotated(demo, {"type": "x", "points": [[0.5, 0.5]], "start": "soon", "end": 0}), base_dir=demo)
    msgs = [e for e in rep.errors if "annotations" in e]
    assert any("start" in e for e in msgs), rep.text()
    assert not any("must be after start" in e for e in msgs), rep.text()


def test_end_before_a_valid_start_is_still_reported(demo):
    rep = validate(annotated(demo, {"type": "x", "points": [[0.5, 0.5]], "start": 1.0, "end": 0.5}), base_dir=demo)
    assert any("must be after start" in e for e in rep.errors), rep.text()
