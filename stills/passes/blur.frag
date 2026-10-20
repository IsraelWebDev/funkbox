// One direction of a separable gaussian blur on premultiplied colour (stills). Edges clamp.
uniform float u_sigma;   // @param 0.3 64 4
uniform ivec2 u_dir;
uniform int   u_taps;

vec4 effect(vec2 p) {
  ivec2 c = ivec2(gl_FragCoord.xy);
  ivec2 hi = textureSize(iChannel0, 0) - 1;
  vec4 acc = vec4(0.0);
  float wsum = 0.0;
  for (int i = -u_taps; i <= u_taps; i++) {
    float w = exp(-0.5 * float(i * i) / (u_sigma * u_sigma));
    vec4 t = texelFetch(iChannel0, clamp(c + u_dir * i, ivec2(0), hi), 0);
    acc += w * vec4(t.rgb * t.a, t.a);
    wsum += w;
  }
  acc /= wsum;
  return vec4(acc.a > 0.0 ? acc.rgb / acc.a : vec3(0.0), acc.a);
}
