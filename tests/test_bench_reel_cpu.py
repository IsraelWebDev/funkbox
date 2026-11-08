"""Reel CPU benchmark (E4): columns, header, median, status, error and cache handling, and the refusals. No real renders."""

import csv
import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("bench_reel_cpu", ROOT / "tools" / "bench_reel_cpu.py")
bench = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bench  # dataclasses looks the module up by name
_SPEC.loader.exec_module(bench)


def _info() -> dict:
    return {
        "nt": False,
        "host": "test-host",
        "python": "3.12.0",
        "onnxruntime": "1.0.0",
        "gl": "test-gl",
        "software": False,
        "models": "models",
        "depth": "available at models/x.onnx",
        "depth_cache": "out/cache/depth",
        "fps": 30,
        "warm": "test warm-up",
        "out": "out/bench",
    }


def _run(total_py: float, total_ch: float, wall: float, partial: bool = False) -> dict:
    return {
        "output_s": 24.5,
        "cpu_python_s": total_py,
        "cpu_children_s": total_ch,
        "wall_s": wall,
        "cpu_total_s": total_py + total_ch,
        "partial": partial,
    }


def _fake_setup(tmp_path, render, clear) -> "bench.Setup":
    plan = SimpleNamespace(reel=SimpleNamespace(settings=SimpleNamespace(width=1080, height=1920, fps=30)))
    return bench.Setup(
        plan=plan,
        render=render,
        clear_depth_cache=clear,
        depth_ok=True,
        depth_reason="",
        out_root=tmp_path,
        info=_info(),
    )


def _table(text: str):
    return list(csv.reader(line for line in text.splitlines() if not line.startswith("#")))


