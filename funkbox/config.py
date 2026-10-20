"""THE ONLY place that reads environment variables and ``funkbox.toml``, and the only path fence.

Every setting resolves in the same order: environment variable (the names funkbox has always used),
then ``funkbox.toml`` in the repo root (git-ignored, written by ``install.py``), then the default.

    funkbox.toml
        fastsd_dir   = "/path/to/vendor/fastsdcpu"
        gimp_mcp_dir = "/path/to/vendor/gimp-mcp"
        gimp_exe     = "/path/to/gimp-3"
        # any other field of Config can be set the same way (home, out, inputs, gl_backend, ...)

``get()`` re-reads the environment on every call (it is a handful of dict lookups), so tests and
long-running servers always see the current settings. Standard library only: ``install.py`` imports
this module before any dependency is installed.

``allowed_roots()`` and ``resolve_path()`` are the path fence: a recipe, reel or poster may name only
files inside FUNK_HOME, FUNK_OUT, FUNK_INPUTS or FUNK_PROJECTS.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, fields
from pathlib import Path

#: the checkout (or plugin) folder this package lives in
ROOT = Path(__file__).resolve().parent.parent

# field -> (environment variable, funkbox.toml key)
_SOURCES: dict[str, str] = {
    "home": "FUNK_HOME",
    "out": "FUNK_OUT",
    "inputs": "FUNK_INPUTS",
    "projects": "FUNK_PROJECTS",
    "inbox": "FUNK_INBOX",
    "models": "FUNK_MODELS",
    "fastsd_dir": "FASTSD_DIR",
    "gimp_mcp_dir": "GIMP_MCP_DIR",
    "gimp_exe": "GIMP_EXE",
    "gl_backend": "FUNK_GL_BACKEND",
    "fastsd_port": "FASTSD_PORT",
    "gimp_port": "GIMP_MCP_PORT",
    "idle_min": "STUDIO_FASTSD_IDLE_MIN",
    "fastsd_device": "FASTSD_DEVICE",
    "fake_sd": "STUDIO_FAKE_SD",
    "reel_depth": "REEL_DEPTH",
    "reel_tts": "REEL_TTS",
}


@dataclass(frozen=True)
class Config:
    root: Path              # the checkout: templates/, fonts/ and the vendor/ defaults live here
    home: Path              # FUNK_HOME (default: root): shaders/, fonts/ and recipe-relative paths
    out: Path
    inputs: Path
    projects: Path
    inbox: Path
    models: Path
    fastsd_dir: Path
    gimp_mcp_dir: Path
    gimp_exe: str | None
    gl_backend: str         # "egl" | "default" | "" (try the default backend, then EGL)
    fastsd_port: int
    gimp_port: int
    idle_min: float
    fastsd_device: str
    fake_sd: bool           # STUDIO_FAKE_SD: the image tools use a deterministic stub (tests)
    reel_depth: str         # REEL_DEPTH: stub|off|model|cached|"" (tests force "stub")
    reel_tts: str           # REEL_TTS: stub|off|"" (tests force "stub")
    toml_path: Path
    sources: tuple[tuple[str, str], ...]   # (field, "env" | "toml" | "default"), for `funkbox doctor`

    # -- data folders ---------------------------------------------------------------------------
    @property
    def shaders_dir(self) -> Path:
        return self.home / "shaders"

    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    @property
    def fonts_dir(self) -> Path:
        return self.root / "fonts"

    def describe(self) -> list[str]:
        src = dict(self.sources)
        rows = []
        for f in fields(self):
            if f.name in ("sources", "toml_path"):
                continue
            rows.append(f"{f.name:<14} {str(getattr(self, f.name)):<60} [{src.get(f.name, 'derived')}]")
        rows.append(f"{'toml_path':<14} {self.toml_path} ({'found' if self.toml_path.is_file() else 'absent'})")
        return rows


# --------------------------------------------------------------------------- #
# funkbox.toml
# --------------------------------------------------------------------------- #
_TOML_CACHE: dict[Path, tuple[int, int, dict]] = {}


def read_toml(path: Path, strict: bool = False) -> dict[str, str]:
    """Read a flat ``key = "value"`` file (hand edits and 'literal' strings included).

    Malformed files give {} at run time; ``strict=True`` (the installer) raises ValueError instead, so a
    rewrite never silently drops keys.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    try:
        import tomllib
        return {k: str(v) for k, v in tomllib.loads(text).items()}
    except ImportError:     # Python 3.10: flat key = "value" lines are all we write
        out: dict[str, str] = {}
        for m in re.finditer(r'^\s*([A-Za-z_]\w*)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|\'([^\']*)\')', text, re.M):
            out[m.group(1)] = re.sub(r"\\(.)", r"\1", m.group(2)) if m.group(2) is not None else m.group(3)
        return out
    except Exception as e:  # noqa: BLE001 - tomllib.TOMLDecodeError
        if strict:
            raise ValueError(f"{path.name} is not valid TOML ({e})") from None
        return {}


