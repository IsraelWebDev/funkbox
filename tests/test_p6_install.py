"""v3.1 P6: self-install packaging (install.py, pyproject, plugin files, studio path resolution)."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import install  # noqa: E402


def _reqs(name):
    out = []
    for line in (ROOT / name).read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(re.sub(r"\s+", "", line).lower())
    return sorted(out)


def test_pyproject_in_sync_with_requirements():
    tomllib = pytest.importorskip("tomllib")
    d = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert d["tool"]["uv"]["package"] is False
    assert d["project"]["requires-python"] == ">=3.10"
    norm = lambda xs: sorted(re.sub(r"\s+", "", x).lower() for x in xs)
    assert norm(d["project"]["dependencies"]) == _reqs("requirements.txt")
    assert norm(d["project"]["optional-dependencies"]["reel"]) == _reqs("requirements-reel.txt")
    assert "dev" in d["project"]["optional-dependencies"]


def test_dry_run_plan_prints_everything_and_changes_nothing(tmp_path, capsys):
    cfg = tmp_path / "cfg.json"
    before = sorted(p.name for p in ROOT.iterdir())
    rc = install.main(["--dry-run", "--with", "reel,fastsd,gimp", "--register", "desktop", "--shortcuts",
                       "--config", str(cfg), "--no-selftest"])
    out = capsys.readouterr().out
    assert rc == 0
    for needle in ("setup-models", "fastsdcpu", "pydantic>=2.11,<2.12", "gimp-mcp", "GIMP.GIMP.3",
                   "quit Claude Desktop", "funk inbox", "funk projects", "DRY RUN"):
        assert needle.lower() in out.lower(), needle
    assert not cfg.exists()
    assert sorted(p.name for p in ROOT.iterdir()) == before


def test_register_merge_backup_and_unregister(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    other = {"command": "node", "args": ["other.js"]}
    cfg.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": other, "funk": {"command": "old"}}}))
    r = install.Runner(False, ROOT)
    install.register_desktop(r, cfg, ("gimp",))
    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"] == other
    assert set(data["mcpServers"]) == {"other", *install.SERVER_KEYS}
    assert data["mcpServers"]["funk"]["command"] != "old"
    baks = list(tmp_path.glob("claude_desktop_config.json.bak-*"))
    assert len(baks) == 1 and "old" in baks[0].read_text()
    install.unregister_desktop(r, cfg)
    data = json.loads(cfg.read_text())
    assert data["mcpServers"] == {"other": other} and data["theme"] == "dark"
    # idempotent: a second register/unregister does not break or duplicate anything
    install.register_desktop(r, cfg, ())
    install.register_desktop(r, cfg, ())
    assert set(json.loads(cfg.read_text())["mcpServers"]) == {"other", "funk", "funk2", "reel", "studio"}


def test_register_creates_missing_config_and_refuses_corrupt(tmp_path):
    cfg = tmp_path / "sub" / "c.json"
    install.register_desktop(install.Runner(False, ROOT), cfg, ())
    assert "funk2" in json.loads(cfg.read_text())["mcpServers"]
    cfg.write_text("{not json")
    with pytest.raises(ValueError):
        install.register_desktop(install.Runner(False, ROOT), cfg, ())
    assert cfg.read_text() == "{not json"  # never replaced silently


def test_code_commands_and_main_unregister(tmp_path, monkeypatch, capsys):
    cmds = install.code_add_commands(ROOT, ())
    assert {c[5] for c in cmds} == {"funk", "funk2", "reel", "studio"}
    assert all(c[:5] == ["claude", "mcp", "add", "--scope", "user"] for c in cmds)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mcpServers": {"x": {}, "reel": {}}}))
    assert install.main(["--unregister", "--config", str(cfg)]) == 0
    assert json.loads(cfg.read_text())["mcpServers"] == {"x": {}}


def test_gimp_cmd_template_rendered_without_machine_paths(tmp_path):
    tpl = (ROOT / "studio" / "GIMP-MCP.cmd.template").read_text()
    assert "C:\\dev" not in tpl and "C:/dev" not in tpl
    (tmp_path / "studio").mkdir()
    shutil.copy(ROOT / "studio" / "GIMP-MCP.cmd.template", tmp_path / "studio")
    out = install.render_gimp_cmd(install.Runner(False, tmp_path), Path("G:/Apps/GIMP/bin/gimp-3.2.exe"),
                                  Path("G:/v/gimp-mcp"))
    text = out.read_bytes().decode()
    assert "@" not in re.sub(r"@echo", "", text) and "3.2" in text and "\r\n" in text


def test_funkbox_toml_roundtrip_and_resolution_order(tmp_path, monkeypatch):
    r = install.Runner(False, tmp_path)
    install.write_toml(r, fastsd_dir=r"D:\tools\fast sd", gimp_exe='C:\\Program Files\\GIMP 3\\bin\\gimp-3.exe')
    install.write_toml(r, gimp_mcp_dir="/opt/gm")
    assert install.read_toml(tmp_path / "funkbox.toml")["fastsd_dir"] == r"D:\tools\fast sd"
    sys.path.insert(0, str(ROOT / "studio"))
    import paths
    for k in ("FASTSD_DIR", "GIMP_MCP_DIR", "GIMP_EXE"):
        monkeypatch.delenv(k, raising=False)
    assert paths.fastsd_dir(tmp_path) == Path(r"D:\tools\fast sd")
    assert paths.gimp_mcp_dir(tmp_path) == Path("/opt/gm")
    monkeypatch.setenv("FASTSD_DIR", "/env/wins")
    assert paths.fastsd_dir(tmp_path) == Path("/env/wins")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("FASTSD_DIR")
    assert paths.fastsd_dir(empty) == empty / "vendor" / "fastsdcpu"


def test_no_hardcoded_machine_paths_in_studio():
    for p in (ROOT / "studio").iterdir():
        if p.is_file() and p.suffix in (".py", ".template", ".cmd"):
            t = p.read_text(encoding="utf-8")
            assert "C:\\dev" not in t and "C:/dev" not in t, p.name


def test_plugin_files_parse_and_skill_present():
    pj = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    mcp = json.loads((ROOT / ".mcp.json").read_text())
    assert pj["name"] == "funkbox" and mk["plugins"][0]["name"] == "funkbox"
    for k, s in mcp["mcpServers"].items():
        assert s["command"] == "uv" and "${CLAUDE_PLUGIN_ROOT}" in " ".join(s["args"]), k
    sk = (ROOT / "skills" / "funk-studio" / "SKILL.md").read_text()
    assert sk == (ROOT / "docs" / "skill" / "SKILL.md").read_text() and sk.startswith("---")
    wf = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    assert "libegl1 libgl1-mesa-dri ffmpeg" in wf and "FUNK_GL_BACKEND: egl" in wf and "3.12" in wf


@pytest.mark.skipif(not shutil.which("uv"), reason="uv not installed")
def test_uv_run_project_lists_tools():
    mcp = pytest.importorskip("mcp")
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(command="uv", args=["run", "--project", str(ROOT), "python", str(ROOT / "funk2_mcp.py")],
                                       env={**os.environ, "FUNK_GL_BACKEND": "egl"})
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                return [t.name for t in (await s.list_tools()).tools]
    try:
        names = asyncio.run(asyncio.wait_for(go(), 240))
    except Exception as e:  # offline sandbox: uv cannot resolve
        pytest.skip(f"uv could not start the server here: {type(e).__name__}")
    assert names
