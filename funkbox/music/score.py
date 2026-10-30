"""Music from a short score: real sampled instruments from a General MIDI SoundFont, mixed with a little room,
written as a 48 kHz stereo WAV a reel can use (its beat grid comes from the score's tempo).

A score (``*.music.json``):

    {"bpm": 104, "meter": 4, "key": "D", "scale": "freygish", "bars": 8, "swing": 0.1, "reverb": 0.25,
     "soundfont": "GeneralUser GS",                 # (default) fetched once, cached; or "builtin", or a .sf2 path
     "tracks": [
       {"name": "clarinet", "program": 71, "octave": 5, "notes": "^1/8 ^2 ^3 ^4 | ^5/4. ^4/8 ^3/4 ^2 | ^1/1"},
       {"name": "piano", "program": 0, "velocity": 70, "notes": "[D3 F#3 A3]/2 [C3 Eb3 G3]/2 | [D3 F#3 A3]/1"},
       {"name": "bass", "program": 32, "octave": 2, "notes": "^1/4 ^5, ^1 ^5,"},
       {"name": "drums", "drums": true, "pattern": {"kick": "x...x...x...x...", "snare": "....x.......x...",
                                                    "hat": "x.x.x.x.x.x.x.x."}}]}

Notes: a pitch (``D4``, ``F#3``, ``Bb2``), a scale degree of the track's key and octave (``^1`` .. ``^7``, ``^8`` the
octave; ``'`` raises and ``,`` lowers an octave: ``^5,``), a chord ``[D3 F#3 A3]`` (degrees work inside too), ``r`` a
rest, ``~`` holds the previous note longer. ``/4`` is a quarter, ``/8`` an eighth, ``/1`` a whole note, a trailing ``.``
dots it; a duration carries over to the next notes until changed. ``!`` accents a note, ``?`` softens it. ``|`` marks
bars (ignored). Drum patterns are 16th steps: ``x`` hit, ``X`` accent, ``o`` soft, ``.`` rest; names: kick, snare,
rim, clap, hat, openhat, pedalhat, tom-lo, tom-mid, tom-hi, crash, ride, tamb, cowbell, shaker. With ``bars`` every
track repeats to fill them. ``program`` is the General MIDI number (0 piano, 24 nylon guitar, 32 acoustic bass,
40 violin, 48 strings, 56 trumpet, 68 oboe, 71 clarinet, 73 flute, 21 accordion ...).
"""
from __future__ import annotations

import json
import math
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SR = 48000
SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10], "harmonic-minor": [0, 2, 3, 5, 7, 8, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10], "mixolydian": [0, 2, 4, 5, 7, 9, 10], "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "freygish": [0, 1, 4, 5, 7, 8, 10], "ahava-raba": [0, 1, 4, 5, 7, 8, 10],
    "misheberach": [0, 2, 3, 6, 7, 9, 10], "pentatonic": [0, 2, 4, 7, 9, 12, 14],
    "minor-pentatonic": [0, 3, 5, 7, 10, 12, 15], "blues": [0, 3, 5, 6, 7, 10, 12],
}
NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
DRUMS = {"kick": 36, "rim": 37, "snare": 38, "clap": 39, "hat": 42, "pedalhat": 44, "openhat": 46, "tom-lo": 45,
         "tom-mid": 47, "tom-hi": 50, "crash": 49, "ride": 51, "tamb": 54, "cowbell": 56, "shaker": 70}
TOKEN = re.compile(r"(\[[^\]]*\]|r|~|\^\d+[',]*|[A-Ga-g][#b]?-?\d)(?:/(\d+))?(\.?)([!?]?)")


class ScoreError(ValueError):
    pass


@dataclass
class Event:
    start: float                     # beats
    keys: list[int]
    beats: float
    velocity: int
    track: int


