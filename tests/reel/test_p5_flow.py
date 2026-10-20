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

import synth
from funkbox.reel import project as P, shortcuts

ROOT = Path(__file__).resolve().parents[2]
mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters, types  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

V3_TOOLS = {"copy_lines", "validate", "storyboard", "still", "render", "list_templates"}
V31_TOOLS = {"new_project", "list_projects", "ingest", "set_product", "brand_from_assets", "set_brand",
             "propose_copy", "approve_copy", "copy_diff", "auto_reel", "project_render"}


@pytest.fixture
def penv(work, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(work / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(work / "inbox"))
    return work


def txt(res):
    return "\n".join(c.text for c in res.content if c.type == "text")


def n_images(res):
    return sum(1 for c in res.content if c.type == "image")


async def session(penv, fn, elicit=None):
    env = {**os.environ, "FUNK_OUT": str(penv / "out"), "FUNK_PROJECTS": str(penv / "projects"),
           "FUNK_INBOX": str(penv / "inbox"), "REEL_DEPTH": "stub", "REEL_TTS": "stub"}
    env.setdefault("FUNK_GL_BACKEND", "egl")
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "reel_mcp.py")], env=env)
    async with stdio_client(params) as (r, w):
        kw = {"elicitation_callback": elicit} if elicit else {}
        async with ClientSession(r, w, **kw) as s:
            await s.initialize()
            return await fn(s)


def test_tool_surface_and_the_only_copy_writer():
    src = (ROOT / "reel_mcp.py").read_text()
    # the server has no file-writing code of its own at all, and copy.json is reachable only through approve_copy
    assert not re.search(r"\.write_text\(|\.write_bytes\(|(?<![.\w])open\(|shutil\.|os\.replace|os\.rename", src)
    assert len(re.findall(r"approval\.approve\(", src)) == 1
    body = src[src.index("async def approve_copy"):]
    assert "approval.approve(" in body.split("@server.tool", 1)[0]
    ins = src[src.index("INSTRUCTIONS = ("):src.index("server = _Server")]
    assert ins.count(". ") >= 4 and "never invent copy" in ins and "approve_copy" in ins


def test_stdio_tool_list_is_exact(penv):
    async def go(s):
        return {t.name for t in (await s.list_tools()).tools}
    names = asyncio.run(session(penv, go))
    assert names == V3_TOOLS | V31_TOOLS, names ^ (V3_TOOLS | V31_TOOLS)
    assert not any(w in n for n in names for w in ("write", "save", "edit", "update", "add_copy", "set_copy"))


