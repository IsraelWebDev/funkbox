"""Reel CPU benchmark (E4): columns, header, medians, status, errors, warm-up and cache handling, and the refusals.

No real renders: every render is a stub, and the child-process tracking is not installed.
"""

import csv
import importlib.metadata
import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("bench_reel_cpu", ROOT / "tools" / "bench_reel_cpu.py")
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


@pytest.fixture(autouse=True)
def _config_in_tmp(tmp_path, monkeypatch):
    """No test reads the repository's funkbox.toml: main() and depth_override() use a temporary folder."""
    monkeypatch.setattr(bench, "CONFIG_ROOT", tmp_path)


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


def _no_setup_no_tracking(monkeypatch, setup=None):
    """Stop main() before any real setup or child tracking; with a setup, use that instead of the real one."""

    def must_not_run(*args, **kwargs):
        raise AssertionError("setup or child tracking ran where it must not")

    if setup is None:
        monkeypatch.setattr(bench, "setup_environment", must_not_run)
    else:
        monkeypatch.setattr(bench, "setup_environment", lambda out_root: setup)
    monkeypatch.setattr(bench, "install_child_tracking", lambda: None if setup is not None else must_not_run())
    monkeypatch.delenv("REEL_DEPTH", raising=False)


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
    assert buf.getvalue().splitlines()[0] == "# timer: test line"
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


def test_per_second_columns_are_the_totals_divided_by_the_output_seconds():
    row = bench.result_row("720x1280", "on", 24.5, 117.84, 49.10, 84.72)
    assert row["cpu_per_output_s"] == pytest.approx((117.84 + 49.10) / 24.5)
    assert row["wall_per_output_s"] == pytest.approx(84.72 / 24.5)
    agg = bench.aggregate_runs("720x1280", "on", [_run(117.84, 49.10, 84.72)])
    assert agg["cpu_per_output_s"] == pytest.approx((117.84 + 49.10) / 24.5)
    assert agg["wall_per_output_s"] == pytest.approx(84.72 / 24.5)


