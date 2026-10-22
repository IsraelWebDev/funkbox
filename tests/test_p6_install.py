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


@pytest.fixture(autouse=True)
def _no_machine_gimp(tmp_path, monkeypatch):
    """Hermetic: on a machine where gimp-mcp/GIMP are installed (funkbox.toml, vendor/), registration would
    add a real `gimp` entry and the dry run would print "GIMP found" instead of the download hint."""
    monkeypatch.setenv("GIMP_MCP_DIR", str(tmp_path / "no-gimp-mcp"))
    monkeypatch.setenv("GIMP_EXE", str(tmp_path / "no-gimp.exe"))
    monkeypatch.setattr(install, "find_gimp", lambda: None)


def test_pyproject_in_sync_with_requirements():
    tomllib = pytest.importorskip("tomllib")
    d = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert d["tool"]["uv"]["package"] is False
    import funkbox
    assert d["project"]["version"] == funkbox.__version__ and d["project"]["scripts"] == {"funkbox": "funkbox.cli:main"}
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


V3_KEYS = ("funk", "funk2", "reel", "studio", "stills")


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
    assert "funk" not in data["mcpServers"]                         # the v3 key was migrated away
    baks = list(tmp_path.glob("claude_desktop_config.json.bak-*"))
    assert len(baks) == 1 and "old" in baks[0].read_text()
    install.unregister_desktop(r, cfg)
    data = json.loads(cfg.read_text())
    assert data["mcpServers"] == {"other": other} and data["theme"] == "dark"
    # idempotent: a second register/unregister does not break or duplicate anything
    install.register_desktop(r, cfg, ())
    install.register_desktop(r, cfg, ())
    assert set(json.loads(cfg.read_text())["mcpServers"]) == {"other", "funkbox"}


def test_migration_from_a_v3_config_keeps_unrelated_servers(tmp_path, capsys):
    """A v3.2 config has five funkbox servers; registering leaves exactly one funkbox entry, a backup of the
    old file, and every unrelated server untouched."""
    cfg = tmp_path / "claude_desktop_config.json"
    unrelated = {"command": "npx", "args": ["-y", "some-server"], "env": {"TOKEN": "x"}}
    v3 = {k: {"command": "py", "args": [f"{k}_mcp.py"], "env": {"FUNK_HOME": "/x"}} for k in V3_KEYS}
    original = {"mcpServers": {"filesystem": unrelated, **v3, "gimp": {"command": "uv", "args": ["old-gimp"]}}, "x": [1]}
    cfg.write_text(json.dumps(original))
    r = install.Runner(False, ROOT)
    install.register_desktop(r, cfg, ())
    servers = json.loads(cfg.read_text())["mcpServers"]
    assert set(servers) == {"filesystem", "funkbox", "gimp"}        # gimp (not ours to remove) stays as it was
    assert servers["filesystem"] == unrelated and servers["gimp"] == original["mcpServers"]["gimp"]
    assert json.loads(cfg.read_text())["x"] == [1]
    e = servers["funkbox"]
    assert e["args"] == [str(ROOT / "funkbox.py"), "serve"] and e["env"]["FUNK_HOME"] == str(ROOT)
    assert "migrated from v3: removed " + ", ".join(V3_KEYS) in capsys.readouterr().out
    (bak,) = tmp_path.glob("claude_desktop_config.json.bak-*")
    assert json.loads(bak.read_text()) == original                  # the backup is the old file, byte for byte in content
    install.unregister_desktop(r, cfg)
    assert json.loads(cfg.read_text())["mcpServers"] == {"filesystem": unrelated}


def test_register_creates_missing_config_and_refuses_corrupt(tmp_path):
    cfg = tmp_path / "sub" / "c.json"
    install.register_desktop(install.Runner(False, ROOT), cfg, ())
    assert "funkbox" in json.loads(cfg.read_text())["mcpServers"]
    cfg.write_text("{not json")
    with pytest.raises(ValueError):
        install.register_desktop(install.Runner(False, ROOT), cfg, ())
    assert cfg.read_text() == "{not json"  # never replaced silently


