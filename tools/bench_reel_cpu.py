"""Reel CPU benchmark (E4): CPU seconds per second of output for the demo reel, CPU only.

Spec: docs/specs/reel-cpu-bench.md. Table: docs/benchmarks/reel-cpu.md. Output: CSV on stdout.

    python tools/bench_reel_cpu.py                          # 540x960 and 720x1280, depth on and off
    python tools/bench_reel_cpu.py --sizes 540x960 --depth off --repeat 2

The depth model and the voices are read from FUNK_MODELS (or the default models folder). Nothing is downloaded.
Imports of funkbox happen inside main(), after the arguments are checked.
"""

from __future__ import annotations

import argparse
import csv
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEMO_REEL = ROOT / "examples" / "reel_demo" / "demo.reel.json"
SIZES = ("540x960", "720x1280")
COLUMNS = (
    "size",
    "depth",
    "output_s",
    "cpu_python_s",
    "cpu_children_s",
    "cpu_total_s",
    "cpu_per_output_s",
    "wall_s",
    "wall_per_output_s",
    "status",
    "note",
)
NUMERIC = ("output_s", "cpu_python_s", "cpu_children_s", "cpu_total_s", "cpu_per_output_s", "wall_s", "wall_per_output_s")
TIMER_LINE = "timer: cpu = time.process_time() (this process, all threads) + child processes (ffmpeg); wall = time.perf_counter()"

# time.process_time() does not see ffmpeg (clip decode, audio mix, H.264 encode), so child CPU is added separately.
# Windows: subprocess.Popen is wrapped in this process only, each child's handle is kept, and GetProcessTimes is read
# when the render ends. Elsewhere: the change in getrusage(RUSAGE_CHILDREN).
_WIN_HANDLES: list[int] = []
_WIN_STATE = {"open_failed": 0}
_K32 = None
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _win_kernel32():
    global _K32
    if _K32 is None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        filetime_p = ctypes.POINTER(wintypes.FILETIME)
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE, filetime_p, filetime_p, filetime_p, filetime_p]
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        _K32 = k32
    return _K32


def install_child_tracking() -> None:
    """Windows only: keep a handle to every child process started from now on."""
    if os.name != "nt":
        return
    k32 = _win_kernel32()

    class _TrackedPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid)
            if handle:
                _WIN_HANDLES.append(handle)
            else:
                _WIN_STATE["open_failed"] += 1

    subprocess.Popen = _TrackedPopen


def _ticks(ft) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _win_child_seconds() -> float:
    """CPU seconds (kernel + user) of every tracked child; each handle is closed after it is read."""
    from ctypes import byref, wintypes

    k32 = _win_kernel32()
    total = 0.0
    while _WIN_HANDLES:
        handle = _WIN_HANDLES.pop()
        times = [wintypes.FILETIME() for _ in range(4)]  # creation, exit, kernel, user
        if k32.GetProcessTimes(handle, *(byref(t) for t in times)):
            total += (_ticks(times[2]) + _ticks(times[3])) / 1e7
        k32.CloseHandle(handle)
    return total


def _rusage_children() -> float:
    import resource

    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


def child_cpu_meter():
    """Start counting child-process CPU. Returns a function that stops the count and gives seconds."""
    if os.name == "nt":
        _WIN_STATE["open_failed"] = 0
        _win_child_seconds()  # forget children that finished before this window
        return _win_child_seconds
    before = _rusage_children()
    return lambda: _rusage_children() - before


def clear_depth_cache() -> None:
    """Empty the depth cache so the ONNX inference is inside the timed render."""
    from funkbox.config import funk_out

    for f in (funk_out() / "cache" / "depth").glob("*.npy"):
        f.unlink()


def parse_sizes(text: str) -> list[str]:
    sizes = [s.strip() for s in text.split(",") if s.strip()]
    if not sizes:
        raise ValueError("no size given")
    for s in sizes:
        if s not in SIZES:
            raise ValueError(f"unknown size {s!r}; this benchmark runs {' and '.join(SIZES)} only")
    return sizes


def result_row(size, depth, output_s, cpu_python_s, cpu_children_s, wall_s, status="ok", note="") -> dict:
    cpu_total = cpu_python_s + cpu_children_s
    return {
        "size": size,
        "depth": depth,
        "output_s": output_s,
        "cpu_python_s": cpu_python_s,
        "cpu_children_s": cpu_children_s,
        "cpu_total_s": cpu_total,
        "cpu_per_output_s": cpu_total / output_s if output_s else None,
        "wall_s": wall_s,
        "wall_per_output_s": wall_s / output_s if output_s else None,
        "status": status,
        "note": note,
    }


def blank_row(size, depth, status, note) -> dict:
    """A row with no numbers: a case that did not run, or failed."""
    return {"size": size, "depth": depth, **{k: None for k in NUMERIC}, "status": status, "note": note}


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return v


def write_csv(rows: list[dict], header_lines: list[str], stream) -> None:
    for line in header_lines:
        stream.write(f"# {line}\n")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in COLUMNS])


