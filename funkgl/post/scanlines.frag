// Darken every third row (rows 1,4,7... from the top, as v1).
uniform float u_strength;   // @param 0 1 0.28
vec4 srcpx() { return texelFetch(iChannel0, ivec2(gl_FragCoord.xy), 0); }

vec4 effect(vec2 p) {
  float row = floor(iResolution.y - gl_FragCoord.y);
  float k = (mod(row, 3.0) == 1.0) ? 1.0 - u_strength : 1.0;
  return vec4(srcpx().rgb * k, 1.0);
}
