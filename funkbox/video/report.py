"""T-6: an acceptance report next to a rendered video: how long it is, how loud, whether it clips, what is left of the
pauses, and whether it is what the cut plan promised.

    acceptance("talk-final.mp4", cutplan="talk.cutplan.json") -> Report(path=talk-final.report.json, ...)

Loudness is measured with the reel's own EBU R128 measurement, against -14 LUFS (the reel's target) within 1 LU. Peak is
the highest sample, from volumedetect. Pauses left are the quiet stretches of at least 0.6 s, the same threshold the cut
finder uses, so the number is what cutplan would still propose. Nothing is changed: the report only reads the file.

A check that cannot be made is reported as ``None`` with the reason kept, never as a pass. The verdict is ``pass`` only
when every hard check passed; the pause count is information, not a pass or fail.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from funkbox.video.clip import VideoError, _run, _src, info
from funkbox.video.cutfind import MIN_PAUSE_S, silences

PEAK_LIMIT_DB = -1.0  # a sample above this is at (or past) the edge of the converter: clipping risk
PLAN_TOLERANCE_S = 0.3  # the render's length may differ from the plan's kept time by this much (frame rounding)
_MAX_VOLUME = re.compile(r"max_volume: (-?[\d.]+|-inf) dB")


@dataclass
class Report:
    path: Path  # <name>.report.json, next to the video
    source: str
    duration: float
    has_audio: bool
    loudness_lufs: float | None
    loudness_reason: str | None
    peak_db: float | None
    pauses_left: int
    pause_s_left: float
    removed: dict | None  # from the cut plan: {"count", "seconds", "by_reason"}; None without a plan
    checks: dict = field(default_factory=dict)  # name -> True / False / None (not checked, reason in the report)
    verdict: str = "pass"  # "pass", "fail" or "unchecked"

    def text(self) -> str:
        lines = [f"{self.verdict.upper()}: {self.source}, {self.duration:.2f}s"]
        lines.append(
            "loudness "
            + (
                f"{self.loudness_lufs:.1f} LUFS"
                if self.loudness_lufs is not None
                else f"not measured ({self.loudness_reason})"
            )
        )
        lines.append(f"peak {self.peak_db:.1f} dBFS" if self.peak_db is not None else "peak not measured")
        lines.append(f"pauses left: {self.pauses_left} ({self.pause_s_left:.1f}s)")
        if self.removed is not None:
            r = self.removed
            why = ", ".join(f"{v} {k}" for k, v in sorted(r["by_reason"].items())) or "none"
            lines.append(f"cut: {r['count']} span(s), {r['seconds']:.1f}s removed ({why})")
        lines.append(f"report: {self.path}")
        return "\n".join(lines)


def _peak_db(p: Path) -> float | None:
    r = _run(["-i", str(p), "-vn", "-af", "volumedetect", "-f", "null", "-"])
    m = _MAX_VOLUME.search(r.stderr.decode("utf-8", "replace"))
    if not m or m.group(1) == "-inf":
        return None
    return float(m.group(1))


def _plan(path) -> tuple[dict, float]:
    """(removed summary, kept seconds) from a <name>.cutplan.json."""
    p = Path(path)
    if not p.exists():
        raise VideoError(f"cut plan not found: {p.name}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        keep = [(float(a), float(b)) for a, b in data["keep"]]
        removed = data["removed"]
    except (ValueError, KeyError, TypeError):
        raise VideoError(f"{p.name} is not a cut plan written by cutplan") from None
    by_reason: dict[str, int] = {}
    for span in removed:
        for reason in span.get("reasons", []):
            kind = reason.split(":", 1)[0]
            by_reason[kind] = by_reason.get(kind, 0) + 1
    seconds = sum(float(s["end"]) - float(s["start"]) for s in removed)
    return {"count": len(removed), "seconds": round(seconds, 3), "by_reason": by_reason}, sum(b - a for a, b in keep)


def acceptance(path, cutplan=None, name: str = "") -> Report:
    """Measure a rendered video and write ``<name>.report.json`` next to it. Never changes the video."""
    from funkbox.reel.loudness import DEFAULT_TARGET, TOLERANCE_LU, measure_lufs

    p = _src(path)
    inf = info(p)
    stem = name or p.stem
    out = p.with_name(f"{stem}.report.json")
    n = 2
    while out.exists():  # never overwrite an earlier report
        out = p.with_name(f"{stem}-{n}.report.json")
        n += 1

    lufs, reason = (None, "no audio") if not inf.audio else measure_lufs(p)
    peak = _peak_db(p) if inf.audio else None
    quiet = silences(p, min_s=MIN_PAUSE_S)
    pause_s = sum(b - a for a, b in quiet)

    removed, expected = (None, None)
    if cutplan is not None:
        removed, expected = _plan(cutplan)

    checks: dict[str, bool | None] = {}
    checks["has_audio"] = inf.audio
    if reason == "silent":
        checks["loudness"] = False  # a silent render is a miss, not an unmeasured one
    else:
        checks["loudness"] = None if lufs is None else abs(lufs - DEFAULT_TARGET) <= TOLERANCE_LU
    checks["peak"] = None if peak is None else peak <= PEAK_LIMIT_DB
    checks["plan"] = None if expected is None else abs(inf.duration - expected) <= PLAN_TOLERANCE_S
    if any(v is False for v in checks.values()):
        verdict = "fail"
    elif checks["loudness"] is None or checks["peak"] is None:
        verdict = "unchecked"  # a measurement could not be made: say so, do not call it a pass
    else:
        verdict = "pass"

    rep = Report(
        path=out,
        source=p.name,
        duration=round(inf.duration, 3),
        has_audio=inf.audio,
        loudness_lufs=None if lufs is None else round(lufs, 2),
        loudness_reason=reason,
        peak_db=None if peak is None else round(peak, 2),
        pauses_left=len(quiet),
        pause_s_left=round(pause_s, 3),
        removed=removed,
        checks=checks,
        verdict=verdict,
    )
    data = {
        "source": p.name,
        "duration": rep.duration,
        "has_audio": rep.has_audio,
        "loudness": {
            "lufs": rep.loudness_lufs,
            "target": DEFAULT_TARGET,
            "tolerance_lu": TOLERANCE_LU,
            "reason": rep.loudness_reason,
        },
        "peak": {"dbfs": rep.peak_db, "limit_dbfs": PEAK_LIMIT_DB},
        "pauses_left": {"count": rep.pauses_left, "seconds": rep.pause_s_left, "min_s": MIN_PAUSE_S},
        "cut_plan": {"removed": removed, "expected_duration": None if expected is None else round(expected, 3)},
        "checks": checks,
        "verdict": verdict,
    }
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return rep
