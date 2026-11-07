# Spec: music variety

Status: MV-1 and MV-2 done (`funkbox music vary` writes a set and a manifest; merged c493c43). MV-3 done (merged): `audio.music` takes the score list, and each rendered variant is measured against the target. The music cards are complete. Effort: small to medium. Source: product feedback. Every demo reel uses the same music at a different tempo, so the demos sound alike.

## Problem

Demos differ only in tempo. A tempo change alone does not make a track sound different: the key, instruments, groove and arrangement stay the same. Listeners hear one song.

## Goal

Each reel variant, and each demo, gets its own score: a different key or scale, instrument palette, drum pattern, swing and arrangement, drawn from a brand's music brief. Scores stay reproducible, and the beat grid and text audit keep working.

## Approach

Generate a `.music.json` per variant from a seed and a music brief. Render it with the existing `music_render`. The score schema does not change, and no new synthesis is added.

Music brief (in the project, or inline in the vary file):

```json
{
  "bpm_range": [96, 124],
  "keys": ["D", "A", "E", "G"],
  "scales": ["freygish", "minor-pentatonic", "dorian", "mixolydian"],
  "programs": [0, 24, 32, 40, 56, 71],
  "drum_patterns": ["straight", "half-time", "shuffle"],
  "swing": [0.0, 0.2],
  "arrangement": "build"
}
```

For each seed, the generator picks one value per field, then writes a score with the same number of bars and the same bar length. The score's `bpm` sits inside `bpm_range`. Reel shot durations in `beats:N` still land on the beat, because the grid follows the score's own `bpm`.

## Where it fits

- `funkbox music vary SCORE.music.json --brief brief.json --seed 7 --count 6` writes one score per variant, with a manifest.
- The reel-variants vary key `audio.music` takes the score list this produces.
- Renders are cached by score content, as today (`FUNK_OUT/music/`).

## Guardrails

- Only the existing score schema and the bundled GeneralUser GS bank are used. No third-party music is added to the repo.
- The bpm band is a hard limit. A variant that falls outside it is an error, not a clamp.
- Loudness of the mix stays at the existing target (about -14 LUFS for reels). A variant that misses it is reported.
- The seed is recorded in the manifest, so any variant can be regenerated.

## Acceptance tests

- Same brief and seed give the same score bytes and the same hash.
- Every generated bpm is inside `bpm_range`.
- Across six variants, at least three distinct keys or scales, and at least three distinct instrument-program sets appear.
- Each variant's score hash is unique.
- A `beats:4` shot keeps 4 beats in every variant (the grid follows each score).
- A diversity check: the chroma profile of each rendered variant differs from every other by a set threshold (set it from the first measured run, then fix it).

## Cards

1. Brief schema and validation, and the generator with a seed. Tests: determinism, bpm band, unique hashes.
2. `funkbox music vary` CLI and manifest. Tests: diversity counts.
3. Hook into reel variants (`audio.music`) and render the loudness check per variant.

## Risks

- Random combinations can clash (a bright instrument in a dark key). Keep the scales and programmes in the brief curated, and review the first batch by ear before turning the defaults on.
- Diversity can be faked by changing only one field. The diversity test counts fields that change, not just the bpm.