# Daily promo run

One short video a day about funkbox, made BY funkbox, tied to something real happening in AI video, posted to
YouTube Shorts and X after an independent review. Follow these steps in order. If anything fails twice, stop, post
nothing, and report why.

The code is in the funkbox checkout; the state is NOT. Each day's files, the post log and videos waiting to post
live in the state repo IsraelWebDev/funkbox-pending, cloned at `$STATE` (`~/funkbox-pending`, see
`promo/state.py`). Work in `$STATE/days/<YYYY-MM-DD>/` (UTC date), written `DAY` below. Never write promo state into
the funkbox checkout.

## 0. Setup (fresh session)
- Get a push-capable checkout of IsraelWebDev/funkbox (attach the repo with push access, then clone), `cd` into it.
  Also attach IsraelWebDev/funkbox-pending with push access, then `STATE=$(python promo/state.py init)` (clones or
  pulls it) and `DAY=$STATE/days/<date>`.
- `pip install --break-system-packages -r requirements.txt` ; on Linux without a display `export FUNK_GL_BACKEND=egl`.
- If `promo/PAUSED` exists: report "paused" and stop.
- Read `promo/FACTS.md`, `promo/REVIEW.md` and the last 14 lines of `$STATE/posts.jsonl` (if it exists).
- Catch up first: `python promo/pending.py catchup`. It posts what earlier runs still owe (a reviewed video saved
  before a failed upload, or an X post whose Short is already up) through the same gates. If it changed the log,
  `python promo/state.py commit "catch up" posts.jsonl`. If it posted to YouTube today, today's
  slot is used: skip to step 5 and report the catch-up.

## 1. Find today's angle (10 minutes, not more)
- Web-search what is new in AI video in the last 48 hours: model releases, features, creator workflows, debates
  about AI ads, open-source tools, on-device / local generation. 3-5 searches.
- Pick ONE angle where funkbox has an honest, specific connection (see FACTS.md). Good: "a new video model can't
  spell; here is how funkbox keeps captions exact", "everyone needs vertical AND horizontal; one edit, both",
  "no GPU? this runs on an Intel laptop". Bad: anything that needs a claim not in FACTS.md.
- Skip trends that are tragedies, politics, lawsuits, personal matters or security incidents.
- If nothing fits honestly, use an evergreen angle from the list at the end. Do not force it.
- Write `brief.json`: {"date", "trend": {"summary", "sources": [{"title", "url", "date"}]}, "angle", "hook",
  "claims": [{"text", "fact": "F9"}], "not_a_repeat_of": "<why it differs from the last posts>"}.

## 2. Make the video with funkbox
- `python promo/assets.py $DAY` builds real assets (fresh loops, the demo ad, FastSD stills, code
  cards from real files, music) and `assets.json`.
- Write `copy.json` (the ONLY on-screen text; 3-5 short caption lines, max ~45 chars each, plus `end_head`, `end_sub`,
  `url` = "github.com/IsraelWebDev/funkbox") and `reel.json` (copy only by id). Start from
  `promo/templates/promo.reel.json`. Rules: 9:16 1080x1920, 15-25 s, `"audio": {"music": "assets/music.mp3", "bpm": 110,
  "snap_cuts_to_beats": true, "voiceover": false}`, cuts as `beats:N`, at least one `frame` push-in on a code card or
  the demo ad, end card `brand-split`. Captions must be readable with the sound off.
- `python -m funkbox reel validate`, then `reel storyboard` and LOOK at it. Fix and repeat until it reads well.
- Render: `python -m funkbox reel render $DAY/reel.json --name final --out $DAY --audit`.
  (Keeps `final.mp4` and `final.text_audit.json` in the day folder.)
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
- `python promo/state.py commit "<date> posted" posts.jsonl`.

## 5. Report
Reply with: the angle and its source links, the post URLs (or "dry run" / "skipped" and why), and the reviewer's notes.

## Evergreen angles (rotate; check `$STATE/posts.jsonl`)
- One edit, two formats: 9:16 and 16:9 from the same shot list.
- Exact captions: the copy lock and the text audit.
- No NVIDIA GPU: everything here rendered on an Intel laptop / a CPU box.
- Push-ins on a screen recording, cuts on a 110 BPM grid.
- Seamless loops: the loop checker.
- Posters with exact text in four palettes.
- Claude drives it: one MCP server, 37 tools.
- The demo ad: how GLOWMOP was made (FastSD scenes, code-drawn bottle, synthesized music).
