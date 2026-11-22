# Video files

Eight tools work on video files you already have (recordings, trailers, gameplay, phone clips). The original is never
changed and nothing is overwritten: outputs go to `FUNK_OUT/video/` and a name that exists gets `-2`, `-3` ...

| tool | CLI | does |
|---|---|---|
| `video_info(path)` | `funkbox video info clip.mp4` | duration, size, frame rate, codec, audio |
| `video_scenes(path, threshold, min_shot)` | `funkbox video scenes trailer.mp4` | finds the cuts; writes `<name>.scenes.jpg` (one frame per shot, with times) and `<name>.scenes.json`, which lists every shot as a ready reel source `{"video": ..., "trim": [start, end]}` |
| `video_trim(path, start, end \| duration, accurate)` | `funkbox video trim clip.mp4 3.2 6.0` | a piece in a new file; frame-accurate (re-encoded H.264/AAC) by default, `--fast` copies the streams from the nearest earlier keyframe |
| `video_subtitles(path, cues \| srt, style, anchor, brand, words)` | `funkbox video subtitles clip.mp4 --srt subs.srt [--words clip.words.json]` | burns subtitles into a copy. `style` is one of the caption styles (auto-box, bold-pop, callout, lower-third, red-box); `anchor` is top, center or bottom. `words` (a `<name>.words.json` from `video transcribe`) makes each word appear when it is said, with the word highlight following it; the cue text is still drawn exactly, the transcript only sets the times. A cue the transcript never heard is drawn whole, and the audit says so |
| `video_contact_sheet(path, every_s, cols, name)` | `funkbox video sheet clip.mp4 --every 2` | a grid of frames every N seconds, each labelled with its time (`<name>-contact.jpg`). Use it to find moments the cuts miss, such as single takes in home video. At most 240 frames |
| `video_edit(cuts, transition, grade, aspect, target_mb)` | `funkbox video edit cuts.json --target-mb 8` | one render from a cut list `[{"path", "start", "end"}, ...]`: each cut framed to 9:16 or 16:9 at 30 fps (`frame`: `pad`, `crop` or `blurred-bg`), graded (`funky`, `warm` or `none`), joined with a white `flash` or a hard `cut` (audio crossfaded). `stabilize` (default `auto`) checks each source for camera shake and steadies only the shaky ones with vidstab (cached per file, cut timing kept); `off` keeps the picture as shot. `target_mb` aims the bitrate at a file size. `preview` renders half size in seconds, so check the cuts first, then render again without it. Takes the trims from `video_scenes` or the sheet. Refuses a flash cut under 0.5 s |
| `video_highlights(paths, target_s, seg_s)` | `funkbox video highlights a.mp4 b.mp4 --target 30 60` | proposes moments: windows of `seg_s` seconds (default 3) ranked by motion, each new one scored by how different its colours are from the moments already chosen, so repeats are dropped. Fills up to the top of `target_s`. Writes `<name>.jpg` (a labelled sheet to approve) and `<name>.json` (the cut list). Nothing is cut until you approve; then pass the cuts to `video_edit`. Says so when the clips do not have enough different motion for the target |
| `video_transcribe(path, language, model)` | `funkbox video transcribe talk.mp4 --language he` | speech to text on this machine (faster-whisper, CPU): every word with its start and end in seconds, to `<name>.words.json`, plus the phrases to `<name>.srt`. `model` is `tiny`, `base`, `small`, `medium` or `large-v3`; left out, it is `large-v3` for `he` and `small` otherwise (Hebrew needs the large model: small got about half the words wrong on a short sample); the first use of a model downloads it. Needs `requirements-transcribe.txt` (or `install.py --with transcribe`). Never overwrites |
| `video_cutplan(path, words, keep_s, min_pause_s, fillers, name)` | `funkbox video cutplan talk.mp4 --words talk.words.json [--fillers um,uh]` | proposes cuts for approval, renders nothing: long pauses found in the sound (a breath, `keep_s`, kept on each side), repeated takes found in the words (the earlier take proposed for removal, the later one kept, when the repeat is within 30 s) and filler words in the words (`fillers`: `default` is um, uh, er, ah, hmm and their spellings; a comma-separated list of your own; or `none`). A filler is only found when the transcript wrote it down. Writes `<name>.cutplan.json` with the removals, the kept ranges and a cut list ready for `video_edit` after approval |
| `video_import(folder)` | `funkbox video import trip-clips` | copies the video files of one folder (not its sub-folders) into the inputs folder, under the folder's name. A file already there is kept, never overwritten; the result lists what was copied and what was kept. The folder must be inside an allowed folder; copy anything else in first |

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
