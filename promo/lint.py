#!/usr/bin/env python3
"""Mechanical checks on a promo day, run by the maker before review and again by the reviewer.

    python promo/lint.py $STATE/days/<date>          # exit 1 and one line per problem, or "lint: OK"
    python promo/lint.py feedback $STATE/days/<date> # add the day's feedback.json to $STATE/feedback.jsonl (once)

1. Captions vs. card text: on a shot whose source is a code card (assets.json `text_rect`), the caption box must
   not cover or touch the card's text at any point of the shot, after framing (push-ins move the text). This is
   what let a few letters of code peek out beside a caption.
2. Variety (brief.json): `format` and `selling_point` come from the lists below; the format differs from the
   previous FORMAT_GAP days, the selling point from the previous SELLING_GAP days, and the hook shares less than
   HOOK_OVERLAP of its words with any of the last 14 hooks. Earlier days are the other folders next to this one.
3. Product feedback: feedback.json exists and has the fields the owner reads (see PLAYBOOK.md).
4. Claims: every claim in brief.json cites a fact id that exists in FACTS.md, or an M-key in measured.json.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

FORMATS = {
    "trend-reaction": "react to something new in AI video, with funkbox's honest connection to it",
    "feature-spotlight": "one feature, shown working on screen",
    "how-to": "a job done in 3 real steps (real commands or shot-list lines)",
    "behind-the-scenes": "how this very video was made: the measured numbers from measured.json",
    "whats-new": "something that changed in funkbox since the last promo (measured.json commits)",
    "use-case": "a concrete job someone has: an app launch clip, an event poster, a product loop",
    "myth-vs-fact": "a common belief about AI or local video, answered with a fact",
    "tip": "one small trick in a shot list or a loop recipe",
}
SELLING_POINTS = {
    "S1": "free and open source, no accounts or credits (F1, F2)",
    "S2": "runs without an NVIDIA GPU: a laptop, or a CPU only (F3, M-machine)",
    "S3": "Claude drives it over MCP (F4, F5)",
    "S4": "captions exactly as approved: the copy lock and the text audit (F12, F13)",
    "S5": "one edit, many formats: 9:16, 16:9, 1:1, 4:5 (F9)",
    "S6": "screen recordings with push-ins and speed ramps that never alter what was recorded (F10, F14)",
    "S7": "cuts on the beat, music ducked under a local voiceover (F11)",
    "S8": "seamless shader loops, checked by machine (F6)",
    "S9": "posters with exact text, layered for GIMP (F7)",
    "S10": "pictures from a description on an ordinary laptop (F8)",
    "S11": "what's new this week (M-commits)",
}
FORMAT_GAP, SELLING_GAP, HOOK_OVERLAP = 2, 4, 0.6
FEEDBACK_FIELDS = ("observation", "suggestion", "evidence")
FACT_RE = re.compile(r"^- (F\d+|L\d+)\.", re.M)


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", s.lower()) if len(w) > 2}


# --------------------------------------------------------------------------- 1. captions vs card text
def caption_problems(day: Path) -> list[str]:
    from funkbox.reel import audio, captions, framing, timeline, validate
    cards = {a["path"]: a["text_rect"] for a in _json(day / "assets.json").get("assets", []) if a.get("text_rect")}
    if not cards or not (day / "reel.json").is_file():
        return []
    try:
        reel = validate.load_reel(day / "reel.json")
    except Exception as e:                                  # noqa: BLE001 - reel validate reports it properly
        return [f"reel.json does not load ({e}); run funkbox reel validate"]
    copy = _json(day / "copy.json").get("lines", {})
    W, H = reel.settings.width, reel.settings.height
    fonts = captions.Fonts(validate._res(reel.brand.font_caption, reel), validate._res(reel.brand.font_display, reel))
    try:                                                    # shot lengths only matter for keys given in seconds
        spans = {s.index: s.end - s.start for s in timeline.build_timeline(reel, beats=audio.beat_grid_for(reel)) if s.kind == "shot"}
    except Exception:                                       # noqa: BLE001
        spans = {}
    out = []
    for i, sh in enumerate(reel.shots):
        src = str(sh.source.value).replace("\\", "/") if sh.source.kind == "image" else ""
        rect = next((r for p, r in cards.items() if src.endswith(p)), None)
        if rect is None or sh.caption is None or sh.caption.copy_id not in copy:
            continue
        lay = captions.layout_block(sh.caption.copy_id, copy[sh.caption.copy_id]["text"],
                                    captions.load_style(sh.caption.style), fonts, reel.brand, W, H,
                                    anchor=sh.caption.anchor, safe_area=reel.settings.safe_area)
        m = 0.015 * H                                       # keep a little air around the box too
        cap = (lay.x0 - m, lay.y0 - m, lay.x0 + lay.box_w + m, lay.y0 + lay.box_h + m)
        secs = spans.get(i, 3.0)
        for u in (0.0, 0.25, 0.5, 0.75, 1.0):
            win = framing.window_for(sh.motion, sh.source, u, secs, 1080, 1920, W, H)
            sx = W / (win.x1 - win.x0)
            sy = H / (win.y1 - win.y0)
            tx0, ty0 = (rect[0] * 1080 - win.x0) * sx, (rect[1] * 1920 - win.y0) * sy
            tx1, ty1 = (rect[2] * 1080 - win.x0) * sx, (rect[3] * 1920 - win.y0) * sy
            if tx0 < cap[2] and cap[0] < tx1 and ty0 < cap[3] and cap[1] < ty1:
                out.append(f"shots[{i}] ({sh.id}): the caption {sh.caption.copy_id!r} sits on the card's text "
                           f"{u:.0%} into the shot; anchor it elsewhere, or frame the card so its text stays clear")
                break
    return out


# --------------------------------------------------------------------------- 2. variety
def earlier_briefs(day: Path, n: int = 14) -> list[dict]:
    days = sorted(p for p in day.parent.iterdir() if p.is_dir() and p.name < day.name and (p / "brief.json").is_file())
    return [_json(p / "brief.json") | {"_day": p.name} for p in days[-n:]][::-1]   # newest first


def variety_problems(day: Path) -> list[str]:
    b = _json(day / "brief.json")
    if not b:
        return ["brief.json is missing or not valid JSON"]
    out, prev = [], earlier_briefs(day)
    fmt, sp = b.get("format"), b.get("selling_point")
    if fmt not in FORMATS:
        out.append(f"brief.format must be one of {sorted(FORMATS)} (got {fmt!r})")
    if sp not in SELLING_POINTS:
        out.append(f"brief.selling_point must be one of {sorted(SELLING_POINTS, key=lambda k: int(k[1:]))} (got {sp!r})")
    for p in prev[:FORMAT_GAP]:
        if fmt and p.get("format") == fmt:
            out.append(f"format {fmt!r} was used on {p['_day']}; pick another (no repeat within {FORMAT_GAP} days)")
    for p in prev[:SELLING_GAP]:
        if sp and p.get("selling_point") == sp:
            out.append(f"selling point {sp} was the focus on {p['_day']}; pick another (no repeat within "
                       f"{SELLING_GAP} days)")
    hook = _words(b.get("hook", ""))
    for p in prev:
        other = _words(p.get("hook", ""))
        if hook and other and len(hook & other) / len(hook | other) >= HOOK_OVERLAP:
            out.append(f"hook is too close to {p['_day']}'s ({p.get('hook')!r})")
    if not str(b.get("novelty", "")).strip():
        out.append("brief.novelty is empty: say what a viewer of the last posts has NOT seen yet")
    return out


# --------------------------------------------------------------------------- 3. feedback, 4. claims
def feedback_problems(day: Path) -> list[str]:
    f = _json(day / "feedback.json")
    if not f:
        return ["feedback.json is missing: write the product feedback this run found (see PLAYBOOK.md)"]
    return [f"feedback.json needs a non-empty {k!r}" for k in FEEDBACK_FIELDS if not str(f.get(k, "")).strip()]


def claim_problems(day: Path, facts_md: Path = HERE / "FACTS.md") -> list[str]:
    known = set(FACT_RE.findall(facts_md.read_text(encoding="utf-8")))
    measured = set(_json(day / "measured.json").get("facts", {}))
    out = []
    for c in _json(day / "brief.json").get("claims", []):
        ids = re.findall(r"\b(?:F\d+|L\d+)\b|\bM-[\w-]+", str(c.get("fact", "")))
        bad = [x for x in ids if x not in known and x not in measured]
        if not ids or bad:
            out.append(f"claim {c.get('text')!r} cites {bad or 'nothing'}: use F/L ids from FACTS.md or M-keys from "
                       "measured.json")
    return out


def check(day: Path) -> list[str]:
    return caption_problems(day) + variety_problems(day) + feedback_problems(day) + claim_problems(day)


def add_feedback(day: Path, log: Path | None = None) -> bool:
    """Append the day's product feedback to the running backlog next to the day folders. False if already there."""
    log = log or day.parent.parent / "feedback.jsonl"
    f = _json(day / "feedback.json")
    if not f:
        raise SystemExit(f"{day}/feedback.json is missing")
    seen = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.is_file() else []
    if any(e.get("day") == day.name for e in seen):
        return False
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"day": day.name, **f}, ensure_ascii=False) + "\n")
    return True


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "feedback":
        print("feedback added" if add_feedback(Path(sys.argv[2])) else "feedback already in the backlog")
        sys.exit(0)
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    probs = check(Path(sys.argv[1]))
    print("\n".join(f"lint: {p}" for p in probs) or "lint: OK")
    sys.exit(1 if probs else 0)
