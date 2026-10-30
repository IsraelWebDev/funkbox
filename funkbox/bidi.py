"""Right-to-left text without libraqm.

Pillow draws Hebrew in the right order only when it was built with libraqm (Linux and macOS wheels usually are; the
Windows wheels are not). Without it Pillow lays characters out left to right, so "צהל" would come out as "להצ".
``visual(line)`` reorders one line into display order with the core of the Unicode Bidirectional Algorithm
(strong types, numbers, neutrals between runs, level reordering, mirrored brackets), which is all Hebrew, Latin and
digits need. ``shown(line)`` applies it only when Pillow cannot. Text is still wrapped and audited in logical order;
only the drawing call gets the visual string. (Arabic also needs letter joining: use a Pillow with libraqm.)
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache

MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«"}


@lru_cache(maxsize=1)
def pillow_has_raqm() -> bool:
    try:
        from PIL import features
        return bool(features.check("raqm"))
    except Exception:                                  # pragma: no cover - very old Pillow
        return False


def _kind(ch: str) -> str:
    b = unicodedata.bidirectional(ch)
    if b in ("R", "AL"):
        return "R"
    if b == "L":
        return "L"
    if b in ("EN", "AN"):
        return "N"
    return "-"                                          # neutral or weak: decided by its neighbours


def is_rtl(text: str) -> bool:
    for ch in text:
        k = _kind(ch)
        if k in ("R", "L"):
            return k == "R"
    return False


def levels(line: str, base: int | None = None) -> list[int]:
    """Embedding level of every character (0, 1 or 2)."""
    if base is None:
        base = 1 if is_rtl(line) else 0
    kinds = [_kind(c) for c in line]
    # W7: numbers after a strong L (or at the start of an LTR paragraph) act as L
    last = "L" if base == 0 else "R"
    for i, k in enumerate(kinds):
        if k in ("L", "R"):
            last = k
        elif k == "N" and last == "L":
            kinds[i] = "L"
    # N1/N2: neutrals take the direction of the strong types around them (numbers count as R), else the paragraph's
    strong = ["R" if k == "N" else k for k in kinds]
    n = len(line)
    out = list(strong)
    i = 0
    while i < n:
        if strong[i] != "-":
            i += 1
            continue
        j = i
        while j < n and strong[j] == "-":
            j += 1
        before = strong[i - 1] if i > 0 else ("R" if base else "L")
        after = strong[j] if j < n else ("R" if base else "L")
        fill = before if before == after else ("R" if base else "L")
        for k in range(i, j):
            out[k] = fill
        i = j
    lv = []
    for k, d in zip(kinds, out):
        if base == 0:
            lv.append(2 if k == "N" else 1 if d == "R" else 0)
        else:
            lv.append(2 if k in ("L", "N") else 1 if d == "R" else 2)
    # L1: trailing whitespace goes back to the paragraph level
    j = n
    while j > 0 and line[j - 1].isspace():
        j -= 1
        lv[j] = base
    return lv


def visual(line: str) -> str:
    """``line`` in display (left-to-right) order."""
    if not line or not any(_kind(c) == "R" for c in line):
        return line
    lv = levels(line)
    chars = [MIRROR.get(c, c) if lv[i] % 2 else c for i, c in enumerate(line)]
    order = list(range(len(chars)))
    for level in range(max(lv), 0, -1):                 # L2: reverse every run at this level or higher
        i = 0
        while i < len(order):
            if lv[order[i]] >= level:
                j = i
                while j < len(order) and lv[order[j]] >= level:
                    j += 1
                order[i:j] = order[i:j][::-1]
                i = j
            else:
                i += 1
    return "".join(chars[k] for k in order)


def shown(line: str) -> str:
    """What to hand Pillow's draw.text so the line reads correctly with this Pillow."""
    return line if pillow_has_raqm() else visual(line)
