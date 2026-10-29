# promo: funkbox promotes itself

Once a day an agent looks for what is new in AI video, picks an honest angle, makes a short vertical video about
funkbox **with funkbox**, has a **second agent** review it, and posts it to YouTube Shorts, then a short post linking to it on X (through Buffer). Every day leaves
its brief, copy, shot list, captions and review in `promo/days/<date>/` and a line in `promo/posts.jsonl`, so the
whole history is public.

| file | what |
|---|---|
| `PLAYBOOK.md` | the daily steps the run follows |
| `FACTS.md` | the only claims a promo may make (edit it when funkbox changes) |
| `REVIEW.md` | the reviewer's checklist |
| `assets.py`, `music.py` | the day's asset pack: fresh loops, the demo ad, real stills, code cards from real files, music |
| `review.py` | writes the verdict, bound to the sha256 of the exact video and caption |
| `post.py` | posts; refuses without a matching PASS, once per platform per day, dry-runs without secrets |
| `pending.py` | keeps a reviewed video in the private repo IsraelWebDev/funkbox-pending until it is posted; the next run's `catchup` finishes what a failed run left (up to 2 days) |
| `auth_youtube.py` | one-time YouTube sign-in that prints the refresh token |

## Pause or stop
Create a file `promo/PAUSED` (GitHub: Add file -> Create new file -> `promo/PAUSED`, commit). The next run posts
nothing. Delete it to resume. To stop for good, delete the scheduled task.

## Setup (once, by the account owner)
Nothing posts until these secrets exist; until then every run renders, reviews and dry-runs.

**YouTube** (free):
1. Create the channel (a Brand Account named funkbox works well).
2. Google Cloud console: new project -> enable **YouTube Data API v3** -> OAuth consent screen: External, add yourself,
   then **Publish app** (status "In production"; in "Testing" the token dies after 7 days) -> Credentials -> Create
   OAuth client ID -> **Desktop app** -> download the JSON.
3. On your computer: `python promo/auth_youtube.py client_secret.json`, sign in as the channel, choose the channel.
   It prints `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` (or writes them to a file with `--out`).

**X** (free, through [Buffer](https://buffer.com)): X's own API charges per post, so the X post goes through Buffer's
free plan instead. It is a short post with the reviewed text plus a link to that day's Short, sent only after the
YouTube upload succeeded.
1. Create a Buffer account, connect the X account as a channel, and create an API key (Settings -> API).
2. Other Buffer channels work the same way: connect, say, Bluesky in Buffer and add `bluesky` to `--platforms`
   (the free plan allows 3 channels). They reuse the X text unless `caption.json` has its own entry.

**Where the secrets go:** never in the repo. In Claude Code on the web, put them in **network secrets** of the
environment the scheduled task runs in, so no session ever sees them, then add plain environment variables that
tell `post.py` the proxy holds them:

| secret | type | website, path | names / value | environment variable |
|---|---|---|---|---|
| YouTube | Body parameter | `oauth2.googleapis.com`, `/token` | `client_id`, `client_secret`, `refresh_token` | `YT_AUTH=proxy` |
| Buffer | Header | `api.buffer.com` | `Authorization` = `Bearer <API key>` | `BUFFER_AUTH=proxy` |

Elsewhere, set `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` and `BUFFER_API_KEY` as environment variables.
`post.py` never prints any of them.

## Run a day by hand
```bash
python promo/assets.py promo/days/$(date -u +%F)        # then write brief/copy/reel/caption (see PLAYBOOK.md)
python -m funkbox reel render promo/days/<date>/reel.json --name final --out promo/days/<date> --audit
python promo/review.py show promo/days/<date>            # after the reviewer wrote its verdict
python promo/post.py promo/days/<date> --dry-run
```
