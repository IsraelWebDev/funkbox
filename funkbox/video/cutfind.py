"""Propose cuts for approval: long pauses (from the audio), repeated takes and filler words (from the transcript).

    cutplan("talk.mp4", words="talk.words.json") -> CutPlan(path=talk-cutplan.json, cuts=[{path, start, end}, ...])

Pauses come from ffmpeg's silencedetect on the sound, so their edges do not depend on the transcript. Repeated takes
come from the words: when the same run of words is said again soon after, the earlier take is proposed for removal and
the later one kept. Filler words ("um", "uh", ...) in the transcript are proposed for removal too. Nothing is rendered: the plan is a JSON file for a person to approve, then ``video_edit`` renders
its cuts. Each pause keeps a breath (``keep_s``) on either side so the cut does not clip the next word.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from funkbox.video.clip import VideoError, _default_name, _load_words, _out_path, _run, _src, info

NOISE_DB = -30.0  # silence = quieter than this (dB)
MIN_SILENCE_S = 0.35  # shorter quiet is a consonant or a breath, not a pause
KEEP_S = 0.25  # breath kept on each side of a removed pause
FILLER_TAIL_S = 0.18  # a filler's sound runs this long past its word's end time (measured on a synthetic talk)
MIN_PAUSE_S = 0.6  # pauses shorter than this stay
MIN_WORDS = 4  # a repeat of fewer words is coincidence
MAX_GAP_S = 30.0  # a retake is said within this many seconds of the take it replaces
#: spoken fillers proposed for removal when the transcript has them (matched on letters only, any case)
# English: um, uh, er, ah, hmm. Hebrew: the sounds people fill with (אה, אמ, הממ) and their stretched spellings. Only
# the sounds, never words that carry meaning, so "כאילו" and "בעצם" stay.
FILLERS = (
    "um",
    "umm",
    "uh",
    "uhm",
    "uhh",
    "er",
    "erm",
    "ah",
    "hmm",
    "mm",
    "אה",
    "אהה",
    "אההה",
    "אמ",
    "אמם",
    "אממ",
    "הממ",
    "ממ",
)

_START = re.compile(r"silence_start: (-?[\d.]+)")
_END = re.compile(r"silence_end: (-?[\d.]+)")


def parse_silences(err: str, duration: float) -> list[tuple[float, float]]:
    """(start, end) of each silence in silencedetect's log. A silence still open at the end runs to ``duration``."""
    out: list[tuple[float, float]] = []
    start = None
    for line in err.splitlines():
        m = _START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _END.search(line)
        if m and start is not None:
            out.append((start, min(float(m.group(1)), duration)))
            start = None
    if start is not None:
        out.append((start, duration))
    return out


def silences(path, noise_db: float = NOISE_DB, min_s: float = MIN_SILENCE_S) -> list[tuple[float, float]]:
    """The quiet stretches of a video's sound, at least ``min_s`` long."""
    if not -90 <= noise_db <= -10:
        raise VideoError("noise_db: between -90 and -10")
    if not 0.1 <= min_s <= 10:
        raise VideoError("min_silence_s: between 0.1 and 10 seconds")
    p = _src(path)
    dur = info(p).duration
    r = _run(["-i", str(p), "-vn", "-af", f"silencedetect=noise={noise_db:g}dB:d={min_s:g}", "-f", "null", "-"])
    if r.returncode != 0:
        raise VideoError(f"could not read the sound of {p.name}")
    return parse_silences(r.stderr.decode("utf-8", "replace"), dur)


def pause_removals(
    sil: list[tuple[float, float]], duration: float, keep_s: float = KEEP_S, min_pause_s: float = MIN_PAUSE_S
) -> list[tuple[float, float]]:
    """The middle of each long pause, leaving ``keep_s`` of quiet next to the speech on either side."""
    out = []
    for s, e in sil:
        if e - s < min_pause_s:
            continue
        a = s + keep_s if s > 0 else 0.0
        b = e - keep_s if e < duration else duration
        if b - a > 0.05:
            out.append((round(a, 3), round(b, 3)))
    return out


def _key(token: str) -> str:
    """A word for matching: letters and digits only, lower case (Hebrew and other scripts included)."""
    return re.sub(r"[^\w]", "", token).lower()


