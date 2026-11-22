"""T-6: an acceptance report next to a rendered video: loudness against -14 LUFS, peak, pauses left, and the cut plan."""

import hashlib
import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from funkbox.media import require_ffmpeg
from funkbox.video import report
from funkbox.video.clip import VideoError

SR = 16000
SECONDS = 7.5
GAPS = [(1.5, 2.4), (5.0, 6.5)]  # two pauses of 0.9 s and 1.5 s, both at least the 0.6 s threshold
# A 1 kHz sine at this amplitude measures about -14.0 LUFS (EBU R128 K-weighting is flat at 1 kHz).
AMP_TARGET = 0.3055


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    return tmp_path


def make_mp4(work: Path, name: str, amp: float, spike: bool = False) -> Path:
    t = np.arange(int(SECONDS * SR)) / SR
    x = amp * np.sin(2 * np.pi * 1000 * t)
    for a, b in GAPS:
        x[int(a * SR) : int(b * SR)] = 0.0
    wav = work / f"{name}.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    out = work / f"{name}.mp4"
    subprocess.run(
        [
            require_ffmpeg(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=25:d={SECONDS}",
            "-i", str(wav), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(out),
        ],
        check=True,
    )  # fmt: skip
    return out


def write_plan(work: Path, keep, removed) -> Path:
    p = work / "plan.cutplan.json"
    p.write_text(json.dumps({"duration": SECONDS, "keep": keep, "removed": removed}), encoding="utf-8")
    return p


def test_a_render_at_the_target_with_clean_levels_passes_and_is_left_unchanged(work):
    src = make_mp4(work, "final", AMP_TARGET)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    rep = report.acceptance(src)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before  # the report only reads
    assert rep.path == work / "final.report.json" and rep.path.exists()
    assert abs(rep.loudness_lufs - (-14.0)) <= 0.5
    assert rep.peak_db is not None and rep.peak_db < -5
    assert rep.pauses_left == 2 and abs(rep.pause_s_left - 2.4) <= 0.1
    assert rep.checks == {"has_audio": True, "loudness": True, "peak": True, "plan": None}
    assert rep.verdict == "pass"
    data = json.loads(rep.path.read_text(encoding="utf-8"))
    assert data["loudness"]["target"] == -14.0 and data["verdict"] == "pass"


def test_a_quiet_render_misses_the_loudness_target(work):
    rep = report.acceptance(make_mp4(work, "quiet", AMP_TARGET / 4))  # about 12 dB under the target
    assert rep.checks["loudness"] is False and rep.verdict == "fail"


def test_a_silent_render_fails_loudness_rather_than_being_unmeasured(work):
    rep = report.acceptance(make_mp4(work, "silent", 0.0))
    assert rep.checks["loudness"] is False and rep.verdict == "fail"


def test_a_render_at_full_scale_fails_the_peak_check(work):
    rep = report.acceptance(make_mp4(work, "loud", 1.0))
    assert rep.peak_db is not None and rep.peak_db > report.PEAK_LIMIT_DB
    assert rep.checks["peak"] is False and rep.verdict == "fail"


def test_the_length_is_checked_against_the_cut_plan(work):
    src = make_mp4(work, "cut", AMP_TARGET)
    keep_all = write_plan(work, [[0.0, SECONDS]], [])
    rep = report.acceptance(src, cutplan=keep_all, name="cut-ok")
    assert rep.checks["plan"] is True and rep.verdict == "pass"
    keep_short = write_plan(work, [[0.0, 3.0]], [{"start": 3.0, "end": SECONDS, "reasons": ["pause"]}])
    rep2 = report.acceptance(src, cutplan=keep_short, name="cut-bad")
    assert rep2.checks["plan"] is False and rep2.verdict == "fail"


def test_the_removed_cuts_are_summed_by_reason(work):
    src = make_mp4(work, "sum", AMP_TARGET)
    plan = write_plan(
        work,
        [[0.0, 1.0], [2.0, 5.0], [6.0, SECONDS]],
        [
            {"start": 1.0, "end": 2.0, "reasons": ["pause"]},
            {"start": 5.0, "end": 6.0, "reasons": ["filler: um", "retake: a b c d"]},
        ],
    )
    rep = report.acceptance(src, cutplan=plan, name="sum-report")
    assert rep.removed == {"count": 2, "seconds": 2.0, "by_reason": {"pause": 1, "filler": 1, "retake": 1}}
    assert "2 span(s), 2.0s removed" in rep.text()


def test_without_a_plan_the_plan_check_is_not_made(work):
    rep = report.acceptance(make_mp4(work, "noplan", AMP_TARGET), name="noplan-report")
    assert rep.removed is None and rep.checks["plan"] is None
    assert json.loads(rep.path.read_text(encoding="utf-8"))["cut_plan"]["removed"] is None


def test_a_second_report_never_overwrites_the_first(work):
    src = make_mp4(work, "twice", AMP_TARGET)
    first = report.acceptance(src)
    second = report.acceptance(src)
    assert first.path.name == "twice.report.json" and second.path.name == "twice-2.report.json"
    assert first.path.exists() and second.path.exists()


def test_a_plan_that_is_not_a_cut_plan_is_refused(work):
    src = make_mp4(work, "bad", AMP_TARGET)
    junk = work / "junk.json"
    junk.write_text("{}", encoding="utf-8")
    with pytest.raises(VideoError, match="not a cut plan"):
        report.acceptance(src, cutplan=junk)
    with pytest.raises(VideoError, match="not found"):
        report.acceptance(src, cutplan=work / "missing.json")
