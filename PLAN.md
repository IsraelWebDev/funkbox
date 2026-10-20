# funk v2: GPU shader engine (implementation plan)

**Audience:** an implementer model working alone. Follow this document literally. When it says
MUST, do exactly that. If something here turns out to be wrong in practice, follow the
"When stuck" rules at the bottom rather than improvising a redesign.

---

## 0. Context and goals

`funk.py` (v1) renders seamless looping videos from six hard-coded numpy effects on the CPU
(about 2.5 fps at 540px on the target laptop). v2 adds a **GPU shader engine** so that:

1. **The agent writes the effects.** Claude writes GLSL fragment shaders on demand and gets
   compiler errors and a PNG preview back through MCP.
2. **It is fast.** Measured on the target laptop (Intel Iris Xe, GL 3.3, through moderngl):
   1080x1080 frames render *and* read back at **74 fps**, about 100x v1.
3. **Loops are guaranteed.** Every shader is machine-checked for a seamless loop before use.
4. **Things compose.** Layers (shaders, still images, v1 effects), blend modes, post-effect
   passes and text are combined into one reproducible JSON recipe.

### Target machine (the user's laptop)
- Windows 11, i7-1255U, 16 GB RAM, Intel Iris Xe (shared memory). Python 3.12 venv at
  `C:\dev\funkbox\.venv`, managed with `uv`.
- `moderngl.create_standalone_context()` works there and reports `GL 3.3.0`.

### Build/test machine (where you work)
- Linux container with no display. `moderngl.create_standalone_context()` **fails** there
  (`XOpenDisplay`), but `moderngl.create_standalone_context(backend="egl")` **works** (Mesa
  llvmpipe, software GL 4.5). It is much slower than the laptop, so keep test sizes small
  (≤ 256px, ≤ 30 frames).
- ffmpeg is on PATH. Python 3.13 system-wide; install packages with
  `pip install --break-system-packages`.

### Non-goals (do NOT build these)
- No AI image generation inside funk (FastSD is a separate MCP server).
- No GUI or live preview window.
- No network access at runtime. No telemetry.
- No changes to `funk.py` behaviour or its CLI flags (v1 must keep working byte-for-byte).

---

## 1. Ground rules for the implementer

1. **Never modify `funk.py` or `funk_mcp.py`** except where a milestone says so explicitly.
   v2 lives in new files.
2. **Python ≥ 3.10.** Type hints on every public function. No new dependencies beyond:
   `moderngl>=5.10`, `numpy`, `pillow`, `imageio-ffmpeg`, `mcp`, `pytest` (dev only).
   Add `moderngl>=5.10` to `requirements.txt`.
