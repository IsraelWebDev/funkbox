# funkbox 4.2.0

Everything since 4.1.0. The MCP server goes from 37 to 47 tools; the full test suite has 578 tests.

## Posters
- **Fonts by name** (`"Rubik:800"`, fetched once from Google Fonts with their licences), a glyph check that names
  any missing letter, and a fallback font (Rubik, bundled) so Hebrew and Cyrillic work out of the box.
- **Shapes and SVG**: polygon, rect, ellipse, thick mitred polylines and SVG path data (curves, arcs, fill rules);
  SVG files placed as layers; any layer clipped to a shape.
- **Text geometry and typography**: arcs, slant and rotation; letter spacing; styled runs (another font, colour or
  size for some words, with the text still exact); text along any path or circle.
- **SVG and PDF export**: vector files with text as HarfBuzz-shaped outlines; pixel-only layers embedded and named.
- **`poster_compare`**: scores a render against a reference (structure, colour, layout), names the worst regions,
  draws a heat map.
- **`poster_sizes`**: one design for story, landscape, banner and custom sizes; layers can be pinned to an edge.
- **`image_trace`**: a logo or flat picture traced into a vector SVG of flat colours, with a fidelity score.
- **`poster_animate`**: a `motion` block (entrances, keyframed tracks, looping shaders) renders the poster as an MP4;
  the last frame is the poster and every frame is audited.
- Right-to-left text now works with Pillow builds that lack libraqm (Windows): lines are reordered for display and
  posters use the HarfBuzz outline engine.

## Video and reels
- **`video_info`, `video_scenes`, `video_trim`, `video_subtitles`**: look inside a video, find its cuts (with a sheet
  and reel-ready trims), cut pieces without touching the original, and burn in subtitles drawn by the caption engine
  with a text audit.
- **`reel_timeline`** / `funkbox reel timeline`: a local drag-and-drop page over a `*.reel.json` that you and Claude
  share: reorder, resize in beats, set in/out points, captions and voice lines, render a draft. Saves are validated,
  backed up and logged in plain words; Claude's edits to the file reload the page.
- **Voices**: Kokoro-82M (Apache-2.0) is the default voice, with three approved ad voices (`af_heart`, `am_michael`,
  `bm_george`), `voice_speed`, and a real pause between sentences; Piper remains the fallback.

## Music
- **`music_render`**: short scores (pitches, scale degrees including freygish, chords, drum patterns, swing) played
  by sampled General MIDI instruments from GeneralUser GS through a numpy SoundFont player; a reel can name a
  `.music.json` as its music and gets the score's beat grid.

## Promo
- A queue for finished promos, a save-for-later fallback, Buffer for X with native video, variety and claim lint,
  measured facts, and `owner_approval.json` for content only the owner can sign off.
