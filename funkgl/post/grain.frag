// Film grain. Loop-safe: the noise pattern is seeded from (iFrame mod frames), so the
// frame at t=1 reuses frame 0's pattern and nothing flickers on a different cycle.
uniform float u_amount;   // @param 0 0.3 0.03
vec4 srcpx() { return texelFetch(iChannel0, ivec2(gl_FragCoord.xy), 0); }

uint pcg(uint v) {
  uint s = v * 747796405u + 2891336453u;
  uint w = ((s >> ((s >> 28u) + 4u)) ^ s) * 277803737u;
  return (w >> 22u) ^ w;
}
float h1(uint x, uint y, uint z) {
  return float(pcg(x + pcg(y + pcg(z)))) * (1.0 / 4294967295.0);
}
vec4 effect(vec2 p) {
  uvec2 q = uvec2(gl_FragCoord.xy);
  uint f = uint(iFrame % max(u_frames, 1)) + uint(iSeed) * 977u;
  float n = (h1(q.x, q.y, f) + h1(q.x + 4099u, q.y + 7919u, f) + h1(q.x + 15485u, q.y + 32452u, f) - 1.5) * 2.0;
  return vec4(srcpx().rgb + u_amount * n, 1.0);
}
