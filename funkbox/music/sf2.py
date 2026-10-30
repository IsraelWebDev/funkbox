"""A small SoundFont 2 player in numpy: real sampled instruments (piano, strings, clarinet, drum kits ...) with no
native synth to install.

    sf = SoundFont("GeneralUser-GS.sf2")
    sf.presets()                      -> [(bank, program, name), ...]
    audio = sf.note(bank=0, program=71, key=62, velocity=100, seconds=0.5, sr=48000)   # (n, 2) float32

What it plays: preset and instrument zones (key and velocity ranges, global zones), sample loops (continuous and
until-release), root key, coarse/fine tuning, scale tuning, pitch correction, the volume envelope (delay, attack, hold,
decay, sustain, release), initial attenuation, pan. What it leaves out: modulators (the defaults' velocity curve is
applied), filters, LFOs, the modulation envelope, chorus/reverb sends (score.py adds one reverb to the mix).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

# generator ids (SoundFont 2.01, section 8.1.2)
G_PAN, G_DELAY, G_ATTACK, G_HOLD, G_DECAY, G_SUSTAIN, G_RELEASE = 17, 33, 34, 35, 36, 37, 38
G_INSTRUMENT, G_KEYRANGE, G_VELRANGE, G_ATTEN = 41, 43, 44, 48
G_COARSE, G_FINE, G_SAMPLE, G_MODES, G_SCALE, G_ROOT = 51, 52, 53, 54, 56, 58
G_START, G_END, G_LSTART, G_LEND = 0, 1, 2, 3
G_START_C, G_END_C, G_LSTART_C, G_LEND_C = 4, 12, 45, 50
RANGES = (G_KEYRANGE, G_VELRANGE)
ADDITIVE = {G_PAN, G_DELAY, G_ATTACK, G_HOLD, G_DECAY, G_SUSTAIN, G_RELEASE, G_ATTEN, G_COARSE, G_FINE, G_SCALE}
DEFAULTS = {G_PAN: 0, G_DELAY: -12000, G_ATTACK: -12000, G_HOLD: -12000, G_DECAY: -12000, G_SUSTAIN: 0,
            G_RELEASE: -12000, G_ATTEN: 0, G_COARSE: 0, G_FINE: 0, G_MODES: 0, G_SCALE: 100, G_ROOT: -1,
            G_START: 0, G_END: 0, G_LSTART: 0, G_LEND: 0, G_START_C: 0, G_END_C: 0, G_LSTART_C: 0, G_LEND_C: 0}
ATTEN_SCALE = 0.4            # most players (FluidSynth among them) apply 0.4 of the written attenuation


class SF2Error(ValueError):
    pass


@dataclass
class Sample:
    name: str
    start: int
    end: int
    loop_start: int
    loop_end: int
    rate: int
    root: int
    correction: int


@dataclass
class Zone:
    gens: dict[int, object] = field(default_factory=dict)


def _chunks(data: bytes, off: int, end: int):
    while off + 8 <= end:
        cid, size = data[off:off + 4], struct.unpack_from("<I", data, off + 4)[0]
        yield cid, off + 8, size
        off += 8 + size + (size & 1)


def _zones(bags, gens, lo: int, hi: int) -> list[Zone]:
    out = []
    for b in range(lo, hi):
        z = Zone()
        for g in range(bags[b], bags[b + 1]):
            oper, amt = gens[g]
            if oper in RANGES:
                z.gens[oper] = (amt & 0xFF, (amt >> 8) & 0xFF)
            elif oper in (G_INSTRUMENT, G_SAMPLE, G_MODES):
                z.gens[oper] = amt & 0xFFFF
            else:
                z.gens[oper] = amt - 0x10000 if amt >= 0x8000 else amt
        out.append(z)
    return out


class SoundFont:
    def __init__(self, path: str | Path):
        data = Path(path).read_bytes()
        if data[:4] != b"RIFF" or data[8:12] != b"sfbk":
            raise SF2Error(f"{path}: not a SoundFont 2 file")
        self.path = Path(path)
        pdta: dict[bytes, tuple[int, int]] = {}
        smpl = None
        for cid, off, size in _chunks(data, 12, len(data)):
            if cid != b"LIST":
                continue
            kind = data[off:off + 4]
            for sid, soff, ssize in _chunks(data, off + 4, off + size):
                if kind == b"sdta" and sid == b"smpl":
                    smpl = np.frombuffer(data, "<i2", ssize // 2, soff).astype(np.float32) / 32768.0
                elif kind == b"pdta":
                    pdta[sid] = (soff, ssize)
        if smpl is None or not {b"phdr", b"pbag", b"pgen", b"inst", b"ibag", b"igen", b"shdr"} <= set(pdta):
            raise SF2Error(f"{path}: missing SoundFont chunks")
        self.data = smpl

        def recs(cid, fmt):
            off, size = pdta[cid]
            n = struct.calcsize(fmt)
            return [struct.unpack_from(fmt, data, off + i * n) for i in range(size // n)]

        phdr = recs(b"phdr", "<20sHHHIII")
        pbag = [r[0] for r in recs(b"pbag", "<HH")]
        pgen = recs(b"pgen", "<HH")
        inst = recs(b"inst", "<20sH")
        ibag = [r[0] for r in recs(b"ibag", "<HH")]
        igen = recs(b"igen", "<HH")
        self.samples = [Sample(r[0].split(b"\0")[0].decode("latin-1"), r[1], r[2], r[3], r[4], r[5], r[6],
                               r[7] - 256 if r[7] > 127 else r[7]) for r in recs(b"shdr", "<20sIIIIIBbHH")[:-1]]
        self.instruments = [_zones(ibag, igen, inst[i][1], inst[i + 1][1]) for i in range(len(inst) - 1)]
        self._presets: dict[tuple[int, int], tuple[str, list[Zone]]] = {}
        for i in range(len(phdr) - 1):
            name, prog, bank, bag = phdr[i][0].split(b"\0")[0].decode("latin-1"), phdr[i][1], phdr[i][2], phdr[i][3]
            self._presets[(bank, prog)] = (name, _zones(pbag, pgen, bag, phdr[i + 1][3]))

    def presets(self) -> list[tuple[int, int, str]]:
        return sorted((b, p, n) for (b, p), (n, _) in self._presets.items())

    def preset_name(self, bank: int, program: int) -> str:
        return self._presets[self._find(bank, program)][0]

    def _find(self, bank: int, program: int) -> tuple[int, int]:
        for k in ((bank, program), (bank, 0) if bank == 128 else (0, program), (0, 0)):
            if k in self._presets:
                return k
        raise SF2Error(f"no preset bank {bank} program {program}")

    def regions(self, bank: int, program: int, key: int, vel: int) -> list[tuple[Sample, dict]]:
        """The samples a note plays, each with its combined generators."""
        _, pzones = self._presets[self._find(bank, program)]
        pglobal = pzones[0].gens if pzones and G_INSTRUMENT not in pzones[0].gens else {}
        out = []
        for pz in pzones:
            if G_INSTRUMENT not in pz.gens:
                continue
            pg = {**pglobal, **pz.gens}
            if not _inside(pg, key, vel):
                continue
            izones = self.instruments[pg[G_INSTRUMENT]]
            iglobal = izones[0].gens if izones and G_SAMPLE not in izones[0].gens else {}
            for iz in izones:
                if G_SAMPLE not in iz.gens:
                    continue
                ig = {**DEFAULTS, **iglobal, **iz.gens}
                if not _inside(ig, key, vel):
                    continue
                for g, v in pg.items():
                    if g in ADDITIVE:
                        ig[g] = ig.get(g, DEFAULTS.get(g, 0)) + v
                out.append((self.samples[ig[G_SAMPLE]], ig))
        return out

    def note(self, bank: int, program: int, key: int, velocity: int, seconds: float, sr: int = 48000) -> np.ndarray:
        """A rendered note: ``seconds`` held, then its release. (n, 2) float32."""
        parts = [self._voice(s, g, key, velocity, seconds, sr) for s, g in self.regions(bank, program, key, velocity)]
        if not parts:
            return np.zeros((1, 2), np.float32)
        n = max(len(p) for p in parts)
        out = np.zeros((n, 2), np.float32)
        for p in parts:
            out[: len(p)] += p
        return out

    def _voice(self, s: Sample, g: dict, key: int, vel: int, held: float, sr: int) -> np.ndarray:
        tc = lambda v: 2.0 ** (max(-12000, min(8000, v)) / 1200.0)
        delay, attack, hold, decay, release = (tc(g[G_DELAY]), tc(g[G_ATTACK]), tc(g[G_HOLD]), tc(g[G_DECAY]),
                                               tc(g[G_RELEASE]))
        release = min(release, 8.0)
        start = s.start + g[G_START] + 32768 * g[G_START_C]
        end = s.end + g[G_END] + 32768 * g[G_END_C]
        ls = s.loop_start + g[G_LSTART] + 32768 * g[G_LSTART_C]
        le = s.loop_end + g[G_LEND] + 32768 * g[G_LEND_C]
        root = g[G_ROOT] if g[G_ROOT] >= 0 else s.root
        cents = (key - root) * g[G_SCALE] + g[G_COARSE] * 100 + g[G_FINE] + s.correction
        step = 2.0 ** (cents / 1200.0) * s.rate / sr
        mode = g[G_MODES] & 3
        looped = mode in (1, 3) and le - ls > 2 and start <= ls < le <= end
        total = held + release
        n = max(1, int(total * sr))
        pos = np.arange(n, dtype=np.float64) * step
        if looped:
            L = le - ls
            rel = start + pos
            if mode == 3:                                 # loop while held, then play on to the end
                n_held = int(held * sr)
                over = rel[:n_held] >= ls
                rel[:n_held][over] = ls + (rel[:n_held][over] - ls) % L
                if n_held < n:
                    last = rel[n_held - 1] if n_held else start
                    rel[n_held:] = last + np.arange(1, n - n_held + 1) * step
            else:
                over = rel >= ls
                rel[over] = ls + (rel[over] - ls) % L
        else:
            rel = start + pos
            stop = np.searchsorted(rel, end - 1)
            n = max(1, min(n, int(stop)))
            rel = rel[:n]
        idx = np.clip(rel, start, end - 2)
        i0 = idx.astype(np.int64)
        fr = (idx - i0).astype(np.float32)
        i1 = np.minimum(i0 + 1, len(self.data) - 1)
        if looped:                                        # across the loop point the next sample is the loop start
            i1 = np.where((i1 >= le) & (i0 < le), ls, i1)
        x = self.data[i0] * (1 - fr) + self.data[i1] * fr
        x[rel >= end - 1] = 0.0
        t = np.arange(n) / sr
        sus = 10.0 ** (-max(0, min(1440, g[G_SUSTAIN])) / 200.0)
        env = np.ones(n, np.float32)
        a0, a1, h1 = delay, delay + attack, delay + attack + hold
        env[t < a0] = 0.0
        m = (t >= a0) & (t < a1)
        env[m] = (t[m] - a0) / max(attack, 1e-6)
        m = t >= h1
        # decay: down by 100 dB over ``decay`` seconds (linear in dB), stopping at the sustain level
        db = -100.0 * (t[m] - h1) / max(decay, 1e-6)
        env[m] = np.maximum(10.0 ** (db / 20.0), sus)
        r = t >= held
        if r.any():
            lvl = float(env[r][0])
            db = -100.0 * (t[r] - held) / max(release, 1e-6)
            env[r] = lvl * 10.0 ** (db / 20.0)
        vel_gain = (max(1, vel) / 127.0) ** 2
        gain = 10.0 ** (-ATTEN_SCALE * max(0, g[G_ATTEN]) / 200.0) * vel_gain
        y = x * env * gain
        pan = max(-500, min(500, g[G_PAN])) / 500.0
        lg, rg = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        return np.stack([y * lg, y * rg], 1).astype(np.float32)


def _inside(g: dict, key: int, vel: int) -> bool:
    k0, k1 = g.get(G_KEYRANGE, (0, 127))
    v0, v1 = g.get(G_VELRANGE, (0, 127))
    return k0 <= key <= k1 and v0 <= vel <= v1


@lru_cache(maxsize=4)
def load(path: str) -> SoundFont:
    return SoundFont(path)


# --------------------------------------------------------------------------- writing (tests, the built-in bank)
def write_sf2(path: str | Path, instruments: list[dict], rate: int = 44100) -> None:
    """Write a minimal SoundFont. Each instrument: {"name", "program", "bank", "zones": [{"wave" (float array),
    "root" (MIDI key), "keys": (lo, hi), "loop": (start, end) or None, "gens": {generator id: value}}]}; a single
    zone may be given inline (wave/root/loop/gens on the instrument). Used for the built-in bank and tests."""
    smpl = bytearray()
    shdr, inst, ibag, igen, phdr, pbag, pgen = [], [], [], [], [], [], []
    for n, ins in enumerate(instruments):
        zones = ins.get("zones") or [ins]
        inst.append(struct.pack("<20sH", ins["name"][:19].encode(), len(ibag)))
        for z in zones:
            w = np.clip(np.asarray(z["wave"], np.float64), -1, 1)
            start = len(smpl) // 2
            smpl += (w * 32767).astype("<i2").tobytes() + b"\0\0" * 46
            loop = z.get("loop")
            ls, le = (start + loop[0], start + loop[1]) if loop else (start, start + len(w) - 1)
            sid = len(shdr)
            shdr.append(struct.pack("<20sIIIIIBbHH", f"{ins['name'][:14]}{sid}".encode(), start, start + len(w),
                                    ls, le, rate, z.get("root", 60), 0, 0, 1))
            ibag.append(struct.pack("<HH", len(igen), 0))
            lo, hi = z.get("keys", (0, 127))
            gens = [(G_KEYRANGE, lo | (hi << 8))] + [(k, v & 0xFFFF) for k, v in (z.get("gens") or {}).items()]
            gens += [(G_MODES, 1 if loop else 0), (G_SAMPLE, sid)]
            igen += [struct.pack("<HH", k, v) for k, v in gens]
        phdr.append(struct.pack("<20sHHHIII", ins["name"][:19].encode(), ins.get("program", n), ins.get("bank", 0),
                                len(pbag), 0, 0, 0))
        pbag.append(struct.pack("<HH", len(pgen), 0))
        pgen.append(struct.pack("<HH", G_INSTRUMENT, n))
    shdr.append(struct.pack("<20sIIIIIBbHH", b"EOS", 0, 0, 0, 0, 0, 0, 0, 0, 0))
    inst.append(struct.pack("<20sH", b"EOI", len(ibag)))
    ibag.append(struct.pack("<HH", len(igen), 0))
    igen.append(struct.pack("<HH", 0, 0))
    phdr.append(struct.pack("<20sHHHIII", b"EOP", 0, 0, len(pbag), 0, 0, 0))
    pbag.append(struct.pack("<HH", len(pgen), 0))
    pgen.append(struct.pack("<HH", 0, 0))

    def ck(cid: bytes, body: bytes) -> bytes:
        return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")

    def lst(kind: bytes, body: bytes) -> bytes:
        return ck(b"LIST", kind + body)
    info = lst(b"INFO", ck(b"ifil", struct.pack("<HH", 2, 1)) + ck(b"INAM", b"funkbox\0"))
    sdta = lst(b"sdta", ck(b"smpl", bytes(smpl)))
    pd = b"".join(ck(c, b"".join(v)) for c, v in ((b"phdr", phdr), (b"pbag", pbag), (b"pmod", [b"\0" * 10]),
                                                   (b"pgen", pgen), (b"inst", inst), (b"ibag", ibag),
                                                   (b"imod", [b"\0" * 10]), (b"igen", igen), (b"shdr", shdr)))
    body = b"sfbk" + info + sdta + lst(b"pdta", pd)
    Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
