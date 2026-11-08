"""TV-1: every spoken line is in the text audit, and its text is what the deck declares (its text, or its say field),
with pronounce applied."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from funkbox.reel import audio, render
from funkbox.reel.copydeck import parse_copy


def span(sid, vo_id, start, dur, kind="shot"):
    return SimpleNamespace(id=sid, kind=kind, vo_id=vo_id, vo_start=start, vo_dur=dur)


def deck_with(lines, pronounce=None, tmp_path=None):
    d = {"brand_name": "T", "lines": lines}
    if pronounce:
        d["pronounce"] = pronounce
    deck = parse_copy(d)
    deck.path = str(tmp_path / "copy.json") if tmp_path else None
    return deck


def audit(tmp_path, deck, spans, voiceover=True):
    """Run the real _audit_json on a minimal plan (one voiced shot, s1, speaking copy id 'hook')."""
    shot = SimpleNamespace(id="s1", vo="hook", caption=None, source=SimpleNamespace(grid=None))
    reel = SimpleNamespace(
        audio=SimpleNamespace(voiceover=voiceover, voice="kokoro:af_heart"),
        settings=SimpleNamespace(fps=30),
        shots=[shot],
        end_card=None,
    )
    plan = SimpleNamespace(deck=deck, spans=spans, reel=reel)
    out = render._audit_json(SimpleNamespace(audit_report=lambda: []), plan, "t", tmp_path)
    return json.loads(Path(out["path"]).read_text(encoding="utf-8"))


def test_undeclared_line_is_spoken_as_the_deck_text():
    deck = deck_with({"hook": {"text": "LOOK\nCLOSER."}})
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.5, 2.0)], 30)
    assert e["text"] == "LOOK CLOSER."
    assert e["deck_text"] == "LOOK\nCLOSER."
    assert e["text_exact"] is True
    assert (e["shot"], e["slot"], e["instance"]) == ("s1", "voiceover", "hook")
    assert (e["first_frame"], e["last_frame"]) == (15, 74)


def test_declared_say_override_is_allowed():
    deck = deck_with({"hook": {"text": "GLOWMOP", "say": "Glow mop."}})
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.0, 1.0)], 30)
    assert e["text"] == "Glow mop."
    assert e["say_declared"] is True
    assert e["text_exact"] is True


def test_undeclared_change_fails_the_audit(tmp_path):
    deck = deck_with({"hook": {"text": "LOOK CLOSER."}}, tmp_path=tmp_path)
    deck.say = lambda cid: "Something else"  # a change no deck field declares
    doc = audit(tmp_path, deck, [span("s1", "hook", 0.0, 1.0)])
    assert doc["ok"] is False
    assert doc["inexact"] == ["s1/voiceover/hook"]


def test_pronounce_does_not_excuse_an_untouched_line():
    deck = deck_with({"hook": {"text": "EVERY LINE."}}, pronounce={"FUNKBOX": "fun box"})
    deck.say = lambda cid: "Something else"  # the line has no FUNKBOX, so no pronounce entry explains this
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.0, 1.0)], 30)
    assert e["text_exact"] is False


def test_pronounce_on_a_touched_line_is_exact():
    deck = deck_with({"hook": {"text": "FUNKBOX RULES."}}, pronounce={"FUNKBOX": "fun box"})
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.0, 1.0)], 30)
    assert e["text"] == "fun box RULES."
    assert e["text_exact"] is True


def test_a_pronounce_bug_in_say_is_caught():
    deck = deck_with({"hook": {"text": "FUNKBOX RULES."}}, pronounce={"FUNKBOX": "fun box"})
    deck.say = lambda cid: "FUNKBOX RULES."  # say() forgot to apply the map
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.0, 1.0)], 30)
    assert e["text_exact"] is False


def test_a_say_override_is_checked_against_its_declared_say():
    deck = deck_with({"hook": {"text": "GLOWMOP", "say": "Glow mop."}})
    deck.say = lambda cid: "Glow mop!"  # say() garbled the declared text
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.0, 1.0)], 30)
    assert e["text_exact"] is False


def test_empty_spoken_text_fails():
    deck = deck_with({"hook": {"text": "HI.", "say": ""}})
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.0, 1.0)], 30)
    assert e["text"] == ""
    assert e["text_exact"] is False


def test_shots_without_a_line_and_end_spans_have_no_entry():
    deck = deck_with({"hook": {"text": "HI."}})
    spans = [span("s1", None, None, None), span("end", None, None, None, kind="end"), span("s2", "hook", None, None)]
    assert audio.voice_audit_entries(deck, spans, 30) == []


def test_missing_spoken_line_fails_the_audit(tmp_path):
    deck = deck_with({"hook": {"text": "HI."}}, tmp_path=tmp_path)
    doc = audit(tmp_path, deck, [span("s1", None, None, None)])
    assert doc["ok"] is False
    assert doc["missing"] == ["s1/voiceover/hook"]


def test_audit_file_lists_the_spoken_lines(tmp_path):
    deck = deck_with({"hook": {"text": "HI."}}, tmp_path=tmp_path)
    doc = audit(tmp_path, deck, [span("s1", "hook", 0.0, 1.0)])
    assert doc["ok"] is True
    assert doc["copy_deck"] == "copy.json"
    assert [e["kind"] for e in doc["entries"]] == ["voiceover"]


def test_voice_off_writes_no_voice_entries(tmp_path):
    deck = deck_with({"hook": {"text": "HI."}}, tmp_path=tmp_path)
    doc = audit(tmp_path, deck, [span("s1", "hook", 0.0, 1.0)], voiceover=False)
    assert doc["entries"] == []
    assert doc["ok"] is True
    assert doc["tts"] is None


def test_audit_records_the_tts_engine_and_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "tts_mode", lambda voice=None: "kokoro")
    deck = deck_with({"hook": {"text": "HI."}}, tmp_path=tmp_path)
    doc = audit(tmp_path, deck, [span("s1", "hook", 0.0, 1.0)])
    assert doc["tts"] == {"mode": "kokoro", "voice": "kokoro:af_heart"}


def test_voiceover_without_a_tts_engine_raises(monkeypatch):
    deck = deck_with({"hook": {"text": "HI."}})
    monkeypatch.setattr(audio, "tts_mode", lambda voice=None: "none")
    with pytest.raises(RuntimeError, match="needs a TTS voice"):
        audio.synthesize(deck, "hook", None)


def test_longest_pronounce_key_wins():
    deck = deck_with({"other": {"text": "GLOW MOP."}}, pronounce={"GLOW": "glow", "GLOW MOP": "glow mop"})
    [e] = audio.voice_audit_entries(deck, [span("s1", "other", 0.0, 1.0)], 30)
    assert e["text"] == "glow mop."
    assert e["text_exact"] is True


def test_pronounce_matches_whole_words_only():
    deck = deck_with({"other": {"text": "FUNKBOXES."}}, pronounce={"FUNK": "fun"})
    [e] = audio.voice_audit_entries(deck, [span("s1", "other", 0.0, 1.0)], 30)
    assert e["text"] == "FUNKBOXES."
    assert e["text_exact"] is True


def test_end_spans_never_get_a_voice_entry():
    deck = deck_with({"hook": {"text": "HI."}})
    assert audio.voice_audit_entries(deck, [span("end", "hook", 0.0, 1.0, kind="end")], 30) == []


def test_pronounce_leading_boundary():
    deck = deck_with({"other": {"text": "BOXFUNK."}}, pronounce={"FUNK": "fun"})
    [e] = audio.voice_audit_entries(deck, [span("s1", "other", 0.0, 1.0)], 30)
    assert e["text"] == "BOXFUNK."
    assert e["text_exact"] is True
