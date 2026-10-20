uniform float u_amount;   // @param 0 1.5 0.35
vec4 srcpx() { return texelFetch(iChannel0, ivec2(gl_FragCoord.xy), 0); }

vec4 effect(vec2 p) {
  float k = 1.0 - u_amount * max(length(p) - 0.4, 0.0);
  return vec4(srcpx().rgb * k, 1.0);
}