def _toml(path: Path) -> dict[str, str]:
    try:
        st = path.stat()
    except OSError:
        return {}
    hit = _TOML_CACHE.get(path)
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size):
        return hit[2]
    data = read_toml(path)
    _TOML_CACHE[path] = (st.st_mtime_ns, st.st_size, data)
    return data


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #
def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _headless_linux() -> bool:
    return sys.platform == "linux" and "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ


def get(root: Path | str | None = None) -> Config:
    """The current configuration. ``root`` replaces the checkout folder (tests use a temp folder)."""
    base = Path(root) if root else ROOT
    toml_path = base / "funkbox.toml"
    toml = _toml(toml_path)
    srcs: list[tuple[str, str]] = []

    def raw(name: str) -> str | None:
        env_name = _SOURCES[name]
        v = os.environ.get(env_name)
        if v:
            srcs.append((name, "env"))
            return v
        v = toml.get(name) or toml.get(env_name.lower())
        if v:
            srcs.append((name, "toml"))
            return str(v)
        srcs.append((name, "default"))
        return None

    def path(name: str, default: Path, resolve: bool = True) -> Path:
        v = raw(name)
        if not v:
            return default
        return Path(v).expanduser().resolve() if resolve else Path(v)    # app folders are used as written

    def num(name: str, default: float, cast=float):
        v = raw(name)
        try:
            return cast(v) if v else default
        except ValueError:
            raise ValueError(f"{_SOURCES[name]}={v!r} is not a number") from None

    home = path("home", base.resolve())
    backend = (raw("gl_backend") or "").strip().lower()
    if not backend and _headless_linux():
        backend = "egl"             # no display: software OpenGL (Mesa llvmpipe) through EGL
    return Config(
        root=base, home=home,
        out=path("out", home / "out"),
        inputs=path("inputs", home / "inputs"),
        projects=path("projects", home / "projects"),
        inbox=path("inbox", home / "inbox"),
        models=path("models", base.resolve() / "models"),
        fastsd_dir=path("fastsd_dir", base / "vendor" / "fastsdcpu", resolve=False),
        gimp_mcp_dir=path("gimp_mcp_dir", base / "vendor" / "gimp-mcp", resolve=False),
        gimp_exe=raw("gimp_exe"),
        gl_backend=backend,
        fastsd_port=num("fastsd_port", 8000, int),
        gimp_port=num("gimp_port", 9877, int),
        idle_min=num("idle_min", 20.0),
        fastsd_device=raw("fastsd_device") or "GPU",
        fake_sd=_truthy(raw("fake_sd") or ""),
        reel_depth=(raw("reel_depth") or "").strip().lower(),
        reel_tts=(raw("reel_tts") or "").strip().lower(),
        toml_path=toml_path, sources=tuple(srcs))


# Short accessors (the old names), so call sites stay readable.
def funk_home() -> Path:
    return get().home


def funk_out() -> Path:
    return get().out


def funk_inputs() -> Path:
    return get().inputs


def funk_projects() -> Path:
    return get().projects


def inbox_dir() -> Path:
    return get().inbox


def models_dir() -> Path:
    return get().models


# --------------------------------------------------------------------------- #
# the path fence
# --------------------------------------------------------------------------- #
def allowed_roots() -> list[Path]:
    c = get()
    return [c.home, c.out, c.inputs, c.projects]


def resolve_path(p: str | Path, base_dir: str | Path | None = None) -> Path:
    """Resolve a recipe path (relative to the recipe's folder, then FUNK_HOME / INPUTS / OUT).

    Raises ValueError if the result is outside FUNK_HOME / FUNK_OUT / FUNK_INPUTS / FUNK_PROJECTS and
    FileNotFoundError if it does not exist.
    """
    c = get()
    raw = Path(str(p).replace("\\", "/")) if os.sep == "/" else Path(p)
    cands: list[Path] = []
    if raw.is_absolute():
        cands.append(raw)
    else:
        if base_dir is not None:
            cands.append(Path(base_dir) / raw)
        cands += [c.home / raw, c.inputs / raw, c.out / raw]
    roots = [c.home, c.out, c.inputs, c.projects]
    inside: list[Path] = []
    for cand in cands:
        try:
            rc = cand.resolve()
        except OSError:
            continue
        if any(rc == r or rc.is_relative_to(r) for r in roots):
            inside.append(rc)
    # Fence first, existence second: an outside path is refused whether or not it exists,
    # so the error never reveals what exists outside the allowed folders.
    if not inside:
        raise ValueError(f"path {p!s} resolves outside the allowed folders "
                         f"({', '.join(str(r) for r in roots)}); put the file in the inputs folder (FUNK_INPUTS)")
    for rc in inside:
        if rc.exists():
            return rc
    raise FileNotFoundError(f"file not found: {p}")
