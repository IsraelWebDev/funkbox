"""Where a clip changes: the box to push in on in a screen recording.

``active_rect`` samples a clip's frames (a few a second, small and grey), marks the pixels that change between
samples, and returns their bounding box as ``[x, y, w, h]`` fractions of the source: the same units as a shot's
``"motion": {"type": "frame", "to": ...}`` rectangle and ``source.focus``. On a screen recording that is the window
or panel where something happened (typing, output, a click), so framing a wide recording for a vertical Short needs
no measuring by hand. It only reads the clip; nothing is written.

    python -m funkbox reel activity clips/screen.mp4 --trim 2 10
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from funkbox.media import require_ffmpeg

from .framing import probe_video

SAMPLE_WIDTH = 320  # frames are compared at this width: enough for UI-sized changes, cheap to decode


@dataclass(frozen=True)
class Activity:
    rect: tuple[float, float, float, float] | None  # [x, y, w, h] fractions of the source; None when nothing moved
    focus: tuple[float, float] | None  # the rect's centre, ready for source.focus
    frames: int  # samples compared
    changed: float  # fraction of the picture that changed at least once

    def as_dict(self) -> dict:
        return {
            "rect": list(self.rect) if self.rect else None,
            "focus": list(self.focus) if self.focus else None,
            "frames": self.frames,
            "changed": self.changed,
        }


def _gray_frames(path: Path, trim: tuple[float, float] | None, fps: float, w: int, h: int) -> np.ndarray:
    cmd = [require_ffmpeg(), "-hide_banner", "-loglevel", "error"]
    if trim:
        cmd += ["-ss", f"{trim[0]:.3f}", "-to", f"{trim[1]:.3f}"]
    cmd += ["-i", str(path), "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode:
        raise ValueError(f"{path}: ffmpeg could not read it ({r.stderr.decode('utf-8', 'replace').strip()[-200:]})")
    n = len(r.stdout) // (w * h)
    return np.frombuffer(r.stdout[: n * w * h], dtype=np.uint8).reshape(n, h, w)


def active_mask(frames: np.ndarray, threshold: int = 12, min_frames: int = 1) -> np.ndarray:
    """True where a pixel's grey level moves by more than ``threshold`` between consecutive samples in at least
    ``min_frames`` of them. Sensor-free sources (screen recordings) barely flicker, so a small threshold works."""
    if len(frames) < 2:
        return np.zeros(frames.shape[1:], dtype=bool)
    diff = np.abs(np.diff(frames.astype(np.int16), axis=0)) > threshold
    return diff.sum(axis=0) >= min_frames


def rect_of(mask: np.ndarray, margin: float = 0.01) -> tuple[float, float, float, float] | None:
    """Bounding box of the True pixels as [x, y, w, h] fractions, grown by ``margin`` on each side and clamped."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    h, w = mask.shape
    x0 = max(0.0, xs.min() / w - margin)
    y0 = max(0.0, ys.min() / h - margin)
    x1 = min(1.0, (xs.max() + 1) / w + margin)
    y1 = min(1.0, (ys.max() + 1) / h + margin)
    return tuple(round(v, 3) for v in (x0, y0, x1 - x0, y1 - y0))  # type: ignore[return-value]


def active_rect(
    path: str | Path,
    trim: tuple[float, float] | None = None,
    fps: float = 4.0,
    threshold: int = 12,
    margin: float = 0.01,
) -> Activity:
    """Sample ``path`` (within ``trim`` seconds) at ``fps`` and return the box where it changes."""
    path = Path(path)
    info = probe_video(path)
    w = min(SAMPLE_WIDTH, info.width) // 2 * 2
    h = max(2, round(info.height * w / info.width) // 2 * 2)
    frames = _gray_frames(path, trim, fps, w, h)
    mask = active_mask(frames, threshold)
    rect = rect_of(mask, margin)
    focus = (round(rect[0] + rect[2] / 2, 3), round(rect[1] + rect[3] / 2, 3)) if rect else None
    return Activity(rect, focus, len(frames), round(float(mask.mean()), 4))
