"""Doctor and GL fallback behaviour without a real OpenGL driver."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from funkbox import cli, config
from funkbox.gl import context, hints

ROOT = Path(__file__).resolve().parent.parent


class FakeContext:
    def __init__(self, renderer="Fake GPU", version="3.3"):
        self.info = {"GL_RENDERER": renderer, "GL_VERSION": version}
        self.released = False

    def release(self):
        self.released = True


def install(monkeypatch, outcomes, renderers=None):
    renderers = renderers or {}
    calls = []
    created = []

    def label_for(kwargs, env):
        label = "egl" if kwargs.get("backend") == "egl" else "default"
        if env.get("LIBGL_ALWAYS_SOFTWARE") == "1":
            label += "-software"
        return label

    def fake(require=330, **kwargs):
        env = dict(os.environ)
        label = label_for(kwargs, env)
        calls.append({"label": label, "kwargs": dict(kwargs), "env": env, "require": require})
        outcome = outcomes.get(label)
        if isinstance(outcome, BaseException):
            raise outcome
        if not outcome:
            raise OSError("no context")
        ctx = FakeContext(renderers.get(label, "Fake GPU"))
        created.append(ctx)
        return ctx

    monkeypatch.setattr(context.moderngl, "create_standalone_context", fake)
    return calls, created


@pytest.fixture(autouse=True)
def clean_gl(monkeypatch):
    context.reset()
    monkeypatch.delenv("FUNK_GL_BACKEND", raising=False)
    monkeypatch.setattr(context, "_config", lambda: SimpleNamespace(gl_backend=""))
    yield
    context.reset()


def test_default_hardware_works_without_notice(monkeypatch, capsys):
    calls, _ = install(monkeypatch, {"default": True})
    ctx = context.create_context()
    assert isinstance(ctx, FakeContext)
    assert context.SOFTWARE is False
    assert len(calls) == 1 and calls[0]["label"] == "default"
    assert "software OpenGL" not in capsys.readouterr().err


def test_falls_back_to_egl_software_and_restores_environment(monkeypatch, capsys):
    monkeypatch.delenv("LIBGL_ALWAYS_SOFTWARE", raising=False)
    monkeypatch.setenv("GALLIUM_DRIVER", "old-driver")
    err = OSError("libEGL.so not loaded")
    calls, _ = install(monkeypatch, {"default": err, "egl": err, "egl-software": True})
    ctx = context.create_context()
    assert isinstance(ctx, FakeContext)
    assert context.SOFTWARE is True
    assert [c["label"] for c in calls] == ["default", "egl", "egl-software"]
    assert calls[-1]["env"]["EGL_PLATFORM"] == "surfaceless"
    assert os.environ["GALLIUM_DRIVER"] == "old-driver"
    assert "LIBGL_ALWAYS_SOFTWARE" not in os.environ
    assert "EGL_PLATFORM" not in os.environ
    errout = capsys.readouterr().err
    assert errout.count("using software OpenGL") == 1


def test_every_attempt_failing_raises_with_debian_hint(monkeypatch):
    err = OSError("libEGL.so not loaded")
    install(monkeypatch, {"default": err, "egl": err, "egl-software": err, "default-software": err})
    monkeypatch.setattr(hints, "read_os_release", lambda path="/etc/os-release": {"ID": "debian"})
    monkeypatch.setattr(hints.platform, "system", lambda: "Linux")
    with pytest.raises(context.GLUnavailable) as caught:
        context.create_context()
    message = str(caught.value)
    assert "sudo apt install -y libegl1 libegl-mesa0 libgl1-mesa-dri" in message
    assert "libEGL" in message


def test_software_backend_tries_only_software_attempts(monkeypatch):
    monkeypatch.setattr(context, "_config", lambda: SimpleNamespace(gl_backend="software"))
    calls, _ = install(monkeypatch, {"egl-software": OSError("no"), "default-software": True})
    ctx = context.create_context()
    assert isinstance(ctx, FakeContext)
    assert [c["label"] for c in calls] == ["egl-software", "default-software"]
    assert all("software" in c["label"] for c in calls)
    assert calls[0]["kwargs"] == {"backend": "egl"}
    assert calls[0]["env"]["EGL_PLATFORM"] == "surfaceless"
    assert calls[1]["kwargs"] == {}


@pytest.mark.parametrize("release, command", [
    ({"ID": "debian"}, "sudo apt install"),
    ({"ID": "ubuntu"}, "sudo apt install"),
    ({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}, "sudo apt install"),
    ({"ID": "fedora"}, "sudo dnf install"),
    ({"ID": "rhel"}, "sudo dnf install"),
    ({"ID": "centos"}, "sudo dnf install"),
    ({"ID": "arch"}, "sudo pacman -S --needed mesa libglvnd"),
    ({"ID": "alpine"}, "sudo apk add mesa-egl mesa-dri-gallium mesa-gl"),
    ({"ID": "suse"}, "sudo zypper install"),
    ({"ID": "opensuse-tumbleweed", "ID_LIKE": "opensuse suse"}, "sudo zypper install"),
    ({"ID": "plan9"}, "install Mesa's EGL, GL and DRI packages"),
    ({}, "install Mesa's EGL, GL and DRI packages"),
])
def test_install_hint_package_managers(release, command):
    text = hints.install_hint([], system="Linux", os_release=release)
    assert command in text
    assert "python -m funkbox doctor" in text


def test_install_hint_is_empty_off_linux():
    assert hints.install_hint(["libEGL.so not loaded"], system="Darwin") == ""
    assert hints.install_hint(["libEGL.so not loaded"], system="Windows") == ""


def test_read_os_release_parses_and_tolerates_missing_file(tmp_path):
    p = tmp_path / "os-release"
    p.write_text('# comment\nID=debian\nID_LIKE="debian ubuntu"\nNAME=\'Debian\'\nBADLINE\n',
                 encoding="utf-8")
    assert hints.read_os_release(str(p)) == {
        "ID": "debian", "ID_LIKE": "debian ubuntu", "NAME": "Debian"}
    assert hints.read_os_release(str(tmp_path / "absent")) == {}


def test_probe_tries_all_four_and_never_caches(monkeypatch, capsys):
    calls, created = install(monkeypatch, {
        "default": True, "egl": True, "egl-software": True, "default-software": True},
        renderers={"egl-software": "llvmpipe (LLVM 18.1)", "default-software": "Fake GPU"})
    probes = context.probe()
    assert [p.label for p in probes] == ["default", "egl", "egl-software", "default-software"]
    assert [c["label"] for c in calls] == ["default", "egl", "egl-software", "default-software"]
    assert all(p.ok for p in probes)
    assert probes[2].detail == "llvmpipe (LLVM 18.1)"
    assert probes[0].software is False and probes[2].software is True
    assert context._CTX is None
    assert context.SOFTWARE is False
    assert all(c.released for c in created)
    assert capsys.readouterr().err == ""


def test_doctor_reports_software_only_as_success(monkeypatch, capsys, tmp_path):
    err = OSError("libEGL.so not loaded")
    install(monkeypatch, {"default": err, "egl": err, "egl-software": True})
    missing = tmp_path / "missing"
    fake_cfg = SimpleNamespace(describe=lambda: ["test config"], fastsd_dir=missing,
                               gimp_mcp_dir=missing, gimp_exe=None, fake_sd=False)
    monkeypatch.setattr(config, "get", lambda *a, **k: fake_cfg)
    import funkbox.media as media
    monkeypatch.setattr(media, "ffmpeg_exe", lambda: "ffmpeg")
    from funkbox.reel import models
    monkeypatch.setattr(models, "status", lambda: {})
    rc = cli.cmd_doctor(None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "GL default" in out and "GL egl-software" in out
    assert "software only" in out
    assert "FAIL OpenGL" not in out
