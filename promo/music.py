#!/usr/bin/env python3
"""The day's music bed for a promo video: a style that changes every day, made by funkbox, reproducible.

    python promo/music.py OUT.mp3 --seconds 30 --day 2026-11-16        # the day's style (see STYLES)
    python promo/music.py OUT.mp3 --seconds 30 --day 2026-11-16 --style lofi-keys
    python promo/music.py OUT.mp3 --seconds 22 --bpm 110 --seed 7    # the original synth bed (write())

Styles rotate by date, so two days in a row never share one. Each style is a funkbox score (funkbox/music:
General MIDI instruments from the GeneralUser GS SoundFont, fetched once, or the built-in bank when it cannot be
fetched), drawn by funkbox.music.vary from the style's brief and the day's seed: tempo, key, scale, chords,
instruments and drum pattern all vary inside the style. write() keeps the original numpy recipe (kick, off-beat
hats, clap on 2 and 4, plucky bass, pad; as in examples/mimic/make_music.py); the daily run no longer uses it.
All of it is rendered locally, so there is nothing to license.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import random
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
PROGRESSIONS = [  # (chords as MIDI triads, bass roots)
    ([(57, 60, 64), (53, 57, 60), (55, 59, 62), (52, 55, 59)], [45, 41, 43, 40]),  # Am F G Em
    ([(60, 64, 67), (55, 59, 62), (57, 60, 64), (53, 57, 60)], [48, 43, 45, 41]),  # C G Am F
    ([(62, 65, 69), (58, 62, 65), (60, 64, 67), (57, 60, 64)], [50, 46, 48, 45]),  # Dm Bb C Am
    ([(64, 67, 71), (60, 64, 67), (62, 66, 69), (59, 62, 66)], [52, 48, 50, 47]),  # Em C D Bm
]


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.clip(t / max(attack, 1e-4), 0, 1) * np.exp(-t / decay)


def _tone(freq: float, n: int) -> np.ndarray:
    ph = (np.arange(n) * freq / SR) % 1.0
    return 2.0 * ph - 1.0  # saw


def _lowpass(x: np.ndarray, a: float) -> np.ndarray:
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):  # short buffers only (one note)
        acc += a * (v - acc)
        y[i] = acc
    return y


def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def synth(seconds: float, bpm: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bars = max(2, int(np.ceil(seconds / (4 * beat))))
    chords, roots = PROGRESSIONS[seed % len(PROGRESSIONS)]
    total = int(bars * 4 * beat * SR) + SR
    mix = np.zeros((total, 2))

    def add(sig, start, gain, pan=0.0):
        i = int(start * SR)
        j = min(total, i + len(sig))
        mix[i:j, 0] += sig[: j - i] * gain * (1 - max(pan, 0))
        mix[i:j, 1] += sig[: j - i] * gain * (1 + min(pan, 0))

    t = np.arange(int(0.4 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(50 + 90 * np.exp(-t * 35)) / SR) * _env(len(t), 0.002, 0.18)
    hat = rng.standard_normal(int(0.08 * SR))
    hat = (hat - np.concatenate([[0], hat[:-1]])) * _env(len(hat), 0.001, 0.02)
    clap = rng.standard_normal(int(0.25 * SR)) * _env(int(0.25 * SR), 0.002, 0.06)
    note_n = int(beat / 2 * SR)
    plucks = {
        r: _lowpass(_tone(_midi(r), note_n), 0.08) * _env(note_n, 0.003, 0.12)
        for r in set(roots) | {x + 12 for x in roots}
    }
    bar_n = int(4 * beat * SR)
    pads = []
    for ch in chords:
        p = sum(_tone(_midi(m), bar_n) + 0.5 * _tone(_midi(m) * 1.004, bar_n) for m in ch) / 6.0
        sm = np.convolve(p, np.ones(40) / 40, mode="same")  # cheap low-pass for long buffers
        pads.append(sm * np.minimum(1.0, np.arange(bar_n) / (0.25 * SR)))
    for bar in range(bars):
        t0 = bar * 4 * beat
        on = bar >= 1
        for b in range(4):
            if on or b == 0:
                add(kick, t0 + b * beat, 0.9)
            if on:
                add(hat, t0 + b * beat + beat / 2, 0.5, 0.3)
                if b in (1, 3):
                    add(clap, t0 + b * beat, 0.45, -0.15)
        root = roots[bar % 4]
        for e in range(8):
            add(plucks[root + (12 if e == 7 else 0)], t0 + e * beat / 2, 0.55)
        add(pads[bar % 4], t0, 0.2, -0.2)
        add(np.roll(pads[bar % 4], int(0.011 * SR)), t0, 0.2, 0.2)
    end = int(seconds * SR)
    mix = mix[:end]
    fade = min(int(1.5 * SR), end // 3)
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return mix * (10 ** (-1 / 20) / max(1e-9, np.abs(mix).max()))


def write(out: Path, seconds: float, bpm: float, seed: int) -> Path:
    return _write_samples(out, synth(seconds, bpm, seed), SR)


# --------------------------------------------------------------------------- the day's style
#: style -> how to draw it. "brief" is a funkbox.music.vary brief (tempo band, keys, scales, swing, drums); "pad",
#: "bass" and "arp" are General MIDI programs picked per role (an arp of the chord in eighths, or none).
#: The order is the rotation: day N plays STYLE_ORDER[N % len], so neighbouring days always differ.
STYLES: dict[str, dict] = {
    "lofi-keys": {
        "brief": {
            "bpm_range": [80, 92],
            "keys": ["D", "F", "A", "C"],
            "scales": ["dorian", "minor"],
            "drum_patterns": ["half-time"],
            "swing": [0.2, 0.25],
        },
        "pad": [4, 5],
        "bass": [33, 32],
        "arp": [11, 8],
        "reverb": 0.4,
        "note": "lo-fi keys: electric piano chords, swung half-time drums, finger bass",
    },
    "bright-pop": {
        "brief": {
            "bpm_range": [108, 120],
            "keys": ["C", "G", "D", "E"],
            "scales": ["major", "mixolydian"],
            "drum_patterns": ["straight"],
            "swing": [0.0],
        },
        "pad": [48, 89],
        "bass": [38, 39],
        "arp": [80, 81],
        "reverb": 0.25,
        "note": "bright pop: strings or warm pad, synth bass, a square-lead arpeggio, straight four",
    },
    "funk-shuffle": {
        "brief": {
            "bpm_range": [96, 108],
            "keys": ["E", "A", "G", "Bb"],
            "scales": ["dorian", "mixolydian"],
            "drum_patterns": ["shuffle"],
            "swing": [0.1, 0.2],
        },
        "pad": [16, 4],
        "bass": [36, 34],
        "arp": [27, None],
        "reverb": 0.2,
        "note": "funk shuffle: organ or electric piano, slap bass, clean guitar, shuffled hats",
    },
    "cinematic": {
        "brief": {
            "bpm_range": [90, 100],
            "keys": ["D", "C", "A"],
            "scales": ["minor", "harmonic-minor"],
            "drum_patterns": ["half-time", "straight"],
            "swing": [0.0],
        },
        "pad": [48, 49],
        "bass": [43, 32],
        "arp": [46, 10],
        "reverb": 0.5,
        "note": "cinematic: string ensemble, contrabass, harp or music-box arpeggio",
    },
    "ambient-pulse": {
        "brief": {
            "bpm_range": [76, 86],
            "keys": ["F", "Eb", "Ab"],
            "scales": ["major", "pentatonic"],
            "drum_patterns": ["half-time"],
            "swing": [0.0],
        },
        "pad": [88, 91, 95],
        "bass": [38, 87],
        "arp": [98, 11],
        "reverb": 0.6,
        "note": "ambient pulse: wide synth pads, a soft half-time beat, a glassy arpeggio",
    },
}
STYLE_ORDER = list(STYLES)
SOUNDFONT = "GeneralUser GS"  # sampled instruments, fetched once; "builtin" needs nothing (tests use it)


def day_style(day: str | None, seed: int) -> str:
    """The style for a day folder name (YYYY-MM-DD): a rotation by date, so consecutive days never repeat."""
    try:
        n = dt.date.fromisoformat(day or "").toordinal()
    except ValueError:
        n = seed
    return STYLE_ORDER[n % len(STYLE_ORDER)]


def style_score(style: str, seconds: float, seed: int, soundfont: str | None = None) -> dict:
    """A funkbox score in ``style``, long enough for ``seconds`` (whole bars), drawn from the style and seed."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from funkbox.music.vary import generate

    st = STYLES[style]
    brief = {**st["brief"], "programs": [0]}  # roles are set below, per style
    hi = st["brief"]["bpm_range"][1]  # enough whole bars at the fastest tempo the style can draw
    bars = max(4, math.ceil(seconds * hi / 240.0) + 1)
    score = generate(brief, seed, 1, bars=bars, name=f"promo-{style}")[0]
    rng = random.Random(f"promo-music|{style}|{seed}")
    score["soundfont"], score["reverb"] = soundfont or SOUNDFONT, st["reverb"]
    tracks = {t["name"]: t for t in score["tracks"]}
    tracks["pad"]["program"] = rng.choice(st["pad"])
    tracks["bass"]["program"] = rng.choice(st["bass"])
    arp = rng.choice(st["arp"])
    if arp is not None:  # the pad's chord, broken into eighths an octave up: ^d ^d+2 ^d+4 ^d+2 x2 per bar
        chords = [c.strip()[1:].split("]")[0].split() for c in tracks["pad"]["notes"].split("|")]
        bars_txt = []
        for ch in chords:
            a, b, c = ch
            bars_txt.append(f"{a}/8 {b} {c} {b} {a} {b} {c} {b}")
        score["tracks"].insert(
            2, {"name": "arp", "program": arp, "octave": 5, "velocity": 50, "pan": 0.25, "notes": " | ".join(bars_txt)}
        )
    return score


