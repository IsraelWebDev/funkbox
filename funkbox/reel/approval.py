"""Copy: draft -> approve -> lock.  This is the ONLY module that writes ``copy.json``.

* ``propose_copy`` validates and writes ``copy.draft.json`` (the agent may do this) and returns the
  exact numbered listing the user must see verbatim.
* ``approve`` copies the draft's exact bytes to ``copy.json`` iff the caller supplies the sha256 of the
  current draft, then records ``copy_lock = sha256(copy.json)``, the time and the method in project.json.
  Who is allowed to call it (elicitation click / explicit chat confirmation / a person at a terminal) is
  decided by the callers (reel_mcp.approve_copy, funkbox project approve-copy), never by the agent's arguments
  alone.
* ``relock`` (user-run CLI only) re-locks a copy.json the user edited by hand.
* The v3 validator then refuses any reel whose copy.json no longer matches the lock (project.py).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
from pathlib import Path

from . import project as P
from .copydeck import parse_copy
from .schema import Brand, ReelError

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
MAX_TEXT = 90
MAX_SAY = 200
MAX_LINES = 40
DRAFT = "copy.draft.json"
APPROVED = "copy.json"


class ApprovalError(P.ProjectError):
    pass


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _fit_context(slug: str):
    """Fonts + Brand of the project (defaults when there is no brand yet) for the caption fit check."""
    from funkbox.config import resolve_path

    from . import captions
    d = P.project_dir(slug)
    b = P.load_meta(slug).get("brand") or {}
    brand = Brand(primary=b.get("primary", "#c8102e"), secondary=b.get("secondary", "#1a1a1a"),
                  text=b.get("text", "#ffffff"), font_caption=b.get("font_caption", "fonts/Oswald-SemiBold.ttf"),
                  font_display=b.get("font_display", "fonts/DMSerifDisplay-Regular.ttf"))
    fonts = captions.Fonts(resolve_path(brand.font_caption, d), resolve_path(brand.font_display, d))
    return fonts, brand


def validate_lines(slug: str, lines: object, pronounce: object = None, brand_name: object = None,
                   style: str = "red-box") -> tuple[dict, list[str]]:
    """Returns (copy-deck dict, errors). Every problem is listed, naming the line id."""
    from . import captions
    errs: list[str] = []
    deck_lines: dict[str, dict] = {}
    if not isinstance(lines, dict) or not lines:
        return {}, ["lines must be a non-empty object {id: {text, say?}}"]
    if len(lines) > MAX_LINES:
        errs.append(f"too many lines ({len(lines)} > {MAX_LINES})")
    try:
        fonts, brand = _fit_context(slug)
    except (ValueError, FileNotFoundError) as e:
        return {}, [f"brand fonts: {e}"]
    for cid, v in lines.items():
        if not isinstance(cid, str) or not ID_RE.match(cid):
            errs.append(f"line id {cid!r}: must match {ID_RE.pattern} (lowercase letters, digits, _)")
            continue
        if isinstance(v, str):
            errs.append(f"line {cid!r}: give {{\"text\": ...}}, not a bare string")
            continue
        if not isinstance(v, dict) or not set(v) <= {"text", "say"}:
            errs.append(f"line {cid!r}: must be an object with `text` and optional `say` only")
            continue
        n_before = len(errs)
        t, s = v.get("text"), v.get("say")
        if not isinstance(t, str) or not t.strip():
            errs.append(f"line {cid!r}: `text` must be a non-empty string")
            continue
        if len(t) > MAX_TEXT:
            errs.append(f"line {cid!r}: text is {len(t)} characters; the limit is {MAX_TEXT}")
        if t != t.strip():
            errs.append(f"line {cid!r}: text has leading/trailing whitespace (it is drawn exactly as written)")
        if re.search(r"[\x00-\x09\x0b-\x1f\x7f]", t):
            errs.append(f"line {cid!r}: text contains control characters")
        if s is not None:
            if not isinstance(s, str) or not s.strip():
                errs.append(f"line {cid!r}: `say` must be a non-empty string when given")
                s = None
            elif len(s) > MAX_SAY:
                errs.append(f"line {cid!r}: say is longer than {MAX_SAY} characters")
        if len(errs) == n_before:
            fit = captions.check_caption(cid, t, style, "bottom", fonts, brand, 1080, 1920, "reels")
            if fit:
                errs.append(fit + " (shorten the line)")
        deck_lines[cid] = {"text": t} if s is None else {"text": t, "say": s}
    pr = {} if pronounce is None else pronounce
    if not isinstance(pr, dict) or not all(isinstance(k, str) and isinstance(x, str) and k and x for k, x in pr.items()) \
            or len(pr) > 20 or any(len(k) > 40 or len(x) > 60 for k, x in pr.items()):
        errs.append("pronounce must be {word: spoken form} (at most 20 short entries); it changes speech only")
        pr = {}
    if brand_name is not None and (not isinstance(brand_name, str) or len(brand_name) > 60):
        errs.append("brand_name must be a short string")
        brand_name = None
    bn = brand_name if brand_name is not None else P.load_meta(slug).get("name", "")
    deck = {"brand_name": bn, "lines": deck_lines, "pronounce": pr}
    if not errs:
        try:
            parse_copy(deck)
        except ReelError as e:
            errs += e.errors
    return deck, errs


def _dump(deck: dict) -> bytes:
    return (json.dumps(deck, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def listing(deck: dict) -> str:
    """The exact numbered listing the user must see. Strings are JSON-quoted so every character
    (including line breaks and spacing) is unambiguous."""
    out = []
    for n, (cid, ln) in enumerate(deck["lines"].items(), 1):
        out.append(f"{n}. {cid}")
        out.append(f"   text: {json.dumps(ln['text'], ensure_ascii=False)}")
        if ln.get("say") is not None:
            out.append(f"   say:  {json.dumps(ln['say'], ensure_ascii=False)}")
    if deck.get("pronounce"):
        out.append("pronounce (speech only): " + json.dumps(deck["pronounce"], ensure_ascii=False))
    return "\n".join(out)


SHOW_VERBATIM = ("Show the numbered listing above to the user VERBATIM (do not summarise or reword it) and ask whether "
                 "they approve exactly these lines. Only these approved lines can ever appear on screen or in the "
                 "voice-over. If they want a change, call propose_copy again; then call approve_copy with the NEW "
                 "draft_sha256.")


def propose_copy(slug: str, lines: dict, pronounce: dict | None = None, brand_name: str | None = None) -> dict:
    d = P.project_dir(slug)
    deck, errs = validate_lines(slug, lines, pronounce, brand_name)
    if errs:
        raise ReelError(["propose_copy rejected the draft (nothing was written):"] + [f"  {e}" for e in errs])
    data = _dump(deck)
    _atomic_write(d / DRAFT, data)
    sha = P.sha256_bytes(data)
    P.advance_status(slug, "copy-drafted")
    return {"draft_sha256": sha, "listing": listing(deck), "lines": len(deck["lines"]),
            "path": str(d / DRAFT), "instruction": SHOW_VERBATIM}


def propose_text(res: dict, slug: str) -> str:
    return (f"DRAFT copy for {slug!r}, {res['lines']} line(s). Nothing is approved yet.\n\n{res['listing']}\n\n"
            f"draft_sha256: {res['draft_sha256']}\n\n{res['instruction']}")


# --------------------------------------------------------------------------- #
# Approval (the only writer of copy.json)
# --------------------------------------------------------------------------- #
def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_draft(slug: str) -> tuple[bytes, dict]:
    p = P.project_dir(slug) / DRAFT
    if not p.is_file():
        raise ApprovalError(f"project {slug!r} has no draft; call propose_copy first")
    data = p.read_bytes()
    return data, json.loads(data.decode("utf-8-sig"))


def prepare_approval(slug: str, draft_sha256: str) -> tuple[str, str]:
    """Check the caller's hash against the CURRENT draft and return (listing, sha). Stale -> error."""
    data, deck = read_draft(slug)
    sha = P.sha256_bytes(data)
    if not isinstance(draft_sha256, str) or draft_sha256.strip().lower() != sha:
        raise ApprovalError("stale draft_sha256: the draft changed after the listing you were given. Call propose_copy "
                            "again (or copy_diff), show the NEW listing to the user and use its draft_sha256. "
                            "Nothing was approved.")
    return listing(deck), sha


