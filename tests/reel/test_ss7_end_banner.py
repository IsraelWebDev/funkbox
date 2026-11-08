import numpy as np

from funkbox.reel import endcard as E
from funkbox.reel import render
from funkbox.reel.validate import validate

W, H, FPS = 540, 960, 30


def banner_reel(copy):
    return {
        "copy": "inputs/brand/copy.json",
        "settings": {"width": W, "height": H, "fps": FPS},
        "shots": [{"id": "c", "source": {"color": "#204060"}, "duration": 2.0}],
        "end_card": {"template": "bottom-banner", "duration": 4.0, "copy": copy},
    }


def make_renderer(demo, reel):
    rep = validate(reel, demo)
    assert rep.ok, rep.errors
    return render.Renderer(rep.reel, rep.deck, rep.spans, width=W, height=H, depth_mode="off")


def test_bottom_banner_is_listed():
    assert "bottom-banner" in E.list_templates()


def test_headline_only_template_validates(demo):
    rep = validate(banner_reel({"headline": "end_head"}), demo)
    assert rep.ok, rep.errors


def test_banner_rejects_slots_it_does_not_declare(demo):
    rep = validate(banner_reel({"headline": "end_head", "url": "url"}), demo)
    assert not rep.ok
    assert any("end_card.copy.url" in e and "bottom-banner" in e for e in rep.errors), rep.errors


def test_banner_rejects_a_sub_line_too(demo):
    rep = validate(banner_reel({"headline": "end_head", "sub": "end_sub"}), demo)
    assert not rep.ok
    assert any("end_card.copy.sub" in e and "bottom-banner" in e for e in rep.errors), rep.errors


def test_banner_types_at_the_bottom_and_leaves_the_top_alone(demo, gl):
    r = make_renderer(demo, banner_reel({"headline": "end_head"}))
    try:
        span = r.spans[-1]
        assert span.kind == "end"
        static = r.endcard().static
        last = span.end_frame - span.start_frame - 1
        top = int(0.6 * H)
        band = slice(int(0.78 * H), H)
        r.audit = {}
        ink = {}
        for k in range(last + 1):
            fr = r.shot_frame(span, k)
            diff = np.abs(fr.astype(np.int16) - static.astype(np.int16)).sum(axis=2)
            assert np.array_equal(fr[:top], static[:top]), k
            ink[k] = int((diff[band] > 60).sum())
        first = min(k for k, v in ink.items() if v > 0)
        assert (
            sum(1 for k in ink if 0 < ink[k] < ink[last]) >= 0.5 * FPS
        )  # spread over half a second or more, not one pop
        assert first > 0.2 * FPS  # nothing is typed before the template's start
        assert ink[first] < ink[last]  # the text grows: it types rather than appearing whole
        entry = {(e["slot"], e["instance"]): e for e in r.audit_report()}
        assert set(entry) == {("headline", "main")}
        assert entry[("headline", "main")]["text"] == r.deck.text("end_head")
    finally:
        r.release()
