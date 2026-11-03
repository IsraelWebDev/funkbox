# Video files

Four tools work on video files you already have (recordings, trailers, gameplay, phone clips). The original is never
changed and nothing is overwritten: outputs go to `FUNK_OUT/video/` and a name that exists gets `-2`, `-3` ...

| tool | CLI | does |
|---|---|---|
| `video_info(path)` | `funkbox video info clip.mp4` | duration, size, frame rate, codec, audio |
| `video_scenes(path, threshold, min_shot)` | `funkbox video scenes trailer.mp4` | finds the cuts; writes `<name>.scenes.jpg` (one frame per shot, with times) and `<name>.scenes.json`, which lists every shot as a ready reel source `{"video": ..., "trim": [start, end]}` |
| `video_trim(path, start, end \| duration, accurate)` | `funkbox video trim clip.mp4 3.2 6.0` | a piece in a new file; frame-accurate (re-encoded H.264/AAC) by default, `--fast` copies the streams from the nearest earlier keyframe |
| `video_subtitles(path, cues \| srt, style, anchor, brand)` | `funkbox video subtitles clip.mp4 --srt subs.srt` | burns subtitles into a copy |

**Scenes.** ffmpeg's scene score marks a cut where the picture changes by more than `threshold` (0.05..0.95, default
0.3; lower finds more). Shots shorter than `min_shot` seconds are merged into their neighbour. The usual flow for a
reel from a trailer: `video_scenes`, look at the sheet, pick shots, copy their trims into the reel's `source`.

**Subtitles.** Cues come from an `.srt` / `.vtt` file or a list `[{"start": 1.2, "end": 3.0, "text": "..."}]` (times
in seconds or `[hh:]mm:ss`). Each cue is drawn by the same caption engine as reels: a caption style (`lower-third`,
`red-box`, `bold-pop`, `auto-box` — there, with no reel frame to sample, the `"auto"` box falls back to `brand.primary`),
the brand's fonts and colours, wrapped on spaces, never shrunk below the minimum size,
right-to-left for Hebrew. The text is never rewritten (tags such as `<i>` are drawn as written), and
`<name>.text_audit.json` proves every cue was drawn exactly. A portrait video uses the reels safe area (clear of the
app's buttons); a landscape one keeps clear of the player controls.
