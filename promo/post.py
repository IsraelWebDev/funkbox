#!/usr/bin/env python3
"""Post a reviewed promo day to YouTube (Shorts), then a short post linking to it on X (through Buffer).

    python promo/post.py $STATE/days/2026-10-29 [--platforms youtube,x] [--dry-run]

Hard gates, in code (the agent cannot talk its way past them):
- promo/PAUSED exists, or PROMO_PAUSED=1  -> nothing is posted (exit 3)
- review.json must be a PASS for the exact bytes of final.mp4 and caption.json (promo/review.py)
- at most one post per platform per UTC day, and each promo day posts once per platform (posts.jsonl, see state.py)
- caption limits: X text <= 255 chars and no URL (this script appends today's YouTube link, 23 chars on X);
  YouTube title <= 100, no < or > anywhere; video <= 60 s, vertical
- a short post (X, or any other Buffer channel) goes out only after today's YouTube post succeeded
- a platform whose secrets are not set is skipped as a dry run, never an error

Secrets come from the environment only: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (YouTube, OAuth refresh
token from promo/auth_youtube.py) and BUFFER_API_KEY (Buffer, which posts to X for free; the X account is connected
inside Buffer). Nothing secret is printed or written anywhere.

YT_AUTH=proxy: the three YouTube values are not in the environment at all. A network proxy that holds them (Claude
Code on the web: a "Body parameter" network secret for oauth2.googleapis.com /token) adds client_id, client_secret
and refresh_token to the token request, so this script sends only grant_type and never sees them.
BUFFER_AUTH=proxy: the same for Buffer, with a "Header" network secret (Authorization: Bearer <key>) for
api.buffer.com; this script then sends no Authorization header.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import review  # noqa: E402
import state  # noqa: E402

LOG = state.LOG
PAUSE = HERE / "PAUSED"
URL_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(com|io|ai|dev|org|net|app|co|so|gg)\b", re.I)
YT_SECRETS = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")


class PostError(RuntimeError):
    pass


# --------------------------------------------------------------------------- checks
def caption_problems(cap: dict) -> list[str]:
    out = []
    yt, x = cap.get("youtube") or {}, cap.get("x") or {}
    title, desc, tags = yt.get("title", ""), yt.get("description", ""), yt.get("tags", [])
    if not title or len(title) > 100:
        out.append(f"youtube.title must be 1-100 chars (has {len(title)})")
    if len(desc) > 4900:
        out.append("youtube.description is over 4900 chars")
    if any(c in title + desc for c in "<>"):
        out.append("youtube title/description must not contain < or >")
    if not isinstance(tags, list) or sum(len(t) + 1 for t in tags) > 450:
        out.append("youtube.tags must be a list totalling under 450 chars")
    text = x.get("text", "")
    if not text or len(text) > 255:
        out.append(f"x.text must be 1-255 chars (has {len(text)}; the YouTube link is added after it)")
    if URL_RE.search(text):
        out.append("x.text must not contain a URL or domain (name the repo as IsraelWebDev/funkbox instead)")
    return out


def video_problems(path: Path) -> list[str]:
    from funkbox.reel import framing
    try:
        info = framing.probe_video(path)
    except (ValueError, OSError) as e:
        return [f"cannot read {path.name}: {e}"]
    out = []
    if info.height <= info.width:
        out.append(f"video is {info.width}x{info.height}; Shorts need vertical")
    if info.duration is None or not 3 <= info.duration <= 60:
        out.append(f"video length {info.duration}s is outside 3-60 s")
    if path.stat().st_size > 256 * 1024 * 1024:
        out.append("video is over 256 MB")
    return out


def history() -> list[dict]:
    if not LOG.is_file():
        return []
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def posted_today(platform: str, today: str) -> dict | None:
    return next((e for e in history() if e.get("platform") == platform and e.get("date") == today
                 and not e.get("dry_run") and e.get("ok")), None)


def posted_day(platform: str, day: str) -> dict | None:
    """The successful post of promo day ``day`` (a folder name) on ``platform``, whenever it went out."""
    return next((e for e in history() if e.get("platform") == platform and e.get("day") == day
                 and not e.get("dry_run") and e.get("ok")), None)


# --------------------------------------------------------------------------- http
def _http(method: str, url: str, *, data: bytes | None = None, headers: dict | None = None,
          timeout: float = 300) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _json(b: bytes) -> dict:
    try:
        return json.loads(b.decode("utf-8") or "{}")
    except ValueError:
        return {"raw": b[:500].decode("utf-8", "replace")}


# --------------------------------------------------------------------------- YouTube
def yt_proxy() -> bool:
    return os.environ.get("YT_AUTH", "").lower() == "proxy"


def youtube_token_body() -> bytes:
    """The token-refresh form. Behind the proxy it must hold no credential keys: a client key that collides with an
    injected one makes the proxy refuse the request (502) instead of overriding it."""
    form = {"grant_type": "refresh_token"}
    if not yt_proxy():
        form.update(client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                    refresh_token=os.environ["YT_REFRESH_TOKEN"])
    return urllib.parse.urlencode(form).encode()


def youtube_upload(video: Path, cap: dict) -> dict:
    tok = youtube_token_body()
    st, _, body = _http("POST", "https://oauth2.googleapis.com/token", data=tok,
                        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
    if st != 200:
        raise PostError(f"YouTube token refresh failed ({st}): {_json(body).get('error_description') or _json(body)}")
    access = _json(body)["access_token"]
    yt = cap["youtube"]
    desc = yt.get("description", "")
    if "#shorts" not in (yt["title"] + desc).lower():
        desc = (desc + "\n\n#Shorts").strip()
    meta = {"snippet": {"title": yt["title"], "description": desc, "tags": yt.get("tags", []), "categoryId": "28"},
            "status": {"privacyStatus": yt.get("privacy", "public"), "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True}}
    size = video.stat().st_size
    st, hdr, body = _http("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                          data=json.dumps(meta).encode(), timeout=60,
                          headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8",
                                   "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    loc = {k.lower(): v for k, v in hdr.items()}.get("location")
    if st != 200 or not loc:
        raise PostError(f"YouTube upload session failed ({st}): {_json(body)}")
    st, _, body = _http("PUT", loc, data=video.read_bytes(), timeout=900,
                        headers={"Authorization": f"Bearer {access}", "Content-Type": "video/mp4"})
    if st not in (200, 201):
        raise PostError(f"YouTube upload failed ({st}): {_json(body)}")
    vid = _json(body).get("id")
    return {"id": vid, "url": f"https://youtube.com/shorts/{vid}"}


# --------------------------------------------------------------------------- X and others, through Buffer
BUFFER_API = "https://api.buffer.com"
BUFFER_SERVICE = {"x": "twitter"}          # our platform name -> Buffer's channel "service"; others map to themselves


def buffer_proxy() -> bool:
    return os.environ.get("BUFFER_AUTH", "").lower() == "proxy"


def buffer_secrets() -> tuple[str, ...]:
    return () if buffer_proxy() else ("BUFFER_API_KEY",)


def _gql(query: str, variables: dict | None = None) -> dict:
    """One Buffer GraphQL call. Behind the proxy no Authorization header is sent: the proxy adds it."""
    headers = {"Content-Type": "application/json"}
    if not buffer_proxy():
        headers["Authorization"] = f"Bearer {os.environ['BUFFER_API_KEY']}"
    st, _, body = _http("POST", BUFFER_API, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                        headers=headers, timeout=60)
    res = _json(body)
    if st != 200 or res.get("errors"):
        raise PostError(f"Buffer API failed ({st}): {res.get('errors') or res}")
    return res.get("data") or {}


def buffer_channel(service: str) -> dict:
    org = os.environ.get("BUFFER_ORG_ID") or next(
        (o["id"] for o in _gql("query { account { organizations { id } } }")["account"]["organizations"]), None)
    if not org:
        raise PostError("Buffer: the account has no organization")
    # ids are inlined as JSON strings rather than typed variables, so nothing depends on Buffer's scalar names
    chans = _gql("query { channels(input: {organizationId: %s}) { id service name isQueuePaused } }"
                 % json.dumps(org))["channels"]
    match = [c for c in chans if c.get("service") == service]
    if not match:
        raise PostError(f"Buffer: no {service} channel connected (have {sorted({c.get('service') for c in chans})})")
    if match[0].get("isQueuePaused"):
        raise PostError(f"Buffer: the {service} channel's queue is paused")
    return match[0]


# Where pending.py keeps the reviewed video while it posts. Buffer only takes video by URL, so this works when the
# state repo is public; PROMO_VIDEO_URL_BASE overrides it, and empty means X gets the Short's link instead.
VIDEO_URL_BASE = os.environ.get("PROMO_VIDEO_URL_BASE", f"https://raw.githubusercontent.com/{state.STATE_REPO}/main/"
                                if state.STATE_REPO else "")
REPO_URL = "https://github.com/IsraelWebDev/funkbox"


def public_video_url(day: str, video: Path) -> str | None:
    """The saved copy's public URL if it is there and the same size as the reviewed video, else None."""
    if not VIDEO_URL_BASE or not video.is_file():
        return None
    url = f"{VIDEO_URL_BASE.rstrip('/')}/{day}.mp4"
    try:
        st, hdr, _ = _http("HEAD", url, timeout=30)
    except OSError:
        return None
    size = {k.lower(): v for k, v in hdr.items()}.get("content-length")
    return url if st == 200 and size == str(video.stat().st_size) else None


