"""grid source: several clips side by side, arriving one after another, each with a label from the copy deck."""

import copy
import json
import subprocess

import numpy as np
import pytest

from conftest import make_plain_reel
from funkbox.reel import copydeck, render, validate
from funkbox.reel import grid as G
from funkbox.reel import schema as S

COLOURS = {"red": (255, 0, 0), "green": (0, 200, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0)}
LABELS = ["tough", "gentle", "fresh", "end_sub"]  # copy ids of the demo deck
W, H, FPS = 270, 480, 10
PAD = (32, 64, 96)  # #204060: a pad colour no cell or frame is


# --------------------------------------------------------------------------- parse / validate
def _parse_reel(grid=None, **source):
    g = {"cols": 2, "rows": 2, "cells": [{"video": f"c{i}.mp4"} for i in range(4)]}
    g.update(grid or {})
    return {"copy": "copy.json", "shots": [{"id": "g1", "source": {"grid": g, **source}, "duration": 2}]}


def _errors(d):
    with pytest.raises(S.ReelError) as ei:
        S.parse_reel(d)
    return ei.value.errors


def test_a_good_grid_parses_with_defaults():
    g = S.parse_reel(_parse_reel()).shots[0].source.grid
    assert (g.cols, g.rows, g.gap, g.reveal, g.stagger, g.ease) == (2, 2, 0.02, "none", None, "out")
    assert [(c.fit, c.label, c.label_style) for c in g.cells] == [("contain", None, "bold-pop")] * 4
    assert S.parse_reel(_parse_reel()).shots[0].source.kind == "grid"


def test_wrong_cell_count_names_the_shot_and_both_counts():
    d = _parse_reel({"cells": [{"video": "a.mp4"}] * 3})
    errs = _errors(d)
    assert any("g1" in e and "4 cells" in e and "got 3" in e for e in errs), errs


def test_bad_reveal_and_bad_ease():
    errs = _errors(_parse_reel({"reveal": "cascade", "ease": "bounce"}))
    assert any("grid.reveal" in e for e in errs) and any("grid.ease" in e for e in errs), errs


def test_bad_stagger_and_gap():
    errs = _errors(_parse_reel({"stagger": "beats:0", "gap": 0.5}))
    assert any("grid.stagger" in e for e in errs) and any("grid.gap" in e for e in errs), errs


def test_cell_with_neither_video_nor_image_is_an_error_and_image_cells_are_not_supported():
    cells = [{"video": "a.mp4"}, {"fit": "cover"}, {"image": "i.png"}, {"video": "a.mp4"}]
    errs = _errors(_parse_reel({"cells": cells}))
    assert any("cells[1]" in e and "needs a video" in e and "neither video nor image" in e for e in errs), errs
    assert any("cells[2]" in e and "needs a video" in e for e in errs), errs  # an image alone is not a cell
    assert any("cells[2]" in e and "image" in e and "unknown" in e.lower() for e in errs), errs
    assert not any("cells[0]" in e or "cells[3]" in e for e in errs)


def test_a_bad_cols_or_rows_is_reported_once_not_as_a_misleading_count_error():
    for bad in ({"cols": 0}, {"cols": 9}, {"rows": 0}, {"rows": "x"}):
        errs = _errors(_parse_reel(bad))
        assert any(("grid.cols" if "cols" in bad else "grid.rows") in e for e in errs), (bad, errs)
        assert not any("cells (cols * rows)" in e for e in errs), (bad, errs)  # no "needs exactly 4 cells"
    errs = _errors(_parse_reel({"cols": 3}))  # a valid 3 x 2 grid with 4 cells
    assert any("a 3 x 2 grid needs exactly 6 cells" in e and "got 4" in e for e in errs), errs


def test_parallax_is_rejected_on_a_grid_shot():
    d = _parse_reel()
    d["shots"][0]["motion"] = {"type": "parallax"}
    assert any("parallax" in e for e in _errors(d))
    for ok in ("none", "kenburns"):
        d["shots"][0]["motion"] = {"type": ok}
        assert S.parse_reel(d).shots[0].motion.type == ok


