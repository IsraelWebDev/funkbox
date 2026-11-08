"""Reel CPU benchmark (E4): CPU seconds per second of output for the demo reel, CPU only.

Spec: docs/specs/reel-cpu-bench.md. Table: docs/benchmarks/reel-cpu.md. Output: CSV on stdout.

    python tools/bench_reel_cpu.py                          # 540x960 and 720x1280, depth on and off
    python tools/bench_reel_cpu.py --sizes 540x960 --depth off --repeat 2

Models come from FUNK_MODELS (or the default models folder). Nothing is downloaded. The script sets the depth mode
itself, so it refuses to start when REEL_DEPTH is set or reel_depth is set in funkbox.toml.
funkbox is imported only inside the functions that need it, after the arguments are checked.
"""

from __future__ import annotations

import argparse
import csv
import os
import platform
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
RUN_FIELDS = ("output_s", "cpu_python_s", "cpu_children_s", "wall_s")
PARTIAL_NOTE = "some runs had a child handle that could not be opened; cpu_children_s and cpu_total_s are low"
TIMER_LINE = "timer: cpu = time.process_time() (this process, all threads) + child processes (ffmpeg); wall = time.perf_counter()"

# time.process_time() does not see ffmpeg (clip decode, audio mix, H.264 encode), so child CPU is added separately.
# Windows: subprocess.Popen is wrapped in this process only, each child's handle is kept, and GetProcessTimes is read
# when the render ends. A child whose handle cannot be opened is counted as missing: the row becomes "partial".
# Elsewhere: the change in getrusage(RUSAGE_CHILDREN).
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


def _on_child_started(handle) -> None:
    """Called with the handle OpenProcess returned for a new child (0 or None when it failed)."""
    if handle:
        _WIN_HANDLES.append(handle)
    else:
        _WIN_STATE["open_failed"] += 1


def install_child_tracking() -> None:
    """Windows only: keep a handle to every child process started from now on."""
    if os.name != "nt":
        return
    k32 = _win_kernel32()

    class _TrackedPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            _on_child_started(k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid))

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
    _WIN_STATE["open_failed"] = 0  # per window, on every platform
    if os.name == "nt":
        _win_child_seconds()  # forget children that finished before this window
        return _win_child_seconds
    before = _rusage_children()
    return lambda: _rusage_children() - before


class Setup:
    """Everything a run needs. Tests replace the render with a stub; the real one is _real_render."""

    def __init__(self, *, plan, render, clear_depth_cache, depth_ok, depth_reason, out_root, info):
        self.plan = plan
        self.render = render  # (plan, out_dir, depth_mode) -> object with .duration (seconds)
        self.clear_depth_cache = clear_depth_cache
        self.depth_ok = depth_ok
        self.depth_reason = depth_reason
        self.out_root = out_root
        self.info = info  # facts for the header comments


def size_pair(size: str) -> tuple[int, int]:
    w, h = size.split("x")
    return int(w), int(h)


def parse_sizes(text: str) -> list[str]:
    sizes = [s.strip() for s in text.split(",") if s.strip()]
    if not sizes:
        raise ValueError("no size given")
    for s in sizes:
        if s not in SIZES:
            raise ValueError(f"unknown size {s!r}; this benchmark runs {' and '.join(SIZES)} only")
    return sizes


def depth_override(root: Path | None = None) -> str:
    """The REEL_DEPTH value (or reel_depth from funkbox.toml under ``root``) if one is set, else ''."""
    from funkbox.config import get

    return get(root).reel_depth or ""


def onnxruntime_version() -> str:
    try:
        import onnxruntime
    except Exception:  # noqa: BLE001 - any failure to import means the version is unknown
        return "unknown"
    return str(getattr(onnxruntime, "__version__", "unknown"))