def short_text(platform: str, cap: dict, link: str) -> str:
    """The reviewed short-post text plus one link, added here rather than by the agent, so the reviewed words are
    exactly what goes out: the repo when the video is attached, else the Short that holds it."""
    text = (cap.get(platform) or cap["x"])["text"]
    return f"{text}\n\n{link}"


def buffer_post(platform: str, cap: dict, link: str, video_url: str | None = None) -> dict:
    ch = buffer_channel(BUFFER_SERVICE.get(platform, platform))
    extra = {"assets": [{"video": {"url": video_url}}]} if video_url else {}
    data = _gql("""mutation($input: CreatePostInput!) { createPost(input: $input) {
        ... on PostActionSuccess { post { id status externalLink } }
        ... on MutationError { message } } }""",
                {"input": {"text": short_text(platform, cap, REPO_URL if video_url else link), "channelId": ch["id"],
                           "schedulingType": "automatic", "mode": "shareNow", **extra}})["createPost"]
    if not data.get("post"):
        raise PostError(f"Buffer refused the post: {data.get('message') or data}")
    post = data["post"]
    for _ in range(12):                                   # shareNow publishes within seconds; wait for the link
        if post.get("externalLink") or post.get("status") in ("error", "sent"):
            break
        time.sleep(5)
        try:
            post = {**post, **_gql("query { post(input: {id: %s}) { id status externalLink error { message } } }"
                                   % json.dumps(post["id"]))["post"]}
        except PostError:
            break                                         # the post went out; only the status lookup failed
    if post.get("status") == "error":
        raise PostError(f"Buffer could not publish: {(post.get('error') or {}).get('message')}")
    return {"id": post["id"], "url": post.get("externalLink") or f"buffer:{post['id']}", "via": "buffer",
            "media": "video" if video_url else "link"}


