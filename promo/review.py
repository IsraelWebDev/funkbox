#!/usr/bin/env python3
"""The review gate: a verdict bound to the exact bytes that would be posted.

    python promo/review.py write DAY --verdict PASS --reviewer "<who>" --notes "<why>"
    python promo/review.py show DAY

``write`` is called by the REVIEWER (a separate agent that did not make the video), never by the maker. It hashes
<day>/final.mp4 and <day>/caption.json into <day>/review.json, so any change to either after the review voids it:
post.py refuses to post unless review.json says PASS for those exact hashes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

VIDEO, CAPTION, REVIEW = "final.mp4", "caption.json", "review.json"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write(day: Path, verdict: str, reviewer: str, notes: str) -> dict:
    if verdict not in ("PASS", "FAIL"):
        raise SystemExit("verdict must be PASS or FAIL")
    if not reviewer.strip() or not notes.strip():
        raise SystemExit("--reviewer and --notes are required (say what you checked)")
    for f in (VIDEO, CAPTION):
        if not (day / f).is_file():
            raise SystemExit(f"{day / f} is missing: nothing to review")
    doc = {"verdict": verdict, "reviewer": reviewer.strip(), "notes": notes.strip(),
           "video_sha256": sha256(day / VIDEO), "caption_sha256": sha256(day / CAPTION),
           "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (day / REVIEW).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def check(day: Path, video_sha: str | None = None) -> str | None:
    """None when <day> holds a PASS review of exactly the current video and caption; else the reason it doesn't.
    ``video_sha``: the video is no longer here (it was posted and not kept); its recorded hash stands in for it."""
    p = day / REVIEW
    if not p.is_file():
        return "no review.json: the reviewer has not signed off"
    try:
        r = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        return f"review.json is not valid JSON: {e}"
    if r.get("verdict") != "PASS":
        return f"review verdict is {r.get('verdict')!r}: {r.get('notes', '')}"
    if video_sha is not None and not (day / VIDEO).is_file():
        if video_sha != r.get("video_sha256"):
            return f"{VIDEO} that was posted is not the one reviewed (hash mismatch)"
        files = ((CAPTION, "caption_sha256"),)
    else:
        files = ((VIDEO, "video_sha256"), (CAPTION, "caption_sha256"))
    for f, key in files:
        if not (day / f).is_file():
            return f"{f} is missing"
        if sha256(day / f) != r.get(key):
            return f"{f} changed after the review (hash mismatch): review again"
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="promo review gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("day")
    w.add_argument("--verdict", required=True)
    w.add_argument("--reviewer", required=True)
    w.add_argument("--notes", required=True)
    s = sub.add_parser("show")
    s.add_argument("day")
    a = ap.parse_args()
    if a.cmd == "write":
        print(json.dumps(write(Path(a.day), a.verdict, a.reviewer, a.notes), indent=2))
    else:
        why = check(Path(a.day))
        print("OK: PASS for the current video and caption" if why is None else f"NOT POSTABLE: {why}")
        sys.exit(0 if why is None else 1)