@dataclass
class Music:
    path: Path | None
    seconds: float
    bpm: float
    beats: list[float]
    soundfont: str
    tracks: list[str]
    audio: np.ndarray | None = None
    notes: int = 0
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- parsing
def key_root(key: str) -> int:
    m = re.fullmatch(r"([A-Ga-g])([#b]?)", key or "C")
    if not m:
        raise ScoreError(f"key {key!r}: a note name like D, F# or Bb")
    return NOTE[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]


def pitch(tok: str, root: int, scale: list[int], octave: int) -> int:
    if tok.startswith("^"):
        m = re.fullmatch(r"\^(\d+)([',]*)", tok)
        deg = int(m.group(1))
        if deg < 1:
            raise ScoreError(f"note {tok!r}: degrees start at ^1")
        o, d = divmod(deg - 1, 7)
        step = scale[d] if len(scale) == 7 and max(scale) < 12 else scale[d % len(scale)]
        shift = 12 * (o + m.group(2).count("'") - m.group(2).count(","))
        return 12 * (octave + 1) + root + step + shift
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", tok)
    if not m:
        raise ScoreError(f"note {tok!r}: use D4, F#3, Bb2, ^1..^7, r or a chord [..]")
    return 12 * (int(m.group(3)) + 1) + NOTE[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]


def parse_notes(text: str, root: int, scale: list[int], octave: int, velocity: int, track: int) -> tuple[list[Event], float]:
    """Events (in beats, a quarter = 1 beat) and the length in beats."""
    events: list[Event] = []
    t, dur = 0.0, 1.0
    pos = 0
    body = text.replace("|", " ")
    for m in TOKEN.finditer(body):
        gap = body[pos:m.start()].strip()
        if gap:
            raise ScoreError(f"notes: cannot read {gap!r}")
        pos = m.end()
        tok, den, dot, acc = m.groups()
        if den:
            if int(den) not in (1, 2, 4, 8, 16, 32):
                raise ScoreError(f"notes: /{den} is not a note length (1, 2, 4, 8, 16, 32)")
            dur = 4.0 / int(den)
            if dot:
                dur *= 1.5
        vel = min(127, velocity + 20) if acc == "!" else max(1, velocity - 30) if acc == "?" else velocity
        if tok == "r":
            pass
        elif tok == "~":
            if not events:
                raise ScoreError("notes: ~ needs a note before it")
            events[-1].beats += dur
        else:
            parts = tok[1:-1].split() if tok.startswith("[") else [tok]
            if not parts:
                raise ScoreError("notes: an empty chord []")
            events.append(Event(t, [pitch(p, root, scale, octave) for p in parts], dur, vel, track))
        t += dur
    tail = body[pos:].strip()
    if tail:
        raise ScoreError(f"notes: cannot read {tail!r}")
    return events, t


def parse_drums(pattern: dict, steps_per_beat: int, velocity: int, track: int) -> tuple[list[Event], float]:
    events, length = [], 0.0
    for name, row in pattern.items():
        if name not in DRUMS:
            raise ScoreError(f"drum {name!r}: one of {', '.join(DRUMS)}")
        row = row.replace("|", "").replace(" ", "")
        if set(row) - set("xXo."):
            raise ScoreError(f"drum {name!r}: use x (hit), X (accent), o (soft) and . (rest)")
        step = 1.0 / steps_per_beat
        for i, ch in enumerate(row):
            if ch != ".":
                v = {"x": velocity, "X": min(127, velocity + 25), "o": max(1, velocity - 35)}[ch]
                events.append(Event(i * step, [DRUMS[name]], step, v, track))
        length = max(length, len(row) * step)
    return events, length


# --------------------------------------------------------------------------- soundfonts
GU_URL = "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main"
BUILTIN_FAMILY = {0: 0, 1: 0, 2: 19, 3: 24, 4: 32, 5: 48, 6: 48, 7: 56, 8: 71, 9: 73, 10: 56, 11: 48}


