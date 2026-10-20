// Map luminance onto a dark-to-light colour ramp (stills).
uniform vec3 u_dark;    // @param color #000000
uniform vec3 u_light;   // @param color #ffffff

vec4 effect(vec2 p) {
  vec4 s = texelFetch(iChannel0, ivec2(gl_FragCoord.xy), 0);
  float l = dot(s.rgb, vec3(0.299, 0.587, 0.114));
  return vec4(mix(u_dark, u_light, clamp(l, 0.0, 1.0)), s.a);
}
