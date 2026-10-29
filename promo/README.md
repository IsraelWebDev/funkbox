# promo: funkbox promotes itself

Once a day a scheduled agent looks for what is new in AI video, picks an honest angle, makes a short vertical video
about funkbox **with funkbox**, has a **second agent** review it, and posts it: the video to YouTube Shorts, then to X
(through Buffer). This folder is the code and the rules; it holds no promo content.

## Where things live

| what | where |
|---|---|
| The code and rules (this folder) | `IsraelWebDev/funkbox`, `promo/` |
| Each day's brief, copy, shot list, caption, review, text audit | [IsraelWebDev/funkbox-pending](https://github.com/IsraelWebDev/funkbox-pending) `days/<date>/` |
| The post log (every attempt, with links or errors) | funkbox-pending `posts.jsonl` |
| A reviewed video waiting to post | funkbox-pending `<date>.mp4`, deleted once it is on YouTube |
| Credentials | network secrets of the cloud environment the task runs in (see Setup); never in a repo |

funkbox-pending is public on purpose: Buffer can only take a video by URL, so X gets it from there. It only holds
what is public on YouTube anyway. `state.py` clones it to `~/funkbox-pending` (`PROMO_STATE_DIR` overrides).

## The files

| file | what |
|---|---|
| `PLAYBOOK.md` | the daily steps the run follows |
| `FACTS.md` | the only claims a promo may make (edit it when funkbox changes) |
| `REVIEW.md` | the reviewer's checklist |
| `assets.py`, `music.py` | the day's asset pack: fresh loops, the demo ad, real stills, code cards from real files, music |
| `review.py` | writes the verdict, bound to the sha256 of the exact video and caption |
| `post.py` | posts. Refuses without a matching PASS; each promo once per platform and at most once per platform per day; X only after the Short is up; dry-runs a platform without credentials |
| `pending.py` | saves the reviewed video before posting; the next run's `catchup` finishes what a failed run left (a missed upload, or an X post whose Short is up), for up to 2 days |
| `state.py` | clones or pulls funkbox-pending and commits to it |
| `auth_youtube.py` | one-time YouTube sign-in that prints the refresh token |

## The safeguards
- **Review gate:** a separate agent reviews every video against `REVIEW.md`. Its verdict is bound to the exact bytes
  of the video and caption, so nothing changed after review can post. Two FAILs mean no post that day.
- **Claims:** every claim must come from `FACTS.md`.
- **Limits in code:** `post.py` enforces the posting rules above, the caption limits, and no URL in the reviewed X
  text (it appends one link itself: the repo when the video is attached, the Short otherwise).
- **Kill switch:** see below.

## Pause or stop
Create a file `promo/PAUSED` in funkbox (GitHub: Add file -> Create new file -> `promo/PAUSED`, commit). The next
run posts nothing. Delete it to resume. To stop for good, delete the scheduled task.

## Setup (once, by the account owner)
Nothing posts until the credentials exist; until then every run renders, reviews and dry-runs.

**YouTube** (free):
1. Create the channel (a Brand Account named funkbox works well).
2. Google Cloud console: new project -> enable **YouTube Data API v3** -> OAuth consent screen: External, add yourself,
   then **Publish app** (status "In production"; in "Testing" the token dies after 7 days) -> Credentials -> Create
   OAuth client ID -> **Desktop app** -> download the JSON.
3. On your computer: `python promo/auth_youtube.py client_secret.json`, sign in as the channel, choose the channel.
   It prints `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` (or writes them to a file with `--out`).

**X** (free, through [Buffer](https://buffer.com)): X's own API charges per post, so the X post goes through Buffer's
free plan instead.
1. Create a Buffer account, connect the X account as a channel, and create an API key (Settings -> API).
2. Other Buffer channels work the same way: connect, say, Bluesky in Buffer and add `bluesky` to `--platforms`
   (the free plan allows 3 channels). They reuse the X text unless `caption.json` has its own entry.

**The state repo:** create `funkbox-pending` (public, empty) and give the scheduled task push access to it.

**Where the credentials go:** in Claude Code on the web, put them in **network secrets** of the environment the
scheduled task runs in, so no session ever sees them, then add plain environment variables that tell `post.py`
the proxy holds them. The scheduled task must run in that same environment.

| secret | type | website, path | names / value | environment variable |
|---|---|---|---|---|
| YouTube | Body parameter | `oauth2.googleapis.com`, `/token` | `client_id`, `client_secret`, `refresh_token` | `YT_AUTH=proxy` |
| Buffer | Bearer | `api.buffer.com` | `Authorization` = `Bearer <API key>` | `BUFFER_AUTH=proxy` |

Elsewhere, set `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` and `BUFFER_API_KEY` as environment variables.
`post.py` never prints any of them.

## Run a day by hand
```bash
STATE=$(python promo/state.py init); DAY=$STATE/days/$(date -u +%F)
python promo/assets.py $DAY                       # then write brief/copy/reel/caption (see PLAYBOOK.md)
python -m funkbox reel render $DAY/reel.json --name final --out $DAY --audit
python promo/review.py show $DAY                  # after the reviewer wrote its verdict
python promo/post.py $DAY --dry-run
```
