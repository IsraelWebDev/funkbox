## Reels and projects: shot list to video ad (9:16, 16:9 or both)

`funkbox reel ...` / `funkbox project ...` and the `reel_*` / `project_*` MCP tools turn a JSON shot list (`*.reel.json`) plus a copy deck (`copy.json`) into a
9:16 ad (or 16:9, square, 4:5, several at once): stills with Ken Burns or 2.5D parallax, video clips and screen
recordings framed with push-ins and speed ramps, your real product photo composited in (shadow + a
capped global Lab colour match, label pixels stay exact), boxed animated captions, a branded end card,
local voice-over (Piper), a music bed with ducking and loudness normalisation.

**Text is exact by construction.** Shots reference copy by ID; the validator rejects any literal-text key;
`reel render --audit` writes `<name>.text_audit.json` proving every drawn string equals its copy entry byte for
byte. The only tool that writes the copy deck is `project_approve_copy`, after your approval.

```
pip install -r requirements-reel.txt          # optional: onnxruntime + piper-tts
python -m funkbox reel setup-models           # once: depth (Apache-2.0) + voice (public-domain data), hashes verified
python examples/reel_demo/make_assets.py      # synthetic demo assets
python -m funkbox reel validate   examples/reel_demo/demo.reel.json
python -m funkbox reel storyboard examples/reel_demo/demo.reel.json -o out/sb.png
python -m funkbox reel still      examples/reel_demo/demo.reel.json --shot s3 --t 0.8
python -m funkbox reel render     examples/reel_demo/demo.reel.json --draft --audit
python -m funkbox reel cutout photo.jpg -o inputs/brand/bottle.png    # product on a plain background -> alpha PNG
python -m funkbox reel beats music.mp3
```
The MCP tools are `reel_copy_lines`, `reel_templates`, `reel_validate`, `reel_storyboard`, `reel_still`, `reel_render`
(registered with everything else by `python install.py --register ...`). On Linux without a
display set `FUNK_GL_BACKEND=egl`. Without the depth model `parallax` degrades to Ken Burns; without a Piper
voice `voiceover: true` is an error (the sine-burst stub exists for tests only: `REEL_TTS=stub`).
Fonts in `fonts/` are SIL OFL (Oswald, DM Serif Display, and Rubik Bold, which covers Hebrew and Cyrillic); licence
texts alongside.

**Fonts by name, and exact glyphs.** Any font setting (`brand.font_caption`, `font_display`, `font_fallback`, in reels
and posters) takes a file path or a Google Fonts name: `"Rubik:800"`, `"Secular One"`, `"Exo 2:700:italic"`. A name
is downloaded once into `FUNK_HOME/fonts-cache/` (only OFL, Apache and UFL fonts; the licence file is saved next to
it) and works offline afterwards; a variable font is cut to the weight asked for. Every caption and text layer is
checked against the font that draws it: a character the font has no glyph for is an error that names it (for
example `'צ' (U+05E6 Hebrew Letter Tsadi)`), never an empty box that the text audit would miss. `brand.font_fallback`
(default: the bundled Rubik Bold) draws any text block the main font cannot, so a Hebrew line in an English design
just works; a block neither font covers is an error. Right-to-left text is laid out right to left.

## Projects: drop files in, get an ad

### Make an ad in 5 steps

1. **Drop your files** into `inbox/` (run `python -m funkbox project shortcuts` once for desktop links "funk inbox" and
   "funk projects"): a product photo (a plain background is fine), a logo, music, video clips, scene images. Or give
   Claude any folder path (external folders are copied, never moved).
2. **Start and ingest.** Tell Claude "make an ad for GLOWMOP". It calls `project_new`, then `project_ingest`, which files
   everything by type, cuts the product out (with a quality score; it says when to use GIMP instead) and shows a
   contact sheet. If two images could be the product it asks you, then calls `project_set_product`.
3. **Brand.** `project_brand` picks the primary colour from the product label, the darkest colour as secondary,
   and white or near-black text (WCAG contrast >= 4.5), with your fonts if you dropped any. Override with `project_set_brand`.
