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


def test_the_model_follows_the_language_when_none_is_named():
    assert tr.default_model("he") == "large-v3"  # small got half the Hebrew words wrong on the T-2 sample
    assert tr.default_model("en") == "small"  # English: small is within 3% of large-v3 at a third of the time
    assert tr.default_model(None) == "small"  # auto-detect cannot know the language before the model loads
    assert {tr.default_model("he"), tr.default_model("en")} <= set(tr.MODELS)


def test_a_hebrew_transcript_on_a_smaller_model_says_to_rerun_on_the_large_one():
    small = tr.Transcript(Path("a.words.json"), Path("a.srt"), "small", "he", 3.0, [])
    assert "rerun with model=large-v3" in small.text()
    large = tr.Transcript(Path("a.words.json"), Path("a.srt"), "large-v3", "he", 3.0, [])
    assert "rerun" not in large.text()
    english = tr.Transcript(Path("a.words.json"), Path("a.srt"), "small", "en", 3.0, [])
    assert "rerun" not in english.text()


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


@needs_whisper
def test_an_audio_only_file_is_transcribed_too(work):
    import subprocess

    from funkbox.media import require_ffmpeg

    wav = work / "speech.wav"
    subprocess.run([require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(FIXTURE), "-vn", "-y", str(wav)], check=True)  # fmt: skip
    res = tr.transcribe(wav, model="tiny", language="en", out_dir=work, name="audio-only")
    assert res.path.name == "audio-only.words.json" and res.duration > 5
    heard = {w["text"].strip(".,!?").lower() for w in res.words}
    assert len(set(KEY_WORDS) & heard) >= 4
    words = tr.words_in(wav, language="en", model="tiny")
    assert words and all({"text", "start", "end", "prob"} <= set(w) for w in words)
