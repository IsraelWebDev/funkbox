// Morphing torus <-> sphere, sphere-traced (max 64 steps). All motion is iPhase * integer.
uniform float u_big;    // @param 0.7 1.1 0.9
uniform float u_thick;  // @param 0.25 0.45 0.33
uniform int   k_spin;   // @param 1 2 1
uniform float u_glow;   // @param 0.2 0.9 0.5

float sdf(vec3 q, float m) {
  q.xz = rot(q.xz, float(k_spin) * iPhase);
  q.yz = rot(q.yz, iPhase + 0.6);
  vec2 t = vec2(length(q.xz) - u_big, q.y);
  float torus = length(t) - u_thick;
  float sphere = length(q) - (u_big + u_thick * 0.6);
  return mix(torus, sphere, m);
}

vec3 normalAt(vec3 q, float m) {
  const vec2 e = vec2(1.0, -1.0) * 0.0015;
  return normalize(e.xyy * sdf(q + e.xyy, m) + e.yyx * sdf(q + e.yyx, m) +
                   e.yxy * sdf(q + e.yxy, m) + e.xxx * sdf(q + e.xxx, m));
}

vec4 effect(vec2 p) {
  float m = 0.5 + 0.5 * sin(iPhase);
  vec3 ro = vec3(0.0, 0.0, -3.2);
  vec3 rd = normalize(vec3(p, 1.7));
  float t = 0.0, dmin = 1e3;
  bool hit = false;
  for (int i = 0; i < 64; i++) {
    vec3 q = ro + rd * t;
    float d = sdf(q, m);
    dmin = min(dmin, d);
    if (d < 0.001) { hit = true; break; }
    t += d * 0.8;                      // interpolated SDF is not exact: under-step
    if (t > 8.0) break;
  }
  vec3 bg = palette(0.15 + 0.2 * length(p)) * (0.25 - 0.1 * length(p));
  vec3 col = bg + u_glow * palette(0.6) * exp(-6.0 * max(dmin, 0.0));   // halo from nearest miss
  if (hit) {
    vec3 q = ro + rd * t;
    vec3 n = normalAt(q, m);
    vec3 l = normalize(vec3(-0.5, 0.8, -0.6));
    float dif = clamp(dot(n, l), 0.0, 1.0);
    float fre = pow(1.0 - clamp(dot(n, -rd), 0.0, 1.0), 3.0);
    float spe = pow(clamp(dot(reflect(-l, n), -rd), 0.0, 1.0), 24.0);
    vec3 base = palette(0.5 + 0.5 * n.y + 0.25 * n.x);
    col = base * (0.2 + 0.9 * dif) + fre * palette(0.9) + spe;
  }
  return vec4(col, 1.0);
}