def test_whole_flow_over_stdio_to_a_draft_render_and_the_text_audit_passes(penv, gl):
    """new_project -> ingest -> brand_from_assets -> propose_copy -> approve_copy (a click) -> auto_reel -> draft render."""
    inbox = penv / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    synth.mixed_folder(inbox, with_product_jpg=False, n_scenes=3)
    lines = {"hook": {"text": "LOOK CLOSER.\nTIME FOR A CLEAN?", "say": "Look closer. Time for a clean?"},
             "body1": {"text": "TOUGH ON STAINS."},
             "end_head": {"text": "Deep\nCleaning\nPower"}, "end_sub": {"text": "PROFESSIONAL RESULTS"},
             "url": {"text": "GLOWMOP.EXAMPLE"}}
    shown = []

    async def click_approve(context, params):
        shown.append(params.message)
        return types.ElicitResult(action="accept", content={"decision": "approve"})

    async def go(s):
        r = {}
        r["new"] = txt(await s.call_tool("new_project", {"name": "Glowmop"}))
        r["list0"] = txt(await s.call_tool("list_projects", {}))
        ing = await s.call_tool("ingest", {"project": "glowmop"})
        r["ingest"], r["ingest_img"] = txt(ing), n_images(ing)
        b = await s.call_tool("brand_from_assets", {"project": "glowmop"})
        r["brand"], r["brand_img"] = txt(b), n_images(b)
        p = await s.call_tool("propose_copy", {"project": "glowmop", "lines": lines,
                                               "pronounce": {"GLOWMOP": "glow mop"}})
        r["propose"] = txt(p)
        # nothing is renderable before approval
        r["auto_early"] = txt(await s.call_tool("auto_reel", {"project": "glowmop"}))
        sha = r["propose"].split("draft_sha256: ")[1].split()[0]
        r["approve"] = txt(await s.call_tool("approve_copy", {"project": "glowmop", "draft_sha256": sha}))
        r["copy_lines"] = txt(await s.call_tool("copy_lines", {"project": "glowmop"}))
        a = await s.call_tool("auto_reel", {"project": "glowmop", "length_s": 10})
        r["auto"], r["auto_img"] = txt(a), n_images(a)
        r["validate"] = txt(await s.call_tool("validate", {"project": "glowmop"}))
        sb = await s.call_tool("storyboard", {"project": "glowmop"})
        r["sb_img"] = n_images(sb)
        st = await s.call_tool("still", {"project": "glowmop", "shot_id": "s1", "t": 0.4})
        r["still_img"] = n_images(st)
        t0 = time.perf_counter()
        rr = await s.call_tool("project_render", {"project": "glowmop", "draft": True})
        r["render"], r["render_s"] = txt(rr), time.perf_counter() - t0
        r["list1"] = txt(await s.call_tool("list_projects", {}))
        return r

    r = asyncio.run(session(penv, go, click_approve))
    assert "project 'glowmop' created" in r["new"] and str(penv / "inbox") in r["new"]
    assert "no projects yet" not in r["list1"] and "glowmop: rendered" in r["list1"]
    assert "product: product/product.png" in r["ingest"] and "music 2" in r["ingest"] and r["ingest_img"] == 1
    assert "primary #" in r["brand"] and r["brand_img"] == 1
    assert r["auto_early"].startswith("ERROR") and "needs approved copy" in r["auto_early"]
    assert r["approve"].startswith("APPROVED (elicitation)") and len(shown) == 1 and "3. end_head" in shown[0]
    assert '"Deep\\nCleaning\\nPower"' in shown[0]
    assert "LOCKED and intact" in r["copy_lines"] and '"TOUGH ON STAINS."' in r["copy_lines"]
    assert r["auto_img"] == 1 and "validate: OK" in r["auto"] and '"copy_id": "hook"' in r["auto"]
    assert r["validate"].startswith("OK") and r["sb_img"] == 1 and r["still_img"] == 1
    assert "text audit: OK" in r["render"] and "glowmop-draft.mp4" in r["render"]
    d = P.project_dir("glowmop")
    assert (d / "out" / "glowmop-draft.mp4").exists() and (d / "out" / "glowmop-draft.png").exists()
    # the independent text check: every drawn string is byte-identical to the approved copy.json entry
    deck = json.loads((d / "copy.json").read_text(encoding="utf-8"))["lines"]
    audit = json.loads((d / "out" / "glowmop-draft.text_audit.json").read_text(encoding="utf-8"))
    assert audit["ok"] and audit["entries"] and not audit["missing"] and not audit["inexact"]
    for e in audit["entries"]:
        assert e["text"].encode("utf-8") == deck[e["copy_id"]]["text"].encode("utf-8"), e
    ids = {e["copy_id"] for e in audit["entries"]}
    assert {"hook", "end_head", "url"} <= ids
    print(f"draft render over MCP: {r['render_s']:.0f}s")


def test_project_arg_conflicts_and_missing_reel(penv, gl):
    synth.make_project()

    async def go(s):
        a = txt(await s.call_tool("validate", {"project": "demo"}))
        b = txt(await s.call_tool("validate", {"project": "demo", "reel": {"version": 3}}))
        c = txt(await s.call_tool("validate", {}))
        d = txt(await s.call_tool("copy_lines", {"project": "demo"}))
        e = txt(await s.call_tool("project_render", {"project": "nope"}))
        return a, b, c, d, e
    a, b, c, d, e = asyncio.run(session(penv, go))
    assert "no reel yet" in a and "either reel or project" in b and "project=<name>" in c
    assert "LOCKED and intact" in d and "ERROR" in e


