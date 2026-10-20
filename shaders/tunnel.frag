// Port of funk v1 effect 'tunnel': infinite checkered wormhole
uniform float u_n;   // @param 1.5 3.5 2.5
uniform int   u_m;   // @param 4 12 8

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
