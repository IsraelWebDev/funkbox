// Port of funk v1 effect 'plasma': classic demoscene sine soup
uniform float u_k0;   // @param 4.0 10.0 7.0
uniform float u_k1;   // @param 4.0 10.0 7.0
uniform float u_k2;   // @param 4.0 10.0 7.0
uniform float u_k3;   // @param 4.0 10.0 7.0

vec4 effect(vec2 p) {
  float u = p.x, v = -p.y, th = iPhase;
  float cx = 0.6 * cos(th), cy = 0.6 * sin(2.0 * th);
  float f = sin(u_k0 * u + th)
          + sin(u_k1 * v - 2.0 * th)
          + sin(u_k2 * (u + v) + th)
          + sin(u_k3 * length(vec2(u - cx, v - cy)) * 2.0 - 3.0 * th);
  return vec4(palette(f * 0.3 + 0.5), 1.0);
}
