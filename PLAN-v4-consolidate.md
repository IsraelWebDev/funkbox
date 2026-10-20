# funkbox v4: consolidate (implementation plan)

**Audience:** an implementer model. The rules from `PLAN.md` sections 1, 5 and 7 apply. **No behaviour
changes** except the ones listed here. Every guarantee stays:
- copy lock;
- text audit;
- path fences;
- loop check;
- no stdout in MCP;
- localhost-only FastSD;
- GIMP never launched or closed by funkbox.

## 0. Why

The repo grew in layers: v1 → v2 → v3 → v3.1 → v3.2 → studio. The result today:
- 9 top-level scripts;
- 5 funkbox MCP servers (`funk`, `funk2`, `reel`, `stills`, `studio`);
- 4 packages;
- path/env resolution in 6 places;
- duplicated MCP helpers;
- a CPU v1 engine whose six looks already exist as GLSL ports;
- 13 plan/handoff/report files at the root.

v4 makes it **one package, one CLI, one MCP server**.

## 1. Target layout

```
funkbox/                       repo root
  funkbox/                     the Python package
    __init__.py                __version__ = "4.0.0"
    __main__.py                python -m funkbox  ->  cli.main()
    config.py                  THE ONLY place that reads env/funkbox.toml and resolves paths + the path fence
    mcp_util.py                MCP import shim (mcp 2.x MCPServer / 1.x FastMCP), _safe, _png, GL-thread runner
    media.py                   ffmpeg_exe, encode (mp4/gif/png), font lookup: shared by everything
    gl/                        was funkgl/  (engine, shader, recipe, loopcheck, text, audio, post, glsl files)
    loops/                     was funk2.py + funk2_mcp.py logic (+ v1 presets as shader recipes)
    reel/                      was reel/ (+ reel_cli/reel_mcp logic)
    stills/                    was stills/ (+ stills_cli/stills_mcp logic)
    studio/                    was studio/ (studio_mcp logic, sdcore, gimp_start_mcp, launcher)
    cli.py                     ONE CLI with sub-commands (section 3)
    server.py                  ONE MCP server named "funkbox" (section 4)
  funkbox.py                   3-line launcher: python funkbox.py ... == python -m funkbox ...
  install.py                   stays (stdlib only), updated for the new server
  shaders/ templates/ fonts/   data dirs stay at the root, resolved through config.py
  examples/ docs/ skills/ tests/ .claude-plugin/ .mcp.json
  docs/history/                ALL PLAN*.md, HANDOFF*.md, BUILD_REPORT*.md moved here (content unchanged)
```

- **Delete:**
  - `funk.py`, `funk_mcp.py`, `funk2.py`, `funk2_mcp.py`, `reel_cli.py`, `reel_mcp.py`,
    `stills_cli.py`, `stills_mcp.py`;
  - `studio/studio_mcp.py` as an entry point (its logic moves into the package);
  - `setup.ps1` and `run-handoff.ps1` (obsolete);
  - the old top-level packages `funkgl/`, `reel/`, `stills/` and `studio/`, after moving them.

  Use `git mv` so history follows the files.
- The FastSD launcher files that `install.py` and the studio copy into the FastSD folder
  (`start_local_mcp.py`, `sdcore.py`) live in `funkbox/studio/` and are still copied by
  `sync_launcher`. They must stay importable **standalone** inside FastSD's venv. `sdcore.py`
  may only depend on numpy and Pillow.

## 2. Retire the v1 CPU engine

The six v1 effects already exist as shaders (`shaders/plasma|moire|warp|tunnel|blobs|rings.frag`,
written by `funkgl/ports.py`). GL works without a GPU through Mesa llvmpipe (EGL), so a CPU
fallback isn't needed.

- **Presets.** Express each v1 preset (`acid-plasma`, `pop-moire`, `liquid-disco`, `hypno-tunnel`,
  `lava-lamp`, `op-art`, `frozen-kaleido`, `party-poster`) as a v2 recipe in `funkbox/loops/presets.py`:
  the shader port, its palette, kaleido and spin via shader params where the port supports them,
  and post passes (chroma, halftone, posterize, scanlines, vignette, grain) plus text. Kaleido and
  spin must work for every preset. If a port lacks them, add `u_kaleido` (int) and `k_spin` (int)
  params to the ports so they stay loop-safe.
