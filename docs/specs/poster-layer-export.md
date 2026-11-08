# Spec: poster layer export

Status: E1, revised after the third review of commit 46d9d4d (branch `feat/e1-layer-export`). Effort: small. Source: owner list, item E1. Unblocks desktop gap item 4 (poster layers for GIMP).

## Problem

`funkbox poster render` writes one composed PNG. The layers are composed in memory (`render_document` keeps one `LayerOut` per visible top-level layer) and then dropped. Anyone who wants to edit a poster layer by layer in GIMP has to rebuild the layers by hand.

## Goal

`funkbox poster render X.poster.json --layers` also writes each visible top-level layer as its own RGBA PNG, at the poster's size, and a manifest that lists them in order. The composed PNG is byte-identical with and without the flag.

## Non-goals

- Changing the compose path, the post passes, or the composed PNG's bytes in any way.
- Exporting hidden layers (`visible: false`). They do not contribute to the composite, so they get no file and no manifest entry.
- Exporting the children of a group. A group is one layer; its children are already composited into it.
- Exporting the poster-level post passes (grain, vignette, ...). They are not part of any layer.
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
- `<name>.layers.json`: the manifest, written last.

## File names and collisions

- `<name>` matches `NAME_RE` (letters, digits, `_`, `-`, 1 to 60 characters).
- `<layer-id>` matches `ID_RE` in `funkbox/stills/schema.py` (letters, digits, `_`, `-`, 1 to 40 characters).
- Layer ids are unique ignoring case. `schema.py` refuses `Bg` next to `bg`.
- The separator stays `-`, as the owner specified. `-` is allowed by both `NAME_RE` and `ID_RE`, so a separator change cannot be a clean guarantee. Collisions are refused instead, and every file-name comparison is casefolded, because Windows treats `demo-Bg.png` and `demo-bg.png` as one file:
  1. A layer file that already exists is refused unless the last readable `<name>.layers.json` lists it (matched ignoring case). The message names the file, and says so when the names differ only in case.
  2. A composed file (`<name>.png` and the other image exports) is refused if some readable `*.layers.json` in the folder lists it as a layer file.
  Both checks run before any file is written.

## Ownership and readable manifests

- A manifest counts only if it is at most 1 MB, parses as JSON, and is nested shallowly enough for the parser. Anything else is treated as missing, so a damaged manifest cannot break a plain render.
- The layer files owned by `<name>` are the ones its readable manifest lists.
- A manifest deleted or damaged by hand removes that protection; the files it listed are then unprotected.

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

## Order of operations

In the CLI, with `--layers`:

1. `check_layer_export`: every refusal, with nothing written. This is `plan_layers` (name, folder, path length of every layer file, collision rule 1) plus the composed-name check (collision rule 2) for the formats being written.
2. `write_outputs`: the composed PNG, the audit and the sidecar, as today.
3. `write_layers`, which runs `plan_layers` again and then:
   a. create the folder;
   b. delete `<name>.layers.json` first;
   c. delete the layer files the last manifest listed that this export does not write (stale files);
   d. write each visible layer to `<file>.part` and move it into place;
   e. write the manifest last, through `<name>.layers.json.part`.

A refused name, folder, collision or path writes nothing at all, not even the folder.

## Failure and re-run

- If any step after the checks fails with an error, `write_layers` removes the manifest first. It then removes the layer files that the old manifest listed, the layer files this export writes, and every `.part` file of this export. A layer file that no manifest lists is never removed by a failed export. The composed files are untouched. A failed export therefore leaves no manifest and no layer file of this export, and an identical re-run then succeeds.
- If a file cannot be removed, the error says which files remain. If the manifest itself cannot be removed, the layer files are kept, so no manifest ever lists a file that is missing; this export's `.part` files are still removed. The error then says so.
- The cleanup-failure error is an `OSError` raised `from` the original exception. The original stays available as `__cause__`, and its text is in the message. The error type is therefore `OSError` even when the original was another type.
- A folder named `<name>.layers.json` is refused before anything is written, with a message saying it is a folder.
- A hard crash (power loss, a process killed mid-export) runs no cleanup. Layer files can then remain with no manifest, and an identical re-run refuses them by name. Delete those files by hand, then re-run.
- On Windows, a file held open by another program (GIMP) makes `os.replace` or the removal fail; the export then fails with the error above.