# ------------------------------------------------------------------ CLI parity
def cli(penv, *args, stdin=""):
    env = {**os.environ, "FUNK_PROJECTS": str(penv / "projects"), "FUNK_INBOX": str(penv / "inbox"),
           "REEL_DEPTH": "stub", "REEL_TTS": "stub"}
    env.setdefault("FUNK_GL_BACKEND", "egl")
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-m", "funkbox", *map(str, args)], input=stdin, capture_output=True,
                          text=True, env=env, cwd=penv)


def test_cli_has_the_same_verbs(penv, gl):
    ext = synth.mixed_folder(penv / "dropbox", with_product_jpg=False, n_scenes=3)
    r = cli(penv, "project", "new", "Glow Mop")
    assert r.returncode == 0 and "glow-mop" in r.stdout, r.stderr
    r = cli(penv, "project", "ingest", "glow-mop", "--source", ext)
    assert r.returncode == 0 and "product: product/product.png" in r.stdout, r.stderr
    assert (ext / "track.mp3").exists()                                    # external source untouched
    r = cli(penv, "project", "brand", "glow-mop")
    assert r.returncode == 0 and "primary #" in r.stdout, r.stderr
    r = cli(penv, "project", "set-brand", "glow-mop", "--primary", "#b00020")
    assert r.returncode == 0 and "#b00020" in r.stdout, r.stderr
    f = penv / "lines.json"
    f.write_text(json.dumps({"hook": {"text": "HELLO THERE."}, "b1": {"text": "ONE MORE LINE."},
                             "end_head": {"text": "Big\nSale"}, "end_sub": {"text": "TODAY ONLY"},
                             "url": {"text": "GLOWMOP.EXAMPLE"}}))
    r = cli(penv, "project", "propose-copy", "glow-mop", "--file", f)
    assert r.returncode == 0 and "VERBATIM" in r.stdout and "project approve-copy glow-mop --from-draft" in r.stdout, r.stderr
    r = cli(penv, "project", "auto", "glow-mop")
    assert r.returncode == 1 and "needs approved copy" in r.stderr
    r = cli(penv, "project", "approve-copy", "glow-mop", "--from-draft", "--stdin", stdin="y\n")
    assert r.returncode == 0, r.stderr
    r = cli(penv, "project", "auto", "glow-mop", "--length", "10")
    assert r.returncode == 0 and "validate: OK" in r.stdout, r.stdout + r.stderr
    r = cli(penv, "reel", "validate", "--project", "glow-mop")
    assert r.returncode == 0 and r.stdout.startswith("OK")
    r = cli(penv, "reel", "storyboard", "--project", "glow-mop", "-o", penv / "sb.png", "--tile", 120)
    assert r.returncode == 0 and (penv / "sb.png").exists(), r.stderr
    r = cli(penv, "project", "copy-diff", "glow-mop")
    assert r.returncode == 0 and "= hook: unchanged" in r.stdout
    r = cli(penv, "project", "list")
    assert "glow-mop: reel-built" in r.stdout


def test_make_shortcuts_windows_script_and_posix_commands(penv, monkeypatch):
    from pathlib import PureWindowsPath
    monkeypatch.setattr(shortcuts, "targets", lambda: [("funk inbox", PureWindowsPath(r"C:\dev\funkbox\inbox")),
                                                       ("funk projects", PureWindowsPath(r"C:\dev\funkbox\projects"))])
    out = "\n".join(shortcuts.make_shortcuts(dry_run=True, platform="win32"))
    assert "WScript.Shell" in out and "'funk inbox.lnk'" in out and "'funk projects.lnk'" in out
    assert r"$l.TargetPath = 'C:\dev\funkbox\inbox'" in out and "GetFolderPath('Desktop')" in out
    assert "'" not in shortcuts._ps_quote("a").strip("'")
    assert shortcuts._ps_quote("it's") == "'it''s'"
    posix = "\n".join(shortcuts.make_shortcuts(dry_run=True, platform="linux"))
    assert 'ln -sfn' in posix and "funk inbox" in posix and "funk projects" in posix and "Not Windows" in posix
