// Port of funk v1 effect 'rings': op-art concentric pulse with angular wobble
uniform float u_k;      // @param 28.0 50.0 38.0
uniform int   u_lobes;  // @param 3 8 5
uniform float u_wob;    // @param 0.05 0.2 0.12

vec4 effect(vec2 p) {
  float u = p.x, v = -p.y, th = iPhase;
  float r = length(vec2(u, v));
  float a = atan(v, u);
  float rr = r + u_wob * sin(float(u_lobes) * a + 2.0 * th) * r;
  float x = 0.5 + 0.5 * sign(sin(u_k * rr - 3.0 * th)) * 0.4 + 0.25 * r;
  return vec4(palette(x), 1.0);
}
