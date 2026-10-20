# Loops: seamless shader loops

GPU shader art that loops exactly: **MP4 / GIF / PNG** from GLSL fragment shaders, composed as layers with blend
modes, post passes and text, with a machine check that the loop is seamless. No accounts, no API keys, no credits,
no NVIDIA GPU (software OpenGL works). Use it from the command line (`python -m funkbox loop ...`) or by Claude over
MCP (the `loop_*` tools). On the target laptop (Iris Xe, GL 3.3) a 1080x1080 frame renders and reads back at ~74 fps.

## Setup

```
python install.py --register desktop          # or --register code; see AGENTS.md
```

That creates a `.venv`, installs numpy / Pillow / a bundled ffmpeg / the MCP SDK / moderngl, renders a smoke test and
registers the one `funkbox` server (a timestamped backup of the Claude config is written first). On a headless Linux
box set `FUNK_GL_BACKEND=egl` (Mesa llvmpipe works, slowly); on Windows leave it unset. `funkbox doctor` shows what
was detected.

## Command line

```
python -m funkbox loop list                                      # presets, shaders, palettes, blend modes, look keys
python -m funkbox loop sheet  --preset hypno-tunnel --seeds 1-9  # pick a variation
python -m funkbox loop sheet  --random --seeds 1-12 --cols 4     # roll whole random looks
python -m funkbox loop render --preset liquid-disco --seed 7
python -m funkbox loop render --random --seed 5 --text "FUNK WAS HERE"
python -m funkbox loop render --preset lava-lamp --set palette=toxic kaleido=6 chroma=5
python -m funkbox loop render --recipe out/hero-sun-mandala.json  # reproduce anything exactly
python -m funkbox loop check  shaders/plasma.frag                  # compile + loop check, exit 1 on failure
python -m funkbox loop sheet  --shader shaders/voronoi_pop.frag --seeds 1-9 -o out/vp.png
python -m funkbox loop still  --recipe examples/disco_cat.json --t 0.25 -o out/x.png
python -m funkbox loop render --shader shaders/plasma.frag --palette vapor --post chroma=4 grain=0.03
python -m funkbox loop render --recipe examples/disco_cat.json --size 720 --seconds 6   # add --gif for a GIF too
python -m funkbox loop write-shader --ports                        # rewrite the six shaders/*.frag classic looks
```

`loop render` runs the loop check first and refuses a broken loop (`--no-check` to override). Every render writes
`<name>.json`: a recipe with the shader source inlined, so it reproduces the render even if the `.frag` changes later.
Older sidecars written by funk v1 load too (they become shader-layer recipes). Defaults: 540x540, 30 fps, 4 s.

## Presets

The eight classic looks are recipes (`funkbox/loops/presets.py`): `acid-plasma`, `pop-moire`, `liquid-disco`,
`hypno-tunnel`, `lava-lamp`, `op-art`, `frozen-kaleido`, `party-poster`. A preset is a shader (one of the six ports
below), a palette, kaleido/spin/zoom as shader params and a stack of post passes plus optional text.
`funkbox loop list` (or the `loop_list` tool) prints them. `docs/history/v1-to-v4-presets.png` compares them with the
retired v1 CPU renderer: same looks, not the same pixels.

## MCP tools

| tool | use |
|---|---|
| `loop_list` | shaders with `@param` ranges, post passes, blend modes, layer types, palettes, preset recipes |
| `loop_preview` | one frame of a recipe, fast. Iterate on the look here |
| `loop_sheet` | grid across seeds to choose a variation |
| `loop_render` | the real MP4 + PNG + JSON (GIF opt-in) |
| `loop_write_shader`, `loop_get_shader` | write GLSL with line-numbered errors and the loop check; read a shader |
| `loop_still` | one frame of a recipe or shader as a poster-sized PNG |
| `loop_gl_info` | renderer, version, shader names, folders |

