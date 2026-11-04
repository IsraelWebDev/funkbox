"""Turn missing-OpenGL errors into a package-manager install hint.

Pure functions, standard library only, so ``funkbox doctor`` and the tests can use them before any
graphics stack is installed.
"""

from __future__ import annotations

import platform
from pathlib import Path

#: (distribution ids matched against os-release ID / ID_LIKE, install command)
_PACKAGE_SETS: tuple[tuple[set[str], str], ...] = (
    ({"debian", "ubuntu"}, "sudo apt install -y libegl1 libegl-mesa0 libgl1-mesa-dri"),
    ({"fedora", "rhel", "centos"}, "sudo dnf install -y mesa-libEGL mesa-dri-drivers mesa-libGL"),
    ({"arch"}, "sudo pacman -S --needed mesa libglvnd"),
    ({"alpine"}, "sudo apk add mesa-egl mesa-dri-gallium mesa-gl"),
    ({"suse", "opensuse"}, "sudo zypper install Mesa-libEGL1 Mesa-dri libGL1"),
)
_GENERIC = "install Mesa's EGL, GL and DRI packages (libEGL, libGL, mesa dri drivers) with your package manager"


def read_os_release(path: str = "/etc/os-release") -> dict:
    """Parse ``KEY=value`` lines from os-release; missing or unreadable files give ``{}``."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _package_command(os_release: dict) -> str:
    ids: set[str] = set()
    for key in ("ID", "ID_LIKE"):
        ids.update(part for part in str(os_release.get(key, "")).lower().split() if part)
    for names, command in _PACKAGE_SETS:
        if ids & names:
            return command
    return _GENERIC


def install_hint(errors: list[str], system: str | None = None, os_release: dict | None = None) -> str:
    """A ready-to-paste install command for Linux, or ``""`` on every other platform."""
    system = platform.system() if system is None else system
    if system != "Linux":
        return ""
    os_release = read_os_release() if os_release is None else os_release
    command = _package_command(os_release)
    text = "\n".join(errors)
    missing = []
    if "libEGL" in text:
        missing.append("(libEGL was not found)")
    if "libGL" in text or "libgl" in text:
        missing.append("(libGL was not found)")
    note = "".join(f"\n{m}" for m in missing)
    return (
        f"OpenGL needs Mesa's EGL/GL libraries. Install them with:{note}\n  {command}\n"
        "then run `python -m funkbox doctor`."
    )
