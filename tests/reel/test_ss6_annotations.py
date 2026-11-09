"""SS-6: annotation layer (arrows, dashed guides, X and check stickers, with a draw-on)."""

import numpy as np
import pytest

from conftest import make_plain_reel
from funkbox.reel import render
from funkbox.reel import schema as S
from funkbox.reel.validate import validate


W, H = 270, 480


def parse_dict(annotations, duration=2.0):
    return {
        "version": 3,
        "copy": "copy.json",
        "shots": [{"id": "s1", "source": {"color": "#202020"}, "duration": duration, "annotations": annotations}],
    }


def annotated_reel(demo, annotations, duration=2.5):
    d = make_plain_reel(demo)
    d["settings"] = {"width": W, "height": H, "fps": 10, "safe_area": "reels"}
    for sh in d["shots"]:
        sh["duration"] = duration
    d["shots"][0].pop("caption", None)  # nothing else drawn on shot 1
    d["shots"][0]["source"] = {"color": "#202020"}
    d["shots"][0].pop("motion", None)  # a colour source takes no camera move
    d["shots"][0]["annotations"] = annotations
    return d


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


def is_red(px):
    return int(px[0]) > 150 and int(px[1]) < 90  # the default brand primary, #c8102e, on a dark frame


def red_at(fr, x):
    return any(is_red(fr[y, x]) for y in range(H // 2 - 4, H // 2 + 5))


def red_count(fr):
    return int(((fr[..., 0] > 150) & (fr[..., 1] < 90)).sum())


# --- parsing -----------------------------------------------------------------


def test_an_arrow_takes_its_defaults():
    reel = S.parse_reel(parse_dict([{"type": "arrow", "points": [[0.2, 0.5], [0.8, 0.5]]}]))
    an = reel.shots[0].annotations[0]
    assert (an.type, an.points, an.start, an.end, an.draw) == ("arrow", [(0.2, 0.5), (0.8, 0.5)], 0.0, None, True)


def test_a_shot_without_annotations_has_none():
    d = parse_dict([])
    del d["shots"][0]["annotations"]
    assert S.parse_reel(d).shots[0].annotations == []


@pytest.mark.parametrize(
    "bad, path",
    [
        ({"type": "star", "points": [[0.1, 0.1]]}, "annotations[0].type"),
        ({"points": [[0.1, 0.1]]}, "annotations[0].type"),
        ({"type": "arrow", "points": [[0.1, 0.1]]}, "annotations[0].points"),
        ({"type": "x", "points": [[0.1, 0.1], [0.2, 0.2]]}, "annotations[0].points"),
        ({"type": "dashed", "points": [[0.1, 0.1]]}, "annotations[0].points"),
        ({"type": "check", "points": [[1.5, 0.1]]}, "annotations[0].points[0]"),
        ({"type": "arrow", "points": [[0.1, 0.1], [0.9, 0.9]], "colour": "red"}, "annotations[0].colour"),
        ({"type": "arrow", "points": [[0.1, 0.1], [0.9, 0.9]], "start": 1.0, "end": 0.5}, "annotations[0].end"),
    ],
)
def test_a_bad_annotation_is_a_reel_error_at_its_path(bad, path):
    with pytest.raises(S.ReelError, match=path.replace("[", r"\[").replace("]", r"\]")):
        S.parse_reel(parse_dict([bad]))


# --- geometry and timing -----------------------------------------------------


def test_progress_rises_from_0_to_1_and_never_falls():
    ts = [i / 100 for i in range(0, 300)]
    ps = [render._progress(t, 0.5, True) for t in ts]
    assert ps == sorted(ps)
    assert render._progress(0.5, 0.5, True) == 0.0
    assert render._progress(0.5 + render.ANNOTATION_DRAW_S, 0.5, True) == 1.0


def test_a_shape_without_draw_on_is_whole_from_its_start():
    assert render._progress(0.49, 0.5, False) == 0.0
    assert render._progress(0.5, 0.5, False) == 1.0


def test_partial_cuts_a_polyline_by_length():
    poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]  # 20 px long
    assert render._partial(poly, 0.25) == [(0.0, 0.0), (5.0, 0.0)]
    assert render._partial(poly, 0.75) == [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)]
    assert render._partial(poly, 1.0) == poly


# --- validation --------------------------------------------------------------


def test_a_centred_annotation_validates(demo):
    d = annotated_reel(demo, [{"type": "x", "points": [[0.5, 0.5]]}])
    assert not validate(d, base_dir=demo).errors


def test_an_annotation_that_starts_after_the_shot_is_a_validation_error(demo):
    d = annotated_reel(demo, [{"type": "arrow", "points": [[0.2, 0.5], [0.8, 0.5]], "start": 3.0}])
    assert any("starts at 3.0s" in e for e in validate(d, base_dir=demo).errors)


