// Port of funk v1 effect 'moire': two orbiting ring emitters interfering
uniform float u_k;    // @param 8.0 16.0 12.0
uniform float u_ph;   // @param 0.0 6.2831853 3.0

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
  vec2 c1 = 0.45 * vec2(cos(th), sin(th));
  vec2 c2 = 0.45 * vec2(cos(-2.0 * th + u_ph), sin(-2.0 * th + u_ph));
  float r1 = length(vec2(u, v) - c1);
  float r2 = length(vec2(u, v) - c2);
  float bands = sign(sin(u_k * r1) * sin(u_k * r2));
  float x = 0.3 * (r1 + r2) + 0.22 * bands;
  return vec4(palette(x), 1.0);
}
