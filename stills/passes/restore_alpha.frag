// Colour from iChannel0, alpha from iChannel1: re-attaches a layer's alpha after a pass that forces alpha 1.
vec4 effect(vec2 p) {
  ivec2 c = ivec2(gl_FragCoord.xy);
  return vec4(texelFetch(iChannel0, c, 0).rgb, texelFetch(iChannel1, c, 0).a);
}
