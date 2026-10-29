#!/usr/bin/env python3
"""Post a reviewed promo day to YouTube (Shorts) and X.

    python promo/post.py promo/days/2026-10-29 [--platforms youtube,x] [--dry-run]

Hard gates, in code (the agent cannot talk its way past them):
- promo/PAUSED exists, or PROMO_PAUSED=1  -> nothing is posted (exit 3)
- review.json must be a PASS for the exact bytes of final.mp4 and caption.json (promo/review.py)
- at most one post per platform per UTC day (promo/posts.jsonl)
- caption limits: X text <= 280 chars and NO URLs (a link post costs ~13x more on X); YouTube title <= 100,
  no < or > anywhere; video <= 60 s, vertical
- a platform whose secrets are not set is skipped as a dry run, never an error

Secrets come from the environment only: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (YouTube, OAuth refresh
token from promo/auth_youtube.py) and X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET (X, OAuth 1.0a user
context). Nothing secret is printed or written anywhere.

YT_AUTH=proxy: the three YouTube values are not in the environment at all. A network proxy that holds them (Claude
Code on the web: a "Body parameter" network secret for oauth2.googleapis.com /token) adds client_id, client_secret
and refresh_token to the token request, so this script sends only grant_type and never sees them.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import review  # noqa: E402

LOG = HERE / "posts.jsonl"
PAUSE = HERE / "PAUSED"
URL_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(com|io|ai|dev|org|net|app|co|so|gg)\b", re.I)
YT_SECRETS = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
X_SECRETS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
X_API = "https://api.x.com"


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
    if not text or len(text) > 280:
        out.append(f"x.text must be 1-280 chars (has {len(text)})")
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


# --------------------------------------------------------------------------- X (OAuth 1.0a user context)
def _pct(s: str) -> str:
    return urllib.parse.quote(str(s), safe="~")


def oauth1_header(method: str, url: str, params: dict, key: str, secret: str, token: str, token_secret: str,
                  nonce: str | None = None, timestamp: str | None = None) -> str:
    """RFC 5849 HMAC-SHA1 Authorization header. ``params``: query (and form) parameters; JSON/multipart bodies are
    not signed."""
    oauth = {"oauth_consumer_key": key, "oauth_nonce": nonce or secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": timestamp or str(int(time.time())),
             "oauth_token": token, "oauth_version": "1.0"}
    allp = {**params, **oauth}
    base_params = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted((str(k), str(v)) for k, v in allp.items()))
    base = "&".join([method.upper(), _pct(url.split("?")[0]), _pct(base_params)])
    sig = base64.b64encode(hmac.new(f"{_pct(secret)}&{_pct(token_secret)}".encode(), base.encode(), hashlib.sha1).digest())
    oauth["oauth_signature"] = sig.decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def _x(method: str, path: str, *, query: dict | None = None, json_body: dict | None = None,
       multipart: tuple[bytes, str] | None = None, timeout: float = 120) -> dict:
    url = X_API + path
    q = query or {}
    auth = oauth1_header(method, url, q, os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                         os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"])
    headers = {"Authorization": auth}
    data = None
    if json_body is not None:
        data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
    elif multipart is not None:
        data, headers["Content-Type"] = multipart
    full = url + ("?" + urllib.parse.urlencode(q) if q else "")
    st, _, body = _http(method, full, data=data, headers=headers, timeout=timeout)
    if st >= 300:
        raise PostError(f"X {method} {path} failed ({st}): {_json(body)}")
    return _json(body)


def _multipart(fields: dict, file_field: str, blob: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in fields.items()]
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"chunk\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + blob + b"\r\n")
    return b"".join(parts) + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


def x_post(video: Path, cap: dict) -> dict:
    blob = video.read_bytes()
    init = _x("POST", "/2/media/upload/initialize",
              json_body={"media_type": "video/mp4", "total_bytes": len(blob), "media_category": "amplify_video"})
    mid = (init.get("data") or init).get("id") or (init.get("data") or init).get("media_id_string")
    if not mid:
        raise PostError(f"X media initialize returned no id: {init}")
    step = 4 * 1024 * 1024
    for i in range(0, len(blob), step):
        _x("POST", f"/2/media/upload/{mid}/append", multipart=_multipart({"segment_index": i // step}, "media",
                                                                            blob[i:i + step]), timeout=300)
    fin = _x("POST", f"/2/media/upload/{mid}/finalize")
    info = (fin.get("data") or fin).get("processing_info")
    for _ in range(60):
        if not info or info.get("state") == "succeeded":
            break
        if info.get("state") == "failed":
            raise PostError(f"X video processing failed: {info}")
        time.sleep(max(2, int(info.get("check_after_secs", 5))))
        stat = _x("GET", "/2/media/upload", query={"command": "STATUS", "media_id": mid})
        info = (stat.get("data") or stat).get("processing_info")
    else:
        raise PostError("X video processing did not finish in time")
    res = _x("POST", "/2/tweets", json_body={"text": cap["x"]["text"], "media": {"media_ids": [str(mid)]}})
    tid = (res.get("data") or {}).get("id")
    return {"id": tid, "url": f"https://x.com/i/web/status/{tid}"}


PLATFORMS = {"youtube": (YT_SECRETS, youtube_upload), "x": (X_SECRETS, x_post)}


# --------------------------------------------------------------------------- main
def run(day: Path, platforms: list[str], dry_run: bool = False, today: str | None = None) -> int:
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    if PAUSE.exists() or os.environ.get("PROMO_PAUSED") == "1":
        print("PAUSED: promo/PAUSED exists or PROMO_PAUSED=1; nothing posted")
        return 3
    why = review.check(day)
    if why:
        print(f"REFUSED: {why}")
        return 2
    cap = json.loads((day / review.CAPTION).read_text(encoding="utf-8"))
    video = day / review.VIDEO
    probs = caption_problems(cap) + video_problems(video)
    if probs:
        print("REFUSED:\n  " + "\n  ".join(probs))
        return 2
    rc = 0
    for p in platforms:
        if p not in PLATFORMS:
            print(f"{p}: unknown platform (have {sorted(PLATFORMS)})")
            rc = 2
            continue
        need, fn = PLATFORMS[p]
        entry = {"date": today, "platform": p, "day": day.name, "video_sha256": review.sha256(video),
                 "title": cap.get("youtube", {}).get("title") if p == "youtube" else cap.get("x", {}).get("text")}
        prev = posted_today(p, today)
        if prev:
            print(f"{p}: already posted today ({prev.get('url')}); skipped")
            continue
        if p == "youtube" and yt_proxy():
            need = ()                                         # the proxy holds them
        missing = [k for k in need if not os.environ.get(k)]
        if dry_run or missing:
            why = "dry run" if dry_run else f"not configured (set {', '.join(missing)})"
            print(f"{p}: DRY RUN ({why}); would post {entry['title']!r}")
            continue
        try:
            res = fn(video, cap)
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
