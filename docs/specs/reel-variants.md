# Spec: reel variants

Status: proposed. Effort: medium. Source: creator feedback. Creator guides recommend shipping many versions of one ad, each with a different first scene ([influee](https://influee.co/gb/blog/doubling_roas_with_ad_variations), [videotok](https://www.videotok.app/blog/performance/ai-creative-testing-workflow-social-ads), [segwise](https://segwise.ai/blog/ai-ugc-perofrmance-marketing-2026)). These links come from the feedback; their content has not been checked.

## Problem

Each extra version of an ad means copying the shot list and copy deck by hand. Nothing checks that the copy in every version is still the approved copy.

## Goal

`funkbox reel variants` renders N alternatives of one reel. The alternatives differ in a named shot or a named copy line, and each has its own text audit. The same command makes a contact sheet of every version's opening. The matching MCP tool is `reel_variants`.

## Non-goals

- Writing new copy. Variants pick only from lines already in the approved deck.
- Writing `copy.json`. Only `project_approve_copy` does that, as today.
- Changing the music. That belongs to [music-variety](music-variety.md), which variants may call.
- Automatic publishing or uploading to ad platforms.

## Rules

1. **Copy lock.** A variant run never writes or edits `copy.json`. If the deck has changed since approval, the run is refused, as `validate` and `render` already do.
2. **Validate first.** Every variant is validated before any is rendered. One invalid variant stops the whole run and names the variant.
3. **Text audit per variant.** Each variant writes its own `<name>-vNN.text_audit.json`. Every drawn string must equal its copy entry byte for byte.
4. **Bounded.** At most 12 variants per run, the same limit as poster variations.
5. **Deterministic.** The same reel, vary file and seed give the same variant reel JSON, with the same hashes. The manifest records those hashes.
6. **Contact sheet labels are not copy.** Grid labels must be copy ids, so a sheet cannot label variants with copy ids without changing the deck. See the design choice below.

## Interface

`vary` reuses the poster variations format: a map from a key to a list of values. Keys are zipped and must have equal length.

```
funkbox reel variants demo.reel.json --vary variants.json --count 6 --sheet --draft --audit
```

```json
{
  "shots.s1.source": ["clips/open-a.mp4", "clips/open-b.mp4", "stills/open-c.png"],
  "shots.s1.motion": ["kenburns", "none", "frame"],
  "brand.palette": ["#f6f7f4,#0f7c6e", "#0f1216,#4fd1b9", "#ffffff,#ff6b3d"],
  "audio.music": ["music/a.music.json", "music/b.music.json", "music/c.music.json"]
}
```

Allowed keys: `shots.<id>.<field>` for source and motion (RV-2); from/to, speed and grid order are deferred and rejected until a later card; `brand.palette`; `audio.music`; `captions.<shot>.copy_id` (approved lines only). Any key that reaches `copy.json`, a literal `text` field, or a file outside the project is an error.

## Outputs

Under `<name>-variants/`:

- `vNN-16x9.mp4` and `vNN-9x16.mp4`, or PNG openings with `--still`.
- `vNN.reel.json`, the resolved reel for that variant.
- `vNN-<format>.text_audit.json`, one audit per rendered file (a reel with no declared formats gives `vNN.text_audit.json`). Every audit is checked against the same copy deck.
- `contact_sheet.png` (and `contact_sheet.mp4` if asked): one cell per variant showing its opening.
- `variants.json`: the manifest with each variant's vary values, reel hash, audit path and render time.

## Design choice: contact sheet labels

The sheet is built with the grid shot (`source.grid`, shipped in 4.4). Its labels must be copy ids, and validation checks them against the deck. Variant numbers are not copy, so the sheet cannot use them as labels without changing the deck.

Recommended: a numeric label mode, `label: {"index": true}`. The grid draws the cell's 1-based number, and the text audit records each one as `slot: "variant_index"` with the exact digits drawn. The copy lock is untouched, and the audit still proves what was drawn.

Alternative (not recommended): label each cell with the variant's first caption copy id. It needs no new label mode, but the sheet then shows copy that varies, which hides differences in the opening.

## Acceptance tests

- A vary key that touches `copy.json` or a literal `text` is rejected.
- Unequal zipped lists are rejected. More than 12 variants is rejected.
- A changed `copy.json` since approval refuses the run.
- Every variant's audit equals the copy deck, byte for byte.
- Two runs with the same inputs give identical manifest hashes.
- The contact sheet audit has one `variant_index` entry per variant, with the matching digits.
- One invalid variant stops the run before any render starts.

## Cards

1. Expand and validate vary keys into variant reels (no rendering). Tests: rejections above.
2. Render loop: per-variant render, audit, sidecar, manifest. Tests: audit equality, determinism.
3. Contact sheet with `label: {"index": true}`. Tests: audit entries for each index.
4. CLI and `reel_variants` MCP tool, plus docs/reel.md and docs/skill/SKILL.md updates.

## Risks

- Render time grows with N. Default to `--draft`, and print the estimate before rendering.
- Openings that differ only in a short first shot can look alike at contact-sheet size. Use a larger cell size for the sheet.