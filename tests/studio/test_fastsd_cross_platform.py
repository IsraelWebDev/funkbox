"""FastSD start/stop/config behave the same on Windows, macOS and Linux.

Every test fakes the platform (apps.WIN, config._is_windows), the Popen handle and the port probes, so
none of them starts a real FastSD, touches the network or needs GIMP, and all run on any OS.
"""

import subprocess

import pytest

from funkbox import config
from funkbox.studio import apps


class FakeProc:
    """Stands in for the subprocess.Popen handle of the FastSD child."""

    def __init__(self, pid=4242, hang=False):
        self.pid = pid
        self.hang = hang  # terminate() is ignored, so wait(timeout) times out
        self.calls = []
        self.dead = False

    def poll(self):
        return 0 if self.dead else None

    def terminate(self):
        self.calls.append("terminate")
        self.dead = not self.hang

    def kill(self):
        self.calls.append("kill")
        self.dead = True

    def wait(self, timeout=None):
        self.calls.append("wait")
        if not self.dead:
            raise subprocess.TimeoutExpired("fastsd", timeout)
        return 0


@pytest.fixture
def fs(tmp_path, monkeypatch):
    d = tmp_path / "fastsd"
    (d / "env" / "bin").mkdir(parents=True)
    (d / "env" / "bin" / "python").write_text("")
    monkeypatch.setenv("FASTSD_DIR", str(d))
    monkeypatch.delenv("FASTSD_DEVICE", raising=False)
    monkeypatch.setattr(apps, "_ensure_log", lambda: None)
    monkeypatch.setattr(apps, "wait_port", lambda *a, **k: True)
    monkeypatch.setattr(apps, "port_open", lambda *a, **k: False)
    monkeypatch.setattr(apps.FastSD, "healthy", lambda self: False)
    f = apps.FastSD()
    f._reaper = True  # no idle-reaper thread
    return f


def _start(fs, monkeypatch, win):
    monkeypatch.setattr(apps, "WIN", win)
    seen = {}

    def fake_popen(cmd, **kw):
        seen.update(kw)
        seen["proc"] = FakeProc()
        return seen["proc"]

    monkeypatch.setattr(apps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(apps, "_detached_flags", lambda: 8 if win else 0)
    fs.start(timeout=1)
    return seen


# ---- stop() ------------------------------------------------------------------------------------------
def test_stop_terminates_the_process_we_started_on_posix(fs, monkeypatch):
    seen = _start(fs, monkeypatch, win=False)
    monkeypatch.setattr(apps, "pid_on_port", lambda port: pytest.fail("no port lookup when we hold the handle"))
    assert "stopped" in fs.stop()
    assert seen["proc"].calls[0] == "terminate"
    assert "kill" not in seen["proc"].calls
    assert fs._proc is None and fs.started_by_us is False


def test_stop_kills_when_terminate_times_out(fs, monkeypatch):
    _start(fs, monkeypatch, win=False)
    fs._proc = proc = FakeProc(hang=True)
    assert "stopped" in fs.stop()
    assert proc.calls[0] == "terminate" and "kill" in proc.calls


def test_stop_uses_taskkill_on_windows(fs, monkeypatch):
    _start(fs, monkeypatch, win=True)
    fs._proc = proc = FakeProc(pid=777)
    ran = []
    monkeypatch.setattr(apps.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or proc.kill())
    assert "stopped" in fs.stop()
    assert ["taskkill", "/PID", "777", "/T", "/F"] in ran


def test_stop_with_nothing_running_says_so(fs, monkeypatch):
    monkeypatch.setattr(apps, "pid_on_port", lambda port: None)
    assert fs.stop() == "FastSD not running"


def test_stop_falls_back_to_port_lookup_when_not_started_by_us(fs, monkeypatch):
    killed = []
    monkeypatch.setattr(apps, "pid_on_port", lambda port: 99)
    monkeypatch.setattr(apps, "kill_tree", killed.append)
    assert "pid 99" in fs.stop()
    assert killed == [99]


def test_pid_on_port_posix_uses_lsof_or_gives_up(monkeypatch):
    monkeypatch.setattr(apps, "WIN", False)
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)
    assert apps.pid_on_port(8000) is None
    cmds = []
    monkeypatch.setattr(apps.shutil, "which", lambda name: "/usr/bin/lsof")
    monkeypatch.setattr(
        apps.subprocess,
        "run",
        lambda cmd, **kw: cmds.append(cmd) or subprocess.CompletedProcess(cmd, 0, stdout="321\n", stderr=""),
    )
    assert apps.pid_on_port(8000) == 321
    assert cmds[0][:2] == ["lsof", "-t"] and "-iTCP:8000" in cmds[0] and "-sTCP:LISTEN" in cmds[0]


# ---- start() -----------------------------------------------------------------------------------------
def test_openvino_path_only_on_windows(fs, monkeypatch):
    assert "openvino" not in _start(fs, monkeypatch, win=False)["env"]["PATH"]
    assert "openvino" in _start(fs, monkeypatch, win=True)["env"]["PATH"]


def test_start_new_session_on_posix_creationflags_on_windows(fs, monkeypatch):
    posix = _start(fs, monkeypatch, win=False)
    assert posix["start_new_session"] is True and not posix.get("creationflags")
    win = _start(fs, monkeypatch, win=True)
    assert win["creationflags"] == 8 and not win.get("start_new_session")


# ---- default device ----------------------------------------------------------------------------------
@pytest.mark.parametrize("win,expected", [(True, "GPU"), (False, "CPU")])
def test_default_device_per_platform(monkeypatch, tmp_path, win, expected):
    monkeypatch.delenv("FASTSD_DEVICE", raising=False)
    monkeypatch.setattr(config, "_is_windows", lambda: win)
    assert config.get(tmp_path).fastsd_device == expected


@pytest.mark.parametrize("win", [True, False])
def test_env_and_toml_override_the_default_device(monkeypatch, tmp_path, win):
    monkeypatch.setattr(config, "_is_windows", lambda: win)
    monkeypatch.setenv("FASTSD_DEVICE", "NPU")
    assert config.get(tmp_path).fastsd_device == "NPU"
    monkeypatch.delenv("FASTSD_DEVICE")
    (tmp_path / "funkbox.toml").write_text('fastsd_device = "GPU"\n')
    assert config.get(tmp_path).fastsd_device == "GPU"
