// Port of funk v1 effect 'blobs': lava-lamp metaballs on Lissajous orbits
uniform int   u_balls;   // @param 4 7 5
uniform float u_ax0;   // @param 0.3 0.8 0.55
uniform float u_ay0;   // @param 0.3 0.8 0.55
uniform int   k_fx0;   // @param 1 3 2
uniform int   k_fy0;   // @param 1 3 2
uniform float u_ph0;   // @param 0.0 6.2831853 3.0
uniform float u_ax1;   // @param 0.3 0.8 0.55
uniform float u_ay1;   // @param 0.3 0.8 0.55
uniform int   k_fx1;   // @param 1 3 2
uniform int   k_fy1;   // @param 1 3 2
uniform float u_ph1;   // @param 0.0 6.2831853 3.0
uniform float u_ax2;   // @param 0.3 0.8 0.55
uniform float u_ay2;   // @param 0.3 0.8 0.55
uniform int   k_fx2;   // @param 1 3 2
uniform int   k_fy2;   // @param 1 3 2
uniform float u_ph2;   // @param 0.0 6.2831853 3.0
uniform float u_ax3;   // @param 0.3 0.8 0.55
uniform float u_ay3;   // @param 0.3 0.8 0.55
uniform int   k_fx3;   // @param 1 3 2
uniform int   k_fy3;   // @param 1 3 2
uniform float u_ph3;   // @param 0.0 6.2831853 3.0
uniform float u_ax4;   // @param 0.3 0.8 0.55
uniform float u_ay4;   // @param 0.3 0.8 0.55
uniform int   k_fx4;   // @param 1 3 2
uniform int   k_fy4;   // @param 1 3 2
uniform float u_ph4;   // @param 0.0 6.2831853 3.0
uniform float u_ax5;   // @param 0.3 0.8 0.55
uniform float u_ay5;   // @param 0.3 0.8 0.55
uniform int   k_fx5;   // @param 1 3 2
uniform int   k_fy5;   // @param 1 3 2
uniform float u_ph5;   // @param 0.0 6.2831853 3.0
uniform float u_ax6;   // @param 0.3 0.8 0.55
uniform float u_ay6;   // @param 0.3 0.8 0.55
uniform int   k_fx6;   // @param 1 3 2
uniform int   k_fy6;   // @param 1 3 2
uniform float u_ph6;   // @param 0.0 6.2831853 3.0

uniform int   u_kaleido;  // @param 0 12 0 fixed
uniform int   k_spin;     // @param -3 3 0 fixed
uniform float u_zoom;     // @param 0.0 0.5 0.0 fixed

// look controls: breathing zoom, whole-picture spin (k_spin turns per loop), kaleidoscope fold
vec2 xform(vec2 q, float th) {
  if (u_zoom != 0.0) q *= 1.0 + u_zoom * sin(th);
  if (k_spin != 0) {
    float c = cos(float(k_spin) * th), s = sin(float(k_spin) * th);
    q = vec2(c * q.x - s * q.y, s * q.x + c * q.y);
  }
  if (u_kaleido >= 2) {
    float seg = TAU / float(u_kaleido);
    float a = abs(mod(atan(q.y, q.x), seg) - 0.5 * seg);
    q = length(q) * vec2(cos(a), sin(a));
  }
  return q;
}

vec4 effect(vec2 p) {
  float th = iPhase;
  vec2 q = xform(vec2(p.x, -p.y), th);
  float u = q.x, v = q.y;
  float ax[7] = float[7](u_ax0, u_ax1, u_ax2, u_ax3, u_ax4, u_ax5, u_ax6);
  float ay[7] = float[7](u_ay0, u_ay1, u_ay2, u_ay3, u_ay4, u_ay5, u_ay6);
  int   fx[7] = int[7](k_fx0, k_fx1, k_fx2, k_fx3, k_fx4, k_fx5, k_fx6);
  int   fy[7] = int[7](k_fy0, k_fy1, k_fy2, k_fy3, k_fy4, k_fy5, k_fy6);
  float ph[7] = float[7](u_ph0, u_ph1, u_ph2, u_ph3, u_ph4, u_ph5, u_ph6);
  float f = 0.0;
  for (int i = 0; i < 7; i++) {
    if (i >= u_balls) break;
    float cx = ax[i] * sin(float(fx[i]) * th + ph[i]);
    float cy = ay[i] * cos(float(fy[i]) * th + ph[i]);
    f += 0.05 / ((u - cx) * (u - cx) + (v - cy) * (v - cy) + 1e-3);
  }
  float lf = log(1.0 + f);
  float x = 0.35 * lf + 0.08 * sin(9.0 * lf);
  return vec4(palette(x), 1.0);
}