def header_lines(info: dict) -> list[str]:
    if info["nt"]:
        child = "GetProcessTimes on each child process handle (Windows)"
    else:
        child = "getrusage(RUSAGE_CHILDREN) delta (POSIX)"
    return [
        TIMER_LINE,
        f"child cpu: {child}",
        f"host: {info['host']}; python {info['python']}",
        f"onnxruntime: {info['onnxruntime']}",
        f"gl: {info['gl']} (software={info['software']})",
        f"models folder: {info['models']}",
        f"depth model: {info['depth']}",
        f"depth cache: cleared before each depth-on render (only *.npy files in this folder): {info['depth_cache']}",
        f"reel: examples/reel_demo/demo.reel.json at {info['fps']} fps, voice-over and music on",
        f"warm-up: {info['warm']}",
        f"mp4s: {info['out']}",
    ]


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


def aggregate_runs(size: str, depth: str, runs: list[dict], notes: list[str] | tuple = ()) -> dict:
    """One row from several runs. The row is the run with the median CPU total; with an even count it is the mean of
    the two middle runs. Every column comes from that row, so the parts add up to the total. Status is partial if any
    run is partial."""
    ordered = sorted(runs, key=lambda r: r["cpu_total_s"])
    n = len(ordered)
    if n % 2:
        mid = ordered[n // 2]
        how = "the middle run"
    else:
        pair = ordered[n // 2 - 1 : n // 2 + 1]
        mid = {k: sum(r[k] for r in pair) / 2 for k in RUN_FIELDS}
        how = "the mean of the two middle runs"
    parts = []
    if n > 1:
        totals = ", ".join(f"{r['cpu_total_s']:.1f}" for r in runs)
        parts.append(f"median of {n} runs by CPU total ({how}); run totals {totals} s")
    parts.extend(notes)
    status = "partial" if any(r["partial"] for r in runs) else "ok"
    return result_row(
        size, depth, mid["output_s"], mid["cpu_python_s"], mid["cpu_children_s"], mid["wall_s"], status, "; ".join(parts)
    )


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return v


def write_csv(rows: list[dict], header: list[str], stream) -> None:
    for line in header:
        stream.write(f"# {line}\n")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in COLUMNS])


def measure(setup: Setup, size: str, depth: str, out_dir: Path, repeat: int) -> dict:
    """Time the full reel render at one size and depth setting, ``repeat`` times."""
    w, h = size_pair(size)
    depth_mode = "model" if depth == "on" else "off"
    runs: list[dict] = []
    notes: list[str] = []
    for _ in range(repeat):
        if depth == "on":
            setup.clear_depth_cache()
        setup.plan.reel.settings.width, setup.plan.reel.settings.height = w, h  # in memory only; the reel file is not written
        stop_children = child_cpu_meter()
        cpu0, wall0 = time.process_time(), time.perf_counter()
        try:
            out = setup.render(setup.plan, out_dir, depth_mode)
        except Exception as exc:  # keep the other rows; this one is marked as an error
            stop_children()
            return blank_row(size, depth, "error", f"{type(exc).__name__}: {str(exc)[:200]}".replace("\n", " "))
        wall = time.perf_counter() - wall0
        cpu_python = time.process_time() - cpu0
        cpu_children = stop_children()
        partial = _WIN_STATE["open_failed"] > 0
        if partial and PARTIAL_NOTE not in notes:
            notes.append(PARTIAL_NOTE)
        runs.append(
            {
                "output_s": out.duration,
                "cpu_python_s": cpu_python,
                "cpu_children_s": cpu_children,
                "wall_s": wall,
                "cpu_total_s": cpu_python + cpu_children,
                "partial": partial,
            }
        )
    return aggregate_runs(size, depth, runs, notes)


def run_rows(setup: Setup, sizes: list[str], depths: list[str], repeat: int) -> list[dict]:
    rows = []
    for size in sizes:
        for depth in depths:
            if depth == "on" and not setup.depth_ok:
                rows.append(blank_row(size, depth, "not run", setup.depth_reason))
                continue
            rows.append(measure(setup, size, depth, setup.out_root / f"{size}-depth-{depth}", repeat))
    return rows


def exit_code(rows: list[dict]) -> int:
    return 1 if any(r["status"] == "error" for r in rows) else 0


def _real_render(plan, out_dir: Path, depth_mode: str):
    from funkbox.reel.render import render_reel

    return render_reel(None, "bench", plan=plan, draft=False, depth_mode=depth_mode, out_dir=out_dir, with_audio=True)


