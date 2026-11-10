// Liquid chrome: domain-warped fractal noise read as a height field, lit like a mirror.
// The loop comes from loopNoise() (the 2D field is sampled on a circle in the 3rd/4th noise
// dimensions), so t=1 is exactly t=0. Cost: 13 4D simplex lookups (2+2 warp, 3 x 3 height) and a few
// dozen ALU ops. The palette is a ramp over the environment brightness: dark ground to bright sky.
// It uses pal() with the recipe's palette colours, not palette(), so `cycles` does not pulse the metal.
uniform float u_scale;    // @param 0.9 1.8 1.3
uniform float u_warp;     // @param 0.5 1.2 0.8
uniform float u_rad;      // @param 0.5 1.1 0.8
uniform float u_bump;     // @param 0.2 0.55 0.35
uniform float u_horizon;  // @param -0.15 0.15 0.0

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

// fractal noise, n octaves; each octave circles the loop phase at its own (fixed) radius
float fbm(vec2 q, int n) {
  float a = 0.5, s = 0.0, r = u_rad;
  for (int i = 0; i < 3; i++) {
    if (i >= n) break;
    s += a * loopNoise(q, r);
    q = rot(q * 2.03, 0.5) + vec2(17.1, 3.7);
    a *= 0.5;
    r *= 1.3;
  }
  return s;
}

// height of the liquid surface: the field is warped by two more fbm fields (w, computed once)
float height(vec2 q, vec2 w) { return fbm(q + u_warp * w, 3); }

// the studio the metal reflects: dark floor, bright sky, a glowing horizon band and one key light
const vec3 KEY = vec3(-0.42, 0.58, 0.70);

float studio(vec3 r) {
  float t = clamp(2.0 * (r.y - u_horizon), -1.0, 1.0);  // elevation; a flat surface looks at the horizon
  float sky = smoothstep(-0.12, 0.12, t);
  float e = mix(0.12 + 0.22 * (t + 1.0), 0.40 + 0.45 * t, sky);  // dark floor, mid-to-bright sky
  e += 0.35 * exp(-t * t * 30.0);                        // bright band on the horizon
  e += 1.2 * pow(max(dot(r, normalize(KEY)), 0.0), 56.0);  // key light specular
  return e;
}

vec4 effect(vec2 p) {
  vec2 q = xform(p, iPhase) * u_scale + vec2(iSeed * 3.1, iSeed * 1.7);
  vec2 w = vec2(fbm(q, 2), fbm(q + vec2(5.2, 1.3), 2));

  // height field and its gradient (forward differences, same warp for all three samples)
  const float EPS = 0.012;
  float h0 = height(q, w);
  vec2 g = vec2(height(q + vec2(EPS, 0.0), w) - h0, height(q + vec2(0.0, EPS), w) - h0) / EPS;
  vec3 n = normalize(vec3(-g * u_bump, 1.0));

  // mirror reflection of the view ray (0,0,-1) and a fresnel rim
  vec3 r = vec3(2.0 * n.z * n.xy, 2.0 * n.z * n.z - 1.0);
  float fres = pow(1.0 - n.z, 3.0);
  float e = clamp(studio(r) + 0.35 * fres, 0.0, 1.0);

  return vec4(pal(e, u_pal_a, u_pal_b, u_pal_c, u_pal_d), 1.0);
}
