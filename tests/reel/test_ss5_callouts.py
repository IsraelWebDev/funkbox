"""SS-5: callout tags (a label in a box, centred on a point, with an optional leader line)."""

from pathlib import Path

import numpy as np
import pytest

from conftest import make_plain_reel
from funkbox.reel import captions, render
from funkbox.reel import schema as S
from funkbox.reel.validate import validate

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "fonts"
W, H = 1080, 1920


def fonts():
    return captions.Fonts(FONTS / "Oswald-SemiBold.ttf", FONTS / "DMSerifDisplay-Regular.ttf", FONTS / "Rubik-Bold.ttf")


def label(text="ONE"):
    return captions.layout_block(
        "c", text, captions.load_style("callout"), fonts(), S.Brand(), W, H, anchor="center", safe_area="reels"
    )


def parse_dict(callouts, duration=2.0):
    return {
        "version": 3,
        "copy": "copy.json",
        "shots": [{"id": "s1", "source": {"color": "#202020"}, "duration": duration, "callouts": callouts}],
    }


# --- parsing -----------------------------------------------------------------


def test_a_callout_takes_its_defaults():
    reel = S.parse_reel(parse_dict([{"copy_id": "hook", "at": [0.5, 0.4]}]))
    co = reel.shots[0].callouts[0]
    assert (co.copy_id, co.at, co.line_to, co.start, co.end, co.style) == (
        "hook",
        (0.5, 0.4),
        None,
        0.0,
        None,
        "callout",
    )


def test_a_shot_without_callouts_has_none():
    d = parse_dict([])
    del d["shots"][0]["callouts"]
    assert S.parse_reel(d).shots[0].callouts == []


def test_a_line_to_and_an_end_are_read():
    co = (
        S.parse_reel(
            parse_dict([{"copy_id": "hook", "at": [0.3, 0.3], "line_to": [0.7, 0.8], "start": 0.5, "end": 1.5}])
        )
        .shots[0]
        .callouts[0]
    )
    assert co.line_to == (0.7, 0.8) and co.start == 0.5 and co.end == 1.5


@pytest.mark.parametrize(
    "bad, path",
    [
        ({"copy_id": "hook", "at": [1.2, 0.5]}, "callouts[0].at"),
        ({"copy_id": "hook", "at": [0.5]}, "callouts[0].at"),
        ({"copy_id": "hook", "at": [0.5, 0.5], "line_to": [0.5, -0.1]}, "callouts[0].line_to"),
        ({"copy_id": "hook", "at": [0.5, 0.5], "start": 1.0, "end": 0.5}, "callouts[0].end"),
        ({"copy_id": "hook", "at": [0.5, 0.5], "colour": "red"}, "callouts[0].colour"),
        ({"at": [0.5, 0.5]}, "callouts[0].copy_id"),
    ],
)
def test_a_bad_callout_is_a_reel_error_at_its_path(bad, path):
    with pytest.raises(S.ReelError, match=path.replace("[", r"\[").replace("]", r"\]")):
        S.parse_reel(parse_dict([bad]))


# --- placement ---------------------------------------------------------------


def test_the_label_is_centred_on_its_anchor():
    placed, inside = captions.place_callout(label(), (0.5, 0.5), W, H, "reels")
    assert inside
    assert abs(placed.x0 + placed.box_w / 2 - W / 2) <= 1
    assert abs(placed.y0 + placed.box_h / 2 - H / 2) <= 1


def test_an_anchor_that_pushes_the_label_out_of_the_side_margin_is_flagged_and_pulled_back():
    placed, inside = captions.place_callout(label("A longer label"), (0.01, 0.5), W, H, "reels")
    assert not inside
    assert placed.x0 >= captions.safe_box(W, H, "reels")[0] - 1


def test_an_anchor_in_the_top_band_is_flagged_and_pulled_into_the_band():
    placed, inside = captions.place_callout(label(), (0.5, 0.02), W, H, "reels")
    assert not inside
    assert placed.y0 >= captions.safe_box(W, H, "reels")[1] - 1


# --- validation --------------------------------------------------------------