def retake_removals(
    words: list[dict], min_words: int = MIN_WORDS, max_gap_s: float = MAX_GAP_S
) -> list[tuple[float, float, str]]:
    """Earlier takes of a run of words that is said again within ``max_gap_s`` seconds: (start, end, text) to remove.
    The later take is kept. Matching is on the words, so the same words in another place are not a retake."""
    toks = []
    for x in words:
        k = _key(str(x["text"]))
        if k:
            toks.append((k, float(x["start"]), float(x["end"]), str(x["text"])))
    n, out, i = len(toks), [], 0
    while i < n:
        best = None  # (run length, index where the later take starts)
        for j in range(i + 1, n):
            if toks[j][1] - toks[i][2] > max_gap_s:
                break  # the words are in time order, so no later start is close enough
            length = 0
            while j + length < n and i + length < j and toks[i + length][0] == toks[j + length][0]:
                length += 1
            if length >= min_words and (best is None or length > best[0]):
                best = (length, j)
        if best is None:
            i += 1
            continue
        length, _ = best
        a, b = toks[i][1], toks[i + length - 1][2]
        out.append((round(a, 3), round(b, 3), " ".join(t[3] for t in toks[i : i + length])))
        i += length
    return out


#: words that introduce a quote: a filler right after one is the speaker quoting someone ("he said, uh, hi").
REPORTING = {"said", "says", "asked", "told", "wrote", "אמר", "אמרה", "אומר", "אומרת", "אמרתי", "אמרנו"}
#: words that make the next filler the subject of the sentence ("I say um", "the word uh"), not the speaker's filler
NAMING = {"say", "says", "saying", "word", "words", "sound", "sounds"}
#: joiners that put fillers in a list of sounds ("um or uh")
JOINERS = {"or", "and"}


def _kept(words: list[dict], keys: set[str]) -> dict[int, str]:
    """Filler words to leave in, as {index: reason}: quotes, named sounds, and fillers in a list of sounds."""
    out: dict[int, str] = {}
    for i in range(len(words)):
        if _key(str(words[i]["text"])) not in keys or i == 0:
            continue
        prev = _key(str(words[i - 1]["text"]))
        if prev in REPORTING:
            out[i] = f"quoted after '{prev}'"
        elif prev in NAMING:
            out[i] = f"named as a sound after '{prev}'"
        else:
            j = i - 2 if prev in JOINERS and i >= 2 else i - 1
            if j >= 0 and _key(str(words[j]["text"])) in keys:
                out[i] = "in a list of sounds"
    return out


def kept_fillers(words: list[dict], fillers=FILLERS) -> list[tuple[float, float, str, str]]:
    """Fillers the cut leaves in, for a person to check: (start, end, text, reason)."""
    keys = {_key(f) for f in fillers if _key(f)}
    kept = _kept(words, keys)
    return [
        (
            round(float(words[i]["start"]), 3),
            round(float(words[i]["end"]), 3),
            str(words[i]["text"]).strip(),
            kept[i],
        )
        for i in sorted(kept)
    ]


def filler_removals(words: list[dict], fillers=FILLERS) -> list[tuple[float, float, str]]:
    """Each word that is a filler ("um", "uh", ...), as (start, end, text) to remove. Matching ignores case and
    punctuation, so "Um," is a filler; a word that only contains one ("umbrella", "hummus") is not.

    The word's end time often falls before the sound does, so the cut runs FILLER_TAIL_S past it, but never into the
    next word: a cut that reached it would clip the start of that word. A filler that quotes someone, names a sound or sits in
    a list of sounds (see ``kept_fillers``) is left in."""
    keys = {_key(f) for f in fillers if _key(f)}
    spans = [(float(x["start"]), float(x["end"]), str(x["text"]).strip()) for x in words]
    starts = sorted(s for s, _, _ in spans)
    kept = _kept(words, keys)
    out = []
    for i, x in enumerate(words):
        if keys and i not in kept and _key(str(x["text"])) in keys:
            a, b = round(float(x["start"]), 3), round(float(x["end"]), 3)
            if b > a:
                nxt = next((s for s in starts if s >= float(x["end"]) - 1e-9 and s > float(x["start"])), None)
                tail = b + FILLER_TAIL_S
                if nxt is not None:
                    tail = min(tail, nxt)
                out.append((a, round(max(b, tail), 3), str(x["text"]).strip()))
    return out


def _merge(spans: list[tuple[float, float, str]]) -> list[tuple[float, float, list[str]]]:
    merged: list[list] = []
    for a, b, why in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
            merged[-1][2].append(why)
        else:
            merged.append([a, b, [why]])
    return [(a, b, reasons) for a, b, reasons in merged]