def _write_samples(out: Path, x: np.ndarray, sr: int) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    if out.suffix.lower() == ".wav":
        return wav
    from funkbox.media import require_ffmpeg

    subprocess.run([require_ffmpeg(), "-y", "-loglevel", "error", "-i", str(wav), "-b:a", "160k", str(out)], check=True)
    wav.unlink()
    return out


def write_style(out: Path, seconds: float, seed: int, style: str) -> tuple[Path, dict]:
    """Render the bed in ``style``; returns the file and what was drawn (style, bpm, key, scale, programs)."""
    st = STYLES[style]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from funkbox.music.score import ScoreError, render

    try:
        score = style_score(style, seconds, seed)
        m = render(score)
    except (ScoreError, OSError) as e:  # the sampled bank could not be fetched: the built-in one needs nothing
        print(f"music: {e}; using the built-in instruments", file=sys.stderr)
        score = style_score(style, seconds, seed, soundfont="builtin")
        m = render(score)
    sr = 48000
    x = m.audio[: int(seconds * sr)].astype(np.float64)
    fade = min(int(1.5 * sr), len(x) // 3)
    x[-fade:] *= np.linspace(1, 0, fade)[:, None]
    x *= 10 ** (-1 / 20) / max(1e-9, np.abs(x).max())
    info = {
        "style": style,
        "bpm": score["bpm"],
        "key": score["key"],
        "scale": score["scale"],
        "programs": {t["name"]: t["program"] for t in score["tracks"] if "program" in t},
        "soundfont": m.soundfont,
        "note": st["note"],
    }
    return _write_samples(out, x, sr), info


TEMPO_TOLERANCE = 1.5  # BPM: the reel's beat detector must hear the tempo the score was written at
MAX_TRIES = 6
CLASSIC_BPM = (96, 110)  # the original bed's fallback band: the detector hears it reliably here


def day_track(out: Path, seconds: float, seed: int, day: str | None = None, style: str | None = None):
    """The day's music bed: ``style`` or the day's rotation pick. Returns (path, info).

    The reel snaps cuts to the beat grid it detects in this file, and shot lengths are counted in beats, so a bed
    whose tempo is heard at double or half speed would change every shot's length. A draw the detector does not
    hear at its written tempo is redrawn (seed, seed+1, ...; still reproducible); info["tries"] says how many."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from funkbox.reel.audio import detect_beats

    style = style or day_style(day, seed)
    heard = 0.0
    for k in range(MAX_TRIES):
        path, info = write_style(out, seconds, seed + k, style)
        heard = float(detect_beats(path).bpm)
        if abs(heard - info["bpm"]) <= TEMPO_TOLERANCE:
            return path, {**info, "tries": k + 1}
    # Last resort (seen only with the built-in bank): the original synth bed, at a tempo the detector hears.
    print(f"music: no {style} bed was heard at its own tempo (last {heard:.1f}); using synth-classic", file=sys.stderr)
    for k in range(MAX_TRIES):
        bpm = random.Random(f"{seed + k}-bpm").randint(*CLASSIC_BPM)
        path = write(out, seconds, bpm, seed + k)
        if abs(float(detect_beats(path).bpm) - bpm) <= TEMPO_TOLERANCE:
            note = "synth classic: the original synthesized kick, hats, clap, plucky bass and pad"
            return path, {"style": "synth-classic", "bpm": bpm, "note": note, "tries": MAX_TRIES + k + 1}
    raise RuntimeError(f"music: no bed in {2 * MAX_TRIES} draws was heard at its own tempo")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=22.0)
    ap.add_argument(
        "--bpm", type=float, default=None, help="the original synth bed at this tempo (styles draw their own)"
    )
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--day", default=None, help="YYYY-MM-DD: pick that day's style")
    ap.add_argument("--style", choices=STYLE_ORDER, default=None)
    a = ap.parse_args()
    if a.bpm is not None and a.style is None and a.day is None:
        print(write(Path(a.out), a.seconds, a.bpm, a.seed))
    else:
        path, info = day_track(Path(a.out), a.seconds, a.seed, a.day, a.style)
        print(path, info)
