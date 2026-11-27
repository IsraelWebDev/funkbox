"""`funkbox loop render --target canvas`: a Spotify Canvas loop (9:16, 720-1080 px short side, 3-8 s, MP4)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from funkbox.gl.recipe import Settings2
from funkbox.loops.targets import apply_target, check_seconds

ROOT = Path(__file__).resolve().parent.parent


def test_canvas_defaults_to_720x1280_and_pulls_the_length_into_3_to_8_s():
    st = apply_target(
        Settings2(width=540, height=540, seconds=2.0), "canvas", explicit_size=False, explicit_seconds=False
    )
    assert (st.width, st.height, st.seconds) == (720, 1280, 3.0)
    st = apply_target(Settings2(seconds=12.0), "canvas", explicit_size=False, explicit_seconds=False)
    assert st.seconds == 8.0
    st = apply_target(Settings2(seconds=4.0), "canvas", explicit_size=False, explicit_seconds=False)
    assert st.seconds == 4.0  # already inside the window: left alone


@pytest.mark.parametrize(
    "w,h,why",
    [(1080, 1080, "is not 9:16"), (540, 960, "short side"), (1215, 2160, "short side")],
)
def test_canvas_names_a_size_it_refuses(w, h, why):
    with pytest.raises(ValueError, match=why):
        apply_target(Settings2(width=w, height=h, seconds=4.0), "canvas", explicit_size=True, explicit_seconds=False)


def test_canvas_accepts_1080x1920_as_given():
    st = apply_target(
        Settings2(width=1080, height=1920, seconds=8.0), "canvas", explicit_size=True, explicit_seconds=True
    )
    assert (st.width, st.height, st.seconds) == (1080, 1920, 8.0)


def test_canvas_checks_a_length_given_on_purpose_and_never_changes_it():
    with pytest.raises(ValueError, match="10 s is outside 3-8 s"):
        apply_target(Settings2(seconds=10.0), "canvas", explicit_size=False, explicit_seconds=True)
    with pytest.raises(ValueError, match="outside 3-8 s"):
        check_seconds(2.5, "canvas")
    check_seconds(6.0, "canvas")


def test_an_audio_set_length_is_left_for_the_later_check():
    st = apply_target(Settings2(seconds=30.0), "canvas", explicit_size=False, explicit_seconds=False, length_fixed=True)
    assert st.seconds == 30.0 and (st.width, st.height) == (720, 1280)


def test_unknown_target():
    with pytest.raises(ValueError, match="unknown target"):
        apply_target(Settings2(), "billboard", explicit_size=False, explicit_seconds=False)


def _run(tmp_path, *extra):
    env = {**os.environ, "FUNK_OUT": str(tmp_path / "out"), "FUNK_INPUTS": str(tmp_path)}
    cmd = [sys.executable, "-m", "funkbox", "loop", "render", "--preset", "lava-lamp", "--fps", "8"]
    cmd += ["--out-dir", str(tmp_path / "out"), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env=env)


def test_cli_renders_a_canvas_loop_with_the_check_on(gl, tmp_path):
    r = _run(tmp_path, "--target", "canvas", "--name", "cv")
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads((tmp_path / "out" / "cv.json").read_text(encoding="utf-8"))
    assert (doc["settings"]["width"], doc["settings"]["height"]) == (720, 1280)
    assert 3 <= doc["settings"]["seconds"] <= 8
    assert doc["loop_check"]["status"] == "ok"
    assert (tmp_path / "out" / "cv.mp4").stat().st_size > 0


def test_cli_refuses_what_canvas_cannot_take(tmp_path):
    r = _run(tmp_path, "--target", "canvas", "--seconds", "12")
    assert r.returncode == 2 and "12 s is outside 3-8 s" in r.stderr, r.stderr
    r = _run(tmp_path, "--target", "canvas", "--size", "640")
    assert r.returncode == 2 and "short side" in r.stderr, r.stderr
    r = _run(tmp_path, "--target", "canvas", "--gif")
    assert r.returncode == 2 and "MP4" in r.stderr, r.stderr
