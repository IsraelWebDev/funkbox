#!/usr/bin/env python3
"""Keep a reviewed promo video until it is posted, so a failed run can post it later instead of starting over.

    python promo/pending.py save promo/days/<date>     # after the review PASS, before posting
    python promo/pending.py done promo/days/<date>     # after posting: drops the saved copy once YouTube has it
    python promo/pending.py catchup                    # at the start of every run: posts what earlier runs still owe

Videos are never committed to funkbox. The saved copy goes to a small PRIVATE repo (default
IsraelWebDev/funkbox-pending, override with PROMO_PENDING_REPO) as <date>.mp4 and is removed once posted. A cloud
session cannot write GitHub releases, but it can push, and a separate private repo keeps the public one lean.

catchup handles the two ways a day can be left unfinished:
- the YouTube upload never happened (failure, or the run died after the review): the video is downloaded, checked
  against the committed review (same bytes, or it is not posted), and posted with post.py's usual gates;
- YouTube went out but the X post did not: X only needs the logged YouTube link, so no video is needed.
Anything older than KEEP_DAYS is dropped instead: a stale angle should not post late.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import post  # noqa: E402
import review  # noqa: E402

PENDING_REPO = os.environ.get("PROMO_PENDING_REPO", "IsraelWebDev/funkbox-pending")
PENDING_URL = os.environ.get("PROMO_PENDING_URL", f"https://github.com/{PENDING_REPO}.git")
PENDING_DIR = Path(os.environ.get("PROMO_PENDING_DIR", Path.home() / "funkbox-pending"))
KEEP_DAYS = 2
DAYS = HERE / "days"
DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class PendingError(RuntimeError):
    pass


class Store:
    """A shallow clone of the private repo; every change is committed and pushed at once."""

    def __init__(self, path: Path):
        self.path = path

    def git(self, *args: str) -> str:
        r = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True)
        if r.returncode:
            raise PendingError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout

    def days(self) -> list[str]:
        return sorted(f.stem for f in self.path.glob("*.mp4"))

    def _commit(self, msg: str) -> None:
        ident = [] if _has_ident(self.path) else \
            ["-c", "user.name=funkbox promo", "-c", "user.email=promo@users.noreply.github.com"]
        self.git(*ident, "commit", "-q", "-m", msg)
        self.git("push", "-q", "origin", "HEAD:main")

    def put(self, day: str, src: Path) -> None:
        shutil.copyfile(src, self.path / f"{day}.mp4")
        self.git("add", f"{day}.mp4")
        self._commit(f"save {day}")

    def get(self, day: str, dst: Path) -> bool:
        src = self.path / f"{day}.mp4"
        if not src.is_file():
            return False
        shutil.copyfile(src, dst)
        return True

    def remove(self, day: str) -> bool:
        if not (self.path / f"{day}.mp4").is_file():
            return False
        self.git("rm", "-q", f"{day}.mp4")
        self._commit(f"posted or expired: {day}")
        return True


def _has_ident(path: Path) -> bool:
    return subprocess.run(["git", "-C", str(path), "config", "user.email"], capture_output=True).returncode == 0


def open_store() -> Store:
    """A fresh shallow clone each time: the repo only ever holds a day or two of videos."""
    if PENDING_DIR.exists():
        shutil.rmtree(PENDING_DIR)
    r = subprocess.run(["git", "clone", "-q", "--depth", "1", PENDING_URL, str(PENDING_DIR)], capture_output=True, text=True)
    if r.returncode:
        raise PendingError(f"cannot clone {PENDING_REPO} (attach it with push access): {r.stderr.strip()[:300]}")
    return Store(PENDING_DIR)


def save(day: Path) -> str:
    why = review.check(day)
    if why:
        raise PendingError(f"not saving {day.name}: {why}")
    open_store().put(day.name, day / review.VIDEO)
    return f"{day.name}: saved for later in {PENDING_REPO}"


def restore(day: Path, store: Store | None = None) -> None:
    if not (store or open_store()).get(day.name, day / review.VIDEO):
        raise PendingError(f"{day.name}: no saved video")
    why = review.check(day)
    if why:
        raise PendingError(f"{day.name}: the saved video does not match its review ({why})")


def done(day: Path) -> str:
    if not post.posted_day("youtube", day.name):
        return f"{day.name}: not on YouTube yet, the saved copy stays"
    return f"{day.name}: saved copy removed" if open_store().remove(day.name) else f"{day.name}: nothing saved"


def _age(day: str, today: dt.date) -> int | None:
    m = DAY_RE.match(day)
    return (today - dt.date.fromisoformat(m.group(1))).days if m else None


def owed(today: dt.date, store: Store | None) -> list[str]:
    """Promo days with something left to post: a saved video, or a YouTube post whose X post never went out."""
    days = set(store.days() if store else [])
    days |= {e["day"] for e in post.history() if e.get("platform") == "youtube" and e.get("ok") and e.get("day")
             and not post.posted_day("x", e["day"])}
    return sorted(d for d in days if (_age(d, today) or 0) >= 0)


def catchup(today: str | None = None) -> list[str]:
    today_d = dt.date.fromisoformat(today) if today else dt.datetime.now(dt.timezone.utc).date()
    notes: list[str] = []
    try:
        store = open_store()
    except PendingError as e:                              # store unreachable: the X catch-up still works
        store, notes = None, [f"saved videos unavailable: {e}"]
    for d in owed(today_d, store):
        folder = DAYS / d
        age = _age(d, today_d)
        if age is None or age > KEEP_DAYS:
            if store and store.remove(d):
                notes.append(f"{d}: older than {KEEP_DAYS} days, saved copy dropped without posting")
            continue
        if not (folder / review.REVIEW).is_file():
            notes.append(f"{d}: no committed review, skipped")
            continue
        if post.posted_day("youtube", d):
            platforms = ["x"]                              # only the short post is owed; it needs no video
        else:
            platforms = ["youtube", "x"]
            try:
                restore(folder, store or open_store())
            except PendingError as e:
                notes.append(str(e))
                continue
        rc = post.run(folder, platforms, today=today_d.isoformat())
        notes.append(f"{d}: post.py exit {rc}")
        if store and post.posted_day("youtube", d):
            store.remove(d)
    return notes or ["nothing owed"]


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "catchup":
        print("\n".join(catchup()))
    elif len(sys.argv) == 3 and sys.argv[1] in ("save", "restore", "done"):
        d = Path(sys.argv[2])
        try:
            print({"save": save, "done": done}.get(sys.argv[1], lambda x: restore(x) or f"{x.name}: restored")(d))
        except PendingError as e:
            sys.exit(f"pending: {e}")
    else:
        sys.exit(__doc__)
