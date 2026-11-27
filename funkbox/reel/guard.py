"""Guards for a reel's text and intent (spec 3.2 and 3.3).

- ``project.pii.json`` (git-ignored) lists forbidden strings and regex rules. Copy lines that match fail validation.
  Reports name the copy id and the rule, never the matched value, so logs do not leak it.
- ``intake.json`` records what the project is for, what to leave out, and the voice choice. Each ``omit`` entry is
  enforced on every shot. ``project_auto_reel`` refuses to build until the intake exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PII_FILE = "project.pii.json"
INTAKE_FILE = "intake.json"
PROJECT_FILE = "project.json"
INTAKE_KEYS = {"audience", "length_s", "omit", "sources", "names", "voiceover"}
SOURCE_KINDS = ("footage", "recording", "generated")
VOICE_KEYS = {"enabled", "voice", "speed"}
MAX_TERMS = 200


class GuardError(ValueError):
    """A bad pii or intake file. Messages name the field, never a forbidden value."""


def find_upwards(start: str | Path | None, name: str, depth: int = 5) -> Path | None:
    """The nearest ``name`` in ``start`` or its parents (a few levels up), or None."""
    if not start:
        return None
    d = Path(start)
    if d.is_file():
        d = d.parent
    for _ in range(depth):
        f = d / name
        if f.is_file():
            return f
        if d.parent == d:
            break
        d = d.parent
    return None


def project_dir_of(path: str | Path | None) -> Path | None:
    """The project folder (the one holding project.json) that a reel or folder sits in, or None."""
    f = find_upwards(path, PROJECT_FILE)
    return f.parent if f else None


# --------------------------------------------------------------------------- #
# 3.2 PII guard
# --------------------------------------------------------------------------- #
def load_pii(start: str | Path | None) -> dict | None:
    """The nearest project.pii.json as {"forbidden": [str], "patterns": [(name, compiled)]}, or None."""
    f = find_upwards(start, PII_FILE)
    if f is None:
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as e:
        raise GuardError(f"{PII_FILE}: cannot read ({e.__class__.__name__})") from None
    if not isinstance(d, dict) or set(d) - {"forbidden", "patterns"}:
        raise GuardError(f"{PII_FILE}: expected an object with forbidden and patterns only")
    forb = d.get("forbidden", [])
    if not isinstance(forb, list) or len(forb) > MAX_TERMS or not all(isinstance(x, str) and x for x in forb):
        raise GuardError(f"{PII_FILE}.forbidden: expected a list of non-empty strings")
    pats = []
    raw = d.get("patterns", [])
    if not isinstance(raw, list):
        raise GuardError(f"{PII_FILE}.patterns: expected a list")
    for i, p in enumerate(raw):
        if not isinstance(p, dict) or set(p) != {"name", "regex"} or not isinstance(p["name"], str):
            raise GuardError(f"{PII_FILE}.patterns[{i}]: expected {{name, regex}}")
        try:
            pats.append((p["name"], re.compile(p["regex"])))
        except (re.error, TypeError):
            raise GuardError(f"{PII_FILE}.patterns[{i}]: regex does not compile") from None
    return {"forbidden": forb, "patterns": pats}


def pii_errors(deck, pii: dict | None) -> list[str]:
    """One error per copy line that matches a forbidden entry or a rule. Never includes the matched text."""
    if not pii or deck is None:
        return []
    errs: list[str] = []
    for cid, ln in deck.lines.items():
        texts = [ln.text] + ([ln.say] if ln.say else [])
        for i, term in enumerate(pii["forbidden"]):
            low = term.casefold()
            if any(low in t.casefold() for t in texts):
                errs.append(f"copy line {cid!r} matches forbidden entry #{i + 1} in {PII_FILE}")
        for name, rx in pii["patterns"]:
            if any(rx.search(t) for t in texts):
                errs.append(f"copy line {cid!r} matches rule {name!r} in {PII_FILE}")
    return errs


# --------------------------------------------------------------------------- #
# 3.3 Intake
# --------------------------------------------------------------------------- #
def validate_intake(d: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(d, dict):
        return ["intake: must be a JSON object"]
    bad = sorted(set(d) - INTAKE_KEYS)
    if bad:
        errs.append(f"intake: unknown keys {bad}")
    if not isinstance(d.get("audience"), str) or not d.get("audience", "").strip():
        errs.append("intake.audience: say who it is for (for example: tradeshow loop, customer training, internal)")
    ln = d.get("length_s")
    if isinstance(ln, bool) or not isinstance(ln, (int, float)) or not 5 <= ln <= 300:
        errs.append("intake.length_s: expected seconds from 5 to 300")
    omit = d.get("omit", [])
    if not isinstance(omit, list) or len(omit) > MAX_TERMS or not all(isinstance(x, str) and x.strip() for x in omit):
        errs.append("intake.omit: expected a list of non-empty strings (use [] when nothing is left out)")
    srcs = d.get("sources", [])
    if not isinstance(srcs, list) or not srcs or not set(srcs) <= set(SOURCE_KINDS):
        errs.append(f"intake.sources: expected a non-empty list from {', '.join(SOURCE_KINDS)}")
    names = d.get("names", [])
    if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
        errs.append("intake.names: expected a list of real or fictional names used in the copy")
    vo = d.get("voiceover", {"enabled": False})
    if not isinstance(vo, dict) or set(vo) - VOICE_KEYS or not isinstance(vo.get("enabled"), bool):
        errs.append("intake.voiceover: expected {enabled: true|false, voice?, speed?}")
    elif vo.get("enabled") and not isinstance(vo.get("voice"), str):
        errs.append("intake.voiceover.voice: name the voice when voiceover is on")
    return errs


def load_intake(project_dir: str | Path | None) -> dict | None:
    """The project's intake.json, checked, or None when there is none yet."""
    if not project_dir:
        return None
    f = Path(project_dir) / INTAKE_FILE
    if not f.is_file():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as e:
        raise GuardError(f"{INTAKE_FILE}: cannot read ({e.__class__.__name__})") from None
    errs = validate_intake(d)
    if errs:
        raise GuardError("; ".join(errs))
    return d


def write_intake(project_dir: str | Path, d: dict) -> Path:
    errs = validate_intake(d)
    if errs:
        raise GuardError("; ".join(errs))
    f = Path(project_dir) / INTAKE_FILE
    f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return f


def omit_errors(reel, deck, intake: dict | None) -> list[str]:
    """Every shot must avoid the intake's omit entries: its id, its source file name, and the text of the copy lines it
    uses. Reports the shot id and the entry number, not the entry text, for copy lines."""
    if not intake or not intake.get("omit"):
        return []
    errs: list[str] = []
    terms = [(i + 1, t.casefold()) for i, t in enumerate(intake["omit"])]
    for sh in reel.shots:
        fields = [sh.id]
        if sh.source.kind in ("image", "video"):
            fields.append(Path(sh.source.value).name)
        copy_ids = [c.copy_id for c in (sh.callouts or []) if getattr(c, "copy_id", "")]
        if sh.caption is not None:
            copy_ids.append(sh.caption.copy_id)
        if sh.vo:
            copy_ids.append(sh.vo)
        for cid in copy_ids:
            if deck is not None and cid in deck.lines:
                fields += [deck.lines[cid].text] + ([deck.lines[cid].say] if deck.lines[cid].say else [])
        for num, term in terms:
            if any(term in f.casefold() for f in fields):
                errs.append(f"shot {sh.id!r} matches intake omit entry #{num}")
    return errs
