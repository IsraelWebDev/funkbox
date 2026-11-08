# funkbox 4.4.1

A fix to the beat grid used for reel timing, plus the storyboard lines that show it. The full test suite has 783 passing
tests and 5 skipped.

## Reels
- **Beat phase from the kick band**: the grid's phase now comes from a low-band onset envelope (bins up to 150 Hz), so
  cuts land on the kick rather than the off-beat hi-hats. A synthetic 128 BPM track with off-beat hi-hats was half a
  beat late before this; it now starts at 0 s. Tempo still comes from the full band. If the low band has no clear pulse,
  the phase falls back to the full band, and the result records which path it took on `BeatInfo.phase_source`.
- **Sample-rate scaling**: the phase lead scales with the window and sample rate, so 44.1 kHz input gives the same
  grid as 22.05 kHz to within a few milliseconds.
- **Beat-grid lines in the storyboard**: `funkbox reel storyboard` and `funkbox project auto` print the bpm, the beat
  count, the first 8 beats, and each cut's distance in ms to the nearest beat, or `beat grid: none (<reason>)`.
- **Missing music file**: validation still reports a readable error. The storyboard keeps the sheet and prints the
  beat-grid note, instead of failing after the sheet is written.
- **Shorter reasons**: a failed beat-grid analysis keeps the ffmpeg error text and drops the file path.

## Compatibility
- `audio.bpm` and `audio.beat_offset` still override detection. `beat_grid`, `detect_beats` and `fixed_grid` keep their
  signatures. A reel that uses detected music can start its grid at 0 s instead of a later beat, so `beats:N` timing
  shifts by that amount.