"""SS-3: the hook title over shot 1, and the shot-length target (pacing) for auto shots."""

import json

import pytest

from conftest import make_plain_reel
from funkbox.reel import captions, render, timeline
from funkbox.reel import schema as S
from funkbox.reel.validate import validate


def reel(**over):
    d = {
        "version": 3,
        "copy": "copy.json",
        "shots": [{"id": "s1", "source": {"color": "#202020"}, "duration": 1.0, "caption": {"copy_id": "hook"}}],
    }
    d.update(over)
    return d


def two_auto_shots(**over):
    d = reel(**over)
    d["shots"] = [
        {"id": "s1", "source": {"color": "#202020"}, "duration": "auto"},
        {"id": "s2", "source": {"color": "#303030"}, "duration": "auto"},
    ]
    return d


# --- parsing and presets -------------------------------------------------------------------------------------------- #
def test_hook_and_target_are_off_by_default():
    r = S.parse_reel(reel())
    assert r.hook is False and r.shot_target_s is None


def test_hook_and_target_parse_on_the_reel():
    r = S.parse_reel(reel(hook=True, shot_target_s=3))
    assert r.hook is True and r.shot_target_s == 3.0


def test_tech_preset_sets_a_shot_target_and_an_explicit_one_wins():
    assert S.parse_reel(reel(type="tech")).shot_target_s == 2.5
    assert S.parse_reel(reel(type="tech", shot_target_s=4.0)).shot_target_s == 4.0


def test_a_preset_may_set_the_hook_and_the_reel_overrides_it(tmp_path, monkeypatch):
    (tmp_path / "titled.json").write_text(json.dumps({"hook": True}), "utf-8")
    monkeypatch.setattr(S, "TYPES_DIR", tmp_path)
    assert S.parse_reel(reel(type="titled")).hook is True
    assert S.parse_reel(reel(type="titled", hook=False)).hook is False


# --- hook style ----------------------------------------------------------------------------------------------------- #
def test_hook_style_is_a_larger_boxed_display_title():
    base = captions.Style(font="caption", size=0.05, box=None)
    h = captions.hook_style(base)
    assert h.font == "display"
    assert h.size == pytest.approx(0.05 * captions.HOOK_SIZE)
    assert h.box == "primary"
    assert captions.hook_style(captions.Style(size="auto", box="auto")).size == "auto"


# --- validation ----------------------------------------------------------------------------------------------------- #
def test_hook_needs_a_caption_with_a_copy_line_on_shot_one(work):
    (work / "copy.json").write_text('{"lines": {"hook": {"text": "LOOK CLOSER"}}}', "utf-8")
    d = reel(hook=True)
    del d["shots"][0]["caption"]
    rep = validate(d, base_dir=work)
    assert [e for e in rep.errors if "hook" in e and "caption" in e]


def test_a_hook_title_that_cannot_fit_is_an_error(work):
    (work / "copy.json").write_text(json.dumps({"lines": {"hook": {"text": "WORD " * 120}}}), "utf-8")
    rep = validate(reel(hook=True), base_dir=work)
    assert [e for e in rep.errors if e.startswith("shots[0] (s1).hook")]


def test_a_short_hook_title_fits(work):
    (work / "copy.json").write_text('{"lines": {"hook": {"text": "LOOK CLOSER"}}}', "utf-8")
    rep = validate(reel(hook=True), base_dir=work)
    assert not [e for e in rep.errors if "hook" in e]


# --- pacing --------------------------------------------------------------------------------------------------------- #
def test_auto_shots_take_the_target_length():
    spans = timeline.build_timeline(S.parse_reel(two_auto_shots(shot_target_s=2.5)))
    assert [s.duration for s in spans[:2]] == pytest.approx([2.5, 2.5])


def test_auto_shots_without_a_target_keep_the_old_length():
    spans = timeline.build_timeline(S.parse_reel(two_auto_shots()))
    assert [s.duration for s in spans[:2]] == pytest.approx([timeline.AUTO_NO_VO] * 2)


def test_a_short_voiceover_line_still_gets_the_target():
    d = two_auto_shots(shot_target_s=2.5)
    d["shots"][0]["vo"] = "hook"
    d["audio"] = {"voiceover": True}
    spans = timeline.build_timeline(S.parse_reel(d), vo_durations={"hook": 4.0})
    assert spans[0].duration == pytest.approx(4.0 + timeline.VO_TAIL, abs=1 / 30)  # whole frames at 30 fps


# --- render --------------------------------------------------------------------------------------------------------- #
def test_the_hook_title_is_in_the_text_audit(demo, gl, work, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    d = make_plain_reel(demo)
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    for sh in d["shots"]:
        sh["duration"] = 2.5
    d["hook"] = True
    render.render_reel(d, "hooked", base_dir=demo, out_dir=work, audit=True, with_audio=False)
    doc = json.loads((work / "hooked.text_audit.json").read_text(encoding="utf-8"))
    hooks = [e for e in doc["entries"] if e["slot"] == "hook"]
    assert hooks, "the hook title should be in the audit"
    assert all(e["shot"] == d["shots"][0]["id"] for e in hooks)
    assert doc["ok"]


def test_a_hook_on_a_shot_shorter_than_the_title_is_an_error(work):
    (work / "copy.json").write_text('{"lines": {"hook": {"text": "LOOK CLOSER"}}}', "utf-8")
    d = reel(hook=True)
    d["shots"][0]["duration"] = 0.5
    rep = validate(d, base_dir=work)
    assert [e for e in rep.errors if "the hook needs" in e]