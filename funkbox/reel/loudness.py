"""Loudness of rendered variants (MV-3): measured on each finished file, against the reel's target.

A file more than ``TOLERANCE_LU`` from the target is a miss, and so is a silent file. A miss is reported in the
manifest and the CLI; it does not fail the run, because the reel's own loudness normalisation aims at the target and
a miss means the file says otherwise. A file that cannot be measured is reported with the reason, never raised.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

TOLERANCE_LU = 1.0
DEFAULT_TARGET = -14.0
TIMEOUT_S = 600
ABSOLUTE_GATE_LUFS = -70.0  # EBU R128 absolute gate
_INTEGRATED = re.compile(r"I:\s*([+-]?(?:inf|\d+(?:\.\d+)?))\s+LUFS")


def target_for(variant: dict) -> float:
    """The reel's loudness target (``audio.loudness_lufs``), or the default if it is missing or not a number."""
    audio = variant.get("audio") if isinstance(variant, dict) else None
    value = audio.get("loudness_lufs") if isinstance(audio, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_TARGET
    return float(value)


def measure_lufs(path: str | Path) -> tuple[float | None, str | None]:
    """``(LUFS, None)`` when measured; ``(None, "silent")`` when the audio is silent; ``(None, reason)`` otherwise."""
    from ..media import ffmpeg_exe

    exe = ffmpeg_exe()
    if not exe:
        return None, "ffmpeg not found"
    try:
        proc = subprocess.run(
            [
                exe,
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-af",
                "ebur128=framelog=quiet",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"ffmpeg could not run: {e}"
    if proc.returncode != 0:
        lines = [ln.strip() for ln in proc.stderr.splitlines() if ln.strip()]
        return None, lines[-1] if lines else f"ffmpeg exited {proc.returncode}"
    found = _INTEGRATED.findall(proc.stderr)
    if not found:
        return None, "no loudness summary in the ffmpeg output"
    value = found[-1]
    if value.endswith("inf"):
        return None, "silent"
    lufs = float(value)
    if (
        lufs <= ABSOLUTE_GATE_LUFS
    ):  # no block passed the absolute gate: nothing audible (some builds print -70, not -inf)
        return None, "silent"
    return lufs, None


def check(lufs: float | None, target: float, reason: str | None = None) -> dict:
    """The report for one file: the measured value, the target, the offset, and whether it is within tolerance."""
    if lufs is None:
        # Silent is a miss; any other failure is unmeasured (None), with its reason kept.
        return {
            "lufs": None,
            "target": target,
            "off_by_lu": None,
            "within_tolerance": False if reason == "silent" else None,
            "reason": reason,
        }
    off = round(lufs - target, 2)
    return {
        "lufs": round(lufs, 2),
        "target": target,
        "off_by_lu": off,
        "within_tolerance": abs(off) <= TOLERANCE_LU,
        "reason": None,
    }


def measure_outputs(paths, target: float) -> dict:
    """Measure exactly these files (the variant's own renders). Keys are file names."""
    report = {}
    for p in paths:
        lufs, reason = measure_lufs(p)
        report[Path(p).name] = check(lufs, target, reason)
    return report


def all_ok(report: dict) -> bool | None:
    """True only if every file is measured and within tolerance. False if any file misses. None if any is unmeasured."""
    if not report:
        return None
    verdicts = [r["within_tolerance"] for r in report.values()]
    if any(v is False for v in verdicts):
        return False
    if any(v is None for v in verdicts):
        return None
    return True
