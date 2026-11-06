"""TV-1: every spoken line is in the text audit, and its text is the deck's text (or a declared change)."""

from types import SimpleNamespace

from funkbox.reel import audio
from funkbox.reel.copydeck import parse_copy


def span(sid, vo_id, start, dur, kind="shot"):
    return SimpleNamespace(id=sid, kind=kind, vo_id=vo_id, vo_start=start, vo_dur=dur)


def test_undeclared_line_is_spoken_as_the_deck_text():
    deck = parse_copy({"brand_name": "T", "lines": {"hook": {"text": "LOOK\nCLOSER."}}})
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.5, 2.0)], 30)
    assert e["text"] == "LOOK CLOSER."
    assert e["deck_text"] == "LOOK\nCLOSER."
    assert e["text_exact"] is True
    assert (e["shot"], e["slot"], e["instance"]) == ("s1", "voiceover", "hook")
    assert (e["first_frame"], e["last_frame"]) == (15, 74)


def test_declared_say_override_is_allowed():
    deck = parse_copy({"brand_name": "T", "lines": {"hook": {"text": "GLOWMOP", "say": "Glow mop."}}})
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.0, 1.0)], 30)
    assert e["text"] == "Glow mop."
    assert e["say_declared"] is True
    assert e["text_exact"] is True


def test_undeclared_change_fails_the_audit():
    deck = parse_copy({"brand_name": "T", "lines": {"hook": {"text": "LOOK CLOSER."}}})
    deck.say = lambda cid: "Something else"  # a change no deck field declares
    [e] = audio.voice_audit_entries(deck, [span("s1", "hook", 0.0, 1.0)], 30)
    assert e["text_exact"] is False


def test_shots_without_a_line_and_end_spans_have_no_entry():
    deck = parse_copy({"brand_name": "T", "lines": {"hook": {"text": "HI."}}})
    spans = [span("s1", None, None, None), span("end", None, None, None, kind="end"), span("s2", "hook", None, None)]
    assert audio.voice_audit_entries(deck, spans, 30) == []