def soundfont_dir() -> Path:
    from funkbox import config
    d = config.get().home / "soundfonts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_soundfont(spec: str | None, base_dir=None) -> tuple[Path, str, list[str]]:
    """(file, label, warnings). The named GM bank is downloaded once (FUNK_SOUNDFONT_URL points at a mirror);
    when that is impossible the built-in bank is used and a warning says so."""
    import os
    warns: list[str] = []
    spec = spec or "GeneralUser GS"
    if spec.lower().endswith(".sf2"):
        from funkbox.config import resolve_path
        return resolve_path(spec, base_dir), Path(spec).name, warns
    if spec == "builtin":
        return builtin_bank(), "builtin", warns
    if spec != "GeneralUser GS":
        raise ScoreError(f"soundfont {spec!r}: \"GeneralUser GS\", \"builtin\" or a .sf2 file")
    out = soundfont_dir() / "GeneralUser-GS.sf2"
    if not out.is_file():
        base = os.environ.get("FUNK_SOUNDFONT_URL", GU_URL).rstrip("/")
        try:
            from funkbox.fonts import _get
            blob = _get(f"{base}/GeneralUser-GS.sf2")
            if blob[:4] != b"RIFF":
                raise OSError("not a SoundFont")
            tmp = out.with_suffix(".part")
            tmp.write_bytes(blob)
            tmp.replace(out)
            try:
                (soundfont_dir() / "GeneralUser-GS-LICENSE.txt").write_bytes(_get(f"{base}/documentation/LICENSE.txt"))
            except OSError:
                pass
        except OSError as e:
            warns.append(f"GeneralUser GS could not be downloaded ({e}); used the built-in instruments")
            return builtin_bank(), "builtin", warns
    return out, "GeneralUser GS", warns


