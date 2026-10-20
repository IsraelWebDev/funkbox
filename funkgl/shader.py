"""Shader assembly, @param annotations, compilation and error remapping."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import moderngl
import numpy as np

_DIR = Path(__file__).resolve().parent

VERTEX = """#version 330
in vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }
"""

_NATIVE_WRAP = "\nvoid main() { fragColor = effect(uv()); }\n"
_TOY_WRAP = ("\nvoid main() { vec4 c = vec4(0.0, 0.0, 0.0, 1.0); "
             "mainImage(c, gl_FragCoord.xy); fragColor = c; }\n")

_PARAM_RE = re.compile(r"^\s*uniform\s+(float|int|vec3)\s+(\w+)\s*;\s*//\s*@param\s+(.+)$")
_TOY_RE = re.compile(r"\bvoid\s+mainImage\s*\(")
_NATIVE_RE = re.compile(r"\bvec4\s+effect\s*\(")


class ShaderError(Exception):
    """The shader source is unusable (no entry point, bad annotation, ...)."""


@dataclass
class CompileResult:
    ok: bool
    errors: list[dict]          # {"line": int|None, "message": str}, line in USER coordinates
    program: moderngl.Program | None


@dataclass
class Param:
    name: str
    type: str                   # float | int | color
    min: float | None = None
    max: float | None = None
    default: float | int | str = 0.0

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "min": self.min, "max": self.max,
                "default": self.default}


def _read(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


_PRELUDE = _read("prelude.glsl")
_NOISE = _read("noise.glsl")
# Glue that needs both the prelude's iPhase and the vendored noise.
_GLUE = """
// 2D noise field that loops exactly: sampled on a circle of `radius` in the 3rd/4th dimensions.
float loopNoise(vec2 p, float radius) {
  return snoise4(vec4(p, radius * cos(iPhase), radius * sin(iPhase)));
}
"""
_HEAD = "#version 330\n" + _PRELUDE.rstrip("\n") + "\n" + _NOISE.rstrip("\n") + "\n" + _GLUE
#: number of lines that precede the user's source in the assembled shader
USER_LINE_OFFSET = _HEAD.count("\n")


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def detect_style(source: str) -> str:
    code = strip_comments(source)
    if _TOY_RE.search(code):
        return "shadertoy"
    if _NATIVE_RE.search(code):
        return "native"
    raise ShaderError("define vec4 effect(vec2 p) or mainImage(...)")


def _num(s: str) -> float:
    return float(s)


def parse_params(source: str) -> list[Param]:
    """Parse `// @param` annotations in declaration order."""
    out: list[Param] = []
    for line in source.splitlines():
        m = _PARAM_RE.match(line)
        if not m:
            continue
        typ, name, rest = m.group(1), m.group(2), m.group(3).split()
        try:
            if typ == "vec3":
                if len(rest) != 2 or rest[0] != "color" or not re.fullmatch(r"#[0-9a-fA-F]{6}", rest[1]):
                    raise ValueError
                out.append(Param(name, "color", None, None, rest[1].lower()))
            elif typ == "int":
                lo, hi, df = int(rest[0]), int(rest[1]), int(rest[2])
                out.append(Param(name, "int", lo, hi, df))
            else:
                lo, hi, df = _num(rest[0]), _num(rest[1]), _num(rest[2])
                out.append(Param(name, "float", lo, hi, df))
        except (ValueError, IndexError):
            raise ShaderError(f"bad @param on uniform {name}: expected "
                              "'min max default' (or 'color #rrggbb' for vec3)") from None
    return out


def sample_params(params: list[Param], seed: int, overrides: dict | None = None) -> dict:
    """Deterministic seeded values, iterating params in declaration order."""
    rng = np.random.default_rng(seed)
    vals: dict = {}
    for p in params:
        if p.type == "float":
            vals[p.name] = round(float(rng.uniform(p.min, p.max)), 6)
        elif p.type == "int":
            vals[p.name] = int(rng.integers(int(p.min), int(p.max) + 1))
        else:
            vals[p.name] = p.default
    if overrides:
        vals.update(overrides)
    return vals


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


# Driver log line formats seen in the wild:
#   Mesa:    0:12(7): error: ...        NVIDIA: 0(12) : error C1008: ...
#   ANGLE/Apple: ERROR: 0:12: ...       Others: ERROR: 12:7: ...
_LOC_RES = [
    re.compile(r"^\s*(?:ERROR|WARNING)?:?\s*(\d+):(\d+)\(\d+\):?\s*(.*)$", re.I),   # 0:12(7)
    re.compile(r"^\s*(?:ERROR|WARNING)?:?\s*(\d+)\((\d+)\)\s*:?\s*(.*)$", re.I),    # 0(12)
    re.compile(r"^\s*(?:ERROR|WARNING)?:?\s*(\d+):(\d+):\s*(.*)$", re.I),           # 0:12: / 12:7:
]


def parse_driver_log(log: str, offset: int, n_user_lines: int) -> list[dict]:
    """Turn a driver info log into [{'line': user_line|None, 'message': str}]."""
    errors: list[dict] = []
    for raw in log.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("GLSL Compiler failed") or raw.startswith("GLSL Linker failed"):
            continue
        for k, rx in enumerate(_LOC_RES):
            m = rx.match(raw)
            if not m:
                continue
            a, b, msg = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            if k == 2 and a != 0:       # "LINE:COL" form
                line = a
            else:                       # "<source-string>:LINE"
                line = b
            if "warning" in raw.lower()[:20] and "error" not in raw.lower()[:30]:
                break
            user = line - offset
            if 1 <= user <= n_user_lines:
                errors.append({"line": user, "message": msg})
            else:
                errors.append({"line": None, "message": f"(engine prelude/wrapper, driver line {line}) {msg}"})
            break
        else:
            if raw.lower().startswith(("error", "fatal")) or "error" in raw.lower():
                errors.append({"line": None, "message": raw})
    if not errors and log.strip():
        errors.append({"line": None, "message": log.strip()})
    return errors


@dataclass
class ShaderSource:
    source: str
    name: str = ""
    style: str = field(init=False)
    params: list[Param] = field(init=False)
    no_loopcheck: bool = field(init=False)

    def __post_init__(self) -> None:
        if re.search(r"^\s*#\s*version\b", self.source, re.M):
            raise ShaderError("remove the #version line; funk adds '#version 330' itself")
        self.style = detect_style(self.source)
        self.params = parse_params(self.source)
        self.no_loopcheck = bool(re.search(r"//\s*@noloopcheck\b", self.source))

    @classmethod
    def from_file(cls, path: str | Path) -> "ShaderSource":
        p = Path(path)
        return cls(p.read_text(encoding="utf-8-sig"), name=p.stem)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()

    @property
    def n_lines(self) -> int:
        return self.source.count("\n") + 1

    def assemble(self) -> tuple[str, int]:
        """Return (full GLSL text, number of lines preceding the user source)."""
        src = self.source if self.source.endswith("\n") else self.source + "\n"
        wrap = _TOY_WRAP if self.style == "shadertoy" else _NATIVE_WRAP
        return _HEAD + src + wrap, USER_LINE_OFFSET

    def compile(self, ctx: moderngl.Context) -> CompileResult:
        text, offset = self.assemble()
        try:
            prog = ctx.program(vertex_shader=VERTEX, fragment_shader=text)
        except moderngl.Error as e:
            return CompileResult(False, parse_driver_log(str(e), offset, self.n_lines), None)
        return CompileResult(True, [], prog)
