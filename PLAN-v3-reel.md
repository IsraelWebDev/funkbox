# funk v3 "reel": short vertical ads from a shot list (implementation plan)

**Audience:** an implementer model working alone. This plan has the same rules as `PLAN.md` (v2).
Read that first: sections 1 (ground rules), 5 (pitfalls) and 7 (when stuck) apply here unchanged.
MUST means exactly that. If something here is impossible in practice, follow "When stuck";
don't redesign.

---

## 0. Goal

Turn a JSON shot list into a polished 9:16 ad like the reference (20 s, ~12 shots of ~1.5 s each,
boxed animated captions, a product in every shot, music or voiceover, a branded end card). It must
run on the user's laptop (Intel Iris Xe, no NVIDIA, 16 GB), be **free**, and need **no accounts**.

Reference anatomy (measured from the user's sample):

| element | reference | v3 delivers |
|---|---|---|
| format | 480x848 to 1080x1920, 30 fps, 20 s | 1080x1920 default, any size |
| shots | 12 cuts, median 1.5 s | shot list with durations or beat snapping |
| motion | AI video with people moving and camera pushes | **stills with 2.5D parallax / Ken Burns**, plus any user-supplied video clips |
| product | the same bottle in every shot, exact label | **the real product photo composited in** (never AI-drawn) |
| captions | red box, white condensed caps, typewriter reveal | caption styles + reveals, exact copy |
| audio | track for 0–15 s, silence on the end card | music bed + local TTS voiceover + ducking + loudness normalisation |
| end card | brand colour, serif headline typed on, URL | end-card templates |

### Non-goals
- No AI video generation. A shot whose `source` is a video file is supported, so options 2 and
  3 (a paid image-to-video service, or a GPU box) plug in later without engine changes.
- No AI-drawn text, ever. Text reaches pixels **only** through the copy deck (section 3.1).
- No GUI.

---

## 1. Ground rules (in addition to PLAN.md section 1)

1. Don't modify `funk.py`, `funk_mcp.py`, `funkgl/` internals or `funk2*.py`. v3 lives in
   `reel/`, `reel_cli.py` and `reel_mcp.py`. Import `funkgl` (Engine, recipes, text
   helpers, encode) and build on it. If funkgl truly needs a hook, add a backwards-compatible
   function and note it in the report.
2. **Allowed new dependencies, and only these:**
   - `onnxruntime` (CPU) for depth estimation;
   - `piper-tts` for voiceover.

   Both are optional at import time: features degrade with a clear error message, and tests
   use stubs when the packages or models are missing (the container may not reach Hugging
   Face). Add them to `requirements-reel.txt`, **not** `requirements.txt`.
3. Models are downloaded **only** by `python reel_cli.py setup-models`, which the user runs
   once. That command fetches the files over plain HTTPS from Hugging Face (no token), puts
   them in `models/`, checks the SHA-256 hashes recorded in `reel/models.json` and prints
   each model's licence. Never download at render time.
   - **Depth:** Depth-Anything-V2-**Small** in ONNX form. The Small model is Apache-2.0; the
     Base and Large models are non-commercial, so MUST NOT be used. Record the exact repo
     and file and its hash once you've found them. If no Apache-2.0 ONNX export is
     available, use MiDaS v2.1 small (MIT) and say so in the report.
   - **Voice:** one English Piper voice. Read its `MODEL_CARD`. Pick a voice whose dataset
     licence allows commercial use, record the licence string in `models.json`, and have
     `setup-models` print it. If you can't confirm one, ship TTS disabled by default and
     explain in the report.
4. **Exact text is a hard guarantee.** Shots and templates reference copy by ID. The validator
   rejects literal text anywhere except in the copy deck. A test (4.R5) asserts that every
   rendered string is byte-identical to its copy entry.

---

## 2. File layout

```
funkbox/
  reel/
    __init__.py
    schema.py        # dataclasses + JSON load/validate (Reel, Shot, Caption, Brand, Audio, EndCard)
    copydeck.py      # load copy.json, resolve IDs, exactness checks
    timeline.py      # durations, beat snapping, VO alignment -> absolute shot start/end times
    depth.py         # ONNX depth estimation + cache; returns HxW float32 in [0,1] (1 = near)
    motion.py        # Ken Burns and 2.5D parallax as GL passes (uses funkgl Engine context)
    product.py       # product cut-out compositing: placement, contact shadow, colour match
    captions.py      # caption styles + reveal animations -> RGBA overlay per frame
    endcard.py       # end-card templates
    audio.py         # TTS (piper), music, beat detection, ducking, loudnorm mix -> wav
    render.py        # orchestrates frames -> encode (reuse funkgl.encode pipe) + audio mux
    storyboard.py    # one still per shot -> contact sheet PNG (fast preview)
    models.json      # model URLs, sha256, licences
  reel_cli.py
  reel_mcp.py
  templates/
    endcards/*.json  # at least: "brand-split" (like the reference), "centered-logo"
    captions/*.json  # at least: "red-box", "lower-third", "bold-pop"
  examples/reel_demo/   # synthetic assets made by examples/reel_demo/make_assets.py
  tests/reel/...
```

---

## 3. Specifications

### 3.1 Copy deck (`copy.json`), the single source of on-screen and spoken text

```json
{
  "brand_name": "GLOWMOP",
  "lines": {
    "hook":     {"text": "LOOK CLOSER.\nTIME FOR A CLEAN?", "say": "Look closer. Time for a clean?"},
    "tackle":   {"text": "TACKLE CARPET CLEANING WITH GLOWMOP.", "say": "Tackle carpet cleaning with GLOWMOP."},
    "care":     {"text": "GIVE YOUR CARPET THE ATTENTION IT NEEDS."},
    "end_head": {"text": "Deep\nCleaning\nPower"},
    "end_sub":  {"text": "PROFESSIONAL RESULTS"},
    "url":      {"text": "GLOWMOP.EXAMPLE"}
  },
  "pronounce": {"GLOWMOP": "glow mop"}
}
```
- `text` is what gets drawn, rendered **exactly**: no case changes, no trimming, no
  smart-quote substitution. Case transforms are forbidden in styles; write the copy in the
  case you want.
- `say` is optional TTS text; it defaults to `text` with newlines turned into spaces.
  `pronounce` substitutions apply **only** to TTS input, never to drawn text.
- The copy deck is the user's file. Tools may read it. `reel_mcp` has **no tool that writes
  it**. That is deliberate, so the agent can't paraphrase the copy. To change copy, the agent
  asks the user.
- `copydeck.check(reel)`: every `copy_id` used in shots, captions and end cards exists. No
  shot or template field may contain free text. The schema has no free-text fields; the
  validator also rejects unknown keys like `text`, `label` or `caption_text` anywhere in
  the reel JSON.

### 3.2 Reel JSON (`*.reel.json`)

```json
{
  "version": 3,
  "settings": {"width": 1080, "height": 1920, "fps": 30, "safe_area": "reels"},
  "brand": {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff",
            "font_caption": "fonts/Oswald-SemiBold.ttf", "font_display": "fonts/DMSerifDisplay-Regular.ttf",
            "logo": "inputs/brand/logo.png"},
  "copy": "inputs/brand/copy.json",
  "audio": {"music": "inputs/music/upbeat.mp3", "music_gain_db": -14, "voiceover": true,
            "duck_db": -10, "snap_cuts_to_beats": true, "loudness_lufs": -14},
  "shots": [
    {"id": "s1", "source": {"image": "inputs/shots/fibers.png"},
     "motion": {"type": "parallax", "push": 0.08, "orbit_deg": 0, "dof": 0.3},
     "caption": {"copy_id": "hook", "style": "red-box", "reveal": "typewriter", "anchor": "top"},
     "vo": "hook", "duration": "auto"},
    {"id": "s3", "source": {"image": "inputs/shots/machine.png"},
     "product": {"image": "inputs/brand/bottle.png", "x": 0.5, "y": 0.62, "height": 0.42,
                 "depth": 0.85, "shadow": 0.6, "match_color": 0.5},
     "motion": {"type": "kenburns", "zoom": [1.0, 1.12], "pan": [[0, 0], [0.02, -0.03]]},
     "caption": {"copy_id": "tackle", "style": "red-box", "reveal": "typewriter", "anchor": "bottom"},
     "vo": "tackle", "duration": 1.6, "transition_in": "cut"},
    {"id": "s7", "source": {"video": "inputs/clips/vacuum.mp4", "trim": [0.5, 2.0]},
     "caption": {"copy_id": "care", "style": "red-box", "reveal": "none", "anchor": "bottom"},
     "duration": "beats:2"},
    {"id": "s9", "source": {"funk2": "examples/disco_cat.json"}, "duration": 1.5}
  ],
  "end_card": {"template": "brand-split", "duration": 5.0, "audio": "silence",
               "copy": {"headline": "end_head", "sub": "end_sub", "url": "url"},
               "product": "inputs/brand/bottle.png", "headline_reveal": "typewriter"}
}
```
- `source`: one of `image`, `video` (with optional `trim`, and `loop` default false), `color`
  (a hex value), or `funk2` (a v2 recipe, rendered at the reel's size). Image and video use
  `fit: cover` by default.
- `duration`: seconds, `"auto"` (the VO line length + 0.35 s padding, minimum 1.0 s), or
  `"beats:N"` (N beats of the detected music tempo).
- `transition_in`: `cut` (default), `flash` (3 white frames fading out), `push` (0.25 s slide
  up), `crossfade` (0.3 s), or `zoom-punch` (0.2 s scale 1.08→1.0). Transitions use time from
  the incoming shot; they never extend the total.
- `safe_area`: `reels` keeps captions out of the top 14% and bottom 22% (platform UI); `none`
  turns this off. Anchors `top`, `center` and `bottom` are placed inside the safe area.
- Every path goes through funkgl's `resolve_path` fence (`FUNK_HOME`, `FUNK_INPUTS`,
  `FUNK_OUT`).

### 3.3 Timeline (`timeline.py`)

`build_timeline(reel, vo_durations: dict[str, float], beats: list[float] | None) -> list[ShotSpan]`
gives absolute `[start, end)` for each shot plus the end card, rounded to whole frames.

- Resolution order:
  1. Explicit seconds.
  2. `auto`, from the VO line length.
  3. `beats:N`, from the beat grid.
  4. With `snap_cuts_to_beats`, each cut moves to the nearest beat within ±0.25 s, as long as
     no shot drops below 0.6 s.
- When VO is present, each VO clip starts 0.1 s after its shot starts. The timeline must make
  each shot at least as long as its VO line + 0.35 s. If an explicit duration is shorter, the
  validator reports an error and names the shot.
- Total length = sum of the shots + the end card. A warning (not an error) when it's over
  60 s.

### 3.4 Depth and motion (`depth.py`, `motion.py`)

- `estimate_depth(image_path) -> np.ndarray` runs the ONNX model on the CPU at the model's
  native input size, upsamples bilinearly and normalises to 0..1 (1 = near). Results are
  cached as `.npy` next to `out/cache/depth/<sha1-of-image>.npy`.
- **Ken Burns:** zoom and pan interpolated with ease-in-out (smoothstep), in a GL pass.
- **2.5D parallax** (a GL fragment shader): offset the UV by `depth * camera_shift`. To fill the
  holes this opens up, sample with the depth-scaled offset, and blur depth edges first
  (separable 9-tap on the depth texture). Camera path parameters:
  - `push`: zoom towards the centre over the shot, near layers zooming more:
    `scale = 1 + push * t * (0.5 + depth)`;
  - `orbit_deg`: horizontal sway.
  - `dof`: blur far regions slightly, `blur ∝ (1 - depth) * dof`. Apply it as a mip/blur mix,
    not a per-pixel loop.
- Limits: clamp the offset so that no more than 3% of the frame width is ever revealed. Over
  that, clamp and log a warning. This keeps the artifacts invisible at 1.5 s per shot.
- **Without a depth model** (not installed), `parallax` degrades to `kenburns` with a warning.
  It never fails.

### 3.5 Product compositing (`product.py`)

The product image is a PNG with alpha. Provide `reel_cli.py cutout <photo>` as a
**helper**: it builds an alpha from a near-uniform background by colour distance + feather. It
is good enough for studio shots on white. Otherwise tell the user to cut the product out in
GIMP (the GIMP MCP can do it).

- Placement: `x`, `y` are the bottom-centre anchor in frame fractions; `height` is a fraction
  of frame height; `depth` places it in the parallax field, so it moves with the camera like
  an object at that depth.
- **Contact shadow:** an elliptical soft shadow under the base, plus a 6% offset drop shadow,
  both multiplied in with opacity `shadow`.
- **Colour match:** move the product's mean and standard deviation per channel in Lab
  space towards the background region around it, by `match_color` (0..1). **Never** warp,
  regenerate or retouch the product pixels beyond this global colour transform and scaling.
  The label must stay exact.
- Products are never passed through any generative model.

### 3.6 Captions (`captions.py`) and end cards (`endcard.py`)

- Styles are JSON in `templates/captions/`:
  - `red-box`: a box in `brand.primary`, `font_caption`, `brand.text`, padding 0.6em, max
    width 82%, line-height 1.1, a slight 0.15 s scale-in of the box.
  - `lower-third`.
  - `bold-pop`.
- Reveals:
  - `none`;
  - `typewriter`: characters at 28 cps, an optional cursor bar, and the box grows with the
    text, like the reference;
  - `words`: word by word, timed to the VO duration;
  - `pop`.
- Text layout uses the funkgl/PIL text helpers. Wrap on spaces only, **never** inside a word,
  and never hyphenate. If the text can't fit at the minimum size (0.032 × height), the
  validator errors with the copy ID. Never shrink below that, never truncate.
- End-card templates: `brand-split` (the reference: a primary-colour panel on the left two
  thirds, URL bars top and bottom, headline typed on, product on the right) and
  `centered-logo`. Each template declares its copy slots; every slot must map to a copy ID.

### 3.7 Audio (`audio.py`)

- TTS: `synthesize(copy_line) -> wav path` with Piper. Cache by `sha1(voice + say text)`.
  Duration comes from the wav header.
- Beats: load the music (decode to mono 22.05 kHz through ffmpeg), then take an onset strength
  envelope (spectral flux) → autocorrelation tempo in 70–180 BPM → beat grid by picking
  peaks along the tempo period. No librosa. Unit-test it on a synthetic click track at
  120 BPM: the tempo must come out within ±1 BPM.
- Mix, as one ffmpeg filtergraph:
  1. music at `music_gain_db`;
  2. VO clips delayed to their start times (`adelay`);
  3. duck the music by `duck_db` while VO plays (`sidechaincompress`, or volume automation
     built from the VO intervals; either is fine);
  4. end-card audio `silence` mutes from the card's start with a 0.3 s fade;
  5. finally `loudnorm` to `loudness_lufs`.

  Output a 48 kHz stereo wav, then mux AAC 192 k with the video.

### 3.8 Rendering (`render.py`), storyboard, CLI and MCP

- Frames: for each frame, find the shot, render source+motion (GL), the product, the caption
  overlay and the transition, then pipe rgb24 into ffmpeg like funkgl encode does. Reuse one GL
  context. Pre-decode video sources with ffmpeg to raw frames on demand (stream them, never
  load whole clips into RAM).
- `storyboard(reel) -> PNG`: shot thumbnails in a grid, each frame taken at the shot's
  midpoint, with the shot ID, duration and copy ID printed **below** the frame (not on it).
  It must take < 10 s for 12 shots on the laptop. This is what the agent iterates on.
- CLI:
  - `reel_cli.py validate X.reel.json`
  - `reel_cli.py storyboard X.reel.json -o out/sb.png`
  - `reel_cli.py still X.reel.json --shot s3 --t 0.5`
  - `reel_cli.py render X.reel.json [--draft]` (`--draft` = 540x960, 24 fps, no depth model)
  - `reel_cli.py setup-models`
  - `reel_cli.py cutout photo.jpg -o inputs/brand/bottle.png`
  - `reel_cli.py beats music.mp3`
- MCP (`reel_mcp.py`, same import shim and `_safe` pattern as `funk2_mcp.py`):
  - `copy_lines()`: read-only, the copy IDs with their exact text.
  - `validate(reel: dict)`
  - `storyboard(reel: dict)`: image.
  - `still(reel, shot_id, t)`: image.
  - `render(reel, name, draft=False)`: paths, timing, poster image, plus a 1-line report
    of the total duration and the shot count.
  - `list_templates()`.
  - **There is no tool that writes the copy deck.** The server `instructions` MUST say:
    "On-screen and spoken text come only from the copy deck by ID. If the needed line
    doesn't exist, ask the user to add it to copy.json; never invent copy."

---

## 4. Milestones (commit `v3 R<n>: <title>` after each one)

- **R0, scaffolding.** Folders, schema dataclasses, `requirements-reel.txt`, test skeleton,
  and `examples/reel_demo/make_assets.py`. That script generates synthetic shots (gradients
  + shapes rendered with funk2), a fake product PNG with alpha and **printed text "DEMO-123"**
  on it, a 120 BPM click-track wav, a demo `copy.json` and `demo.reel.json`.
  **Accept:** `pytest -q` runs.
- **R1, copy deck + validator + timeline.**
  **Accept:** the tests check:
  - an unknown copy ID → error;
  - a literal `text` key anywhere → error;
  - `auto` durations follow given VO durations;
  - beat snapping moves cuts to within ±1 frame of the beats;
  - a too-short explicit duration with VO → an error naming the shot.
- **R2, motion.** Ken Burns + parallax GL passes, the depth module with a stub depth
  (vertical gradient) when no model is present, and a reveal clamp.
  **Accept:**
  - parallax on the stub depth moves the bottom rows more than the top rows (measured with
    phase correlation between two frames);
  - no frame contains pixels from outside the source (border check);
  - `still` works.
- **R3, product.** Compositing, shadow, colour match.
  **Accept:**
  - the product region of a render, inverse colour-transformed, matches the source PNG within
    ±3 levels on 99% of its opaque pixels (proves the label isn't altered);
  - a test reads back the "DEMO-123" area by template matching against the source: the
    correlation must be > 0.95.
- **R4, captions + end cards.**
  **Accept:**
  - typewriter at frame k shows exactly `text[:n(k)]`: render the caption layer on its own and
    compare it pixel-exact to a direct render of the prefix;
  - wrapping never splits words;
  - text that can't fit → a validator error;
  - `brand-split` renders with every slot filled.
- **R5, exactness audit.** `render(..., audit=True)` writes `out/<name>.text_audit.json`: every
  string drawn, its copy ID, and the frame range.
  **Accept:** a test renders the demo and asserts that every drawn string equals
  `copy.lines[id].text` byte for byte, and that the audit has an entry for every caption
  and end-card slot.
- **R6, audio.**
  - TTS through Piper if a voice is installed, else a stub (sine bursts whose length is
    proportional to the character count, so timing logic is still tested);
  - beats, ducking, loudnorm and mux.

  **Accept:**
  - the click track gives 120±1 BPM;
  - in the rendered mp4's audio, the VO windows are ≥ 6 dB louder than the music-only
    windows (measured with ffmpeg `astats` per segment);
  - the end card's RMS is < -50 dBFS when `audio: silence`;
  - integrated loudness is within ±1 LU of target (ffmpeg `ebur128`).
- **R7, render + storyboard + CLI + MCP.**
  **Accept:**
  - the full demo renders at 540x960 (draft) in the container;
  - the storyboard PNG has one tile per shot plus the end card;
  - the MCP stdio test calls every tool;
  - `copy_lines` is read-only (there is no write tool; assert the tool list).
- **R8, docs + report.** Add a README section and `BUILD_REPORT-v3.md` covering:
  - what was built;
  - the pytest line;
  - draft-render time for the demo in the container, labelled llvmpipe;
  - the model hashes and licences you recorded;
  - deviations.

Definition of done: R0–R8, the full suite green with v1/v2 tests included, and no edits to
v1/v2 files except documented hooks.

---

## 5. Pitfalls (on top of PLAN.md section 5)

| Pitfall | Symptom | Fix |
|---|---|---|
| Agent paraphrases copy | "Tackle carpet cleaning with GLOWMOP" (missing hyphen) | Copy IDs only; validator rejects literal text; audit test |
| Case transforms in a style | the deck says "Deep", the screen shows "DEEP" | styles may not transform case; write copy in final case |
| TTS mispronounces brand names | "glow-mop" | `pronounce` map, applied to TTS input only |
| Parallax holes | smeared edges at depth discontinuities | blur depth, clamp reveal ≤ 3%, short shots |
| Product looks pasted on | floating bottle | contact shadow + colour match + depth-locked motion |
| Colour match alters the label | wrong red on the logo | global per-channel transform only, capped by `match_color`; R3 test |
| Long videos in RAM | 4 GB spike | stream decode per shot |
| Loudness jumps between ads | inconsistent volume | `loudnorm` on the final mix |
| Commercial licence of models/voices | legal exposure for a client ad | Apache/MIT depth only; voice licence printed by `setup-models` |

## 6. When stuck
Same as PLAN.md section 7. Never weaken the exactness tests (R3, R4, R5); they are the reason v3
exists.
