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
