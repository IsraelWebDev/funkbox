import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FUNK2 = str(ROOT / "funk2.py")


def run(*args, out=None, env_extra=None, **kw):
    env = {**os.environ, **(env_extra or {})}
    if sys.platform == "linux" and "DISPLAY" not in env:
        env.setdefault("FUNK_GL_BACKEND", "egl")
    if out is not None:
        env["FUNK_OUT"] = str(out)
    return subprocess.run([sys.executable, FUNK2, *map(str, args)], capture_output=True, text=True,
                          cwd=ROOT, env=env, **kw)


def count_frames(mp4: Path) -> int:
    probe = shutil.which("ffprobe")
    if probe:
        r = subprocess.run([probe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(mp4)],
                           capture_output=True, text=True, check=True)
        return int(r.stdout.strip().splitlines()[0])
    import funk
    ff = funk.ffmpeg_exe()
    if not ff:
        pytest.skip("no ffprobe or ffmpeg")
    r = subprocess.run([ff, "-i", str(mp4), "-map", "0:v:0", "-f", "null", "-"],
                       capture_output=True, text=True)
    return int(re.findall(r"frame=\s*(\d+)", r.stderr)[-1])


def test_render_shader_makes_mp4_with_15_frames(gl, tmp_path):
    r = run("render", "--shader", "shaders/plasma.frag", "--size", "64", "--seconds", "0.5", out=tmp_path)
    assert r.returncode == 0, r.stderr
    mp4 = tmp_path / "plasma-s1.mp4"
    assert mp4.exists() and mp4.stat().st_size > 0
    assert re.search(r"15 frames 64x64 in [\d.]+s \([\d.]+ fps\)", r.stdout)
    assert count_frames(mp4) == 15
    for ext in ("png", "json"):
        assert (tmp_path / f"plasma-s1.{ext}").exists()
    assert not (tmp_path / "plasma-s1.gif").exists()      # GIF is opt-in (slow on busy loops)
    r = run("render", "--shader", "shaders/plasma.frag", "--size", "64", "--seconds", "0.5",
            "--gif", "--name", "withgif", out=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "withgif.gif").exists()


def test_sidecar_reproduces_render(gl, tmp_path):
    a = run("render", "--shader", "shaders/warp.frag", "--size", "48", "--seconds", "0.3",
            "--seed", "5", "--no-gif", "--no-mp4", out=tmp_path)
    assert a.returncode == 0, a.stderr
    b = run("still", "--recipe", tmp_path / "warp-s5.json", "--t", "0", "-o", tmp_path / "again.png",
            out=tmp_path)
    assert b.returncode == 0, b.stderr
    p1 = Image.open(tmp_path / "warp-s5.png").convert("RGB")
    p2 = Image.open(tmp_path / "again.png").convert("RGB")
    assert p1.tobytes() == p2.tobytes()


def test_check_reports_user_line_and_exit_code(gl, tmp_path):
    bad = tmp_path / "bad.frag"
    bad.write_text("// c\nvec4 effect(vec2 p) {\n  return vec4(nope);\n}\n")
    r = run("check", bad)
    assert r.returncode == 1
    assert ":3:" in r.stderr
    good = run("check", "shaders/plasma.frag")
    assert good.returncode == 0, good.stderr
    itime = tmp_path / "itime.frag"
    itime.write_text("vec4 effect(vec2 p){ return vec4(vec3(0.5+0.5*sin(5.0*p.x+iTime*1.3)),1.0); }\n")
    r = run("check", itime)
    assert r.returncode == 1 and "iTime" in r.stderr


def test_render_refuses_a_broken_loop(gl, tmp_path):
    itime = tmp_path / "itime.frag"
    itime.write_text("vec4 effect(vec2 p){ return vec4(vec3(0.5+0.5*sin(5.0*p.x+iTime*1.3)),1.0); }\n")
    r = run("render", "--shader", itime, "--size", "48", "--seconds", "0.5", out=tmp_path / "o")
    assert r.returncode == 1 and "iTime" in r.stderr


def test_usage_errors_exit_2(tmp_path):
    assert run("render").returncode == 2
    assert run("render", "--shader", "shaders/plasma.frag", "--post", "nope=1").returncode == 2
    assert run("nonsense").returncode == 2


def test_sheet_and_still(gl, tmp_path):
    r = run("sheet", "--shader", "shaders/voronoi_pop.frag", "--seeds", "1-6", "--tile", "64",
            "-o", tmp_path / "s.png")
    assert r.returncode == 0, r.stderr
    im = Image.open(tmp_path / "s.png")
    assert im.size == (3 * 64, 2 * 64)
    tiles = [im.crop(((i % 3) * 64, (i // 3) * 64, (i % 3) * 64 + 64, (i // 3) * 64 + 64)).tobytes()
             for i in range(6)]
    assert len(set(tiles)) == 6
    r = run("still", "--recipe", "examples/disco_cat.json", "--size", "96", "-o", tmp_path / "c.png")
    assert r.returncode == 0, r.stderr
    assert Image.open(tmp_path / "c.png").size == (96, 96)


def test_port_v1_is_idempotent():
    names = ["plasma", "moire", "warp", "tunnel", "blobs", "rings"]
    before = {n: hashlib.sha256((ROOT / "shaders" / f"{n}.frag").read_bytes()).hexdigest() for n in names}
    r = run("port-v1")
    assert r.returncode == 0, r.stderr
    run("port-v1")
    after = {n: hashlib.sha256((ROOT / "shaders" / f"{n}.frag").read_bytes()).hexdigest() for n in names}
    assert before == after


def test_info():
    r = run("info")
    assert r.returncode == 0, r.stderr
    assert "GL_RENDERER" in r.stdout and "plasma" in r.stdout


def test_render_with_audio(gl, tmp_path):
    import wave
    import numpy as np
    inp = tmp_path / "in"
    inp.mkdir()
    t = np.arange(11025) / 22050
    with wave.open(str(inp / "a.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(22050)
        w.writeframes((0.5 * np.sin(2 * np.pi * 100 * t) * 32767).astype("<i2").tobytes())
    frag = tmp_path / "react.frag"
    frag.write_text("vec4 effect(vec2 p){ return vec4(vec3(iAudio.x), 1.0); }\n")
    r = run("render", "--shader", frag, "--audio", inp / "a.wav", "--size", "32", "--fps", "10",
            "--no-gif", out=tmp_path / "o", env_extra={"FUNK_INPUTS": str(inp)})
    assert r.returncode == 0, r.stderr
    assert "warning" in r.stderr and "audio" in r.stderr
    assert "5 frames 32x32" in r.stdout                  # 0.5 s of audio at 10 fps
    probe = shutil.which("ffprobe")
    if probe:
        o = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(tmp_path / "o" / "react-s1.mp4")], capture_output=True, text=True, check=True)
        assert "audio" in o.stdout
