"""funkbox doctor --bench: time a few frames on this machine and estimate a Short's render."""

from types import SimpleNamespace

import pytest

from funkbox import cli
from funkbox.gl import bench


def test_estimate_seconds_scales_with_frames():
    assert bench.estimate_seconds(30.0) == pytest.approx(20.0)  # 600 frames at 30 frames/s
    assert bench.estimate_seconds(10.0, video_seconds=15, video_fps=24) == pytest.approx(36.0)
    with pytest.raises(ValueError):
        bench.estimate_seconds(0)


def test_report_names_backend_rate_and_estimate():
    res = bench.BenchResult("liquid-disco", 1080, 1920, 30, 1.5, "llvmpipe (LLVM 20)", True)
    assert res.fps == pytest.approx(20.0)
    text = bench.report(res)
    assert "30 frames of 'liquid-disco' at 1080x1920 in 1.50 s = 20.0 frames/s" in text
    assert "software OpenGL: llvmpipe" in text
    assert "about 30 s to draw its frames" in text
    gpu = bench.report(bench.BenchResult("op-art", 1080, 1920, 30, 0.5, "Intel Iris Xe", False))
    assert "(GPU: Intel Iris Xe)" in gpu


def test_run_rejects_bad_input():
    with pytest.raises(ValueError):
        bench.run(frames=0)
    with pytest.raises(ValueError, match="unknown look"):
        bench.run(frames=1, look="no-such-look")


def test_run_times_real_frames(gl):
    res = bench.run(frames=3, width=108, height=192)
    assert (res.width, res.height, res.frames) == (108, 192, 3)
    assert res.seconds > 0 and res.fps > 0
    assert res.renderer


def _doctor_env(monkeypatch, tmp_path):
    from funkbox import config
    import funkbox.media as media
    from funkbox.gl import context
    from funkbox.reel import models

    missing = tmp_path / "missing"
    cfg = SimpleNamespace(
        describe=lambda: ["test config"], fastsd_dir=missing, gimp_mcp_dir=missing, gimp_exe=None, fake_sd=False
    )
    monkeypatch.setattr(config, "get", lambda *a, **k: cfg)
    monkeypatch.setattr(media, "ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(models, "status", lambda: {})
    monkeypatch.setattr(
        context, "probe", lambda: [SimpleNamespace(ok=True, label="egl", detail="Fake", software=False)]
    )


def test_doctor_bench_flag_prints_bench(monkeypatch, capsys, tmp_path):
    _doctor_env(monkeypatch, tmp_path)
    calls = []

    def fake_run(frames=30, **kw):
        calls.append(frames)
        return bench.BenchResult("liquid-disco", 1080, 1920, frames, 1.0, "Fake GPU", False)

    monkeypatch.setattr(bench, "run", fake_run)
    args = cli.build_parser().parse_args(["doctor", "--bench", "--bench-frames", "12"])
    assert cli.cmd_doctor(args) == 0
    out = capsys.readouterr().out
    assert calls == [12]
    assert "BENCH" in out and "12 frames of 'liquid-disco'" in out and "about 50 s" in out


def test_doctor_without_bench_does_not_render(monkeypatch, capsys, tmp_path):
    _doctor_env(monkeypatch, tmp_path)
    monkeypatch.setattr(bench, "run", lambda **kw: pytest.fail("bench ran without --bench"))
    assert cli.cmd_doctor(cli.build_parser().parse_args(["doctor"])) == 0
    assert cli.cmd_doctor(None) == 0  # older callers pass no args
    assert "BENCH" not in capsys.readouterr().out


def test_doctor_bench_failure_is_reported(monkeypatch, capsys, tmp_path):
    _doctor_env(monkeypatch, tmp_path)

    def boom(**kw):
        raise RuntimeError("no context")

    monkeypatch.setattr(bench, "run", boom)
    assert cli.cmd_doctor(cli.build_parser().parse_args(["doctor", "--bench"])) == 1
    assert "FAIL bench" in capsys.readouterr().out