def builtin_bank() -> Path:
    """A small synthesised bank (piano, organ, guitar, bass, strings, brass, clarinet, flute, a drum kit), written
    once to the soundfont folder. Plainer than a sampled bank, but needs nothing."""
    out = soundfont_dir() / "funkbox-builtin-v1.sf2"
    if out.is_file():
        return out
    from .sf2 import G_DECAY, G_RELEASE, G_SUSTAIN, G_ATTACK, write_sf2
    rate = 44100
    tc = lambda s: int(round(1200 * math.log2(max(s, 0.001))))

    def tone(f0, secs, harm, decay=None):
        t = np.arange(int(secs * rate)) / rate
        w = sum(a * np.sin(2 * np.pi * f0 * k * t) * (np.exp(-t * decay * k ** 0.5) if decay else 1)
                for k, a in harm)
        return w / max(1e-9, np.abs(w).max()) * 0.8

    def looped(f0, harm, periods=60):
        n = int(round(periods * rate / f0))
        f = periods * rate / n                       # an exact whole number of periods in the loop
        t = np.arange(n * 3) / rate
        w = sum(a * np.sin(2 * np.pi * f * k * t) for k, a in harm)
        w = w / np.abs(w).max() * 0.7
        return w, (n, 2 * n)

    f60 = 261.6256
    insts = []
    w = tone(f60, 3.0, [(1, 1), (2, .5), (3, .25), (4, .15), (5, .08)], decay=1.2)
    insts.append({"name": "Piano", "program": 0, "wave": w, "root": 60, "gens": {G_RELEASE: tc(0.35)}})
    w, lp = looped(f60, [(1, 1), (2, .6), (3, .4), (4, .3), (6, .2), (8, .15)])
    insts.append({"name": "Organ", "program": 19, "wave": w, "root": 60, "loop": lp,
                  "gens": {G_ATTACK: tc(0.02), G_RELEASE: tc(0.12)}})
    n = int(rate * 2.0)
    period = rate / 196.0
    buf = np.random.default_rng(3).uniform(-1, 1, int(period))
    ks = np.zeros(n)
    for i in range(n):                                # Karplus-Strong pluck
        ks[i] = buf[i % len(buf)]
        buf[i % len(buf)] = 0.996 * 0.5 * (buf[i % len(buf)] + buf[(i + 1) % len(buf)])
    insts.append({"name": "Guitar", "program": 24, "wave": ks / np.abs(ks).max() * 0.8, "root": 55,
                  "gens": {G_RELEASE: tc(0.2)}})
    insts.append({"name": "Bass", "program": 32, "wave": tone(65.406, 2.5, [(1, 1), (2, .5), (3, .2)], decay=0.9),
                  "root": 36, "gens": {G_RELEASE: tc(0.15)}})
    w, lp = looped(f60, [(k, 1 / k) for k in range(1, 12)])
    insts.append({"name": "Strings", "program": 48, "wave": w, "root": 60, "loop": lp,
                  "gens": {G_ATTACK: tc(0.25), G_RELEASE: tc(0.6)}})
    w, lp = looped(f60, [(k, 1 / k ** 0.8) for k in range(1, 10)])
    insts.append({"name": "Brass", "program": 56, "wave": w, "root": 60, "loop": lp,
                  "gens": {G_ATTACK: tc(0.04), G_RELEASE: tc(0.15)}})
    w, lp = looped(f60, [(1, 1), (3, .55), (5, .3), (7, .18), (9, .1)])
    insts.append({"name": "Clarinet", "program": 71, "wave": w, "root": 60, "loop": lp,
                  "gens": {G_ATTACK: tc(0.03), G_RELEASE: tc(0.1)}})
    w, lp = looped(f60 * 2, [(1, 1), (2, .1), (3, .05)])
    insts.append({"name": "Flute", "program": 73, "wave": w, "root": 72, "loop": lp,
                  "gens": {G_ATTACK: tc(0.05), G_RELEASE: tc(0.12)}})
    rng = np.random.default_rng(7)
    t = np.arange(int(rate * 0.6)) / rate

    def hit(w):
        return w / max(1e-9, np.abs(w).max()) * 0.9
    noise = rng.uniform(-1, 1, len(t))
    kick = hit(np.sin(2 * np.pi * np.cumsum(50 + 110 * np.exp(-t * 30)) / rate) * np.exp(-t * 7))
    snare = hit(noise * np.exp(-t * 18) * 0.7 + np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25) * 0.5)
    hat = hit(np.diff(noise, prepend=0) * np.exp(-t * 60))
    ohat = hit(np.diff(noise, prepend=0) * np.exp(-t * 9))
    clap = hit(noise * (np.exp(-t * 30) + 0.6 * np.exp(-np.maximum(t - 0.012, 0) * 30) * (t > 0.012)))
    tom = lambda f: hit(np.sin(2 * np.pi * np.cumsum(f + f * 0.6 * np.exp(-t * 20)) / rate) * np.exp(-t * 8))
    crash = hit(np.diff(rng.uniform(-1, 1, int(rate * 2)), prepend=0) * np.exp(-np.arange(int(rate * 2)) / rate * 1.8))
    kit = {36: kick, 37: hit(snare[: rate // 20]), 38: snare, 39: clap, 42: hat, 44: hat * 0.6, 46: ohat,
           45: tom(90), 47: tom(130), 50: tom(180), 49: crash, 51: ohat * 0.7, 54: hat, 56: tom(560), 70: hat * 0.5}
    insts.append({"name": "Drums", "program": 0, "bank": 128,
                  "zones": [{"wave": w, "root": k, "keys": (k, k), "gens": {G_RELEASE: tc(0.3)}}
                            for k, w in sorted(kit.items())]})
    tmp = out.with_suffix(".part")
    write_sf2(tmp, insts, rate)
    tmp.replace(out)
    return out


# --------------------------------------------------------------------------- rendering
def _reverb(x: np.ndarray, amount: float, seconds: float = 1.6) -> np.ndarray:
    """A small room: convolution with decaying, decorrelated noise (stereo), mixed in at ``amount``."""
    if amount <= 0:
        return x
    n = int(SR * seconds)
    t = np.arange(n) / SR
    rng = np.random.default_rng(11)
    env = np.exp(-6.9 * t / seconds) * (t > 0.012)
    ir = rng.standard_normal((n, 2)) * env[:, None]
    ir /= np.sqrt((ir ** 2).sum(0, keepdims=True))
    m = len(x) + n
    nfft = 1 << (m - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, nfft, axis=0) * np.fft.rfft(ir, nfft, axis=0), nfft, axis=0)[: len(x)]
    return x * (1 - amount * 0.5) + wet * amount


def check(score: dict) -> list[str]:
    errs = []
    if not isinstance(score, dict):
        return ["a score is a JSON object"]
    bad = set(score) - {"bpm", "meter", "key", "scale", "bars", "swing", "reverb", "soundfont", "tracks", "gain_db",
                        "name"}
    if bad:
        errs.append(f"unknown keys {sorted(bad)}")
    if not (isinstance(score.get("bpm", 100), (int, float)) and 30 <= score.get("bpm", 100) <= 240):
        errs.append("bpm: 30..240")
    if score.get("scale", "major") not in SCALES:
        errs.append(f"scale: one of {', '.join(SCALES)}")
    if not isinstance(score.get("meter", 4), int) or not 2 <= score.get("meter", 4) <= 12:
        errs.append("meter: beats per bar, 2..12")
    if "bars" in score and not (isinstance(score["bars"], int) and 1 <= score["bars"] <= 128):
        errs.append("bars: 1..128")
    if not 0 <= score.get("swing", 0) <= 0.5:
        errs.append("swing: 0..0.5")
    if not 0 <= score.get("reverb", 0.2) <= 1:
        errs.append("reverb: 0..1")
    tr = score.get("tracks")
    if not isinstance(tr, list) or not 1 <= len(tr) <= 16:
        errs.append("tracks: a list of 1..16 tracks")
        return errs
    for i, t in enumerate(tr):
        w = f"tracks[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{w}: an object")
            continue
        bad = set(t) - {"name", "program", "bank", "octave", "velocity", "gain_db", "pan", "notes", "drums",
                        "pattern", "steps_per_beat", "kit"}
        if bad:
            errs.append(f"{w}: unknown keys {sorted(bad)}")
        if t.get("drums"):
            if not isinstance(t.get("pattern"), dict) or not t["pattern"]:
                errs.append(f"{w}: a drum track needs `pattern` {{name: 'x...x...'}}")
        elif not isinstance(t.get("notes"), str) or not t["notes"].strip():
            errs.append(f"{w}: needs `notes` (or \"drums\": true and a `pattern`)")
        if not 0 <= t.get("program", 0) <= 127:
            errs.append(f"{w}: program is a General MIDI number 0..127")
        if not -1 <= t.get("pan", 0) <= 1:
            errs.append(f"{w}: pan -1 (left) .. 1 (right)")
        if not 1 <= t.get("velocity", 90) <= 127:
            errs.append(f"{w}: velocity 1..127")
    return errs