3. **Run `git init` first** (if `.git` doesn't exist). Commit at the end of every milestone with
   the message `v2 M<n>: <title>`. Add `out/`, `.venv/`, `__pycache__/`, `*.mp4`, `*.gif` to
   `.gitignore`.
4. Each milestone ends with an **acceptance check**: run the listed commands, and only start
   the next milestone once they pass.
5. **Keep stdout clean in anything MCP-reachable.** Logs go to stderr (`print(..., file=sys.stderr)`)
   or the `logging` module configured to stderr. A stray `print()` corrupts the MCP stdio
   protocol.
6. **Windows-safe code:** use `pathlib` and no shell-specific commands. Spawn subprocesses
   with argument lists, never `shell=True`. Anything using `multiprocessing` must be
   top-level-picklable (Windows uses spawn). GL rendering is single-process; don't add
   multiprocessing to it.
7. **Seams are bugs.** If the loop validator fails on a bundled shader, fix the shader;
   don't loosen the threshold.

---

## 2. File layout (create exactly this)

```
funkbox/
  funk.py                 # v1, unchanged
  funk_mcp.py             # v1 MCP, unchanged until M5
  funkgl/
    __init__.py           # exports: Engine, Recipe2, load_recipe, render_recipe, validate_loop
    context.py            # GL context factory
    prelude.glsl          # uniforms + helpers prepended to every user shader
    noise.glsl            # vendored 2D/3D/4D simplex noise (MIT, with license header)
    shader.py             # ShaderSource: load/annotate/assemble/compile, error remapping
    engine.py             # Engine: renders layers + post passes into numpy frames
    blend.glsl            # composite pass: blend modes
    post/                 # one .frag per post effect
      chroma.frag  halftone.frag  posterize.frag  scanlines.frag  vignette.frag  grain.frag
    recipe.py             # Recipe2 dataclasses, JSON load/save, v1 recipe adapter
    loopcheck.py          # validate_loop()
    encode.py             # ffmpeg pipe to mp4/gif (moved logic, v1 untouched)
    text.py               # PIL text overlay (port of v1 _text with auto-fit)
    audio.py              # M6 only
  shaders/                # the bundled shader library (*.frag)
  funk2.py                # v2 CLI
  funk2_mcp.py            # v2 MCP server (M5)
  tests/
    conftest.py           # gl fixture (skips cleanly if no GL)
    test_context.py test_shader.py test_loop.py test_engine.py
    test_recipe.py test_cli.py test_mcp.py test_v1_regression.py
  PLAN.md  HANDOFF.md  README.md
```

---

## 3. Specifications

### 3.1 GL context (`context.py`)

```python
def create_context(prefer: str | None = None) -> moderngl.Context
```
- Order: if env `FUNK_GL_BACKEND` is set, use it (`"egl"` or `"default"`). Otherwise try
  `moderngl.create_standalone_context(require=330)`. On any exception, try
  `moderngl.create_standalone_context(require=330, backend="egl")`. If both fail, raise
  `GLUnavailable` (custom exception) carrying both error messages.
- Cache one context per process (module-level). Provide `gl_info() -> dict` returning
  `GL_RENDERER` and `GL_VERSION`.

### 3.2 Shader contract (`prelude.glsl`, `shader.py`)

User shaders are **fragment shaders without a `#version` line**. The engine assembles:
`#version 330` + prelude + noise + user source + (Shadertoy wrapper if needed).

**Uniforms every shader can use (declared in the prelude):**

| uniform | type | meaning |
|---|---|---|
| `iResolution` | vec3 | (width, height, 1.0), Shadertoy-compatible |
| `iLoop` | float | loop position t in [0, 1) |
| `iPhase` | float | 2π·t; **the only sanctioned way to animate** |
| `iTime` | float | t · seconds; Shadertoy compat; NOT loop-safe on its own |
| `iFrame` | int | frame index |
| `iSeed` | float | seed as float, for per-variation offsets |
| `iChannel0..3` | sampler2D | input textures: lower layers, images, audio |
| `iAudio` | vec4 | (bass, mid, treble, level) for the current frame, zeros if no audio |

**Helpers in the prelude (the implementer writes these):**
```glsl
vec2  uv();                     // centred coords, short side spans [-1,1] (same as v1)
vec3  pal(float x, vec3 a, vec3 b, vec3 c, vec3 d); // IQ cosine palette
vec3  palette(float x);         // uses u_pal_* uniforms set from recipe palette name
float loopNoise(vec2 p, float radius);  // snoise4(vec4(p, radius*cos(iPhase), radius*sin(iPhase)))
vec2  rot(vec2 p, float a);
float wrap01(float x);          // fract() that's explicit about intent
```
- `palette()` reads `uniform vec3 u_pal_a, u_pal_b, u_pal_c, u_pal_d; uniform int u_pal_cycles;`.
  The engine fills them from `funk.PALETTES` (import v1's dict, don't copy it) and adds
  `u_pal_cycles * iLoop` to the phase, exactly like v1. Reduce `c*x+d` with `fract()` before
  adding time (precision trick from v1).
- Noise: vendor `noise2D/3D/4D` from **ashima/webgl-noise** (MIT, Stefan Gustavson & Ian McEwan).
  Keep their license header verbatim at the top of `noise.glsl`. Rename to
  `snoise2/snoise3/snoise4` to avoid overload ambiguity.

**Two shader styles are accepted:**
1. *Native:* defines `vec4 effect(vec2 p)`, where `p = uv()`, and returns RGBA.
   The wrapper's `main()` calls it.
2. *Shadertoy:* defines `void mainImage(out vec4 fragColor, in vec2 fragCoord)`.
   Detect it with the regex `\bvoid\s+mainImage\s*\(`.
   If neither is found, raise `ShaderError("define vec4 effect(vec2 p) or mainImage(...)")`.

**Parameter annotations.** Uniform declarations may carry a range comment:
```glsl
uniform float u_freq;   // @param 2.0 9.0 4.0      (min max default)
uniform int   u_arms;   // @param 2 8 5
uniform vec3  u_tint;   // @param color #ff2bd6
```
- `shader.py` parses these with the regex
  `^\s*uniform\s+(float|int|vec3)\s+(\w+)\s*;\s*//\s*@param\s+(.+)$`.
- Seed sampling: `rng = np.random.default_rng(seed)`. Iterate params **in declaration order**.
  Floats are uniform in [min, max]; ints use `rng.integers(min, max+1)`; colors stay at their
  default. Recipe-provided values override sampled ones.
- **Integer warning:** if a param name starts with `k_` it must be an int multiplier of
  `iPhase`. Document this; no enforcement needed.

**Compile errors.** Compile with `ctx.program(...)`. On `moderngl.Error`, parse driver
messages for line numbers in all of these forms:
`0(LINE)`, `0:LINE`, `ERROR: 0:LINE:`, `LINE:COL`. Subtract the number of lines that precede
the user source, and return:
```python
@dataclass
class CompileResult:
    ok: bool
    errors: list[dict]   # {"line": int|None, "message": str}, line in USER coordinates
    program: moderngl.Program | None
```
Prepend nothing to the user's source that would shift lines unpredictably. Count the
prelude's lines once and store the offset. **Test this** (3.8 lists the test).

### 3.3 Engine (`engine.py`)

```python
class Engine:
    def __init__(self, width: int, height: int, ctx: moderngl.Context | None = None): ...
    def render(self, recipe: "Recipe2", frame: int) -> np.ndarray  # HxWx3 uint8, top row first
    def release(self) -> None
```
- Width and height MUST be even; round down with `w -= w % 2` like v1.
- Fullscreen geometry: a single triangle `[-1,-1, 3,-1, -1,3]`.
- Each layer renders into its own RGBA8 texture FBO at full resolution. Composite bottom→top
  into an accumulation texture with `blend.glsl` (ping-pong between two FBOs).
- Layer types:
  - `shader`: compile (cache programs by source hash), set uniforms, render.
  - `image`: load with PIL → RGBA → texture (cache by path+mtime), drawn with a `fit` of
    `cover|contain|stretch`. Optional `uv_warp` float: amount of loop-safe wobble,
    `p += 0.02*warp*vec2(sin(3*p.y + iPhase), cos(3*p.x + iPhase))`.
  - `v1`: call `funk.render_frame()` on the CPU and upload as a texture. This keeps all v1
    effects usable as layers.
- Inside a shader layer, `iChannel0` = the composite of all layers below it (enables
  feedback-style effects); `iChannel1..3` = images listed in that layer's `channels`.
- **Blend modes** in `blend.glsl` (uniform `int mode`): `normal, add, screen, multiply,
  overlay, difference, softlight, hue`. Use the standard formulas in straight-alpha space,
  then `mix(base, blended, opacity * src.a)`.
- Post passes run in recipe order after compositing, each a `.frag` in `funkgl/post/` with
  the same prelude. Port v1's behaviour: chroma (pulse with `sin(2*iPhase)`), halftone,
  posterize, scanlines, vignette, grain. **Grain must be loop-safe**: seed it from
  `iFrame % frames`. Exact equality at the wrap isn't required for grain, but it must not
  flicker on a pattern different from the rest of the loop.
- Readback: `fbo.read(components=3, alignment=1)`, then `np.frombuffer(...).reshape(h, w, 3)`,
  then **flip vertically** (`[::-1]`), because GL's origin is bottom-left. Return a contiguous
  array (`np.ascontiguousarray`).
- The text overlay (`text.py`) is applied after readback on the CPU, ported from v1 `_text`
  including auto-fit and `\n` handling.

### 3.4 Recipe v2 (`recipe.py`)

JSON on disk. Example:
```json
{
  "version": 2,
  "settings": {"width": 720, "height": 720, "fps": 30, "seconds": 4, "seed": 7},
  "palette": {"name": "vapor", "cycles": 1},
  "layers": [
    {"type": "shader", "shader": "shaders/warp.frag", "params": {"u_amp": 0.4}},
    {"type": "image", "path": "inputs/cat.png", "fit": "contain", "blend": "screen",
     "opacity": 0.8, "uv_warp": 1.0},
    {"type": "v1", "effect": "blobs", "blend": "add", "opacity": 0.5}
  ],
  "post": [{"name": "chroma", "px": 4}, {"name": "grain", "amount": 0.03}],
  "text": {"text": "FUNK", "size": 0.13, "wobble": 6, "color": "#ffffff", "shadow": "#ff2bd6"}
}
```
- Dataclasses: `Settings2`, `Layer`, `PostPass`, `TextSpec`, `Recipe2`. Each `Layer` has
  `type`, `blend="normal"`, `opacity=1.0`, plus type-specific fields; unknown keys go into
  `extra: dict`, so they round-trip without data loss.
- A shader may be given as `"shader": "<path>"` **or** `"source": "<inline glsl>"`. On save
  after a render, **always also write the inline `source`** plus `"source_sha256"`, so the
  sidecar fully reproduces the render even if the `.frag` file later changes.
- On save, record the resolved params (seed-sampled plus overrides).
- `load_recipe(path_or_dict)` accepts:
  - v2 JSON;
  - **v1 sidecar JSON** (has `recipe.effect`), converted to one `v1` layer with v1 post
    options mapped to v2 post passes.

  Write `recipe_from_v1(v1_dict) -> Recipe2`.
- Path resolution: relative paths resolve against the recipe file's directory, then
  `FUNK_HOME` (default: the funkbox folder). Image paths must stay inside `FUNK_HOME`,
  `FUNK_OUT` or `FUNK_INPUTS` once resolved, otherwise raise `ValueError` (the MCP server
  must not read arbitrary files).

### 3.5 Loop validator (`loopcheck.py`)

```python
def validate_loop(recipe: Recipe2, size: int = 192, frames: int | None = None) -> LoopReport
@dataclass
class LoopReport:
    ok: bool
    max_diff: int          # 0..255, frame0 vs frame N (t=1)
    bad_pixel_frac: float  # fraction of pixels with any channel diff > 3
    seam_step: float       # mean abs diff between frame N-1 and frame 0
    typical_step: float    # mean abs diff of consecutive frames, sampled every k frames
    reason: str
```
- Render with grain disabled. Render frame 0 and "frame N" by passing `t = 1.0` exactly.
  Add `render_at_t(recipe, t: float)` to the engine for this.
- **Pass** if `bad_pixel_frac <= 0.001` **and** `seam_step <= 2.5 * typical_step + 0.5`.
  These thresholds come from v1 testing; don't change them.
- `reason` names the failing condition in plain English. Example: "frame at t=1 differs from
  t=0 in 2.3% of pixels; you probably animate with iTime, or multiply iPhase by a
  non-integer".
- **Static-shader trap:** if `typical_step < 0.05`, report `ok=True` but set
  `reason="static: nothing moves"`, so the MCP layer can warn.

### 3.6 Encoding (`encode.py`)

Reuse v1's approach. Import `funk.ffmpeg_exe()`; don't duplicate it.
```python
def encode(frames: Iterable[np.ndarray], w, h, fps, out_base: Path,
           mp4=True, gif=True, gif_width=400) -> dict[str, str]
```
- Write raw rgb24 into ffmpeg stdin with `stdout=subprocess.DEVNULL`. Use the same libx264
  settings as v1 (crf 20, yuv420p, faststart). GIF uses the same palettegen chain as v1
  (fps ≤ 20).
- Also write a poster PNG (frame 0) and the recipe sidecar JSON (3.4).

### 3.7 CLI (`funk2.py`)

```
python funk2.py info                                    # GL renderer/version, shader list
python funk2.py check  shaders/foo.frag                 # compile + loop check, exit 1 on failure
python funk2.py still  --recipe r.json [--t 0.25] [--seed N] [--size 512] -o out/x.png
python funk2.py sheet  --recipe r.json --seeds 1-9 [--cols 3] -o out/sheet.png
python funk2.py sheet  --shader shaders/foo.frag --seeds 1-9    # quick sweep, one-layer recipe
python funk2.py render --recipe r.json [--seed N] [--size 720] [--seconds 6] [--name x]
python funk2.py render --shader shaders/foo.frag [--palette vapor] [--post chroma=4 grain=0.03]
python funk2.py port-v1                                  # writes shaders/*.frag ports (M2)
```
- `--shader` builds a one-layer recipe on the fly. `--post key=val` pairs map to post passes.
- `render` prints a timing line like v1 (`N frames WxH in Xs (Y fps)`) and the output paths.
- Exit codes: 0 ok, 1 validation/compile failure (message on stderr), 2 bad usage.

### 3.8 Tests (pytest)

`tests/conftest.py` provides a session-scoped `gl` fixture that calls `create_context()` and
`pytest.skip`s with the reason on `GLUnavailable`. In `conftest.py`, before any import,
apply `os.environ.setdefault("FUNK_GL_BACKEND", "egl")` **only when**
`sys.platform == "linux"` and `"DISPLAY" not in os.environ`. Windows must use the default
backend.

Required tests (minimum):
1. `test_context`: the context is created; renderer string is non-empty.
2. `test_shader`:
   - (a) A known-good native shader compiles.
   - (b) A shader with an error on **user line 3** reports `line == 3`.
   - (c) Shadertoy-style detection works.
   - (d) Neither entry point raises `ShaderError`.
   - (e) `@param` parsing returns names, types and ranges in declaration order.
   - (f) Seed sampling is deterministic and different seeds give different values.
3. `test_engine`:
   - (a) Output shape and dtype are correct and the array is contiguous.
   - (b) A shader that returns red only in the **top** half produces red in rows 0..h/2:
     this catches the Y-flip bug.
   - (c) Blend `difference` of two identical layers is ~black.
   - (d) An image layer round-trips: a solid colour PNG with `fit=stretch` reads back
     within ±2.
4. `test_loop`:
   - (a) Every file in `shaders/` passes `validate_loop` (parametrize over the files).
   - (b) A deliberately broken shader using `iTime * 1.3` fails with a reason mentioning
     iTime.
   - (c) A constant-colour shader reports `static`.
5. `test_recipe`:
   - (a) v2 round-trip: save, load, equal.
   - (b) A v1 sidecar from `out/*.json` (generate one with `funk.py render ... --seconds 0.5
     --size 64`) loads as a one-layer recipe and renders.
   - (c) A path outside the roots raises `ValueError`.
6. `test_cli`: run `funk2.py render --shader shaders/plasma.frag --size 64 --seconds 0.5`
   in a subprocess and assert the mp4 exists. Check with `ffprobe` (from
   `imageio_ffmpeg`, or skip) that the frame count is 15 at fps 30.
7. `test_mcp`: (M5) start `funk2_mcp.py` over stdio with the `mcp` client (the pattern is in
   section 6) and call each tool once. Assert there's no `is_error`, and that the image
   content is present where promised.
8. `test_v1_regression`: run `funk.py render --preset lava-lamp --seed 2 --seconds 0.5 --size
   64 --no-gif` twice and assert the PNGs are byte-identical. Also assert the `funk.py` file
   hash equals the hash recorded in `tests/v1.sha256` (write that file in M0).

Run: `python -m pytest -q` (conftest picks the backend). All must pass before a milestone is done.

---

## 4. Milestones

### M0: Scaffolding (small)
- `git init`, `.gitignore`, create `funkgl/` and `tests/` skeletons, add `moderngl` to
  `requirements.txt`.
- Write `tests/v1.sha256` containing `sha256(funk.py)`.
- **Accept:** `python -m pytest -q` runs. Zero tests or skips are fine; it must not error.

### M1: GL core
- `context.py`, `prelude.glsl`, `noise.glsl`, `shader.py` (assemble, compile, errors,
  params), and a minimal `Engine.render` for a single shader layer with no blend or post.
- **Accept:** tests 1, 2(a–f) and 3(a, b) pass. Then run
  `python -c "from funkgl import Engine; ..."`, render 30 frames at 256px with a plasma
  shader, and print the fps.

### M2: Shader library + loop validator
- Port the six v1 effects to `shaders/` (`plasma, moire, warp, tunnel, blobs, rings`), matching
  v1 maths. Their params become `@param` uniforms with v1's sampling ranges. The tunnel MUST
  keep v1's two fixes:
  - reduce `depth` with `fract`;
  - add the `+0.37` angular offset.
- Add four new shaders that show off GPU-only things v1 can't do cheaply:
  - `fbm_flow.frag`: domain-warped fBm using `loopNoise`;
  - `voronoi_pop.frag`: animated Voronoi cells with cell-ID palette;
  - `raymarch_blob.frag`: a small SDF raymarch of a morphing torus/sphere, ≤ 64 steps;
  - `feedback_echo.frag`: samples `iChannel0` with zoom+rotate for trails. Note: it
    loops only if the lower layers do; skip it in test 4(a) via a `// @noloopcheck` marker,
    and document why.
- Implement `loopcheck.py`, plus `funk2.py check` and `funk2.py port-v1`. `port-v1` is
  idempotent: it overwrites the six files.
- **Accept:** test 4(a–c) passes, and `python funk2.py check shaders/*.frag` exits 0
  (loop over the files in Python; don't rely on shell globbing on Windows).

### M3: Composition
- Recipe dataclasses and IO (3.4), all layer types, blend modes, post passes, text, and
  `render_at_t`.
- **Accept:** tests 3(c, d) and 5(a–c) pass. A sample recipe `examples/disco_cat.json`, using a
  generated test image so no external assets are needed, renders to mp4.

### M4: CLI + encode
- `encode.py` and the full `funk2.py`.
- **Accept:** test 6 passes. Run `python funk2.py sheet --shader shaders/voronoi_pop.frag
  --seeds 1-6 -o out/vp.png` and open the PNG (read the image) to confirm 6 distinct tiles.

### M5: MCP server (`funk2_mcp.py`)
Use the same import shim as `funk_mcp.py` (mcp 2.x `MCPServer`, falling back to 1.x
`FastMCP`). Tools:

| tool | args | returns |
|---|---|---|
| `gl_info` | (none) | renderer, version, shader names |
| `list_looks` | (none) | v1 presets/effects/palettes + v2 shaders with their `@param`s + post passes + blend modes |
| `write_shader` | `name: str, source: str, check: bool = True` | text: compile result (errors with user line numbers) + loop report; image: a 384px still at t=0.25 if it compiled. Saves to `shaders/<name>.frag` **only if** it compiles. `name` must match `^[a-z0-9_\-]{1,40}$` |
| `get_shader` | `name` | its source text |
| `preview` | `recipe: dict, t: float = 0.25, size: int = 448` | text summary + image |
| `contact_sheet` | `recipe: dict, seeds: str = "1-6"` | image (≤ 900px wide) + saved path |
| `render` | `recipe: dict, name: str = ""` | paths + timing + poster image. Enforce limits: ≤ 1080px side, ≤ 20 s, ≤ 60 fps |
| `render_v1` | same args as v1 `render_loop` | delegate to `funk.render` (keeps v1 reachable) |

- `recipe` may omit `settings`; defaults are 540px, 30 fps, 4 s, seed 1.
- All tools catch exceptions and return a readable error string, never a traceback dump.
- Paths: env `FUNK_OUT` (default `<funkbox>/out`), `FUNK_INPUTS` (default
  `<funkbox>/inputs`, created on start).
- The server `instructions` string MUST teach the loop contract in two sentences: animate
  only through `iPhase` times integers, or through `loopNoise`; `iTime` breaks loops.
- **Accept:** test 7 passes.

### M6: Audio-reactive (stretch, do last, skippable)
- `audio.py`: read a WAV with the stdlib `wave` module plus numpy (no new deps), mono-mix,
  then for each frame compute an FFT of a Hann window centred on the frame time. Output
  `iAudio = (bass 20–150 Hz, mid 150–2k, treble 2k–8k, rms)`, each normalised to 0..1 by its
  95th percentile. Also produce a 512x1 texture of log-spaced magnitudes bound as
  `iChannel3` when `settings.audio` is set.
- With audio set, `seconds` defaults to the audio duration and the loop check is skipped,
  with a warning: audio loops aren't seamless unless the music is.
- Mux the audio into the mp4 (`-i audio.wav -c:a aac -shortest`).
- **Accept:** a unit test with a generated 2 s sine at 100 Hz gives `bass > treble` in every
  frame.

### M7: Docs + wiring (no deployment)
- Update `README.md` with a v2 section: quickstart, shader contract table, recipe example,
  and the MCP tools table.
- Do **not** edit the user's Claude config or touch any machine other than the container.
  The supervising agent handles deployment to the laptop.
- **Accept:** the full test suite is green. Then append a short `BUILD_REPORT.md` covering:
  - what was built;
  - test results (paste the pytest summary line);
  - measured fps at 512px for each bundled shader (container numbers are fine, label them
    "llvmpipe");
  - any deviations from this plan, each with its reason.

---

## 5. Pitfalls (read before coding)

| Pitfall | Symptom | Fix |
|---|---|---|
| GL origin is bottom-left | image upside down | flip on readback; test 3(b) |
| `fbo.read` row alignment | sheared image at odd widths | `alignment=1`, even dims |
| Line numbers include prelude | errors point to wrong line | subtract stored offset |
| `iTime` in shaders | seam at loop point | use `iPhase*int`; validator catches it |
| float precision far from origin | sparkly seam (v1 tunnel bug) | `fract()` before adding phase |
| `sign()`/`step()` on exact symmetry lines | single-pixel flips at wrap | small constant phase offset (v1 used +0.37) |
| Unused uniform optimised away | `KeyError` setting it | `if name in program:` before assigning |
| stdout writes in MCP server | client hangs / protocol error | stderr only; ffmpeg stdout → DEVNULL |
| Windows path in recipe JSON | backslash escapes | always `Path(...).as_posix()` when saving |
| Texture cache stale | edited image not picked up | key cache on `(path, mtime_ns)` |
| GL objects leak in long MCP sessions | memory growth | `Engine.release()`; reuse one Engine per size |

---

## 6. Reference snippets

**MCP stdio test client** (works with mcp 2.x; attribute is `is_error`, not `isError`):
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
params = StdioServerParameters(command=sys.executable, args=["funk2_mcp.py"],
                               env={**os.environ, "FUNK_OUT": str(tmp)})
async with stdio_client(params) as (r, w):
    async with ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("preview", {"recipe": {...}})
        assert not res.is_error
```

**Returning an image from a tool:** copy `_png()` from `funk_mcp.py`.

**Minimal moderngl render** (verified on the laptop at 74 fps for 1080²):
```python
vbo = ctx.buffer(np.array([-1,-1, 3,-1, -1,3], dtype="f4"))
vao = ctx.simple_vertex_array(prog, vbo, "p")
fbo = ctx.simple_framebuffer((w, h)); fbo.use(); vao.render()
img = np.frombuffer(fbo.read(components=3, alignment=1), np.uint8).reshape(h, w, 3)[::-1]
```

---

## 7. When stuck

- If a spec detail is impossible as written, choose the smallest change that keeps every
  test meaningful. Then record it under "Deviations" in `BUILD_REPORT.md`.
- If a test fails three times in a row after real fixes, stop. Write what you tried in
  `BUILD_REPORT.md` and finish with the milestone marked BLOCKED. Don't fake, skip or loosen
  tests to get green.
- Never edit `funk.py` to make something easier.
- Don't add dependencies. If you feel you need one, write the case for it in
  `BUILD_REPORT.md` and continue without it.

## 8. Definition of done
- M0–M5 and M7 complete; M6 complete or explicitly skipped in the report.
- `python -m pytest -q` is all green, with v1 regression included.
- `BUILD_REPORT.md` is present. There's one git commit per milestone.
