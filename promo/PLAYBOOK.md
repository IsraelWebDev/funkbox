# Daily promo run

One short video a day about funkbox, made BY funkbox, tied to something real happening in AI video, posted to
YouTube Shorts and X after an independent review. Follow these steps in order. If anything fails twice, stop, post
nothing, and report why.

The code is in the funkbox checkout; the state is NOT. Each day's files, the post log and videos waiting to post
live in the state repo named by `$PROMO_STATE_REPO`, cloned at `$STATE` (see `promo/state.py`). Work in `$STATE/days/<YYYY-MM-DD>/` (UTC date), written `DAY` below. Never write promo state into
the funkbox checkout.

## 0. Setup (fresh session)
- Get a push-capable checkout of IsraelWebDev/funkbox (attach the repo with push access, then clone), `cd` into it.
  Also attach the state repo (`$PROMO_STATE_REPO`) with push access, then `eval "$(python promo/state.py env)"`
  (clones or pulls it, sets `STATE` and lets funkbox read and write the day folders) and `DAY=$STATE/days/<date>`.
- `python -m pip install --break-system-packages --ignore-installed -r requirements.txt` (with `python -m`, so the
  packages land in the Python that runs the scripts; `--ignore-installed` steps over Debian's own Pillow and PyJWT).
  On Linux without a display, `export FUNK_GL_BACKEND=egl`.
- If `promo/PAUSED` exists: report "paused" and stop.
- Read `promo/FACTS.md`, `promo/REVIEW.md` and the last 14 lines of `$STATE/posts.jsonl` (if it exists).
- Catch up first: `python promo/pending.py catchup`. It posts what earlier runs still owe (a reviewed video saved
  before a failed upload, or an X post whose Short is already up) through the same gates. If it changed the log,
  `python promo/state.py commit "catch up" posts.jsonl`. If it posted to YouTube today, today's
  slot is used: skip to step 5 and report the catch-up.

## Queue first
`python promo/enqueue.py take $DAY`. If it prints `took <item>`, the owner queued a finished promo: today's folder now
holds its video, copy, shot list, caption, brief and feedback. Skip steps 1 and 2 and do NOT change its video or
on-screen copy. Run `python promo/measure.py facts $DAY`, then `python promo/lint.py $DAY`: you may fix brief.json
and feedback.json fields lint names, and caption.json if the review asks; if lint names anything in the video
itself, stop, post nothing and report it. Then continue at step 3 (review) as usual. If it prints `queue empty`,
carry on with step 1.

## 1. Pick today's idea (15 minutes, not more)
The point is a promo a follower of the account has NOT seen yet. Gather candidates from three places, then choose.
- **What's new in AI video** (last 48 hours): 3-5 web searches across model releases, creator workflows, ad and
  marketing trends, open-source tools, on-device / local generation, and what creators complain about. Skip
  tragedies, politics, lawsuits, personal matters and security incidents.
- **What's new in funkbox:** `python promo/measure.py facts $DAY` and read M-commits (changes since the last promo).
- **What hasn't been said:** read the last 14 `$STATE/days/*/brief.json` (format, selling point, hook).
Write down at least 3 candidates, each as format + selling point + hook, then pick the one that is most specific
and least like recent posts. Formats and selling points (ids are what lint checks):
- formats: trend-reaction, feature-spotlight, how-to, behind-the-scenes, whats-new, use-case, myth-vs-fact, tip
  (`promo/lint.py` describes each)
- selling points: S1-S11 (`promo/lint.py`; each names the facts it rests on)
- rules lint enforces: a format not used in the previous 2 days, a selling point not used in the previous 4, a hook
  that does not echo any of the last 14.
Write `brief.json`: {"date", "format", "selling_point", "candidates": [<the 3+ you considered, one line each>],
"trend": {"summary", "sources": [{"title", "url", "date"}]} (for formats that use a trend; otherwise null), "angle",
"hook", "novelty": "<what a follower of the last posts has not seen yet>", "claims": [{"text", "fact": "F9" or
"M-render"}]}. Claims should be strong in the FACTS.md sense: a number, a real file, a before/after.

Write `feedback.json` too, the product feedback for the owner from today's research:
{"observation": "<what you saw creators want or struggle with, with whom/where>", "suggestion": "<a concrete
funkbox change that would serve it>", "evidence": [<links>], "effort": "small|medium|large",
"would_enable": "<the promo this would let us make>"}. One good idea beats three vague ones; say "nothing new
today" in observation only if the searches really showed nothing.

## 2. Make the video with funkbox
- `python promo/assets.py $DAY` builds real assets (fresh loops, the demo ad, FastSD stills, code
  cards from real files, music) and `assets.json`.
- Write `copy.json` (the ONLY on-screen text; 3-5 short caption lines, max ~45 chars each, plus `end_head`, `end_sub`,
  `url` = "github.com/IsraelWebDev/funkbox") and `reel.json` (copy only by id). Start from
  `promo/templates/promo.reel.json`. Rules: 9:16 1080x1920, 15-25 s, `"audio": {"music": "assets/music.mp3", "bpm": 110,
  "snap_cuts_to_beats": true, "voiceover": false}`, cuts as `beats:N`, at least one `frame` push-in on a code card or
  the demo ad, end card `brand-split`. Captions must be readable with the sound off. On a code card, the caption
  must stay clear of the card's text (its `text_rect` in assets.json): anchor it at the bottom, and if you push in,
  push toward the text with no caption, or keep the window wide enough that the text stays above the caption.
  Vary the shots too: not every promo needs a code card or the demo ad; pick what proves today's claim.
- `python -m funkbox reel validate`, then `reel storyboard` and LOOK at it. Fix and repeat until it reads well.
- Render with `python promo/measure.py render $DAY`: it renders `final.mp4` and the text audit, times the render and
  writes `measured.json` (the M- facts claims may cite). Re-run it after any change to the video.
- `python promo/lint.py $DAY` must print `lint: OK` (captions clear of card text, variety, feedback, claim ids).
  Fix what it names and re-render; never edit its rules.
- Write `caption.json`: {"youtube": {"title" (<= 100 chars, no < >), "description" (2-4 short lines; first line the
  hook; include "Free, open source, runs locally: https://github.com/IsraelWebDev/funkbox" and "Made with funkbox."),
  "tags": [<= 8]}, "x": {"text" (<= 255 chars, NO URL or domain; name the repo as IsraelWebDev/funkbox; 1-2 hashtags; post.py
  appends a link: the repo, or the Short when the video cannot be attached)}}.

## 3. Independent review
- Spawn a SEPARATE agent (it must not be you) with this instruction: "Review the promo in <the DAY path> by
  following promo/REVIEW.md exactly. You did not make it." Give it the folder path only.
- If it FAILs: fix exactly what it names (re-render if the video changed), then spawn a NEW reviewer. A second
  FAIL means no post today: log why in your report and stop.

## 4. Post
- Save first: `python promo/pending.py save $DAY` puts the reviewed video in the state repo until it is posted (X
  gets the video from there), so a failure below can be finished by tomorrow's catch-up instead of redone. If saving
  fails, note it in the report and continue.
- Commit the day BEFORE posting (the catch-up needs the review): `python promo/state.py commit "<date> <angle>"
  days/<date>`. The state repo's .gitignore keeps the day's video and assets out. Never commit secrets anywhere,
  and never commit anything for the promo to funkbox.
- `python promo/post.py $DAY` (it refuses without a PASS for the exact files, posts each promo once
  per platform and at most once per platform per day, posts X only after the Short is up, and dry-runs a platform
  whose secrets are missing). If it fails, run it once more; if it fails again, stop: the catch-up retries tomorrow.
- `python promo/pending.py done $DAY` (drops the saved copy once YouTube has it).
- `python promo/lint.py feedback $DAY` (adds today's feedback to `$STATE/feedback.jsonl`).
- `python promo/state.py commit "<date> posted" posts.jsonl feedback.jsonl`.

## 5. Report
Reply with: the format, selling point and angle (and the other candidates you passed on), its source links, the
product feedback (observation and suggestion), the post URLs (or "dry run" / "skipped" and why), and the reviewer's notes.

## When nothing new fits
Use a format with no trend: behind-the-scenes (today's measured.json), how-to, tip, use-case or whats-new. They
need no news and are often the strongest posts, because the proof is the video itself.
