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

from funkbox.video.clip import VideoError, _default_name, _out_path, _src, duration_of

MODELS = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_MODEL = "small"  # English: T-2 on the laptop, small within 3% of large-v3 at a third of medium's time
HEBREW_MODEL = "large-v3"  # Hebrew: small got 46% of words wrong on a short sample, large-v3 got 18% (2026-11-22)
COMPUTE = "int8"  # CPU only (the laptop has no NVIDIA GPU); a bigger compute type gains little on speech
_LOADED: dict = {}
#: ``prompt="fillers"``: Whisper tends to leave um and uh out of its transcript, so video_cutplan cannot propose them.
#: A first prompt that is itself full of fillers makes it write them down (openai/whisper discussion #1174). It can also
#: make the model add a filler it did not hear, so this is opt-in and every cut it leads to is still a proposal.
FILLER_PROMPTS = {
    "en": "Umm, let me think, uh, like, hmm... Okay, er, here's what I'm, um, thinking.",
    "he": "אממ, רגע, אה... אז, הממ, אה, זה מה שאני, אמ, חושב.",
}


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


def _check(model: str, language) -> None:
    if model not in MODELS:
        raise VideoError(f"model must be one of {', '.join(MODELS)}")
    if language is not None and not (language.isalpha() and 2 <= len(language) <= 3):
        raise VideoError(f"language must be a code such as en or he, got {language!r}")


def resolve_prompt(prompt, language) -> str | None:
    """The first prompt given to the model: None for blank, the filler prompt for ``fillers`` (Hebrew when the language
    is he, English otherwise), or the text as given."""
    prompt = (prompt or "").strip()
    if not prompt:
        return None
    if prompt.lower() == "fillers":
        return FILLER_PROMPTS["he" if language == "he" else "en"]
    return prompt


def _recognise(src: Path, language, model: str, prompt: str | None = None) -> tuple[list, list, str]:
    """Run the model over a file: (phrases, words, language). Reads the whole generator before returning."""
    wm = _load(model)
    kw = {"initial_prompt": prompt} if prompt else {}
    segments, meta = wm.transcribe(str(src), language=language or None, word_timestamps=True, vad_filter=True, **kw)
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
    return phrases, words, meta.language


def words_in(path, language=None, model=None) -> list[dict]:
    """The words of an audio or video file with their timings, and nothing written. For callers that keep their own
    copy, such as the reel's voice-over lines. Raises VideoError when faster-whisper is not installed."""
    model = model or default_model(language)
    _check(model, language)
    src = _src(path)
    duration_of(src)  # refuses a file ffmpeg cannot read, before the model loads
    return _recognise(src, language, model)[1]


def transcribe(path, language=None, model=None, out_dir=None, name="", prompt=None) -> Transcript:
    """Transcribe the speech in a video or an audio file. ``language`` is a code such as ``en`` or ``he``; left out, it
    is detected. ``model`` is one of MODELS; left out, it follows the language (see default_model). ``prompt`` is a
    first prompt for the model: ``fillers`` keeps um and uh in the transcript (see FILLER_PROMPTS), any other text is
    passed as given (names, spellings). The words.json records it. Never overwrites: a name that exists gets -2, -3 ..."""
    model = model or default_model(language)
    _check(model, language)
    src = _src(path)
    dur = duration_of(src)  # refuses a file ffmpeg cannot read, before the model loads
    first = resolve_prompt(prompt, language)
    phrases, words, lang = _recognise(src, language, model, first)
    out = _out_path(src, out_dir, name or _default_name(src, "transcript"), suffix=".words.json")
    srt = out.with_name(out.name[: -len(".words.json")] + ".srt")
    data = {
        "source": src.name,
        "model": model,
        "language": lang,
        "duration": round(dur, 3),
        "prompt": first,
        "words": words,
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    srt.write_text(
        "".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(phrases, 1)),
        encoding="utf-8",
    )
    return Transcript(out, srt, model, lang, dur, words)