def approve(slug: str, draft_sha256: str, method: str) -> dict:
    """Write copy.json from the draft's exact bytes and lock it. ``method`` is recorded
    ("elicitation" | "chat" | "cli"); the caller is responsible for having obtained consent that way."""
    if method not in ("elicitation", "chat", "cli"):
        raise ApprovalError(f"unknown approval method {method!r}")
    d = P.project_dir(slug)
    data, deck = read_draft(slug)
    sha = P.sha256_bytes(data)
    if not isinstance(draft_sha256, str) or draft_sha256.strip().lower() != sha:
        raise ApprovalError("stale draft_sha256: the draft changed since the user saw it; nothing was approved. "
                            "Show the new listing and ask again.")
    _, errs = validate_lines(slug, deck.get("lines"), deck.get("pronounce"), deck.get("brand_name", ""))
    if errs:
        raise ReelError(["the draft no longer validates:"] + errs)
    cp = d / APPROVED
    if cp.is_symlink():
        raise ApprovalError("copy.json is a symlink; refusing to write through it")
    _atomic_write(cp, data)
    lock = P.sha256_file(cp)
    meta = P.load_meta(slug)
    meta["copy_lock"] = lock
    meta["copy_approval"] = {"at": P.now_iso(), "method": method, "draft_sha256": sha}
    P.save_meta(slug, meta)
    P.advance_status(slug, "copy-approved")
    return {"copy_lock": lock, "method": method, "lines": len(deck["lines"])}


