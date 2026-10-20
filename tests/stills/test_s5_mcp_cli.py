"""v3.2 S5: the stills MCP server over stdio (every tool), the CLI, and the install wiring."""
import asyncio
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import stills_assets as assets
from stills_fixtures import gl, work  # noqa: F401

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOOLS = {"presets", "render_poster", "preview_poster", "poster_variations", "edit_image", "funk_still", "export_ora"}


def text(res):
    return "\n".join(c.text for c in res.content if c.type == "text")


def kinds(res):
    return [c.type for c in res.content]


def poster(photo):
    return {"version": 1, "name": "mcpposter", "canvas": {"width": 320, "height": 400}, "casual": True, "seed": 4,
            "layers": [{"id": "bg", "type": "shader", "shader": "shaders/fbm_flow.frag", "palette": "vapor"},
                       {"id": "pic", "type": "image", "path": str(photo), "opacity": 0.6, "blend": "overlay"},
                       {"id": "head", "type": "text", "text": "Hello stills", "x": 0.1, "y": 0.1, "size": 0.1}],
            "export": {"formats": ["png", "ora"]}}


def env():
    e = {**os.environ, "STUDIO_FAKE_SD": "1"}
    if sys.platform == "linux" and "DISPLAY" not in e:
        e.setdefault("FUNK_GL_BACKEND", "egl")
    return e


def test_every_tool_over_stdio(gl, work):
    photo = assets.make_kitchen(work / "photo.png", 400, 500)
    out = work / "outdir"
    doc = poster(photo)

    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "stills_mcp.py")], env=env())
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                assert "preview_poster" in (init.instructions or "") and "copy_id" in init.instructions
                assert init.instructions.count(". ") <= 3 and "GIMP" in init.instructions
                assert {t.name for t in (await s.list_tools()).tools} == TOOLS

                async def call(name, args=None):
                    res = await s.call_tool(name, args or {})
                    assert not res.is_error, (name, res.content)
                    return res

                t = text(await call("presets"))
                assert "square 1080x1080" in t and "ora" in t and "remove_background" in t

                r = await call("preview_poster", {"poster": doc, "max_side": 200})
                assert kinds(r) == ["text", "image"] and "not saved" in text(r) and "1 string(s)" in text(r)

                r = await call("render_poster", {"poster": doc, "name": "mcp-r1"})
                t = text(r)
                assert kinds(r) == ["text", "image"] and "320x400" in t and "mcp-r1.png" in t and "mcp-r1.ora" in t
                png = Path([l.split(": ", 1)[1] for l in t.splitlines() if l.startswith("png: ")][0])
                assert Image.open(png).size == (320, 400)
                assert (png.parent / "mcp-r1.text_audit.json").is_file() and (png.parent / "mcp-r1.poster.json").is_file()

                # a sidecar path renders again
                side = png.parent / "mcp-r1.poster.json"
                r = await call("preview_poster", {"poster": str(side), "max_side": 160})
                assert "ERROR" not in text(r)

                r = await call("poster_variations", {"poster": doc, "vary": {"seed": [1, 2, 3]}, "name": "mcp-v"})
                assert kinds(r) == ["text", "image"] and "3 variants" in text(r) and "mcp-v-sheet.png" in text(r)

                r = await call("edit_image", {"path": str(photo), "ops": [{"op": "crop", "aspect": "1:1"},
                                                                          {"op": "adjust", "saturation": 0.2}], "name": "mcp"})
                t = text(r)
                assert kinds(r) == ["text", "image"] and "photo-mcp.png" in t and "compare" in t
                assert photo.is_file()

                r = await call("funk_still", {"recipe_or_shader": "shaders/warp.frag", "t": 0.3, "preset": "square", "name": "mcp-st"})
                assert kinds(r) == ["text", "image"] and "1080x1080" in text(r)
                r = await call("funk_still", {"recipe_or_shader": "vec4 effect(vec2 p){ return vec4(p, 0.5, 1.0); }",
                                              "preset": "square", "name": "mcp-st2"})
                assert "ERROR" not in text(r)

                r = await call("export_ora", {"poster": doc, "name": "mcp-ora"})
                t = text(r)
                assert "valid OpenRaster 320x400" in t and "svg:overlay" in t
                ora_path = Path([l.split(": ", 1)[1] for l in t.splitlines() if l.startswith("ora: ")][0])
                with zipfile.ZipFile(ora_path) as z:
                    assert z.infolist()[0].filename == "mimetype"

                # errors come back as text, never as a crash
                bad = json.loads(json.dumps(doc))
                bad["casual"] = False
                t = text(await call("render_poster", {"poster": bad}))
                assert t.startswith("ERROR") and "casual" in t
                assert text(await call("edit_image", {"path": "/etc/hostname", "ops": [{"op": "flip", "axis": "h"}]})).startswith("ERROR")
                assert text(await call("funk_still", {"recipe_or_shader": "nope.frag"})).startswith("ERROR")
    asyncio.run(scenario())


def cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, str(ROOT / "stills_cli.py"), *map(str, args)], cwd=cwd, env=env(),
                          capture_output=True, text=True, timeout=300)


def test_cli_verbs(gl, work):
    photo = assets.make_kitchen(work / "photo.png", 400, 500)
    pj = work / "p.poster.json"
    pj.write_text(json.dumps(poster(photo)))
    r = cli("presets")
    assert r.returncode == 0 and "square" in r.stdout
    r = cli("preview", pj, "-o", work / "pv.png", "--max-side", 200)
    assert r.returncode == 0 and Image.open(work / "pv.png").width <= 200, r.stderr
    r = cli("render", pj, "--out", work / "o", "--name", "cli1")
    assert r.returncode == 0 and (work / "o" / "cli1.png").is_file() and (work / "o" / "cli1.ora").is_file(), r.stderr
    r = cli("ora-check", work / "o" / "cli1.ora")
    assert r.returncode == 0 and "valid OpenRaster 320x400" in r.stdout
    r = cli("vary", pj, "--vary", '{"seed":[1,2]}', "--out", work / "o", "--name", "cv")
    assert r.returncode == 0 and (work / "o" / "cv-sheet.png").is_file(), r.stderr
    r = cli("edit", photo, "--ops", '[{"op":"flip","axis":"h"}]', "--out", work / "e", "--name", "x")
    assert r.returncode == 0 and (work / "e" / "photo-x.png").is_file(), r.stderr
    r = cli("still", "shaders/warp.frag", "--t", "0.2", "--out", work / "s", "--name", "w")
    assert r.returncode == 0 and (work / "s" / "w.png").is_file(), r.stderr
    bad = work / "bad.poster.json"
    bad.write_text(json.dumps({"version": 1, "layers": []}))
    r = cli("render", bad, "--out", work / "o")
    assert r.returncode == 1 and r.stderr.startswith("error:") and "Traceback" not in r.stderr


def test_install_wires_the_stills_server(capsys):
    sys.path.insert(0, str(ROOT))
    import install
    assert "stills" in install.SERVER_KEYS and install.SCRIPTS["stills"] == "stills_mcp.py"
    e = install.server_entries(ROOT, ())["stills"]
    assert e["args"][0].endswith("stills_mcp.py") and "FUNK_HOME" in e["env"]
    assert install.main(["--dry-run", "--register", "desktop", "--config", str(ROOT / "out" / "dry-cfg.json"),
                         "--no-selftest"]) == 0
    assert "stills" in capsys.readouterr().out
    plugin = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["stills"]
    assert plugin["args"][-1].endswith("/stills_mcp.py") and plugin["env"]["FUNK_HOME"] == "${CLAUDE_PLUGIN_ROOT}"
    assert not (ROOT / "out" / "dry-cfg.json").exists()


def test_demo_poster_renders_with_exact_copy(gl, work):
    import subprocess as sp
    from stills import render
    ex = ROOT / "examples" / "stills"
    sp.run([sys.executable, str(ex / "make_assets.py"), "--dest", str(work / "inputs")], check=True, capture_output=True)
    doc = json.loads((ex / "glowmop_poster.poster.json").read_text())
    for lay in doc["layers"]:                                     # point the poster at the freshly generated assets
        for k in ("path", "image"):
            if str(lay.get(k, "")).startswith("inputs/"):
                lay[k] = str(work / lay[k])
    doc["copy"] = str(ex / "glowmop.copy.json")
    r = render.render_document(doc, ex)
    assert (r.width, r.height) == (1080, 1350) and not r.warnings
    deck = json.loads((ex / "glowmop.copy.json").read_text())["lines"]
    assert [a["text"] for a in r.audit] == [deck[a["copy_id"]]["text"] for a in r.audit] and len(r.audit) == 5
    assert all(a["text_exact"] for a in r.audit)
    src = json.loads((ex / "glowmop_poster.poster.json").read_text())
    texts = [l for l in src["layers"] if l["type"] == "text"]
    assert texts and all("copy_id" in l and "text" not in l for l in texts) and "casual" not in src      # copy ids only
