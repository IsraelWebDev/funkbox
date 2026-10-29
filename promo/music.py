#!/usr/bin/env python3
"""A seeded, royalty-free-by-construction music bed (numpy only) for promo videos.

    python promo/music.py OUT.mp3 --seconds 22 --bpm 110 --seed 20261029

Same recipe as examples/mimic/make_music.py (kick, off-beat hats, clap on 2 and 4, plucky bass, pad), but the
tempo, length and seed are parameters, and the seed picks one of several chord progressions, so every day
sounds a little different and every file is reproducible.
"""
from __future__ import annotations

import argparse
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 44100
PROGRESSIONS = [  # (chords as MIDI triads, bass roots)
    ([(57, 60, 64), (53, 57, 60), (55, 59, 62), (52, 55, 59)], [45, 41, 43, 40]),    # Am F G Em
    ([(60, 64, 67), (55, 59, 62), (57, 60, 64), (53, 57, 60)], [48, 43, 45, 41]),    # C G Am F
    ([(62, 65, 69), (58, 62, 65), (60, 64, 67), (57, 60, 64)], [50, 46, 48, 45]),    # Dm Bb C Am
    ([(64, 67, 71), (60, 64, 67), (62, 66, 69), (59, 62, 66)], [52, 48, 50, 47]),    # Em C D Bm
]


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.clip(t / max(attack, 1e-4), 0, 1) * np.exp(-t / decay)


def _tone(freq: float, n: int) -> np.ndarray:
    ph = (np.arange(n) * freq / SR) % 1.0
    return 2.0 * ph - 1.0                                  # saw


def _lowpass(x: np.ndarray, a: float) -> np.ndarray:
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):                              # short buffers only (one note)
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
    plucks = {r: _lowpass(_tone(_midi(r), note_n), 0.08) * _env(note_n, 0.003, 0.12) for r in set(roots) | {x + 12 for x in roots}}
    bar_n = int(4 * beat * SR)
    pads = []
    for ch in chords:
        p = sum(_tone(_midi(m), bar_n) + 0.5 * _tone(_midi(m) * 1.004, bar_n) for m in ch) / 6.0
        sm = np.convolve(p, np.ones(40) / 40, mode="same")            # cheap low-pass for long buffers
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
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((synth(seconds, bpm, seed) * 32767).astype("<i2").tobytes())
    if out.suffix.lower() == ".wav":
        return wav
    from funkbox.media import require_ffmpeg
    subprocess.run([require_ffmpeg(), "-y", "-loglevel", "error", "-i", str(wav), "-b:a", "160k", str(out)], check=True)
    wav.unlink()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=22.0)
    ap.add_argument("--bpm", type=float, default=110.0)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    print(write(Path(a.out), a.seconds, a.bpm, a.seed))
