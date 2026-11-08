"""Reel CPU benchmark (E4): the CSV has the agreed columns, and an unknown size is refused before any render. No rendering."""

import csv
import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("bench_reel_cpu", ROOT / "tools" / "bench_reel_cpu.py")
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


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
    table = list(csv.reader(line for line in lines if not line.startswith("#")))
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


def test_parse_sizes_accepts_only_the_two_sizes():
    assert bench.parse_sizes("540x960,720x1280") == ["540x960", "720x1280"]
    assert bench.parse_sizes(" 720x1280 ") == ["720x1280"]
    with pytest.raises(ValueError, match="unknown size"):
        bench.parse_sizes("1920x1080")
    with pytest.raises(ValueError, match="no size"):
        bench.parse_sizes(" , ")


def test_unknown_size_is_refused_before_any_render(monkeypatch, capsys):
    def must_not_run(*args, **kwargs):
        raise AssertionError("a render was started for an unknown size")

    monkeypatch.setattr(bench, "measure", must_not_run)
    monkeypatch.setattr(bench, "install_child_tracking", must_not_run)
    with pytest.raises(SystemExit) as exc:
        bench.main(["--sizes", "1920x1080"])
    assert exc.value.code == 2
    assert "unknown size" in capsys.readouterr().err