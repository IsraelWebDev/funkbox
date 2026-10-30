"""Audio for reels: local TTS (Kokoro, or Piper), music beat detection, ducked mix, loudness normalisation.

No librosa: onset strength = spectral flux, tempo = autocorrelation (70-180 BPM), beat grid by
scoring a comb against the onset envelope. The mix is one ffmpeg filtergraph per pass
(music at ``music_gain_db``, VO delayed to its start times, music ducked while VO plays, end card
muted, then two-pass ``loudnorm``).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from funkbox.config import funk_out, get as _config, models_dir, resolve_path
from funkbox.media import filter_script_args, require_ffmpeg

from .copydeck import CopyDeck
from .depth import models_manifest
from .schema import Reel

SR_VO = 22050
STUB_SEC_PER_CHAR = 0.06
DUCK_RAMP = 0.15
TARGET_TP = -1.5
LIMITER_CEIL = 0.708         # -3 dBFS sample peak: headroom for inter-sample peaks and loudnorm's trim
END_FADE = 0.3


def _log(*a) -> None:
    print(*a, file=sys.stderr)


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
_VOICE = None
_KOKORO = None
DEFAULT_VOICE = "kokoro:af_heart"
#: the Kokoro voices we have listened to and approved for ads; any Kokoro voice id works
APPROVED_VOICES = ("kokoro:af_heart", "kokoro:am_michael", "kokoro:bm_george")
VOICE_RE = re.compile(r"^(piper|kokoro:[a-z]{2}_[a-z]+)$")


def voice_files() -> tuple[Path, Path]:
    fs = [models_dir() / f["dest"] for f in models_manifest()["voice"]["files"]]
    return fs[0], fs[1]


def kokoro_files() -> tuple[Path, Path]:
    fs = [models_dir() / f["dest"] for f in models_manifest()["kokoro"]["files"]]
    return fs[0], fs[1]


def _has(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def _kokoro_ready() -> bool:
    return _has("kokoro_onnx") and all(p.exists() for p in kokoro_files())


def _piper_ready() -> bool:
    return _has("piper") and all(p.exists() for p in voice_files())


def resolve_voice(voice: str | None = None) -> str:
    """The voice a reel will speak with: its ``audio.voice`` ("kokoro:<id>" or "piper"), else FUNK_VOICE, else
    Kokoro's af_heart when installed, else Piper."""
    import os
    want = voice or os.environ.get("FUNK_VOICE") or ""
    if want:
        return want
    return DEFAULT_VOICE if _kokoro_ready() or not _piper_ready() else "piper"


def tts_mode(voice: str | None = None) -> str:
    """"kokoro" | "piper" | "stub" | "none". REEL_TTS=stub|off forces a mode; the stub is for tests only."""
    forced = _config().reel_tts
    if forced in ("stub", "off", "none"):
        return "stub" if forced == "stub" else "none"
    v = resolve_voice(voice)
    if v.startswith("kokoro:"):
        return "kokoro" if _kokoro_ready() else "none"
    return "piper" if _piper_ready() else "none"


def tts_unavailable_reason(voice: str | None = None) -> str:
    v = resolve_voice(voice)
    if v.startswith("kokoro:"):
        if not _has("kokoro_onnx"):
            return "kokoro-onnx is not installed (pip install -r requirements-reel.txt)"
        return f"no Kokoro model in {models_dir()} (run: funkbox reel setup-models)"
    if not _has("piper"):
        return "piper-tts is not installed (pip install -r requirements-reel.txt)"
    return f"no Piper voice found in {models_dir()} (run: funkbox reel setup-models)"


def _voice():
    global _VOICE
    if _VOICE is None:
        from piper import PiperVoice
        onnx, cfg = voice_files()
        _VOICE = PiperVoice.load(onnx, cfg)
    return _VOICE


