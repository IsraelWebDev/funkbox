## Reels and projects: shot list to video ad (9:16, 16:9 or both)

`funkbox reel ...` / `funkbox project ...` and the `reel_*` / `project_*` MCP tools turn a JSON shot list (`*.reel.json`) plus a copy deck (`copy.json`) into a
9:16 ad (or 16:9, square, 4:5, several at once): stills with Ken Burns or 2.5D parallax, video clips and screen
recordings framed with push-ins and speed ramps, your real product photo composited in (shadow + a
capped global Lab colour match, label pixels stay exact), boxed animated captions, a branded end card,
local voice-over (Kokoro, or Piper), a music bed with ducking and loudness normalisation.

**Text is exact by construction.** Shots reference copy by ID; the validator rejects any literal-text key;
`reel render --audit` writes `<name>.text_audit.json` proving every drawn string equals its copy entry byte for
byte. The only tool that writes the copy deck is `project_approve_copy`, after your approval.

```
pip install -r requirements-reel.txt          # optional: onnxruntime + kokoro-onnx + piper-tts
python -m funkbox reel setup-models           # once: depth + Kokoro (Apache-2.0) + Piper voice (public-domain data), hashes verified
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
display set `FUNK_GL_BACKEND=egl`. Without the depth model `parallax` degrades to Ken Burns; without a voice
`voiceover: true` is an error (the sine-burst stub exists for tests only: `REEL_TTS=stub`).

**Voices.** `"audio": {"voiceover": true, "voice": "kokoro:af_heart", "voice_speed": 1.0}`. The default is Kokoro-82M
(natural, about twice real time on a laptop CPU, 24 kHz) when installed, else Piper (`en_US-ljspeech-medium`). The
approved ad voices are `kokoro:af_heart` (US, female), `kokoro:am_michael` (US, male) and `kokoro:bm_george` (UK, male);
any Kokoro voice id works (`a`/`b` prefixes speak US/UK English). `"voice": "piper"` keeps the old voice;
`voice_speed` is 0.7..1.4. `FUNK_VOICE` sets the default for every reel. Each line is cached per voice and speed.
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

## Voiceover

A reel can speak its copy with a local voice. Set `audio.voiceover: true` and a `voice` such as `kokoro:af_heart`, and each shot's `vo` names a copy line. The spoken text is that line's `say` form in the copy deck, with its `pronounce` substitutions applied, so the caption, the voice and the text audit all come from one approved deck.

- Setup: `pip install -r requirements-reel.txt`, then `python -m funkbox reel setup-models`. Without a voice, `voiceover: true` is an error.
- The timeline sizes each shot to its spoken line plus a 0.35 s tail (`VO_TAIL`). The voice starts 0.1 s into the shot (`VO_LEAD`), so at least 0.25 s of silence follows the line. A shot too short for its line fails validation.
- `--audit` writes the spoken lines into `<name>.text_audit.json` as `voiceover` entries.
- A worked example with a music bed is in [`examples/voiceover_demo/`](../examples/voiceover_demo/README.md).

## Variants: several versions of one ad

`funkbox reel variants` renders N alternatives of one reel from a vary file. Each key maps to a list of values, all lists have the same length, and position i of every list makes variant i:

```
funkbox reel variants demo.reel.json --vary variants.json --count 3 --sheet --name demo
```

- Allowed keys: `shots.<id>.source` and `shots.<id>.motion`, `brand.palette`, `audio.music`, and `captions.<shot>.copy_id` (approved lines only). A key that writes copy, a literal `text`, or a file outside the project is refused.
- Every variant is validated before any render starts. One invalid variant stops the whole run and names it.
- Every variant is text-audited against the approved copy deck. The copy lock applies: an edited, unapproved `copy.json` refuses the run.
- Output goes to `<name>-variants/`: `vNN.reel.json`, `vNN-<format>.mp4` (`vNN.mp4` when the reel declares no formats), `vNN-<format>.text_audit.json`, and `variants.json` (the manifest, with each variant's values and hashes). If any audit fails the command exits 1, and the manifest is still written.
- `--sheet` also builds `contact_sheet.png`: one cell per variant, numbered 1 to N. The numbers are the only labels, so the copy is not changed, and the sheet has its own audit. `--sheet-video` also renders `contact_sheet.mp4`.
- Drafts are the default (540 px, 24 fps, no depth model); `--full` renders every variant at full quality. At most 12 variants per run.
- To render one chosen variant in full, copy its `vNN.reel.json` into the original reel's folder first (its asset paths are relative to that folder), then run `funkbox reel render` on the copy.
- The MCP tool is `reel_variants`. It takes `reel` (a path, or a JSON object), `vary` (a JSON object or its text), `name` (required unless `project` is given), `draft` (default true), `sheet`, `sheet_video` and `project`, and it returns the manifest. It has no `count`, `out`, no-audio or `full` options.
- Music per variant: `audio.music` takes a list of score paths, one per variant. Make the set with `funkbox music vary SCORE.music.json --brief brief.json --seed 7 --count 3` (the CLI prints each file), then point the vary file at those files, relative to the reel's folder:

  ```
  {"audio.music": ["music/BED-01.music.json", "music/BED-02.music.json", "music/BED-03.music.json"]}
  ```

  Each path must exist and stay inside the project. The check runs before any render.
- Loudness: each rendered MP4 is measured after the render (EBU R128, integrated) against the reel's `audio.loudness_lufs` (default -14). The result is in the manifest per file (`loudness`, `loudness_ok`) and printed under each variant. A file more than 1 LU from the target is reported as `MISSES`; the run still succeeds, and the manifest is still written. The loudness report is not part of `manifest_sha256`.
- Not built yet: `--still` (PNG openings), `from`/`to` keys, speed, and grid order.

## Type presets: one setting per kind of video

`"type": "explainer"` in a reel picks `templates/types/explainer.json`. A preset fills the fields the reel leaves unset: `brand.font_caption`, `brand.font_display`, `brand.font_fallback`, `audio.music_gain_db`, `audio.duck_db`, `audio.snap_cuts_to_beats`, and the caption style of each shot that has a caption. A field the reel sets always wins. A reel without a `type` is read exactly as before.

Shipped types:
- `explainer`: a condensed caption face, a serif display face, speech-first audio.
- `tech`: Inter captions, Space Grotesk display, bold-pop captions.

Every preset keeps `brand.font_fallback` (bundled Rubik Bold, which draws Latin, Hebrew and Cyrillic). A caption the main font cannot draw falls back per text block, and `validate` warns and names the characters it draws in the fallback.

Every font a preset uses is bundled in `fonts/` with its OFL licence beside it, so presets work offline. The tests check each bundled font's licence and that each type's fallback draws its Hebrew test line.

Not yet: the `--type` flag on the CLI and a project-wide default type. Presets set only the fields above; pacing and end-card keys come with later cards.

## Hook title and shot length

`"hook": true` on a reel (or in a type preset) draws shot 1's caption as a title for the first 0.8 s: the display font, 1.4 times the caption size, in a box, centred. The caption then starts at 0.8 s on its own reveal clock. The title wraps to as many lines as fit at that size; a two-line title is not forced. Validate needs a caption with a copy line on shot 1 and checks the title fits every format.

`"shot_target_s": 2.5` sets the length of `auto` shots: a shot with a voiceover line lasts at least its line plus padding and at least the target; a shot with no voiceover lasts the target (default 1.5 s). The tech preset sets 2.5. Cuts still follow the voiceover; cuts at sentence ends are not separate. With beat snapping on (the tech preset), a cut can move up to a quarter second earlier, so a shot may end just under its target.
## Word highlight

A caption can put a pill behind the word the typewriter has reached. Set it in a caption style file, templates/captions/<name>.json (every caption in that style gets it), or on one caption (it overrides the style):

    templates/captions/<name>.json: "highlight": {"color": "secondary", "radius": 0.3}
    "caption": {"copy_id": "hook", "highlight": {"color": "#ffd400"}}

- `color`: `primary`, `secondary`, `text` (palette colours) or `#rrggbb`. Default `secondary`, which shows on the default red box. A colour equal to the box or the text colour hides the pill, or the words with it. `lower-third` has a secondary box, so give its pill another colour.
- `radius`: corner rounding from 0 (square) to 0.5 (fully round). Default 0.3.
- The pill covers the last word typed so far and moves on word by word. It does not cover spaces, and it follows the reveal. With reveal `none` (the default) the whole caption shows at once, so the pill stays on the last word; use reveal `words` for a pill that moves.
- A blank line (a paragraph break) keeps the pill on the last word before it.
- A line with any right-to-left text gets no pill: their words are reordered on screen, so the pill would not sit on the right word.
- Word timing is estimated from characters per second. The voice path has no per-word timings yet, so on voiced shots the pill can drift from the spoken word.
- A caption cannot switch off its style's pill yet; use a style without one.
- The text audit records the copy only, not the highlight.

