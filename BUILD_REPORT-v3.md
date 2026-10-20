# BUILD REPORT v3 "reel"

All milestones R0-R8 complete (none BLOCKED). Commits: `v3 R0` ... `v3 R8`.

## What was built
`reel/` package (schema, copydeck, timeline, depth, motion, product, captions, endcard, audio, render,
storyboard, plus helpers `validate.py`, `models.py`, `encode_util.py`), `reel_cli.py`, `reel_mcp.py`,
`templates/{captions,endcards}`, `examples/reel_demo/` (generator for synthetic assets), `fonts/` (OFL),
`requirements-reel.txt`, tests in `tests/reel/`. No v1/v2 file was modified (no hooks needed).

## Test result
`164 passed, 1 skipped in 216.30s` (full suite incl. v1/v2; the skip is the pre-existing one).
Exactness tests: R3 (label survives compositing, +/-3 on >=99% of opaque pixels, also with scaling;
DEMO-123 template match > 0.95), R4 (typewriter frame k == text prefix, pixel-exact), R5 (audit byte-equality).

## Demo draft render (container, Mesa llvmpipe, no GPU)
12 shots + end card, 25.0 s, 540x960 @ 24 fps, 600 frames, real Piper voice: **43.2 s** wall (llvmpipe, not the laptop).
Storyboard of 13 tiles: 2.9 s. Full-res depth-model still (540 wide) 3.6 s.

## Models (setup-models run and verified here)
- Depth: onnx-community/depth-anything-v2-small @ 4472b736..., `onnx/model.onnx`, sha256
  `afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c`, Apache-2.0 (Small only).
- Voice: rhasspy/piper-voices @ c10ece1a..., en_US-ljspeech-medium.onnx sha256
  `6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22`, .onnx.json sha256
  `141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860`. MODEL_CARD: dataset licence public
  domain (LJ Speech); repo MIT. Commercial use OK, so TTS is enabled.
- Note: the `piper-tts` runtime package is GPL-3.0 (bundles espeak-ng). Kept as an optional extra, not distributed.
- Fonts: Oswald (static SemiBold instanced from the OFL variable font) and DM Serif Display, both SIL OFL 1.1, texts in `fonts/`.

## Deviations (smallest changes that keep tests meaningful)
1. `brand.text` is a colour in the plan's own example but `text` keys are forbidden: allowed only at exactly `brand.text` and only as `#rrggbb`.
2. `match_color` is a CAP. The applied strength is backed off until <=0.1% of product pixels would clip by >1.2 sRGB levels, because gamut clipping is not invertible and would alter the label. For gamut-edge brand reds (the demo bottle) the effective strength is small (~0.06 of the requested 0.6). `ColorTransform.applied` reports it.
3. Resize happens before the colour transform (so inverse(render) == resized source exactly); product sprites are integer-positioned.
4. Depth blur (9-tap separable) is done once on the CPU at upload instead of a GL pass; parallax always runs at 1.07x base zoom so the <=3% offset can never reveal outside pixels (border test checks alpha from the shader).
5. Stub TTS is only used when `REEL_TTS=stub` (tests). With no voice installed `voiceover: true` errors, rather than silently putting sine beeps in an ad.
6. `auto` duration with no VO line = 1.5 s (plan only defines the VO case). VO `adelay` uses the timeline's 0.1 s lead.
7. End-card audio: fade-out ends AT the card start (0.3 s before it) so the whole card is digital silence.
8. Mix: a loud VO over a quiet bed has a big peak-to-loudness ratio, so plain `loudnorm` fell back to dynamic mode and landed at -17.6 LUFS. Pipeline is now gain -> limiter (-3 dBFS) -> measure/trim -> `loudnorm` linear; results -14.0 LUFS (stub and Piper).
9. Caption reveals are laid out once on the full text and show a prefix of that layout (so a frame is exactly `text[:n]`); `\n` and wrap spaces count as typed characters; trailing spaces are not drawn.
10. Extra files beyond the layout: `reel/validate.py`, `reel/models.py`, `reel/encode_util.py` (own AAC 192k mux; funkgl.encode untouched). MCP `render` also takes `audit` (default true); copy_lines takes an optional deck path.
11. Product/caption sizes in draft are the same fractions of a smaller frame, so wraps can differ slightly from full res.
12. Parallax in draft falls back to Ken Burns by design (draft = no depth model).