4. **Approve the copy.** Claude drafts lines with `project_propose_copy` and shows you the numbered, exact listing. You approve
   it in a prompt in your client (MCP elicitation), or in chat if your client has no prompts. Only approved lines can
   reach the screen or the voice-over, and `copy.json` is then hash-locked: if it changes afterwards, `validate` and
   `render` refuse until you re-approve (`python -m funkbox project approve-copy NAME`, which asks you y/N at the terminal).
5. **Build and render.** `project_auto_reel` writes `projects/<name>/<name>.reel.json` (hook, body lines, branded end card,
   the product in every other shot, captions that dodge the product) and a storyboard to look at; then
   `project_render` (draft first). The text audit proves every drawn string equals your approved copy.

Name the copy lines `hook`, then any body lines, plus `end_head`, `end_sub` and `url` for the end card. Everything is
also available from the CLI (`python -m funkbox project new|ingest|brand|propose-copy|approve-copy|auto|render`)
and the reel tools (`reel_validate`, `reel_storyboard`, `reel_still`, `reel_render`) accept `project=<name>` instead of a reel.
The reel file is plain JSON you can edit by hand; copy still comes only from `copy.json` by id.


## Shot lists for specific briefs

`project_auto_reel` is a first draft. When a brief gives exact timings, framing or mostly screen recordings, edit the
reel JSON (or write it from scratch) with these fields. Every one of them is optional, and none changes pixel content:
framing only crops and scales.

**Framing (`"motion": {"type": "frame"}`)** works on video and stills. Rectangles are `[x, y, w, h]` in fractions of the
*source*; each frame shows the smallest window of the *output* aspect that contains the rectangle, so one rectangle
gives the right crop in 9:16 and in 16:9.

```json
{"id": "accept", "source": {"video": "clips/app.mp4", "trim": [3.2, 6.0]}, "duration": "beats:4",
 "motion": {"type": "frame", "to": [0.72, 0.54, 0.20, 0.13], "at": [0.1, 0.8], "ease": "out"},
 "caption": {"copy_id": "hook", "style": "red-box", "anchor": "top"}}
```

- `from` (default: the whole frame) and `to`, moving during `at` = [start, end] fractions of the shot; or `keys`:
  `[{"rect": [...], "t": 0.0}, {"rect": [...], "s": 1.6, "ease": "hold"}]` with `t` (fraction) or `s` (seconds) on
  every key. Eases: `smooth` (default), `linear`, `in`, `out`, `hold` (jump at the key).
- The centre moves linearly and the size changes geometrically, so a push-in feels even.
- `kenburns` (zoom / pan) and `none` also work on video now. `parallax` needs a still.

**Sources.** `focus: [x, y]` moves the default crop centre (a 16:9 recording in a 9:16 frame shows its left third with
`[0.2, 0.5]`). `fit: "contain"` never crops; the rest is `pad`: a colour (`"#101820"`) or `"blur"` (a blurred, dimmed
copy of the same frame). On video, `speed: 2` plays twice as fast and `speed: [1, 3]` ramps from 1x to 3x over the
shot; `validate` warns when a clip runs out before its shot ends (its last frame is held unless `loop` is set).

**Beat grid.** `"audio": {"bpm": 110, "beat_offset": 0.0}` sets a fixed grid (one beat = 60 / 110 = 0.545 s) without
analysing any music. `"duration": "beats:4"` (halves allowed: `beats:1.5`) and `snap_cuts_to_beats` use it. Without `bpm`
the grid is detected from `audio.music`.

**Formats.** `"formats": ["9x16", "16x9"]` (presets `9x16`, `16x9`, `1x1`, `4x5`, or `{"id", "width", "height",
"safe_area"}`) renders the same edit at each size: `reel_render` / `project_render` write `<name>-9x16.mp4` and
`<name>-16x9.mp4` (`format=` renders one; `reel_storyboard` and `reel_still` take `format=` too). A shot can override
per format: `"formats": {"16x9": {"caption": {"anchor": "bottom"}, "motion": {...}}}` (motion is replaced whole;
`caption.anchor`, `product` x/y/height and `source` focus/fit/pad are merged). `validate` checks every format and
prefixes problems with `[16x9]`. Safe areas: `reels` (keeps captions clear of the 9:16 app UI), `landscape`, `none`.

**`project_auto_reel` options.** `clip_share` (default: follows the assets, so mostly recordings gives mostly clips),
`product_every` (2 = every other shot, 0 = only on the end card), `formats`, `bpm`.
