"""Camera motion for still images: Ken Burns and 2.5D parallax, as one GL fragment pass.

The camera model lives here twice on purpose: in the shader (per pixel) and in numpy
(``backward`` / ``forward``) so a product sprite can be locked to the same camera at its depth.

Frame coordinates are fractions of the frame, origin top-left, y down. For an output point p
at depth d the source-frame point q it shows is

    Ken Burns:  q = c + (p - c) / zoom + pan
    parallax :  q = c + (p - c) / s(d) + d * shift,   s(d) = base * (1 + push * t * (0.5 + d))

with c = (0.5, 0.5). Parallax always runs at ``base`` = 1.07 so that the offset (<= 3% of the
frame width) can never reveal anything outside the source.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import moderngl
import numpy as np
from PIL import Image

from funkbox.gl.context import create_context

from . import depth as depthmod
from .schema import Motion

MAX_REVEAL = 0.03                       # max horizontal camera offset, fraction of frame width
PARALLAX_BASE = 1.0 + 2 * MAX_REVEAL + 0.01
DEPTH_SIDE = 512                        # depth is stored on the GPU at <= this many px per side
MAX_LOD = 4.0
_WARNED: set[str] = set()


def _warn(msg: str) -> None:
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(f"reel: warning: {msg}", file=sys.stderr)


def smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


# --------------------------------------------------------------------------- #
# Camera (pure python / numpy)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Camera:
    mode: str = "kenburns"            # kenburns | parallax
    zoom: float = 1.0                 # kenburns
    pan: tuple[float, float] = (0.0, 0.0)
    base: float = 1.0                 # parallax
    push: float = 0.0                 # push * t
    shift: tuple[float, float] = (0.0, 0.0)
    dof: float = 0.0

    def scale(self, d: float | np.ndarray) -> float | np.ndarray:
        """Zoom factor applied to content at depth d."""
        if self.mode == "parallax":
            return self.base * (1.0 + self.push * (0.5 + d))
        return self.zoom

    def offset(self, d: float | np.ndarray):
        if self.mode == "parallax":
            return d * self.shift[0], d * self.shift[1]
        return self.pan

    def backward(self, p: np.ndarray, d: float | np.ndarray = 0.5) -> np.ndarray:
        """Output-frame points -> source-frame points. p: (...,2)."""
        c = np.array([0.5, 0.5])
        s = np.asarray(self.scale(d))[..., None] if np.ndim(self.scale(d)) else self.scale(d)
        ox, oy = self.offset(d)
        off = np.stack(np.broadcast_arrays(ox, oy), axis=-1)
        return c + (p - c) / s + off

    def forward(self, q: tuple[float, float], d: float) -> tuple[tuple[float, float], float]:
        """Where a world point q (frame fractions) at depth d lands, plus its scale factor."""
        s = float(self.scale(d))
        ox, oy = self.offset(d)
        return (0.5 + (q[0] - 0.5 - ox) * s, 0.5 + (q[1] - 0.5 - oy) * s), s


def camera(motion: Motion, t: float, mode: str | None = None) -> Camera:
    """Camera for normalised shot time t in [0, 1]. ``mode`` overrides ``motion.type``
    (used to degrade parallax to kenburns when no depth is available)."""
    t = min(1.0, max(0.0, t))
    kind = mode or motion.type
    if kind == "none":
        return Camera("kenburns", 1.0, (0.0, 0.0))
    if kind == "kenburns":
        e = smoothstep(t)
        z0, z1 = motion.zoom
        z = z0 + (z1 - z0) * e
        (x0, y0), (x1, y1) = motion.pan
        px, py = x0 + (x1 - x0) * e, y0 + (y1 - y0) * e
        lim = 0.5 - 0.5 / z                      # keep the window inside the source
        cx, cy = max(-lim, min(lim, px)), max(-lim, min(lim, py))
        if (cx, cy) != (px, py):
            _warn(f"Ken Burns pan {motion.pan} would reveal outside the image at zoom {z:.2f}; clamped")
        return Camera("kenburns", z, (cx, cy))
    # parallax
    amp = math.tan(math.radians(abs(motion.orbit_deg))) * 0.35
    if amp > MAX_REVEAL:
        _warn(f"orbit_deg={motion.orbit_deg} asks for {amp:.3f} of the frame width; clamped to {MAX_REVEAL}")
        amp = MAX_REVEAL
    sx = math.copysign(amp, motion.orbit_deg) * (2.0 * smoothstep(t) - 1.0)
    return Camera("parallax", base=PARALLAX_BASE, push=motion.push * t, shift=(sx, 0.0), dof=motion.dof)


def max_source_extent(cam_fn, samples: int = 41) -> float:
    """Largest |q - c| (per axis) the camera can ever sample over the shot; must be <= 0.5."""
    worst = 0.0
    corners = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], dtype=np.float64)
    for t in np.linspace(0, 1, samples):
        cam = cam_fn(float(t))
        for d in np.linspace(0, 1, 5):
            q = cam.backward(corners, d)
            worst = max(worst, float(np.abs(q - 0.5).max()))
    return worst


# --------------------------------------------------------------------------- #
# GL pass
# --------------------------------------------------------------------------- #
_VERT = "#version 330\nin vec2 p;\nvoid main() { gl_Position = vec4(p, 0.0, 1.0); }\n"
_FRAG = """#version 330
uniform sampler2D u_src;
uniform sampler2D u_depth;
uniform vec2  u_res;
uniform vec2  u_img;
uniform int   u_mode;      // 0 kenburns, 1 parallax
uniform float u_zoom;
uniform vec2  u_pan;
uniform float u_base;
uniform float u_push;
uniform vec2  u_shift;
uniform float u_dof;
out vec4 frag;

