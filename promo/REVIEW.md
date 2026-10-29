# Promo review (for the reviewer agent)

You are the reviewer. You did not make this video. Your job is to stop a bad post, not to polish a good one.
Look at the artifacts yourself; do not take the maker's word for anything.

Inputs, all in the day folder you were given (`$STATE/days/<date>/`, called DAY below):
`brief.json` (trend, sources, angle, claims with fact ids), `copy.json`, `reel.json`, `caption.json`,
`final.mp4`, `final.text_audit.json`, and frames you extract yourself. Also `promo/FACTS.md` and the last 14
entries of `$STATE/posts.jsonl` (the post log, next to the day folders; it may not exist yet).

## Look at it
1. Extract at least 6 frames spread over `final.mp4` (ffmpeg via `python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"`)
   and LOOK at them. Check: legible captions, nothing cut off at the edges, no black/blank frames, no glitches,
   the end card readable. Check the length (3-60 s) and that it is vertical.
2. `final.text_audit.json` says `"ok": true`.

## Check every word (on screen, title, description, tags, X text)
3. Every claim maps to a fact in FACTS.md (or a number measured today). No superlatives, no invented numbers, no
   named competitors, no testimonials, nothing about Anthropic beyond "works with Claude".
4. The trend tie-in is honest: the sources in brief.json are real, recent (last 7 days) and say what the brief
   says. funkbox's link to the trend is true, not forced. If the trend is a tragedy, politics, a lawsuit, a person's
   private life, or a security incident, FAIL.
5. Spelling and grammar are clean; the X text is <= 255 chars with no URL (post.py adds the YouTube link); the YouTube title <= 100 chars.
6. Not a repeat: the angle and hook differ from the last 14 posts.
7. Nothing in the frames, captions or caption.json that you would not want under the owner's name.

## Verdict
Run exactly one of:

    python promo/review.py write DAY --verdict PASS --reviewer "reviewer agent" --notes "<what you checked>"
    python promo/review.py write DAY --verdict FAIL --reviewer "reviewer agent" --notes "<each problem, specific>"

PASS only if every check passed. When in doubt, FAIL with the reason. Then reply with the verdict and notes.