def load_score(score) -> tuple[dict, Path | None]:
    if isinstance(score, dict):
        return score, None
    from funkbox.config import resolve_path
    p = resolve_path(str(score))
    return json.loads(p.read_text(encoding="utf-8-sig")), p.parent


def render(score, out: str | Path | None = None, base_dir=None) -> Music:
    """Render ``score`` (dict or path); write a WAV to ``out`` when given."""
    from .sf2 import load
    sc, sdir = load_score(score)
    errs = check(sc)
    if errs:
        raise ScoreError("; ".join(errs))
    bpm, meter = float(sc.get("bpm", 100)), int(sc.get("meter", 4))
    root, scale = key_root(sc.get("key", "C")), SCALES[sc.get("scale", "major")]
    path, label, warns = resolve_soundfont(sc.get("soundfont"), base_dir or sdir)
    sf = load(str(path))
    builtin = label == "builtin"
    tracks, events, lengths = [], [], []
    for i, t in enumerate(sc["tracks"]):
        vel = int(t.get("velocity", 90))
        try:
            if t.get("drums"):
                ev, L = parse_drums(t["pattern"], int(t.get("steps_per_beat", 4)), vel, i)
            else:
                ev, L = parse_notes(t["notes"], root, scale, int(t.get("octave", 4)), vel, i)
        except ScoreError as e:
            raise ScoreError(f"tracks[{i}] ({t.get('name', i)}): {e}") from None
        tracks.append(t.get("name", f"track{i + 1}"))
        events.append(ev)
        lengths.append(L)
    total = float(sc["bars"] * meter) if "bars" in sc else max(lengths)
    if total <= 0:
        raise ScoreError("the score has no length")
    swing = float(sc.get("swing", 0))
    spb = 60.0 / bpm
    tail = 2.0
    mix = np.zeros((int((total * spb + tail) * SR), 2), np.float32)
    cache: dict = {}
    count = 0
    for i, (t, ev, L) in enumerate(zip(sc["tracks"], events, lengths)):
        drums = bool(t.get("drums"))
        bank = 128 if drums else int(t.get("bank", 0))
        prog = int(t.get("kit", 0)) if drums else int(t.get("program", 0))
        if builtin:
            prog = 0 if drums else BUILTIN_FAMILY.get(prog // 8, 0)
        g = 10 ** (float(t.get("gain_db", 0)) / 20)
        pan = float(t.get("pan", 0))
        pl, pr = math.cos((pan + 1) * math.pi / 4) * math.sqrt(2), math.sin((pan + 1) * math.pi / 4) * math.sqrt(2)
        reps = max(1, math.ceil(total / L)) if L > 0 and "bars" in sc else 1
        for r in range(reps):
            for e in ev:
                beat = e.start + r * L
                if beat >= total - 1e-9:
                    continue
                frac = beat % 1.0
                if swing and abs(frac - 0.5) < 1e-6:                  # the off-beat eighth comes late
                    beat += swing * 0.5
                elif swing and drums and min(abs(frac - 0.25), abs(frac - 0.75)) < 1e-6:
                    beat += swing * 0.25                               # and the off-beat sixteenths
                secs = min(e.beats, total - beat) * spb * (1.0 if drums else 0.95)
                for k in e.keys:
                    ck = (bank, prog, k, e.velocity, round(secs, 3))
                    if ck not in cache:
                        cache[ck] = sf.note(bank, prog, k, e.velocity, secs, SR)
                    a = cache[ck]
                    s0 = int(beat * spb * SR)
                    n = min(len(a), len(mix) - s0)
                    if n > 0:
                        mix[s0:s0 + n, 0] += a[:n, 0] * g * pl
                        mix[s0:s0 + n, 1] += a[:n, 1] * g * pr
                    count += 1
    mix = _reverb(mix.astype(np.float64), float(sc.get("reverb", 0.2)))
    end = len(mix)
    while end > int(total * spb * SR) and np.abs(mix[end - SR // 10:end]).max() < 1e-4:
        end -= SR // 10
    mix = mix[:end]
    rms = math.sqrt(float((mix ** 2).mean()) + 1e-12)
    mix *= min(10 ** (-16 / 20) / rms, 0.95 / max(1e-9, np.abs(mix).max() * 1.0))   # about -16 dBFS RMS, no clipping
    mix *= 10 ** (float(sc.get("gain_db", 0)) / 20)
    mix = np.tanh(mix * 1.1) / math.tanh(1.1)                                         # gentle limiter
    res = Music(None, len(mix) / SR, bpm, [i * spb for i in range(int(total))], label, tracks, mix.astype(np.float32),
                count, warns)
    if out is not None:
        res.path = write_wav(mix, out)
    return res


def write_wav(x: np.ndarray, out: str | Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    tmp = out.with_suffix(".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    tmp.replace(out)
    return out