def test_code_commands_and_main_unregister(tmp_path, monkeypatch, capsys):
    cmds = install.code_add_commands(ROOT, ())
    assert {c[5] for c in cmds} == {"funkbox"}
    assert all(c[:5] == ["claude", "mcp", "add", "--scope", "user"] for c in cmds)
    assert cmds[0][-2:] == [str(ROOT / "funkbox.py"), "serve"]
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mcpServers": {"x": {}, "reel": {}, "funkbox": {}}}))
    assert install.main(["--unregister", "--config", str(cfg)]) == 0
    assert json.loads(cfg.read_text())["mcpServers"] == {"x": {}}


def test_the_registered_entry_starts_the_server_from_any_folder(tmp_path):
    """The entry install.py writes (command + args, no cwd) lists the tools when started from an unrelated folder."""
    mcp = pytest.importorskip("mcp")
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    e = install.server_entries(ROOT, ())["funkbox"]
    env = {**os.environ, **e["env"], "FUNK_OUT": str(tmp_path / "out"), "FUNK_INPUTS": str(tmp_path / "in"),
           "FUNK_PROJECTS": str(tmp_path / "pr"), "FUNK_INBOX": str(tmp_path / "ib")}

    async def go():
        params = StdioServerParameters(command=sys.executable, args=e["args"], env=env, cwd=str(tmp_path))
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                return {t.name for t in (await s.list_tools()).tools}
    names = asyncio.run(go())
    assert len(names) == 37 and "project_approve_copy" in names


def test_gimp_cmd_template_rendered_without_machine_paths(tmp_path):
    tpl = (ROOT / "funkbox" / "studio" / "GIMP-MCP.cmd.template").read_text()
    assert "C:\\dev" not in tpl and "C:/dev" not in tpl
    (tmp_path / "funkbox" / "studio").mkdir(parents=True)
    shutil.copy(ROOT / "funkbox" / "studio" / "GIMP-MCP.cmd.template", tmp_path / "funkbox" / "studio")
    out = install.render_gimp_cmd(install.Runner(False, tmp_path), Path("G:/Apps/GIMP/bin/gimp-3.2.exe"),
                                  Path("G:/v/gimp-mcp"))
    text = out.read_bytes().decode()
    assert "@" not in re.sub(r"@echo", "", text) and "3.2" in text and "\r\n" in text


def test_funkbox_toml_roundtrip_and_resolution_order(tmp_path, monkeypatch):
    r = install.Runner(False, tmp_path)
    install.write_toml(r, fastsd_dir=r"D:\tools\fast sd", gimp_exe='C:\\Program Files\\GIMP 3\\bin\\gimp-3.exe')
    install.write_toml(r, gimp_mcp_dir="/opt/gm")
    assert install.read_toml(tmp_path / "funkbox.toml")["fastsd_dir"] == r"D:\tools\fast sd"
    from funkbox import config as paths
    for k in ("FASTSD_DIR", "GIMP_MCP_DIR", "GIMP_EXE"):
        monkeypatch.delenv(k, raising=False)
    assert paths.get(tmp_path).fastsd_dir == Path(r"D:\tools\fast sd")
    assert paths.get(tmp_path).gimp_mcp_dir == Path("/opt/gm")
    monkeypatch.setenv("FASTSD_DIR", "/env/wins")
    assert paths.get(tmp_path).fastsd_dir == Path("/env/wins")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("FASTSD_DIR")
    assert paths.get(empty).fastsd_dir == empty / "vendor" / "fastsdcpu"


def test_no_hardcoded_machine_paths_in_studio():
    # install.py renders GIMP-MCP.cmd (git-ignored) with this machine's real paths; skip generated files by
    # name, not via git, since an install may not be a git checkout
    generated = {"GIMP-MCP.cmd"}
    for p in (ROOT / "funkbox" / "studio").iterdir():
        if p.name in generated:
            continue
        if p.is_file() and p.suffix in (".py", ".template", ".cmd"):
            t = p.read_text(encoding="utf-8")
            assert "C:\\dev" not in t and "C:/dev" not in t, p.name


