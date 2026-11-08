# Music

`music_render(score, name)` (CLI: `funkbox music render song.music.json`) turns a short score into a 48 kHz stereo WAV
played by real sampled instruments from a General MIDI SoundFont. The default bank is
[GeneralUser GS](https://www.schristiancollins.com/generaluser) (free for any use, private or commercial), downloaded
once into `FUNK_HOME/soundfonts/` with its licence; `FUNK_SOUNDFONT_URL` points at a mirror. `"soundfont": "builtin"`
uses a small synthesised bank that needs nothing, and any `.sf2` file works too. If the download is impossible the
built-in bank is used and the result says so. `funkbox music instruments` lists the bank's programs.

```json
{"bpm": 104, "meter": 4, "key": "D", "scale": "freygish", "bars": 8, "swing": 0.1, "reverb": 0.25,
 "tracks": [
   {"name": "clarinet", "program": 71, "octave": 4, "notes": "^1/8 ^2 ^3 ^4 | ^5/4. ^4/8 ^3/4 ^2 | ^1/1"},
   {"name": "piano", "program": 0, "velocity": 70, "notes": "[D3 F#3 A3]/2 [C3 Eb3 G3]/2"},
   {"name": "bass", "program": 32, "octave": 2, "notes": "^1/4 ^5, ^1 ^5,"},
   {"name": "drums", "drums": true, "pattern": {"kick": "x...x...", "snare": "....x...", "hat": "x.x.x.x."}}]}
```

- **Notes:** pitches (`D4`, `F#3`, `Bb2`), scale degrees of the key in the track's `octave` (`^1`..`^7`, `^8` the octave;
  `'` up and `,` down an octave), chords `[D3 F#3 A3]` (degrees work inside), `r` rests, `~` holds the previous note.
  `/4` quarter, `/8` eighth, `/1` whole, `.` dots; a length carries over until changed. `!` accents, `?` softens.
  `|` marks bars for the reader.
- **Scales:** major, minor, harmonic-minor, dorian, mixolydian, phrygian, freygish (= ahava-raba), misheberach,
  pentatonic, minor-pentatonic, blues.
- **Drums:** 16th-note steps, `x` hit, `X` accent, `o` soft, `.` rest; kick, snare, rim, clap, hat, openhat, pedalhat,
  tom-lo, tom-mid, tom-hi, crash, ride, tamb, cowbell, shaker.
- **Tracks:** `program` (General MIDI: 0 piano, 21 accordion, 24 nylon guitar, 32 acoustic bass, 40 violin,
  48 strings, 56 trumpet, 68 oboe, 71 clarinet, 73 flute ...), `velocity`, `pan` (-1..1), `gain_db`. With `bars`,
  every track repeats to fill them.

A reel can name the score directly: `"audio": {"music": "song.music.json"}`. It is rendered once per score content
(cached in `FUNK_OUT/music/`) and the reel's beat grid follows the score's `bpm`, so cuts land on the beat.

The player (`funkbox/music/sf2.py`) is numpy only: preset and instrument zones, loops, tuning, the volume envelope,
attenuation and pan. It leaves out SoundFont modulators, filters and LFOs, so some instruments sound plainer than in
a full synth; the mix gets one room reverb and a gentle limiter at about -16 dBFS RMS.

## Variety: several scores from one brief

`funkbox music vary SCORE.music.json --brief brief.json --seed 7 --count 6` writes a set next to the base score (or into the folder given by `--out`): one file per variant and a `manifest.json`. For `song.music.json` the variants are `song-01.music.json`, `song-02.music.json`, and so on. The base score's bars and meter are kept. Each variant draws its own tempo, key, scale, programs, drum pattern, swing and chord progression, so the set does not sound like one song at different speeds.

The brief is JSON, and the tool reports every problem in it at once:

- `bpm_range`: `[low, high]`, the tempo band.
- `keys` and `scales`: lists to choose from, one per variant. Keys are note names such as `D` or `Bb`. Scales are `major`, `minor`, `harmonic-minor`, `dorian`, `mixolydian`, `phrygian`, `freygish`, `misheberach`, `pentatonic`, `minor-pentatonic` and `blues`.
- `programs`: General MIDI programs for the pad and bass.
- `drum_patterns`: any of `straight`, `half-time`, `shuffle`.
- `swing`: a list of values from 0 to 0.5; one is picked per variant.
- `arrangement` (optional): the only accepted value is `build`.

```json
{"bpm_range": [96, 112], "keys": ["D", "E", "A"], "scales": ["freygish", "minor"],
 "programs": [0, 24, 32, 48], "drum_patterns": ["straight", "half-time", "shuffle"], "swing": [0, 0.1]}
```

`--seed` is required, so a set can be reproduced; `--count` defaults to 6. Duplicate variants are redrawn, and every score is checked before it is written. A set that already exists in the folder is refused unless you pass `--force`, which replaces it.

To use a set in a reel, list the files under `audio.music` in a variants file, one per variant (see Variants in `docs/reel.md`). Each rendered variant is then measured against the reel's loudness target. A file more than 1 LU off is reported as MISSES, and the run still succeeds.