PLATFORMS = ("youtube", "x")               # any other Buffer service name (bluesky, mastodon, threads, ...) works too


def secrets_for(p: str) -> tuple[str, ...]:
    if p == "youtube":
        return () if yt_proxy() else YT_SECRETS
    return buffer_secrets()


def publish(p: str, video: Path, cap: dict, link: str | None, video_url: str | None = None) -> dict:
    if p == "youtube":
        return youtube_upload(video, cap)
    if video_url:
        try:
            return buffer_post(p, cap, link, video_url)
        except PostError as e:                            # the video was refused: the link still gets the post out
            print(f"{p}: native video refused ({e}); posting the link instead")
    return buffer_post(p, cap, link)


# --------------------------------------------------------------------------- main
def run(day: Path, platforms: list[str], dry_run: bool = False, today: str | None = None) -> int:
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    if PAUSE.exists() or os.environ.get("PROMO_PAUSED") == "1":
        print("PAUSED: promo/PAUSED exists or PROMO_PAUSED=1; nothing posted")
        return 3
    video = day / review.VIDEO
    yt_done = posted_day("youtube", day.name)
    # A day whose Short is already up can finish its short posts without the video: they only link to it.
    linked_only = not video.is_file() and yt_done is not None and "youtube" not in platforms
    why = review.check(day, video_sha=yt_done.get("video_sha256") if linked_only else None)
    if why:
        print(f"REFUSED: {why}")
        return 2
    cap = json.loads((day / review.CAPTION).read_text(encoding="utf-8"))
    probs = caption_problems(cap) + ([] if linked_only else video_problems(video))
    if probs:
        print("REFUSED:\n  " + "\n  ".join(probs))
        return 2
    rc = 0
    for p in platforms:
        if not re.fullmatch(r"[a-z]+", p):
            print(f"{p}: not a platform name (use youtube, x, or a Buffer service like bluesky)")
            rc = 2
            continue
        text = cap.get("youtube", {}).get("title") if p == "youtube" else (cap.get(p) or cap["x"])["text"]
        entry = {"date": today, "platform": p, "day": day.name, "title": text,
                 "video_sha256": yt_done["video_sha256"] if linked_only else review.sha256(video)}
        prev = posted_day(p, day.name)
        if prev:
            print(f"{p}: this promo is already posted ({prev.get('url')}); skipped")
            continue
        prev = posted_today(p, today)
        if prev:
            print(f"{p}: already posted today ({prev.get('url')}); skipped")
            continue
        link = None
        if p != "youtube":                                # short posts link to this promo's Short, so it must exist
            yt = posted_day("youtube", day.name)
            link = yt.get("url") if yt else None
        missing = [k for k in secrets_for(p) if not os.environ.get(k)]
        if dry_run or missing:
            why = "dry run" if dry_run else f"not configured (set {', '.join(missing)})"
            print(f"{p}: DRY RUN ({why}); would post {text!r}" + (f" + {link or '<the YouTube link>'}" if p != "youtube" else ""))
            continue
        if p != "youtube" and not link:
            print(f"{p}: skipped, this promo has no YouTube post to link to yet")
            continue
        try:
            res = publish(p, video, cap, link, None if p == "youtube" or linked_only else
                          public_video_url(day.name, video))
            entry.update(ok=True, **res)
            print(f"{p}: posted {res.get('url')}")
        except (PostError, OSError, KeyError) as e:
            entry.update(ok=False, error=str(e)[:500])
            print(f"{p}: FAILED {e}")
            rc = 1
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="post a reviewed promo day")
    ap.add_argument("day")
    ap.add_argument("--platforms", default="youtube,x")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.exit(run(Path(a.day), [p.strip() for p in a.platforms.split(",") if p.strip()], a.dry_run))