def test_plugin_files_parse_and_skill_present():
    pj = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    mcp = json.loads((ROOT / ".mcp.json").read_text())
    assert pj["name"] == "funkbox" and mk["plugins"][0]["name"] == "funkbox"
    assert list(mcp["mcpServers"]) == ["funkbox"]                        # one server, not five
    for k, s in mcp["mcpServers"].items():
        assert s["command"] == "uv" and "${CLAUDE_PLUGIN_ROOT}" in " ".join(s["args"]), k
        assert s["args"][-2:] == ["${CLAUDE_PLUGIN_ROOT}/funkbox.py", "serve"] and s["env"]["FUNK_HOME"] == "${CLAUDE_PLUGIN_ROOT}"
    import funkbox
    assert pj["version"] == funkbox.__version__                           # one version everywhere
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
        params = StdioServerParameters(command="uv", args=["run", "--project", str(ROOT), "python", str(ROOT / "funkbox.py"), "serve"],
                                       env={**os.environ}, cwd=str(ROOT.parent))
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                return [t.name for t in (await s.list_tools()).tools]
    try:
        names = asyncio.run(asyncio.wait_for(go(), 240))
    except Exception as e:  # offline sandbox: uv cannot resolve
        pytest.skip(f"uv could not start the server here: {type(e).__name__}")
    assert "loop_render" in names and "project_approve_copy" in names


# --------------------------------------------------------------------------- #
# installer robustness: packaged Desktop config, dead-venv detection, failures are fatal
# --------------------------------------------------------------------------- #
def _fake_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "system", lambda: "Windows")
    monkeypatch.delenv("CLAUDE_DESKTOP_CONFIG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))


def test_packaged_desktop_config_wins_over_appdata(tmp_path, monkeypatch):
    _fake_windows(monkeypatch, tmp_path)
    assert install.desktop_config_path() == tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"
    private = tmp_path / "Local" / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
    private.mkdir(parents=True)
    (private / "claude_desktop_config.json").write_text("{}")
    assert install.desktop_config_path() == private / "claude_desktop_config.json"
    monkeypatch.setenv("CLAUDE_DESKTOP_CONFIG", str(tmp_path / "explicit.json"))   # explicit still wins
    assert install.desktop_config_path() == tmp_path / "explicit.json"


def test_packaged_package_without_private_config_is_ignored(tmp_path, monkeypatch):
    _fake_windows(monkeypatch, tmp_path)
    (tmp_path / "Local" / "Packages" / "Claude_x" / "LocalCache").mkdir(parents=True)
    assert install.desktop_config_path().parent == tmp_path / "Roaming" / "Claude"


def test_failed_command_is_recorded_and_main_exits_nonzero(tmp_path, monkeypatch):
    r = install.Runner(False, tmp_path)
    assert r.run([sys.executable, "-c", "import sys; sys.exit(3)"]) == 3
    assert r.run([str(tmp_path / "no-such.exe")]) == 127
    assert len(r.failures) == 2 and install.summary(r) == 1
    assert install.summary(install.Runner(False, tmp_path)) == 0


def test_register_reads_back_and_fails_when_the_write_did_not_stick(tmp_path, monkeypatch):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    monkeypatch.setattr(install, "write_config", lambda p, c: None)   # e.g. Desktop rewrote it underneath us
    r = install.Runner(False, ROOT)
    install.register_desktop(r, cfg, ())
    assert r.failures and "funkbox" in r.failures[0]
    assert not any("read back" in line for line in r.log)


def test_dead_venv_is_detected_and_stops_the_install(tmp_path, monkeypatch):
    py = install.venv_python(tmp_path)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"not an interpreter")
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = C:\\gone\nversion_info = 3.12.13\n")
    assert install.venv_base_version(tmp_path) == "3.12.13"
    assert not install.venv_ok(py)
    monkeypatch.setattr(install, "find_uv", lambda: None)
    r = install.Runner(False, tmp_path)
    assert install.make_venv(r, ()) is False
    assert any("unusable" in f for f in r.failures)
    cfg = tmp_path / "cfg.json"
    assert install.main(["--register", "desktop", "--config", str(cfg), "--yes"], root=tmp_path) == 1
    assert not cfg.exists()                                           # never registers a server that cannot start


def test_healthy_venv_probe(tmp_path):
    assert install.venv_ok(Path(sys.executable))
    assert not install.venv_ok(tmp_path / "missing" / "python.exe")
