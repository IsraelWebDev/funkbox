"""Desktop shortcuts "funk inbox" and "funk projects" (stdlib only, so install.py can use it too).

Windows: .lnk files created through PowerShell (WScript.Shell). macOS/Linux: the equivalent commands are
printed (symlinks into ~/Desktop). Creating a link changes nothing else, so an agent may run it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from funkbox.config import get



def inbox_path() -> Path:
    return get().inbox


def projects_path() -> Path:
    return get().projects


def targets() -> list[tuple[str, Path]]:
    return [("funk inbox", inbox_path()), ("funk projects", projects_path())]


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def windows_script(links: list[tuple[str, Path]]) -> str:
    """The PowerShell that creates the .lnk files on the user's desktop."""
    lines = ["$ErrorActionPreference = 'Stop'", "$desktop = [Environment]::GetFolderPath('Desktop')",
             "$sh = New-Object -ComObject WScript.Shell"]
    for name, target in links:
        lines += [f"$l = $sh.CreateShortcut((Join-Path $desktop {_ps_quote(name + '.lnk')}))",
                  f"$l.TargetPath = {_ps_quote(str(target))}", "$l.Save()"]
    return "\n".join(lines)


def posix_commands(links: list[tuple[str, Path]]) -> list[str]:
    cmds = ['mkdir -p "$HOME/Desktop"']
    for name, target in links:
        cmds.append(f'mkdir -p "{target}" && ln -sfn "{target}" "$HOME/Desktop/{name}"')
    return cmds


def make_shortcuts(dry_run: bool = False, platform: str | None = None) -> list[str]:
    """Create (or, off Windows, print the commands for) the two shortcuts. Returns the lines to show."""
    plat = platform or sys.platform
    links = targets()
    out: list[str] = []
    if plat.startswith("win"):
        script = windows_script(links)
        if dry_run:
            return ["would run PowerShell:", script]
        for _, t in links:
            t.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise RuntimeError("could not create the shortcuts: " + (r.stderr.strip() or r.stdout.strip()))
        return [f"created desktop shortcut {n!r} -> {t}" for n, t in links]
    out.append("Not Windows: run these commands to make the desktop links:")
    out += posix_commands(links)
    return out
