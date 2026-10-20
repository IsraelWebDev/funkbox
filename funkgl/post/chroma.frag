// RGB split; shift pulses with sin(2*iPhase) like v1. Wraps horizontally (np.roll).
uniform float u_px;   // @param 0 24 4
vec4 srcpx() { return texelFetch(iChannel0, ivec2(gl_FragCoord.xy), 0); }

vec4 effect(vec2 p) {
  int w = int(iResolution.x);
  int s = int(floor(u_px * (1.0 + 0.5 * sin(2.0 * iPhase)) + 0.5));
  ivec2 c = ivec2(gl_FragCoord.xy);
  vec4 m = texelFetch(iChannel0, c, 0);
  float r = texelFetch(iChannel0, ivec2((c.x - s + 4 * w) % w, c.y), 0).r;
  float b = texelFetch(iChannel0, ivec2((c.x + s + 4 * w) % w, c.y), 0).b;
  return vec4(r, m.g, b, 1.0);
}
