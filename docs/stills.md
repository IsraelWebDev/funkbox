# Posters and photo edits

`funkbox poster ...`, `funkbox image edit ...` and the `poster_*` / `image_edit` MCP tools render a JSON poster
(`*.poster.json`) or edit a photo on the GPU you already use for loops. Layers are rasterised on the CPU, filtered and composited on the GPU with the same blend modes as the loop engine, and
text is drawn by funkbox from the copy deck, never by an image model.

![The GLOWMOP demo poster](stills-demo.png)

```
python examples/stills/make_assets.py                                   # synthetic bottle + carpet texture
python -m funkbox poster preview examples/stills/glowmop_poster.poster.json -o out/pv.png
python -m funkbox poster render  examples/stills/glowmop_poster.poster.json --name glowmop
python -m funkbox poster vary    examples/stills/glowmop_poster.poster.json --vary '{"seed":[1,2,3,4]}'
python -m funkbox image edit     photo.jpg --ops '[{"op":"crop","aspect":"1:1"},{"op":"adjust","contrast":0.2}]'
python -m funkbox loop still --shader shaders/warp.frag --canvas story --t 0.3
python -m funkbox poster export-ora examples/stills/glowmop_poster.poster.json   # layers for GIMP / Krita
```

## MCP tools

| tool | does |
|---|---|
| `poster_presets()` | canvas presets, layer types, blends, filters, effects, export formats, edit ops |
| `poster_preview(poster, max_side=720)` | small render with the same layout and text, writes nothing: look before you render |
| `poster_render(poster, name)` | full render to `out/posters/` (a project poster goes to `projects/<p>/out/`): PNG (or JPG/WebP/ORA per `export.formats`), `<name>.poster.json` sidecar, `<name>.text_audit.json` |
| `poster_variations(poster, vary, name)` | up to 12 variants and a labelled contact sheet |
| `image_edit(path, ops, name)` | non-destructive edit of a copy; writes `out/edits/<stem>-<n>.png`, a sidecar and a before/after sheet |
| `loop_still(recipe_or_shader, t, preset)` | one frame of a loop recipe or shader as a poster background |
| `poster_export_ora(poster, name)` | the poster as a layered OpenRaster file plus the PNG |

`poster` is a JSON object or a path to a `.poster.json` inside `FUNK_HOME`, `FUNK_OUT`, `FUNK_INPUTS` or the projects
folder. Errors come back as `ERROR: ...` text listing every problem found.

## The poster document

```json
{ "version": 1, "name": "x", "canvas": {"preset": "insta-portrait", "background": "#0b1030"},
  "copy": "glowmop.copy.json", "brand": {"primary": "#ff3d7f", "secondary": "#2fd6ff", "text": "#ffffff"},
  "seed": 11, "layers": [ ... ], "post": [{"name": "grain", "amount": 0.04}],
  "export": {"formats": ["png", "ora"], "jpg_quality": 92} }
```

Canvas: a preset (`square` 1080x1080, `insta-portrait` 1080x1350, `story` 1080x1920, `landscape` 1920x1080, `twitter`
1600x900, `a4-150dpi` 1240x1754) or `width`/`height` (64..4096, rounded to even). `background` is a colour or
`"transparent"` (PNG/WebP keep the alpha, JPG goes over white).

Layers (bottom first; every layer takes `blend`, `opacity`, `mask`, `adjust`, `filters`, `visible`):

| type | keys |
|---|---|
| `image` | `path`, `fit` cover/contain/stretch, `x`, `y`, `scale`, `rotate_deg`, `pre` (crop, resize, rotate, flip, remove_background, trim_even) |
| `solid` | `color` (`#rrggbb[aa]` or `brand.primary|secondary|text`) |
| `gradient` | `stops`, `kind` linear/radial, `angle`, `cx`, `cy`, `radius`, `dither` |
| `shader` | one of `shader` (.frag path), `source` (GLSL), `recipe` (loop recipe or path); `t` (0..1), `palette`, `cycles`, `params`, `seed`. A recipe that draws its own text is refused |
| `product` | `image` (PNG with alpha), `x`, `y` (bottom centre), `height`, `shadow`, `match_color`, `effects`. Same compositor as the reel: the product's pixels are composited, never regenerated |
| `text` | `copy_id` or `text`, `style` display/caption, `size`, `x`, `y`, `max_width`, `color`, `align`, `line_height`, `effects` (shadow, stroke, glow) |
| `group` | `layers` (flattened, then blended as one layer; nesting up to 8) |
| `border` | `px`, `color` |

`adjust`: brightness, contrast, saturation, hue, levels `[black, white, gamma]`, temperature, invert. `filters`: blur,
sharpen, posterize, halftone, duotone `[dark, light]`, pixelate, chroma, vignette. Masks: `radial`, `linear`, `image`
(with `feather`, `invert`). `post`: the loop post passes (grain, vignette, chroma, halftone, posterize, scanlines).

## Text is exact

* **Project posters** (`"project": "<name>"`): text layers use `copy_id` only, against the project's approved
  `copy.json`. A literal `text` is rejected even in a casual document, and a copy deck that changed since approval is
  refused (same hash lock as the reel).
* **Other posters**: `copy_id` needs a `"copy"` deck; a literal `text` layer needs `"casual": true`.
* Every render writes `<name>.text_audit.json`: for each text layer the source, the drawn lines reconstructed from the
  layout, and whether they equal the source byte for byte. Text that does not fit at the minimum size is an error, not
  a silent shrink.

## Variations

`vary` maps a key to a list of values: `seed`, `layers.<id>.<property...>` (e.g. `layers.bg.palette`,
`layers.photo.adjust.hue`), `canvas.<k>`, `post.<n>.<k>`. Several keys are zipped and must have equal length; at most
12 variants. Every variant is validated before any is rendered, and gets its own sidecar.

## Edits

`image_edit` never writes the source: it builds a one-image poster, renders it, and writes `<stem>-<name|n>.png` (n
counts up, nothing is overwritten) with the sidecar that re-renders it to the same pixels. Ops run in order and
geometry ops (`crop` with `aspect` + `anchor` or `x,y,w,h`; `resize`; `rotate`; `flip`; `remove_background`;
`trim_even`) come first. Then `adjust`, `filter`, `vignette`, `overlay {path, x, y, scale, blend, opacity}`,
`text {copy_id|text}` and `border`. `remove_background` uses the reel's cut-out and reports its quality score.

## OpenRaster

`.ora` is a zip GIMP and Krita open with the stack intact: `mimetype` first and stored, `stack.xml` (top layer first,
with blend and opacity), one PNG per layer, a thumbnail and `mergedimage.png`. Layers are pre-rendered with their
adjustments, filters, effects and masks; blend modes map to `svg:src-over`, `svg:multiply`, `svg:screen`, `svg:plus`,
`svg:overlay`, `svg:difference`, `svg:soft-light`, `svg:hue`. A group is one flattened layer. Poster-level `post`
passes cannot be layers, so they appear only in the PNG/JPG, not in the `.ora` (`mergedimage.png` is the stack
without them). `funkbox poster ora-check FILE` validates a file.

## Generating pictures first

The image tools (see [studio.md](studio.md)) make pictures with FastSD (`image_generate`, `image_restyle`,
`image_upscale`) into `inputs/`; use them as `image` layers or edit them. Generated pictures never carry the text: add
it as a text layer.
