# BUILD REPORT v3.2 "stills"

All of S1 to S5 are done. Full suite: 342 passed, 1 skipped (baseline was 241 passed, 1 skipped). Clean venv with only
`requirements.txt` + pytest (what CI runs): 336 passed, 7 skipped (the optional-extra tests skip there).

| milestone | status | notes |
|---|---|---|
| S1 studio extensions | done | `studio/sdcore.py` (aspects, step clamp for img2img, path fence, request builder, Lanczos 2x upscale, deterministic stub); `generate_image` aspects/n/contact sheet with `confirm_slow` gate for new sizes, `restyle_image`, `upscale_image`; `STUDIO_FAKE_SD=1` stub for tests; licence notes |
| S2 poster core | done | `stills/` package: schema with all-errors reporting, CPU raster (image, solid, gradient, shader, product, border), GPU compositing with the funk2 blend modes (matches the engine within +-1), masks, groups, post passes, sidecar with inlined shader source and resolved seeds |
| S3 adjust, filters, text | done | GPU adjust/blur/sharpen/duotone/pixelate/chroma/halftone/posterize/vignette; text via the reel's wrapping and fonts, effects (shadow, stroke, glow); project copy lock and casual rule; `<name>.text_audit.json` |
| S4 OpenRaster, variations, edits | done | `.ora` (mimetype first and stored, top-first stack.xml, per-layer PNGs, thumbnail, merged image); variations with contact sheet; `edit_image` that never writes the source and whose sidecar re-renders to identical pixels |
| S5 MCP, CLI, install, docs | done | `stills_mcp.py` (7 tools), `stills_cli.py`, `install.py` + `.mcp.json` + plugin version 3.2.0, `docs/stills.md`, README row and demo, AGENTS.md, both SKILL.md copies (section E), GLOWMOP demo poster and a test that renders it |

## Deviations and decisions
1. `mergedimage.png` inside an `.ora` is the layer stack WITHOUT the poster-level `post` passes (grain etc. cannot be
   layers); the PNG/JPG exports include them. Documented in `docs/stills.md`.
2. `generate_image` asks for `confirm_slow=true` before the first image at a size FastSD has not compiled yet (~2.5 min).
3. The stills tests live in `tests/stills/` with fixtures in `stills_fixtures.py` instead of a `conftest.py`: a second
   `conftest` module shadowed the one `tests/reel` imports by name (fixed in a follow-up commit after S4).
4. `test_p6_install.py` expectations were extended for the new `stills` server key (nothing weakened).
5. Plugin and package version bumped to 3.2.0; `uv.lock` stays untracked (as in v3.1), so its local version line is not part of the repo.
6. Commits use the neutral `funk builder` identity; pre-existing history was not rewritten, nothing was pushed.

## Not exercised here (needs the Windows laptop)
* Real FastSD: `generate_image` at all five aspects, img2img (`restyle_image`), the FastSD upscaler, the slow-size gate
  against a real first-compile, and that real SD-Turbo output is usable as a poster image layer. Tests use the stub.
* Opening a rendered `.ora` in GIMP 3 (layers, blend modes and opacity as expected); it validates structurally and its
  stack re-composites to the merged image within +-3, but no GIMP ran here.
* The plugin `.mcp.json` entry for `stills` through a live Claude Code `uv run` install, and the Desktop-config merge on
  a real Claude Desktop.
* Hardware GPU rendering (everything here ran on Mesa llvmpipe via EGL).
