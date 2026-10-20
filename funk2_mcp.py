#!/usr/bin/env python3
"""
funk v2 as an MCP server: Claude writes GLSL shaders, gets compiler errors (with YOUR line
numbers) and a PNG back, composes layers/post/text into JSON recipes, and renders seamless loops.

Register (Claude Code):
    claude mcp add funk2 -e FUNK_OUT=C:\\dev\\funkbox\\out -- C:\\dev\\funkbox\\.venv\\Scripts\\python.exe C:\\dev\\funkbox\\funk2_mcp.py
Env: FUNK_OUT (default <funkbox>/out), FUNK_INPUTS (default <funkbox>/inputs, created on start),
     FUNK_HOME (default <funkbox>), FUNK_GL_BACKEND (egl|default).

NOTHING may be printed to stdout here: stdout is the MCP protocol. Logs go to stderr.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Callable, Union

from PIL import Image as PILImage

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore

import funk
from funkbox.gl.context import GLUnavailable, gl_info as _gl_info
from funkbox.gl.encode import render_recipe
from funkbox.gl.engine import Engine
from funkbox.gl.loopcheck import validate_loop
from funkbox.gl.recipe import BLEND_MODES, LAYER_TYPES, Recipe2, resolve_recipe
from funkbox.config import funk_home, funk_inputs, funk_out
from funkbox.gl.shader import ShaderError, ShaderSource

Scalar = Union[str, int, float, bool]
NAME_RE = re.compile(r"^[a-z0-9_\-]{1,40}$")
OUT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
MAX_SIDE, MAX_SECONDS, MAX_FPS = 1080, 20.0, 60

INSTRUCTIONS = (
    "GPU shader art engine: you write GLSL fragment shaders (define `vec4 effect(vec2 p)` or "
    "Shadertoy `mainImage`), compose them with images/v1 effects into layers, then render "
    "seamless looping MP4/GIF. LOOP CONTRACT: animate ONLY through iPhase multiplied by an integer "
    "(or through loopNoise(p, radius)); iTime and non-integer multiples of iPhase break the loop, "
    "and write_shader/render will tell you. "
    "Workflow: list_looks, write_shader (compiler errors come back with your own line numbers), "
    "preview / contact_sheet to explore, render once it looks right. Every visual tool returns a PNG."
)

server = _Server("funk2", instructions=INSTRUCTIONS)


def _log(*a) -> None:
    print(*a, file=sys.stderr)


# --------------------------------------------------------------------------- #
# All GL work happens on ONE dedicated thread: GL contexts are thread-affine and the MCP
# runtime calls sync tools from a pool of worker threads.
# --------------------------------------------------------------------------- #
_GL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="funk-gl")
_ENGINES: dict[tuple[int, int], Engine] = {}


def on_gl(fn: Callable, *a, **k):
    return _GL.submit(lambda: fn(*a, **k)).result()


def _engine(w: int, h: int) -> Engine:      # call only on the GL thread
    w, h = w - w % 2, h - h % 2
    eng = _ENGINES.get((w, h))
    if eng is None:
        while len(_ENGINES) >= 3:
            _ENGINES.pop(next(iter(_ENGINES))).release()
        eng = _ENGINES[(w, h)] = Engine(w, h)
    return eng


def _png(img: PILImage.Image, max_side: int = 640) -> Image:
    img = img.copy()
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return Image(data=buf.getvalue(), format="png")


def _safe(fn):
    """Tools never raise: they return a readable one-line error instead of a traceback."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except GLUnavailable as e:
            return f"ERROR: no OpenGL context available. {e}"
        except ShaderError as e:
            return "ERROR: " + _shader_error_text(e)
        except (ValueError, KeyError, TypeError, FileNotFoundError, RuntimeError) as e:
            return f"ERROR: {type(e).__name__}: {e}"
        except Exception as e:  # noqa: BLE001
            _log(f"funk2_mcp: unexpected {type(e).__name__}: {e}")
            return f"ERROR: unexpected {type(e).__name__}: {e}"
    return wrapper


