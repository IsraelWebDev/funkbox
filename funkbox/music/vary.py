"""Music variety: a brief and a seed give a different score each time (MV-1).

A brief names the choices: a bpm band, keys, scales, instrument programs, drum patterns, swing and an
arrangement. For each (seed, index) the generator picks one value per field and writes a score in the
existing score schema, with the same number of bars as the base and 4/4 bars. Notes are scale degrees
(^1..^7), so a key or scale change moves the whole score, not only the tempo. The same brief, seed and
count give the same score bytes. A duplicate is redrawn, so every variant is different by content.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from .score import SCALES, check

ALLOWED = {"bpm_range", "keys", "scales", "programs", "drum_patterns", "swing", "arrangement"}
ARRANGEMENTS = ("build",)
MAX_VARIANTS = 12
MAX_DRAWS = 50  # redraws per variant before the brief is called too small
_NOTE = re.compile(r"[A-Ga-g][#b]?")

# 16 steps = one 4/4 bar of 16th notes (x hit, X accent, o soft, . rest).
DRUM_PATTERNS = {
    "straight": {"kick": "x.......x.......", "snare": "....x.......x...", "hat": "..o...o...o...o."},
    "half-time": {"kick": "x...............", "snare": "........x.......", "hat": "..o...o...o...o."},
    "shuffle": {"kick": "x.......x.......", "snare": "....x.......x...", "hat": "x.x.x.x.x.x.x.x."},
}
# Chord roots in scale degrees, one per bar, cycled across the score.
PROGRESSIONS = ((1, 6, 3, 7), (1, 4, 5, 1), (6, 4, 1, 5), (1, 5, 6, 4))


class BriefError(ValueError):
    """One message per problem in a brief or a request."""

    def __init__(self, problems):
        problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__(problems)
        self.problems = problems

    def __str__(self) -> str:
        return "; ".join(self.problems)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nonempty(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0


def validate_brief(brief: Any) -> list[str]:
    """Every problem in ``brief``, or an empty list."""
    if not isinstance(brief, dict):
        return ["brief: a JSON object"]
    errs: list[str] = []
    bad = set(brief) - ALLOWED
    if bad:
        errs.append(f"brief: unknown keys {sorted(bad)}")
    br = brief.get("bpm_range")
    if not (isinstance(br, list) and len(br) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in br)):
        errs.append("bpm_range: two numbers [low, high]")
    elif not 30 <= br[0] <= br[1] <= 240:
        errs.append("bpm_range: 30 <= low <= high <= 240")
    keys = brief.get("keys")
    if not _nonempty(keys):
        errs.append("keys: a non-empty list of note names like D, F#, Bb")
    else:
        errs += [f"keys: {k!r} is not a note name" for k in keys if not (isinstance(k, str) and _NOTE.fullmatch(k))]
    scales = brief.get("scales")
    if not _nonempty(scales):
        errs.append("scales: a non-empty list of scale names")
    else:
        errs += [f"scales: {s!r} is not a scale ({', '.join(SCALES)})" for s in scales if s not in SCALES]
    programs = brief.get("programs")
    if not _nonempty(programs):
        errs.append("programs: a non-empty list of General MIDI numbers 0..127")
    else:
        errs += [f"programs: {p!r} is not 0..127" for p in programs if not (_is_int(p) and 0 <= p <= 127)]
    drums = brief.get("drum_patterns")
    if not _nonempty(drums):
        errs.append(f"drum_patterns: a non-empty list of {', '.join(DRUM_PATTERNS)}")
    else:
        errs += [f"drum_patterns: {d!r} is not a pattern" for d in drums if d not in DRUM_PATTERNS]
    swing = brief.get("swing")
    if not _nonempty(swing):
        errs.append("swing: a non-empty list of values 0..0.5")
    else:
        errs += [
            f"swing: {s!r} is not 0..0.5"
            for s in swing
            if not (isinstance(s, (int, float)) and not isinstance(s, bool) and 0 <= s <= 0.5)
        ]
    if "arrangement" in brief and brief["arrangement"] not in ARRANGEMENTS:
        errs.append(f"arrangement: one of {', '.join(ARRANGEMENTS)}")
    return errs


def _draw(brief: dict, rng: random.Random, bars: int, name: str) -> dict:
    lo, hi = brief["bpm_range"]
    d_root = rng.choice(PROGRESSIONS)
    roots = [d_root[i % len(d_root)] for i in range(bars)]
    entry_bass = 1 if bars > 1 else 0  # "build": the pad starts alone, the bass joins in bar two
    pad = " | ".join(f"[^{d} ^{d + 2} ^{d + 4}]/1" for d in roots)
    bass = " | ".join(
        f"^{d}/4 ^{d}/4 ^{d + 4}/4 ^{d + 4}/4" if b >= entry_bass else "r/1" for b, d in enumerate(roots)
    )
    score = {
        "name": name,
        "bpm": rng.randint(int(lo), int(hi)),
        "meter": 4,
        "key": rng.choice(brief["keys"]),
        "scale": rng.choice(brief["scales"]),
        "bars": bars,
        "swing": float(rng.choice(brief["swing"])),
        "reverb": 0.3,
        "soundfont": "builtin",
        "tracks": [
            {"name": "pad", "program": rng.choice(brief["programs"]), "octave": 4, "velocity": 45, "notes": pad},
            {"name": "bass", "program": rng.choice(brief["programs"]), "octave": 2, "velocity": 60, "notes": bass},
            {"name": "drums", "drums": True, "pattern": dict(DRUM_PATTERNS[rng.choice(brief["drum_patterns"])])},
        ],
    }
    return score


def generate(brief: Any, seed: int, count: int, *, bars: int = 8, meter: int = 4, name: str = "variant") -> list[dict]:
    """``count`` distinct scores for ``brief`` and ``seed``. Raises ``BriefError`` listing every problem."""
    errs = validate_brief(brief)
    if not _is_int(seed):
        errs.append("seed: an integer")
    if not (_is_int(count) and 1 <= count <= MAX_VARIANTS):
        errs.append(f"count: 1..{MAX_VARIANTS}")
    if meter != 4:
        errs.append("meter: the generator writes 4/4 bars only")
    if not (_is_int(bars) and 1 <= bars <= 128):
        errs.append("bars: 1..128")
    if errs:
        raise BriefError(errs)
    out: list[dict] = []
    seen: set[str] = set()
    for index in range(1, count + 1):
        for attempt in range(MAX_DRAWS):
            rng = random.Random(f"funkbox-music-vary|{seed}|{index}|{attempt}")
            score = _draw(brief, rng, bars, f"{name}-{index:02d}")
            content = json.dumps({k: v for k, v in score.items() if k != "name"}, sort_keys=True)
            if content not in seen:
                seen.add(content)
                out.append(score)
                break
        else:
            raise BriefError(f"brief: could not find {count} different scores; widen the lists")
    for score in out:
        schema = check(score)
        if schema:  # the generator writes only valid scores; this guards the writer
            raise BriefError(schema)
    return out


def score_sha256(score: dict) -> str:
    """The SHA-256 of the score's canonical JSON (sorted keys, no spaces)."""
    text = json.dumps(score, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()