// Mosaic: every cell shows its centre pixel; cells are anchored at the top-left of the image (stills).
uniform float u_cell;   // @param 2 128 8

vec4 effect(vec2 p) {
  ivec2 sz = textureSize(iChannel0, 0);
  float cell = max(1.0, floor(u_cell + 0.5));
  vec2 px = vec2(gl_FragCoord.x - 0.5, float(sz.y) - 0.5 - gl_FragCoord.y);          // top-left pixel index
  vec2 cc = floor(px / cell) * cell + floor(cell * 0.5);
  ivec2 q = ivec2(min(cc.x, float(sz.x - 1)), float(sz.y - 1) - min(cc.y, float(sz.y - 1)));
  return texelFetch(iChannel0, q, 0);
}
