uniform float u_levels;   // @param 2 16 7
vec4 srcpx() { return texelFetch(iChannel0, ivec2(gl_FragCoord.xy), 0); }

vec4 effect(vec2 p) {
  float n = floor(u_levels + 0.5);
  vec3 c = floor(clamp(srcpx().rgb, 0.0, 1.0) * n) / (n - 1.0);
  return vec4(c, 1.0);
}