def setup_environment(out_root: Path | None) -> Setup:
    from funkbox.config import funk_out, models_dir
    from funkbox.gl import context as glctx
    from funkbox.reel import depth as depthmod
    from funkbox.reel.render import prepare

    try:
        renderer = glctx.create_context().info.get("GL_RENDERER", "unknown")
    except glctx.GLUnavailable as exc:
        renderer = f"unavailable ({exc})"
    plan = prepare(DEMO_REEL)
    depth_ok = depthmod.available()
    depth_reason = "" if depth_ok else depthmod.unavailable_reason()
    depth_dir = funk_out() / "cache" / "depth"

    def clear_depth_cache() -> None:
        for f in depth_dir.glob("*.npy"):
            f.unlink()

    root = out_root or funk_out() / "bench-reel-cpu"
    info = {
        "nt": os.name == "nt",
        "host": f"{platform.node()} {platform.platform()}",
        "python": sys.version.split()[0],
        "onnxruntime": onnxruntime_version(),
        "gl": renderer,
        "software": glctx.SOFTWARE,
        "models": str(models_dir()),
        "depth": f"available at {depthmod.model_path()}" if depth_ok else f"unavailable ({depth_reason})",
        "depth_cache": str(depth_dir),
        "fps": plan.reel.settings.fps,
        "warm": "",
        "out": str(root),
    }
    return Setup(
        plan=plan,
        render=_real_render,
        clear_depth_cache=clear_depth_cache,
        depth_ok=depth_ok,
        depth_reason=depth_reason,
        out_root=root,
        info=info,
    )


def warm_up(setup: Setup, size: str, depth_mode: str) -> None:
    """One untimed render at the first timed size, full settings, so the one-time costs are paid before timing."""
    w, h = size_pair(size)
    setup.plan.reel.settings.width, setup.plan.reel.settings.height = w, h
    setup.render(setup.plan, setup.out_root / "warmup", depth_mode)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CPU seconds per second of output for the demo reel (CSV on stdout).")
    ap.add_argument("--sizes", default=",".join(SIZES), help="comma-separated; allowed: " + ", ".join(SIZES))
    ap.add_argument("--depth", choices=("both", "on", "off"), default="both")
    ap.add_argument("--repeat", type=int, default=1, help="timed renders per row; the row is the median run by CPU total")
    ap.add_argument("--out", type=Path, default=None, help="where the MP4s go (default FUNK_OUT/bench-reel-cpu)")
    args = ap.parse_args(argv)
    try:
        sizes = parse_sizes(args.sizes)
    except ValueError as exc:
        ap.error(str(exc))
    if args.repeat < 1:
        ap.error("--repeat must be 1 or more")
    depths = ["on", "off"] if args.depth == "both" else [args.depth]
    override = depth_override()
    if override:
        ap.error(
            f"REEL_DEPTH is set to {override!r} (or reel_depth is set in funkbox.toml). The benchmark sets the depth "
            "mode itself, so unset REEL_DEPTH and remove reel_depth from funkbox.toml, then run it again."
        )

    install_child_tracking()
    setup = setup_environment(args.out)
    # The warm-up runs depth inference only when a depth-on row will run, so a depth-off run writes no depth cache.
    warm_depth = "model" if (setup.depth_ok and "on" in depths) else "off"
    warm_error = None
    try:
        warm_up(setup, sizes[0], warm_depth)
        setup.info["warm"] = (
            f"one untimed full-settings render at {sizes[0]}, depth {warm_depth}; "
            "one-time costs of the other size are not warmed"
        )
    except Exception as exc:  # the timed rows still run; the header and the exit code report the failure
        warm_error = f"{type(exc).__name__}: {str(exc)[:200]}".replace("\n", " ")
        setup.info["warm"] = f"error {warm_error}; the timed rows are not warmed"
    rows = run_rows(setup, sizes, depths, args.repeat)
    write_csv(rows, header_lines(setup.info), sys.stdout)
    return 1 if (warm_error is not None or exit_code(rows)) else 0


if __name__ == "__main__":
    raise SystemExit(main())