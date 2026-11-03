// Gear train: three meshing gears turning at integer ratios, so one loop = whole turns.
uniform float u_speed; // @param 1.0 3.0 1.0 fixed

float gear(vec2 q, vec2 c, float teeth, float rot, float rad) {
  vec2 d = q - c;
  float r = length(d);
  float a = atan(d.y, d.x) + rot;
  float tooth = 0.5 + 0.5 * cos(teeth * a);
  float edge = rad * (0.86 + 0.14 * tooth);
  float body = smoothstep(0.004, -0.004, r - edge);
  float hole = smoothstep(0.006, 0.0, abs(r - 0.04)) ;
  return body * (1.0 - 0.0 * hole);
}

vec4 effect(vec2 p) {
  float th = iPhase * u_speed;
  vec2 q = vec2(p.x, -p.y) * 1.25;
  float g1 = gear(q, vec2(-0.30, 0.0), 12.0,  th,        0.26);
  float g2 = gear(q, vec2( 0.25, 0.0), 12.0, -th,        0.26);
  float g3 = gear(q, vec2( 0.00, 0.44), 6.0, -2.0 * th,  0.16);
  float m = max(g1, max(g2, g3));
  float f = g1 + 2.0 * g2 + 3.0 * g3;
  vec3 col = mix(vec3(0.06, 0.05, 0.1), palette(0.25 + 0.1 * f), m);
  return vec4(col, 1.0);
}
