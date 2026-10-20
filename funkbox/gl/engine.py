"""GPU engine: renders layers + post passes into numpy frames."""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import moderngl
import numpy as np
from PIL import Image

from funkbox.config import resolve_path

from . import audio as _audio
from .context import create_context
from .recipe import BLEND_MODES, PALETTES, Layer, Recipe2, recipe_digest, resolve_recipe
from .shader import ShaderError, ShaderSource, hex_to_rgb
from .text import draw_text

_DIR = Path(__file__).resolve().parent
_BLEND_FRAG = "#version 330\n" + (_DIR / "blend.glsl").read_text(encoding="utf-8")
_VERT = "#version 330\nin vec2 p;\nvoid main() { gl_Position = vec4(p, 0.0, 1.0); }\n"
_FIT = {"cover": 0, "contain": 1, "stretch": 2}

_IMAGE_SRC = """
uniform int   u_fit;     // 0 cover, 1 contain, 2 stretch
uniform float u_warp;
uniform vec2  u_img;     // image size in pixels
vec4 effect(vec2 p) {
  p += 0.02 * u_warp * vec2(sin(3.0 * p.y + iPhase), cos(3.0 * p.x + iPhase));
  vec2 t = toTex(p);
  float sa = iResolution.x / iResolution.y, ia = u_img.x / u_img.y;
  if (u_fit != 2) {
    bool xFull = (u_fit == 1) ? (ia > sa) : (ia < sa);
    if (xFull) t.y = (t.y - 0.5) * (ia / sa) + 0.5;
    else       t.x = (t.x - 0.5) * (sa / ia) + 0.5;
  }
  vec4 c = texture(iChannel1, t);
  bool inside = t.x >= 0.0 && t.x <= 1.0 && t.y >= 0.0 && t.y <= 1.0;
  return inside ? c : vec4(0.0);
}
"""


def _log(*a) -> None:
    print(*a, file=sys.stderr)


@dataclass
class _Prog:
    prog: moderngl.Program
    vao: moderngl.VertexArray
    src: ShaderSource


@dataclass
class _Prepared:
    recipe: Recipe2                      # resolved
    layer_progs: list[_Prog | None] = field(default_factory=list)
    layer_params: list[dict] = field(default_factory=list)
    post_progs: list[tuple[str, _Prog, dict]] = field(default_factory=list)


def _set(prog: moderngl.Program, name: str, value) -> None:
    if name in prog:
        prog[name].value = value


