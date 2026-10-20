// Port of funk v1 effect 'tunnel': infinite checkered wormhole
uniform float u_n;   // @param 1.5 3.5 2.5
uniform int   u_m;   // @param 4 12 8

vec4 effect(vec2 p) {
  float u = p.x, v = -p.y, th = iPhase;
  float r = length(vec2(u, v)) + 1e-3;
  float a = atan(v, u);
  float depth = u_n / r;
  // v1 fix 1: reduce depth with fract BEFORE sin(); near the centre depth is huge and
  // float rounding would flip signs between t=0 and t=1.
  // v1 fix 2: u_m is an integer so the atan branch cut is invisible; the +0.37 keeps sign()
  // edges off the exact symmetry diagonals where sin(0) vs sin(2pi) would flip pixels.
  float s = sin(TAU * fract(depth) + 2.0 * th) * sin(float(u_m) * a + th + 0.37);
  float x = 0.05 * depth + 0.25 * sign(s);
  float shade = clamp(r * 1.6, 0.0, 1.0);
  return vec4(palette(x) * shade, 1.0);
}
