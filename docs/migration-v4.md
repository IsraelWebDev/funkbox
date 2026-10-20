# Migrating to funkbox v4

v4 folds nine scripts, five MCP servers and four packages into one package (`funkbox/`), one command line
(`python -m funkbox`, or `python funkbox.py` from a checkout) and one MCP server named `funkbox`
(`python -m funkbox serve`). Output formats, recipe files, poster files, reel files, copy files and the
environment variables are unchanged. Old recipe sidecars written by `funk.py` still load.

## Command line: old to new

Option names are unchanged unless noted. Run `python -m funkbox <area> <verb> --help` for any of them.

| Old | New |
| --- | --- |
| `funk.py list` | `funkbox loop list` |
| `funk.py render --preset P ...` | `funkbox loop render --preset P ...` (`--out` is now `--out-dir`; default folder is `FUNK_OUT`; the GIF is opt-in with `--gif`) |
| `funk.py sheet --preset P --seeds 1-9` | `funkbox loop sheet --preset P --seeds 1-9` |
| `funk2.py info` | `funkbox doctor` (GL renderer) and `funkbox loop list` (shaders) |
| `funk2.py check F.frag` | `funkbox loop check F.frag` |
| `funk2.py still --recipe R --t T -o X` | `funkbox loop still --recipe R --t T -o X` |
| `funk2.py sheet --shader S --seeds 1-9` | `funkbox loop sheet --shader S --seeds 1-9` |
| `funk2.py render --shader S ...` | `funkbox loop render --shader S ...` |
| `funk2.py port-v1` | `funkbox loop write-shader --ports` |
| `reel_cli.py validate\|storyboard\|still\|render` | `funkbox reel validate\|storyboard\|still\|render` |
| `reel_cli.py beats\|cutout\|setup-models` | `funkbox reel beats\|cutout\|setup-models` |
| `reel_cli.py new-project NAME` | `funkbox project new NAME` |
| `reel_cli.py list-projects` | `funkbox project list` |
| `reel_cli.py ingest P` | `funkbox project ingest P` |
| `reel_cli.py set-product P FILE` | `funkbox project set-product P FILE` |
| `reel_cli.py brand P` | `funkbox project brand P` |
| `reel_cli.py set-brand P --primary ...` | `funkbox project set-brand P --primary ...` |
| `reel_cli.py propose-copy P --file F` | `funkbox project propose-copy P --file F` |
| `reel_cli.py approve-copy P [--from-draft]` | `funkbox project approve-copy P [--from-draft]` (still an interactive y/N) |
| `reel_cli.py copy-diff P` | `funkbox project copy-diff P` |
| `reel_cli.py auto-reel P` | `funkbox project auto P` |
| `reel_cli.py project-render P [--full]` | `funkbox project render P [--full]` |
| `reel_cli.py make-shortcuts` | `funkbox project shortcuts` |
| `stills_cli.py presets\|preview\|render\|vary\|export-ora\|ora-check` | `funkbox poster presets\|preview\|render\|vary\|export-ora\|ora-check` |
| `stills_cli.py edit PHOTO --ops ...` | `funkbox image edit PHOTO --ops ...` |
| `stills_cli.py still SHADER --preset square` | `funkbox loop still --shader SHADER --canvas square` |
| (studio MCP only) | `funkbox image generate\|restyle\|upscale`, `funkbox apps status\|start\|stop` |

Things that changed on purpose:

- `funkbox loop render` with no `--recipe` and no `--shader` renders a named look (`--preset`, default
  `liquid-disco`), which is what `funk.py render` did. `funk2.py render` with no source used to be a usage error.
- The v1 look controls (`kaleido`, `spin`, `zoom`) are shader params now: `u_kaleido`, `k_spin`, `u_zoom` on the six
  ported shaders. Presets and `--set kaleido=8` still work; recipes may also set the params directly.
- A layer of `{"type": "v1", "effect": X}` is converted to a shader layer on `shaders/X.frag` when the recipe loads.

## MCP: one server instead of five

Before, `claude_desktop_config.json` had `funk`, `funk2`, `reel`, `studio` and `stills` entries. Now there is one
`funkbox` entry (`python -m funkbox serve`) and, when gimp-mcp is installed, `gimp`. `install.py --register` writes
the new entry and removes the old keys after backing the file up. Tool names are prefixed by area, and every old tool
maps to exactly one new tool with the same parameters:

| Old server: tool | New tool |
| --- | --- |
| funk2: `gl_info` | `loop_gl_info` |
| funk2: `list_looks` | `loop_list` |
| funk2: `write_shader` | `loop_write_shader` |
| funk2: `get_shader` | `loop_get_shader` |
| funk2: `preview` | `loop_preview` |
| funk2: `contact_sheet` | `loop_sheet` |
| funk2: `render` | `loop_render` |
| funk: `render_v1` | removed; render a preset recipe with `loop_render` (`loop_list` shows the preset recipes) |
| reel: `copy_lines` | `reel_copy_lines` |
| reel: `validate` | `reel_validate` |
| reel: `storyboard` | `reel_storyboard` |
| reel: `still` | `reel_still` |
| reel: `render` | `reel_render` |
| reel: `list_templates` | `reel_templates` |
| reel: `new_project` | `project_new` |
| reel: `list_projects` | `project_list` |
| reel: `ingest` | `project_ingest` |
| reel: `set_product` | `project_set_product` |
| reel: `brand_from_assets` | `project_brand` |
| reel: `set_brand` | `project_set_brand` |
| reel: `propose_copy` | `project_propose_copy` |
| reel: `approve_copy` | `project_approve_copy` |
| reel: `copy_diff` | `project_copy_diff` |
| reel: `auto_reel` | `project_auto_reel` |
| reel: `project_render` | `project_render` |
| stills: `presets` | `poster_presets` |
| stills: `preview_poster` | `poster_preview` |
| stills: `render_poster` | `poster_render` |
| stills: `poster_variations` | `poster_variations` |
| stills: `export_ora` | `poster_export_ora` |
| stills: `edit_image` | `image_edit` |
| stills: `funk_still` | `loop_still` |
| studio: `generate_image` | `image_generate` |
| studio: `restyle_image` | `image_restyle` |
| studio: `upscale_image` | `image_upscale` |
| studio: `status` | `apps_status` |
| studio: `start` | `apps_start` |
| studio: `stop` | `apps_stop` |

The copy lock is unchanged: no tool writes `copy.json` except `project_approve_copy` (MCP approval prompt, or an
explicit chat confirmation when the client cannot show one). `project_propose_copy` writes only the draft.
