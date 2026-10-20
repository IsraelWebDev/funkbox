// @noloopcheck
// Echo/trail effect: re-samples everything BELOW this layer (iChannel0) at progressively
// zoomed + rotated coordinates and accumulates the copies. It loops seamlessly only if the
// layers beneath it loop, which a standalone check cannot know, so it opts out of the
// automatic loop test with the marker above. Alone it echoes its own pulsing seed rings.
uniform int   u_echoes;  // @param 3 8 6
uniform float u_zoom;    // @param 0.04 0.14 0.08
uniform float u_twist;   // @param 0.02 0.12 0.06
uniform float u_decay;   // @param 0.65 0.9 0.8
uniform int   k_pulse;   // @param 1 2 1

vec3 seed(vec2 q) {
  float r = length(q);
  float ring = 0.5 + 0.5 * sin(14.0 * r - float(k_pulse) * iPhase);
  float a = atan(q.y, q.x);
  return palette(0.3 * r + 0.1 * sin(3.0 * a)) * pow(ring, 6.0);
}

vec4 effect(vec2 p) {
  vec3 acc = vec3(0.0);
  float w = 1.0, wsum = 0.0;
  vec2 q = p;
  for (int i = 0; i < 12; i++) {
    if (i >= u_echoes) break;
    vec3 s = src0(q).rgb + 1.6 * seed(q);
    acc += w * s;
    wsum += w;
    w *= u_decay;
    q = rot(q * (1.0 - u_zoom), u_twist * sin(iPhase) + 0.35 * u_twist);
  }
  return vec4(acc / wsum, 1.0);
}