vec2 cover(vec2 f) {                       // frame -> source (both y down, 0..1)
  float sa = u_res.x / u_res.y, ia = u_img.x / u_img.y;
  vec2 q = f;
  if (ia < sa) q.y = (f.y - 0.5) * (ia / sa) + 0.5;
  else         q.x = (f.x - 0.5) * (sa / ia) + 0.5;
  return q;
}

void main() {
  vec2 f = vec2(gl_FragCoord.x / u_res.x, 1.0 - gl_FragCoord.y / u_res.y);
  vec2 c = vec2(0.5);
  vec2 q;
  float bias = 0.0;
  if (u_mode == 0) {
    q = c + (f - c) / u_zoom + u_pan;
  } else {
    float d = texture(u_depth, cover(f)).r;
    float s = u_base * (1.0 + u_push * (0.5 + d));
    q = c + (f - c) / s + d * u_shift;
    bias = (1.0 - d) * u_dof * %MAXLOD%;
  }
  vec2 t = cover(q);
  bool inside = all(greaterThanEqual(t, vec2(0.0))) && all(lessThanEqual(t, vec2(1.0)));
  frag = vec4(texture(u_src, t, bias).rgb, inside ? 1.0 : 0.0);
}
""".replace("%MAXLOD%", f"{MAX_LOD:.1f}")


@dataclass
class SourceTex:
    tex: moderngl.Texture
    depth: moderngl.Texture | None
    size: tuple[int, int]


class MotionGL:
    """Renders still images through a camera. One per (width, height); shares funkgl's context."""

    def __init__(self, width: int, height: int, ctx: moderngl.Context | None = None):
        self.ctx = ctx or create_context()
        self.w, self.h = int(width), int(height)
        c = self.ctx
        self.prog = c.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        self.vbo = c.buffer(np.array([-1, -1, 3, -1, -1, 3], dtype="f4").tobytes())
        self.vao = c.vertex_array(self.prog, [(self.vbo, "2f", "p")])
        self.out = c.texture((self.w, self.h), 4)
        self.fbo = c.framebuffer(color_attachments=[self.out])
        self._dummy_depth = c.texture((1, 1), 1, np.array([0.5], dtype="f4").tobytes(), dtype="f4")
        self._cache: dict[tuple, SourceTex] = {}

    # ---- sources
    def upload(self, rgb: np.ndarray, depth: np.ndarray | None = None) -> SourceTex:
        """rgb: HxWx3 uint8 (top row first). depth: HxW float32 in 0..1 or None."""
        h, w = rgb.shape[:2]
        tex = self.ctx.texture((w, h), 3, np.ascontiguousarray(rgb).tobytes())
        tex.build_mipmaps()
        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        dtex = None
        if depth is not None:
            dh, dw = depth.shape
            sc = min(1.0, DEPTH_SIDE / max(dh, dw))
            d = depth
            if sc < 1.0:
                d = np.asarray(Image.fromarray(depth.astype(np.float32), mode="F").resize(
                    (max(2, round(dw * sc)), max(2, round(dh * sc))), Image.BILINEAR), dtype=np.float32)
            d = depthmod.blur_depth(d)
            dtex = self.ctx.texture((d.shape[1], d.shape[0]), 1, np.ascontiguousarray(d, dtype="f4").tobytes(),
                                    dtype="f4")
            dtex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            dtex.repeat_x = dtex.repeat_y = False
        return SourceTex(tex, dtex, (w, h))

    def load(self, path: Path, depth: np.ndarray | None) -> SourceTex:
        key = (str(path), path.stat().st_mtime_ns, depth is not None, None if depth is None else depth.shape)
        hit = self._cache.get(key)
        if hit is None:
            while len(self._cache) >= 24:
                self._drop(self._cache.pop(next(iter(self._cache))))
            im = Image.open(path).convert("RGB")
            hit = self._cache[key] = self.upload(np.asarray(im), depth)
        return hit

    @staticmethod
    def _drop(s: SourceTex) -> None:
        s.tex.release()
        if s.depth is not None:
            s.depth.release()

    # ---- render
    def render(self, src: SourceTex, cam: Camera) -> np.ndarray:
        """Returns HxWx4 uint8: rgb plus alpha = 255 where the sample was inside the source."""
        prog = self.prog
        parallax = cam.mode == "parallax"
        if parallax and src.depth is None:
            raise ValueError("parallax needs a depth map; degrade to kenburns instead")
        prog["u_src"].value = 0
        prog["u_depth"].value = 1
        prog["u_res"].value = (float(self.w), float(self.h))
        prog["u_img"].value = (float(src.size[0]), float(src.size[1]))
        prog["u_mode"].value = 1 if parallax else 0
        prog["u_zoom"].value = float(cam.zoom)
        prog["u_pan"].value = (float(cam.pan[0]), float(cam.pan[1]))
        prog["u_base"].value = float(cam.base)
        prog["u_push"].value = float(cam.push)
        prog["u_shift"].value = (float(cam.shift[0]), float(cam.shift[1]))
        prog["u_dof"].value = float(cam.dof)
        src.tex.use(0)
        (src.depth or self._dummy_depth).use(1)
        self.fbo.use()
        self.ctx.viewport = (0, 0, self.w, self.h)
        self.ctx.disable(moderngl.BLEND)
        self.vao.render(moderngl.TRIANGLES, vertices=3)
        raw = self.fbo.read(components=4, alignment=1)
        return np.ascontiguousarray(np.frombuffer(raw, np.uint8).reshape(self.h, self.w, 4)[::-1])

    def release(self) -> None:
        for s in self._cache.values():
            self._drop(s)
        self._cache.clear()
        for o in (self.vao, self.vbo, self.fbo, self.out, self._dummy_depth, self.prog):
            try:
                o.release()
            except Exception:  # noqa: BLE001
                pass