- **Visual check.** Render each preset with v1 (before deleting it) and with v4 at t=0.25,
  256 px, side by side in `docs/history/v1-to-v4-presets.png`. Look at it: the presets must still be
  recognisably the same look, though exact pixels are not required.
- **Old recipes still load.**
  - A v1 sidecar JSON converts to an equivalent v2 recipe.
  - A `{"type": "v1", "effect": X}` layer converts to the matching shader layer with mapped
    params.
  - Keep `recipe_from_v1` for both, now producing shader layers. Test both conversions.
- **Removed:** the CPU renderer, the `render_v1` tool, and `tests/v1.sha256` with its hash test.
  Replace the v1 tests with tests of the preset recipes: each renders, passes the loop check,
  and is deterministic per seed.

## 3. One CLI: `python -m funkbox` (or `python funkbox.py`)

```
funkbox loop    list | render | still | sheet | check | write-shader     (was funk.py + funk2.py)
funkbox reel    validate | storyboard | still | render | beats | cutout | setup-models
funkbox project new | list | ingest | brand | propose-copy | approve-copy | auto | render | shortcuts
funkbox poster  presets | preview | render | vary | export-ora
funkbox image   generate | restyle | upscale | edit                     (studio FastSD + stills edit)
funkbox apps    status | start | stop                                   (FastSD, GIMP)
funkbox doctor                                                          GL info, ffmpeg, models, FastSD/GIMP paths, config file
```

- Each area module exposes `add_cli(subparsers)`, and `cli.py` only wires them together.
- Keep the option names the old CLIs had, so command translation is mechanical. Write the
  old→new table in `docs/migration-v4.md`.
- `approve-copy` keeps its interactive y/N on stdin. That path still exists so a human can
  approve outside MCP.

## 4. One MCP server: `funkbox` (`python -m funkbox serve`)

- Each area module exposes `register(server)`. `server.py` builds one server, calls each
  `register`, and runs it.
- Imports are **lazy**: onnxruntime and piper are imported only inside the tools that need
  them, so the server starts fast without extras.
- All GL work goes through **one** GL thread and context (`mcp_util.on_gl`). Remove the
  per-server GL threads.
- **Tool names are prefixed by area.** Keep the old tool's parameters, and map every old tool
  to exactly one new tool:
  - `loop_list`, `loop_preview`, `loop_sheet`, `loop_render`, `loop_write_shader`,
    `loop_get_shader`, `loop_gl_info`;
  - `reel_validate`, `reel_storyboard`, `reel_still`, `reel_render`, `reel_templates`,
    `reel_copy_lines`;
  - `project_new`, `project_list`, `project_ingest`, `project_set_product`, `project_brand`,
    `project_set_brand`, `project_propose_copy`, `project_approve_copy`, `project_copy_diff`,
    `project_auto_reel`, `project_render`;
  - `poster_presets`, `poster_preview`, `poster_render`, `poster_variations`,
    `poster_export_ora`, `loop_still` (was `funk_still`);
  - `image_generate`, `image_restyle`, `image_upscale`, `image_edit`;
  - `apps_status`, `apps_start`, `apps_stop`.
- Write the mapping in `docs/migration-v4.md`.
- The server `instructions` are ≤ 8 sentences and cover:
  - the areas;
  - previews before full renders;
  - the loop contract;
  - copy only by approved ids;
  - square images for SD-Turbo;
  - GIMP opened by the user only;
  - errors come back as `ERROR:` text.
- **Copy-lock invariant:** there is still no tool that writes `copy.json` except
  `project_approve_copy`, with the same elicitation and chat-fallback rules. The test asserting
  this checks the new tool list.

## 5. Config in one place (`funkbox/config.py`)

- One `Config` dataclass resolved once:
  - `home` (the repo root);
  - `out`, `inputs`, `projects`, `inbox`, `models`;
  - `fastsd_dir`, `gimp_mcp_dir`, `gimp_exe`;
  - `gl_backend`;
  - `fastsd_port`, `gimp_port`, `idle_min`.
- Resolution order: env var (the existing names, so nothing breaks), then `funkbox.toml`, then
  the default.
