"""Speech to text for video, on this machine: faster-whisper with word timings.

    transcribe("talk.mp4", language="he") -> Transcript(path=talk.words.json, srt=talk.srt, words=[...])

Writes two files next to each other: ``<name>.words.json`` (every word with its start and end in seconds, the data the
caption, retake and pause steps read) and ``<name>.srt`` (one line per phrase, for players and review).

Nothing leaves the machine: the model runs on the CPU. The first use of a model downloads it from Hugging Face into
the usual cache. ``tiny`` is about 75 MB, ``small`` about 480 MB and ``large-v3`` about 3 GB.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from funkbox.video.clip import VideoError, _default_name, _out_path, _src, info

MODELS = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_MODEL = "small"  # English: T-2 on the laptop, small within 3% of large-v3 at a third of medium's time
HEBREW_MODEL = "large-v3"  # Hebrew: small got 46% of words wrong on a short sample, large-v3 got 18% (2026-11-22)
COMPUTE = "int8"  # CPU only (the laptop has no NVIDIA GPU); a bigger compute type gains little on speech
_LOADED: dict = {}


@dataclass
class Transcript:
    path: Path  # <name>.words.json
    srt: Path  # <name>.srt
    model: str
    language: str
    duration: float
    words: list[dict] = field(default_factory=list)

    def text(self) -> str:
        avg = sum(w["prob"] for w in self.words) / len(self.words) if self.words else 0.0
        out = (
            f"{len(self.words)} words, {self.duration:.1f}s, language {self.language}, model {self.model}, "
            f"average confidence {avg:.2f}"
        )
        if self.language == "he" and self.model != HEBREW_MODEL:
            out += f"\nHebrew is much more accurate on {HEBREW_MODEL}: rerun with model={HEBREW_MODEL} for captions."
        return out


def srt_time(t: float) -> str:
    """Seconds -> the SRT form ``HH:MM:SS,mmm``."""
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _load(model: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise VideoError("speech to text needs faster-whisper: pip install -r requirements-transcribe.txt") from None
    if model not in _LOADED:
        _LOADED[model] = WhisperModel(model, device="cpu", compute_type=COMPUTE)
    return _LOADED[model]


def default_model(language) -> str:
    """The model used when none is named: large-v3 for Hebrew, small for everything else (and for auto-detect)."""
    return HEBREW_MODEL if language == "he" else DEFAULT_MODEL


def transcribe(path, language=None, model=None, out_dir=None, name="") -> Transcript:
    """Transcribe the speech in a video. ``language`` is a code such as ``en`` or ``he``; left out, it is detected.
    ``model`` is one of MODELS; left out, it follows the language (see default_model). Never overwrites: a name that
    exists gets -2, -3 ..."""
    model = model or default_model(language)
    if model not in MODELS:
        raise VideoError(f"model must be one of {', '.join(MODELS)}")
    if language is not None and not (language.isalpha() and 2 <= len(language) <= 3):
        raise VideoError(f"language must be a code such as en or he, got {language!r}")
    src = _src(path)
    inf = info(src)  # refuses a file ffmpeg cannot read as video, before the model loads
    wm = _load(model)
    segments, meta = wm.transcribe(str(src), language=language or None, word_timestamps=True, vad_filter=True)
    phrases, words = [], []
    for seg in segments:  # the generator does the work: read it all before writing anything
        text = seg.text.strip()
        if text:
            phrases.append((float(seg.start), float(seg.end), text))
        for w in seg.words or []:
            token = w.word.strip()
            if token:
                words.append(
                    {
                        "text": token,
                        "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3),
                        "prob": round(float(w.probability), 3),
                    }
                )
    out = _out_path(src, out_dir, name or _default_name(src, "transcript"), suffix=".words.json")
    srt = out.with_name(out.name[: -len(".words.json")] + ".srt")
    data = {
        "source": src.name,
        "model": model,
        "language": meta.language,
        "duration": round(inf.duration, 3),
        "words": words,
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    srt.write_text(
        "".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(phrases, 1)),
        encoding="utf-8",
    )
    return Transcript(out, srt, model, meta.language, inf.duration, words)
