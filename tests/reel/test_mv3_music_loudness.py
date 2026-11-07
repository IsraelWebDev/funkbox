"""MV-3: a variant's music comes from a music set, and each rendered variant is measured against the target."""

import json
import subprocess

import pytest

from funkbox.media import ffmpeg_exe
from funkbox.music.vary import write_set
from funkbox.reel import loudness
from funkbox.reel.variants import expand_reel
from funkbox.reel.variants_render import render_variants

BRIEF = {
    "bpm_range": [96, 124],
    "keys": ["D", "A"],
    "scales": ["dorian", "mixolydian"],
    "programs": [0, 24, 32, 40],
    "drum_patterns": ["straight", "half-time"],
    "swing": [0.0, 0.2],
}
BASE = {"name": "bed", "bpm": 100, "meter": 4, "key": "D", "scale": "minor", "bars": 8, "swing": 0.0,
        "tracks": [{"name": "pad", "program": 0, "octave": 4, "notes": "[^1 ^3 ^5]/1"}]}


def _ffmpeg():
    exe = ffmpeg_exe()
    if not exe:
        pytest.skip("ffmpeg not available")
    return exe


def _quiet(exe, *args):
    subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def test_loudness_moves_by_the_gain_applied(tmp_path):
    exe = _ffmpeg()
    loud, quiet = tmp_path / "loud.wav", tmp_path / "quiet.wav"
    _quiet(exe, "-f", "lavfi", "-i", "sine=frequency=1000:duration=5", "-ac", "2", str(loud))
    _quiet(exe, "-f", "lavfi", "-i", "sine=frequency=1000:duration=5", "-ac", "2", "-af", "volume=-6dB", str(quiet))
    a, b = loudness.measure_lufs(loud), loudness.measure_lufs(quiet)
    assert a is not None and b is not None
    assert abs((a - b) - 6.0) < 0.2


def test_a_file_without_audio_is_not_measured(tmp_path):
    exe = _ffmpeg()
    silent = tmp_path / "silent.mp4"
    _quiet(exe, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-an", str(silent))
    assert loudness.measure_lufs(silent) is None


def test_a_miss_is_reported_and_not_an_error():
    assert loudness.check(-14.6, -14.0)["within_tolerance"] is True
    miss = loudness.check(-17.0, -14.0)
    assert miss["within_tolerance"] is False and miss["off_by_lu"] == -3.0
    ok = loudness.check(-14.0, -14.0)
    assert loudness.all_ok({"a.mp4": miss, "b.mp4": ok}) is False
    assert loudness.all_ok({"a.mp4": ok}) is True
    assert loudness.all_ok({"a.mp4": loudness.check(None, -14.0)}) is None


def test_audio_music_takes_one_score_per_variant(demo, work):
    write_set(BRIEF, 7, 2, demo / "mv3-music", base=BASE, name="BED", force=True)  # validation checks they exist
    reel = json.loads((demo / "demo.reel.json").read_text(encoding="utf-8"))
    paths = ["mv3-music/BED-01.music.json", "mv3-music/BED-02.music.json"]
    variants, labels = expand_reel(reel, {"audio.music": paths}, base_dir=demo, deep=False)
    assert [v["audio"]["music"] for v in variants] == paths
    assert len(labels) == 2


def test_each_variant_renders_its_own_score_and_is_measured(demo, work):
    write_set(BRIEF, 7, 2, demo / "mv3-music", base=BASE, name="BED", force=True)
    reel = json.loads((demo / "demo.reel.json").read_text(encoding="utf-8"))
    reel["audio"] = {"voiceover": False}
    for sh in reel["shots"]:
        sh["duration"] = 3.0
        sh.pop("vo", None)
    vary = {"audio.music": ["mv3-music/BED-01.music.json", "mv3-music/BED-02.music.json"]}
    manifest = render_variants(reel, vary, "mv3", base_dir=demo, out_dir=work, draft=True, with_audio=True)
    assert manifest["count"] == 2
    vdir = work / "mv3-variants"
    for entry in manifest["variants"]:
        sidecar = json.loads((vdir / entry["reel"]).read_text(encoding="utf-8"))
        assert sidecar["audio"]["music"] == vary["audio.music"][entry["index"] - 1]
        mp4s = {k: v for k, v in entry["loudness"].items() if k.endswith(".mp4")}
        assert mp4s, entry["loudness"]
        for name, res in mp4s.items():
            assert res["target"] == -14.0, name
            assert res["lufs"] is not None, name
            assert res["within_tolerance"] is True, (name, res)
        assert entry["loudness_ok"] is True