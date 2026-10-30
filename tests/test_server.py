"""The one MCP server over stdio: the exact tool set, one call per area, the copy lock, startup time."""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

LOOP = {"loop_list", "loop_preview", "loop_sheet", "loop_render", "loop_write_shader", "loop_get_shader",
        "loop_gl_info", "loop_still"}
REEL = {"reel_validate", "reel_storyboard", "reel_still", "reel_render", "reel_templates", "reel_copy_lines"}
PROJECT = {"project_new", "project_list", "project_ingest", "project_set_product", "project_brand",
           "project_set_brand", "project_propose_copy", "project_approve_copy", "project_copy_diff",
           "project_auto_reel", "project_render"}
POSTER = {"poster_presets", "poster_preview", "poster_render", "poster_variations", "poster_export_ora", "poster_compare", "poster_sizes", "poster_animate"}
IMAGE = {"image_generate", "image_restyle", "image_upscale", "image_edit", "image_trace"}
APPS = {"apps_status", "apps_start", "apps_stop"}
MUSIC = {"music_render"}
PLANNED = LOOP | REEL | PROJECT | POSTER | IMAGE | APPS | MUSIC

#: every tool of the five old servers -> its one new tool (also the table in docs/migration-v4.md)
OLD_TOOLS = {
    "funk2: gl_info": "loop_gl_info", "funk2: list_looks": "loop_list", "funk2: write_shader": "loop_write_shader",
    "funk2: get_shader": "loop_get_shader", "funk2: preview": "loop_preview", "funk2: contact_sheet": "loop_sheet",
    "funk2: render": "loop_render", "funk: render_v1": None,
    "reel: copy_lines": "reel_copy_lines", "reel: validate": "reel_validate", "reel: storyboard": "reel_storyboard",
    "reel: still": "reel_still", "reel: render": "reel_render", "reel: list_templates": "reel_templates",
    "reel: new_project": "project_new", "reel: list_projects": "project_list", "reel: ingest": "project_ingest",
    "reel: set_product": "project_set_product", "reel: brand_from_assets": "project_brand",
    "reel: set_brand": "project_set_brand", "reel: propose_copy": "project_propose_copy",
    "reel: approve_copy": "project_approve_copy", "reel: copy_diff": "project_copy_diff",
    "reel: auto_reel": "project_auto_reel", "reel: project_render": "project_render",
    "stills: presets": "poster_presets", "stills: preview_poster": "poster_preview",
    "stills: render_poster": "poster_render", "stills: poster_variations": "poster_variations",
    "stills: edit_image": "image_edit", "stills: funk_still": "loop_still", "stills: export_ora": "poster_export_ora",
    "studio: generate_image": "image_generate", "studio: restyle_image": "image_restyle",
    "studio: upscale_image": "image_upscale", "studio: status": "apps_status", "studio: start": "apps_start",
    "studio: stop": "apps_stop",
}


def server_env(home, **extra):
    env = {**os.environ, "FUNK_HOME": str(home), "FUNK_OUT": str(home / "out"), "FUNK_INPUTS": str(home / "inputs"),
           "FUNK_PROJECTS": str(home / "projects"), "FUNK_INBOX": str(home / "inbox"), "STUDIO_FAKE_SD": "1",
           "REEL_DEPTH": "stub", "REEL_TTS": "stub", **extra}
    if sys.platform == "linux" and "DISPLAY" not in env:
        env.setdefault("FUNK_GL_BACKEND", "egl")
    return env


async def session(home, fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "funkbox", "serve"], env=server_env(home),
                                   cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            return await fn(s, init)


def text(res):
    return "\n".join(c.text for c in res.content if c.type == "text")


def test_tool_list_equals_the_plan_and_every_old_tool_is_mapped(gl, tmp_path):
    async def go(s, init):
        return {t.name: t for t in (await s.list_tools()).tools}
    tools = asyncio.run(session(tmp_path / "h", go))
    assert set(tools) == PLANNED and len(tools) == 42
    assert all(re.match(r"^(loop|reel|project|poster|image|apps|music)_", n) for n in tools)
    assert set(v for v in OLD_TOOLS.values() if v) <= PLANNED
    assert len(set(v for v in OLD_TOOLS.values() if v)) == len([v for v in OLD_TOOLS.values() if v])   # one-to-one
    doc = (ROOT / "docs" / "migration-v4.md").read_text()
    for old, new in OLD_TOOLS.items():
        assert old.split(": ")[1] in doc, old
        if new:
            assert new in doc, new


def test_instructions_are_short_and_say_the_important_things(tmp_path):
    from funkbox import server
    assert len(re.findall(r"\.(?:\s|$)", server.INSTRUCTIONS.replace("e.g.", ""))) <= 8, server.INSTRUCTIONS
    for needle in ("loop_*", "reel_*", "poster_*", "image_*", "apps_*", "Preview before", "LOOP CONTRACT", "iPhase",
                   "never invent copy", "project_approve_copy", "composited, never generated", "512x512",
                   "GIMP is opened by the user", "ERROR:"):
        assert needle in server.INSTRUCTIONS, needle


