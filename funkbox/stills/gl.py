"""GPU side of the stills renderer: alpha-aware layer compositing, per-layer colour/filter passes and the
poster-level post passes. Everything reuses funkgl: the blend functions of ``funkgl/blend.glsl``, the shader
prelude and ``Engine`` (programs, uniforms, the post pass chain, shader layers).
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import moderngl
import numpy as np

from funkbox.gl import engine as _engine_mod
from funkbox.gl.engine import Engine
from funkbox.gl.recipe import BLEND_MODES, Recipe2, Settings2
from funkbox.gl.shader import ShaderSource

PASSES = Path(__file__).resolve().parent / "passes"
POST = Path(_engine_mod.__file__).resolve().parent / "post"

# funkgl's blend functions (blendRGB and friends) + a main() that also handles a base with alpha < 1.
# With an opaque base it is exactly the engine's `mix(base, blend(base, src), opacity * src.a)`.
_BLEND_FUNCS = _engine_mod._BLEND_FRAG.split("void main()")[0]
_BLEND_MAIN = """
void main() {
  ivec2 c = ivec2(gl_FragCoord.xy);
  vec4 b = texelFetch(u_base, c, 0);
  vec4 s = texelFetch(u_src, c, 0);
  float sa = clamp(u_opacity, 0.0, 1.0) * s.a;
  vec3 mixed = (1.0 - b.a) * s.rgb + b.a * blendRGB(b.rgb, s.rgb);
  float ra = sa + b.a * (1.0 - sa);
  vec3 rc = ra > 0.0 ? (sa * mixed + (1.0 - sa) * b.a * b.rgb) / ra : vec3(0.0);
  fragColor = vec4(rc, ra);
}
"""


class Buf:
    """An RGBA8 texture with a framebuffer; ``upload``/``read`` take top-row-first numpy arrays."""

    def __init__(self, ctx: moderngl.Context, w: int, h: int):
        self.w, self.h = w, h
        self.tex = ctx.texture((w, h), 4)
        self.tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.tex.repeat_x = self.tex.repeat_y = False
        self.fbo = ctx.framebuffer(color_attachments=[self.tex])

    def upload(self, rgba: np.ndarray) -> None:
        assert rgba.shape == (self.h, self.w, 4) and rgba.dtype == np.uint8, (rgba.shape, rgba.dtype)
        self.tex.write(np.ascontiguousarray(rgba[::-1]).tobytes())

    def clear(self) -> None:
        self.fbo.use()
        self.fbo.clear(0.0, 0.0, 0.0, 0.0)

    def read(self) -> np.ndarray:
        raw = self.fbo.read(components=4, alignment=1)
        return np.ascontiguousarray(np.frombuffer(raw, np.uint8).reshape(self.h, self.w, 4)[::-1])

    def release(self) -> None:
        self.fbo.release()
        self.tex.release()


class Step:
    """One fx pass: a .frag file, values for its @param uniforms (keyed u_<name>), raw uniforms, and whether the
    pass forces alpha to 1 (funkgl post passes do) so the layer's own alpha must be restored afterwards."""

    def __init__(self, frag: Path, values: dict | None = None, extra: dict | None = None, restore: bool = False):
        self.frag, self.values, self.extra, self.restore = frag, values or {}, extra or {}, restore


