"""Loudness of rendered variants (MV-3): measured on each finished file, against the reel's target.

A variant that misses the target by more than ``TOLERANCE_LU`` is reported in the manifest and the CLI. It is not an
error: the reel's own loudness normalisation aims at the target, and a miss means the file itself says otherwise.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

TOLERANCE_LU = 1.0
DEFAULT_TARGET = -14.0
_INTEGRATED = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s+LUFS")


def measure_lufs(path: str | Path) -> float | None:
    """Integrated loudness (LUFS, EBU R128) of the file's first audio stream, or None if there is none."""
    from ..media import ffmpeg_exe

    exe = ffmpeg_exe()
    if not exe:
        return None
    proc = subprocess.run(
        [
            exe, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
            "-af", "ebur128=framelog=quiet", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    found = _INTEGRATED.findall(proc.stderr)
    return float(found[-1]) if found else None


def check(lufs: float | None, target: float) -> dict:
    """The report for one file: the measured value, the target, the offset, and whether it is within tolerance."""
    if lufs is None:
        return {"lufs": None, "target": target, "off_by_lu": None, "within_tolerance": None}
    off = round(lufs - target, 2)
    return {"lufs": round(lufs, 2), "target": target, "off_by_lu": off, "within_tolerance": abs(off) <= TOLERANCE_LU}


def measure_outputs(vdir: Path, stem: str, target: float) -> dict:
    """Measure each MP4 a variant rendered (``stem*.mp4``). Keys are file names."""
    return {p.name: check(measure_lufs(p), target) for p in sorted(Path(vdir).glob(f"{stem}*.mp4"))}


def all_ok(report: dict) -> bool | None:
    """True if every measured file is within tolerance, False if one misses, None if nothing was measured."""
    measured = [r["within_tolerance"] for r in report.values() if r["within_tolerance"] is not None]
    return all(measured) if measured else None