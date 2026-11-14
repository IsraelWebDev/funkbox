# Video files

Four tools work on video files you already have (recordings, trailers, gameplay, phone clips). The original is never
changed and nothing is overwritten: outputs go to `FUNK_OUT/video/` and a name that exists gets `-2`, `-3` ...

| tool | CLI | does |
|---|---|---|
| `video_info(path)` | `funkbox video info clip.mp4` | duration, size, frame rate, codec, audio |
| `video_scenes(path, threshold, min_shot)` | `funkbox video scenes trailer.mp4` | finds the cuts; writes `<name>.scenes.jpg` (one frame per shot, with times) and `<name>.scenes.json`, which lists every shot as a ready reel source `{"video": ..., "trim": [start, end]}` |
| `video_trim(path, start, end \| duration, accurate)` | `funkbox video trim clip.mp4 3.2 6.0` | a piece in a new file; frame-accurate (re-encoded H.264/AAC) by default, `--fast` copies the streams from the nearest earlier keyframe |
| `video_subtitles(path, cues \| srt, style, anchor, brand)` | `funkbox video subtitles clip.mp4 --srt subs.srt` | burns subtitles into a copy. `style` is one of the caption styles (auto-box, bold-pop, callout, lower-third, red-box); `anchor` is top, center or bottom |

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


## Plan-driven renders (proposed, not live)

The tools and their input schemas are listed in [docs/catalog.json](https://github.com/IsraelWebDev/funkbox/blob/main/docs/catalog.json). The file is generated from the MCP server: `python -m funkbox catalog` (`--check` verifies it). The website is proposed to plan a render from this catalog: an AI planner writes a plan of these tools, the server checks the plan, and a preview runs it at low resolution or shows a storyboard before a paid render runs the same stored plan. Nothing here changes how the local tools work today. Decision and sign-offs: [docs/adr-plan-driven-renders.md in IsraelWebDev/funkbox-online](https://github.com/IsraelWebDev/funkbox-online/blob/main/docs/adr-plan-driven-renders.md).
