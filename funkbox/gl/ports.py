"""GLSL source of the six classic effects (plasma, moire, warp, tunnel, blobs, rings), written to
shaders/ by `funkbox loop write-shader --ports`. They began as ports of the CPU engine that v4 retired.

Conventions: the old (u, v) has +v pointing DOWN the image, uv() has +y pointing UP, so every
effect starts with `vec2 q = xform(vec2(p.x, -p.y), th)` to keep the original maths. `th` is
iPhase; every use of it is multiplied by an integer so the loop is seamless.

Every effect also takes three look controls (`fixed` params: never sampled from the seed, off by
default): u_kaleido (mirror segments, 0 = off), k_spin (turns per loop) and u_zoom (breathing zoom).
"""
from __future__ import annotations

_XFORM = """
uniform int   u_kaleido;  // @param 0 12 0 fixed
uniform int   k_spin;     // @param -3 3 0 fixed
uniform float u_zoom;     // @param 0.0 0.5 0.0 fixed

// look controls: breathing zoom, whole-picture spin (k_spin turns per loop), kaleidoscope fold
vec2 xform(vec2 q, float th) {
  if (u_zoom != 0.0) q *= 1.0 + u_zoom * sin(th);
  if (k_spin != 0) {
    float c = cos(float(k_spin) * th), s = sin(float(k_spin) * th);
    q = vec2(c * q.x - s * q.y, s * q.x + c * q.y);
  }
  if (u_kaleido >= 2) {
    float seg = TAU / float(u_kaleido);
    float a = abs(mod(atan(q.y, q.x), seg) - 0.5 * seg);
    q = length(q) * vec2(cos(a), sin(a));
  }
  return q;
}
"""

_HEAD = "// Port of funk v1 effect '{name}': {blurb}\n"

PLASMA = _HEAD.format(name="plasma", blurb="classic demoscene sine soup") + """\
uniform float u_k0;   // @param 4.0 10.0 7.0
uniform float u_k1;   // @param 4.0 10.0 7.0
uniform float u_k2;   // @param 4.0 10.0 7.0
uniform float u_k3;   // @param 4.0 10.0 7.0

vec4 effect(vec2 p) {
  float th = iPhase;
  vec2 q = xform(vec2(p.x, -p.y), th);
  float u = q.x, v = q.y;
  float cx = 0.6 * cos(th), cy = 0.6 * sin(2.0 * th);
  float f = sin(u_k0 * u + th)
          + sin(u_k1 * v - 2.0 * th)
          + sin(u_k2 * (u + v) + th)
          + sin(u_k3 * length(vec2(u - cx, v - cy)) * 2.0 - 3.0 * th);
  return vec4(palette(f * 0.3 + 0.5), 1.0);
}
"""

MOIRE = _HEAD.format(name="moire", blurb="two orbiting ring emitters interfering") + """\
uniform float u_k;    // @param 8.0 16.0 12.0
uniform float u_ph;   // @param 0.0 6.2831853 3.0

vec4 effect(vec2 p) {
  float th = iPhase;
  vec2 q = xform(vec2(p.x, -p.y), th);
  float u = q.x, v = q.y;
  vec2 c1 = 0.45 * vec2(cos(th), sin(th));
  vec2 c2 = 0.45 * vec2(cos(-2.0 * th + u_ph), sin(-2.0 * th + u_ph));
  float r1 = length(vec2(u, v) - c1);
  float r2 = length(vec2(u, v) - c2);
  float bands = sign(sin(u_k * r1) * sin(u_k * r2));
  float x = 0.3 * (r1 + r2) + 0.22 * bands;
  return vec4(palette(x), 1.0);
}
"""

WARP = _HEAD.format(name="warp", blurb="domain-warped liquid stripes") + """\
uniform float u_amp;      // @param 0.25 0.55 0.4
uniform float u_freq;     // @param 2.0 4.5 3.0
uniform float u_stripes;  // @param 6.0 14.0 10.0
uniform int   u_iters;    // @param 2 3 2

vec4 effect(vec2 p) {
  float th = iPhase;
  vec2 q = xform(vec2(p.x, -p.y), th);
  float u = q.x, v = q.y;
  for (int i = 0; i < u_iters; i++) {
    float k = float(i + 1);
    vec2 w = vec2(u + u_amp * sin(u_freq * v + k * th),
                  v + u_amp * cos(u_freq * u - k * th));
    u = w.x; v = w.y;
  }
  float x = 0.5 + 0.35 * sin(u_stripes * (u + v)) + 0.12 * length(vec2(u, v));
  return vec4(palette(x), 1.0);
}
"""

