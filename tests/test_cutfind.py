"""T-4: propose cuts for approval: pauses from the audio, repeated takes from the transcript. Nothing is rendered."""

import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from funkbox.media import require_ffmpeg
from funkbox.video import cutfind

BENCH_TOL = 0.04  # seconds: the +-40 ms the plan asks for, measured against known gap edges


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    return tmp_path


def w(text, start, end):
    return {"text": text, "start": start, "end": end, "prob": 0.9}


# Known truth for the benchmark: tone, gap, tone, short gap, tone, long gap, tone (seconds).
TRUTH_GAPS = [(1.5, 2.4), (3.5, 3.8), (5.0, 6.5)]
TRUTH_TOTAL = 7.5


def bench_wav(path: Path) -> Path:
    sr = 16000
    t = np.arange(int(TRUTH_TOTAL * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220 * t)
    for a, b in TRUTH_GAPS:
        x[int(a * sr) : int(b * sr)] = 0.0
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def bench_mp4(work: Path) -> Path:
    wav = bench_wav(work / "tone.wav")
    out = work / "bench.mp4"
    subprocess.run(
        [
            require_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x180:r=25:d={TRUTH_TOTAL}",
            "-i",
            str(wav),
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(out),
        ],
        check=True,
    )
    return out


def test_silencedetect_output_is_parsed():
    err = (
        "[silencedetect @ 0x1] silence_start: 1.5\n"
        "[silencedetect @ 0x1] silence_end: 2.4 | silence_duration: 0.9\n"
        "[silencedetect @ 0x1] silence_start: 9.0\n"
    )
    assert cutfind.parse_silences(err, duration=10.0) == [(1.5, 2.4), (9.0, 10.0)]


def test_pauses_are_found_within_the_benchmark_tolerance(work):
    found = cutfind.silences(bench_mp4(work), min_s=0.35)
    assert len(found) == 2  # the 0.3 s gap is shorter than the minimum and is not a pause
    for (fs, fe), (ts, te) in zip(found, TRUTH_GAPS[::2]):
        assert abs(fs - ts) <= BENCH_TOL and abs(fe - te) <= BENCH_TOL, (fs, fe, ts, te)


def test_pause_removals_keep_a_breath_on_each_side():
    removals = cutfind.pause_removals([(1.5, 2.4), (3.5, 3.8), (5.0, 6.5)], duration=7.5, keep_s=0.25, min_pause_s=0.6)
    assert len(removals) == 2
    (a1, b1), (a2, b2) = removals
    assert abs(a1 - 1.75) <= BENCH_TOL and abs(b1 - 2.15) <= BENCH_TOL
    assert abs(a2 - 5.25) <= BENCH_TOL and abs(b2 - 6.25) <= BENCH_TOL


def test_a_pause_at_the_very_start_or_end_keeps_only_the_breath_next_to_speech():
    removals = cutfind.pause_removals([(0.0, 1.0), (6.0, 7.5)], duration=7.5, keep_s=0.25, min_pause_s=0.6)
    assert removals[0][0] == 0.0 and abs(removals[0][1] - 0.75) <= BENCH_TOL
    assert abs(removals[1][0] - 6.25) <= BENCH_TOL and removals[1][1] == 7.5


def test_a_repeated_take_drops_the_earlier_one_and_keeps_the_later():
    earlier = [
        w("I'm", 0.0, 0.2),
        w("recreating", 0.2, 0.7),
        w("my", 0.7, 0.8),
        w("soft", 0.8, 1.1),
        w("disk", 1.1, 1.5),
    ]
    later = [w("I'm", 2.6, 2.8), w("recreating", 2.8, 3.3), w("my", 3.3, 3.4), w("soft", 3.4, 3.7), w("disk", 3.7, 4.1)]
    words = earlier + later + [w("tzitzot", 4.2, 4.6)]
    removals = cutfind.retake_removals(words, min_words=4, max_gap_s=30.0)
    assert len(removals) == 1
    a, b, text = removals[0]
    assert a == 0.0 and b == 1.5 and text == "I'm recreating my soft disk"


def test_a_phrase_repeated_far_apart_is_not_a_retake():
    phrase = [w("one", 0.0, 0.2), w("two", 0.2, 0.4), w("three", 0.4, 0.6), w("four", 0.6, 0.8)]
    far = [w(x["text"], x["start"] + 100.0, x["end"] + 100.0) for x in phrase]
    assert cutfind.retake_removals(phrase + far, min_words=4, max_gap_s=30.0) == []


def test_no_transcript_means_no_retakes_and_hebrew_matches_in_order():
    assert cutfind.retake_removals([], min_words=4, max_gap_s=30.0) == []
    heb = [w("שלום", 0.0, 0.3), w("לכולם", 0.3, 0.6), w("היום", 0.6, 0.9), w("טוב", 0.9, 1.2)]
    again = [w(x["text"], x["start"] + 5.0, x["end"] + 5.0) for x in heb]
    removals = cutfind.retake_removals(heb + again, min_words=4, max_gap_s=30.0)
    assert len(removals) == 1 and removals[0][0] == 0.0


def test_cutplan_writes_a_plan_and_renders_nothing(work):
    src = bench_mp4(work)
    words = work / "w.json"
    words.write_text(json.dumps({"words": []}), encoding="utf-8")
    res = cutfind.cutplan(src, words=words, name="bench-plan")
    data = json.loads(res.path.read_text(encoding="utf-8"))
    assert res.path.name == "bench-plan.cutplan.json"
    assert data["source"] == "bench.mp4" and data["duration"] == pytest.approx(TRUTH_TOTAL, abs=0.1)
    kept = sum(b - a for a, b in data["keep"])
    # the 0.9 s gap loses 0.5 s (a breath each side stays), the 1.5 s gap loses 1.0 s; the 0.3 s gap is kept
    assert kept == pytest.approx(TRUTH_TOTAL - 0.4 - 1.0, abs=0.1)
    assert all(c["path"].endswith("bench.mp4") and c["start"] < c["end"] for c in data["cuts"])
    assert not (work / "out" / "video" / "bench-plan.mp4").exists()  # a plan, not a render
    assert "pause" in res.text() and "approve" in res.text()


def test_cutplan_refuses_a_missing_words_file(work):
    with pytest.raises(cutfind.VideoError, match="words"):
        cutfind.cutplan(bench_mp4(work), words=work / "nope.json", name="x")


def test_filler_words_are_proposed_and_lookalikes_are_not():
    words = [
        w("So", 0.0, 0.2),
        w("Um,", 0.2, 0.5),
        w("the", 0.5, 0.6),
        w("umbrella", 0.6, 1.0),
        w("uh", 1.0, 1.2),
        w("hummus", 1.2, 1.6),
    ]
    removals = cutfind.filler_removals(words)
    assert removals == [(0.2, 0.5, "Um,"), (1.0, 1.2, "uh")]
    assert cutfind.filler_removals(words, fillers=()) == []
    assert cutfind.filler_removals(words, fillers=("so",)) == [(0.0, 0.2, "So")]


def test_a_filler_cut_runs_past_its_end_but_stops_before_the_next_word():
    # the sound of "um" runs past the word's end time, so the cut reaches 0.18 s further...
    words = [w("so", 0.0, 0.4), w("um,", 0.4, 0.6), w("the", 0.9, 1.1), w("uh,", 1.5, 1.7)]
    assert cutfind.filler_removals(words) == [(0.4, 0.78, "um,"), (1.5, 1.88, "uh,")]
    # ...but not into the next word, which starts at 0.62 here
    tight = [w("um,", 0.4, 0.6), w("or", 0.62, 0.9)]
    assert cutfind.filler_removals(tight) == [(0.4, 0.62, "um,")]


def test_cutplan_proposes_fillers_from_the_words_and_can_turn_them_off(work):
    src = bench_mp4(work)
    words = work / "w.json"
    words.write_text(json.dumps({"words": [w("hello", 0.2, 0.6), w("um", 0.7, 1.0), w("there", 1.0, 1.4)]}), "utf-8")
    res = cutfind.cutplan(src, words=words, name="fill-plan")
    data = json.loads(res.path.read_text(encoding="utf-8"))
    assert {"start": 0.7, "end": 1.0, "reasons": ["filler: um"]} in data["removed"]
    assert not any(c["start"] < 0.85 < c["end"] for c in data["cuts"])  # the "um" is not in any kept cut
    assert "um" in data["settings"]["fillers"] and "1 filler word(s)" in res.text()
    off = cutfind.cutplan(src, words=words, fillers=(), name="fill-off")
    assert not any("filler" in r for x in off.removed for r in x["reasons"])


def test_the_fillers_option_reads_default_none_and_a_list():
    from funkbox.video import tools

    assert tools._fillers("default") == cutfind.FILLERS
    assert tools._fillers("none") == () and tools._fillers("") == ()
    assert tools._fillers(" um, like ,") == ("um", "like")


def test_hebrew_fillers_are_proposed_and_real_words_are_not():
    words = [
        w("אז", 0.0, 0.2),  # "so": a real word, kept
        w("אמ,", 0.2, 0.5),  # filler with punctuation
        w("אמא", 0.5, 0.9),  # "mom": looks like "אמ" but is not a filler
        w("הממ", 0.9, 1.2),
        w("כאילו", 1.2, 1.6),  # "like": kept on purpose
        w("אההה", 1.6, 2.0),
    ]
    removals = cutfind.filler_removals(words)
    # the last filler has no word after it, so its cut runs the full tail past 2.0
    assert removals == [(0.2, 0.5, "אמ,"), (0.9, 1.2, "הממ"), (1.6, 2.18, "אההה")]
    assert "אמא" not in {t for _, _, t in removals}


def test_a_filler_quoted_after_a_reporting_word_is_kept_and_flagged():
    words = [w("he", 0.0, 0.2), w("said,", 0.2, 0.5), w("uh", 0.5, 0.7), w("hi", 0.7, 1.0)]
    assert cutfind.filler_removals(words) == []
    assert cutfind.kept_fillers(words) == [(0.5, 0.7, "uh", "quoted after 'said'")]


def test_a_hebrew_filler_quoted_after_a_reporting_word_is_kept():
    words = [w("הוא", 0.0, 0.2), w("אמר", 0.2, 0.5), w("אההה", 0.5, 0.9), w("שלום", 0.9, 1.2)]
    assert cutfind.filler_removals(words) == []
    assert len(cutfind.kept_fillers(words)) == 1


def test_a_filler_after_an_ordinary_word_is_still_cut():
    words = [w("so", 0.0, 0.2), w("um,", 0.2, 0.5), w("it", 0.6, 0.8), w("works", 0.8, 1.1)]
    assert [r[2] for r in cutfind.filler_removals(words)] == ["um,"]
    assert cutfind.kept_fillers(words) == []


def test_a_filler_the_speaker_names_as_a_sound_is_kept():
    # "I say um, or uh, in the middle": the sounds are the subject of the sentence, not the speaker's filler
    words = [
        w("I", 0.0, 0.2),
        w("say", 0.2, 0.5),
        w("um,", 0.5, 0.8),
        w("or", 0.9, 1.1),
        w("uh,", 1.1, 1.4),
        w("in", 1.5, 1.6),
        w("the", 1.6, 1.7),
        w("middle", 1.7, 2.0),
    ]
    assert cutfind.filler_removals(words) == []
    assert [k[2] for k in cutfind.kept_fillers(words)] == ["um,", "uh,"]


def test_a_filler_in_a_list_of_sounds_is_kept():
    words = [
        w("the", 0.0, 0.2),
        w("sounds", 0.2, 0.5),
        w("um", 0.5, 0.8),
        w("or", 0.9, 1.1),
        w("uh", 1.1, 1.4),
        w("hm", 1.5, 1.8),
        w("are", 1.9, 2.0),
    ]
    assert [k[2] for k in cutfind.kept_fillers(words)] == ["um", "uh"]


def test_cutplan_lists_kept_fillers_in_the_plan_for_review(work):
    src = bench_mp4(work)
    words = work / "w.json"
    words.write_text(
        json.dumps({"words": [w("he", 0.2, 0.4), w("said", 0.4, 0.6), w("uh", 0.7, 0.9), w("hi", 0.9, 1.2)]}), "utf-8"
    )
    res = cutfind.cutplan(src, words=words, name="kept-plan")
    data = json.loads(res.path.read_text(encoding="utf-8"))
    assert data["kept_fillers"] == [{"start": 0.7, "end": 0.9, "text": "uh", "reason": "quoted after 'said'"}]
    assert "1 filler word(s) kept" in res.text()
