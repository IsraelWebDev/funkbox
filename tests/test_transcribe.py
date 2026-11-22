"""Speech to text: word timings from faster-whisper, written to words.json and an SRT.

The fixture tests/data/speech_en.mp4 is a black frame with a 7.5 s English sentence spoken by Kokoro-82M (Apache-2.0),
generated for this test and committed as the output of that model.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from funkbox.video import transcribe as tr
from funkbox.video.clip import VideoError

FIXTURE = Path(__file__).parent / "data" / "speech_en.mp4"
KEY_WORDS = ["shua", "chanuka", "party", "candles", "together", "family"]
needs_whisper = pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is None,
    reason="faster-whisper is not installed (pip install -r requirements-transcribe.txt)",
)


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    return tmp_path


def test_srt_timestamps_are_hours_minutes_seconds_and_milliseconds():
    assert tr.srt_time(0) == "00:00:00,000"
    assert tr.srt_time(2.5) == "00:00:02,500"
    assert tr.srt_time(3723.4567) == "01:02:03,457"


def test_unknown_model_is_refused(work):
    with pytest.raises(VideoError, match="model must be one of"):
        tr.transcribe(FIXTURE, model="huge", out_dir=work)


def test_a_language_must_be_a_code(work):
    with pytest.raises(VideoError, match="language"):
        tr.transcribe(FIXTURE, language="english please", out_dir=work)


def test_missing_package_gives_the_install_hint(work, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def without_faster_whisper(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_faster_whisper)
    with pytest.raises(VideoError, match="requirements-transcribe.txt"):
        tr.transcribe(FIXTURE, model="tiny", out_dir=work)


@needs_whisper
def test_speech_becomes_words_with_timings_and_an_srt(work):
    res = tr.transcribe(FIXTURE, model="tiny", language="en", out_dir=work, name="speech")
    assert res.path.name == "speech.words.json" and res.srt.name == "speech.srt"
    data = json.loads(res.path.read_text(encoding="utf-8"))
    assert data["model"] == "tiny" and data["language"] == "en"
    words = data["words"]
    assert words and all(set(w) == {"text", "start", "end", "prob"} for w in words)
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)
    assert all(0 <= w["start"] <= w["end"] <= data["duration"] + 0.05 for w in words)
    heard = {w["text"].strip(".,!?").lower() for w in words}
    missing = [w for w in KEY_WORDS if w not in heard]
    assert len(missing) <= 1, missing  # a small model may mishear one word
    srt = res.srt.read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,") and " --> " in srt


@needs_whisper
def test_a_second_run_never_overwrites_the_first(work):
    first = tr.transcribe(FIXTURE, model="tiny", out_dir=work, name="again")
    second = tr.transcribe(FIXTURE, model="tiny", out_dir=work, name="again")
    assert first.path != second.path and second.path.name == "again-2.words.json"
    assert first.path.exists() and second.srt.name == "again-2.srt"