TUNNEL = _HEAD.format(name="tunnel", blurb="infinite checkered wormhole") + """\
uniform float u_n;   // @param 1.5 3.5 2.5
uniform int   u_m;   // @param 4 12 8

vec4 effect(vec2 p) {
  float th = iPhase;
  vec2 q = xform(vec2(p.x, -p.y), th);
  float u = q.x, v = q.y;
  float r = length(vec2(u, v)) + 1e-3;
  float a = atan(v, u);
  float depth = u_n / r;
  // v1 fix 1: reduce depth with fract BEFORE sin(); near the centre depth is huge and
  // float rounding would flip signs between t=0 and t=1.
  // v1 fix 2: u_m is an integer so the atan branch cut is invisible; the +0.37 keeps sign()
  // edges off the exact symmetry diagonals where sin(0) vs sin(2pi) would flip pixels.
  float s = sin(TAU * fract(depth) + 2.0 * th) * sin(float(u_m) * a + th + 0.37);
  float x = 0.05 * depth + 0.25 * sign(s);
  float shade = clamp(r * 1.6, 0.0, 1.0);
  return vec4(palette(x) * shade, 1.0);
}
"""

RINGS = _HEAD.format(name="rings", blurb="op-art concentric pulse with angular wobble") + """\
uniform float u_k;      // @param 28.0 50.0 38.0
uniform int   u_lobes;  // @param 3 8 5
uniform float u_wob;    // @param 0.05 0.2 0.12

vec4 effect(vec2 p) {
  float th = iPhase;
  vec2 q = xform(vec2(p.x, -p.y), th);
  float u = q.x, v = q.y;
  float r = length(vec2(u, v));
  float a = atan(v, u);
  float rr = r + u_wob * sin(float(u_lobes) * a + 2.0 * th) * r;
  float x = 0.5 + 0.5 * sign(sin(u_k * rr - 3.0 * th)) * 0.4 + 0.25 * r;
  return vec4(palette(x), 1.0);
}
"""


def _blobs() -> str:
    decl = ["uniform int   u_balls;   // @param 4 7 5"]
    for i in range(7):
        decl += [f"uniform float u_ax{i};   // @param 0.3 0.8 0.55",
                 f"uniform float u_ay{i};   // @param 0.3 0.8 0.55",
                 f"uniform int   k_fx{i};   // @param 1 3 2",
                 f"uniform int   k_fy{i};   // @param 1 3 2",
                 f"uniform float u_ph{i};   // @param 0.0 6.2831853 3.0"]
    arr = lambda pre: ", ".join(f"{pre}{i}" for i in range(7))  # noqa: E731
    return _HEAD.format(name="blobs", blurb="lava-lamp metaballs on Lissajous orbits") + \
        "\n".join(decl) + f"""

vec4 effect(vec2 p) {{
  float th = iPhase;
  vec2 q = xform(vec2(p.x, -p.y), th);
  float u = q.x, v = q.y;
  float ax[7] = float[7]({arr('u_ax')});
  float ay[7] = float[7]({arr('u_ay')});
  int   fx[7] = int[7]({arr('k_fx')});
  int   fy[7] = int[7]({arr('k_fy')});
  float ph[7] = float[7]({arr('u_ph')});
  float f = 0.0;
  for (int i = 0; i < 7; i++) {{
    if (i >= u_balls) break;
    float cx = ax[i] * sin(float(fx[i]) * th + ph[i]);
    float cy = ay[i] * cos(float(fy[i]) * th + ph[i]);
    f += 0.05 / ((u - cx) * (u - cx) + (v - cy) * (v - cy) + 1e-3);
  }}
  float lf = log(1.0 + f);
  float x = 0.35 * lf + 0.08 * sin(9.0 * lf);
  return vec4(palette(x), 1.0);
}}
"""


def _with_look(src: str) -> str:
    """Insert the look controls (after the effect's own params, so seeded sampling is unchanged)."""
    head, tail = src.split("\nvec4 effect(vec2 p)", 1)
    return head + "\n" + _XFORM.strip("\n") + "\n\nvec4 effect(vec2 p)" + tail


PORTS: dict[str, str] = {name: _with_look(src) for name, src in {
    "plasma": PLASMA, "moire": MOIRE, "warp": WARP, "tunnel": TUNNEL,
    "blobs": _blobs(), "rings": RINGS,
}.items()}