- `allowed_roots()` and `resolve_path()` (the fence) live here. **Every** module uses them; grep
  must find no other `os.environ` reads of `FUNK_*`/`FASTSD_*`/`GIMP_*`/`STUDIO_*` outside
  `config.py`. Exception: the standalone FastSD launcher, which reads its own env.
- `funkbox doctor` prints the resolved config.

## 6. Install, plugin, docs

- **`install.py`:**
  - `--register` writes **one** `funkbox` server entry (`<venv python> -m funkbox serve`,
    `cwd` = the repo root), plus `gimp` when gimp-mcp is present;
  - **migration:** it removes the old keys `funk`, `funk2`, `reel`, `stills` and `studio` if
    present, after backing up;
  - `--unregister` removes `funkbox`, `gimp` and the old keys.

  Tests cover migration from a v3.2 config that also has an unrelated server: the unrelated
  server survives.
- **Plugin `.mcp.json`:** one `funkbox` server
  (`uv run --project ${CLAUDE_PLUGIN_ROOT} python -m funkbox serve`, with the same data-dir env
  as now). Bump the plugin version to 4.0.0.
- **pyproject:** add `[project.scripts] funkbox = "funkbox.cli:main"`. Keep `package = false`
  semantics unless switching is trivial and tested. Don't break `uv run --project`.
- **Docs:**
  - the README "What is in the box" becomes **areas** (loops, reel/projects, posters & images,
    apps), not versions;
  - update AGENTS.md, docs/*.md and both SKILL.md copies (identical) to the new CLI and tool
    names;
  - `docs/migration-v4.md` holds the old→new tables;
  - the link-check test must pass.

## 7. Shared code (de-duplicate)

- One MCP import shim, one `_safe`, one `_png` (in `mcp_util.py`).
- One `ffmpeg_exe` and one mp4/gif encode path (`media.py`). The reel's audio mux and its
  ffmpeg-version option handling (`-/filter_complex` vs `-filter_complex_script`) move there.
- One font lookup.
- Text layout: if funkgl text, reel captions and stills text share wrapping/fit logic, extract
  it into `gl/text.py` and use it from all three. If they differ deliberately, leave them and
  explain in the report.
- Run `vulture` (pip install is fine) at ≥ 80% confidence and delete truly dead code. List what
  you removed.

## 8. Milestones (commit `v4 C<n>: …`, author `funk builder <builder@example.com>`, no push, no history rewrite)

- **C1, package skeleton + config + mcp_util + media.** Move the packages with `git mv` and fix
  the imports. Everything still works through temporary shims if needed.
  **Accept:**
  - the full suite is green;
  - the grep rule in section 5 holds.
- **C2, v1 retirement:** presets as recipes, converters, the comparison sheet (looked at).
  **Accept:**
  - the preset tests pass;
  - the v1 recipe conversions work;
  - the sheet is in docs/history.
- **C3, one CLI.**
  **Accept:** every old CLI verb has a new equivalent, tested by a parametrised smoke test that
  runs each sub-command's `--help`, plus real runs of `loop render`, `reel storyboard`,
  `poster render` and `image edit` on demo assets.
- **C4, one MCP server.**
  **Accept:**
  - a stdio test lists the tools and checks they equal the planned set;
  - one call per area works (image tools with `STUDIO_FAKE_SD=1`);
  - the copy-lock tool-list assertion passes;
  - the server starts in < 3 s without extras.
- **C5, install/plugin/docs/cleanup.**
  - Move the history docs; delete the obsolete scripts and old entry points.
  - Run the vulture pass.
  - Report LOC before/after (`git ls-files '*.py' | xargs wc -l`), file counts and server/tool
    counts.

  **Accept:**
  - the full suite is green;
  - a clean venv (`requirements.txt` + pytest, `FUNK_GL_BACKEND=egl`) is green;
  - the link check, hygiene and secret tests pass.
  - Write `docs/history/BUILD_REPORT-v4.md`.

## 9. Don't

- Weaken or delete any exactness, lock, fence or loop test. Moving them is fine.
- Change render output for reel or stills (pixel tests must still pass), or rename the
  env vars.
- Push, rewrite history, or touch anything outside the repo.
