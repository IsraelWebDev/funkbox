"""Test-only numpy oracle: the retired v1 CPU effects (field, transform, palette), kept so the GLSL ports
can be checked against an independent implementation. Not part of the package."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

import numpy as np

TAU = 2.0 * math.pi
f32 = np.float32

PALETTES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "acid":   ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), (0.00, 0.33, 0.67)),
    "vapor":  ((0.6, 0.4, 0.7), (0.4, 0.4, 0.3), (1.0, 1.0, 1.0), (0.00, 0.45, 0.25)),
    "sunset": ((0.5, 0.3, 0.3), (0.5, 0.4, 0.3), (1.0, 1.0, 1.0), (0.00, 0.10, 0.25)),
    "toxic":  ((0.4, 0.6, 0.3), (0.4, 0.4, 0.4), (1.0, 1.0, 1.0), (0.30, 0.10, 0.60)),
    "candy":  ((0.8, 0.5, 0.6), (0.3, 0.5, 0.4), (1.0, 1.0, 1.0), (0.00, 0.30, 0.55)),
    "ice":    ((0.4, 0.6, 0.8), (0.3, 0.3, 0.3), (1.0, 1.0, 1.0), (0.55, 0.60, 0.65)),
    "mono":   ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), (0.00, 0.00, 0.00)),
}


FieldFn = Callable[[np.ndarray, np.ndarray, float, dict], "np.ndarray | tuple[np.ndarray, np.ndarray]"]


@dataclass(frozen=True)
class Effect:
    fn: FieldFn
    sample: Callable[[np.random.Generator], dict]
    blurb: str


EFFECTS: dict[str, Effect] = {}


def effect(name: str, blurb: str, sample: Callable[[np.random.Generator], dict]):
    def deco(fn: FieldFn) -> FieldFn:
        EFFECTS[name] = Effect(fn, sample, blurb)
        return fn
    return deco


@effect("plasma", "classic demoscene sine soup",
        lambda r: {"k": r.uniform(4.0, 10.0, 4).round(3).tolist()})
def _plasma(u, v, th, p):
    k = p["k"]
    cx, cy = 0.6 * math.cos(th), 0.6 * math.sin(2 * th)
    f = (np.sin(k[0] * u + th)
         + np.sin(k[1] * v - 2 * th)
         + np.sin(k[2] * (u + v) + th)
         + np.sin(k[3] * np.hypot(u - cx, v - cy) * 2 - 3 * th))
    return f * 0.3 + 0.5


@effect("moire", "two orbiting ring emitters interfering",
        lambda r: {"k": float(r.uniform(8, 16)), "ph": float(r.uniform(0, TAU))})
def _moire(u, v, th, p):
    c1 = (0.45 * math.cos(th), 0.45 * math.sin(th))
    c2 = (0.45 * math.cos(-2 * th + p["ph"]), 0.45 * math.sin(-2 * th + p["ph"]))
    r1 = np.hypot(u - c1[0], v - c1[1])
    r2 = np.hypot(u - c2[0], v - c2[1])
    bands = np.sign(np.sin(p["k"] * r1) * np.sin(p["k"] * r2))
    return 0.3 * (r1 + r2) + 0.22 * bands


@effect("warp", "domain-warped liquid stripes",
        lambda r: {"amp": float(r.uniform(0.25, 0.55)), "freq": float(r.uniform(2.0, 4.5)),
                   "stripes": float(r.uniform(6, 14)), "iters": int(r.integers(2, 4))})
def _warp(u, v, th, p):
    a, f = p["amp"], p["freq"]
    for i in range(p["iters"]):
        u, v = (u + a * np.sin(f * v + (i + 1) * th),
                v + a * np.cos(f * u - (i + 1) * th))
    return 0.5 + 0.35 * np.sin(p["stripes"] * (u + v)) + 0.12 * np.hypot(u, v)


@effect("tunnel", "infinite checkered wormhole",
        lambda r: {"n": float(r.uniform(1.5, 3.5)), "m": int(r.integers(4, 13))})
def _tunnel(u, v, th, p):
    r = np.hypot(u, v).astype(np.float64) + 1e-3
    a = np.arctan2(v, u)
    depth = p["n"] / r
    # Reduce the phase before sin(): near the centre depth is huge, and float32
    # rounding there would flip signs between t=0 and t=1, breaking the loop.
    # m is an integer, so the atan2 branch cut is invisible. The 0.37 offset keeps
    # sign() edges off the exact symmetry diagonals, where sin(0) vs sin(2pi)=-2e-16
    # would flip pixels between the first and last frame.
    s = np.sin(TAU * np.mod(depth, 1.0) + 2 * th) * np.sin(p["m"] * a + th + 0.37)
    return 0.05 * depth + 0.25 * np.sign(s), np.clip(r * 1.6, 0.0, 1.0)


@effect("blobs", "lava-lamp metaballs on Lissajous orbits",
        lambda r: {"balls": [[float(r.uniform(0.3, 0.8)), float(r.uniform(0.3, 0.8)),
                              int(r.integers(1, 4)), int(r.integers(1, 4)),
                              float(r.uniform(0, TAU))] for _ in range(int(r.integers(4, 8)))]})
def _blobs(u, v, th, p):
    f = np.zeros_like(u)
    for ax, ay, fx, fy, ph in p["balls"]:
        cx, cy = ax * math.sin(fx * th + ph), ay * math.cos(fy * th + ph)
        f += 0.05 / ((u - cx) ** 2 + (v - cy) ** 2 + 1e-3)
    lf = np.log1p(f)
    return 0.35 * lf + 0.08 * np.sin(9.0 * lf)


@effect("rings", "op-art concentric pulse with angular wobble",
        lambda r: {"k": float(r.uniform(28, 50)), "lobes": int(r.integers(3, 9)),
                   "wob": float(r.uniform(0.05, 0.2))})
def _rings(u, v, th, p):
    r = np.hypot(u, v)
    a = np.arctan2(v, u)
    rr = r + p["wob"] * np.sin(p["lobes"] * a + 2 * th) * r
    return 0.5 + 0.5 * np.sign(np.sin(p["k"] * rr - 3 * th)) * 0.4 + 0.25 * r



@dataclass
class Look:
    effect: str = "plasma"
    palette: str = "acid"
    cycles: int = 1
    spin: int = 0
    zoom: float = 0.0
    kaleido: int = 0
    params: dict | None = None


@lru_cache(maxsize=4)
def _grid(w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    s = 2.0 / min(w, h)
    x = (np.arange(w, dtype=f32) - (w - 1) / 2) * s
    y = (np.arange(h, dtype=f32) - (h - 1) / 2) * s
    return np.meshgrid(x, y)


def _transform(u, v, th, rc: Look):
    if rc.zoom:
        z = 1.0 + rc.zoom * math.sin(th)
        u, v = u * z, v * z
    if rc.spin:
        c, s = math.cos(rc.spin * th), math.sin(rc.spin * th)
        u, v = c * u - s * v, s * u + c * v
    if rc.kaleido >= 2:
        r = np.hypot(u, v)
        seg = TAU / rc.kaleido
        a = np.mod(np.arctan2(v, u), seg)
        a = np.abs(a - seg / 2)
        u, v = r * np.cos(a), r * np.sin(a)
    return u, v


def _palette(x: np.ndarray, t: float, rc: Look) -> np.ndarray:
    a, b, c, d = (np.asarray(q, dtype=f32) for q in PALETTES[rc.palette])
    # mod before adding time: keeps float32 precision when x is large (tunnel centre)
    phase = np.mod(c * x[..., None] + d, 1.0) + rc.cycles * t
    return a + b * np.cos(TAU * phase)


def render_field(i: int, frames: int, w: int, h: int, rc: Look) -> np.ndarray:
    """One frame, no post passes, as uint8 RGB."""
    t = i / frames
    th = TAU * t
    u, v = _transform(*_grid(w, h), th, rc)
    out = EFFECTS[rc.effect].fn(u, v, th, rc.params or {})
    x, shade = out if isinstance(out, tuple) else (out, None)
    img = _palette(x.astype(f32), t, rc)
    if shade is not None:
        img = img * shade[..., None]
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
