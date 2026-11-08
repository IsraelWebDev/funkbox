"""Music variety, card 1: the brief schema and the seeded generator (funkbox/music/vary.py)."""

import json

import pytest

from funkbox.music.score import SCALES, check, key_root, parse_notes
from funkbox.music.vary import MAX_VARIANTS, BriefError, generate, score_sha256, validate_brief

BRIEF = {
    "bpm_range": [96, 124],
    "keys": ["D", "A", "E", "G"],
    "scales": ["freygish", "minor-pentatonic", "dorian", "mixolydian"],
    "programs": [0, 24, 32, 40, 56, 71],
    "drum_patterns": ["straight", "half-time", "shuffle"],
    "swing": [0.0, 0.2],
    "arrangement": "build",
}


def test_same_brief_and_seed_give_the_same_score_bytes():
    a = generate(BRIEF, 7, 6)
    b = generate(BRIEF, 7, 6)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert [score_sha256(s) for s in a] == [score_sha256(s) for s in b]


def test_another_seed_gives_other_scores():
    assert generate(BRIEF, 7, 6) != generate(BRIEF, 8, 6)


def test_every_bpm_is_inside_the_band():
    for s in generate(BRIEF, 3, 12):
        assert 96 <= s["bpm"] <= 124


def test_every_score_passes_the_schema_check():
    for s in generate(BRIEF, 3, 12):
        assert check(s) == []


def test_variant_hashes_and_names_are_unique():
    scores = generate(BRIEF, 5, 12)
    assert len({score_sha256(s) for s in scores}) == 12
    assert len({s["name"] for s in scores}) == 12


def test_six_variants_differ_in_key_or_scale_and_in_instruments():
    scores = generate(BRIEF, 11, 6)
    assert len({(s["key"], s["scale"]) for s in scores}) >= 3
    program_sets = {tuple(t["program"] for t in s["tracks"] if "program" in t) for s in scores}
    assert len(program_sets) >= 3


def test_every_pitched_track_fills_the_bars_in_beats():
    for s in generate(BRIEF, 2, 6, bars=8):
        root, scale = key_root(s["key"]), SCALES[s["scale"]]
        for t in s["tracks"]:
            if t.get("drums"):
                continue
            _, beats = parse_notes(t["notes"], root, scale, t["octave"], t["velocity"], 0)
            assert beats == s["bars"] * s["meter"], t["name"]


def test_build_arrangement_holds_the_bass_back_for_one_bar():
    s = generate(BRIEF, 2, 1, bars=8)[0]
    notes = {t["name"]: t["notes"] for t in s["tracks"] if not t.get("drums")}
    assert notes["pad"].split(" | ")[0] != "r/1"
    assert notes["bass"].split(" | ")[0] == "r/1"
    assert notes["bass"].split(" | ")[1] != "r/1"


def test_a_brief_too_small_for_the_count_is_refused_not_repeated():
    small = dict(
        BRIEF,
        keys=["D"],
        scales=["dorian"],
        programs=[0],
        drum_patterns=["straight"],
        swing=[0.0],
        bpm_range=[100, 100],
    )
    with pytest.raises(BriefError, match="could not find 12 different scores"):
        generate(small, 1, 12)


def test_invalid_briefs_are_refused_with_every_problem():
    bad = {
        "bpm_range": [20, 300],
        "keys": ["H"],
        "scales": ["nope"],
        "programs": [200],
        "drum_patterns": ["techno"],
        "swing": [0.9],
        "arrangement": "drop",
        "tempo": 1,
    }
    errs = validate_brief(bad)
    for want in ("bpm_range", "keys", "scales", "programs", "drum_patterns", "swing", "arrangement", "unknown keys"):
        assert any(want in e for e in errs), want


def test_generate_refuses_a_bad_seed_count_and_meter_together():
    with pytest.raises(BriefError) as exc:
        generate(BRIEF, "7", 0, meter=3)
    text = str(exc.value)
    assert "seed" in text and "count" in text and "meter" in text


def test_generate_refuses_more_than_the_variant_limit():
    with pytest.raises(BriefError, match="count: 1..12"):
        generate(BRIEF, 1, MAX_VARIANTS + 1)


def _base_brief():
    return {
        "bpm_range": [90, 100],
        "keys": ["D"],
        "scales": ["minor"],
        "programs": [0],
        "drum_patterns": ["straight"],
        "swing": [0.0],
    }


def test_unhashable_list_items_are_refused_not_crashed():
    from funkbox.music.vary import validate_brief

    b = _base_brief()
    b["scales"] = [["minor"]]
    b["drum_patterns"] = [{"a": 1}]
    errs = validate_brief(b)
    assert any(e.startswith("scales:") for e in errs)
    assert any(e.startswith("drum_patterns:") for e in errs)


def test_fractional_bpm_range_stays_inside_the_range():
    from funkbox.music.vary import validate_brief, generate

    b = _base_brief()
    b["bpm_range"] = [120.2, 120.8]
    assert any(e.startswith("bpm_range:") for e in validate_brief(b))
    b["bpm_range"] = [120.2, 125.8]
    for s in generate(b, seed=1, count=3):
        assert 121 <= s["bpm"] <= 125


def test_mixed_type_unknown_keys_are_reported_not_crashed():
    from funkbox.music.vary import validate_brief

    b = _base_brief()
    b[1] = 0
    errs = validate_brief(b)
    assert any(e.startswith("brief: unknown keys") for e in errs)
