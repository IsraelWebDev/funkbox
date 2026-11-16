"""Machine check that a recipe loops seamlessly."""

from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np

from funkbox.config import resolve_path

from .engine import Engine
from .recipe import Recipe2

BAD_FRAC_MAX = 0.001
SEAM_FACTOR = 2.5
SEAM_SLACK = 0.5
AUDIO_SEAM_FACTOR = 1.5  # audio loops: the seam step against the largest step inside the loop
STATIC_STEP = 0.05


@dataclass
class LoopReport:
    ok: bool
    max_diff: int  # 0..255, frame0 vs frame N (t=1)
    bad_pixel_frac: float  # fraction of pixels with any channel diff > 3
    seam_step: float  # mean abs diff between frame N-1 and frame 0
    typical_step: float  # mean abs diff of consecutive frames, sampled every k frames
    reason: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def loop_check_entry(rep: LoopReport | None, reason: str = "audio") -> dict:
    """The sidecar's "loop_check" entry: the report's key numbers if the seam was checked, else why it was not."""
    if rep is None:
        return {"status": "skipped", "reason": reason}
    return {
        "status": "ok" if rep.ok else "failed",
        "max_diff": rep.max_diff,
        "bad_pixel_frac": round(rep.bad_pixel_frac, 6),
        "seam_step": round(rep.seam_step, 4),
        "typical_step": round(rep.typical_step, 4),
        "reason": rep.reason,
    }


def _mad(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def validate_loop(
    recipe: Recipe2, size: int = 192, frames: int | None = None, engine: Engine | None = None
) -> LoopReport:
    """Render t=0 and t=1.0 (grain off) and compare; also compare the seam step with the
    typical frame-to-frame step.

    An audio loop (settings.audio_loop) moves in bursts: a kick makes one step many times the mean, and a section
    that starts on a beat has such a step at its seam. So there every step of the loop is measured, and the steps
    at the wrap (between frames whose audio window reaches across it) are compared with the largest step in the
    rest of the loop: the wrap may be as abrupt as the music already is, not more."""
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
        audio_loop = bool(rec.settings.audio and rec.settings.audio_loop)
        steps = []
        wrap_step = inner_step = 0.0
        if audio_loop:  # every step, each frame rendered once
            from .audio import wrap_frames

            at_wrap = wrap_frames(resolve_path(rec.settings.audio, rec.base_dir), n)
            a = f0
            for i in range(1, n):
                b = eng.render(rec, i, grain=False)
                steps.append(_mad(a, b))
                if i in at_wrap or i - 1 in at_wrap:
                    wrap_step = max(wrap_step, steps[-1])
                else:
                    inner_step = max(inner_step, steps[-1])
                a = b
        else:
            k = max(1, n // 8)
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
    wrap_step = max(wrap_step, seam)
    typical = float(np.mean(steps)) if steps else 0.0

    ok, reason = True, "seamless"
    if bad > BAD_FRAC_MAX:
        ok = False
        reason = (
            f"frame at t=1 differs from t=0 in {bad * 100:.1f}% of pixels "
            f"(max diff {max_diff}); you probably animate with iTime, or multiply "
            "iPhase by a non-integer"
        )
    elif audio_loop and inner_step > 0 and wrap_step > AUDIO_SEAM_FACTOR * inner_step + SEAM_SLACK:
        ok = False
        reason = (
            f"the picture jumps at the loop point: the largest step around the wrap is {wrap_step:.2f} vs "
            f"{inner_step:.2f} for the largest step in the rest of the loop; the audio does not wrap cleanly "
            "(cut the section with --audio-loop / audio_loop=true)"
        )
    elif not audio_loop and seam > SEAM_FACTOR * typical + SEAM_SLACK:
        ok = False
        reason = (
            f"motion jumps at the loop point: the step from the last frame to the first "
            f"is {seam:.2f} vs a typical {typical:.2f}; check for iTime or a non-integer "
            "multiple of iPhase"
        )
    elif typical < STATIC_STEP:
        reason = "static: nothing moves"
    return LoopReport(ok, max_diff, bad, seam, typical, reason)
