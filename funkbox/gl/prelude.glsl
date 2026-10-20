// ---- funk prelude: prepended to every shader (after "#version 330") ----
// Animate ONLY through iPhase * (integer), or loopNoise(); iTime does not loop.
#define PI  3.14159265358979323846
#define TAU 6.28318530717958647692

uniform vec3  iResolution;   // (width, height, 1.0)
uniform float iLoop;         // loop position t in [0,1)
uniform float iPhase;        // 2*pi*t
uniform float iTime;         // t * seconds (NOT loop-safe on its own)
uniform int   iFrame;        // frame index
uniform float iSeed;         // seed as float
uniform sampler2D iChannel0; // layers below / post input
uniform sampler2D iChannel1;
uniform sampler2D iChannel2;
uniform sampler2D iChannel3;
uniform vec4  iAudio;        // (bass, mid, treble, level)
uniform int   u_frames;      // frames per loop (engine internal, used by grain)
uniform vec3  u_pal_a;
uniform vec3  u_pal_b;
uniform vec3  u_pal_c;
uniform vec3  u_pal_d;
uniform int   u_pal_cycles;

out vec4 fragColor;

// centred coords, short side spans [-1,1]; +y points UP in the output image
vec2 uv() {
  return (gl_FragCoord.xy - 0.5 * iResolution.xy) / (0.5 * min(iResolution.x, iResolution.y));
}
// inverse of uv(): centred coords -> texture coordinate in [0,1]
vec2 toTex(vec2 p) {
  return p * (0.5 * min(iResolution.x, iResolution.y)) / iResolution.xy + 0.5;
}
// sample iChannel0 at centred coords p
vec4 src0(vec2 p) { return texture(iChannel0, toTex(p)); }

// Inigo Quilez cosine palette
vec3 pal(float x, vec3 a, vec3 b, vec3 c, vec3 d) {
  return a + b * cos(TAU * (c * x + d));
}
// recipe palette; rotates u_pal_cycles times per loop. Reduce c*x+d with fract() BEFORE
// adding time so float precision survives large x.
vec3 palette(float x) {
  vec3 ph = fract(u_pal_c * x + u_pal_d) + float(u_pal_cycles) * iLoop;
  return u_pal_a + u_pal_b * cos(TAU * ph);
}
vec2 rot(vec2 p, float a) {
  float c = cos(a), s = sin(a);
  return vec2(c * p.x - s * p.y, s * p.x + c * p.y);
}
float wrap01(float x) { return fract(x); }

