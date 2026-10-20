// Composite pass: out = mix(base, blend(base, src), opacity * src.a). Straight alpha.
// "#version 330" is prepended by the engine; this pass does not use the shader prelude.
uniform sampler2D u_base;
uniform sampler2D u_src;
uniform int   u_mode;      // 0 normal 1 add 2 screen 3 multiply 4 overlay 5 difference 6 softlight 7 hue
uniform float u_opacity;
out vec4 fragColor;

float lum(vec3 c) { return dot(c, vec3(0.3, 0.59, 0.11)); }
vec3 clipColor(vec3 c) {
  float l = lum(c), n = min(c.r, min(c.g, c.b)), x = max(c.r, max(c.g, c.b));
  if (n < 0.0) c = l + (c - l) * l / (l - n);
  if (x > 1.0) c = l + (c - l) * (1.0 - l) / (x - l);
  return c;
}
vec3 setLum(vec3 c, float l) { return clipColor(c + (l - lum(c))); }
vec3 setSat(vec3 c, float s) {
  float mx = max(c.r, max(c.g, c.b)), mn = min(c.r, min(c.g, c.b));
  return mx > mn ? (c - mn) * s / (mx - mn) : vec3(0.0);
}
float sat(vec3 c) { return max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b)); }

float softlight1(float cb, float cs) {
  if (cs <= 0.5) return cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb);
  float d = cb <= 0.25 ? ((16.0 * cb - 12.0) * cb + 4.0) * cb : sqrt(cb);
  return cb + (2.0 * cs - 1.0) * (d - cb);
}

vec3 blendRGB(vec3 b, vec3 s) {
  if (u_mode == 1) return min(b + s, 1.0);
  if (u_mode == 2) return 1.0 - (1.0 - b) * (1.0 - s);
  if (u_mode == 3) return b * s;
  if (u_mode == 4) return mix(2.0 * b * s, 1.0 - 2.0 * (1.0 - b) * (1.0 - s), step(0.5, b));
  if (u_mode == 5) return abs(b - s);
  if (u_mode == 6) return vec3(softlight1(b.r, s.r), softlight1(b.g, s.g), softlight1(b.b, s.b));
  if (u_mode == 7) return setLum(setSat(s, sat(b)), lum(b));
  return s;
}

void main() {
  ivec2 c = ivec2(gl_FragCoord.xy);
  vec4 b = texelFetch(u_base, c, 0);
  vec4 s = texelFetch(u_src, c, 0);
  vec3 outc = mix(b.rgb, blendRGB(b.rgb, s.rgb), clamp(u_opacity, 0.0, 1.0) * s.a);
  fragColor = vec4(outc, 1.0);
}
