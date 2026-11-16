# Reel beat audit: every cut's distance from the beat, saved

Status: built on `card/reel-beat-audit`, PR pending.

## Problem

Beat-sync tools promise "song in, cuts on the beat out" and show no numbers. funkbox already measures how close each cut lands: the storyboard prints the distance from the nearest beat. Nothing saves those numbers, so a post cannot quote them and a reviewer cannot check them. Source: product feedback of 2026-11-16, claude/beat-audit-feedback-2026-11-16.md.

## Design

- New module `funkbox/reel/beataudit.py`. `nearest_beats(spans, beats, fps)` is the one computation; the storyboard report and the audit both use it, so the printed and the saved numbers cannot differ.
- When a reel sets `audio.snap_cuts_to_beats` and has a beat grid, a render with the audit on writes `<name>.beat_audit.json` next to `<name>.text_audit.json`:
  - `bpm`, `grid` (`fixed` = `audio.bpm`, `score` = a `*.music.json`, `detected` = found in the music file), `fps`, `frame_ms`, `threshold_ms` (30);
  - `cuts`: for every span start after the first (the end card's too): `cut` (the shot that starts), `frame`, `time_s`, `beat_s`, `offset_ms` (signed, positive = after the beat), `ok`;
  - `max_offset_ms`, `over` (cuts past the threshold), `ok` (no cut past it).
- The cut times are the rendered ones: a 24 fps draft is audited at 24 fps, from the renderer's rescaled spans.
- A reel that does not snap, or has no grid, gets no file, and a beat audit left by an earlier render of the same name is deleted so it cannot back a claim.
- `RenderOutput.beat_ok` carries the verdict. The CLI and `reel_render` print one line. A failed beat audit does not change the exit code: a cut that cannot snap (a voiceover line needs the time, or no beat within 0.25 s) is a pacing fact, not a broken render. The text audit still decides the exit code.
- `promo/measure.py` exposes it as `M-beats`: `"every cut lands within N ms of the beat (K cuts at B BPM)"`, with N the largest distance rounded up to a whole ms. With a detected grid the sentence says "the detected beat".

## What the number means

The offset is the distance from the cut to the beat grid the reel was cut on. For `audio.bpm` and for a funkbox score the grid is exact. For a detected grid it is funkbox's estimate of the beats in the file; the audit does not measure how good that estimate is (the detector's own tests do), which is why M-beats words it as "the detected beat".

## Not in this card

- No setting for the threshold (30 ms, `beataudit.MAX_OFFSET_MS`).
- No beat audit for reels that do not snap their cuts.
- Variants and contact sheets do not collect beat audits into their manifests.

## Tests

- `tests/reel/test_beat_audit.py`: signed offsets within half a frame after snapping; a far cut fails and is named; draft frame rate; grid source; file written only when snapping, stale file removed; report line; two render tests (GL).
- `tests/test_promo.py`: `M-beats` sentence and value for a passing, a failing and an empty audit.
- The storyboard tests in `tests/reel/test_r7_cli_mcp.py` cover the shared computation.
