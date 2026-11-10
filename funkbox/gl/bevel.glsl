// Bevel pass: lights a masked layer from its mask's edges, so the shape reads as raised.
// Colour only: the alpha (and so the coverage) is unchanged, and the interior of the shape is untouched.
// out.rgb = layer.rgb * (1 + k * (dot(n, l) - l.z)), where n is the normal of the mask's height (its alpha),
// l = the light direction (u_light, the light's angle in the plane), k = u_strength.
// "#version 330" is prepended by the engine; this pass does not use the shader prelude.
uniform sampler2D u_layer;
uniform sampler2D u_mask;
uniform int   u_width;     // px: the slope spans 2 * u_width pixels
uniform float u_strength;  // 0..1
uniform vec2  u_light;     // (cos, sin) of the light angle
out vec4 fragColor;

float height(ivec2 c) {
  ivec2 s = textureSize(u_mask, 0);
  if (c.x < 0 || c.y < 0 || c.x >= s.x || c.y >= s.y) return 0.0;
  return texelFetch(u_mask, c, 0).a;
}

void main() {
  ivec2 c = ivec2(gl_FragCoord.xy);
  vec4 col = texelFetch(u_layer, c, 0);
  float w = float(max(u_width, 1));
  float gx = (height(c + ivec2(u_width, 0)) - height(c - ivec2(u_width, 0))) / (2.0 * w);
  float gy = (height(c + ivec2(0, u_width)) - height(c - ivec2(0, u_width))) / (2.0 * w);
  vec3 n = normalize(vec3(-3.5 * gx, -3.5 * gy, 1.0));  // 3.5: how steep the edge reads
  vec3 l = normalize(vec3(u_light, 1.0));
  float shade = dot(n, l) - l.z;
  fragColor = vec4(clamp(col.rgb * (1.0 + u_strength * shade), 0.0, 1.0), col.a);
}
