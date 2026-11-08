# Spec: short style match

Status: card 1 measured (see Card 1 results). Card 2 built: type presets and fonts (explainer, tech; both fonts bundled with OFL licences). The explainer keeps music at -14 dB, since the -18 change waits for the listening check. Cards 5 and 6 (callout tags, annotations) built; see Callout tags and Annotations in docs/reel.md. Cards 1-4 are merged per the Funkbox project doc. Card 7 (end banner) built as the bottom-banner template; see End banner in docs/reel.md. Effort: medium overall; cards 1-3 are small. Source: a reference short the author shared (YouTube adP2iO2E15k, 98 s, 9:16). It was analysed from frames, cut times, motion, colour and audio levels. Nothing from the video is copied or embedded here.

## Problem

Reels are built from one set of defaults: a red box caption, music at -14 dB and no pacing rules. The reference uses a different kit: callouts, word-level captions, diagram annotations, a quiet bed under speech, and shots of 2-8 s. Some of this is only a default change. Some needs new features.

## Goal

Make the common talking-head short reachable with reel fields and presets. Add the missing elements as separate, opt-in features. Existing reels render the same unless they opt in.

## Non-goals

- The highlight region (flat red fill over muscle areas). Dropped at the author's request.
- Copying the reference's music, footage, graphics or copy.
- Relaxing the text audit: every drawn string still equals its copy entry byte for byte.
- Changing the default loudness target without a measured reason.

## Rules

1. Opt-in. Every new field has a default that leaves today's reels unchanged. A hash test proves it.
2. Presets only set existing fields. A type preset is a JSON file in templates/types/. Explicit reel fields always win.
3. Copy stays in the deck. Callouts and word highlights show deck text only. Styles still may not change case.
4. Glyph check before render. A font that lacks a glyph falls back per text block to brand.font_fallback. Every preset keeps the fallback, so Hebrew and Cyrillic copy still draw.
5. Bundled fonts work offline. A font a preset ships lives in fonts/ with its OFL licence. Name lookup is a convenience only.

## Default values review

Current defaults (funkbox/reel/schema.py, autoreel.py): music_gain_db -14, duck_db -10, snap_cuts_to_beats false, loudness_lufs -14, caption style red-box (size 0.052, box primary, align left), cps 28, shot duration auto, caption reveal none, shot motion none.

| Setting | Now | Reference | Verdict |
|---|---|---|---|
| loudness_lufs | -14 | about -26 LUFS integrated | Keep -14 as the delivery target. Platforms turn loud content down, but do not lift a quiet master, so it would sound quiet on phones. Keep the target in presets as an explicit field. |
| music_gain_db | -14 | music sits well under speech | Measured in card 1: the bed sits 15 LU under speech at -14 with the duck. The numbers do not show -14 to be too loud, so keep -14. The -18 proposal for voiced types needs a listening check before any change. |
| duck_db | -10 | speech dominates, no pumping | Keep. Re-check after the music change, since a lower bed may make the duck redundant. |
| snap_cuts_to_beats | false | cuts follow speech, not the beat | Keep false by default. Turn on only in music-led presets. |
| shot duration | auto | 2-3 s early, 4-8 s later, cut about every 4-5 s | Keep auto. A pacing preset sets a target of 2.5-4 s and cuts at sentence ends. |
| caption style | red-box | bold condensed caps, white on dark, centred | Keep red-box as the default. bold-pop already gives the centred, stroked look, so no new style is needed for the basic look. |
| cps | 28 | typewriter pace | Keep. |
| shot motion | none | static or handheld, small push-ins on the hook only | Keep none. The hook shot may use the existing small push. |

Measure before changing any default: render the demo at -14 and at -18, measure the voice-to-music ratio with ebur128, and record the numbers in card 1. The -18 figure is a hypothesis until then.

## Card 1 results (SS-1)

Measured on the voiceover demo (examples/voiceover_demo, 13.7 s, Kokoro af_heart, duck -10 dB) with `examples/voiceover_demo/measure_ratio.py`. Each stem is rendered with the reel's own mix graph, with loudness normalisation off, and measured with ffmpeg ebur128 (integrated, gated).

| music_gain_db | voice LUFS | music only LUFS | music minus voice (no duck) | ducked mix LUFS | mix minus voice |
|---|---|---|---|---|---|
| -14 | -21.8 | -26.9 | -5.1 LU | -21.9 | -0.1 LU |
| -18 | -21.8 | -30.9 | -9.1 LU | -21.8 | 0.0 LU |
| -22 (demo setting) | -21.8 | -34.9 | -13.1 LU | -21.8 | 0.0 LU |

Derived, not measured separately: during speech the bed is also ducked by 10 dB, so it sits 15.1 LU under the voice at -14, 19.1 LU at -18 and 23.1 LU at -22.

Reading: the bed is well under the speech at every setting, and adding it changes the integrated loudness by under 0.1 LU. The numbers do not show -14 to be too loud under speech, so there is no measured reason to move the default. The -18 proposal stays open for voiced types until a listening check on a rendered short. Music-led types are not affected by this card.