def relock(slug: str, method: str = "cli") -> dict:
    """User-run CLI path: a person edited copy.json by hand and re-approves it as it now stands."""
    d = P.project_dir(slug)
    cp = d / APPROVED
    if cp.is_symlink() or not cp.is_file():
        raise ApprovalError(f"project {slug!r} has no copy.json to approve")
    try:
        deck = parse_copy(json.loads(cp.read_text(encoding="utf-8-sig")), str(cp))
    except (ValueError, ReelError) as e:
        raise ApprovalError(f"copy.json is not a valid copy deck: {e}") from None
    from . import captions
    fonts, brand = _fit_context(slug)
    bad = []
    for cid, ln in deck.lines.items():
        err = captions.check_caption(cid, ln.text, "red-box", "bottom", fonts, brand, 1080, 1920, "reels")
        if err:
            bad.append(err)
    if bad:
        raise ApprovalError("copy.json has lines that do not fit: " + "; ".join(bad))
    lock = P.sha256_file(cp)
    meta = P.load_meta(slug)
    meta["copy_lock"] = lock
    meta["copy_approval"] = {"at": P.now_iso(), "method": method, "draft_sha256": None}
    P.save_meta(slug, meta)
    P.advance_status(slug, "copy-approved")
    return {"copy_lock": lock, "method": method, "lines": len(deck.lines)}


def approved_listing(slug: str) -> str:
    d = P.project_dir(slug)
    deck = json.loads((d / APPROVED).read_text(encoding="utf-8-sig"))
    return listing({"lines": deck.get("lines", {}), "pronounce": deck.get("pronounce", {})})


# --------------------------------------------------------------------------- #
def copy_diff(slug: str) -> str:
    d = P.project_dir(slug)
    dp, ap = d / DRAFT, d / APPROVED
    draft = json.loads(dp.read_text(encoding="utf-8-sig")) if dp.is_file() else None
    appr = json.loads(ap.read_text(encoding="utf-8-sig")) if ap.is_file() else None
    state = P.copy_lock_error(slug)
    head = [f"copy diff for {slug!r}",
            "approved copy.json: " + ("none yet" if appr is None else ("LOCKED and intact" if state is None else f"PROBLEM, {state}")),
            "draft: " + ("none" if draft is None else f"sha256 {P.sha256_file(dp)}")]
    if draft is None:
        return "\n".join(head + ["nothing to compare: no draft"])
    a_lines = (appr or {}).get("lines", {})
    d_lines = draft.get("lines", {})
    out = head[:]
    same = 0
    for cid in list(d_lines) + [c for c in a_lines if c not in d_lines]:
        a, b = a_lines.get(cid), d_lines.get(cid)
        if a is None:
            out.append(f"+ {cid}: NEW  text {json.dumps(b['text'], ensure_ascii=False)}"
                       + (f"  say {json.dumps(b['say'], ensure_ascii=False)}" if b.get("say") else ""))
        elif b is None:
            out.append(f"- {cid}: REMOVED (approved text {json.dumps(a['text'], ensure_ascii=False)})")
        elif a == b:
            same += 1
            out.append(f"= {cid}: unchanged")
        else:
            out.append(f"~ {cid}: CHANGED")
            for k in ("text", "say"):
                if a.get(k) != b.get(k):
                    out.append(f"    {k} approved: {json.dumps(a.get(k), ensure_ascii=False)}")
                    out.append(f"    {k} draft:    {json.dumps(b.get(k), ensure_ascii=False)}")
                    if k == "text" and a.get(k) and b.get(k):
                        sm = difflib.SequenceMatcher(None, a[k], b[k])
                        out.append("    changes: " + "; ".join(
                            f"{op} {a[k][i1:i2]!r}->{b[k][j1:j2]!r}" for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"))
    if (appr or {}).get("pronounce", {}) != draft.get("pronounce", {}):
        out.append(f"~ pronounce: approved {json.dumps((appr or {}).get('pronounce', {}))} -> draft {json.dumps(draft.get('pronounce', {}))}")
    out.append(f"{same} line(s) unchanged; the draft is NOT in effect until approve_copy succeeds.")
    return "\n".join(out)