def _shader_error_text(e: ShaderError) -> str:
    return str(e)


def _fmt_errors(errs: list[dict], source: str | None = None) -> str:
    lines = source.splitlines() if source else []
    out = []
    for e in errs:
        ln = e["line"]
        txt = f"  line {ln}: {e['message']}" if ln else f"  {e['message']}"
        if ln and 1 <= ln <= len(lines):
            txt += f"\n      > {lines[ln - 1].strip()}"
        out.append(txt)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Recipe helpers
# --------------------------------------------------------------------------- #
def _parse_recipe(recipe: dict) -> Recipe2:
    if isinstance(recipe, str):
        recipe = json.loads(recipe)
    if not isinstance(recipe, dict):
        raise ValueError("recipe must be a JSON object")
    rec = Recipe2.from_dict(copy.deepcopy(recipe))
    if not rec.layers:
        raise ValueError("recipe needs at least one layer, e.g. "
                         '{"layers": [{"type": "shader", "shader": "shaders/plasma.frag"}]}')
    return rec


def _limits(rec: Recipe2) -> None:
    st = rec.settings
    if not (16 <= st.width <= MAX_SIDE and 16 <= st.height <= MAX_SIDE):
        raise ValueError(f"width/height must be 16..{MAX_SIDE}px (got {st.width}x{st.height})")
    if not (0 < st.seconds <= MAX_SECONDS):
        raise ValueError(f"seconds must be in (0, {MAX_SECONDS:g}]")
    if not (1 <= st.fps <= MAX_FPS):
        raise ValueError(f"fps must be 1..{MAX_FPS}")


def _fit(rec: Recipe2, size: int) -> tuple[int, int]:
    size = max(32, min(int(size), MAX_SIDE))
    w, h = rec.settings.width, rec.settings.height
    if w >= h:
        return size, max(2, round(size * h / w))
    return max(2, round(size * w / h)), size


def _summary(rec: Recipe2) -> str:
    r = resolve_recipe(rec)
    lines = [f"settings: {r.settings.width}x{r.settings.height} {r.settings.fps}fps "
             f"{r.settings.seconds:g}s seed={r.settings.seed}; palette={r.palette} cycles={r.cycles}"]
    for i, l in enumerate(r.layers):
        what = l.shader or l.path or l.effect or "inline"
        extra = f" params={json.dumps(l.params)}" if l.params else ""
        lines.append(f"layer {i}: {l.type} {what} blend={l.blend} opacity={l.opacity:g}{extra}")
    if r.post:
        lines.append("post: " + ", ".join(json.dumps(p.to_dict()) for p in r.post))
    if r.text and r.text.text:
        lines.append(f"text: {r.text.text!r}")
    return "\n".join(lines)


def _shaders_dir() -> Path:
    return funk_home() / "shaders"


def _shader_names() -> list[str]:
    return sorted(p.stem for p in _shaders_dir().glob("*.frag"))