Limits: integrated loudness gives one number per stem. It does not show how loud the bed is in the gaps between lines, where the bed is the only sound. A listening check is still needed before any default changes.

## Feature 1: Video type presets and fonts

A type preset bundles defaults for one kind of video: templates/types/<name>.json. Keys: caption_style, font_caption, font_display, font_fallback, music_gain_db, duck_db, snap_cuts_to_beats, shot_target_s, cps, end_card. Select with `"type": "explainer"` in the reel, a project default, or `--type` on the CLI. Explicit reel fields override the preset.

The font slots already exist. brand.font_caption, brand.font_display and brand.font_fallback take a file path or a name. A name is fetched once from the OFL Google Fonts folders and cached. Only three families ship today: Oswald SemiBold, DM Serif Display Regular and Rubik Bold. So the new part is the mapping from video type to a font pairing, not the font support.

Starter types (proposed; each name must pass the lookup and licence check before it ships):

- explainer (the reference style): caption Oswald, display DM Serif Display, fallback Rubik, all bundled. Style red-box or bold-pop with caps copy. Music -18, duck -10, auto cuts.
- beauty / wellness: display DM Serif Display, caption Rubik at weight 500. Calm pacing, shot target 4-6 s, music -16.
- tech / app: caption Inter, display Space Grotesk (both to be fetched and shipped). Music-led, snap on, shot target 1.5-3 s, music -14.
- kids / fun: caption and display Fredoka (to be fetched and shipped). Music-led, snap on.

Offline installs: a type's fonts must be in fonts/ or already in FUNK_HOME/fonts-cache. Name lookup needs the network the first time.

Hebrew: several display families have no Hebrew glyphs. The per-block fallback covers them, and the type must keep brand.font_fallback. Each type gets a Hebrew test line.

Acceptance: a type sets the font slots and music gain; an explicit reel field overrides it; a font that lacks glyphs falls back and validate reports it.

## Feature 2: Hook card

The first 0.6-1.0 s of the reel is a larger two-line title box over shot 1, in the display font. Set with `"hook": true` on a type or a reel. Uses the existing caption machinery with a larger size and a box.

Acceptance: the hook fits the safe area in every format; the hook text is in the audit.

## Feature 3: Word highlight

A pill sits behind the spoken word. The layout is fixed and the highlight moves to word k when the typewriter prefix reaches it, so the mechanics already exist. Style key: "highlight": {"color": "primary", "radius": 0.3}.

Word times come from the cps estimate. Where the voice path provides timings, use those instead (check audio.py first). The reveal "words" already exists and the highlight is drawn on top of it.

Acceptance: each highlight box lies inside its line's layout box; no highlight on spaces; the audit records text only, not highlight state.

## Feature 4: Callout tags

A label (white box, optional red sub-label) anchored to a point, with an optional leader line to a second point. Shape: shots[].callouts = [{copy_id, at: [x, y], line_to?: [x, y], start, end, style}]. Positions are source fractions. Copy id only; the id must exist in the deck.

Acceptance: the callout text is in the audit; anchors outside the frame are rejected; the label stays inside the safe area.

## Feature 5: Annotation layer

Arrows, dashed guide lines, X and check stickers, with a draw-on animation. Shape: shots[].annotations = [{type: arrow | dashed | x | check, points: [...], start, end, draw: true}]. Drawn with PIL in frame pixels, so no new dependency. Shapes carry no text, so they are listed in the sidecar, not the audit.

Acceptance: the draw progress is monotone; the same inputs give the same frames.

## Feature 6: End banner

A typewriter banner at the bottom for the last 3-6 s. First check whether an existing end-card template (brand-split, centered-logo, logo-clean) already covers it. If not, add a template rather than a new feature.

## Cards

1. Defaults and measurement. Render the demo at -14 and -18 music, measure the ratio, record the numbers. No default changes in this card. Small.
2. Type presets and fonts (Feature 1), starting with explainer and tech. Tests: override precedence, fallback reporting, bundled fonts present with licences. Small to medium.
3. Pacing preset and hook card (Feature 2). Small.
4. Word highlight (Feature 3). Medium.
5. Callout tags (Feature 4). Medium.
6. Annotation layer (Feature 5). Medium to large.
7. End banner (Feature 6), only if the end-card templates don't cover it.

Cards 1-3 change presets and defaults only, so they ship first and carry most of the look.

## Acceptance tests (overall)

- A reel with none of the new fields renders byte-identical to today (hash check).
- Every new drawn string appears in the text audit.
- A type preset never changes copy or the copy lock.
- Every font a type uses is bundled or cached, with its licence beside it.

## Risks

- Name-lookup fonts need the network once. Offline desktop installs must ship the fonts.
- Estimated word times drift on slow voices. Prefer TTS timings where they exist.
- Arrows and stickers add visual noise. Keep them to talking-head types.
- A quiet bed under speech suits talking heads, not music-led content. This is why the setting is per type.