#!/usr/bin/env python3
"""Owner seeds: things the owner saw (on X, say) that the daily run may ride as a trend.

    python promo/seeds.py add --note "<what you saw>" --url URL [--url URL ...] [--seen YYYY-MM-DD]
    python promo/seeds.py list
    python promo/seeds.py next [--today YYYY-MM-DD]     # the oldest unused seed that is still fresh, as JSON, or "none"
    python promo/seeds.py use ID $DAY                    # the daily run marks the seed it rode

A seed lives in the state repo as one line of seeds.jsonl: {"id", "added", "seen", "note", "urls", "used"}.
The web search cannot read the owner's feed, so a seed is how a trend that lives there reaches the run. A seed is
the owner's pointer to a trend, not a licence: the review still checks the brief against REVIEW.md check 4, and a
seed is fresh for FRESH_DAYS days after it was seen.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import state  # noqa: E402

FRESH_DAYS = 21
NOTE_MIN, NOTE_MAX = 10, 400
ID_RE = re.compile(r"^sd-\d{4}$")


def path() -> Path:
    return state.STATE_DIR / "seeds.jsonl"


def load() -> list[dict]:
    p = path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def save(seeds: list[dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in seeds), encoding="utf-8")


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def add(note: str, urls: list[str], seen: str | None = None, today: str | None = None) -> dict:
    today_d = _date(today) if today else dt.date.today()
    seen_d = _date(seen) if seen else today_d
    note = (note or "").strip()
    if not NOTE_MIN <= len(note) <= NOTE_MAX:
        raise ValueError(f"note must be {NOTE_MIN}-{NOTE_MAX} characters: say what you saw, in your own words")
    if not urls or any(not re.match(r"^https?://\S+$", u) for u in urls):
        raise ValueError("give at least one http(s) link to a post or page that shows it (--url)")
    if seen_d > today_d:
        raise ValueError("seen is in the future")
    seeds = load()
    n = 1 + max([int(s["id"][3:]) for s in seeds if ID_RE.match(s.get("id", ""))] or [0])
    seed = {"id": f"sd-{n:04d}", "added": today_d.isoformat(), "seen": seen_d.isoformat(), "note": note,
            "urls": list(urls), "used": []}
    save(seeds + [seed])
    return seed


def fresh(seed: dict, today: dt.date) -> bool:
    return (today - _date(seed["seen"])).days <= FRESH_DAYS


def next_seed(today: str | None = None) -> dict | None:
    t = _date(today) if today else dt.date.today()
    for s in sorted(load(), key=lambda s: (s["seen"], s["id"])):
        if not s.get("used") and fresh(s, t):
            return s
    return None


def use(seed_id: str, day: str) -> dict:
    seeds = load()
    for s in seeds:
        if s["id"] == seed_id:
            if day not in s["used"]:
                s["used"].append(day)
            save(seeds)
            return s
    raise ValueError(f"no seed {seed_id!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--note", required=True)
    a.add_argument("--url", action="append", default=[])
    a.add_argument("--seen")
    sub.add_parser("list")
    n = sub.add_parser("next")
    n.add_argument("--today")
    u = sub.add_parser("use")
    u.add_argument("id")
    u.add_argument("day")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            s = add(args.note, args.url, args.seen)
            print(json.dumps(s, ensure_ascii=False))
            print("commit it: python promo/state.py commit \"seed\" seeds.jsonl")
        elif args.cmd == "list":
            today = dt.date.today()
            for s in load():
                status = "used" if s["used"] else ("open" if fresh(s, today) else "stale")
                print(f"{s['id']} [{status}] seen {s['seen']}: {s['note']}")
        elif args.cmd == "next":
            s = next_seed(args.today)
            print(json.dumps(s, ensure_ascii=False) if s else "none")
        else:
            s = use(args.id, Path(args.day).name)
            print(json.dumps(s, ensure_ascii=False))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
