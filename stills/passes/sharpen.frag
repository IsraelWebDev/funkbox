// Unsharp mask with a 3x3 gaussian (stills).
uniform float u_amount;   // @param 0 3 0.5
vec4 px(ivec2 q) { return texelFetch(iChannel0, clamp(q, ivec2(0), textureSize(iChannel0, 0) - 1), 0); }

vec4 effect(vec2 p) {
  ivec2 c = ivec2(gl_FragCoord.xy);
  vec4 m = px(c);
  vec3 b = (4.0 * m.rgb + 2.0 * (px(c + ivec2(1, 0)).rgb + px(c - ivec2(1, 0)).rgb + px(c + ivec2(0, 1)).rgb + px(c - ivec2(0, 1)).rgb)
            + px(c + ivec2(1, 1)).rgb + px(c + ivec2(-1, 1)).rgb + px(c + ivec2(1, -1)).rgb + px(c + ivec2(-1, -1)).rgb) / 16.0;
  return vec4(clamp(m.rgb + u_amount * (m.rgb - b), 0.0, 1.0), m.a);
}
