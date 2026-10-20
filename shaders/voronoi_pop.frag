// Animated Voronoi cells coloured by cell id. Cell points orbit with sin(k*iPhase + id),
// k an integer, so the loop is seamless. The id is the integer grid cell, which never moves.
uniform float u_scale;  // @param 3.0 8.0 5.0
uniform float u_wob;    // @param 0.2 0.5 0.4
uniform int   k_orbit;  // @param 1 2 1
uniform float u_edge;   // @param 0.02 0.08 0.04

vec2 hash22(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.xx + p3.yz) * p3.zy);
}

vec4 effect(vec2 p) {
  vec2 x = p * u_scale + vec2(iSeed * 7.3, iSeed * 2.9);
  vec2 n = floor(x), f = fract(x);
  float d1 = 8.0, d2 = 8.0;
  vec2 id = vec2(0.0), best = vec2(0.0);
  for (int j = -1; j <= 1; j++)
    for (int i = -1; i <= 1; i++) {
      vec2 g = vec2(float(i), float(j));
      vec2 h = hash22(n + g);
      vec2 o = 0.5 + u_wob * sin(float(k_orbit) * iPhase + TAU * h + vec2(0.0, 1.7));
      vec2 r = g + o - f;
      float d = dot(r, r);
      if (d < d1) { d2 = d1; d1 = d; id = n + g; best = r; }
      else if (d < d2) { d2 = d; }
    }
  float edge = smoothstep(0.0, u_edge, sqrt(d2) - sqrt(d1));
  float cell = hash22(id + 11.0).x;
  vec3 col = palette(cell);
  col *= 0.55 + 0.45 * smoothstep(0.7, 0.0, sqrt(d1));      // bright centres
  col += 0.35 * smoothstep(0.12, 0.0, sqrt(d1));             // glowing seed dots
  col *= edge;
  return vec4(col, 1.0);
}
