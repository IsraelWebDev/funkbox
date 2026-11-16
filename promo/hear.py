#!/usr/bin/env python3
"""Hear a promo's voiceover back: speech-to-text on each spoken line of final.mp4, compared with the approved text.

    python promo/hear.py $STATE/days/<date>      # writes <day>/heard.json; exit 1 if a line was not heard as written

The text audit proves which text was SENT to the voice (copy.json, byte for byte). It cannot prove what the voice
SAID: a misread word, a mangled name or a line lost under the music. The reviewer is an agent and cannot listen,
so this script listens for it. For every "voiceover" entry in final.text_audit.json it cuts that line's window out
of the posted video's own audio track, transcribes it locally (faster-whisper, model MODEL, CPU) and compares the
transcript with the spoken text: lower-case letters and digits only, spaces dropped (so "funk box" and "funkbox"
agree), similarity by difflib. A line passes at MIN_SIMILARITY or above.

heard.json: {"video_sha256", "model", "min_similarity", "ok", "lines": [{"copy_id", "expected", "heard",
"similarity", "ok"}]}. promo/lint.py requires it, for the current final.mp4, whenever reel.json has a voiceover.
promo/measure.py render runs it after rendering a voiceover reel.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

MODEL = "base.en"  # about 140 MB, fetched once; tiny.en misses short lines under music
MIN_SIMILARITY = 0.85
SR = 16000
PAD_BEFORE, PAD_AFTER = 0.15, 0.45  # seconds around the audit's window (the voice's tail decays past it)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def similarity(expected: str, heard: str) -> float:
    a, b = norm(expected), norm(heard)
    if not a:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def video_audio(video: Path):
    import numpy as np

    from funkbox.media import require_ffmpeg

    raw = subprocess.run(
        [
            require_ffmpeg(),
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SR),
            "-f",
            "f32le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.float32)


def spoken_lines(audit: dict) -> list[dict]:
    return [e for e in audit.get("entries", []) if e.get("kind") == "voiceover"]


def hear(day: Path, model_name: str = MODEL) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from faster_whisper import WhisperModel

    audit = json.loads((day / "final.text_audit.json").read_text(encoding="utf-8"))
    fps = float(audit.get("fps") or 30)
    video = day / "final.mp4"
    y = video_audio(video)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    lines = []
    for e in spoken_lines(audit):
        a = max(0, int((e["first_frame"] / fps - PAD_BEFORE) * SR))
        b = min(len(y), int(((e["last_frame"] + 1) / fps + PAD_AFTER) * SR))
        segs, _ = model.transcribe(
            y[a:b], language="en", beam_size=5, vad_filter=False, condition_on_previous_text=False
        )
        heard = " ".join(s.text.strip() for s in segs).strip()
        sim = similarity(e["text"], heard)
        lines.append(
            {
                "copy_id": e["copy_id"],
                "expected": e["text"],
                "heard": heard,
                "similarity": sim,
                "ok": sim >= MIN_SIMILARITY,
            }
        )
    return {
        "video_sha256": sha256(video),
        "model": f"faster-whisper {model_name}",
        "min_similarity": MIN_SIMILARITY,
        "ok": bool(lines) and all(x["ok"] for x in lines),
        "lines": lines,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    day = Path(argv[0])
    try:
        audit = json.loads((day / "final.text_audit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"hear: no text audit ({e}); render first (promo/measure.py render)", file=sys.stderr)
        return 1
    if not spoken_lines(audit):
        print("hear: no voiceover in this video")
        return 0
    rep = hear(day)
    (day / "heard.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for x in rep["lines"]:
        mark = "ok  " if x["ok"] else "MISS"
        print(f"{mark} {x['copy_id']}: {x['similarity']:.2f}  said {x['expected']!r}  heard {x['heard']!r}")
    print("hear: OK" if rep["ok"] else "hear: a spoken line was not heard as written (see heard.json)")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
