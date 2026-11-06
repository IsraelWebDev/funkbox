# Spec: text-to-voice demo

Status: built on card/tv-1-voice-audit (TV-1 to TV-3), not yet merged. Effort: small. Source: product feedback. No demo uses voiceover yet, even though the engine can speak copy locally.

## Why this matters

Voiceover is the clearest test of the engine's text promise. The spoken line, the caption and the audit should all come from one approved copy deck. A demo with voice shows that end to end.

## What exists today

- Kokoro-82M is the default local voice (Apache-2.0). Model page: [huggingface.co/hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M). Approved ad voices: `kokoro:af_heart`, `kokoro:am_michael`, `kokoro:bm_george`, with `voice_speed`.
- Piper (`en_US-ljspeech-medium`) is the fallback. It is GPL-3.0 and optional.
- Setup: `pip install -r requirements-reel.txt`, then `python -m funkbox reel setup-models` (about 520 MB, hashes checked). Without a voice, `voiceover: true` is an error, by design.
- Licences are recorded in THIRD_PARTY_NOTICES.md. The demo ships generated output and its source, not the models.

## Goal

One demo under `examples/voiceover_demo/`, plus a voice variant that uses the same copy with a second approved voice.

Files:
- `copy.json`: approved, hash-locked copy.
- `demo.reel.json`: `audio.voiceover: true`, `voice: "kokoro:af_heart"`, `voice_speed: 1.0`, music bed ducked under the voice, an end card.
- `README.md`: how to reproduce, with the text audit excerpt.
- Output: 16:9 and 9:16 MP4 and the `text_audit.json`.

The voice variant is `voice: ["kokoro:af_heart", "kokoro:am_michael"]` in a reel-variants run, once that spec lands. TV-2 ships the single-voice demo only; the variant is not built.

## Acceptance tests

- Renders with a real Kokoro voice on the laptop. The stub is not allowed for the demo.
- The text audit covers the voiceover lines. Check first whether it does. If not, add a `slot: "voiceover"` entry for each spoken line, with the text that was synthesised, and compare it to what the copy deck declares: the line's text with newlines as spaces, or its `say` field when it has one, with `pronounce` applied. The demo's hook line uses a `say` field, so its spoken text is checked against that field, not against the caption text.
- `validate` reports no spoken line that runs past its shot.
- Mix loudness is within the existing target.
- The README reproduces the output from a clean clone with the reel extras installed.

## Cards

1. Verify the audit covers voiceover lines. Add the entries and tests if missing.
2. Build the demo files and render both formats.
3. README and a short section in docs/reel.md linking to it.

## Risks

- Kokoro is about twice real time on a laptop CPU, so a 25 s reel takes a few minutes. Use `--draft` while iterating.
- espeak-ng (GPL-3.0) is a dependency of the phonemizer used by kokoro-onnx. It is not bundled, and the README says so.