# funk v3.2 "stills": images from a description, layered composition, and edits (implementation plan)

**Audience:** an implementer model. The rules from `PLAN.md` sections 1, 5 and 7 apply, along with the
guarantees from v3/v3.1:
- copy lock;
- path fences;
- no stdout in MCP servers;
- tests must not need network, models or FastSD.

The whole existing suite (235 passed + 7 skipped without extras) must stay green, including CI
(`.github/workflows/tests.yml`).

## 0. Goal: close the gap between loops and ads

| need | today | v3.2 |
|---|---|---|
| image from a description | FastSD txt2img, 512x512 only | aspect presets, variations, img2img restyle, 2x upscale |
| abstract stills | funk2 previews only (small) | full-res stills from any funk2 recipe |
| layered composition | funk2 recipe (loop-oriented) | **poster documents**: layers, blend, masks, adjustments, effects, text, product |
| edit an existing image | GIMP (manual app) | `edit_image`: a non-destructive op list → new file + reproducible sidecar |
| hand-off to GIMP | none | layered **OpenRaster (.ora)** export, which GIMP opens with layers |
| exact text on stills | none | text layers by copy id (project) or literal (casual), always audited |

## 1. FastSD extensions (studio)

All of this lives in `studio/start_local_mcp.py` (the launcher endpoint) and `studio/studio_mcp.py` (tools).

- `/studio/generate` gains:
  - `mode`: `"txt2img"` | `"img2img"`;
  - `init_image` (a path that must resolve inside `STUDIO_OUT` or the funkbox roots; the launcher
    checks this too);
  - `strength` (0.05–0.95).
  - For img2img, set `diffusion_task = "image_to_image"`, `init_image` (a PIL image resized to the
    target size, fit cover) and `strength`. SD-Turbo img2img needs `steps * strength >= 1`: clamp
    steps up to `ceil(1/strength)` and report it.
- **Aspect presets** (multiples of 64): `square` 512x512, `portrait` 512x768, `landscape` 768x512,
  `story` 512x896, `wide` 896x512.
  - Each new size recompiles the OpenVINO model once, ~2.5 min on the laptop.
  - Track the sizes compiled in this process. The tool result warns *before* generating at a new
    size ("first image at 512x896 will take ~2–3 min").
- `/studio/upscale`: 2x upscale, factor 2 only. Use FastSD's `backend.upscale` if it works with
  OpenVINO/CPU (`generate_upscaled_image`, EDSR or similar). Otherwise fall back to Pillow LANCZOS
  plus a mild unsharp mask, and report which method ran.
- New studio MCP tools:
  - `generate_image(prompt, aspect="square", seed=-1, n=1, negative_prompt="", name="")`. With
    `n>1` it returns variations (seeds `seed`, `seed+1`, …, or random) as a contact sheet plus a
    file per image.
  - `restyle_image(path, prompt, strength=0.5, aspect=None, seed=-1, name="")`: img2img.
  - `upscale_image(path, name="")`.
- **Test stub:** with `STUDIO_FAKE_SD=1`, the studio server doesn't launch FastSD. It produces
  deterministic procedural images (seeded from the prompt hash) at the requested size, and for
  img2img blends toward the init image by `strength`. All studio tests use the stub. Keep the
  real code path covered by unit tests of request building.
- **Licence:** FastSD's default model is SD-Turbo, under the Stability AI Community License
  (commercial use is free under US$1M annual revenue, and Stability asks commercial users to
  register). Add this to `THIRD_PARTY_NOTICES.md` and `docs/studio.md`. `generate_image`'s
  description must mention it in one sentence.

## 2. Poster documents (`stills/`)