def test_copy_lock_on_the_tool_list(tmp_path):
    """No tool but project_approve_copy can write copy.json: the tool names say it, and the source agrees."""
    from funkbox import server
    srv = server.build_server()
    names = [t.name for t in asyncio.run(srv.list_tools())]
    copyish = {n for n in names if "copy" in n}
    assert copyish == {"reel_copy_lines", "project_propose_copy", "project_approve_copy", "project_copy_diff"}
    assert not any(w in n for n in names for w in ("add_copy", "set_copy", "edit_copy", "write_copy", "save_copy", "update_copy"))
    # the reel/project tools hold no file-writing code, and approval.approve is called exactly once: by the approve tool
    src = (ROOT / "funkbox" / "reel" / "tools.py").read_text()
    assert not re.search(r"\.write_text\(|\.write_bytes\(|(?<![.\w])open\(|shutil\.|os\.replace|os\.rename", src)
    assert len(re.findall(r"approval\.approve\(", src)) == 1
    for other in ("loops", "stills", "studio"):
        assert "approval.approve" not in (ROOT / "funkbox" / other / "tools.py").read_text()


def test_one_call_per_area_over_stdio(gl, tmp_path):
    home = tmp_path / "home"
    (home / "inputs").mkdir(parents=True)
    import shutil
    shutil.copytree(ROOT / "shaders", home / "shaders")
    shutil.copytree(ROOT / "fonts", home / "fonts")
    recipe = {"settings": {"width": 64, "height": 64, "fps": 10, "seconds": 0.5, "seed": 3},
              "layers": [{"type": "shader", "shader": "shaders/plasma.frag"}]}
    poster = {"version": 1, "name": "one", "canvas": {"width": 160, "height": 200}, "casual": True, "seed": 4,
              "layers": [{"id": "bg", "type": "shader", "shader": "shaders/fbm_flow.frag", "palette": "vapor"},
                         {"id": "head", "type": "text", "text": "Hello", "x": 0.1, "y": 0.1, "size": 0.1}]}
    photo = home / "inputs" / "photo.png"
    Image.new("RGB", (80, 100), (200, 120, 40)).save(photo)

    async def go(s, init):
        async def call(name, args=None):
            r = await s.call_tool(name, args or {})
            assert not r.is_error, (name, r.content)
            return r
        out = {}
        out["loop"] = await call("loop_render", {"recipe": recipe, "name": "one"})
        out["reel"] = await call("reel_templates")
        out["project"] = await call("project_new", {"name": "One"})
        out["poster"] = await call("poster_preview", {"poster": poster, "max_side": 120})
        out["edit"] = await call("image_edit", {"path": str(photo), "ops": [{"op": "flip", "axis": "h"}], "name": "f"})
        out["image"] = await call("image_generate", {"prompt": "a kettle", "name": "kettle", "seed": 2})
        out["apps"] = await call("apps_status")
        return out, init.instructions
    out, instr = asyncio.run(session(home, go))
    assert "5 frames 64x64" in text(out["loop"]) and (home / "out" / "one.mp4").exists()
    assert "caption styles" in text(out["reel"]) and "project 'one'" in text(out["project"])
    assert any(c.type == "image" for c in out["poster"].content)
    assert any((home / "out" / "edits").glob("*.png"))
    assert (home / "inputs" / "kettle.png").is_file() and "saved" in text(out["image"])
    assert "stub" in text(out["apps"])
    assert instr == __import__("funkbox.server", fromlist=["x"]).INSTRUCTIONS


def test_server_starts_fast_without_extras(tmp_path):
    """The initialize handshake answers in < 3 s on a clean process (lazy imports: no onnxruntime, no piper)."""
    async def go(s, init):
        return init
    t0 = time.perf_counter()
    init = asyncio.run(session(tmp_path / "h", go))
    dt = time.perf_counter() - t0
    info = getattr(init, "server_info", None) or init.serverInfo     # mcp 2.x / 1.x
    assert info.name == "funkbox" and dt < 3.0, dt
    print(f"handshake in {dt:.2f}s")
    code = ("import sys; sys.path.insert(0, %r); from funkbox import server; server.build_server(); "
            "print(sorted(m for m in ('onnxruntime', 'piper', 'scipy') if m in sys.modules))" % str(ROOT))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=server_env(tmp_path / "h"))
    assert r.returncode == 0 and r.stdout.strip() == "[]", r.stderr + r.stdout


def test_reel_tools_accept_a_path_like_poster_tools(gl, tmp_path):
    """reel_* tools take a .reel.json path as well as an inline object (found on the laptop: a path string
    was rejected by the schema while poster_* accepted it)."""
    home = ROOT  # the demo reel and its assets live in the repo
    async def go(s, init):
        r = await s.call_tool("reel_validate", {"reel": "examples/mimic/mimic.reel.json"})
        return r
    res = asyncio.run(session(home, go))
    assert not res.is_error, res.content
    assert "OK" in text(res)
