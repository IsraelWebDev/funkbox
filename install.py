#!/usr/bin/env python3
"""funkbox self-installer. Standard library only; safe to run again (idempotent).

    python install.py                                   # venv + core deps + self-test
    python install.py --with reel,fastsd,gimp           # optional parts
    python install.py --register desktop|code|none      # connect to Claude
    python install.py --shortcuts                       # "funk inbox", "funk projects", "GIMP + MCP"
    python install.py --unregister                      # remove only funkbox's own entries
    python install.py --dry-run                         # print the plan, change nothing
    python install.py --yes                             # no questions

It never installs GIMP (prints where to get it), never touches Claude config keys that are not its own
(funkbox, gimp, and the v3 keys funk, funk2, reel, studio, stills, which it migrates away) and takes a
timestamped backup before changing a config file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from funkbox import config  # noqa: E402  (stdlib only: the one place that reads env and funkbox.toml)
SERVER_KEYS = ("funkbox", "gimp")
#: the five v3 servers that `funkbox` replaces; registering removes them (after the backup)
OLD_SERVER_KEYS = ("funk", "funk2", "reel", "studio", "stills")
LAUNCHER = "funkbox.py"
FASTSD_URL = "https://github.com/rupeshs/fastsdcpu"
GIMP_MCP_URL = "https://github.com/maorcc/gimp-mcp"
GIMP_DOWNLOAD = "https://www.gimp.org/downloads/"
GIMP_WINGET = "GIMP.GIMP.3"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PARTS = ("reel", "fastsd", "gimp")


# --------------------------------------------------------------------------- #
# paths and small helpers
# --------------------------------------------------------------------------- #
def system() -> str:
    return platform.system()  # Windows | Darwin | Linux


def venv_python(root: Path = ROOT) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def msix_config_dirs() -> list[Path]:
    """Config folders of a packaged (Microsoft Store / MSIX) Claude Desktop, newest config first.

    A packaged app's AppData writes are redirected to Packages/Claude_<id>/LocalCache/Roaming, and once that
    private copy exists the app reads only it. A terminal outside the package sees the real AppData instead, so
    writing %APPDATA%/Claude from there lands in a file Desktop never reads (and the reverse: things uv installed
    from inside Claude are invisible to an outside terminal). Only folders that already exist count: until
    Desktop has written its private copy, it still reads the real one."""
    base = os.environ.get("LOCALAPPDATA")
    if system() != "Windows" or not base:
        return []
    dirs = [d for d in (p / "LocalCache" / "Roaming" / "Claude" for p in Path(base, "Packages").glob("Claude_*"))
            if d.is_dir()]
    cfg = lambda d: d / "claude_desktop_config.json"
    return sorted(dirs, key=lambda d: cfg(d).stat().st_mtime if cfg(d).exists() else 0.0, reverse=True)


def desktop_config_path() -> Path:
    env = os.environ.get("CLAUDE_DESKTOP_CONFIG")
    if env:
        return Path(env)
    s = system()
    if s == "Windows":
        for d in msix_config_dirs():
            return d / "claude_desktop_config.json"
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"
    if s == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "Claude" / "claude_desktop_config.json"


def toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Runner:
    """Runs (or, in a dry run, only prints) the steps."""

    def __init__(self, dry: bool, root: Path = ROOT):
        self.dry = dry
        self.root = root
        self.log: list[str] = []
        self.failures: list[str] = []

    def say(self, msg: str) -> None:
        self.log.append(msg)
        print(msg, flush=True)

    def step(self, title: str) -> None:
        self.say(f"\n== {title}")

    def fail(self, msg: str) -> None:
        """A step that did not do its job: reported again in the summary and turned into a non-zero exit, so an
        agent installing funkbox can trust the exit code instead of parsing prose."""
        self.failures.append(msg)
        self.say(f"   FAILED: {msg}")

    def run(self, cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
        shown = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        self.say(f"   $ {shown}" + (f"   (in {cwd})" if cwd else ""))
        if self.dry:
            return 0
        try:
            rc = subprocess.call(cmd, cwd=str(cwd) if cwd else None, env=env)
        except OSError as e:
            self.fail(f"could not start {cmd[0]}: {e}")
            return 127
        if rc != 0:
            self.fail(f"exit {rc}: {shown}")
        return rc


def find_uv() -> str | None:
    return shutil.which("uv")


# --------------------------------------------------------------------------- #
# config building (pure functions: these are what the tests exercise)
# --------------------------------------------------------------------------- #
def server_entries(root: Path = ROOT, parts: tuple[str, ...] = ()) -> dict[str, dict]:
    py = str(venv_python(root))
    # The launcher script puts the checkout on sys.path itself, so the entry needs no cwd (Claude clients start
    # servers wherever they like) and no PYTHONPATH.
    out = {"funkbox": {"command": py, "args": [str(root / LAUNCHER), "serve"], "env": {"FUNK_HOME": str(root)}}}
    # gimp-mcp location: env / funkbox.toml / vendor/ (same resolution as the studio server)
    gdir = config.get(root).gimp_mcp_dir
    if "gimp" in parts or (gdir / "gimp_mcp_server.py").exists():
        # absolute uv path: Claude Desktop does not necessarily inherit the shell's PATH
        out["gimp"] = {"command": find_uv() or "uv",
                       "args": ["run", "--directory", str(gdir), "gimp_mcp_server.py"]}
    return out


def merge_servers(cfg: dict, entries: dict[str, dict]) -> tuple[dict, list[str]]:
    """Return (cfg with our keys set inside mcpServers and the v3 keys removed, the v3 keys removed).
    Everything else is left as it was."""
    cfg = dict(cfg)
    servers = dict(cfg.get("mcpServers") or {})
    migrated = [k for k in OLD_SERVER_KEYS if k in servers and k not in entries]
    for k in migrated:
        del servers[k]
    for k, v in entries.items():
        servers[k] = v
    cfg["mcpServers"] = servers
    return cfg, migrated


def strip_servers(cfg: dict, keys: tuple[str, ...] = SERVER_KEYS + OLD_SERVER_KEYS) -> tuple[dict, list[str]]:
    cfg = dict(cfg)
    servers = dict(cfg.get("mcpServers") or {})
    removed = [k for k in keys if k in servers]
    for k in removed:
        del servers[k]
    if "mcpServers" in cfg or removed:
        cfg["mcpServers"] = servers
    return cfg, removed


def read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)  # an unreadable config is an error, never silently replaced
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    b = path.with_name(path.name + ".bak-" + dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    shutil.copy2(path, b)
    return b


def write_config(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def register_desktop(r: Runner, path: Path, parts: tuple[str, ...]) -> None:
    r.step(f"Register with Claude Desktop ({path})")
    entries = server_entries(r.root, parts)
    r.say("   servers to add or update: " + ", ".join(entries) + " (no other key is touched; the v3 entries "
          + ", ".join(OLD_SERVER_KEYS) + " are removed if present, after a backup)")
    r.say("   WARNING: quit Claude Desktop first (right-click the tray icon, Quit) or it may overwrite this file; "
          "start it again afterwards.")
    if r.dry:
        r.say("   (dry run: nothing written, no backup made)")
        return
    cfg = read_config(path)
    b = backup(path)
    if b:
        r.say(f"   backup: {b}")
    new, migrated = merge_servers(cfg, entries)
    write_config(path, new)
    if migrated:
        r.say("   migrated from v3: removed " + ", ".join(migrated))
    got = read_config(path).get("mcpServers") or {}
    wrong = [k for k, v in entries.items() if got.get(k) != v]
    if wrong:
        r.fail(f"{path} does not hold {', '.join(wrong)} after writing it (is Claude Desktop still running?)")
        return
    r.say(f"   written and read back: {path}")
    shadow = [d / "claude_desktop_config.json" for d in msix_config_dirs()]
    if shadow and path.resolve() not in {s.resolve() for s in shadow}:
        r.say(f"   WARNING: Claude Desktop here is the packaged (Store/MSIX) build and reads {shadow[0]}, not this "
              "file; drop --config / CLAUDE_DESKTOP_CONFIG to target it.")


def unregister_desktop(r: Runner, path: Path) -> None:
    r.step(f"Unregister from Claude Desktop ({path})")
    if not path.exists():
        r.say("   no config file: nothing to do")
        return
    if r.dry:
        r.say("   would remove only the keys: " + ", ".join(SERVER_KEYS + OLD_SERVER_KEYS))
        return
    cfg = read_config(path)
    new, removed = strip_servers(cfg)
    if not removed:
        r.say("   none of funkbox's entries are present")
        return
    b = backup(path)
    r.say(f"   backup: {b}")
    write_config(path, new)
    r.say("   removed: " + ", ".join(removed))


def code_add_commands(root: Path, parts: tuple[str, ...]) -> list[list[str]]:
    cmds = []
    for k, e in server_entries(root, parts).items():
        c = ["claude", "mcp", "add", "--scope", "user", k]
        for a_, b_ in e.get("env", {}).items():
            c += ["-e", f"{a_}={b_}"]
        cmds.append(c + ["--", e["command"], *e["args"]])
    return cmds


def register_code(r: Runner, parts: tuple[str, ...]) -> None:
    """Claude Code: user-scope servers through its own CLI (the repo's .mcp.json belongs to the plugin)."""
    r.step("Register with Claude Code")
    have = shutil.which("claude")
    for k in OLD_SERVER_KEYS:       # migrate from v3: drop the five old servers (no-op when absent)
        c = ["claude", "mcp", "remove", "--scope", "user", k]
        if have and not r.dry:
            if subprocess.run(c, capture_output=True).returncode == 0:
                r.say(f"   migrated from v3: removed {k}")
        else:
            r.say("   (if present) " + " ".join(c))
    for c in code_add_commands(r.root, parts):
        if have:
            r.run(c)
        else:
            r.say("   " + " ".join(f'"{x}"' if " " in x else x for x in c))
    if not have:
        r.say("   (the claude CLI was not found: run the lines above in a terminal where it works.)")
    r.say("   Alternative without this script: claude --plugin-dir . (the repo is also a Claude Code plugin).")


def unregister_code(r: Runner) -> None:
    r.step("Unregister from Claude Code")
    have = shutil.which("claude")
    for k in SERVER_KEYS + OLD_SERVER_KEYS:
        c = ["claude", "mcp", "remove", "--scope", "user", k]
        if have:
            r.run(c)
        else:
            r.say("   " + " ".join(c))


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def venv_ok(py: Path) -> bool:
    """Can this process actually start the venv's Python? Its python.exe is only a launcher for the interpreter
    named in pyvenv.cfg, so the file can exist while that interpreter is gone or not visible from here (moved,
    `uv python uninstall`-ed, or installed from inside packaged Claude Desktop: see msix_config_dirs)."""
    if not py.exists():
        return False
    try:
        return subprocess.run([str(py), "-c", "pass"], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def venv_base_version(root: Path = ROOT) -> str | None:
    cfg = root / ".venv" / "pyvenv.cfg"
    if not cfg.exists():
        return None
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        k, _, v = line.partition("=")
        if k.strip() in ("version_info", "version") and v.strip():
            return v.strip()
    return None


def make_venv(r: Runner, parts: tuple[str, ...]) -> bool:
    """True when .venv is usable afterwards (always True in a dry run); every later step depends on it."""
    r.step("Python environment (.venv) and dependencies")
    uv = find_uv()
    py = venv_python(r.root)
    if py.exists() and (r.dry or venv_ok(py)):
        r.say("   .venv already exists: reusing it")
    elif py.exists():
        ver = venv_base_version(r.root)
        r.say(f"   .venv exists but its base Python{' ' + ver if ver else ''} cannot be started from here")
        if uv and ver:  # non-destructive: put the interpreter back where pyvenv.cfg says, for this process
            r.run([uv, "python", "install", ver])
        if not venv_ok(py):
            r.fail(f"{r.root / '.venv'} is unusable; delete it (quit Claude Desktop first if funkbox is "
                   "registered: it runs from there) and run this again")
            return False
        r.say("   .venv repaired")
    elif uv:
        r.run([uv, "venv", str(r.root / ".venv")])
    else:
        r.run([sys.executable, "-m", "venv", str(r.root / ".venv")])
    req = ["-r", str(r.root / "requirements.txt")]
    if "reel" in parts:
        req += ["-r", str(r.root / "requirements-reel.txt")]
    if uv:
        r.run([uv, "pip", "install", "--python", str(py), *req])
    else:
        r.run([str(py), "-m", "pip", "install", "--upgrade", "pip", "-q"])
        r.run([str(py), "-m", "pip", "install", *req])
    if r.dry or venv_ok(py):
        return True
    r.fail(f"{py} does not start after setup")
    return False


def setup_reel(r: Runner) -> None:
    r.step("reel models (depth + voices)")
    r.say("   Licences: Depth-Anything-V2-Small and Kokoro-82M are Apache-2.0; Piper voice en_US-ljspeech-medium is "
          "public-domain data; piper-tts and espeak-ng (used by kokoro-onnx) are GPL-3.0 (installed here, not "
          "bundled). See THIRD_PARTY_NOTICES.md.")
    r.run([str(venv_python(r.root)), "-m", "funkbox", "reel", "setup-models"], cwd=r.root)


def git_clone(r: Runner, url: str, dest: Path) -> bool:
    if (dest / ".git").exists() or (dest.exists() and any(dest.iterdir())):
        r.say(f"   {dest} already present: leaving it as is")
        return True
    if not shutil.which("git") and not r.dry:
        r.say("   git not found; install git and run this again")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True) if not r.dry else None
    return r.run(["git", "clone", "--depth", "1", url, str(dest)]) == 0


def configured_dir(r: "Runner", key: str, default: Path) -> Path:
    """An existing install named by env / funkbox.toml wins over cloning a fresh copy into vendor/."""
    d = getattr(config.get(r.root), key)
    return d if d.exists() else default


def setup_fastsd(r: Runner) -> None:
    r.step("FastSD CPU (text-to-image, optional, MIT)")
    dest = configured_dir(r, "fastsd_dir", r.root / "vendor" / "fastsdcpu")
    if not git_clone(r, FASTSD_URL, dest):
        return
    uv = find_uv()
    if not uv and r.dry:
        r.say("   (uv not found: a real run would stop here and ask for it; showing the plan with 'uv')")
        uv = "uv"
    if not uv:
        r.say("   uv is required for FastSD's Python 3.11 environment: https://docs.astral.sh/uv/ "
              "(pip install uv), then run this again")
        return
    env_dir = dest / "env"
    fpy = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not fpy.exists():
        r.run([uv, "venv", "--python", "3.11", str(env_dir)])
    r.run([uv, "pip", "install", "--python", str(fpy), "torch", "torchvision", "--index-url", TORCH_CPU_INDEX])
    reqs = dest / "requirements.txt"
    if reqs.exists() or r.dry:
        r.run([uv, "pip", "install", "--python", str(fpy), "-r", str(reqs)])
    r.run([uv, "pip", "install", "--python", str(fpy), "pydantic>=2.11,<2.12", "fastapi-mcp"])
    r.say("   copying funkbox/studio/start_local_mcp.py and funkbox/studio/sdcore.py into the FastSD folder (localhost-only launcher)")
    if not r.dry:
        for f in ("start_local_mcp.py", "sdcore.py"):
            shutil.copy2(r.root / "funkbox" / "studio" / f, dest / f)
    write_toml(r, fastsd_dir=str(dest))


def find_gimp() -> Path | None:
    env = config.get().gimp_exe
    if env and Path(env).exists():
        return Path(env)
    s = system()
    cands: list[Path] = []
    if s == "Windows":
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
            if base:
                for d in sorted(Path(base, "Programs").glob("GIMP 3*"), reverse=True) + sorted(Path(base).glob("GIMP 3*"), reverse=True):
                    cands += [d / "bin" / n for n in ("gimp-3.2.exe", "gimp-3.exe", "gimp.exe")]
    elif s == "Darwin":
        cands.append(Path("/Applications/GIMP.app/Contents/MacOS/gimp"))
    for c in cands:
        if c.exists():
            return c
    for n in ("gimp-3", "gimp-3.2", "gimp"):
        f = shutil.which(n)
        if f:
            return Path(f)
    return None


def gimp_profile(exe: Path | None) -> str:
    import re
    m = re.search(r"gimp-(\d+)\.(\d+)", exe.name if exe else "")
    return f"{m.group(1)}.{m.group(2)}" if m else "3.2"


def setup_gimp(r: Runner, shortcuts: bool) -> None:
    r.step("GIMP MCP (optional, GPL-3.0, cloned not bundled)")
    dest = configured_dir(r, "gimp_mcp_dir", r.root / "vendor" / "gimp-mcp")
    if git_clone(r, GIMP_MCP_URL, dest):
        uv = find_uv()
        if uv:
            r.run([uv, "sync"], cwd=dest)
        else:
            r.say("   uv not found: install it (https://docs.astral.sh/uv/) and run this again to finish gimp-mcp")
    exe = find_gimp()
    if exe:
        r.say(f"   GIMP found: {exe}")
        write_toml(r, gimp_mcp_dir=str(dest), gimp_exe=str(exe))
    else:
        r.say("   GIMP 3 was not found. Install it yourself (this script never installs GIMP):")
        r.say(f"     download: {GIMP_DOWNLOAD}")
        r.say(f"     Windows:  winget install {GIMP_WINGET}")
        write_toml(r, gimp_mcp_dir=str(dest))
    if system() == "Windows":
        render_gimp_cmd(r, exe, dest)


def read_toml(p: Path) -> dict[str, str]:
    """funkbox.toml including hand edits; a malformed file stops the run so a rewrite never drops keys."""
    try:
        return config.read_toml(p, strict=True)
    except ValueError as e:
        raise SystemExit(f"{e}; fix or delete it and run again") from None


def write_toml(r: Runner, **kv: str) -> None:
    """Merge keys into funkbox.toml (git-ignored); the studio server reads it."""
    p = r.root / "funkbox.toml"
    cur = read_toml(p)
    cur.update(kv)
    r.say("   funkbox.toml: " + ", ".join(f"{k}={v}" for k, v in kv.items()))
    if not r.dry:
        p.write_text("".join(f"{k} = {toml_str(v)}\n" for k, v in cur.items()), encoding="utf-8")


def render_gimp_cmd(r: Runner, exe: Path | None, gimp_dir: Path) -> Path:
    tpl = (r.root / "funkbox" / "studio" / "GIMP-MCP.cmd.template").read_text(encoding="utf-8")
    out = r.root / "funkbox" / "studio" / "GIMP-MCP.cmd"
    text = (tpl.replace("@GIMP_EXE@", str(exe or r"%LOCALAPPDATA%\Programs\GIMP 3\bin\gimp-3.exe"))
            .replace("@GIMP_MCP_DIR@", str(gimp_dir))
            .replace("@GIMP_PROFILE@", gimp_profile(exe))
            .replace("@FUNK_HOME_FWD@", str(r.root).replace("\\", "/")))
    r.say(f"   rendering {out} from the template")
    if not r.dry:
        out.write_text(text, encoding="utf-8", newline="\r\n")
    return out


def make_shortcuts(r: Runner, parts: tuple[str, ...]) -> None:
    r.step('Shortcuts: "funk inbox", "funk projects"' + (', "GIMP + MCP"' if "gimp" in parts else ""))
    from funkbox.reel import shortcuts  # stdlib-only module

    for line in shortcuts.make_shortcuts(dry_run=r.dry):
        r.say("   " + line)
    if "gimp" in parts:
        cmd = r.root / "funkbox" / "studio" / "GIMP-MCP.cmd"
        if system() == "Windows":
            script = ("$sh=New-Object -ComObject WScript.Shell;"
                      "$l=$sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'GIMP + MCP.lnk'));"
                      f"$l.TargetPath='{cmd}';$l.Save()")
            r.say(f'   creating "GIMP + MCP" desktop shortcut -> {cmd}')
            if not r.dry:
                subprocess.call(["powershell", "-NoProfile", "-Command", script])
        else:
            r.say("   GIMP + MCP: open GIMP 3, then Tools > MCP > Start MCP Server (the .cmd launcher is Windows only)")


def self_test(r: Runner) -> int:
    r.step("Self-test: one tiny loop render")
    py = venv_python(r.root)
    if not py.exists():
        py = Path(sys.executable)  # dry run, or a venv elsewhere
    out = r.root / "out" / "install-selftest"
    shader = sorted((r.root / "shaders").glob("*.frag"))
    shader_arg = ["--shader", str(shader[0])] if shader else []
    env = dict(os.environ)
    rc = r.run([str(py), "-m", "funkbox", "loop", "render", *shader_arg, "--size", "128", "--seconds", "1",
                "--fps", "12", "--out-dir", str(out), "--name", "selftest", "--no-png", "--no-check"],
               cwd=r.root, env=env)
    if rc == 0 and not r.dry:
        r.say(f"   OK: {out}")
    elif rc != 0:
        r.say("   self-test failed. If this is a headless Linux box, try: FUNK_GL_BACKEND=egl python install.py")
    return rc


def next_steps(r: Runner, register: str, parts: tuple[str, ...]) -> None:
    r.step("Next steps")
    if register == "desktop":
        r.say("   1. Start Claude Desktop again (it was to be closed while this ran).")
    elif register == "code":
        r.say("   1. Restart Claude Code in this folder; check with: claude mcp list")
    else:
        r.say("   1. Connect later with: python install.py --register desktop   (or code)")
    r.say("   2. Ask Claude: 'make me a funky looping video'. For ads: project_new, then drop files in the inbox folder.")
    r.say("   3. Verify: " + str(venv_python(r.root)) + " -m pytest -q   (needs: pip install pytest)")
    r.say("   4. Remove: python install.py --unregister   (then delete the folder)")


# --------------------------------------------------------------------------- #
def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="funkbox self-installer (idempotent, stdlib only)")
    ap.add_argument("--with", dest="with_", default="", help="comma list of: " + ",".join(PARTS))
    ap.add_argument("--register", choices=["desktop", "code", "none"], default="none")
    ap.add_argument("--shortcuts", action="store_true")
    ap.add_argument("--unregister", action="store_true", help="remove funkbox's own entries from the Claude config")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--config", help="Claude Desktop config path (default: the standard location)")
    ap.add_argument("--no-selftest", action="store_true")
    a = ap.parse_args(argv)
    bad = [p for p in a.with_.split(",") if p and p not in PARTS]
    if bad:
        ap.error(f"unknown part(s) {bad}; choose from {PARTS}")
    a.parts = tuple(p for p in a.with_.split(",") if p)
    return a


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    a = parse(argv)
    r = Runner(a.dry_run, root)
    cfg_path = Path(a.config) if a.config else desktop_config_path()
    r.say(f"funkbox installer  ({system()}, Python {platform.python_version()}, {'DRY RUN' if a.dry_run else 'live'})")
    if a.unregister:
        unregister_desktop(r, cfg_path)
        if a.register == "code":
            unregister_code(r)
        return 0
    if not a.yes and not a.dry_run and sys.stdin.isatty():
        r.say(f"Plan: venv + core deps; parts: {', '.join(a.parts) or 'none'}; register: {a.register}; "
              f"shortcuts: {a.shortcuts}. (Use --dry-run to see every step.)")
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted")
            return 1
    if not make_venv(r, a.parts):
        r.say("   stopping: nothing else can work, and registering a server that cannot start is worse than none.")
        return summary(r)
    if "reel" in a.parts:
        setup_reel(r)
    if "fastsd" in a.parts:
        setup_fastsd(r)
    if "gimp" in a.parts:
        setup_gimp(r, a.shortcuts)
    if a.register == "desktop":
        register_desktop(r, cfg_path, a.parts)
    elif a.register == "code":
        register_code(r, a.parts)
    if a.shortcuts:
        make_shortcuts(r, a.parts)
    if not a.no_selftest:
        self_test(r)
    next_steps(r, a.register, a.parts)
    return summary(r)


def summary(r: Runner) -> int:
    if not r.failures:
        return 0
    r.step(f"{len(r.failures)} step(s) FAILED: funkbox is not fully installed")
    for f in r.failures:
        r.say("   - " + f)
    return 1


if __name__ == "__main__":
    sys.exit(main())
