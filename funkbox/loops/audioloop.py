"""Audio-loop mode: cut a short section of a track that a loop can react to and still loop.

A loop with ``settings.audio`` normally plays the whole track once, so its last frame cannot match its first and
the loop check is skipped. ``cut_section`` takes a whole number of bars (3 to 8 s) from the track's detected beat
grid, rounds it to whole video frames, crossfades the audio where it wraps, and writes it as a 16-bit WAV. A recipe
that uses that file with ``settings.audio_loop`` analyses it as a circle, so frame N equals frame 0 and the loop
check stays on.

The detector finds beats, not bars: it does not know the meter or which beat is the first of a bar. ``meter`` is
given (4 by default) and any beat may start the section; the automatic choice takes the start and bar count whose
two ends sound most alike.
"""

from __future__ import annotations

import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from funkbox.gl.audio import read_wav_channels

MIN_SECONDS, MAX_SECONDS = 3.0, 8.0
CROSSFADE_S = 0.05
METER = 4
MIN_LEVEL = 0.5  # the automatic choice skips sections quieter than this share of the track's RMS (intros, breaks)
ANALYSIS_SR = 22050
N_FFT, HOP, N_BANDS = 1024, 512, 24


@dataclass
class Section:
    source: str  # the track the section was cut from
    path: str  # the WAV written
    bpm: float
    meter: int
    bars: int
    start_s: float  # where the section starts in the source (on a detected beat)
    seconds: float  # its length: frames / fps
    frames: int
    fps: int
    crossfade_ms: float
    drift_ms: float  # seconds minus the true length of the bars, from rounding to whole frames
    picked: str  # "auto", or "start" when the caller gave the start time

    def to_dict(self) -> dict:
        return asdict(self)

    def line(self) -> str:
        return (
            f"audio loop: {self.bars} bar{'' if self.bars == 1 else 's'} of {self.meter} beats at {self.bpm:.1f} BPM, "
            f"from {self.start_s:.2f}s of {Path(self.source).name}; {self.seconds:.3f}s = {self.frames} frames "
            f"({self.drift_ms:+.1f} ms against the bars), {self.crossfade_ms:.0f} ms crossfade at the wrap"
        )


def bar_counts(bar_s: float, lo: float = MIN_SECONDS, hi: float = MAX_SECONDS) -> list[int]:
    """How many whole bars make a section between ``lo`` and ``hi`` seconds."""
    return [n for n in range(1, 65) if lo - 1e-6 <= n * bar_s <= hi + 1e-6]


def band_frames(y: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """Log energy in N_BANDS log-spaced bands per analysis frame -> (frames x bands, frames per second)."""
    if len(y) < N_FFT:
        y = np.pad(y, (0, N_FFT - len(y)))
    n = 1 + (len(y) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    power = np.abs(np.fft.rfft(y[idx] * np.hanning(N_FFT).astype(np.float32), axis=1)) ** 2
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    edges = np.geomspace(40.0, min(8000.0, sr / 2.0 - 1.0), N_BANDS + 1)
    bands = np.stack([power[:, (freqs >= a) & (freqs < b)].sum(axis=1) for a, b in zip(edges, edges[1:])], axis=1)
    return np.log1p(100.0 * bands).astype(np.float32), sr / HOP


def _alike(f: np.ndarray, a: int, b: int, k: int) -> float | None:
    """How different two stretches of ``k`` analysis frames are (0 = the same); None when either leaves the track."""
    if a < 0 or b < 0 or a + k > len(f) or b + k > len(f):
        return None
    x, y = f[a : a + k], f[b : b + k]
    return float(np.abs(x - y).mean() / ((x + y).mean() / 2.0 + 1e-6))


def pick_section(
    y: np.ndarray,
    sr: int,
    beats: list[float],
    bpm: float,
    fps: int,
    *,
    meter: int = METER,
    start: float | None = None,
    bars: int | None = None,
    crossfade_s: float = CROSSFADE_S,
) -> tuple[float, int, int]:
    """(start in seconds, bars, frames) of the section to cut from mono samples ``y``.

    Candidates: every detected beat as the start (or the beat nearest ``start``), every bar count that gives 3 to
    8 s (or ``bars``). A candidate must fit in the track and leave ``crossfade_s`` of audio after it or before it.
    The score compares one beat of audio after the section's end with the same beat after its start, and the beat
    before its end with the beat before its start: in a section that repeats, both pairs sound alike. Rounding to
    whole frames adds its share of a beat to the score. Lowest score wins; ties go to the earlier start, then to
    the longer section."""
    period = 60.0 / bpm
    bar_s = meter * period
    counts = [bars] if bars else bar_counts(bar_s)
    if not counts:
        raise ValueError(
            f"a bar of {meter} beats at {bpm:.1f} BPM is {bar_s:.2f}s: no whole number of bars is "
            f"{MIN_SECONDS:g} to {MAX_SECONDS:g}s long; give the bar count or a smaller meter"
        )
    dur = len(y) / sr
    starts = [min(beats, key=lambda b: abs(b - start))] if start is not None else list(beats)
    f, ffps = band_frames(y, sr)
    k = max(2, round(period * ffps))
    track_rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2))) + 1e-9
    found = []
    for s in starts:
        for n in counts:
            frames = round(n * bar_s * fps)
            length = frames / fps
            if frames < 2 or s + length > dur + 1e-6:
                continue
            if s + length + crossfade_s > dur and s < crossfade_s:
                continue  # nothing to crossfade with
            a, b = round(s * ffps), round((s + length) * ffps)
            pairs = [d for d in (_alike(f, a, b, k), _alike(f, a - k, b - k, k)) if d is not None]
            score = (sum(pairs) / len(pairs) if pairs else 1.0) + abs(length - n * bar_s) / period
            seg = y[round(s * sr) : round((s + length) * sr)].astype(np.float64)
            level = float(np.sqrt(np.mean(seg**2))) / track_rms if len(seg) else 0.0
            found.append((round(score, 6), s, -n, frames, level))
    if not found:
        need = min(counts) * bar_s
        raise ValueError(
            f"no section fits: the track is {dur:.1f}s and the shortest section is {need:.1f}s "
            f"({min(counts)} bar{'' if min(counts) == 1 else 's'} of {meter} beats at {bpm:.1f} BPM)"
        )
    loud = [c for c in found if c[4] >= MIN_LEVEL] if start is None else found
    _, s, neg_n, frames, _ = min(loud or found)
    return s, -neg_n, frames