def test_cols_and_rows_are_required():
    errs = _errors({"copy": "copy.json", "shots": [{"id": "g1", "source": {"grid": {"cells": [{"video": "a.mp4"}]}}}]})
    assert any("grid.cols: required" in e for e in errs) and any("grid.rows: required" in e for e in errs), errs


def test_a_label_is_a_copy_id_never_text():
    d = _parse_reel()
    d["shots"][0]["source"]["grid"]["cells"][0]["label"] = "tough"  # an id: allowed
    assert S.parse_reel(d).shots[0].source.grid.cells[0].label == "tough"
    d["shots"][0]["source"]["grid"]["cells"][0]["label"] = "Before: the old way"  # text: refused
    errs = _errors(d)
    assert any("free text" in e for e in errs), errs
    # `label` anywhere else stays forbidden
    d2 = _parse_reel()
    d2["shots"][0]["label"] = "tough"
    assert any("free text" in e for e in _errors(d2))


def test_unknown_label_is_caught_by_the_copy_checks():
    d = _parse_reel()
    d["shots"][0]["source"]["grid"]["cells"][1]["label"] = "nope"
    d["shots"][0]["source"]["grid"]["cells"][2]["label"] = "a"
    reel = S.parse_reel(d)
    deck = copydeck.parse_copy({"lines": {"a": {"text": "A"}}})
    ids = dict((cid, where) for where, cid in copydeck.used_ids(reel))
    assert set(ids) == {"nope", "a"} and "cells[1].label" in ids["nope"]
    errs = copydeck.check(reel, deck)
    assert len(errs) == 1 and "'nope'" in errs[0] and "cells[1].label" in errs[0] and "g1" in errs[0]


def test_geometry_equal_cells_gap_of_the_width():
    rects = G.cell_rects(2, 2, 0.02, 270, 480)
    assert len(rects) == 4 and len({(w, h) for _, _, w, h in rects}) == 1
    (x0, y0, cw, ch), (x1, y1, *_), (x2, y2, *_) = rects[0], rects[1], rects[2]
    gp = round(0.02 * 270)
    assert x1 - (x0 + cw) == gp and y2 - (y0 + ch) == gp and y1 == y0 and x2 == x0  # row-major
    assert all(x >= 0 and y >= 0 and x + w <= 270 and y + h <= 480 for x, y, w, h in rects)


def test_stagger_default_follows_the_beat_grid():
    g = S.parse_reel(_parse_reel({"reveal": "sequence"})).shots[0].source.grid
    assert G.stagger_seconds(g, None) == 0.25 and G.stagger_seconds(g, 0.5) == 0.5
    g = S.parse_reel(_parse_reel({"reveal": "sequence", "stagger": "beats:2"})).shots[0].source.grid
    assert G.stagger_seconds(g, 0.5) == 1.0
    with pytest.raises(ValueError, match="beat grid"):
        G.stagger_seconds(g, None)
    g = S.parse_reel(_parse_reel({"reveal": "sequence", "stagger": 0.4})).shots[0].source.grid
    assert G.stagger_seconds(g, 0.5) == 0.4


def test_a_cell_is_not_drawn_before_its_arrival_then_drops_and_fades_in():
    g = S.parse_reel(_parse_reel({"reveal": "sequence"})).shots[0].source.grid
    assert G.pose(g, -0.01, 200) is None and G.pose(g, 0.0, 200) is None
    a, dy = G.pose(g, 0.05, 200)
    assert 0 < a < 1 and -20 <= dy < 0
    assert G.pose(g, 0.25, 200) == (1.0, 0) and G.pose(g, 5.0, 200) == (1.0, 0)
    g = S.parse_reel(_parse_reel()).shots[0].source.grid  # reveal none: always there
    assert G.pose(g, 0.0, 200) == (1.0, 0)


