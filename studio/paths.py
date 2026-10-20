"""Where the optional studio apps live. Stdlib only.

Resolution order for each setting: environment variable, then ``funkbox.toml`` in the repo root
(git-ignored, written by install.py), then the ``vendor/`` defaults install.py clones into.

    funkbox.toml
        fastsd_dir   = "/path/to/vendor/fastsdcpu"
        gimp_mcp_dir = "/path/to/vendor/gimp-mcp"
        gimp_exe     = "/path/to/gimp-3"
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = {"FASTSD_DIR": "fastsd_dir", "GIMP_MCP_DIR": "gimp_mcp_dir", "GIMP_EXE": "gimp_exe"}


def load_toml(root: Path = ROOT) -> dict:
    p = root / "funkbox.toml"
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    try:
        import tomllib
        return tomllib.loads(text)
    except ImportError:  # Python 3.10: flat key = "value" lines are all we write
        out = {}
        for m in re.finditer(r'^\s*([A-Za-z_]\w*)\s*=\s*"((?:[^"\\]|\\.)*)"', text, re.M):
            out[m.group(1)] = m.group(2).replace("\\\\", "\\")
        return out
    except Exception:
        return {}


def setting(env_name: str, root: Path = ROOT) -> str | None:
    v = os.environ.get(env_name)
    if v:
        return v
    v = load_toml(root).get(KEYS[env_name])
    return str(v) if v else None


def fastsd_dir(root: Path = ROOT) -> Path:
    return Path(setting("FASTSD_DIR", root) or root / "vendor" / "fastsdcpu")


def gimp_mcp_dir(root: Path = ROOT) -> Path:
    return Path(setting("GIMP_MCP_DIR", root) or root / "vendor" / "gimp-mcp")


def gimp_exe_setting(root: Path = ROOT) -> str | None:
    return setting("GIMP_EXE", root)


def fastsd_python(d: Path) -> Path:
    """The interpreter of FastSD's own venv (created by install.py as <dir>/env)."""
    for rel in (("env", "Scripts", "python.exe"), ("env", "bin", "python")):
        p = d.joinpath(*rel)
        if p.exists():
            return p
    return d / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
