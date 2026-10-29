# What a funkbox promo may claim

Every claim in a promo (on screen, in a title, description or post) must be one of these facts, or follow
directly from one, or be a number measured in that day's run. The reviewer rejects anything else. When a fact
changes in the code, change it here first.

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

## Limits (say these plainly when relevant; never imply otherwise)
- L1. It does not generate real motion of people; it animates stills or uses clips you supply.
- L2. It installs locally (about 5 minutes); it is not a hosted service.
- L3. Videos are short (the reel engine caps at 60 s).

## Never
- Comparisons that name or disparage another product, user counts, stars, "best", "fastest", "#1", testimonials,
  prices of other tools, or anything about Anthropic/Claude beyond "works with Claude".
