# Spec: poster layer export

Status: E1, revised after review of commit 0d4f801 (branch `feat/e1-layer-export`). Effort: small. Source: owner list, item E1. Unblocks desktop gap item 4 (poster layers for GIMP).

## Problem

`funkbox poster render` writes one composed PNG. The layers are composed in memory (`render_document` keeps one `LayerOut` per visible top-level layer) and then dropped. Anyone who wants to edit a poster layer by layer in GIMP has to rebuild the layers by hand.

## Goal

`funkbox poster render X.poster.json --layers` also writes each visible top-level layer as its own RGBA PNG, at the poster's size, and a manifest that lists them in order. The composed PNG is byte-identical with and without the flag.

## Non-goals

- Changing the compose path, the post passes, or the composed PNG's bytes in any way.
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
- `<name>.layers.json`: the manifest. Written last, atomically.

## File names and collisions

- `<name>` matches `NAME_RE` (letters, digits, `_`, `-`, 1 to 60 characters).
- `<layer-id>` matches `ID_RE` in `funkbox/stills/schema.py` (letters, digits, `_`, `-`, 1 to 40 characters).
- Layer ids are unique ignoring case (`schema.py` refuses `Bg` next to `bg`), because Windows file names are case-insensitive and `demo-Bg.png` and `demo-bg.png` are one file.
- The separator stays `-`, as the owner specified. `-` is allowed by both `NAME_RE` and `ID_RE`, so a separator change cannot be a clean guarantee. Instead, collisions are refused, never overwritten:
  1. `write_layers` refuses, before it writes anything, a layer file that already exists unless the previous `<name>.layers.json` in the folder lists it. The refusal names the file.
  2. `write_outputs` refuses a composed file (`<name>.png` and the other image exports) that some `*.layers.json` in the folder lists as a layer file. The refusal names the file.
  Both checks run before any file is written. A manifest deleted by hand removes that protection; the layer files it listed are then unprotected.

## Layer PNG format

- RGBA PNG, straight alpha, transparency kept.
- Size: the canvas size of the poster at full scale (`width` x `height`), the same for every layer.
- Content: the layer after its own raster, its own `adjust`/`filters` and its own `mask`/`clip`, which is exactly the array the compositor blends. Blend mode and opacity are not applied.
- Each file is first written to `<file>.part` in the same folder and then moved into place with `os.replace`.

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

- `layers` is in composite order, first entry at the bottom, the same order as the poster's `layers` array. If every layer is hidden, `layers` is `[]`.
- `index` is the position in the poster's `layers` array. Hidden layers leave gaps in it.
- `width` and `height` are read from the written array, so they always match the PNG.

## Order of operations (what `write_layers` does)

1. Check the name (`NAME_RE`) and the folder (`fence_out`).
2. Refuse a layer file whose path is 260 characters or more (the Windows limit), before any folder is created.
3. Refuse a layer file that exists and is not listed by the previous manifest of this name (see collisions, point 1).
4. Create the folder.
5. Delete the layer files the previous manifest listed that this run does not write (stale files).
6. Delete the previous `<name>.layers.json`. From here on, no manifest from an old run survives.
7. For each visible layer: write `<file>.part`, then `os.replace` it onto `<file>`. On any error, delete the `.part` file and raise.
8. Write the new manifest last.

A failed export therefore leaves no manifest. Some layer PNGs may be new and some old, so the next successful export or re-render is needed before the set is used. The composed PNG is never touched by `write_layers`.

## Path length

- A layer file name is at most 105 characters: name 60, separator 1, id 40, `.png` 4.
- `write_layers` refuses any layer file whose full path is 260 characters or more. A full-length name and id therefore need an output folder of at most 153 characters. A shorter name or id, or a shorter folder, is the fix.

## Rules

1. The compose path is not changed. `write_outputs` gains only the refusal in collision point 2. It writes no different bytes in any other case.
2. The composed PNG is byte-identical with and without `--layers`.
3. The same folder fence (`fence_out`) and name check (`NAME_RE`) apply. A refused name or folder writes nothing.
4. Layer files and the manifest are deterministic: the same poster gives the same bytes.
5. Hidden layers are not written and do not appear in the manifest. A layer that was visible in the previous export and is hidden now loses its file.

## Tests

All in `tests/stills/test_e1_layer_export.py`:

1. `test_schema_refuses_layer_ids_that_differ_only_in_case`: `Bg` and `bg` are a duplicate.
2. `test_cli_layers_flag_leaves_the_composed_png_byte_identical`: CLI with and without `--layers`; the composed PNG bytes are equal, and no layer files exist without the flag.
3. `test_cli_layers_flag_writes_one_png_per_visible_layer_and_a_manifest`: one RGBA PNG per visible layer at poster size, the hidden layer absent, the manifest order and indices (with the gap).
4. `test_cli_layer_pngs_equal_the_compositor_layers_with_mask_and_fx_applied`: each layer PNG written by the CLI equals the in-process layer array; the inverted layer is inverted, the radial-masked layer has alpha 255 at the centre and 0 in the corner, and the semi-transparent layer keeps alpha 128.
5. `test_layer_export_refuses_a_bad_name_or_folder_before_writing`: bad name and folder outside the allowed roots.
6. `test_a_failed_layer_write_leaves_no_manifest_and_does_not_touch_the_composed_png`: a save that fails on the second layer leaves no manifest, no `.part` file, and the composed PNG unchanged.
7. `test_a_layer_hidden_on_the_next_render_loses_its_file`: the stale file is removed and the new manifest does not list it.
8. `test_when_every_layer_is_hidden_the_manifest_lists_none`: composed PNG written, manifest with `"layers": []`, no layer files.
9. `test_a_layer_export_refuses_to_overwrite_another_posters_composed_file`: collision point 1.
10. `test_a_composed_file_refuses_to_overwrite_another_posters_layer_file`: collision point 2.
11. `test_a_layer_path_of_260_characters_or_more_is_refused_before_anything_is_written`: the path limit, with no folder created.

Also run unchanged: `tests/stills/test_s2_poster.py`, and the `poster render` tests in `tests/test_funkbox_cli.py` (`real_runs`, `stills_cli`).

## Done when

- `python -m pytest -q tests/stills/test_e1_layer_export.py tests/stills/test_s2_poster.py` passes (41 tests).
- The branch `feat/e1-layer-export` holds the first E1 commit (0d4f801) and the review-fix commit on top of it. The first commit is not amended.

## Open questions

- Should the MCP `poster_render` tool also accept `layers`? Not in E1; the GIMP path goes through the CLI.
- Should a later item also write the post-processed flat PNG next to the layers, so GIMP can compare them?
