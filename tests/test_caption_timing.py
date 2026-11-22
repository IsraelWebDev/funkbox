"""T-3: captions timed from a transcript's words (words.json). The text drawn is always the approved copy; the
transcript only supplies when each word appears."""

import json
import re
from pathlib import Path

import numpy as np
import pytest

from funkbox.reel import captions
from funkbox.reel import schema as S
from funkbox.video import clip
from test_video import make_clip

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "fonts"
W, H = 1080, 1920


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    return tmp_path


def fonts():
    return captions.Fonts(FONTS / "Oswald-SemiBold.ttf", FONTS / "DMSerifDisplay-Regular.ttf", FONTS / "Rubik-Bold.ttf")


def layout(text):
    style = captions.Style(font="caption", size=0.05, box="primary")
    return captions.layout_block("c", text, style, fonts(), S.Brand(), W, H, anchor="center", safe_area="reels")


def w(text, start, end):
    return {"text": text, "start": start, "end": end, "prob": 0.9}


def test_word_timings_follow_the_transcript_for_the_approved_text():
    words = [w("Shua", 0.2, 0.5), w("chanuka,", 0.6, 1.0), w("party", 1.2, 1.5)]
    assert captions.word_timings("SHUA Chanuka, party!", words) == [(0.2, 0.5), (0.6, 1.0), (1.2, 1.5)]


def test_a_misheard_word_takes_the_time_between_its_neighbours():
    got = captions.word_timings("one two three", [w("one", 0.0, 0.4), w("tu", 0.5, 0.9), w("three", 1.6, 2.0)])
    assert got[0] == (0.0, 0.4) and got[2] == (1.6, 2.0)
    assert 0.4 <= got[1][0] <= got[1][1] <= 1.6
    missing = captions.word_timings("one two three", [w("one", 0.0, 0.4), w("three", 1.6, 2.0)])
    assert 0.4 <= missing[1][0] <= missing[1][1] <= 1.6 and missing[2] == (1.6, 2.0)


def test_hebrew_words_match_in_order():
    words = [w("שלום", 0.1, 0.4), w("עולם", 0.5, 0.9)]
    assert captions.word_timings("שלום עולם", words) == [(0.1, 0.4), (0.5, 0.9)]


def test_no_matching_word_gives_no_timing():
    assert captions.word_timings("alpha beta", [w("gamma", 0.0, 1.0)]) is None
    assert captions.word_timings("alpha beta", []) is None


def test_only_the_words_that_have_started_show():
    timings = [(0.5, 0.8), (1.0, 1.3), (2.0, 2.4)]
    assert captions.words_visible(timings, 0.2) == 0
    assert captions.words_visible(timings, 0.5) == 1
    assert captions.words_visible(timings, 1.5) == 2
    assert captions.words_visible(timings, 9.0) == 3


def test_word_reveal_follows_real_timings_not_even_spacing():
    lay = layout("ONE TWO THREE")
    ends = [m.end() for m in re.finditer(r"\S+", lay.text)]
    timings = [(0.0, 0.2), (0.3, 0.5), (2.0, 2.2)]  # the third word is said late
    n = lambda t: captions.reveal_count(lay, "words", t, timings=timings)  # noqa: E731
    assert n(-0.1) == 0 and n(0.1) == ends[0] and n(1.0) == ends[1] and n(1.9) == ends[1]
    assert n(2.0) == ends[2]
    assert captions.reveal_seconds(lay, "words", timings=timings) == 2.0


def test_a_bad_words_file_is_refused_with_its_fault(work):
    p = make_clip(work / "c.mp4", colours=((40, 40, 40),) * 3, size="640x360")
    bad = work / "bad.json"
    bad.write_text(json.dumps({"words": [{"text": "x", "end": 1.0}]}))
    with pytest.raises(clip.VideoError, match="words"):
        clip.subtitles(p, cues=[{"start": 0.5, "end": 1.5, "text": "x"}], words=bad)


def test_timed_subtitles_reveal_each_word_when_it_is_said(work):
    p = make_clip(work / "c.mp4", colours=((40, 40, 40),) * 3, size="640x360")
    wj = work / "words.json"
    wj.write_text(json.dumps({"words": [w("EXACT", 0.6, 0.9), w("WORDS", 1.0, 1.4)]}), encoding="utf-8")
    res = clip.subtitles(p, cues=[{"start": 0.5, "end": 1.5, "text": "EXACT WORDS"}], style="red-box", words=wj)
    audit = json.loads(res.audit.read_text(encoding="utf-8"))
    assert audit[0]["text"] == "EXACT WORDS" and audit[0]["text_exact"] and audit[0]["timed"]
    assert [s["start"] for s in audit[0]["states"]] == [0.6, 1.0]
    assert [s["words"] for s in audit[0]["states"]] == [1, 2]

    bg = np.asarray(clip.frame_at(res.path, 0.1, 640)).astype(int)

    def ink(t):
        a = np.asarray(clip.frame_at(res.path, t, 640)).astype(int)
        return int((np.abs(a - bg).sum(axis=2) > 60).sum())

    before, one, two, after = ink(0.3), ink(0.8), ink(1.2), ink(1.8)
    assert before == 0 and 0 < one < two and after == 0
    assert abs(ink(0.95) - one) < 0.05 * one  # the first word holds until the second is said


def test_a_cue_the_transcript_never_heard_is_drawn_untimed(work):
    p = make_clip(work / "c.mp4", colours=((40, 40, 40),) * 3, size="640x360")
    wj = work / "words.json"
    wj.write_text(json.dumps({"words": [w("nothing", 0.0, 1.0)]}), encoding="utf-8")
    res = clip.subtitles(p, cues=[{"start": 0.5, "end": 1.5, "text": "EXACT WORDS"}], style="red-box", words=wj)
    audit = json.loads(res.audit.read_text(encoding="utf-8"))
    assert audit[0]["timed"] is False and audit[0]["text_exact"] and audit[0]["states"] == []
