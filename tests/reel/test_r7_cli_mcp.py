import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def small_demo_reel(demo):
    d = json.loads((demo / "demo.reel.json").read_text())
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    return d


def test_full_demo_renders_draft_in_container(demo, gl, work):
    from funkbox.reel import render

    d = small_demo_reel(demo)
    d["settings"] = {"width": 1080, "height": 1920, "fps": 30, "safe_area": "reels"}
    out = render.render_reel(d, "draft", base_dir=demo, out_dir=work, draft=True, audit=True)
    assert (out.width, out.height, out.fps) == (540, 960, 24)
    assert out.audit_ok is True
    probe = subprocess.run(
        [__import__("funkbox.media", fromlist=["x"]).ffmpeg_exe(), "-hide_banner", "-i", out.paths["mp4"]],
        capture_output=True,
        text=True,
    ).stderr
    assert "540x960" in probe and "24 fps" in probe and "Audio: aac" in probe
    assert abs(out.duration - 25.0) < 1.5, out.duration
    print(out.report_line())


def test_storyboard_has_one_tile_per_shot_plus_end_card(demo, gl, work):
    from funkbox.reel import render
    from funkbox.reel.storyboard import storyboard

    plan = render.prepare(small_demo_reel(demo), demo)
    sb = storyboard(plan, work / "sb.png", tile_w=135)
    assert len(plan.spans) == 13 and plan.spans[-1].kind == "end"
    cols, tile_h = 4, round(135 * 480 / 270)
    rows = -(-13 // cols)
    assert sb.height > rows * tile_h and sb.width >= cols * 135
    # every tile area is populated (not blank); labels live below the frame
    for i in range(13):
        x = 10 + (i % cols) * (135 + 10)
        y = 10 + (i // cols) * (
            tile_h + sb.height // 40 * 0 + (sb.height - 10 - rows * 10 - rows * tile_h) // rows + 10
        )
        tile = sb.crop((x, y, x + 135, y + tile_h)).convert("L")
        assert tile.getextrema()[1] - tile.getextrema()[0] > 20


def test_storyboard_report_prints_the_beat_grid_and_cut_distances(demo, gl, capsys):
    from funkbox.reel import render
    from funkbox.reel.storyboard import print_beat_grid

    d = small_demo_reel(demo)
    d["audio"] = {**d.get("audio", {}), "bpm": 120}  # a fixed grid: one beat = 0.5 s
    plan = render.prepare(d, demo)
    print_beat_grid(plan)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("beat grid: 120.0 BPM, ") and " beats in " in lines[0]
    assert lines[1] == "first beats (s): " + ", ".join(f"{k * 0.5:.3f}" for k in range(8))
    cuts = lines[2:]
    assert len(cuts) == len(plan.spans) - 1  # one line per cut: every shot start after the first
    for line, sp in zip(cuts, plan.spans[1:]):
        beat_ms = abs(sp.start - round(sp.start / 0.5) * 0.5) * 1000
        assert line == f"cut {sp.id} at {sp.start:.2f}s: {beat_ms:.0f} ms from the nearest beat"


def test_storyboard_report_uses_the_detected_grid_of_the_music(demo, gl, capsys):
    from funkbox.reel import audio, render
    from funkbox.reel.storyboard import print_beat_grid

    d = small_demo_reel(demo)
    assert d["audio"].get("music") and not d["audio"].get("bpm")  # detected from click120.wav
    plan = render.prepare(d, demo)
    grid = audio.beat_grid_for(plan.reel)
    print_beat_grid(plan)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("beat grid: ") and " BPM, " in lines[0], lines[0]
    assert lines[1] == "first beats (s): " + ", ".join(f"{b:.3f}" for b in grid[:8])
    for line, sp in zip(lines[2:], plan.spans[1:]):
        ms = min(abs(sp.start - b) for b in grid) * 1000
        assert line == f"cut {sp.id} at {sp.start:.2f}s: {ms:.0f} ms from the nearest beat"


def test_storyboard_report_says_none_without_a_grid(demo, gl, capsys):
    from funkbox.reel import render
    from funkbox.reel.storyboard import print_beat_grid

    d = small_demo_reel(demo)
    d["audio"] = {"voiceover": True}  # no music, no bpm
    for sh in d["shots"]:
        if str(sh.get("duration", "")).startswith("beats:"):
            sh["duration"] = 1.0
    print_beat_grid(render.prepare(d, demo))
    assert capsys.readouterr().out.strip() == "beat grid: none"


def test_storyboard_report_says_why_when_the_beat_grid_cannot_be_made(demo, gl, capsys, monkeypatch):
    from funkbox.reel import audio, render
    from funkbox.reel.storyboard import beat_grid_lines, print_beat_grid

    plan = render.prepare(small_demo_reel(demo), demo)

    def boom(exc):
        def f(reel):
            raise exc

        return f

    monkeypatch.setattr(audio, "beat_grid_for", boom(RuntimeError("cannot decode x.wav: Invalid data")))
    assert beat_grid_lines(plan) == ["beat grid: none (Invalid data)"]
    monkeypatch.setattr(audio, "beat_grid_for", boom(ValueError("audio too short or silent for tempo estimation")))
    print_beat_grid(plan)  # does not raise
    assert capsys.readouterr().out.strip() == "beat grid: none (audio too short or silent for tempo estimation)"
    monkeypatch.setattr(audio, "beat_grid_for", boom(RuntimeError("x" * 500 + "\nnext line")))
    (line,) = beat_grid_lines(plan)
    assert line.startswith("beat grid: none (") and line.endswith(")") and len(line) < 120 and "\n" not in line


def test_project_storyboard_prints_the_same_beat_grid_as_reel_storyboard(demo, gl, capsys, monkeypatch):
    from types import SimpleNamespace

    from funkbox.reel import autoreel, cli, render, storyboard as sbmod

    reel = demo / "small.reel.json"
    reel.write_text(json.dumps(small_demo_reel(demo)))
    monkeypatch.setattr(sbmod, "storyboard", lambda plan, out=None, **kw: None)  # the sheet is not under test
    monkeypatch.setattr(autoreel, "auto_reel", lambda *a, **kw: SimpleNamespace(path=reel, text=lambda: "auto: ok"))

    cli.cmd_storyboard(
        SimpleNamespace(reel=str(reel), project=None, format=None, output=str(demo / "sb.png"), tile=120)
    )
    reel_out = capsys.readouterr().out.splitlines()
    args = SimpleNamespace(
        project="demo", length=None, style=None, pace=None, cta=None, music=None, clip_share=None,
        product_every=None, formats=None, bpm=None,
    )  # fmt: skip
    cli.cmd_project_auto(args)
    project_out = capsys.readouterr().out.splitlines()

    expected = sbmod.beat_grid_lines(render.prepare(str(reel)))
    assert expected[0].startswith("beat grid: ") and len(expected) > 2
    assert reel_out[-len(expected) :] == expected
    assert project_out[-len(expected) :] == expected
    assert project_out[-len(expected) - 1].startswith("storyboard: ")


def test_storyboard_reason_drops_the_path_and_keeps_the_ffmpeg_error(demo, gl, monkeypatch):
    from funkbox.reel import audio, render
    from funkbox.reel.storyboard import beat_grid_lines

    plan = render.prepare(small_demo_reel(demo), demo)
    long_path = "C:\\" + "\\".join(["a-very-long-folder-name"] * 6) + "\\song.wav"

    def boom(exc):
        def f(reel):
            raise exc

        return f

    msg = f"cannot decode {long_path}: {long_path}: Invalid data found when processing input"
    monkeypatch.setattr(audio, "beat_grid_for", boom(RuntimeError(msg)))
    assert beat_grid_lines(plan) == ["beat grid: none (Invalid data found when processing input)"]
    msg = f"cannot decode {long_path}: Error opening input file: zzz"
    monkeypatch.setattr(audio, "beat_grid_for", boom(RuntimeError(msg)))
    (line,) = beat_grid_lines(plan)
    # the path goes, the ffmpeg error stays whole (even the harmless "Error opening input file:" part)
    assert line == "beat grid: none (Error opening input file: zzz)" and "a-very-long-folder-name" not in line
    msg = f"cannot decode {long_path}: Error opening input file: " + "z" * 200
    monkeypatch.setattr(audio, "beat_grid_for", boom(RuntimeError(msg)))
    (line,) = beat_grid_lines(plan)
    assert line == "beat grid: none (Error opening input file: " + "z" * 54 + ")"  # 80 characters after the prefix
    assert "a-very-long-folder-name" not in line
    monkeypatch.setattr(audio, "beat_grid_for", boom(RuntimeError("cannot decode x.wav: ")))
    (line,) = beat_grid_lines(plan)  # nothing left after the path: the class name plus the message
    assert line.startswith("beat grid: none (RuntimeError: cannot decode x.wav") and len(line) < 120
    msg = f"cannot decode {long_path}: "  # same fallback with a long path: no folder name may leak either
    monkeypatch.setattr(audio, "beat_grid_for", boom(RuntimeError(msg)))
    (line,) = beat_grid_lines(plan)
    assert line.startswith("beat grid: none (RuntimeError: cannot decode song.wav") and len(line) < 120, line
    assert "a-very-long-folder-name" not in line, line


def test_a_missing_music_file_is_a_readable_validate_error_and_a_storyboard_note(demo, gl, work, capsys):
    import shutil

    from funkbox.reel import render, validate
    from funkbox.reel.schema import ReelError
    from funkbox.reel.storyboard import beat_grid_lines, print_beat_grid, storyboard

    d = small_demo_reel(demo)
    assert d["audio"].get("music") and not d["audio"].get("bpm")
    song = work / "missing-song.wav"
    shutil.copy(demo / "inputs/music/click120.wav", song)
    d["audio"] = {**d["audio"], "music": str(song)}
    plan = render.prepare(d, demo)  # the music is there now: the reel validates and the grid is detected

    song.unlink()  # ...and then it is gone: validate says so, readably (no traceback), and prepare refuses the reel
    rep = validate.validate(d, base_dir=demo)
    assert not rep.ok and any("missing-song.wav" in e for e in rep.errors), rep.errors
    with pytest.raises(ReelError, match="missing-song.wav"):
        render.prepare(d, demo)

    storyboard(plan, work / "sb-missing.png", tile_w=100)  # the sheet is written, then the grid line
    assert (work / "sb-missing.png").exists()
    (line,) = beat_grid_lines(plan)
    assert line.startswith("beat grid: none (") and "missing-song.wav" in line and len(line) < 120
    assert str(work) not in line
    print_beat_grid(plan)  # does not raise
    assert capsys.readouterr().out.strip() == line


def test_cli_storyboard_writes_the_sheet_and_says_why_when_the_music_vanishes(demo, gl, work, capsys, monkeypatch):
    """The CLI path a user runs after validating: the music file is deleted while the sheet is being drawn -
    after prepare has validated it. The command still writes the contact sheet and reports beat grid: none
    (...) on stdout - no traceback. cmd_storyboard looks up storyboard.storyboard when it runs, so the swap
    takes effect."""
    import shutil
    from types import SimpleNamespace

    from funkbox.reel import cli
    from funkbox.reel import storyboard as sbmod

    d = small_demo_reel(demo)
    song = work / "vanishing.wav"
    shutil.copy(demo / "inputs/music/click120.wav", song)
    d["audio"] = {**d["audio"], "music": str(song)}
    reel_json = demo / "vanishing.reel.json"  # the CLI resolves the reel's relative paths from its own folder
    reel_json.write_text(json.dumps(d))

    real = sbmod.storyboard

    def drawing(plan, out=None, **kw):  # the real sheet is drawn, and the song vanishes as it is rendered
        img = real(plan, out, **kw)
        song.unlink()
        assert not song.exists()
        return img

    monkeypatch.setattr(sbmod, "storyboard", drawing)
    out = work / "sb-vanished.png"
    rc = cli.cmd_storyboard(SimpleNamespace(reel=str(reel_json), project=None, format=None, output=str(out), tile=100))
    assert rc == 0 and out.exists(), (rc, out)
    lines = capsys.readouterr().out.splitlines()
    assert any(" tiles in " in ln for ln in lines), lines
    (line,) = [ln for ln in lines if ln.startswith("beat grid")]
    assert line.startswith("beat grid: none (") and line.endswith(")"), line
    assert "vanishing.wav" in line and len(line) < 120 and "\\" not in line, line


def test_cli_validate_still_storyboard_beats_cutout(demo, gl, work):
    env = {**os.environ, "REEL_DEPTH": "stub", "REEL_TTS": "stub", "PYTHONPATH": str(ROOT)}
    reel = demo / "small.reel.json"
    reel.write_text(json.dumps(small_demo_reel(demo)))

    def run(*a):
        return subprocess.run(
            [sys.executable, "-m", "funkbox", "reel", *map(str, a)], capture_output=True, text=True, env=env, cwd=work
        )

    r = run("validate", reel)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr
    r = run("storyboard", reel, "-o", work / "sb.png", "--tile", 120)
    assert r.returncode == 0 and (work / "sb.png").exists(), r.stderr
    assert "beat grid: " in r.stdout and "ms from the nearest beat" in r.stdout, r.stdout  # the demo has music
    r = run("still", reel, "--shot", "s3", "--t", "0.4", "-o", work / "s3.png", "--width", 270)
    assert r.returncode == 0 and Image.open(work / "s3.png").size == (270, 480), r.stderr
    r = run("beats", demo / "inputs/music/click120.wav")
    assert r.returncode == 0 and "120" in r.stdout
    r = run("cutout", demo / "inputs/shots/shot1.png", "-o", work / "cut.png")
    assert r.returncode == 0 and (work / "cut.png").exists()
    bad = json.loads(reel.read_text())
    bad["shots"][0]["caption"]["text"] = "hello"
    (demo / "bad.reel.json").write_text(json.dumps(bad))
    r = run("validate", demo / "bad.reel.json")
    assert r.returncode == 1 and "free text is not allowed" in r.stdout


def test_setup_models_checks_hashes(work, monkeypatch):
    from funkbox.reel import models

    st = models.status()
    assert set(st) == {"depth", "voice", "kokoro", "lama"}
    man = models.models_manifest()
    for m in man.values():
        assert m["license"] and all(len(f["sha256"]) == 64 for f in m["files"])
    assert man["depth"]["license"] == "Apache-2.0"
    # a corrupt download must be rejected and leave nothing behind
    monkeypatch.setenv("FUNK_MODELS", str(work))
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not the model")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        models._download(f"http://127.0.0.1:{srv.server_port}/x", work / "m.onnx", "0" * 64, 0)
    srv.shutdown()
    assert not (work / "m.onnx").exists() and not (work / "m.onnx.part").exists()


EXPECTED_TOOLS = {"reel_copy_lines", "reel_validate", "reel_storyboard", "reel_still", "reel_render", "reel_templates"}
# v3.1 "projects" adds these (the draft -> approve -> lock flow is the ONLY way copy changes; see test_p3/test_p5)
V31_TOOLS = {
    "project_new",
    "project_list",
    "project_ingest",
    "project_set_product",
    "project_brand",
    "project_set_brand",
    "project_propose_copy",
    "project_approve_copy",
    "project_copy_diff",
    "project_auto_reel",
    "project_render",
}


async def _session(home, out, fn):
    env = {
        **os.environ,
        "FUNK_OUT": str(out),
        "FUNK_HOME": str(home),
        "FUNK_INPUTS": str(home / "inputs"),
        "REEL_DEPTH": "stub",
        "REEL_TTS": "stub",
    }
    if sys.platform == "linux" and "DISPLAY" not in env:
        env.setdefault("FUNK_GL_BACKEND", "egl")
    params = StdioServerParameters(command=sys.executable, args=["-m", "funkbox", "serve"], env=env, cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def test_mcp_every_tool_and_no_write_tool(demo, gl, work):
    import shutil

    home = work / "home"
    shutil.copytree(demo, home / "examples_demo")
    shutil.copytree(ROOT / "fonts", home / "fonts")
    shutil.copytree(ROOT / "shaders", home / "shaders")  # funk2 recipes reference shaders/ under FUNK_HOME
    d = small_demo_reel(demo)
    for k in ("copy",):
        d[k] = "examples_demo/" + d[k]
    d["brand"]["logo"] = "examples_demo/" + d["brand"]["logo"]
    d["audio"]["music"] = "examples_demo/" + d["audio"]["music"]
    for sh in d["shots"]:
        s = sh["source"]
        for kk in ("image", "video", "funk2"):
            if kk in s:
                s[kk] = "examples_demo/" + s[kk]
        if "product" in sh:
            sh["product"]["image"] = "examples_demo/" + sh["product"]["image"]
    d["end_card"]["product"] = "examples_demo/" + d["end_card"]["product"]
    out = work / "out"
    out.mkdir()

    async def go(s):
        tools = await s.list_tools()
        names = {t.name for t in tools.tools}
        assert EXPECTED_TOOLS | V31_TOOLS <= names, names
        assert not any(w in n for n in EXPECTED_TOOLS for w in ("write", "save", "set", "edit", "update", "add_copy"))
        assert not any(
            w in n
            for n in EXPECTED_TOOLS | V31_TOOLS
            for w in ("write", "save", "edit", "update", "add_copy", "set_copy")
        )
        init = s.get_server_capabilities() if hasattr(s, "get_server_capabilities") else None
        res = {}
        res["copy"] = await s.call_tool("reel_copy_lines", {"path": "examples_demo/inputs/brand/copy.json"})
        res["tpl"] = await s.call_tool("reel_templates", {})
        res["val"] = await s.call_tool("reel_validate", {"reel": d})
        res["sb"] = await s.call_tool("reel_storyboard", {"reel": d})
        res["still"] = await s.call_tool("reel_still", {"reel": d, "shot_id": "s3", "t": 0.4})
        res["render"] = await s.call_tool("reel_render", {"reel": d, "name": "mcpdraft", "draft": True})
        bad = json.loads(json.dumps(d))
        bad["shots"][0]["caption"]["text"] = "invented"
        res["bad"] = await s.call_tool("reel_validate", {"reel": bad})
        return res

    res = asyncio.run(_session(home, out, go))
    for k, v in res.items():
        assert not v.is_error, (k, v)
    txt = lambda r: "\n".join(c.text for c in r.content if c.type == "text")  # noqa: E731
    assert '"LOOK CLOSER.\\nTIME FOR A CLEAN?"' in txt(res["copy"])
    assert "brand-split" in txt(res["tpl"])
    assert txt(res["val"]).startswith("OK")
    assert any(c.type == "image" for c in res["sb"].content), txt(res["sb"])
    assert any(c.type == "image" for c in res["still"].content), txt(res["still"])
    rt = txt(res["render"])
    assert "mcpdraft.mp4" in rt and "text audit: OK" in rt and "shots" in rt
    assert (out / "mcpdraft.mp4").exists() and (out / "mcpdraft.text_audit.json").exists()
    assert "free text is not allowed" in txt(res["bad"])


def test_server_instructions_forbid_inventing_copy():
    from funkbox import server

    assert "never invent copy" in server.INSTRUCTIONS and "ask the user to add it to copy.json" in server.INSTRUCTIONS


def test_storyboard_tiny_tiles_use_the_ad_layout(gl, tmp_path):
    """A reel that validates must storyboard at any tile size: tiles are rendered at ad size and shrunk, so
    the caption 8 px floor never applies to thumbnails (a 100 px tile failed on Windows before)."""
    from funkbox.reel import render as R, storyboard as SB
    import json, sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "examples" / "reel_demo"))
    import make_assets
    from funkbox import config

    demo = config.funk_out() / "test_sb_tiny" / "demo"  # inside the path fence
    make_assets.make(demo, 270, 480)
    d = json.loads((demo / "demo.reel.json").read_text())
    d["settings"] = {"width": 540, "height": 960, "fps": 10, "safe_area": "reels"}
    (demo / "s.reel.json").write_text(json.dumps(d))
    plan = R.prepare(str(demo / "s.reel.json"))
    sheet = SB.storyboard(plan, tmp_path / "sb.png", tile_w=60)
    assert sheet.width > 60
