"""Where the promo's working state lives: a separate git repo, never the funkbox checkout.

    <STATE_DIR>/days/<date>/   one folder per promo day: brief, copy, reel, caption, review, text audit
    <STATE_DIR>/posts.jsonl    every post attempt, one JSON line each
    <STATE_DIR>/<date>.mp4     a reviewed video waiting to be posted (promo/pending.py)

Configuration (environment):
    PROMO_STATE_REPO   owner/name of the state repo on GitHub (required to save, catch up or commit)
    PROMO_STATE_DIR    where it is cloned (default ~/promo-state)
    PROMO_STATE_URL    clone URL (default https://github.com/<PROMO_STATE_REPO>.git)
If the repo is public, Buffer can fetch a waiting video from it by URL (see post.py VIDEO_URL_BASE).

    eval "$(python promo/state.py env)"    # clones or pulls it, then exports STATE and the funkbox folder settings
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

STATE_REPO = os.environ.get("PROMO_STATE_REPO", "")
STATE_URL = os.environ.get("PROMO_STATE_URL", f"https://github.com/{STATE_REPO}.git" if STATE_REPO else "")
STATE_DIR = Path(os.environ.get("PROMO_STATE_DIR", Path.home() / "promo-state"))
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
    elif not STATE_URL:
        raise StateError("no state repo configured: set PROMO_STATE_REPO=owner/name")
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
        elif sys.argv[1:2] == ["env"]:                    # day folders sit outside the checkout: allow them
            d = ensure()
            print(f"export STATE={d} FUNK_INPUTS={d / 'days'} FUNK_OUT={d / 'days'}")
        elif len(sys.argv) >= 4 and sys.argv[1] == "commit":
            print("committed and pushed" if commit(sys.argv[3:], sys.argv[2]) else "nothing to commit")
        else:
            sys.exit("usage: state.py init | state.py env | state.py commit MESSAGE PATH...   (paths relative to the state repo)")
    except StateError as e:
        sys.exit(f"state: {e}")