def measure(plan, size: str, depth: str, out_dir: Path, repeat: int) -> dict:
    """Time the full reel render at one size and depth setting, ``repeat`` times; the row reports medians."""
    from funkbox.reel.render import render_reel

    w, h = (int(x) for x in size.split("x"))
    runs: list[dict] = []
    notes: list[str] = []
    for _ in range(repeat):
        if depth == "on":
            clear_depth_cache()
        plan.reel.settings.width, plan.reel.settings.height = w, h  # in memory only; the reel file is not written
        stop_children = child_cpu_meter()
        cpu0, wall0 = time.process_time(), time.perf_counter()
        try:
            out = render_reel(
                None,
                "bench",
                plan=plan,
                draft=False,
                depth_mode="model" if depth == "on" else "off",
                out_dir=out_dir,
                with_audio=True,
            )
        except Exception as exc:  # keep the other rows; this one is marked as an error
            stop_children()
            return blank_row(size, depth, "error", f"{type(exc).__name__}: {str(exc)[:200]}".replace("\n", " "))
        wall = time.perf_counter() - wall0
        cpu_python = time.process_time() - cpu0
        cpu_children = stop_children()
        if _WIN_STATE["open_failed"]:
            note = f"child CPU not read for {_WIN_STATE['open_failed']} process(es)"
            if note not in notes:
                notes.append(note)
        runs.append({"output_s": out.duration, "cpu_python_s": cpu_python, "cpu_children_s": cpu_children, "wall_s": wall})

    med = {k: statistics.median(r[k] for r in runs) for k in ("output_s", "cpu_python_s", "cpu_children_s", "wall_s")}
    parts = []
    if len(runs) > 1:
        cpu_totals = ", ".join(f"{r['cpu_python_s'] + r['cpu_children_s']:.1f}" for r in runs)
        parts.append(f"median of {len(runs)} runs, each field separately; cpu totals {cpu_totals} s")
    parts.extend(notes)
    return result_row(size, depth, med["output_s"], med["cpu_python_s"], med["cpu_children_s"], med["wall_s"], "ok", "; ".join(parts))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CPU seconds per second of output for the demo reel (CSV on stdout).")
    ap.add_argument("--sizes", default=",".join(SIZES), help="comma-separated; allowed: " + ", ".join(SIZES))
    ap.add_argument("--depth", choices=("both", "on", "off"), default="both")
    ap.add_argument("--repeat", type=int, default=1, help="timed renders per row; the row reports medians")
    ap.add_argument("--out", type=Path, default=None, help="where the MP4s go (default FUNK_OUT/bench-reel-cpu)")
    args = ap.parse_args(argv)
    try:
        sizes = parse_sizes(args.sizes)
    except ValueError as exc:
        ap.error(str(exc))
    if args.repeat < 1:
        ap.error("--repeat must be 1 or more")
    depths = ["on", "off"] if args.depth == "both" else [args.depth]

    install_child_tracking()

    from funkbox.config import funk_out
    from funkbox.gl import context as glctx
    from funkbox.reel import depth as depthmod
    from funkbox.reel.render import prepare, render_reel

    try:
        renderer = glctx.create_context().info.get("GL_RENDERER", "unknown")
    except glctx.GLUnavailable as exc:
        renderer = f"unavailable ({exc})"
    out_root = args.out or funk_out() / "bench-reel-cpu"
    plan = prepare(DEMO_REEL)
    depth_ok = depthmod.available()

    # Untimed warm-up: imports, fonts, GL, depth session and voice-line cache. A draft render is enough for that.
    render_reel(None, "warmup", plan=plan, draft=True, depth_mode="model" if depth_ok else "off", out_dir=out_root / "warmup")

    rows: list[dict] = []
    for size in sizes:
        for depth in depths:
            if depth == "on" and not depth_ok:
                rows.append(blank_row(size, depth, "not run", depthmod.unavailable_reason()))
                continue
            rows.append(measure(plan, size, depth, out_root / f"{size}-depth-{depth}", args.repeat))

    if os.name == "nt":
        child_line = "child cpu: GetProcessTimes on each child process handle (Windows)"
    else:
        child_line = "child cpu: getrusage(RUSAGE_CHILDREN) delta (POSIX)"
    header = [
        TIMER_LINE,
        child_line,
        f"host: {platform.node()} {platform.platform()}; python {sys.version.split()[0]}",
        f"gl: {renderer} (software={glctx.SOFTWARE})",
        (
            f"depth model: available at {depthmod.model_path()}"
            if depth_ok
            else f"depth model: unavailable ({depthmod.unavailable_reason()})"
        ),
        f"reel: examples/reel_demo/demo.reel.json at {plan.reel.settings.fps} fps, voice-over and music on",
        f"mp4s: {out_root}",
    ]
    write_csv(rows, header, sys.stdout)
    return 1 if any(r["status"] == "error" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())