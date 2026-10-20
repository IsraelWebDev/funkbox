// Port of funk v1 effect 'plasma': classic demoscene sine soup
uniform float u_k0;   // @param 4.0 10.0 7.0
uniform float u_k1;   // @param 4.0 10.0 7.0
uniform float u_k2;   // @param 4.0 10.0 7.0
uniform float u_k3;   // @param 4.0 10.0 7.0

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
  float cx = 0.6 * cos(th), cy = 0.6 * sin(2.0 * th);
  float f = sin(u_k0 * u + th)
          + sin(u_k1 * v - 2.0 * th)
          + sin(u_k2 * (u + v) + th)
          + sin(u_k3 * length(vec2(u - cx, v - cy)) * 2.0 - 3.0 * th);
  return vec4(palette(f * 0.3 + 0.5), 1.0);
}
