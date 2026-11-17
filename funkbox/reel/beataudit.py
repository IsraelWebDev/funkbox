"""Beat audit: how far each cut of a reel lands from the beat grid it was cut against.

The storyboard prints these distances. A render with ``audit`` saves them as ``<name>.beat_audit.json`` when the
reel snaps its cuts to beats, so "every cut within N ms of the beat" is a claim a reviewer can check. The offsets
are measured at the frame rate of the render, against the grid the reel was cut on: ``audio.bpm``, a score's tempo,
or the beats detected in the music file. A detected grid is funkbox's own estimate of where the beats are.
"""

from __future__ import annotations

import bisect
import json
from pathlib import Path

from .schema import Reel
from .timeline import ShotSpan, beat_period

MAX_OFFSET_MS = 30.0  # a cut further than this from its nearest beat fails the audit


def nearest_beats(spans: list[ShotSpan], beats: list[float], fps: float) -> list[tuple[ShotSpan, float, float]]:
    """(span, cut time, nearest beat) for every cut. A cut is where a span starts, after the first (so the end
    card's start is one). The cut time is the span's first frame at ``fps``; ``beats`` is sorted, two or more."""
    out = []
    for sp in spans[1:]:
        t = sp.start_frame / fps
        i = bisect.bisect_left(beats, t)
        out.append((sp, t, min(beats[max(0, i - 1) : i + 1], key=lambda b: abs(b - t))))
    return out


def grid_source(reel: Reel) -> str:
    """Where the beat grid comes from: "fixed" (``audio.bpm``), "score" (a ``*.music.json`` knows its tempo) or
    "detected" (found in the music file). The order is the one ``audio.beat_grid_for`` uses."""
    if reel.audio.bpm:
        return "fixed"
    if reel.audio.music and str(reel.audio.music).endswith(".music.json"):
        return "score"
    return "detected"


def beat_audit(
    reel: Reel, spans: list[ShotSpan], fps: float, beats: list[float], max_ms: float = MAX_OFFSET_MS
) -> dict:
    """The audit document (without the reel name): tempo, grid source, and for every cut its signed offset from the
    nearest beat in ms (positive = the cut is after the beat). ``ok`` is false when any cut is over ``max_ms``."""
    cuts = []
    for sp, t, b in nearest_beats(spans, beats, fps):
        off = round((t - b) * 1000.0, 1)
        cuts.append(
            {
                "cut": sp.id,
                "frame": sp.start_frame,
                "time_s": round(t, 4),
                "beat_s": round(b, 4),
                "offset_ms": off,
                "ok": abs(off) <= max_ms,
            }
        )
    over = [c["cut"] for c in cuts if not c["ok"]]
    return {
        "fps": fps,
        "bpm": round(60.0 / beat_period(beats), 2),
        "grid": grid_source(reel),
        "threshold_ms": max_ms,
        "frame_ms": round(1000.0 / fps, 1),  # a cut sits on a frame, so it can be up to half of this off a beat
        "ok": not over,
        "max_offset_ms": max((abs(c["offset_ms"]) for c in cuts), default=0.0),
        "over": over,
        "cuts": cuts,
    }


def write_beat_audit(reel: Reel, spans: list[ShotSpan], fps: float, name: str, out_dir: str | Path) -> dict | None:
    """Write ``<name>.beat_audit.json`` and return ``{"path", "ok"}``. None, and no file, when the reel does not
    snap its cuts to beats or has no beat grid: there is no beat claim to check then. A beat audit left by an
    earlier render of the same name is removed in that case, so a stale file cannot back a claim."""
    from . import audio

    path = Path(out_dir) / f"{name}.beat_audit.json"
    beats = audio.beat_grid_for(reel) if reel.audio.snap_cuts_to_beats else None
    if not beats or len(beats) < 2:
        path.unlink(missing_ok=True)
        return None
    doc = {"reel": name, **beat_audit(reel, spans, fps, beats)}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "ok": doc["ok"]}


def report_line(out) -> str | None:
    """The one line a render reports about its beat audit (``out`` is a RenderOutput); None when none was written."""
    ok = getattr(out, "beat_ok", None)
    if ok is None:
        return None
    if ok:
        return f"beat audit: OK, every cut is within {MAX_OFFSET_MS:.0f} ms of a beat"
    return f"beat audit: a cut is more than {MAX_OFFSET_MS:.0f} ms from a beat (see beat_audit.json)"