def _kokoro():
    global _KOKORO
    if _KOKORO is None:
        from kokoro_onnx import Kokoro
        model, voices = kokoro_files()
        _KOKORO = Kokoro(str(model), str(voices))
    return _KOKORO


def _kokoro_wav(text: str, voice_id: str, speed: float, path: Path) -> None:
    lang = "en-gb" if voice_id.startswith("b") else "en-us"
    k = _kokoro()
    if voice_id not in k.get_voices():
        raise RuntimeError(f"Kokoro has no voice {voice_id!r}; try {', '.join(v.split(':')[1] for v in APPROVED_VOICES)}")
    y, sr = k.create(text, voice=voice_id, speed=speed, lang=lang)
    y = np.asarray(y, np.float64)
    peak = float(np.abs(y).max()) if y.size else 0.0
    if peak > 0.98:
        y = y * (0.98 / peak)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def _stub_wav(text: str, path: Path) -> None:
    """Sine bursts, one per word, total length proportional to the character count."""
    n = len(text)
    total = max(0.2, n * STUB_SEC_PER_CHAR)
    y = np.zeros(int(round(total * SR_VO)), dtype=np.float64)
    pos = 0
    for word in re.split(r"( )", text):
        L = len(word) * STUB_SEC_PER_CHAR
        if word.strip():
            a, b = int(pos * SR_VO), int(min(total, pos + max(0.03, L - 0.03)) * SR_VO)
            t = np.arange(b - a) / SR_VO
            f = 170 + 17 * (int(hashlib.md5(word.encode()).hexdigest(), 16) % 9)
            env = np.sin(np.pi * np.arange(b - a) / max(1, b - a)) ** 0.5
            y[a:b] += 0.35 * env * np.sin(2 * np.pi * f * t)
        pos += L
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR_VO)
        wf.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def synthesize(deck: CopyDeck, copy_id: str, voice: str | None = None, speed: float = 1.0) -> Path:
    """TTS for one copy line (``say`` text, pronunciations applied) -> cached wav."""
    mode = tts_mode(voice)
    if mode == "none":
        raise RuntimeError(f"voiceover needs a TTS voice: {tts_unavailable_reason(voice)}")
    text = deck.say(copy_id)
    onnx, _ = voice_files()
    ident = {"piper": f"piper:{onnx.name}", "stub": "stub",
             "kokoro": f"{resolve_voice(voice)}@{speed:g}:{kokoro_files()[0].name}"}[mode]
    key = hashlib.sha1((ident + "\n" + text).encode("utf-8")).hexdigest()
    out = funk_out() / "cache" / "tts" / f"{key}.wav"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.wav")
    if mode == "stub":
        _stub_wav(text, tmp)
    elif mode == "kokoro":
        _kokoro_wav(text, resolve_voice(voice).split(":", 1)[1], speed, tmp)
    else:
        with wave.open(str(tmp), "wb") as wf:
            _voice().synthesize_wav(text, wf)
    tmp.replace(out)
    return out


def wav_duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def vo_ids(reel: Reel) -> list[str]:
    seen: list[str] = []
    for sh in reel.shots:
        if sh.vo and sh.vo not in seen:
            seen.append(sh.vo)
    return seen


def vo_durations(reel: Reel, deck: CopyDeck) -> dict[str, float]:
    """Seconds of speech per copy id (from the wav header)."""
    return {cid: wav_duration(synthesize(deck, cid, reel.audio.voice, reel.audio.voice_speed)) for cid in vo_ids(reel)}


# --------------------------------------------------------------------------- #
# Beat detection
# --------------------------------------------------------------------------- #
@dataclass
class BeatInfo:
    bpm: float
    beats: list[float]
    duration: float


