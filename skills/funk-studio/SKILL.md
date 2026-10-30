---
name: funk-studio
description: Make funky images, seamless looping videos, or short product and app ads (9:16, 16:9 or both, from stills, clips or screen recordings), posters and photo edits locally with funkbox (one MCP server: loop_*, reel_*, project_*, poster_*, image_*, apps_* tools; FastSD and GIMP on demand) - drop files in, approve copy, render.
---

# funk studio

funkbox is a free, local, no-account creative pipeline. It needs no NVIDIA GPU; an Intel iGPU
is fine. It is one MCP server, `funkbox`, with tools prefixed by area (plus `gimp`, GIMP's own bridge):

| tools | role |
|---|---|
| `project_*`, `reel_*` | **video ads** (9:16, 16:9, both): projects, ingest, brand, copy approval, shot lists, storyboard, render |
| `loop_*` | GPU shader engine: write GLSL, preview, contact sheets, seamless MP4 loops, preset looks |
| `poster_*`, `image_edit` | layered posters with exact text, photo edits, OpenRaster export |
| `image_generate`, `image_restyle`, `image_upscale` | text-to-image and img2img via FastSD |
| `apps_*` | starts and stops FastSD, reports on GIMP |
| `gimp` server | GIMP 3 editing (`get_state_snapshot` previews) |

If these tools are missing, funkbox isn't installed. Follow `AGENTS.md` in the funkbox repo
(`python install.py ...`), or tell the user what to install. Everything also works from a terminal:
`python -m funkbox <area> <verb>`, and `docs/migration-v4.md` maps the old v3 names to these.

## A. Making an ad (the main flow)

1. **Project:** call `project_new("<short-name>")`.
2. **Assets:** ask the user to drop everything into the **funk inbox** folder (desktop
   shortcut): product photo, logo, music, clips, scene photos. Or have them name a folder.
   Then call `project_ingest(project[, source])`.
   - Look at the returned contact sheet.
   - If it reports several product candidates, ask which one and call `project_set_product`.
   - If the cut-out score is low, say so and offer GIMP cleanup.
   - If there are fewer than 4 visuals, offer to generate scenes with
     `image_generate` at **square** (tall sizes duplicate subjects with SD-Turbo), scene and mood only. **Never ask the image model for
     the product or any text.** The real product photo is composited in later.
3. **Brand:** call `project_brand(project)` and show the swatch. Accept the user's
   overrides with `project_set_brand`.
4. **Copy** (exact text, never improvised):
   - Draft lines with the user, or derive them from material they gave you, such as a product
     page they linked. Use short punchy caption lines (≤ 90 chars). Include a hook first,
     2–5 body lines, `end_head`, `end_sub` and `url`.
   - Call `project_propose_copy(...)`. Show the numbered listing **verbatim**.
   - Approve with `project_approve_copy(project, draft_sha256)`. The client may pop an approval
     prompt; that's the user approving directly. If it falls back to chat approval, pass
     `user_confirmed_in_chat=true` **only after** the user explicitly approved that exact
     listing. "Looks fine" about a different draft doesn't count.
   - Copy is hash-locked. Any change means propose and approve again. Never edit
     `copy.json`; no tool but `project_approve_copy` writes it.
5. **Shot list:** call `project_auto_reel(project, length_s=20, style="red-box", pace="fast")`
   (add `formats=["9x16","16x9"]`, `bpm=110`, `clip_share`, `product_every=0` as the brief needs),
   then show the storyboard. Iterate by editing the shot list: swap shots, motion, reveals,
   anchors. Copy only by **id**.
   - **Specific briefs** (exact timings, push-ins, mostly screen recordings): treat auto_reel as a
     draft and write the shot list yourself. Use `"motion": {"type": "frame", "to": [x,y,w,h]}` to push
     in on a UI element (rect in fractions of the source; works on video), `source.speed` for speed
     ramps, `source.focus` / `fit: "contain"` + `pad`, `"audio": {"bpm": 110}` with `"duration": "beats:N"`,
     and `"formats"` for 9:16 + 16:9 from one edit. See `docs/reel.md`, "Shot lists for specific briefs",
     and `reel_templates`. Run `reel_validate` (it checks every format) and `reel_storyboard(format=...)`.
   - Framing only crops and scales; never promise or attempt to alter what a recording shows.
6. **Render:** `project_render(project, draft=True)` takes ~40 s on the laptop. When the
   user likes it, render the full version: 1080x1920, ~4 min for 25 s (each extra format adds
   about as much again). Mention the text audit
   result and where the file is.

Rules:
- On-screen and spoken text only ever come from approved copy ids.
- The product image is only placed, scaled and colour-matched. Never generate, redraw or
  "improve" it.
- If a caption would cover the product, the validator flags it. Fix it by moving the anchor
  or shrinking the product.
- Fonts: any brand font may be a Google Fonts name instead of a file (`"Rubik:800"`, `"Secular One"`); it is fetched
  once. Text in Hebrew, Cyrillic or any script works when the font has the glyphs; a missing glyph is an error that
  names the character (switch to a font that covers it, or set `font_fallback`). The default fallback, Rubik Bold,
  covers Hebrew. Never work around this by drawing text outside funkbox.
- Emblems, badges and logos: build them as posters. `shape` layers draw polygons, ellipses and thick mitred strokes
  (block lettering, ribbons); text layers take `arc` (text along a ribbon), `skew_x`/`skew_y` (slanted bands),
  `rotate_deg` and `wrap: false`. Example: examples/stills/badge.poster.json.
- Licences: the bundled models, voice and fonts are commercially usable. Remind the user
  that their own music and photos must be licensed for ads.

## B. Funky loops

`loop_list` (shaders, presets, palettes) → `loop_sheet` (choose a seed) → `loop_preview` → `loop_render`.

- **Loop contract:** animate only through `iPhase` times an **integer**, or with
  `loopNoise(p, r)`. `iTime` breaks loops. The checker refuses broken loops.
- Write new looks with `loop_write_shader(name, source)`. Define `vec4 effect(vec2 p)` (centred
  coords, +y up). Errors come back with your line numbers.
- The eight classic looks (acid-plasma, pop-moire, liquid-disco, hypno-tunnel, lava-lamp, op-art,
  frozen-kaleido, party-poster) are recipes: `loop_list` prints each one to copy into `loop_preview`.
- Images (e.g. from FastSD) go in as image layers: `inputs/<name>.png`, `fit:contain`,
  `uv_warp`.
- 720px x 6 s renders in ~3–4 s. Only request `gif=true` if the user asked for a GIF.

## C. Apps on demand

Call `apps_status()` first.

- **FastSD** auto-starts on `image_generate`: ~20 s cold, then ~3–5 s per 512x512 image. It
  idles off after 20 minutes. Prefer `aspect="square"` (512x512): the first image at any other
  size recompiles the model (~2.5 min), so `image_generate` asks you to confirm that with the user first.
- **GIMP is opened by the user, never by you.** Claude Desktop's sandbox would kill a
  GIMP you launched, along with unsaved work. If `apps_start('gimp')` says it isn't
  running, ask the user to double-click **GIMP + MCP** on the desktop, then call
  `apps_start('gimp', wait_s=60)`. Never close GIMP.

## D. Report

End with what you made and the file paths. Don't recap the steps. If something was slow or
failed, name the app and the fix.

## E. Stills

Describe → generate variations → pick → poster or edit → export.

- **Pictures first:** `image_generate(prompt, aspect, n=4)` gives a contact sheet; the user picks one.
  `image_restyle` and `image_upscale` refine it. Generated pictures never carry text.
- **Poster:** write a `*.poster.json` (`poster_presets()` lists the pieces) and `poster_preview` it. Look at the picture,
  fix, repeat, then `poster_render`. `poster_variations` with `vary={"seed":[1,2,3,4]}` (or a palette, a hue) shows
  options on one sheet.
- **Text is exact:** in a project, text layers use `copy_id` from the approved copy; never literal text. Literal text
  only in a poster marked `"casual": true`. The product photo is composited, never generated. Read the text audit.
- **Edit a photo:** `image_edit(path, ops)` (crop, resize, adjust, filter, remove_background, overlay, text, border)
  writes a new file and a before/after sheet; the original is never touched.
- **Export:** `poster_render` gives PNG/JPG; `formats=["svg","pdf"]` adds vector files (text as outlines, sharp at any
  size, for print). Offer `poster_export_ora` so the user can keep editing the layers in GIMP.
- **Typography:** `letter_spacing` for tracking, `runs` to colour or change the font of some words in a block (the
  text stays exact), `on_path` to set a line along any curve or circle. Prefer these over splitting text into layers.
- **Logo as vectors:** `image_trace(logo, palette=[brand colours])` gives an SVG for an `svg` layer; check the fidelity
  score and the side-by-side, raise `detail` or lower `tolerance` for small lettering.
- **Other sizes:** `poster_sizes(poster, ["story","landscape","square"])` makes the set from one design; pin bands with
  `"resize": "bottom"`, use `fit_to="content"` for banners, and fix one size with its `set` patch, never by hand.
- **Match a reference:** when the user wants it "like this picture", `poster_compare(poster, reference)` after each
  change; fix the worst regions it names and stop when the score stops rising.
  `loop_still` turns a shader or recipe into a poster background.

## Troubleshooting

- Tools missing after install: fully quit Claude Desktop from the tray, run
  `python install.py --register desktop --yes`, then reopen it. Upgrading from v3: the same command removes
  the five old server entries (`funk`, `funk2`, `reel`, `studio`, `stills`) after a backup.
- `copy.json changed since approval`: someone edited it. Propose and approve again, or have
  the user run `python -m funkbox project approve-copy <project>`.
- FastSD errors: read `vendor/fastsdcpu/_studio_fastsd.log`. GIMP shortcut but no port: the
  first run takes ~2 min, and `out/gimp_start.log` says why.
- `python -m funkbox doctor` prints the resolved settings, GL, ffmpeg, models and app paths.
