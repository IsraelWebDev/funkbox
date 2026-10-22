"""Shot timing: explicit seconds, VO-driven ``auto``, beat-grid ``beats:N`` and beat snapping."""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass

from .schema import BEATS_RE, Reel

VO_LEAD = 0.1          # VO clip starts this long after its shot starts
VO_TAIL = 0.35         # a shot is at least VO length + this long
AUTO_MIN = 1.0
AUTO_NO_VO = 1.5       # `auto` with no voiceover line
SNAP_WINDOW = 0.25
SNAP_MIN_SHOT = 0.6
END_MIN = 0.5
LONG_REEL_WARN = 60.0


class TimelineError(ValueError):
    pass


@dataclass(frozen=True)
class ShotSpan:
    id: str
    kind: str                 # "shot" | "end"
    index: int
    start: float              # seconds, a whole number of frames
    end: float
    start_frame: int
    end_frame: int            # exclusive
    vo_id: str | None = None
    vo_start: float | None = None
    vo_dur: float | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


def beat_period(beats: list[float]) -> float:
    d = [b - a for a, b in zip(beats, beats[1:]) if b > a]
    if not d:
        raise TimelineError("beat grid needs at least two beats")
    return float(statistics.median(d))


def _vo_len(reel: Reel, shot, vo_durations: dict[str, float]) -> float | None:
    if not (reel.audio.voiceover and shot.vo):
        return None
    if shot.vo not in vo_durations:
        raise TimelineError(f"shot {shot.id}: no voiceover duration for copy id {shot.vo!r}")
    return float(vo_durations[shot.vo])


def build_timeline(reel: Reel, vo_durations: dict[str, float] | None = None,
                   beats: list[float] | None = None) -> list[ShotSpan]:
    """Absolute [start, end) for every shot plus the end card, rounded to whole frames.

    ``vo_durations`` maps copy id -> seconds of speech. ``beats`` is the music beat grid (seconds).
    """
    vo_durations = vo_durations or {}
    fps = reel.settings.fps
    n = len(reel.shots)
    if n == 0:
        raise TimelineError("reel has no shots")
    period = beat_period(beats) if beats and len(beats) >= 2 else None

    nominal: list[float] = []
    minimum: list[float] = []
    vo: list[float | None] = []
    for sh in reel.shots:
        v = _vo_len(reel, sh, vo_durations)
        vo.append(v)
        need = (v + VO_TAIL) if v is not None else 0.0
        d = sh.duration
        if d == "auto":
            dur = max(AUTO_MIN, need) if v is not None else AUTO_NO_VO
        elif isinstance(d, str):
            m = BEATS_RE.match(d)
            assert m
            if period is None:
                raise TimelineError(f"shot {sh.id}: duration {d!r} needs a beat grid "
                                    f"(set audio.bpm, or audio.music to detect one, or use seconds)")
            dur = float(m.group(1)) * period
        else:
            dur = float(d)
        if v is not None and dur < need - 1e-6:
            raise TimelineError(f"shot {sh.id}: duration {dur:.2f}s is shorter than its voiceover line "
                                f"{sh.vo!r} ({v:.2f}s) + {VO_TAIL}s padding = {need:.2f}s")
        nominal.append(dur)
        minimum.append(max(need, 0.0))

    # cut times; cut k (1-based) ends shot k-1; cut n is the shot -> end card boundary
    cuts = [0.0]
    for d in nominal:
        cuts.append(cuts[-1] + d)
    if reel.audio.snap_cuts_to_beats and beats and len(beats) >= 2:
        floor = [max(SNAP_MIN_SHOT, m) for m in minimum]
        for k in range(1, n + 1):
            nxt_end = cuts[k + 1] if k < n else cuts[k] + (reel.end_card.duration if reel.end_card else END_MIN)
            nxt_min = floor[k] if k < n else END_MIN
            i = bisect.bisect_left(beats, cuts[k])
            near = sorted(beats[max(0, i - 2): i + 2], key=lambda b: abs(b - cuts[k]))
            for cand in near:
                if abs(cand - cuts[k]) > SNAP_WINDOW:
                    break
                if cand - cuts[k - 1] >= floor[k - 1] - 1e-9 and nxt_end - cand >= nxt_min - 1e-9:
                    cuts[k] = cand
                    break
    # whole frames; never let rounding squeeze a shot under its minimum
    fr = [0]
    for k in range(1, n + 1):
        f = round(cuts[k] * fps)
        need_f = max(1, math.ceil(minimum[k - 1] * fps - 1e-9))
        fr.append(max(f, fr[-1] + need_f))
    spans: list[ShotSpan] = []
    for i, sh in enumerate(reel.shots):
        s_f, e_f = fr[i], fr[i + 1]
        start = s_f / fps
        v = vo[i]
        spans.append(ShotSpan(sh.id, "shot", i, start, e_f / fps, s_f, e_f,
                              sh.vo if v is not None else None,
                              start + VO_LEAD if v is not None else None, v))
    if reel.end_card:
        e_frames = max(1, round(reel.end_card.duration * fps))
        spans.append(ShotSpan("end_card", "end", n, fr[n] / fps, (fr[n] + e_frames) / fps,
                              fr[n], fr[n] + e_frames))
    return spans


def total_seconds(spans: list[ShotSpan]) -> float:
    return spans[-1].end if spans else 0.0


def warnings(spans: list[ShotSpan]) -> list[str]:
    t = total_seconds(spans)
    return [f"total length {t:.1f}s is over {LONG_REEL_WARN:.0f}s"] if t > LONG_REEL_WARN else []