def _shader_path(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad shader name {name!r}: use 1-40 chars of a-z 0-9 _ - (no paths, no dots)")
    return _shaders_dir() / f"{name}.frag"


def _loop_line(rep) -> str:
    return (f"loop check: {'OK' if rep.ok else 'FAIL'} - {rep.reason} "
            f"(bad_px={rep.bad_pixel_frac:.4f}, seam_step={rep.seam_step:.2f}, "
            f"typical_step={rep.typical_step:.2f})")


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@server.tool()
@_safe
def gl_info() -> str:
    """GPU renderer/version, bundled shader names and the output folders."""
    info = on_gl(_gl_info)
    return (f"GL_RENDERER: {info['GL_RENDERER']}\nGL_VERSION: {info['GL_VERSION']}\n"
            f"shaders: {', '.join(_shader_names())}\n"
            f"output dir: {funk_out()}\ninputs dir (put images here): {funk_inputs()}")


@server.tool()
@_safe
def list_looks() -> str:
    """Everything you can use: v2 shaders with their @param ranges, post passes, blend modes,
    layer types, plus the v1 presets/effects/palettes."""
    lines = ["== v2 SHADERS (shaders/<name>.frag; params are recipe layer `params`) =="]
    for n in _shader_names():
        try:
            s = ShaderSource.from_file(_shaders_dir() / f"{n}.frag")
        except Exception as e:  # noqa: BLE001
            lines.append(f"  {n}: unreadable ({e})")
            continue
        ps = []
        for p in s.params:
            if p.type == "color":
                ps.append(f"{p.name}=color {p.default}")
            else:
                ps.append(f"{p.name}={p.type} {p.min:g}..{p.max:g} (def {p.default:g})")
        note = " [no auto loop check]" if s.no_loopcheck else ""
        lines.append(f"  {n}{note}: " + ("; ".join(ps) if ps else "no params"))
    post_dir = Path(__file__).resolve().parent / "funkbox" / "gl" / "post"
    lines.append("== POST PASSES (recipe `post`: [{\"name\": ..., <param>: value}]) ==")
    for f in sorted(post_dir.glob("*.frag")):
        s = ShaderSource.from_file(f)
        lines.append(f"  {f.stem}: " + ("; ".join(f"{p.name[2:]} {p.min:g}..{p.max:g} (def {p.default:g})"
                                                  for p in s.params) or "no params"))
    lines.append("== BLEND MODES == " + ", ".join(BLEND_MODES))
    lines.append("== LAYER TYPES == " + ", ".join(LAYER_TYPES) +
                 "  (shader: shader|source; image: path, fit cover|contain|stretch, uv_warp; "
                 "v1: effect; all: blend, opacity; shader layers may list `channels` images -> iChannel1..3; "
                 "iChannel0 = everything below)")
    lines.append("== PALETTES == " + ", ".join(funk.PALETTES))
    lines.append("== SHADER CONTRACT == uniforms iResolution iLoop iPhase iTime iFrame iSeed iChannel0..3 "
                 "iAudio; helpers uv() pal() palette(x) loopNoise(p,r) snoise2/3/4 rot() wrap01() src0(p); "
                 "uv().y points UP; `uniform float u_x; // @param min max default` exposes a param; "
                 "ints named k_* must be integer multipliers of iPhase.")
    lines.append("== v1 (render_v1) == PRESETS: " + ", ".join(funk.PRESETS) +
                 " | EFFECTS: " + ", ".join(funk.EFFECTS))
    lines.append(f"inputs dir (put images here): {funk_inputs()}")
    return "\n".join(lines)


def _write_shader_gl(name: str, source: str, check: bool):
    src = ShaderSource(source, name=name)
    eng = _engine(384, 384)
    res = src.compile(eng.ctx)
    if not res.ok:
        return {"ok": False, "errors": res.errors, "src": src}
    from funkbox.gl.recipe import Layer, Settings2
    rec = Recipe2(settings=Settings2(width=384, height=384, seed=1), palette="vapor",
                  layers=[Layer(source=source)])
    rep = None
    if check and not src.no_loopcheck:
        rep = validate_loop(rec, size=160)
    arr = eng.render_at_t(rec, 0.25)
    return {"ok": True, "src": src, "rep": rep, "frame": arr}


@server.tool()
@_safe
def write_shader(name: str, source: str, check: bool = True) -> list:
    """Compile a GLSL fragment shader (no #version line; define `vec4 effect(vec2 p)` or Shadertoy
    `mainImage`), report errors in YOUR line numbers, run the seamless-loop check and return a
    preview still at t=0.25. Saved to shaders/<name>.frag only if it compiles (and, with
    check=True, loops). name: a-z 0-9 _ - up to 40 chars."""
    path = _shader_path(name)
    out = on_gl(_write_shader_gl, name, source, check)
    if not out["ok"]:
        return [f"COMPILE FAILED (line numbers are in your source):\n{_fmt_errors(out['errors'], source)}\nNot saved."]
    src, rep = out["src"], out["rep"]
    msgs = [f"compiled OK ({src.style} style, {len(src.params)} @param)"]
    save = True
    if src.no_loopcheck:
        msgs.append("loop check skipped (@noloopcheck marker): it loops only if the layers below do")
    elif not check:
        msgs.append("loop check skipped (check=false)")
    else:
        msgs.append(_loop_line(rep))
        if not rep.ok:
            save = False
        elif rep.reason.startswith("static"):
            msgs.append("WARNING: static - nothing moves. Animate with iPhase * integer or loopNoise().")
    if save:
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="\n")
        msgs.append(f"{'overwrote' if existed else 'saved'} shaders/{name}.frag")
    else:
        msgs.append("NOT saved: it does not loop. Fix it (animate with iPhase * integer, or loopNoise), "
                    "or call again with check=false to save anyway.")
    return ["\n".join(msgs), _png(PILImage.fromarray(out["frame"]), 384)]


