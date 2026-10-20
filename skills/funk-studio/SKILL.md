---
name: funk-studio
description: Make funky images, seamless looping videos, or short vertical product ads, posters and photo edits locally with funkbox (funk2, reel, stills, studio, FastSD, GIMP MCP servers) - drop files in, approve copy, render.
---

# funk studio

funkbox is a free, local, no-account creative pipeline. It needs no NVIDIA GPU; an Intel iGPU
is fine. Its MCP servers:

| server | role |
|---|---|
| `reel` | **vertical ads**: projects, ingest, brand, copy approval, auto shot list, storyboard, render |
| `funk2` | GPU shader engine: write GLSL, preview, contact sheets, seamless MP4 loops |
| `studio` | starts and stops the heavy apps; `generate_image` = text-to-image via FastSD |
| `gimp` | GIMP 3 editing (`get_state_snapshot` previews) |
| `funk` | v1 CPU engine, presets only; prefer funk2 |

If these tools are missing, funkbox isn't installed. Follow `AGENTS.md` in the funkbox repo
(`python install.py ...`), or tell the user what to install.

## A. Making an ad (the main flow)

1. **Project:** call `reel.new_project("<short-name>")`.
2. **Assets:** ask the user to drop everything into the **funk inbox** folder (desktop
   shortcut): product photo, logo, music, clips, scene photos. Or have them name a folder.
   Then call `reel.ingest(project[, source])`.
   - Look at the returned contact sheet.
   - If it reports several product candidates, ask which one and call `set_product`.
   - If the cut-out score is low, say so and offer GIMP cleanup.
   - If there are fewer than 4 visuals, offer to generate scenes with
     `studio.generate_image`: 512x512, scene and mood only. **Never ask the image model for
     the product or any text.** The real product photo is composited in later.
3. **Brand:** call `reel.brand_from_assets(project)` and show the swatch. Accept the user's
   overrides with `set_brand`.
4. **Copy** (exact text, never improvised):
   - Draft lines with the user, or derive them from material they gave you, such as a product
     page they linked. Use short punchy caption lines (≤ 90 chars). Include a hook first,
     2–5 body lines, `end_head`, `end_sub` and `url`.
   - Call `reel.propose_copy(...)`. Show the numbered listing **verbatim**.
   - Approve with `reel.approve_copy(project, draft_sha256)`. The client may pop an approval
     prompt; that's the user approving directly. If it falls back to chat approval, pass
     `user_confirmed_in_chat=true` **only after** the user explicitly approved that exact
     listing. "Looks fine" about a different draft doesn't count.
   - Copy is hash-locked. Any change means propose and approve again. Never edit
     `copy.json`.
5. **Shot list:** call `reel.auto_reel(project, length_s=20, style="red-box", pace="fast")`,
   then show the storyboard. Iterate by editing the shot list: swap shots, motion, reveals,
   anchors. Copy only by **id**.
6. **Render:** `reel.project_render(project, draft=True)` takes ~40 s on the laptop. When the
   user likes it, render the full version: 1080x1920, ~4 min for 25 s. Mention the text audit
   result and where the file is.

Rules:
- On-screen and spoken text only ever come from approved copy ids.
- The product image is only placed, scaled and colour-matched. Never generate, redraw or
  "improve" it.
- If a caption would cover the product, the validator flags it. Fix it by moving the anchor
  or shrinking the product.
- Licences: the bundled models, voice and fonts are commercially usable. Remind the user
  that their own music and photos must be licensed for ads.

## B. Funky loops (funk2)

`list_looks` → `contact_sheet` (choose a seed) → `preview` → `render`.

- **Loop contract:** animate only through `iPhase` times an **integer**, or with
  `loopNoise(p, r)`. `iTime` breaks loops. The checker refuses broken loops.
- Write new looks with `write_shader(name, source)`. Define `vec4 effect(vec2 p)` (centred
  coords, +y up). Errors come back with your line numbers.
- Images (e.g. from FastSD) go in as image layers: `inputs/<name>.png`, `fit:contain`,
  `uv_warp`.
- 720px x 6 s renders in ~3–4 s. Only request `gif=true` if the user asked for a GIF.

## C. Apps on demand (studio)

Call `studio.status()` first.

- **FastSD** auto-starts on `generate_image`: ~20 s cold, then ~3–5 s per 512x512 image. It
  idles off after 20 minutes. Prefer `aspect="square"` (512x512): the first image at any other
  size recompiles the model (~2.5 min), so `generate_image` asks you to confirm that with the user first.
- **GIMP is opened by the user, never by you.** Claude Desktop's sandbox would kill a
  GIMP you launched, along with unsaved work. If `studio.start('gimp')` says it isn't
  running, ask the user to double-click **GIMP + MCP** on the desktop, then call
  `studio.start('gimp', wait_s=60)`. Never close GIMP.

## D. Report

End with what you made and the file paths. Don't recap the steps. If something was slow or
failed, name the app and the fix.

## E. Stills

Describe → generate variations → pick → poster or edit → export.

- **Pictures first:** `studio.generate_image(prompt, aspect, n=4)` gives a contact sheet; the user picks one.
  `restyle_image` and `upscale_image` refine it. Generated pictures never carry text.
- **Poster:** write a `*.poster.json` (`stills.presets()` lists the pieces) and `preview_poster` it. Look at the picture,
  fix, repeat, then `render_poster`. `poster_variations` with `vary={"seed":[1,2,3,4]}` (or a palette, a hue) shows
  options on one sheet.
- **Text is exact:** in a project, text layers use `copy_id` from the approved copy; never literal text. Literal text
  only in a poster marked `"casual": true`. The product photo is composited, never generated. Read the text audit.
- **Edit a photo:** `edit_image(path, ops)` (crop, resize, adjust, filter, remove_background, overlay, text, border)
  writes a new file and a before/after sheet; the original is never touched.
- **Export:** `render_poster` gives PNG/JPG; offer `export_ora` so the user can keep editing the layers in GIMP.
  `funk_still` turns a funk2 shader or recipe into a poster background.

## Troubleshooting

- Tools missing after install: fully quit Claude Desktop from the tray, run
  `python install.py --register desktop --yes`, then reopen it.
- `copy.json changed since approval`: someone edited it. Propose and approve again, or have
  the user run `reel_cli.py approve-copy <project>`.
- FastSD errors: read `vendor/fastsdcpu/_studio_fastsd.log`. GIMP shortcut but no port: the
  first run takes ~2 min, and `out/gimp_start.log` says why.
