#!/usr/bin/env python3
"""One-time YouTube sign-in for promo/post.py. Run it YOURSELF on your own computer:

    python promo/auth_youtube.py path/to/client_secret.json [--out promo/secrets/youtube.env]

It opens your browser at Google's consent page (scope: upload videos only), catches the reply on
http://127.0.0.1:<port>, and prints the three values to store as secrets: YT_CLIENT_ID, YT_CLIENT_SECRET and
YT_REFRESH_TOKEN (with --out, they are written to that file instead of the screen). Nothing is sent anywhere
except Google.

client_secret.json: Google Cloud console -> APIs & Services -> enable "YouTube Data API v3" -> Credentials ->
Create OAuth client ID -> Application type "Desktop app" -> Download JSON. Set the OAuth consent screen's publishing
status to "In production" (an app left in "Testing" gets refresh tokens that expire after 7 days).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def main(path: str, out: str | None = None) -> None:
    raw = json.load(open(path, encoding="utf-8"))
    c = raw.get("installed") or raw.get("web") or raw
    cid, csec = c["client_id"], c["client_secret"]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state:
                got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and go back to the terminal.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "access_type": "offline",
        "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening your browser. Sign in with the account that owns the funkbox YouTube channel.\nIf nothing opens, "
          "paste this URL into your browser:\n\n" + url + "\n")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    while "code" not in got and "error" not in got:
        srv.handle_request()
    if "error" in got:
        sys.exit(f"Google said: {got['error']}")
    body = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": csec, "redirect_uri": redirect,
                                   "grant_type": "authorization_code", "code_verifier": verifier}).encode()
    tok = json.load(urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body)))
    if "refresh_token" not in tok:
        sys.exit("No refresh token came back. Remove the app's access at myaccount.google.com/permissions and run "
                 "this again.")
    lines = f"YT_CLIENT_ID={cid}\nYT_CLIENT_SECRET={csec}\nYT_REFRESH_TOKEN={tok['refresh_token']}\n"
    if out:
        from pathlib import Path
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(lines, encoding="utf-8")
        print(f"Done. The three values are in {out}: copy them into the cloud environment's network secret "
              "(see promo/README.md), then delete the file.")
    else:
        print("Store these three as secrets (environment variables) where the daily run executes:\n\n" + lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--out"]
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    if out:
        args.remove(out)
    if len(args) != 1:
        sys.exit(__doc__)
    main(args[0], out)