def test_a_cell_is_not_drawn_a_frame_early_by_float_noise():
    g = S.parse_reel(_parse_reel({"reveal": "sequence", "stagger": 0.7})).shots[0].source.grid
    fps = 30
    arrive = G.arrival(g, 3, 0.7)  # 3 * 0.7 = 2.0999999999999996
    noise = 63 / fps - arrive
    assert 0 < noise < 1e-9, noise  # frame 63 is the arrival, off by ~4e-16
    first = next(k for k in range(200) if G.pose(g, k / fps - arrive, 200) is not None)
    assert first == 64, first  # the frame after the arrival, not 63


# --------------------------------------------------------------------------- render
def _solid_clip(path, rgb, size="132x236", seconds=4):
    from funkbox.media import ffmpeg_exe

    hexc = "0x{:02x}{:02x}{:02x}".format(*rgb)
    subprocess.run(
        [
            ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={hexc}:s={size}:r={FPS}",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def clips(work):
    return [_solid_clip(work / f"{n}.mp4", rgb) for n, rgb in COLOURS.items()]


def grid_reel(demo, clips, *, reveal="sequence", stagger=0.5, dur=3.0, labels=LABELS, pad="#204060", **grid_over):
    d = make_plain_reel(demo)
    d["settings"] = {"width": W, "height": H, "fps": FPS, "safe_area": "reels"}
    d["audio"] = {"voiceover": False}
    d.pop("end_card", None)
    cells = [{"video": str(p), **({"label": labels[i]} if labels else {})} for i, p in enumerate(clips)]
    grid = {"cols": 2, "rows": 2, "gap": 0.02, "reveal": reveal, "cells": cells, **grid_over}
    if stagger is not None:  # None: leave the key out (the default applies)
        grid["stagger"] = stagger
    d["shots"] = [{"id": "g1", "source": {"pad": pad, "grid": grid}, "duration": dur}]
    return d


def _centre(fr, rect):
    """A pixel well inside the cell's picture and clear of its label (the label sits low in the cell; a bottom-row
    label is lifted into the cell's middle by the reel's safe area)."""
    x, y, w, h = rect
    return tuple(int(v) for v in fr[y + h // 5, x + w // 2])


def _near(px, rgb, tol=45):
    return all(abs(a - b) <= tol for a, b in zip(px, rgb))


def _renderer(demo, d, **kw):
    plan = render.prepare(d, demo, **kw)
    return render.Renderer(
        plan.reel,
        plan.deck,
        plan.spans,
        width=plan.reel.settings.width,
        height=plan.reel.settings.height,
        depth_mode="off",
    )


def _is_pad(fr, rect):
    x, y, w, h = rect
    return bool((fr[y : y + h, x : x + w] == PAD).all())


def test_sequence_reveal_cell_i_arrives_at_i_times_the_stagger(demo, gl, clips, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    rects = G.cell_rects(2, 2, 0.02, W, H)
    r = _renderer(demo, grid_reel(demo, clips))
    try:
        for i, rect in enumerate(rects):
            want = list(COLOURS.values())[i]
            if i > 0:
                assert _is_pad(r.still("g1", i * 0.5 - 0.1), rect), i  # neither the cell nor its label yet
            assert _is_pad(r.still("g1", i * 0.5), rect), (i, "alpha is 0 at the arrival itself")
            after = r.still("g1", i * 0.5 + 0.3)
            assert _near(_centre(after, rect), want), (i, _centre(after, rect), want)
        last = r.still("g1", 2.9)  # everything is there, nothing moved
        for i, rect in enumerate(rects):
            assert _near(_centre(last, rect), list(COLOURS.values())[i])
        x, y, w, h = rects[0]
        assert tuple(int(v) for v in last[y + h // 2, x + w + 1]) == PAD  # the gap is the pad colour
        assert tuple(int(v) for v in last[y + h + 1, x + w // 2]) == PAD
    finally:
        r.release()


def test_a_label_is_hidden_until_its_cell_arrives_then_appears_with_it(demo, gl, clips, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    rects = G.cell_rects(2, 2, 0.02, W, H)
    r = _renderer(demo, grid_reel(demo, clips))
    try:
        for i, (x, y, w, h) in enumerate(rects):
            colour = np.array(list(COLOURS.values())[i])
            before = r.still("g1", i * 0.5)  # first frame is the arrival itself: alpha 0
            assert _is_pad(before, rects[i]), i
            lab = r.still("g1", i * 0.5 + 0.3)[y : y + h - 2, x : x + w]  # the cell has arrived: a label sits in it
            off = np.abs(lab.astype(int) - colour).sum(-1) > 90  # pixels that are not the clip's colour
            assert off.sum() > 20, (i, "no label drawn")
            rows = np.flatnonzero(off.any(axis=1))
            if i < 2:
                assert rows.min() > h // 2, (i, "the label is anchored to the bottom of its cell")
            assert y + rows.max() <= round(H * 0.78) + 1, (i, "a label stays out of the reel's bottom safe-area band")
    finally:
        r.release()


def test_arrival_drops_in_inside_the_slot_and_never_touches_a_neighbour(demo, gl, clips, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    rects = G.cell_rects(2, 2, 0.02, W, H)
    g = S.parse_reel(grid_reel(demo, clips)).shots[0].source.grid
    r = _renderer(demo, grid_reel(demo, clips, labels=None))
    try:
        fr = r.still("g1", 0.1)  # cell 0, 0.1 s after its arrival
        alpha, dy = G.pose(g, 0.1, rects[0][3])
        assert 0 < alpha < 1 and dy < 0
        x, y, w, h = rects[0]
        top = tuple(int(v) for v in fr[y, x + w // 2])
        assert top != PAD and 0 < top[0] - PAD[0] < 255 - PAD[0]  # faded in: a blend of red and the pad
        assert (fr[y + h + dy : y + h, x : x + w] == PAD).all()  # it dropped: the slot's bottom rows are empty
        assert (fr[y + h + dy - 3, x + w // 2] != PAD).any()  # the rows above them are the picture
        for k in (1, 2, 3):
            assert _is_pad(fr, rects[k]), k  # the other cells are not drawn yet
        assert (fr[:, x + w : rects[1][0]] == PAD).all()  # the gap between columns is untouched
        assert (fr[y + h : rects[2][1]] == PAD).all()  # and between rows
    finally:
        r.release()


def test_reveal_none_draws_every_cell_from_the_first_frame(demo, gl, clips, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    r = _renderer(demo, grid_reel(demo, clips, reveal="none", stagger=None))
    try:
        fr = r.still("g1", 0.0)
        for rect, want in zip(G.cell_rects(2, 2, 0.02, W, H), COLOURS.values()):
            assert _near(_centre(fr, rect), want)
    finally:
        r.release()


def test_default_stagger_is_one_beat_with_a_beat_grid(demo, gl, clips, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    d = grid_reel(demo, clips, stagger=None, dur=5.0)
    d["audio"] = {"voiceover": False, "bpm": 60}  # one beat = 1 s
    rects = G.cell_rects(2, 2, 0.02, W, H)
    r = _renderer(demo, d)
    try:
        fr = r.still("g1", 1.4)  # cell 0 and 1 are in, 2 is not
        assert _near(_centre(fr, rects[1]), COLOURS["green"]) and _is_pad(fr, rects[2])
    finally:
        r.release()


def test_the_beat_grid_is_computed_once_per_render_not_once_per_grid_shot(demo, gl, clips, monkeypatch):
    from funkbox.reel import audio

    monkeypatch.setenv("REEL_DEPTH", "stub")
    d = grid_reel(demo, clips, stagger=None, dur=5.0)
    d["audio"] = {"voiceover": False, "bpm": 60}
    d["shots"].append({**copy.deepcopy(d["shots"][0]), "id": "g2"})  # a second grid shot
    plan = render.prepare(d, demo)
    calls = []
    real = audio.beat_grid_for
    monkeypatch.setattr(audio, "beat_grid_for", lambda reel: calls.append(1) or real(reel))
    r = render.Renderer(plan.reel, plan.deck, plan.spans, width=W, height=H, depth_mode="off")
    try:
        r.still("g1", 0.0)
        r.still("g2", 0.0)
        r.still("g1", 1.4)
        assert len(calls) == 1, calls
    finally:
        r.release()


def test_default_stagger_without_a_beat_grid_is_a_quarter_second(demo, gl, clips, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    rects = G.cell_rects(2, 2, 0.02, W, H)
    r = _renderer(demo, grid_reel(demo, clips, stagger=None))
    try:
        fr = r.still("g1", 0.4)  # cell 1 arrived at 0.25 s (it is fading in), 2 comes at 0.5
        assert not _is_pad(fr, rects[1]) and _is_pad(fr, rects[2])
        assert _near(_centre(r.still("g1", 0.7), rects[2]), COLOURS["blue"])
    finally:
        r.release()


def test_a_broken_beat_grid_is_an_error_not_a_silent_fallback(demo, gl, clips, work):
    junk = work / "junk.wav"
    junk.write_bytes(b"this is not audio")  # exists, but no beat grid can be detected in it
    for stagger in (None, "beats:2"):  # the default stagger and an explicit beat stagger
        d = grid_reel(demo, clips, stagger=stagger)
        d["audio"] = {"voiceover": False, "music": str(junk)}
        rep = validate.validate(d, demo)
        assert not rep.ok, stagger
        assert any(e.startswith("timeline:") and "cannot decode" in e for e in rep.errors), (stagger, rep.errors)
        assert not any("source.grid.stagger" in e for e in rep.errors), (stagger, rep.errors)  # the cause, once
        with pytest.raises(S.ReelError):
            render.prepare(d, demo)  # and the render is refused, not run on a made-up beat


def test_pad_blur_on_a_grid_blurs_the_cell_padding_and_leaves_the_gaps_black(demo, gl, work, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    wide = _solid_clip(work / "wide.mp4", (250, 10, 250), size="236x100")  # wider than the 132x237 cell
    d = grid_reel(demo, [wide] * 4, reveal="none", stagger=None, labels=None, pad="blur")
    d["shots"][0]["source"]["grid"]["cells"][0]["fit"] = "contain"
    r = _renderer(demo, d)
    try:
        fr, rects = r.still("g1", 0.0), G.cell_rects(2, 2, 0.02, W, H)
        x, y, w, h = rects[0]
        band = tuple(int(v) for v in fr[y + 2, x + w // 2])  # the padding above the picture
        assert band != PAD and band != (0, 0, 0) and _near(band, (137, 5, 137), 25), band  # blurred, dimmed copy
        assert _near(tuple(int(v) for v in fr[y + h // 2, x + w // 2]), (250, 10, 250))  # the picture itself
        assert (fr[y : y + h, x + w : rects[1][0]] == 0).all()  # gap between columns: black
        assert (fr[y + h : rects[2][1]] == 0).all()  # gap between rows: black
    finally:
        r.release()


def test_cover_fit_fills_the_cell_and_contain_pads_it(demo, gl, work, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    wide = _solid_clip(work / "wide.mp4", (250, 10, 250), size="236x100")  # wider than the 132x237 cell
    d = grid_reel(demo, [wide] * 4, reveal="none", stagger=None, labels=None, pad="#204060")
    cells = d["shots"][0]["source"]["grid"]["cells"]
    cells[0]["fit"] = "cover"  # fills the cell
    cells[1]["fit"] = "contain"  # a band of pad above and below
    plan = render.prepare(d, demo)
    r = render.Renderer(plan.reel, plan.deck, plan.spans, width=W, height=H, depth_mode="off")
    try:
        fr, rects = r.still("g1", 0.0), G.cell_rects(2, 2, 0.02, W, H)
        x, y, w, h = rects[0]
        assert _near(tuple(int(v) for v in fr[y + 2, x + 2]), (250, 10, 250))  # cover: the corner is picture
        x, y, w, h = rects[1]
        assert tuple(int(v) for v in fr[y + 2, x + w // 2]) == PAD  # contain: the corner is pad
        assert _near(tuple(int(v) for v in fr[y + h // 2, x + w // 2]), (250, 10, 250))
    finally:
        r.release()


def test_the_shots_motion_moves_the_whole_composed_grid(demo, gl, clips, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    d = grid_reel(demo, clips, reveal="none", stagger=None, labels=None)
    d["shots"][0]["motion"] = {"type": "kenburns", "zoom": [1.2, 1.2], "pan": [[0, 0], [0.08, 0]]}
    plan = render.prepare(d, demo)
    r = render.Renderer(plan.reel, plan.deck, plan.spans, width=W, height=H, depth_mode="off")

    def gap_columns(fr):  # x of the pad-coloured gap between the columns
        return np.flatnonzero((fr[60] == PAD).all(axis=1))

    try:
        a, b = gap_columns(r.still("g1", 0.0)), gap_columns(r.still("g1", 2.9))
        assert len(a) and len(b)
        assert abs(int(b.mean()) - int(a.mean())) > 10, (a, b)  # the gap moved with the picture, as a whole
        top_gap = lambda fr: np.flatnonzero((fr[:, 30] == PAD).all(axis=1))  # the gap between the rows
        ra, rb = top_gap(r.still("g1", 0.0)), top_gap(r.still("g1", 2.9))
        assert len(ra) and len(rb) and abs(int(rb.mean()) - int(ra.mean())) <= 1  # (the pan was horizontal only)
    finally:
        r.release()


def test_validate_reports_missing_files_unknown_label_and_beat_needs(demo, gl, clips, work):
    d = grid_reel(demo, clips)
    assert validate.validate(d, demo).ok
    bad = copy.deepcopy(d)
    bad["shots"][0]["source"]["grid"]["cells"][2]["video"] = str(work / "missing.mp4")
    rep = validate.validate(bad, demo)
    assert any("cells[2].video" in e for e in rep.errors), rep.errors
    bad = copy.deepcopy(d)
    bad["shots"][0]["source"]["grid"]["cells"][0]["label"] = "nope"
    rep = validate.validate(bad, demo)
    assert any("'nope'" in e and "cells[0].label" in e for e in rep.errors), rep.errors
    bad = copy.deepcopy(d)
    bad["shots"][0]["source"]["grid"]["stagger"] = "beats:1"  # no bpm, no music
    rep = validate.validate(bad, demo)
    assert any("beat grid" in e for e in rep.errors), rep.errors
    ok = copy.deepcopy(bad)
    ok["audio"] = {"voiceover": False, "bpm": 120}  # bpm set: a beat grid exists, no error
    assert validate.validate(ok, demo).ok


def test_a_beats_stagger_with_music_or_bpm_set_does_not_blame_the_stagger_when_the_grid_failed(demo, gl, clips, work):
    junk = work / "junk.wav"
    junk.write_bytes(b"this is not audio")
    d = grid_reel(demo, clips, stagger="beats:1")
    d["audio"] = {"voiceover": False}
    rep = validate.validate(d, demo)  # neither music nor bpm: the stagger is blamed
    assert any("source.grid.stagger" in e and "needs a beat grid" in e for e in rep.errors), rep.errors
    d["audio"] = {"voiceover": False, "music": str(junk)}  # music set, its beat grid fails
    rep = validate.validate(d, demo)
    assert any(e.startswith("timeline:") for e in rep.errors), rep.errors  # the real cause
    assert not any("needs a beat grid" in e or "source.grid.stagger" in e for e in rep.errors), rep.errors


def test_a_label_that_cannot_fit_its_cell_is_a_validation_error(demo, gl, clips):
    d = grid_reel(demo, clips, reveal="none", stagger=None, labels=None)
    g = d["shots"][0]["source"]["grid"]
    g["cols"], g["rows"] = 8, 1  # 8 cells of 29 px: a long line cannot fit
    g["cells"] = [{"video": str(clips[0])} for _ in range(8)]
    assert validate.validate(d, demo).ok
    g["cells"][3]["label"] = "care"  # GIVE YOUR CARPET THE ATTENTION IT NEEDS.
    rep = validate.validate(d, demo)
    assert any("cells[3].label" in e and "care" in e for e in rep.errors), rep.errors
    assert not any("cells[0]" in e for e in rep.errors)


def test_late_cells_are_a_validation_error_on_the_boundary(demo, gl, clips):
    def check(stagger, dur):
        return validate.validate(grid_reel(demo, clips, stagger=stagger, dur=dur), demo)

    assert check(0.5, 2.0).ok  # last cell at 1.5 s, fully in by 1.75 s
    assert check(0.5, 1.75).ok  # exactly fully in at the last moment
    rep = check(0.95, 2.0)  # cell 3 at 2.85 s, cell 2 at 1.9 s = last frame
    assert not rep.ok and any("cell(s) [2, 3]" in e and "g1" in e for e in rep.errors), rep.errors
    rep = check(0.6, 2.0)  # cell 3 at 1.8 s: its fade-in is cut off
    assert not rep.ok and any("cell(s) [3]" in e for e in rep.errors), rep.errors
    assert check(0.0, 1.0).ok  # a zero stagger: all at once
    assert G.arrives_in_time(1.75, 2.0, 10) and not G.arrives_in_time(1.76, 2.0, 10)
    assert not G.arrives_in_time(1.9, 2.0, 10) and not G.arrives_in_time(2.0, 2.0, 10)
    assert validate.validate(grid_reel(demo, clips, reveal="none", stagger=None, dur=0.5), demo).ok


def test_labels_stay_out_of_the_safe_area_bands_and_inside_their_cell(demo, gl, clips):
    from funkbox.reel import captions

    reel = S.parse_reel(grid_reel(demo, clips))
    fonts = captions.fonts_for(reel.brand, reel.base_dir)
    deck = copydeck.parse_copy(json.loads((demo / "inputs/brand/copy.json").read_text(encoding="utf-8")))
    for safe, (top, bot) in (
        ("reels", (captions.SAFE_TOP, 1 - captions.SAFE_BOTTOM)),
        ("landscape", (captions.LAND_TOP, 1 - captions.LAND_BOTTOM)),
    ):
        for rect in G.cell_rects(2, 2, 0.02, W, H):
            lay = G.layout_label("tough", deck.text("tough"), "bold-pop", fonts, reel.brand, rect, W, H, safe)
            x, y, cw, ch = rect
            assert 0 <= lay.y0 and lay.y0 + lay.box_h <= ch, (safe, rect)  # inside its cell
            assert y + lay.y0 + lay.box_h <= round(bot * H) + 1, (safe, rect, y + lay.y0 + lay.box_h)
            assert y + lay.y0 >= round(top * H) - 1
    none = G.layout_label(
        "tough", deck.text("tough"), "bold-pop", fonts, reel.brand, G.cell_rects(2, 2, 0.02, W, H)[3], W, H, "none"
    )  # bottom row, no bands: lowest
    reels = G.layout_label(
        "tough", deck.text("tough"), "bold-pop", fonts, reel.brand, G.cell_rects(2, 2, 0.02, W, H)[3], W, H, "reels"
    )
    assert reels.y0 < none.y0


def test_motion_that_crops_a_label_is_warned_about(demo, gl, clips):
    d = grid_reel(demo, clips)
    assert validate.validate(d, demo).warnings == []
    d["shots"][0]["motion"] = {"type": "kenburns", "zoom": [1.0, 1.0]}  # no zoom: nothing is cropped
    assert not [w for w in validate.validate(d, demo).warnings if "cuts this label" in w]
    d["shots"][0]["motion"] = {"type": "kenburns", "zoom": [1.0, 1.5]}
    rep = validate.validate(d, demo)
    assert rep.ok and any("cells[2].label" in w and "cuts this label" in w for w in rep.warnings), rep.warnings
    d["shots"][0]["motion"] = {
        "type": "frame",
        "keys": [{"rect": [0.0, 0.0, 1.0, 1.0], "t": 0.0}, {"rect": [0.25, 0.0, 0.5, 0.5], "t": 1.0}],
    }
    rep = validate.validate(d, demo)
    assert rep.ok and any("cuts this label" in w for w in rep.warnings), rep.warnings
    d["shots"][0]["motion"] = {"type": "kenburns", "zoom": [1.0, 1.5]}
    for c in d["shots"][0]["source"]["grid"]["cells"]:
        c.pop("label")  # no labels: nothing to cut off
    assert not [w for w in validate.validate(d, demo).warnings if "cuts this label" in w]


def test_a_9x16_and_16x9_reel_lays_the_grid_out_per_format(demo, gl, clips, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    d = grid_reel(demo, clips)
    d["formats"] = [
        {"id": "tall", "width": 180, "height": 320},
        {"id": "wide", "width": 320, "height": 180, "safe_area": "landscape"},
    ]
    rep = validate.validate(d, demo)  # checked in every format
    assert rep.ok, rep.errors
    for fmt, (w, h) in (("tall", (180, 320)), ("wide", (320, 180))):
        r = _renderer(demo, d, fmt=fmt)
        try:
            fr = r.still("g1", 2.9)
            assert fr.shape == (h, w, 3)
            for rect, want in zip(G.cell_rects(2, 2, 0.02, w, h), COLOURS.values()):
                assert _near(_centre(fr, rect), want), (fmt, rect)
        finally:
            r.release()


# --------------------------------------------------------------------------- audit
def _audit(demo, d, work, name):
    out = render.render_reel(d, name, base_dir=demo, out_dir=work, audit=True, with_audio=False)
    return out, json.loads((work / f"{name}.text_audit.json").read_text(encoding="utf-8"))


def test_every_label_is_audited_once_and_byte_identical_to_the_deck(demo, gl, clips, work, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    out, doc = _audit(demo, grid_reel(demo, clips), work, "gridded")
    deck = json.loads((demo / "inputs/brand/copy.json").read_text(encoding="utf-8"))["lines"]
    ents = [e for e in doc["entries"] if e["slot"] == "grid_label"]
    assert sorted(e["instance"] for e in ents) == ["0", "1", "2", "3"] and len(doc["entries"]) == 4
    for e in ents:
        i = int(e["instance"])
        assert e["shot"] == "g1" and e["copy_id"] == LABELS[i]
        assert e["text"].encode("utf-8") == deck[e["copy_id"]]["text"].encode("utf-8")
        assert e["text_exact"] and e["complete_frame"] is not None
        assert e["first_frame"] == i * 5 + 1  # the frame after its cell arrived (0.5 s = 5 frames; alpha 0 at 5)
    assert doc["ok"] and not doc["missing"] and not doc["inexact"] and out.audit_ok is True


def test_a_reel_with_a_labelled_grid_and_a_late_cell_is_refused_before_it_can_fail_the_audit(demo, gl, clips):
    d = grid_reel(demo, clips, stagger=1.0, dur=2.0)  # cells 2 and 3 arrive at 2 s and 3 s
    with pytest.raises(S.ReelError) as ei:
        render.prepare(d, demo)
    assert any("cell(s) [2, 3]" in e for e in ei.value.errors), ei.value.errors


def test_a_grid_whose_gap_leaves_no_room_is_refused_not_clamped():
    with pytest.raises(ValueError, match="no room"):
        G.cell_rects(6, 1, 0.2, 1080, 1920)  # five gaps of 216 px fill the 1080 px width
    with pytest.raises(ValueError, match="no room"):
        G.cell_rects(1, 4, 0.2, 1920, 1080)  # three gaps of 384 px fill the 1080 px height
    assert G.geometry_error(2, 2, 0.2, 1920, 1080) is None  # 2 x 2 at gap 0.2 still fits: cells 768 x 348
    assert "minimum" in G.geometry_error(8, 1, 0.1, 270, 480)  # eight cells of 10 px


def test_a_grid_too_big_for_its_frame_is_a_validation_error_not_a_crash(demo, gl, clips):
    d = grid_reel(demo, clips, reveal="none", stagger=None, labels=None)
    g = d["shots"][0]["source"]["grid"]
    g["cols"], g["rows"], g["gap"] = 6, 1, 0.2  # 6 cells, 270 px wide: the gaps fill the frame
    g["cells"] = [{"video": str(clips[0])} for _ in range(6)]
    rep = validate.validate(d, demo)
    assert not rep.ok and any("source.grid" in e and "no room" in e for e in rep.errors), rep.errors