## Callout tags

A shot can carry labels that point at a place in the picture. Each label shows a line of deck copy, centred on an anchor point, with an optional leader line to a second point:

    "callouts": [{"copy_id": "hook", "at": [0.5, 0.3], "line_to": [0.5, 0.9], "start": 0.5, "end": 2.0}]

- `copy_id` (required): a copy line in the deck. The text comes from the deck, so the text audit lists it.
- `at` (required): the label's centre, as [x, y] fractions of the frame, from 0 to 1.
- `line_to` (optional): the end of the leader line, in the same units. The line runs from the label's centre to this point and ends in a dot. Without it the label is drawn alone.
- `start`, `end` (optional): seconds into the shot when the label shows. They default to 0 and the end of the shot. `end` must come after `start`.
- `style` (optional): a caption style. Default `callout` (templates/captions/callout.json).

Labels are drawn over the shot's caption. The label box and the leader line take the style's box colour, which is the brand's text colour (white by default). The optional red sub-label in the spec is not built yet.

The label must stay inside the safe area for the format. A label that would sit in the top or bottom band, or past the side margins, is a validation error: move `at` towards the middle. The window must also fit the shot: `start` must come before the shot ends, and `end` must not be later. For `auto` and `beats:N` shots, the shot length is the one the timeline resolves.