def callout_reel(demo, *, start=0.5, end=1.5, at=(0.5, 0.5), line_to=None):
    d = make_plain_reel(demo)
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    for sh in d["shots"]:
        sh["duration"] = 2.5
    cid = next(sh for sh in d["shots"] if "caption" in sh)["caption"]["copy_id"]
    d["shots"][0].pop("caption", None)  # nothing else drawn on shot 1
    d["shots"][0]["source"] = {"color": "#202020"}
    d["shots"][0].pop("motion", None)  # a colour source takes no camera move
    co = {"copy_id": cid, "at": list(at), "start": start, "end": end}
    if line_to is not None:
        co["line_to"] = list(line_to)
    d["shots"][0]["callouts"] = [co]
    return d, cid


def test_a_centred_callout_validates(demo):
    d, _ = callout_reel(demo)
    assert not validate(d, base_dir=demo).errors


def test_an_unknown_copy_id_is_a_validation_error(demo):
    d, _ = callout_reel(demo)
    d["shots"][0]["callouts"][0]["copy_id"] = "no-such-copy"
    assert any("no-such-copy" in e for e in validate(d, base_dir=demo).errors)


def test_a_label_that_leaves_the_safe_area_is_a_validation_error(demo):
    d, _ = callout_reel(demo, at=(0.02, 0.5))
    assert any("safe area" in e for e in validate(d, base_dir=demo).errors)


# --- render ------------------------------------------------------------------


def renderer(demo, d):
    plan = render.prepare(d, demo)
    r = render.Renderer(
        plan.reel,
        plan.deck,
        plan.spans,
        width=plan.reel.settings.width,
        height=plan.reel.settings.height,
        depth_mode="off",
    )
    r.audit = {}
    return r, plan.reel.shots[0].id


def test_a_callout_is_drawn_only_inside_its_window(demo, gl):
    d, cid = callout_reel(demo, start=0.5, end=1.5)
    r, sid = renderer(demo, d)
    for i in range(25):  # t = 0.0 .. 2.4 s at 10 fps
        r.still(sid, i / 10)
    entries = [e for e in r.audit_report() if e["kind"] == "callout"]
    assert len(entries) == 1
    e = entries[0]
    assert e["copy_id"] == cid and e["text_exact"]
    assert (e["first_frame"], e["last_frame"]) == (5, 14)  # 0.5 s up to, not including, 1.5 s


def test_the_leader_line_is_drawn_from_the_label_to_line_to(demo, gl):
    d, _ = callout_reel(demo, start=0.5, end=1.5, at=(0.5, 0.3), line_to=(0.5, 0.9))
    r, sid = renderer(demo, d)
    x, y = 135, 336  # the middle of the frame, well below the label
    inside = r.still(sid, 1.0)
    outside = r.still(sid, 0.2)
    assert inside[y, x].mean() > 200, "the leader line is missing inside its window"
    assert outside[y, x].mean() < 100, "the leader line shows before its window"


def test_a_reel_without_callouts_has_no_callout_entries(demo, gl):
    d = make_plain_reel(demo)
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    for sh in d["shots"]:
        sh["duration"] = 2.5
    r, sid = renderer(demo, d)
    for i in range(10):
        r.still(sid, i / 10)
    assert not [e for e in r.audit_report() if e["kind"] == "callout"]


def test_a_callout_that_starts_after_the_shot_is_a_validation_error(demo):
    d, cid = callout_reel(demo)
    d["shots"][0]["callouts"] = [{"copy_id": cid, "at": [0.5, 0.5], "start": 3.0}]
    assert any("starts at 3.0s" in e for e in validate(d, base_dir=demo).errors)


def test_a_callout_that_ends_after_the_shot_is_a_validation_error(demo):
    d, _ = callout_reel(demo, end=3.0)
    assert any("ends at 3.0s" in e for e in validate(d, base_dir=demo).errors)


def test_the_landscape_band_pulls_a_top_anchor_into_its_band():
    placed, inside = captions.place_callout(label(), (0.5, 0.02), W, H, "landscape")
    assert not inside
    assert placed.y0 == pytest.approx(captions.LAND_TOP * H, abs=1)


def test_a_callout_past_an_auto_shot_is_a_validation_error(demo):
    d, cid = callout_reel(demo)
    d["shots"][0].pop("duration", None)  # an auto shot has its length only once the timeline is built
    d["shots"][0]["callouts"] = [{"copy_id": cid, "at": [0.5, 0.5], "start": 60.0}]
    assert any("starts at 60.0s" in e for e in validate(d, base_dir=demo).errors)
