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
