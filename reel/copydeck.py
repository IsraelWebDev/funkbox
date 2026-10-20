"""The copy deck (copy.json): the single source of on-screen and spoken text.

``text`` is drawn EXACTLY as written (no case change, no trimming). ``say`` is optional TTS text.
``pronounce`` substitutions apply to TTS input only. Tools may read the deck; nothing in funk
writes it (there is deliberately no write API here).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import ID_RE, Reel, ReelError


@dataclass(frozen=True)
class CopyLine:
    id: str
    text: str
    say: str | None = None


@dataclass
class CopyDeck:
    lines: dict[str, CopyLine] = field(default_factory=dict)
    brand_name: str = ""
    pronounce: dict[str, str] = field(default_factory=dict)
    path: str | None = None

    def text(self, copy_id: str) -> str:
        return self.lines[copy_id].text

    def say(self, copy_id: str) -> str:
        """TTS text: ``say`` (or ``text`` with newlines -> spaces) with pronunciations applied."""
        ln = self.lines[copy_id]
        raw = ln.say if ln.say is not None else re.sub(r"\s*\n\s*", " ", ln.text)
        for word in sorted(self.pronounce, key=len, reverse=True):
            raw = re.sub(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])",
                         lambda _m, w=word: self.pronounce[w], raw)
        return raw


def parse_copy(d: Any, path: str | None = None) -> CopyDeck:
    errs: list[str] = []
    if not isinstance(d, dict):
        raise ReelError(["copy deck: must be a JSON object"])
    for k in d:
        if k not in ("brand_name", "lines", "pronounce"):
            errs.append(f"copy deck: unknown key {k!r}")
    deck = CopyDeck(path=path)
    bn = d.get("brand_name", "")
    if not isinstance(bn, str):
        errs.append("copy deck: brand_name must be a string")
    else:
        deck.brand_name = bn
    lines = d.get("lines")
    if not isinstance(lines, dict) or not lines:
        errs.append("copy deck: `lines` must be a non-empty object {id: {text, say?}}")
        lines = {}
    for cid, v in lines.items():
        if not ID_RE.match(str(cid)):
            errs.append(f"copy deck: bad line id {cid!r} (use letters, digits, _ and -)")
            continue
        if not isinstance(v, dict):
            errs.append(f"copy deck: line {cid!r} must be an object")
            continue
        for k in v:
            if k not in ("text", "say"):
                errs.append(f"copy deck: line {cid!r} has unknown key {k!r}")
        t, s = v.get("text"), v.get("say")
        if not isinstance(t, str) or t == "":
            errs.append(f"copy deck: line {cid!r} needs a non-empty string `text`")
            continue
        if s is not None and not isinstance(s, str):
            errs.append(f"copy deck: line {cid!r}: `say` must be a string")
            s = None
        deck.lines[cid] = CopyLine(cid, t, s)
    pr = d.get("pronounce", {})
    if not isinstance(pr, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in pr.items()):
        errs.append("copy deck: `pronounce` must be {word: spoken form}")
    else:
        deck.pronounce = dict(pr)
    if errs:
        raise ReelError(errs)
    return deck


def load_copy(path: str | Path) -> CopyDeck:
    """Read copy.json as UTF-8 (a BOM is tolerated; nothing else is altered)."""
    p = Path(path)
    return parse_copy(json.loads(p.read_text(encoding="utf-8-sig")), str(p))


def used_ids(reel: Reel) -> list[tuple[str, str]]:
    """Every (where, copy_id) the reel references."""
    out: list[tuple[str, str]] = []
    for i, sh in enumerate(reel.shots):
        if sh.caption:
            out.append((f"shots[{i}] ({sh.id}).caption.copy_id", sh.caption.copy_id))
        if sh.vo:
            out.append((f"shots[{i}] ({sh.id}).vo", sh.vo))
    if reel.end_card:
        for slot, cid in reel.end_card.copy.items():
            out.append((f"end_card.copy.{slot}", cid))
    return out


def check(reel: Reel, deck: CopyDeck) -> list[str]:
    """Every copy_id used in shots, captions and end cards must exist in the deck."""
    errs = []
    for where, cid in used_ids(reel):
        if cid not in deck.lines:
            errs.append(f"{where}: unknown copy id {cid!r}; the deck has {sorted(deck.lines)}")
    return errs
