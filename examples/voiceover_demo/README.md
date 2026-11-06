# Voiceover demo

A 13.7-second ad with its copy spoken by a local voice (Kokoro `af_heart`), a quiet music bed under the voice, and an end card. It is the reference for the text-to-voice path: one approved copy deck drives the spoken line, the caption and the text audit.

## Files

- `copy.json`: the approved copy deck. `copy.sha256` is its locked hash, and `tests/test_voiceover_demo.py` checks it. If you change the copy, update the lock on purpose.
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

`final-16x9.text_audit.json` has four `voiceover` entries, one per spoken line. Each matches what the copy deck declares: three match the line's text, and the hook matches its `say` field (`Your words. Your voice.`), which the deck uses so the voice reads it in normal case. `missing` 0, `inexact` 0 and `never_fully_typed` 0:

- "Your words. Your voice."
- "EVERY SPOKEN LINE COMES FROM THIS DECK."
- "THE CAPTIONS MATCH. THE AUDIT PROVES IT."
- "THE VOICE RUNS ON YOUR COMPUTER."

The 9:16 audit matches.

## Mix

Measured on the encoded MP4s with ffmpeg `ebur128`:

- 16:9: integrated loudness -13.9 LUFS (target -14), loudness range 5.2 LU, true peak -2.8 dBFS.
- 9:16: the same audio, so the same figures.
- The music bed sits at -22 dB and is ducked a further 10 dB while the voice plays.

## Render times and voice

On the author's laptop, one run: frame rendering took about 16 s (16:9) and 28 s (9:16). The renders used the Kokoro voice: `tts_mode()` resolves to `kokoro` with the models installed. The render log does not print the voice mode, so that is the evidence.

## Not done yet

- Reproduced from a fresh clone of `card/tv-1-voice-audit` at 9d83388, with the steps above: both MP4s came out byte-identical to the committed ones, and `reel validate` reports OK. This was run on the author's laptop only.
- A shot too short for its spoken line fails validation. That rule is tested in `tests/reel/test_r1_copy_timeline.py`, not in this demo's own tests.
- The second-voice variant waits for the reel-variants voice key.