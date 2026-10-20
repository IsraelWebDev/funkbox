import copy
import json

import pytest

from reel import copydeck, timeline, validate
from reel.schema import ReelError, parse_reel

DECK = {"brand_name": "X", "lines": {"a": {"text": "ONE\nTWO", "say": "one two"}, "b": {"text": "Bee GLOWMOP"}},
        "pronounce": {"GLOWMOP": "glow mop"}}


def reel_dict(**kw):
    d = {"copy": "c.json", "settings": {"fps": 30},
         "shots": [{"id": "s1", "source": {"color": "#112233"}, "duration": 1.4},
                   {"id": "s2", "source": {"color": "#223344"}, "duration": 1.4},
                   {"id": "s3", "source": {"color": "#334455"}, "duration": 1.4}],
         "end_card": {"template": "brand-split", "duration": 2.0, "copy": {"headline": "a"}}}
    d.update(kw)
    return d


def test_unknown_copy_id_is_an_error():
    d = reel_dict()
    d["shots"][0]["caption"] = {"copy_id": "nope"}
    reel = parse_reel(d)
    errs = copydeck.check(reel, copydeck.parse_copy(DECK))
    assert any("unknown copy id 'nope'" in e and "shots[0]" in e for e in errs)


@pytest.mark.parametrize("where", ["caption", "shot", "brand", "end_card", "motion"])
def test_literal_text_key_anywhere_is_an_error(where):
    d = reel_dict()
    if where == "caption":
        d["shots"][0]["caption"] = {"copy_id": "a", "text": "hello"}
    elif where == "shot":
        d["shots"][1]["label"] = "hello"
    elif where == "brand":
        d["brand"] = {"text": "hello"}         # brand.text is a colour; a non-colour is literal text
    elif where == "end_card":
        d["end_card"]["caption_text"] = "x"
    else:
        d["shots"][2]["motion"] = {"type": "none", "title": "x"}
    with pytest.raises(ReelError) as e:
        parse_reel(d)
    assert "free text is not allowed" in str(e.value)


def test_brand_text_colour_is_allowed():
    parse_reel(reel_dict(brand={"text": "#ffffff"}))


def test_copy_deck_exactness_and_pronounce(tmp_path):
    p = tmp_path / "copy.json"
    p.write_bytes(json.dumps(DECK, ensure_ascii=False).encode("utf-8"))
    deck = copydeck.load_copy(p)
    assert deck.text("a") == "ONE\nTWO" and deck.text("b") == "Bee GLOWMOP"      # never altered
    assert deck.say("a") == "one two"
    assert deck.say("b") == "Bee glow mop"                                     # TTS only
    assert deck.text("b") == "Bee GLOWMOP"


def test_say_defaults_to_text_without_newlines():
    deck = copydeck.parse_copy({"lines": {"h": {"text": "Deep\nCleaning\nPower"}}})
    assert deck.say("h") == "Deep Cleaning Power"


def vo_reel(durs, voiceover=True, snap=False, fps=30):
    d = reel_dict(audio={"voiceover": voiceover, "snap_cuts_to_beats": snap, "music": "m.wav"})
    d["shots"] = [{"id": f"s{i}", "source": {"color": "#112233"}, "vo": "a", "duration": x}
                  for i, x in enumerate(durs)]
    d["settings"]["fps"] = fps
    return parse_reel(d)


def test_auto_follows_vo_durations():
    reel = vo_reel(["auto", "auto"])
    reel.shots[1].vo = "b"
    spans = timeline.build_timeline(reel, {"a": 1.8, "b": 0.4}, None)
    assert spans[0].duration == pytest.approx(1.8 + 0.35, abs=1 / 30)
    assert spans[1].duration == pytest.approx(1.0, abs=1 / 30)             # min 1.0 s
    assert spans[0].vo_start == pytest.approx(spans[0].start + 0.1)
    assert spans[1].start == pytest.approx(spans[0].end)
    assert spans[-1].kind == "end"


def test_too_short_explicit_duration_names_the_shot():
    reel = vo_reel([1.0])
    reel.shots[0].id = "s_short"
    with pytest.raises(timeline.TimelineError) as e:
        timeline.build_timeline(reel, {"a": 1.2}, None)
    assert "s_short" in str(e.value)


def test_validator_reports_short_duration_with_shot_name(work):
    tmp_path = work
    raw = reel_dict(audio={"voiceover": True})
    raw["shots"] = [{"id": "tooshort", "source": {"color": "#112233"}, "vo": "a", "duration": 1.0}]
    (tmp_path / "c.json").write_text(json.dumps(DECK))
    rep = validate.validate(raw, tmp_path, vo_durations={"a": 1.5}, deep=False)
    assert not rep.ok and any("tooshort" in e for e in rep.errors)


def test_beat_snapping_moves_cuts_within_one_frame_of_beats():
    beats = [i * 0.5 for i in range(60)]                                   # 120 BPM
    reel = vo_reel([1.4, 1.35, 1.6, 1.45], voiceover=False, snap=True)
    spans = timeline.build_timeline(reel, {}, beats)
    cuts = [s.start for s in spans[1:]]
    assert len(cuts) == 4
    for c in cuts:
        nearest = min(beats, key=lambda b: abs(b - c))
        assert abs(nearest - c) <= 1 / 30 + 1e-9, (c, nearest)
    assert all(s.frames >= 0.6 * 30 for s in spans[:-1])                   # no shot under 0.6 s


def test_snapping_never_breaks_min_length_or_vo():
    beats = [i * 0.5 for i in range(60)]
    reel = vo_reel([0.7, 0.62, 1.2], voiceover=False, snap=True)
    spans = timeline.build_timeline(reel, {}, beats)
    assert all(s.duration >= 0.6 - 1e-9 for s in spans[:-1])


def test_beats_duration_uses_tempo_and_needs_music_grid():
    reel = vo_reel(["beats:3"], voiceover=False)
    spans = timeline.build_timeline(reel, {}, [i * 0.5 for i in range(20)])
    assert spans[0].duration == pytest.approx(1.5)
    with pytest.raises(timeline.TimelineError):
        timeline.build_timeline(reel, {}, None)


def test_long_reel_warns_not_errors():
    reel = vo_reel([30.0, 30.0], voiceover=False)
    spans = timeline.build_timeline(reel, {}, None)
    assert timeline.warnings(spans)