def decode_mono(path: str | Path, sr: int = 22050) -> np.ndarray:
    cmd = [require_ffmpeg(), "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"cannot decode {path}: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def onset_envelope(y: np.ndarray, sr: int, n_fft: int = 1024, hop: int = 256) -> tuple[np.ndarray, float]:
    """Spectral flux (half-wave rectified log-magnitude difference), centred frames."""
    if len(y) < n_fft * 2:
        return np.zeros(1, np.float32), sr / hop
    pad = n_fft // 2
    yp = np.pad(y, (pad, pad), mode="reflect")
    n = 1 + (len(yp) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    win = np.hanning(n_fft).astype(np.float32)
    mag = np.abs(np.fft.rfft(yp[idx] * win, axis=1))
    lm = np.log1p(20.0 * mag)
    flux = np.maximum(0.0, np.diff(lm, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(31) / 31, mode="same")           # local mean removal
    flux = np.maximum(flux, 0.0)
    m = flux.max()
    return (flux / m if m > 0 else flux).astype(np.float32), sr / hop


def estimate_tempo(env: np.ndarray, fps: float, lo: float = 70.0, hi: float = 180.0) -> float:
    """BPM from the autocorrelation of the onset envelope, refined by scoring a beat comb."""
    if len(env) < fps * 2 or env.max() <= 0:
        raise ValueError("audio too short or silent for tempo estimation")
    x = env - env.mean()
    n = 1 << (2 * len(x) - 1).bit_length()
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, n)) ** 2)[: len(x)]
    ac /= ac[0] + 1e-12
    lags = np.arange(int(fps * 60 / hi), int(fps * 60 / lo) + 1)
    bpms = 60.0 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 1.0) ** 2)           # mild preference for ~120 BPM
    best = lags[int(np.argmax(ac[lags] * prior))]
    bpm0 = 60.0 * fps / best
    # refine: comb score over a fine BPM grid
    cands = np.linspace(bpm0 * 0.97, bpm0 * 1.03, 121)
    best_s, best_b = -1.0, bpm0
    t = np.arange(len(env))
    for b in cands:
        s, _ = _comb_score(env, t, fps, b)
        if s > best_s:
            best_s, best_b = s, float(b)
    return best_b


def _comb_score(env: np.ndarray, t: np.ndarray, fps: float, bpm: float) -> tuple[float, float]:
    period = 60.0 * fps / bpm
    best, ph_best = -1.0, 0.0
    for ph in np.arange(0.0, period, 0.5):
        pos = np.arange(ph, len(env) - 1, period)
        s = float(np.interp(pos, t, env).sum() / max(1, len(pos)))
        if s > best:
            best, ph_best = s, float(ph)
    return best, ph_best


def beat_grid(y: np.ndarray, sr: int) -> BeatInfo:
    env, fps = onset_envelope(y, sr)
    bpm = estimate_tempo(env, fps)
    _, phase = _comb_score(env, np.arange(len(env)), fps, bpm)
    dur = len(y) / sr
    period = 60.0 / bpm
    beats = []
    t = phase / fps
    while t < dur - 1e-6:
        beats.append(round(t, 4))
        t += period
    return BeatInfo(round(bpm, 2), beats, dur)


def detect_beats(path: str | Path) -> BeatInfo:
    return beat_grid(decode_mono(path), 22050)


_BEAT_CACHE: dict[tuple, BeatInfo] = {}


def fixed_grid(bpm: float, offset: float = 0.0, seconds: float = 600.0) -> list[float]:
    """A steady beat grid: ``offset + k * 60 / bpm`` (no detection; e.g. 110 BPM -> one beat = 0.545 s)."""
    period = 60.0 / bpm
    return [round(offset + k * period, 4) for k in range(int((seconds - offset) / period) + 1)]


def music_file(reel: Reel) -> Path | None:
    """The reel's music as an audio file. A ``*.music.json`` score is rendered with funkbox.music (once per score
    content, cached under FUNK_OUT/music)."""
    if not reel.audio.music:
        return None
    p = resolve_path(reel.audio.music, reel.base_dir)
    if not p.name.endswith(".music.json"):
        return p
    import hashlib
    from funkbox.config import funk_out
    from funkbox.music.score import render as render_music
    out = funk_out() / "music" / f"{p.name[:-len('.music.json')]}-{hashlib.sha256(p.read_bytes()).hexdigest()[:16]}.wav"
    if not out.is_file():
        render_music(p, out)
    return out


