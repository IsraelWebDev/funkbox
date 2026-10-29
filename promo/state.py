"""Where the promo's working state lives: a clone of IsraelWebDev/funkbox-pending, outside the funkbox repo.

    <STATE_DIR>/days/<date>/   one folder per promo day: brief, copy, reel, caption, review, text audit
    <STATE_DIR>/posts.jsonl    every post attempt, one JSON line each
    <STATE_DIR>/<date>.mp4     a reviewed video waiting to be posted (promo/pending.py)

funkbox holds only the code. The state repo is public so that Buffer can fetch a waiting video by URL.
STATE_DIR defaults to ~/funkbox-pending; PROMO_STATE_DIR overrides it, PROMO_STATE_URL the clone source.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

STATE_REPO = os.environ.get("PROMO_STATE_REPO", "IsraelWebDev/funkbox-pending")
STATE_URL = os.environ.get("PROMO_STATE_URL", f"https://github.com/{STATE_REPO}.git")
STATE_DIR = Path(os.environ.get("PROMO_STATE_DIR", Path.home() / "funkbox-pending"))
DAYS = STATE_DIR / "days"
LOG = STATE_DIR / "posts.jsonl"
IDENT = ["-c", "user.name=funkbox promo", "-c", "user.email=promo@users.noreply.github.com"]
GITIGNORE = "days/*/assets/\ndays/*/final.mp4\ndays/*/final.png\ndays/*/.final.audio/\n"


class StateError(RuntimeError):
    pass


def git(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", "-C", str(cwd or STATE_DIR), *args], capture_output=True, text=True)
    if r.returncode:
        raise StateError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def has_ident() -> bool:
    return subprocess.run(["git", "-C", str(STATE_DIR), "config", "user.email"], capture_output=True).returncode == 0


def ensure() -> Path:
    """Clone the state repo (shallow) the first time, pull it afterwards. Returns STATE_DIR."""
    if (STATE_DIR / ".git").is_dir():
        if _has_head():                                   # a brand-new empty repo has nothing to pull yet
            git("pull", "-q", "--rebase", "--autostash", "origin", "main")
    else:
        STATE_DIR.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", STATE_URL, str(STATE_DIR)],
                           capture_output=True, text=True)
        if r.returncode:
            raise StateError(f"cannot clone {STATE_REPO} (attach it with push access): {r.stderr.strip()[:300]}")
    gi = STATE_DIR / ".gitignore"
    if not gi.is_file():
        gi.write_text(GITIGNORE, encoding="utf-8")
    DAYS.mkdir(exist_ok=True)
    return STATE_DIR


def _has_head() -> bool:
    return subprocess.run(["git", "-C", str(STATE_DIR), "ls-remote", "--exit-code", "--heads", "origin", "main"],
                          capture_output=True).returncode == 0


def commit(paths: list[str], msg: str) -> bool:
    """Stage ``paths`` (relative to STATE_DIR), commit and push. False when there was nothing to commit."""
    git("add", "--", ".gitignore", *paths)                # .gitignore rides along with the first commit
    if not git("diff", "--cached", "--name-only").strip():
        return False
    git(*([] if has_ident() else IDENT), "commit", "-q", "-m", msg)
    if _has_head():
        git("pull", "-q", "--rebase", "origin", "main")
    git("push", "-q", "origin", "HEAD:main")
    return True


if __name__ == "__main__":
    import sys
    try:
        if sys.argv[1:2] == ["init"]:
            print(ensure())
        elif len(sys.argv) >= 4 and sys.argv[1] == "commit":
            print("committed and pushed" if commit(sys.argv[3:], sys.argv[2]) else "nothing to commit")
        else:
            sys.exit("usage: state.py init | state.py commit MESSAGE PATH...   (paths relative to the state repo)")
    except StateError as e:
        sys.exit(f"state: {e}")
