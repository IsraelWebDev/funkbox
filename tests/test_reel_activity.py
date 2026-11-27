"""funkbox reel activity: the box where a clip changes, as frame-rect fractions."""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from funkbox import media
from funkbox.reel.activity import active_mask, active_rect, rect_of

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(media.ffmpeg_exe() is None, reason="needs ffmpeg")


def _clip(path: Path, box=(400, 120, 200, 120), w=960, h=540, n=24) -> Path:
    """A still 'desktop' with one 'window' (box = x, y, w, h in pixels) whose content changes every frame."""
    x, y, bw, bh = box
    rng = np.random.default_rng(1)

    def frames():
        for i in range(n):
            f = np.full((h, w, 3), 30, np.uint8)
            f[y : y + bh, x : x + bw] = 15
            k = (i % 4 + 1) * bh // 5  # 'output' scrolling in, line by line
            f[y : y + k, x : x + bw] = rng.integers(100, 255, (k, bw, 1), dtype=np.uint8)
            yield f

    media.encode(frames(), w, h, 12, path.with_suffix(""), mp4=True, gif=False, poster=False)
    return path


def test_rect_of_and_mask():
    frames = np.zeros((3, 10, 20), np.uint8)
    frames[1, 2:5, 4:8] = 200
    mask = active_mask(frames)
    assert mask.sum() == 12
    assert rect_of(mask, margin=0) == (0.2, 0.2, 0.2, 0.3)
    assert rect_of(np.zeros((10, 20), bool)) is None
    assert not active_mask(frames[:1]).any()


def test_active_rect_finds_the_changing_window(tmp_path):
    clip = _clip(tmp_path / "screen.mp4")
    act = active_rect(clip, margin=0)
    x, y, w, h = act.rect
    # the window is at x 400..600, y 120..240 of 960x540; its top rows change in every frame
    assert x == pytest.approx(400 / 960, abs=0.02)
    assert y == pytest.approx(120 / 540, abs=0.03)
    assert x + w == pytest.approx(600 / 960, abs=0.02)
    assert y + h <= 240 / 540 + 0.03
    assert act.focus == (round(x + w / 2, 3), round(y + h / 2, 3))
    assert act.frames >= 6 and 0 < act.changed < 0.1


def test_a_still_clip_has_no_rect(tmp_path):
    def frames():
        for _ in range(12):
            yield np.full((180, 320, 3), 40, np.uint8)

    media.encode(frames(), 320, 180, 12, tmp_path / "still", mp4=True, gif=False, poster=False)
    act = active_rect(tmp_path / "still.mp4")
    assert act.rect is None and act.focus is None and act.changed == 0


def test_cli_prints_a_frame_rect_and_json(tmp_path):
    clip = _clip(tmp_path / "screen.mp4")
    env = {**os.environ}
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "funkbox", "reel", "activity", str(clip), *a],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
    )
    r = run()
    assert r.returncode == 0, r.stderr
    assert '"motion": {"type": "frame", "to": [' in r.stdout
    r = run("--trim", "0", "1", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert len(d["rect"]) == 4 and len(d["focus"]) == 2 and d["frames"] >= 2