class Engine:
    def __init__(self, width: int, height: int, ctx: moderngl.Context | None = None):
        self.ctx = ctx or create_context()
        self.w = int(width) - int(width) % 2
        self.h = int(height) - int(height) % 2
        if self.w < 2 or self.h < 2:
            raise ValueError("width and height must be at least 2")
        c = self.ctx
        self._vbo = c.buffer(np.array([-1, -1, 3, -1, -1, 3], dtype="f4").tobytes())
        self._tex: list[moderngl.Texture] = []
        self._fbo: list[moderngl.Framebuffer] = []
        for _ in range(3):                      # A, B (ping-pong accumulation), L (layer)
            t = c.texture((self.w, self.h), 4)
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = t.repeat_y = False
            self._tex.append(t)
            self._fbo.append(c.framebuffer(color_attachments=[t]))
        self._dummy = c.texture((1, 1), 4, b"\x00\x00\x00\xff")
        self._blend = self._mk(_BLEND_FRAG)
        self._prog_cache: dict[str, _Prog] = {}
        self._img_cache: dict[tuple, tuple[moderngl.Texture, tuple[int, int]]] = {}
        self._prepared: dict[str, _Prepared] = {}
        self._audio = None
        self._audio_cache: dict[tuple, _audio.AudioFeatures] = {}
        self._spec_tex: moderngl.Texture | None = None
        c.disable(moderngl.BLEND)
        c.disable(moderngl.DEPTH_TEST)

    # ----------------------------------------------------------------- setup
    def _mk(self, frag: str) -> tuple[moderngl.Program, moderngl.VertexArray]:
        prog = self.ctx.program(vertex_shader=_VERT, fragment_shader=frag)
        vao = self.ctx.vertex_array(prog, [(self._vbo, "2f", "p")])
        return prog, vao

    def _compile(self, src: ShaderSource, what: str) -> _Prog:
        key = src.sha256 + ":" + what
        hit = self._prog_cache.get(key)
        if hit:
            return hit
        res = src.compile(self.ctx)
        if not res.ok:
            msg = "; ".join((f"line {e['line']}: " if e["line"] else "") + e["message"]
                            for e in res.errors)
            err = ShaderError(f"{what} failed to compile: {msg}")
            err.errors = res.errors            # type: ignore[attr-defined]
            raise err
        assert res.program is not None
        vao = self.ctx.vertex_array(res.program, [(self._vbo, "2f", "p")])
        p = _Prog(res.program, vao, src)
        self._prog_cache[key] = p
        return p

    def prepare(self, recipe: Recipe2) -> _Prepared:
        key = recipe_digest(recipe)
        hit = self._prepared.get(key)
        if hit:
            return hit
        rr = resolve_recipe(recipe)
        prep = _Prepared(rr)
        for i, lay in enumerate(rr.layers):
            if lay.type == "shader":
                src = ShaderSource(lay.source or "", name=lay.shader or f"layer{i}")
                prep.layer_progs.append(self._compile(src, f"layer {i} ({lay.shader or 'inline'})"))
                prep.layer_params.append(lay.params or {})
            elif lay.type == "image":
                prep.layer_progs.append(self._compile(ShaderSource(_IMAGE_SRC), "image program"))
                prep.layer_params.append({})
        for pp in rr.post:
            f = _DIR / "post" / f"{pp.name}.frag"
            if not f.exists():
                names = sorted(x.stem for x in (_DIR / "post").glob("*.frag"))
                raise ValueError(f"unknown post pass {pp.name!r}; have {names}")
            src = ShaderSource.from_file(f)
            valid = {p.name for p in src.params}
            vals = {p.name: p.default for p in src.params}
            for k, v in pp.params.items():
                kk = k if k.startswith("u_") else "u_" + k
                if kk not in valid:
                    raise ValueError(f"post pass {pp.name!r} has no parameter {k!r}; "
                                     f"have {sorted(v_[2:] for v_ in valid)}")
                vals[kk] = v
            prep.post_progs.append((pp.name, self._compile(src, f"post pass {pp.name}"), vals))
        if len(self._prepared) > 16:
            self._prepared.clear()
        self._prepared[key] = prep
        return prep

    # --------------------------------------------------------------- textures
    def _load_image(self, path: str, base_dir) -> tuple[moderngl.Texture, tuple[int, int]]:
        p = resolve_path(path, base_dir)
        key = (str(p), p.stat().st_mtime_ns)
        hit = self._img_cache.get(key)
        if hit:
            return hit
        im = Image.open(p).convert("RGBA")
        out = self._upload(np.asarray(im), 4)
        res = (out, im.size)
        for k in [k for k in self._img_cache if k[0] == key[0]]:    # stale mtime
            self._img_cache.pop(k)[0].release()
        self._img_cache[key] = res
        return res

    def _upload(self, arr: np.ndarray, comps: int, tex: moderngl.Texture | None = None):
        arr = np.ascontiguousarray(arr[::-1])        # GL row 0 is the bottom
        h, w = arr.shape[:2]
        if tex is None or tex.size != (w, h):
            if tex is not None:
                tex.release()
            tex = self.ctx.texture((w, h), comps)
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.write(arr.tobytes())
        return tex

    # ---------------------------------------------------------------- uniforms
    def _common(self, prog, rec: Recipe2, t: float, frame: int, lay: Layer | None = None) -> None:
        st = rec.settings
        x = lay.extra if lay is not None else {}      # a layer may carry its own palette / cycles
        a, b, c, d = (tuple(map(float, q)) for q in PALETTES[x.get("palette", rec.palette)])
        _set(prog, "iResolution", (float(self.w), float(self.h), 1.0))
        _set(prog, "iLoop", float(t))
        _set(prog, "iPhase", float(2.0 * math.pi * t))
        _set(prog, "iTime", float(t * st.seconds))
        _set(prog, "iFrame", int(frame))
        _set(prog, "iSeed", float(st.seed))
        _set(prog, "u_frames", int(st.frames))
        _set(prog, "u_pal_a", a)
        _set(prog, "u_pal_b", b)
        _set(prog, "u_pal_c", c)
        _set(prog, "u_pal_d", d)
        _set(prog, "u_pal_cycles", int(x.get("cycles", rec.cycles)))
        _set(prog, "iAudio", self._audio_vec(frame))
        for i in range(4):
            _set(prog, f"iChannel{i}", i)

    def _audio_for(self, rec: Recipe2):
        """Analyse settings.audio once per (file, fps, frames); None when no audio is set."""
        st = rec.settings
        if not st.audio:
            return None
        p = resolve_path(st.audio, rec.base_dir)
        key = (str(p), p.stat().st_mtime_ns, st.fps, st.frames)
        feats = self._audio_cache.get(key)
        if feats is None:
            self._audio_cache.clear()
            feats = self._audio_cache[key] = _audio.analyze(p, st.fps, st.frames)
        return feats

    def _audio_vec(self, frame: int) -> tuple[float, float, float, float]:
        if self._audio is None:
            return (0.0, 0.0, 0.0, 0.0)
        return self._audio.vec(frame)

    def _set_params(self, prog, params: dict, declared) -> None:
        for p in declared:
            v = params.get(p.name, p.default)
            if p.type == "color":
                _set(prog, p.name, hex_to_rgb(str(v)))
            elif p.type == "int":
                _set(prog, p.name, int(v))
            else:
                _set(prog, p.name, float(v))

    def _bind(self, units: dict[int, moderngl.Texture | None], frame: int = 0) -> None:
        if self._audio is not None:                       # iChannel3 = 512x1 log-spaced spectrum
            if self._spec_tex is None:
                self._spec_tex = self.ctx.texture((_audio.SPEC_BINS, 1), 1, dtype="f4")
                self._spec_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                self._spec_tex.repeat_x = self._spec_tex.repeat_y = False
            self._spec_tex.write(np.ascontiguousarray(self._audio.spec_row(frame), dtype="f4").tobytes())
            units = {**units, 3: self._spec_tex}
        for u in range(4):
            (units.get(u) or self._dummy).use(location=u)

    def _draw(self, p: _Prog, target: int) -> None:
        self._fbo[target].use()
        self.ctx.viewport = (0, 0, self.w, self.h)
        p.vao.render(moderngl.TRIANGLES, vertices=3)

    # ------------------------------------------------------------------ render
    def render(self, recipe: Recipe2, frame: int, grain: bool = True) -> np.ndarray:
        frames = recipe.settings.frames
        return self._render(recipe, frame / frames, int(frame), grain)

    def render_at_t(self, recipe: Recipe2, t: float, grain: bool = True) -> np.ndarray:
        frame = int(round(t * recipe.settings.frames))
        return self._render(recipe, float(t), frame, grain)

    def _render(self, recipe: Recipe2, t: float, frame: int, grain: bool) -> np.ndarray:
        prep = self.prepare(recipe)
        rec = prep.recipe
        self._audio = self._audio_for(rec)
        if not rec.layers:
            raise ValueError("recipe has no layers")
        A, B, L = 0, 1, 2
        self._fbo[A].use()
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)

        for i, lay in enumerate(rec.layers):
            p = prep.layer_progs[i]
            assert p is not None
            self._common(p.prog, rec, t, frame, lay)
            if lay.type == "shader":
                chans: dict[int, moderngl.Texture | None] = {0: self._tex[A]}
                for k, cp in enumerate(lay.channels[:3]):
                    chans[k + 1] = self._load_image(cp, rec.base_dir)[0]
                self._bind(chans, frame)
                self._set_params(p.prog, prep.layer_params[i], p.src.params)
            else:                                      # image layer
                tex, size = self._load_image(lay.path or "", rec.base_dir)
                fit, warp = _FIT.get(lay.fit, 0), float(lay.uv_warp)
                self._bind({1: tex}, frame)
                _set(p.prog, "u_fit", fit)
                _set(p.prog, "u_warp", warp)
                _set(p.prog, "u_img", (float(size[0]), float(size[1])))
            self._draw(p, L)
            # composite L over A -> B
            bprog, bvao = self._blend
            self._tex[A].use(location=0)
            self._tex[L].use(location=1)
            bprog["u_base"].value = 0
            bprog["u_src"].value = 1
            bprog["u_mode"].value = BLEND_MODES.index(lay.blend)
            bprog["u_opacity"].value = float(lay.opacity)
            self._fbo[B].use()
            bvao.render(moderngl.TRIANGLES, vertices=3)
            A, B = B, A

        for name, p, vals in prep.post_progs:
            if name == "grain" and not grain:
                continue
            self._common(p.prog, rec, t, frame)
            self._bind({0: self._tex[A]}, frame)
            self._set_params(p.prog, vals, p.src.params)
            self._draw(p, B)
            A, B = B, A

        raw = self._fbo[A].read(components=3, alignment=1)
        arr = np.frombuffer(raw, np.uint8).reshape(self.h, self.w, 3)[::-1]
        arr = np.ascontiguousarray(arr)
        if rec.text and rec.text.text:
            arr = np.ascontiguousarray(draw_text(arr, 2.0 * math.pi * t, rec.text))
        return arr

    def release(self) -> None:
        for o in [*self._fbo, *self._tex, self._dummy, self._vbo]:
            try:
                o.release()
            except Exception:  # noqa: BLE001
                pass
        for t, _ in self._img_cache.values():
            t.release()
        if self._spec_tex is not None:
            self._spec_tex.release()
        for pr in self._prog_cache.values():
            pr.vao.release()
            pr.prog.release()
        self._blend[1].release()
        self._blend[0].release()
        self._img_cache.clear()
        self._prog_cache.clear()
        self._prepared.clear()
