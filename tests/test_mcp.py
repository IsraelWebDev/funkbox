import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

GOOD = """uniform float u_freq; // @param 2.0 9.0 4.0
vec4 effect(vec2 p) {
  float f = sin(u_freq * p.x + iPhase) * sin(u_freq * p.y - 2.0 * iPhase);
  return vec4(palette(f * 0.5 + 0.5), 1.0);
}
"""
BAD = "// one\nvec4 effect(vec2 p) {\n  return vec4(nope, 0.0, 0.0, 1.0);\n}\n"
ITIME = "vec4 effect(vec2 p) { return vec4(vec3(0.5 + 0.5 * sin(6.0 * p.x + iTime * 1.3)), 1.0); }\n"
RECIPE = {"settings": {"width": 64, "height": 64, "fps": 10, "seconds": 0.5, "seed": 3},
          "palette": {"name": "vapor", "cycles": 1},
          "layers": [{"type": "shader", "shader": "shaders/warp.frag"},
                     {"type": "v1", "effect": "blobs", "blend": "add", "opacity": 0.4}],
          "post": [{"name": "chroma", "px": 2}]}


def kinds(res):
    return [c.type for c in res.content]


def text(res):
    return "\n".join(c.text for c in res.content if c.type == "text")


async def _session(home, out, fn):
    env = {**os.environ, "FUNK_OUT": str(out), "FUNK_HOME": str(home), "FUNK_INPUTS": str(home / "inputs")}
    if sys.platform == "linux" and "DISPLAY" not in env:
        env.setdefault("FUNK_GL_BACKEND", "egl")
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "funk2_mcp.py")], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def test_every_tool(gl, tmp_path):
    home, out = tmp_path / "home", tmp_path / "out"
    shutil.copytree(ROOT / "shaders", home / "shaders")

    async def scenario(s):
        tools = {t.name for t in (await s.list_tools()).tools}
        assert tools == {"gl_info", "list_looks", "write_shader", "get_shader", "preview",
                         "contact_sheet", "render", "render_v1"}
        res = {}

        async def call(name, args=None):
            r = await s.call_tool(name, args or {})
            assert not r.is_error, (name, r.content)
            res[name] = r
            return r

        r = await call("gl_info")
        assert "GL_RENDERER" in text(r) and "plasma" in text(r)
        r = await call("list_looks")
        t = text(r)
        assert "voronoi_pop" in t and "u_scale" in t and "chroma" in t and "softlight" in t and "lava-lamp" in t

        r = await call("write_shader", {"name": "wavy", "source": GOOD})
        assert kinds(r) == ["text", "image"] and "compiled OK" in text(r) and "saved" in text(r)
        assert (home / "shaders" / "wavy.frag").read_text() == GOOD
        r = await call("get_shader", {"name": "wavy"})
        assert text(r) == GOOD

        r = await call("write_shader", {"name": "broken", "source": BAD})
        assert kinds(r) == ["text"] and "line 3" in text(r) and "Not saved" in text(r)
        assert not (home / "shaders" / "broken.frag").exists()
        r = await call("write_shader", {"name": "tick", "source": ITIME})
        assert "FAIL" in text(r) and "iTime" in text(r) and "NOT saved" in text(r)
        assert not (home / "shaders" / "tick.frag").exists()
        r = await call("write_shader", {"name": "../evil", "source": GOOD})
        assert text(r).startswith("ERROR") and not (tmp_path / "evil.frag").exists()

        r = await call("preview", {"recipe": RECIPE, "size": 96})
        assert kinds(r) == ["text", "image"] and "layer 0: shader shaders/warp.frag" in text(r)
        r = await call("preview", {"recipe": {"layers": [{"type": "shader", "shader": "shaders/plasma.frag"}]}})
        assert kinds(r) == ["text", "image"] and "540x540 30fps 4s seed=1" in text(r)   # defaults
        r = await call("contact_sheet", {"recipe": RECIPE, "seeds": "1-4"})
        assert kinds(r) == ["text", "image"] and "saved" in text(r)
        assert list(out.glob("sheet-*.png"))
        r = await call("render", {"recipe": RECIPE, "name": "mcp-test"})
        assert kinds(r) == ["text", "image"] and "5 frames 64x64" in text(r)
        assert (out / "mcp-test.mp4").exists() and (out / "mcp-test.json").exists()
        r = await call("render_v1", {"preset": "lava-lamp", "size": 64, "seconds": 0.5, "gif": False})
        assert kinds(r) == ["text", "image"] and (out / "v1-lava-lamp-s1.mp4").exists()

        # limits and errors come back as readable strings, not exceptions / is_error
        big = {**RECIPE, "settings": {**RECIPE["settings"], "width": 4000}}
        r = await call("render", {"recipe": big})
        assert text(r).startswith("ERROR") and "1080" in text(r)
        r = await call("render", {"recipe": {**RECIPE, "settings": {**RECIPE["settings"], "seconds": 99}}})
        assert text(r).startswith("ERROR")
        r = await call("render", {"recipe": {**RECIPE, "settings": {**RECIPE["settings"], "fps": 120}}})
        assert text(r).startswith("ERROR")
        r = await call("preview", {"recipe": {"layers": [{"type": "shader", "source": BAD}]}})
        assert text(r).startswith("ERROR") and "line 3" in text(r) and "Traceback" not in text(r)
        r = await call("preview", {"recipe": {"layers": [{"type": "image", "path": "/etc/hostname"}]}})
        assert text(r).startswith("ERROR")
        r = await call("render", {"recipe": {"layers": [{"type": "shader", "source": ITIME}],
                                              "settings": RECIPE["settings"]}})
        assert "NOT RENDERED" in text(r)

    asyncio.run(_session(home, out, scenario))
