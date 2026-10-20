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
    probe = subprocess.run([__import__("funkbox.media", fromlist=["x"]).ffmpeg_exe(), "-hide_banner", "-i", out.paths["mp4"]],
                           capture_output=True, text=True).stderr
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
        y = 10 + (i // cols) * (tile_h + sb.height // 40 * 0 + (sb.height - 10 - rows * 10 - rows * tile_h) // rows + 10)
        tile = sb.crop((x, y, x + 135, y + tile_h)).convert("L")
        assert tile.getextrema()[1] - tile.getextrema()[0] > 20


def test_cli_validate_still_storyboard_beats_cutout(demo, gl, work):
    env = {**os.environ, "REEL_DEPTH": "stub", "REEL_TTS": "stub"}
    reel = demo / "small.reel.json"
    reel.write_text(json.dumps(small_demo_reel(demo)))

    def run(*a):
        return subprocess.run([sys.executable, str(ROOT / "reel_cli.py"), *map(str, a)], capture_output=True,
                              text=True, env=env, cwd=work)
    r = run("validate", reel)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr
    r = run("storyboard", reel, "-o", work / "sb.png", "--tile", 120)
    assert r.returncode == 0 and (work / "sb.png").exists(), r.stderr
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
    assert set(st) == {"depth", "voice"}
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
            self.send_response(200); self.end_headers(); self.wfile.write(b"not the model")
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        models._download(f"http://127.0.0.1:{srv.server_port}/x", work / "m.onnx", "0" * 64, 0)
    srv.shutdown()
    assert not (work / "m.onnx").exists() and not (work / "m.onnx.part").exists()


EXPECTED_TOOLS = {"copy_lines", "validate", "storyboard", "still", "render", "list_templates"}
# v3.1 "projects" adds these (the draft -> approve -> lock flow is the ONLY way copy changes; see test_p3/test_p5)
V31_TOOLS = {"new_project", "list_projects", "ingest", "set_product", "brand_from_assets", "set_brand",
             "propose_copy", "approve_copy", "copy_diff", "auto_reel", "project_render"}


async def _session(home, out, fn):
    env = {**os.environ, "FUNK_OUT": str(out), "FUNK_HOME": str(home), "FUNK_INPUTS": str(home / "inputs"),
           "REEL_DEPTH": "stub", "REEL_TTS": "stub"}
    if sys.platform == "linux" and "DISPLAY" not in env:
        env.setdefault("FUNK_GL_BACKEND", "egl")
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "reel_mcp.py")], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def test_mcp_every_tool_and_no_write_tool(demo, gl, work):
    import shutil
    home = work / "home"
    shutil.copytree(demo, home / "examples_demo")
    shutil.copytree(ROOT / "fonts", home / "fonts")
    shutil.copytree(ROOT / "shaders", home / "shaders")           # funk2 recipes reference shaders/ under FUNK_HOME
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
        assert names - V31_TOOLS == EXPECTED_TOOLS, names
        assert not any(w in n for n in EXPECTED_TOOLS for w in ("write", "save", "set", "edit", "update", "add_copy"))
        assert not any(w in n for n in names for w in ("write", "save", "edit", "update", "add_copy", "set_copy"))
        init = s.get_server_capabilities() if hasattr(s, "get_server_capabilities") else None
        res = {}
        res["copy"] = await s.call_tool("copy_lines", {"path": "examples_demo/inputs/brand/copy.json"})
        res["tpl"] = await s.call_tool("list_templates", {})
        res["val"] = await s.call_tool("validate", {"reel": d})
        res["sb"] = await s.call_tool("storyboard", {"reel": d})
        res["still"] = await s.call_tool("still", {"reel": d, "shot_id": "s3", "t": 0.4})
        res["render"] = await s.call_tool("render", {"reel": d, "name": "mcpdraft", "draft": True})
        bad = json.loads(json.dumps(d))
        bad["shots"][0]["caption"]["text"] = "invented"
        res["bad"] = await s.call_tool("validate", {"reel": bad})
        return res

    res = asyncio.run(_session(home, out, go))
    for k, v in res.items():
        assert not v.is_error, (k, v)
    txt = lambda r: "\n".join(c.text for c in r.content if c.type == "text")          # noqa: E731
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
    src = (ROOT / "reel_mcp.py").read_text()
    assert "never invent copy" in src and "ask the user to add it to copy.json" in src
