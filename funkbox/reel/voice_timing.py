"""Word timings for a voice-over line, from a transcript of the synthesized speech.

A reel's captions used to be spread evenly over a voiced shot (characters per second), so the highlighted word could
drift from the voice. Here each synthesized line is transcribed once with faster-whisper, the transcript is cached beside
the wav, and its words are aligned to the approved caption text (captions.word_timings), so each word appears when it is
spoken.

Without faster-whisper (the reel install does not include it) or when nothing is heard, this returns None and the
caption keeps the estimate. The shot length never depends on it: that is still the voice's own duration.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from funkbox.reel import captions

_HEBREW = re.compile(r"[֐-׿]")


def _language(say: str) -> str:
    """The language to transcribe a line in: Hebrew when it has Hebrew letters, else English."""
    return "he" if _HEBREW.search(say) else "en"


def line_words(wav: Path, say: str) -> list[dict] | None:
    """The transcript words of a synthesized line, cached as ``<wav>.words.json``. None when faster-whisper is not
    installed (nothing is cached then, so a later install is used) or when no word was heard."""
    cache = wav.with_name(wav.stem + ".words.json")
    if cache.exists():
        try:
            words = json.loads(cache.read_text(encoding="utf-8")).get("words") or []
        except (ValueError, AttributeError):
            words = None  # a damaged cache is redone below
        if words is not None:
            return words or None
    from funkbox.video.clip import VideoError
    from funkbox.video.transcribe import words_in

    lang = _language(say)
    try:
        words = words_in(wav, language=lang)
    except VideoError:
        return None  # faster-whisper is not installed, or the wav cannot be read: keep the estimate
    cache.write_text(json.dumps({"language": lang, "words": words}, ensure_ascii=False), encoding="utf-8")
    return words or None


def caption_timings(
    display: str, wav: Path, say: str, vo_start: float, shot_start: float, caption_start: float
) -> list[tuple[float, float]] | None:
    """(start, end) of each word of the caption ``display``, in seconds from the caption's own start.

    ``vo_start`` and ``shot_start`` are timeline seconds; ``caption_start`` is how long after the shot starts the caption
    appears (the caption delay, plus any hook title on shot 1). The transcript times are from the start of the wav, so
    they are moved by where the voice starts within the shot."""
    words = line_words(wav, say)
    if not words:
        return None
    timed = captions.word_timings(display, words)
    if timed is None:
        return None
    shift = (vo_start - shot_start) - caption_start
    return [(s + shift, e + shift) for s, e in timed]