## Annotations

A shot can carry drawn shapes over its picture: arrows, dashed guide lines, an X and a check sticker. They are drawn in frame pixels, in the brand's primary colour, and draw on over 0.4 s:

    "annotations": [{"type": "arrow", "points": [[0.2, 0.5], [0.8, 0.5]], "start": 0.5, "end": 2.0}]

- `type` (required): `arrow` takes two points (tail and head; the arrowhead appears once the shaft is drawn). `dashed` takes two or more points, drawn as a polyline. `x` and `check` take one point, the centre. Both stickers are 12% of the frame width across.
- `points`: [x, y] fractions of the frame, each from 0 to 1.
- `start`, `end` (optional): seconds into the shot. They default to 0 and the end of the shot, and `end` must come after `start`. The window must fit the shot, as for callouts, including `auto` and `beats:N` shots.
- `draw` (optional, default true): `false` shows the whole shape at once instead of drawing it on.

Shapes carry no text, so they are not in the text audit. The spec's sidecar listing is not built; the reel JSON is the record. There is no per-shape colour yet.

## End banner

`bottom-banner` is a headline-only end-card template. Its headline is bottom-anchored (so a long headline is checked against the frame) on the brand primary colour, types at 14 characters a second, and shows a cursor. It has no sub-line, URL or product.

- Length: `end_card.duration` (default 5 s, 0.5 to 30 s). The banner starts at 0.2 s. Typing takes about characters / 14 seconds, so for a banner of 3 to 6 s, pick a duration that covers the typing plus a short hold.
- Reveal: `end_card.headline_reveal` is `typewriter` by default. Set `none` to show the headline at once.
- Copy: `copy.headline` only. A `sub` or `url` copy id is a validation error for this template.
- The other templates keep their static URL: brand-split and centered-logo put it in both brand bars, and logo-clean puts it under the last text block.
- The end-card renderer lays out `sub` and `url` only when the template declares those slots. Existing templates declare both, so their output is unchanged.
## Shot lists for specific briefs

`project_auto_reel` is a first draft. When a brief gives exact timings, framing or mostly screen recordings, edit the
reel JSON (or write it from scratch) with these fields. Every one of them is optional, and none changes pixel content:
framing only crops and scales.

