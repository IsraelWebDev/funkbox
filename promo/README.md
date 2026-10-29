# promo: funkbox promotes itself

Once a day an agent looks for what is new in AI video, picks an honest angle, makes a short vertical video about
funkbox **with funkbox**, has a **second agent** review it, and posts it to YouTube Shorts and X. Every day leaves
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

**X** (pay per use, about $0.015 per post; no link in the post text, which would cost about $0.20):
1. Create the account, then console.x.com: create an app, **User authentication settings: Read and write**, then
   generate the **Access Token and Secret** (after setting Read and write, or they stay read-only). Add credits.
2. You need `X_API_KEY`, `X_API_SECRET` (the app's consumer keys), `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`.

**Where the secrets go:** never in the repo. In Claude Code on the web, put the YouTube three in a **network secret**
of the environment the scheduled task runs in, so no session ever sees them: Secret type **Body parameter**, website
`oauth2.googleapis.com`, path `/token`, parameters `client_id`, `client_secret`, `refresh_token`; then add the plain
environment variable `YT_AUTH=proxy`. Elsewhere, set `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` as
environment variables. X signs each request with its secrets (OAuth 1.0a), so its four values must be environment
variables. `post.py` never prints any of them.

## Run a day by hand
```bash
python promo/assets.py promo/days/$(date -u +%F)        # then write brief/copy/reel/caption (see PLAYBOOK.md)
python -m funkbox reel render promo/days/<date>/reel.json --name final --out promo/days/<date> --audit
python promo/review.py show promo/days/<date>            # after the reviewer wrote its verdict
python promo/post.py promo/days/<date> --dry-run
```