def _score_bpm(reel: Reel) -> float | None:
    if reel.audio.music and str(reel.audio.music).endswith(".music.json"):
        p = resolve_path(reel.audio.music, reel.base_dir)
        return float(json.loads(p.read_text(encoding="utf-8-sig")).get("bpm", 100))
    return None


def beat_grid_for(reel: Reel) -> list[float] | None:
    """Beat times (seconds): ``audio.bpm`` when set (a fixed grid, music or not); otherwise the beats detected in
    the reel's music, extended over the whole reel (the music loops)."""
    if reel.audio.bpm:
        return fixed_grid(reel.audio.bpm, reel.audio.beat_offset)
    if not reel.audio.music:
        return None
    bpm = _score_bpm(reel)
    if bpm:                                            # a score knows its own tempo
        return fixed_grid(bpm, reel.audio.beat_offset)
    p = resolve_path(reel.audio.music, reel.base_dir)
    key = (str(p), p.stat().st_mtime_ns)
    info = _BEAT_CACHE.get(key)
    if info is None:
        info = _BEAT_CACHE[key] = detect_beats(p)
    period = 60.0 / info.bpm
    # loop the grid: music repeats from its start; keep a steady period across the loop point
    last = max(info.duration, 1.0) * 6
    n = int(last / period) + 1
    first = info.beats[0] if info.beats else 0.0
    return [round(first + k * period, 4) for k in range(n)]


# --------------------------------------------------------------------------- #
# Mix
# --------------------------------------------------------------------------- #
def _duck_expr(intervals: list[tuple[float, float]], duck_db: float) -> str | None:
    if not intervals or duck_db >= 0:
        return None
    g = 10 ** (duck_db / 20.0)
    r = DUCK_RAMP
    terms = [f"clip(min((t-{a - r:.4f})/{r},({b + r:.4f}-t)/{r}),0,1)" for a, b in intervals]
    m = terms[0]
    for t in terms[1:]:
        m = f"max({m},{t})"
    return f"1-{1 - g:.5f}*{m}"


def mix_graph(plan, music: Path | None, clips: list[tuple[Path, float]], total: float,
              loudnorm: str | None) -> tuple[list[str], str]:
    """Return (ffmpeg input args, filtergraph). ``clips`` = [(wav, start_seconds)]."""
    reel = plan.reel
    a = reel.audio
    args: list[str] = []
    parts: list[str] = []
    labels: list[str] = []
    n = 0
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    intervals = [(s, s + wav_duration(p)) for p, s in clips]
    if music is not None:
        args += ["-stream_loop", "-1", "-i", str(music)]
        duck = _duck_expr(intervals, a.duck_db) if a.voiceover else None
        chain = f"[{n}:a]{fmt},volume={a.music_gain_db}dB,atrim=0:{total:.4f},asetpts=PTS-STARTPTS"
        if duck:
            chain += f",volume='{duck}':eval=frame"
        parts.append(chain + "[mus]")
        labels.append("[mus]")
        n += 1
    for k, (p, start) in enumerate(clips):
        args += ["-i", str(p)]
        ms = int(round(start * 1000))
        parts.append(f"[{n}:a]{fmt},adelay={ms}|{ms},asetpts=PTS-STARTPTS[v{k}]")
        labels.append(f"[v{k}]")
        n += 1
    if len(labels) == 1:
        parts.append(f"{labels[0]}anull[mix]")
    else:
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[mix]")
    tail = f"[mix]apad=whole_dur={total:.4f},atrim=0:{total:.4f}"
    ec = plan.spans[-1] if plan.spans and plan.spans[-1].kind == "end" else None
    if ec is not None and reel.end_card and reel.end_card.audio == "silence":
        s = ec.start
        tail += f",afade=t=out:st={max(0.0, s - END_FADE):.4f}:d={END_FADE},volume=0:enable='gte(t,{s:.4f})'"
    if loudnorm:
        tail += "," + loudnorm
    tail += ",aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[out]"
    parts.append(tail)
    return args, ";\n".join(parts)


