# Studio: images and the heavy apps on demand

`funkbox/studio/` holds the on-demand local apps and the image tools. They are the `apps_*` and `image_*` tools of the
`funkbox` MCP server, and `funkbox apps ...` / `funkbox image ...` on the command line:

| tool | does |
|---|---|
| `apps_status()` (`funkbox apps status`) | FastSD on :8000, GIMP MCP on :9877, inputs folder |
| `apps_start(service, wait_s=0)` (`apps start`) | `fastsd`: launches the localhost-only launcher and waits (~20 s). `gimp`: tells Claude what to ask you (double-click **GIMP + MCP** on the desktop); with `wait_s` it waits for GIMP's port |
| `apps_stop(service)` (`apps stop`) | stops FastSD (frees ~4 GB). Never closes GIMP |
| `image_generate(prompt, aspect, seed, n, ...)` (`image generate`) | auto-starts FastSD, saves `inputs/<name>.png` (ready for a loop or poster image layer). `aspect`: `square` 512x512, `portrait` 512x768, `landscape` 768x512, `story` 512x896, `wide` 896x512. `n=2..8` gives variations (seeds `seed`, `seed+1`, ...) as a contact sheet plus one file each |
| `image_restyle(path, prompt, strength=0.5, aspect=None, ...)` (`image restyle`) | img2img: re-renders a picture in a new style. SD-Turbo needs `steps * strength >= 1`, so steps are raised automatically and reported |
| `image_upscale(path)` (`image upscale`) | 2x only: FastSD's upscaler when it works on the machine, otherwise LANCZOS plus a mild unsharp mask; the result says which ran |

FastSD shuts itself down after 20 idle minutes if funkbox started it (`STUDIO_FASTSD_IDLE_MIN`). 512x512 stays
warm (~3-5 s per image); the first image at any other size recompiles the iGPU model (~2.5 min).

**Why GIMP is started by you, not by Claude:** Claude Desktop is a sandboxed (MSIX) app that kills everything an MCP
server launched when it stops that server, so a Claude-launched GIMP would close and lose unsaved work. It also
sees a virtualized `%APPDATA%`. The **GIMP + MCP** shortcut (`funkbox/studio/GIMP-MCP.cmd`, rendered from `GIMP-MCP.cmd.template` by `install.py --with gimp`) runs in your normal session: it
syncs the plug-in into your real GIMP profile and opens GIMP with the MCP server already listening.
`funkbox/studio/start_local_mcp.py` is the FastSD launcher (copied into `vendor/fastsdcpu` by `install.py --with fastsd`): it binds 127.0.0.1 only and
works around FastSD's FastAPI-kwargs crash. Register everything with `python install.py --register desktop` (quit Claude Desktop first).


**Sizes cost time.** The OpenVINO model is compiled once per image size (~2.5 min on the laptop). funkbox remembers which
sizes the running FastSD has compiled; asking for a new one returns a warning (`first image at 512x896 will take ~2-3 min`)
and does nothing until the call is repeated with `confirm_slow=true`. Restarting FastSD forgets the sizes.

**Licence.** The default model, SD-Turbo, is under the Stability AI Community License: commercial use is free under
US$1M annual revenue, and Stability AI asks commercial users to register. See `THIRD_PARTY_NOTICES.md`.

**Test stub.** With `STUDIO_FAKE_SD=1` the three AI image tools never start FastSD: they draw deterministic procedural
pictures (seeded from the prompt hash and the seed) at the requested size, and img2img blends toward the init image by
`strength`. The tests use it. The request-building rules (aspect presets, the img2img step clamp, the path fence for
`init_image`) live in `funkbox/studio/sdcore.py`, which the launcher imports too (`install.py --with fastsd` copies both files).
`init_image` and upscale sources must resolve (symlinks followed) inside `STUDIO_OUT` or the funkbox folders.


## Aspect ratios and SD-Turbo

SD-Turbo is trained at 512x512. Measured on an Iris Xe laptop: `square` gives clean single subjects in ~1.3 s; `story` (512x896) often duplicates the subject (two dogs stacked, a living room on two floors). For vertical ads generate `square` (optionally `image_upscale` to 1024) and let reels and posters cover-crop it with a slow camera move. img2img (`image_restyle`) loads a second pipeline the first time (~40-55 s), then runs in seconds.
