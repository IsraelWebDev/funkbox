"""Reel voice-over timing: each spoken line's words, from a transcript, put on its caption's own clock."""

import json

import pytest

from funkbox.reel import voice_timing as vt
from funkbox.video.clip import VideoError


def fake_words(*triples):
    return [{"text": t, "start": s, "end": e, "prob": 0.9} for t, s, e in triples]


def flat(pairs):
    return [x for pair in pairs for x in pair]


HEARD = fake_words(("one", 0.0, 0.4), ("two", 0.5, 0.9), ("three", 1.0, 1.3))


def test_a_hebrew_line_is_transcribed_as_hebrew_and_others_as_english():
    assert vt._language("שלום לכולם") == "he"
    assert vt._language("hello everyone") == "en"
    assert vt._language("שלום and hello") == "he"


def test_the_caption_clock_starts_at_the_caption_not_at_the_shot(monkeypatch, tmp_path):
    monkeypatch.setattr(vt, "line_words", lambda wav, say: HEARD)
    wav = tmp_path / "x.wav"
    # the voice starts 0.1 s into the shot, the caption 0.1 s into the shot: the words keep their times
    same = vt.caption_timings("one two three", wav, "one two three", 2.1, 2.0, 0.1)
    assert flat(same) == pytest.approx([0.0, 0.4, 0.5, 0.9, 1.0, 1.3])
    # a voice that starts 0.4 s into the shot, with the caption at 0.1 s, moves every word by 0.3 s
    shifted = vt.caption_timings("one two three", wav, "one two three", 2.4, 2.0, 0.1)
    assert flat(shifted) == pytest.approx([0.3, 0.7, 0.8, 1.2, 1.3, 1.6])


def test_a_caption_that_does_not_match_what_was_heard_is_not_timed(monkeypatch, tmp_path):
    monkeypatch.setattr(vt, "line_words", lambda wav, say: fake_words(("zebra", 0.0, 0.5)))
    assert vt.caption_timings("one two three", tmp_path / "x.wav", "x", 0.0, 0.0, 0.0) is None


def test_no_words_heard_means_no_timings(monkeypatch, tmp_path):
    monkeypatch.setattr(vt, "line_words", lambda wav, say: None)
    assert vt.caption_timings("one", tmp_path / "x.wav", "one", 0.0, 0.0, 0.0) is None


def test_the_transcript_is_cached_beside_the_wav_and_read_back(monkeypatch, tmp_path):
    wav = tmp_path / "abc123.wav"
    wav.write_bytes(b"")
    (tmp_path / "abc123.words.json").write_text(json.dumps({"language": "en", "words": HEARD}), encoding="utf-8")

    def must_not_run(*a, **k):
        raise AssertionError("the cached transcript should be used")

    monkeypatch.setattr("funkbox.video.transcribe.words_in", must_not_run)
    assert vt.line_words(wav, "one two three") == HEARD


def test_without_faster_whisper_the_line_keeps_its_estimate_and_caches_nothing(monkeypatch, tmp_path):
    wav = tmp_path / "def456.wav"
    wav.write_bytes(b"")

    def not_installed(*a, **k):
        raise VideoError("speech to text needs faster-whisper")

    monkeypatch.setattr("funkbox.video.transcribe.words_in", not_installed)
    assert vt.line_words(wav, "one two three") is None
    assert not (tmp_path / "def456.words.json").exists()  # a later install is used, not a cached miss


def test_a_non_media_file_is_refused_before_any_model_loads(tmp_path, monkeypatch):
    from funkbox.video import transcribe as tr

    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    junk = tmp_path / "junk.wav"
    junk.write_text("not audio", encoding="utf-8")
    with pytest.raises(VideoError, match="not a media file"):
        tr.words_in(junk, language="en", model="tiny")