def _run_graph(args: list[str], graph: str, out: list[str], work: Path) -> str:
    script = work / "mix.filtergraph"
    script.write_text(graph, encoding="utf-8")
    cmd = [require_ffmpeg(), "-y", "-hide_banner", "-nostats", *args, *filter_script_args(script),
           "-map", "[out]", *out]
    r = subprocess.run(cmd, capture_output=True)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        raise RuntimeError(f"audio mix failed: {err.strip()[-600:]}")
    return err


def build_mix(plan, out_dir: Path, name: str) -> Path | None:
    """Mix music + VO into ``<out_dir>/<name>.mix.wav`` (48 kHz stereo). None if the reel has no audio."""
    reel, deck, spans = plan.reel, plan.deck, plan.spans
    a = reel.audio
    music = music_file(reel)
    clips: list[tuple[Path, float]] = []
    if a.voiceover:
        for sp in spans:
            if sp.kind == "shot" and sp.vo_id and sp.vo_start is not None:
                clips.append((synthesize(deck, sp.vo_id, a.voice, a.voice_speed), sp.vo_start))
    if music is None and not clips:
        return None
    total = spans[-1].end
    work = out_dir / f".{name}.audio"
    work.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.mix.wav"
    target = a.loudness_lufs
    # A: how loud is the raw mix?
    args, g = mix_graph(plan, music, clips, total, f"loudnorm=I={target}:TP={TARGET_TP}:LRA=50:print_format=json")
    meas = _loudnorm_json(_run_graph(args, g, ["-f", "null", "-"], work))
    # B: a loud VO over a quiet bed has a large peak-to-loudness ratio, so a plain gain to the target
    # would clip (and loudnorm would then fall back to dynamic mode and miss the target). Apply the
    # gain first and catch the peaks with a limiter; re-measure and trim the gain until the limited
    # mix sits on the target, then let loudnorm finish linearly.
    gain = target - float(meas["input_i"])
    for _ in range(4):
        pre = f"volume={gain:.3f}dB,alimiter=limit={LIMITER_CEIL}:attack=5:release=50:level=false"
        args, g = mix_graph(plan, music, clips, total,
                            pre + f",loudnorm=I={target}:TP={TARGET_TP}:LRA=50:print_format=json")
        meas = _loudnorm_json(_run_graph(args, g, ["-f", "null", "-"], work))
        err = target - float(meas["input_i"])
        if abs(err) < 0.1:
            break
        gain += err
    lra = min(50.0, max(11.0, math.ceil(float(meas["input_lra"]) + 1.0)))
    ln = (f"loudnorm=I={target}:TP={TARGET_TP}:LRA={lra:g}:measured_I={float(meas['input_i'])}:"
          f"measured_TP={float(meas['input_tp'])}:measured_LRA={float(meas['input_lra'])}:"
          f"measured_thresh={float(meas['input_thresh'])}:offset={float(meas['target_offset'])}:linear=true")
    args, g = mix_graph(plan, music, clips, total, pre + "," + ln)
    _run_graph(args, g, ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out)], work)
    return out


def _loudnorm_json(stderr: str) -> dict:
    m = re.findall(r"\{[^{}]*\}", stderr)
    if not m:
        raise RuntimeError("loudnorm produced no measurement (is the mix silent?)")
    d = json.loads(m[-1])
    if str(d.get("input_i", "")).strip("-") in ("inf", ""):
        raise RuntimeError("cannot measure loudness of a silent mix")
    return d
