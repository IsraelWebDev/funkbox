"""Machine check that a recipe loops seamlessly."""
from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np

from .engine import Engine
from .recipe import Recipe2

BAD_FRAC_MAX = 0.001
SEAM_FACTOR = 2.5
SEAM_SLACK = 0.5
STATIC_STEP = 0.05


@dataclass
class LoopReport:
    ok: bool
    max_diff: int          # 0..255, frame0 vs frame N (t=1)
    bad_pixel_frac: float  # fraction of pixels with any channel diff > 3
    seam_step: float       # mean abs diff between frame N-1 and frame 0
    typical_step: float    # mean abs diff of consecutive frames, sampled every k frames
    reason: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _mad(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def validate_loop(recipe: Recipe2, size: int = 192, frames: int | None = None,
                  engine: Engine | None = None) -> LoopReport:
    """Render t=0 and t=1.0 (grain off) and compare; also compare the seam step with the
    typical frame-to-frame step."""
    rec = copy.deepcopy(recipe)
    if frames is not None:
        rec.settings.fps, rec.settings.seconds = int(frames), 1.0
    n = rec.settings.frames
    aspect = rec.settings.width / rec.settings.height
    if aspect >= 1:
        h, w = size, int(round(size * aspect))
    else:
        w, h = size, int(round(size / aspect))
    own = engine is None
    eng = engine or Engine(w, h)
    try:
        f0 = eng.render_at_t(rec, 0.0, grain=False)
        fn = eng.render_at_t(rec, 1.0, grain=False)
        fprev = eng.render(rec, n - 1, grain=False) if n > 1 else f0
        k = max(1, n // 8)
        steps = []
        for i in range(0, max(1, n - 1), k):
            a = eng.render(rec, i, grain=False)
            b = eng.render(rec, min(i + 1, n), grain=False)
            steps.append(_mad(a, b))
    finally:
        if own:
            eng.release()
    diff = np.abs(f0.astype(np.int16) - fn.astype(np.int16))
    max_diff = int(diff.max())
    bad = float((diff.max(axis=2) > 3).mean())
    seam = _mad(fprev, f0)
    typical = float(np.mean(steps)) if steps else 0.0

    ok, reason = True, "seamless"
    if bad > BAD_FRAC_MAX:
        ok = False
        reason = (f"frame at t=1 differs from t=0 in {bad * 100:.1f}% of pixels "
                  f"(max diff {max_diff}); you probably animate with iTime, or multiply "
                  "iPhase by a non-integer")
    elif seam > SEAM_FACTOR * typical + SEAM_SLACK:
        ok = False
        reason = (f"motion jumps at the loop point: the step from the last frame to the first "
                  f"is {seam:.2f} vs a typical {typical:.2f}; check for iTime or a non-integer "
                  "multiple of iPhase")
    elif typical < STATIC_STEP:
        reason = "static: nothing moves"
    return LoopReport(ok, max_diff, bad, seam, typical, reason)
