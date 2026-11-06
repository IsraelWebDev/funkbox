# Voiceover demo

A 14-second ad with its copy spoken by a local voice (Kokoro `af_heart`), a quiet music bed under the voice, and an end card. It is the reference for the text-to-voice path: one approved copy deck drives the spoken line, the caption and the text audit.

## Files

- `copy.json`: the approved copy deck.
- `demo.reel.json`: the reel. Voiceover on, `kokoro:af_heart` at speed 1.0, music bed at -22 dB with a 10 dB duck under the voice, end card.
- `bed.music.json`: the music score. General MIDI tracks on the built-in soundfont, so nothing is downloaded. 92 bpm, 8 bars.
- `logo.png`: end-card logo.
- `output/`: `final-16x9.mp4`, `final-9x16.mp4`, poster frames, and `final-*.text_audit.json`.

## Reproduce

1. `pip install -r requirements-reel.txt`
2. `python -m funkbox reel setup-models` (about 520 MB, hashes checked)
3. `python -m funkbox reel render examples/voiceover_demo/demo.reel.json --audit --name final --out out/voiceover_demo`

Add `--draft` while iterating (540 px, 24 fps).

The phonemizer behind `kokoro-onnx` needs espeak-ng (GPL-3.0). It is not bundled here, so install it with your system package manager before step 3.

## Text audit

`final-16x9.text_audit.json` has four `voiceover` entries, one per spoken line. Each matches the copy deck, with `missing` 0 and `inexact` 0 (`never_fully_typed` 0):

- "Your words. Your voice."
- "EVERY SPOKEN LINE COMES FROM THIS DECK."
- "THE CAPTIONS MATCH. THE AUDIT PROVES IT."
- "THE VOICE RUNS ON YOUR COMPUTER."

The 9:16 audit matches.

## Mix

- 16:9 mix: integrated loudness -13.9 LUFS (target -14), true peak -2.7 dBFS, loudness range 5.9 LU.
- The music bed sits at -22 dB and is ducked a further 10 dB while the voice plays.

Frame rendering took 15 s (16:9) and 28 s (9:16) on the laptop CPU, with the voice running on the CPU too.

## Not done yet

- Reproduction from a clean clone is not run yet. The steps above are written from the render on the author's laptop.
- "No spoken line runs past its shot" is covered by the timeline's VO_TAIL sizing (each shot is at least the spoken line plus 0.35 s). There is no dedicated test for it yet.
- The second-voice variant waits for the reel-variants voice key.