**Timeline.** `funkbox reel timeline X.reel.json` (or `reel_timeline(reel)`, which returns the URL) opens a page on
this computer that shows the reel as a strip of shots, each as wide as its length, with a frame of each. Drag a shot to
move it, drag its right edge to change its length (whole and half beats when the reel has a beat grid), click it to set
a clip's in and out points against frames of the source, its speed and crop, its caption and voice line, or to
duplicate or delete it; Undo and Revert work until you Save. Saving validates the whole reel first (an invalid reel is
never written), keeps the previous file as `X.reel.json.bak` and appends what changed, in plain words, to
`X.edits.jsonl`. Render preview makes a draft of the saved reel and plays it on the page.

The reel file is the shared state between you and Claude: when Claude changes it, the page reloads within two seconds
(or, if you have unsaved changes, asks which to keep) and the log records it as a change made outside the page.
`reel_timeline(reel, action="changes")` gives Claude your edits ("moved 's1' from position 1 to 4", "'s3': duration
beats:2 -> beats:4") before it touches the reel again. The page only listens on 127.0.0.1 and refuses requests from
other sites.

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

**Grid sources (`"source": {"grid": ...}`).** Several clips side by side in one shot (before/after, a set of app screens),
arriving one after another, each with a label from the copy deck:

```json
{"id": "screens", "duration": "beats:4", "source": {"pad": "#101820", "grid": {
   "cols": 2, "rows": 2, "gap": 0.02, "reveal": "sequence", "stagger": "beats:1", "ease": "out",
   "cells": [{"video": "clips/a.mp4", "fit": "contain", "label": "tough", "label_style": "bold-pop"},
             {"video": "clips/b.mp4", "label": "gentle"}, {"video": "clips/c.mp4", "fit": "cover"}, {"video": "clips/d.mp4"}]}}}
```

- `cells` is row-major and must have exactly `cols * rows` entries (the error names the shot and both counts). Each cell
  takes a `video` (a path, resolved like other sources; image cells are not supported) and `fit`: `contain` (default) or `cover`. The pad
  colour is the source's `pad` (default black, `"blur"` pads each cell with a blurred copy; the gaps then stay black).
- `gap` is a fraction of the output width, 0 to 0.2 (default 0.02), between columns and between rows. Cells are equal in
  size and have no border.
- `label` is a copy id (never text; validation checks it against the deck like a caption's). It is drawn with the caption
  path (`label_style`, default `bold-pop`) at the bottom of its cell, laid out and fit-checked against the cell's size
  (so the type scales with the cell, and the 0.032 minimum is measured on the cell's height) and kept out of the reel's
  `safe_area` bands (the box moves up inside its cell; if it cannot, `validate` reports an error),
  and the text audit gets one entry per label (`slot: "grid_label"`, `instance` = the cell index, 0-based, as a string).
  A cell that cannot finish arriving before its shot ends would never be drawn and its label could not be audited, so
  `validate` reports it as an error (shorten the stagger or lengthen the shot).
  In the audit, `complete_frame` is the first frame the exact text is drawn; while the cell is still fading in that can be
  at low opacity. `opaque_frame` is the first frame where the label is at full opacity, the first it can be read.
- `reveal`: `none` (default: every cell is drawn for the whole shot) or `sequence`: cell `i` arrives at shot time
  `i * stagger`. `stagger` is seconds, or `"beats:N"` (N beat periods, needs `audio.bpm` or music). Default: `beats:1`
  when the reel has a beat grid, else 0.25 s. A cell arrives by dropping in from 10% of its height above its place (inside
  its own slot) while fading in over 0.25 s, eased with `ease` (`smooth`, `linear`, `in`, `out`, `hold`; default `out`).
  Before that the cell and its label are not drawn; the label appears and fades with its cell.
- A video cell plays from its own arrival (the whole shot with `reveal: "none"`), and holds its last frame if it runs out.
- The shot's own `motion` (`none`, `kenburns`, `frame`) applies to the whole composed grid, labels included. Per-cell
  push-ins are not supported. `parallax` needs a still and is an error here. A zoom or `frame` that crops a label at the
  frame edge makes `validate` warn.

**Caption styles.** Styles live in `templates/captions/` (`red-box`, `lower-third`, `bold-pop`, `auto-box`); `box` is
`primary`/`secondary`/`text`/`#rrggbb`, or `"auto"` (only for `box`, as in `auto-box`): the box then takes a dominant
colour from the shot's own frame (the text switches to dark when that reads better; contrast-checked), falling back to `brand.primary`.
`size` and `cps` also accept `"auto"` (as `auto-box` does): the font size then follows the line's length (0.062 of frame
height at 14 characters or fewer, 0.046 at 40 or more, linear between, never below the minimum), and a typewriter reveal
with `cps: "auto"` finishes in about 55% of its shot (cps = characters / (0.55 x shot seconds), clamped to 14..40; 28 when
no shot length is known). Every other style keeps its fixed numbers, so existing reels render exactly as before.

**`brand.from`.** A reel's `brand` may name an image (or an mp4, read at its frame at 1.0 s) with `"from"`, relative to the reel's folder. The primary, secondary and text colours are then resolved from that file with the same helpers as `brand_from_assets` (the most vivid usable cluster, a dark colour for secondary, and white or near-black text, darkened or lightened to reach contrast 4.5). The explicit `primary`, `secondary` and `text` keys are the fallback: if the file is missing or has no usable colours, validation warns and names the file, and the explicit colours are used. A reel without `from` renders exactly as before.

**Beat grid.** `"audio": {"bpm": 110, "beat_offset": 0.0}` sets a fixed grid (one beat = 60 / 110 = 0.545 s) without
analysing any music. `"duration": "beats:4"` (halves allowed: `beats:1.5`) and `snap_cuts_to_beats` use it. Without `bpm`
the grid is detected from `audio.music`: the tempo comes from the whole spectrum, the phase (where the beats fall) from
the low band (kick range, up to 150 Hz), so off-beat hi-hats do not pull the grid half a beat late. With no clear
low-band pulse the phase falls back to the whole spectrum. `funkbox reel storyboard` and `project auto` print
the grid (BPM, beat count, first 8 beats) and each cut's distance in ms to the nearest beat, or `beat grid: none`.

**Formats.** `"formats": ["9x16", "16x9"]` (presets `9x16`, `16x9`, `1x1`, `4x5`, or `{"id", "width", "height",
"safe_area"}`) renders the same edit at each size: `reel_render` / `project_render` write `<name>-9x16.mp4` and
`<name>-16x9.mp4` (`format=` renders one; `reel_storyboard` and `reel_still` take `format=` too). A shot can override
per format: `"formats": {"16x9": {"caption": {"anchor": "bottom"}, "motion": {...}}}` (motion is replaced whole;
`caption.anchor`, `product` x/y/height and `source` focus/fit/pad are merged). `validate` checks every format and
prefixes problems with `[16x9]`. Safe areas: `reels` (keeps captions clear of the 9:16 app UI), `landscape`, `none`.

**`project_auto_reel` options.** `clip_share` (default: follows the assets, so mostly recordings gives mostly clips),
`product_every` (2 = every other shot, 0 = only on the end card), `formats`, `bpm`.


## Plan-driven renders (proposed, not live)

The tools and their input schemas are listed in [docs/catalog.json](https://github.com/IsraelWebDev/funkbox/blob/main/docs/catalog.json). The file is generated from the MCP server: `python -m funkbox catalog` (`--check` verifies it). The website is proposed to plan a render from this catalog: an AI planner writes a plan of these tools, the server checks the plan, and a preview runs it at low resolution or shows a storyboard before a paid render runs the same stored plan. Nothing here changes how the local tools work today. Decision and sign-offs: [docs/adr-plan-driven-renders.md in IsraelWebDev/funkbox-online](https://github.com/IsraelWebDev/funkbox-online/blob/main/docs/adr-plan-driven-renders.md).

## Text mask from the copy deck

A funk2 shot can take its one text mask from a copy line: `source: {"funk2": "recipe.json", "mask_copy_id": "hook"}`. The recipe's text-masked layer (`mask: {"font", "size", "align"}`) must not carry its own `text`; the copy line supplies it. Validate checks that the copy id exists, that the recipe has exactly one text-masked layer, and that the mask has no text of its own. The text audit lists the shot as `<shot>/source_mask/0` with the exact copy text and the frames it shows, so a masked word is covered by the copy lock like a caption.
