"""The eight named looks, as v2 recipes (shader layer + palette + look controls + post passes + text).

A look is a small dict in the old v1 vocabulary (effect, palette, cycles, spin, zoom, kaleido, posterize,
halftone, chroma, scanlines, vignette, grain, text, ...); ``preset_recipe`` turns it into a v2 recipe
through ``recipe_from_v1``, so presets, v1 sidecars and ``--set key=value`` overrides share one path.
Spin and kaleido are ``fixed`` shader params (k_spin / u_kaleido): they stay put when a seed resamples
the effect's other params, and they keep the loop seamless (integer multiples of the loop phase).
"""
from __future__ import annotations

import numpy as np

from funkbox.gl.recipe import PALETTES, V1_EFFECTS, Recipe2, Settings2, recipe_from_v1

#: every key a look understands, with the v1 defaults
LOOK_DEFAULTS: dict = dict(
    effect="plasma", palette="acid", cycles=1, spin=0, zoom=0.0, kaleido=0, posterize=0, halftone=0, chroma=0,
    scanlines=False, vignette=0.35, grain=0.0, text="", text_size=0.13, text_wobble=0.0, text_color="#ffffff",
    shadow="#ff2bd6")

#: the named looks (only what differs from LOOK_DEFAULTS)
LOOKS: dict[str, dict] = {
    "acid-plasma":    dict(effect="plasma", palette="acid", cycles=1, chroma=4, scanlines=True, grain=0.04),
    "pop-moire":      dict(effect="moire", palette="candy", cycles=1, halftone=11, vignette=0.2),
    "liquid-disco":   dict(effect="warp", palette="vapor", cycles=1, spin=1, kaleido=6, chroma=3, grain=0.03),
    "hypno-tunnel":   dict(effect="tunnel", palette="toxic", cycles=2, spin=1, scanlines=True, chroma=2),
    "lava-lamp":      dict(effect="blobs", palette="sunset", cycles=1, posterize=7, grain=0.05, vignette=0.5),
    "op-art":         dict(effect="rings", palette="mono", cycles=0, kaleido=0, chroma=5, vignette=0.15),
    "frozen-kaleido": dict(effect="plasma", palette="ice", cycles=1, spin=1, kaleido=8, posterize=9, chroma=2),
    "party-poster":   dict(effect="warp", palette="candy", cycles=1, kaleido=8, halftone=7, chroma=4,
                           text="FUNK", text_wobble=8, shadow="#00e5ff"),
}
DEFAULT_PRESET = "liquid-disco"


def parse_seeds(spec: str) -> list[int]:
    """"1-9" or "3,7,12" or "1-3,8" -> [1, 2, 3, ...]."""
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b) + 1) if b else [int(a)])
    return out


def random_look(seed: int) -> dict:
    """A whole random look from the seed (``--random``)."""
    r = np.random.default_rng(seed ^ 0x5EED)
    return {**LOOK_DEFAULTS,
            "effect": str(r.choice(sorted(V1_EFFECTS))),
            "palette": str(r.choice([p for p in sorted(PALETTES) if p != "mono"])),
            "cycles": int(r.integers(1, 3)), "spin": int(r.choice([0, 0, 1, -1])),
            "zoom": float(r.choice([0.0, 0.15])), "kaleido": int(r.choice([0, 0, 4, 6, 8])),
            "posterize": int(r.choice([0, 0, 6, 9])), "halftone": int(r.choice([0, 0, 0, 8])),
            "chroma": int(r.integers(0, 6)), "scanlines": bool(r.random() < 0.3),
            "grain": float(r.choice([0.0, 0.03]))}


def _coerce(raw: str, default):
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def apply_overrides(look: dict, overrides: dict | list[str] | None) -> dict:
    """Override look keys: a dict, or ``key=value`` strings as on the command line."""
    if not overrides:
        return look
    pairs = overrides.items() if isinstance(overrides, dict) else (p.partition("=")[::2] for p in overrides)
    out = dict(look)
    for key, val in pairs:
        if key not in LOOK_DEFAULTS:
            raise ValueError(f"unknown recipe key {key!r}; have {sorted(LOOK_DEFAULTS)}")
        out[key] = _coerce(val, LOOK_DEFAULTS[key]) if isinstance(val, str) else val
    return out


def look(preset: str = DEFAULT_PRESET, seed: int = 1, text: str = "", overrides: dict | list[str] | None = None,
         random: bool = False) -> dict:
    if random:
        d = random_look(seed)
    elif preset in LOOKS:
        d = {**LOOK_DEFAULTS, **LOOKS[preset]}
    else:
        raise ValueError(f"unknown preset {preset!r}; have {sorted(LOOKS)}")
    if text:
        d = {**d, "text": text, "text_wobble": d["text_wobble"] or 6.0}
    return apply_overrides(d, overrides)


def look_recipe(d: dict, settings: Settings2 | None = None) -> Recipe2:
    return recipe_from_v1({"recipe": d, "settings": vars(settings) if settings else {}})


def preset_recipe(preset: str = DEFAULT_PRESET, seed: int = 1, text: str = "", overrides: dict | list[str] | None = None,
                  random: bool = False, width: int = 540, height: int | None = None, fps: int = 30,
                  seconds: float = 4.0) -> Recipe2:
    """A named (or random) look as a v2 recipe, ready for preview / sheet / render."""
    rec = look_recipe(look(preset, seed, text, overrides, random))
    rec.settings = Settings2(width=width, height=height or width, fps=fps, seconds=seconds, seed=seed)
    return rec


#: the presets as plain v2 recipe dicts (what ``loop list`` documents and ``--recipe`` files look like)
PRESETS: dict[str, dict] = {name: preset_recipe(name).to_dict() for name in LOOKS}


def describe(name: str) -> str:
    v = {**LOOK_DEFAULTS, **LOOKS[name]}
    extras = [f"{k}={v[k]}" for k in ("spin", "kaleido", "halftone", "posterize", "chroma") if v[k]]
    return f"{name:<15} {v['effect']}/{v['palette']}  {' '.join(extras)}"

