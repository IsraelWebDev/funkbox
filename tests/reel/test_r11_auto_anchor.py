import json
from types import SimpleNamespace

import numpy as np
import pytest

from funkbox.reel import captions as C
from funkbox.reel import schema
from funkbox.reel.timeline import AUTO_TRANSITIONS, resolve_transition, resolve_transitions

W, H = 270, 480


def lay_for(anchor, w, h, safe_area):
    """A fake layout: a 200 x 60 box at the top, middle or bottom of the frame."""
    y0 = {"top": 40, "center": 210, "bottom": 380}[anchor]
    return SimpleNamespace(x0=35, y0=y0, box_w=200, box_h=60)


def frame(busy_rows=()):
    f = np.full((H, W, 3), 90, np.uint8)
    rng = np.random.default_rng(5)
    for a, b in busy_rows:
        f[a:b] = rng.integers(0, 255, (b - a, W, 3), dtype=np.uint8)
    return f


def test_pick_anchor_takes_the_emptiest_region():
    assert C.pick_anchor(frame([(370, 450), (200, 280)]), lay_for, W, H) == "top"          # bottom and middle busy
    assert C.pick_anchor(frame([(30, 110), (370, 450)]), lay_for, W, H) == "center"
    assert C.pick_anchor(frame([(30, 110), (200, 280)]), lay_for, W, H) == "bottom"


def test_pick_anchor_ties_go_bottom_and_is_deterministic():
    flat = frame()
    assert C.pick_anchor(flat, lay_for, W, H) == "bottom"
    busy = frame([(0, H)])
    assert C.pick_anchor(busy, lay_for, W, H) == C.pick_anchor(busy.copy(), lay_for, W, H)


def reel_with(anchor="auto", transition="auto"):
    return {"version": 3, "settings": {"width": 1080, "height": 1920, "fps": 30}, "copy": "copy.json",
            "shots": [{"id": "a", "source": {"color": "#222222"}, "duration": 1, "transition_in": transition,
                       "caption": {"copy_id": "c", "style": "red-box", "anchor": anchor}}]}


def test_schema_accepts_auto_and_still_rejects_other_values():
    assert "auto" in schema.ANCHORS and "auto" in schema.TRANSITIONS
    reel = schema.parse_reel(reel_with())
    assert reel.shots[0].caption.anchor == "auto" and reel.shots[0].transition_in == "auto"
    for bad in ({"anchor": "middle"}, {"transition": "wipe"}):
        with pytest.raises(schema.ReelError):
            schema.parse_reel(reel_with(**bad))


def shots(ids, value="auto"):
    return [SimpleNamespace(id=i, transition_in=value) for i in ids]


def test_auto_transitions_cut_first_never_repeat_and_are_stable():
    ids = [f"shot{i}" for i in range(12)]
    out = resolve_transitions(shots(ids))
    assert out[0] == "cut" and all(t in AUTO_TRANSITIONS for t in out[1:])
    assert all(a != b for a, b in zip(out[1:], out[2:]))
    assert out == resolve_transitions(shots(ids))
    assert len(set(out[1:])) > 1                                   # different shot ids can differ
    other = resolve_transitions(shots([f"x{i}" for i in range(12)]))
    assert out != other


def test_explicit_transitions_are_untouched():
    assert resolve_transition("a", 3, "push") == "push"
    assert resolve_transitions(shots(["a", "b", "c"], value="flash")) == ["flash"] * 3


def test_lint_demands_an_explicit_anchor_on_code_card_shots(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from PIL import Image
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "promo"))
    import lint
    day = tmp_path / "2026-11-03"
    (day / "assets").mkdir(parents=True)
    Image.new("RGB", (1080, 1920), (18, 20, 28)).save(day / "assets" / "card.png")
    (day / "copy.json").write_text(json.dumps({"lines": {"c": {"text": "CAPTIONS MATCH\nYOUR COPY"}}}))
    (day / "assets.json").write_text(json.dumps({"assets": [{"path": "assets/card.png",
                                                             "text_rect": [0.02, 0.09, 0.7, 0.56]}]}))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    reel = {"version": 3, "settings": {"width": 1080, "height": 1920, "fps": 30, "safe_area": "reels"},
            "copy": "copy.json", "shots": [{"id": "card", "source": {"image": "assets/card.png"}, "duration": 2,
                                            "caption": {"copy_id": "c", "style": "red-box", "anchor": "auto"}}]}
    (day / "reel.json").write_text(json.dumps(reel))
    assert "explicit caption anchor" in lint.caption_problems(day)[0]
    reel["shots"][0]["caption"]["anchor"] = "bottom"
    (day / "reel.json").write_text(json.dumps(reel))
    assert lint.caption_problems(day) == []