@server.tool()
@_safe
def get_shader(name: str) -> str:
    """Return the source of shaders/<name>.frag."""
    p = _shader_path(name)
    if not p.exists():
        return f"ERROR: no shader named {name!r}. Have: {', '.join(_shader_names())}"
    return p.read_text(encoding="utf-8-sig")


def _preview_gl(rec: Recipe2, t: float, w: int, h: int):
    return _engine(w, h).render_at_t(rec, t)


@server.tool()
@_safe
def preview(recipe: dict, t: float = 0.25, size: int = 448) -> list:
    """Render one frame of a recipe at loop position t (0..1). Recipe: {"settings"?, "palette"?
    {"name","cycles"}, "layers": [...], "post"?, "text"?} - see list_looks. Fast; iterate here."""
    rec = _parse_recipe(recipe)
    w, h = _fit(rec, size)
    arr = on_gl(_preview_gl, rec, float(t), w, h)
    return [_summary(rec) + f"\nt={t:g}", _png(PILImage.fromarray(arr), max(w, h))]


def _sheet_gl(rec: Recipe2, seeds: list[int], t: float, w: int, h: int):
    eng = _engine(w, h)
    tiles = []
    for s in seeds:
        r = copy.deepcopy(rec)
        r.settings.seed = s
        for lay in r.layers:                     # resample seeded params per tile
            if lay.type in ("shader", "v1"):
                lay.params = None
        im = PILImage.fromarray(eng.render_at_t(r, t))
        from PIL import ImageDraw
        ImageDraw.Draw(im).text((8, 6), f"seed {s}", font=funk._font(max(12, eng.h // 16)),
                                fill="#ffffff", stroke_width=2, stroke_fill="#000000")
        tiles.append(im)
    return tiles, eng.w, eng.h


@server.tool()
@_safe
def contact_sheet(recipe: dict, seeds: str = "1-6") -> list:
    """Grid of stills of one recipe across seeds (e.g. "1-6" or "3,7,12"): the seed resamples every
    shader's @param values, so this is how you pick a variation. Returns the PNG and its saved path."""
    rec = _parse_recipe(recipe)
    seed_list = funk.parse_seeds(seeds)
    if not 1 <= len(seed_list) <= 16:
        raise ValueError("seeds: give between 1 and 16 seeds, e.g. '1-6'")
    cols = 3 if len(seed_list) > 2 else len(seed_list)
    w, h = _fit(rec, 300)
    tiles, tw, th = on_gl(_sheet_gl, rec, seed_list, 0.25, w, h)
    rows = -(-len(tiles) // cols)
    sheet = PILImage.new("RGB", (cols * tw, rows * th), "#000")
    for k, im in enumerate(tiles):
        sheet.paste(im, ((k % cols) * tw, (k // cols) * th))
    key = hashlib.sha1((json.dumps(rec.to_dict(), sort_keys=True) + seeds).encode()).hexdigest()[:8]
    path = funk_out() / f"sheet-{key}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return [f"saved {path}\nseeds: {seed_list}", _png(sheet, 900)]


def _render_gl(rec: Recipe2, out_dir: Path, name: str, gif: bool = False):
    eng = _engine(rec.settings.width, rec.settings.height)
    rep = None
    if not rec.settings.audio:
        rep = validate_loop(rec, size=160)
        if not rep.ok:
            return None, rep
    return render_recipe(rec, out_dir, name, gif=gif, engine=eng), rep


@server.tool()
@_safe
def render(recipe: dict, name: str = "", gif: bool = False) -> list:
    """Render a seamless loop (MP4 + PNG poster + JSON sidecar that fully reproduces it; GIF only if
    gif=True, since it is ~10x slower than the mp4 on busy loops).
    Limits: <=1080px per side, <=20 s, <=60 fps. Runs the loop check first and refuses a broken loop."""
    rec = _parse_recipe(recipe)
    _limits(rec)
    if not name:
        name = "funk-" + hashlib.sha1(json.dumps(rec.to_dict(), sort_keys=True).encode()).hexdigest()[:8]
    if not OUT_NAME_RE.match(name):
        raise ValueError("bad name: use 1-60 chars of A-Z a-z 0-9 _ -")
    res, rep = on_gl(_render_gl, rec, funk_out(), name, gif)
    if res is None:
        return [f"NOT RENDERED - the loop is not seamless.\n{_loop_line(rep)}"]
    lines = [res.timing_line(), *(f"{k}: {v}" for k, v in res.paths.items())]
    if rep is not None:
        lines.append(_loop_line(rep))
    else:
        lines.append("loop check skipped: audio is set, and audio loops are not seamless unless the music is")
    return ["\n".join(lines), _png(PILImage.open(res.paths["png"]))]


@server.tool()
@_safe
def render_v1(preset: str = "liquid-disco", seed: int = 1, seconds: float = 4.0, size: int = 540,
              fps: int = 30, text: str = "", overrides: dict[str, Scalar] | None = None,
              random: bool = False, gif: bool = True, name: str = "") -> list:
    """Render with the original CPU v1 engine (same arguments as the v1 server's render_loop)."""
    if random:
        rc = funk.random_recipe(seed)
    elif preset in funk.PRESETS:
        rc = replace(funk.PRESETS[preset])
    else:
        raise ValueError(f"unknown preset {preset!r}; have {sorted(funk.PRESETS)}")
    if text:
        rc = replace(rc, text=text, text_wobble=rc.text_wobble or 6.0)
    rc = funk.apply_overrides(rc, [f"{k}={v}" for k, v in (overrides or {}).items()])
    if not (16 <= size <= MAX_SIDE and 0 < seconds <= MAX_SECONDS and 1 <= fps <= MAX_FPS):
        raise ValueError(f"size 16..{MAX_SIDE}, seconds <= {MAX_SECONDS:g}, fps <= {MAX_FPS}")
    st = funk.Settings(width=size, height=size, fps=fps, seconds=seconds, seed=seed)
    label = "random" if random else preset
    out_name = name or f"v1-{label}-s{seed}"
    if not OUT_NAME_RE.match(out_name):
        raise ValueError("bad name: use 1-60 chars of A-Z a-z 0-9 _ -")
    paths = funk.render(rc, st, funk_out(), out_name, gif=gif)
    return ["\n".join(f"{k}: {v}" for k, v in paths.items()), _png(PILImage.open(paths["png"]))]


def main() -> None:
    funk_inputs().mkdir(parents=True, exist_ok=True)
    funk_out().mkdir(parents=True, exist_ok=True)
    server.run()


if __name__ == "__main__":
    main()