def _keep(removed: list[tuple[float, float, list[str]]], duration: float) -> list[tuple[float, float]]:
    keep, at = [], 0.0
    for a, b, _ in removed:
        if a > at + 0.05:
            keep.append((round(at, 3), round(a, 3)))
        at = max(at, b)
    if duration > at + 0.05:
        keep.append((round(at, 3), round(duration, 3)))
    return keep


@dataclass
class CutPlan:
    path: Path  # <name>.cutplan.json
    source: str
    duration: float
    removed: list[dict] = field(default_factory=list)  # [{"start", "end", "reasons"}]
    keep: list[tuple[float, float]] = field(default_factory=list)
    cuts: list[dict] = field(default_factory=list)  # [{"path", "start", "end"}], ready for video_edit
    kept: list[dict] = field(default_factory=list)  # [{"start", "end", "text", "reason"}]: fillers left in, to check

    def text(self) -> str:
        pauses = sum(1 for r in self.removed for x in r["reasons"] if x.startswith("pause"))
        retakes = sum(1 for r in self.removed for x in r["reasons"] if x.startswith("retake"))
        fillers = sum(1 for r in self.removed for x in r["reasons"] if x.startswith("filler"))
        gone = sum(r["end"] - r["start"] for r in self.removed)
        kept = sum(b - a for a, b in self.keep)
        return (
            f"{pauses} pause(s), {retakes} retake(s) and {fillers} filler word(s) proposed: {gone:.1f}s of {self.duration:.1f}s removed, "
            f"{kept:.1f}s kept. {len(self.kept)} filler word(s) kept (quotes), listed for you to check. "
            f"Not rendered: approve the plan, then video_edit renders its cuts.\nplan: {self.path}"
        )


def cutplan(
    path,
    words=None,
    keep_s: float = KEEP_S,
    min_pause_s: float = MIN_PAUSE_S,
    noise_db: float = NOISE_DB,
    min_silence_s: float = MIN_SILENCE_S,
    min_words: int = MIN_WORDS,
    max_gap_s: float = MAX_GAP_S,
    fillers=FILLERS,
    out_dir=None,
    name: str = "",
) -> CutPlan:
    """Propose what to cut from a talking-head video. Writes <name>.cutplan.json and renders nothing.
    ``fillers``: the filler words to propose for removal from ``words`` (default ``FILLERS``; empty turns it off)."""
    if not 0 <= keep_s <= 1.0:
        raise VideoError("keep_s: between 0 and 1 second")
    if not 1 <= min_words <= 50:
        raise VideoError("min_words: between 1 and 50")
    p = _src(path)
    inf = info(p)
    wl = _load_words(words) if words is not None else None
    sil = silences(p, noise_db, min_silence_s)
    spans: list[tuple[float, float, str]] = [
        (a, b, "pause") for a, b in pause_removals(sil, inf.duration, keep_s, min_pause_s)
    ]
    kept_list = []
    if wl:
        spans += [(a, b, f"retake: {t}") for a, b, t in retake_removals(wl, min_words, max_gap_s)]
        spans += [(a, b, f"filler: {t}") for a, b, t in filler_removals(wl, fillers or ())]
        kept_list = [
            {"start": a, "end": b, "text": t, "reason": why} for a, b, t, why in kept_fillers(wl, fillers or ())
        ]
    removed = _merge(spans)
    keep = _keep(removed, inf.duration)
    cuts = [{"path": str(p), "start": a, "end": b} for a, b in keep if b - a >= 0.1]
    out = _out_path(p, out_dir, name or _default_name(p, "cutplan"), suffix=".cutplan.json")
    data = {
        "source": p.name,
        "duration": round(inf.duration, 3),
        "settings": {
            "keep_s": keep_s,
            "min_pause_s": min_pause_s,
            "noise_db": noise_db,
            "min_silence_s": min_silence_s,
            "min_words": min_words,
            "max_gap_s": max_gap_s,
            "fillers": list(fillers or ()),
        },
        "removed": [{"start": a, "end": b, "reasons": r} for a, b, r in removed],
        "kept_fillers": kept_list,
        "keep": [list(k) for k in keep],
        "cuts": cuts,
        "note": "Proposal only. Nothing is rendered. Approve, then render the cuts with video_edit.",
    }
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return CutPlan(
        out,
        p.name,
        inf.duration,
        [{"start": a, "end": b, "reasons": r} for a, b, r in removed],
        keep,
        cuts,
        kept_list,
    )
