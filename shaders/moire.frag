// Port of funk v1 effect 'moire': two orbiting ring emitters interfering
uniform float u_k;    // @param 8.0 16.0 12.0
uniform float u_ph;   // @param 0.0 6.2831853 3.0

vec4 effect(vec2 p) {
  float u = p.x, v = -p.y, th = iPhase;
  vec2 c1 = 0.45 * vec2(cos(th), sin(th));
  vec2 c2 = 0.45 * vec2(cos(-2.0 * th + u_ph), sin(-2.0 * th + u_ph));
  float r1 = length(vec2(u, v) - c1);
  float r2 = length(vec2(u, v) - c2);
  float bands = sign(sin(u_k * r1) * sin(u_k * r2));
  float x = 0.3 * (r1 + r2) + 0.22 * bands;
  return vec4(palette(x), 1.0);
}
