#!/usr/bin/env python3
"""Synthesize a short upbeat, royalty-free-by-construction music bed (numpy only) for the demo ad.

    python examples/mimic/make_music.py  -> examples/mimic/music.mp3 (or .wav if no mp3 encoder)

Four-on-the-floor kick, off-beat hats, a clap on 2 and 4, a plucky bass line and warm pad chords,
110 BPM, ~24 s, with a gentle fade at the end. Deterministic (seeded), so the file is reproducible.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 44100
BPM = 110
BARS = 11                     # 11 bars * 4 beats at 110 BPM = 24 s
BEAT = 60.0 / BPM
rng = np.random.default_rng(7)


def env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-t / decay)


def kick(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    f = 50 + 90 * np.exp(-t * 35)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.002, 0.18)


def hat(n: int) -> np.ndarray:
    noise = rng.standard_normal(n)
    hp = noise - np.concatenate([[0], noise[:-1]])          # crude high-pass
    return hp * env(n, 0.001, 0.03) * 0.25


def clap(n: int) -> np.ndarray:
    noise = rng.standard_normal(n)
    e = env(n, 0.001, 0.08) + 0.6 * np.roll(env(n, 0.001, 0.02), int(0.012 * SR))
    return noise * e * 0.35


def tone(freq: float, n: int, wave_kind: str = "saw") -> np.ndarray:
    t = np.arange(n) / SR
    if wave_kind == "sine":
        return np.sin(2 * np.pi * freq * t)
    ph = (freq * t) % 1.0
    return 2 * ph - 1


def lowpass(x: np.ndarray, alpha: float) -> np.ndarray:
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):            # one-pole; short buffers only
        acc += alpha * (v - acc)
        y[i] = acc
    return y


def midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def main() -> Path:
    total = int(BARS * 4 * BEAT * SR) + SR
    mix = np.zeros((total, 2), np.float64)

    def add(sig: np.ndarray, start_s: float, gain: float, pan: float = 0.0) -> None:
        i = int(start_s * SR)
        j = min(total, i + len(sig))
        l, r = gain * (1 - max(pan, 0)), gain * (1 + min(pan, 0))
        mix[i:j, 0] += sig[: j - i] * l
        mix[i:j, 1] += sig[: j - i] * r

    k, h, c = kick(int(0.4 * SR)), hat(int(0.08 * SR)), clap(int(0.25 * SR))
    chords = [(57, 60, 64), (53, 57, 60), (55, 59, 62), (52, 55, 59)]       # Am F G Em (A minor, upbeat)
    bass_roots = [45, 41, 43, 40]
    for bar in range(BARS):
        t0 = bar * 4 * BEAT
        drums_on = bar >= 1
        for b in range(4):
            tb = t0 + b * BEAT
            if drums_on or b == 0:
                add(k, tb, 0.9)
            if drums_on:
                add(h, tb + BEAT / 2, 0.6, pan=0.3)
                if b in (1, 3):
                    add(c, tb, 0.55, pan=-0.15)
        # bass: eighth-note plucks on the root, octave jump on the last eighth
        root = bass_roots[bar % 4]
        for e in range(8):
            n = root + (12 if e == 7 else 0)
            sig = lowpass(tone(midi(n), int(BEAT / 2 * SR)), 0.08) * env(int(BEAT / 2 * SR), 0.003, 0.12)
            add(sig, t0 + e * BEAT / 2, 0.55)
        # pad: soft chord for the whole bar
        n = int(4 * BEAT * SR)
        pad = sum(tone(midi(m), n, "saw") + 0.5 * tone(midi(m) * 1.004, n, "saw") for m in chords[bar % 4])
        pad = lowpass(pad / 6.0, 0.02) * np.minimum(1.0, np.arange(n) / (0.25 * SR))
        add(pad, t0, 0.18, pan=-0.2)
        add(np.roll(pad, int(0.011 * SR)), t0, 0.18, pan=0.2)

    # fade-out over the last 2 s, normalise to -1 dBFS peak
    fade = int(2.0 * SR)
    end = int(BARS * 4 * BEAT * SR)
    mix = mix[:end]
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None]
    mix *= 10 ** (-1 / 20) / max(1e-9, np.abs(mix).max())
    pcm = (mix * 32767).astype("<i2")

    wav = HERE / "music.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    ff = shutil.which("ffmpeg")
    if ff is None:
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ff = None
    if ff:
        mp3 = HERE / "music.mp3"
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k", str(mp3)])
        if r.returncode == 0:
            wav.unlink()
            return mp3
    return wav


if __name__ == "__main__":
    print(main())
    sys.exit(0)