Each tool returns a PNG preview with its text, so Claude sees the result and can critique it ("too busy, drop
halftone, push chroma") instead of rendering blind.

Try: *"Make a 6-second 720px loop for a party invite: something hypnotic and warm, with the text SHABBAT PARTY. Show
me 6 options first."*

## Look keys

A preset or `--set key=value` is made of these keys (the `--set` names are the same as the old v1 recipe fields):

| key | effect |
|---|---|
| `effect` | plasma, moire, warp, tunnel, blobs, rings |
| `palette` | acid, vapor, sunset, toxic, candy, ice, mono |
| `cycles` | palette rotations per loop (int) |
| `spin` | rotations per loop (int, negative = counter-clockwise): the shader param `k_spin` |
| `zoom` | breathing zoom amplitude (0.1–0.3): `u_zoom` |
| `kaleido` | mirror segments (4, 6, 8...): `u_kaleido` |
| `posterize` | colour levels (5–10 = screenprint) |
| `halftone` | dot cell size in px (pop-art) |
| `chroma` | RGB split px (pulses with the loop) |
| `scanlines`, `vignette`, `grain` | CRT dressing |
| `text`, `text_size`, `text_wobble`, `text_color`, `shadow` | wavy title; auto-fits width, `\n` for line breaks |

## Why the loops are seamless

Time enters every shader only as `iPhase = 2*pi*t`, multiplied by an integer (or through `loopNoise`). Frame N is
therefore frame 0, and is pixel-identical to it except for grain. The loop check renders t=0 and t=1 and compares
them. Float traps are handled in the ports: the palette phase is reduced mod 1 *before* time is added so large field
values keep precision, and the tunnel's `sign()` edges are nudged off the exact symmetry diagonals, where `sin(0)` vs
`sin(2*pi) = -2e-16` would flip pixels between the first and last frame.

## Finishing in other apps (optional)

Loops are a base layer: point another MCP app's read root at the output folder, or use `poster_*` and `image_edit`
(see [stills.md](stills.md)) for layout, typography and compositing.

## Adding an effect

Write a shader (`funkbox loop write-shader NAME --file x.frag`, or the `loop_write_shader` tool):

```glsl
uniform int k_arms; // @param 2 7 4
vec4 effect(vec2 p) {
  float r = length(p) + 1e-3, a = atan(p.y, p.x);
  float f = 0.5 + 0.5 * sin(float(k_arms) * a + 6.0 * log(r) - 2.0 * iPhase);
  return vec4(palette(f), 1.0);
}
```

Keep every use of `iPhase` multiplied by an integer and the loop stays seamless. The seed samples the `@param`
values, so contact sheets show variations. Params flagged `fixed` (`// @param 0 12 0 fixed`, like the look controls
`u_kaleido`, `k_spin`, `u_zoom` on the six classic shaders) are never sampled: they keep their default or the recipe's value.

## Shader engine

## Shader contract

User shaders are fragment shaders **without a `#version` line**; define `vec4 effect(vec2 p)` (p = `uv()`)
or Shadertoy's `void mainImage(out vec4 fragColor, in vec2 fragCoord)`.

| name | type | meaning |
|---|---|---|
| `iResolution` | vec3 | (width, height, 1.0) |
| `iLoop` | float | loop position t in [0, 1) |
| `iPhase` | float | 2*pi*t. **The only sanctioned way to animate: always multiply it by an integer** |
| `iTime` | float | t * seconds. Shadertoy compat; **breaks loops** |
| `iFrame` | int | frame index |
| `iSeed` | float | the seed, for per-variation offsets |
| `iChannel0` | sampler2D | everything composited below this layer (post passes: their input) |
| `iChannel1..3` | sampler2D | images listed in the layer's `channels`; with audio, `iChannel3` is the 512x1 log spectrum |
| `iAudio` | vec4 | (bass, mid, treble, level) 0..1 for this frame; zeros without audio |
| `uv()` | vec2 | centred coords, short side spans [-1, 1], **+y points up** |
| `palette(x)` | vec3 | the recipe palette; rotates `cycles` times per loop |
| `pal(x,a,b,c,d)` | vec3 | Inigo Quilez cosine palette |
| `loopNoise(p, r)` | float | 2D simplex noise that loops exactly (circle of radius r in 4D noise) |
| `snoise2/3/4` | float | ashima/webgl-noise simplex noise (MIT) |
| `rot(p,a)`, `wrap01(x)`, `src0(p)` | | rotate; fract; sample `iChannel0` at centred coords |

Parameters: `uniform float u_freq; // @param 2.0 9.0 4.0` (min max default), `uniform int u_arms; // @param 2 8 5`,
`uniform vec3 u_tint; // @param color #ff2bd6`. The recipe seed samples floats/ints in declaration order;
explicit recipe `params` win. **Name integer multipliers of `iPhase` `k_*`** (a non-integer one breaks the loop).
Opt out of the automatic loop test with a `// @noloopcheck` comment (used by `feedback_echo.frag`, which loops
only if the layers beneath it do). Fix a failing loop in the shader; never loosen the checker.

Bundled: `plasma moire warp tunnel blobs rings` (the classic looks, with `u_kaleido`/`k_spin`/`u_zoom` controls) and
`fbm_flow voronoi_pop raymarch_blob feedback_echo` (GPU-only looks).

## Recipe (JSON)

```json
{
  "version": 2,
  "settings": {"width": 720, "height": 720, "fps": 30, "seconds": 4, "seed": 7},
  "palette": {"name": "vapor", "cycles": 1},
  "layers": [
    {"type": "shader", "shader": "shaders/warp.frag", "params": {"u_amp": 0.4}},
    {"type": "image", "path": "cat.png", "fit": "contain", "blend": "screen", "opacity": 0.8, "uv_warp": 1.0},
    {"type": "shader", "shader": "shaders/blobs.frag", "blend": "add", "opacity": 0.5}
  ],
  "post": [{"name": "chroma", "px": 4}, {"name": "grain", "amount": 0.03}],
  "text": {"text": "FUNK", "size": 0.13, "wobble": 6, "color": "#ffffff", "shadow": "#ff2bd6"}
}
```

Blend modes: `normal add screen multiply overlay difference softlight hue`. Post passes: `chroma halftone
posterize scanlines vignette grain`. Relative paths resolve against the recipe's folder, then `FUNK_HOME`;
**files must live inside `FUNK_HOME`, `FUNK_OUT` or `FUNK_INPUTS`** (drop your images in `inputs/`). Optional
`"audio": "song.wav"` in `settings` (PCM WAV) drives `iAudio`/`iChannel3`, defaults `seconds` to the clip
length, is muxed into the mp4, and skips the loop check (audio loops are not seamless unless the music is).
`examples/disco_cat.json` is a runnable sample; `python examples/make_cat.py` regenerates its image.

A legacy layer `{"type": "v1", "effect": "blobs"}` is still accepted and converted to the matching shader layer when
the recipe loads. Per-layer `palette` and `cycles` (as `extra` keys of a layer) are honoured by the engine.

Tools never raise: failures come back as a readable `ERROR: ...` string. Nothing is ever printed to stdout (it is the
MCP protocol).

## Tests

```
python -m pytest -q        # picks the EGL backend automatically on headless Linux
```
