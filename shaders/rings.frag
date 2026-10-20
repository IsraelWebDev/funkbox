// Port of funk v1 effect 'rings': op-art concentric pulse with angular wobble
uniform float u_k;      // @param 28.0 50.0 38.0
uniform int   u_lobes;  // @param 3 8 5
uniform float u_wob;    // @param 0.05 0.2 0.12

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
  float r = length(vec2(u, v));
  float a = atan(v, u);
  float rr = r + u_wob * sin(float(u_lobes) * a + 2.0 * th) * r;
  float x = 0.5 + 0.5 * sign(sin(u_k * rr - 3.0 * th)) * 0.4 + 0.25 * r;
  return vec4(palette(x), 1.0);
}