A poster is JSON (`*.poster.json`), rendered on the GPU with the funkgl Engine context (reuse its
blend shaders and post passes; don't copy them).

```json
{
  "version": 1,
  "canvas": {"preset": "insta-portrait", "background": "#101014"},
  "project": "glowmop-demo",
  "layers": [
    {"id": "bg", "type": "shader", "shader": "shaders/fbm_flow.frag", "t": 0.3, "palette": "vapor"},
    {"id": "photo", "type": "image", "path": "inputs/kitchen.png", "fit": "cover",
     "adjust": {"brightness": 0.05, "contrast": 0.1, "saturation": -0.2, "hue": 0},
     "blend": "multiply", "opacity": 0.85,
     "mask": {"type": "radial", "feather": 0.35}},
    {"id": "bottle", "type": "product", "image": "product/product.png", "x": 0.7, "y": 0.9,
     "height": 0.55, "shadow": 0.6},
    {"id": "grad", "type": "gradient", "stops": ["#00000000", "#000000cc"], "angle": 90},
    {"id": "head", "type": "text", "copy_id": "hook", "style": "display", "x": 0.08, "y": 0.12,
     "max_width": 0.6, "color": "brand.text", "effects": {"shadow": 0.5, "stroke": 0}}
  ],
  "post": [{"name": "grain", "amount": 0.02}],
  "export": {"formats": ["png", "jpg", "ora"], "jpg_quality": 92}
}
```

- **Canvas presets:**
  - `square` 1080x1080;
  - `insta-portrait` 1080x1350;
  - `story` 1080x1920;
  - `landscape` 1920x1080;
  - `twitter` 1600x900;
  - `a4-150dpi` 1240x1754;
  - custom `{width, height}` up to 4096 a side.
- **Layer types:**
  - `image` (fit cover, contain or stretch; plus x, y, scale, rotate_deg);
  - `shader` (a funk2 shader or recipe frozen at `t`);
  - `solid`;
  - `gradient` (linear or radial);
  - `product` (reuse reel `product.py` compositing: shadow and colour match, never altering
    pixels beyond that);
  - `text`;
  - `group` (nested layers with their own blend and opacity).
- **Per-layer properties:**
  - `blend` (the 8 funkgl modes) and `opacity`;
  - `mask`: `none`, `radial`, `linear` (angle, position, feather), or `image` (the grey levels
    of a file);
  - `adjust`: brightness, contrast, saturation, hue (degrees), levels as `[black, white, gamma]`,
    temperature, invert. These are GL passes;
  - `filters`: blur (px), sharpen, posterize, halftone, duotone `[dark, light]`, pixelate,
    chroma. Reuse funkgl post shaders where they exist;
  - `effects` (text and product): drop shadow, stroke, glow.
- **Text rules** (exactness):
  - In a **project** poster, text layers MUST use `copy_id` and the copy lock is enforced (the
    same check as reel).
  - Outside a project, a text layer may use `text` (literal), and the doc must set
    `"casual": true`. Non-casual docs reject literal text, so the exactness rule stays the
    default.
  - Every render writes `<name>.text_audit.json`: each drawn string, its source (copy id or
    literal) and its layer id. The tool result echoes the strings, so the user sees exactly what
    was drawn.
  - Wrapping, fit and font rules are the same as reel captions: never split words, never
    truncate, and error if the text can't fit at the minimum size.
- **Exports:**
  - PNG and JPG at full resolution, plus WebP if Pillow supports it.
  - **OpenRaster `.ora`:** a zip with `mimetype` (stored first, uncompressed), `stack.xml`,
    `data/<layer>.png` (each layer pre-rendered with its own adjustments and filters, *without*
    blend), `Thumbnails/thumbnail.png` and `mergedimage.png`. Map blend modes to OpenRaster
    `composite-op` names (`svg:multiply`, `svg:screen`, `svg:overlay`, `svg:difference`,
    `svg:soft-light`, `svg:hue`, `svg:plus` for add, `svg:src-over` for normal). Masks are baked
    into the layer alpha.
  - Test: re-composite the `.ora` layers in numpy using the stack and ops. The result must match
    `mergedimage.png` within ±3 levels on 99% of pixels (for the modes with exact formulas:
    normal, multiply, screen, add, difference).
  - A sidecar `<name>.poster.json` with shaders inlined and resolved seeds, so it reproduces the
    render.
- `stills/render.py`: `render_poster(doc, out_dir, name) -> paths`.
- `stills/variations.py`: `poster_variations(doc, vary)`, where `vary` is either `{"seed": [..]}`
  or `{"layers.<id>.<prop>": [values...]}`. It produces a contact sheet plus files.

## 3. Edits (`edit_image`)

`edit_image(path, ops: list, name="")` is sugar that builds a one-image poster and renders it.

- Ops, applied in order:
  - `crop` `{x, y, w, h}` in fractions, or `{aspect: "4:5", anchor: "center|top|..."}`;
  - `resize` `{width?, height?}` keeping aspect;
  - `rotate` `{deg}`;
  - `flip` `{axis}`;
  - `adjust {...}` and `filter {...}` (same names as the layer properties);
  - `remove_background` (reuse `reel cutout` with its quality score);
  - `overlay {path, x, y, scale, blend, opacity}`;
  - `text {copy_id | text (casual only), ...}`;
  - `border {px, color}`;
  - `vignette {amount}`.
- The output never overwrites the source. It's written as `out/edits/<stem>-<name|n>.png` plus a
  `.poster.json` sidecar, so the edit is repeatable and further editable.
- A contact sheet compares before and after side by side.

## 4. MCP and CLI

- New server **`stills_mcp.py`**, with the same import shim and `_safe` pattern. Tools:
  - `presets()`
  - `render_poster(doc, name="")`: image + paths + text audit;
  - `preview_poster(doc, max_side=720)`: fast;
  - `poster_variations(doc, vary)`: contact sheet;
  - `edit_image(path, ops, name="")`: before/after + paths;
  - `funk_still(recipe_or_shader, t=0.25, preset="square")`: a full-res still of a funk2
    recipe;
  - `export_ora(doc_or_sidecar)`.
- `stills_cli.py` exposes the same verbs.
- **install.py:** register `stills` with the other servers (`SERVER_KEYS`), and add it to the
  plugin `.mcp.json`. Keep the `--register` merge semantics; tests must cover the new key.
- Server `instructions`, 3 sentences:
  - previews first, full render last;
  - text comes from approved copy ids in projects, literal only in casual docs;
  - offer `.ora` when the user wants to finish in GIMP.

## 5. Milestones (commit `v3.2 S<n>: …`)

- **S1, studio extensions + stub.**
  **Accept:**
  - request-building unit tests: img2img clamp, aspect presets, path fence;
  - stub generation is deterministic;
  - `n=3` gives 3 files and a sheet;
  - the upscale fallback gives exactly 2x size.
- **S2, poster core.** Canvas, image/solid/gradient/shader/product layers, blend, opacity and
  masks.
  **Accept:**
  - blend correctness against numpy references for normal, multiply, screen, add and difference
    (±2 levels);
  - a radial mask gives centre alpha > edge alpha;
  - product pixels are unaltered (reuse the reel R3 check).
- **S3, adjustments, filters, effects, text + audit.**
  **Accept:**
  - brightness/contrast/saturation/hue match numpy references (±3);
  - a literal text layer in a non-casual doc is rejected;
  - a project doc with a stale copy lock is rejected;
  - the audit lists every string, and every string is byte-equal to its source.
- **S4, OpenRaster export + variations + edits.**
  **Accept:**
  - the `.ora` validates (mimetype first and stored, stack.xml parses, all layer files present);
  - the re-composite matches the merged image (see 2);
  - `edit_image` never touches the source (mtime and hash unchanged);
  - the edit sidecar re-renders to the same pixels.
- **S5, MCP + CLI + install/plugin + docs.**
  - The stdio MCP test calls every tool on stub/synthetic assets.
  - `install.py --dry-run` lists `stills`.
  - Update `docs/stills.md`, the README feature table, `AGENTS.md` and
    `docs/skill/SKILL.md` + `skills/funk-studio/SKILL.md` (add an "E. Stills" section: describe
    → generate variations → pick → poster/edit → export; keep everything else).
  - Write `BUILD_REPORT-v3.2.md`.
  - CI must be green locally in a clean venv with only `requirements.txt` + pytest (reproduce
    it like CI does).

Hygiene from v3.1 still applies:
- no personal data;
- no real brands (the demo brand is GLOWMOP);
- tracked files ≤ 5 MB;
- the secret scan.
