#!/usr/bin/env python3
"""A queue of ready-made promos: the next daily run posts the oldest one instead of making a new video.

    python promo/enqueue.py add SRC_DIR --name SLUG     # owner: put a finished promo in the queue
    python promo/enqueue.py list
    python promo/enqueue.py take $STATE/days/<date>     # the daily run: move the oldest item into today's folder

An item is a folder with the same files a day has: final.mp4, final.text_audit.json, brief.json, copy.json,
reel.json, caption.json and feedback.json (see PLAYBOOK.md). It lives in the state repo under queue/<n>-<slug>/
until a run takes it. Taking it changes nothing about posting: the run still writes measured.json, runs lint, has
a separate agent review it and posts through post.py's gates. A queued promo that fails review is not posted.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import state  # noqa: E402

FILES = ("final.mp4", "final.text_audit.json", "brief.json", "copy.json", "reel.json", "caption.json",
         "feedback.json")


def qdir() -> Path:
    return state.STATE_DIR / "queue"


def items() -> list[Path]:
    q = qdir()
    return sorted(p for p in q.iterdir() if p.is_dir()) if q.is_dir() else []


def problems(src: Path) -> list[str]:
    out = [f"{f} is missing" for f in FILES if not (src / f).is_file()]
    for f in FILES:
        if f.endswith(".json") and (src / f).is_file():
            try:
                json.loads((src / f).read_text(encoding="utf-8"))
            except ValueError as e:
                out.append(f"{f} is not valid JSON ({e})")
    return out


def add(src: Path, slug: str, commit: bool = True) -> Path:
    probs = problems(src)
    if probs:
        raise SystemExit("not queued: " + "; ".join(probs))
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-") or "promo"
    dst = qdir() / f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{slug}"
    dst.mkdir(parents=True)
    for f in FILES:
        shutil.copy2(src / f, dst / f)
    if commit:
        state.commit([str(dst.relative_to(state.STATE_DIR))], f"queue {dst.name}")
    return dst


def take(day: Path, commit: bool = True) -> Path | None:
    """Move the oldest item into ``day`` (which must not hold a video yet). None when the queue is empty."""
    q = items()
    if not q:
        return None
    if (day / "final.mp4").exists():
        raise SystemExit(f"{day} already has a video; not taking from the queue")
    item = q[0]
    day.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        shutil.move(str(item / f), str(day / f))
    for extra in item.iterdir():                   # anything else the owner left (notes, assets list)
        shutil.move(str(extra), str(day / extra.name))
    item.rmdir()
    b = json.loads((day / "brief.json").read_text(encoding="utf-8"))
    b["queued_as"] = item.name
    b["date"] = day.name
    (day / "brief.json").write_text(json.dumps(b, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if commit:
        state.commit([str(item.relative_to(state.STATE_DIR))], f"take {item.name} for {day.name}")
    return item


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("src")
    a.add_argument("--name", required=True)
    sub.add_parser("list")
    t = sub.add_parser("take")
    t.add_argument("day")
    x = ap.parse_args()
    try:
        if x.cmd != "list":
            state.ensure()
        if x.cmd == "add":
            print(f"queued {add(Path(x.src), x.name).name}")
        elif x.cmd == "list":
            print("\n".join(p.name for p in items()) or "queue empty")
        else:
            got = take(Path(x.day))
            print(f"took {got.name}" if got else "queue empty")
    except state.StateError as e:
        sys.exit(f"queue: {e}")
