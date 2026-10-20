# Build report v4 (consolidation)

## Milestones

| | commit | status |
|---|---|---|
| C1 one package, config, v1 engine retired | d133225 | green |
| C2 loops: presets, `fixed` look controls, oracle | 75b3e39 | green |
| C3 one CLI (`python -m funkbox <area> <verb>`) | 4e65f5e | green |
| C4 one MCP server `funkbox`, 37 tools | 473f46b | green |
| C5 install/plugin/docs/cleanup | see git log | green |

## Tests

Full suite: 419 passed, 1 skipped. Clean venv (requirements.txt + pytest only, EGL): 413 passed, 7 skipped (optional-dependency tests).
The two runs must not share `out/test_work`; run them one after the other.

## Counts (before -> after)

* Python LOC (tracked): 18842 -> 19087 (library 13592, tests the rest; the six shader ports, the preset module, the v1 oracle and the migration/CLI/server tests were added, four MCP servers and the v1 engine removed)
* Tracked files: 200 -> 213; Python files: 101 -> 113
* Top-level entries: 47 -> 31 (ls -A, including untracked working folders)
* MCP servers: 5 -> 1; tools: 42 -> 37

## Dead code removed

update_meta, load_deck, NAME_OK, release_all, SEED_STEP, LICENCE, templates_dir, fonts_dir, IngestResult.manifest, fake_enabled, the v1 CPU engine, unused imports in eight modules. A scan at 80% confidence finds only the FastAPI keyword workaround in start_local_mcp.py, kept on purpose.

## Text layout

No shared extraction was needed: stills/text.py already reuses the reel caption layout (Layout, wrap, Fonts). funkbox/gl/text.py is a different small per-letter wobble title without wrapping.

## Deviations

* `fixed` flag on shader @params (look controls u_kaleido, k_spin, u_zoom) and per-layer palette/cycles honoured by the engine, so v1 presets convert faithfully.
* Extra CLI verbs: project set-product, set-brand, copy-diff; loop write-shader --ports; poster ora-check; loop still --canvas; shared `image` group.
* Registration and plugin start `funkbox.py serve` instead of `-m funkbox` plus cwd (works from any folder).
* Old server keys (funk, funk2, reel, studio, stills) are removed literally by key after a backup; gimp is kept.
* Unified font lookup changed the studio contact-sheet label font.
* The numpy v1 oracle and its fixtures stay in tests.
* `loop render` with no source defaults to a preset (usage error before).
* Studio log file is created lazily; `image generate` slow-size warning exits 1.
* The reel schema key "funk2" (shot source kind) is kept for file compatibility.
* `loop_list` prints preset recipes; `render_v1` removed.

## Supervisor checks on the Windows laptop

* GIMP-MCP.cmd rendering with the new funkbox/studio path.
* FastSD launcher sync (start_local_mcp.py and sdcore.py copied to the vendor folder).
* `install.py --register desktop`: the five old keys are migrated and a backup is written.
* `.lnk` shortcuts via `funkbox project shortcuts`.
* Native OpenGL versus EGL.
* The Claude Desktop entry running `funkbox.py serve`.
* The plugin `uv run --extra reel` start.
