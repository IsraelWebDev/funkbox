// Round dots on a grid anchored at the top-left (as v1), luminance-sized.
uniform float u_cell;   // @param 3 40 8
vec4 srcpx() { return texelFetch(iChannel0, ivec2(gl_FragCoord.xy), 0); }

vec4 effect(vec2 p) {
  float cell = floor(u_cell + 0.5);
  vec2 px = vec2(gl_FragCoord.x - 0.5, iResolution.y - gl_FragCoord.y - 0.5);   // top-left pixel index
  vec2 cellc = min(floor(px / cell) * cell + floor(cell * 0.5), vec2(iResolution.x - 1.0, iResolution.y - 1.0));
  vec3 centre = texelFetch(iChannel0, ivec2(cellc.x, iResolution.y - 1.0 - cellc.y), 0).rgb;
  float l = dot(centre, vec3(0.299, 0.587, 0.114));
  float radius = cell * 0.62 * sqrt(clamp(l, 0.0, 1.0));
  bool dot_ = length(px - cellc) < radius;
  return vec4(dot_ ? centre * 1.15 : centre * 0.12, 1.0);
}
