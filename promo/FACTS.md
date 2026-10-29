# What a funkbox promo may claim

Every claim in a promo (on screen, in a title, description or post) must be one of these facts, or follow
directly from one, or be a measured fact from that day's `measured.json` (an `M-` key, written by
`promo/measure.py`). The reviewer rejects anything else. When a fact changes in the code, change it here first.

## Make claims strong, not loud
A claim gets past the reviewer AND lands with viewers when it is specific and checkable:
- **A number from today:** "rendered in 83 seconds on a 2-core CPU with no GPU" (M-render) beats "fast".
- **The real thing on screen:** a real file, command or shot-list line (the code cards), the text audit's result.
- **A before/after or a cause:** "change one line, render again" (F12, F13), "one edit, four formats" (F9).
- **The video itself as proof:** everything in a promo was made by funkbox (F18), so "this video" is evidence.
Words like fast, easy, powerful or best add nothing and invite a FAIL. Quote an M-key's "say" sentence or a fact
closely; rounding a number down is fine, rounding it up is not.

## What it is
- F1. funkbox is free and open source (MIT), at github.com/IsraelWebDev/funkbox.
- F2. It runs on your own computer: no cloud, no accounts, no API keys, no credits.
- F3. No NVIDIA GPU is needed. OpenGL 3.3 is enough; an Intel integrated GPU works.
- F4. Claude drives it over MCP (one server, 37 tools), in Claude Desktop or Claude Code; everything also has a command line.
- F5. There is a Claude Code plugin: /plugin install funkbox --marketplace IsraelWebDev/funkbox

## What it makes
- F6. Seamless looping videos from GPU shaders, with a machine check that the last frame flows into the first.
- F7. Posters and photo edits whose text is exact, with layered export for GIMP.
- F8. Images from a description with FastSD (SD-Turbo); about a few seconds per 512x512 image on an Intel iGPU once warm.
- F9. Short product and app video ads: 9:16, 16:9 (and 1:1, 4:5) from one edit.
- F10. Ads use stills (parallax, Ken Burns), clips and screen recordings with push-ins and speed ramps.
- F11. Cuts can follow a beat grid (e.g. 110 BPM); music is ducked under a local voiceover (Piper).

## The copy lock
- F12. Claude can only propose wording; the user approves it, and the approved copy is hash-locked.
- F13. Every render writes a text audit proving each on-screen string equals the approved copy, byte for byte.
- F14. Framing only crops and scales; it never changes what a recording shows.

## Proof points (real outputs in the repo)
- F15. The demo ad (examples/mimic): 23 s, fictional brand GLOWMOP, rendered on an Intel Iris Xe laptop in about 3.5 minutes.
- F16. Its scenes came from FastSD, its bottle is drawn by code, its music is synthesized.

## About these promos
- F18. Every promo on this channel is made with funkbox; a second AI agent reviews it before it posts, and the code
  that does this is in the repo (promo/).
- F19. The promos use no video-generation model: motion comes from shaders, parallax, push-ins and edits; stills come
  from FastSD or real files.

## Measured each run (measured.json; cite the M-key)
- M-render (this video's render time and machine), M-machine (CPU cores, GPU or none), M-video (length, size),
  M-audit (strings checked, all exact), M-tools / M-presets / M-shaders / M-tests (counts read from the code),
  M-commits (what changed since the last promo, by commit subject).

## Limits (say these plainly when relevant; never imply otherwise)
- L1. It does not generate real motion of people; it animates stills or uses clips you supply.
- L2. It installs locally (about 5 minutes); it is not a hosted service.
- L3. Videos are short (the reel engine caps at 60 s).

## Never
- Comparisons that name or disparage another product, user counts, stars, "best", "fastest", "#1", testimonials,
  prices of other tools, or anything about Anthropic/Claude beyond "works with Claude".