## Path length

- A layer file name is at most 105 characters: name 60, separator 1, id 40, `.png` 4.
- `write_layers` refuses any layer file whose full path is 260 characters or more, before the folder is created. A full-length name and id therefore need an output folder of at most 153 characters.

## Rules

1. The compose path is not changed. The composed-name check runs in ``write_outputs`` and is also run before it by the CLI. It refuses only in the collision case and writes the same bytes otherwise.
   Two intended exceptions to the byte-identical rule, and only these:
   (a) a plain render (without `--layers`) refuses a composed name that a readable manifest lists as a layer file (the collision guard), which is the one case where the composed output is refused rather than written;
   (b) a manifest over 1 MB, or one that cannot be parsed, is treated as missing, so a plain render no longer refuses a name that only that manifest claims.
2. The composed PNG is byte-identical with and without `--layers`.
3. The folder fence (`fence_out`) and the name check (`NAME_RE`) apply. A refused name or folder writes nothing.
4. Layer files and the manifest are deterministic: the same poster gives the same bytes.
5. Hidden layers are not written and do not appear in the manifest. A layer that was visible in the previous export and is hidden now loses its file.

## Tests

All in `tests/stills/test_e1_layer_export.py` (21 tests):

1. `test_schema_refuses_layer_ids_that_differ_only_in_case`
2. `test_cli_layers_flag_leaves_the_composed_png_byte_identical`
3. `test_cli_layers_flag_writes_one_png_per_visible_layer_and_a_manifest`
4. `test_cli_layer_pngs_equal_the_compositor_layers_with_mask_and_fx_applied`
5. `test_layer_export_refuses_a_bad_name_or_folder_before_writing`
6. `test_a_failed_layer_write_leaves_no_layer_file_and_an_identical_rerun_succeeds`
7. `test_a_failed_layer_write_over_an_earlier_export_leaves_nothing_and_the_rerun_succeeds`
8. `test_a_layer_hidden_on_the_next_render_loses_its_file`
9. `test_when_every_layer_is_hidden_the_manifest_lists_none`
10. `test_a_layer_export_refuses_to_overwrite_another_posters_composed_file`
11. `test_a_composed_file_refuses_to_overwrite_another_posters_layer_file`
12. `test_a_layer_path_of_260_characters_or_more_is_refused_before_anything_is_written`
13. `test_stale_layer_files_are_removed_after_the_manifest_and_a_failed_removal_leaves_nothing` (the removal stub records the order and fails once)
14. `test_a_failed_cleanup_is_reported_in_the_error`
15. `test_cli_refuses_a_collision_before_writing_the_composed_png`
16. `test_cli_refuses_a_too_long_path_before_creating_the_folder`
17. `test_a_deeply_nested_or_oversized_manifest_does_not_break_a_plain_render`
18. `test_a_name_that_differs_only_in_case_is_the_same_layer_file_on_windows` (Windows only)
19. ``test_a_case_only_clash_with_another_posters_file_says_so`` (Windows only)
20. ``test_cli_refuses_a_folder_named_like_the_manifest_before_writing_anything``
21. ``test_when_the_manifest_cannot_be_removed_this_exports_part_files_are_still_removed``

Also run unchanged: `tests/stills/test_s2_poster.py`, and `tests/test_funkbox_cli.py -k "real_runs or stills_cli"`.

## Done when

- `python -m pytest -q tests/stills/test_e1_layer_export.py tests/stills/test_s2_poster.py` passes (51 tests: 21 new, 30 existing).
- `ruff check` passes on the changed Python files, using the system Python's ruff, because ruff is not installed in the project venv.

## Open questions

- Should the MCP `poster_render` tool also accept `layers`? Not in E1; the GIMP path goes through the CLI.
- Should a later item also write the post-processed flat PNG next to the layers, so GIMP can compare them?
