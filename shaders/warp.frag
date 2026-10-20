// Port of funk v1 effect 'warp': domain-warped liquid stripes
uniform float u_amp;      // @param 0.25 0.55 0.4
uniform float u_freq;     // @param 2.0 4.5 3.0
uniform float u_stripes;  // @param 6.0 14.0 10.0
uniform int   u_iters;    // @param 2 3 2

vec4 effect(vec2 p) {
  float u = p.x, v = -p.y, th = iPhase;
  for (int i = 0; i < u_iters; i++) {
    float k = float(i + 1);
    vec2 w = vec2(u + u_amp * sin(u_freq * v + k * th),
                  v + u_amp * cos(u_freq * u - k * th));
    u = w.x; v = w.y;
  }
  float x = 0.5 + 0.35 * sin(u_stripes * (u + v)) + 0.12 * length(vec2(u, v));
  return vec4(palette(x), 1.0);
}