class GL:
    """Buffers and programs for one canvas size. GL objects are thread-affine: use from one thread only."""

    def __init__(self, w: int, h: int):
        self.eng = Engine(w, h)
        self.ctx = self.eng.ctx
        self.w, self.h = self.eng.w, self.eng.h
        self.blend = self.eng._mk(_BLEND_FUNCS + _BLEND_MAIN)
        self._rec = Recipe2(settings=Settings2(width=self.w, height=self.h, seed=1, seconds=1.0, fps=1))
        self._pool: list[Buf] = []
        self._progs: dict[str, object] = {}

    # ---- buffers
    def acquire(self) -> Buf:
        return self._pool.pop() if self._pool else Buf(self.ctx, self.w, self.h)

    def give_back(self, *bufs: Buf) -> None:
        self._pool.extend(bufs)
        while len(self._pool) > 8:
            self._pool.pop().release()

    # ---- programs
    def _prog(self, frag: Path):
        key = str(frag)
        p = self._progs.get(key)
        if p is None:
            p = self._progs[key] = self.eng._compile(ShaderSource.from_file(frag), f"stills pass {frag.stem}")
        return p

    def run(self, frag: Path, inputs: dict[int, Buf], out: Buf, values: dict | None = None,
            extra: dict | None = None) -> None:
        """Draw one fullscreen pass of ``frag`` reading ``inputs`` (iChannelN) into ``out``."""
        p = self._prog(frag)
        eng = self.eng
        eng._common(p.prog, self._rec, 0.0, 0)
        eng._bind({k: b.tex for k, b in inputs.items()}, 0)
        vals = {p_.name: p_.default for p_ in p.src.params}
        vals.update(values or {})
        eng._set_params(p.prog, vals, p.src.params)
        for k, v in (extra or {}).items():
            if k in p.prog:
                p.prog[k].value = v
        out.fbo.use()
        self.ctx.viewport = (0, 0, self.w, self.h)
        p.vao.render(moderngl.TRIANGLES, vertices=3)

    # ---- per-layer fx
    def apply(self, rgba: np.ndarray, steps: list[Step]) -> np.ndarray:
        """Run ``steps`` over an RGBA image and return the result (unchanged copy when there are none)."""
        if not steps:
            return rgba
        cur = self.acquire()
        cur.upload(rgba)
        held = [cur]
        try:
            for st in steps:
                out = self.acquire()
                held.append(out)
                self.run(st.frag, {0: cur}, out, st.values, st.extra)
                if st.restore:
                    third = self.acquire()
                    held.append(third)
                    self.run(PASSES / "restore_alpha.frag", {0: out, 1: cur}, third)
                    out = third
                cur = out
            return cur.read()
        finally:
            self.give_back(*held)

    # ---- compositing
    def composite(self, base: np.ndarray | None = None) -> "Comp":
        return Comp(self, base)

    # ---- poster-level post passes (funkgl/post/*.frag through the Engine's own chain)
    def post(self, rgb: np.ndarray, passes: list[dict], seed: int) -> np.ndarray:
        if not passes:
            return rgb
        from funkbox.gl.recipe import PostPass
        rec = Recipe2(settings=Settings2(width=self.w, height=self.h, seed=seed, seconds=1.0, fps=1), layers=[],
                      post=[PostPass.from_dict(dict(p)) for p in passes])
        eng = self.eng
        prep = eng.prepare(rec)
        a, b = 0, 1
        rgba = np.concatenate([rgb, np.full(rgb.shape[:2] + (1,), 255, np.uint8)], axis=-1)
        eng._tex[a].write(np.ascontiguousarray(rgba[::-1]).tobytes())
        for _name, p, vals in prep.post_progs:
            eng._common(p.prog, prep.recipe, 0.0, 0)
            eng._bind({0: eng._tex[a]}, 0)
            eng._set_params(p.prog, vals, p.src.params)
            eng._draw(p, b)
            a, b = b, a
        raw = eng._fbo[a].read(components=3, alignment=1)
        return np.ascontiguousarray(np.frombuffer(raw, np.uint8).reshape(self.h, self.w, 3)[::-1])

    # ---- shader layers (funk2 recipes frozen at t)
    def render_recipe(self, rec: Recipe2, t: float, grain: bool = False) -> np.ndarray:
        return self.eng.render_at_t(rec, float(t), grain=grain)

    def release(self) -> None:
        for b in self._pool:
            b.release()
        self._pool.clear()
        self.blend[1].release()
        self.blend[0].release()
        self.eng.release()


class Comp:
    """Ping-pong accumulation of layers with funkgl's blend modes; straight alpha, any base alpha."""

    def __init__(self, gl: GL, base: np.ndarray | None):
        self.gl = gl
        self.a, self.b, self.s = gl.acquire(), gl.acquire(), gl.acquire()
        if base is None:
            self.a.clear()
        else:
            self.a.upload(base)

    def add(self, rgba: np.ndarray, blend: str = "normal", opacity: float = 1.0) -> None:
        gl = self.gl
        self.s.upload(rgba)
        prog, vao = gl.blend
        self.a.tex.use(location=0)
        self.s.tex.use(location=1)
        prog["u_base"].value = 0
        prog["u_src"].value = 1
        prog["u_mode"].value = BLEND_MODES.index(blend)
        prog["u_opacity"].value = float(opacity)
        self.b.fbo.use()
        gl.ctx.viewport = (0, 0, gl.w, gl.h)
        vao.render(moderngl.TRIANGLES, vertices=3)
        self.a, self.b = self.b, self.a

    def read(self) -> np.ndarray:
        return self.a.read()

    def close(self) -> None:
        self.gl.give_back(self.a, self.b, self.s)


# --------------------------------------------------------------------------- #
_GLS: "OrderedDict[tuple[int, int], GL]" = OrderedDict()


def get_gl(w: int, h: int) -> GL:
    """A cached GL for this (even) size; at most two are kept alive."""
    w, h = w - w % 2, h - h % 2
    key = (w, h)
    gl = _GLS.get(key)
    if gl is None:
        while len(_GLS) >= 2:
            _GLS.popitem(last=False)[1].release()
        gl = _GLS[key] = GL(w, h)
    else:
        _GLS.move_to_end(key)
    return gl


def release_all() -> None:
    while _GLS:
        _GLS.popitem(last=False)[1].release()
