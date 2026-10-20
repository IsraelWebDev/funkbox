"""Audio-reactive inputs (stdlib `wave` + numpy only).

For every video frame: an FFT of a Hann window centred on the frame time gives
iAudio = (bass 20-150 Hz, mid 150-2k, treble 2k-8k, rms), each normalised to 0..1 by its own
95th percentile over the clip, plus a 512-bin log-spaced magnitude row (bound as iChannel3).
PCM WAV only (8/16/24/32-bit integer); convert other formats with ffmpeg first.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

WINDOW = 2048
BANDS = ((20.0, 150.0), (150.0, 2000.0), (2000.0, 8000.0))
SPEC_BINS = 512
SPEC_LO, SPEC_HI = 20.0, 16000.0
FLOOR = 1e-4          # amplitude floor (-80 dB): silence in a band must not be amplified into noise


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Return (mono float32 samples in [-1, 1], sample rate)."""
    try:
        with wave.open(str(path), "rb") as w:
            n, ch, width, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(n)
    except wave.Error as e:
        raise ValueError(f"cannot read {Path(path).name} as PCM WAV ({e}); convert with "
                         "`ffmpeg -i in.mp3 out.wav`") from None
    if width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / float(1 << 23)
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    x = x.reshape(-1, ch).mean(axis=1) if ch > 1 else x
    return np.ascontiguousarray(x, dtype=np.float32), int(sr)


def wav_duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


@dataclass
class AudioFeatures:
    fps: int
    bands: np.ndarray      # (n, 4) float32: bass, mid, treble, rms, each 0..1
    spectrum: np.ndarray   # (n, 512) float32, 0..1, log-spaced SPEC_LO..SPEC_HI

    def _idx(self, frame: int) -> int:
        return int(min(max(frame, 0), len(self.bands) - 1))

    def vec(self, frame: int) -> tuple[float, float, float, float]:
        b = self.bands[self._idx(frame)]
        return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))

    def spec_row(self, frame: int) -> np.ndarray:
        return self.spectrum[self._idx(frame)]


def analyze(path: str | Path, fps: int, n_frames: int) -> AudioFeatures:
    """Features for frames 0..n_frames inclusive (the extra one serves t=1.0)."""
    x, sr = read_wav(path)
    n = n_frames + 1
    win = np.hanning(WINDOW).astype(np.float32)
    wsum = float(win.sum()) / 2.0
    freqs = np.fft.rfftfreq(WINDOW, 1.0 / sr)
    masks = [(freqs >= lo) & (freqs < hi) for lo, hi in BANDS]
    logf = np.geomspace(SPEC_LO, min(SPEC_HI, sr / 2.0 - 1.0), SPEC_BINS)
    # Edge frames would need samples beyond the clip. Padding (zeros or mirrored) puts a step or
    # a kink into the window and splatters broadband energy into every band, so instead the
    # window centre is clamped to keep the whole window inside the clip: edge frames see the
    # nearest full window (at most half a window, ~23 ms, away). Clips shorter than one window
    # are zero-padded.
    if len(x) >= WINDOW:
        pad, lo, hi = x, WINDOW // 2, len(x) - WINDOW // 2
    else:
        pad, lo, hi = np.pad(x, WINDOW), WINDOW + len(x) // 2, WINDOW + len(x) // 2
    raw = np.zeros((n, 4), np.float64)
    spec = np.zeros((n, SPEC_BINS), np.float32)
    for i in range(n):
        centre = int(min(max(round(i / fps * sr), lo), hi))
        seg = pad[centre - WINDOW // 2: centre + WINDOW // 2]
        mag = np.abs(np.fft.rfft(seg * win)) / wsum
        for k, m in enumerate(masks):
            raw[i, k] = float(np.sqrt(np.sum(mag[m] ** 2)))
        raw[i, 3] = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        spec[i] = np.interp(logf, freqs, mag).astype(np.float32)
    bands = np.zeros_like(raw)
    for k in range(4):
        p95 = max(float(np.percentile(raw[:, k], 95)), FLOOR)
        bands[:, k] = np.clip(raw[:, k] / p95, 0.0, 1.0)
    s = np.sqrt(spec)
    spec = np.clip(s / max(float(np.percentile(s, 99)), np.sqrt(FLOOR)), 0.0, 1.0)
    return AudioFeatures(fps, bands.astype(np.float32), spec.astype(np.float32))
