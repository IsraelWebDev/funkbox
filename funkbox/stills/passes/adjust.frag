// Per-layer colour adjustment (stills). Alpha is passed through untouched.
// Order: levels, brightness, contrast, saturation, hue, temperature, invert (all in sRGB, unclamped between steps).
uniform float u_brightness;   // @param -1 1 0
uniform float u_contrast;     // @param -1 1 0
uniform float u_saturation;   // @param -1 2 0
uniform float u_hue;          // @param -360 360 0
uniform float u_black;        // @param 0 1 0
uniform float u_white;        // @param 0 1 1
uniform float u_gamma;        // @param 0.1 10 1
uniform float u_temp;         // @param -1 1 0
uniform float u_invert;       // @param 0 1 0

vec4 effect(vec2 p) {
  vec4 s = texelFetch(iChannel0, ivec2(gl_FragCoord.xy), 0);
  vec3 c = pow(clamp((s.rgb - u_black) / (u_white - u_black), 0.0, 1.0), vec3(1.0 / u_gamma));
  c += u_brightness;
  c = (c - 0.5) * (1.0 + u_contrast) + 0.5;
  float L = dot(c, vec3(0.299, 0.587, 0.114));
  c = L + (c - L) * (1.0 + u_saturation);
  if (abs(u_hue) > 1e-6) {                       // rotate the I/Q plane of YIQ
    float Y = dot(c, vec3(0.299, 0.587, 0.114));
    float I = dot(c, vec3(0.596, -0.274, -0.322));
    float Q = dot(c, vec3(0.211, -0.523, 0.312));
    float h = radians(u_hue), cs = cos(h), sn = sin(h);
    float I2 = I * cs - Q * sn, Q2 = I * sn + Q * cs;
    c = vec3(Y + 0.956 * I2 + 0.621 * Q2, Y - 0.272 * I2 - 0.647 * Q2, Y - 1.106 * I2 + 1.703 * Q2);
  }
  c.r += 0.1 * u_temp;
  c.b -= 0.1 * u_temp;
  if (u_invert > 0.5) c = 1.0 - c;
  return vec4(clamp(c, 0.0, 1.0), s.a);
}