def test_header_has_an_onnxruntime_line():
    try:
        import onnxruntime  # noqa: F401 - the import name; onnxruntime-gpu and onnxruntime-directml provide it too
    except ImportError:
        pytest.skip("the onnxruntime module cannot be imported here, so there is no version to check")
    # The version comes from the installed distribution that provides the module, not from the function under test.
    dists = importlib.metadata.packages_distributions().get("onnxruntime", [])
    assert dists, "onnxruntime imports, but no installed distribution provides it"
    expected = importlib.metadata.version(dists[0])
    assert bench.onnxruntime_version() == expected
    info = _info()
    info["onnxruntime"] = expected
    assert f"onnxruntime: {expected}" in bench.header_lines(info)


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
    _no_setup_no_tracking(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        bench.main(["--sizes", "1920x1080"])
    assert exc.value.code == 2
    assert "unknown size" in capsys.readouterr().err


def test_reel_depth_env_is_refused_before_any_setup(monkeypatch, capsys):
    _no_setup_no_tracking(monkeypatch)
    monkeypatch.setenv("REEL_DEPTH", "model")
    with pytest.raises(SystemExit) as exc:
        bench.main(["--sizes", "540x960"])
    assert exc.value.code == 2
    assert "REEL_DEPTH" in capsys.readouterr().err


def test_reel_depth_in_funkbox_toml_is_read_and_is_an_override(tmp_path, monkeypatch):
    monkeypatch.delenv("REEL_DEPTH", raising=False)
    assert bench.depth_override(tmp_path) == ""  # no funkbox.toml in the temporary folder
    (tmp_path / "funkbox.toml").write_text('reel_depth = "model"\n', encoding="utf-8")
    assert bench.depth_override(tmp_path) == "model"


def test_reel_depth_in_funkbox_toml_refuses_through_main(tmp_path, monkeypatch, capsys):
    (tmp_path / "funkbox.toml").write_text('reel_depth = "model"\n', encoding="utf-8")
    _no_setup_no_tracking(monkeypatch)
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
    row = bench.aggregate_runs("540x960", "off", runs, [bench.PARTIAL_NOTE])
    assert row["status"] == "partial"
    assert "could not be opened" in row["note"]


def test_a_failed_child_handle_makes_the_row_partial_and_the_next_window_is_clean(tmp_path):
    def render_with_failed_handle(plan, out_dir, depth_mode):
        bench._on_child_started(0)  # what the tracked Popen does when OpenProcess returns NULL
        return SimpleNamespace(duration=24.5)

    def render_clean(plan, out_dir, depth_mode):
        return SimpleNamespace(duration=24.5)

    setup = _fake_setup(tmp_path, render_with_failed_handle, lambda: None)
    first = bench.measure(setup, "540x960", "off", tmp_path / "a", 1)
    assert first["status"] == "partial"
    assert "could not be opened" in first["note"]
    setup.render = render_clean
    second = bench.measure(setup, "540x960", "off", tmp_path / "b", 1)
    assert second["status"] == "ok"
    assert second["note"] == ""


def test_clear_cache_failure_becomes_an_error_row_and_the_csv_is_printed(monkeypatch, capsys, tmp_path):
    def clear_locked():
        raise PermissionError("locked by another process: depth_x.npy")

    def render(plan, out_dir, depth_mode):
        return SimpleNamespace(duration=24.5)

    _no_setup_no_tracking(monkeypatch, _fake_setup(tmp_path, render, clear_locked))
    code = bench.main(["--sizes", "540x960", "--depth", "on"])
    assert code == 1
    table = _table(capsys.readouterr().out)
    row = dict(zip(table[0], table[1]))
    assert row["status"] == "error"
    assert "PermissionError" in row["note"]


def test_render_error_gives_an_error_row_and_exit_code_1(monkeypatch, capsys, tmp_path):
    def render(plan, out_dir, depth_mode):
        if out_dir.name == "warmup":
            return SimpleNamespace(duration=1.0)
        raise RuntimeError("boom")

    _no_setup_no_tracking(monkeypatch, _fake_setup(tmp_path, render, lambda: None))
    code = bench.main(["--sizes", "540x960", "--depth", "off"])
    assert code == 1
    table = _table(capsys.readouterr().out)
    row = dict(zip(table[0], table[1]))
    assert row["status"] == "error"
    assert "boom" in row["note"]
    assert row["cpu_total_s"] == ""


def test_warm_up_error_message_stays_on_one_header_line(monkeypatch, capsys, tmp_path):
    def render(plan, out_dir, depth_mode):
        if out_dir.name == "warmup":
            raise RuntimeError("line one\r\nline two\rthree")
        return SimpleNamespace(duration=24.5)

    _no_setup_no_tracking(monkeypatch, _fake_setup(tmp_path, render, lambda: None))
    code = bench.main(["--sizes", "540x960", "--depth", "off"])
    out = capsys.readouterr().out
    assert code == 1
    warm = [line for line in out.split("\n") if line.startswith("# warm-up")]
    assert len(warm) == 1
    assert "line one line two three" in warm[0]


def test_warm_up_failure_still_prints_the_csv_and_exits_1(monkeypatch, capsys, tmp_path):
    def render(plan, out_dir, depth_mode):
        if out_dir.name == "warmup":
            raise RuntimeError("warm boom")
        return SimpleNamespace(duration=24.5)

    _no_setup_no_tracking(monkeypatch, _fake_setup(tmp_path, render, lambda: None))
    code = bench.main(["--sizes", "540x960", "--depth", "off"])
    out = capsys.readouterr().out
    assert code == 1
    assert "# warm-up: error RuntimeError: warm boom; the timed rows are not warmed" in out.splitlines()
    table = _table(out)
    assert table[0] == list(bench.COLUMNS)
    row = dict(zip(table[0], table[1]))
    assert row["status"] == "ok"  # the timed render itself worked


@pytest.mark.parametrize("flag, expected", [("off", "off"), ("on", "model"), ("both", "model")])
def test_warm_up_runs_depth_only_when_a_depth_on_row_will_run(monkeypatch, capsys, tmp_path, flag, expected):
    warm_modes = []

    def render(plan, out_dir, depth_mode):
        if out_dir.name == "warmup":
            warm_modes.append(depth_mode)
        return SimpleNamespace(duration=24.5)

    _no_setup_no_tracking(monkeypatch, _fake_setup(tmp_path, render, lambda: None))
    bench.main(["--sizes", "540x960", "--depth", flag])
    assert warm_modes == [expected]


def test_depth_off_is_passed_to_render(tmp_path):
    # funkbox/reel/depth.py get_depth() returns None for "off" before it looks at the cache. This test only checks
    # that the benchmark passes "off" to the render, and clears the depth cache before the depth-on render only.
    clears = []
    modes = []

    def render(plan, out_dir, depth_mode):
        if out_dir.name != "warmup":
            modes.append(depth_mode)
        return SimpleNamespace(duration=24.5)

    setup = _fake_setup(tmp_path, render, lambda: clears.append(1))
    rows = bench.run_rows(setup, ["540x960"], ["on", "off"], 1)
    assert modes == ["model", "off"]
    assert len(clears) == 1
    assert [r["status"] for r in rows] == ["ok", "ok"]