# Spec: poster layer export

Status: E1 in progress on branch `feat/e1-layer-export`. Effort: small. Source: owner list, item E1. Unblocks desktop gap item 4 (poster layers for GIMP).

## Problem

`funkbox poster render` writes one composed PNG. The layers are composed in memory (`render_document` keeps one `LayerOut` per visible top-level layer) and then dropped. Anyone who wants to edit a poster layer by layer in GIMP has to rebuild the layers by hand.

## Goal

`funkbox poster render X.poster.json --layers` also writes each visible top-level layer as its own RGBA PNG, at the poster's size, and a manifest that lists them in order. The composed PNG is byte-identical with and without the flag.

## Non-goals

- Changing the compose path, the post passes, or the composed PNG in any way.
- Exporting hidden layers (`visible: false`). They do not contribute to the composite, so they get no file and no manifest entry.
- Exporting the children of a group. A group is one layer; its children are already composited into it.
- Exporting the poster-level post passes (grain, vignette, ...). They are not part of any layer. The sum of the layers is the flat composite before those passes, which is not written as a file in this item.
- Baking blend mode or opacity into the layer PNGs. They are recorded in the manifest so GIMP can apply them.
- The MCP `poster_render` tool. It keeps its current behaviour.
- The ORA export, which already writes layers in its own format.

## CLI

```
funkbox poster render X.poster.json [--out DIR] [--name N] [--project P] [--formats ...] --layers
```

`--layers` is off by default. Without it the command writes exactly what it writes today.

## Outputs

In the same folder as the composed PNG (the poster's `out/` for a project poster, else `posters/`):

- `<name>.png` (and the other exports): unchanged.
- `<name>-<layer-id>.png`: one per visible top-level layer.
- `<name>.layers.json`: the manifest.

## File names

- `<name>` must match the existing `NAME_RE` (letters, digits, `_`, `-`, 1 to 60 characters).
- `<layer-id>` already matches `ID_RE` in `funkbox/stills/schema.py` (letters, digits, `_`, `-`, 1 to 40 characters), and ids are unique, so the file names are safe and do not collide.

## Layer PNG format

- RGBA PNG, straight alpha, transparency kept.
- Size: the canvas size of the poster at full scale (`width` x `height`), the same for every layer.
- Content: the layer after its own raster, its own `adjust`/`filters` and its own `mask`/`clip`, which is exactly the array the compositor blends. Blend mode and opacity are not applied.

## Manifest format

`<name>.layers.json`, UTF-8, indented 2 spaces, written atomically like the other sidecars:

```json
{
  "name": "demo",
  "width": 1080,
  "height": 1350,
  "order": "bottom-to-top",
  "layers": [
    {"index": 0, "id": "bg", "type": "solid", "file": "demo-bg.png", "width": 1080, "height": 1350, "blend": "normal", "opacity": 1.0},
    {"index": 2, "id": "pic", "type": "image", "file": "demo-pic.png", "width": 1080, "height": 1350, "blend": "overlay", "opacity": 0.6}
  ]
}
```

- `layers` is in composite order, first entry at the bottom, the same order as the poster's `layers` array.
- `index` is the position in the poster's `layers` array. Hidden layers leave gaps in it.
- `width` and `height` are read from the written array, so they always match the PNG.

## Rules

1. The compose path is not changed. The flag only adds writes after `write_outputs` has finished.
2. The composed PNG is byte-identical with and without `--layers`.
3. The same folder fence (`fence_out`) and name check (`NAME_RE`) apply. A refused name or folder writes nothing.
4. Layer files and the manifest are deterministic: the same poster gives the same bytes.
5. Hidden layers are not written and do not appear in the manifest.

## Tests

`tests/stills/test_e1_layer_export.py`, in the style of `test_s2_poster.py`:

1. `test_cli_layers_flag_leaves_the_composed_png_byte_identical`: a poster with three visible layers (a solid, a semi-transparent image, a second solid with alpha) and one hidden layer. Render through the CLI with and without `--layers` into two folders, and compare the composed PNG bytes.
2. `test_layer_pngs_and_manifest_match_the_compositor`: with `--layers`, each visible layer has one RGBA PNG at poster size, the pixels equal `Rendered.layers[i].rgba`, the hidden layer has no file, and the manifest lists the visible ids in order with the right sizes, blend and opacity.
3. `test_layer_export_refuses_a_bad_name_or_folder`: a bad `--name` or a folder outside the allowed roots raises before any file is written.

## Done when

- The three tests pass with `python -m pytest -q tests/stills/test_e1_layer_export.py`.
- The existing targeted tests that touch the poster render (`tests/stills/test_s2_poster.py`) still pass.
- The branch `feat/e1-layer-export` has one commit with only the spec, the code and the test.

## Open questions

- Should the MCP `poster_render` tool also accept `layers`? Not in E1; the GIMP path goes through the CLI.
- Should a later item also write the post-processed flat PNG next to the layers, so GIMP can compare them?
