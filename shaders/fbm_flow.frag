// Domain-warped fBm. GPU-only luxury: ~15 4D simplex lookups per pixel, every frame.
// The loop comes from loopNoise(): the 2D field is sampled on a circle in the 3rd/4th noise
// dimensions, so t=1 is exactly t=0.
uniform float u_scale;  // @param 0.8 2.2 1.3
uniform float u_warp;   // @param 0.4 1.4 0.9
uniform float u_rad;    // @param 0.5 1.2 0.8
uniform int   u_oct;    // @param 3 6 5

float fbm(vec2 q, float rad) {
  float a = 0.5, s = 0.0;
  for (int i = 0; i < 6; i++) {
    if (i >= u_oct) break;
    s += a * loopNoise(q, rad);
    q = rot(q * 2.03, 0.5) + vec2(17.1, 3.7);
    a *= 0.5;
  }
  return s;
}

vec4 effect(vec2 p) {
  vec2 q = p * u_scale + vec2(iSeed * 3.1, iSeed * 1.7);
  vec2 w = vec2(fbm(q, u_rad), fbm(q + vec2(5.2, 1.3), u_rad));
  float f = fbm(q + u_warp * w, u_rad);
  float shade = 0.65 + 0.5 * length(w);
  vec3 col = palette(f * 0.9 + 0.5 + 0.3 * w.x) * shade;
  return vec4(col, 1.0);
}
