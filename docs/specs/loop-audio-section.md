# Audio-loop mode: a loop that reacts to music and still loops

Status: built on `card/loop-audio-section`, PR pending.

## Problem

Musicians want the loop behind a song to react to the music and still loop with no visible jump. A loop with `settings.audio` plays the whole track once, so its last frame cannot lead into its first, and funkbox skips the loop check and records `{"status": "skipped", "reason": "audio"}`. Source: product feedback of 2026-11-15, claude/audio-loop-feedback-2026-11-15.md.

## Design

Two parts, kept apart so each can be used alone.

### 1. Cutting the section (`funkbox/loops/audioloop.py`)

- Beats come from the reel detector (`reel.audio.beat_grid`, on a 22050 Hz mono decode). It gives a tempo and a beat grid, not bars: no meter, no downbeat. So `meter` is an input (default 4) and any beat may start the section.
- Length: a whole number of bars that is 3 to 8 s long, unless the caller gives `bars`. Rounded to whole video frames at the recipe's fps, because the mp4 and its audio must be the same length to loop in a player. `drift_ms` records how far that moved it off the true bar length (at most half a frame).
- Start: the beat nearest `start` if given. Otherwise automatic: for every beat and bar count, compare one beat of audio after the section's end with the beat after its start, and the beat before its end with the beat before its start (24 log-spaced band energies per 23 ms frame). In a section that repeats, both pairs sound alike. The frame rounding, as a share of a beat, is added to the score. Sections quieter than half the track's RMS are skipped (silence matches silence). Lowest score wins; ties go to the earlier start, then the longer section.
- Crossfade: 50 ms, linear. If the track continues after the section, the section's first 50 ms fade in from what follows it, so the wrap plays on as the track did. If the section ends with the track, its last 50 ms fade into what led up to it.
- Output: 16-bit PCM WAV, the source's sample rate and channels, `<out>/<name>.loop.wav`.
- `apply(recipe, out_dir, name)` cuts from the recipe's `settings.audio` and sets `audio`, `seconds` and `audio_loop` on it.

### 2. Rendering a looped clip (`settings.audio_loop`)

- New recipe setting `audio_loop` (bool, default false, written to the sidecar only when true). It says the audio is one loop long. Loading a recipe refuses it when the WAV's length differs from `seconds` by more than 50 ms, or when there is no audio.
- `gl.audio.analyze(..., loop=True)`: frame i sits at i / n of the clip and its FFT window wraps round the clip's ends. Frame n therefore reads the same samples as frame 0. The crossfade alone would not do this: the analysis has to wrap too.
- The loop check runs. Its first test (frame at t=1 equals frame at t=0) is unchanged. Its second test is different for audio loops: music moves the picture in bursts, and a section that starts on a kick has a burst at its seam, so comparing the seam with the mean step would fail good loops. Instead every step is measured, and the largest step around the wrap (between frames whose audio window reaches across it) must not exceed 1.5 times the largest step in the rest of the loop (plus the usual slack).
- CLI: `funkbox loop render --audio TRACK.wav --audio-loop [--audio-start S] [--audio-bars N] [--audio-meter B]`. `--seconds` with `--audio-loop` is an error. MCP: `loop_render(audio_loop=true, audio_start, audio_bars, audio_meter)`.
- Sidecar: `settings.audio` is the cut file, `settings.audio_loop` is true, `loop_check` has real numbers, and `audio_section` records the cut. Re-rendering the sidecar does not cut again.
- A whole track without `audio_loop` behaves as before (unchecked); the warning now names the option.

## Not in this card

- No downbeat or meter detection.
- No time-stretching: the section is rounded to whole frames, not stretched to them.
- The cut needs a PCM WAV (as `settings.audio` always has) and ffmpeg for the analysis decode.
- Reels (funk2 shots inside a reel) do not use this yet.
- A tempo outside 70-180 BPM is read as its half or double, as in reels.

## Tests

`tests/test_audio_loop.py`: periodic analysis; crossfade (both directions, and the errors); bar counts; a cut from a synthetic 120 BPM track that repeats every 2 bars (2 or 4 bars chosen, on the beat, whole frames, channels kept, no click at the wrap); given start and bars; quiet intro skipped; error messages; recipe settings; `apply`; a reactive loop on a cut section passes the loop check pixel-exact and does react; a clip that does not wrap fails it; the CLI end to end (and the sidecar renders again); `loop_render` over MCP.