def crossfade_cut(x: np.ndarray, sr: int, start_s: float, frames: int, fps: int, crossfade_s: float) -> np.ndarray:
    """``frames / fps`` seconds of ``x`` (samples x channels) from ``start_s``, made to wrap without a click.

    With audio after the section, its first ``crossfade_s`` fade in from what follows the section, so the wrap
    plays on as the track did. Otherwise its last ``crossfade_s`` fade into what led up to the section. The fade is
    linear, so the result never exceeds the louder of the two."""
    s = round(start_s * sr)
    n = round(frames * sr / fps)
    if s < 0 or s + n > len(x):
        raise ValueError(f"the section ({start_s:.2f}s + {frames / fps:.2f}s) runs past the end of the audio")
    k = min(round(crossfade_s * sr), n // 4)
    out = x[s : s + n].astype(np.float32).copy()
    if k < 1:
        return out
    if s + n + k <= len(x):
        w = (np.arange(k, dtype=np.float32) / k)[:, None]  # 0 at the first sample: it is the sample after the section
        out[:k] = x[s + n : s + n + k] * (1.0 - w) + out[:k] * w
    elif s >= k:
        w = (np.arange(1, k + 1, dtype=np.float32) / k)[:, None]  # 1 at the last sample: the one before the section
        out[n - k :] = out[n - k :] * (1.0 - w) + x[s - k : s] * w
    else:
        raise ValueError("the track has no audio before or after the section to crossfade with")
    return out


def write_wav16(path: str | Path, x: np.ndarray, sr: int) -> None:
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def cut_section(
    src: str | Path,
    out: str | Path,
    fps: int,
    *,
    start: float | None = None,
    bars: int | None = None,
    meter: int = METER,
    crossfade_s: float = CROSSFADE_S,
) -> Section:
    """Detect the beats of ``src`` (a PCM WAV), pick a section (see ``pick_section``) and write it to ``out``."""
    from funkbox.reel import audio as reel_audio

    if meter < 1 or (bars is not None and bars < 1):
        raise ValueError("meter and bars must be 1 or more")
    x, sr = read_wav_channels(src)
    y = reel_audio.decode_mono(src, ANALYSIS_SR)
    try:
        info = reel_audio.beat_grid(y, ANALYSIS_SR)
    except ValueError as e:
        raise ValueError(f"{Path(src).name}: {e}; an audio loop needs a track with a beat") from None
    if len(info.beats) < 2:
        raise ValueError(f"{Path(src).name}: no beat grid found; an audio loop needs a track with a beat")
    s, n, frames = pick_section(
        y, ANALYSIS_SR, info.beats, info.bpm, fps, meter=meter, start=start, bars=bars, crossfade_s=crossfade_s
    )
    cut = crossfade_cut(x, sr, s, frames, fps, crossfade_s)
    write_wav16(out, cut, sr)
    length = frames / fps
    return Section(
        source=Path(src).as_posix(),
        path=Path(out).as_posix(),
        bpm=round(info.bpm, 2),
        meter=meter,
        bars=n,
        start_s=round(s, 4),
        seconds=round(length, 6),
        frames=frames,
        fps=fps,
        crossfade_ms=round(min(round(crossfade_s * sr), len(cut) // 4) / sr * 1000.0, 1),
        drift_ms=round((length - n * meter * 60.0 / info.bpm) * 1000.0, 1),
        picked="start" if start is not None else "auto",
    )


def apply(recipe, out_dir: str | Path, name: str, **opts) -> Section:
    """Cut a section of the recipe's ``settings.audio`` to ``<out_dir>/<name>.loop.wav`` and point the recipe at
    it: ``audio`` becomes that file, ``seconds`` its length, ``audio_loop`` true and ``beats`` / ``meter`` the
    section's beat grid (for iBeat). ``opts`` go to cut_section."""
    from funkbox.config import resolve_path

    st = recipe.settings
    if not st.audio:
        raise ValueError("an audio loop needs settings.audio (a WAV file)")
    if st.audio_loop:
        raise ValueError("settings.audio_loop is already set: this audio is a cut loop; give the full track instead")
    src = resolve_path(st.audio, recipe.base_dir)
    sec = cut_section(src, Path(out_dir) / f"{name}.loop.wav", st.fps, **opts)
    st.audio, st.seconds, st.audio_loop = sec.path, sec.seconds, True
    st.beats, st.meter = sec.bars * sec.meter, sec.meter  # the section starts on a beat: iBeat lines up with it
    return sec