def test_columns_are_the_agreed_ones():
    assert bench.COLUMNS == (
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


def test_csv_has_header_and_every_row_has_all_columns():
    rows = [
        bench.result_row("540x960", "on", 23.0, 40.0, 60.0, 90.0, note="median of 1 runs"),
        bench.result_row("720x1280", "off", 23.0, 80.0, 30.0, 120.0),
        bench.blank_row("540x960", "on", "not run", "depth model not found at models/x.onnx, with a comma"),
    ]
    buf = io.StringIO()
    bench.write_csv(rows, ["timer: test line"], buf)
    lines = buf.getvalue().splitlines()
    assert lines[0] == "# timer: test line"
    table = _table(buf.getvalue())
    assert table[0] == list(bench.COLUMNS)
    assert all(len(r) == len(bench.COLUMNS) for r in table[1:])
    ok = dict(zip(table[0], table[1]))
    assert ok["cpu_total_s"] == "100.00"
    assert ok["cpu_per_output_s"] == "4.35"
    assert ok["status"] == "ok"
    not_run = dict(zip(table[0], table[3]))
    assert not_run["status"] == "not run"
    assert not_run["cpu_total_s"] == ""
    assert "with a comma" in not_run["note"]


def test_header_has_an_onnxruntime_line():
    lines = bench.header_lines(_info())
    assert "onnxruntime: 1.0.0" in lines


def test_onnxruntime_version_is_unknown_when_the_import_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # makes "import onnxruntime" raise ImportError
    assert bench.onnxruntime_version() == "unknown"
    info = _info()
    info["onnxruntime"] = bench.onnxruntime_version()
    assert "onnxruntime: unknown" in bench.header_lines(info)


def test_parse_sizes_accepts_only_the_two_sizes():
    assert bench.parse_sizes("540x960,720x1280") == ["540x960", "720x1280"]
    assert bench.parse_sizes(" 720x1280 ") == ["720x1280"]
    with pytest.raises(ValueError, match="unknown size"):
        bench.parse_sizes("1920x1080")
    with pytest.raises(ValueError, match="no size"):
        bench.parse_sizes(" , ")


def test_unknown_size_is_refused_before_any_setup(monkeypatch, capsys):
    def must_not_run(*args, **kwargs):
        raise AssertionError("setup or a render started for an unknown size")

    monkeypatch.setattr(bench, "setup_environment", must_not_run)
    monkeypatch.setattr(bench, "install_child_tracking", must_not_run)
    with pytest.raises(SystemExit) as exc:
        bench.main(["--sizes", "1920x1080"])
    assert exc.value.code == 2
    assert "unknown size" in capsys.readouterr().err


def test_reel_depth_override_is_refused_before_any_setup(monkeypatch, capsys):
    monkeypatch.setenv("REEL_DEPTH", "model")

    def must_not_run(*args, **kwargs):
        raise AssertionError("setup ran while REEL_DEPTH was set")

    monkeypatch.setattr(bench, "setup_environment", must_not_run)
    monkeypatch.setattr(bench, "install_child_tracking", must_not_run)
    with pytest.raises(SystemExit) as exc:
        bench.main(["--sizes", "540x960"])
    assert exc.value.code == 2
    assert "REEL_DEPTH" in capsys.readouterr().err


def test_median_of_three_takes_the_middle_run_by_cpu_total():
    runs = [_run(50, 0, 5), _run(40, 40, 9), _run(70, 30, 7)]  # totals 50, 80, 100
    row = bench.aggregate_runs("540x960", "off", runs)
    assert row["cpu_total_s"] == 80.0
    assert row["cpu_python_s"] == 40.0
    assert row["cpu_children_s"] == 40.0
    assert row["wall_s"] == 9.0  # the wall of the same run
    assert row["status"] == "ok"
    assert "median of 3 runs" in row["note"]


def test_median_of_four_is_the_mean_of_the_two_middle_runs():
    runs = [_run(10, 0, 1), _run(20, 0, 2), _run(30, 0, 3), _run(40, 0, 4)]  # middle totals 20 and 30
    row = bench.aggregate_runs("720x1280", "on", runs)
    assert row["cpu_total_s"] == 25.0
    assert row["cpu_python_s"] == 25.0
    assert row["wall_s"] == 2.5


def test_a_partial_run_makes_the_row_partial():
    runs = [_run(10, 5, 1), _run(10, 5, 1, partial=True)]
    row = bench.aggregate_runs("540x960", "off", runs, ["child CPU not read for 1 process(es) in at least one run"])
    assert row["status"] == "partial"
    assert "child CPU not read" in row["note"]


def test_render_error_gives_an_error_row_and_exit_code_1(monkeypatch, capsys, tmp_path):
    def render(plan, out_dir, depth_mode):
        if out_dir.name == "warmup":
            return SimpleNamespace(duration=1.0)
        raise RuntimeError("boom")

    setup = _fake_setup(tmp_path, render, lambda: None)
    monkeypatch.setattr(bench, "setup_environment", lambda out_root: setup)
    monkeypatch.setattr(bench, "install_child_tracking", lambda: None)
    monkeypatch.delenv("REEL_DEPTH", raising=False)
    code = bench.main(["--sizes", "540x960", "--depth", "off"])
    assert code == 1
    table = _table(capsys.readouterr().out)
    row = dict(zip(table[0], table[1]))
    assert row["status"] == "error"
    assert "boom" in row["note"]
    assert row["cpu_total_s"] == ""


def test_depth_off_after_depth_on_does_not_read_the_cache(tmp_path):
    cache: dict = {}
    clears: list[int] = []
    seen: list[tuple] = []

    def get_depth(mode):  # the same shape as funkbox depth.get_depth: "off" returns before the cache is consulted
        if mode == "off":
            return None
        if "frame" in cache:
            return "from cache"
        cache["frame"] = True
        return "computed"

    def clear():
        cache.clear()
        clears.append(1)

    def render(plan, out_dir, depth_mode):
        if out_dir.name != "warmup":
            seen.append((depth_mode, get_depth(depth_mode)))
        return SimpleNamespace(duration=24.5)

    setup = _fake_setup(tmp_path, render, clear)
    rows = bench.run_rows(setup, ["540x960"], ["on", "off"], 1)
    assert seen == [("model", "computed"), ("off", None)]
    assert len(clears) == 1  # cleared before the depth-on render only
    assert [r["status"] for r in rows] == ["ok", "ok"]