def test_an_annotation_that_ends_after_the_shot_is_a_validation_error(demo):
    d = annotated_reel(demo, [{"type": "arrow", "points": [[0.2, 0.5], [0.8, 0.5]], "end": 3.0}])
    assert any("ends at 3.0s" in e for e in validate(d, base_dir=demo).errors)


# --- render ------------------------------------------------------------------


def test_an_arrow_is_drawn_inside_its_window_only(demo, gl):
    d = annotated_reel(
        demo, [{"type": "arrow", "points": [[0.2, 0.5], [0.8, 0.5]], "start": 0.5, "end": 1.5, "draw": False}]
    )
    r, sid = renderer(demo, d)
    assert not red_at(r.still(sid, 0.2), W // 2), "the arrow shows before its window"
    assert red_at(r.still(sid, 1.0), W // 2), "the arrow is missing inside its window"
    assert not red_at(r.still(sid, 1.5), W // 2), "the arrow shows after its window"


def test_an_arrow_draws_on_from_its_tail(demo, gl):
    d = annotated_reel(demo, [{"type": "arrow", "points": [[0.2, 0.5], [0.8, 0.5]], "start": 0.5}])
    r, sid = renderer(demo, d)
    half = r.still(sid, 0.7)  # half drawn
    done = r.still(sid, 0.9)
    assert red_at(half, int(0.3 * W)) and not red_at(half, int(0.7 * W))
    assert red_at(done, int(0.7 * W))


@pytest.mark.parametrize(
    "ann",
    [
        {"type": "arrow", "points": [[0.2, 0.5], [0.8, 0.5]], "start": 0.5},
        {"type": "dashed", "points": [[0.1, 0.5], [0.5, 0.45], [0.9, 0.5]], "start": 0.5},
    ],
)
def test_the_drawn_area_only_grows_during_the_draw_on(demo, gl, ann):
    d = annotated_reel(demo, [ann])
    r, sid = renderer(demo, d)
    counts = [red_count(r.still(sid, t)) for t in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1)]
    assert counts == sorted(counts) and counts[-1] > counts[0]


def test_the_dashes_only_grow_as_the_line_is_drawn():
    poly = [(10.0, 50.0), (60.0, 52.0), (120.0, 40.0), (200.0, 60.0)]
    prev = []
    for k in range(0, 1001):
        pieces = render._dash_pieces(poly, W, k / 1000)
        assert pieces[: len(prev)] == prev, f"a dash moved or vanished at progress {k / 1000}"
        prev = pieces
    assert len(prev) > 0


def test_a_renderer_gives_the_same_frame_whatever_it_rendered_before(demo, gl):
    d = annotated_reel(demo, [{"type": "dashed", "points": [[0.1, 0.9], [0.5, 0.6], [0.9, 0.9]], "start": 0.5}])
    r, sid = renderer(demo, d)
    first = r.still(sid, 0.8)
    r.still(sid, 1.2)
    r.still(sid, 0.6)
    assert np.array_equal(first, r.still(sid, 0.8))


def test_a_dashed_line_has_gaps(demo, gl):
    d = annotated_reel(demo, [{"type": "dashed", "points": [[0.1, 0.5], [0.9, 0.5]], "draw": False}])
    r, sid = renderer(demo, d)
    fr = r.still(sid, 0.5)
    row = [red_at(fr, x) for x in range(int(0.15 * W), int(0.85 * W))]
    assert any(row) and not all(row)


def test_x_and_check_stickers_draw_at_their_centre(demo, gl):
    d = annotated_reel(
        demo,
        [
            {"type": "x", "points": [[0.25, 0.5]], "draw": False},
            {"type": "check", "points": [[0.75, 0.5]], "draw": False},
        ],
    )
    r, sid = renderer(demo, d)
    fr = r.still(sid, 0.5)
    x_box = fr[H // 2 - 20 : H // 2 + 20, int(0.25 * W) - 20 : int(0.25 * W) + 20]
    c_box = fr[H // 2 - 20 : H // 2 + 20, int(0.75 * W) - 20 : int(0.75 * W) + 20]
    assert red_count(x_box) > 10 and red_count(c_box) > 10
    assert not red_at(fr, 5), "a sticker is drawn far from its point"


def test_the_same_reel_renders_the_same_frames(demo, gl):
    d = annotated_reel(
        demo,
        [
            {"type": "arrow", "points": [[0.2, 0.3], [0.8, 0.7]], "start": 0.5},
            {"type": "dashed", "points": [[0.1, 0.9], [0.5, 0.6], [0.9, 0.9]], "start": 0.5},
        ],
    )
    a, sid_a = renderer(demo, d)
    b, sid_b = renderer(demo, d)
    for t in (0.6, 0.8, 1.2):
        assert np.array_equal(a.still(sid_a, t), b.still(sid_b, t))
